import argparse
from pathlib import Path
import sys
import bpy
from mathutils import Vector

def main():
    parser = argparse.ArgumentParser(description="Render the CurioMesh comparison cameras in Blender")
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    directory = options.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    bpy.ops.import_scene.gltf(filepath=str(options.input.resolve()))
    mesh = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    coords = [mesh.matrix_world @ Vector(corner) for corner in mesh.bound_box]
    center = sum(coords, Vector()) / len(coords)
    scene = bpy.context.scene
    camera = bpy.data.objects.new("Comparison", bpy.data.cameras.new("Comparison"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.data.lens = 33
    for name,position,energy in (("Key",(2,-4,6),2.3),("Fill",(-4,2,3),1.1),("Rim",(1,4,3),0.8)):
        data = bpy.data.lights.new(name,"SUN")
        data.energy = energy
        light = bpy.data.objects.new(name,data)
        scene.collection.objects.link(light)
        light.rotation_euler = (-Vector(position)).to_track_quat('-Z','Y').to_euler()
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.16,0.16,0.16,1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.55
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.resolution_x=680
    scene.render.resolution_y=680
    scene.render.resolution_percentage=100
    for name, offset in {"front":(0,-3.50,0),"side":(3.50,0,0),"three_quarter":(2.45,-2.45,1.15)}.items():
        camera.location=center+Vector(offset)
        camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
        scene.render.filepath=str(directory/f"blender-{name}.png")
        bpy.ops.render.render(write_still=True)
    return 0


if __name__ == "__main__":
    main()
