"""Roark / NAFEMS-cousin identities. No gmsh, no ccx, no API key."""

from cadfree.catalog import MATERIALS
from cadfree.kinematics.dynamics import probe_exudyn, rk4_1dof
from cadfree.physics.book import BY_ID, H_STILL_AIR, SIGMA_SB
from cadfree.physics.convergence import char_lengths, richardson
from cadfree.physics.engine import solve_formula
from cadfree.physics.validation import (
    NAFEMS_UNIAXIAL,
    check_first_order_on_box,
    check_formula_book_cantilever,
    roark_cantilever_truth,
    run_suite,
    sphere_cd_truth,
    uniaxial_bar_truth,
)


def test_roark_formula_book_matches_identity():
    out = check_formula_book_cantilever()
    assert out["ok"] is True
    assert out["stress"]["pass"] is True
    assert out["deflection"]["pass"] is True


def test_roark_stress_value_is_the_textbook_one():
    t = roark_cantilever_truth()
    want = 6.0 * t["F_N"] * t["L"] / (t["b"] * t["t"] ** 2)
    assert abs(t["sigma_Pa"] - want) < 1e-6


def test_first_order_box_tracks_roark():
    out = check_first_order_on_box((80.0, 40.0, 6.0), 50.0 * 4.448221615, MATERIALS["petg"])
    assert out["ok"] is True
    assert out["relative_error"] < 0.35


def test_uniaxial_bar_hooke():
    bar = uniaxial_bar_truth()
    assert abs(bar["sigma_Pa"] - NAFEMS_UNIAXIAL["F_N"] / NAFEMS_UNIAXIAL["A"]) < 1e-9
    assert abs(bar["delta_m"] - bar["sigma_Pa"] * bar["L"] / bar["E"]) < 1e-18


def test_suite_ok_without_fea_binaries():
    suite = run_suite()
    assert suite["ok"] is True
    ids = {c["id"] for c in suite["cases"]}
    assert "roark_cantilever_tip" in ids


def test_richardson_two_and_three_levels():
    two = richardson([100.0, 95.0], quantity="sigma")
    assert two["ok"] is True
    assert two["finest"] == 95.0
    assert two["relative_change"] == abs(95 - 100) / 95
    three = richardson([12.0, 10.0, 9.2], ratio=2.0, quantity="u")
    assert three["ok"] is True
    assert three.get("extrapolated") is not None
    one = richardson([1.0])
    assert one["ok"] is False


def test_char_lengths_coarse_to_fine():
    lens = char_lengths([0.08, 0.04, 0.006], n_levels=3, ratio=1.5)
    assert len(lens) == 3
    assert lens[0] > lens[1] > lens[2]


def test_sphere_cd_newtonian_not_our_blunt_default():
    hit = sphere_cd_truth(1e4)
    assert abs(hit["Cd"] - 0.47) < 1e-9
    assert "Cd=1" in hit["disclaimer"] or "Cd=1" in hit["disclaimer"].replace(" ", "")


def test_newton_cooling_and_radiation_book():
    cool = solve_formula("newton_cooling", {"h": 10.0, "A": 0.01, "T": 350.0, "T_inf": 300.0})
    assert cool["ok"] is True
    assert abs(cool["value"] - 10.0 * 0.01 * 50.0) < 1e-9
    rad = solve_formula(
        "radiation_net",
        {"epsilon": 1.0, "sigma": SIGMA_SB, "A": 1.0, "T": 300.0, "T_inf": 0.0},
    )
    assert rad["ok"] is True
    assert abs(rad["value"] - SIGMA_SB * 300.0**4) / rad["value"] < 1e-6
    assert "newton_cooling" in BY_ID
    assert H_STILL_AIR == 10.0


def test_rk4_undamped_oscillator_amplitude():
    # x'' + ωn² x = 0, x(0)=0.01, v(0)=0, no gravity. Amplitude should hold.
    k, m = 100.0, 1.0
    out = rk4_1dof(m, k, 0.0, x0=0.01, v0=0.0, t_end=0.5, dt=0.0005, gravity=False)
    assert out["ok"] is True
    assert abs(out["x_max_m"] - 0.01) / 0.01 < 0.05


def test_exudyn_probe_does_not_throw():
    probe = probe_exudyn()
    assert "available" in probe
    if not probe["available"]:
        assert "install_hint" in probe
