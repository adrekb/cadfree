"""URDF export from unique parts + joints. Shop-scale, SI units.

CadQuery assemblies are millimetres. URDF is metres. Pattern copies are not
extra links — joints bind instance ids. Gear/spring/torsion are not URDF
joint types; they are named in comments / dummy fixed, never faked as a
constraint the robot stack would honor.
"""

from __future__ import annotations

import math
import re
import shutil
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from cadfree.cad.assembly import list_instances, list_parts, part_dir
from cadfree.kinematics.mechanism import list_joints
from cadfree.paths import project_dir

MM = 0.001
HONEST = (
    "URDF from unique instances + joints. Mesh scale 0.001 (mm→m). "
    "Inertia is a bbox brick, not a mesh-density integral. "
    "gear/spring/torsion are not URDF joints — they become fixed with a comment. "
    "Not ROS control, not Adams."
)

_URDF_JOINT = {
    "revolute": "revolute",
    "prismatic": "prismatic",
    "fixed": "fixed",
}


def urdf_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "sim" / "urdf"
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_urdf(project_id: str, name: str | None = None) -> dict[str, Any]:
    dest = urdf_dir(project_id)
    mesh_dir = dest / "meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    parts = {p["id"]: p for p in list_parts(project_id)}
    instances = list_instances(project_id)
    joints = list_joints(project_id)
    robot = _safe(name or "cadfree")
    copied: dict[str, str] = {}
    links_xml: list[str] = ['  <link name="world"/>']
    notes: list[str] = []
    inst_names: dict[str, str] = {}
    used: set[str] = {"world"}

    for inst in instances:
        link = _unique(_safe(inst.get("name") or inst["id"]), used)
        inst_names[inst["id"]] = link
        used.add(link)
        part = parts.get(inst.get("part_id") or "")
        kind = (part or {}).get("kind") or "part"
        stl = part_dir(project_id, part["id"]) / "model.stl" if part else None
        mass, inertia, mass_note = _inertia(part)
        visual = ""
        if part and stl and stl.is_file() and kind not in {"purchased", "fastener", "subassembly"}:
            rel = copied.get(part["id"])
            if rel is None:
                dest_stl = mesh_dir / f"{_safe(part.get('name') or part['id'])}.stl"
                shutil.copy2(stl, dest_stl)
                rel = f"meshes/{dest_stl.name}"
                copied[part["id"]] = rel
            visual = (
                f'    <visual>\n'
                f'      <origin xyz="0 0 0" rpy="0 0 0"/>\n'
                f'      <geometry>\n'
                f'        <mesh filename="{rel}" scale="{MM} {MM} {MM}"/>\n'
                f'      </geometry>\n'
                f'    </visual>\n'
                f'    <collision>\n'
                f'      <origin xyz="0 0 0" rpy="0 0 0"/>\n'
                f'      <geometry>\n'
                f'        <mesh filename="{rel}" scale="{MM} {MM} {MM}"/>\n'
                f'      </geometry>\n'
                f'    </collision>\n'
            )
        elif kind in {"purchased", "fastener"}:
            notes.append(f"{link}: purchased/fastener — dummy inertia, no mesh.")
        links_xml.append(
            f'  <link name="{link}">\n'
            f'    <inertial>\n'
            f'      <mass value="{mass:.6g}"/>\n'
            f'      <inertia ixx="{inertia[0]:.6g}" ixy="0" ixz="0" '
            f'iyy="{inertia[1]:.6g}" iyz="0" izz="{inertia[2]:.6g}"/>\n'
            f'    </inertial>\n'
            f'{visual}  </link>'
        )
        if mass_note:
            notes.append(f"{link}: {mass_note}")

    joints_xml: list[str] = []
    for joint in joints:
        parent = inst_names.get(joint.get("instance_a") or "") or "world"
        child = inst_names.get(joint.get("instance_b") or "")
        if not child:
            notes.append(f"skipped joint {joint.get('name')}: instance_b is not in the assembly")
            continue
        kind = (joint.get("kind") or "fixed").lower()
        urdf_kind = _URDF_JOINT.get(kind, "fixed")
        if kind not in _URDF_JOINT:
            notes.append(f"{joint.get('name')}: kind={kind} is not a URDF joint — exported as fixed.")
        origin = joint.get("origin") or {}
        axis = joint.get("axis") or {"x": 0, "y": 0, "z": 1}
        ox = float(origin.get("x") or 0) * MM
        oy = float(origin.get("y") or 0) * MM
        oz = float(origin.get("z") or 0) * MM
        ax = float(axis.get("x") or 0)
        ay = float(axis.get("y") or 0)
        az = float(axis.get("z") or 1)
        jname = _safe(joint.get("name") or joint["id"])
        limit = _limit_xml(kind, joint.get("limits") or {})
        joints_xml.append(
            f'  <joint name="{jname}" type="{urdf_kind}">\n'
            f'    <parent link="{parent}"/>\n'
            f'    <child link="{child}"/>\n'
            f'    <origin xyz="{ox:.6g} {oy:.6g} {oz:.6g}" rpy="0 0 0"/>\n'
            f'    <axis xyz="{ax:.6g} {ay:.6g} {az:.6g}"/>\n'
            f'{limit}  </joint>'
        )

    body = (
        f'<?xml version="1.0"?>\n'
        f'<!-- {escape(HONEST)} -->\n'
        f'<robot name="{robot}">\n'
        + "\n".join(links_xml)
        + "\n"
        + "\n".join(joints_xml)
        + "\n</robot>\n"
    )
    path = dest / "robot.urdf"
    path.write_text(body, encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
        "url": f"/api/projects/{project_id}/urdf",
        "links": len(links_xml),
        "joints": len(joints_xml),
        "meshes": list(copied.values()),
        "notes": notes,
        "honest": HONEST,
    }


