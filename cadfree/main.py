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
from cadfree.agent.mcp import PROTOCOL as MCP_PROTOCOL, mcp_endpoint
from cadfree.agent.providers import llm_config
from cadfree.agent.survey import list_pending, submit_answers
from cadfree.agent.vision import default_model, list_attachments, load_images, save_attachment, vision_capable, VISION_MODELS
from cadfree.cad.assembly import (
    assembly_scene,
    assembly_snapshot,
    compose_assembly_stl,
    ensure_default_part,
    get_part,
    part_dir,
    set_active_part,
)
from cadfree.cad.params import STARTER_BRACKET, apply_params, extract_params
from cadfree.cad.features import FEATURE_TREE_NOTE, extract_features, patch_feature
from cadfree.cad.import_cad import import_bytes, import_status
from cadfree.cad.runner import build_cadquery, cadquery_status
from cadfree.catalog import catalog_payload, preset_by_id
from cadfree.manufacturing.evaluate import evaluate
from cadfree.manufacturing.mesh import load_mesh, metrics_from_mesh
from cadfree.matlab.engine import find_engine
from cadfree.paths import project_dir
from cadfree.physics.book import list_book, lookup_formula
from cadfree.physics.dispatch import probe_solvers, run_solvers, solve_on_part
from cadfree.physics.snapshot import write_si_status
from cadfree.physics.topology import run_generate
from cadfree.simulation.pipeline import probe as sim_probe
from cadfree.kinematics.mechanism import check_mechanism, list_joints, remove_joint, sweep_mechanism, upsert_joint
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


class FeaturePatchIn(BaseModel):
    feature_id: str
    value: float
    arg_index: int = 0
    part_id: str | None = None


class JointIn(BaseModel):
    name: str
    kind: str = "revolute"
    instance_a: str = ""
    instance_b: str
    origin: dict[str, Any] = Field(default_factory=dict)
    axis: Any = "z"
    driven: bool = False
    ratio: float | None = None
    limits: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    joint_id: str | None = None


class MotionIn(BaseModel):
    start_deg: float = 0.0
    end_deg: float = 360.0
    steps: int = 24


class FormulaSolveIn(BaseModel):
    formula_id: str
    values: dict[str, Any] = Field(default_factory=dict)
    solve_for: str | None = None
    use_part: bool = True
    part_id: str | None = None


class SolversIn(BaseModel):
    solvers: list[str] | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    pack: str | None = None
    part_id: str | None = None


