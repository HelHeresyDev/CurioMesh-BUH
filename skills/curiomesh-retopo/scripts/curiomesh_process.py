"""Retopologize, screen repairs, and validate a GLB using an isolated Blender profile."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory

SCRIPTS = Path(__file__).resolve().parent
DETAIL = {"high_fidelity": 2, "balanced": 5, "aggressive": 10}


def glb_summary(path):
    data = Path(path).read_bytes()
    if len(data) < 20 or struct.unpack_from("<4sII", data) != (b"glTF", 2, len(data)):
        raise ValueError("Invalid GLB header or length")
    offset, chunks = 12, {}
    while offset < len(data):
        size, kind = struct.unpack_from("<I4s", data, offset)
        offset += 8
        if kind in chunks or offset + size > len(data) or size % 4:
            raise ValueError("Invalid GLB chunk")
        chunks[kind] = data[offset:offset + size]
        offset += size
    document = json.loads(chunks[b"JSON"])
    binary = chunks.get(b"BIN\x00", b"")
    buffers, views = document.get("buffers", []), document.get("bufferViews", [])
    if len(buffers) != 1 or buffers[0].get("uri") or buffers[0]["byteLength"] > len(binary):
        raise ValueError("Expected one embedded GLB buffer")
    for view in views:
        start = view.get("byteOffset", 0)
        if view["buffer"] != 0 or start < 0 or start + view["byteLength"] > buffers[0]["byteLength"]:
            raise ValueError("GLB buffer view exceeds embedded data")
    accessors = document["accessors"]
    primitives = [primitive for mesh in document["meshes"] for primitive in mesh["primitives"]]
    if not primitives:
        raise ValueError("No mesh primitives in GLB")
    triangles, uv_primitives, assigned_primitives = 0, 0, 0
    material_count = len(document.get("materials", []))
    for primitive in primitives:
        attributes = primitive["attributes"]
        index = primitive.get("indices", attributes["POSITION"])
        count = accessors[index]["count"]
        if primitive.get("mode", 4) != 4 or count <= 0 or count % 3:
            raise ValueError("Expected non-empty triangle primitives")
        triangles += count // 3
        uv_primitives += int("TEXCOORD_0" in attributes)
        if "material" in primitive:
            if not 0 <= primitive["material"] < material_count:
                raise ValueError("GLB primitive references a missing material")
            assigned_primitives += 1
    images = []
    for image in document.get("images", []):
        view = views[image["bufferView"]]
        start = view.get("byteOffset", 0)
        payload = binary[start:start + view["byteLength"]]
        if not payload:
            raise ValueError("GLB contains an empty image")
        images.append({"mime_type": image["mimeType"], "sha256": sha256(payload).hexdigest()})
    return {"triangles": triangles, "primitives": len(primitives), "primitives_with_uv": uv_primitives,
            "primitives_with_material": assigned_primitives, "materials": material_count, "embedded_images": images}


def run_stage(options, profile_env, name, input_path, stage_args, output, report):
    command = [options.blender, "-b", "--factory-startup", "-noaudio", "-t", str(options.threads),
               "--python-exit-code", "1", "--python", str(SCRIPTS / name), "--", str(input_path),
               "--addon", str(options.addon.resolve()), "--out", str(output), "--report", str(report), *stage_args]
    result = subprocess.run(command, env=profile_env, capture_output=True, text=True, timeout=options.timeout)
    if result.returncode:
        raise RuntimeError(f"{name} exited {result.returncode}\n{result.stderr[-2000:]}\n{result.stdout[-2000:]}")
    data = json.loads(report.read_text())
    if not data.get("ok") or not output.is_file():
        raise RuntimeError(f"{name} failed: {data.get('error', 'missing output')}")
    return data


def unreal_import(helper, output, run_id):
    def request(toolset, tool, arguments):
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "args.json"
            path.write_text(json.dumps(arguments))
            response = json.loads(subprocess.check_output(
                [sys.executable, str(helper), toolset, tool, str(path)], text=True, timeout=100))
        result = response.get("result", {})
        if not result or result.get("isError"):
            raise RuntimeError(f"Unreal MCP reported an error: {response}")
        return result["content"][0]["text"]

    tools = request("-", "list_toolsets", {})
    match = re.search(r"^- (PythonTypes\.CurioMeshValidation[^:]+):", tools, re.M)
    if not match:
        raise RuntimeError("The Unreal editor has no CurioMeshValidation toolset")
    data = request(match[1], "import_mesh", {"filename": output.name, "run_id": run_id})
    return json.loads(json.loads(data)["returnValue"])


def unreal_warnings(imported, exported):
    meshes = imported["meshes"]
    sources = [mesh.get("mesh_description") for mesh in meshes]
    warnings = []
    if not sources or any(source is None for source in sources):
        warnings.append("unreal_source_triangle_count_unavailable")
    elif sum(source["triangles"] for source in sources) != exported["triangles"]:
        warnings.append("unreal_source_triangle_count_changed")
    if exported["primitives_with_uv"] and any(not m["lods"][0]["uv_channels"] for m in meshes):
        warnings.append("unreal_uv_layer_missing")
    if len(imported["textures"]) < len(exported["embedded_images"]):
        warnings.append("unreal_texture_count_lower_than_export")
    return warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--addon", type=Path, required=True)
    parser.add_argument("--preset", choices=DETAIL, default="balanced")
    parser.add_argument("--repair", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--quality-policy", type=Path)
    parser.add_argument("--blender", default=os.getenv("BLENDER", "blender"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--unreal-mcp-helper", type=Path)
    parser.add_argument("--unreal-run-id")
    options = parser.parse_args()
    paths = (options.input.resolve(), options.out.resolve(), options.report.resolve())
    if len(set(paths)) != 3 or paths[1].suffix.lower() != ".glb":
        parser.error("Use distinct input, GLB output and JSON report paths")
    if any(path.exists() for path in paths[1:]):
        parser.error("Choose new output and report paths")
    if options.threads < 1 or options.timeout < 1:
        parser.error("Threads and timeout must be positive")
    if bool(options.unreal_mcp_helper) != bool(options.unreal_run_id):
        parser.error("Pass the Unreal MCP helper and run ID together")
    source, output, report_path = paths
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "human_review": True, "input": str(source), "preset": options.preset}
    try:
        with TemporaryDirectory(prefix=".curiomesh-process-", dir=output.parent) as name:
            scratch = Path(name)
            env = dict(os.environ)
            for var, dirname in (("BLENDER_USER_CONFIG","config"), ("BLENDER_USER_EXTENSIONS","extensions"),
                                 ("BLENDER_USER_SCRIPTS","scripts")):
                directory = scratch / dirname
                directory.mkdir()
                env[var] = str(directory)
            draft, exported = scratch / "candidate.blend", scratch / "candidate.glb"
            report["retopo"] = run_stage(options, env, "curiomesh_run.py", source,
                                         ["--use","GAME","--detail",str(DETAIL[options.preset])],
                                         draft, scratch / "retopo.json")
            args = ["--original", str(source)]
            if options.repair:
                args += ["--fix", "ALL"]
            if options.quality_policy:
                args += ["--quality-policy", str(options.quality_policy.resolve())]
            report["repair"] = run_stage(options, env, "curiomesh_fix.py", draft, args, exported,
                                         scratch / "repair.json")
            report["export"] = glb_summary(exported)
            if report["export"]["triangles"] != report["repair"]["after"]["triangulated_faces"]:
                raise ValueError("Exported GLB triangle count differs from the accepted mesh")
            warnings = []
            if source.suffix.lower() == ".glb":
                try:
                    report["source_glb"] = glb_summary(source)
                except (KeyError, TypeError, ValueError, struct.error):
                    warnings.append("source_glb_not_comparable")
                else:
                    source_images = report["source_glb"]["embedded_images"]
                    output_images = report["export"]["embedded_images"]
                    if source_images and sorted(i["sha256"] for i in source_images) != sorted(i["sha256"] for i in output_images):
                        warnings.append("embedded_image_payloads_changed")
            report["export"]["warnings"] = warnings
            os.link(exported, output)
        report["output"] = str(output)
        if options.unreal_mcp_helper:
            report["unreal"] = unreal_import(options.unreal_mcp_helper, output, options.unreal_run_id)
            report["unreal"]["warnings"] = unreal_warnings(report["unreal"], report["export"])
        report["human_review"] = bool(report["repair"]["quality_verdict"]["human_review"] or warnings
                                      or report.get("unreal", {}).get("warnings"))
        report["ok"] = True
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, struct.error, subprocess.SubprocessError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    with report_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(f"CurioMesh report {report_path}", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
