"""Orthographic silhouette snapshots and kernel IoU vs a drawing.

GIFT (ICML 2026) trains on near-miss CAD programs scored by kernel IoU.
Cadfree does not train GIFT. After a build, we rasterize the STL, score the
silhouette against an attached image, and return `iterate` the same way FEA
does. Missing JPEG decoder → one sentence, never a fake IoU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from cadfree.cad.assembly import get_part, part_dir
from cadfree.cad.png import read_png, write_gray_png
from cadfree.cad.record import read_stl_triangles
from cadfree.paths import project_dir

VIEWS = ("x", "y", "z")
SIZE = 128
MATCH_IOU = 0.85
HONEST = (
    "Silhouette IoU of the built STL vs a thresholded image, orthographic +x/+y/+z. "
    "Not a trained GIFT model, not a registered camera, not a shop drawing."
)


def snapshot_dir(project_id: str, part_id: str | None = None) -> Path:
    path = project_dir(project_id) / "sim" / "snapshots"
    if part_id:
        path = path / part_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def triangles_from_stl(path: Path) -> np.ndarray:
    tris = read_stl_triangles(Path(path).read_bytes())
    if not tris:
        raise ValueError(f"no triangles in {path}")
    out = np.empty((len(tris), 3, 3), dtype=np.float64)
    for i, tri in enumerate(tris):
        out[i, 0] = tri["v0"]
        out[i, 1] = tri["v1"]
        out[i, 2] = tri["v2"]
    return out


def box_triangles(
    sx: float = 10.0, sy: float = 20.0, sz: float = 5.0, center: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> np.ndarray:
    """Axis-aligned box as 12 triangles. Used by tests; no CadQuery required."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    cx, cy, cz = center
    v = np.array(
        [
            [cx - hx, cy - hy, cz - hz],
            [cx + hx, cy - hy, cz - hz],
            [cx + hx, cy + hy, cz - hz],
            [cx - hx, cy + hy, cz - hz],
            [cx - hx, cy - hy, cz + hz],
            [cx + hx, cy - hy, cz + hz],
            [cx + hx, cy + hy, cz + hz],
            [cx - hx, cy + hy, cz + hz],
        ],
        dtype=np.float64,
    )
    faces = (
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (2, 6, 7, 3),
        (0, 3, 7, 4),
        (1, 5, 6, 2),
    )
    tris = []
    for a, b, c, d in faces:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[d]))
    return np.array(tris, dtype=np.float64)


