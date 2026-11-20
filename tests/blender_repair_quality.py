"""Exercise the repair gate in Blender with geometry, UVs, and material edits."""

from importlib import import_module
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import bpy

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/curiomesh-retopo/scripts"
sys.path.insert(0, str(SCRIPTS))
cm = import_module("cm_agent")
quality = import_module("cm_quality")
fix = import_module("curiomesh_fix")


def model(name, points, faces, materials):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(points, [], faces)
    for material in materials:
        mesh.materials.append(material)
    uv = mesh.uv_layers.new(name="TextureUV")
    for polygon in mesh.polygons:
        for offset, index in enumerate(polygon.loop_indices):
            uv.uv[index].vector = [(0, 0), (1, 0), (1, 1), (0, 1)][offset]
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def main():
    import bmesh
    import numpy as np

    package = cm.load_addon(os.environ["CURIOMESH_TEST_ADDON"])
    scene = bpy.context
    pictures = cm.sub(package, "autoretopo").pictures
    checks = cm.sub(package, "fix_select")
    material = bpy.data.materials.new("Paint")
    material.use_nodes = True
    image = bpy.data.images.new("PaintMap", width=8, height=8)
    material.node_tree.nodes.new("ShaderNodeTexImage").image = image
    second = bpy.data.materials.new("Metal")

    points = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0), (1, 1, 0.15),
              (3, 0, 0), (4, 0, 0), (3, 1, 0)]
    faces = [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4), (5, 6, 7)]
    base = model("Intentional shells", points, faces, [material, second])
    base.data.polygons[4].material_index = 1
    defects = fix.count_bad(base, checks, scene)
    initial = quality.Snapshot(base, {**defects, "bad": 1, "bent": 1})
    evaluation = quality.Evaluation(pictures, initial, None, quality.Policy())

    unchanged = quality.Snapshot(base, defects)
    changes, failures, warnings = evaluation.compare(initial, unchanged)
    assert not failures and not warnings, (failures, warnings)
    assert unchanged.report["components"] == 2 and unchanged.report["open_edges"] == 7
    assert unchanged.report["texture_bindings"] and unchanged.report["uv_layers"] == ["TextureUV"]
    edge = np.array([[(0, 0, 0), (2, 0, 0)]], dtype=float)
    split = np.array([[(0, 0, 0), (1, 0, 0)], [(1, 0, 0), (2, 0, 0)]], dtype=float)
    assert quality._new_boundary(edge, split, 1e-6) == 0

    holed = model("Missing sector", points, faces[1:], [material, second])
    holed.data.polygons[3].material_index = 1
    candidate = quality.Snapshot(holed, fix.count_bad(holed, checks, scene))
    changes, failures, _ = evaluation.compare(initial, candidate)
    assert changes["new_boundary_segments"] == 2 and "new_open_boundary" in failures, (changes, failures)

    before = base.data
    trial = before.copy()
    base.data = trial
    trial.polygons[4].material_index = 0
    trial.uv_layers[0].uv[0].vector = (0.1, 0.1)
    changed = quality.Snapshot(base, defects)
    changes, failures, _ = evaluation.compare(initial, changed)
    assert {"uvs_changed_on_untouched_faces", "face_material_assignments_changed"} <= set(failures), failures
    base.data = before
    assert base.data == before and base.data.polygons[4].material_index == 1
    assert tuple(base.data.uv_layers[0].uv[0].vector) == (0.0, 0.0)

    irregular = model("Non-manifold", points + [(1, 0, 0)], faces + [(0, 1, 8), (1, 0, 3)],
                      [material, second])
    irregular_snapshot = quality.Snapshot(irregular, fix.count_bad(irregular, checks, scene))
    assert irregular_snapshot.report["non_manifold_edges"] > 0
    assert irregular_snapshot.report["degenerate_faces"] > 0
    _, failures, _ = evaluation.compare(initial, irregular_snapshot)
    assert {"non_manifold_edges_increased", "degenerate_faces_increased"} <= set(failures)

    twisted = model("Bent", [(0, 0, 0), (1, 0, 0), (1, 1, 2), (0, 1, 0)],
                    [(0, 1, 2, 3)], [material])
    d = fix.count_bad(twisted, checks, scene)
    assert d["bent"] == 1, d

    def cancel_after_edit(obj, indices):
        assert indices == [0]
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts[0].co.x = 199
        bmesh.update_edit_mesh(obj.data)
        return {'CANCELLED'}

    with TemporaryDirectory() as directory:
        path, report = Path(directory) / "input.blend", Path(directory) / "quality.json"
        bpy.data.libraries.write(str(path), {twisted})
        argv = ["blender", "--", str(path), "--fix", "TRIANGULATE_BENT", "--report", str(report)]
        with patch.object(cm, "load_addon", return_value=package), patch.object(sys, "argv", argv):
            with patch.object(fix, "triangulate_bent", side_effect=cancel_after_edit):
                assert fix.main() == 0
        data = json.loads(report.read_text())
        assert data["before"] == data["after"] and data["steps"][0]["rollback_succeeded"]
        assert bpy.data.objects["Result"].data.vertices[0].co.x == 0

    print("Repair gate checked open boundaries, components, UV seams, material rollback, non-manifold and bent faces", flush=True)
    return 0


if __name__ == "__main__":
    main()
