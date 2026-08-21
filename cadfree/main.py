from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cadfree.agent.loop import run_turn
from cadfree.agent.providers import llm_config
from cadfree.agent.survey import list_pending, submit_answers
from cadfree.agent.vision import default_model, list_attachments, load_images, save_attachment, vision_capable
from cadfree.cad.assembly import (
    assembly_snapshot,
    compose_assembly_stl,
    ensure_default_part,
    get_part,
    part_dir,
    set_active_part,
)
from cadfree.cad.params import STARTER_BRACKET, apply_params, extract_params
from cadfree.cad.runner import build_cadquery, cadquery_status
from cadfree.catalog import catalog_payload, preset_by_id
from cadfree.manufacturing.evaluate import evaluate
from cadfree.manufacturing.mesh import load_mesh, metrics_from_mesh
from cadfree.matlab.engine import find_engine
from cadfree.paths import project_dir
from cadfree.simulation.pipeline import probe as sim_probe
from cadfree.store.db import all_settings, db, init_db, set_setting

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class SettingsIn(BaseModel):
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    ui_theme: str | None = None
    ui_accent: str | None = None
    search_provider: str | None = None
    search_api_key: str | None = None
    llm_thinking: str | None = None


class CapabilityIn(BaseModel):
    kind: str
    name: str
    preset_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    materials: list[Any] = Field(default_factory=list)
    notes: str = ""


class ProjectIn(BaseModel):
    name: str
    spec_text: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    capability_ids: list[str] = Field(default_factory=list)


class SourceIn(BaseModel):
    source: str


class ParamsIn(BaseModel):
    params: dict[str, Any]


class ChatIn(BaseModel):
    content: str
    mode: str = "agent"
    attachment_ids: list[str] = Field(default_factory=list)