def rasterize_silhouette(triangles: np.ndarray, view: str = "z", size: int = SIZE) -> np.ndarray:
    """Orthographic occupancy. True = solid covers the pixel."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    pts = np.asarray(triangles, dtype=np.float64)
    if pts.size == 0:
        return np.zeros((size, size), dtype=bool)
    axes = {"x": (1, 2, 0), "y": (0, 2, 1), "z": (0, 1, 2)}[view]
    xy = pts[..., list(axes[:2])]
    depth = pts[..., axes[2]]
    lo = xy.reshape(-1, 2).min(axis=0)
    hi = xy.reshape(-1, 2).max(axis=0)
    span = np.maximum(hi - lo, 1e-9)
    pad = 0.06 * float(span.max())
    lo = lo - pad
    hi = hi + pad
    span = hi - lo
    scale = (size - 1) / float(max(span[0], span[1]))
    mask = np.zeros((size, size), dtype=bool)
    zbuf = np.full((size, size), -np.inf, dtype=np.float64)
    for i in range(pts.shape[0]):
        p = (xy[i] - lo) * scale
        z = depth[i]
        _fill_triangle(mask, zbuf, p, z, size)
    return mask


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.asarray(a, dtype=bool)
    bb = np.asarray(b, dtype=bool)
    if aa.shape != bb.shape:
        raise ValueError("masks must share a shape")
    inter = int(np.logical_and(aa, bb).sum())
    union = int(np.logical_or(aa, bb).sum())
    if union == 0:
        return 1.0
    return float(inter) / float(union)


def luminance_to_mask(lum: np.ndarray, size: int = SIZE, polarity: str = "auto") -> tuple[np.ndarray, str]:
    """Threshold a photo/drawing into a silhouette.

    Snapshots are white-on-black. Drawings are usually dark ink on light paper.
    `auto` returns both polarities' masks is not needed here — callers that
    compare to an STL should try both and keep the higher IoU.
    """
    arr = np.asarray(lum, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError("luminance must be 2-D")
    resized = _nearest_resize(arr, size, size)
    light = resized > 0.5
    dark = resized < 0.5
    if polarity == "light":
        return light, "thresholded as light-solid-on-dark"
    if polarity == "dark":
        return dark, "thresholded as dark-ink-on-light"
    mean = float(resized.mean())
    if mean > 0.55:
        return dark, "thresholded as dark-ink-on-light (drawing-style)"
    return light, "thresholded as light-solid-on-dark"


def load_target_mask(path: Path, size: int = SIZE) -> tuple[np.ndarray, np.ndarray, str]:
    """Return (light-solid mask, dark-ink mask, note)."""
    data = Path(path).read_bytes()
    mime_hint = Path(path).suffix.lower()
    if data.startswith(b"\x89PNG"):
        lum = read_png(data)
        src = "PNG"
    elif mime_hint in {".jpg", ".jpeg", ".webp", ".gif"} or data[:3] == b"\xff\xd8\xff":
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            raise ValueError(
                "JPEG/WebP/GIF target needs Pillow (`pip install pillow`) or attach a PNG silhouette. "
                "Cadfree will not invent an IoU from an unread photo."
            ) from None
        img = Image.open(path).convert("L")
        lum = np.asarray(img, dtype=np.float32) / 255.0
        src = "Pillow"
    else:
        raise ValueError("target image must be PNG (or JPEG/WebP with Pillow installed)")
    light, _ = luminance_to_mask(lum, size=size, polarity="light")
    dark, _ = luminance_to_mask(lum, size=size, polarity="dark")
    return light, dark, src + " — both ink polarities scored; best IoU kept"


def render_part_snapshots(
    triangles: np.ndarray, dest: Path, size: int = SIZE
) -> dict[str, Any]:
    dest.mkdir(parents=True, exist_ok=True)
    views: dict[str, Any] = {}
    for view in VIEWS:
        mask = rasterize_silhouette(triangles, view=view, size=size)
        png = dest / f"{view}.png"
        write_gray_png(png, np.where(mask, 255, 0).astype(np.uint8))
        views[view] = {
            "png": str(png),
            "filled": int(mask.sum()),
            "size": size,
        }
    (dest / "views.json").write_text(json.dumps({"views": views, "honest": HONEST}, indent=2), encoding="utf-8")
    return {"ok": True, "views": views, "honest": HONEST, "dir": str(dest)}


def score_against_mask(
    triangles: np.ndarray, target: np.ndarray, size: int = SIZE
) -> dict[str, Any]:
    scores = []
    for view in VIEWS:
        sil = rasterize_silhouette(triangles, view=view, size=size)
        scores.append({"view": view, "iou": mask_iou(sil, target)})
    scores.sort(key=lambda s: s["iou"], reverse=True)
    best = scores[0]
    iou = float(best["iou"])
    match = iou >= MATCH_IOU
    iterate = None
    if not match:
        iterate = {
            "action": "set_params",
            "reason": (
                f"Best silhouette IoU is {iou:.2f} on +{best['view']} "
                f"(need ≥{MATCH_IOU:.2f}). Match overall proportions before fillets."
            ),
        }
    return {
        "ok": True,
        "iou": iou,
        "best_view": best["view"],
        "views": scores,
        "match": match,
        "threshold": MATCH_IOU,
        "iterate": iterate,
        "honest": HONEST,
    }


def verify_against_image(
    project_id: str,
    attachment_id: str | None = None,
    part_id: str | None = None,
) -> dict[str, Any]:
    """Build-time GIFT loop: rasterize the active solid, score vs an attachment."""
    part = get_part(project_id, part_id)
    stl = part_dir(project_id, part["id"]) / "model.stl"
    if not stl.is_file():
        return {"ok": False, "error": "Build the model before verifying against an image."}
    target_path, target_note = _resolve_attachment(project_id, attachment_id)
    if target_path is None:
        return {
            "ok": False,
            "error": target_note,
        }
    try:
        triangles = triangles_from_stl(stl)
        light, dark, load_note = load_target_mask(target_path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "honest": HONEST}
    dest = snapshot_dir(project_id, part["id"])
    snaps = render_part_snapshots(triangles, dest)
    scored_light = score_against_mask(triangles, light)
    scored_dark = score_against_mask(triangles, dark)
    scored = scored_light if scored_light["iou"] >= scored_dark["iou"] else scored_dark
    scored["polarity"] = "light" if scored is scored_light else "dark"
    scored["part_id"] = part["id"]
    scored["attachment"] = target_note
    scored["target_note"] = load_note
    scored["snapshots"] = snaps["views"]
    scored["snapshot_dir"] = snaps["dir"]
    (dest / "last_verify.json").write_text(json.dumps(scored, indent=2), encoding="utf-8")
    return scored


def snapshot_status() -> dict[str, Any]:
    return {
        "id": "snapshots",
        "label": "Silhouette IoU",
        "available": True,
        "views": list(VIEWS),
        "size": SIZE,
        "honest": HONEST,
    }


def _resolve_attachment(project_id: str, attachment_id: str | None) -> tuple[Path | None, str]:
    from cadfree.store.db import db

    with db() as conn:
        if attachment_id:
            row = conn.execute(
                "SELECT * FROM attachments WHERE id = ? AND project_id = ?",
                (attachment_id, project_id),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM attachments WHERE project_id = ? ORDER BY created_at DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
    if not row:
        return None, "No image attachment. Upload a drawing or photo, then call verify_against_image."
    path = Path(row["path"])
    if not path.is_file():
        return None, "Attachment file is missing on disk."
    return path, row["id"]


def _nearest_resize(arr: np.ndarray, h: int, w: int) -> np.ndarray:
    src_h, src_w = arr.shape
    ys = np.clip((np.arange(h) + 0.5) * src_h / h, 0, src_h - 1).astype(int)
    xs = np.clip((np.arange(w) + 0.5) * src_w / w, 0, src_w - 1).astype(int)
    return arr[ys][:, xs]


def _fill_triangle(
    mask: np.ndarray,
    zbuf: np.ndarray,
    pts: np.ndarray,
    z: np.ndarray,
    size: int,
) -> None:
    x0, y0 = pts[0]
    x1, y1 = pts[1]
    x2, y2 = pts[2]
    minx = max(int(np.floor(min(x0, x1, x2))), 0)
    maxx = min(int(np.ceil(max(x0, x1, x2))), size - 1)
    miny = max(int(np.floor(min(y0, y1, y2))), 0)
    maxy = min(int(np.ceil(max(y0, y1, y2))), size - 1)
    if maxx < minx or maxy < miny:
        return
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(area) < 1e-12:
        return
    for y in range(miny, maxy + 1):
        py = y + 0.5
        for x in range(minx, maxx + 1):
            px = x + 0.5
            w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) / area
            w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) / area
            w2 = 1.0 - w0 - w1
            if w0 < -1e-6 or w1 < -1e-6 or w2 < -1e-6:
                continue
            depth = float(w0 * z[0] + w1 * z[1] + w2 * z[2])
            if depth >= zbuf[y, x]:
                zbuf[y, x] = depth
                mask[y, x] = True
