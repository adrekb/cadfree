"""Search PARAMS against rung-0 feasibility. CadQuery is not rebuilt per eval.

The agent should not rewrite a solid ten times to hunt thickness. This inner
loop scales the *current* mesh by PARAMS ratios, runs the same evaluate() the
studio already uses, and returns a knee. FEA verifies the winner after
build_model — it does not run inside this loop.
"""

from __future__ import annotations

import math
from typing import Any

from cadfree.manufacturing.evaluate import evaluate
from cadfree.manufacturing.types import MeshMetrics

STRUCTURAL = {
    "thickness_mm",
    "width_mm",
    "height_mm",
    "length_mm",
    "span_mm",
    "foot_mm",
    "upright_mm",
    "depth_mm",
    "wall_mm",
    "arm_mm",
    "beam_mm",
    "plate_mm",
    "web_mm",
    "flange_mm",
}
THICKNESS_KEYS = {"thickness_mm", "wall_mm", "web_mm", "flange_mm", "plate_mm"}
SKIP_SUB = ("hole", "fillet", "offset", "radius", "chamfer", "cbore", "csk", "angle", "count")
DISCLAIMER = (
    "Coordinate descent on PARAMS vs first-order cantilever / mass / envelope. "
    "Mesh is scaled from the current STL, not rebuilt. Not FEA, not a Pareto CAD kernel."
)


def _num(raw: Any) -> float | None:
    try:
        if raw in (None, ""):
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def is_structural(key: str) -> bool:
    k = str(key)
    if k in STRUCTURAL:
        return True
    if not k.endswith("_mm"):
        return False
    low = k.lower()
    if any(s in low for s in SKIP_SUB):
        return False
    return True


def default_bounds(params: dict[str, Any], user: dict[str, Any] | None = None) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for key, raw in (params or {}).items():
        val = _num(raw)
        if val is None or val <= 0:
            continue
        if not is_structural(key):
            continue
        lo, hi = val * 0.5, val * 1.5
        if key in THICKNESS_KEYS or "thickness" in key:
            lo = max(lo, 1.0)
        out[key] = (lo, hi)
    for key, spec in (user or {}).items():
        if isinstance(spec, dict):
            lo = _num(spec.get("min") if spec.get("min") is not None else spec.get("lo"))
            hi = _num(spec.get("max") if spec.get("max") is not None else spec.get("hi"))
        elif isinstance(spec, (list, tuple)) and len(spec) >= 2:
            lo, hi = _num(spec[0]), _num(spec[1])
        else:
            continue
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        out[str(key)] = (float(lo), float(hi))
    return out


def scale_metrics(metrics: MeshMetrics, old: dict[str, Any], new: dict[str, Any]) -> MeshMetrics:
    """Scale volume/bbox by at most three linear PARAMS ratios (bbox axes)."""
    t_ratio = 1.0
    extents: list[tuple[float, float, str]] = []
    for key, nv in (new or {}).items():
        ov = _num((old or {}).get(key))
        nn = _num(nv)
        if ov is None or nn is None or ov == 0 or not is_structural(key):
            continue
        r = nn / ov
        if key in THICKNESS_KEYS or "thickness" in key:
            t_ratio = r
        else:
            extents.append((abs(math.log(max(r, 1e-9))), r, key))
    extents.sort(reverse=True)
    chosen = [r for _, r, _ in extents[:2]]
    vol_scale = t_ratio
    for r in chosen:
        vol_scale *= r
    vol_scale = max(vol_scale, 1e-6)
    area_scale = vol_scale ** (2.0 / 3.0)
    dims = [float(d) for d in metrics.bbox_mm]
    mn = min(range(3), key=lambda i: dims[i])
    mx = max(range(3), key=lambda i: dims[i])
    dims[mn] *= t_ratio
    if chosen:
        dims[mx] *= chosen[0]
        mid = next(i for i in range(3) if i != mn and i != mx) if mn != mx else (mn + 1) % 3
        if len(chosen) > 1:
            dims[mid] *= chosen[1]
    min_t = metrics.min_thickness_mm
    if min_t is not None:
        min_t = float(min_t) * t_ratio
    return MeshMetrics(
        volume_mm3=float(metrics.volume_mm3) * vol_scale,
        surface_area_mm2=float(metrics.surface_area_mm2) * area_scale,
        bbox_mm=(dims[0], dims[1], dims[2]),
        watertight=metrics.watertight,
        triangle_count=metrics.triangle_count,
        solidity=metrics.solidity,
        overhang_ratio=metrics.overhang_ratio,
        min_thickness_mm=min_t,
    )


def _score_row(
    params: dict[str, Any],
    report: Any,
) -> dict[str, Any]:
    data = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    mass = (data.get("mass") or {}).get("mass_g")
    strength = data.get("strength") or {}
    sf = strength.get("safety_factor_actual")
    return {
        "params": {k: round(float(v), 4) if isinstance(v, (int, float)) else v for k, v in params.items()},
        "mass_g": None if mass is None else round(float(mass), 3),
        "sf": None if sf is None else round(float(sf), 3),
        "possible": bool(data.get("possible")),
        "verdict": data.get("verdict"),
        "summary": data.get("summary"),
        "strength_status": strength.get("status"),
    }


