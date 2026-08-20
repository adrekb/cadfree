from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from cadfree.store.db import get_setting


class LLMError(RuntimeError):
    pass


def llm_config() -> dict[str, Any]:
    return {
        "provider": get_setting("llm_provider", "openai"),
        "api_key": get_setting("llm_api_key", "") or "",
        "model": get_setting("llm_model", "gpt-4.1"),
        "base_url": get_setting("llm_base_url", "") or "",
    }


def _openai_compatible_url(cfg: dict[str, Any]) -> str:
    provider = (cfg.get("provider") or "openai").lower()
    if cfg.get("base_url"):
        return cfg["base_url"].rstrip("/") + "/chat/completions"
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1/chat/completions"
    if provider == "ollama":
        return "http://127.0.0.1:11434/v1/chat/completions"
    if provider == "deepseek":
        return "https://api.deepseek.com/chat/completions"
    return "https://api.openai.com/v1/chat/completions"


def complete(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = llm_config()
    provider = (cfg.get("provider") or "openai").lower()
    if not cfg.get("api_key") and provider not in {"ollama"}:
        raise LLMError(
            "No API key saved. Open Settings and paste a key for OpenAI, Anthropic, "
            "OpenRouter, or DeepSeek — the same flow as Carrot."
        )
    if provider == "anthropic":
        return _anthropic(cfg, messages, tools)
    return _openai(cfg, messages, tools)


def _openai(cfg: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    body: dict[str, Any] = {"model": cfg["model"], "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(_openai_compatible_url(cfg), headers=headers, json=body)
        if resp.status_code >= 400:
            raise LLMError(f"{cfg['provider']} HTTP {resp.status_code}: {resp.text[:800]}")
        data = resp.json()
    choice = data["choices"][0]["message"]
    return {
        "content": choice.get("content") or "",
        "tool_calls": choice.get("tool_calls") or [],
        "raw": choice,
    }


def _anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for spec in tools:
        fn = spec["function"]
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system = ""
    converted: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system += (msg.get("content") or "") + "\n"
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id") or "",
                            "content": msg.get("content") or "",
                        }
                    ],
                }
            )
            continue
        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg["tool_calls"]:
                args = call["function"].get("arguments") or "{}"
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": args,
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
            continue
        converted.append({"role": role, "content": msg.get("content") or ""})
    return system.strip(), converted


def _anthropic(cfg: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    system, converted = _to_anthropic_messages(messages)
    body: dict[str, Any] = {
        "model": cfg.get("model") or "claude-sonnet-4-5",
        "max_tokens": 4096,
        "messages": converted,
        "system": system or "You are Cadfree.",
    }
    if tools:
        body["tools"] = _anthropic_tools(tools)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        if resp.status_code >= 400:
            raise LLMError(f"Anthropic HTTP {resp.status_code}: {resp.text[:800]}")
        data = resp.json()
    text = ""
    tool_calls = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text += block.get("text") or ""
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
    return {"content": text, "tool_calls": tool_calls, "raw": data}


def iter_sse(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, default=str)}\n\n"
    yield "data: {\"type\": \"done\"}\n\n"
