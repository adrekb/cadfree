"""Convex-hull interference. Exact tooth mesh needs the teeth in the STL.

This is not contact dynamics. It answers: do these two posed solids overlap?
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from cadfree.manufacturing.mesh import load_mesh

_HULL_CACHE: dict[str, tuple[int, np.ndarray]] = {}


def hull_vertices(stl_path: str | Path, max_verts: int = 48) -> np.ndarray:
    path = Path(stl_path)
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    hit = _HULL_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    mesh = load_mesh(stl_path)
    try:
        hull = mesh.convex_hull
        verts = np.asarray(hull.vertices, dtype=float)
    except Exception:
        verts = np.asarray(mesh.vertices, dtype=float)
    if len(verts) > max_verts:
        idx = np.linspace(0, len(verts) - 1, max_verts).astype(int)
        verts = verts[idx]
    _HULL_CACHE[key] = (mtime, verts)
    return verts


def transform_points(verts: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    m = np.asarray(matrix, dtype=float)
    ones = np.ones((len(verts), 1))
    h = np.hstack([verts, ones])
    return (h @ m.T)[:, :3]


def aabb_overlap(a: np.ndarray, b: np.ndarray, pad: float = 0.2) -> bool:
    amin, amax = a.min(axis=0) - pad, a.max(axis=0) + pad
    bmin, bmax = b.min(axis=0) - pad, b.max(axis=0) + pad
    return bool(np.all(amin <= bmax) and np.all(bmin <= amax))


def convex_overlap(a: np.ndarray, b: np.ndarray) -> bool:
    """AABB clash of posed hull vertices. Pins that share a joint are skipped upstream."""
    if len(a) == 0 or len(b) == 0:
        return False
    return aabb_overlap(a, b, pad=0.05)


def posed_hit(
    stl_a: str | Path,
    matrix_a: np.ndarray,
    stl_b: str | Path,
    matrix_b: np.ndarray,
) -> bool:
    va = transform_points(hull_vertices(stl_a), matrix_a)
    vb = transform_points(hull_vertices(stl_b), matrix_b)
    return convex_overlap(va, vb)


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
