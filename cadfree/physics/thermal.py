"""Thermal rungs: service-temp check, lumped handbook, optional CalculiX heat transfer.

CadQuery does not run this. Missing gmsh/ccx is named; we never invent NT.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from cadfree.physics.book import H_STILL_AIR, SIGMA_SB
from cadfree.physics.engine import solve_formula
from cadfree.physics.fea import _copy_geometry, _element_kind, _job_dir, _parse_nodes, _write_geo, probe_fea

DISCLAIMER = (
    "Steady isotropic conduction + still-air film (h = 10 W/(m²·K) unless surveyed). "
    "CalculiX *HEAT TRANSFER when gmsh/ccx exist. Not radiation cavities, not a transient "
    "network unless lumped τ is reported, not a UL/IEC thermal test."
)


def _c_to_k(c: float) -> float:
    return float(c) + 273.15


def _k_to_c(k: float) -> float:
    return float(k) - 273.15


def operating_temp_c(constraints: dict[str, Any] | None) -> tuple[float | None, str]:
    """Return (T_op °C, provenance). Does not invent an indoor number."""
    blob = dict(constraints or {})
    for key in ("operating_temp_c", "ambient_temp_c", "T_c", "temp_c"):
        raw = blob.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw), key
        except (TypeError, ValueError):
            continue
    env = str(blob.get("environment") or "").lower()
    if "hot" in env or "motor" in env:
        return 80.0, "environment='hot / near motors' → named 80 °C assumption (not measured)"
    return None, "missing — survey operating_temp_c"


def check_service_temp(material: dict[str, Any], constraints: dict[str, Any] | None) -> dict[str, Any]:
    """Catalog service_temp_c vs a surveyed (or named-assumption) operating temperature."""
    service = material.get("service_temp_c")
    try:
        service_f = float(service) if service is not None else None
    except (TypeError, ValueError):
        service_f = None
    t_op, prov = operating_temp_c(constraints)
    name = material.get("name") or material.get("id") or "material"
    if service_f is None:
        return {
            "id": "service_temp",
            "ok": True,
            "status": "warn",
            "T_op_c": t_op,
            "service_temp_c": None,
            "message": f"{name} has no catalog service_temp_c.",
            "provenance": prov,
        }
    if t_op is None:
        return {
            "id": "service_temp",
            "ok": True,
            "status": "warn",
            "T_op_c": None,
            "service_temp_c": service_f,
            "message": (
                f"{name} is catalogued to {service_f:.0f} °C continuous, but no operating "
                "temperature was surveyed. Not a pass."
            ),
            "provenance": prov,
        }
    margin = service_f - t_op
    if t_op > service_f:
        return {
            "id": "service_temp",
            "ok": False,
            "status": "fail",
            "T_op_c": t_op,
            "service_temp_c": service_f,
            "margin_c": margin,
            "message": (
                f"Operating {t_op:.0f} °C exceeds {name} service temperature {service_f:.0f} °C "
                f"({prov}). Swap material or cool the part."
            ),
            "provenance": prov,
        }
    status = "warn" if margin < 10 else "pass"
    extra = " — thin margin." if status == "warn" else "."
    return {
        "id": "service_temp",
        "ok": True,
        "status": status,
        "T_op_c": t_op,
        "service_temp_c": service_f,
        "margin_c": margin,
        "message": (
            f"Operating {t_op:.0f} °C is within {name} {service_f:.0f} °C service temp{extra} "
            f"({prov}). Not a heat-deflection coupon."
        ),
        "provenance": prov,
    }


def _parse_nt(dat: Path) -> dict[str, float]:
    temps: list[float] = []
    in_nt = False
    for raw in dat.read_text(encoding="utf-8", errors="ignore").splitlines():
        low = raw.lower()
        if "temperature" in low or low.strip().startswith("nt ") or " nt" in f" {low}":
            in_nt = True
            continue
        if raw.strip().startswith("*") or (in_nt and raw.strip() and raw.strip()[0].isalpha() and "e+" not in low):
            if "displac" in low or "stress" in low:
                in_nt = False
        parts = raw.split()
        nums = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                continue
        if in_nt and nums:
            temps.append(nums[-1])
    if not temps:
        return {}
    return {"T_max_K": max(temps), "T_min_K": min(temps), "T_max_C": _k_to_c(max(temps)), "T_min_C": _k_to_c(min(temps))}


def run_thermal_handbook(status: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    inputs = dict(status.get("inputs") or {})
    mat = status.get("material") or {}
    env = status.get("environment") or {}
    part = status.get("part") or {}
    A = float(extra.get("A") or inputs.get("A_surf") or part.get("area_m2") or 0.0)
    L = float(extra.get("L") or inputs.get("t") or (min(part.get("bbox_m") or [0.01]) if part.get("bbox_m") else 0.01))
    k = float(extra.get("k") or mat.get("k") or inputs.get("k") or 0.2)
    h = float(extra.get("h") or inputs.get("h") or H_STILL_AIR)
    cp = float(extra.get("cp") or mat.get("cp") or inputs.get("cp") or 1500.0)
    rho = float(mat.get("rho") or inputs.get("rho_solid") or 1200.0)
    V = float(part.get("volume_m3") or inputs.get("V") or 0.0)
    T_inf_c = extra.get("T_inf_c")
    if T_inf_c is None:
        T_inf_c = env.get("T_inf_c")
        if T_inf_c is None:
            t_op, _ = operating_temp_c(extra if "operating_temp_c" in extra else env)
            T_inf_c = t_op if t_op is not None else 25.0
            assumed_inf = t_op is None
        else:
            assumed_inf = False
    else:
        assumed_inf = False
    T_inf = _c_to_k(float(T_inf_c))
    Qdot = extra.get("Qdot") or extra.get("Q") or inputs.get("Qdot") or env.get("Qdot_W")
    try:
        Qdot = float(Qdot) if Qdot is not None else None
    except (TypeError, ValueError):
        Qdot = None
    if Qdot is None:
        # Friction power if the snapshot already has it as watts.
        ff = inputs.get("F_f")
        v = inputs.get("v")
        if ff is not None and v is not None:
            Qdot = abs(float(ff) * float(v))
    worksheets = []
    bound_common = {"k": k, "A": A, "L": L, "h": h, "T_inf": T_inf, "cp": cp, "rho_solid": rho, "V": V, "sigma": SIGMA_SB, "epsilon": float(extra.get("epsilon") or 0.9)}
    if Qdot is not None:
        bound_common["Qdot"] = Qdot
        worksheets.append(solve_formula("lumped_Tss", bound_common))
    worksheets.append(solve_formula("lumped_tau", bound_common))
    T_ss_K = None
    tss = next((w for w in worksheets if w.get("formula_id") == "lumped_Tss" and w.get("ok")), None)
    if tss:
        T_ss_K = float(tss["value"])
        bound_common["T"] = T_ss_K
        bound_common["dT"] = T_ss_K - T_inf
        worksheets.append(solve_formula("newton_cooling", bound_common))
        worksheets.append(solve_formula("radiation_net", bound_common))
        worksheets.append(solve_formula("conduction", bound_common))
    bi = (h * L / k) if k else None
    service = check_service_temp(mat, {**(status.get("constraints") or {}), **extra, "operating_temp_c": extra.get("operating_temp_c") or env.get("T_op_c") or ( _k_to_c(T_ss_K) if T_ss_K else None)})
    # If we have T_ss, score service against that predicted surface temp.
    if T_ss_K is not None:
        service = check_service_temp(mat, {"operating_temp_c": _k_to_c(T_ss_K)})
        service["message"] = (
            f"Lumped Tss {_k_to_c(T_ss_K):.0f} °C vs {mat.get('name')} "
            f"{mat.get('service_temp_c')} °C. "
        ) + (service.get("message") or "")
        service["T_ss_c"] = _k_to_c(T_ss_K)
    iterate = []
    if service.get("status") == "fail":
        iterate.append(
            {
                "param": "material_id",
                "reason": service.get("message"),
                "note": "Or add vents / a heat sink; this is lumped, not FEA.",
            }
        )
    return {
        "ok": True,
        "kind": "thermal",
        "solver": "handbook",
        "worksheets": worksheets,
        "T_ss_K": T_ss_K,
        "T_ss_C": _k_to_c(T_ss_K) if T_ss_K is not None else None,
        "T_inf_C": float(T_inf_c),
        "T_inf_assumed_room": assumed_inf,
        "Qdot_W": Qdot,
        "h": h,
        "k": k,
        "Bi": bi,
        "Bi_lumped_ok": (bi is not None and bi < 0.1),
        "service_temp": service,
        "iterate": iterate,
        "disclaimer": DISCLAIMER
        + (" Room 25 °C used for T∞ because none was surveyed." if assumed_inf else ""),
        "error": None
        if Qdot is not None
        else "No Qdot_W / friction power — lumped Tss not scored. ask_survey for heat load or operating_temp_c.",
    }


def _append_heat_ccx(
    inp: Path,
    status: dict[str, Any],
    nodes: list[tuple[int, float, float, float]],
    spec: dict[str, Any] | None,
    extra: dict[str, Any],
) -> dict[str, Any]:
    from cadfree.physics.bcs import resolve_bcs

    resolved = resolve_bcs(status, nodes, spec=spec)
    mat = status.get("material") or {}
    k = float(extra.get("k") or mat.get("k") or 0.2)
    h = float(extra.get("h") or H_STILL_AIR)
    env = status.get("environment") or {}
    t_inf_c = extra.get("T_inf_c") or env.get("T_inf_c") or 25.0
    t_hot_c = extra.get("T_hot_c") or env.get("T_hot_c")
    if t_hot_c is None:
        t_op, _ = operating_temp_c({**extra, **env})
        t_hot_c = t_op if t_op is not None else (float(t_inf_c) + 40.0)
    T_inf = _c_to_k(float(t_inf_c))
    T_hot = _c_to_k(float(t_hot_c))
    hot = resolved.get("load_nodes") or ([nodes[-1][0]] if nodes else [1])
    film = resolved.get("fix_nodes") or ([nodes[0][0]] if nodes else [1])
    qdot = extra.get("Qdot") or extra.get("qdot")
    try:
        qdot = float(qdot) if qdot is not None else None
    except (TypeError, ValueError):
        qdot = None
    mag = (qdot / max(len(hot), 1)) if qdot else None
    lines = [
        "",
        "*MATERIAL, NAME=PART",
        "*CONDUCTIVITY",
        f"{k:.6g}",
        "*SOLID SECTION, ELSET=Eall, MATERIAL=PART",
        "*INITIAL CONDITIONS, TYPE=TEMPERATURE",
        f"Nall, {T_inf:.4g}",
        "*PHYSICAL CONSTANTS, ABSOLUTE ZERO=-273.15, STEFAN BOLTZMANN=5.67e-8",
        "*BOUNDARY",
    ]
    if mag is None:
        for nid in hot:
            lines.append(f"{nid}, 11, 11, {T_hot:.6g}")
    lines += ["*STEP", "*HEAT TRANSFER, STEADY STATE", "1.0, 1.0"]
    if mag is not None:
        lines.append("*CFLUX")
        for nid in hot:
            lines.append(f"{nid}, 11, {mag:.6g}")
    lines.append("*FILM")
    for nid in film:
        lines.append(f"{nid}, F, {T_inf:.6g}, {h:.6g}")
    lines += [
        "*NODE FILE",
        "NT",
        "*NODE PRINT, NSET=Nall",
        "NT",
        "*END STEP",
        "",
    ]
    with inp.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    resolved["T_hot_K"] = T_hot
    resolved["T_inf_K"] = T_inf
    resolved["h"] = h
    resolved["k"] = k
    resolved["Qdot"] = qdot
    resolved["mode"] = "cflux" if mag is not None else "fixed_NT"
    return resolved


def run_thermal_fea(status: dict[str, Any], extra: dict[str, Any] | None = None, timeout: int = 120) -> dict[str, Any]:
    extra = dict(extra or {})
    probe = probe_fea()
    sim = Path((status.get("paths") or {}).get("sim") or ".")
    dest = _job_dir(sim) / "thermal"
    dest.mkdir(parents=True, exist_ok=True)
    stl_path = _copy_geometry(status, dest)
    handoff = {
        "ok": False,
        "kind": "thermal",
        "solver": "calculix_heat",
        "handoff": str(dest),
        "disclaimer": DISCLAIMER,
    }
    if stl_path is None:
        handoff["error"] = "No SI STL copy. build_model first."
        return handoff
    if not probe["gmsh"]["available"]:
        handoff["error"] = probe["install_hint"]
        return handoff
    bbox = (status.get("part") or {}).get("bbox_m") or [0.04, 0.04, 0.01]
    from cadfree.physics.convergence import char_lengths

    char = char_lengths(bbox, n_levels=1)[0]
    geo = dest / "part.geo"
    inp = dest / "part.inp"
    _write_geo(stl_path, geo, char, order=2)
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
        _write_geo(stl_path, geo, char, order=1)
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
        return handoff
    spec = extra.get("fea_bcs") if isinstance(extra.get("fea_bcs"), dict) else extra
    nodes = _parse_nodes(inp)
    resolved = _append_heat_ccx(inp, status, nodes, spec, extra)
    (dest / "bcs.json").write_text(json.dumps(resolved, indent=2, default=str), encoding="utf-8")
    if not probe["calculix"]["available"]:
        handoff["error"] = "Gmsh wrote a thermal INP, but CalculiX `ccx` is not on PATH."
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
    parsed = _parse_nt(dat) if dat.is_file() else {}
    t_max_c = parsed.get("T_max_C")
    service = check_service_temp(
        status.get("material") or {},
        {"operating_temp_c": t_max_c} if t_max_c is not None else extra,
    )
    iterate = []
    if service.get("status") == "fail":
        iterate.append({"param": "material_id", "reason": service.get("message")})
    return {
        "ok": ccx_run.returncode == 0 and bool(parsed),
        "kind": "thermal",
        "solver": "calculix_heat",
        "handoff": str(dest),
        "inp": str(inp),
        "element": _element_kind(inp),
        "nodes": len(nodes),
        "T_max_C": t_max_c,
        "T_min_C": parsed.get("T_min_C"),
        "T_max_K": parsed.get("T_max_K"),
        "service_temp": service,
        "iterate": iterate,
        "stdout": (ccx_run.stdout or "")[-1500:],
        "disclaimer": DISCLAIMER + " Fixed-NT or nodal CFLUX on the load set; FILM on the fixture set.",
        "error": None if ccx_run.returncode == 0 else (ccx_run.stderr or "ccx failed")[-2000:],
    }


def run_thermal(status: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    handbook = run_thermal_handbook(status, extra)
    probe = probe_fea()
    fea = None
    if probe["available"] and ((status.get("part") or {}).get("files") or {}).get("stl_m"):
        fea = run_thermal_fea(status, extra)
    payload = {
        "ok": bool(handbook.get("ok") or (fea and fea.get("ok"))),
        "kind": "thermal",
        "solver": (fea or {}).get("solver") if (fea and fea.get("ok")) else "handbook",
        "handbook": handbook,
        "fea": fea,
        "iterate": list(handbook.get("iterate") or []) + list((fea or {}).get("iterate") or []),
        "service_temp": (fea or {}).get("service_temp") or handbook.get("service_temp"),
        "disclaimer": DISCLAIMER,
        "probe": probe,
        "error": None
        if handbook.get("ok")
        else (handbook.get("error") or (fea or {}).get("error")),
    }
    sim = Path((status.get("paths") or {}).get("sim") or ".")
    dest = sim / "thermal"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "last.json").write_text(json.dumps(payload, indent=2, default=str)[:200000], encoding="utf-8")
    return payload


def probe_thermal() -> dict[str, Any]:
    fea = probe_fea()
    return {
        "available": True,
        "handbook": True,
        "fea": fea,
        "label": (
            "Lumped handbook (always) + CalculiX *HEAT TRANSFER if gmsh/ccx exist. "
            "service_temp_c is enforced when an operating temperature is known."
        ),
        "install_hint": fea.get("install_hint") if not fea.get("available") else None,
    }
