import json
import threading
import time

from cadfree.agent.survey import (
    create_survey,
    list_pending,
    merge_answers_into_project,
    normalize_questions,
    submit_answers,
    wait_for_answers,
)
from cadfree.store.db import db, init_db


def test_normalize_questions_requires_prompt():
    qs = normalize_questions(
        [
            {"id": "load_n", "prompt": "Load?", "type": "number", "unit": "N"},
            {"prompt": "", "type": "text"},
            {"question": "Direction?", "type": "choice", "options": ["hanging", "cantilever"]},
        ]
    )
    assert len(qs) == 2
    assert qs[0]["id"] == "load_n"
    assert qs[1]["type"] == "choice"


def test_survey_submit_unblocks_waiter(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    init_db()
    with db() as conn:
        conn.execute(
            """INSERT INTO projects(id, name, spec_text, constraints, capability_ids,
               cadquery_source, created_at, updated_at)
               VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            ("p1", "bracket", "50 lb", "{}", "[]", ""),
        )
    created = create_survey(
        "p1",
        "Load case",
        normalize_questions(
            [
                {"id": "load_lbf", "prompt": "Load", "type": "number", "unit": "lbf"},
                {"id": "load_direction", "prompt": "Direction", "type": "choice", "options": ["hanging"]},
            ]
        ),
    )
    pending = list_pending("p1")
    assert pending and pending[0]["id"] == created["id"]

    got: dict = {}

    def waiter():
        got.update(wait_for_answers(created["id"], timeout=5))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    assert submit_answers(created["id"], {"load_lbf": 50, "load_direction": "hanging"})
    thread.join(2)
    assert got.get("ok") is True
    assert got["answers"]["load_lbf"] == 50
    assert list_pending("p1") == []

    with db() as conn:
        row = conn.execute("SELECT constraints FROM projects WHERE id = ?", ("p1",)).fetchone()
    constraints = json.loads(row["constraints"])
    assert constraints["load_lbf"] == 50
    assert constraints["survey"]["load_direction"] == "hanging"


def test_merge_answers_types(tmp_path, monkeypatch):
    monkeypatch.setenv("CADFREE_HOME", str(tmp_path))
    init_db()
    with db() as conn:
        conn.execute(
            """INSERT INTO projects(id, name, spec_text, constraints, capability_ids,
               cadquery_source, created_at, updated_at)
               VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            ("p2", "x", "", "{}", "[]", ""),
        )
    out = merge_answers_into_project(
        "p2",
        {"load_n": "200", "quantity": "3", "standard": "ISO 4762", "notes": "ignore"},
    )
    assert out["load_n"] == 200.0
    assert out["quantity"] == 3
    assert out["standard"] == "ISO 4762"
    assert "notes" not in out
    assert out["survey"]["notes"] == "ignore"
