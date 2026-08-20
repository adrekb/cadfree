from cadfree.cad.params import STARTER_BRACKET, apply_params, extract_params


def test_extract_starter_params():
    params = extract_params(STARTER_BRACKET)
    assert params["width_mm"] == 40.0
    assert params["hole_d_mm"] == 5.2


def test_apply_params_updates_values():
    updated = apply_params(STARTER_BRACKET, {"width_mm": 55.0})
    params = extract_params(updated)
    assert params["width_mm"] == 55.0
    assert params["thickness_mm"] == 6.0
