from cadfree.cad.assembly import (
    MAX_EXPANDED,
    bom_from_instances,
    expand_instances,
    expand_pattern,
    loc_matrix,
)


def test_linear_and_grid_counts():
    loc = {"x": 0, "y": 0, "z": 0, "rx": 0, "ry": 0, "rz": 0}
    linear = expand_pattern(loc, {"kind": "linear", "count": 10, "dx": 25})
    assert len(linear) == 10
    assert linear[-1]["x"] == 225
    grid = expand_pattern(loc, {"kind": "grid", "nx": 8, "ny": 6, "dx": 20, "dy": 15})
    assert len(grid) == 48
    circ = expand_pattern(loc, {"kind": "circular", "count": 8, "radius": 40, "axis": "z"})
    assert len(circ) == 8


def test_bom_multiplies_pattern():
    parts = [{"id": "bracket", "name": "L-bracket", "kind": "part", "material_id": "petg"}]
    instances = [
        {
            "id": "i1",
            "part_id": "bracket",
            "loc": {},
            "pattern": {"kind": "grid", "nx": 4, "ny": 3, "dx": 30, "dy": 30},
        }
    ]
    bom = bom_from_instances(parts, instances)
    assert bom[0]["qty"] == 12
    assert bom[0]["name"] == "L-bracket"


def test_expand_cap():
    instances = [
        {
            "id": "i1",
            "part_id": "p",
            "loc": {},
            "pattern": {"kind": "grid", "nx": 20, "ny": 20, "dx": 10, "dy": 10},
        }
    ]
    try:
        expand_instances(instances)
        raise AssertionError("should cap")
    except ValueError as exc:
        assert str(MAX_EXPANDED) in str(exc)


def test_loc_matrix_translate():
    m = loc_matrix({"x": 10, "y": 0, "z": 0})
    assert abs(m[0, 3] - 10) < 1e-9
