"""Closed-form verification cases. These are the numbers FEA/handbook must hit.

Roark / NAFEMS-style benchmarks are the honesty check behind 'not Ansys':
if the formula book or a CalculiX job cannot reproduce a cantilever within a
stated band, we say so. Cases run without gmsh/ccx; the mesh rung is optional.
"""

from __future__ import annotations

from typing import Any

from cadfree.physics.engine import solve_formula

# NAFEMS LE10 is a thick-plate pressure case we do not implement. LE1-style
# uniaxial / cantilever numbers below are the shop-scale cousins.

ROARK_CANTILEVER = {
    "id": "roark_cantilever_tip",
    "title": "Roark cantilever, rectangular section, tip load",
    "source": "Roark's Formulas for Stress and Strain, Table 8.1 (end load, fixed-free)",
    "L": 0.080,  # m
    "b": 0.040,
    "t": 0.006,
    "F_N": 50.0 * 4.448221615,  # 50 lbf
    "E": 2.1e9,  # PETG-ish
    "formula": {
        "stress": "sigma = 6 F L / (b t^2)",
        "deflection": "delta = F L^3 / (3 E I), I = b t^3 / 12",
    },
}

NAFEMS_UNIAXIAL = {
    "id": "nafems_le1_cousin",
    "title": "Uniform bar, uniaxial tension (NAFEMS LE1 spirit, not the full plate)",
    "source": "NAFEMS LE1 is a 2-D plate; this is the 1-D bar the same patch test reduces to.",
    "L": 1.0,
    "A": 1e-4,  # 10 mm × 10 mm
    "F_N": 1e3,
    "E": 210e9,
    "nu": 0.3,
}


def roark_cantilever_truth(case: dict[str, Any] | None = None) -> dict[str, float]:
    c = case or ROARK_CANTILEVER
    L, b, t, F, E = c["L"], c["b"], c["t"], c["F_N"], c["E"]
    I = b * t**3 / 12.0
    sigma = 6.0 * F * L / (b * t**2)
    delta = F * L**3 / (3.0 * E * I)
    return {"sigma_Pa": sigma, "delta_m": delta, "I_m4": I, "F_N": F, "L": L, "b": b, "t": t, "E": E}


def check_formula_book_cantilever(tol: float = 1e-6) -> dict[str, Any]:
    truth = roark_cantilever_truth()
    stress = solve_formula(
        "cantilever_stress",
        {"F_N": truth["F_N"], "L": truth["L"], "b": truth["b"], "t": truth["t"]},
    )
    deflection = solve_formula(
        "cantilever_deflection",
        {"F_N": truth["F_N"], "L": truth["L"], "E": truth["E"], "b": truth["b"], "t": truth["t"]},
    )
    s_ok = bool(stress.get("ok")) and abs(float(stress["value"]) - truth["sigma_Pa"]) <= tol * max(
        abs(truth["sigma_Pa"]), 1.0
    )
    d_ok = bool(deflection.get("ok")) and abs(float(deflection["value"]) - truth["delta_m"]) <= tol * max(
        abs(truth["delta_m"]), 1e-12
    )
    return {
        "id": ROARK_CANTILEVER["id"],
        "ok": s_ok and d_ok,
        "stress": {"got": stress.get("value"), "want": truth["sigma_Pa"], "pass": s_ok},
        "deflection": {"got": deflection.get("value"), "want": truth["delta_m"], "pass": d_ok},
        "source": ROARK_CANTILEVER["source"],
        "disclaimer": "Closed-form identity check of the formula book. Not a mesh.",
    }


def uniaxial_bar_truth(case: dict[str, Any] | None = None) -> dict[str, float]:
    c = case or NAFEMS_UNIAXIAL
    sigma = c["F_N"] / c["A"]
    delta = sigma * c["L"] / c["E"]
    return {"sigma_Pa": sigma, "delta_m": delta, **{k: c[k] for k in ("L", "A", "F_N", "E")}}


def check_first_order_on_box(
    bbox_mm: tuple[float, float, float],
    load_n: float,
    material: dict[str, Any],
    *,
    band: float = 0.35,
) -> dict[str, Any]:
    """First-order cantilever vs Roark on the same rectangle. band is relative."""
    from cadfree.manufacturing.strength import estimate_strength
    from cadfree.manufacturing.types import MeshMetrics

    truth = roark_cantilever_truth(
        {
            **ROARK_CANTILEVER,
            "L": max(d / 1000.0 for d in bbox_mm),
            "b": sorted(d / 1000.0 for d in bbox_mm)[1],
            "t": min(d / 1000.0 for d in bbox_mm),
            "F_N": load_n,
            "E": float(material.get("flex_modulus_gpa") or 2.1) * 1e9,
        }
    )
    metrics = MeshMetrics(
        volume_mm3=bbox_mm[0] * bbox_mm[1] * bbox_mm[2],
        surface_area_mm2=2
        * (bbox_mm[0] * bbox_mm[1] + bbox_mm[1] * bbox_mm[2] + bbox_mm[2] * bbox_mm[0]),
        bbox_mm=bbox_mm,
        watertight=True,
        triangle_count=12,
        solidity=1.0,
    )
    est = estimate_strength(metrics, material, {"load_n": load_n, "safety_factor": 1.0}, process_kind="cnc_mill")
    got = float(est.get("max_stress_mpa") or 0.0) * 1e6
    want = truth["sigma_Pa"]
    rel = abs(got - want) / max(abs(want), 1.0)
    return {
        "id": "first_order_vs_roark",
        "ok": rel <= band,
        "got_Pa": got,
        "want_Pa": want,
        "relative_error": rel,
        "band": band,
        "strength": est,
        "note": (
            "First-order uses printed knockdowns and infill on FDM; this call used cnc_mill "
            "so the rectangle should match Roark within the band. Not FEA."
        ),
    }


def sphere_cd_truth(re: float) -> dict[str, Any]:
    """Order-of-magnitude Cd(Re) for a smooth sphere. Not our default blunt Cd=1."""
    if re < 1:
        cd = 24.0 / max(re, 1e-9)
        regime = "Stokes"
    elif re < 1e3:
        cd = 24.0 / re * (1.0 + 0.15 * re**0.687) + 0.42 / (1.0 + 4.25e4 * re**-1.16)
        regime = "intermediate"
    elif re < 2e5:
        cd = 0.47
        regime = "Newtonian"
    else:
        cd = 0.1
        regime = "critical"
    return {
        "Cd": cd,
        "Re": re,
        "regime": regime,
        "disclaimer": "Sphere correlation (White / standard drag curve). Catalog blunt-body default remains Cd=1.",
    }


def run_suite() -> dict[str, Any]:
    book = check_formula_book_cantilever()
    bar = uniaxial_bar_truth()
    from cadfree.catalog import MATERIALS

    first = check_first_order_on_box((80.0, 40.0, 6.0), 50.0 * 4.448221615, MATERIALS["petg"])
    return {
        "ok": book["ok"] and first["ok"],
        "cases": [book, {"id": NAFEMS_UNIAXIAL["id"], "truth": bar, "ok": True}, first],
        "disclaimer": (
            "Analytical identities + first-order vs Roark. Mesh FEA is a separate optional rung "
            "and is not faked when gmsh/ccx are missing."
        ),
    }
