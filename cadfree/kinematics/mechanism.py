"""Joints on assembly instances. CadQuery will not move a linkage; this will.

Shop-scale: planar four-bar, open revolute/prismatic chains, gear ratio, convex
interference along a drive sweep. Not SolidWorks Motion, not contact dynamics.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any

import numpy as np

from cadfree.cad.assembly import (
    assembly_scene,
    expand_instances,
    list_instances,
    loc_matrix,
    matrix_colmajor,
    part_dir,
)
from cadfree.kinematics.collision import gear_center_check, posed_hit
from cadfree.kinematics.fourbar import link_stats, solve_fourbar
from cadfree.kinematics.geometry import (
    from_2d,
    normalize,
    plane_basis,
    rotate_around,
    to_2d,
    vec3,
)
from cadfree.kinematics.slidercrank import solve_slider_crank
from cadfree.store.db import db

JOINT_KINDS = ("revolute", "prismatic", "fixed", "gear", "spring", "torsion")


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _parse_json(raw: Any, default: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default
    return default or {}


def _node(joint: dict[str, Any], which: str) -> str:
    val = (joint.get(which) or "").strip()
    return val or "ground"


def list_joints(project_id: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM joints WHERE project_id = ? ORDER BY name", (project_id,)
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["origin"] = _parse_json(item.get("origin"), {})
        item["axis"] = _parse_json(item.get("axis"), {"x": 0, "y": 0, "z": 1})
        item["limits"] = _parse_json(item.get("limits"), {})
        item["params"] = _parse_json(item.get("params"), {})
        item["driven"] = bool(item.get("driven"))
        out.append(item)
    return out


def upsert_joint(
    project_id: str,
    *,
    name: str,
    kind: str,
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
    kind = (kind or "revolute").lower()
    if kind not in JOINT_KINDS:
        raise ValueError(f"joint kind must be {JOINT_KINDS}")
    if not instance_b:
        raise ValueError("instance_b (the moving child) is required")
    ax = vec3(axis)
    origin = origin or {}
    if any(k in origin for k in ("x", "y", "z")):
        origin_json = json.dumps(
            {
                "x": float(origin.get("x") or 0),
                "y": float(origin.get("y") or 0),
                "z": float(origin.get("z") or 0),
            }
        )
    else:
        origin_json = "{}"
    payload = (
        joint_id or _new_id(),
        project_id,
        name or kind,
        kind,
        instance_a or "",
        instance_b,
        origin_json,
        json.dumps({"x": float(ax[0]), "y": float(ax[1]), "z": float(ax[2])}),
        json.dumps(limits or {}),
        float(ratio if ratio is not None else (params or {}).get("ratio") or -1.0),
        1 if driven else 0,
        json.dumps(params or {}),
    )
    with db() as conn:
        if joint_id:
            exists = conn.execute(
                "SELECT id FROM joints WHERE id = ? AND project_id = ?", (joint_id, project_id)
            ).fetchone()
        else:
            exists = None
        if exists:
            conn.execute(
                """UPDATE joints SET name=?, kind=?, instance_a=?, instance_b=?, origin=?, axis=?,
                   limits=?, ratio=?, driven=?, params=? WHERE id=?""",
                payload[2:] + (joint_id,),
            )
            jid = joint_id
        else:
            conn.execute(
                """INSERT INTO joints(id, project_id, name, kind, instance_a, instance_b, origin, axis,
                   limits, ratio, driven, params) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                payload,
            )
            jid = payload[0]
    return next(j for j in list_joints(project_id) if j["id"] == jid)


def remove_joint(project_id: str, joint_id: str) -> bool:
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM joints WHERE id = ? AND project_id = ?", (joint_id, project_id)
        )
        return cur.rowcount > 0


def _rest_state(project_id: str) -> dict[str, Any]:
    instances = list_instances(project_id)
    expanded = expand_instances(instances)
    by_id: dict[str, dict[str, Any]] = {}
    for inst in expanded:
        if inst.get("index", 0) != 0:
            continue
        by_id[inst["id"]] = inst
    for inst in instances:
        by_id.setdefault(inst["id"], {**inst, "matrix": loc_matrix(inst.get("loc")).tolist()})
    return {"instances": instances, "by_id": by_id}


def _instance_origin(rest: dict[str, Any], instance_id: str | None) -> np.ndarray:
    inst = rest["by_id"].get(instance_id or "")
    if inst:
        m = np.array(inst["matrix"], dtype=float)
        return m[:3, 3]
    return np.zeros(3)


