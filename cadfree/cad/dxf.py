"""ASCII DXF of the largest-area 2D projection. Sheet-process helper, not a drawing.

Laser/waterjet shops want a closed outline. Cadfree projects the built STL
(or a convex hull of that silhouette) onto the plane with the smallest thickness.
This is not a shop drawing, not a flattened sheet-metal unfold, not DXF from
a CadQuery face unless that face is later wired in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from cadfree.cad.assembly import get_part, part_dir
from cadfree.cad.snapshot import triangles_from_stl
from cadfree.paths import project_dir

HONEST = (
    "DXF is the convex hull of the largest-area orthographic projection of the STL. "
    "Not a shop drawing, not a flattened unfold, not GD&T."
)


def dxf_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "sim" / "dxf"
    path.mkdir(parents=True, exist_ok=True)
    return path


def convex_hull_2d(points: np.ndarray) -> np.ndarray:
    """Monotone-chain convex hull. Returns vertices CCW, last ≠ first."""
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError("convex_hull_2d expects Nx2 points")
    uniq = np.unique(np.round(pts, 8), axis=0)
    if len(uniq) <= 1:
        return uniq
    uniq = uniq[np.lexsort((uniq[:, 1], uniq[:, 0]))]

    def cross(o: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
        return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))

    lower: list[np.ndarray] = []
    for p in uniq:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[np.ndarray] = []
    for p in uniq[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = np.array(lower[:-1] + upper[:-1], dtype=np.float64)
    return hull


def largest_projection(triangles: np.ndarray) -> tuple[np.ndarray, str, float]:
    """Drop the axis with the smallest extent (sheet thickness)."""
    pts = np.asarray(triangles, dtype=np.float64).reshape(-1, 3)
    extents = pts.max(axis=0) - pts.min(axis=0)
    drop = int(np.argmin(extents))
    keep = [i for i in range(3) if i != drop]
    axis = "xyz"[drop]
    area = float(extents[keep[0]] * extents[keep[1]])
    return pts[:, keep], f"dropped {axis} (extent {extents[drop]:.3g} mm)", area


def ascii_dxf_polyline(vertices: np.ndarray, layer: str = "0") -> str:
    verts = np.asarray(vertices, dtype=np.float64)
    if len(verts) == 0:
        raise ValueError("no vertices to write")
    lines = [
        "0",
        "SECTION",
        "2",
        "ENTITIES",
        "0",
        "LWPOLYLINE",
        "8",
        layer,
        "90",
        str(len(verts)),
        "70",
        "1",
    ]
    for x, y in verts:
        lines.extend(["10", f"{float(x):.6f}", "20", f"{float(y):.6f}"])
    lines.extend(["0", "ENDSEC", "0", "EOF", ""])
    return "\n".join(lines)


def export_dxf(project_id: str, part_id: str | None = None) -> dict[str, Any]:
    part = get_part(project_id, part_id)
    stl = part_dir(project_id, part["id"]) / "model.stl"
    if not stl.is_file():
        return {"ok": False, "error": "Build the model before exporting DXF."}
    triangles = triangles_from_stl(stl)
    proj, plane_note, area = largest_projection(triangles)
    hull = convex_hull_2d(proj)
    if len(hull) < 3:
        return {"ok": False, "error": "Projection collapsed — the solid has no area in 2D."}
    text = ascii_dxf_polyline(hull)
    dest = dxf_dir(project_id)
    path = dest / f"{part['id']}.dxf"
    path.write_text(text, encoding="ascii")
    return {
        "ok": True,
        "path": str(path),
        "url": f"/api/projects/{project_id}/dxf",
        "part_id": part["id"],
        "vertices": len(hull),
        "area_mm2": area,
        "plane": plane_note,
        "honest": HONEST,
    }
