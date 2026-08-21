"""Gmsh + CalculiX from the SI mesh copy. CadQuery does not run this.

Default mesh is quadratic tets (C3D10). Linear C3D4 is the fallback if Gmsh
refuses second-order on the STL, and an explicit order=1 switch for a fast
check. Mesh-convergence (2–3 characteristic lengths + Richardson) is opt-in
via values.converge / values.mesh_levels — we never invent a missing level.

If gmsh/ccx are missing the mesh copy and job card still land in sim/fea/
so something that *is* installed can pick them up. We never claim a solve.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cadfree.physics.convergence import char_lengths, richardson

DISCLAIMER = (
    "Linear static, isotropic E. Quadratic tets (C3D10) when Gmsh succeeds; "
    "linear C3D4 is the named fallback. Fixture/load are bbox faces or named "
    "picks, not mate-face contact. Not anisotropic FDM. Not a sign-off."
)


def probe_fea() -> dict[str, Any]:
    gmsh = shutil.which("gmsh")
    ccx = shutil.which("ccx") or shutil.which("calculix")
    return {
        "available": bool(gmsh and ccx),
        "gmsh": {"available": bool(gmsh), "path": gmsh},
        "calculix": {"available": bool(ccx), "path": ccx},
        "label": "Gmsh tet mesh (C3D10 default) + CalculiX linear static",
        "install_hint": "Install `gmsh` and CalculiX `ccx` on PATH for mesh FEA.",
        "elements": "C3D10 quadratic tets; C3D4 if second-order meshing fails.",
    }


def _job_dir(project_sim: Path) -> Path:
    path = project_sim / "fea"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_geo(stl: Path, geo: Path, char_len: float, order: int = 2) -> None:
    order = 2 if int(order) >= 2 else 1
    geo.write_text(
        f"""// Cadfree FEA job. Geometry is SI metres from the CadQuery STL copy.
