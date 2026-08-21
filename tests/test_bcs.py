"""Picked-face / hole FEA BCs — no gmsh required."""

from cadfree.cad.params import STARTER_BRACKET, extract_params
from cadfree.cad.record import write_binary_stl
from cadfree.physics.bcs import (
    hole_axes_from_params,
    nodes_near_axes,
    normalize_fea_bcs,
    parse_direction,
    resolve_bcs,
    selector_nodes,
    verts_for_pick,
)
from cadfree.physics.fea import _append_ccx, _parse_nodes
from cadfree.physics.snapshot import _material_si


def _tri(x, y, z, attr=0):
    return {
        "n": (0.0, 0.0, 1.0),
        "v0": (x, y, z),
        "v1": (x + 1.0, y, z),
        "v2": (x, y + 1.0, z),
        "attr": attr,
    }


def test_direction_names_and_vectors():
    assert parse_direction("-z")[2] == -1.0
    assert parse_direction("down") == (0.0, 0.0, -1.0)
    assert parse_direction([0, 0, -2]) == (0.0, 0.0, -1.0)
    assert parse_direction(None, "hanging")[2] == -1.0


def test_normalize_accepts_convenience_keys():
    blob = normalize_fea_bcs(
        {
            "fix_selector": "holes",
            "load_selector": "max_x",
            "load_direction": "-z",
        }
    )
    assert blob["fix"][0]["selector"] == "holes"
    assert blob["load"][0]["selector"] == "max_x"
    assert blob["load"][0]["direction"] == "-z"


def test_bbox_selector_picks_min_x_band():
    nodes = [
        (1, 0.0, 0.0, 0.0),
        (2, 0.001, 0.0, 0.0),
        (3, 0.08, 0.0, 0.0),
        (4, 0.079, 0.0, 0.0),
    ]
    fix = selector_nodes(nodes, "min_x")
    load = selector_nodes(nodes, "max_x")
    assert 1 in fix and 3 not in fix
    assert 3 in load and 1 not in load


def test_hole_axes_match_starter_bracket_params():
    params = extract_params(STARTER_BRACKET)
    axes = hole_axes_from_params(params)
    assert len(axes) == 2
    assert abs(axes[0]["x"] + params["width_mm"] / 4.0) < 1e-9
    cy = -params["foot_mm"] / 2.0 + params["hole_offset_mm"]
    assert abs(axes[0]["y"] - cy) < 1e-9
    nodes = [
        (10, axes[0]["x"] * 0.001, axes[0]["y"] * 0.001, 0.0),
        (11, axes[1]["x"] * 0.001, axes[1]["y"] * 0.001, 0.003),
        (99, 0.2, 0.2, 0.0),
    ]
    hit = nodes_near_axes(nodes, axes)
    assert 10 in hit and 11 in hit
    assert 99 not in hit


def test_pick_id_nodes_not_bbox(tmp_path):
    stl = tmp_path / "part_mm.stl"
    write_binary_stl(
        stl,
        [
            _tri(0.0, 0.0, 0.0, attr=3),
            _tri(1.0, 0.0, 0.0, attr=3),
            _tri(80.0, 0.0, 6.0, attr=9),
        ],
    )
    verts = verts_for_pick(
        [
            _tri(0.0, 0.0, 0.0, attr=3),
            _tri(80.0, 0.0, 6.0, attr=9),
        ],
        3,
    )
    assert verts and verts[0][0] < 2.0
    nodes = [
        (1, 0.0, 0.0, 0.0),
        (2, 0.001, 0.0004, 0.0),
        (3, 0.080, 0.0, 0.006),
        (4, 0.040, 0.020, 0.003),
    ]
    status = {
        "cadquery": {"params_mm": {}},
        "load": {"F_N": 220.0, "direction": "down"},
        "pick": {"stl_mm": str(stl)},
        "fea_bcs": {"fix": [{"pick_index": 3}], "load": [{"pick_index": 9, "direction": "-z"}]},
    }
    out = resolve_bcs(status, nodes)
    assert out["source"] == "pick"
    assert out["fallback"] is False
    assert 1 in out["fix_nodes"]
    assert 3 in out["load_nodes"]
    assert 4 not in out["fix_nodes"]
    assert out["direction"][2] < 0


def test_holes_selector_falls_to_params_not_bbox():
    params = extract_params(STARTER_BRACKET)
    axes = hole_axes_from_params(params)
    nodes = [
        (1, axes[0]["x"] * 0.001, axes[0]["y"] * 0.001, 0.0),
        (2, axes[1]["x"] * 0.001, axes[1]["y"] * 0.001, 0.0),
        (3, 0.080, 0.0, 0.0),
        (4, 0.0, 0.0, 0.0),
    ]
    status = {
        "cadquery": {"params_mm": params},
        "load": {"F_N": 220.0, "direction": "-z"},
        "fea_bcs": {"fix": [{"selector": "holes"}], "load": [{"selector": "max_x", "direction": "-z"}]},
    }
    out = resolve_bcs(status, nodes)
    assert out["fix_source"] == "holes"
    assert 1 in out["fix_nodes"] and 2 in out["fix_nodes"]
    assert 3 not in out["fix_nodes"]


def test_unnamed_bcs_are_explicit_bbox_fallback():
    nodes = [
        (1, 0.0, 0.0, 0.0),
        (2, 0.08, 0.0, 0.0),
        (3, 0.04, 0.0, 0.006),
    ]
    status = {"load": {"F_N": 50.0, "direction": "cantilever / shelf"}}
    out = resolve_bcs(status, nodes)
    assert out["source"] == "bbox"
    assert out["fallback"] is True
    assert "named fallback" in " ".join(out["notes"]).lower() or "bbox" in out["disclaimer"].lower()
    assert 1 in out["fix_nodes"]
    assert 2 in out["load_nodes"]


def test_append_ccx_uses_minus_z(tmp_path):
    inp = tmp_path / "part.inp"
    inp.write_text("*NODE\n1, 0, 0, 0\n2, 0.08, 0, 0\n*ELEMENT, TYPE=C3D4\n", encoding="utf-8")
    nodes = _parse_nodes(inp)
    status = {
        "material": {"E": 1.47e9, "nu": 0.38},
        "load": {"F_N": 220.0, "direction": "-z"},
        "fea_bcs": {"fix": [{"selector": "min_x"}], "load": [{"selector": "max_x", "direction": "-z"}]},
    }
    resolved = _append_ccx(inp, status, nodes)
    text = inp.read_text(encoding="utf-8")
    assert ", 3," in text
    assert resolved["direction"][2] < 0
    assert "*CLOAD" in text


def test_fdm_knockdown_is_z_over_xy():
    petg = _material_si("petg", "fdm")
    assert petg["fdm_knockdown"] == 28.0 / 40.0
    assert abs(petg["E"] - 2.1e9 * (28.0 / 40.0)) < 1.0
    mill = _material_si("petg", "cnc_mill")
    assert mill["fdm_knockdown"] == 1.0
    assert abs(mill["E"] - 2.1e9) < 1.0
