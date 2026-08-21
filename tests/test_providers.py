from cadfree.agent.providers import (
    apply_thinking,
    assistant_history_message,
    normalize_thinking,
    openai_chat_body,
    parse_choice,
    uses_deepseek_thinking,
)


def test_normalize_thinking():
    assert normalize_thinking("MAX") == "max"
    assert normalize_thinking("none") == "off"
    assert normalize_thinking("medium") == "high"
    assert normalize_thinking("nope") == "high"


def test_deepseek_thinking_body():
    cfg = {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "max"}
    body = openai_chat_body(cfg, [{"role": "user", "content": "hi"}], [])
    assert body["reasoning_effort"] == "max"
    assert body["thinking"] == {"type": "enabled"}
    assert body["max_tokens"] == 65536

    off = apply_thinking({"model": "deepseek-v4-pro"}, {**cfg, "thinking": "off"})
    assert off["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in off


def test_openai_maps_max_to_xhigh():
    cfg = {"provider": "openai", "model": "gpt-5", "thinking": "max"}
    body = apply_thinking({"model": "gpt-5"}, cfg)
    assert body["reasoning_effort"] == "xhigh"
    assert "thinking" not in body


def test_parse_choice_and_passback():
    parsed = parse_choice(
        {
            "message": {
                "content": "ok",
                "reasoning_content": "first check the load",
                "tool_calls": [{"id": "c1", "function": {"name": "ask_survey", "arguments": "{}"}}],
            }
        }
    )
    assert parsed["reasoning_content"] == "first check the load"
    hist = assistant_history_message(parsed)
    assert hist["reasoning_content"] == "first check the load"
    assert hist["tool_calls"][0]["id"] == "c1"


def test_openrouter_deepseek_uses_thinking_object():
    cfg = {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro", "thinking": "low"}
    assert uses_deepseek_thinking(cfg) is True
    body = apply_thinking({"model": cfg["model"]}, cfg)
    assert body["thinking"]["type"] == "enabled"
    assert body["reasoning_effort"] == "low"
