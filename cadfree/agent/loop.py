from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterator

from cadfree.agent.plugins import Tool, all_tools, openai_tools, register, system_prompt_sections
from cadfree.agent.prompts import SYSTEM_CORE
from cadfree.agent.providers import LLMError, assistant_history_message, complete, llm_config, normalize_thinking
from cadfree.agent.survey import wait_for_answers
from cadfree.agent.tools import make_handlers

MAX_STEPS = 18
STEP_BUDGET = {"off": 18, "low": 20, "high": 24, "max": 32}
SURVEY_WAIT_S = 600.0
PING_EVERY_S = 2.0
PLAN_BLOCKED = {
    "write_cadquery",
    "set_params",
    "patch_feature",
    "build_model",
    "run_matlab",
    "upsert_part",
    "place_instance",
    "remove_instance",
    "define_joint",
    "remove_joint",
    "generate_designs",
}


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
            "Read the current project's spec, constraints, CadQuery source, assembly (parts/instances/BOM), last feasibility, and pending surveys.",
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
            "list_assembly",
            "Parts, instances, expanded count, and BOM. Unique parts + patterns, not one script per copy.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            handlers["list_assembly"],
        ),
        Tool(
            "upsert_part",
            "Create or update a unique part. kind: part | purchased | fastener | subassembly | imported. "
            "Purchased/fastener/subassembly need no CadQuery. Imported parts come from Import CAD (STEP/STL). "
            "Shop-scale assemblies reuse these, they do not add more scripts.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source": {"type": "string"},
                    "part_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["part", "purchased", "fastener", "subassembly", "imported"]},
                    "material_id": {"type": "string"},
                },
                "required": ["name"],
            },
            handlers["upsert_part"],
            mutating=True,
        ),
        Tool(
            "place_instance",
            "Place a part in the assembly. loc is mm + degrees {x,y,z,rx,ry,rz}. "
            "pattern: {kind: none|linear|grid|circular|mirror, count, dx, dy, dz, nx, ny, nz, radius, axis, at}. "
            "parent_id is another INSTANCE id — a patterned parent multiplies children "
            "(e.g. 12 bays × 4 brackets). Shop-scale cap is 400 instances of up to 32 unique parts.",
            {
                "type": "object",
                "properties": {
                    "part_id": {"type": "string"},
                    "name": {"type": "string"},
                    "loc": {"type": "object"},
                    "pattern": {"type": "object"},
                    "parent_id": {"type": "string"},
                    "instance_id": {"type": "string"},
                },
                "required": ["part_id"],
            },
            handlers["place_instance"],
            mutating=True,
        ),
        Tool(
            "remove_instance",
            "Remove a placed instance (and its pattern) from the assembly.",
            {
                "type": "object",
                "properties": {"instance_id": {"type": "string"}},
                "required": ["instance_id"],
            },
            handlers["remove_instance"],
            mutating=True,
        ),
        Tool(
            "set_active_part",
            "Select which unique part the editor and write_cadquery target.",
            {
                "type": "object",
                "properties": {"part_id": {"type": "string"}},
                "required": ["part_id"],
            },
            handlers["set_active_part"],
        ),
        Tool(
            "define_joint",
            "Add a kinematic joint between instances. kind: revolute | prismatic | gear | fixed. "
            "instance_a is parent or empty for ground; instance_b is the moving child. "
            "origin {x,y,z} mm is the pin (world). axis x|y|z. driven=true on the input crank. "
            "Four revolutes ground-crank-coupler-rocker-ground is a four-bar. "
            "Gear needs params {module_mm, teeth_a, teeth_b} and ratio.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["revolute", "prismatic", "gear", "fixed"]},
                    "instance_a": {"type": "string"},
                    "instance_b": {"type": "string"},
                    "origin": {"type": "object"},
                    "axis": {"type": "string"},
                    "driven": {"type": "boolean"},
                    "ratio": {"type": "number"},
                    "limits": {"type": "object"},
                    "params": {"type": "object"},
                    "joint_id": {"type": "string"},
                },
                "required": ["name", "instance_b"],
            },
            handlers["define_joint"],
            mutating=True,
        ),
        Tool(
            "remove_joint",
            "Delete a kinematic joint.",
            {
                "type": "object",
                "properties": {"joint_id": {"type": "string"}},
                "required": ["joint_id"],
            },
            handlers["remove_joint"],
            mutating=True,
        ),
        Tool(
            "sweep_mechanism",
            "Drive the input joint through an angle (or mm for a slider) and report lock-ups "
            "plus AABB clashes. CadQuery does not do this — this is the kinematics layer. "
            "Not contact dynamics.",
            {
                "type": "object",
                "properties": {
                    "start_deg": {"type": "number"},
                    "end_deg": {"type": "number"},
                    "steps": {"type": "integer"},
                },
            },
            handlers["sweep_mechanism"],
        ),
        Tool(
            "check_mesh",
            "First-order gear pitch-diameter check and interference at rest. "
            "Needs gear joints with module_mm and teeth counts, or any joints for clash.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            handlers["check_mesh"],
        ),
        Tool(
            "lookup_formula",
            "Search the SI formula book (friction, PV, wear, pipe, aero drag, beams). "
            "Do not invent μ, C_d, or viscosity — use a book pair/fluid or ask_survey.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "domain": {
                        "type": "string",
                        "enum": ["friction", "fluids", "aero", "solids", "heat", "maintenance"],
                    },
                },
                "required": ["query"],
            },
            handlers["lookup_formula"],
        ),
        Tool(
            "solve_formula",
            "Solve one book formula in SI. Default use_part=true binds CadQuery mesh/PARAMS/spec "
            "so numbers come from the solid, not guesses. Studio renders the LaTeX steps.",
            {
                "type": "object",
                "properties": {
                    "formula_id": {"type": "string"},
                    "values": {"type": "object"},
                    "solve_for": {"type": "string"},
                    "use_part": {"type": "boolean"},
                },
                "required": ["formula_id"],
            },
            handlers["solve_formula"],
        ),
        Tool(
            "run_solvers",
            "Snapshot the built part as SI (metres, N, Pa) and send a mesh copy to packaged "
            "solvers: analytical formula book (always), Gmsh+CalculiX FEA if installed, "
            "fluids/aero handbook + CFD handoff if OpenFOAM/Elmer/SU2 exist. "
            "Then iterate PARAMS from results[].iterate. Never invent FEA/CFD numbers.",
            {
                "type": "object",
                "properties": {
                    "solvers": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["analytical", "fea", "fluids", "topology"],
                        },
                    },
                    "values": {"type": "object"},
                    "pack": {
                        "type": "string",
                        "enum": ["strength", "bushing", "aero", "pipe"],
                    },
                    "part_id": {"type": "string"},
                },
            },
            handlers["run_solvers"],
        ),
        Tool(
            "generate_designs",
            "Fusion-style generative design analogue: Sigmund/Liu–Tovar SIMP on a voxel "
            "copy of the built SI mesh. Returns 1–3 organic STL candidates as imported "
            "parts (does not rewrite CadQuery). Not Autodesk Generative Design. "
            "Requires scipy. Call build_model first. Never invent compliance.",
            {
                "type": "object",
                "properties": {
                    "part_id": {"type": "string"},
                    "volfrac": {
                        "type": "number",
                        "description": "Target solid fraction 0.08–0.9. Omit for 30% and 40% outcomes.",
                    },
                    "design_space": {"type": "string", "enum": ["part", "bbox"]},
                    "mill_25d": {
                        "type": "boolean",
                        "description": "Force 2.5D mill extrusion filter. Default from workshop machines.",
                    },
                    "additive": {
                        "type": "boolean",
                        "description": "Force AM overhang filter. Default from FDM/SLA/metal AM in workshop.",
                    },
                    "assumed_load": {
                        "type": "boolean",
                        "description": "If no F_N, use a unit load and say so. Default true.",
                    },
                },
            },
            handlers["generate_designs"],
            mutating=True,
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
            "Replace a part's CadQuery script. Must assign `result` and keep a PARAMS dict. Optional part_id (defaults to the active part). Do not copy-paste 40 bodies — use place_instance patterns.",
            {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "part_id": {"type": "string"},
                },
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
                "properties": {
                    "params": {"type": "object"},
                    "part_id": {"type": "string"},
                },
                "required": ["params"],
            },
            handlers["set_params"],
            mutating=True,
        ),
        Tool(
            "list_features",
            "CadQuery feature tree for the active part. Not a SolidWorks kernel — "
            "after build_model we wrap Workplane and stamp STL pick-ids so the user "
            "can click THAT fillet in 3D. Use feature_id with patch_feature.",
            {
                "type": "object",
                "properties": {"part_id": {"type": "string"}},
            },
            handlers["list_features"],
        ),
        Tool(
            "patch_feature",
            "Change one numeric argument on one CadQuery operation (e.g. that fillet radius) "
            "without rewriting the script. If the PARAMS key is shared, isolates this call "
            "onto a new key so the other fillet does not move. Then call build_model.",
            {
                "type": "object",
                "properties": {
                    "feature_id": {"type": "string"},
                    "value": {"type": "number"},
                    "arg_index": {"type": "integer"},
                    "part_id": {"type": "string"},
                },
                "required": ["feature_id", "value"],
            },
            handlers["patch_feature"],
            mutating=True,
        ),
        Tool(
            "build_model",
            "Run CadQuery for one unique part (optional part_id) and export its STL. "
            "The viewer instances that mesh — it does not merge a giant assembly STL.",
            {
                "type": "object",
                "properties": {"part_id": {"type": "string"}},
            },
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
    images: list[dict[str, Any]] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """ReAct loop. Plugins supply tools; this file only drives steps.

    `ask_survey` yields a form to the UI, then blocks until submit (with SSE pings).
    """
    from cadfree.agent import plugins as plug

    plug._PLUGINS.clear()
    _bind_tools(project_id)

    emit = on_event or (lambda _e: None)
    user_msg: dict[str, Any] = {"role": "user", "content": user_text}
    if images:
        user_msg["images"] = images
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt_sections()},
        *history,
        user_msg,
    ]
    tools = openai_tools()
    if mode == "plan":
        tools = [t for t in tools if t["function"]["name"] not in PLAN_BLOCKED]
    steps = STEP_BUDGET.get(normalize_thinking(llm_config().get("thinking")), MAX_STEPS)

    for step in range(steps):
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
        reasoning = reply.get("reasoning_content") or ""
        if reasoning:
            yield {"type": "thinking", "content": reasoning}
        if content and not calls:
            event = {"type": "assistant", "content": content}
            emit(event)
            yield event
            return

        messages.append(assistant_history_message(reply))
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
