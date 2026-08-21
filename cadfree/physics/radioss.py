"""OpenRadioss explicit rung. Burst/containment/impact — not a pump optimizer.

Writes starter+engine decks with /LOAD/CENTRI under sim/radioss/. Converts a
CalculiX INP tet mesh when one exists. ok=True only if the engine ran and
output parsed. Missing binaries are named in one sentence; numbers are never
invented. This is not Ansys CFX and not Euler head.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

DISCLAIMER = (
    "OpenRadioss explicit. /LOAD/CENTRI is a rotating pre-load, not CFD. "
    "Burst/impact is not claimed until the engine runs and output parses. "
    "Not meanline Euler head, not a pump curve."
)

_STARTER_NAMES = (
    "starter_linux64_gf",
    "starter_linux64",
    "openradioss_starter",
    "s_2023_linux64",
    "s_2022_linux64",
)
_ENGINE_NAMES = (
    "engine_linux64_gf",
    "engine_linux64",
    "openradioss_engine",
    "e_2023_linux64",
    "e_2022_linux64",
)


def probe_radioss() -> dict[str, Any]:
    starter = next((shutil.which(n) for n in _STARTER_NAMES if shutil.which(n)), None)
    engine = next((shutil.which(n) for n in _ENGINE_NAMES if shutil.which(n)), None)
    bundled = shutil.which("openradioss")
    return {
        "available": bool((starter and engine) or bundled),
        "starter": {"available": bool(starter), "path": starter},
        "engine": {"available": bool(engine), "path": engine},
        "openradioss": {"available": bool(bundled), "path": bundled},
        "label": "OpenRadioss explicit (/LOAD/CENTRI). Burst/impact if the engine runs; never a fake field.",
        "install_hint": (
            "OpenRadioss starter/engine not on PATH (starter_linux64_gf, engine_linux64_gf). "
            "Decks still land in sim/radioss/. Meanline head is pack=turbo analytical."
        ),
    }


def _omega(status: dict[str, Any], extra: dict[str, Any] | None) -> float | None:
    extra = extra or {}
    for src in (extra, status.get("environment") or {}, status.get("inputs") or {}, status.get("constraints") or {}):
        for key in ("n_rpm", "rpm", "omega"):
            raw = src.get(key) if isinstance(src, dict) else None
            if raw in (None, ""):
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if key == "omega":
                return val
            return 2.0 * math.pi * val / 60.0
    return None


def _parse_inp(inp: Path) -> tuple[list[tuple[int, float, float, float]], list[tuple[int, int, int, int, int]]]:
    nodes: list[tuple[int, float, float, float]] = []
    tets: list[tuple[int, int, int, int, int]] = []
    in_nodes = False
    in_tets = False
    eid = 0
    for raw in inp.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("*NODE"):
            in_nodes, in_tets = True, False
            continue
        if upper.startswith("*ELEMENT"):
            in_nodes = False
            in_tets = "C3D4" in upper or "C3D10" in upper or "TETRA" in upper
            continue
        if line.startswith("*"):
            in_nodes = in_tets = False
            continue
        if not line:
            continue
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if in_nodes and len(parts) >= 4:
            try:
                nodes.append((int(float(parts[0])), float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif in_tets and len(parts) >= 5:
            try:
                nid = int(float(parts[0]))
                n1, n2, n3, n4 = (int(float(parts[i])) for i in range(1, 5))
                tets.append((nid, n1, n2, n3, n4))
            except ValueError:
                continue
        elif in_tets and len(parts) == 4:
            try:
                eid += 1
                n1, n2, n3, n4 = (int(float(p)) for p in parts)
                tets.append((eid, n1, n2, n3, n4))
            except ValueError:
                continue
    return nodes, tets


def _find_inp(sim: Path) -> Path | None:
    for path in (
        sim / "fea" / "L0" / "part.inp",
        sim / "fea" / "L1" / "part.inp",
        sim / "fea" / "part.inp",
        sim / "radioss" / "part.inp",
    ):
        if path.is_file():
            return path
    return None


def _write_decks(
    dest: Path,
    *,
    nodes: list[tuple[int, float, float, float]],
    tets: list[tuple[int, int, int, int, int]],
    omega: float,
    rho: float,
    e_pa: float,
    nu: float,
) -> dict[str, str]:
    dest.mkdir(parents=True, exist_ok=True)
    run = "cadfree"
    starter = dest / f"{run}_0000.rad"
    engine = dest / f"{run}_0001.rad"
    node_block = "\n".join(f"{nid} {x:.6g} {y:.6g} {z:.6g}" for nid, x, y, z in nodes[:200000])
    tet_block = "\n".join(f"{eid} {n1} {n2} {n3} {n4}" for eid, n1, n2, n3, n4 in tets[:400000])
    mesh_note = (
        f"{len(nodes)} nodes / {len(tets)} tets converted from CalculiX INP"
        if nodes and tets
        else "No tet mesh yet — /LOAD/CENTRI is declared; do not treat this as a solved burst."
    )
    starter.write_text(
        f"""#RADIOSS STARTER
