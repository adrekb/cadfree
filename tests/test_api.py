import os

from fastapi.testclient import TestClient


def test_workshop_and_project(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.main import create_app

    client = TestClient(create_app())
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    added = client.post(
        "/api/workshop",
        json={"kind": "fdm", "name": "Prusa Mini+", "preset_id": "prusa_mini"},
    )
    assert added.status_code == 200
    assert added.json()["params"]["bed_x_mm"] == 180

    created = client.post(
        "/api/projects",
        json={
            "name": "50lb bracket",
            "spec_text": "bracket that holds 50 pounds, 100g filament",
            "constraints": {"load_lbf": 50, "max_mass_g": 100},
            "capability_ids": [added.json()["id"]],
        },
    )
    assert created.status_code == 200
    project = client.get("/api/projects/" + created.json()["id"])
    assert "width_mm" in project.json()["params"]
    assert client.get("/").status_code == 200
