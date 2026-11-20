"""Validated conventional LOD and collision operations for isolated static mesh assets."""

import math

import unreal
from editor_toolset.toolsets.static_mesh import StaticMeshTools

COLLISION_MODES = {"auto", "none", "simple", "convex", "complex"}
CATEGORIES = {"architecture", "organic", "hard_surface", "decorative", "unspecified"}
LOD_SCREEN_SIZES = [1.0, 0.42, 0.19]


def subsystem():
    return unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)


def dynamic_mesh(mesh, index=0, render=False):
    target = unreal.DynamicMesh()
    lod = unreal.GeometryScriptMeshReadLOD()
    lod.lod_type = unreal.GeometryScriptLODType.RENDER_DATA if render else unreal.GeometryScriptLODType.SOURCE_MODEL
    lod.lod_index = index
    _, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        mesh, target, unreal.GeometryScriptCopyMeshFromAssetOptions(), lod)
    if outcome != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise RuntimeError(f"Could not read {'render' if render else 'source'} mesh at LOD{index}")
    return target


def same_geometry(first, second):
    options = unreal.GeometryScriptIsSameMeshOptions()
    options.check_normals = True
    options.check_connectivity = True
    options.check_u_vs = True
    _, same, _ = unreal.GeometryScript_MeshComparison.is_same_mesh_as(first, second, options)
    return bool(same)


def render_quality(target):
    queries = unreal.GeometryScript_MeshQueries
    faces = target.get_triangle_count()
    channels = queries.get_num_uv_sets(target)
    sampled = min(512, faces)
    uv_invalid = uv_collapsed = 0
    if channels and sampled:
        for offset in range(sampled):
            triangle = offset * faces // sampled
            u, v, w, valid = queries.get_triangle_u_vs(target, 0, triangle)
            coordinates = (u.x, u.y, v.x, v.y, w.x, w.y)
            if not valid or not all(math.isfinite(value) for value in coordinates):
                uv_invalid += 1
            elif abs((v.x - u.x) * (w.y - u.y) - (v.y - u.y) * (w.x - u.x)) <= 1e-10:
                uv_collapsed += 1
    area, _ = queries.get_mesh_uv_area(target, 0) if channels else (0.0, False)
    return {"triangles": faces, "render_uv_channels": channels, "has_render_normals": queries.get_has_triangle_normals(target),
            "uv_abs_area": area, "uv_samples": sampled, "invalid_uv_samples": uv_invalid,
            "collapsed_uv_samples": uv_collapsed}


def material_paths(mesh):
    return [m.get_path_name() if (m := mesh.get_material(i)) else None for i in range(len(mesh.static_materials))]


def build_lods(mesh, ratios):
    sub = subsystem()
    if len(ratios) != 2 or not 1 > ratios[0] > ratios[1] > 0:
        raise ValueError("LOD ratios must be two descending fractions between zero and one")
    settings = sub.get_nanite_settings(mesh)
    settings.enabled = False
    sub.set_nanite_settings(mesh, settings)
    source = dynamic_mesh(mesh)
    reference = dynamic_mesh(mesh, render=True)
    materials = material_paths(mesh)
    reports, reasons = [], []
    try:
        if StaticMeshTools.generate_lods(mesh, list(ratios)) != 3:
            raise RuntimeError("Unreal did not generate both reduced LODs")
        if not StaticMeshTools.set_lod_thresholds(mesh, LOD_SCREEN_SIZES):
            raise RuntimeError("Unreal could not set the LOD switching thresholds")
        for index in range(3):
            target = dynamic_mesh(mesh, index, render=True)
            entry = render_quality(target)
            entry["vertices"] = sub.get_number_verts(mesh, index)
            entry["source_triangles"] = source.get_triangle_count() if index == 0 else None
            entry["material_slots"] = [sub.get_lod_material_slot(mesh, index, section)
                                       for section in range(mesh.get_num_sections(index))]
            entry["materials"] = [materials[slot] if 0 <= slot < len(materials) else None
                                  for slot in entry["material_slots"]]
            if index:
                opts = unreal.GeometryScriptMeasureMeshDistanceOptions()
                opts.symmetric = True
                _, maximum, _, mean, rms = unreal.GeometryScript_MeshComparison.measure_distances_between_meshes(
                    target, reference, opts)
                entry["deviation_cm"] = {"max": maximum, "mean": mean, "rms": rms}
            reports.append(entry)
        exact_source = same_geometry(source, dynamic_mesh(mesh))
        exact_render = same_geometry(reference, dynamic_mesh(mesh, render=True))
        if not exact_source or not exact_render or reports[0]["triangles"] != source.get_triangle_count():
            reasons.append("lod0_geometry_changed")
        if not reports[0]["triangles"] > reports[1]["triangles"] > reports[2]["triangles"] > 0:
            reasons.append("triangle_counts_not_descending")
        base = reports[0]
        span = mesh.get_bounding_box()
        diagonal = (span.max - span.min).length()
        for index, report in enumerate(reports):
            if not report["has_render_normals"]:
                reasons.append(f"lod{index}_normals_missing")
            if base["render_uv_channels"] and (
                    report["render_uv_channels"] < base["render_uv_channels"] or report["invalid_uv_samples"]
                    or report["collapsed_uv_samples"] > base["collapsed_uv_samples"] + 10
                    or not 0.65 * base["uv_abs_area"] <= report["uv_abs_area"] <= 1.5 * base["uv_abs_area"]):
                reasons.append(f"lod{index}_uv_damage")
            if set(report["material_slots"]) != set(base["material_slots"]) or set(report["materials"]) != set(base["materials"]):
                reasons.append(f"lod{index}_material_assignment_changed")
            if index and report["deviation_cm"]["rms"] > max(0.01, 0.015 * diagonal):
                reasons.append(f"lod{index}_surface_deviation")
        result = {"status": "fail" if reasons else "pass", "reasons": reasons, "ratios": list(ratios),
                  "lod0_source_identical": exact_source, "lod0_render_identical": exact_render, "levels": reports,
                  "screen_sizes": list(sub.get_lod_screen_sizes(mesh)), "nanite_enabled": False}
    except Exception:
        StaticMeshTools.remove_lods(mesh)
        raise
    if reasons:
        result["rollback_succeeded"] = StaticMeshTools.remove_lods(mesh) and sub.get_lod_count(mesh) == 1
    return result


