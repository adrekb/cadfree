"""Fluid / aero handoff. CadQuery does not run CFD.

The SI mesh copy always lands in sim/fluids/. If OpenFOAM, Elmer, or SU2 is
on PATH we say so and leave a case card; we do not fake a RANS field.
Handbook drag/Re still run from the same snapshot so the agent can iterate.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from cadfree.physics.book import BY_ID
from cadfree.physics.engine import solve_formula
from cadfree.physics.snapshot import bind_formula


def probe_fluids() -> dict[str, Any]:
    names = {
        "openfoam_simple": shutil.which("simpleFoam"),
        "openfoam_potential": shutil.which("potentialFoam"),
        "elmer": shutil.which("ElmerSolver"),
        "su2": shutil.which("SU2_CFD"),
    }
    present = {k: v for k, v in names.items() if v}
    return {
        "available": bool(present),
        "engines": present,
        "all": names,
        "label": "OpenFOAM / Elmer / SU2 if installed; else handbook aero/pipe from the SI solid",
        "install_hint": "No CFD engine on PATH (simpleFoam, ElmerSolver, SU2_CFD). Analytical drag still uses the part snapshot.",
    }


def run_fluids(status: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    if extra.get("mrf") or extra.get("rotating") or extra.get("pack") == "turbo":
        from cadfree.physics.mrf import run_mrf

        return run_mrf(status, extra)
    probe = probe_fluids()
    sim = Path((status.get("paths") or {}).get("sim") or ".")
    dest = sim / "fluids"
    dest.mkdir(parents=True, exist_ok=True)
    files = (status.get("part") or {}).get("files") or {}
    stl = files.get("stl_m")
    geom = None
    if stl and Path(stl).is_file():
        target = dest / "part_si.stl"
        if Path(stl).resolve() != target.resolve():
            shutil.copy2(stl, target)
        geom = str(target)
    card = {
        "units": "SI",
        "geometry": geom,
        "fluid": (status.get("environment") or {}),
        "inlet_v_ms": (status.get("environment") or {}).get("v_ms"),
        "farfield": "bbox × 5 when a volume mesher is wired",
        "note": "This is the copy. CadQuery will not solve it.",
    }
    (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")

    worksheets = []
    for fid in ("reynolds", "dynamic_pressure", "drag_force", "aero_power"):
        formula = BY_ID[fid]
        bound, prov = bind_formula(status, formula)
        bound.update({k: v for k, v in extra.items() if v is not None})
        if fid == "aero_power" and worksheets:
            drag = next((w for w in worksheets if w.get("formula_id") == "drag_force"), None)
            if drag and drag.get("ok"):
                bound.setdefault("F_D", drag["value"])
        worksheets.append(solve_formula(fid, bound, provenance=prov))

    iterate = []
    drag = next((w for w in worksheets if w.get("formula_id") == "drag_force" and w.get("ok")), None)
    if drag and (status.get("cadquery") or {}).get("params_mm"):
        fd = float(drag["value"])
        load = (status.get("load") or {}).get("F_N")
        if load and fd > 0.5 * float(load):
            iterate.append(
                {
                    "param": "width_mm",
                    "reason": f"handbook drag {fd:.3g} N is a large fraction of the spec load",
                    "note": "Reduce projected area or Cd; this is not a CFD field.",
                }
            )

    from cadfree.physics.openfoam import probe_openfoam, run_openfoam

    of = run_openfoam(status, extra, try_run=bool(extra.get("run_cfd", True)))
    of_probe = probe_openfoam()
    if of.get("Cd") is not None:
        iterate.append(
            {
                "param": "width_mm",
                "reason": f"OpenFOAM forceCoeffs Cd={of['Cd']:.3g} on the coarse mesh",
                "note": "Not a y+ study. Compare to handbook drag_force before changing PARAMS.",
            }
        )

    cfd = {
        "ok": bool(any(w.get("ok") for w in worksheets) or of.get("ok")),
        "kind": "fluids",
        "solver": of.get("solver") if of.get("ran") else ("handbook+template"),
        "handoff": of.get("handoff") or str(dest),
        "geometry": geom,
        "probe": {**probe, "openfoam": of_probe},
        "worksheets": worksheets,
        "openfoam": of,
        "iterate": iterate,
        "disclaimer": (
            "Handbook aero/pipe from the SI snapshot plus an OpenFOAM simpleFoam template "
            "in sim/fluids/openfoam/. Cd from RANS is reported only if forceCoeffs parsed. "
            "Not a wind tunnel."
        ),
    }
    if of.get("Cd") is not None:
        cfd["Cd_cfd"] = of["Cd"]
        cfd["ok"] = True
        cfd["error"] = None
    elif of_probe["available"] and of.get("error"):
        cfd["error"] = of["error"]
        cfd["ok"] = any(w.get("ok") for w in worksheets)
    elif not geom:
        cfd["error"] = "No SI STL yet (build_model). Handbook numbers still used the bbox/PARAMS snapshot."
        cfd["ok"] = any(w.get("ok") for w in worksheets)
    else:
        cfd["ok"] = any(w.get("ok") for w in worksheets)
        if not cfd["ok"]:
            cfd["error"] = probe["install_hint"]
    return cfd
