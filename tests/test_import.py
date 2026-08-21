import io
import zipfile

from cadfree.cad.import_cad import classify, import_status
from cadfree.cad.runner import cadquery_available


ASCII_STL = """solid cadfree
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 10 0 0
  vertex 0 10 0
 endloop
endfacet
facet normal 0 0 -1
 outer loop
  vertex 0 0 0
  vertex 0 10 0
  vertex 10 0 0
 endloop
endfacet
endsolid cadfree
"""


def test_classify_formats():
    step = classify("frame.STEP")
    assert step["kind"] == "kernel"
    assert step["ok"] is True
    mesh = classify("bracket.stl")
    assert mesh["kind"] == "mesh"
    native = classify("widget.sldprt")
    assert native["ok"] is False
    assert "SolidWorks" in native["error"]
    fusion = classify("enclosure.f3d")
    assert fusion["ok"] is False
    assert "Fusion" in fusion["error"]
    bad = classify("notes.pdf")
    assert bad["ok"] is False


def test_import_status_lists_formats():
    status = import_status()
    assert status["mesh"] is True
    assert ".step" in status["formats"]["kernel"]
    assert ".stl" in status["formats"]["mesh"]
    assert status["step_iges_brep"] is cadquery_available()


def test_import_stl_replaces_starter(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.main import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    created = client.post("/api/projects", json={"name": "from fusion", "spec_text": "imported plate"})
    pid = created.json()["id"]
    imported = client.post(
        f"/api/projects/{pid}/import",
        files={"file": ("plate.stl", ASCII_STL.encode(), "model/stl")},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["ok"] is True
    assert len(body["parts"]) == 1
    assert body["parts"][0]["kind"] == "imported"
    assert body["assembly"]["unique_parts"] == 1
    stl = client.get(f"/api/projects/{pid}/parts/{body['parts'][0]['id']}/stl")
    assert stl.status_code == 200
    scene = client.get(f"/api/projects/{pid}/scene")
    assert scene.status_code == 200
    assert scene.json()["draw_count"] >= 1


def test_import_zip_two_meshes(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.main import create_app
    from fastapi.testclient import TestClient

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.stl", ASCII_STL)
        zf.writestr("b.stl", ASCII_STL)
    created = TestClient(create_app())
    client = created
    pid = client.post("/api/projects", json={"name": "kit", "spec_text": ""}).json()["id"]
    imported = client.post(
        f"/api/projects/{pid}/import",
        files={"file": ("kit.zip", buf.getvalue(), "application/zip")},
    )
    assert imported.status_code == 200, imported.text
    assert len(imported.json()["parts"]) == 2


def test_import_native_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.main import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "sw", "spec_text": ""}).json()["id"]
    resp = client.post(
        f"/api/projects/{pid}/import",
        files={"file": ("part.sldprt", b"not-a-real-file", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "SolidWorks" in resp.json()["detail"]


def test_step_without_cadquery_is_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    if cadquery_available():
        return
    from cadfree.main import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "step", "spec_text": ""}).json()["id"]
    resp = client.post(
        f"/api/projects/{pid}/import",
        files={"file": ("frame.step", b"ISO-10303-21;", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "CadQuery" in resp.json()["detail"] or "STL" in resp.json()["detail"]
