"""Unique parts + placed instances. That is how large assemblies exist.

Forty copies of a bracket are one CadQuery solid and a linear pattern, not
forty scripts. Fasteners can be purchased BOM lines with no solid.
"""

from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from cadfree.cad.params import STARTER_BRACKET, extract_params
from cadfree.paths import project_dir
from cadfree.store.db import db

MAX_EXPANDED = 250


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def loc_dict(raw: Any) -> dict[str, float]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    out = {}
    for key in ("x", "y", "z", "rx", "ry", "rz"):
        try:
            out[key] = float(raw.get(key) or 0)
        except (TypeError, ValueError):
            out[key] = 0.0
    return out


def loc_matrix(loc: dict[str, float]) -> np.ndarray:
    """4x4: translation in mm, rx/ry/rz in degrees, applied Rx then Ry then Rz."""
    loc = loc_dict(loc)
    rx, ry, rz = (math.radians(loc[k]) for k in ("rx", "ry", "rz"))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    rx_m = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry_m = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz_m = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    r = rz_m @ ry_m @ rx_m
    m = np.eye(4)
    m[:3, :3] = r
    m[0, 3] = loc["x"]
    m[1, 3] = loc["y"]
    m[2, 3] = loc["z"]
    return m


def expand_pattern(loc: dict[str, float], pattern: Any) -> list[dict[str, float]]:
    loc = loc_dict(loc)
    if isinstance(pattern, str):
        try:
            pattern = json.loads(pattern)
        except json.JSONDecodeError:
            pattern = {}
    if not isinstance(pattern, dict):
        pattern = {}
    kind = str(pattern.get("kind") or "none").lower()
    if kind in {"", "none", "single"}:
        return [loc]
    if kind == "linear":
        n = max(1, int(pattern.get("count") or 1))
        dx, dy, dz = float(pattern.get("dx") or 0), float(pattern.get("dy") or 0), float(pattern.get("dz") or 0)
        return [
            {**loc, "x": loc["x"] + i * dx, "y": loc["y"] + i * dy, "z": loc["z"] + i * dz}
            for i in range(n)
        ]
    if kind == "grid":
        nx = max(1, int(pattern.get("nx") or pattern.get("count_x") or 1))
        ny = max(1, int(pattern.get("ny") or pattern.get("count_y") or 1))
        nz = max(1, int(pattern.get("nz") or pattern.get("count_z") or 1))
        dx, dy, dz = float(pattern.get("dx") or 0), float(pattern.get("dy") or 0), float(pattern.get("dz") or 0)
        out = []
        for k in range(nz):
            for j in range(ny):
                for i in range(nx):
                    out.append(
                        {
                            **loc,
                            "x": loc["x"] + i * dx,
                            "y": loc["y"] + j * dy,
                            "z": loc["z"] + k * dz,
                        }
                    )
        return out
    if kind == "circular":
        n = max(1, int(pattern.get("count") or 1))
        radius = float(pattern.get("radius") or 0)
        axis = str(pattern.get("axis") or "z").lower()
        start = math.radians(float(pattern.get("start_deg") or 0))
        out = []
        for i in range(n):
            ang = start + (2 * math.pi * i / n)
            item = dict(loc)
            if axis == "x":
                item["y"] = loc["y"] + radius * math.cos(ang)
                item["z"] = loc["z"] + radius * math.sin(ang)
                item["rx"] = loc["rx"] + math.degrees(ang)
            elif axis == "y":
                item["x"] = loc["x"] + radius * math.cos(ang)
                item["z"] = loc["z"] + radius * math.sin(ang)
                item["ry"] = loc["ry"] + math.degrees(ang)
            else:
                item["x"] = loc["x"] + radius * math.cos(ang)
                item["y"] = loc["y"] + radius * math.sin(ang)
                item["rz"] = loc["rz"] + math.degrees(ang)
            out.append(item)
        return out
    return [loc]


