"""ISO 286 fits + print shrink + stackup — catalog overlay, not a CMM."""

from cadfree.catalog import catalog_payload
from cadfree.manufacturing.fits import (
    evaluate_fit,
    fit_spec_overlay,
    it_um,
    shaft_limits_mm,
    shrink_mm,
    stackup_from,
)
from cadfree.physics.engine import solve_formula


def test_it_grades_10mm():
    assert it_um(6, 10.0) == 9
    assert it_um(7, 10.0) == 15
    assert it_um(11, 5.2) == 75


def test_h7_g6_close_running_is_clearance():
    # 10 mm, 6–10 mm band. Not the full ISO clause book — tabulated first-order.
    hmin, hmax = 10.0, 10.0 + 0.015
    smin, smax = shaft_limits_mm("g6", 10.0)
    assert abs(smax - 9.995) < 1e-9
    assert abs(smin - 9.986) < 1e-9
    report = evaluate_fit(10.0, 10.0, fit="H7/g6", process_kind="cnc_mill")
    assert report["ok"] is True
    assert report["result_kind"] == "clearance"
    assert abs(report["clearance_min_mm"] - (hmin - smax)) < 1e-9
    assert report["clearance_min_mm"] > 0
    assert report["shrink_mm"] == 0.0


def test_h7_p6_is_interference_on_a_mill():
    report = evaluate_fit(10.0, 10.0, fit="H7/p6", process_kind="cnc_mill")
    assert report["result_kind"] == "interference"
    assert report["clearance_min_mm"] < 0


def test_fdm_hole_too_small_for_purchased_pin():
    report = evaluate_fit(5.2, 5.0, fit="H11/h11", process_kind="fdm")
    assert report["shrink_mm"] > 0.15
    overlay = fit_spec_overlay(
        {"hole_d_mm": 5.2, "pin_d_mm": 5.0, "fit": "H11/h11", "process_kind": "fdm"}
    )
    assert overlay is not None
    assert overlay["possible"] is False
    assert overlay["verdict"] == "needs_spec_change"
    assert any(c["id"] == "fit" and c["status"] == "fail" for c in overlay["checks"])


def test_enlarged_printed_hole_assembles():
    overlay = fit_spec_overlay(
        {"hole_d_mm": 5.6, "pin_d_mm": 5.0, "fit": "H11/h11", "process_kind": "fdm"}
    )
    assert overlay["possible"] is True
    assert overlay["score"]["fit"]["clearance_min_mm"] > 0


def test_stackup_fails_limit():
    overlay = fit_spec_overlay(
        {
            "stackup": [
                {"name": "a", "nominal_mm": 10, "plus_mm": 0.2, "minus_mm": 0.1},
                {"name": "b", "nominal_mm": 8, "plus_mm": 0.2, "minus_mm": 0.1},
            ],
            "stackup_limit_mm": 0.4,
        }
    )
    assert overlay["possible"] is False
    stack = overlay["score"]["stackup"]
    assert abs(stack["wc_band_mm"] - 0.6) < 1e-9
    assert stack["rss_band_mm"] > 0


def test_param_tol_rss_and_book():
    stack = stackup_from(None, {"width_mm": 40.0, "width_tol_mm": 0.2, "foot_mm": 50.0, "foot_tol_mm": 0.3})
    assert stack is not None
    assert abs(stack["wc_band_mm"] - 1.0) < 1e-9
    wc = solve_formula("stackup_wc", {"t1": 0.0002, "t2": 0.0003})
    assert wc["ok"] is True
    assert abs(wc["value"] - 0.0005) < 1e-12
    clr = solve_formula("fit_clearance", {"D_hole": 0.0052, "d_pin": 0.005, "shrink": 0.0002})
    assert clr["ok"] is True
    assert abs(clr["value"] - 0.0) < 1e-12


def test_catalog_payload_has_fits():
    payload = catalog_payload()
    ids = {f["id"] for f in payload["fits"]}
    assert {"H11/h11", "H8/h7", "H7/g6", "H7/k6", "H7/p6"} <= ids
    assert "ISO 286" in payload["fits_note"]


def test_shrink_laser_is_oversize():
    s, _ = shrink_mm("laser_cut", 6.0)
    assert s < 0