def _pivot_world(joint: dict[str, Any], rest: dict[str, Any]) -> np.ndarray:
    origin = joint.get("origin") or {}
    if isinstance(origin, dict) and any(k in origin for k in ("x", "y", "z")):
        return vec3(origin, default=(0.0, 0.0, 0.0))
    child = rest["by_id"].get(joint.get("instance_b") or "")
    if child:
        m = np.array(child["matrix"], dtype=float)
        return m[:3, 3]
    parent = rest["by_id"].get(joint.get("instance_a") or "")
    if parent:
        m = np.array(parent["matrix"], dtype=float)
        return m[:3, 3]
    return np.zeros(3)


def _detect_fourbar(joints: list[dict[str, Any]]) -> dict[str, Any] | None:
    revs = [j for j in joints if j.get("kind") == "revolute"]
    if len(revs) < 4:
        return None
    driven = next((j for j in revs if j.get("driven")), revs[0])
    crank = _node(driven, "instance_b")
    if crank == "ground":
        return None

    def edge(a: str, b: str) -> dict[str, Any] | None:
        for j in revs:
            n0, n1 = _node(j, "instance_a"), _node(j, "instance_b")
            if {n0, n1} == {a, b}:
                return j
        return None

    for j_bc in revs:
        if j_bc.get("id") == driven.get("id"):
            continue
        nodes = {_node(j_bc, "instance_a"), _node(j_bc, "instance_b")}
        if crank not in nodes:
            continue
        coupler = next(n for n in nodes if n != crank)
        if coupler == "ground":
            continue
        for j_cd in revs:
            if j_cd.get("id") in {driven.get("id"), j_bc.get("id")}:
                continue
            nodes2 = {_node(j_cd, "instance_a"), _node(j_cd, "instance_b")}
            if coupler not in nodes2:
                continue
            rocker = next(n for n in nodes2 if n != coupler)
            if rocker in {crank, coupler, "ground"}:
                continue
            j_dg = edge(rocker, "ground")
            if j_dg:
                return {
                    "crank": crank,
                    "coupler": coupler,
                    "rocker": rocker,
                    "j_ab": driven,
                    "j_bc": j_bc,
                    "j_cd": j_cd,
                    "j_da": j_dg,
                }
    return None


def _pose_fourbar(
    rest: dict[str, Any],
    loop: dict[str, Any],
    drive_rad: float,
    prev_c: np.ndarray | None,
) -> dict[str, Any]:
    j_ab, j_da = loop["j_ab"], loop["j_da"]
    axis = vec3(j_ab.get("axis"))
    e1, e2, k = plane_basis(axis)
    a3 = _pivot_world(j_ab, rest)
    d3 = _pivot_world(j_da, rest)
    b3 = _pivot_world(loop["j_bc"], rest)
    c3 = _pivot_world(loop["j_cd"], rest)
    origin = a3 - float(np.dot(a3, k)) * k
    a = to_2d(a3, origin, e1, e2)
    d = to_2d(d3, origin, e1, e2)
    b0 = to_2d(b3, origin, e1, e2)
    c0 = to_2d(c3, origin, e1, e2)
    l1 = float(np.linalg.norm(b0 - a))
    l2 = float(np.linalg.norm(c0 - b0))
    l3 = float(np.linalg.norm(c0 - d))
    l4 = float(np.linalg.norm(d - a))
    if min(l1, l2, l3, l4) < 1e-6:
        return {"ok": False, "error": "four-bar pivots are coincident — set joint origin mm"}
    stats = link_stats([l1, l2, l3, l4])
    theta0 = math.atan2(b0[1] - a[1], b0[0] - a[0])
    solved = solve_fourbar(a, d, l1, l2, l3, theta0 + drive_rad, prev_c)
    if not solved.get("ok"):
        return {**solved, "stats": stats, "loop": loop}
    b1 = np.array(solved["B"])
    c1 = np.array(solved["C"])
    ha = float(np.dot(a3, k))
    hb = float(np.dot(b3, k))
    hc = float(np.dot(c3, k))
    hd = float(np.dot(d3, k))
    b3n = from_2d(b1, origin, e1, e2, hb, k)
    c3n = from_2d(c1, origin, e1, e2, hc, k)
    d_keep = from_2d(d, origin, e1, e2, hd, k)
    a_keep = from_2d(a, origin, e1, e2, ha, k)

    crank_m = np.array(rest["by_id"][loop["crank"]]["matrix"], dtype=float)
    rocker_m = np.array(rest["by_id"][loop["rocker"]]["matrix"], dtype=float)
    coupler_m = np.array(rest["by_id"][loop["coupler"]]["matrix"], dtype=float)
    d_crank = math.atan2(b1[1] - a[1], b1[0] - a[0]) - theta0
    theta_rock0 = math.atan2(c0[1] - d[1], c0[0] - d[0])
    theta_rock1 = math.atan2(c1[1] - d[1], c1[0] - d[0])
    crank_m = rotate_around(crank_m, a_keep, k, d_crank)
    rocker_m = rotate_around(rocker_m, d_keep, k, theta_rock1 - theta_rock0)

    # Coupler: map rest B,C onto B',C' in the plane
    v0 = c0 - b0
    v1 = c1 - b1
    ang0 = math.atan2(v0[1], v0[0])
    ang1 = math.atan2(v1[1], v1[0])
    coupler_m = rotate_around(coupler_m, b3, k, ang1 - ang0)
    coupler_m = coupler_m.copy()
    delta = b3n - b3
    coupler_m[:3, 3] = coupler_m[:3, 3] + delta

    poses = {
        loop["crank"]: crank_m,
        loop["rocker"]: rocker_m,
        loop["coupler"]: coupler_m,
    }
    return {
        "ok": True,
        "locked": False,
        "stats": stats,
        "loop": loop,
        "poses": poses,
        "C2": c1,
        "transmission_deg": solved.get("transmission_deg"),
        "pivots": {"A": a_keep.tolist(), "B": b3n.tolist(), "C": c3n.tolist(), "D": d_keep.tolist()},
    }


