"""Register the CurioMesh asset-validation tools in an Unreal Editor MCP session."""

import json
import os
from pathlib import Path
import re

import unreal
from editor_toolset.toolsets.static_mesh import StaticMeshTools
from editor_toolset.toolsets.asset import import_asset
from toolset_registry import tool_call
from toolset_registry._registry_interface import register_toolset_classes

DESTINATION = "/Game/Developer/CurioMeshBUHTests"
EXPORT_DIR = Path(os.environ["CURIOMESH_VALIDATION_EXPORTS"]).resolve()


def package_for(run_id):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", run_id):
        raise ValueError("Use an alphanumeric run ID starting with a letter")
    return f"{DESTINATION}/{run_id}"


def vector(v):
    return [v.x, v.y, v.z]


def texture_parameters(material):
    if not isinstance(material, unreal.MaterialInstanceConstant):
        return {}
    library = unreal.MaterialEditingLibrary
    return {str(name): texture.get_path_name() for name in library.get_texture_parameter_names(material)
            if (texture := library.get_material_instance_texture_parameter_value(material, name))}


def validation_mesh(run_id, mesh_path):
    if not mesh_path.startswith(package_for(run_id) + "/") or ".." in mesh_path:
        raise ValueError("The mesh must be from this validation run")
    mesh = unreal.load_asset(mesh_path)
    if not isinstance(mesh, unreal.StaticMesh):
        raise ValueError("The asset must be a static mesh")
    return mesh


def mesh_report(mesh):
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    lod_count = subsystem.get_lod_count(mesh)
    box = mesh.get_bounding_box()
    materials = []
    for index, slot in enumerate(mesh.static_materials):
        material = mesh.get_material(index)
        parameters = texture_parameters(material)
        textures = (list(parameters.values()) if isinstance(material, unreal.MaterialInstanceConstant) else
                    [texture.get_path_name() for texture in unreal.MaterialEditingLibrary.get_used_textures(material)]
                    if material else [])
        materials.append({
            "slot": str(slot.material_slot_name),
            "material": material.get_path_name() if material else None,
            "textures": sorted(set(textures)),
            "texture_parameters": parameters,
        })
    source = None
    asset_utils = getattr(unreal, "GeometryScript_AssetUtils", None)
    if asset_utils is not None:
        target = unreal.DynamicMesh()
        lod = unreal.GeometryScriptMeshReadLOD()
        _, outcome = asset_utils.copy_mesh_from_static_mesh(mesh, target, unreal.GeometryScriptCopyMeshFromAssetOptions(), lod)
        if outcome == unreal.GeometryScriptOutcomePins.SUCCESS:
            queries = unreal.GeometryScript_MeshQueries
            source = {"triangles": target.get_triangle_count(), "components": queries.get_num_connected_components(target)}
            options = unreal.GeometryScriptWeldEdgesOptions()
            options.tolerance = 0.001
            unreal.GeometryScript_MeshRepair.weld_mesh_edges(target, options)
            source["components_after_diagnostic_weld"] = queries.get_num_connected_components(target)
    return {
        "path": mesh.get_path_name(),
        "bounds": {"min_cm": vector(box.min), "max_cm": vector(box.max)},
        "lods": [
            {"triangles": mesh.get_num_triangles(lod), "vertices": subsystem.get_number_verts(mesh, lod),
             "uv_channels": subsystem.get_num_uv_channels(mesh, lod)} for lod in range(lod_count)
        ],
        "materials": materials,
        "simple_collision_shapes": subsystem.get_simple_collision_count(mesh),
        "collision_complexity": str(subsystem.get_collision_complexity(mesh)),
        "nanite_enabled": bool(subsystem.get_nanite_settings(mesh).enabled),
        "mesh_description": source,
    }


