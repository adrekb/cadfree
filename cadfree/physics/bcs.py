"""Resolve FEA fixtures and loads onto mesh nodes.

The user/agent names holes, a pick-id, or a feature. Bbox faces are a
*named* fallback, not a silent default. Pick-ids live on the millimetre STL
CadQuery exported; trimesh's SI copy drops those attributes.

Not mate-face contact FEA, not anisotropic FDM, not a sign-off.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from cadfree.cad.record import read_stl_triangles

MM = 0.001
SELECTORS = {
    "holes",
    "hub",
    "min_r",
    "min_x",
    "max_x",
    "min_y",
    "max_y",
    "min_z",
    "max_z",
    "<x",
    ">x",
    "<y",
    ">y",
    "<z",
    ">z",
    "<X",
    ">X",
    "<Y",
    ">Y",
    "<Z",
    ">Z",
}
_AXIS = {
    "min_x": (0, False),
    "max_x": (0, True),
    "<x": (0, False),
    ">x": (0, True),
    "<X": (0, False),
    ">X": (0, True),
    "min_y": (1, False),
    "max_y": (1, True),
    "<y": (1, False),
    ">y": (1, True),
    "<Y": (1, False),
    ">Y": (1, True),
    "min_z": (2, False),
    "max_z": (2, True),
    "<z": (2, False),
    ">z": (2, True),
    "<Z": (2, False),
    ">Z": (2, True),
}
_DIR_NAMES = {
    "-x": (-1.0, 0.0, 0.0),
    "+x": (1.0, 0.0, 0.0),
    "x": (1.0, 0.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "-z": (0.0, 0.0, -1.0),
    "+z": (0.0, 0.0, 1.0),
    "z": (0.0, 0.0, 1.0),
    "down": (0.0, 0.0, -1.0),
    "up": (0.0, 0.0, 1.0),
    "hanging": (0.0, 0.0, -1.0),
    "gravity": (0.0, 0.0, -1.0),
    "compression": (0.0, 0.0, -1.0),
    "cantilever / shelf": (1.0, 0.0, 0.0),
    "cantilever": (1.0, 0.0, 0.0),
    "shelf": (1.0, 0.0, 0.0),
}


def parse_direction(raw: Any, hint: str | None = None) -> tuple[float, float, float]:
    if raw is None or raw == "":
        raw = hint
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        vec = (float(raw[0]), float(raw[1]), float(raw[2]))
    elif isinstance(raw, str):
        key = raw.strip().lower()
        if key in _DIR_NAMES:
            vec = _DIR_NAMES[key]
        elif key in {"cantilever / shelf", "cantilever", "shelf"}:
            vec = (1.0, 0.0, 0.0)
        else:
            vec = _DIR_NAMES.get(key, (1.0, 0.0, 0.0))
    else:
        vec = (1.0, 0.0, 0.0)
    mag = math.sqrt(vec[0] ** 2 + vec[1] ** 2 + vec[2] ** 2) or 1.0
    return (vec[0] / mag, vec[1] / mag, vec[2] / mag)


def _as_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return [raw]


def _normalize_ref(item: Any, direction: Any = None) -> dict[str, Any]:
    if isinstance(item, int):
        out: dict[str, Any] = {"pick_index": int(item)}
    elif isinstance(item, str):
        key = item.strip()
        if key.lower() in {s.lower() for s in SELECTORS} or key in SELECTORS:
            out = {"selector": key}
        elif key.isdigit():
            out = {"pick_index": int(key)}
        else:
            out = {"feature_id": key}
    elif isinstance(item, dict):
        out = dict(item)
    else:
        out = {}
    if direction is not None and "direction" not in out:
        out["direction"] = direction
    return out


def normalize_fea_bcs(raw: Any) -> dict[str, Any]:
    """Accept the tool blob, run_solvers values, or constraints.fea_bcs."""
    if not raw:
        return {"fix": [], "load": []}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {"fix": [], "load": []}
    if not isinstance(raw, dict):
        return {"fix": [], "load": []}
    if isinstance(raw.get("fea_bcs"), dict) and "fix" not in raw and "load" not in raw:
        return normalize_fea_bcs(raw["fea_bcs"])
    direction = raw.get("load_direction") or raw.get("direction")
    fix = [_normalize_ref(x) for x in _as_list(raw.get("fix"))]
    load = [_normalize_ref(x, direction) for x in _as_list(raw.get("load"))]
    for fid in _as_list(raw.get("fix_features")):
        fix.append(_normalize_ref(fid))
    for fid in _as_list(raw.get("load_features")):
        load.append(_normalize_ref(fid, direction))
    if raw.get("fix_selector"):
        fix.append(_normalize_ref({"selector": raw["fix_selector"]}))
    if raw.get("load_selector"):
        load.append(_normalize_ref({"selector": raw["load_selector"]}, direction))
    if raw.get("fix_pick") is not None:
        fix.append(_normalize_ref({"pick_index": raw["fix_pick"]}))
    if raw.get("load_pick") is not None:
        load.append(_normalize_ref({"pick_index": raw["load_pick"]}, direction))
    if direction and load:
        for item in load:
            item.setdefault("direction", direction)
    return {"fix": fix, "load": load}


def _bbox_band(nodes: list[tuple[int, float, float, float]], axis: int, high: bool, frac: float = 0.05) -> list[int]:
    if not nodes:
        return []
    vals = [n[1 + axis] for n in nodes]
    lo, hi = min(vals), max(vals)
    span = max(hi - lo, 1e-12)
    if high:
        cut = hi - frac * span
        return [n[0] for n in nodes if n[1 + axis] >= cut]
    cut = lo + frac * span
    return [n[0] for n in nodes if n[1 + axis] <= cut]


def hub_nodes(nodes: list[tuple[int, float, float, float]], frac: float = 0.28) -> list[int]:
    """Nodes with hypot(x, y) ≤ frac × r_max. Shaft along +Z."""
    if not nodes:
        return []
    radii = [math.hypot(n[1], n[2]) for n in nodes]
    rmax = max(radii) if radii else 0.0
    cut = max(frac, 0.05) * max(rmax, 1e-12)
    return [n[0] for n, r in zip(nodes, radii) if r <= cut]


def selector_nodes(nodes: list[tuple[int, float, float, float]], selector: str) -> list[int]:
    key = str(selector or "").strip()
    low = key.lower()
    if low in {"hub", "min_r"}:
        return hub_nodes(nodes)
    spec = _AXIS.get(key) or _AXIS.get(low)
    if spec is None:
        return []
    axis, high = spec
    return _bbox_band(nodes, axis, high)


def hole_axes_from_params(params: dict[str, Any] | None) -> list[dict[str, float]]:
    """Starter-bracket hole pattern, or explicit hole_xy / hole_x_mm lists."""
    p = params or {}
    axes: list[dict[str, float]] = []
    d = p.get("hole_d_mm")
    try:
        d_mm = float(d) if d is not None else 0.0
    except (TypeError, ValueError):
        d_mm = 0.0
    r_mm = d_mm / 2.0 if d_mm else 2.5

    raw_xy = p.get("hole_xy_mm") or p.get("holes_xy_mm") or p.get("holes")
    if isinstance(raw_xy, list) and raw_xy:
        for item in raw_xy:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                axes.append({"x": float(item[0]), "y": float(item[1]), "z": float(item[2]) if len(item) > 2 else 0.0, "r_mm": r_mm})
            elif isinstance(item, dict) and item.get("x") is not None:
                axes.append(
                    {
                        "x": float(item["x"]),
                        "y": float(item.get("y") or 0.0),
                        "z": float(item.get("z") or 0.0),
                        "r_mm": float(item.get("r_mm") or r_mm),
                    }
                )
        return axes

    xs, ys = p.get("hole_x_mm"), p.get("hole_y_mm")
    if isinstance(xs, list) and isinstance(ys, list) and len(xs) == len(ys):
        for x, y in zip(xs, ys):
            axes.append({"x": float(x), "y": float(y), "z": 0.0, "r_mm": r_mm})
        return axes

    try:
        width = float(p["width_mm"]) if p.get("width_mm") is not None else None
        foot = float(p["foot_mm"]) if p.get("foot_mm") is not None else None
        offset = float(p["hole_offset_mm"]) if p.get("hole_offset_mm") is not None else None
    except (TypeError, ValueError, KeyError):
        width = foot = offset = None
    if width and foot and offset is not None and d_mm:
        # STARTER_BRACKET: <Z workplane, two holes on the foot.
        cy = -foot / 2.0 + offset
        axes.append({"x": -width / 4.0, "y": cy, "z": 0.0, "r_mm": r_mm})
        axes.append({"x": width / 4.0, "y": cy, "z": 0.0, "r_mm": r_mm})
    return axes


def nodes_near_points(
    nodes: list[tuple[int, float, float, float]],
    points_m: list[tuple[float, float, float]],
    radius_m: float,
) -> list[int]:
    if not nodes or not points_m:
        return []
    r2 = radius_m * radius_m
    hit: list[int] = []
    seen: set[int] = set()
    for nid, x, y, z in nodes:
        for px, py, pz in points_m:
            dx, dy, dz = x - px, y - py, z - pz
            if dx * dx + dy * dy + dz * dz <= r2:
                if nid not in seen:
                    seen.add(nid)
                    hit.append(nid)
                break
    return hit


def nodes_near_axes(
    nodes: list[tuple[int, float, float, float]],
    axes: list[dict[str, float]],
    scale: float = 1.2,
) -> list[int]:
    """Nodes within scale*r of a Z-aligned hole axis (coords in metres on `nodes`)."""
    if not nodes or not axes:
        return []
    hit: list[int] = []
    seen: set[int] = set()
    for nid, x, y, _z in nodes:
        for ax in axes:
            r = float(ax.get("r_mm") or 2.5) * MM * scale
            dx = x - float(ax["x"]) * MM
            dy = y - float(ax["y"]) * MM
            if dx * dx + dy * dy <= r * r:
                if nid not in seen:
                    seen.add(nid)
                    hit.append(nid)
                break
    return hit


def verts_for_pick(triangles: list[dict[str, Any]], pick_index: int) -> list[tuple[float, float, float]]:
    seen: set[tuple[float, float, float]] = set()
    out: list[tuple[float, float, float]] = []
    want = int(pick_index)
    for tri in triangles:
        if int(tri.get("attr") or 0) != want:
            continue
        for key in ("v0", "v1", "v2"):
            v = tri.get(key)
            if not v:
                continue
            pt = (round(float(v[0]), 5), round(float(v[1]), 5), round(float(v[2]), 5))
            if pt not in seen:
                seen.add(pt)
                out.append((float(v[0]), float(v[1]), float(v[2])))
    return out


def _load_triangles(status: dict[str, Any]) -> list[dict[str, Any]]:
    pick = status.get("pick") or {}
    path = pick.get("stl_mm") or (status.get("part") or {}).get("files", {}).get("stl_mm")
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    try:
        return read_stl_triangles(p.read_bytes())
    except OSError:
        return []


def _features(status: dict[str, Any]) -> list[dict[str, Any]]:
    cached = status.get("_features")
    if isinstance(cached, list):
        return cached
    pid = status.get("project_id")
    part_id = (status.get("part") or {}).get("id")
    if not pid or not part_id:
        return []
    try:
        from cadfree.cad.assembly import get_part, part_dir
        from cadfree.cad.features import extract_features
        from cadfree.cad.record import load_live

        part = get_part(pid, part_id)
        live = load_live(part_dir(pid, part_id) / "features.live.json")
        tree = extract_features(part.get("cadquery_source") or "", live=live)
        return list(tree.get("features") or [])
    except Exception:
        return []


def _pick_from_feature(features: list[dict[str, Any]], feature_id: str) -> int | None:
    want = str(feature_id or "").strip()
    if not want:
        return None
    for feat in features:
        if str(feat.get("id") or "") == want and feat.get("pick_index"):
            return int(feat["pick_index"])
    # kind shortcut: "hole" if exactly one live hole
    kind_hits = [f for f in features if str(f.get("kind") or "") == want and f.get("pick_index")]
    if len(kind_hits) == 1:
        return int(kind_hits[0]["pick_index"])
    return None


def _hole_picks(features: list[dict[str, Any]]) -> list[int]:
    ids = []
    for feat in features:
        if feat.get("kind") in {"hole", "cboreHole", "cskHole"} and feat.get("pick_index"):
            ids.append(int(feat["pick_index"]))
    return ids


def _expand_near(
    nodes: list[tuple[int, float, float, float]],
    points_mm: list[tuple[float, float, float]],
    radius_mm: float = 2.0,
) -> list[int]:
    pts_m = [(p[0] * MM, p[1] * MM, p[2] * MM) for p in points_mm]
    r = radius_mm * MM
    hit = nodes_near_points(nodes, pts_m, r)
    tries = 0
    while len(hit) < 3 and tries < 3 and pts_m:
        r *= 2.0
        hit = nodes_near_points(nodes, pts_m, r)
        tries += 1
    return hit


def _resolve_refs(
    refs: list[dict[str, Any]],
    nodes: list[tuple[int, float, float, float]],
    status: dict[str, Any],
    *,
    role: str,
) -> tuple[list[int], str, list[str]]:
    """Return (node ids, source tag, notes)."""
    notes: list[str] = []
    collected: list[int] = []
    sources: list[str] = []
    triangles = _load_triangles(status)
    features = _features(status)
    params = (status.get("cadquery") or {}).get("params_mm") or {}

    for ref in refs:
        selector = str(ref.get("selector") or "").strip()
        pick = ref.get("pick_index")
        fid = ref.get("feature_id")
        if fid and pick is None:
            pick = _pick_from_feature(features, str(fid))
            if pick is None:
                notes.append(f"{role}: feature {fid!r} has no live pick-id — rebuild, or use selector=holes.")
        if selector.lower() == "holes" or (fid and str(fid).lower() in {"holes", "hole"} and pick is None):
            hole_picks = _hole_picks(features)
            ids: list[int] = []
            if hole_picks and triangles:
                verts: list[tuple[float, float, float]] = []
                for idx in hole_picks:
                    verts.extend(verts_for_pick(triangles, idx))
                ids = _expand_near(nodes, verts)
                if ids:
                    collected.extend(ids)
                    sources.append("pick")
                    continue
            axes = hole_axes_from_params(params)
            ids = nodes_near_axes(nodes, axes)
            if ids:
                collected.extend(ids)
                sources.append("holes")
                notes.append(f"{role}: PARAMS hole pattern ({len(axes)} axes), not a meshed contact pair.")
                continue
            notes.append(f"{role}: no hole pick-ids or PARAMS hole pattern; will fall back.")
            continue
        if pick:
            verts = verts_for_pick(triangles, int(pick))
            ids = _expand_near(nodes, verts)
            if ids:
                collected.extend(ids)
                sources.append("pick")
            else:
                notes.append(f"{role}: pick_index {pick} matched no INP nodes.")
            continue
        if selector:
            ids = selector_nodes(nodes, selector)
            if ids:
                collected.extend(ids)
                if str(selector).strip().lower() in {"hub", "min_r"}:
                    sources.append("hub")
                    notes.append(f"{role}: selector {selector} is hypot(x,y) ≤ 0.28 r_max about +Z, not a mate face.")
                else:
                    sources.append("bbox")
                    notes.append(f"{role}: selector {selector} is a bbox band, not a mate face.")
            else:
                notes.append(f"{role}: selector {selector} matched no nodes.")

    # unique, stable
    seen: set[int] = set()
    uniq: list[int] = []
    for nid in collected:
        if nid not in seen:
            seen.add(nid)
            uniq.append(nid)
    source = sources[0] if len(set(sources)) == 1 else ("mix" if sources else "")
    return uniq, source, notes


def _bbox_for_direction(
    nodes: list[tuple[int, float, float, float]],
    direction: tuple[float, float, float],
) -> tuple[list[int], list[int]]:
    """Load the face the force points at; fix the opposite face."""
    ax = max(range(3), key=lambda i: abs(direction[i]))
    high = direction[ax] > 0
    load = _bbox_band(nodes, ax, high)
    fix = _bbox_band(nodes, ax, not high)
    return fix, load


def resolve_bcs(
    status: dict[str, Any],
    nodes: list[tuple[int, float, float, float]],
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map named fixtures/loads onto INP node ids. Bbox is explicit fallback."""
    raw = spec if spec is not None else (status.get("fea_bcs") or {})
    blob = normalize_fea_bcs(raw)
    hint = (status.get("load") or {}).get("direction")
    direction = parse_direction(None, hint)
    if blob.get("load"):
        for item in blob["load"]:
            if item.get("direction") is not None:
                direction = parse_direction(item.get("direction"), hint)
                break

    notes: list[str] = []
    fix, fix_src, n1 = _resolve_refs(blob.get("fix") or [], nodes, status, role="fix")
    load, load_src, n2 = _resolve_refs(blob.get("load") or [], nodes, status, role="load")
    notes.extend(n1)
    notes.extend(n2)
    used_named = bool(blob.get("fix") or blob.get("load"))
    fallback = False
    centrif = bool((status.get("_fea") or {}).get("centrif"))
    if centrif and fix and not load:
        load_src = "centrif"
        notes.append("CENTRIF body load — no nodal *CLOAD unless F_N is set.")
    elif not fix or not load:
        bfix, bload = _bbox_for_direction(nodes, direction)
        if not fix:
            fix = bfix
            fix_src = "bbox"
            fallback = True
        if not load:
            load = bload
            load_src = "bbox"
            fallback = True
        if not nodes:
            fix, load = [1], [1]
        elif not fix:
            fix = [nodes[0][0]]
        elif not load:
            load = [nodes[-1][0]]
        if used_named:
            notes.append("Named fixtures/loads did not all resolve; bbox face filled the gap.")
        else:
            notes.append("No fea_bcs set — bbox faces (named fallback, not a silent default).")

    source = "bbox" if (fix_src == "bbox" and load_src == "bbox") else (fix_src or load_src or "bbox")
    if fix_src != load_src and fix_src and load_src:
        source = "mix"
    disclaimer = (
        "Linear static, isotropic E (FDM knockdown if printed). "
        + (
            "Fixture/load are bbox faces, not your mate faces. "
            if source == "bbox"
            else "Fixtures/loads from pick-ids, named features, or PARAMS holes — not contact FEA. "
        )
        + "Not a sign-off."
    )
    return {
        "fix_nodes": fix,
        "load_nodes": load,
        "direction": list(direction),
        "source": source,
        "fix_source": fix_src or "bbox",
        "load_source": load_src or "bbox",
        "fallback": fallback,
        "named": used_named,
        "F_N": (status.get("load") or {}).get("F_N"),
        "material": status.get("material"),
        "notes": notes,
        "disclaimer": disclaimer,
        "spec": blob,
    }