def _detect_slider_crank(joints: list[dict[str, Any]]) -> dict[str, Any] | None:
    """ground–crank–rod–slider with one prismatic on the slider."""
    pris = [j for j in joints if j.get("kind") == "prismatic"]
    revs = [j for j in joints if j.get("kind") == "revolute"]
    if len(pris) != 1 or len(revs) < 2:
        return None
    slider_j = pris[0]
    slider = _node(slider_j, "instance_b")
    if slider == "ground":
        return None
    driven = next((j for j in revs if j.get("driven")), None)
    if not driven:
        return None
    crank = _node(driven, "instance_b")
    if crank in {"ground", slider}:
        return None

    def between(a: str, b: str) -> dict[str, Any] | None:
        want = {a, b}
        for j in revs:
            if {_node(j, "instance_a"), _node(j, "instance_b")} == want:
                return j
        return None

    for j_bc in revs:
        nodes = {_node(j_bc, "instance_a"), _node(j_bc, "instance_b")}
        if crank not in nodes:
            continue
        rod = next(n for n in nodes if n != crank)
        if rod in {"ground", crank, slider}:
            continue
        j_cd = between(rod, slider)
        if j_cd:
            return {
                "crank": crank,
                "rod": rod,
                "slider": slider,
                "j_ab": driven,
                "j_bc": j_bc,
                "j_cd": j_cd,
                "j_s": slider_j,
            }
    return None


