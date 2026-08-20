from __future__ import annotations

from typing import Any

from cadfree.catalog import MATERIALS
from cadfree.manufacturing.types import MeshMetrics


def resolve_material(material: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(material, dict):
        mid = material.get("id") or material.get("material_id")
        if mid and mid in MATERIALS:
            base = dict(MATERIALS[mid])
            base.update({k: v for k, v in material.items() if v is not None})
            return base
        return material
    if material in MATERIALS:
        return MATERIALS[material]
    raise KeyError(f"Unknown material '{material}'")


def fdm_effective_fill(metrics: MeshMetrics, wall_mm: float, infill: float) -> float:
    """Approximate printed solid fraction from shells + infill."""
    volume = max(metrics.volume_mm3, 1.0)
    shell_vol = metrics.surface_area_mm2 * max(wall_mm, 0.0)
    shell_frac = float(min(0.95, shell_vol / volume))
    infill = min(max(infill, 0.0), 1.0)
    return shell_frac + (1.0 - shell_frac) * infill


def estimate_mass(
    metrics: MeshMetrics,
    material: str | dict[str, Any],
    *,
    process_kind: str,
    infill: float = 0.35,
    wall_mm: float = 1.6,
    filament_diameter_mm: float = 1.75,
) -> dict[str, Any]:
    mat = resolve_material(material)
    density = float(mat.get("density_g_cm3") or 1.2)
    volume_cm3 = metrics.volume_mm3 / 1000.0
    if process_kind == "fdm":
        fill = fdm_effective_fill(metrics, wall_mm, infill)
        mass_g = volume_cm3 * density * fill
        filament_mm3 = mass_g / density * 1000.0
        area = 3.141592653589793 * (filament_diameter_mm / 2.0) ** 2
        filament_m = (filament_mm3 / area) / 1000.0
        return {
            "mass_g": mass_g,
            "solid_mass_g": volume_cm3 * density,
            "effective_fill": fill,
            "infill": infill,
            "wall_mm": wall_mm,
            "density_g_cm3": density,
            "filament_m": filament_m,
            "volume_cm3": volume_cm3,
            "material_id": mat.get("id"),
            "material_name": mat.get("name"),
            "process_kind": process_kind,
        }
    mass_g = volume_cm3 * density
    return {
        "mass_g": mass_g,
        "solid_mass_g": mass_g,
        "effective_fill": 1.0,
        "infill": 1.0,
        "wall_mm": None,
        "density_g_cm3": density,
        "filament_m": None,
        "volume_cm3": volume_cm3,
        "material_id": mat.get("id"),
        "material_name": mat.get("name"),
        "process_kind": process_kind,
    }