def _inertia(part: dict[str, Any] | None) -> tuple[float, tuple[float, float, float], str]:
    metrics = (part or {}).get("metrics") or {}
    bbox = metrics.get("bbox_mm") or [10.0, 10.0, 10.0]
    try:
        lx, ly, lz = (float(bbox[0]) * MM, float(bbox[1]) * MM, float(bbox[2]) * MM)
    except (TypeError, ValueError, IndexError):
        lx = ly = lz = 0.01
    vol_mm3 = float(metrics.get("volume_mm3") or 0)
    mass = 0.1
    note = "bbox brick inertia; mass defaulted to 0.1 kg"
    if vol_mm3 > 0:
        # ~1.2 g/cm3 street plastic if density is unknown — named, not a PE stamp.
        mass = max(vol_mm3 * 1.2e-6, 1e-6)
        note = "mass from volume × 1.2 g/cm³ (named plastic); inertia is a bbox brick"
    ixx = mass / 12.0 * (ly * ly + lz * lz)
    iyy = mass / 12.0 * (lx * lx + lz * lz)
    izz = mass / 12.0 * (lx * lx + ly * ly)
    return mass, (ixx, iyy, izz), note


def _limit_xml(kind: str, limits: dict[str, Any]) -> str:
    if kind == "prismatic":
        lo = float(limits.get("min") or limits.get("lower") or 0) * MM
        hi = float(limits.get("max") or limits.get("upper") or 0.2)
        if hi > 2:
            hi = hi * MM
        return f'    <limit lower="{lo:.6g}" upper="{hi:.6g}" effort="10" velocity="0.5"/>\n'
    if kind == "revolute":
        lo = math.radians(float(limits.get("min") or limits.get("lower") or -180))
        hi = math.radians(float(limits.get("max") or limits.get("upper") or 180))
        return f'    <limit lower="{lo:.6g}" upper="{hi:.6g}" effort="10" velocity="3.14"/>\n'
    return ""


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name or "link").strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = "n_" + cleaned
    return cleaned or "link"


def _unique(name: str, used: set[str]) -> str:
    if name not in used:
        return name
    n = 2
    while f"{name}_{n}" in used:
        n += 1
    return f"{name}_{n}"