def _pose_slider_crank(
    rest: dict[str, Any],
    loop: dict[str, Any],
    drive_rad: float,
    prev_s: float | None,
) -> dict[str, Any]:
    j_ab, j_s = loop["j_ab"], loop["j_s"]
    axis = vec3(j_ab.get("axis"))
    e1, e2, k = plane_basis(axis)
    a3 = _pivot_world(j_ab, rest)
    b3 = _pivot_world(loop["j_bc"], rest)
    c3 = _pivot_world(loop["j_cd"], rest)
    origin = a3 - float(np.dot(a3, k)) * k
    a = to_2d(a3, origin, e1, e2)
    b0 = to_2d(b3, origin, e1, e2)
    c0 = to_2d(c3, origin, e1, e2)
    slide_ax = vec3(j_s.get("axis"), default=(1.0, 0.0, 0.0))
    u2 = np.array([float(np.dot(slide_ax, e1)), float(np.dot(slide_ax, e2))])
    if float(np.linalg.norm(u2)) < 1e-6:
        u2 = np.array([1.0, 0.0])
    l1 = float(np.linalg.norm(b0 - a))
    l2 = float(np.linalg.norm(c0 - b0))
    if min(l1, l2) < 1e-6:
        return {"ok": False, "error": "slider-crank pins are coincident — set joint origin mm"}
    theta0 = math.atan2(b0[1] - a[1], b0[0] - a[0])
    solved = solve_slider_crank(a, c0, u2, l1, l2, theta0 + drive_rad, prev_s)
    if not solved.get("ok"):
        return {**solved, "loop": loop}
    b1 = np.array(solved["B"])
    c1 = np.array(solved["C"])
    ha, hb, hc = float(np.dot(a3, k)), float(np.dot(b3, k)), float(np.dot(c3, k))
    b3n = from_2d(b1, origin, e1, e2, hb, k)
    c3n = from_2d(c1, origin, e1, e2, hc, k)
    a_keep = from_2d(a, origin, e1, e2, ha, k)

    crank_m = np.array(rest["by_id"][loop["crank"]]["matrix"], dtype=float)
    rod_m = np.array(rest["by_id"][loop["rod"]]["matrix"], dtype=float)
    slider_m = np.array(rest["by_id"][loop["slider"]]["matrix"], dtype=float)
    d_crank = math.atan2(b1[1] - a[1], b1[0] - a[0]) - theta0
    crank_m = rotate_around(crank_m, a_keep, k, d_crank)
    v0, v1 = c0 - b0, c1 - b1
    ang0 = math.atan2(v0[1], v0[0])
    ang1 = math.atan2(v1[1], v1[0])
    rod_m = rotate_around(rod_m, b3, k, ang1 - ang0)
    rod_m = rod_m.copy()
    rod_m[:3, 3] = rod_m[:3, 3] + (b3n - b3)
    slider_m = slider_m.copy()
    slider_m[:3, 3] = slider_m[:3, 3] + (c3n - c3)
    return {
        "ok": True,
        "locked": False,
        "loop": loop,
        "poses": {loop["crank"]: crank_m, loop["rod"]: rod_m, loop["slider"]: slider_m},
        "C2": c1,
        "s": solved.get("s"),
        "slide_mm": solved.get("slide_mm"),
        "pivots": {"A": a_keep.tolist(), "B": b3n.tolist(), "C": c3n.tolist()},
    }


def _open_chain_poses(rest: dict[str, Any], joints: list[dict[str, Any]], drive_deg: float) -> dict[str, np.ndarray]:
    poses = {iid: np.array(info["matrix"], dtype=float) for iid, info in rest["by_id"].items()}
    drive_rad = math.radians(drive_deg)
    driven = [j for j in joints if j.get("kind") == "revolute" and j.get("driven")]
    sliders = [j for j in joints if j.get("kind") == "prismatic" and j.get("driven")]
    for joint in driven:
        child = joint.get("instance_b") or ""
        if child not in poses:
            continue
        pivot = _pivot_world(joint, rest)
        axis = vec3(joint.get("axis"))
        poses[child] = rotate_around(poses[child], pivot, axis, drive_rad)
        # children in the assembly parent_id tree ride along as a rigid group
        for iid, info in rest["by_id"].items():
            if info.get("parent_id") == child and iid in poses:
                poses[iid] = rotate_around(poses[iid], pivot, axis, drive_rad)
    for joint in sliders:
        child = joint.get("instance_b") or ""
        if child not in poses:
            continue
        axis = normalize(vec3(joint.get("axis"), default=(1.0, 0.0, 0.0)))
        poses[child] = poses[child].copy()
        poses[child][:3, 3] = poses[child][:3, 3] + axis * drive_deg
    gears = [j for j in joints if j.get("kind") == "gear"]
    for joint in gears:
        child = joint.get("instance_b") or ""
        if child not in poses:
            continue
        ratio = float(joint.get("ratio") or -1.0)
        pivot = _pivot_world(joint, rest)
        axis = vec3(joint.get("axis"))
        q = math.radians(drive_deg) * ratio
        poses[child] = rotate_around(poses[child], pivot, axis, q)
    return poses


