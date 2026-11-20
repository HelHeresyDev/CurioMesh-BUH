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
from time import perf_counter, sleep

SCRIPTS = Path(__file__).resolve().parent
DETAIL = {"high_fidelity": 2, "balanced": 5, "aggressive": 10}
CATEGORIES = ("architecture", "organic", "hard_surface", "decorative", "unspecified")
COLLISION = ("auto", "none", "simple", "convex", "complex")
PBR_TEXTURES = {"baseColorTexture": "BaseColorTexture", "metallicRoughnessTexture": "MetallicRoughnessTexture"}


class UnrealUnavailable(RuntimeError):
    pass


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
    materials = document.get("materials", [])
    texture_roles = set()
    for primitive in primitives:
        attributes = primitive["attributes"]
        index = primitive.get("indices", attributes["POSITION"])
        count = accessors[index]["count"]
        if primitive.get("mode", 4) != 4 or count <= 0 or count % 3:
            raise ValueError("Expected non-empty triangle primitives")
        triangles += count // 3
        uv_primitives += int("TEXCOORD_0" in attributes)
        if "material" in primitive:
            if not 0 <= primitive["material"] < len(materials):
                raise ValueError("GLB primitive references a missing material")
            pbr = materials[primitive["material"]].get("pbrMetallicRoughness", {})
            for role in pbr.keys() & PBR_TEXTURES.keys():
                texture = pbr[role]
                channel = f"TEXCOORD_{texture.get('texCoord', 0)}"
                if channel not in attributes or not 0 <= texture["index"] < len(document.get("textures", [])):
                    raise ValueError(f"GLB has an invalid {role} texture or UV assignment")
                texture_roles.add(role)
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
            "primitives_with_material": assigned_primitives, "materials": len(materials), "embedded_images": images,
            "pbr_texture_roles": sorted(texture_roles)}


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


def unreal_import(helper, output, run_id, *, build=None, probes=None, pie=False):
    def request(toolset, tool, arguments):
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "args.json"
            path.write_text(json.dumps(arguments))
            process = subprocess.run([sys.executable, str(helper), toolset, tool, str(path)],
                                     text=True, timeout=100, capture_output=True)
        if process.returncode:
            raise RuntimeError(f"Unreal MCP helper exited {process.returncode}: {process.stderr.strip()[-1000:]}")
        response = json.loads(process.stdout)
        result = response.get("result", {})
        if not result or result.get("isError"):
            raise RuntimeError(f"Unreal MCP reported an error: {response}")
        return result["content"][0]["text"]

    try:
        tools = request("-", "list_toolsets", {})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise UnrealUnavailable(str(error)) from error
    match = re.search(r"^- ([^:\n]+\.CurioMeshValidation(?:_0x[0-9A-Fa-f]+)?):", tools, re.M)
    if not match:
        raise UnrealUnavailable("The Unreal editor has no CurioMeshValidation toolset")

    def call(tool, args):
        data = request(match[1], tool, {"run_id": run_id, **args})
        return json.loads(json.loads(data)["returnValue"])

    def is_playing():
        data = request("EditorToolset.EditorAppToolset", "IsPIERunning", {})
        return json.loads(data)["returnValue"] is True

    if pie and is_playing():
        raise UnrealUnavailable("The editor already has an active PIE session")
    payload = {"filename": output.name}
    if build is None:
        return {"import": call("import_mesh", payload)}
    result = call("build_static_asset", {**payload, **build, "expected_sha256": sha256(output.read_bytes()).hexdigest()})
    if probes and "mesh" in result and result["collision"]["status"] != "fail":
        result["preview"] = call("prepare_preview", {"mesh_path": result["mesh"]["path"]})
        arguments = {"probes_json": json.dumps(probes)}
        if pie:
            editor = "EditorToolset.EditorAppToolset"
            request(editor, "StartPIE", {"options": {"bSimulate": False, "playMode": "PlayMode_InViewPort",
                                                      "warmupSeconds": 3}})
            try:
                for _ in range(20):
                    if is_playing():
                        break
                    sleep(0.25)
                else:
                    raise RuntimeError("Unreal did not start PIE")
                measured = call("validate_collision", arguments)
                if measured["world"] != "pie":
                    raise RuntimeError("The collision tests did not run in PIE")
            finally:
                request(editor, "StopPIE", {})
        else:
            measured = call("validate_collision", arguments)
        result["collision"].update(measured)
    return result


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
    textures = {texture["path"] for texture in imported["textures"]}
    assignments = [slot["texture_parameters"] for mesh in meshes for slot in mesh["materials"]]
    for role in exported["pbr_texture_roles"]:
        if not any(params.get(PBR_TEXTURES[role]) in textures for params in assignments):
            warnings.append(f"unreal_missing_{PBR_TEXTURES[role]}")
    return warnings


