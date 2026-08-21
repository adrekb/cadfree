import json

import trimesh
from fastapi.testclient import TestClient

from cadfree.physics.book import lookup_formula
from cadfree.physics.dispatch import probe_solvers, run_solvers
from cadfree.physics.engine import solve_formula
from cadfree.physics.fea import probe_fea


def test_coulomb_latex_steps():
    out = solve_formula("coulomb_friction", {"mu": 0.12, "F_N": 200})
    assert out["ok"] is True
    assert abs(out["value"] - 24.0) < 1e-9
    assert out["steps"][0]["latex"]
    assert "F_f" in out["steps"][-1]["latex"]
    assert "Coulomb" in out["disclaimer"] or "Amontons" in out["disclaimer"]


def test_lookup_friction():
    hit = lookup_formula("bushing PV grease")
    ids = [f["id"] for f in hit["formulas"]]
    assert "pv_bushing" in ids or "coulomb_friction" in ids


def test_missing_mu_is_not_invented():
    out = solve_formula("coulomb_friction", {"F_N": 10})
    assert out["ok"] is False
    assert "mu" in out["needed"]


def test_pair_from_book():
    out = solve_formula("coulomb_friction", {"F_N": 100, "pair": "greased_steel"})
    assert out["ok"] is True
    assert abs(out["inputs"]["mu"] - 0.12) < 1e-9


def test_drag_from_numbers():
    out = solve_formula(
        "drag_force",
        {"rho": 1.225, "v": 10.0, "Cd": 1.0, "A": 0.002},
    )
    assert out["ok"] is True
    assert out["value"] == 0.5 * 1.225 * 100 * 1.0 * 0.002


def test_si_snapshot_and_solvers_iterate(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.cad.assembly import get_part, part_dir
    from cadfree.main import create_app
    from cadfree.store.db import db

    client = TestClient(create_app())
    pid = client.post(
        "/api/projects",
        json={"name": "sim", "spec_text": "bracket 50 lb", "constraints": {"load_lbf": 50, "material_id": "petg"}},
    ).json()["id"]
    with db() as conn:
        conn.execute(
            "UPDATE projects SET constraints = ? WHERE id = ?",
            (
                json.dumps(
                    {
                        "load_lbf": 50,
                        "material_id": "petg",
                        "safety_factor": 2,
                        "v_ms": 12,
                        "pair": "greased_steel",
                    }
                ),
                pid,
            ),
        )
    part = get_part(pid, None)
    mesh = trimesh.creation.box(extents=[80, 40, 6])
    mesh.export(part_dir(pid, part["id"]) / "model.stl")

    status = client.get(f"/api/projects/{pid}/si-status").json()
    assert status["units"] == "SI"
    assert abs(status["part"]["bbox_m"][0] - 0.08) < 1e-6 or max(status["part"]["bbox_m"]) > 0.07
    assert status["load"]["F_N"] > 200
    assert status["part"]["files"].get("stl_m")
    assert abs(status["inputs"]["L"] - max(status["part"]["bbox_m"])) < 1e-9

    body = client.post(
        f"/api/projects/{pid}/solvers",
        json={"solvers": ["analytical", "fea", "fluids"]},
    ).json()
    assert body["ok"] is True
    kinds = {r["kind"] for r in body["results"]}
    assert {"analytical", "fea", "fluids"} <= kinds
    fea = next(r for r in body["results"] if r["kind"] == "fea")
    assert fea["ok"] is False
    assert "von_mises_max" not in fea or not fea.get("ok")
    err = (fea.get("error") or "").lower()
    assert "gmsh" in err or "install" in err or "ccx" in err or "calculix" in err or "stl" in err
    analytical = next(r for r in body["results"] if r["kind"] == "analytical")
    assert any(w.get("ok") for w in analytical["worksheets"])
    stress = next(w for w in analytical["worksheets"] if w["formula_id"] == "cantilever_stress")
    assert stress["ok"] is True
    assert stress["provenance"].get("L")
    book = client.get("/api/physics/book").json()
    assert book["ok"] is True
    assert len(book["formulas"]) > 8


def test_probe_does_not_fake_fea():
    probe = probe_solvers()
    assert probe["analytical"]["available"] is True
    fea = probe_fea()
    if not fea["available"]:
        assert fea["install_hint"]


def test_run_solvers_without_mesh_still_uses_params(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.main import create_app
    from cadfree.store.db import db

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "thin", "spec_text": "x"}).json()["id"]
    with db() as conn:
        conn.execute(
            "UPDATE projects SET constraints = ? WHERE id = ?",
            (json.dumps({"load_n": 100, "material_id": "petg"}), pid),
        )
    out = run_solvers(pid, solvers=["analytical"])
    assert out["ok"] is True
    # starter PARAMS still bind width/thickness even before a mesh exists
    status = client.get(f"/api/projects/{pid}/si-status").json()
    assert status["cadquery"]["params_mm"]["thickness_mm"] == 6.0
    assert abs(status["inputs"]["width"] - 0.04) < 1e-9


def test_quadratic_geo_and_probe_label(tmp_path):
    from cadfree.physics.fea import _write_geo, probe_fea

    stl = tmp_path / "part.stl"
    stl.write_text("solid x\nendsolid x\n", encoding="utf-8")
    geo = tmp_path / "part.geo"
    _write_geo(stl, geo, 0.004, order=2)
    text = geo.read_text(encoding="utf-8")
    assert "ElementOrder = 2" in text
    probe = probe_fea()
    assert "C3D10" in probe["label"] or "C3D10" in (probe.get("elements") or "")


def test_thermal_solver_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    import json

    import trimesh
    from fastapi.testclient import TestClient

    from cadfree.cad.assembly import get_part, part_dir
    from cadfree.main import create_app
    from cadfree.store.db import db

    client = TestClient(create_app())
    pid = client.post("/api/projects", json={"name": "hot", "spec_text": "bracket"}).json()["id"]
    with db() as conn:
        conn.execute(
            "UPDATE projects SET constraints = ? WHERE id = ?",
            (json.dumps({"load_n": 10, "material_id": "petg", "Qdot_W": 3, "operating_temp_c": 40}), pid),
        )
    part = get_part(pid, None)
    trimesh.creation.box(extents=[80, 40, 6]).export(part_dir(pid, part["id"]) / "model.stl")
    body = client.post(f"/api/projects/{pid}/solvers", json={"pack": "heat"}).json()
    kinds = {r["kind"] for r in body["results"]}
    assert "thermal" in kinds
    assert "analytical" in kinds