def pose_at(project_id: str, drive_deg: float, prev_c: np.ndarray | None = None) -> dict[str, Any]:
    joints = list_joints(project_id)
    rest = _rest_state(project_id)
    drive_rad = math.radians(float(drive_deg))
    loop = _detect_fourbar(joints)
    slider = None if loop else _detect_slider_crank(joints)
    moved: dict[str, np.ndarray] = {iid: np.array(info["matrix"], dtype=float) for iid, info in rest["by_id"].items()}
    meta: dict[str, Any] = {"kind": "rest", "ok": True, "locked": False}
    if loop:
        result = _pose_fourbar(rest, loop, drive_rad, prev_c)
        meta = {k: v for k, v in result.items() if k != "poses"}
        if result.get("ok"):
            moved.update(result["poses"])
        meta["kind"] = "fourbar"
    elif slider:
        prev_s = float(prev_c[0]) if prev_c is not None and np.size(prev_c) else None
        result = _pose_slider_crank(rest, slider, drive_rad, prev_s)
        meta = {k: v for k, v in result.items() if k != "poses"}
        if result.get("ok"):
            moved.update(result["poses"])
        meta["kind"] = "slider-crank"
        if result.get("s") is not None:
            meta["C2"] = [float(result["s"]), 0.0]
    elif [j for j in joints if j.get("kind") not in {"spring", "torsion"}]:
        moved = _open_chain_poses(rest, joints, float(drive_deg))
        meta = {"kind": "open-chain", "ok": True, "locked": False}
    elif joints:
        meta = {"kind": "spring", "ok": True, "locked": False}
    gears = [j for j in joints if j.get("kind") == "gear"]
    if gears and loop:
        for joint in gears:
            child = joint.get("instance_b") or ""
            if child in moved:
                ratio = float(joint.get("ratio") or -1.0)
                pivot = _pivot_world(joint, rest)
                moved[child] = rotate_around(moved[child], pivot, vec3(joint.get("axis")), drive_rad * ratio)
    return {
        **meta,
        "drive_deg": float(drive_deg),
        "matrices": {iid: m for iid, m in moved.items()},
        "prev_c": meta.get("C2"),
    }


def _scene_draws(
    project_id: str,
    matrices: dict[str, np.ndarray],
    clash_ids: set[str] | None = None,
    locked: bool = False,
) -> list[dict[str, Any]]:
    scene = assembly_scene(project_id)
    clash_ids = clash_ids or set()
    draws = []
    for d in scene.get("draws") or []:
        iid = d.get("instance_id") or ""
        m = matrices.get(iid)
        item = dict(d)
        if m is not None:
            item["matrix"] = matrix_colmajor(m)
        item["clash"] = iid in clash_ids
        item["locked"] = bool(locked)
        draws.append(item)
    return draws


def _collisions(project_id: str, matrices: dict[str, np.ndarray], joints: list[dict[str, Any]]) -> list[dict[str, str]]:
    rest = _rest_state(project_id)
    from cadfree.cad.assembly import list_parts

    parts = {p["id"]: p for p in list_parts(project_id)}
    skip: set[tuple[str, str]] = set()
    for j in joints:
        a, b = j.get("instance_a") or "", j.get("instance_b") or ""
        if a and b:
            skip.add(tuple(sorted((a, b))))
    ids = [iid for iid, info in rest["by_id"].items() if iid in matrices]
    hits = []
    for i, ia in enumerate(ids):
        pa = parts.get(rest["by_id"][ia].get("part_id") or "")
        if not pa or pa.get("kind") in {"purchased", "fastener", "subassembly"}:
            continue
        sa = part_dir(project_id, pa["id"]) / "model.stl"
        if not sa.is_file():
            continue
        for ib in ids[i + 1 :]:
            if tuple(sorted((ia, ib))) in skip:
                continue
            pb = parts.get(rest["by_id"][ib].get("part_id") or "")
            if not pb or pb.get("kind") in {"purchased", "fastener", "subassembly"}:
                continue
            sb = part_dir(project_id, pb["id"]) / "model.stl"
            if not sb.is_file():
                continue
            try:
                if posed_hit(sa, matrices[ia], sb, matrices[ib]):
                    hits.append(
                        {
                            "a": rest["by_id"][ia].get("name") or ia,
                            "b": rest["by_id"][ib].get("name") or ib,
                            "a_id": ia,
                            "b_id": ib,
                        }
                    )
            except Exception:
                continue
    return hits


