"""Gmsh + CalculiX from the SI mesh copy. CadQuery does not run this.

If gmsh/ccx are missing the mesh copy and job card still land in sim/fea/
so something that *is* installed can pick them up. We never claim a solve.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Any


def probe_fea() -> dict[str, Any]:
    gmsh = shutil.which("gmsh")
    ccx = shutil.which("ccx") or shutil.which("calculix")
    return {
        "available": bool(gmsh and ccx),
        "gmsh": {"available": bool(gmsh), "path": gmsh},
        "calculix": {"available": bool(ccx), "path": ccx},
        "label": "Gmsh tet mesh + CalculiX linear static",
        "install_hint": "Install `gmsh` and CalculiX `ccx` on PATH for mesh FEA.",
    }


def _job_dir(project_sim: Path) -> Path:
    path = project_sim / "fea"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_geo(stl: Path, geo: Path, char_len: float) -> None:
    geo.write_text(
        f"""// Cadfree FEA job. Geometry is SI metres from the CadQuery STL copy.
Merge "{stl.as_posix()}";
Mesh.CharacteristicLengthMax = {char_len:.6g};
Mesh.CharacteristicLengthMin = {char_len / 4.0:.6g};
Mesh.Algorithm3D = 1;
Mesh 3;
""",
        encoding="utf-8",
    )


def _append_ccx(inp: Path, status: dict[str, Any], nodes: list[tuple[int, float, float, float]]) -> None:
    mat = status.get("material") or {}
    load = (status.get("load") or {}).get("F_N") or 0.0
    xs = [n[1] for n in nodes] or [0.0]
    xmin, xmax = min(xs), max(xs)
    span = max(xmax - xmin, 1e-9)
    fix = [n[0] for n in nodes if n[1] <= xmin + 0.05 * span]
    pull = [n[0] for n in nodes if n[1] >= xmax - 0.05 * span]
    if not fix:
        fix = [nodes[0][0]] if nodes else [1]
    if not pull:
        pull = [nodes[-1][0]] if nodes else [1]
    fx = float(load) / max(len(pull), 1)
    e_pa = float(mat.get("E") or 2.1e9)
    nu = float(mat.get("nu") or 0.38)
    lines = [
        "",
        "*MATERIAL, NAME=PART",
        "*ELASTIC",
        f"{e_pa:.6g}, {nu:.4g}",
        "*SOLID SECTION, ELSET=Eall, MATERIAL=PART",
        "*BOUNDARY",
    ]
    for nid in fix:
        lines.append(f"{nid}, 1, 3, 0.0")
    lines += ["*STEP", "*STATIC", "*CLOAD"]
    for nid in pull:
        lines.append(f"{nid}, 1, {fx:.6g}")
    lines += [
        "*NODE FILE",
        "U",
        "*EL FILE",
        "S",
        "*EL PRINT, ELSET=Eall",
        "S",
        "*END STEP",
        "",
    ]
    with inp.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


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


def run_fea(status: dict[str, Any], *, timeout: int = 120) -> dict[str, Any]:
    probe = probe_fea()
    sim = Path((status.get("paths") or {}).get("sim") or ".")
    dest = _job_dir(sim)
    files = (status.get("part") or {}).get("files") or {}
    stl = files.get("stl_m") or ""
    stl_path = dest / "part_si.stl"
    if stl and Path(stl).is_file():
        if Path(stl).resolve() != stl_path.resolve():
            shutil.copy2(stl, stl_path)
        else:
            stl_path = Path(stl)
    bbox = (status.get("part") or {}).get("bbox_m") or [0.04, 0.04, 0.01]
    char = max(min(bbox) / 6.0 if bbox else 0.004, 0.0008)
    geo = dest / "part.geo"
    if stl_path.is_file():
        _write_geo(stl_path, geo, char)
    bcs = {
        "fix": "nodes on min-x 5% of span, all DOF",
        "load": "Fx distributed on max-x 5% of span = F_N from SI status",
        "F_N": (status.get("load") or {}).get("F_N"),
        "material": status.get("material"),
    }
    (dest / "bcs.json").write_text(
        __import__("json").dumps(bcs, indent=2, default=str), encoding="utf-8"
    )
    handoff = {
        "ok": False,
        "kind": "fea",
        "solver": "calculix",
        "label": probe["label"],
        "handoff": str(dest),
        "geometry": str(stl_path) if stl_path.is_file() else None,
        "bcs": bcs,
        "disclaimer": (
            "Linear static, isotropic E. Fixture/load are bbox faces, not your mate faces. "
            "Not anisotropic FDM. Not a sign-off."
        ),
    }
    if not stl_path.is_file():
        handoff["error"] = "No SI STL copy. build_model so CadQuery can tessellate the solid first."
        return handoff
    if not probe["gmsh"]["available"]:
        handoff["error"] = probe["install_hint"]
        return handoff

    inp = dest / "part.inp"
    gmsh = probe["gmsh"]["path"]
    try:
        gmsh_run = subprocess.run(
            [gmsh, str(geo), "-3", "-format", "inp", "-o", str(inp)],
            cwd=str(dest),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        handoff["error"] = f"gmsh timed out after {timeout}s"
        return handoff
    if not inp.is_file() or gmsh_run.returncode != 0:
        handoff["error"] = (gmsh_run.stderr or gmsh_run.stdout or "gmsh failed")[-2000:]
        handoff["gmsh_log"] = (gmsh_run.stdout or "")[-1000:]
        return handoff

    nodes = _parse_nodes(inp)
    _append_ccx(inp, status, nodes)
    if not probe["calculix"]["available"]:
        handoff["error"] = "Gmsh wrote an INP, but CalculiX `ccx` is not on PATH."
        handoff["inp"] = str(inp)
        return handoff

    ccx = probe["calculix"]["path"]
    try:
        ccx_run = subprocess.run(
            [ccx, "part"],
            cwd=str(dest),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        handoff["error"] = f"ccx timed out after {timeout}s"
        return handoff
    dat = dest / "part.dat"
    parsed = _parse_stress(dat) if dat.is_file() else {}
    allow = float((status.get("material") or {}).get("allowable") or 0) or 1.0
    sf_req = float((status.get("load") or {}).get("safety_factor") or 2.0)
    vm = parsed.get("von_mises_max") or 0.0
    sf = allow / vm if vm else None
    iterate = []
    if sf is not None and sf < sf_req:
        iterate.append(
            {
                "param": "thickness_mm",
                "reason": f"CalculiX von Mises SF {sf:.2f} < required {sf_req:g}",
                "scale": (sf_req / max(sf, 0.05)) ** 0.5,
            }
        )
    return {
        "ok": ccx_run.returncode == 0 and vm > 0,
        "kind": "fea",
        "solver": "calculix",
        "handoff": str(dest),
        "inp": str(inp),
        "nodes": len(nodes),
        "von_mises_max": vm,
        "von_mises_MPa": vm / 1e6,
        "u_max_m": parsed.get("u_max"),
        "SF": sf,
        "allowable_Pa": allow,
        "iterate": iterate,
        "stdout": (ccx_run.stdout or "")[-1500:],
        "disclaimer": handoff["disclaimer"],
        "error": None if ccx_run.returncode == 0 else (ccx_run.stderr or "ccx failed")[-2000:],
    }