def _rank_tuple(row: dict[str, Any], goal: str) -> tuple:
    fail = 0 if row.get("possible") else 1
    mass = float(row["mass_g"]) if row.get("mass_g") is not None else 1e9
    sf = float(row["sf"]) if row.get("sf") is not None else 0.0
    if goal == "sf":
        return (fail, -sf, mass)
    return (fail, mass, -sf)


def _pareto_knee(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pool = [r for r in rows if r.get("possible")] or list(rows)
    masses = [float(r["mass_g"]) for r in pool if r.get("mass_g") is not None]
    sfs = [float(r["sf"]) for r in pool if r.get("sf") is not None]
    if not masses or not sfs:
        return min(pool, key=lambda r: _rank_tuple(r, "mass"))
    m0, m1 = min(masses), max(masses)
    s0, s1 = min(sfs), max(sfs)

    def nd(row: dict[str, Any]) -> bool:
        m = float(row["mass_g"] or m1)
        s = float(row["sf"] or 0.0)
        for other in pool:
            if other is row:
                continue
            om = float(other["mass_g"] or m1)
            os = float(other["sf"] or 0.0)
            if om <= m + 1e-9 and os >= s - 1e-9 and (om < m - 1e-9 or os > s + 1e-9):
                return False
        return True

    front = [r for r in pool if nd(r)] or pool
    best = None
    best_d = 1e9
    for row in front:
        mn = 0.0 if m1 <= m0 else (float(row["mass_g"] or m1) - m0) / (m1 - m0)
        sn = 0.0 if s1 <= s0 else (s1 - float(row["sf"] or 0.0)) / (s1 - s0)
        d = math.hypot(mn, sn)
        if d < best_d:
            best_d = d
            best = row
    return best or front[0]


def evaluate_params(
    metrics: MeshMetrics,
    capabilities: list[dict[str, Any]],
    constraints: dict[str, Any],
    old_params: dict[str, Any],
    new_params: dict[str, Any],
) -> dict[str, Any]:
    scaled = scale_metrics(metrics, old_params, new_params)
    report = evaluate(scaled, capabilities, constraints)
    return _score_row(new_params, report)


def optimize_params(
    metrics: MeshMetrics,
    capabilities: list[dict[str, Any]],
    constraints: dict[str, Any],
    params: dict[str, Any],
    *,
    bounds: dict[str, Any] | None = None,
    goal: str = "pareto",
    max_evals: int = 40,
) -> dict[str, Any]:
    goal = (goal or "pareto").strip().lower()
    if goal not in {"mass", "sf", "pareto"}:
        goal = "pareto"
    current = {k: v for k, v in (params or {}).items() if _num(v) is not None}
    lim = default_bounds(current, bounds)
    if not lim:
        return {
            "ok": False,
            "error": "No structural PARAMS (*_mm thickness/width/…) to search. Pass bounds={param:{min,max}}.",
            "disclaimer": DISCLAIMER,
        }
    if not capabilities:
        return {"ok": False, "error": "No workshop processes on this project.", "disclaimer": DISCLAIMER}
    budget = max(6, min(int(max_evals or 40), 80))
    keys = list(lim)
    grid_n = 5

    def snap(key: str, value: float) -> float:
        lo, hi = lim[key]
        return round(min(max(value, lo), hi), 4)

    seed = dict(current)
    for k, (lo, hi) in lim.items():
        if k in seed:
            seed[k] = snap(k, float(seed[k]))
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def eval_one(trial: dict[str, Any]) -> dict[str, Any] | None:
        key = tuple(sorted((k, round(float(trial[k]), 4)) for k in lim if k in trial))
        if key in seen:
            return None
        seen.add(key)
        merged = dict(current)
        merged.update({k: trial[k] for k in lim if k in trial})
        row = evaluate_params(metrics, capabilities, constraints, current, merged)
        rows.append(row)
        return row

    best = eval_one(seed)
    if best is None:
        return {"ok": False, "error": "seed eval failed", "disclaimer": DISCLAIMER}

    while len(rows) < budget:
        progressed = False
        for key in keys:
            if len(rows) >= budget:
                break
            lo, hi = lim[key]
            samples = [lo + (hi - lo) * i / (grid_n - 1) for i in range(grid_n)]
            samples.append(float(best["params"].get(key, current.get(key) or lo)))
            local = best
            for val in samples:
                if len(rows) >= budget:
                    break
                trial = dict(best["params"])
                trial[key] = snap(key, val)
                row = eval_one(trial)
                if row and _rank_tuple(row, "mass" if goal == "pareto" else goal) < _rank_tuple(local, "mass" if goal == "pareto" else goal):
                    local = row
                    progressed = True
            best = local
        if not progressed:
            break

    if goal == "pareto":
        winner = _pareto_knee(rows)
    else:
        winner = min(rows, key=lambda r: _rank_tuple(r, goal))

    return {
        "ok": True,
        "goal": goal,
        "evals": len(rows),
        "bounds": {k: {"min": lo, "max": hi} for k, (lo, hi) in lim.items()},
        "winner": winner,
        "candidates": sorted(rows, key=lambda r: _rank_tuple(r, "mass" if goal == "pareto" else goal))[:12],
        "next": (
            "set_params with winner.params, then build_model, then check_feasibility. "
            "run_solvers fea only verifies the winner — it was not in this loop."
        ),
        "disclaimer": DISCLAIMER,
    }
