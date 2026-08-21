"""Meanline impeller worksheets and a PARAMS search on those numbers.

CadQuery authors the solid. This file does Euler / Wiesner / Ns / NPSHa / hoop
on the SI snapshot. It does not run CFD, does not invent η or NPSHr, and does
not rebuild the mesh per eval.
"""

from __future__ import annotations

import math
from typing import Any

from cadfree.physics.book import BY_ID, G, PACKS
from cadfree.physics.engine import solve_formula
from cadfree.physics.snapshot import bind_formula

DISCLAIMER = (
    "Incompressible meanline: Euler head with Wiesner slip, SI Ns, NPSHa, "
    "thin-ring hoop. β is from tangential. Not a pump curve, not CFD, not burst."
)
WATER_PVAPOR_20C = 2339.0  # Pa, named 20 °C water. Not a temperature sweep.
TURBO_PARAM_KEYS = (
    "r2_mm",
    "r1_mm",
    "beta2_deg",
    "beta1_deg",
    "b2_mm",
    "b1_mm",
    "n_blades",
    "hub_r_mm",
)


def _num(raw: Any) -> float | None:
    try:
        if raw in (None, ""):
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def volume_flow_m3s(constraints: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> float | None:
    blob = {**(constraints or {}), **(extra or {})}
    for key, scale in (("Q_m3s", 1.0), ("Q", 1.0), ("flow_m3s", 1.0), ("Q_lpm", 1.0 / 60000.0), ("Q_gpm", 6.309e-5)):
        val = _num(blob.get(key))
        if val is not None:
            return val * scale
    return None


def meanline_inputs(status: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    inputs = dict(status.get("inputs") or {})
    env = status.get("environment") or {}
    params = (status.get("cadquery") or {}).get("params_mm") or {}
    cons = dict(status.get("constraints") or {})
    cons.update(extra)

    def pick(*keys: str, default: float | None = None) -> float | None:
        for key in keys:
            if key in extra and extra[key] not in (None, ""):
                return _num(extra[key])
            if key in cons and cons[key] not in (None, ""):
                return _num(cons[key])
            if key in inputs and inputs[key] not in (None, ""):
                return _num(inputs[key])
            if key in params and params[key] not in (None, ""):
                return _num(params[key])
            if key in env and env[key] not in (None, ""):
                return _num(env[key])
        return default

    def pick_mm(key: str) -> float | None:
        """Millimetre PARAMS from extra/constraints/script — not snapshot SI aliases."""
        for src in (extra, cons, params):
            if not isinstance(src, dict):
                continue
            if key in src and src[key] not in (None, ""):
                val = _num(src[key])
                if val is not None:
                    return val / 1000.0
        return None

    r2 = pick_mm("r2_mm")
    if r2 is None:
        r2 = pick("r2", "r2_m")
    r1 = pick_mm("r1_mm")
    if r1 is None:
        r1 = pick("r1", "r1_m")
    if r2 is None:
        bbox = (status.get("part") or {}).get("bbox_m") or []
        if bbox:
            r2 = max(float(bbox[0]), float(bbox[1])) / 2.0
    if r1 is None and r2:
        hub = pick_mm("hub_r_mm")
        r1 = hub if hub else 0.3 * r2
    b2 = pick_mm("b2_mm")
    if b2 is None:
        b2 = pick("b2", "b2_m")
    b1 = pick_mm("b1_mm")
    if b1 is None:
        b1 = pick("b1", "b1_m")
    if b2 is None:
        bbox = (status.get("part") or {}).get("bbox_m") or [0, 0, 0.006]
        b2 = float(bbox[2] or 0.006)
    if b1 is None:
        b1 = b2
    beta2 = None
    for src in (extra, cons, params):
        if isinstance(src, dict) and src.get("beta2_deg") not in (None, ""):
            deg = _num(src.get("beta2_deg"))
            if deg is not None:
                beta2 = math.radians(deg)
                break
    if beta2 is None:
        beta2 = pick("beta2")
    beta1 = None
    for src in (extra, cons, params):
        if isinstance(src, dict) and src.get("beta1_deg") not in (None, ""):
            deg = _num(src.get("beta1_deg"))
            if deg is not None:
                beta1 = math.radians(deg)
                break
    if beta1 is None:
        beta1 = pick("beta1")
    z = None
    for src in (extra, cons, params):
        if isinstance(src, dict) and src.get("n_blades") not in (None, ""):
            z = _num(src.get("n_blades"))
            if z is not None:
                break
    if z is None:
        z = pick("z", "n_blades", default=6.0)
    n_rpm = pick("n_rpm", "rpm")
    Q = volume_flow_m3s(cons, extra) or pick("Q")
    psi = pick("psi", default=0.9) or 0.9
    p_inlet = pick("p_inlet", "p_inlet_pa")
    p_vapor = pick("p_vapor", "p_vapor_pa")
    fluid = str(env.get("fluid") or cons.get("fluid") or "air")
    if p_vapor is None and fluid == "water":
        p_vapor = WATER_PVAPOR_20C
    npshr = pick("NPSHr", "npshr_m")
    target_h = pick("target_H_m", "H", "H_m")
    return {
        "r1": r1,
        "r2": r2,
        "b1": b1,
        "b2": b2,
        "beta1": beta1,
        "beta2": beta2,
        "z": z,
        "n_rpm": n_rpm,
        "Q": Q,
        "psi": psi,
        "p_inlet": p_inlet,
        "p_vapor": p_vapor,
        "NPSHr": npshr,
        "target_H": target_h,
        "rho": pick("rho") or env.get("rho"),
        "rho_solid": pick("rho_solid") or (status.get("material") or {}).get("rho"),
        "g": G,
        "fluid": fluid,
    }


def run_meanline(status: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    ml = meanline_inputs(status, extra)
    missing = []
    if not ml["n_rpm"]:
        missing.append("n_rpm")
    if not ml["r2"]:
        missing.append("r2_mm")
    if not ml["Q"]:
        missing.append("Q_lpm / Q_m3s")
    if not ml["beta2"]:
        missing.append("beta2_deg")
    worksheets: list[dict[str, Any]] = []
    numbers: dict[str, Any] = {"psi": ml["psi"], "assumptions": [f"passage blockage ψ={ml['psi']} (named)"]}

    def add(fid: str, values: dict[str, Any]) -> dict[str, Any]:
        formula = BY_ID[fid]
        bound, prov = bind_formula(status, formula)
        bound.update({k: v for k, v in values.items() if v is not None})
        row = solve_formula(fid, bound, provenance=prov)
        worksheets.append(row)
        return row

    if ml["n_rpm"] and ml["r2"]:
        u2 = add("tip_speed", {"n_rpm": ml["n_rpm"], "r": ml["r2"]})
        if u2.get("ok"):
            numbers["U2"] = u2["value"]
        if ml["r1"]:
            u1 = add("tip_speed", {"n_rpm": ml["n_rpm"], "r": ml["r1"]})
            if u1.get("ok"):
                numbers["U1"] = u1["value"]
    if ml["Q"] and ml["r2"] and ml["b2"]:
        cm2 = add("flow_cm", {"Q": ml["Q"], "r": ml["r2"], "b": ml["b2"], "psi": ml["psi"]})
        if cm2.get("ok"):
            numbers["Cm2"] = cm2["value"]
    if ml["Q"] and ml["r1"] and ml["b1"]:
        cm1 = add("flow_cm", {"Q": ml["Q"], "r": ml["r1"], "b": ml["b1"], "psi": ml["psi"]})
        if cm1.get("ok"):
            numbers["Cm1"] = cm1["value"]
    if ml["beta2"] and ml["z"]:
        slip = add("wiesner_slip", {"beta2": ml["beta2"], "z": ml["z"]})
        if slip.get("ok"):
            numbers["sigma"] = slip["value"]
    u2 = numbers.get("U2")
    cm2 = numbers.get("Cm2")
    sigma = numbers.get("sigma")
    if u2 is not None and cm2 is not None and ml["beta2"]:
        tb = math.tan(ml["beta2"])
        if abs(tb) < 1e-6:
            numbers["Cu2_inf"] = u2
            numbers["triangle_note"] = "tan(β2)≈0 — whirl set to U2 (radial blades)."
        else:
            numbers["Cu2_inf"] = u2 - cm2 / tb
        if sigma is not None:
            numbers["Cu2"] = float(sigma) * float(numbers["Cu2_inf"])
        else:
            numbers["Cu2"] = numbers["Cu2_inf"]
        euler = add("euler_head", {"U2": u2, "Cu2": numbers["Cu2"]})
        if euler.get("ok"):
            numbers["H"] = euler["value"]
    if ml["n_rpm"] and ml["Q"] and numbers.get("H"):
        ns = add("specific_speed", {"n_rpm": ml["n_rpm"], "Q": ml["Q"], "H": numbers["H"]})
        if ns.get("ok"):
            numbers["ns"] = ns["value"]
    if ml["n_rpm"] and ml["r2"] and ml["rho_solid"]:
        hoop = add("disc_hoop", {"n_rpm": ml["n_rpm"], "r": ml["r2"], "rho_solid": ml["rho_solid"]})
        if hoop.get("ok"):
            numbers["hoop_Pa"] = hoop["value"]
            hoop["sigma_MPa"] = hoop["value"] / 1e6
    if ml["p_inlet"] is not None and ml["p_vapor"] is not None and numbers.get("Cm1") is not None and ml["rho"]:
        npsha = add(
            "npsh_available",
            {
                "p_inlet": ml["p_inlet"],
                "p_vapor": ml["p_vapor"],
                "rho": ml["rho"],
                "Cm": numbers["Cm1"],
            },
        )
        if npsha.get("ok"):
            numbers["NPSHa"] = npsha["value"]
            if ml["fluid"] == "water" and abs(float(ml["p_vapor"]) - WATER_PVAPOR_20C) < 1:
                numbers["assumptions"].append("p_vapor = 2339 Pa (water 20 °C, named)")
    elif ml["fluid"] and ml["fluid"] != "water":
        numbers["assumptions"].append("NPSH skipped — cavitation is a liquid-inlet check.")
    if ml["NPSHr"] is not None and numbers.get("NPSHa") is not None:
        numbers["NPSH_margin_m"] = float(numbers["NPSHa"]) - float(ml["NPSHr"])
        numbers["assumptions"].append("NPSHr from survey/catalog, not a correlation.")
    elif numbers.get("NPSHa") is not None:
        numbers["assumptions"].append("NPSHr missing — ask_survey. Cadfree will not invent it.")
    if ml["rho"] and ml["Q"] and numbers.get("H"):
        power = add("hydraulic_power", {"rho": ml["rho"], "Q": ml["Q"], "H": numbers["H"]})
        if power.get("ok"):
            numbers["P_W"] = power["value"]

    iterate = _iterate(status, ml, numbers)
    ok = any(w.get("ok") for w in worksheets)
    return {
        "ok": ok,
        "kind": "analytical",
        "solver": "meanline",
        "pack": "turbo",
        "meanline": numbers,
        "inputs": {k: v for k, v in ml.items() if v is not None},
        "missing": missing,
        "worksheets": worksheets,
        "iterate": iterate,
        "disclaimer": DISCLAIMER,
        "error": None if ok else ("missing SI inputs: " + ", ".join(missing) if missing else "meanline did not evaluate"),
    }


def _iterate(status: dict[str, Any], ml: dict[str, Any], numbers: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    params = (status.get("cadquery") or {}).get("params_mm") or {}
    allow = float((status.get("material") or {}).get("allowable") or 0) or 0.0
    sf_req = float((status.get("load") or {}).get("safety_factor") or 2.0)
    hoop = numbers.get("hoop_Pa")
    if hoop and allow:
        sf = allow / max(float(hoop), 1e-9)
        numbers["hoop_SF"] = sf
        if sf < sf_req:
            scale = math.sqrt(max(sf, 0.05) / sf_req)
            if "r2_mm" in params:
                out.append(
                    {
                        "param": "r2_mm",
                        "from": params["r2_mm"],
                        "to": round(float(params["r2_mm"]) * scale, 3),
                        "reason": f"thin-ring hoop SF {sf:.2f} < {sf_req:g} — shrink r2 or rpm (not CENTRIF FEA)",
                    }
                )
            else:
                out.append(
                    {
                        "param": "n_rpm",
                        "reason": f"thin-ring hoop SF {sf:.2f} < {sf_req:g}",
                        "scale": scale,
                    }
                )
    target = ml.get("target_H")
    h = numbers.get("H")
    if target and h is not None and float(h) < 0.85 * float(target):
        ratio = math.sqrt(float(target) / max(float(h), 1e-6))
        if "r2_mm" in params:
            out.append(
                {
                    "param": "r2_mm",
                    "from": params["r2_mm"],
                    "to": round(float(params["r2_mm"]) * min(ratio, 1.4), 3),
                    "reason": f"Euler head {h:.3g} m < target {target:g} m — raise r2 or n_rpm (meanline, not CFD)",
                }
            )
        elif "beta2_deg" in params:
            out.append(
                {
                    "param": "beta2_deg",
                    "reason": f"Euler head {h:.3g} m < target {target:g} m — a larger β2 (from tangential) raises Cu2",
                }
            )
    margin = numbers.get("NPSH_margin_m")
    if margin is not None and float(margin) < 0:
        out.append(
            {
                "param": "r1_mm",
                "reason": f"NPSHa − NPSHr = {margin:.3g} m. Enlarge the eye or drop Cm1. NPSHr was surveyed, not invented.",
            }
        )
    return out


def optimize_turbo(
    status: dict[str, Any],
    params: dict[str, Any],
    *,
    bounds: dict[str, Any] | None = None,
    goal: str = "head",
    max_evals: int = 40,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Coordinate descent on impeller PARAMS using meanline only — no mesh rebuild."""
    extra = dict(extra or {})
    goal = (goal or "head").strip().lower()
    if goal in {"turbo", "pareto"}:
        goal = "pareto"
    if goal not in {"head", "hoop", "pareto"}:
        goal = "head"
    current = {k: float(v) for k, v in (params or {}).items() if _num(v) is not None}
    lim: dict[str, tuple[float, float]] = {}
    for key in TURBO_PARAM_KEYS:
        val = _num(current.get(key))
        if val is None or val <= 0:
            continue
        lim[key] = (val * 0.7, val * 1.4)
        if key == "n_blades":
            lim[key] = (max(3.0, val - 3), val + 4)
        if key.startswith("beta"):
            lim[key] = (max(8.0, val - 15), min(70.0, val + 15))
    for key, spec in (bounds or {}).items():
        if isinstance(spec, dict):
            lo, hi = _num(spec.get("min") if spec.get("min") is not None else spec.get("lo")), _num(
                spec.get("max") if spec.get("max") is not None else spec.get("hi")
            )
        elif isinstance(spec, (list, tuple)) and len(spec) >= 2:
            lo, hi = _num(spec[0]), _num(spec[1])
        else:
            continue
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        lim[str(key)] = (float(lo), float(hi))
    if not lim:
        return {
            "ok": False,
            "error": "No impeller PARAMS (r2_mm, beta2_deg, n_blades, …). Call draft_impeller first.",
            "disclaimer": DISCLAIMER,
        }
    budget = max(6, min(int(max_evals or 40), 80))
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def eval_one(trial: dict[str, Any]) -> dict[str, Any] | None:
        key = tuple(sorted((k, round(float(trial[k]), 4)) for k in lim if k in trial))
        if key in seen:
            return None
        seen.add(key)
        merged = dict(current)
        merged.update(trial)
        report = run_meanline(status, {**extra, **merged, **{k: merged[k] for k in TURBO_PARAM_KEYS if k in merged}})
        ml = report.get("meanline") or {}
        h = _num(ml.get("H")) or 0.0
        hoop = _num(ml.get("hoop_Pa")) or 0.0
        target = _num(meanline_inputs(status, {**extra, **merged}).get("target_H"))
        head_err = abs(h - target) if target else -h
        row = {
            "params": {k: round(float(merged[k]), 4) for k in lim if k in merged},
            "H_m": round(h, 4),
            "hoop_Pa": hoop,
            "hoop_MPa": round(hoop / 1e6, 3) if hoop else None,
            "U2": ml.get("U2"),
            "ns": ml.get("ns"),
            "NPSHa": ml.get("NPSHa"),
            "head_err": head_err,
            "ok": bool(report.get("ok")),
        }
        rows.append(row)
        return row

    def rank(row: dict[str, Any]) -> tuple:
        fail = 0 if row.get("ok") else 1
        hoop = float(row.get("hoop_Pa") or 1e12)
        if goal == "hoop":
            return (fail, hoop, float(row.get("head_err") or 0))
        if goal == "pareto":
            return (fail, float(row.get("head_err") or 0) + hoop / 1e8, hoop)
        return (fail, float(row.get("head_err") or 0), hoop)

    seed = {k: current[k] for k in lim}
    best = eval_one(seed)
    if best is None:
        return {"ok": False, "error": "seed meanline failed", "disclaimer": DISCLAIMER}
    keys = list(lim)
    while len(rows) < budget:
        progressed = False
        for key in keys:
            if len(rows) >= budget:
                break
            lo, hi = lim[key]
            samples = [lo + (hi - lo) * i / 4.0 for i in range(5)]
            local = best
            for val in samples:
                if len(rows) >= budget:
                    break
                trial = dict(best["params"])
                trial[key] = round(min(max(val, lo), hi), 4)
                if key == "n_blades":
                    trial[key] = float(int(round(trial[key])))
                row = eval_one(trial)
                if row and rank(row) < rank(local):
                    local = row
                    progressed = True
            best = local
        if not progressed:
            break
    winner = min(rows, key=rank)
    return {
        "ok": True,
        "goal": goal,
        "evals": len(rows),
        "bounds": {k: {"min": lo, "max": hi} for k, (lo, hi) in lim.items()},
        "winner": winner,
        "candidates": sorted(rows, key=rank)[:12],
        "next": (
            "set_params with winner.params, then build_model, then run_solvers pack=turbo. "
            "Meanline was the objective — CalculiX CENTRIF / OpenRadioss verify stress, they were not in this loop."
        ),
        "disclaimer": DISCLAIMER + " Search does not rebuild CadQuery.",
    }


def pack_solvers() -> list[str]:
    return list(PACKS["turbo"]["solvers"])