def check_gears(project_id: str) -> list[dict[str, Any]]:
    rest = _rest_state(project_id)
    out = []
    for joint in list_joints(project_id):
        if joint.get("kind") != "gear":
            continue
        params = joint.get("params") or {}
        teeth_a = params.get("teeth_a") or params.get("z1")
        teeth_b = params.get("teeth_b") or params.get("z2")
        module_mm = params.get("module_mm") or params.get("module")
        if not (teeth_a and teeth_b and module_mm):
            out.append(
                {
                    "joint": joint.get("name"),
                    "ok": False,
                    "error": "gear joint needs params module_mm, teeth_a, teeth_b",
                }
            )
            continue
        pa = _instance_origin(rest, joint.get("instance_a"))
        pb = _instance_origin(rest, joint.get("instance_b"))
        dist = float(np.linalg.norm(pb - pa))
        report = gear_center_check(
            dist,
            float(teeth_a),
            float(teeth_b),
            float(module_mm),
            internal=bool(params.get("internal")),
        )
        report["joint"] = joint.get("name")
        report["instances"] = [joint.get("instance_a"), joint.get("instance_b")]
        out.append(report)
    return out


def sweep_mechanism(
    project_id: str,
    start_deg: float = 0.0,
    end_deg: float = 360.0,
    steps: int = 24,
    *,
    include_frames: bool = False,
) -> dict[str, Any]:
    joints = list_joints(project_id)
    if not joints:
        return {
            "ok": False,
            "error": "No joints yet. define_joint (revolute/prismatic/gear) between instances, then sweep.",
        }
    steps = max(2, min(int(steps or 24), 72))
    start, end = float(start_deg), float(end_deg)
    prev_c = None
    lockups: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    last_kind = "open-chain"
    stats = None
    trans: list[float] = []
    slides: list[float] = []
    for i in range(steps):
        t = i / (steps - 1)
        deg = start + t * (end - start)
        posed = pose_at(project_id, deg, prev_c)
        last_kind = posed.get("kind") or last_kind
        stats = posed.get("stats") or stats
        if posed.get("prev_c") is not None:
            prev_c = np.array(posed["prev_c"], dtype=float)
        locked = bool(posed.get("locked")) or not posed.get("ok", True)
        hits = []
        if posed.get("ok") and posed.get("matrices") and not locked:
            hits = _collisions(project_id, posed["matrices"], joints)
        mu = posed.get("transmission_deg")
        if mu is not None and posed.get("ok"):
            trans.append(float(mu))
        if posed.get("slide_mm") is not None and posed.get("ok"):
            slides.append(float(posed["slide_mm"]))
        if locked:
            lockups.append({"deg": round(deg, 2), "reason": posed.get("reason") or posed.get("error")})
        if hits:
            collisions.append({"deg": round(deg, 2), "hits": hits})
        clash_ids: set[str] = set()
        for h in hits:
            if h.get("a_id"):
                clash_ids.add(h["a_id"])
            if h.get("b_id"):
                clash_ids.add(h["b_id"])
        if include_frames:
            frames.append(
                {
                    "deg": round(deg, 2),
                    "locked": locked,
                    "hits": hits,
                    "transmission_deg": mu,
                    "slide_mm": posed.get("slide_mm"),
                    "draws": _scene_draws(
                        project_id, posed.get("matrices") or {}, clash_ids, locked=locked
                    ),
                }
            )
    gears = check_gears(project_id)
    clear_lo, clear_hi = start, end
    if collisions:
        first = collisions[0]["deg"]
        if first > start:
            clear_hi = first
        else:
            clear_lo, clear_hi = start, start
    trans_min = min(trans) if trans else None
    trans_max = max(trans) if trans else None
    summary_bits = [f"{last_kind} sweep {start:g}→{end:g}° in {steps} steps"]
    if stats:
        summary_bits.append("Grashof" if stats.get("grashof") else "not Grashof")
        summary_bits.append(stats.get("class") or "")
    if trans_min is not None:
        summary_bits.append(f"min transmission {trans_min:.0f}°")
    if not lockups and not collisions:
        summary_bits.append("clears the travel (convex-hull SAT, pin mates ignored)")
    if lockups:
        summary_bits.append(f"lock-up at {lockups[0]['deg']}°")
    if collisions:
        summary_bits.append(
            f"collision at {collisions[0]['deg']}° ({collisions[0]['hits'][0]['a']} vs {collisions[0]['hits'][0]['b']})"
        )
    return {
        "ok": True,
        "kind": last_kind,
        "stats": stats,
        "gears": gears,
        "lockups": lockups,
        "collisions": collisions,
        "transmission_min_deg": trans_min,
        "transmission_max_deg": trans_max,
        "slide_mm_range": [min(slides), max(slides)] if slides else None,
        "clear_deg": [clear_lo, clear_hi] if not lockups else [],
        "steps": steps,
        "frames": frames if include_frames else [],
        "summary": "; ".join(x for x in summary_bits if x),
        "disclaimer": (
            "Kinematics, not CadQuery. Planar four-bar / slider-crank / open chain / gear ratio. "
            "Collision is SAT on convex hulls of the tessellated meshes — pins that share a joint "
            "are ignored. Not contact dynamics, not SolidWorks Motion."
        ),
    }