@unreal.uclass()
class CurioMeshValidation(unreal.ToolsetDefinition):
    """Import and inspect CurioMesh exports in the project's isolated validation directory."""

    @tool_call
    @staticmethod
    def import_mesh(run_id: str, filename: str) -> str:
        """Import a GLB or FBX from the configured validation export directory."""
        source = (EXPORT_DIR / filename).resolve()
        if source.parent != EXPORT_DIR or source.suffix.lower() not in {".glb", ".fbx"} or not source.is_file():
            raise ValueError("Supply a GLB or FBX file in the validation export directory")
        folder = f"{package_for(run_id)}/{source.suffix[1:].upper()}"
        if source.suffix.lower() == ".fbx":
            imported = StaticMeshTools.import_file(folder, source.stem, str(source), True, True, True)
        else:
            imported = import_asset(folder, source.stem, str(source))
        assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        assets.save_loaded_assets(imported, only_if_is_dirty=False)
        meshes = [mesh_report(obj) for obj in imported if isinstance(obj, unreal.StaticMesh)]
        textures = [{"path": texture.get_path_name(), "width": texture.blueprint_get_size_x(),
                     "height": texture.blueprint_get_size_y(), "srgb": texture.get_editor_property("srgb")}
                    for texture in imported if isinstance(texture, unreal.Texture2D)]
        if not meshes:
            raise RuntimeError("The import produced no static mesh")
        return json.dumps({"engine": unreal.SystemLibrary.get_engine_version(), "format": source.suffix[1:],
                           "meshes": meshes, "textures": textures,
                           "assets": [obj.get_path_name() for obj in imported]})

    @tool_call
    @staticmethod
    def inspect_mesh(run_id: str, mesh_path: str) -> str:
        """Inspect source and render geometry for an imported validation mesh."""
        return json.dumps(mesh_report(validation_mesh(run_id, mesh_path)))

    @tool_call
    @staticmethod
    def make_fbx_lod_variant(run_id: str, fbx_path: str, glb_path: str) -> str:
        """Clone the FBX mesh, apply the verified GLB material, and generate three Unreal LODs."""
        folder = package_for(run_id)
        fbx, glb = validation_mesh(run_id, fbx_path), validation_mesh(run_id, glb_path)
        if fbx.get_num_sections(0) != 1 or glb.get_num_sections(0) != 1:
            raise ValueError("This material comparison requires a single section on each mesh")
        assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        variant_path = f"{folder}/FBX_LODs/Lantern"
        if assets.does_asset_exist(variant_path):
            raise ValueError("The validation variant already exists")
        variant = assets.duplicate_asset(fbx.get_path_name(), variant_path)
        if not variant:
            raise RuntimeError("Could not duplicate the isolated FBX mesh")
        variant.set_material(0, glb.get_material(0))
        if StaticMeshTools.generate_lods(variant, [0.5, 0.25]) != 3:
            raise RuntimeError("Unreal did not generate both reduced LODs")
        assets.save_loaded_asset(variant)
        return json.dumps(mesh_report(variant))

    @tool_call
    @staticmethod
    def prepare_preview(run_id: str, mesh_path: str) -> str:
        """Create a blank isolated validation level and preview one imported static mesh."""
        folder = package_for(run_id)
        mesh = validation_mesh(run_id, mesh_path)
        levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        level = f"{folder}/Preview_{mesh.get_outermost().get_name().split('/')[-2]}"
        if assets.does_asset_exist(level):
            raise ValueError("Use a unique run ID for each preview level")
        if not levels.new_level(level):
            raise RuntimeError("Could not create the validation level")
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actor = actors.spawn_actor_from_object(mesh, unreal.Vector(0, 0, 0))
        actor.set_actor_label("CurioMesh Balanced test asset")
        for pitch, yaw, intensity in ((-48, 135, 4.0), (-25, -20, 1.8), (-30, -120, 2.3)):
            light = actors.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(),
                                                  unreal.Rotator(roll=0, pitch=pitch, yaw=yaw))
            comp = light.get_editor_property("directional_light_component")
            comp.set_intensity(intensity)
            comp.set_editor_property("cast_shadows", False)
        actors.clear_actor_selection_set()
        levels.editor_set_game_view(True)
        levels.save_current_level()
        origin, extent = actor.get_actor_bounds(False)
        return json.dumps({"level": level, "center_cm": vector(origin), "extent_cm": vector(extent),
                           "actor_pivot_cm": vector(actor.get_actor_location())})

    @tool_call
    @staticmethod
    def capture_preview(run_id: str, forced_lod: int) -> str:
        """Render comparison angles from the active isolated preview level."""
        levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        level = levels.get_current_level().get_outermost().get_name()
        if not level.startswith(package_for(run_id) + "/Preview_"):
            raise ValueError("Load an isolated CurioMesh preview level before capturing")
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actor = next(obj for obj in actors.get_all_level_actors() if isinstance(obj, unreal.StaticMeshActor))
        component = actor.static_mesh_component
        lod_count = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).get_lod_count(component.static_mesh)
        if not -1 <= forced_lod < lod_count:
            raise ValueError("The requested LOD is unavailable")
        center, _ = actor.get_actor_bounds(False)
        capture = actors.spawn_actor_from_class(unreal.SceneCapture2D, center)
        capture_component = capture.get_component_by_class(unreal.SceneCaptureComponent2D)
        rendering = unreal.RenderingLibrary
        target = rendering.create_render_target2d(capture, 680, 680, unreal.TextureRenderTargetFormat.RTF_RGBA8)
        out = EXPORT_DIR / "preview_renders"
        out.mkdir(exist_ok=True)
        previous = component.get_editor_property("forced_lod_model")
        paths = []
        try:
            component.set_forced_lod_model(forced_lod + 1)
            capture_component.texture_target = target
            capture_component.capture_source = unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR
            capture_component.capture_every_frame = False
            capture_component.capture_on_movement = False
            capture_component.fov_angle = 57.22
            post = capture_component.post_process_settings
            for prop, value in (("motion_blur_amount", 0.0), ("auto_exposure_min_brightness", 0.0),
                                ("auto_exposure_max_brightness", 0.0), ("auto_exposure_bias", -0.8)):
                post.set_editor_property("override_" + prop, True)
                post.set_editor_property(prop, value)
            capture_component.post_process_settings = post
            capture_component.post_process_blend_weight = 1.0
            distance = 1.0 if forced_lod < 1 else (1.65 if forced_lod == 1 else 2.4)
            for angle, offset in (("front", (0, 350, 0)), ("side", (350, 0, 0)),
                                  ("three_quarter", (245, 245, 115))):
                location = center + unreal.Vector(*(coord * distance for coord in offset))
                rotation = unreal.MathLibrary.find_look_at_rotation(location, center)
                capture.set_actor_location_and_rotation(location, rotation, False, False)
                capture_component.capture_scene()
                name = f"{run_id}-{level.rsplit('/', 1)[-1]}-{forced_lod}-{angle}.png"
                rendering.export_render_target(capture, target, str(out), name)
                path = out / name
                if not path.is_file() or path.stat().st_size < 100:
                    raise RuntimeError(f"The render target did not produce {name}")
                paths.append(str(path))
        finally:
            component.set_forced_lod_model(previous)
            actors.destroy_actor(capture)
        return json.dumps({"lod": forced_lod, "renders": paths})


errors = register_toolset_classes([CurioMeshValidation])
if errors:
    raise RuntimeError("; ".join(errors))
unreal.EditorPythonScripting.set_keep_python_script_alive(True)
unreal.log("CurioMesh MCP tools registered")
