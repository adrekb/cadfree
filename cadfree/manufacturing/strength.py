"""First-order 'will it hold' — not certified FEA.

We treat the part as a cantilever whose span is the longest bbox edge and whose
section is a rectangle scaled by solidity. Printed plastics get XY/Z knockdown
and an infill knockdown. The point is to catch impossible specs (50 lb on 8 g of
PLA) before anyone waits on a print.
"""

from __future__ import annotations

from typing import Any

from cadfree.manufacturing.mass import fdm_effective_fill, resolve_material
from cadfree.manufacturing.types import MeshMetrics

LBF_TO_N = 4.4482216153


def parse_load_n(constraints: dict[str, Any]) -> float | None:
    if constraints.get("load_n") is not None:
        return float(constraints["load_n"])
    if constraints.get("load_lbf") is not None:
        return float(constraints["load_lbf"]) * LBF_TO_N
    if constraints.get("load_lb") is not None:
        return float(constraints["load_lb"]) * LBF_TO_N
    if constraints.get("load_kg") is not None:
        return float(constraints["load_kg"]) * 9.80665
    return None


def _section_from_bbox(
    bbox: tuple[float, float, float], span_index: int
) -> tuple[float, float, float]:
    """Return span, width, thickness (mm) with thickness = smallest remaining side."""
    dims = list(bbox)
    span = dims.pop(span_index)
    dims.sort()
    thickness, width = dims[0], dims[1]
    return span, width, thickness


def estimate_strength(
    metrics: MeshMetrics,
    material: str | dict[str, Any],
    constraints: dict[str, Any],
    *,
    process_kind: str,
    infill: float = 0.35,
    wall_mm: float = 1.6,
    print_up_axis: str = "z",
) -> dict[str, Any]:
    mat = resolve_material(material)
    load_n = parse_load_n(constraints)
    safety = float(constraints.get("safety_factor") or 2.0)
    bbox = metrics.bbox_mm
    span_index = int(max(range(3), key=lambda i: bbox[i]))
    span_mm, width_mm, thick_mm = _section_from_bbox(bbox, span_index)
    # Effective thickness: solid parts keep bbox; sparse printed parts shrink with fill.
    if process_kind == "fdm":
        fill = fdm_effective_fill(metrics, wall_mm, infill)
    else:
        fill = max(metrics.solidity, 0.05)
    # Rectangle I = w t^3 / 12, with t scaled by sqrt(fill) so mass and stiffness move together.
    t_eff = max(thick_mm * (fill ** 0.5), 0.3)
    w_eff = max(width_mm * (fill ** 0.5), 0.3)
    i_mm4 = w_eff * (t_eff**3) / 12.0
    c_mm = t_eff / 2.0
    # Pick the weaker of XY vs Z if the long span is not in the layer plane.
    xy = float(mat.get("tensile_xy_mpa") or 20.0)
    z = float(mat.get("tensile_z_mpa") or xy)
    if process_kind == "fdm" and print_up_axis.lower() == "z" and span_index == 2:
        allowable_mpa = z
        orientation = "layers in tension (worst, span along Z)"
    else:
        allowable_mpa = xy if process_kind == "fdm" else min(xy, z)
        orientation = "layers mostly in-plane" if process_kind == "fdm" else "bulk isotropic"

    result: dict[str, Any] = {
        "material_id": mat.get("id"),
        "material_name": mat.get("name"),
        "load_n": load_n,
        "safety_factor_required": safety,
        "allowable_mpa": allowable_mpa,
        "orientation": orientation,
        "span_mm": span_mm,
        "section_width_mm": w_eff,
        "section_thickness_mm": t_eff,
        "method": "cantilever-bbox first-order (not FEA)",
        "flex_modulus_gpa": mat.get("flex_modulus_gpa"),
        "service_temp_c": mat.get("service_temp_c"),
        "tpu_warning": mat.get("id") == "tpu",
    }
    if load_n is None:
        result.update(
            {
                "status": "info",
                "message": "No load specified. Strength not scored.",
                "max_stress_mpa": None,
                "safety_factor_actual": None,
                "capacity_n": None,
            }
        )
        return result

    # σ = M c / I, M = F * span. Convert mm to MPa: N/mm^2 = MPa.
    moment_nmm = load_n * span_mm
    if i_mm4 <= 1e-9:
        stress_mpa = 1e9
    else:
        stress_mpa = (moment_nmm * c_mm) / i_mm4
    sf_actual = allowable_mpa / max(stress_mpa, 1e-9)
    capacity_n = (allowable_mpa * i_mm4) / max(c_mm * span_mm, 1e-9)
    capacity_with_sf = capacity_n / safety

    if mat.get("id") == "tpu" and load_n > 5:
        status = "fail"
        message = "TPU is an elastomer. It will not hold a structural load like a bracket."
    elif sf_actual >= safety:
        status = "pass"
        message = (
            f"First-order cantilever check: {stress_mpa:.1f} MPa vs {allowable_mpa:.0f} MPa "
            f"allowable ({mat.get('name')}). Estimated SF {sf_actual:.1f} (need {safety:.1f})."
        )
    elif sf_actual >= 1.0:
        status = "warn"
        message = (
            f"May hold the load but below the required safety factor "
            f"(SF {sf_actual:.1f} < {safety:.1f}). Thicker section, higher infill, or a stronger material."
        )
    else:
        status = "fail"
        message = (
            f"Predicted stress {stress_mpa:.1f} MPa exceeds {mat.get('name')} allowable "
            f"{allowable_mpa:.0f} MPa (SF {sf_actual:.2f}). This geometry/material will not take "
            f"{load_n:.0f} N on a {span_mm:.0f} mm span."
        )

    result.update(
        {
            "status": status,
            "message": message,
            "max_stress_mpa": stress_mpa,
            "safety_factor_actual": sf_actual,
            "capacity_n": capacity_n,
            "capacity_n_with_safety": capacity_with_sf,
        }
    )
    return result
