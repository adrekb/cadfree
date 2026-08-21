from cadfree.catalog import MATERIALS
from cadfree.manufacturing.evaluate import evaluate
from cadfree.manufacturing.mesh import metrics_from_mesh
from cadfree.physics.engine import solve_formula
from cadfree.physics.thermal import check_service_temp, operating_temp_c, run_thermal_handbook
import trimesh


def _box(x, y, z):
    return metrics_from_mesh(trimesh.creation.box(extents=(x, y, z)))


def test_operating_temp_not_invented_for_indoor():
    t, prov = operating_temp_c({"environment": "indoor dry"})
    assert t is None
    assert "missing" in prov


def test_hot_motors_named_80c_assumption():
    t, prov = operating_temp_c({"environment": "hot / near motors"})
    assert t == 80.0
    assert "80" in prov


def test_pla_fails_hot_service_temp():
    hit = check_service_temp(MATERIALS["pla"], {"operating_temp_c": 90})
    assert hit["status"] == "fail"
    assert hit["ok"] is False


def test_petg_passes_room_service_temp():
    hit = check_service_temp(MATERIALS["petg"], {"operating_temp_c": 25})
    assert hit["status"] == "pass"


def test_feasibility_includes_service_temp_check():
    metrics = _box(50, 20, 6)
    caps = [
        {
            "id": "mini",
            "kind": "fdm",
            "name": "Prusa Mini+",
            "params": {
                "bed_x_mm": 180,
                "bed_y_mm": 180,
                "max_z_mm": 180,
                "nozzle_mm": 0.4,
                "max_hotend_c": 280,
            },
            "materials": ["pla"],
        }
    ]
    report = evaluate(
        metrics,
        caps,
        {"max_mass_g": 500, "load_lbf": 1, "safety_factor": 2, "operating_temp_c": 90, "material_id": "pla"},
    )
    assert any(c.id == "service_temp" and c.status == "fail" for c in report.checks)
    assert report.possible is False


def test_lumped_tss_energy_balance():
    out = solve_formula("lumped_Tss", {"T_inf": 298.15, "Qdot": 5.0, "h": 10.0, "A": 0.02})
    assert out["ok"] is True
    assert abs(out["value"] - (298.15 + 5.0 / 0.2)) < 1e-9


def test_handbook_thermal_on_snapshot_shape():
    status = {
        "inputs": {"A_surf": 0.01, "t": 0.004, "V": 2e-5},
        "material": {"name": "PETG", "k": 0.19, "cp": 1800, "rho": 1270, "service_temp_c": 70},
        "environment": {"T_inf_c": 25},
        "part": {"area_m2": 0.01, "volume_m3": 2e-5, "bbox_m": [0.08, 0.04, 0.006]},
        "paths": {"sim": "/tmp"},
    }
    hit = run_thermal_handbook(status, {"Qdot": 2.0})
    assert hit["ok"] is True
    assert hit["T_ss_C"] is not None
    assert hit["service_temp"]["id"] == "service_temp"
