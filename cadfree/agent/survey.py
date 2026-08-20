"""Blocking survey tool — the agent asks; the human answers.

Guessing load direction, fastener class, or which ASTM/ISO applies is how
text-to-CAD ships impossible parts. ask_survey pauses the turn until the
user submits the form in the studio.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from cadfree.store.db import db

ALLOWED_TYPES = {"choice", "multi", "number", "text", "bool"}

_PENDING: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list):
        raise ValueError("questions must be a list")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or f"q{i + 1}").strip()
        prompt = str(item.get("prompt") or item.get("question") or "").strip()
        if not prompt:
            continue
        qtype = str(item.get("type") or "text").lower()
        if qtype not in ALLOWED_TYPES:
            qtype = "text"
        options = item.get("options") or []
        if not isinstance(options, list):
            options = [str(options)]
        out.append(
            {
                "id": qid,
                "prompt": prompt,
                "type": qtype,
                "options": [str(o) for o in options],
                "required": bool(item.get("required", True)),
                "unit": item.get("unit") or "",
                "help": item.get("help") or "",
            }
        )
    if not out:
        raise ValueError("ask_survey needs at least one question with a prompt")
    return out[:16]


def create_survey(project_id: str, title: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
    sid = uuid.uuid4().hex[:16]
    event = threading.Event()
    record = {
        "id": sid,
        "project_id": project_id,
        "title": title or "A few questions before designing",
        "questions": questions,
        "answers": None,
        "event": event,
        "created": time.time(),
    }
    with _LOCK:
        _PENDING[sid] = record
    with db() as conn:
        conn.execute(
            """INSERT INTO surveys(id, project_id, questions, answers, status, created_at)
               VALUES(?,?,?,?,?,datetime('now'))""",
            (sid, project_id, json.dumps({"title": record["title"], "questions": questions}), None, "pending"),
        )
    return {"id": sid, "title": record["title"], "questions": questions}


def wait_for_answers(survey_id: str, timeout: float = 600.0) -> dict[str, Any]:
    with _LOCK:
        rec = _PENDING.get(survey_id)
    if rec is None:
        return {"ok": False, "error": "unknown survey", "skipped": True, "timed_out": False}
    ok = rec["event"].wait(timeout=timeout)
    if not ok:
        return {
            "ok": False,
            "skipped": True,
            "timed_out": True,
            "error": "the user did not answer in time — do not invent the missing fields; ask again or stop.",
        }
    with _LOCK:
        answers = rec.get("answers") or {}
    return {"ok": True, "skipped": False, "timed_out": False, "answers": answers, "survey_id": survey_id}


def submit_answers(survey_id: str, answers: dict[str, Any]) -> bool:
    if not isinstance(answers, dict):
        return False
    project_id = None
    with _LOCK:
        rec = _PENDING.get(survey_id)
        if rec is not None:
            rec["answers"] = answers
            rec["event"].set()
            project_id = rec["project_id"]
    with db() as conn:
        row = conn.execute("SELECT project_id, status FROM surveys WHERE id = ?", (survey_id,)).fetchone()
        if not row:
            return False
        project_id = project_id or row["project_id"]
        conn.execute(
            "UPDATE surveys SET answers = ?, status = 'answered' WHERE id = ?",
            (json.dumps(answers), survey_id),
        )
    merge_answers_into_project(project_id, answers)
    return True


def list_pending(project_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """SELECT id, questions, status, created_at FROM surveys
               WHERE project_id = ? AND status = 'pending' ORDER BY created_at""",
            (project_id,),
        ).fetchall()
    out = []
    for row in rows:
        payload = json.loads(row["questions"] or "{}")
        out.append(
            {
                "id": row["id"],
                "survey_id": row["id"],
                "title": payload.get("title") or "A few questions before designing",
                "questions": payload.get("questions") or [],
                "status": row["status"],
                "created_at": row["created_at"],
            }
        )
    return out


CONSTRAINT_KEYS = {
    "load_lbf",
    "load_lb",
    "load_n",
    "load_kg",
    "max_mass_g",
    "safety_factor",
    "material_id",
    "infill",
    "wall_mm",
    "environment",
    "load_direction",
    "standard",
    "quantity",
    "fastener",
    "mounting",
}


def merge_answers_into_project(project_id: str, answers: dict[str, Any]) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT constraints FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            return {}
        constraints = json.loads(row["constraints"] or "{}")
        survey = dict(constraints.get("survey") or {})
        survey.update(answers)
        constraints["survey"] = survey
        for key, value in answers.items():
            if key in CONSTRAINT_KEYS and value not in (None, ""):
                if key in {"load_lbf", "load_lb", "load_n", "load_kg", "max_mass_g", "safety_factor", "infill", "wall_mm"}:
                    try:
                        constraints[key] = float(value)
                    except (TypeError, ValueError):
                        constraints[key] = value
                elif key == "quantity":
                    try:
                        constraints[key] = int(value)
                    except (TypeError, ValueError):
                        constraints[key] = value
                else:
                    constraints[key] = value
        conn.execute(
            "UPDATE projects SET constraints = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(constraints), project_id),
        )
    return constraints
