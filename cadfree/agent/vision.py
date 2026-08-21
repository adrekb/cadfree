"""Image attachments for vision-native models (drawings, photos, screenshots)."""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

from cadfree.paths import project_dir
from cadfree.store.db import db

ALLOWED_MIME = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
MAX_BYTES = 8 * 1024 * 1024


def vision_capable(provider: str, model: str = "") -> bool:
    p = (provider or "").lower()
    m = (model or "").lower()
    if p in {"deepseek", "ollama"}:
        return False
    if "deepseek" in m:
        return False
    return p in {"openai", "anthropic", "gemini", "openrouter", "custom"}


def default_model(provider: str) -> str:
    return {
        "openai": "gpt-4.1",
        "anthropic": "claude-sonnet-4-5",
        "gemini": "gemini-2.5-flash",
        "deepseek": "deepseek-v4-pro",
        "ollama": "llama3.2",
        "openrouter": "openai/gpt-4.1",
        "custom": "",
    }.get((provider or "").lower(), "gpt-4.1")


def _attach_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_attachment(project_id: str, filename: str, mime: str, data: bytes) -> dict[str, Any]:
    mime = (mime or "").split(";")[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ALLOWED_MIME:
        raise ValueError(f"unsupported image type {mime}. Use png, jpeg, webp, or gif.")
    if len(data) > MAX_BYTES:
        raise ValueError("image is larger than 8 MB")
    aid = uuid.uuid4().hex[:16]
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[mime]
    path = _attach_dir(project_id) / f"{aid}{ext}"
    path.write_bytes(data)
    with db() as conn:
        conn.execute(
            """INSERT INTO attachments(id, project_id, filename, mime, path, created_at)
               VALUES(?,?,?,?,?,datetime('now'))""",
            (aid, project_id, filename or path.name, mime, str(path)),
        )
    return {"id": aid, "filename": filename or path.name, "mime": mime, "url": f"/api/projects/{project_id}/attachments/{aid}"}


def list_attachments(project_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, filename, mime, created_at FROM attachments WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "filename": r["filename"],
            "mime": r["mime"],
            "url": f"/api/projects/{project_id}/attachments/{r['id']}",
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def load_images(project_id: str, ids: list[str]) -> list[dict[str, str]]:
    if not ids:
        return []
    out = []
    with db() as conn:
        for aid in ids:
            row = conn.execute(
                "SELECT * FROM attachments WHERE id = ? AND project_id = ?",
                (aid, project_id),
            ).fetchone()
            if not row:
                continue
            path = Path(row["path"])
            if not path.is_file():
                continue
            raw = path.read_bytes()
            out.append(
                {
                    "id": row["id"],
                    "filename": row["filename"],
                    "mime": row["mime"],
                    "b64": base64.b64encode(raw).decode("ascii"),
                }
            )
    return out


def openai_image_parts(images: list[dict[str, str]]) -> list[dict[str, Any]]:
    parts = []
    for img in images:
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{img['mime']};base64,{img['b64']}"},
            }
        )
    return parts


def anthropic_image_blocks(images: list[dict[str, str]]) -> list[dict[str, Any]]:
    blocks = []
    for img in images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["mime"],
                    "data": img["b64"],
                },
            }
        )
    return blocks


def materialize_openai_messages(messages: list[dict[str, Any]], *, include_images: bool) -> list[dict[str, Any]]:
    out = []
    for msg in messages:
        images = msg.get("images") or []
        content = msg.get("content")
        if msg.get("role") == "user" and images:
            if include_images:
                parts: list[dict[str, Any]] = [{"type": "text", "text": content or ""}]
                parts.extend(openai_image_parts(images))
                item = {k: v for k, v in msg.items() if k != "images"}
                item["content"] = parts
                out.append(item)
            else:
                names = ", ".join(i.get("filename") or "image" for i in images)
                note = (
                    f"{content or ''}\n\n[{len(images)} image(s) attached ({names}) but this "
                    "model is not vision-native. Switch to OpenAI, Anthropic, Gemini, or an "
                    "OpenRouter vision model to read drawings/photos.]"
                ).strip()
                item = {k: v for k, v in msg.items() if k != "images"}
                item["content"] = note
                out.append(item)
            continue
        out.append({k: v for k, v in msg.items() if k != "images"})
    return out


def materialize_anthropic_user_content(msg: dict[str, Any], *, include_images: bool) -> Any:
    images = msg.get("images") or []
    text = msg.get("content") or ""
    if not images:
        return text
    if not include_images:
        names = ", ".join(i.get("filename") or "image" for i in images)
        return (
            f"{text}\n\n[{len(images)} image(s) attached ({names}) but this model is not "
            "vision-native.]"
        ).strip()
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    blocks.extend(anthropic_image_blocks(images))
    return blocks
