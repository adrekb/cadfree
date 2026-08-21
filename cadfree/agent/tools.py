from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cadfree.agent.survey import create_survey, list_pending, normalize_questions, questions_for_template
from cadfree.cad.assembly import (
    assembly_snapshot,
    ensure_default_part,
    get_part,
    part_dir,
    place_instance,
    remove_instance,
    save_part_metrics,
    save_part_source,
    set_active_part,
    upsert_part,
)
from cadfree.cad.params import apply_params, extract_params
from cadfree.cad.features import FEATURE_TREE_NOTE, extract_features, patch_feature
from cadfree.cad.import_cad import import_status
from cadfree.cad.runner import build_cadquery, cadquery_status
from cadfree.catalog import MATERIALS, catalog_payload
from cadfree.manufacturing.evaluate import evaluate
from cadfree.manufacturing.mesh import MeshMetrics, load_mesh, metrics_from_mesh
from cadfree.matlab.engine import find_engine, run_matlab
from cadfree.paths import project_dir
from cadfree.search.standards import read_url, search_standards
from cadfree.simulation.pipeline import probe as sim_probe, simulate
from cadfree.kinematics.mechanism import (
    check_gears,
    check_mechanism,
    list_joints,
    remove_joint,
    sweep_mechanism,
    upsert_joint,
)
from cadfree.physics.book import lookup_formula
from cadfree.physics.dispatch import run_solvers, solve_on_part
from cadfree.physics.topology import run_generate
from cadfree.physics.engine import solve_formula
from cadfree.store.db import db
from cadfree.cots.kit import commit_cots_kit, search_parts
from cadfree.cots.score import catalog_spec_overlay
from cadfree.cots.springs import spring_spec_overlay
from cadfree.kinematics.loads import mechanism_load_overlay


