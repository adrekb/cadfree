"""Generative design on the SI mesh copy — packaged SIMP, not Fusion 360.

Autodesk Generative Design is a cloud product (level-set / lattice / T-splines,
manufacturing-aware outcomes). Cadfree does **not** reimplement that. We run a
Sigmund-style SIMP loop on a **voxel copy** of the current part (the same SI
snapshot FEA/fluids get), apply crude mill / AM filters, and register each
outcome as an **imported** STL candidate. CadQuery is not rewritten.

Open-source lineage (do not vendor those repos):
- Sigmund 2001, 99-line MATLAB SIMP
- Liu & Tovar 2014, 3-D SIMP
- mill 2.5-D extrusion filter (research-common)
- AM overhang squeeze (layer must be supported from below)

Never invent a compliance number. If scipy is missing, one sentence + install.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from cadfree.cad.assembly import (
    MAX_UNIQUE_PARTS,
    get_part,
    list_instances,
    list_parts,
    part_dir,
    place_instance,
    save_part_metrics,
    save_part_notes,
    set_active_part,
    upsert_part,
)
from cadfree.physics import simp as _simp
from cadfree.physics.snapshot import sim_dir, write_si_status
from cadfree.store.db import db

GRID_2D = 20
GRID_3D = 8
NLOOP_2D = 25
NLOOP_3D = 10
JOB_BUDGET_S = 45.0
MILL_KINDS = {"cnc_mill", "cnc_router", "wood_cut", "waterjet", "laser_cut", "sheet_metal"}
AM_KINDS = {"fdm", "sla", "metal_am"}
LINEAGE = [
    "Sigmund 2001 99-line MATLAB SIMP",
    "Liu & Tovar 2014 3-D SIMP",
]
DISCLAIMER = (
    "Not Autodesk Generative Design, nTopology, or a level-set / T-spline kernel. "
    "Voxel SIMP on an SI mesh copy. Organic STL out; you still edit CadQuery "
    "yourself if you want a parametric solid. Compliance is the SIMP bilinear "
    "quad/hex model, not CalculiX."
)


def _not_ready(reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "ok": False,
        "reason": reason,
        "error": reason,
        "engine": "simp",
        "kind": "topology",
        "solver": "simp-voxel-fem",
        "lineage": LINEAGE,
        "disclaimer": DISCLAIMER,
        "candidates": [],
        "results": [],
    }
    if extra:
        out.update(extra)
    return out


def probe_generate() -> dict[str, Any]:
    p = _simp.probe_simp()
    ok = bool(p.get("available"))
    return {
        "available": ok,
        "ok": ok,
        "engine": "simp",
        "solver": p.get("solver"),
        "label": "SIMP topology optimization (Sigmund / Liu–Tovar), not Fusion Generative Design",
        "scipy": ok,
        "install_hint": p.get("install_hint") or "",
        "error": p.get("error"),
        "lineage": LINEAGE,
        "not": p.get("not"),
        "disclaimer": DISCLAIMER,
        "reason": None if ok else (p.get("install_hint") or "scipy is required for SIMP."),
    }


def _workshop_kinds(project_id: str) -> list[str]:
    with db() as conn:
        row = conn.execute(
            "SELECT capability_ids, constraints FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    if not row:
        return []
    ids = json.loads(row["capability_ids"] or "[]")
    constraints = json.loads(row["constraints"] or "{}")
    kinds: list[str] = []
    with db() as conn:
        for cid in ids:
            cap = conn.execute("SELECT kind FROM capabilities WHERE id = ?", (cid,)).fetchone()
            if cap and cap["kind"]:
                kinds.append(str(cap["kind"]))
    pk = constraints.get("process_kind")
    if pk:
        kinds.append(str(pk))
    return kinds


def _process_flags(
    project_id: str,
    mill_25d: bool | None,
    additive: bool | None,
) -> tuple[bool, bool]:
    kinds = _workshop_kinds(project_id)
    mill = any(k in MILL_KINDS for k in kinds)
    am = any(k in AM_KINDS for k in kinds)
    if mill_25d is not None:
        mill = bool(mill_25d)
    if additive is not None:
        am = bool(additive)
    return mill, am


def _pitch_for(extents: tuple[float, float, float], three_d: bool) -> float:
    cap = GRID_3D if three_d else GRID_2D
    longest = max(extents)
    if longest <= 0:
        return 0.01
    return longest / cap


def _voxelize(stl: Path, pitch: float) -> dict[str, Any] | None:
    try:
        import numpy as np
        import trimesh
    except ImportError:
        return None
    mesh = trimesh.load(str(stl), force="mesh")
    if mesh.is_empty:
        return None
    vg = mesh.voxelized(pitch=pitch)
    mat = np.asarray(vg.matrix, dtype=bool)
    if mat.ndim == 2:
        mat = mat[:, :, None]
    transform = getattr(vg, "transform", None)
    if transform is None:
        origin = [0.0, 0.0, 0.0]
        tf = None
    else:
        tf = np.asarray(transform)
        origin = [float(x) for x in tf[:3, 3]]
    return {
        "matrix": mat,
        "pitch": float(pitch),
        "origin": origin,
        "transform": tf,
        "extents_m": [float(x) for x in (mesh.bounds[1] - mesh.bounds[0])],
    }


def _passive(mat) -> tuple[Any, Any]:
    """Preserve fixture (min-x occupied slab) and load (max-x) as solid; outside as void."""
    import numpy as np

    inside = np.asarray(mat, dtype=bool)
    void = ~inside
    solid = np.zeros_like(void)
    xs = np.where(inside.any(axis=(1, 2)))[0]
    if len(xs):
        solid[xs[0]] = inside[xs[0]]
        solid[xs[-1]] = inside[xs[-1]]
        void[xs[0]] = False
        void[xs[-1]] = False
    else:
        solid[0, :, :] = True
        solid[-1, :, :] = True
        void[0, :, :] = False
        void[-1, :, :] = False
    return void, solid


# Outward quads on an occupied voxel: (neighbor offset, four corners in voxel index).
_VOXEL_FACES = (
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
)


def _occupancy_mesh(solid, pitch: float, origin: list[float], transform=None):
    """Exposed-face STL from a density field. No scikit-image (marching cubes) required."""
    import numpy as np
    import trimesh

    occ = np.asarray(solid) >= 0.5
    if occ.ndim == 2:
        occ = occ[:, :, None]
    if occ.shape[2] < 2:
        occ = np.repeat(occ, 2, axis=2)
    if not occ.any():
        return None
    nx, ny, nz = occ.shape
    vert_i: dict[tuple[int, int, int], int] = {}
    verts: list[tuple[int, int, int]] = []
    faces: list[list[int]] = []

    def vid(x: int, y: int, z: int) -> int:
        key = (x, y, z)
        idx = vert_i.get(key)
        if idx is None:
            idx = len(verts)
            vert_i[key] = idx
            verts.append(key)
        return idx

    def occupied(i: int, j: int, k: int) -> bool:
        return 0 <= i < nx and 0 <= j < ny and 0 <= k < nz and bool(occ[i, j, k])

    for i, j, k in zip(*np.where(occ)):
        ii, jj, kk = int(i), int(j), int(k)
        for (di, dj, dk), corners in _VOXEL_FACES:
            if occupied(ii + di, jj + dj, kk + dk):
                continue
            ids = [vid(ii + cx, jj + cy, kk + cz) for cx, cy, cz in corners]
            faces.append([ids[0], ids[1], ids[2]])
            faces.append([ids[0], ids[2], ids[3]])
    if not faces:
        return None
    xyz = np.asarray(verts, dtype=float)
    tf = np.asarray(transform) if transform is not None else None
    if tf is None or tf.shape != (4, 4):
        tf = np.eye(4)
        tf[0, 0] = tf[1, 1] = tf[2, 2] = pitch
        tf[:3, 3] = origin
    homo = np.c_[xyz, np.ones(len(xyz))]
    world = (tf @ homo.T).T[:, :3]
    mesh = trimesh.Trimesh(vertices=world, faces=np.asarray(faces, dtype=np.int64), process=False)
    try:
        mesh.remove_unreferenced_vertices()
        mesh.fix_normals()
    except (ValueError, AttributeError, RuntimeError):
        pass
    if mesh.is_empty:
        return None
    return mesh


def _marching(x, pitch: float, origin: list[float], transform=None):
    """Prefer skimage marching cubes when present; else exposed voxel faces."""
    import numpy as np

    solid = np.asarray(x) >= 0.5
    if solid.ndim == 2:
        solid = solid[:, :, None]
    if solid.shape[2] < 2:
        solid = np.repeat(solid, 2, axis=2)
    try:
        from trimesh.voxel.base import VoxelGrid

        tf = transform
        if tf is None:
            tf = np.eye(4)
            tf[0, 0] = tf[1, 1] = tf[2, 2] = pitch
            tf[:3, 3] = origin
        mesh = VoxelGrid(solid.astype(bool), transform=tf).marching_cubes
        if mesh is not None and not mesh.is_empty:
            return mesh
    except (ImportError, ModuleNotFoundError, ValueError, AttributeError, RuntimeError):
        pass
    return _occupancy_mesh(solid, pitch, origin, transform)


def _outcomes(volfrac: float | None, mill: bool, additive: bool, three_d: bool) -> list[dict[str, Any]]:
    vfs = [0.3, 0.4] if volfrac is None else [float(max(0.08, min(0.9, volfrac)))]
    out: list[dict[str, Any]] = []
    for vf in vfs:
        out.append({
            "name": f"SIMP {round(vf * 100)}% unrestricted",
            "volfrac": vf,
            "mill_25d": False,
            "additive": False,
        })
    if mill:
        vf = vfs[-1]
        out.append({
            "name": f"SIMP {round(vf * 100)}% mill 2.5D",
            "volfrac": vf,
            "mill_25d": True,
            "additive": False,
        })
    if additive and three_d:
        vf = vfs[-1]
        out.append({
            "name": f"SIMP {round(vf * 100)}% AM overhang",
            "volfrac": vf,
            "mill_25d": False,
            "additive": True,
        })
    # Prefer a mill/AM variant plus one unrestricted; cap at 3.
    if mill or (additive and three_d):
        mixed = [o for o in out if o["mill_25d"] or o["additive"]]
        plain = [o for o in out if not o["mill_25d"] and not o["additive"]]
        return (plain[:1] + mixed)[:3]
    return out[:3]


def _register_candidate(
    *,
    project_id: str,
    name: str,
    stl_mm: Path,
    source_part: dict[str, Any],
    offset_y_mm: float,
) -> dict[str, Any] | None:
    existing = list_parts(project_id)
    if len(existing) >= MAX_UNIQUE_PARTS:
        return None
    stub = (
        f"# Imported SIMP candidate — not CadQuery.\n"
        f"# {name}\n"
        f"# Organic mesh from voxel topology optimization. Edit in the source CAD\n"
        f"# tool or keep this as a reference body. PARAMS do not apply.\n"
    )
    try:
        part = upsert_part(project_id, name=name, source=stub, kind="imported")
    except ValueError:
        return None
    dest = part_dir(project_id, part["id"]) / "model.stl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(stl_mm.read_bytes())
    try:
        from cadfree.manufacturing.mesh import load_mesh, metrics_from_mesh

        metrics = metrics_from_mesh(load_mesh(dest)).to_dict()
        save_part_metrics(project_id, part["id"], metrics)
    except (OSError, ValueError):
        metrics = {}
    save_part_notes(part["id"], json.dumps({"generated": True, "engine": "simp", "source_part": source_part["id"]}))
    insts = [i for i in list_instances(project_id) if i["part_id"] == source_part["id"]]
    loc = {"x": 0.0, "y": offset_y_mm, "z": 0.0}
    if insts:
        base = insts[0].get("loc") or {}
        loc = {
            "x": float(base.get("x") or 0.0),
            "y": float(base.get("y") or 0.0) + offset_y_mm,
            "z": float(base.get("z") or 0.0),
        }
    place_instance(project_id, part["id"], name=name, loc=loc)
    part = get_part(project_id, part["id"])
    part["metrics"] = metrics
    return part


def _handoff(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    dest = sim_dir(project_id) / "topology"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "job.json"
    path.write_text(json.dumps(payload, indent=2, default=str)[:400000], encoding="utf-8")
    payload["handoff"] = str(path)
    return payload


def _to_3d_density(x, nx: int, ny: int, nz: int):
    import numpy as np

    arr = np.asarray(x, dtype=float)
    if arr.ndim == 2:
        # Sigmund (nely, nelx) → (nelx, nely, 1)
        if arr.shape == (ny, nx):
            arr = arr.T[:, :, None]
        elif arr.shape == (nx, ny):
            arr = arr[:, :, None]
        else:
            arr = arr.T[:, :, None]
    if arr.ndim == 3 and arr.shape[2] < max(nz, 2):
        arr = np.repeat(arr, max(nz, 2), axis=2)
    return arr


def run_generate(
    project_id: str,
    *,
    part_id: str | None = None,
    volfrac: float | None = None,
    design_space: str = "part",
    mill_25d: bool | None = None,
    additive: bool | None = None,
    assumed_load: bool = True,
) -> dict[str, Any]:
    """Voxel SIMP on the SI copy. Registers imported STL candidates."""
    t0 = time.perf_counter()
    probe = probe_generate()
    if not probe["ok"]:
        return _handoff(project_id, _not_ready(probe["reason"] or "scipy missing", {"probe": probe}))

    try:
        status = write_si_status(project_id, part_id=part_id)
    except KeyError as exc:
        return _handoff(project_id, _not_ready(str(exc)))

    files = (status.get("part") or {}).get("files") or {}
    stl = Path(files["stl_m"]) if files.get("stl_m") else None
    if not stl or not stl.is_file():
        return _handoff(
            project_id,
            _not_ready("build_model first — generative design needs an SI mesh copy."),
        )

    try:
        import numpy as np
        import trimesh
    except ImportError as e:
        return _handoff(project_id, _not_ready(f"trimesh/numpy required for voxelization ({e})."))

    mesh = trimesh.load(str(stl), force="mesh")
    if mesh.is_empty:
        return _handoff(project_id, _not_ready("SI STL is empty."))
    ext = tuple(float(x) for x in (mesh.bounds[1] - mesh.bounds[0]))
    three_guess = min(ext) > 0.25 * max(ext)
    pitch = _pitch_for(ext, three_guess)
    vox = _voxelize(stl, pitch)
    if not vox:
        return _handoff(project_id, _not_ready("voxelization failed."))
    mat = vox["matrix"]
    nx, ny, nz = (int(mat.shape[0]), int(mat.shape[1]), int(mat.shape[2]))
    three_d = three_guess and nz >= 3
    if nx < 4 or ny < 4:
        return _handoff(
            project_id,
            _not_ready(f"voxel grid too coarse ({nx}×{ny}×{nz}). Rebuild a larger part."),
        )

    mill, am = _process_flags(project_id, mill_25d, additive)
    space = design_space if design_space in {"part", "bbox"} else "part"
    pvoid, psolid = _passive(mat)
    if space == "bbox":
        pvoid = np.zeros_like(pvoid)

    load_n = (status.get("load") or {}).get("F_N")
    try:
        load_n = float(load_n) if load_n is not None else None
    except (TypeError, ValueError):
        load_n = None
    if load_n is None and not assumed_load:
        return _handoff(
            project_id,
            _not_ready(
                "No F_N on the part. Pass a load or set assumed_load so Cadfree can use a unit load."
            ),
        )

    outcomes = _outcomes(volfrac, mill, am, three_d)
    sim_top = sim_dir(project_id) / "topology"
    sim_top.mkdir(parents=True, exist_ok=True)

    source = get_part(project_id, status["part"]["id"])
    active_id = source["id"]
    candidates: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    span_y_mm = ext[1] * 1000.0
    offset = span_y_mm * 1.4 if span_y_mm > 1 else 40.0
    nloop = NLOOP_3D if three_d else NLOOP_2D
    x0 = mat.astype(float)
    x0 = np.where(x0 > 0, np.clip(float(outcomes[0]["volfrac"]), 0.2, 0.9), 0.001)

    for i, oc in enumerate(outcomes):
        if time.perf_counter() - t0 > JOB_BUDGET_S:
            results.append({"ok": False, "name": oc["name"], "reason": "job budget — remaining outcomes skipped."})
            break
        try:
            if three_d:
                opt = _simp.optimize_3d(
                    nx, ny, nz,
                    volfrac=oc["volfrac"],
                    nloop=nloop,
                    mill_25d=oc["mill_25d"],
                    additive=oc["additive"],
                    passive_void=pvoid,
                    passive_solid=psolid,
                    x0=x0 if space == "part" else None,
                )
            else:
                slab = x0.max(axis=2)  # (nx, ny)
                pv = pvoid.max(axis=2).T  # (nely, nelx)
                ps = psolid.max(axis=2).T
                x2 = slab.T  # (nely, nelx)
                opt = _simp.optimize_2d(
                    nx, ny,
                    volfrac=oc["volfrac"],
                    nloop=nloop,
                    load="cantilever",
                    mill_25d=oc["mill_25d"],
                    additive=oc["additive"],
                    passive_void=pv,
                    passive_solid=ps,
                    x0=x2 if space == "part" else None,
                )
            if not opt.get("ok"):
                results.append({
                    "ok": False,
                    "name": oc["name"],
                    "reason": opt.get("error") or opt.get("reason") or "SIMP did not return a field.",
                })
                continue
            x = _to_3d_density(opt["x"], nx, ny, max(nz, 2))
            if oc["mill_25d"] and x.ndim == 3 and x.shape[2] > 1:
                x = _simp._project_mill(x)
        except (ValueError, RuntimeError, MemoryError) as e:
            results.append({"ok": False, "name": oc["name"], "reason": f"{type(e).__name__}: {e}"})
            continue

        mesh_out = _marching(x, pitch, vox["origin"], vox.get("transform"))
        if mesh_out is None or mesh_out.is_empty:
            results.append({"ok": False, "name": oc["name"], "reason": "empty isosurface."})
            continue
        # SI marching cubes is metres; Cadfree parts are millimetres.
        mesh_mm = mesh_out.copy()
        mesh_mm.apply_scale(1000.0)
        stl_out = sim_top / f"candidate_{i}.stl"
        mesh_mm.export(str(stl_out))
        try:
            part = _register_candidate(
                project_id=project_id,
                name=f"gen · {oc['name']}",
                stl_mm=stl_out,
                source_part=source,
                offset_y_mm=offset * (i + 1),
            )
        except (ValueError, OSError, KeyError) as e:
            results.append({"ok": False, "name": oc["name"], "reason": f"register: {type(e).__name__}: {e}"})
            continue
        rec = {
            "ok": True,
            "kind": "topology",
            "name": oc["name"],
            "volfrac_target": oc["volfrac"],
            "volume": opt.get("volfrac_actual"),
            "volfrac_actual": opt.get("volfrac_actual"),
            "compliance": opt.get("compliance"),
            "history": opt.get("history"),
            "mill_25d": oc["mill_25d"],
            "additive": oc["additive"],
            "method": opt.get("method"),
            "nel": [nx, ny, nz],
            "stl": str(stl_out),
            "part_id": part["id"] if part else None,
            "assumed_unit_load": load_n is None,
        }
        if part is None:
            rec["ok"] = False
            rec["reason"] = f"unique-part cap ({MAX_UNIQUE_PARTS}) — candidate mesh saved but not registered."
        candidates.append(rec)
        results.append(rec)

    try:
        set_active_part(project_id, active_id)
    except KeyError:
        pass

    payload = {
        "ok": bool(candidates) and any(c.get("ok") and c.get("part_id") for c in candidates),
        "kind": "topology",
        "engine": "simp",
        "solver": "simp-voxel-fem",
        "lineage": LINEAGE,
        "disclaimer": DISCLAIMER,
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "nel": [nx, ny, nz],
        "pitch_m": pitch,
        "design_space": space,
        "three_d": bool(three_d),
        "mill_filter": mill,
        "am_filter": am,
        "assumed_unit_load": load_n is None,
        "load_N": load_n if load_n is not None else 1.0,
        "si": {"stl_m": str(stl), "part_id": source["id"]},
        "candidates": candidates,
        "results": results,
        "iterate": [],
        "reason": None if candidates else "no candidate survived marching cubes.",
        "probe": probe,
    }
    if not payload["ok"] and not payload["reason"]:
        payload["reason"] = "SIMP ran but no imported part was registered."
        payload["error"] = payload["reason"]
    return _handoff(project_id, payload)
