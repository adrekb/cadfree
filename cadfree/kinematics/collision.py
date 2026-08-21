"""Convex-hull interference via SAT. Exact tooth mesh needs the teeth in the STL.

This is not contact dynamics. It answers: do these two posed solids overlap?
AABB is only the broad phase — we do not call that a Motion study.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from cadfree.manufacturing.mesh import load_mesh

_HULL_CACHE: dict[str, tuple[int, np.ndarray, np.ndarray]] = {}


def hull_data(stl_path: str | Path, max_verts: int = 64) -> tuple[np.ndarray, np.ndarray]:
    path = Path(stl_path)
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    hit = _HULL_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1], hit[2]
    mesh = load_mesh(stl_path)
    try:
        hull = mesh.convex_hull
        verts = np.asarray(hull.vertices, dtype=float)
        faces = np.asarray(hull.faces, dtype=int)
    except Exception:
        verts = np.asarray(mesh.vertices, dtype=float)
        faces = np.asarray(getattr(mesh, "faces", np.zeros((0, 3))), dtype=int)
    if len(verts) > max_verts and len(faces):
        # Prefer the real hull. Only stride if it is huge.
        if len(verts) > 120:
            idx = np.unique(np.linspace(0, len(verts) - 1, max_verts).astype(int))
            keep = {int(i): n for n, i in enumerate(idx)}
            mapped = []
            for tri in faces:
                if all(int(v) in keep for v in tri):
                    mapped.append([keep[int(v)] for v in tri])
            if mapped:
                verts = verts[idx]
                faces = np.asarray(mapped, dtype=int)
    _HULL_CACHE[key] = (mtime, verts, faces)
    return verts, faces


def hull_vertices(stl_path: str | Path, max_verts: int = 48) -> np.ndarray:
    verts, _ = hull_data(stl_path, max_verts=max_verts)
    return verts


def transform_points(verts: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    m = np.asarray(matrix, dtype=float)
    if len(verts) == 0:
        return verts
    ones = np.ones((len(verts), 1))
    h = np.hstack([verts, ones])
    return (h @ m.T)[:, :3]


def aabb_overlap(a: np.ndarray, b: np.ndarray, pad: float = 0.2) -> bool:
    if len(a) == 0 or len(b) == 0:
        return False
    amin, amax = a.min(axis=0) - pad, a.max(axis=0) + pad
    bmin, bmax = b.min(axis=0) - pad, b.max(axis=0) + pad
    return bool(np.all(amin <= bmax) and np.all(bmin <= amax))


def _face_normals(verts: np.ndarray, faces: np.ndarray) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    if faces is None or len(faces) == 0 or len(verts) < 3:
        return out
    for tri in faces:
        p0, p1, p2 = verts[int(tri[0])], verts[int(tri[1])], verts[int(tri[2])]
        n = np.cross(p1 - p0, p2 - p0)
        ln = float(np.linalg.norm(n))
        if ln > 1e-9:
            out.append(n / ln)
    return out


def _unique_edges(verts: np.ndarray, faces: np.ndarray) -> list[np.ndarray]:
    seen: set[tuple[int, int]] = set()
    edges: list[np.ndarray] = []
    if faces is None or len(faces) == 0:
        return edges
    for tri in faces:
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = int(tri[i]), int(tri[j])
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            e = verts[b] - verts[a]
            ln = float(np.linalg.norm(e))
            if ln > 1e-9:
                edges.append(e / ln)
    return edges


def _separated(a: np.ndarray, b: np.ndarray, axis: np.ndarray) -> bool:
    n = float(np.linalg.norm(axis))
    if n < 1e-9:
        return False
    axis = axis / n
    da = a @ axis
    db = b @ axis
    return bool(da.max() < db.min() - 1e-6 or db.max() < da.min() - 1e-6)


def convex_overlap(a: np.ndarray, b: np.ndarray, faces_a=None, faces_b=None) -> bool:
    """SAT on convex hulls. AABB is only the reject test."""
    if len(a) == 0 or len(b) == 0:
        return False
    if not aabb_overlap(a, b, pad=0.05):
        return False
    axes = _face_normals(a, faces_a if faces_a is not None else np.zeros((0, 3), dtype=int))
    axes += _face_normals(b, faces_b if faces_b is not None else np.zeros((0, 3), dtype=int))
    if not axes:
        return True  # hull faces missing — AABB overlap stands, named as such upstream
    ea = _unique_edges(a, faces_a) if faces_a is not None and len(faces_a) else []
    eb = _unique_edges(b, faces_b) if faces_b is not None and len(faces_b) else []
    if 0 < len(ea) <= 16 and 0 < len(eb) <= 16:
        for u in ea:
            for v in eb:
                axes.append(np.cross(u, v))
    for axis in axes:
        if _separated(a, b, axis):
            return False
    return True


def posed_hit(
    stl_a: str | Path,
    matrix_a: np.ndarray,
    stl_b: str | Path,
    matrix_b: np.ndarray,
) -> bool:
    va, fa = hull_data(stl_a)
    vb, fb = hull_data(stl_b)
    pa = transform_points(va, matrix_a)
    pb = transform_points(vb, matrix_b)
    return convex_overlap(pa, pb, fa, fb)


def clear_hull_cache() -> None:
    _HULL_CACHE.clear()


def gear_center_check(
    dist_mm: float,
    teeth_a: float,
    teeth_b: float,
    module_mm: float,
    *,
    internal: bool = False,
) -> dict[str, Any]:
    """Do two spur gears mesh at this center distance? First-order, not a contact sim."""
    z1, z2, m = float(teeth_a), float(teeth_b), float(module_mm)
    if m <= 0 or z1 <= 0 or z2 <= 0:
        return {"ok": False, "error": "need module_mm and teeth counts"}
    pitch = m * (abs(z1 - z2) if internal else (z1 + z2)) / 2.0
    err = float(dist_mm) - pitch
    tol = max(0.08 * m, 0.15)
    if err < -0.25 * m:
        verdict = "interference — centers too close for those teeth"
        ok = False
    elif abs(err) <= tol:
        verdict = "meshes at first-order (pitch diameters)"
        ok = True
    elif err > 0:
        verdict = "too far apart — teeth will not mesh"
        ok = False
    else:
        verdict = "tight mesh / possible interference"
        ok = False
    return {
        "ok": ok,
        "verdict": verdict,
        "center_mm": float(dist_mm),
        "pitch_sum_mm": pitch,
        "error_mm": err,
        "ratio": -z1 / z2 if not internal else z1 / z2,
        "disclaimer": "Spur-gear pitch-diameter check. Not a profile / backlash / helical sim.",
    }
