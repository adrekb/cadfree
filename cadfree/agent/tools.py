from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cadfree.cad.params import apply_params, extract_params
from cadfree.cad.runner import build_cadquery, cadquery_status
from cadfree.catalog import MATERIALS, catalog_payload
from cadfree.manufacturing.evaluate import evaluate
from cadfree.manufacturing.mesh import MeshMetrics, load_mesh, metrics_from_mesh
from cadfree.matlab.engine import find_engine, run_matlab
from cadfree.paths import project_dir
from cadfree.simulation.pipeline import probe as sim_probe, simulate
from cadfree.store.db import db


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


def _save_source(project_id: str, source: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE projects SET cadquery_source = ?, updated_at = datetime('now') WHERE id = ?",
            (source, project_id),
        )


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


def make_handlers(project_id: str) -> dict[str, Any]:
    def get_workshop() -> dict[str, Any]:
        with db() as conn:
            caps = [_cap_dict(r) for r in conn.execute("SELECT * FROM capabilities").fetchall()]
        return {"capabilities": caps, "catalog": catalog_payload(), "cadquery": cadquery_status(), "matlab": find_engine(), "simulation": sim_probe()}

    def get_project() -> dict[str, Any]:
        p = _row(project_id)
        return {
            "id": p["id"],
            "name": p["name"],
            "spec_text": p["spec_text"],
            "constraints": json.loads(p["constraints"] or "{}"),
            "capability_ids": json.loads(p["capability_ids"] or "[]"),
            "cadquery_source": p["cadquery_source"],
            "params": extract_params(p["cadquery_source"] or ""),
            "metrics": json.loads(p["metrics"] or "{}"),
            "feasibility": json.loads(p["feasibility"] or "{}"),
            "status": p["status"],
        }

    def write_cadquery(source: str) -> dict[str, Any]:
        _save_source(project_id, source)
        return {"ok": True, "params": extract_params(source), "chars": len(source)}

    def set_params(params: dict[str, Any]) -> dict[str, Any]:
        p = _row(project_id)
        source = apply_params(p["cadquery_source"] or "", params)
        _save_source(project_id, source)
        return {"ok": True, "params": extract_params(source)}

    def build_model() -> dict[str, Any]:
        p = _row(project_id)
        source = p["cadquery_source"]
        if not source.strip():
            return {"ok": False, "error": "No CadQuery source yet. Call write_cadquery first."}
        built = build_cadquery(source, project_dir(project_id))
        if built.get("ok"):
            _save_build(project_id, built)
        return {k: v for k, v in built.items() if k != "stl_path"}

    def check_feasibility() -> dict[str, Any]:
        p = _row(project_id)
        work = project_dir(project_id)
        metrics = _metrics(p, work)
        if metrics is None:
            return {"ok": False, "error": "Build the model before checking feasibility."}
        caps = _caps(json.loads(p["capability_ids"] or "[]"))
        constraints = json.loads(p["constraints"] or "{}")
        report = evaluate(metrics, caps, constraints)
        data = report.to_dict()
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

    return {
        "get_workshop": get_workshop,
        "get_project": get_project,
        "write_cadquery": write_cadquery,
        "set_params": set_params,
        "build_model": build_model,
        "check_feasibility": check_feasibility,
        "run_simulation": run_simulation,
        "run_matlab": matlab_run,
        "lookup_material": lookup_material,
    }
