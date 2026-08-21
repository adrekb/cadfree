"""SIMP topology / generative-design analogue — not Fusion 360."""

import json
from pathlib import Path

import numpy as np
import trimesh
from fastapi.testclient import TestClient

from cadfree.physics.simp import _project_mill, optimize_2d, optimize_3d, probe_simp


def test_probe_names_scipy_when_missing(monkeypatch):
    from cadfree.physics import simp

    monkeypatch.setattr(simp, "_SCIPY", False)
    monkeypatch.setattr(simp, "_SCIPY_ERR", "no scipy")
    p = probe_simp()
    assert p["available"] is False
    assert "scipy" in (p["install_hint"] or "").lower()
    assert p.get("compliance") is None


def test_2d_mbb_compliance_falls_volume_holds():
    out = optimize_2d(16, 8, volfrac=0.5, nloop=25, load="mbb")
    assert out["ok"] is True
    hist = out["history"]
    assert len(hist) >= 2
    assert hist[-1] < hist[0]
    assert abs(out["volfrac_actual"] - 0.5) < 0.08
    assert out["compliance"] == hist[-1]
    assert "invent" not in (out.get("error") or "")


def test_3d_tiny_compliance_falls():
    out = optimize_3d(6, 4, 3, volfrac=0.4, nloop=8)
    assert out["ok"] is True
    hist = out["history"]
    assert hist[-1] < hist[0]
    assert abs(out["volfrac_actual"] - 0.4) < 0.12


def test_mill_filter_is_z_invariant():
    rng = np.random.default_rng(0)
    x = rng.random((6, 4, 5))
    y = _project_mill(x)
    assert y.shape == x.shape
    assert float(y.std(axis=2).max()) < 1e-12


def test_generate_without_mesh_is_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.main import create_app

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "empty", "spec_text": "x"}).json()["id"]
    out = client.post(f"/api/projects/{pid}/generate", json={"volfrac": 0.4}).json()
    assert out["ok"] is False
    assert "build_model" in (out.get("reason") or out.get("error") or "").lower() or "mesh" in (out.get("reason") or "").lower()
    assert "compliance" not in out or out.get("compliance") is None
    assert Path(out["handoff"]).is_file()


def test_generate_registers_imported_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    import cadfree.physics.topology as topo

    monkeypatch.setattr(topo, "GRID_2D", 12)
    monkeypatch.setattr(topo, "NLOOP_2D", 12)
    monkeypatch.setattr(topo, "NLOOP_3D", 6)
    from cadfree.cad.assembly import get_part, list_parts, part_dir
    from cadfree.main import create_app

    client = TestClient(create_app())
    pid = client.post(
        "/api/projects",
        json={
            "name": "gen",
            "spec_text": "bracket",
            "constraints": {"load_n": 200, "material_id": "petg"},
        },
    ).json()["id"]
    part = get_part(pid, None)
    mesh = trimesh.creation.box(extents=[80, 40, 8])
    mesh.export(part_dir(pid, part["id"]) / "model.stl")

    body = client.post(
        f"/api/projects/{pid}/generate",
        json={"volfrac": 0.4, "design_space": "part", "assumed_load": True},
    ).json()
    assert body.get("engine") == "simp"
    assert "Fusion" not in (body.get("disclaimer") or "") or "Not Autodesk" in (body.get("disclaimer") or "")
    assert Path(body["handoff"]).is_file()
    job = json.loads(Path(body["handoff"]).read_text(encoding="utf-8"))
    assert job["engine"] == "simp"
    assert body["ok"] is True, body.get("reason") or body.get("results")
    cands = [c for c in body["candidates"] if c.get("ok") and c.get("part_id")]
    assert cands, body.get("results")
    for c in cands:
        assert c["compliance"] is not None
        assert c.get("history")
        assert c["history"][-1] <= c["history"][0]
        stl = Path(c["stl"])
        assert stl.is_file()
    names = {p["name"] for p in list_parts(pid)}
    assert any(n.startswith("gen ·") for n in names)
    kinds = {p["kind"] for p in list_parts(pid)}
    assert "imported" in kinds
    # source CadQuery part still exists
    assert any(p["kind"] == "part" for p in list_parts(pid))


def test_probe_solvers_includes_topology():
    from cadfree.physics.dispatch import probe_solvers

    probe = probe_solvers()
    assert "topology" in probe
    assert probe["topology"]["available"] is True or "scipy" in (probe["topology"].get("reason") or "").lower()


def test_agent_tools_include_generate(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.agent import plugins as plug
    from cadfree.agent.loop import PLAN_BLOCKED, _bind_tools
    from cadfree.agent.plugins import all_tools
    from cadfree.store.db import init_db

    init_db()
    plug._PLUGINS.clear()
    _bind_tools("p-missing")
    names = set(all_tools())
    assert "generate_designs" in names
    assert "generate_designs" in PLAN_BLOCKED
    assert "set_load_path" in names
    assert "optimize_params" in names
    assert "optimize_params" in PLAN_BLOCKED
