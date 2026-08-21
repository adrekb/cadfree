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

    extra = extra or {}
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

    cfd = {
        "ok": False,
        "kind": "fluids",
        "solver": "none",
        "handoff": str(dest),
        "geometry": geom,
        "probe": probe,
        "worksheets": worksheets,
        "iterate": iterate,
        "disclaimer": (
            "Handbook aero/pipe from the SI snapshot. Not OpenFOAM, not a wind tunnel. "
            "If a CFD engine is installed, the mesh copy is in sim/fluids/ — this build does not "
            "run a RANS loop."
        ),
    }
    if probe["available"]:
        engine = next(iter(probe["engines"]))
        cfd["solver"] = engine
        cfd["error"] = (
            f"{engine} is on PATH. Case card and SI STL are in {dest}. "
            "Cadfree does not drive a full RANS/LES loop in this build — use the engine on that copy, "
            "or iterate from the handbook worksheets."
        )
    else:
        cfd["ok"] = any(w.get("ok") for w in worksheets)
        if not geom:
            cfd["error"] = "No SI STL yet (build_model). Handbook numbers still used the bbox/PARAMS snapshot."
        elif not any(w.get("ok") for w in worksheets):
            cfd["error"] = probe["install_hint"]
    return cfd