Merge "{stl.as_posix()}";
Mesh.CharacteristicLengthMax = {char_len:.6g};
Mesh.CharacteristicLengthMin = {char_len / 4.0:.6g};
Mesh.Algorithm3D = 1;
Mesh.ElementOrder = {order};
Mesh.SecondOrderLinear = 0;
Mesh.HighOrderOptimize = {1 if order == 2 else 0};
Mesh 3;
""",
        encoding="utf-8",
    )


def _append_ccx(
    inp: Path,
    status: dict[str, Any],
    nodes: list[tuple[int, float, float, float]],
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from cadfree.physics.bcs import resolve_bcs

    resolved = resolve_bcs(status, nodes, spec=spec)
    mat = status.get("material") or {}
    load = float((status.get("load") or {}).get("F_N") or 0.0)
    fea = status.get("_fea") or {}
    centrif = bool(fea.get("centrif"))
    omega = float(fea.get("omega") or 0.0)
    modal = bool(fea.get("modal") or fea.get("frequency"))
    try:
        n_modes = max(1, min(int(fea.get("n_modes") or 8), 20))
    except (TypeError, ValueError):
        n_modes = 8
    fix = resolved.get("fix_nodes") or ([nodes[0][0]] if nodes else [1])
    pull = resolved.get("load_nodes") or ([nodes[-1][0]] if nodes else [1])
    direction = resolved.get("direction") or [1.0, 0.0, 0.0]
    mag = float(load) / max(len(pull), 1) if pull else 0.0
    e_pa = float(mat.get("E") or 2.1e9)
    nu = float(mat.get("nu") or 0.38)
    rho = float(mat.get("rho") or 1200.0)
    lines = [
        "",
        "*MATERIAL, NAME=PART",
        "*ELASTIC",
        f"{e_pa:.6g}, {nu:.4g}",
    ]
    if centrif or modal:
        lines += ["*DENSITY", f"{rho:.6g},"]
    lines += [
        "*SOLID SECTION, ELSET=Eall, MATERIAL=PART",
        "*BOUNDARY",
    ]
    for nid in fix:
        lines.append(f"{nid}, 1, 3, 0.0")
    lines += ["*STEP", "*STATIC"]
    if centrif and omega:
        w2 = omega * omega
        lines += ["*DLOAD", f"Eall, CENTRIF, {w2:.6g}, 0,0,0, 0,0,1"]
    if (not centrif or load) and pull:
        lines += ["*CLOAD"]
        for nid in pull:
            for dof, comp in enumerate(direction, start=1):
                if abs(float(comp)) < 1e-12:
                    continue
                lines.append(f"{nid}, {dof}, {mag * float(comp):.6g}")
    lines += [
        "*NODE FILE",
        "U",
        "*EL FILE",
        "S",
        "*EL PRINT, ELSET=Eall",
        "S",
        "*NODE PRINT, NSET=Nall",
        "U",
        "*END STEP",
    ]
    if modal:
        lines += [
            "*STEP",
            "*FREQUENCY",
            str(n_modes),
            "*NODE FILE",
            "U",
            "*END STEP",
        ]
    lines.append("")
    with inp.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return resolved


def _parse_nodes(inp: Path) -> list[tuple[int, float, float, float]]:
    nodes: list[tuple[int, float, float, float]] = []
    in_nodes = False
    for raw in inp.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.upper().startswith("*NODE"):
            in_nodes = True
            continue
        if line.startswith("*"):
            in_nodes = False
            continue
        if not in_nodes or not line:
            continue
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if len(parts) >= 4:
            try:
                nodes.append((int(float(parts[0])), float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
    return nodes


def _element_kind(inp: Path) -> str:
    text = inp.read_text(encoding="utf-8", errors="ignore").upper()
    if "C3D10" in text or "TETRA10" in text:
        return "C3D10"
    if "C3D4" in text or "TETRA4" in text:
        return "C3D4"
    return "unknown"


def _parse_stress(dat: Path) -> dict[str, float]:
    max_vm = 0.0
    max_u = 0.0
    in_stress = False
    in_disp = False
    for raw in dat.read_text(encoding="utf-8", errors="ignore").splitlines():
        low = raw.lower()
        if "stress" in low:
            in_stress, in_disp = True, False
            continue
        if "displac" in low:
            in_disp, in_stress = True, False
            continue
        parts = raw.split()
        nums = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                continue
        if in_stress and len(nums) >= 6:
            sxx, syy, szz, sxy, sxz, syz = nums[-6:]
            vm = math.sqrt(
                0.5
                * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                + 3.0 * (sxy**2 + sxz**2 + syz**2)
            )
            max_vm = max(max_vm, vm)
        if in_disp and len(nums) >= 3:
            ux, uy, uz = nums[-3:]
            max_u = max(max_u, math.sqrt(ux * ux + uy * uy + uz * uz))
    return {"von_mises_max": max_vm, "u_max": max_u}


def _parse_modes(dat: Path) -> list[dict[str, float]]:
    """Eigenvalues from a CalculiX *FREQUENCY .dat, if present."""
    modes: list[dict[str, float]] = []
    if not dat.is_file():
        return modes
    in_eigs = False
    for raw in dat.read_text(encoding="utf-8", errors="ignore").splitlines():
        low = raw.lower()
        if "eigenvalue" in low or "eigen value" in low:
            in_eigs = True
            continue
        if not in_eigs:
            continue
        parts = raw.split()
        nums: list[float] = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                continue
        if len(nums) >= 2:
            n = int(nums[0]) if nums[0] == int(nums[0]) else len(modes) + 1
            eig = nums[1]
            freq = nums[2] if len(nums) >= 3 else (math.sqrt(abs(eig)) / (2.0 * math.pi) if eig else 0.0)
            modes.append({"mode": n, "eigenvalue": eig, "frequency_hz": freq})
        if "displacement" in low or raw.strip().startswith("*"):
            in_eigs = False
    return modes


def _omega_from(status: dict[str, Any], extra: dict[str, Any]) -> float | None:
    for src in (extra, status.get("environment") or {}, status.get("inputs") or {}, status.get("constraints") or {}):
        if not isinstance(src, dict):
            continue
        rpm = src.get("n_rpm") if src.get("n_rpm") is not None else src.get("rpm")
        if rpm in (None, ""):
            continue
        try:
            return 2.0 * math.pi * float(rpm) / 60.0
        except (TypeError, ValueError):
            continue
    return None


def _gmsh_mesh(gmsh: str, geo: Path, inp: Path, dest: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [gmsh, str(geo), "-3", "-format", "inp", "-o", str(inp)],
        cwd=str(dest),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _one_level(
    *,
    dest: Path,
    stl_path: Path,
    status: dict[str, Any],
    spec: dict[str, Any] | None,
    probe: dict[str, Any],
    char: float,
    order: int,
    timeout: int,
    tag: str,
) -> dict[str, Any]:
    work = dest / tag
    work.mkdir(parents=True, exist_ok=True)
    geo = work / "part.geo"
    inp = work / "part.inp"
    used_order = 2 if order >= 2 else 1
    _write_geo(stl_path, geo, char, order=used_order)
    gmsh = probe["gmsh"]["path"]
    try:
        gmsh_run = _gmsh_mesh(gmsh, geo, inp, work, timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"gmsh timed out after {timeout}s", "char_m": char, "order": used_order}
    if (not inp.is_file() or gmsh_run.returncode != 0) and used_order == 2:
        used_order = 1
        _write_geo(stl_path, geo, char, order=1)
        try:
            gmsh_run = _gmsh_mesh(gmsh, geo, inp, work, timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"gmsh timed out after {timeout}s", "char_m": char, "order": 1}
        fallback_note = "Quadratic tet mesh failed; fell back to linear C3D4 on this level."
    else:
        fallback_note = None
    if not inp.is_file() or gmsh_run.returncode != 0:
        return {
            "ok": False,
            "error": (gmsh_run.stderr or gmsh_run.stdout or "gmsh failed")[-2000:],
            "char_m": char,
            "order": used_order,
            "gmsh_log": (gmsh_run.stdout or "")[-1000:],
        }
    nodes = _parse_nodes(inp)
    resolved = _append_ccx(inp, status, nodes, spec=spec)
    element = _element_kind(inp)
    if not probe["calculix"]["available"]:
        return {
            "ok": False,
            "error": "Gmsh wrote an INP, but CalculiX `ccx` is not on PATH.",
            "inp": str(inp),
            "nodes": len(nodes),
            "element": element,
            "char_m": char,
            "order": used_order,
            "bcs": resolved,
        }
    ccx = probe["calculix"]["path"]
    try:
        ccx_run = subprocess.run(
            [ccx, "part"],
            cwd=str(work),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"ccx timed out after {timeout}s",
            "char_m": char,
            "order": used_order,
            "nodes": len(nodes),
            "element": element,
        }
    dat = work / "part.dat"
    parsed = _parse_stress(dat) if dat.is_file() else {}
    vm = parsed.get("von_mises_max") or 0.0
    modes = _parse_modes(dat) if dat.is_file() else []
    return {
        "ok": ccx_run.returncode == 0 and (vm > 0 or bool(modes)),
        "char_m": char,
        "order": used_order,
        "element": element,
        "nodes": len(nodes),
        "von_mises_max": vm,
        "u_max_m": parsed.get("u_max"),
        "modes": modes,
        "inp": str(inp),
        "handoff": str(work),
        "bcs": resolved,
        "fallback_note": fallback_note,
        "stdout": (ccx_run.stdout or "")[-1500:],
        "error": None if ccx_run.returncode == 0 else (ccx_run.stderr or "ccx failed")[-2000:],
    }


def _copy_geometry(status: dict[str, Any], dest: Path) -> Path | None:
    files = (status.get("part") or {}).get("files") or {}
    stl = files.get("stl_m") or ""
    stl_path = dest / "part_si.stl"
    if stl and Path(stl).is_file():
        if Path(stl).resolve() != stl_path.resolve():
            shutil.copy2(stl, stl_path)
        else:
            stl_path = Path(stl)
    pick_mm = (status.get("pick") or {}).get("stl_mm") or files.get("stl_mm")
    if pick_mm and Path(pick_mm).is_file():
        shutil.copy2(pick_mm, dest / "part_mm.stl")
    return stl_path if stl_path.is_file() else None


def run_fea(
    status: dict[str, Any],
    *,
    timeout: int = 120,
    values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from cadfree.physics.bcs import normalize_fea_bcs, resolve_bcs

    probe = probe_fea()
    sim = Path((status.get("paths") or {}).get("sim") or ".")
    dest = _job_dir(sim)
    extra = dict(values or {})
    spec = extra.get("fea_bcs") if isinstance(extra.get("fea_bcs"), dict) else extra
    if not normalize_fea_bcs(spec).get("fix") and not normalize_fea_bcs(spec).get("load"):
        spec = status.get("fea_bcs")
    omega = _omega_from(status, extra)
    centrif = bool(extra.get("centrif") or extra.get("CENTRIF") or extra.get("rotating"))
    if extra.get("centrif") is False:
        centrif = False
    modal = bool(extra.get("modal") or extra.get("frequency") or extra.get("FREQUENCY"))
    if centrif and omega:
        status["_fea"] = {
            "centrif": True,
            "omega": omega,
            "axis": [0.0, 0.0, 1.0],
            "modal": modal,
            "n_modes": extra.get("n_modes") or 8,
        }
        if not normalize_fea_bcs(spec).get("fix"):
            merged = dict(spec or {}) if isinstance(spec, dict) else {}
            merged.setdefault("fix_selector", "hub")
            spec = merged
    elif modal:
        status["_fea"] = {"centrif": False, "omega": omega or 0.0, "modal": True, "n_modes": extra.get("n_modes") or 8}
    stl_path = _copy_geometry(status, dest)
    bbox = (status.get("part") or {}).get("bbox_m") or [0.04, 0.04, 0.01]
    try:
        order = int(extra.get("order") or extra.get("element_order") or 2)
    except (TypeError, ValueError):
        order = 2
    converge = bool(extra.get("converge") or extra.get("mesh_convergence"))
    try:
        n_levels = int(extra.get("mesh_levels") or (3 if converge else 1))
    except (TypeError, ValueError):
        n_levels = 1 if not converge else 3
    n_levels = max(1, min(n_levels, 3))
    if converge:
        n_levels = max(n_levels, 2)
    lengths = char_lengths(bbox, n_levels=n_levels, base=extra.get("char_m"))
    preview = resolve_bcs(status, [], spec=spec)
    bcs = {
        "fix": preview.get("fix_source"),
        "load": preview.get("load_source"),
        "direction": preview.get("direction"),
        "source": preview.get("source"),
        "F_N": (status.get("load") or {}).get("F_N"),
        "material": status.get("material"),
        "notes": preview.get("notes"),
        "spec": preview.get("spec"),
        "centrif": bool((status.get("_fea") or {}).get("centrif")),
        "omega": (status.get("_fea") or {}).get("omega"),
    }
    (dest / "bcs.json").write_text(json.dumps(bcs, indent=2, default=str), encoding="utf-8")
    handoff = {
        "ok": False,
        "kind": "fea",
        "solver": "calculix",
        "label": probe["label"],
        "handoff": str(dest),
        "geometry": str(stl_path) if stl_path else None,
        "order_requested": 2 if order >= 2 else 1,
        "mesh_levels": lengths,
        "bcs": bcs,
        "disclaimer": preview.get("disclaimer") or DISCLAIMER,
    }
    if stl_path is None:
        handoff["error"] = "No SI STL copy. build_model so CadQuery can tessellate the solid first."
        return handoff
    if not probe["gmsh"]["available"]:
        handoff["error"] = probe["install_hint"]
        return handoff

    levels: list[dict[str, Any]] = []
    per_timeout = timeout if n_levels == 1 else max(45, timeout // n_levels)
    for i, char in enumerate(lengths):
        levels.append(
            _one_level(
                dest=dest,
                stl_path=stl_path,
                status=status,
                spec=spec,
                probe=probe,
                char=char,
                order=order,
                timeout=per_timeout,
                tag=f"L{i}",
            )
        )

    ok_levels = [lv for lv in levels if lv.get("ok")]
    winner = ok_levels[-1] if ok_levels else levels[-1]
    resolved = winner.get("bcs") or {}
    if resolved:
        (dest / "bcs.json").write_text(json.dumps(resolved, indent=2, default=str), encoding="utf-8")
        handoff["bcs"] = {
            "fix": f"{len(resolved.get('fix_nodes') or [])} nodes ({resolved.get('fix_source')})",
            "load": f"{len(resolved.get('load_nodes') or [])} nodes ({resolved.get('load_source')})",
            "direction": resolved.get("direction"),
            "source": resolved.get("source"),
            "F_N": resolved.get("F_N"),
            "notes": resolved.get("notes"),
        }
        handoff["disclaimer"] = resolved.get("disclaimer") or handoff["disclaimer"]

    allow = float((status.get("material") or {}).get("allowable") or 0) or 1.0
    sf_req = float((status.get("load") or {}).get("safety_factor") or 2.0)
    vm = float(winner.get("von_mises_max") or 0.0)
    sf = allow / vm if vm else None
    iterate = []
    params_mm = (status.get("cadquery") or {}).get("params_mm") or {}
    if sf is not None and sf < sf_req:
        if "r2_mm" in params_mm:
            iterate.append(
                {
                    "param": "r2_mm",
                    "reason": f"CalculiX von Mises SF {sf:.2f} < required {sf_req:g} (CENTRIF, not handbook hoop)",
                    "scale": math.sqrt(max(sf, 0.05) / sf_req),
                }
            )
        else:
            iterate.append(
                {
                    "param": "thickness_mm",
                    "reason": f"CalculiX von Mises SF {sf:.2f} < required {sf_req:g}",
                    "scale": (sf_req / max(sf, 0.05)) ** 0.5,
                }
            )
    conv = None
    if n_levels > 1:
        conv = {
            "von_mises": richardson(
                [float(lv["von_mises_max"]) for lv in ok_levels],
                quantity="von_mises_Pa",
            ),
            "u_max": richardson(
                [float(lv.get("u_max_m") or 0.0) for lv in ok_levels],
                quantity="u_max_m",
            ),
        }
        (dest / "convergence.json").write_text(json.dumps(conv, indent=2, default=str), encoding="utf-8")

    notes = [lv.get("fallback_note") for lv in levels if lv.get("fallback_note")]
    error = None
    if not winner.get("ok"):
        error = winner.get("error") or "CalculiX did not produce a stress field"
    elif n_levels > 1 and len(ok_levels) < 2:
        error = (
            "Asked for mesh convergence but only one level solved. "
            "Reporting that mesh; not extrapolating."
        )

    payload = {
        "ok": bool(winner.get("ok") and vm > 0),
        "kind": "fea",
        "solver": "calculix",
        "handoff": str(dest),
        "inp": winner.get("inp"),
        "nodes": winner.get("nodes"),
        "element": winner.get("element"),
        "order": winner.get("order"),
        "char_m": winner.get("char_m"),
        "von_mises_max": vm,
        "von_mises_MPa": vm / 1e6,
        "u_max_m": winner.get("u_max_m"),
        "modes": winner.get("modes") or [],
        "centrif": bool((status.get("_fea") or {}).get("centrif")),
        "SF": sf,
        "allowable_Pa": allow,
        "iterate": iterate,
        "levels": [
            {
                "char_m": lv.get("char_m"),
                "ok": lv.get("ok"),
                "element": lv.get("element"),
                "nodes": lv.get("nodes"),
                "von_mises_max": lv.get("von_mises_max"),
                "u_max_m": lv.get("u_max_m"),
                "error": lv.get("error"),
            }
            for lv in levels
        ],
        "convergence": conv,
        "notes": notes,
        "stdout": winner.get("stdout"),
        "disclaimer": handoff["disclaimer"],
        "error": error,
    }
    if conv and conv["von_mises"].get("band"):
        payload["convergence_band"] = conv["von_mises"]["band"]
    (dest / "last.json").write_text(json.dumps(payload, indent=2, default=str)[:200000], encoding="utf-8")
    return payload
