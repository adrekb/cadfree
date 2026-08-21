"""Send the SI part copy to packaged solvers; feed numbers back for PARAMS iterate.

CadQuery never runs friction, FEA, or CFD. It writes the solid. This module
snapshots that solid in SI, copies the mesh into each solver's folder, runs
whatever is actually installed, and returns iterate hints. Missing tools are
named in one sentence.
"""

from __future__ import annotations

import json
from typing import Any

from cadfree.matlab.engine import find_engine
from cadfree.physics.book import BY_ID, PACKS, lookup_formula
from cadfree.physics.engine import solve_formula, sympy_status
from cadfree.physics.fea import probe_fea, run_fea
from cadfree.physics.fluids import probe_fluids, run_fluids
from cadfree.physics.snapshot import bind_formula, sim_dir, write_si_status
from cadfree.physics.topology import probe_generate, run_generate


def probe_solvers() -> dict[str, Any]:
    fea = probe_fea()
    fluids = probe_fluids()
    return {
        "analytical": {
            "available": True,
            "label": "Formula book (friction, fluids, aero, beams) on the SI snapshot",
        },
        "sympy": sympy_status(),
        "fea": fea,
        "fluids": fluids,
        "matlab": find_engine(),
        "kinematics": {
            "available": True,
            "label": "Planar four-bar / open chain / gear pitch + AABB clash",
        },
        "first_order": {"available": True, "label": "Closed-form cantilever (always on)"},
        "topology": probe_generate(),
        "contract": (
            "CadQuery → SI status.json + part_si.stl copies → solvers → iterate PARAMS. "
            "Never invent a mesh or CFD result. Topology is packaged SIMP, not Fusion GD."
        ),
    }


def _analytical_ids(status: dict[str, Any], pack: str | None) -> list[str]:
    if pack and pack in PACKS:
        return list(PACKS[pack]["formulas"])
    ids = ["cantilever_stress", "cantilever_deflection"]
    env = status.get("environment") or {}
    inputs = status.get("inputs") or {}
    if env.get("v_ms") or inputs.get("v"):
        ids.extend(["reynolds", "dynamic_pressure", "drag_force", "aero_power"])
    if inputs.get("mu") or env.get("pair"):
        ids.extend(["coulomb_friction", "friction_power", "pv_bushing"])
    if inputs.get("Q") or inputs.get("hole_d"):
        ids.append("reynolds")
    # unique, stable order
    seen: list[str] = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    return seen


def run_analytical(
    status: dict[str, Any], extra: dict[str, Any] | None = None, pack: str | None = None
) -> dict[str, Any]:
    extra = extra or {}
    worksheets = []
    for fid in _analytical_ids(status, pack):
        formula = BY_ID[fid]
        bound, prov = bind_formula(status, formula)
        bound.update({k: v for k, v in extra.items() if v is not None})
        if fid == "friction_power":
            prev = next((w for w in worksheets if w.get("formula_id") == "coulomb_friction" and w.get("ok")), None)
            if prev:
                bound.setdefault("F_f", prev["value"])
        if fid == "aero_power":
            prev = next((w for w in worksheets if w.get("formula_id") == "drag_force" and w.get("ok")), None)
            if prev:
                bound.setdefault("F_D", prev["value"])
        worksheets.append(solve_formula(fid, bound, provenance=prov))

    iterate = []
    stress = next((w for w in worksheets if w.get("formula_id") == "cantilever_stress" and w.get("ok")), None)
    sf_req = float((status.get("load") or {}).get("safety_factor") or 2.0)
    allow = float((status.get("material") or {}).get("allowable") or 0)
    params_mm = (status.get("cadquery") or {}).get("params_mm") or {}
    if stress and allow:
        sf = allow / max(float(stress["value"]), 1e-9)
        stress["SF"] = sf
        if sf < sf_req and "thickness_mm" in params_mm:
            scale = (sf_req / max(sf, 0.05)) ** 0.5
            iterate.append(
                {
                    "param": "thickness_mm",
                    "from": params_mm["thickness_mm"],
                    "to": round(float(params_mm["thickness_mm"]) * scale, 3),
                    "reason": f"handbook bending SF {sf:.2f} < {sf_req:g} (σ from the SI solid, not FEA)",
                }
            )
    return {
        "ok": any(w.get("ok") for w in worksheets),
        "kind": "analytical",
        "solver": "formula_book",
        "worksheets": worksheets,
        "iterate": iterate,
        "disclaimer": (
            "Closed-form handbook on the SI snapshot of the CadQuery solid. "
            "Not mesh FEA, not CFD."
        ),
    }


def run_solvers(
    project_id: str,
    solvers: list[str] | None = None,
    values: dict[str, Any] | None = None,
    pack: str | None = None,
    part_id: str | None = None,
) -> dict[str, Any]:
    status = write_si_status(project_id, part_id)
    want = solvers or ["analytical", "fea", "fluids"]
    want = [s.lower().strip() for s in want]
    extra = dict(values or {})
    results: list[dict[str, Any]] = []
    if "analytical" in want:
        results.append(run_analytical(status, extra, pack=pack))
    if "fea" in want or "calculix" in want or "fem" in want:
        results.append(run_fea(status))
    if "fluids" in want or "cfd" in want or "aero" in want:
        results.append(run_fluids(status, extra))
    if "topology" in want or "generate" in want or "simp" in want:
        vf = extra.get("volfrac") or extra.get("target_mass_fraction")
        try:
            vf = float(vf) if vf is not None else None
        except (TypeError, ValueError):
            vf = None
        results.append(
            run_generate(
                project_id,
                part_id=part_id,
                volfrac=vf,
                design_space=str(extra.get("design_space") or "part"),
                assumed_load=bool(extra.get("assumed_load", True)),
            )
        )

    iterate: list[dict[str, Any]] = []
    for r in results:
        iterate.extend(r.get("iterate") or [])
    payload = {
        "ok": True,
        "snapshot": status["paths"],
        "part_built": bool((status.get("part") or {}).get("built")),
        "probe": probe_solvers(),
        "results": results,
        "iterate": iterate,
        "missing_inputs": status.get("missing") or [],
        "next": (
            "set_params with iterate[].param, then build_model, then run_solvers again. "
            "Do not invent FEA/CFD numbers that are not in results[]."
        ),
        "disclaimer": probe_solvers()["contract"],
    }
    last = sim_dir(project_id) / "last.json"
    last.write_text(json.dumps(payload, indent=2, default=str)[:400000], encoding="utf-8")
    return payload


def solve_on_part(
    project_id: str,
    formula_id: str,
    values: dict[str, Any] | None = None,
    solve_for: str | None = None,
    part_id: str | None = None,
) -> dict[str, Any]:
    status = write_si_status(project_id, part_id)
    formula = BY_ID.get(formula_id)
    if not formula:
        return lookup_formula(formula_id)
    bound, prov = bind_formula(status, formula)
    bound.update({k: v for k, v in (values or {}).items() if v is not None})
    result = solve_formula(formula_id, bound, solve_for=solve_for, provenance=prov)
    result["snapshot"] = status["paths"]
    result["part_built"] = bool((status.get("part") or {}).get("built"))
    return result