def check_mechanism(
    project_id: str,
    start_deg: float = 0.0,
    end_deg: float = 360.0,
    steps: int = 36,
) -> dict[str, Any]:
    """One-shot 'does this linkage work?' for the agent and the studio."""
    joints = list_joints(project_id)
    if not joints:
        return {
            "ok": False,
            "verdict": "no_joints",
            "works": False,
            "error": "No joints yet. define_joint (revolute/prismatic/gear) between instances, then check.",
            "for_model": (
                "No kinematic joints. Call define_joint on instances (driven crank, then the rest "
                "of the loop) before asking if it moves. CadQuery will not simulate motion."
            ),
            "disclaimer": "Not SolidWorks Motion.",
        }
    sweep = sweep_mechanism(project_id, start_deg, end_deg, steps, include_frames=False)
    gears = sweep.get("gears") or []
    lockups = sweep.get("lockups") or []
    collisions = sweep.get("collisions") or []
    stats = sweep.get("stats") or {}
    trans_min = sweep.get("transmission_min_deg")
    gear_fail = [g for g in gears if not g.get("ok")]
    awkward = bool(
        (trans_min is not None and trans_min < 40.0)
        or (stats and stats.get("grashof") is False)
    )
    if lockups:
        verdict = "locks"
    elif collisions:
        verdict = "collides"
    elif gear_fail:
        verdict = "gears_wrong"
    elif awkward:
        verdict = "awkward"
    else:
        verdict = "works"
    bits = [sweep.get("summary") or sweep.get("kind") or "mechanism"]
    if verdict == "works":
        bits.append("the input can travel this range without lock-up or hull clash")
    elif verdict == "awkward":
        if trans_min is not None and trans_min < 40:
            bits.append(
                f"it assembles but min transmission angle is {trans_min:.0f}° (want ≥40° for comfort)"
            )
        if stats and not stats.get("grashof"):
            bits.append("not Grashof — crank cannot fully rotate")
    elif verdict == "locks":
        bits.append(f"cannot assemble at {lockups[0]['deg']}° — shorten a link or change the drive range")
    elif verdict == "collides":
        hit = collisions[0]["hits"][0]
        bits.append(f"{hit['a']} hits {hit['b']} at {collisions[0]['deg']}° — move a pivot or thin a body")
    elif verdict == "gears_wrong":
        bits.append(gear_fail[0].get("verdict") or gear_fail[0].get("error") or "gears do not mesh")
    for_model = (
        f"Verdict: {verdict}. " + " ".join(bits) + " "
        "This is planar kinematics + convex-hull SAT, not SolidWorks Motion and not contact FEA. "
        "Do not claim a Motion study sign-off."
    )
    from cadfree.kinematics.loads import analyze_mechanism_loads

    loads = analyze_mechanism_loads(project_id)
    if loads.get("skipped") is False and loads.get("possible") is False and verdict in {"works", "awkward"}:
        verdict = "overloaded"
        bits.append(loads.get("for_model") or "spring / pin load does not close")
        for_model = (
            f"Verdict: {verdict}. " + " ".join(bits) + " " + (loads.get("disclaimer") or "")
        )
    return {
        **sweep,
        "ok": verdict in {"works", "awkward"},
        "verdict": verdict,
        "works": verdict == "works",
        "awkward": verdict == "awkward",
        "overloaded": verdict == "overloaded",
        "loads": loads if not loads.get("skipped") else None,
        "for_model": for_model,
        "comfort": {
            "kind": sweep.get("kind"),
            "grashof": stats.get("grashof") if stats else None,
            "class": stats.get("class") if stats else None,
            "transmission_min_deg": trans_min,
            "transmission_max_deg": sweep.get("transmission_max_deg"),
            "lockups": lockups,
            "collisions": collisions,
            "gears": gears,
            "clear_deg": sweep.get("clear_deg"),
            "slide_mm_range": sweep.get("slide_mm_range"),
        },
        "frames": [],
    }
