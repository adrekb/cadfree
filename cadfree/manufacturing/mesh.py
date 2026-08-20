from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from cadfree.manufacturing.types import MeshMetrics


def _bbox_mm(mesh: trimesh.Trimesh) -> tuple[float, float, float]:
    extents = mesh.extents.astype(float)
    return (float(extents[0]), float(extents[1]), float(extents[2]))


def overhang_ratio(mesh: trimesh.Trimesh, angle_deg: float = 45.0) -> float:
    """Fraction of area whose outward normal leans past `angle_deg` from +Z.

    Faces sitting on the bed (lowest Z) are ignored. This is a DFM hint, not a slicer.
    """
    if mesh.faces is None or len(mesh.faces) == 0:
        return 0.0
    normals = mesh.face_normals
    areas = mesh.area_faces
    limit = -np.cos(np.deg2rad(angle_deg))
    zmin = float(mesh.bounds[0, 2])
    centroids = mesh.triangles_center
    on_bed = centroids[:, 2] <= zmin + 0.4
    hanging = (normals[:, 2] < limit) & (~on_bed)
    total = float(np.sum(areas))
    if total <= 0:
        return 0.0
    return float(np.sum(areas[hanging]) / total)


def sample_min_thickness_mm(mesh: trimesh.Trimesh, samples: int = 80) -> float | None:
    """Ray-cast from random surface points along inward normals."""
    if not mesh.is_watertight or len(mesh.faces) == 0:
        return None
    try:
        points, index = mesh.sample(samples, return_index=True)
    except Exception:
        return None
    normals = -mesh.face_normals[index]
    try:
        locations, _, _ = mesh.ray.intersects_location(
            ray_origins=points + normals * 0.05,
            ray_directions=normals,
            multiple_hits=False,
        )
    except Exception:
        return None
    if locations is None or len(locations) == 0:
        return None
    # intersects_location does not pair 1:1 with rays when some miss; use hit distances
    # from a simpler per-ray loop for robustness on small meshes.
    thicknesses: list[float] = []
    for origin, direction in zip(points, normals, strict=False):
        try:
            hits, _, _ = mesh.ray.intersects_location(
                ray_origins=np.array([origin + direction * 0.08]),
                ray_directions=np.array([direction]),
                multiple_hits=False,
            )
        except Exception:
            continue
        if hits is None or len(hits) == 0:
            continue
        dist = float(np.linalg.norm(hits[0] - origin))
        if 0.05 < dist < 1e4:
            thicknesses.append(dist)
    if not thicknesses:
        return None
    return float(np.percentile(thicknesses, 10))


def metrics_from_mesh(mesh: trimesh.Trimesh) -> MeshMetrics:
    mesh = mesh.copy()
    if not mesh.is_watertight:
        try:
            trimesh.repair.fill_holes(mesh)
        except Exception:
            pass
    volume = float(abs(mesh.volume)) if mesh.is_watertight else float(abs(getattr(mesh, "volume", 0.0)))
    bbox = _bbox_mm(mesh)
    bbox_vol = max(bbox[0] * bbox[1] * bbox[2], 1e-9)
    solidity = float(np.clip(volume / bbox_vol, 0.0, 1.0))
    return MeshMetrics(
        volume_mm3=volume,
        surface_area_mm2=float(mesh.area),
        bbox_mm=bbox,
        watertight=bool(mesh.is_watertight),
        triangle_count=int(len(mesh.faces)),
        solidity=solidity,
        overhang_ratio=overhang_ratio(mesh),
        min_thickness_mm=sample_min_thickness_mm(mesh),
    )


def load_mesh(path: str | Path) -> trimesh.Trimesh:
    loaded = trimesh.load(path, force="mesh")
    if isinstance(loaded, trimesh.Scene):
        geoms = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not geoms:
            raise ValueError("STL/scene contained no mesh geometry")
        loaded = trimesh.util.concatenate(geoms)
    if not isinstance(loaded, trimesh.Trimesh):
        raise ValueError(f"Could not load a triangle mesh from {path}")
    return loaded
