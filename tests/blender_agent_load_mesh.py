import hashlib
import importlib
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "curiomesh-retopo" / "scripts"))
cm = importlib.import_module("cm_agent")


def mesh_object(name, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def fixture(path, extra=False):
    source = mesh_object("Lantern", [(0, 1, 2), (0, 2, 3)])
    result = mesh_object("Lantern_Retopo", [(0, 1, 2, 3)])
    fixture_parent = bpy.data.objects.new("Fixture parent", None)
    bpy.context.scene.collection.objects.link(fixture_parent)
    fixture_parent.location.x = 20
    result.parent = fixture_parent
    result.location.x = 4
    objects = {source, result, fixture_parent}
    if extra:
        objects.add(mesh_object("Second_Retopo.001", [(0, 1, 2, 3)]))
    bpy.context.view_layer.update()
    bpy.data.libraries.write(str(path), objects)
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)


def main():
    with tempfile.TemporaryDirectory() as temporary:
        original = Path(temporary) / "retopo.blend"
        ambiguous = Path(temporary) / "multiple.blend"
        fixture(original)
        fixture(ambiguous, extra=True)
        original_digest = hashlib.sha256(original.read_bytes()).digest()
        existing = mesh_object("Lantern_Retopo", [(0, 1, 2)])
        scene = bpy.context.scene
        before = set(scene.objects)

        result = cm.load_mesh(str(original), "Result", prefer_retopo=True)
        assert set(scene.objects) == before | {result}
        assert cm.mesh_stats(result) == {"faces": 1, "quads": 1, "tris": 0, "ngons": 0}
        assert result.parent is None and result.matrix_world.is_identity
        assert sorted(vertex.co.x for vertex in result.data.vertices) == [24, 24, 25, 25]
        assert existing.name == "Lantern_Retopo" and cm.mesh_stats(existing)["tris"] == 1

        reference = cm.load_mesh(str(original), "Original", object_name="Lantern")
        assert cm.mesh_stats(reference)["tris"] == 2
        combined = cm.load_mesh(str(original), "Combined")
        assert cm.mesh_stats(combined)["faces"] == 3
        assert {existing, result, reference, combined} <= set(scene.objects)
        before = set(scene.objects)

        for path, options in [(ambiguous, {"prefer_retopo": True}),
                              (original, {"object_name": "Missing"}),
                              (original, {"object_name": "Fixture parent"})]:
            try:
                cm.load_mesh(str(path), "Unwanted", **options)
            except ValueError as exc:
                assert set(scene.objects) == before
                if path == ambiguous:
                    assert "'Lantern_Retopo'" in str(exc) and "'Second_Retopo.001'" in str(exc) and "--object" in str(exc)
            else:
                raise AssertionError(f"Expected input to be rejected: {options}")

        explicit = cm.load_mesh(str(ambiguous), "Explicit", object_name="Second_Retopo.001", prefer_retopo=True)
        assert cm.mesh_stats(explicit)["quads"] == 1
        assert hashlib.sha256(original.read_bytes()).digest() == original_digest
    print("Multi-mesh selection, legacy joining, parent transform, and retry passed", flush=True)
    return 0


if __name__ == "__main__":
    main()
