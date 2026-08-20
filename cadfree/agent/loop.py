from __future__ import annotations

import json
from typing import Any, Callable, Iterator

from cadfree.agent.plugins import Tool, all_tools, openai_tools, register, system_prompt_sections
from cadfree.agent.prompts import SYSTEM_CORE
from cadfree.agent.providers import LLMError, complete
from cadfree.agent.tools import make_handlers

MAX_STEPS = 12


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
            "Read the current project's spec, constraints, CadQuery source, and last feasibility.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            handlers["get_project"],
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


def run_turn(
    project_id: str,
    history: list[dict[str, Any]],
    user_text: str,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """ReAct loop. Plugins supply tools; this file only drives steps."""
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
            yield {"type": "tool_call", "name": name, "arguments": args}
            result = _call_tool(name, args if isinstance(args, dict) else {})
            payload = result if isinstance(result, (dict, list, str, int, float, bool)) or result is None else str(result)
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
