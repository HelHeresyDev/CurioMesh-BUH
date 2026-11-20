"""Register the CurioMesh asset-validation tools in an Unreal Editor MCP session."""

import json
from hashlib import sha256
import os
from pathlib import Path
import re
import sys

import unreal
from editor_toolset.toolsets.static_mesh import StaticMeshTools
from editor_toolset.toolsets.asset import import_asset
from toolset_registry import tool_call
from toolset_registry._registry_interface import register_toolset_classes

sys.path.insert(0, str(Path(__file__).resolve().parent))
import static_asset as runtime  # noqa: E402

DESTINATION = "/Game/Developer/CurioMeshBUHTests"
EXPORT_DIR = Path(os.environ["CURIOMESH_VALIDATION_EXPORTS"]).resolve()


def package_for(run_id):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", run_id):
        raise ValueError("Use an alphanumeric run ID starting with a letter")
    return f"{DESTINATION}/{run_id}"


def export_file(filename, expected_sha256=""):
    source = (EXPORT_DIR / filename).resolve()
    if source.parent != EXPORT_DIR or source.suffix.lower() not in {".glb", ".fbx"} or not source.is_file():
        raise ValueError("Supply a GLB or FBX file in the validation export directory")
    if expected_sha256:
        with source.open("rb") as handle:
            if sha256(handle.read()).hexdigest() != expected_sha256:
                raise ValueError("The Unreal export directory contains a different file with this name")
    return source


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


