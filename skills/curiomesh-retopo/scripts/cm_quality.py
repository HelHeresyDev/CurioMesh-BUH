"""Measure and compare repair candidates in the same world-space frame."""

from collections import Counter, defaultdict
from dataclasses import dataclass, fields
import json
import math


@dataclass(frozen=True)
class Policy:
    boundary_tolerance_ratio: float = 0.0001
    surface_p95_growth_ratio: float = 0.0005
    surface_max_growth_ratio: float = 0.002
    silhouette_growth_pct: float = 0.15
    silhouette_step_pct: float = 0.35
    bounds_shift_ratio: float = 0.002
    allowed_component_change: int = 0

    @classmethod
    def load(cls, path):
        if not path:
            return cls()
        with open(path, encoding="utf-8") as handle:
            values = json.load(handle)
        if not isinstance(values, dict):
            raise ValueError("The quality policy must be a JSON object")
        policy = cls(**values)
        for field in fields(policy):
            value = getattr(policy, field.name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{field.name} must be finite and non-negative")
        if type(policy.allowed_component_change) is not int:
            raise ValueError("allowed_component_change must be an integer")
        return policy


def decide(before, candidate, changes, policy):
    failures, warnings = [], []
    if candidate["bad"] >= before["bad"]:
        failures.append("bad_face_count_did_not_decrease")
    for key in ("degenerate_faces", "non_manifold_edges", "loose_edges", "intersection_pairs",
                "invalid_uv_corners", "collapsed_uv_faces"):
        if candidate[key] > before[key]:
            failures.append(f"{key}_increased")
    for key in ("inward", "sunk", "crossing", "overlap", "flat", "duplicate", "folded_over"):
        if candidate[key] > before[key]:
            failures.append(f"{key}_faces_increased")
    if candidate["bent"] > before["bent"]:
        warnings.append("bent_faces_increased")
    if candidate["triangles"] > before["triangles"]:
        warnings.append("triangle_faces_increased")
    if abs(candidate["components"] - before["components"]) > policy.allowed_component_change:
        failures.append("connected_component_count_changed")
    if changes["new_boundary_segments"]:
        failures.append("new_open_boundary")
    elif candidate["open_edges"] != before["open_edges"]:
        warnings.append("boundary_edge_count_changed_on_existing_boundary")
    for key in ("uv_layers", "material_slots", "texture_bindings"):
        if candidate[key] != before[key]:
            failures.append(f"{key}_changed")
    if changes["changed_uv_faces"]:
        failures.append("uvs_changed_on_untouched_faces")
    if changes["changed_material_faces"]:
        failures.append("face_material_assignments_changed")
    for measured, limit, failure in (
        ("surface_p95_growth_ratio", policy.surface_p95_growth_ratio, "surface_p95_worsened"),
        ("surface_max_growth_ratio", policy.surface_max_growth_ratio, "surface_max_worsened"),
        ("silhouette_growth_pct", policy.silhouette_growth_pct, "source_silhouette_worsened"),
        ("silhouette_step_pct", policy.silhouette_step_pct, "candidate_silhouette_changed"),
        ("bounds_shift_ratio", policy.bounds_shift_ratio, "bounds_shifted"),
    ):
        if changes[measured] > limit:
            failures.append(failure)
    return failures, warnings


def _groups(items, edges):
    parent = {item: item for item in items}

    def root(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for a, b in edges:
        parent[root(a)] = root(b)
    return len({root(item) for item in parent})


def _position_key(point):
    return tuple(round(float(axis), 7) for axis in point)


def _mesh_arrays(obj):
    import numpy as np

    mesh = obj.data
    local = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", local)
    matrix = np.asarray(obj.matrix_world)
    points = local.reshape((-1, 3)).astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]
    if not len(points) or not np.isfinite(points).all() or not len(mesh.polygons):
        raise ValueError("Repair quality requires a non-empty mesh with finite positions")
    faces = [tuple(polygon.vertices) for polygon in mesh.polygons]
    return points, faces


def _texture_bindings(mesh):
    bindings = []
    for slot, material in enumerate(mesh.materials):
        if not material or not material.node_tree:
            continue
        seen = set()
        pending = [(material.node_tree, "")]
        while pending:
            tree, prefix = pending.pop()
            if tree in seen:
                continue
            seen.add(tree)
            for node in tree.nodes:
                if node.type == "GROUP" and node.node_tree:
                    pending.append((node.node_tree, prefix + node.name + "/"))
                image = getattr(node, "image", None)
                if image:
                    outputs = sorted((link.from_socket.name, link.to_node.name, link.to_socket.name)
                                     for socket in node.outputs for link in socket.links)
                    bindings.append((slot, prefix + node.name, image.name_full,
                                     tuple(image.size), image.colorspace_settings.name, tuple(outputs)))
    return sorted(bindings)


class Snapshot:
    def __init__(self, obj, defects=None):
        import numpy as np
        from mathutils.bvhtree import BVHTree

        mesh = obj.data
        self.points, self.faces = _mesh_arrays(obj)
        self.bounds = np.stack((self.points.min(axis=0), self.points.max(axis=0)))
        self.tree = BVHTree.FromPolygons(self.points.tolist(), self.faces)
        mesh.calc_loop_triangles()
        self.tris = np.asarray([tuple(triangle.vertices) for triangle in mesh.loop_triangles])
        triangle_points = self.points[self.tris]
        centers = triangle_points.mean(axis=1)
        self.samples = np.concatenate([array[np.linspace(0, len(array) - 1, min(8192, len(array)), dtype=int)]
                                       for array in (self.points, centers)])

        self.report = {}
        if defects is None:
            return
        face_incidence = Counter(edge for polygon in mesh.polygons for edge in polygon.edge_keys)
        edges = [tuple(sorted(edge.vertices)) for edge in mesh.edges]
        boundary = [edge for edge in edges if face_incidence[edge] == 1]
        boundary_vertices = {vertex for edge in boundary for vertex in edge}
        self.boundary = self.points[np.asarray(boundary, dtype=int).reshape((-1, 2))]

        areas = np.linalg.norm(np.cross(triangle_points[:, 1] - triangle_points[:, 0],
                                        triangle_points[:, 2] - triangle_points[:, 0]), axis=1) * 0.5
        face_areas = np.zeros(len(self.faces))
        np.add.at(face_areas, [triangle.polygon_index for triangle in mesh.loop_triangles], areas)
        span = max(float(np.linalg.norm(self.bounds[1] - self.bounds[0])), 1e-12)
        uv_layers = []
        invalid_uv = 0
        for layer in mesh.uv_layers:
            coordinates = np.empty(len(mesh.loops) * 2, dtype=np.float32)
            layer.uv.foreach_get("vector", coordinates)
            uv_layers.append(coordinates.reshape((-1, 2)))
            invalid_uv += int((~np.isfinite(coordinates)).reshape((-1, 2)).any(axis=1).sum())

        positions = [_position_key(point) for point in self.points]
        self.face_records = defaultdict(Counter)
        self.material_indices = []
        collapsed_uv = 0
        for polygon, face in zip(mesh.polygons, self.faces):
            mat = polygon.material_index
            self.material_indices.append(mat)
            attrs = []
            for layer in uv_layers:
                uv = layer[polygon.loop_start:polygon.loop_start + len(face)].astype(np.float64)
                area = np.dot(uv[:, 0], np.roll(uv[:, 1], -1)) - np.dot(uv[:, 1], np.roll(uv[:, 0], -1))
                collapsed_uv += int(abs(area) <= 1e-10)
                attrs.append(tuple(sorted((positions[v], _position_key(coord)) for v, coord in zip(face, uv))))
            key = tuple(sorted(positions[v] for v in face))
            self.face_records[key][(mat, tuple(attrs))] += 1

        self.report = dict(defects)
        self.report.update(
            vertices=len(self.points),
            triangulated_faces=len(self.tris),
            triangles=sum(len(face) == 3 for face in self.faces),
            quads=sum(len(face) == 4 for face in self.faces),
            open_edges=len(boundary),
            boundary_components=_groups(boundary_vertices, boundary),
            boundary_length=float(np.linalg.norm(self.boundary[:, 1] - self.boundary[:, 0], axis=1).sum()),
            non_manifold_edges=sum(incidence > 2 for incidence in face_incidence.values()),
            loose_edges=sum(face_incidence[edge] == 0 for edge in edges),
            components=_groups(range(len(self.points)), edges),
            degenerate_faces=int((face_areas <= span * span * 1e-12).sum()),
            uv_layers=[layer.name for layer in mesh.uv_layers],
            invalid_uv_corners=invalid_uv,
            collapsed_uv_faces=collapsed_uv,
            material_slots=[material.name_full if material else None for material in mesh.materials],
            texture_bindings=_texture_bindings(mesh),
            bounds=self.bounds.tolist(),
        )


def _new_boundary(before, candidate, tolerance):
    import numpy as np
    from mathutils.kdtree import KDTree

    if not len(before):
        return len(candidate)
    if not len(candidate):
        return 0
    previous = {tuple(sorted(map(_position_key, edge))) for edge in before}
    changed = [edge for edge in candidate if tuple(sorted(map(_position_key, edge))) not in previous]
    if not changed:
        return 0
    tree = KDTree(len(before) * 3)
    for i, edge in enumerate(before):
        for point in (*edge, edge.mean(axis=0)):
            tree.insert(point, i)
    tree.balance()

    def lies_on_previous(point):
        indices = {index for _, index, _ in tree.find_n(point, min(24, len(before) * 3))}
        edges = before[list(indices)]
        direction = edges[:, 1] - edges[:, 0]
        offset = point - edges[:, 0]
        squared_length = (direction * direction).sum(axis=1)
        ratio = np.divide((direction * offset).sum(axis=1), squared_length,
                          out=np.zeros(len(edges)), where=squared_length > 0).clip(0, 1)
        return bool((np.linalg.norm(offset - ratio[:, None] * direction, axis=1) <= tolerance).any())

    return sum(not all(lies_on_previous(point) for point in (*edge, edge.mean(axis=0))) for edge in changed)


def _attribute_changes(before, candidate):
    uv_changes = material_changes = 0
    for key, records in candidate.face_records.items():
        previous = before.face_records.get(key)
        if previous:
            assignments = Counter()
            old_assignments = Counter()
            coordinates = Counter()
            old_coordinates = Counter()
            for (mat, uv), count in records.items():
                assignments[mat] += count
                coordinates[uv] += count
            for (mat, uv), count in previous.items():
                old_assignments[mat] += count
                old_coordinates[uv] += count
            matched = min(sum(records.values()), sum(previous.values()))
            uv_changes += matched - sum((coordinates & old_coordinates).values())
            material_changes += matched - sum((assignments & old_assignments).values())
    if len(before.report["material_slots"]) > 1:
        for face, material in zip(candidate.faces, candidate.material_indices):
            key = tuple(sorted(_position_key(candidate.points[vertex]) for vertex in face))
            if key in before.face_records:
                continue
            _, _, index, _ = before.tree.find_nearest(candidate.points[list(face)].mean(axis=0))
            material_changes += int(index is not None and material != before.material_indices[index])
    return uv_changes, material_changes


def _collect(generator):
    while True:
        try:
            next(generator)
        except StopIteration as result:
            return result.value


def _silhouette(first, second):
    different = covered = 0
    for (a, *_), (b, *_) in zip(first, second):
        different += int((a ^ b).sum())
        covered += int((a | b).sum())
    return 100 * different / max(covered, 1)


class Evaluation:
    def __init__(self, pictures, first, reference, policy):
        import numpy as np

        self.pictures = pictures
        self.reference = reference if reference is not None else first
        self.policy = policy
        low = np.minimum(first.bounds[0], self.reference.bounds[0])
        high = np.maximum(first.bounds[1], self.reference.bounds[1])
        self.span = max(float(np.linalg.norm(high - low)), 1e-12)
        self.center = (low + high) / 2
        self.half = max(float((high - low).max()) * 0.55, 1e-12)
        self.reference_views = self._views(self.reference)
        self.measure(first)
        self.initial = first

    def _views(self, snapshot):
        return _collect(self.pictures(snapshot.points, snapshot.tris.tolist(), self.center, self.half, 128))

    def measure(self, candidate):
        import numpy as np

        to_reference = [self.reference.tree.find_nearest(point)[3] for point in candidate.samples]
        from_reference = [candidate.tree.find_nearest(point)[3] for point in self.reference.samples]
        candidate.views = self._views(candidate)
        candidate.report.update(
            surface_p95=max(float(np.percentile(distances, 95)) for distances in (to_reference, from_reference)),
            surface_max=max(max(to_reference), max(from_reference)),
            silhouette_miss_pct=_silhouette(self.reference_views, candidate.views),
        )

    def compare(self, before, candidate):
        import numpy as np

        self.measure(candidate)
        uv, materials = _attribute_changes(before, candidate)
        changes = dict(
            new_boundary_segments=_new_boundary(before.boundary, candidate.boundary,
                                                self.policy.boundary_tolerance_ratio * self.span),
            changed_uv_faces=uv,
            changed_material_faces=materials,
            silhouette_step_pct=_silhouette(before.views, candidate.views),
            bounds_shift_ratio=max(float(np.abs(snapshot.bounds - candidate.bounds).max()) / self.span
                                   for snapshot in (before, self.initial)),
        )
        for key, scale in (("surface_p95", self.span), ("surface_max", self.span), ("silhouette_miss_pct", 1)):
            growth = (candidate.report[key] - min(before.report[key], self.initial.report[key])) / scale
            growth_key = {"surface_p95": "surface_p95_growth_ratio", "surface_max": "surface_max_growth_ratio",
                          "silhouette_miss_pct": "silhouette_growth_pct"}[key]
            changes[growth_key] = growth
        failures, warnings = decide(before.report, candidate.report, changes, self.policy)
        return changes, failures, warnings