def _row(project_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise KeyError(f"unknown project {project_id}")
    return dict(row)


def _caps(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        with db() as conn:
            rows = conn.execute("SELECT * FROM capabilities").fetchall()
        return [_cap_dict(r) for r in rows]
    out = []
    with db() as conn:
        for cid in ids:
            row = conn.execute("SELECT * FROM capabilities WHERE id = ?", (cid,)).fetchone()
            if row:
                out.append(_cap_dict(row))
    return out


def _cap_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["params"] = json.loads(d.get("params") or "{}")
    d["materials"] = json.loads(d.get("materials") or "[]")
    return d


def _metrics(project: dict[str, Any], work: Path) -> MeshMetrics | None:
    stl = work / "model.stl"
    if stl.is_file():
        return metrics_from_mesh(load_mesh(stl))
    raw = json.loads(project.get("metrics") or "{}")
    if not raw:
        return None
    bbox = raw.get("bbox_mm") or [0, 0, 0]
    return MeshMetrics(
        volume_mm3=float(raw.get("volume_mm3") or 0),
        surface_area_mm2=float(raw.get("surface_area_mm2") or 0),
        bbox_mm=(float(bbox[0]), float(bbox[1]), float(bbox[2])),
        watertight=bool(raw.get("watertight")),
        triangle_count=int(raw.get("triangle_count") or 0),
        solidity=float(raw.get("solidity") or 0),
        overhang_ratio=float(raw.get("overhang_ratio") or 0),
        min_thickness_mm=raw.get("min_thickness_mm"),
    )


def _save_source(project_id: str, source: str, part_id: str | None = None) -> dict[str, Any]:
    return save_part_source(project_id, part_id, source)


def _save_build(project_id: str, built: dict[str, Any], feasibility: dict[str, Any] | None = None) -> None:
    with db() as conn:
        conn.execute(
            """UPDATE projects SET metrics = ?, feasibility = ?, status = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (
                json.dumps(built.get("metrics") or {}),
                json.dumps(feasibility or {}),
                "feasible"
                if (feasibility or {}).get("possible")
                else ("infeasible" if feasibility else "designed"),
                project_id,
            ),
        )


def _last_solvers(project_id: str) -> dict[str, Any] | None:
    path = project_dir(project_id) / "sim" / "last.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def make_handlers(project_id: str) -> dict[str, Any]:
    def get_workshop() -> dict[str, Any]:
        with db() as conn:
            caps = [_cap_dict(r) for r in conn.execute("SELECT * FROM capabilities").fetchall()]
        return {"capabilities": caps, "catalog": catalog_payload(), "cadquery": cadquery_status(), "import": import_status(), "matlab": find_engine(), "simulation": sim_probe()}

    def get_project() -> dict[str, Any]:
        p = _row(project_id)
        ensure_default_part(project_id, p.get("cadquery_source") or "")
        snap = assembly_snapshot(project_id)
        active = get_part(project_id, None)
        src = active.get("cadquery_source") or p["cadquery_source"] or ""
        from cadfree.cad.record import load_live

        tree = extract_features(src, live=load_live(part_dir(project_id, active["id"]) / "features.live.json"))
        return {
            "id": p["id"],
            "name": p["name"],
            "spec_text": p["spec_text"],
            "constraints": json.loads(p["constraints"] or "{}"),
            "capability_ids": json.loads(p["capability_ids"] or "[]"),
            "cadquery_source": active.get("cadquery_source") or p["cadquery_source"],
            "params": extract_params(active.get("cadquery_source") or p["cadquery_source"] or ""),
            "metrics": json.loads(p["metrics"] or "{}"),
            "feasibility": json.loads(p["feasibility"] or "{}"),
            "status": p["status"],
            "pending_surveys": list_pending(project_id),
            "assembly": snap,
            "active_part_id": snap.get("active_part_id"),
            "joints": list_joints(project_id),
            "last_solvers": _last_solvers(project_id),
            "feature_tree": {
                "count": len(tree.get("features") or []),
                "kinds": [f["kind"] for f in (tree.get("features") or [])],
                "note": "Call list_features to inspect ops; patch_feature to change one fillet without rewriting the script. Not a SolidWorks history kernel.",
            },
        }

    def write_cadquery(source: str, part_id: str | None = None) -> dict[str, Any]:
        part = _save_source(project_id, source, part_id)
        return {"ok": True, "params": extract_params(source), "chars": len(source), "part_id": part["id"], "part": part["name"]}

    def set_params(params: dict[str, Any], part_id: str | None = None) -> dict[str, Any]:
        part = get_part(project_id, part_id)
        source = apply_params(part.get("cadquery_source") or "", params)
        saved = _save_source(project_id, source, part["id"])
        return {"ok": True, "params": extract_params(source), "part_id": saved["id"]}

    def list_features(part_id: str | None = None) -> dict[str, Any]:
        part = get_part(project_id, part_id)
        if part.get("kind") == "imported":
            return {
                "ok": True,
                "imported": True,
                "features": [],
                "note": "Imported mesh — no CadQuery feature tree.",
                "honest": FEATURE_TREE_NOTE,
            }
        from cadfree.cad.record import load_live

        out = extract_features(
            part.get("cadquery_source") or "",
            live=load_live(part_dir(project_id, part["id"]) / "features.live.json"),
        )
        out["ok"] = True
        out["part_id"] = part["id"]
        return out

    def patch_one_feature(feature_id: str, value: float, arg_index: int = 0, part_id: str | None = None) -> dict[str, Any]:
        part = get_part(project_id, part_id)
        if part.get("kind") == "imported":
            return {"ok": False, "error": "Imported mesh has no feature tree."}
        result = patch_feature(part.get("cadquery_source") or "", feature_id, float(value), int(arg_index or 0))
        if result.get("ok"):
            _save_source(project_id, result["source"], part["id"])
            result["part_id"] = part["id"]
        return result

    def build_model(part_id: str | None = None) -> dict[str, Any]:
        ensure_default_part(project_id)
        part = get_part(project_id, part_id)
        source = part.get("cadquery_source") or ""
        if part.get("kind") in {"purchased", "fastener"}:
            return {"ok": True, "purchased": True, "part_id": part["id"], "note": "Purchased/fastener — no CadQuery solid."}
        if part.get("kind") == "imported":
            stl = part_dir(project_id, part["id"]) / "model.stl"
            if not stl.is_file():
                return {"ok": False, "error": "Imported part has no mesh. Import CAD again."}
            metrics = _metrics({}, stl.parent)
            built = {
                "ok": True,
                "imported": True,
                "metrics": metrics.to_dict() if metrics else {},
                "note": "Imported mesh — PARAMS do not apply.",
            }
            save_part_metrics(project_id, part["id"], built.get("metrics") or {})
            _save_build(project_id, built)
            built["assembly"] = assembly_snapshot(project_id)
            built["scene_url"] = f"/api/projects/{project_id}/scene"
            built["part_id"] = part["id"]
            return built
        if not source.strip():
            return {"ok": False, "error": "No CadQuery source yet. Call write_cadquery or upsert_part first."}
        built = build_cadquery(source, part_dir(project_id, part["id"]))
        if built.get("ok"):
            save_part_metrics(project_id, part["id"], built.get("metrics") or {})
            _save_build(project_id, built)
            built["assembly"] = assembly_snapshot(project_id)
            built["scene_url"] = f"/api/projects/{project_id}/scene"
        out = {k: v for k, v in built.items() if k != "stl_path"}
        out["part_id"] = part["id"]
        return out

    def check_feasibility() -> dict[str, Any]:
        p = _row(project_id)
        ensure_default_part(project_id, p.get("cadquery_source") or "")
        snap = assembly_snapshot(project_id)
        caps = _caps(json.loads(p["capability_ids"] or "[]"))
        constraints = json.loads(p["constraints"] or "{}")
        work = project_dir(project_id)
        active = get_part(project_id, None)
        stl = part_dir(project_id, active["id"]) / "model.stl"
        if not stl.is_file():
            stl = work / "model.stl"
        metrics = _metrics(p, stl.parent) if stl.is_file() else _metrics(p, work)
        overlay_v = catalog_spec_overlay(constraints)
        overlay_s = spring_spec_overlay(constraints)
        overlay_m = mechanism_load_overlay(project_id, constraints)
        overlays = [o for o in (overlay_v, overlay_s, overlay_m) if o]

        def _apply_overlays(data: dict[str, Any]) -> dict[str, Any]:
            if overlay_v:
                data["catalog_class"] = overlay_v.get("score")
            if overlay_s:
                data["catalog_spring"] = overlay_s.get("score")
            if overlay_m:
                data["mechanism_loads"] = overlay_m.get("score")
            if not overlays:
                return data
            for o in overlays:
                data["checks"] = list(o.get("checks") or []) + list(data.get("checks") or [])
                data["recommendations"] = list(o.get("recommendations") or []) + list(data.get("recommendations") or [])
                data["assumptions"] = list(o.get("assumptions") or []) + list(data.get("assumptions") or [])
            fail = next((o for o in overlays if o.get("possible") is False), None)
            if fail:
                data["possible"] = False
                data["verdict"] = fail["verdict"]
                data["summary"] = fail["summary"]
            return data

        if metrics is None:
            if overlays:
                data = {
                    "ok": True,
                    "possible": True,
                    "verdict": "feasible",
                    "summary": overlays[0]["summary"],
                    "checks": [],
                    "recommendations": [],
                    "mass": {},
                    "strength": {},
                    "assumptions": ["No mesh yet — catalog / spec / pose statics, not DFM on a solid."],
                    "geometry": False,
                }
                data = _apply_overlays(data)
                _save_build(project_id, {"metrics": {}}, data)
                return data
            return {"ok": False, "error": "Build the model before checking feasibility."}

        report = evaluate(metrics, caps, constraints)
        data = report.to_dict()
        data["ok"] = True
        data["geometry"] = True
        data = _apply_overlays(data)
        if snap.get("expanded_count", 1) > 1:
            qty = next((b["qty"] for b in snap.get("bom") or [] if b["part_id"] == active["id"]), 1)
            mass = dict(data.get("mass") or {})
            if mass.get("mass_g") is not None:
                mass["unit_mass_g"] = mass["mass_g"]
                mass["mass_g"] = float(mass["mass_g"]) * qty
                mass["qty"] = qty
                data["mass"] = mass
            data["assembly"] = {"expanded": snap["expanded_count"], "bom": snap.get("bom")}
            data["assumptions"] = list(data.get("assumptions") or []) + [
                "Assembly mass is unique-part mass × instance count. Mate/joint strength is not FEA."
            ]
        _save_build(project_id, {"metrics": metrics.to_dict()}, data)
        return data

    def run_simulation(prefer: str = "auto") -> dict[str, Any]:
        p = _row(project_id)
        metrics = _metrics(p, project_dir(project_id))
        if metrics is None:
            return {"ok": False, "error": "Build the model before simulating."}
        constraints = json.loads(p["constraints"] or "{}")
        caps = _caps(json.loads(p["capability_ids"] or "[]"))
        kind = (caps[0]["kind"] if caps else "fdm")
        material = constraints.get("material_id") or "petg"
        if caps:
            mats = caps[0].get("materials") or []
            if mats and not constraints.get("material_id"):
                material = mats[0] if isinstance(mats[0], str) else mats[0].get("id", "petg")
        return simulate(metrics, material, constraints, process_kind=kind, prefer=prefer)

    def matlab_run(code: str) -> dict[str, Any]:
        return run_matlab(code, workdir=project_dir(project_id) / "matlab")

    def lookup_material(material_id: str) -> dict[str, Any]:
        if material_id not in MATERIALS:
            return {"ok": False, "error": f"unknown material {material_id}", "known": list(MATERIALS)}
        return MATERIALS[material_id]

    def search_std(query: str, intent: str = "standards", max_results: int = 8) -> dict[str, Any]:
        return search_standards(query, intent=intent, max_results=int(max_results or 8))

    def fetch_url(url: str) -> dict[str, Any]:
        return read_url(url)

    def ask_survey(questions: Any = None, title: str = "", template: str = "") -> dict[str, Any]:
        tpl = (template or "").strip()
        if tpl:
            spec = questions_for_template(tpl)
            qs = spec["questions"]
            title = title or spec["title"]
        elif questions:
            qs = normalize_questions(questions)
        else:
            raise ValueError("ask_survey needs template=drone|load or a questions list")
        if questions and tpl:
            qs = normalize_questions(questions)
        created = create_survey(project_id, title, qs)
        return {
            "ok": True,
            "survey_id": created["id"],
            "title": created["title"],
            "questions": created["questions"],
            "template": tpl or None,
            "waiting": True,
            "note": "The form is on screen. Do not invent answers; wait for the tool result.",
        }

    def search_cots(
        query: str,
        role: str = "",
        class_id: str = "",
        budget_usd: float | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        return search_parts(
            query,
            role=role or None,
            class_id=class_id or None,
            budget_usd=budget_usd,
            limit=int(limit or 8),
        )

    def commit_kit(
        class_id: str = "",
        motor_id: str = "",
        prop_id: str = "",
        battery_id: str = "",
        fc_id: str = "",
        esc_id: str = "",
        accept_alternative: bool = False,
        printed_frame: bool | None = None,
    ) -> dict[str, Any]:
        return commit_cots_kit(
            project_id,
            class_id=class_id or None,
            motor_id=motor_id or None,
            prop_id=prop_id or None,
            battery_id=battery_id or None,
            fc_id=fc_id or None,
            esc_id=esc_id or None,
            accept_alternative=bool(accept_alternative),
            printed_frame=printed_frame,
        )

    def list_assy() -> dict[str, Any]:
        p = _row(project_id)
        ensure_default_part(project_id, p.get("cadquery_source") or "")
        snap = assembly_snapshot(project_id)
        snap["joints"] = list_joints(project_id)
        return snap

    def define_joint(
        name: str,
        kind: str = "revolute",
        instance_a: str = "",
        instance_b: str = "",
        origin: dict[str, Any] | None = None,
        axis: Any = "z",
        driven: bool = False,
        ratio: float | None = None,
        limits: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        joint_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            joint = upsert_joint(
                project_id,
                name=name,
                kind=kind,
                instance_a=instance_a,
                instance_b=instance_b,
                origin=origin,
                axis=axis,
                driven=driven,
                ratio=ratio,
                limits=limits,
                params=params,
                joint_id=joint_id,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "joint": joint, "joints": list_joints(project_id)}

    def drop_joint(joint_id: str) -> dict[str, Any]:
        return {"ok": remove_joint(project_id, joint_id)}

    def sweep(
        start_deg: float = 0.0,
        end_deg: float = 360.0,
        steps: int = 24,
    ) -> dict[str, Any]:
        return sweep_mechanism(project_id, start_deg, end_deg, steps, include_frames=False)

    def mesh_check() -> dict[str, Any]:
        return check_mechanism(project_id)

    def mechanism_check(
        start_deg: float = 0.0,
        end_deg: float = 360.0,
        steps: int = 36,
    ) -> dict[str, Any]:
        return check_mechanism(project_id, start_deg, end_deg, steps)

    def lookup_f(query: str, domain: str = "") -> dict[str, Any]:
        return lookup_formula(query, domain=domain or None)

    def solve_f(
        formula_id: str,
        values: dict[str, Any] | None = None,
        solve_for: str | None = None,
        use_part: bool = True,
    ) -> dict[str, Any]:
        if use_part:
            return solve_on_part(project_id, formula_id, values=values, solve_for=solve_for)
        return solve_formula(formula_id, values or {}, solve_for=solve_for)

    def solvers(
        solvers: list[str] | None = None,
        values: dict[str, Any] | None = None,
        pack: str | None = None,
        part_id: str | None = None,
    ) -> dict[str, Any]:
        return run_solvers(project_id, solvers=solvers, values=values, pack=pack, part_id=part_id)

    def generate_designs(
        part_id: str | None = None,
        volfrac: float | None = None,
        design_space: str = "part",
        mill_25d: bool | None = None,
        additive: bool | None = None,
        assumed_load: bool = True,
    ) -> dict[str, Any]:
        return run_generate(
            project_id,
            part_id=part_id,
            volfrac=volfrac,
            design_space=design_space or "part",
            mill_25d=mill_25d,
            additive=additive,
            assumed_load=assumed_load,
        )

    def upsert(name: str, source: str = "", part_id: str | None = None, kind: str = "part", material_id: str | None = None) -> dict[str, Any]:
        try:
            part = upsert_part(
                project_id, name=name, source=source, part_id=part_id, kind=kind, material_id=material_id
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "part": part}

    def place(part_id: str, name: str = "", loc: dict[str, Any] | None = None, pattern: dict[str, Any] | None = None, parent_id: str | None = None, instance_id: str | None = None) -> dict[str, Any]:
        try:
            inst = place_instance(
                project_id,
                part_id,
                name=name,
                loc=loc,
                pattern=pattern,
                parent_id=parent_id,
                instance_id=instance_id,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        snap = assembly_snapshot(project_id)
        return {"ok": True, "instance": inst, "expanded_count": snap["expanded_count"], "bom": snap["bom"]}

    def drop_instance(instance_id: str) -> dict[str, Any]:
        ok = remove_instance(project_id, instance_id)
        return {"ok": ok}

    def activate_part(part_id: str) -> dict[str, Any]:
        part = set_active_part(project_id, part_id)
        return {"ok": True, "part": part}

    return {
        "get_workshop": get_workshop,
        "get_project": get_project,
        "write_cadquery": write_cadquery,
        "set_params": set_params,
        "list_features": list_features,
        "patch_feature": patch_one_feature,
        "build_model": build_model,
        "check_feasibility": check_feasibility,
        "run_simulation": run_simulation,
        "run_matlab": matlab_run,
        "lookup_material": lookup_material,
        "search_standards": search_std,
        "read_url": fetch_url,
        "ask_survey": ask_survey,
        "list_assembly": list_assy,
        "upsert_part": upsert,
        "place_instance": place,
        "remove_instance": drop_instance,
        "set_active_part": activate_part,
        "define_joint": define_joint,
        "remove_joint": drop_joint,
        "sweep_mechanism": sweep,
        "check_mesh": mesh_check,
        "check_mechanism": mechanism_check,
        "lookup_formula": lookup_f,
        "solve_formula": solve_f,
        "run_solvers": solvers,
        "generate_designs": generate_designs,
        "search_parts": search_cots,
        "commit_cots_kit": commit_kit,
    }