# Cadfree impeller / rotating-solid handoff. {mesh_note}
# /LOAD/CENTRI is a body load about +Z. Not CFD. Not Euler head.
/BEGIN
{run}
2022 0
0
0
/NODE
{node_block if node_block else "# (empty — run_solvers fea first so Gmsh writes an INP)"}
/TETRA4/1
{tet_block if tet_block else "# (empty)"}
/MAT/ELAST/1
impeller
{rho:.6g} {e_pa:.6g} {nu:.4g}
/PROP/SOLID/1
solid
1
/PART/1
impeller
1 1
/LOAD/CENTRI/1
centrifugal
0 0 0 0 0
{omega:.6g}
0. 0. 1.  0. 0. 0.
/END
""",
        encoding="utf-8",
    )
    engine.write_text(
        f"""#RADIOSS ENGINE
# Explicit continuation after the starter CENTRI pre-load.
# Burst / containment / impact are not wired until this engine actually runs.
/RUN/{run}/1
/TFILE
0.001
/ANIM/VECT/DISP
/STOP
0.001
""",
        encoding="utf-8",
    )
    return {"starter": str(starter), "engine": str(engine)}


def _parse_engine_out(dest: Path) -> dict[str, Any]:
    hits: dict[str, Any] = {}
    for path in dest.glob("*"):
        if path.suffix.lower() not in {".out", ".t01", ".txt", ".log"} and "T01" not in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "ERROR" in text.upper() and "ERROR 0" not in text.upper():
            hits.setdefault("log", text[-2000:])
        for line in text.splitlines():
            low = line.lower()
            if "kinetic" in low or "energie" in low or "energy" in low:
                nums = []
                for tok in line.replace(",", " ").split():
                    try:
                        nums.append(float(tok))
                    except ValueError:
                        continue
                if nums:
                    hits["energy_line"] = nums[-1]
                    hits["parsed_from"] = path.name
                    return hits
    return hits


def run_radioss(status: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    probe = probe_radioss()
    sim = Path((status.get("paths") or {}).get("sim") or ".")
    dest = sim / "radioss"
    dest.mkdir(parents=True, exist_ok=True)
    omega = _omega(status, extra) or 0.0
    mat = status.get("material") or {}
    inp = _find_inp(sim)
    nodes: list[tuple[int, float, float, float]] = []
    tets: list[tuple[int, int, int, int, int]] = []
    if inp is not None:
        nodes, tets = _parse_inp(inp)
        shutil.copy2(inp, dest / "part.inp")
    files = _write_decks(
        dest,
        nodes=nodes,
        tets=tets,
        omega=omega,
        rho=float(mat.get("rho") or 1200.0),
        e_pa=float(mat.get("E") or 2.1e9),
        nu=float(mat.get("nu") or 0.38),
    )
    card = {
        "units": "SI",
        "omega_rad_s": omega,
        "n_nodes": len(nodes),
        "n_tets": len(tets),
        "files": files,
        "note": DISCLAIMER,
    }
    (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    payload: dict[str, Any] = {
        "ok": False,
        "kind": "radioss",
        "solver": "openradioss",
        "handoff": str(dest),
        "files": files,
        "probe": probe,
        "omega_rad_s": omega,
        "mesh": {"nodes": len(nodes), "tets": len(tets), "inp": str(inp) if inp else None},
        "iterate": [],
        "disclaimer": DISCLAIMER,
    }
    if omega <= 0:
        payload["error"] = "n_rpm missing — ask_survey. /LOAD/CENTRI deck written with ω=0."
        return payload
    if not probe["available"]:
        payload["error"] = probe["install_hint"]
        return payload
    starter = probe["starter"]["path"]
    engine = probe["engine"]["path"]
    try:
        if starter:
            subprocess.run(
                [starter, "-i", files["starter"]],
                cwd=str(dest),
                capture_output=True,
                text=True,
                timeout=int(extra.get("timeout") or 120),
                check=False,
            )
        if engine:
            eng = subprocess.run(
                [engine, "-i", files["engine"]],
                cwd=str(dest),
                capture_output=True,
                text=True,
                timeout=int(extra.get("timeout") or 120),
                check=False,
            )
        else:
            payload["error"] = "OpenRadioss starter ran (or was missing); engine binary not on PATH."
            return payload
    except subprocess.TimeoutExpired:
        payload["error"] = "OpenRadioss timed out."
        return payload
    parsed = _parse_engine_out(dest)
    if parsed.get("energy_line") is None:
        payload["error"] = (
            "OpenRadioss engine ran but no energy/T-file parsed. "
            "Not inventing a burst factor. stdout: " + ((eng.stderr or eng.stdout or "")[-800:])
        )
        payload["stdout"] = (eng.stdout or "")[-1500:]
        return payload
    payload["ok"] = True
    payload["energy"] = parsed["energy_line"]
    payload["parsed_from"] = parsed.get("parsed_from")
    return payload