def parse_ratios(value):
    try:
        ratios = [float(part) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use two comma-separated fractions") from error
    if len(ratios) != 2 or not 1 > ratios[0] > ratios[1] > 0:
        raise argparse.ArgumentTypeError("LOD ratios must satisfy 1 > first > second > 0")
    return ratios


def disposition(report):
    if not report["ok"]:
        return "FAIL", [report.get("error", "pipeline_failed")]
    failed = [name for name in ("lods", "collision") if report[name]["status"] == "fail"]
    fatal = [f"{name}_validation_failed" for name in failed]
    if report["unreal"]["status"] == "fail":
        fatal.extend(report["unreal"].get("warnings") or ["unreal_validation_failed"])
    if fatal:
        return "FAIL", fatal
    reasons = list(report["export"]["warnings"])
    quality = report["repair"]["quality_verdict"]
    if quality["human_review"]:
        reasons.extend(quality["warnings"] + quality.get("residual_checks", []))
        if not quality["warnings"] and not quality.get("residual_checks"):
            reasons.append("repair_review_required")
    if report["unreal"]["status"] in {"untested", "unavailable"}:
        reasons.append(f"unreal_{report['unreal']['status']}")
    else:
        reasons.extend(report["unreal"]["warnings"])
    if report["lods"]["requested"] and report["lods"].get("visual_status") != "pass":
        reasons.append("lod_visual_validation_pending")
    if report["collision"]["requested"] not in {None, "none"} and report["collision"].get("mode") != "none":
        if report["collision"]["status"] != "pass":
            reasons.append("collision_queries_untested")
        elif report["collision"].get("world") != "pie":
            reasons.append("collision_pie_untested")
    if report.get("automatic_category_review"):
        reasons.append("asset_category_unspecified")
    return ("REVIEW", sorted(set(reasons))) if reasons else ("PASS", [])


def main():
    started = perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--addon", type=Path, required=True)
    parser.add_argument("--preset", choices=(*DETAIL, "auto"), default="balanced")
    parser.add_argument("--asset-category", choices=CATEGORIES, default="unspecified")
    parser.add_argument("--repair", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--quality-policy", type=Path)
    parser.add_argument("--blender", default=os.getenv("BLENDER", "blender"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--unreal-mcp-helper", type=Path)
    parser.add_argument("--unreal-run-id")
    parser.add_argument("--unreal-pie", action="store_true", help="Run requested collision probes in Unreal PIE")
    parser.add_argument("--lods", action="store_true", help="Build Unreal LOD0–2 from the final GLB")
    parser.add_argument("--lod-ratios", type=parse_ratios, default=[0.5, 0.25])
    parser.add_argument("--collision", choices=COLLISION)
    parser.add_argument("--collision-probes", type=Path, help="JSON line and capsule checks in Unreal centimeters")
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
    if options.collision_probes and not (options.unreal_mcp_helper and (options.collision or options.lods)):
        parser.error("Collision probes require Unreal validation and a collision or LOD request")
    if options.unreal_pie and not options.collision_probes:
        parser.error("Unreal PIE requires collision probes")
    source, output, report_path = paths
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    preset = "balanced" if options.preset == "auto" else options.preset
    collision = options.collision or ("auto" if options.lods else None)
    report = {"ok": False, "input": str(source), "preset": preset, "preset_requested": options.preset,
              "asset_category": options.asset_category,
              "automatic_category_review": options.preset == "auto" and options.asset_category == "unspecified",
              "lods": {"requested": options.lods, "status": "untested"},
              "collision": {"requested": collision, "status": "untested"},
              "unreal": {"status": "untested"}}
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
                                         ["--use","GAME","--detail",str(DETAIL[preset])],
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
                    if not set(report["source_glb"]["pbr_texture_roles"]) <= set(report["export"]["pbr_texture_roles"]):
                        raise ValueError("The optimized GLB lost a source PBR texture assignment")
                    if source_images and sorted(i["sha256"] for i in source_images) != sorted(i["sha256"] for i in output_images):
                        warnings.append("embedded_image_payloads_changed")
            report["export"]["warnings"] = warnings
            os.link(exported, output)
        report["output"] = str(output)
        if options.unreal_mcp_helper:
            config = {"ratios": options.lod_ratios if options.lods else [], "collision": collision,
                      "category": options.asset_category} if collision else None
            probes = json.loads(options.collision_probes.read_text()) if options.collision_probes else None
            result = unreal_import(options.unreal_mcp_helper, output, options.unreal_run_id, build=config,
                                   probes=probes, pie=options.unreal_pie)
            imported = result["import"]
            warnings = unreal_warnings(imported, report["export"])
            critical = [warning for warning in warnings if warning in {"unreal_uv_layer_missing",
                        "unreal_source_triangle_count_changed", "unreal_texture_count_lower_than_export"}
                        or warning.startswith("unreal_missing_")]
            report["unreal"] = {**imported, "status": "fail" if critical else "pass", "warnings": warnings}
            if "mesh" in result:
                report["unreal"]["asset"] = result["mesh"]
            if "preview" in result:
                report["unreal"]["preview"] = result["preview"]
            if "lods" in result:
                report["lods"].update(result["lods"], visual_status="untested")
            if "collision" in result:
                report["collision"].update(result["collision"])
        report["ok"] = True
    except UnrealUnavailable as error:
        report["unreal"] = {"status": "unavailable", "detail": str(error)}
        report["ok"] = True
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, struct.error, subprocess.SubprocessError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
        if options.unreal_mcp_helper and "output" in report:
            report["unreal"]["status"] = "fail"
    decision, reasons = disposition(report)
    report.update(disposition=decision, disposition_reasons=reasons, ok=decision != "FAIL",
                  human_review=decision != "PASS", processing_seconds=round(perf_counter() - started, 2))
    with report_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(f"CurioMesh report {report_path}", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
