"""Create two small textured classification fixtures without external assets."""

import argparse
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils.noise import noise_vector


def clear():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def rock():
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=0.65)
    obj = bpy.context.object
    for vertex in obj.data.vertices:
        p = vertex.co
        noise = noise_vector(p * 3.6, noise_basis="PERLIN_NEW")[0]
        p *= 1 + 0.23 * noise
        p.x *= 1.24
        p.y *= 0.91
        p.z *= 0.72
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    return obj


def chest():
    components = [
        ((0, 0, -0.09), (1.20, 0.72, 0.58), 0.035),
        ((0, 0, 0.25), (1.27, 0.76, 0.19), 0.055),
        ((0, -0.38, 0.08), (0.12, 0.055, 0.19), 0.012),
        ((-0.36, 0.0, 0.345), (0.095, 0.71, 0.03), 0.010),
        ((0.36, 0.0, 0.345), (0.095, 0.71, 0.03), 0.010),
    ]
    for position, dimensions, bevel in components:
        bpy.ops.mesh.primitive_cube_add(size=1, location=position)
        obj = bpy.context.object
        obj.dimensions = dimensions
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        modifier = obj.modifiers.new("Edge bevel", "BEVEL")
        modifier.width = bevel
        modifier.segments = 5
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        modifier = obj.modifiers.new("Fixture tessellation", "SUBSURF")
        modifier.subdivision_type = "SIMPLE"
        modifier.levels = 1
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.join()
    return bpy.context.object


def painted_material(name, rock_like):
    n = 128
    x, y = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    pattern = (0.72 + 0.15*np.sin(y*87+np.sin(x*18)*3)
               + 0.10*np.sin(x*58)*np.cos(y*31))
    color = (0.29, 0.43, 0.37) if rock_like else (0.58, 0.24, 0.07)

    def image(suffix, rgb, linear=False):
        tex = bpy.data.images.new(f"{name}_{suffix}", width=n, height=n)
        if linear:
            tex.colorspace_settings.name = "Non-Color"
        pixels = np.ones((n, n, 4), dtype=np.float32)
        pixels[:, :, :3] = rgb
        tex.pixels.foreach_set(pixels.reshape(-1))
        tex.pack()
        return tex

    base = image("BaseColor", pattern[:, :, None] * np.array(color))
    packed = np.stack((np.ones_like(pattern), 0.48 + 0.35*pattern,
                       0.08 + 0.1*(1-pattern)), axis=2)
    roughness = image("MetallicRoughness", packed, linear=True)
    mat = bpy.data.materials.new(name + "_Material")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    diffuse = nodes.new("ShaderNodeTexImage")
    diffuse.image = base
    links.new(diffuse.outputs["Color"], bsdf.inputs["Base Color"])
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = roughness
    separate = nodes.new("ShaderNodeSeparateColor")
    links.new(tex.outputs["Color"], separate.inputs["Color"])
    links.new(separate.outputs["Green"], bsdf.inputs["Roughness"])
    links.new(separate.outputs["Blue"], bsdf.inputs["Metallic"])
    return mat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    options.output.mkdir(parents=True, exist_ok=True)
    for name, make in (("RockFixture", rock), ("ChestFixture", chest)):
        out = options.output / f"{name}.glb"
        if out.exists():
            raise FileExistsError(out)
        clear()
        obj = make()
        obj.name = name
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(island_margin=0.01)
        bpy.ops.object.mode_set(mode="OBJECT")
        obj.data.materials.clear()
        obj.data.materials.append(painted_material(name, name == "RockFixture"))
        bpy.ops.export_scene.gltf(filepath=str(out), use_selection=True)
    return 0


if __name__ == "__main__":
    main()
