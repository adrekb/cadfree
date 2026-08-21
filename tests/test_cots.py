import json

from cadfree.agent.tools import make_handlers
from cadfree.agent.survey import questions_for_template
from cadfree.cad.params import extract_params
from cadfree.catalog import catalog_payload
from cadfree.cots.catalog import search_catalog
from cadfree.cots.kit import commit_cots_kit, pick_kit, search_parts
from cadfree.cots.score import catalog_spec_overlay, score_vehicle_spec, spec_from_constraints
from cadfree.physics.engine import solve_formula
from cadfree.store.db import db, init_db


def _project(pid: str, constraints: dict) -> None:
    with db() as conn:
        conn.execute(
            """INSERT INTO projects(id, name, spec_text, constraints, capability_ids,
               cadquery_source, created_at, updated_at)
               VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (pid, pid, "spec", json.dumps(constraints), "[]", ""),
        )


def test_fifty_mph_at_eighty_dollars_is_impossible():
    out = score_vehicle_spec(speed_mph=50, budget_usd=80)
    assert out["possible"] is False
    assert "50" in out["for_model"]
    assert "$80" in out["for_model"] or "80" in out["for_model"]
    ids = {a["class_id"] for a in out["alternatives"]}
    assert "whoop_65" in ids or "five_inch" in ids
    assert any("25" in a["change"] or "whoop" in a["change"].lower() for a in out["alternatives"])


def test_twenty_five_mph_at_one_hundred_is_possible():
    out = score_vehicle_spec(speed_mph=25, budget_usd=100)
    assert out["possible"] is True
    assert out["class_id"] == "whoop_65"


def test_drone_survey_template():
    spec = questions_for_template("drone")
    ids = {q["id"] for q in spec["questions"]}
    assert {"speed_mph", "budget_usd", "range_km", "vehicle_kind"} <= ids
    assert len(spec["questions"]) <= 16


def test_catalog_search_no_network():
    hits = search_catalog("2207 motor", role="motor", class_id="five_inch")
    assert hits
    assert all(h["role"] == "motor" for h in hits)
    assert all(h["in_stock_claim"] is False for h in hits)


def test_search_parts_catalog_even_if_web_fails(monkeypatch):
    monkeypatch.setattr(
        "cadfree.search.standards.search_standards",
        lambda *a, **k: {"ok": True, "citations": [], "note": "offline"},
    )
    out = search_parts("whoop motor", role="motor", class_id="whoop_65", include_web=True)
    assert out["catalog"]
    assert "not live" in (out["note"] + out["price_note"]).lower() or "street" in out["price_note"].lower()


def test_commit_kit_registers_purchased_parts(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    init_db()
    with db() as conn:
        conn.execute(
            """INSERT INTO projects(id, name, spec_text, constraints, capability_ids,
               cadquery_source, created_at, updated_at)
               VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            ("p-drone", "whoop", "drone", json.dumps({"speed_mph": 20, "budget_usd": 90}), "[]", ""),
        )
    out = commit_cots_kit("p-drone", class_id="whoop_65")
    assert out["ok"] is True
    kit = out["kit"]
    roles = {line["role"] for line in kit["lines"]}
    assert "motor" in roles
    assert "fc" in roles
    assert extract_params  # frame PARAMS exist
    assert out["params"]["motor_pcd_mm"]
    with db() as conn:
        parts = list(conn.execute("SELECT name, kind FROM parts WHERE project_id = ?", ("p-drone",)))
    kinds = {row["kind"] for row in parts}
    assert "purchased" in kinds
    assert any("frame" in (row["name"] or "").lower() or row["kind"] == "part" for row in parts)


def test_commit_refuses_impossible_without_accept(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    init_db()
    with db() as conn:
        conn.execute(
            """INSERT INTO projects(id, name, spec_text, constraints, capability_ids,
               cadquery_source, created_at, updated_at)
               VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            ("p-fast", "racer", "drone", json.dumps({"speed_mph": 50, "budget_usd": 80}), "[]", ""),
        )
    out = commit_cots_kit("p-fast")
    assert out["ok"] is False
    assert "50" in (out.get("error") or "")


def test_hover_formula_si():
    disk = solve_formula("disk_area", {"n": 4, "d": 0.127})
    assert disk["ok"] is True
    hover = solve_formula(
        "momentum_hover",
        {"T": 5.4, "rho": 1.225, "A": disk["value"]},
    )
    assert hover["ok"] is True
    assert 20 < hover["value"] < 80


def test_pick_kit_five_inch_has_four_motors():
    kit = pick_kit("five_inch", budget_usd=250)
    motors = next(l for l in kit["lines"] if l["role"] == "motor")
    assert motors["qty"] == 4
    assert kit["frame_params"]["fc_pcd_mm"] == 30.5


def test_catalog_payload_includes_cots_classes():
    payload = catalog_payload()
    ids = {c["id"] for c in payload["cots_classes"]}
    assert {"whoop_65", "three_inch", "five_inch", "seven_inch"} <= ids
    assert any(p["role"] == "motor" for p in payload["cots_parts"])
    assert "live" in payload["cots_price_note"].lower() or "street" in payload["cots_price_note"].lower()


def test_bracket_constraints_are_not_a_vehicle_spec():
    spec = spec_from_constraints({"load_lbf": 50, "max_mass_g": 100, "budget_usd": 40})
    assert catalog_spec_overlay({"load_lbf": 50, "max_mass_g": 100, "budget_usd": 40}) is None
    assert spec["speed_mph"] is None


def test_check_feasibility_refuses_impossible_spec_without_mesh(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    init_db()
    _project("p-fast", {"speed_mph": 50, "budget_usd": 80})
    out = make_handlers("p-fast")["check_feasibility"]()
    assert out["ok"] is True
    assert out["possible"] is False
    assert out["geometry"] is False
    assert "50" in out["summary"]
    assert out["catalog_class"]["possible"] is False


def test_check_feasibility_whoop_spec_closes_without_mesh(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    init_db()
    _project("p-whoop", {"speed_mph": 25, "budget_usd": 100})
    out = make_handlers("p-whoop")["check_feasibility"]()
    assert out["possible"] is True
    assert out["geometry"] is False
    assert out["catalog_class"]["class_id"] == "whoop_65"


def test_check_feasibility_bracket_without_mesh_still_needs_build(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    init_db()
    _project("p-bracket", {"load_lbf": 50, "max_mass_g": 100})
    out = make_handlers("p-bracket")["check_feasibility"]()
    assert out["ok"] is False
    assert "Build the model" in (out.get("error") or "")
