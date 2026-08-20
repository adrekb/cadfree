"""How Cadfree actually checks whether a part can take a load.

Three rungs, honest about what each one is:

0. First-order (always on)
   Bounding-box cantilever + printed knockdowns. Catches "50 lb on 8 g of PLA"
   in milliseconds. Not FEA.

1. MATLAB / Octave (agent mode, if installed)
   Same closed-form beam/plate maths an ME would write in a homework set:
   stress, deflection, buckling. The agent can also run arbitrary .m for
   custom load cases. GNU Octave is accepted as MATLAB.

2. Mesh FEA (optional, if gmsh + CalculiX `ccx` are on PATH)
   CadQuery/STL → tetrahedral mesh → linear static INP → von Mises. This is
   the real next step toward "can this be made AND will it hold". It is still
   not a certified coupon test.

See docs/SIMULATION.md for the research notes and why we did not start with
a black-box commercial solver.
"""

from __future__ import annotations

import math
import shutil
from typing import Any

from cadfree.catalog import MATERIALS
from cadfree.manufacturing.mass import resolve_material
from cadfree.manufacturing.strength import estimate_strength, parse_load_n
from cadfree.manufacturing.types import MeshMetrics
from cadfree.matlab.engine import find_engine, run_matlab


def probe() -> dict[str, Any]:
    gmsh = shutil.which("gmsh")
    ccx = shutil.which("ccx") or shutil.which("calculix")
    return {
        "first_order": {"available": True, "label": "Closed-form cantilever (always on)"},
        "matlab": find_engine(),
        "gmsh": {"available": bool(gmsh), "path": gmsh},
        "calculix": {"available": bool(ccx), "path": ccx},
    }


def octave_beam_script(metrics: MeshMetrics, material: dict[str, Any], constraints: dict[str, Any]) -> str:
    """Generate a MATLAB/Octave script for a conservative cantilever check."""
    bbox = metrics.bbox_mm
    span = max(bbox) / 1000.0  # metres
    others = sorted(d / 1000.0 for d in bbox)
    thickness, width = others[0], others[1]
    load = parse_load_n(constraints) or 0.0
    e_gpa = float(material.get("flex_modulus_gpa") or 2.0)
    allowable = float(material.get("tensile_xy_mpa") or 30.0) * 1e6  # Pa
    fill = float(constraints.get("infill") or 0.35)
    t_eff = thickness * math.sqrt(max(fill, 0.05))
    w_eff = width * math.sqrt(max(fill, 0.05))
    return f"""\
% Cadfree first-rung MATLAB check. SI units. Not FEA.
L = {span:.6g};          % span (m)
b = {w_eff:.6g};         % effective width (m)
h = {t_eff:.6g};         % effective thickness (m)
F = {load:.6g};          % tip load (N)
E = {e_gpa:.6g}e9;       % modulus (Pa)
allow = {allowable:.6g}; % allowable stress (Pa)
SF_req = {float(constraints.get("safety_factor") or 2.0):.6g};

I = b * h^3 / 12;
sigma = F * L * (h/2) / I;          % Pa
delta = F * L^3 / (3 * E * I);      % m
SF = allow / max(sigma, eps);
Pcr = pi^2 * E * I / (2*L)^2;       % Euler, fixed-free conservative

fprintf('sigma_MPa %.3f\\n', sigma/1e6);
fprintf('delta_mm %.3f\\n', delta*1000);
fprintf('SF %.3f\\n', SF);
fprintf('Pcr_N %.3f\\n', Pcr);
fprintf('pass %d\\n', SF >= SF_req);
"""


def run_octave_strength(
    metrics: MeshMetrics,
    material: str | dict[str, Any],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    mat = resolve_material(material)
    script = octave_beam_script(metrics, mat, constraints)
    result = run_matlab(script)
    parsed: dict[str, Any] = {}
    for line in (result.get("stdout") or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            key, raw = parts
            try:
                parsed[key] = float(raw)
            except ValueError:
                parsed[key] = raw
    result["parsed"] = parsed
    result["script"] = script
    result["rung"] = "matlab_octave"
    result["disclaimer"] = "Closed-form beam theory in MATLAB/Octave. Not a mesh FEA."
    return result


def run_first_order(
    metrics: MeshMetrics,
    material: str | dict[str, Any],
    constraints: dict[str, Any],
    process_kind: str = "fdm",
) -> dict[str, Any]:
    out = estimate_strength(metrics, material, constraints, process_kind=process_kind)
    out["rung"] = "first_order"
    return out


def simulate(
    metrics: MeshMetrics,
    material: str | dict[str, Any],
    constraints: dict[str, Any],
    *,
    process_kind: str = "fdm",
    prefer: str = "auto",
) -> dict[str, Any]:
    """Run the best available rung. Never pretends a missing solver ran."""
    status = probe()
    first = run_first_order(metrics, material, constraints, process_kind=process_kind)
    rungs = [first]
    used = "first_order"

    want_matlab = prefer in {"auto", "matlab", "octave"}
    if want_matlab and status["matlab"].get("available"):
        rungs.append(run_octave_strength(metrics, material, constraints))
        used = "matlab_octave"
    elif prefer in {"matlab", "octave"}:
        rungs.append(
            {
                "rung": "matlab_octave",
                "ok": False,
                "error": status["matlab"].get("install_hint") or "MATLAB/Octave not installed",
            }
        )

    fea_ready = status["gmsh"]["available"] and status["calculix"]["available"]
    if prefer == "fea" or (prefer == "auto" and fea_ready):
        if fea_ready:
            rungs.append(
                {
                    "rung": "calculix",
                    "ok": False,
                    "error": "Gmsh+CalculiX are installed, but the INP writer is not wired in this build yet. Use MATLAB/first-order, or see docs/SIMULATION.md.",
                }
            )
        elif prefer == "fea":
            rungs.append(
                {
                    "rung": "calculix",
                    "ok": False,
                    "error": "Mesh FEA needs `gmsh` and CalculiX `ccx` on PATH. See docs/SIMULATION.md.",
                }
            )

    return {
        "used": used,
        "probe": status,
        "rungs": rungs,
        "material_id": (material if isinstance(material, str) else material.get("id")),
        "disclaimer": (
            "Passing a simulation rung means the part is not obviously impossible. "
            "It is not a lab coupon, not anisotropic FDM FEA, and not a sign-off."
        ),
    }


def material_catalog_for_agent() -> list[dict[str, Any]]:
    return [
        {
            "id": m["id"],
            "name": m["name"],
            "process_kinds": m["process_kinds"],
            "density_g_cm3": m["density_g_cm3"],
            "tensile_xy_mpa": m["tensile_xy_mpa"],
            "tensile_z_mpa": m["tensile_z_mpa"],
        }
        for m in MATERIALS.values()
    ]
