from cadfree.agent.providers import openai_chat_body
from cadfree.agent.vision import (
    default_model,
    materialize_anthropic_user_content,
    materialize_openai_messages,
    vision_capable,
)


def test_vision_capable_providers():
    assert vision_capable("openai", "gpt-4.1") is True
    assert vision_capable("anthropic", "claude-sonnet-4-5") is True
    assert vision_capable("gemini", "gemini-2.5-flash") is True
    assert vision_capable("openrouter", "openai/gpt-4.1") is True
    assert vision_capable("deepseek", "deepseek-v4-pro") is False
    assert vision_capable("openai", "deepseek-v4-pro") is False
    assert vision_capable("ollama", "llama3.2") is False
    assert vision_capable("ollama", "llama3.2-vision") is True
    assert default_model("gemini") == "gemini-2.5-flash"
    assert default_model("openai") == "gpt-4.1"


def test_openai_keeps_images_for_vision_models():
    img = {"filename": "drawing.png", "mime": "image/png", "b64": "aaaa"}
    msgs = [{"role": "user", "content": "make this bracket", "images": [img]}]
    out = materialize_openai_messages(msgs, include_images=True)
    content = out[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_deepseek_does_not_send_image_payload():
    img = {"filename": "drawing.png", "mime": "image/png", "b64": "aaaa"}
    msgs = [{"role": "user", "content": "make this", "images": [img]}]
    body = openai_chat_body(
        {"provider": "deepseek", "model": "deepseek-v4-pro", "thinking": "off"},
        msgs,
        [],
    )
    content = body["messages"][0]["content"]
    assert isinstance(content, str)
    assert "not vision-native" in content
    assert "aaaa" not in content


def test_anthropic_image_blocks():
    img = {"filename": "photo.jpg", "mime": "image/jpeg", "b64": "bbbb"}
    blocks = materialize_anthropic_user_content(
        {"role": "user", "content": "from this photo", "images": [img]},
        include_images=True,
    )
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["data"] == "bbbb"