class SurveyAnswersIn(BaseModel):
    answers: dict[str, Any]


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Cadfree", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "cadquery": cadquery_status(),
            "matlab": find_engine(),
            "simulation": sim_probe(),
            "llm": {k: (bool(v) if k == "api_key" else v) for k, v in llm_config().items()},
        }

    @app.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        return catalog_payload()

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        cfg = all_settings()
        if cfg.get("llm_api_key"):
            cfg["llm_api_key_set"] = True
            cfg["llm_api_key"] = ""
        else:
            cfg["llm_api_key_set"] = False
        if cfg.get("search_api_key"):
            cfg["search_api_key_set"] = True
            cfg["search_api_key"] = ""
        else:
            cfg["search_api_key_set"] = False
        cfg.setdefault("ui_theme", "auto")
        cfg.setdefault("ui_accent", "carrot")
        cfg.setdefault("llm_provider", "openai")
        provider = cfg.get("llm_provider") or "openai"
        if not cfg.get("llm_model"):
            cfg["llm_model"] = default_model(provider)
        cfg.setdefault("llm_thinking", "high")
        cfg["vision"] = vision_capable(provider, cfg.get("llm_model") or "")
        cfg.setdefault("search_provider", "auto")
        return cfg

    @app.put("/api/config/{key}")
    async def put_config(key: str, request: Request) -> dict[str, Any]:
        set_setting(key, await request.json())
        return {"ok": True}

    @app.post("/api/settings")
    def save_settings(body: SettingsIn) -> dict[str, Any]:
        data = body.model_dump(exclude_none=True)
        if data.get("llm_api_key") == "":
            data.pop("llm_api_key", None)
        if data.get("search_api_key") == "":
            data.pop("search_api_key", None)
        for key, value in data.items():
            if key == "llm_thinking":
                from cadfree.agent.providers import normalize_thinking

                value = normalize_thinking(value)
            set_setting(key, value)
        return config()

    @app.get("/api/workshop")
    def list_workshop() -> dict[str, Any]:
        with db() as conn:
            rows = conn.execute("SELECT * FROM capabilities ORDER BY name").fetchall()
        caps = []
        for row in rows:
            item = dict(row)
            item["params"] = json.loads(item["params"] or "{}")
            item["materials"] = json.loads(item["materials"] or "[]")
            caps.append(item)
        return {"capabilities": caps}

    @app.post("/api/workshop")
    def add_capability(body: CapabilityIn) -> dict[str, Any]:
        preset = preset_by_id(body.preset_id) if body.preset_id else None
        params = dict((preset or {}).get("params") or {})
        params.update(body.params)
        materials = body.materials or list((preset or {}).get("suggested_materials") or [])
        name = body.name or (preset["name"] if preset else body.kind)
        cid = _new_id()
        with db() as conn:
            conn.execute(
                """INSERT INTO capabilities(id, kind, name, preset_id, params, materials, notes)
                   VALUES(?,?,?,?,?,?,?)""",
                (cid, body.kind, name, body.preset_id, json.dumps(params), json.dumps(materials), body.notes),
            )
        return {"id": cid, "kind": body.kind, "name": name, "params": params, "materials": materials}

    @app.delete("/api/workshop/{cap_id}")
    def delete_capability(cap_id: str) -> dict[str, Any]:
        with db() as conn:
            conn.execute("DELETE FROM capabilities WHERE id = ?", (cap_id,))
        return {"ok": True}

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        with db() as conn:
            rows = conn.execute(
                "SELECT id, name, spec_text, status, updated_at FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return {"projects": [dict(r) for r in rows]}

    @app.post("/api/projects")
    def create_project(body: ProjectIn) -> dict[str, Any]:
        pid = _new_id()
        now = _now()
        with db() as conn:
            conn.execute(
                """INSERT INTO projects(id, name, spec_text, constraints, capability_ids,
                   cadquery_source, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    body.name,
                    body.spec_text,
                    json.dumps(body.constraints),
                    json.dumps(body.capability_ids),
                    STARTER_BRACKET,
                    now,
                    now,
                ),
            )
        ensure_default_part(pid, STARTER_BRACKET, body.name or "main")
        return {"id": pid}

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            messages = conn.execute(
                "SELECT role, content, tool_name, created_at FROM messages WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        if not row:
            raise HTTPException(404, "project not found")
        item = dict(row)
        ensure_default_part(project_id, item.get("cadquery_source") or "", item.get("name") or "main")
        snap = assembly_snapshot(project_id)
        active = get_part(project_id, None)
        item["constraints"] = json.loads(item["constraints"] or "{}")
        item["capability_ids"] = json.loads(item["capability_ids"] or "[]")
        item["metrics"] = json.loads(item["metrics"] or "{}")
        item["feasibility"] = json.loads(item["feasibility"] or "{}")
        item["cadquery_source"] = active.get("cadquery_source") or item.get("cadquery_source") or ""
        item["params"] = extract_params(item["cadquery_source"])
        item["messages"] = [dict(m) for m in messages]
        item["stl_url"] = f"/api/projects/{project_id}/stl"
        item["pending_surveys"] = list_pending(project_id)
        item["assembly"] = snap
        item["active_part_id"] = snap.get("active_part_id")
        item["vision_attachments"] = list_attachments(project_id)
        return item

    @app.put("/api/projects/{project_id}/source")
    def save_source(project_id: str, body: SourceIn) -> dict[str, Any]:
        from cadfree.cad.assembly import save_part_source

        part = save_part_source(project_id, None, body.source)
        return {"ok": True, "params": extract_params(body.source), "part_id": part["id"]}

    @app.put("/api/projects/{project_id}/params")
    def save_params(project_id: str, body: ParamsIn) -> dict[str, Any]:
        part = get_part(project_id, None)
        source = apply_params(part.get("cadquery_source") or "", body.params)
        from cadfree.cad.assembly import save_part_source

        save_part_source(project_id, part["id"], source)
        return {"ok": True, "source": source, "params": extract_params(source)}

    @app.post("/api/projects/{project_id}/build")
    def build_project(project_id: str) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        ensure_default_part(project_id, row["cadquery_source"] or "")
        part = get_part(project_id, None)
        built = build_cadquery(part.get("cadquery_source") or row["cadquery_source"], part_dir(project_id, part["id"]))
        if not built.get("ok"):
            return built
        from cadfree.cad.assembly import save_part_metrics

        save_part_metrics(project_id, part["id"], built.get("metrics") or {})
        compose_assembly_stl(project_id)
        caps = _project_caps(json.loads(row["capability_ids"] or "[]"))
        constraints = json.loads(row["constraints"] or "{}")
        mesh = load_mesh(built["stl_path"])
        metrics = metrics_from_mesh(mesh)
        report = evaluate(metrics, caps, constraints).to_dict()
        with db() as conn:
            conn.execute(
                """UPDATE projects SET metrics = ?, feasibility = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    json.dumps(metrics.to_dict()),
                    json.dumps(report),
                    "feasible" if report.get("possible") else "infeasible",
                    _now(),
                    project_id,
                ),
            )
        built["feasibility"] = report
        built["metrics"] = metrics.to_dict()
        built["stl_url"] = f"/api/projects/{project_id}/stl?t={_now()}"
        built["assembly"] = assembly_snapshot(project_id)
        return built

    @app.get("/api/projects/{project_id}/stl")
    def get_stl(project_id: str) -> FileResponse:
        root = project_dir(project_id)
        for path in (root / "assembly.stl", root / "model.stl"):
            if path.is_file():
                return FileResponse(path, media_type="model/stl", filename=path.name)
        try:
            part = get_part(project_id, None)
            pth = part_dir(project_id, part["id"]) / "model.stl"
            if pth.is_file():
                return FileResponse(pth, media_type="model/stl", filename="model.stl")
        except KeyError:
            pass
        raise HTTPException(404, "no STL yet — rebuild from the editor")

    @app.get("/api/projects/{project_id}/assembly")
    def get_assembly(project_id: str) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return assembly_snapshot(project_id)

    @app.post("/api/projects/{project_id}/parts/{part_id}/activate")
    def activate_part(project_id: str, part_id: str) -> dict[str, Any]:
        try:
            part = set_active_part(project_id, part_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "part": part, "params": extract_params(part.get("cadquery_source") or "")}

    @app.post("/api/projects/{project_id}/attachments")
    async def upload_attachment(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        data = await file.read()
        try:
            return save_attachment(project_id, file.filename or "image", file.content_type or "image/png", data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/projects/{project_id}/attachments/{att_id}")
    def get_attachment(project_id: str, att_id: str) -> FileResponse:
        with db() as conn:
            row = conn.execute(
                "SELECT path, mime, filename FROM attachments WHERE id = ? AND project_id = ?",
                (att_id, project_id),
            ).fetchone()
        if not row:
            raise HTTPException(404, "attachment not found")
        path = Path(row["path"])
        if not path.is_file():
            raise HTTPException(404, "attachment file missing")
        return FileResponse(path, media_type=row["mime"], filename=row["filename"])

    @app.post("/api/projects/{project_id}/chat")
    def chat(project_id: str, body: ChatIn) -> StreamingResponse:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
            prior = conn.execute(
                "SELECT role, content FROM messages WHERE project_id = ? AND role IN ('user','assistant') ORDER BY created_at",
                (project_id,),
            ).fetchall()
        if not row:
            raise HTTPException(404, "project not found")
        history = [{"role": r["role"], "content": r["content"]} for r in prior][-24:]
        images = load_images(project_id, body.attachment_ids or [])
        prefix = ""
        if body.mode == "plan":
            prefix = (
                "[Plan mode: do not write CadQuery or run MATLAB. You MAY "
                "ask_survey, search_standards, read_url, and list_assembly. "
                "Propose geometry, process, and simulation rungs only.]\n\n"
            )

        def gen():
            with db() as conn:
                conn.execute(
                    "INSERT INTO messages(id, project_id, role, content, created_at) VALUES(?,?,?,?,?)",
                    (_new_id(), project_id, "user", body.content, _now()),
                )
            assistant_bits: list[str] = []
            for event in run_turn(
                project_id,
                history,
                prefix + body.content,
                mode=body.mode,
                images=images,
            ):
                if event.get("type") == "assistant":
                    assistant_bits.append(event.get("content") or "")
                yield f"data: {json.dumps(event, default=str)}\n\n"
            text = "\n".join(assistant_bits).strip()
            if text:
                with db() as conn:
                    conn.execute(
                        "INSERT INTO messages(id, project_id, role, content, created_at) VALUES(?,?,?,?,?)",
                        (_new_id(), project_id, "assistant", text, _now()),
                    )
            yield "data: {\"type\": \"done\"}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/projects/{project_id}/surveys")
    def get_surveys(project_id: str) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return {"surveys": list_pending(project_id)}

    @app.post("/api/projects/{project_id}/survey/{survey_id}")
    def post_survey(project_id: str, survey_id: str, body: SurveyAnswersIn) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute(
                "SELECT id, project_id, status FROM surveys WHERE id = ?",
                (survey_id,),
            ).fetchone()
        if not row or row["project_id"] != project_id:
            raise HTTPException(404, "survey not found")
        if not submit_answers(survey_id, body.answers):
            raise HTTPException(400, "could not record answers")
        return {"ok": True, "survey_id": survey_id, "answers": body.answers}

    if WEB_DIR.is_dir():
        app.mount("/css", StaticFiles(directory=WEB_DIR / "css"), name="css")
        app.mount("/js", StaticFiles(directory=WEB_DIR / "js"), name="js")
        app.mount("/vendor", StaticFiles(directory=WEB_DIR / "vendor"), name="vendor")
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


def _project_caps(ids: list[str]) -> list[dict[str, Any]]:
    with db() as conn:
        if not ids:
            rows = conn.execute("SELECT * FROM capabilities").fetchall()
        else:
            rows = []
            for cid in ids:
                row = conn.execute("SELECT * FROM capabilities WHERE id = ?", (cid,)).fetchone()
                if row:
                    rows.append(row)
    out = []
    for row in rows:
        item = dict(row)
        item["params"] = json.loads(item["params"] or "{}")
        item["materials"] = json.loads(item["materials"] or "[]")
        out.append(item)
    return out


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("cadfree.main:app", host="127.0.0.1", port=8181, reload=False)


if __name__ == "__main__":
    run()
