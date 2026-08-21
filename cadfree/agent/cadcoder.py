"""Optional CAD-Coder image→CadQuery provider. Probed, never required.

CAD-Coder is an open VLM that emits CadQuery Python from a drawing. Cadfree
talks to an OpenAI-compatible HTTP endpoint if you set cadcoder_base_url.
No URL / no GPU checkpoint → one sentence, the rest of the studio still runs.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from cadfree.agent.vision import list_attachments, load_images
from cadfree.store.db import get_setting

HONEST = (
    "CAD-Coder is an optional image→CadQuery endpoint. Cadfree does not bundle "
    "the GPU checkpoint. Missing URL = install/URL hint, not a hallucinated script."
)
_FENCE = re.compile(r"```(?:python)?\s*([\s\S]*?)```", re.IGNORECASE)


def cadcoder_status() -> dict[str, Any]:
    base = (get_setting("cadcoder_base_url") or os.environ.get("CADCODER_BASE_URL") or "").strip()
    model = (get_setting("cadcoder_model") or os.environ.get("CADCODER_MODEL") or "cad-coder").strip()
    keyed = bool(get_setting("cadcoder_api_key") or os.environ.get("CADCODER_API_KEY"))
    return {
        "id": "cadcoder",
        "label": "CAD-Coder (optional)",
        "available": bool(base),
        "base_url": base,
        "model": model,
        "api_key_set": keyed,
        "install_hint": (
            "Set cadcoder_base_url (Settings or CADCODER_BASE_URL) to an OpenAI-compatible "
            "CAD-Coder endpoint. GPU checkpoint is optional; Cadfree never requires it."
        ),
        "honest": HONEST,
    }


def draft_from_image(
    project_id: str,
    attachment_id: str | None = None,
    part_id: str | None = None,
) -> dict[str, Any]:
    status = cadcoder_status()
    if not status["available"]:
        return {
            "ok": False,
            "available": False,
            "error": status["install_hint"],
            "honest": HONEST,
        }
    aid = attachment_id
    if not aid:
        atts = list_attachments(project_id)
        if not atts:
            return {
                "ok": False,
                "error": "No image attachment. Upload a drawing or photo first.",
                "honest": HONEST,
            }
        aid = atts[-1]["id"]
    images = load_images(project_id, [aid])
    if not images:
        return {"ok": False, "error": f"attachment {aid} is missing.", "honest": HONEST}
    img = images[0]
    try:
        source = _complete(status, img)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"CAD-Coder request failed: {exc}",
            "honest": HONEST,
        }
    if not source.strip():
        return {
            "ok": False,
            "error": "CAD-Coder returned no CadQuery. Do not invent a script from the photo.",
            "honest": HONEST,
        }
    from cadfree.cad.assembly import save_part_source
    from cadfree.cad.params import extract_params

    part = save_part_source(project_id, part_id, source)
    return {
        "ok": True,
        "available": True,
        "source": source,
        "chars": len(source),
        "params": extract_params(source),
        "part_id": part["id"],
        "attachment_id": aid,
        "model": status["model"],
        "note": "Wrote CAD-Coder output. Call build_model, then verify_against_image. Not a PE stamp.",
        "honest": HONEST,
    }


def _complete(status: dict[str, Any], img: dict[str, str]) -> str:
    base = status["base_url"].rstrip("/")
    if base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    key = (get_setting("cadcoder_api_key") or os.environ.get("CADCODER_API_KEY") or "").strip()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": status["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "Emit CadQuery Python only. Assign the solid to `result`. "
                    "Keep a top-level PARAMS dict of millimetre numbers. "
                    "Do not invent dimensions the image does not show."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Write CadQuery for this drawing or photo.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{img['mime']};base64,{img['b64']}"},
                    },
                ],
            },
        ],
        "temperature": 0.1,
    }
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        payload = resp.json()
    text = (
        payload.get("choices") or [{}]
    )[0].get("message", {}).get("content") or ""
    return _extract_source(text)


def _extract_source(text: str) -> str:
    fences = _FENCE.findall(text or "")
    if fences:
        return fences[0].strip()
    return (text or "").strip()
