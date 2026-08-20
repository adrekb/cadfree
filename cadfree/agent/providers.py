from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from cadfree.store.db import get_setting

THINKING_LEVELS = ("off", "low", "high", "max")
DEFAULT_THINKING = "high"
MAX_TOKENS = {"off": 8192, "low": 16384, "high": 32768, "max": 65536}
TIMEOUT_S = {"off": 120.0, "low": 180.0, "high": 300.0, "max": 600.0}


class LLMError(RuntimeError):
    pass


def normalize_thinking(value: Any) -> str:
    raw = str(value or DEFAULT_THINKING).strip().lower()
    aliases = {
        "none": "off",
        "disabled": "off",
        "disable": "off",
        "minimal": "low",
        "medium": "high",
        "xhigh": "max",
        "extra": "max",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in THINKING_LEVELS else DEFAULT_THINKING


def llm_config() -> dict[str, Any]:
    provider = (get_setting("llm_provider", "openai") or "openai").lower()
    model = get_setting("llm_model", "") or ""
    if not str(model).strip():
        model = "deepseek-v4-pro" if provider == "deepseek" else "gpt-4.1"
    return {
        "provider": provider,
        "api_key": get_setting("llm_api_key", "") or "",
        "model": model,
        "base_url": get_setting("llm_base_url", "") or "",
        "thinking": normalize_thinking(get_setting("llm_thinking", DEFAULT_THINKING)),
    }


def uses_deepseek_thinking(cfg: dict[str, Any]) -> bool:
    provider = (cfg.get("provider") or "").lower()
    model = (cfg.get("model") or "").lower()
    return provider == "deepseek" or "deepseek" in model


def apply_thinking(body: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Mutate an OpenAI-compatible chat body with thinking / reasoning_effort."""
    thinking = normalize_thinking(cfg.get("thinking"))
    provider = (cfg.get("provider") or "").lower()
    if provider == "ollama":
        return body
    if thinking == "off":
        if uses_deepseek_thinking(cfg):
            body["thinking"] = {"type": "disabled"}
        return body
    body["max_tokens"] = MAX_TOKENS[thinking]
    if uses_deepseek_thinking(cfg):
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = thinking
        return body
    if provider in {"openai", "openrouter", "custom"}:
        body["reasoning_effort"] = "xhigh" if thinking == "max" else thinking
    return body


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


def _text_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    return str(value)


def parse_choice(choice: dict[str, Any]) -> dict[str, Any]:
    message = choice.get("message") or choice
    return {
        "content": _text_content(message.get("content")),
        "tool_calls": message.get("tool_calls") or [],
        "reasoning_content": _text_content(
            message.get("reasoning_content") or message.get("reasoning")
        ),
        "raw": message,
    }


def assistant_history_message(reply: dict[str, Any]) -> dict[str, Any]:
    """Assistant turn to send back to the API. DeepSeek tool-calls need reasoning_content."""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": reply.get("content") or "",
    }
    calls = reply.get("tool_calls") or []
    if calls:
        msg["tool_calls"] = calls
    reasoning = reply.get("reasoning_content") or ""
    if reasoning:
        msg["reasoning_content"] = reasoning
    return msg


def openai_chat_body(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    body: dict[str, Any] = {"model": cfg["model"], "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    return apply_thinking(body, cfg)


def _openai(cfg: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    body = openai_chat_body(cfg, messages, tools)
    timeout = TIMEOUT_S.get(normalize_thinking(cfg.get("thinking")), 120.0)
    data = _post_json(_openai_compatible_url(cfg), headers, body, timeout, cfg.get("provider") or "openai")
    return parse_choice(data["choices"][0])


def _post_json(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    provider: str,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=body)
        if resp.status_code == 400 and ("reasoning_effort" in body or "thinking" in body):
            stripped = {k: v for k, v in body.items() if k not in {"reasoning_effort", "thinking"}}
            retry = client.post(url, headers=headers, json=stripped)
            if retry.status_code < 400:
                return retry.json()
            resp = retry
        if resp.status_code >= 400:
            raise LLMError(f"{provider} HTTP {resp.status_code}: {resp.text[:800]}")
        return resp.json()


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
    thinking = normalize_thinking(cfg.get("thinking"))
    body: dict[str, Any] = {
        "model": cfg.get("model") or "claude-sonnet-4-5",
        "max_tokens": MAX_TOKENS.get(thinking, 4096),
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
    timeout = TIMEOUT_S.get(thinking, 120.0)
    with httpx.Client(timeout=timeout) as client:
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
    return {"content": text, "tool_calls": tool_calls, "reasoning_content": "", "raw": data}


def iter_sse(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, default=str)}\n\n"
    yield "data: {\"type\": \"done\"}\n\n"
