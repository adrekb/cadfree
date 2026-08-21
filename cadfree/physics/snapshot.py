"""SI part status: the copy CadQuery does not simulate, but every solver receives.

CadQuery (and the STL it exports) is millimetres. This file is metres, newtons,
kilograms, pascals. Mesh files are copied next to the JSON so FEA/CFD tools
get geometry, not a guessed bounding box.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

from cadfree.cad.assembly import get_part, list_parts, part_dir
from cadfree.cad.params import extract_params
from cadfree.catalog import MATERIALS
from cadfree.kinematics.mechanism import list_joints
from cadfree.manufacturing.mass import resolve_material
from cadfree.manufacturing.mesh import load_mesh, metrics_from_mesh
from cadfree.manufacturing.strength import parse_load_n
from cadfree.manufacturing.types import MeshMetrics
from cadfree.paths import project_dir
from cadfree.physics.book import FLUIDS, FRICTION_PAIRS, G
from cadfree.store.db import db

MM = 0.001


def sim_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "sim"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _metrics_from_part(project_id: str, part: dict[str, Any]) -> MeshMetrics | None:
    stl = part_dir(project_id, part["id"]) / "model.stl"
    if stl.is_file():
        return metrics_from_mesh(load_mesh(stl))
    raw = part.get("metrics") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not raw or not raw.get("bbox_mm"):
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


def _copy_meshes(project_id: str, part: dict[str, Any], dest: Path) -> dict[str, str]:
    dest.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    src_stl = part_dir(project_id, part["id"]) / "model.stl"
    if src_stl.is_file():
        mm_stl = dest / "part_mm.stl"
        shutil.copy2(src_stl, mm_stl)
        files["stl_mm"] = str(mm_stl)
        try:
            mesh = load_mesh(src_stl)
            mesh.apply_scale(MM)
            si_stl = dest / "part_si.stl"
            mesh.export(si_stl)
            files["stl_m"] = str(si_stl)
        except Exception:
            files["stl_m_error"] = "could not scale STL to metres"
    for name in ("model.step", "model.stp"):
        src = part_dir(project_id, part["id"]) / name
        if src.is_file():
            step = dest / "part.step"
            shutil.copy2(src, step)
            files["step"] = str(step)
            break
    return files


def _params_si(params: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, raw in (params or {}).items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if key.endswith("_mm"):
            metres = val * MM
            stem = key[: -len("_mm")]
            out[stem] = metres
            out[key] = metres
            out[stem + "_m"] = metres
        elif key.endswith("_m"):
            out[key] = val
            out[key[: -len("_m")]] = val
        else:
            out[key] = val
    return out


def _material_si(material_id: str | None, process_kind: str) -> dict[str, Any]:
    mid = material_id if material_id in MATERIALS else "petg"
    mat = resolve_material(mid)
    e_gpa = float(mat.get("flex_modulus_gpa") or 2.0)
    nu = 0.33 if process_kind in {"metal_am", "mill"} else 0.38
    rho = float(mat.get("density_g_cm3") or 1.2) * 1000.0
    allow = float(mat.get("tensile_xy_mpa") or 30.0) * 1e6
    return {
        "id": mat.get("id") or mid,
        "name": mat.get("name"),
        "E": e_gpa * 1e9,
        "nu": nu,
        "rho": rho,
        "allowable": allow,
        "tensile_xy_mpa": mat.get("tensile_xy_mpa"),
        "tensile_z_mpa": mat.get("tensile_z_mpa"),
        "process_kind": process_kind,
    }


def _project_row(project_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise KeyError(f"unknown project {project_id}")
    return dict(row)


def write_si_status(project_id: str, part_id: str | None = None) -> dict[str, Any]:
    """Build the SI job card and copy meshes into project/sim/."""
    row = _project_row(project_id)
    constraints = json.loads(row.get("constraints") or "{}")
    part = get_part(project_id, part_id)
    metrics = _metrics_from_part(project_id, part)
    dest = sim_dir(project_id)
    files = _copy_meshes(project_id, part, dest)
    params = extract_params(part.get("cadquery_source") or row.get("cadquery_source") or "")
    if not params and part.get("cadquery_source"):
        params = extract_params(part["cadquery_source"])
    params_si = _params_si(params)
    process_kind = str(constraints.get("process_kind") or "fdm")
    material = _material_si(constraints.get("material_id"), process_kind)
    load = parse_load_n(constraints)
    bbox_m = [0.0, 0.0, 0.0]
    volume_m3 = 0.0
    area_m2 = 0.0
    min_t = None
    if metrics:
        bbox_m = [d * MM for d in metrics.bbox_mm]
        volume_m3 = metrics.volume_mm3 * 1e-9
        area_m2 = metrics.surface_area_mm2 * 1e-6
        if metrics.min_thickness_mm:
            min_t = metrics.min_thickness_mm * MM
    sides = sorted(bbox_m)
    L, b, t = (sides[2] if sides else 0.0), (sides[1] if len(sides) > 1 else 0.0), (sides[0] if sides else 0.0)
    if min_t:
        t = min_t
    A_xy = bbox_m[0] * bbox_m[1]
    A_yz = bbox_m[1] * bbox_m[2]
    A_zx = bbox_m[2] * bbox_m[0]
    A_proj = max(A_xy, A_yz, A_zx)
    D = params_si.get("hole_d") or params_si.get("D") or (min(bbox_m) if bbox_m else 0.0)
    infill = float(constraints.get("infill") or 0.35)
    mass = material["rho"] * volume_m3 * (infill if process_kind == "fdm" else 1.0)

    fluid_name = str(constraints.get("fluid") or constraints.get("environment") or "air").lower()
    if fluid_name not in FLUIDS:
        if "water" in fluid_name:
            fluid_name = "water"
        elif "oil" in fluid_name:
            fluid_name = "oil_iso32"
        else:
            fluid_name = "air"
    fluid = FLUIDS[fluid_name]
    v = constraints.get("v_ms") or constraints.get("velocity_ms") or constraints.get("air_speed_ms")
    try:
        v_ms = float(v) if v is not None else None
    except (TypeError, ValueError):
        v_ms = None
    rpm = constraints.get("rpm") or constraints.get("n_rpm")
    try:
        n_rpm = float(rpm) if rpm is not None else None
    except (TypeError, ValueError):
        n_rpm = None
    if v_ms is None and n_rpm and D:
        v_ms = (math.pi * D * n_rpm) / 60.0

    mu = constraints.get("mu")
    pair = constraints.get("pair") or constraints.get("friction_pair")
    if mu is None and pair in FRICTION_PAIRS:
        mu = FRICTION_PAIRS[pair]

    inputs: dict[str, float] = {
        "L": L,
        "b": b,
        "t": t,
        "A": A_proj,
        "A_xy": A_xy,
        "A_surf": area_m2,
        "V": volume_m3,
        "D": D,
        "r": D / 2.0 if D else 0.0,
        "E": float(material["E"]),
        "nu": float(material["nu"]),
        "rho": float(fluid["rho"]),
        "rho_solid": float(material["rho"]),
        "mu_visc": float(fluid["mu_visc"]),
        "g": G,
        "m": mass,
        "allowable": float(material["allowable"]),
    }
    inputs.update({k: v for k, v in params_si.items() if isinstance(v, float)})
    if load is not None:
        inputs["F_N"] = float(load)
        inputs["P"] = float(load)
    if v_ms is not None:
        inputs["v"] = float(v_ms)
        if D:
            inputs["s"] = float(v_ms) * float(constraints.get("hours") or 100.0) * 3600.0
    if n_rpm is not None:
        inputs["n_rpm"] = float(n_rpm)
    if mu is not None:
        inputs["mu"] = float(mu)
    if "Cd" not in inputs and constraints.get("Cd") is not None:
        inputs["Cd"] = float(constraints["Cd"])
    k_mm = constraints.get("k_n_per_mm")
    try:
        if k_mm is not None:
            inputs["k"] = float(k_mm) * 1000.0
    except (TypeError, ValueError):
        pass
    stroke = constraints.get("stroke_mm")
    try:
        if stroke is not None:
            inputs["x"] = float(stroke) / 1000.0
    except (TypeError, ValueError):
        pass
    if constraints.get("spring_force_n") is not None:
        try:
            inputs["F"] = float(constraints["spring_force_n"])
        except (TypeError, ValueError):
            pass
    elif load is not None:
        inputs.setdefault("F", float(load))
    pin_d = constraints.get("pin_d_mm") or params.get("pin_d_mm")
    wire_d = constraints.get("wire_d_mm") or params.get("wire_d_mm")
    mean_d = constraints.get("mean_d_mm") or params.get("mean_d_mm")
    try:
        if wire_d is not None:
            inputs["d"] = float(wire_d) * MM
        elif pin_d is not None:
            inputs["d"] = float(pin_d) * MM
    except (TypeError, ValueError):
        pass
    try:
        if mean_d is not None:
            inputs["D"] = float(mean_d) * MM
        if constraints.get("n_active") is not None:
            inputs["n"] = float(constraints["n_active"])
    except (TypeError, ValueError):
        pass
    provenance = {
        "L": "max bbox edge from mesh (m)",
        "b": "middle bbox edge from mesh (m)",
        "t": "min thickness or min bbox edge from mesh (m)",
        "A": "largest projected bbox face from mesh (m^2)",
        "D": "PARAMS hole_d_mm or min bbox (m)",
        "E": f"catalog {material['id']} flex_modulus → Pa",
        "F_N": "constraints load_* converted to N" if load is not None else "missing — survey load",
        "v": "constraints v_ms / rpm" if v_ms is not None else "missing — survey speed if fluids/friction",
        "mu": "constraints mu or book pair" if mu is not None else "missing — do not invent μ",
        "Cd": "constraints Cd, else book blunt-body default 1.0",
        "rho": f"book fluid {fluid_name}",
    }
    missing = []
    if load is None:
        missing.append("F_N (load_n / load_lbf)")
    status = {
        "units": "SI",
        "length": "m",
        "force": "N",
        "mass": "kg",
        "pressure": "Pa",
        "cadquery": {
            "role": "geometry author only",
            "source_length_unit": "mm",
            "params_mm": params,
        },
        "part": {
            "id": part["id"],
            "name": part.get("name"),
            "kind": part.get("kind"),
            "bbox_m": bbox_m,
            "volume_m3": volume_m3,
            "area_m2": area_m2,
            "min_thickness_m": min_t,
            "projected_area_m2": {"xy": A_xy, "yz": A_yz, "zx": A_zx},
            "files": files,
            "built": bool(files.get("stl_mm")),
        },
        "material": material,
        "load": {
            "F_N": load,
            "direction": constraints.get("load_direction") or "tip / longest span",
            "safety_factor": float(constraints.get("safety_factor") or 2.0),
        },
        "environment": {
            "fluid": fluid_name,
            "rho": fluid["rho"],
            "mu_visc": fluid["mu_visc"],
            "label": fluid["label"],
            "v_ms": v_ms,
            "n_rpm": n_rpm,
            "pair": pair,
        },
        "inputs": inputs,
        "provenance": provenance,
        "missing": missing,
        "joints": list_joints(project_id),
        "parts": [{"id": p["id"], "name": p.get("name"), "kind": p.get("kind")} for p in list_parts(project_id)],
        "spec_text": row.get("spec_text") or "",
        "assumptions": [
            "CadQuery/STL length unit is millimetres; this card and solver copies are SI.",
            "Projected area is bbox faces, not a wind-tunnel silhouette.",
            "Cd defaults to 1.0 (blunt body) in the formula book when unset.",
            "Not a certified coupon. Solvers must not be faked if missing.",
        ],
        "paths": {"sim": str(dest), "status": str(dest / "status.json")},
    }
    (dest / "status.json").write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    return status


def bind_formula(status: dict[str, Any], formula: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    inputs = dict(status.get("inputs") or {})
    prov_all = dict(status.get("provenance") or {})
    used: dict[str, Any] = {}
    prov: dict[str, str] = {}
    for name in formula.get("variables") or {}:
        if name in inputs and inputs[name] not in (None,):
            used[name] = float(inputs[name])
            if name in prov_all:
                prov[name] = prov_all[name]
    env = status.get("environment") or {}
    if env.get("pair"):
        used["pair"] = env["pair"]
    if env.get("fluid"):
        used["fluid"] = env["fluid"]
    return used, prov
