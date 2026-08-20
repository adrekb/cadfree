import trimesh

from cadfree.catalog import MATERIALS
from cadfree.manufacturing.evaluate import evaluate, fit_bbox
from cadfree.manufacturing.mass import estimate_mass
from cadfree.manufacturing.mesh import metrics_from_mesh
from cadfree.manufacturing.strength import estimate_strength


def _box(x, y, z):
    return metrics_from_mesh(trimesh.creation.box(extents=(x, y, z)))


def test_tiny_printer_rejects_large_part():
    metrics = _box(300, 300, 40)
    caps = [{
        "id": "mini",
        "kind": "fdm",
        "name": "Prusa Mini+",
        "params": {"bed_x_mm": 180, "bed_y_mm": 180, "max_z_mm": 180, "nozzle_mm": 0.4, "max_hotend_c": 280},
        "materials": ["pla"],
    }]
    report = evaluate(metrics, caps, {"max_mass_g": 500, "load_lbf": 1, "safety_factor": 2})
    assert report.possible is False
    assert any(c.id == "envelope" and c.status == "fail" for c in report.checks)


def test_mass_budget_pla_box():
    metrics = _box(50, 20, 6)
    mass = estimate_mass(metrics, "pla", process_kind="fdm", infill=0.2, wall_mm=1.6)
    assert 0 < mass["mass_g"] < 50


def test_tpu_fails_structural_load():
    metrics = _box(80, 40, 8)
    strength = estimate_strength(metrics, "tpu", {"load_lbf": 50, "safety_factor": 2}, process_kind="fdm")
    assert strength["status"] == "fail"


def test_laser_rejects_chunky_solid():
    metrics = _box(80, 40, 40)
    caps = [{
        "id": "laser",
        "kind": "laser_cut",
        "name": "Hobby CO2",
        "params": {"bed_x_mm": 500, "bed_y_mm": 300, "max_z_mm": 8},
        "materials": ["acrylic"],
    }]
    report = evaluate(metrics, caps, {})
    assert report.possible is False
    assert any(c.id == "constant_thickness" and c.status == "fail" for c in report.checks)


def test_fit_bbox_permutation():
    ok, perm = fit_bbox((170, 10, 170), (180, 180, 180))
    assert ok
    assert perm is not None


def test_petg_listed():
    assert "petg" in MATERIALS
    assert 1.2 < MATERIALS["petg"]["density_g_cm3"] < 1.4