def collision_data(mesh):
    body = mesh.get_editor_property("body_setup")
    geometry = body.get_editor_property("agg_geom")
    hulls = geometry.get_editor_property("convex_elems")
    hull_vertices = []
    if hulls:
        target = unreal.DynamicMesh()
        scenes = unreal.GeometryScript_SceneUtils
        _, _, outcome = scenes.copy_collision_meshes_from_object(mesh, target, False)
        if outcome == unreal.GeometryScriptOutcomePins.SUCCESS:
            _, parts = unreal.GeometryScript_MeshDecomposition.split_mesh_by_components(target, scenes.create_dynamic_mesh_pool())
            hull_vertices = [unreal.GeometryScript_MeshQueries.get_num_vertex_i_ds(part) for part in parts]
        if len(hull_vertices) != len(hulls):
            hull_vertices = None
    primitives = subsystem().get_simple_collision_count(mesh)
    return {"simple_shapes": primitives + len(hulls), "primitive_shapes": primitives,
            "convex_hulls": len(hulls), "hull_vertex_counts": hull_vertices,
            "complexity": str(body.get_editor_property("collision_trace_flag")),
            "collision_lod": int(mesh.get_editor_property("lod_for_collision"))}


def build_collision(mesh, requested, category):
    if requested not in COLLISION_MODES or category not in CATEGORIES:
        raise ValueError("Unsupported collision mode or asset category")
    mode = ("none" if category == "decorative" else "convex") if requested == "auto" else requested
    sub = subsystem()
    sub.remove_collisions(mesh)
    mesh.set_editor_property("lod_for_collision", 0)
    body = mesh.get_editor_property("body_setup")
    body.set_editor_property("collision_trace_flag", unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
                             if mode == "complex" else unreal.CollisionTraceFlag.CTF_USE_SIMPLE_AND_COMPLEX)
    succeeded = True
    if mode == "convex":
        count = {"architecture": 16, "organic": 5, "hard_surface": 8}.get(category, 8)
        succeeded = sub.set_convex_decomposition_collisions(mesh, count, 24, 75000)
    elif mode == "simple":
        succeeded = sub.add_simple_collisions(mesh, unreal.ScriptCollisionShapeType.BOX) >= 0
    body = mesh.get_editor_property("body_setup")
    instance = body.get_editor_property("default_instance")
    instance.set_editor_property("collision_profile_name", "NoCollision" if mode == "none" else "BlockAll")
    instance.set_editor_property("collision_enabled", unreal.CollisionEnabled.NO_COLLISION if mode == "none"
                                 else unreal.CollisionEnabled.QUERY_AND_PHYSICS)
    body.set_editor_property("default_instance", instance)
    data = collision_data(mesh)
    if mode == "convex":
        succeeded = succeeded and data["convex_hulls"] > 0
    elif mode == "simple":
        succeeded = succeeded and data["primitive_shapes"] > 0
    return {**data, "requested": requested, "mode": mode, "status": "untested" if succeeded else "fail",
            "warnings": [] if succeeded else ["collision_geometry_generation_failed"]}


def trace_probes(world, actor, specs):
    trace_channel = unreal.TraceTypeQuery.TRACE_TYPE_QUERY1
    debug = unreal.DrawDebugTrace.NONE
    rows = []
    for spec in specs:
        name = spec["name"]
        coords = spec["start_cm"], spec["end_cm"]
        if any(len(v) != 3 or not all(type(x) in (int, float) and math.isfinite(x) for x in v) for v in coords):
            raise ValueError(f"Invalid coordinates for {name}")
        if type(spec["blocked"]) is not bool:
            raise ValueError(f"Specify a boolean blocked prediction for {name}")
        origin = actor.get_actor_location()
        start, end = (origin + unreal.Vector(*v) for v in coords)
        line = unreal.SystemLibrary.line_trace_single(world, start, end, trace_channel, False, [], debug)
        radius, half_height = spec.get("radius_cm", 3), spec.get("half_height_cm", 5)
        if not 0 < radius <= half_height <= 1000:
            raise ValueError(f"Invalid capsule size for {name}")
        capsule = unreal.SystemLibrary.capsule_trace_single(
            world, start, end, radius, half_height, trace_channel, False, [], debug)
        blocker = bool(line)
        sweep_blocker = bool(capsule)
        matches = blocker == spec["blocked"] and sweep_blocker == spec.get("capsule_blocked", spec["blocked"])
        rows.append({**spec, "line_blocked": blocker, "capsule_blocked_observed": sweep_blocker, "pass": matches})
    return {"status": ("pass" if all(row["pass"] for row in rows) else "fail") if rows else "untested", "probes": rows}
