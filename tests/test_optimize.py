"""PARAMS inner loop vs rung-0 feasibility — no CadQuery rebuild."""

from cadfree.cad.optimize import default_bounds, optimize_params, scale_metrics
from cadfree.manufacturing.mesh import metrics_from_mesh
from cadfree.manufacturing.types import MeshMetrics

try:
    import trimesh
except ImportError:
    trimesh = None


CAPS = [
    {
        "id": "x1",
        "kind": "fdm",
        "name": "Bambu",
        "params": {
            "bed_x_mm": 256,
            "bed_y_mm": 256,
            "max_z_mm": 256,
            "nozzle_mm": 0.4,
            "max_hotend_c": 300,
        },
        "materials": ["petg"],
    }
]


def _metrics() -> MeshMetrics:
    if trimesh is not None:
        return metrics_from_mesh(trimesh.creation.box(extents=(80, 40, 6)))
    return MeshMetrics(
        volume_mm3=80 * 40 * 6,
        surface_area_mm2=2 * (80 * 40 + 80 * 6 + 40 * 6),
        bbox_mm=(80.0, 40.0, 6.0),
        watertight=True,
        triangle_count=12,
        solidity=1.0,
        min_thickness_mm=6.0,
    )


def test_default_bounds_skip_holes():
    b = default_bounds({"thickness_mm": 6.0, "width_mm": 40.0, "hole_d_mm": 5.2, "fillet_mm": 1.2})
    assert "thickness_mm" in b and "width_mm" in b
    assert "hole_d_mm" not in b and "fillet_mm" not in b
    assert b["thickness_mm"][0] >= 1.0


def test_scale_metrics_thickness_only():
    m = _metrics()
    old = {"thickness_mm": 6.0, "width_mm": 40.0}
    new = {"thickness_mm": 12.0, "width_mm": 40.0}
    scaled = scale_metrics(m, old, new)
    assert scaled.volume_mm3 == m.volume_mm3 * 2.0
    assert min(scaled.bbox_mm) == min(m.bbox_mm) * 2.0


def test_goal_sf_goes_thicker_than_mass():
    m = _metrics()
    params = {"thickness_mm": 6.0, "width_mm": 40.0, "foot_mm": 50.0}
    cons = {"load_n": 80.0, "safety_factor": 2.0, "material_id": "petg", "max_mass_g": 500}
    bounds = {"thickness_mm": {"min": 3.0, "max": 12.0}}
    mass = optimize_params(m, CAPS, cons, params, bounds=bounds, goal="mass", max_evals=20)
    sf = optimize_params(m, CAPS, cons, params, bounds=bounds, goal="sf", max_evals=20)
    assert mass["ok"] and sf["ok"]
    assert mass["evals"] <= 20 and sf["evals"] <= 20
    t_mass = mass["winner"]["params"]["thickness_mm"]
    t_sf = sf["winner"]["params"]["thickness_mm"]
    assert t_sf >= t_mass
    assert sf["winner"]["sf"] >= mass["winner"]["sf"] or t_sf > t_mass


def test_pareto_returns_possible_winner():
    m = _metrics()
    params = {"thickness_mm": 6.0, "width_mm": 40.0}
    out = optimize_params(
        m,
        CAPS,
        {"load_n": 20.0, "safety_factor": 2.0, "material_id": "petg"},
        params,
        bounds={"thickness_mm": {"min": 4.0, "max": 10.0}},
        goal="pareto",
        max_evals=16,
    )
    assert out["ok"] is True
    assert out["winner"]["params"]["thickness_mm"] >= 4.0
    assert "not rebuilt" in out["disclaimer"].lower() or "not FEA" in out["disclaimer"]