def expand_instances(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {i["id"]: i for i in instances if i.get("id")}
    expanded: list[dict[str, Any]] = []
    for inst in instances:
        origins = expand_pattern(inst.get("loc"), inst.get("pattern"))
        parent = by_id.get(inst.get("parent_id") or "")
        parent_m = loc_matrix(parent.get("loc") if parent else {})
        for idx, loc in enumerate(origins):
            world = parent_m @ loc_matrix(loc)
            expanded.append(
                {
                    **inst,
                    "index": idx,
                    "loc": {
                        "x": float(world[0, 3]),
                        "y": float(world[1, 3]),
                        "z": float(world[2, 3]),
                        "rx": loc.get("rx", 0),
                        "ry": loc.get("ry", 0),
                        "rz": loc.get("rz", 0),
                    },
                    "matrix": world.tolist(),
                }
            )
    if len(expanded) > MAX_EXPANDED:
        raise ValueError(
            f"Assembly expands to {len(expanded)} instances (cap {MAX_EXPANDED}). "
            "Use fewer unique parts and patterns, not one body per copy."
        )
    return expanded


def bom_from_instances(parts: list[dict[str, Any]], instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = {p["id"]: p for p in parts}
    counts: dict[str, int] = {}
    for inst in expand_instances(instances):
        pid = inst.get("part_id") or ""
        counts[pid] = counts.get(pid, 0) + 1
    rows = []
    for pid, qty in counts.items():
        part = names.get(pid) or {"id": pid, "name": pid, "kind": "part"}
        rows.append(
            {
                "part_id": pid,
                "name": part.get("name") or pid,
                "kind": part.get("kind") or "part",
                "qty": qty,
                "material_id": part.get("material_id"),
            }
        )
    rows.sort(key=lambda r: r["name"])
    return rows


def part_dir(project_id: str, part_id: str) -> Path:
    path = project_dir(project_id) / "parts" / part_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _part_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["metrics"] = json.loads(d.get("metrics") or "{}")
    d["params"] = extract_params(d.get("cadquery_source") or "")
    return d


def _inst_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["loc"] = loc_dict(d.get("loc"))
    try:
        d["pattern"] = json.loads(d.get("pattern") or "{}")
    except json.JSONDecodeError:
        d["pattern"] = {}
    return d


def list_parts(project_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM parts WHERE project_id = ? ORDER BY name", (project_id,)
        ).fetchall()
    return [_part_row(r) for r in rows]


def list_instances(project_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM instances WHERE project_id = ? ORDER BY name", (project_id,)
        ).fetchall()
    return [_inst_row(r) for r in rows]


def ensure_default_part(project_id: str, source: str = "", name: str = "main") -> dict[str, Any]:
    parts = list_parts(project_id)
    if parts:
        return parts[0]
    pid = _new_id()
    src = source.strip() or STARTER_BRACKET
    with db() as conn:
        conn.execute(
            """INSERT INTO parts(id, project_id, name, kind, cadquery_source, metrics, qty, notes, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (pid, project_id, name or "main", "part", src, "{}", 1, ""),
        )
        conn.execute(
            """INSERT INTO instances(id, project_id, part_id, name, parent_id, loc, pattern)
               VALUES(?,?,?,?,?,?,?)""",
            (
                _new_id(),
                project_id,
                pid,
                name or "main",
                None,
                json.dumps(loc_dict({})),
                json.dumps({"kind": "none"}),
            ),
        )
        conn.execute(
            "UPDATE projects SET cadquery_source = ?, updated_at = datetime('now') WHERE id = ?",
            (src, project_id),
        )
    return list_parts(project_id)[0]


def get_part(project_id: str, part_id: str | None) -> dict[str, Any]:
    ensure_default_part(project_id)
    parts = list_parts(project_id)
    if part_id:
        for part in parts:
            if part["id"] == part_id:
                return part
        raise KeyError(f"unknown part {part_id}")
    with db() as conn:
        row = conn.execute("SELECT constraints FROM projects WHERE id = ?", (project_id,)).fetchone()
    constraints = json.loads((row["constraints"] if row else None) or "{}")
    active = constraints.get("active_part_id")
    for part in parts:
        if part["id"] == active:
            return part
    return parts[0]


def set_active_part(project_id: str, part_id: str) -> dict[str, Any]:
    part = get_part(project_id, part_id)
    with db() as conn:
        row = conn.execute("SELECT constraints FROM projects WHERE id = ?", (project_id,)).fetchone()
        constraints = json.loads(row["constraints"] or "{}") if row else {}
        constraints["active_part_id"] = part["id"]
        conn.execute(
            "UPDATE projects SET constraints = ?, cadquery_source = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(constraints), part.get("cadquery_source") or "", project_id),
        )
    return part


def upsert_part(
    project_id: str,
    *,
    name: str,
    source: str = "",
    part_id: str | None = None,
    kind: str = "part",
    material_id: str | None = None,
) -> dict[str, Any]:
    ensure_default_part(project_id)
    kind = kind if kind in {"part", "purchased", "fastener"} else "part"
    if part_id:
        with db() as conn:
            row = conn.execute(
                "SELECT id FROM parts WHERE id = ? AND project_id = ?", (part_id, project_id)
            ).fetchone()
        if not row:
            part_id = None
    if part_id:
        with db() as conn:
            conn.execute(
                """UPDATE parts SET name = ?, kind = ?, cadquery_source = COALESCE(NULLIF(?, ''), cadquery_source),
                   material_id = ?, updated_at = datetime('now') WHERE id = ?""",
                (name, kind, source, material_id, part_id),
            )
        return get_part(project_id, part_id)
    pid = _new_id()
    src = source if kind == "part" else (source or "")
    with db() as conn:
        conn.execute(
            """INSERT INTO parts(id, project_id, name, kind, cadquery_source, metrics, qty, material_id, notes, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))""",
            (pid, project_id, name, kind, src, "{}", 1, material_id, ""),
        )
    set_active_part(project_id, pid)
    return get_part(project_id, pid)


def save_part_source(project_id: str, part_id: str | None, source: str) -> dict[str, Any]:
    part = get_part(project_id, part_id)
    with db() as conn:
        conn.execute(
            "UPDATE parts SET cadquery_source = ?, updated_at = datetime('now') WHERE id = ?",
            (source, part["id"]),
        )
        conn.execute(
            "UPDATE projects SET cadquery_source = ?, updated_at = datetime('now') WHERE id = ?",
            (source, project_id),
        )
    return get_part(project_id, part["id"])


def save_part_metrics(project_id: str, part_id: str, metrics: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE parts SET metrics = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(metrics), part_id),
        )


def place_instance(
    project_id: str,
    part_id: str,
    name: str = "",
    loc: dict[str, Any] | None = None,
    pattern: dict[str, Any] | None = None,
    parent_id: str | None = None,
    instance_id: str | None = None,
) -> dict[str, Any]:
    get_part(project_id, part_id)
    existing = [i for i in list_instances(project_id) if i["id"] != instance_id]
    n_new = len(expand_pattern(loc, pattern or {"kind": "none"}))
    n_old = len(expand_instances(existing)) if existing else 0
    if n_new + n_old > MAX_EXPANDED:
        raise ValueError(
            f"That pattern would make {n_new + n_old} instances (cap {MAX_EXPANDED})."
        )
    loc_s = json.dumps(loc_dict(loc))
    pat = pattern or {"kind": "none"}
    if instance_id:
        with db() as conn:
            conn.execute(
                """UPDATE instances SET part_id = ?, name = ?, parent_id = ?, loc = ?, pattern = ?
                   WHERE id = ? AND project_id = ?""",
                (part_id, name or part_id, parent_id, loc_s, json.dumps(pat), instance_id, project_id),
            )
        iid = instance_id
    else:
        iid = _new_id()
        with db() as conn:
            conn.execute(
                """INSERT INTO instances(id, project_id, part_id, name, parent_id, loc, pattern)
                   VALUES(?,?,?,?,?,?,?)""",
                (iid, project_id, part_id, name or part_id, parent_id, loc_s, json.dumps(pat)),
            )
    return next(i for i in list_instances(project_id) if i["id"] == iid)


def remove_instance(project_id: str, instance_id: str) -> bool:
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM instances WHERE id = ? AND project_id = ?", (instance_id, project_id)
        )
        return cur.rowcount > 0


def assembly_snapshot(project_id: str) -> dict[str, Any]:
    ensure_default_part(project_id)
    parts = list_parts(project_id)
    instances = list_instances(project_id)
    try:
        expanded = expand_instances(instances)
        error = ""
    except ValueError as exc:
        expanded = []
        error = str(exc)
    return {
        "parts": parts,
        "instances": instances,
        "expanded_count": len(expanded),
        "bom": bom_from_instances(parts, instances) if not error else [],
        "error": error,
        "active_part_id": get_part(project_id, None)["id"],
    }


def compose_assembly_stl(project_id: str) -> dict[str, Any]:
    """Merge placed part STLs with trimesh. No CadQuery required at compose time."""
    snap = assembly_snapshot(project_id)
    if snap.get("error"):
        return {"ok": False, "error": snap["error"]}
    instances = list_instances(project_id)
    try:
        expanded = expand_instances(instances)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    parts = {p["id"]: p for p in list_parts(project_id)}
    try:
        import trimesh
    except ImportError:
        return {"ok": False, "error": "trimesh is required to compose an assembly preview"}

    meshes = []
    missing = []
    for inst in expanded:
        part = parts.get(inst.get("part_id") or "")
        if not part or part.get("kind") in {"purchased", "fastener"}:
            continue
        stl = part_dir(project_id, part["id"]) / "model.stl"
        if not stl.is_file():
            missing.append(part.get("name") or part["id"])
            continue
        mesh = trimesh.load(str(stl), force="mesh")
        mesh.apply_transform(np.array(inst["matrix"], dtype=float))
        meshes.append(mesh)
    if missing:
        return {
            "ok": False,
            "error": "Build these parts before composing the assembly: " + ", ".join(sorted(set(missing))),
            "missing": sorted(set(missing)),
        }
    out = project_dir(project_id) / "assembly.stl"
    if not meshes:
        return {"ok": True, "stl_path": None, "bodies": 0, "note": "No solids to merge (BOM-only / purchased)."}
    combined = trimesh.util.concatenate(meshes)
    combined.export(str(out))
    extents = combined.extents.tolist() if hasattr(combined, "extents") else [0, 0, 0]
    return {
        "ok": True,
        "stl_path": str(out),
        "bodies": len(meshes),
        "expanded": len(expanded),
        "bbox_mm": [float(v) for v in extents],
        "bom": snap["bom"],
    }
