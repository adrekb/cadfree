"""Stateless MCP 2026-07-28 Streamable HTTP for Cadfree skills.

No protocol sessions, no initialize handshake. Each POST is one JSON-RPC
request. Application state is a `project_id` handle the model passes as a
tool argument — that is the 2026 pattern, not Mcp-Session-Id.

This is Cadfree speaking the protocol, not a fork of an SDK. Tools are the
same plugin handlers the studio agent loop uses.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from cadfree import __version__
from cadfree.agent.loop import _bind_tools
from cadfree.agent.plugins import all_tools
from cadfree.agent.survey import submit_answers

PROTOCOL = "2026-07-28"
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022
METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600
INTERNAL = -32603

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}

INSTRUCTIONS = (
    "Cadfree: one loop — ask_survey for what you don't know, check_feasibility "
    "(workshop DFM and catalog classes; whoop vs 5-inch is catalog data, not a "
    "special agent), refuse an impossible spec, then CadQuery around envelopes. "
    "Every tools/call needs project_id (the studio project handle). Catalog prices "
    "are street-typical, not live stock. Never invent FEA, clause numbers, or inventory."
)


def _rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _header(request: Request, name: str) -> str:
    return (request.headers.get(name) or "").strip()


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    host = (urlparse(origin).hostname or "").lower().removeprefix("[")
    host = host.rstrip("]")
    return host in _LOCAL_HOSTS or host.endswith(".localhost")


def _meta_version(params: dict[str, Any]) -> str:
    meta = params.get("_meta") or {}
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("io.modelcontextprotocol/protocolVersion") or "").strip()


def _ensure_tools(project_id: str) -> None:
    from cadfree.agent import plugins as plug

    plug._PLUGINS.clear()
    _bind_tools(project_id)


def _mcp_input_schema(parameters: dict[str, Any]) -> dict[str, Any]:
    schema = json.loads(json.dumps(parameters or {"type": "object", "properties": {}}))
    props = dict(schema.get("properties") or {})
    props["project_id"] = {
        "type": "string",
        "description": "Studio project handle. Pass it on every call; MCP has no session.",
    }
    schema["properties"] = props
    required = list(schema.get("required") or [])
    if "project_id" not in required:
        required.append("project_id")
    schema["required"] = required
    schema["type"] = "object"
    return schema


def _tool_list() -> list[dict[str, Any]]:
    _ensure_tools("_mcp_list")
    tools = []
    for name, tool in sorted(all_tools().items(), key=lambda kv: kv[0]):
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": _mcp_input_schema(tool.parameters),
            }
        )
    return tools


def discover_result() -> dict[str, Any]:
    return {
        "resultType": "complete",
        "supportedVersions": [PROTOCOL],
        "capabilities": {"tools": {"listChanged": False}},
        "instructions": INSTRUCTIONS,
        "ttlMs": 3_600_000,
        "cacheScope": "public",
        "_meta": {
            "io.modelcontextprotocol/serverInfo": {
                "name": "cadfree",
                "version": __version__,
            }
        },
    }


def _questions_to_schema(questions: list[dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for q in questions:
        qid = str(q.get("id") or "")
        if not qid:
            continue
        spec: dict[str, Any] = {"description": q.get("prompt") or qid}
        qtype = q.get("type") or "text"
        if qtype == "number":
            spec["type"] = "number"
        elif qtype == "bool":
            spec["type"] = "boolean"
        else:
            spec["type"] = "string"
        opts = q.get("options") or []
        if opts and qtype != "multi":
            spec["enum"] = list(opts)
        props[qid] = spec
        if q.get("required", True):
            required.append(qid)
    return {"type": "object", "properties": props, "required": required}


def _call_tool(name: str, arguments: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    project_id = str(arguments.get("project_id") or extra.get("project_id") or "").strip()
    if not project_id:
        return {
            "resultType": "complete",
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": "project_id is required on every tools/call. MCP 2026-07-28 has no session.",
                }
            ],
        }
    args = {k: v for k, v in arguments.items() if k != "project_id"}
    _ensure_tools(project_id)
    tool = all_tools().get(name)
    if not tool:
        return {
            "resultType": "complete",
            "isError": True,
            "content": [{"type": "text", "text": f"unknown tool {name}"}],
        }

    responses = extra.get("inputResponses") or {}
    state = extra.get("requestState")
    if name == "ask_survey" and state and isinstance(responses, dict) and responses:
        first = next(iter(responses.values()), {})
        content = first.get("content") if isinstance(first, dict) else {}
        if not isinstance(content, dict):
            content = {}
        ok = submit_answers(str(state), content)
        payload = {"ok": ok, "survey_id": state, "answers": content}
        return {
            "resultType": "complete",
            "isError": not ok,
            "structuredContent": payload,
            "content": [{"type": "text", "text": json.dumps(payload, default=str)[:16000]}],
        }

    try:
        payload = tool.handler(**args)
    except TypeError as exc:
        return {
            "resultType": "complete",
            "isError": True,
            "content": [{"type": "text", "text": str(exc)}],
        }
    except Exception as exc:
        return {
            "resultType": "complete",
            "isError": True,
            "content": [{"type": "text", "text": str(exc)}],
        }

    if name == "ask_survey" and isinstance(payload, dict) and payload.get("survey_id"):
        questions = payload.get("questions") or []
        return {
            "resultType": "input_required",
            "inputRequests": {
                "survey": {
                    "method": "elicitation/create",
                    "params": {
                        "mode": "form",
                        "message": payload.get("title") or "A few questions before designing",
                        "requestedSchema": _questions_to_schema(questions),
                    },
                }
            },
            "requestState": payload["survey_id"],
            "structuredContent": payload,
        }

    text = json.dumps(payload, default=str)[:16000]
    return {
        "resultType": "complete",
        "isError": bool(isinstance(payload, dict) and payload.get("ok") is False),
        "structuredContent": payload,
        "content": [{"type": "text", "text": text}],
    }


def handle_rpc(body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any] | None]:
    req_id = body.get("id")
    method = str(body.get("method") or "")
    params = body.get("params") if isinstance(body.get("params"), dict) else {}

    proto = headers.get("mcp-protocol-version") or headers.get("MCP-Protocol-Version") or ""
    mcp_method = headers.get("mcp-method") or headers.get("Mcp-Method") or ""
    mcp_name = headers.get("mcp-name") or headers.get("Mcp-Name") or ""

    if not proto:
        return 400, _rpc_error(
            req_id,
            HEADER_MISMATCH,
            "missing MCP-Protocol-Version header",
        )
    meta_ver = _meta_version(params)
    if meta_ver and meta_ver != proto:
        return 400, _rpc_error(
            req_id,
            HEADER_MISMATCH,
            f"Header mismatch: MCP-Protocol-Version header value {proto!r} does not match body value {meta_ver!r}",
        )
    if proto != PROTOCOL:
        return 400, _rpc_error(
            req_id,
            UNSUPPORTED_PROTOCOL_VERSION,
            "unsupported protocol version",
            {"supported": [PROTOCOL], "requested": proto},
        )
    if not mcp_method:
        return 400, _rpc_error(req_id, HEADER_MISMATCH, "missing Mcp-Method header")
    if mcp_method != method:
        return 400, _rpc_error(
            req_id,
            HEADER_MISMATCH,
            f"Header mismatch: Mcp-Method header value {mcp_method!r} does not match body value {method!r}",
        )
    if method in {"tools/call", "resources/read", "prompts/get"}:
        body_name = str((params or {}).get("name") or (params or {}).get("uri") or "")
        if not mcp_name:
            return 400, _rpc_error(req_id, HEADER_MISMATCH, "missing Mcp-Name header")
        if mcp_name != body_name:
            return 400, _rpc_error(
                req_id,
                HEADER_MISMATCH,
                f"Header mismatch: Mcp-Name header value {mcp_name!r} does not match body value {body_name!r}",
            )

    if req_id is None and method.startswith("notifications/"):
        return 202, None

    if method == "server/discover":
        return 200, _ok(req_id, discover_result())
    if method == "tools/list":
        return 200, _ok(
            req_id,
            {
                "resultType": "complete",
                "tools": _tool_list(),
                "ttlMs": 300_000,
                "cacheScope": "public",
            },
        )
    if method == "tools/call":
        name = str((params or {}).get("name") or "")
        arguments = (params or {}).get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        extra = {
            "inputResponses": (params or {}).get("inputResponses"),
            "requestState": (params or {}).get("requestState"),
        }
        result = _call_tool(name, arguments, extra)
        return 200, _ok(req_id, result)
    if method == "ping":
        return 200, _ok(req_id, {"resultType": "complete"})

    return 404, _rpc_error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")


async def mcp_endpoint(request: Request) -> Response:
    origin = _header(request, "origin")
    if origin and not _origin_allowed(origin):
        return JSONResponse(
            _rpc_error(None, HEADER_MISMATCH, "invalid Origin"),
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_error(None, INVALID_REQUEST, "invalid JSON"), status_code=400)
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return JSONResponse(_rpc_error(None, INVALID_REQUEST, "expected JSON-RPC 2.0 object"), status_code=400)

    headers = {k: v for k, v in request.headers.items()}
    status, payload = handle_rpc(body, headers)
    if payload is None:
        return Response(status_code=status)
    return JSONResponse(payload, status_code=status)