def preview_world(run_id):
    editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    pie = editor.get_game_world()
    world = pie if pie else editor.get_editor_world()
    package = world.get_outermost().get_name()
    folder, name = package.rsplit("/", 1)
    level = f"{folder}/{re.sub(r'^UEDPIE_[0-9]+_', '', name)}"
    if not level.startswith(package_for(run_id) + "/Preview_"):
        raise ValueError("Load the validation run's preview level first")
    return world, level, pie is not None


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
        source = export_file(filename)
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
    def build_static_asset(run_id: str, filename: str, ratios: list[float], collision: str, category: str,
                           expected_sha256: str = "") -> str:
        """Build conventional LODs and collision directly on an isolated textured GLB import."""
        if Path(filename).suffix.lower() != ".glb":
            raise ValueError("Production validation requires a textured GLB")
        if collision not in runtime.COLLISION_MODES or category not in runtime.CATEGORIES:
            raise ValueError("Unsupported collision mode or asset category")
        assets = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
        if assets.does_directory_exist(package_for(run_id)):
            raise ValueError("Use a new run ID to preserve prior results")
        export_file(filename, expected_sha256)
        imported = json.loads(CurioMeshValidation.import_mesh(run_id, filename))
        if len(imported["meshes"]) != 1:
            raise ValueError("The GLB must contain one static mesh")
        mesh = validation_mesh(run_id, imported["meshes"][0]["path"])
        report = {"import": imported}
        if ratios:
            report["lods"] = runtime.build_lods(mesh, ratios)
            if report["lods"]["status"] == "fail":
                assets.save_loaded_asset(mesh)
                return json.dumps(report)
        report["collision"] = runtime.build_collision(mesh, collision, category)
        assets.set_metadata_tag(mesh, "CurioMeshCollisionMode", report["collision"]["mode"])
        if not assets.save_loaded_asset(mesh):
            raise RuntimeError("Could not save the isolated static mesh")
        report["mesh"] = mesh_report(mesh)
        return json.dumps(report)

    @tool_call
    @staticmethod
    def validate_collision(run_id: str, probes_json: str) -> str:
        """Run explicit line traces and capsule sweeps in an isolated preview or its PIE world."""
        world, level, pie = preview_world(run_id)
        specs = json.loads(probes_json)
        if not isinstance(specs, list) or len(specs) > 32:
            raise ValueError("Collision probes must be a JSON list of up to 32 checks")
        actor, = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor)
        component = actor.static_mesh_component
        count = component.static_mesh.get_num_lods()
        previous = component.get_editor_property("forced_lod_model")
        try:
            report = runtime.trace_probes(world, actor, specs)
            report["forced_lods"] = {}
            if specs and count > 1:
                for index in range(count):
                    component.set_forced_lod_model(index + 1)
                    report["forced_lods"][index] = runtime.trace_probes(world, actor, specs)["status"]
                if "fail" in report["forced_lods"].values():
                    report["status"] = "fail"
        finally:
            component.set_forced_lod_model(previous)
        return json.dumps({**report, "world": "pie" if pie else "editor", "level": level})

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
        actor.set_actor_label("CurioMesh validation mesh")
        mode = assets.get_metadata_tag(mesh, "CurioMeshCollisionMode")
        if mode:
            actor.static_mesh_component.set_collision_profile_name("NoCollision" if mode == "none" else "BlockAll")
        for pitch, yaw, intensity in ((-48, 135, 4.0), (-25, -20, 1.8), (-30, -120, 2.3)):
            light = actors.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(),
                                                  unreal.Rotator(roll=0, pitch=pitch, yaw=yaw))
            comp = light.get_editor_property("directional_light_component")
            comp.set_intensity(intensity)
            comp.set_editor_property("cast_shadows", False)
        origin, extent = actor.get_actor_bounds(False)
        capture = actors.spawn_actor_from_class(unreal.SceneCapture2D, origin)
        sensor = capture.get_component_by_class(unreal.SceneCaptureComponent2D)
        sensor.capture_every_frame = False
        sensor.capture_on_movement = False
        viewpoint = origin + unreal.Vector(245, 245, 115)
        rotation = unreal.MathLibrary.find_look_at_rotation(viewpoint, origin)
        actors.spawn_actor_from_class(unreal.PlayerStart, viewpoint, rotation)
        actors.clear_actor_selection_set()
        levels.editor_set_game_view(True)
        if not levels.save_current_level():
            raise RuntimeError("Could not save the isolated preview level")
        return json.dumps({"level": level, "center_cm": vector(origin), "extent_cm": vector(extent),
                           "actor_pivot_cm": vector(actor.get_actor_location())})

    @tool_call
    @staticmethod
    def capture_preview(run_id: str, forced_lod: int) -> str:
        """Render comparison angles at the default distances."""
        return CurioMeshValidation.capture_preview_at_distance(run_id, forced_lod, 0.0)

    @tool_call
    @staticmethod
    def capture_preview_at_distance(run_id: str, forced_lod: int, distance_scale: float) -> str:
        """Render comparison angles from the active isolated preview level."""
        world, level, pie = preview_world(run_id)
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actor, = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor)
        component = actor.static_mesh_component
        lod_count = component.static_mesh.get_num_lods()
        if not -1 <= forced_lod < lod_count or not 0 <= distance_scale <= 20:
            raise ValueError("The requested LOD is unavailable")
        center, _ = actor.get_actor_bounds(False)
        captures = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.SceneCapture2D)
        if not captures and pie:
            raise ValueError("Create a preview with the current toolset before capturing in PIE")
        capture = captures[0] if captures else actors.spawn_actor_from_class(unreal.SceneCapture2D, center)
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
            distance = distance_scale or (1.0 if forced_lod < 1 else (1.65 if forced_lod == 1 else 2.4))
            for angle, offset in (("front", (0, 350, 0)), ("side", (350, 0, 0)),
                                  ("three_quarter", (245, 245, 115))):
                location = center + unreal.Vector(*(coord * distance for coord in offset))
                rotation = unreal.MathLibrary.find_look_at_rotation(location, center)
                capture.set_actor_location_and_rotation(location, rotation, False, False)
                capture_component.capture_scene()
                name = f"{run_id}-{level.rsplit('/', 1)[-1]}-{forced_lod}-d{distance:.2f}-{angle}.png"
                rendering.export_render_target(capture, target, str(out), name)
                path = out / name
                if not path.is_file() or path.stat().st_size < 100:
                    raise RuntimeError(f"The render target did not produce {name}")
                paths.append(str(path))
        finally:
            component.set_forced_lod_model(previous)
            capture_component.texture_target = None
            if not captures:
                actors.destroy_actor(capture)
        return json.dumps({"lod": forced_lod, "distance_scale": distance, "world": "pie" if pie else "editor", "renders": paths})


errors = register_toolset_classes([CurioMeshValidation])
if errors:
    raise RuntimeError("; ".join(errors))
unreal.EditorPythonScripting.set_keep_python_script_alive(True)
unreal.log("CurioMesh MCP tools registered")
