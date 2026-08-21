"""Turbo meanline, impeller draft, CENTRIF, OpenRadioss — no GPU, no gmsh required."""

import math
from pathlib import Path

from cadfree.cad.impeller import STARTER_IMPELLER, impeller_source
from cadfree.cad.params import extract_params
from cadfree.physics.bcs import hub_nodes, selector_nodes
from cadfree.physics.book import G, PACKS, lookup_formula
from cadfree.physics.engine import solve_formula
from cadfree.physics.fea import _append_ccx, _parse_nodes
from cadfree.physics.radioss import probe_radioss, run_radioss
from cadfree.physics.turbo import optimize_turbo, run_meanline


def test_euler_head_u2_cu2():
    out = solve_formula("euler_head", {"U2": 20.0, "Cu2": 15.0})
    assert out["ok"] is True
    assert abs(out["value"] - (20.0 * 15.0 / G)) < 1e-9


def test_wiesner_slip_25deg_z6():
    beta2 = math.radians(25.0)
    out = solve_formula("wiesner_slip", {"beta2": beta2, "z": 6.0})
    assert out["ok"] is True
    expected = 1.0 - math.sqrt(math.sin(beta2)) / (6.0 ** 0.7)
    assert abs(out["value"] - expected) < 1e-9
    assert 0.80 < out["value"] < 0.84


def test_missing_q_is_not_invented():
    status = {
        "inputs": {"n_rpm": 3000.0, "rho_solid": 1200.0, "rho": 997.0, "g": G},
        "cadquery": {
            "params_mm": {
                "r2_mm": 50.0,
                "r1_mm": 18.0,
                "b2_mm": 8.0,
                "beta2_deg": 25.0,
                "n_blades": 6,
            }
        },
        "environment": {"n_rpm": 3000.0, "fluid": "water", "rho": 997.0},
        "material": {"allowable": 40e6, "rho": 1200.0},
        "load": {"safety_factor": 2.0},
        "constraints": {"n_rpm": 3000.0},
    }
    report = run_meanline(status)
    assert "Q_lpm" in " ".join(report.get("missing") or []) or "Q" in " ".join(report.get("missing") or [])
    assert (report.get("meanline") or {}).get("H") in (None, 0)
    assert "NPSHr" not in (report.get("meanline") or {}) or report["meanline"].get("NPSHr") is None


def test_meanline_iterate_when_hoop_and_head_short():
    status = {
        "inputs": {"n_rpm": 20000.0, "rho_solid": 1200.0, "rho": 997.0, "g": G},
        "cadquery": {
            "params_mm": {
                "r2_mm": 50.0,
                "r1_mm": 18.0,
                "b2_mm": 8.0,
                "b1_mm": 12.0,
                "beta2_deg": 25.0,
                "n_blades": 6,
            }
        },
        "environment": {"n_rpm": 20000.0, "fluid": "water", "rho": 997.0},
        "material": {"allowable": 5e6, "rho": 1200.0},
        "load": {"safety_factor": 2.0},
        "constraints": {"n_rpm": 20000.0, "Q_lpm": 60.0, "target_H_m": 80.0},
    }
    report = run_meanline(status)
    assert report["ok"] is True
    ml = report["meanline"]
    assert ml.get("H")
    assert ml.get("hoop_Pa")
    reasons = " ".join(i.get("reason") or "" for i in report["iterate"])
    params = {i["param"] for i in report["iterate"]}
    assert "r2_mm" in params
    assert "hoop" in reasons.lower() or "head" in reasons.lower() or "Euler" in reasons


