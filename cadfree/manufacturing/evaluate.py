from __future__ import annotations

from itertools import permutations
from typing import Any

from cadfree.catalog import MATERIALS, PROCESS_KINDS
from cadfree.manufacturing.mass import estimate_mass, resolve_material
from cadfree.manufacturing.strength import estimate_strength, parse_load_n
from cadfree.manufacturing.types import (
    Check,
    FeasibilityReport,
    MeshMetrics,
    Recommendation,
    Verdict,
)

SHEET_KINDS = {"laser_cut", "waterjet", "sheet_metal", "wood_cut"}


def _num(params: dict[str, Any], key: str, default: float) -> float:
    raw = params.get(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _bool(params: dict[str, Any], key: str, default: bool = False) -> bool:
    raw = params.get(key, default)
    if isinstance(raw, str):
        return raw.lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def fit_bbox(
    bbox: tuple[float, float, float],
    envelope: tuple[float, float, float],
    margin_mm: float = 2.0,
) -> tuple[bool, tuple[float, float, float] | None]:
    """True if some axis permutation of bbox fits in envelope with margin."""
    ex, ey, ez = (max(v - margin_mm, 0.0) for v in envelope)
    for perm in permutations(bbox):
        if perm[0] <= ex + 1e-6 and perm[1] <= ey + 1e-6 and perm[2] <= ez + 1e-6:
            return True, perm
    return False, None


def check_envelope(
    metrics: MeshMetrics, capability: dict[str, Any], *, sheet_like: bool
) -> Check:
    params = capability.get("params") or {}
    name = capability.get("name") or capability.get("kind")
    env = (
        _num(params, "bed_x_mm", 0),
        _num(params, "bed_y_mm", 0),
        _num(params, "max_z_mm", 0),
    )
    if min(env) <= 0:
        return Check(
            "envelope",
            "Build envelope",
            "warn",
            f"{name} is missing bed/travel numbers.",
            process=name,
        )
    ok, perm = fit_bbox(metrics.bbox_mm, env)
    bbox_txt = "×".join(f"{v:.1f}" for v in metrics.bbox_mm)
    env_txt = "×".join(f"{v:.0f}" for v in env)
    if ok:
        return Check(
            "envelope",
            "Build envelope",
            "pass",
            f"Part {bbox_txt} mm fits {name} envelope {env_txt} mm"
            + (f" (oriented {perm[0]:.1f}×{perm[1]:.1f}×{perm[2]:.1f})." if perm else "."),
            process=name,
            details={"orientation_mm": list(perm) if perm else None, "envelope_mm": list(env)},
        )
    extra = ""
    if sheet_like:
        extra = " Sheet processes cannot stand a thick part on edge to 'make it fit' if thickness exceeds max Z."
        # For sheet processes, only XY permutation, Z is thickness
        z_ok = metrics.bbox_mm[2] <= env[2] + 1e-6 or min(metrics.bbox_mm) <= env[2] + 1e-6
        if not z_ok:
            extra = " Thickness exceeds the process max."
    return Check(
        "envelope",
        "Build envelope",
        "fail",
        f"Part {bbox_txt} mm does not fit {name} envelope {env_txt} mm in any orientation.{extra}",
        process=name,
        details={"envelope_mm": list(env), "bbox_mm": list(metrics.bbox_mm)},
    )


def check_fdm_machine(metrics: MeshMetrics, capability: dict[str, Any], material: dict[str, Any]) -> list[Check]:
    params = capability.get("params") or {}
    name = capability.get("name") or "FDM printer"
    checks: list[Check] = []
    nozzle = _num(params, "nozzle_mm", 0.4)
    min_wall = 2.0 * nozzle
    thick = metrics.min_thickness_mm
    if thick is None:
        checks.append(
            Check(
                "min_wall",
                "Minimum wall",
                "warn",
                f"Could not sample thickness. Keep walls ≥ {min_wall:.1f} mm (2× {nozzle:.2f} mm nozzle).",
                process=name,
            )
        )
    elif thick + 1e-6 < min_wall:
        checks.append(
            Check(
                "min_wall",
                "Minimum wall",
                "fail",
                f"Sampled thickness {thick:.2f} mm is below 2× nozzle ({min_wall:.2f} mm). Thin walls will skip or shatter.",
                process=name,
            )
        )
    else:
        checks.append(
            Check(
                "min_wall",
                "Minimum wall",
                "pass",
                f"Sampled thickness {thick:.2f} mm ≥ {min_wall:.2f} mm nozzle rule.",
                process=name,
            )
        )

    overhang = metrics.overhang_ratio
    if overhang > 0.25:
        checks.append(
            Check(
                "overhangs",
                "Overhangs",
                "fail",
                f"{overhang:.0%} of surface area hangs past 45°. Redesign or accept heavy supports (and weaker down-faces).",
                process=name,
            )
        )
    elif overhang > 0.08:
        checks.append(
            Check(
                "overhangs",
                "Overhangs",
                "warn",
                f"{overhang:.0%} of area is past 45°. Printable with supports; expect uglier surfaces and extra filament.",
                process=name,
            )
        )
    else:
        checks.append(
            Check(
                "overhangs",
                "Overhangs",
                "pass",
                f"Overhang ratio {overhang:.0%} is modest.",
                process=name,
            )
        )

    extrude = material.get("extrude_c") or (0, 0)
    max_hot = _num(params, "max_hotend_c", 250)
    if isinstance(extrude, (list, tuple)) and extrude[-1] > max_hot + 1:
        checks.append(
            Check(
                "hotend",
                "Hotend temperature",
                "fail",
                f"{material.get('name')} wants up to {extrude[-1]}°C; {name} max hotend is {max_hot:.0f}°C.",
                process=name,
            )
        )
    else:
        checks.append(
            Check(
                "hotend",
                "Hotend temperature",
                "pass",
                f"{name} can reach the {material.get('name')} print temperature.",
                process=name,
            )
        )

    if material.get("needs_enclosure") and not _bool(params, "enclosure"):
        checks.append(
            Check(
                "enclosure",
                "Enclosure",
                "fail" if material.get("id") in {"pc", "nylon", "pa_cf"} else "warn",
                f"{material.get('name')} wants an enclosure. {name} does not have one — warp and delamination risk.",
                process=name,
            )
        )
    if material.get("needs_hardened_nozzle") and not _bool(params, "hardened_nozzle"):
        checks.append(
            Check(
                "nozzle_wear",
                "Hardened nozzle",
                "fail",
                f"{material.get('name')} is abrasive. {name} has no hardened nozzle — it will eat a brass nozzle.",
                process=name,
            )
        )
    return checks


def check_sheet_like(metrics: MeshMetrics, capability: dict[str, Any]) -> list[Check]:
    name = capability.get("name") or capability.get("kind")
    kind = capability.get("kind")
    bbox = sorted(metrics.bbox_mm)
    aspect = bbox[2] / max(bbox[0], 1e-6)  # thickest / thinnest
    checks: list[Check] = []
    if metrics.solidity < 0.15 and kind in {"laser_cut", "waterjet"}:
        checks.append(
            Check(
                "sheet_topology",
                "Sheet topology",
                "warn",
                "Part is very sparse; confirm it is a 2.5D cut profile, not a 3D lattice.",
                process=name,
            )
        )
    if aspect > 8 and kind in {"laser_cut", "waterjet", "wood_cut", "sheet_metal"}:
        checks.append(
            Check(
                "constant_thickness",
                "Constant thickness",
                "pass",
                f"BBox looks plate-like (aspect {aspect:.1f}:1).",
                process=name,
            )
        )
    elif kind in {"laser_cut", "waterjet", "sheet_metal"}:
        checks.append(
            Check(
                "constant_thickness",
                "Constant thickness",
                "fail",
                f"BBox {metrics.bbox_mm[0]:.1f}×{metrics.bbox_mm[1]:.1f}×{metrics.bbox_mm[2]:.1f} mm is not sheet-like. "
                f"{PROCESS_KINDS.get(kind, {}).get('label', kind)} cannot make arbitrary 3D solids.",
                process=name,
            )
        )
    return checks


def check_cnc(metrics: MeshMetrics, capability: dict[str, Any]) -> list[Check]:
    name = capability.get("name") or "CNC"
    params = capability.get("params") or {}
    tool = _num(params, "min_tool_mm", 3.0)
    axes = _num(params, "axes", 3)
    checks = []
    if metrics.min_thickness_mm is not None and metrics.min_thickness_mm < tool * 0.6:
        checks.append(
            Check(
                "thin_walls_cnc",
                "Thin walls",
                "warn",
                f"Sampled thickness {metrics.min_thickness_mm:.2f} mm is frail relative to a {tool:.1f} mm tool.",
                process=name,
            )
        )
    if axes <= 3 and metrics.overhang_ratio > 0.2:
        checks.append(
            Check(
                "undercuts",
                "Undercuts / 3-axis access",
                "fail",
                "Mesh has substantial downward faces off the bed plane — a 3-axis mill cannot cut undercuts without flipping, and may not at all.",
                process=name,
            )
        )
    else:
        checks.append(
            Check(
                "undercuts",
                "Tool access",
                "info",
                "Full 3-axis accessibility is not proven from the mesh. Avoid undercuts and sharp inside corners tighter than the tool radius.",
                process=name,
                details={"min_inside_radius_mm": tool / 2.0},
            )
        )
    return checks


def check_injection(metrics: MeshMetrics, capability: dict[str, Any]) -> list[Check]:
    name = capability.get("name") or "Injection mold"
    params = capability.get("params") or {}
    min_wall = _num(params, "min_wall_mm", 1.2)
    checks = []
    thick = metrics.min_thickness_mm
    if thick is not None and thick < min_wall:
        checks.append(
            Check(
                "im_wall",
                "Mold wall thickness",
                "fail",
                f"Sampled wall {thick:.2f} mm < {min_wall:.1f} mm minimum for this tool.",
                process=name,
            )
        )
    elif thick is not None:
        checks.append(
            Check(
                "im_wall",
                "Mold wall thickness",
                "pass",
                f"Sampled wall {thick:.2f} mm meets {min_wall:.1f} mm minimum.",
                process=name,
            )
        )
    checks.append(
        Check(
            "im_draft",
            "Draft & undercuts",
            "warn",
            "Cadfree cannot fully certify draft angles or side-actions from STL yet. "
            "Assume 1–2° draft, uniform walls, no undercuts unless you have slides.",
            process=name,
        )
    )
    return checks


def _compatible_materials(capability: dict[str, Any]) -> list[dict[str, Any]]:
    kind = capability.get("kind")
    listed = capability.get("materials") or []
    out: list[dict[str, Any]] = []
    for item in listed:
        try:
            out.append(resolve_material(item))
        except KeyError:
            if isinstance(item, dict):
                out.append(item)
    if out:
        return out
    return [m for m in MATERIALS.values() if kind in (m.get("process_kinds") or [])]


def _mass_budget_g(constraints: dict[str, Any]) -> float | None:
    for key in ("max_mass_g", "max_filament_g", "mass_budget_g"):
        if constraints.get(key) is not None:
            return float(constraints[key])
    return None


def evaluate_capability(
    metrics: MeshMetrics,
    capability: dict[str, Any],
    material: dict[str, Any],
    constraints: dict[str, Any],
) -> tuple[list[Check], dict[str, Any], dict[str, Any]]:
    kind = capability.get("kind") or ""
    name = capability.get("name") or kind
    infill = float(constraints.get("infill") or (0.35 if kind == "fdm" else 1.0))
    wall_mm = float(constraints.get("wall_mm") or 1.6)
    filament_d = _num(capability.get("params") or {}, "filament_diameter_mm", 1.75)

    checks = [
        check_envelope(metrics, capability, sheet_like=kind in SHEET_KINDS),
        Check(
            "material_process",
            "Material vs process",
            "pass"
            if kind in (material.get("process_kinds") or [])
            else "fail",
            f"{material.get('name')} is listed for {kind}."
            if kind in (material.get("process_kinds") or [])
            else f"{material.get('name')} cannot be processed as {kind}.",
            process=name,
        ),
    ]
    if kind == "fdm":
        checks.extend(check_fdm_machine(metrics, capability, material))
    elif kind in SHEET_KINDS:
        checks.extend(check_sheet_like(metrics, capability))
    elif kind in {"cnc_mill", "cnc_router"}:
        checks.extend(check_cnc(metrics, capability))
    elif kind == "injection_mold":
        checks.extend(check_injection(metrics, capability))

    mass = estimate_mass(
        metrics,
        material,
        process_kind=kind,
        infill=infill,
        wall_mm=wall_mm,
        filament_diameter_mm=filament_d,
    )
    budget = _mass_budget_g(constraints)
    if budget is not None:
        if mass["mass_g"] <= budget + 1e-6:
            checks.append(
                Check(
                    "mass_budget",
                    "Mass / filament budget",
                    "pass",
                    f"{mass['mass_g']:.1f} g ≤ {budget:.1f} g budget ({material.get('name')}"
                    + (f", {infill:.0%} infill" if kind == "fdm" else "")
                    + ").",
                    process=name,
                    details=mass,
                )
            )
        else:
            checks.append(
                Check(
                    "mass_budget",
                    "Mass / filament budget",
                    "fail",
                    f"{mass['mass_g']:.1f} g exceeds the {budget:.1f} g budget. "
                    "Lighten the geometry, drop infill, or raise the budget.",
                    process=name,
                    details=mass,
                )
            )

    strength = estimate_strength(
        metrics,
        material,
        constraints,
        process_kind=kind,
        infill=infill,
        wall_mm=wall_mm,
    )
    checks.append(
        Check(
            "strength",
            "Load capacity (first-order)",
            strength["status"],
            strength["message"],
            process=name,
            details={k: v for k, v in strength.items() if k != "message"},
        )
    )
    from cadfree.physics.thermal import check_service_temp

    thermal = check_service_temp(material, constraints)
    checks.append(
        Check(
            "service_temp",
            "Service temperature",
            thermal.get("status") or "warn",
            thermal.get("message") or "No thermal check.",
            process=name,
            details={k: v for k, v in thermal.items() if k not in {"message", "id"}},
        )
    )
    if not metrics.watertight:
        checks.append(
            Check(
                "watertight",
                "Solid model",
                "warn",
                "Mesh is not watertight. Volume/mass/strength numbers are approximate.",
                process=name,
            )
        )
    return checks, mass, strength


def _score(checks: list[Check]) -> tuple[int, int, int]:
    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    passes = sum(1 for c in checks if c.status == "pass")
    return fails, warns, passes


def _verdict_from(
    best_checks: list[Check],
    capabilities: list[dict[str, Any]],
    tried_materials: list[str],
) -> tuple[bool, Verdict, str, list[Recommendation]]:
    recs: list[Recommendation] = []
    fails = [c for c in best_checks if c.status == "fail"]
    if not fails:
        warns = [c for c in best_checks if c.status == "warn"]
        if warns:
            return True, "feasible", "Printable/machinable with warnings. Read the checks before you hit go.", recs
        return True, "feasible", "This part is possible on the selected shop setup with the stated spec.", recs

    fail_ids = {c.id for c in fails}
    if "material_process" in fail_ids or "hotend" in fail_ids or "nozzle_wear" in fail_ids or "enclosure" in fail_ids:
        recs.append(
            Recommendation(
                "material",
                "Current material is a mismatch for the machine. Swap filament/stock before changing the whole process.",
            )
        )
        verdict: Verdict = "needs_material_change"
    elif "envelope" in fail_ids:
        recs.append(
            Recommendation(
                "process",
                "The part does not fit this machine. Use a larger bed/travel, split the part, or pick another process.",
            )
        )
        verdict = "needs_process_change"
    elif "mass_budget" in fail_ids and "strength" not in fail_ids:
        recs.append(
            Recommendation(
                "spec",
                "Geometry holds the load but overruns the mass budget. Raise the budget or cut non-structural volume.",
            )
        )
        verdict = "needs_spec_change"
    elif "service_temp" in fail_ids:
        recs.append(
            Recommendation(
                "material",
                "Operating temperature exceeds this material's catalog service_temp_c. Use a higher-temp filament/stock or cool the part.",
            )
        )
        verdict = "needs_material_change"
    elif "strength" in fail_ids:
        recs.append(
            Recommendation(
                "material",
                "Load exceeds this material. Try PETG/PA-CF/aluminum, more infill, or a thicker section.",
            )
        )
        recs.append(
            Recommendation(
                "process",
                "If plastics cannot take the load inside the mass budget, this is a CNC or metal-AM part — or the spec is impossible.",
            )
        )
        verdict = "needs_material_change"
    elif "constant_thickness" in fail_ids:
        recs.append(
            Recommendation(
                "process",
                "This is a 3D solid. Laser, waterjet, wood cut, and sheet metal cannot make it. Use FDM, SLA, mill, or mold.",
            )
        )
        verdict = "needs_process_change"
    else:
        verdict = "infeasible"

    kinds = {c.get("kind") for c in capabilities}
    summary = (
        "Not possible as specified on the selected equipment. "
        + " ".join(c.message for c in fails[:2])
    )
    if len(capabilities) == 1 and "fdm" in kinds:
        recs.append(
            Recommendation(
                "process",
                "Only one production method is attached to this project. Add a mill, larger printer, or different filament in the workshop if you have them.",
            )
        )
    _ = tried_materials
    possible = False
    return possible, verdict, summary, recs


def evaluate(
    metrics: MeshMetrics,
    capabilities: list[dict[str, Any]],
    constraints: dict[str, Any],
    *,
    preferred_material_id: str | None = None,
) -> FeasibilityReport:
    if not capabilities:
        return FeasibilityReport(
            possible=False,
            verdict="needs_process_change",
            summary="No production methods on this project. Add what you actually have in the workshop.",
            checks=[
                Check(
                    "shop",
                    "Workshop",
                    "fail",
                    "Project has zero production methods.",
                )
            ],
            recommendations=[
                Recommendation("process", "Add an FDM printer, mill, laser, or other real process.")
            ],
            mass={},
            strength={},
            assumptions=[],
        )

    preferred = constraints.get("material_id") or preferred_material_id
    candidates: list[tuple[list[Check], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for cap in capabilities:
        materials = _compatible_materials(cap)
        if preferred:
            materials = sorted(
                materials, key=lambda m: 0 if m.get("id") == preferred else 1
            )
        if not materials:
            materials = [{"id": "unknown", "name": "unspecified", "process_kinds": [cap.get("kind")], "density_g_cm3": 1.2, "tensile_xy_mpa": 20.0, "tensile_z_mpa": 10.0}]
        for mat in materials:
            checks, mass, strength = evaluate_capability(metrics, cap, mat, constraints)
            candidates.append((checks, mass, strength, cap, mat))

    def rank(item: tuple) -> tuple:
        checks = item[0]
        fails, warns, passes = _score(checks)
        return (fails, warns, -passes)

    candidates.sort(key=rank)
    checks, mass, strength, cap, mat = candidates[0]
    possible, verdict, summary, recs = _verdict_from(
        checks, capabilities, [c[4].get("id") for c in candidates]
    )

    # If preferred material failed but another combo on the same shop passes, say so.
    if not possible:
        for alt_checks, alt_mass, alt_strength, alt_cap, alt_mat in candidates:
            alt_fails = sum(1 for c in alt_checks if c.status == "fail")
            if alt_fails == 0 and alt_mat.get("id") != mat.get("id"):
                recs.insert(
                    0,
                    Recommendation(
                        "material",
                        f"{alt_mat.get('name')} on {alt_cap.get('name')} passes these checks; {mat.get('name')} does not.",
                        from_value=mat.get("name"),
                        to_value=alt_mat.get("name"),
                    ),
                )
                break
            if alt_fails == 0 and alt_cap.get("id") != cap.get("id"):
                recs.insert(
                    0,
                    Recommendation(
                        "process",
                        f"{alt_cap.get('name')} can make this; {cap.get('name')} cannot.",
                        from_value=cap.get("name"),
                        to_value=alt_cap.get("name"),
                    ),
                )
                break

    assumptions = [
        "Strength is a first-order cantilever using bounding-box section properties — not FEA, not a lab coupon.",
        "FDM mass assumes shells plus infill, not a slicer.",
        "Build-volume check tries axis permutations; it does not pack multiple bodies.",
        "Passing checks means 'not physically impossible on this shop', not 'certified'.",
        "service_temp_c is catalog continuous-use, not a heat-deflection coupon. Unsurveyed operating temperature is a warning, not a pass.",
    ]
    load = parse_load_n(constraints)
    if load:
        assumptions.append(f"Applied load is treated as {load:.1f} N at the end of the longest bbox edge.")

    return FeasibilityReport(
        possible=possible,
        verdict=verdict,
        summary=summary,
        checks=checks,
        recommendations=recs,
        mass=mass,
        strength=strength,
        assumptions=assumptions,
        best_capability_id=cap.get("id"),
        best_material_id=mat.get("id"),
    )
