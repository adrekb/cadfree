from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterator

from cadfree.agent.plugins import Tool, all_tools, openai_tools, register, system_prompt_sections
from cadfree.agent.prompts import SYSTEM_CORE
from cadfree.agent.providers import LLMError, complete
from cadfree.agent.survey import wait_for_answers
from cadfree.agent.tools import make_handlers

MAX_STEPS = 18
SURVEY_WAIT_S = 600.0
PING_EVERY_S = 2.0
PLAN_BLOCKED = {"write_cadquery", "set_params", "build_model", "run_matlab"}


def _bind_tools(project_id: str) -> None:
    handlers = make_handlers(project_id)
    tools = [
        Tool(
            "get_workshop",
            "List the user's machines, materials, CadQuery/MATLAB/FEA availability.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            handlers["get_workshop"],
        ),
        Tool(
            "get_project",
            "Read the current project's spec, constraints, CadQuery source, last feasibility, and pending surveys.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            handlers["get_project"],
        ),
        Tool(
            "ask_survey",
            "Ask the user a structured form (never guess). Pauses until they submit. "
            "Call this before writing CadQuery on a new spec. Types: choice, multi, number, text, bool. "
            "Use constraint ids: load_n, load_lbf, load_direction, mounting, fastener, environment, "
            "standard, safety_factor, max_mass_g, material_id, quantity.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "prompt": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["choice", "multi", "number", "text", "bool"],
                                },
                                "options": {"type": "array", "items": {"type": "string"}},
                                "required": {"type": "boolean"},
                                "unit": {"type": "string"},
                                "help": {"type": "string"},
                            },
                            "required": ["prompt"],
                        },
                    },
                },
                "required": ["questions"],
            },
            handlers["ask_survey"],
        ),
        Tool(
            "search_standards",
            "Web search for ISO/ASTM/ASME/DIN/SAE/MIL-STD/NAS/IPC documents and manufacturer datasheets. "
            "Ranks standards bodies first. Cite URLs; do not invent paywalled clauses.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "intent": {
                        "type": "string",
                        "enum": ["standards", "datasheet", "machine"],
                    },
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            handlers["search_standards"],
        ),
        Tool(
            "read_url",
            "Fetch a public http(s) page and extract text. Refuses private IPs. "
            "PDFs and logins are reported as paywalled — do not guess the body.",
            {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            handlers["read_url"],
        ),
        Tool(
            "write_cadquery",
            "Replace the project's CadQuery script. Must assign `result` and keep a PARAMS dict.",
            {
                "type": "object",
                "properties": {"source": {"type": "string"}},
                "required": ["source"],
            },
            handlers["write_cadquery"],
            mutating=True,
        ),
        Tool(
            "set_params",
            "Patch PARAMS in the CadQuery script without rewriting topology.",
            {
                "type": "object",
                "properties": {"params": {"type": "object"}},
                "required": ["params"],
            },
            handlers["set_params"],
            mutating=True,
        ),
        Tool(
            "build_model",
            "Run CadQuery, export STL, return volume/bbox/overhang metrics.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            handlers["build_model"],
            mutating=True,
        ),
        Tool(
            "check_feasibility",
            "DFM + mass budget + first-order strength against the selected production methods.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            handlers["check_feasibility"],
        ),
        Tool(
            "run_simulation",
            "Run the best available physics rung: first-order, MATLAB/Octave beam theory, or mesh FEA if installed.",
            {
                "type": "object",
                "properties": {
                    "prefer": {
                        "type": "string",
                        "enum": ["auto", "first_order", "matlab", "octave", "fea"],
                    }
                },
            },
            handlers["run_simulation"],
        ),
        Tool(
            "run_matlab",
            "Run a MATLAB or Octave script in Agent mode. Octave is used if MATLAB is absent.",
            {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
            handlers["run_matlab"],
            mutating=True,
        ),
        Tool(
            "lookup_material",
            "Printed/as-manufactured properties for a catalog material id (pla, petg, al6061, ...).",
            {
                "type": "object",
                "properties": {"material_id": {"type": "string"}},
                "required": ["material_id"],
            },
            handlers["lookup_material"],
        ),
    ]
    from cadfree.agent.plugins import Plugin

    register(
        Plugin(
            name="cadfree",
            tools=tools,
            prompt=SYSTEM_CORE,
        )
    )


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    tool = all_tools().get(name)
    if not tool:
        return {"error": f"unknown tool {name}"}
    try:
        return tool.handler(**(arguments or {}))
    except Exception as exc:
        return {"error": str(exc)}


def _wait_survey(survey_id: str) -> Iterator[dict[str, Any]]:
    deadline = time.monotonic() + SURVEY_WAIT_S
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            yield {
                "type": "tool_result",
                "name": "ask_survey",
                "result": {
                    "ok": False,
                    "skipped": True,
                    "timed_out": True,
                    "error": "the user did not answer in time — do not invent the missing fields.",
                },
            }
            return
        result = wait_for_answers(survey_id, timeout=min(PING_EVERY_S, remaining))
        if result.get("ok"):
            yield {"type": "survey_answered", "survey_id": survey_id, "answers": result.get("answers")}
            yield {"type": "tool_result", "name": "ask_survey", "result": result}
            return
        if not result.get("timed_out"):
            yield {"type": "tool_result", "name": "ask_survey", "result": result}
            return
        yield {"type": "ping"}


def run_turn(
    project_id: str,
    history: list[dict[str, Any]],
    user_text: str,
    *,
    mode: str = "agent",
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """ReAct loop. Plugins supply tools; this file only drives steps.

    `ask_survey` yields a form to the UI, then blocks until submit (with SSE pings).
    """
    from cadfree.agent import plugins as plug

    plug._PLUGINS.clear()
    _bind_tools(project_id)

    emit = on_event or (lambda _e: None)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt_sections()},
        *history,
        {"role": "user", "content": user_text},
    ]
    tools = openai_tools()
    if mode == "plan":
        tools = [t for t in tools if t["function"]["name"] not in PLAN_BLOCKED]

    for step in range(MAX_STEPS):
        emit({"type": "status", "message": f"Agent step {step + 1}"})
        try:
            reply = complete(messages, tools)
        except LLMError as exc:
            event = {"type": "error", "message": str(exc)}
            emit(event)
            yield event
            return

        content = reply.get("content") or ""
        calls = reply.get("tool_calls") or []
        if content and not calls:
            event = {"type": "assistant", "content": content}
            emit(event)
            yield event
            return

        messages.append(
            {
                "role": "assistant",
                "content": content or "",
                "tool_calls": calls,
            }
        )
        if content:
            yield {"type": "assistant_partial", "content": content}

        if not calls:
            event = {"type": "assistant", "content": content or "(no reply)"}
            yield event
            return

        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            if mode == "plan" and name in PLAN_BLOCKED:
                payload = {"error": f"{name} is blocked in Plan mode"}
                yield {"type": "tool_call", "name": name, "arguments": args}
                yield {"type": "tool_result", "name": name, "result": payload}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "name": name,
                        "content": json.dumps(payload)[:16000],
                    }
                )
                continue
            yield {"type": "tool_call", "name": name, "arguments": args}
            payload = _call_tool(name, args)
            if name == "ask_survey" and isinstance(payload, dict) and payload.get("survey_id"):
                yield {
                    "type": "survey",
                    "survey_id": payload["survey_id"],
                    "title": payload.get("title") or "A few questions before designing",
                    "questions": payload.get("questions") or [],
                }
                wait_payload = None
                for event in _wait_survey(payload["survey_id"]):
                    if event.get("type") == "tool_result":
                        wait_payload = event.get("result")
                    yield event
                payload = wait_payload if wait_payload is not None else payload
            else:
                payload = (
                    payload
                    if isinstance(payload, (dict, list, str, int, float, bool)) or payload is None
                    else str(payload)
                )
                yield {"type": "tool_result", "name": name, "result": payload}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "name": name,
                    "content": json.dumps(payload, default=str)[:16000],
                }
            )

    yield {
        "type": "assistant",
        "content": "Stopped after the step budget. Ask me to continue, or rebuild from the editor.",
    }
