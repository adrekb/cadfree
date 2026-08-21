import math

import numpy as np

from cadfree.cots.springs import spring_spec_overlay
from cadfree.kinematics.fourbar import solve_fourbar
from cadfree.kinematics.statics import (
    coil_rate_n_per_m,
    fourbar_pin_forces,
    ondof,
    slider_crank_pin_forces,
    wahl_shear_pa,
)
from cadfree.physics.engine import solve_formula


def test_hooke_and_coil_rate_book():
    hooke = solve_formula("hooke_spring", {"k": 2000.0, "x": 0.01})
    assert hooke["ok"] is True
    assert abs(hooke["value"] - 20.0) < 1e-9
    coil = solve_formula(
        "coil_rate",
        {"G": 79.3e9, "d": 0.001, "D": 0.01, "n": 8},
    )
    assert coil["ok"] is True
    k = coil_rate_n_per_m(0.001, 0.01, 8)
    assert abs(coil["value"] - k) / k < 1e-6
    assert coil["extra"]["k_n_per_mm"] == coil["value"] / 1000.0


def test_wahl_and_pin_shear_book():
    tau = solve_formula("wahl_stress", {"F": 20.0, "D": 0.012, "d": 0.0014})
    assert tau["ok"] is True
    assert tau["extra"]["tau_MPa"] > 0
    pin = solve_formula("pin_shear", {"F": 200.0, "d": 0.004})
    assert pin["ok"] is True
    assert abs(pin["value"] - (200.0 / (math.pi * 0.004**2 / 4))) < 1e-6


def test_ondof_mass_spring():
    out = ondof(1.0, 100.0, 0.0, include_gravity=False)
    assert out["ok"] is True
    assert abs(out["wn_rad_s"] - 10.0) < 1e-9
    assert abs(out["fn_hz"] - 10.0 / (2 * math.pi)) < 1e-9
    sag = ondof(2.0, 200.0)
    assert sag["static_sag_m"] > 0


def test_slider_crank_pin_two_force():
    a = np.array([0.0, 0.0])
    b = np.array([0.0, 0.03])
    c = np.array([0.0632455532, 0.0])
    out = slider_crank_pin_forces(a, b, c, np.array([1.0, 0.0]), 100.0)
    assert out["ok"] is True
    assert abs(out["pins"]["B"]["F_n"] - out["pins"]["C"]["F_n"]) < 1e-6
    assert out["max_pin_n"] > 100.0
    assert abs(out["T_hold_nm"]) > 0


def test_fourbar_holding_torque_matches_virtual_work():
    a = np.array([0.0, 0.0])
    d = np.array([0.080, 0.0])
    l1, l2, l3 = 0.030, 0.070, 0.055
    th0 = 0.4
    s0 = solve_fourbar(a, d, l1, l2, l3, th0)
    s1 = solve_fourbar(a, d, l1, l2, l3, th0 + 1e-4, np.array(s0["C"]))
    assert s0["ok"] and s1["ok"]
    b0, c0 = np.array(s0["B"]), np.array(s0["C"])
    c1 = np.array(s1["C"])
    f = (0.0, -12.0)
    dc = c1 - c0
    t_vw = -np.dot(f, dc) / 1e-4
    stat = fourbar_pin_forces(a, b0, c0, d, f_at_c=f)
    assert stat["ok"] is True
    assert abs(stat["T_hold_nm"] - t_vw) / max(abs(t_vw), 1e-9) < 0.15
    assert stat["max_pin_n"] > 0


def test_spring_catalog_refuses_impossible_stroke(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.agent.tools import make_handlers
    from cadfree.store.db import db, init_db
    import json

    init_db()
    with db() as conn:
        conn.execute(
            """INSERT INTO projects(id, name, spec_text, constraints, capability_ids,
               cadquery_source, created_at, updated_at)
               VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (
                "p-latch",
                "latch",
                "spring return",
                json.dumps({"stroke_mm": 40, "spring_force_n": 80, "spring_kind": "compression"}),
                "[]",
                "",
            ),
        )
    overlay = spring_spec_overlay({"stroke_mm": 40, "spring_force_n": 80})
    assert overlay is not None
    out = make_handlers("p-latch")["check_feasibility"]()
    assert out["ok"] is True
    assert out["geometry"] is False
    assert out["possible"] is False
    assert "catalog_spring" in out


def test_spring_catalog_small_latch_closes():
    overlay = spring_spec_overlay({"stroke_mm": 12, "k_n_per_mm": 0.4, "spring_kind": "compression"})
    assert overlay["possible"] is True
    assert overlay["score"]["spring_id"] == "comp_latch_8x32"


def test_catalog_payload_has_springs():
    from cadfree.catalog import catalog_payload

    payload = catalog_payload()
    ids = {s["id"] for s in payload["cots_springs"]}
    assert "comp_latch_8x32" in ids
    assert payload["cots_price_note"]


def test_wahl_library_matches_book():
    tau = wahl_shear_pa(20.0, 0.012, 0.0014)
    book = solve_formula("wahl_stress", {"F": 20.0, "D": 0.012, "d": 0.0014})
    assert abs(tau - book["value"]) / tau < 1e-6