class GenerateIn(BaseModel):
    part_id: str | None = None
    volfrac: float | None = None
    target_mass_fraction: float | None = None
    design_space: str = "part"
    mill_25d: bool | None = None
    additive: bool | None = None
    assumed_load: bool = True


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Cadfree", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "cadquery": cadquery_status(),
            "import": import_status(),
            "matlab": find_engine(),
            "simulation": sim_probe(),
            "solvers": probe_solvers(),
            "mcp": {
                "protocol": MCP_PROTOCOL,
                "endpoint": "/mcp",
                "sessions": False,
                "transport": "streamable-http",
            },
            "llm": {k: (bool(v) if k == "api_key" else v) for k, v in llm_config().items()},
        }

    @app.post("/mcp")
    async def mcp(request: Request) -> Any:
        return await mcp_endpoint(request)

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
        cfg["ui_theme"] = "dark"
        cfg.setdefault("ui_accent", "carrot")
        cfg.setdefault("llm_provider", "openai")
        provider = cfg.get("llm_provider") or "openai"
        if not cfg.get("llm_model"):
            cfg["llm_model"] = default_model(provider)
        cfg.setdefault("llm_thinking", "high")
        cfg["vision"] = vision_capable(provider, cfg.get("llm_model") or "")
        cfg["vision_models"] = VISION_MODELS
        cfg.setdefault("search_provider", "auto")
        return cfg

    @app.put("/api/config/{key}")
    async def put_config(key: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        if key == "ui_theme":
            payload = "dark"
        set_setting(key, payload)
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
            if key == "ui_theme":
                value = "dark"
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
        from cadfree.cad.record import load_live

        live = load_live(part_dir(project_id, active["id"]) / "features.live.json")
        tree = extract_features(item["cadquery_source"], live=live)
        item["features"] = tree.get("features") or []
        item["feature_note"] = tree.get("honest") or FEATURE_TREE_NOTE
        item["feature_live"] = bool(tree.get("live"))
        item["messages"] = [dict(m) for m in messages]
        item["stl_url"] = f"/api/projects/{project_id}/stl"
        item["pending_surveys"] = list_pending(project_id)
        item["assembly"] = snap
        item["active_part_id"] = snap.get("active_part_id")
        item["joints"] = list_joints(project_id)
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

    @app.get("/api/projects/{project_id}/features")
    def get_features(project_id: str, part_id: str | None = None) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        try:
            part = get_part(project_id, part_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        if part.get("kind") == "imported":
            return {
                "features": [],
                "params": {},
                "parse_error": None,
                "imported": True,
                "note": "Imported mesh — no CadQuery feature tree. Edit it in the original program and Import CAD again.",
                "honest": FEATURE_TREE_NOTE,
            }
        from cadfree.cad.assembly import part_dir
        from cadfree.cad.record import load_live

        live = load_live(part_dir(project_id, part["id"]) / "features.live.json")
        out = extract_features(part.get("cadquery_source") or "", live=live)
        out["part_id"] = part["id"]
        out["imported"] = False
        return out

    @app.post("/api/projects/{project_id}/features/patch")
    def patch_project_feature(project_id: str, body: FeaturePatchIn) -> dict[str, Any]:
        from cadfree.cad.assembly import save_part_source

        try:
            part = get_part(project_id, body.part_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        if part.get("kind") == "imported":
            raise HTTPException(400, "Imported mesh has no feature tree.")
        source = part.get("cadquery_source") or ""
        result = patch_feature(source, body.feature_id, body.value, body.arg_index)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error") or "Could not patch that feature.")
        save_part_source(project_id, part["id"], result["source"])
        result["part_id"] = part["id"]
        return result

    @app.post("/api/projects/{project_id}/build")
    def build_project(project_id: str) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        ensure_default_part(project_id, row["cadquery_source"] or "")
        part = get_part(project_id, None)
        if part.get("kind") == "imported":
            stl = part_dir(project_id, part["id"]) / "model.stl"
            if not stl.is_file():
                return {"ok": False, "error": "Imported part has no mesh. Import CAD again."}
            mesh = load_mesh(stl)
            metrics = metrics_from_mesh(mesh)
            caps = _project_caps(json.loads(row["capability_ids"] or "[]"))
            constraints = json.loads(row["constraints"] or "{}")
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
            return {
                "ok": True,
                "imported": True,
                "error": "",
                "metrics": metrics.to_dict(),
                "feasibility": report,
                "stl_url": f"/api/projects/{project_id}/parts/{part['id']}/stl?t={_now()}",
                "scene_url": f"/api/projects/{project_id}/scene",
                "assembly": assembly_snapshot(project_id),
                "note": "Imported mesh — PARAMS do not apply.",
            }
        built = build_cadquery(part.get("cadquery_source") or row["cadquery_source"], part_dir(project_id, part["id"]))
        if not built.get("ok"):
            return built
        from cadfree.cad.assembly import save_part_metrics

        save_part_metrics(project_id, part["id"], built.get("metrics") or {})
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
        built["stl_url"] = f"/api/projects/{project_id}/parts/{part['id']}/stl?t={_now()}"
        built["scene_url"] = f"/api/projects/{project_id}/scene"
        built["assembly"] = assembly_snapshot(project_id)
        return built

    @app.get("/api/projects/{project_id}/stl")
    def get_stl(project_id: str, combined: bool = False) -> FileResponse:
        root = project_dir(project_id)
        if combined:
            composed = compose_assembly_stl(project_id)
            if not composed.get("ok"):
                raise HTTPException(400, composed.get("error") or "could not merge STL")
            path = Path(composed["stl_path"]) if composed.get("stl_path") else None
            if path and path.is_file():
                return FileResponse(path, media_type="model/stl", filename="assembly.stl")
            raise HTTPException(404, composed.get("note") or "no solids to merge")
        try:
            part = get_part(project_id, None)
            pth = part_dir(project_id, part["id"]) / "model.stl"
            if pth.is_file():
                return FileResponse(pth, media_type="model/stl", filename=f"{part.get('name') or 'part'}.stl")
        except KeyError:
            pass
        for path in (root / "model.stl",):
            if path.is_file():
                return FileResponse(path, media_type="model/stl", filename=path.name)
        raise HTTPException(404, "no STL yet — rebuild from the editor")

    @app.get("/api/projects/{project_id}/parts/{part_id}/stl")
    def get_part_stl(project_id: str, part_id: str) -> FileResponse:
        try:
            part = get_part(project_id, part_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        pth = part_dir(project_id, part["id"]) / "model.stl"
        if not pth.is_file():
            raise HTTPException(404, "rebuild this unique part first")
        return FileResponse(pth, media_type="model/stl", filename=f"{part.get('name') or part_id}.stl")

    @app.get("/api/projects/{project_id}/scene")
    def get_scene(project_id: str) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        ensure_default_part(project_id)
        return assembly_scene(project_id)

    @app.get("/api/projects/{project_id}/assembly")
    def get_assembly(project_id: str) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return assembly_snapshot(project_id)

    @app.get("/api/projects/{project_id}/joints")
    def get_joints(project_id: str) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return {"joints": list_joints(project_id)}

    @app.post("/api/projects/{project_id}/joints")
    def post_joint(project_id: str, body: JointIn) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        try:
            joint = upsert_joint(
                project_id,
                name=body.name,
                kind=body.kind,
                instance_a=body.instance_a,
                instance_b=body.instance_b,
                origin=body.origin,
                axis=body.axis,
                driven=body.driven,
                ratio=body.ratio,
                limits=body.limits,
                params=body.params,
                joint_id=body.joint_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "joint": joint, "joints": list_joints(project_id)}

    @app.delete("/api/projects/{project_id}/joints/{joint_id}")
    def delete_joint(project_id: str, joint_id: str) -> dict[str, Any]:
        return {"ok": remove_joint(project_id, joint_id)}

    @app.get("/api/projects/{project_id}/motion")
    def get_motion(
        project_id: str, start_deg: float = 0.0, end_deg: float = 360.0, steps: int = 24
    ) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return sweep_mechanism(project_id, start_deg, end_deg, steps, include_frames=True)

    @app.post("/api/projects/{project_id}/motion")
    def post_motion(project_id: str, body: MotionIn) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return sweep_mechanism(
            project_id, body.start_deg, body.end_deg, body.steps, include_frames=True
        )

    @app.get("/api/projects/{project_id}/mechanism")
    def get_mechanism_check(
        project_id: str, start_deg: float = 0.0, end_deg: float = 360.0, steps: int = 36
    ) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return check_mechanism(project_id, start_deg, end_deg, steps)

    @app.get("/api/physics/book")
    def physics_book(domain: str | None = None, q: str | None = None) -> dict[str, Any]:
        if q:
            return lookup_formula(q, domain=domain)
        return {"ok": True, "formulas": list_book(domain), "probe": probe_solvers()}

    @app.get("/api/projects/{project_id}/si-status")
    def get_si_status(project_id: str, part_id: str | None = None) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        try:
            return write_si_status(project_id, part_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/projects/{project_id}/solvers")
    def post_solvers(project_id: str, body: SolversIn) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return run_solvers(
            project_id, solvers=body.solvers, values=body.values, pack=body.pack, part_id=body.part_id
        )

    @app.post("/api/projects/{project_id}/generate")
    def post_generate(project_id: str, body: GenerateIn) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        vf = body.volfrac if body.volfrac is not None else body.target_mass_fraction
        return run_generate(
            project_id,
            part_id=body.part_id,
            volfrac=vf,
            design_space=body.design_space,
            mill_25d=body.mill_25d,
            additive=body.additive,
            assumed_load=body.assumed_load,
        )

    @app.post("/api/projects/{project_id}/formula")
    def post_formula(project_id: str, body: FormulaSolveIn) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        if body.use_part:
            return solve_on_part(
                project_id, body.formula_id, values=body.values, solve_for=body.solve_for, part_id=body.part_id
            )
        from cadfree.physics.engine import solve_formula as _solve

        return _solve(body.formula_id, body.values, solve_for=body.solve_for)

    @app.post("/api/projects/{project_id}/parts/{part_id}/activate")
    def activate_part(project_id: str, part_id: str) -> dict[str, Any]:
        try:
            part = set_active_part(project_id, part_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "part": part, "params": extract_params(part.get("cadquery_source") or "")}

    @app.post("/api/projects/{project_id}/import")
    async def import_cad_file(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        data = await file.read()
        result = import_bytes(project_id, file.filename or "import.bin", data)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error") or "import failed")
        result["assembly"] = assembly_snapshot(project_id)
        result["scene_url"] = f"/api/projects/{project_id}/scene"
        slim_parts = []
        for part in result.get("parts") or []:
            slim_parts.append(
                {
                    "id": part["id"],
                    "name": part.get("name"),
                    "kind": part.get("kind"),
                    "metrics": part.get("metrics") or {},
                }
            )
        result["parts"] = slim_parts
        return result

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
                "ask_survey, search_standards, search_parts, check_feasibility, "
                "read_url, and list_assembly. "
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
