from cadfree.cad.features import extract_features, patch_feature
from cadfree.cad.params import STARTER_BRACKET, extract_params


def test_starter_lists_box_hole_fillet():
    out = extract_features(STARTER_BRACKET)
    assert out["parse_error"] is None
    kinds = [f["kind"] for f in out["features"]]
    assert kinds.count("box") == 2
    assert "union" in kinds
    assert "hole" in kinds
    assert "fillet" in kinds
    fillet = next(f for f in out["features"] if f["kind"] == "fillet")
    assert fillet["editable"] is True
    assert fillet["primary"]["key"] == "fillet_mm"
    assert fillet["primary"]["value"] == 1.2
    hole = next(f for f in out["features"] if f["kind"] == "hole")
    assert hole["primary"]["key"] == "hole_d_mm"
    assert any("faces" in s for s in hole["selectors"])


def test_syntax_error_is_honest():
    out = extract_features("result = cq.Workplane('XY').box(1, 1")
    assert out["features"] == []
    assert out["parse_error"]
    assert "history" in (out["note"] or "").lower() or "script" in (out["honest"] or "").lower()


def test_patch_literal_fillet_only():
    src = """\
import cadquery as cq
PARAMS = {"w": 10.0}
result = cq.Workplane("XY").box(10, 10, 10).edges("|Z").fillet(1.2)
result = result.edges("|X").fillet(0.4)
"""
    feats = extract_features(src)["features"]
    fillets = [f for f in feats if f["kind"] == "fillet"]
    assert len(fillets) == 2
    out = patch_feature(src, fillets[0]["id"], 2.5)
    assert out["ok"] is True
    assert ".fillet(2.5)" in out["source"]
    assert ".fillet(0.4)" in out["source"]
    assert "1.2" not in out["source"]


def test_patch_isolates_shared_params_key():
    src = """\
import cadquery as cq
PARAMS = {
    "fillet_mm": 1.2,
}
p = PARAMS
result = cq.Workplane("XY").box(20, 20, 8).edges("|Z").fillet(p["fillet_mm"])
result = result.edges("|X").fillet(p["fillet_mm"])
"""
    feats = extract_features(src)["features"]
    fillets = [f for f in feats if f["kind"] == "fillet"]
    assert len(fillets) == 2
    out = patch_feature(src, fillets[1]["id"], 3.0)
    assert out["ok"] is True
    assert out["isolated"] is True
    params = extract_params(out["source"])
    assert params["fillet_mm"] == 1.2
    assert 3.0 in params.values()
    assert out["source"].count('p["fillet_mm"]') + out["source"].count("p['fillet_mm']") == 1
    kinds = [f["kind"] for f in out["features"]]
    assert kinds.count("fillet") == 2


def test_patch_unique_params_updates_dict():
    src = STARTER_BRACKET
    fillet = next(f for f in extract_features(src)["features"] if f["kind"] == "fillet")
    out = patch_feature(src, fillet["id"], 2.0)
    assert out["ok"] is True
    assert extract_params(out["source"])["fillet_mm"] == 2.0
    assert "fillet_mm" in out["source"]


def test_stl_pick_attribute_roundtrip(tmp_path):
    from cadfree.cad.record import read_stl_pick_ids, write_binary_stl

    path = tmp_path / "pick.stl"
    write_binary_stl(
        path,
        [{"n": (0, 0, 1), "v0": (0, 0, 0), "v1": (1, 0, 0), "v2": (0, 1, 0), "attr": 7}],
    )
    assert read_stl_pick_ids(path.read_bytes()) == [7]


def test_attach_live_marks_that_fillet():
    from cadfree.cad.record import attach_live

    feats = extract_features(STARTER_BRACKET)["features"]
    attached = attach_live(feats, {"ops": [{"kind": "fillet", "pick_index": 9, "new_faces": 8}]})
    fillet = next(f for f in attached if f["kind"] == "fillet")
    assert fillet["pick_index"] == 9
    assert fillet["live"] is True


def test_recorded_starter_stamps_fillet_pick_ids(tmp_path):
    import pytest

    pytest.importorskip("cadquery")
    from cadfree.cad.record import execute_recorded, read_stl_pick_ids

    stl = tmp_path / "model.stl"
    live_path = tmp_path / "features.live.json"
    out = execute_recorded(STARTER_BRACKET, stl, live_path)
    assert out["ok"] is True, out.get("error")
    fillet_ops = [op for op in out["live"]["ops"] if op["kind"] == "fillet"]
    assert fillet_ops
    assert fillet_ops[0]["new_faces"] >= 1
    pick = fillet_ops[0]["pick_index"]
    ids = read_stl_pick_ids(stl.read_bytes())
    assert pick in ids
    feat = next(f for f in out["features"] if f["kind"] == "fillet")
    assert feat["live"] is True
    assert feat["pick_index"] == pick


def test_runner_writes_live_json(tmp_path):
    import pytest

    pytest.importorskip("cadquery")
    from cadfree.cad.runner import build_cadquery

    built = build_cadquery(STARTER_BRACKET, tmp_path)
    assert built["ok"] is True, built.get("error")
    live = built.get("live") or {}
    assert live.get("live") is True
    assert (tmp_path / "features.live.json").is_file()
    assert (tmp_path / "model.stl").is_file()
