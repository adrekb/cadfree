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
            "pattern": {"kind": "grid", "nx": 50, "ny": 50, "dx": 10, "dy": 10},
        }
    ]
    try:
        expand_instances(instances)
        raise AssertionError("should cap")
    except ValueError as exc:
        assert str(MAX_EXPANDED) in str(exc)


def test_large_grid_is_ok_under_cap():
    instances = [
        {
            "id": "i1",
            "part_id": "p",
            "loc": {},
            "pattern": {"kind": "grid", "nx": 20, "ny": 16, "dx": 25, "dy": 25},
        }
    ]
    expanded = expand_instances(instances)
    assert len(expanded) == 320
    assert len(expanded[0]["matrix_colmajor"]) == 16


def test_nested_parent_pattern_multiplies():
    instances = [
        {
            "id": "frame",
            "part_id": "bay",
            "loc": {"x": 0, "y": 0, "z": 0},
            "pattern": {"kind": "linear", "count": 10, "dx": 100},
        },
        {
            "id": "bracket",
            "part_id": "L",
            "parent_id": "frame",
            "loc": {"x": 12, "y": 0, "z": 0},
            "pattern": {"kind": "linear", "count": 4, "dy": 30},
        },
    ]
    expanded = expand_instances(instances)
    frames = [e for e in expanded if e["id"] == "frame"]
    kids = [e for e in expanded if e["id"] == "bracket"]
    assert len(frames) == 10
    assert len(kids) == 40
    xs = sorted({round(k["loc"]["x"], 3) for k in kids})
    assert xs == [12.0, 112.0, 212.0, 312.0, 412.0, 512.0, 612.0, 712.0, 812.0, 912.0]


def test_mirror_pattern():
    loc = {"x": 20, "y": 0, "z": 0, "rx": 0, "ry": 0, "rz": 0}
    pair = expand_pattern(loc, {"kind": "mirror", "axis": "x", "at": 0})
    assert len(pair) == 2
    assert pair[1]["x"] == -20
    assert pair[1]["sx"] == -1
    m = loc_matrix(pair[1])
    assert m[0, 0] < 0


def test_loc_matrix_translate():
    m = loc_matrix({"x": 10, "y": 0, "z": 0})
    assert abs(m[0, 3] - 10) < 1e-9
