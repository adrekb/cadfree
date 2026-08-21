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
    "speed_mph",
    "range_km",
    "budget_usd",
    "payload_g",
    "flight_min",
    "vehicle_kind",
    "printed_frame",
    "cots_electronics",
    "confirm_kit",
    "k_n_per_mm",
    "stroke_mm",
    "spring_force_n",
    "spring_kind",
    "pin_d_mm",
    "input_torque_nm",
    "cycles",
    "wire_d_mm",
    "mean_d_mm",
    "n_active",
    "mass_kg",
    "hole_d_mm",
    "fit",
    "stackup_limit_mm",
    "shrink_mm",
    "operating_temp_c",
    "ambient_temp_c",
    "Qdot_W",
    "heat_w",
    "n_rpm",
    "rpm",
    "Q_lpm",
    "Q_m3s",
    "Q_gpm",
    "Q",
    "target_H_m",
    "p_inlet_pa",
    "npshr_m",
    "beta2_deg",
    "n_blades",
    "fluid",
}

FLOAT_KEYS = {
    "load_lbf",
    "load_lb",
    "load_n",
    "load_kg",
    "max_mass_g",
    "safety_factor",
    "infill",
    "wall_mm",
    "speed_mph",
    "range_km",
    "budget_usd",
    "payload_g",
    "flight_min",
    "k_n_per_mm",
    "stroke_mm",
    "spring_force_n",
    "pin_d_mm",
    "input_torque_nm",
    "cycles",
    "wire_d_mm",
    "mean_d_mm",
    "n_active",
    "mass_kg",
    "hole_d_mm",
    "stackup_limit_mm",
    "shrink_mm",
    "operating_temp_c",
    "ambient_temp_c",
    "Qdot_W",
    "heat_w",
    "n_rpm",
    "rpm",
    "Q_lpm",
    "Q_m3s",
    "Q_gpm",
    "Q",
    "target_H_m",
    "p_inlet_pa",
    "npshr_m",
    "beta2_deg",
    "n_blades",
}

BOOL_KEYS = {"printed_frame", "cots_electronics", "confirm_kit"}

SURVEY_TEMPLATES: dict[str, dict[str, Any]] = {
    "load": {
        "title": "Before we design the part",
        "questions": [
            {
                "id": "load_n",
                "prompt": "What load must this hold?",
                "type": "number",
                "unit": "N",
                "help": "Pounds are fine too — use load_lbf if that is how you think.",
            },
            {
                "id": "load_direction",
                "prompt": "How is that load applied?",
                "type": "choice",
                "options": ["hanging", "cantilever / shelf", "compression", "unknown"],
            },
            {
                "id": "fastener",
                "prompt": "What fasteners / holes?",
                "type": "text",
                "required": False,
            },
            {
                "id": "environment",
                "prompt": "Where does it live?",
                "type": "choice",
                "options": ["indoor dry", "outdoor", "hot / near motors", "unknown"],
            },
            {
                "id": "operating_temp_c",
                "prompt": "Continuous operating temperature?",
                "type": "number",
                "unit": "°C",
                "required": False,
                "help": "Skip if indoor room temp. Required if it sits near a motor, ESC, or hot bed.",
            },
            {
                "id": "max_mass_g",
                "prompt": "Mass budget?",
                "type": "number",
                "unit": "g",
                "required": False,
            },
        ],
    },
    "drone": {
        "title": "Before we design a drone",
        "questions": [
            {
                "id": "vehicle_kind",
                "prompt": "What kind of drone?",
                "type": "choice",
                "options": ["quadcopter", "whoop", "long-range", "not sure"],
            },
            {
                "id": "speed_mph",
                "prompt": "How fast does it need to go?",
                "type": "number",
                "unit": "mph",
                "help": "Cruise / what you actually want to fly, not a marketing top speed.",
            },
            {
                "id": "budget_usd",
                "prompt": "All-in parts budget?",
                "type": "number",
                "unit": "USD",
                "help": "Motors, FC, ESC, battery, props, frame. Goggles/radio are extra.",
            },
            {
                "id": "range_km",
                "prompt": "How far from you?",
                "type": "number",
                "unit": "km",
                "required": False,
            },
            {
                "id": "payload_g",
                "prompt": "Payload besides the airframe and kit?",
                "type": "number",
                "unit": "g",
                "required": False,
                "help": "Camera already on a whoop counts as zero extra. Action cam / lidar is payload.",
            },
            {
                "id": "flight_min",
                "prompt": "Hover / cruise time you need?",
                "type": "number",
                "unit": "min",
                "required": False,
            },
            {
                "id": "printed_frame",
                "prompt": "Print the airframe on your workshop machines?",
                "type": "bool",
                "help": "Electronics stay off-the-shelf either way.",
            },
            {
                "id": "cots_electronics",
                "prompt": "Buy motors, FC, ESC, and battery instead of designing them?",
                "type": "bool",
                "help": "Yes. Cadfree does not wind a BLDC or lay out a flight controller.",
            },
        ],
    },
    "impeller": {
        "title": "Before we design the impeller",
        "questions": [
            {
                "id": "n_rpm",
                "prompt": "Shaft speed?",
                "type": "number",
                "unit": "rpm",
                "help": "Duty speed. Affinity laws can scale later; do not invent a design rpm.",
            },
            {
                "id": "Q_lpm",
                "prompt": "Volume flow?",
                "type": "number",
                "unit": "L/min",
                "help": "Q_m3s or Q_gpm also work. Cadfree will not invent flow.",
            },
            {
                "id": "target_H_m",
                "prompt": "Target head?",
                "type": "number",
                "unit": "m",
                "required": False,
                "help": "Euler head is U2 Cu2 / g, not a measured pump curve.",
            },
            {
                "id": "fluid",
                "prompt": "Working fluid?",
                "type": "choice",
                "options": ["water", "air", "oil_iso32", "unknown"],
            },
            {
                "id": "p_inlet_pa",
                "prompt": "Inlet static pressure (for NPSHa)?",
                "type": "number",
                "unit": "Pa",
                "required": False,
            },
            {
                "id": "npshr_m",
                "prompt": "Required NPSH from a catalog (optional)?",
                "type": "number",
                "unit": "m",
                "required": False,
                "help": "Leave blank if you do not have NPSHr. Cadfree will not invent it.",
            },
        ],
    },
}


def questions_for_template(name: str) -> dict[str, Any]:
    key = (name or "").strip().lower()
    if key not in SURVEY_TEMPLATES:
        known = ", ".join(sorted(SURVEY_TEMPLATES))
        raise ValueError(f"unknown survey template {name!r}. Known: {known}")
    spec = SURVEY_TEMPLATES[key]
    return {
        "template": key,
        "title": spec["title"],
        "questions": normalize_questions(spec["questions"]),
    }


def _as_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"yes", "true", "1", "y"}:
        return True
    if text in {"no", "false", "0", "n"}:
        return False
    return value


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
                if key in FLOAT_KEYS:
                    try:
                        constraints[key] = float(value)
                    except (TypeError, ValueError):
                        constraints[key] = value
                elif key == "quantity":
                    try:
                        constraints[key] = int(value)
                    except (TypeError, ValueError):
                        constraints[key] = value
                elif key in BOOL_KEYS:
                    constraints[key] = _as_bool(value)
                else:
                    constraints[key] = value
        conn.execute(
            "UPDATE projects SET constraints = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(constraints), project_id),
        )
    return constraints
