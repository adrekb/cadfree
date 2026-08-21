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
    assert project.json()["pending_surveys"] == []
    assert project.json()["assembly"]["parts"]


def test_survey_api_and_search_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.agent.survey import create_survey, normalize_questions
    from cadfree.main import create_app

    client = TestClient(create_app())
    created = client.post(
        "/api/projects",
        json={"name": "surveyed", "spec_text": "bracket", "constraints": {}},
    )
    pid = created.json()["id"]
    survey = create_survey(
        pid,
        "Before CAD",
        normalize_questions([{"id": "load_lbf", "prompt": "Load?", "type": "number"}]),
    )
    listed = client.get(f"/api/projects/{pid}/surveys")
    assert listed.status_code == 200
    assert listed.json()["surveys"][0]["id"] == survey["id"]

    posted = client.post(
        f"/api/projects/{pid}/survey/{survey['id']}",
        json={"answers": {"load_lbf": 50}},
    )
    assert posted.json()["ok"] is True
    project = client.get(f"/api/projects/{pid}")
    assert project.json()["constraints"]["load_lbf"] == 50
    assert project.json()["pending_surveys"] == []

    saved = client.post(
        "/api/settings",
        json={"search_provider": "duckduckgo", "search_api_key": "secret-brave"},
    )
    assert saved.json()["search_provider"] == "duckduckgo"
    assert saved.json()["search_api_key_set"] is True
    assert saved.json()["search_api_key"] == ""

    think = client.post(
        "/api/settings",
        json={"llm_provider": "deepseek", "llm_model": "deepseek-v4-pro", "llm_thinking": "max"},
    )
    assert think.json()["llm_thinking"] == "max"
    assert think.json()["llm_model"] == "deepseek-v4-pro"


def test_agent_tools_include_survey_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    from cadfree.agent import plugins as plug
    from cadfree.agent.loop import _bind_tools
    from cadfree.agent.plugins import all_tools
    from cadfree.store.db import init_db

    init_db()
    plug._PLUGINS.clear()
    _bind_tools("p-missing")
    names = set(all_tools())
    assert {"ask_survey", "search_standards", "read_url", "list_assembly", "place_instance"} <= names