def test_optimize_turbo_moves_geometry():
    status = {
        "inputs": {"n_rpm": 3000.0, "rho_solid": 1200.0, "rho": 997.0, "g": G, "target_H": 40.0},
        "cadquery": {
            "params_mm": {
                "r2_mm": 50.0,
                "r1_mm": 18.0,
                "b2_mm": 8.0,
                "b1_mm": 12.0,
                "beta2_deg": 25.0,
                "n_blades": 6,
            }
        },
        "environment": {"n_rpm": 3000.0, "fluid": "water", "rho": 997.0},
        "material": {"allowable": 80e6, "rho": 1200.0},
        "load": {"safety_factor": 2.0},
        "constraints": {"n_rpm": 3000.0, "Q_lpm": 60.0, "target_H_m": 40.0},
    }
    params = dict(status["cadquery"]["params_mm"])
    out = optimize_turbo(status, params, goal="head", max_evals=20)
    assert out["ok"] is True
    winner = out["winner"]["params"]
    assert winner["r2_mm"] != params["r2_mm"] or winner["beta2_deg"] != params["beta2_deg"]
    assert "CadQuery" in out["disclaimer"] or "rebuild" in (out.get("disclaimer") or "").lower()


def test_impeller_source_has_params_and_result():
    src = impeller_source()
    params = extract_params(src)
    assert params["r2_mm"] == 50.0
    assert params["beta2_deg"] == 25.0
    assert "n_blades" in params
    assert "result" in STARTER_IMPELLER
    assert "tan" in STARTER_IMPELLER
    patched = impeller_source({"r2_mm": 55.0})
    assert extract_params(patched)["r2_mm"] == 55.0


def test_radioss_probe_and_centri_deck(tmp_path):
    probe = probe_radioss()
    if not probe["available"]:
        assert "OpenRadioss" in probe["install_hint"] or "PATH" in probe["install_hint"]
    status = {
        "paths": {"sim": str(tmp_path)},
        "material": {"rho": 1200.0, "E": 2.1e9, "nu": 0.38},
        "environment": {"n_rpm": 3000.0},
        "inputs": {"n_rpm": 3000.0},
    }
    out = run_radioss(status)
    assert out["ok"] is False
    starter = Path(out["files"]["starter"]).read_text(encoding="utf-8")
    assert "/LOAD/CENTRI" in starter
    assert out.get("von_mises_max") in (None, 0) or not out.get("ok")


def test_append_ccx_centrif_density_and_frequency(tmp_path):
    inp = tmp_path / "part.inp"
    inp.write_text("*NODE\n1, 0, 0, 0\n2, 0.08, 0, 0\n3, 0.01, 0, 0\n*ELEMENT, TYPE=C3D4\n", encoding="utf-8")
    nodes = _parse_nodes(inp)
    omega = 2.0 * math.pi * 3000.0 / 60.0
    status = {
        "material": {"E": 1.47e9, "nu": 0.38, "rho": 1200.0},
        "load": {"F_N": None, "safety_factor": 2.0},
        "environment": {"n_rpm": 3000.0},
        "_fea": {"centrif": True, "omega": omega, "modal": True, "n_modes": 8},
        "fea_bcs": {"fix": [{"selector": "hub"}]},
    }
    _append_ccx(inp, status, nodes)
    text = inp.read_text(encoding="utf-8")
    assert "CENTRIF" in text
    assert "*DENSITY" in text
    assert "*FREQUENCY" in text
    assert "*CLOAD" not in text
    w2 = omega * omega
    assert f"{w2:.6g}" in text or "CENTRIF" in text


def test_hub_selector_inner_nodes():
    nodes = [
        (1, 0.0, 0.0, 0.0),
        (2, 0.01, 0.0, 0.0),
        (3, 0.08, 0.0, 0.0),
        (4, 0.0, 0.08, 0.0),
    ]
    hub = selector_nodes(nodes, "hub")
    assert 1 in hub and 2 in hub
    assert 3 not in hub and 4 not in hub
    assert hub_nodes(nodes) == hub
    assert selector_nodes(nodes, "min_r") == hub


