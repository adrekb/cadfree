from pathlib import Path

import numpy as np

from cadfree.agent.cadcoder import cadcoder_status
from cadfree.cad.dxf import ascii_dxf_polyline, convex_hull_2d, largest_projection
from cadfree.cad.png import read_png, write_gray_png
from cadfree.cad.refs import format_cad_ref, parse_cad_refs
from cadfree.cad.runner import build123d_status, script_dialect
from cadfree.cad.snapshot import (
    box_triangles,
    luminance_to_mask,
    mask_iou,
    rasterize_silhouette,
    score_against_mask,
)


def test_box_silhouette_iou_is_one():
    tris = box_triangles(10, 20, 5)
    mask = rasterize_silhouette(tris, view="z", size=64)
    assert mask.any()
    assert mask_iou(mask, mask) == 1.0
    empty = np.zeros_like(mask)
    assert mask_iou(mask, empty) == 0.0


def test_png_roundtrip_and_iou_against_self(tmp_path):
    tris = box_triangles(8, 8, 8)
    mask = rasterize_silhouette(tris, view="y", size=48)
    path = tmp_path / "sil.png"
    write_gray_png(path, np.where(mask, 255, 0).astype(np.uint8))
    lum = read_png(path.read_bytes())
    loaded, note = luminance_to_mask(lum, size=48, polarity="light")
    assert "light-solid" in note
    assert mask_iou(mask, loaded) == 1.0


def test_mismatched_boxes_are_below_match():
    a = box_triangles(10, 20, 4)
    b = box_triangles(40, 4, 4)
    target = rasterize_silhouette(b, view="z", size=64)
    scored = score_against_mask(a, target, size=64)
    assert scored["ok"] is True
    assert 0.0 <= scored["iou"] < 0.85
    assert scored["match"] is False
    assert scored["iterate"]


def test_parse_and_format_cad_refs():
    assert format_cad_ref("face", 17) == "@cad[face:17]"
    assert format_cad_ref("feature", "fillet:12:8") == "@cad[feature:fillet:12:8]"
    text = "fix @cad[face:17] then patch @cad[feature:fillet:12:8] on @cad[part:abc]"
    refs = parse_cad_refs(text)
    assert [r["kind"] for r in refs] == ["face", "feature", "part"]
    assert refs[0]["id"] == "17"
    assert refs[1]["id"] == "fillet:12:8"


def test_dxf_contains_entities():
    hull = convex_hull_2d(np.array([[0, 0], [10, 0], [10, 4], [0, 4], [5, 2]], dtype=float))
    assert len(hull) == 4
    dxf = ascii_dxf_polyline(hull)
    assert "ENTITIES" in dxf
    assert "LWPOLYLINE" in dxf
    assert "ENDSEC" in dxf
    tris = np.array(
        [
            [[0, 0, 0], [10, 0, 0], [10, 4, 0]],
            [[0, 0, 0], [10, 4, 0], [0, 4, 0]],
            [[0, 0, 1], [10, 0, 1], [10, 4, 1]],
            [[0, 0, 1], [10, 4, 1], [0, 4, 1]],
        ],
        dtype=float,
    )
    proj, plane, area = largest_projection(tris)
    assert "dropped z" in plane
    assert area > 0
    assert proj.shape[1] == 2


def test_build123d_missing_is_honest():
    status = build123d_status()
    assert status["id"] == "build123d"
    assert "pip install build123d" in status["install_hint"]
    assert script_dialect("import cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)") == "cadquery"
    assert script_dialect("from build123d import *\nresult = Box(1,1,1)") == "build123d"
    mixed = "import cadquery as cq\nimport build123d\nresult = cq.Workplane('XY').box(1,1,1)"
    assert script_dialect(mixed) == "cadquery"


def test_cadcoder_probe_without_url(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    monkeypatch.delenv("CADCODER_BASE_URL", raising=False)
    from cadfree.store.db import init_db

    init_db()
    status = cadcoder_status()
    assert status["available"] is False
    assert "cadcoder_base_url" in status["install_hint"]


def _write_box_stl(path):
    from cadfree.cad.record import write_binary_stl
    from cadfree.cad.snapshot import box_triangles

    tris = box_triangles(20, 10, 2)
    payload = []
    for tri in tris:
        payload.append({"n": (0, 0, 1), "v0": tuple(tri[0]), "v1": tuple(tri[1]), "v2": tuple(tri[2]), "attr": 0})
    write_binary_stl(path, payload)


def test_urdf_has_link_and_revolute(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from fastapi.testclient import TestClient

    from cadfree.cad.assembly import list_parts, part_dir, place_instance
    from cadfree.cad.urdf import export_urdf
    from cadfree.main import create_app

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "arm"}).json()["id"]
    project = client.get(f"/api/projects/{pid}").json()
    a = project["assembly"]["instances"][0]["id"]
    part_id = list_parts(pid)[0]["id"]
    _write_box_stl(part_dir(pid, part_id) / "model.stl")
    b = place_instance(pid, part_id, name="link-b", loc={"x": 40, "y": 0, "z": 0})["id"]
    client.post(
        f"/api/projects/{pid}/joints",
        json={
            "name": "elbow",
            "kind": "revolute",
            "instance_a": a,
            "instance_b": b,
            "origin": {"x": 20, "y": 0, "z": 0},
            "axis": "z",
        },
    )
    out = export_urdf(pid, name="arm")
    assert out["ok"] is True
    xml = Path(out["path"]).read_text(encoding="utf-8")
    assert "<robot name=" in xml
    assert "<link name=" in xml
    assert 'type="revolute"' in xml
    assert "<joint name=" in xml
    assert "0.001 0.001 0.001" in xml
    listed = client.get(f"/api/projects/{pid}/cad-refs").json()
    assert listed["ok"] is True
    kinds = {r["kind"] for r in listed["refs"]}
    assert {"part", "instance", "joint", "feature"} <= kinds
    resolved = client.post(
        f"/api/projects/{pid}/cad-refs/resolve",
        json={"ref": listed["refs"][0]["ref"]},
    )
    assert resolved.status_code == 200


def test_dxf_export_and_draft_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    monkeypatch.delenv("CADCODER_BASE_URL", raising=False)
    from fastapi.testclient import TestClient

    from cadfree.cad.assembly import list_parts, part_dir
    from cadfree.cad.dxf import export_dxf
    from cadfree.main import create_app

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "sheet"}).json()["id"]
    part_id = list_parts(pid)[0]["id"]
    _write_box_stl(part_dir(pid, part_id) / "model.stl")
    out = export_dxf(pid)
    assert out["ok"] is True
    text = Path(out["path"]).read_text(encoding="ascii")
    assert "ENTITIES" in text
    dxf = client.get(f"/api/projects/{pid}/dxf")
    assert dxf.status_code == 200
    draft = client.post(f"/api/projects/{pid}/draft-from-image", json={})
    assert draft.status_code == 400
    assert "cadcoder_base_url" in draft.json()["detail"]


def test_build123d_runner_names_missing_install(tmp_path):
    from cadfree.cad.runner import build123d_available, build_cadquery

    source = "from build123d import Box\nresult = Box(10, 10, 10)\n"
    built = build_cadquery(source, tmp_path)
    if build123d_available():
        assert built["ok"] is True
        assert built.get("dialect") == "build123d"
        return
    assert built["ok"] is False
    assert "build123d" in (built.get("error") or "").lower()
    assert "pip install" in (built.get("error") or "").lower()
