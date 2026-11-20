# SPDX-License-Identifier: GPL-3.0-or-later
"""Find and repair bad faces headlessly, screening each candidate for geometry changes.

blender -b --factory-startup --python curiomesh_fix.py -- RESULT --original ORIGINAL --fix ALL --out FIXED.glb --report FIX.json

--fix: ALL, or a comma list in the order to try: CLEAN,FLIP,UNTANGLE,RELAX,PATCH (omit to only measure).
--faces 3,10-20: fix only these face numbers (default: every bad face).  --grow N: rings of faces checked around them (0 with --faces).
--original: your source mesh. Says which way is out; Flip Inward and Untangle need it.
Each candidate needs fewer bad faces and must pass the geometry checks. TRIANGULATE_BENT is an opt-in local alternative.
Use --quality-policy with a JSON file to override thresholds. bad_face_ids in the report feed --faces.
"""

import argparse
from dataclasses import asdict
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cm_agent as cm  # noqa: E402
import cm_quality as quality  # noqa: E402


def count_bad(obj, fix_select, context, limit=2000):
    """Defects of the whole mesh, by kind, using the same checks the Fix Selection panel uses."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    area = list(bm.faces)
    found, bad = fix_select.measure(area, fix_select._source(context, obj))
    out = {k: int(getattr(found, k).sum()) for k in cm.DEFECT_KINDS}
    out["bad"] = int(bad.sum())
    out["faces"] = len(area)
    out["intersection_pairs"] = len(found.pairs)
    out["bent_face_ids"] = [int(i) for i in found.bent.nonzero()[0]]
    out["bad_face_ids"] = [int(i) for i in bad.nonzero()[0][:limit]]
    out["bad_faces"] = [
        {"face_id": i, "center_world": list(obj.matrix_world @ area[i].calc_center_median()),
         "kinds": [kind for kind in cm.DEFECT_KINDS if getattr(found, kind)[i]]}
        for i in out["bad_face_ids"]
    ]
    bm.free()
    return out


def select_faces(obj, ids):
    """Select exactly these faces (none selected = the whole mesh) and go into Edit Mode."""
    import bpy
    import numpy as np

    me = obj.data
    n = len(me.polygons)
    face = np.zeros(n, bool)
    vert = np.zeros(len(me.vertices), bool)
    if ids:
        face[[i for i in ids if 0 <= i < n]] = True
        verts = np.array([v for i in np.nonzero(face)[0] for v in me.polygons[int(i)].vertices], dtype=int)
        vert[verts] = True
    me.vertices.foreach_set("select", vert)
    me.polygons.foreach_set("select", face)
    me.edges.foreach_set("select", np.zeros(len(me.edges), bool))
    me.update()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="VERT")
    if not ids:
        bpy.ops.mesh.select_all(action="DESELECT")


def fix_rounds(fixes, first, do_step, *, stop_after_change=False):
    """Keep going while the bad-face count drops: run the whole fix list again after every round that gained something,
    and stop when a round gains nothing or no bad face is left. No round cap. do_step(key, current, fresh) returns
    (record, new_count_or_None); None means the step was undone."""
    current, steps, rounds = first, [], 0
    while fixes and current["bad"] > 0:
        rounds += 1
        gained = False
        for key in fixes:
            record, after = do_step(key, current, current is first)
            record["round"] = rounds
            steps.append(record)
            if after is not None:
                current, gained = after, True
                if stop_after_change:
                    return current, steps, rounds
                if current["bad"] == 0:
                    break
        if not gained:
            break
    return current, steps, rounds


def triangulate_bent(obj, indices):
    import bmesh

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    faces = [bm.faces[i] for i in indices if len(bm.faces[i].verts) == 4]
    if not faces:
        return {'CANCELLED'}
    bmesh.ops.triangulate(bm, faces=faces, quad_method="BEAUTY")
    bmesh.update_edit_mesh(obj.data)
    return {'FINISHED'}


def report_metrics(snapshot):
    return {key: value for key, value in snapshot.items() if key not in {"bad_face_ids", "bad_faces", "bent_face_ids"}}


def main() -> int:
    ap = argparse.ArgumentParser(prog="curiomesh_fix.py")
    ap.add_argument("input")
    ap.add_argument("--object", help="Exact result object name in a .blend file")
    ap.add_argument("--original", default=None)
    ap.add_argument("--original-object", help="Exact source object name in the --original .blend file")
    ap.add_argument("--fix", default=None)
    ap.add_argument("--faces", default=None)
    ap.add_argument("--grow", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--addon", default=None)
    ap.add_argument("--quality-policy", help="JSON file of repair threshold overrides")
    args = ap.parse_args(cm.script_args())
    if args.original_object and not args.original:
        raise ValueError("--original-object requires --original")
    fixes = cm.parse_fixes(args.fix) if args.fix else []
    ids = cm.parse_faces(args.faces) if args.faces else []
    if args.grow is not None and args.grow < 0:
        raise ValueError("--grow must be non-negative")
    policy = quality.Policy.load(args.quality_policy)
    inputs = {Path(path).resolve() for path in (args.input, args.original) if path}
    if any(Path(path).resolve() in inputs for path in (args.out, args.report) if path):
        raise ValueError("Output and report must use paths different from the inputs")

    import bpy

    package = cm.load_addon(args.addon)
    fix_select = cm.sub(package, "fix_select")
    pictures = cm.sub(package, "autoretopo").pictures
    ctx = bpy.context
    obj = cm.load_mesh(args.input, "Result", object_name=args.object, prefer_retopo=True)
    original = None
    if args.original:
        original = cm.load_mesh(args.original, "Original", object_name=args.original_object)
        original.hide_set(True)
        ctx.scene.curiomesh_fix.source = original
    ctx.scene.curiomesh_fix.grow = args.grow if args.grow is not None else (0 if ids else 1)

    if args.faces and (not ids or any(i < 0 or i >= len(obj.data.polygons) for i in ids)):
        raise ValueError("--faces must identify existing face indices")
    ctx.view_layer.update()
    initial = quality.Snapshot(obj, count_bad(obj, fix_select, ctx))
    reference = quality.Snapshot(original) if original is not None else None
    evaluation = quality.Evaluation(pictures, initial, reference, policy)
    accepted = initial
    first = initial.report

    def do_step(key, current, fresh):
        nonlocal accepted
        cm.select_only(obj)
        previous = obj.data
        working = previous.copy()
        obj.data = working
        scoped_ids = ids if fresh else []
        step = {"fix": key, "kept": False, "bad_before": current["bad"],
                "before": report_metrics(current),
                "parameters": {"face_ids": scoped_ids or None, "grow": ctx.scene.curiomesh_fix.grow},
                "rollback_succeeded": None}
        try:
            select_faces(obj, scoped_ids)
            if key == "TRIANGULATE_BENT":
                indices = current["bent_face_ids"]
                if scoped_ids:
                    indices = sorted(set(indices).intersection(scoped_ids))
                status = triangulate_bent(obj, indices)
            else:
                status = bpy.ops.curiomesh.fix_run(how=key)
            bpy.ops.object.mode_set(mode="OBJECT")
            if status != {'FINISHED'}:
                raise RuntimeError("Repair operator cancelled")
            candidate = quality.Snapshot(obj, count_bad(obj, fix_select, ctx))
            changes, failures, warnings = evaluation.compare(accepted, candidate)
            after = candidate.report
            step.update(candidate=report_metrics(after), measurements=changes, reasons=failures,
                        warnings=warnings, bad_after=after["bad"], faces_after=after["faces"],
                        by_kind={kind: after[kind] for kind in cm.DEFECT_KINDS})
            if not failures:
                step["kept"] = True
                accepted = candidate
                if previous.users == 0:
                    bpy.data.meshes.remove(previous)
                return step, after
        except Exception as exc:
            step.update(reasons=["candidate_evaluation_failed"], error=f"{type(exc).__name__}: {exc}")
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        obj.data = previous
        step["rollback_succeeded"] = obj.data == previous and obj.mode == "OBJECT"
        if not step["rollback_succeeded"]:
            raise RuntimeError("Could not restore the last accepted mesh")
        if working.users == 0:
            bpy.data.meshes.remove(working)
        return step, None

    current, steps, rounds = fix_rounds(fixes, first, do_step, stop_after_change=bool(ids))
    warnings = sorted({warning for step in steps if step["kept"] for warning in step["warnings"]})
    if original is None:
        warnings.append("no_original_surface_reference")
    remaining = [f"remaining_{name}" for name in ("bad", "degenerate_faces", "non_manifold_edges", "loose_edges",
                                                  "invalid_uv_corners", "collapsed_uv_faces") if current[name]]
    review = bool(remaining or warnings)
    report = {
        "input": args.input,
        "original": args.original,
        "had_original": bool(args.original),
        "before": report_metrics(first),
        "after": report_metrics(current),
        "quality_policy": asdict(policy),
        "surface_reference": "original" if original is not None else "initial_candidate",
        "quality_verdict": {"status": "review" if review else "pass", "human_review": review,
                            "warnings": warnings, "residual_checks": remaining},
        "bad_face_ids": current["bad_face_ids"],
        "bad_faces": current["bad_faces"],
        "steps": steps,
        "rounds": rounds,
        "improved": cm.better(first, current),
        "ok": True,
    }
    if args.out:
        report["out_file"] = cm.export_mesh(obj, args.out)
    if Path(args.input).suffix.lower() in (".glb", ".gltf"):
        report["warning"] = "glTF stores triangles only: quads were split in two, so counts are on triangles. Use .obj or .blend between steps"
    if fixes and not args.original:
        report["note"] = "no --original given: Flip Inward and Untangle need it, so they could not run"
    cm.write_json(args.report, report)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:
        import traceback

        traceback.print_exc()
        args = cm.script_args()
        path = args[args.index("--report") + 1] if "--report" in args else None
        cm.write_json(path, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        code = 1
    sys.exit(code)