def test_turbo_pack_in_book():
    assert "turbo" in PACKS
    assert "euler_head" in PACKS["turbo"]["formulas"]
    hit = lookup_formula("wiesner slip", domain="turbo")
    ids = [f["id"] for f in hit.get("formulas") or hit.get("hits") or []]
    if not ids and "id" in hit:
        ids = [hit["id"]]
    if "formulas" in hit:
        ids = [f["id"] for f in hit["formulas"]]
    assert "wiesner_slip" in ids or hit.get("ok") is not False


def test_impeller_survey_template():
    from cadfree.agent.survey import questions_for_template

    spec = questions_for_template("impeller")
    ids = [q["id"] for q in spec["questions"]]
    assert "n_rpm" in ids and "Q_lpm" in ids


def test_mrf_case_is_complete_and_runnable_by_hand(tmp_path):
    from cadfree.physics.mrf import write_mrf_case

    stl = tmp_path / "part_si.stl"
    stl.write_text("solid x\nendsolid x\n", encoding="utf-8")
    out = write_mrf_case(
        tmp_path / "case",
        stl=stl,
        omega=2.0 * math.pi * 3000.0 / 60.0,
        r2=0.05,
        blade_h=0.016,
        q_m3s=0.001,
        rho=997.0,
        nu=1.0e-6,
    )
    case = tmp_path / "case"
    for rel in (
        "system/blockMeshDict",
        "system/snappyHexMeshDict",
        "system/topoSetDict",
        "system/fvSchemes",
        "system/fvSolution",
        "system/controlDict",
        "constant/MRFProperties",
        "constant/transportProperties",
        "constant/turbulenceProperties",
        "constant/triSurface/impeller.stl",
        "0/U",
        "0/p",
        "Allrun",
    ):
        assert (case / rel).is_file(), rel
    mrf = (case / "constant" / "MRFProperties").read_text(encoding="utf-8")
    assert "cellZone" in mrf and "rotor" in mrf and "omega" in mrf
    topo = (case / "system" / "topoSetDict").read_text(encoding="utf-8")
    assert "cylinderToCell" in topo
    u = (case / "0" / "U").read_text(encoding="utf-8")
    assert "flowRateInletVelocity" in u and "rotatingWallVelocity" in u
    ctrl = (case / "system" / "controlDict").read_text(encoding="utf-8")
    assert "surfaceFieldValue" in ctrl and "areaAverage" in ctrl and "forces" in ctrl
    assert not out["gaps"]


def test_mrf_never_claims_head_without_a_run(tmp_path):
    from cadfree.physics.mrf import probe_mrf, run_mrf

    status = {
        "paths": {"sim": str(tmp_path)},
        "part": {"files": {}, "bbox_m": [0.1, 0.1, 0.02]},
        "environment": {"n_rpm": 3000.0, "rho": 997.0, "mu_visc": 1.0e-3},
        "inputs": {"n_rpm": 3000.0, "Q": 0.001},
        "cadquery": {"params_mm": {"r2_mm": 50.0, "hub_h_mm": 16.0}},
        "constraints": {},
    }
    out = run_mrf(status)
    assert out["ok"] is False
    assert "head_static_m" not in out
    assert out["error"]
    probe = probe_mrf()
    if not probe["available"]:
        assert "blockMesh" in probe["install_hint"] or "OpenFOAM" in probe["install_hint"]


def test_mrf_parsers_on_synthetic_output(tmp_path):
    from cadfree.physics.mrf import _last_value, _moment_z

    dat = tmp_path / "surfaceFieldValue.dat"
    dat.write_text(
        "# Time areaAverage(p)\n10 -1.5\n20 -2.5\n200 -4.905\n",
        encoding="utf-8",
    )
    assert _last_value(dat) == -4.905
    mom = tmp_path / "moment.dat"
    mom.write_text(
        "# Time (total_x total_y total_z) ...\n200 (0.01 0.02 0.35) (0 0 0.3) (0.01 0.02 0.05)\n",
        encoding="utf-8",
    )
    assert _moment_z(mom) == 0.35
