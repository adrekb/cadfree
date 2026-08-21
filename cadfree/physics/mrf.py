"""OpenFOAM MRF rotating-frame template for an impeller. Not TurboGrid.

Writes sim/fluids/mrf/ with MRFProperties, rotatingWallVelocity, and a
topoSet cylinder of radius r2 about +Z. The STL is the full 360° solid —
not a periodic sector. Head / Cd are reported only if fields parse from a
real run. Missing simpleFoam is named; numbers are never invented.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

DISCLAIMER = (
    "OpenFOAM MRF template on the full impeller STL (not a periodic sector, "
    "not TurboGrid). rotatingWallVelocity + cylinder cellZone about +Z. "
    "Head is not claimed from this template; parse p/U or forceCoeffs only if they exist."
)


def _num(raw: Any) -> float | None:
    try:
        if raw in (None, ""):
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _omega(status: dict[str, Any], extra: dict[str, Any] | None) -> float:
    extra = extra or {}
    for src in (extra, status.get("environment") or {}, status.get("inputs") or {}, status.get("constraints") or {}):
        if not isinstance(src, dict):
            continue
        rpm = _num(src.get("n_rpm") if src.get("n_rpm") is not None else src.get("rpm"))
        if rpm is not None:
            return 2.0 * math.pi * rpm / 60.0
        om = _num(src.get("omega"))
        if om is not None:
            return om
    return 0.0


def _r2(status: dict[str, Any], extra: dict[str, Any] | None) -> float:
    extra = extra or {}
    params = (status.get("cadquery") or {}).get("params_mm") or {}
    for src in (extra, params, status.get("inputs") or {}):
        if not isinstance(src, dict):
            continue
        mm = _num(src.get("r2_mm"))
        if mm is not None:
            return mm / 1000.0 if mm > 1.0 else mm
        r = _num(src.get("r2") if src.get("r2") is not None else src.get("r2_m"))
        if r is not None:
            return r
    bbox = (status.get("part") or {}).get("bbox_m") or [0.1, 0.1, 0.02]
    return max(float(bbox[0] or 0.05), float(bbox[1] or 0.05)) / 2.0


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip("\n"), encoding="utf-8")


def write_mrf_case(
    dest: Path,
    *,
    stl: Path | None,
    omega: float,
    r2: float,
    rho: float,
    nu: float,
) -> dict[str, Any]:
    dest.mkdir(parents=True, exist_ok=True)
    tri = dest / "constant" / "triSurface"
    tri.mkdir(parents=True, exist_ok=True)
    geom = None
    if stl and stl.is_file():
        target = tri / "impeller.stl"
        if stl.resolve() != target.resolve():
            shutil.copy2(stl, target)
        geom = str(target)
    om = max(float(omega), 0.0)
    radius = max(float(r2), 1e-3)
    _write(
        dest / "constant" / "MRFProperties",
        f"""
FoamFile {{ version 2.0; format ascii; class dictionary; object MRFProperties; }}
// Full 360° STL, not a periodic blade sector. Axis +Z through the origin.
MRF1
{{
    cellZone        rotating;
    active          yes;
    nonRotatingPatches ();
    origin          (0 0 0);
    axis            (0 0 1);
    omega           {om:.6g};
}}
""",
    )
    _write(
        dest / "0" / "U",
        f"""
FoamFile {{ version 2.0; format ascii; class volVectorField; object U; }}
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);
boundaryField
{{
    impeller
    {{
        type            rotatingWallVelocity;
        origin          (0 0 0);
        axis            (0 0 1);
        omega           {om:.6g};
        value           uniform (0 0 0);
    }}
    inlet
    {{
        type            pressureInletOutletVelocity;
        value           uniform (0 0 0);
    }}
    outlet
    {{
        type            inletOutlet;
        inletValue      uniform (0 0 0);
        value           uniform (0 0 0);
    }}
    walls
    {{
        type            noSlip;
    }}
}}
""",
    )
    _write(
        dest / "system" / "topoSetDict",
        f"""
FoamFile {{ version 2.0; format ascii; class dictionary; object topoSetDict; }}
actions
(
    {{
        name    rotating;
        type    cellSet;
        action  new;
        source  cylinderToCell;
        p1      (0 0 {-2.0 * radius:.6g});
        p2      (0 0 {2.0 * radius:.6g});
        radius  {radius:.6g};
    }}
    {{
        name    rotating;
        type    cellZoneSet;
        action  new;
        source  setToCellZone;
        set     rotating;
    }}
);
""",
    )
    _write(
        dest / "constant" / "transportProperties",
        f"""
FoamFile {{ version 2.0; format ascii; class dictionary; object transportProperties; }}
transportModel  Newtonian;
nu              {max(float(nu), 1e-7):.6g};
rho             {max(float(rho), 1.0):.6g};
""",
    )
    _write(
        dest / "system" / "controlDict",
        """
FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         1;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
purgeWrite      1;
writeFormat     ascii;
runTimeModifiable true;
""",
    )
    note = dest / "README.txt"
    note.write_text(
        "Cadfree MRF handoff. Full impeller STL, cylinder cellZone radius r2 about +Z.\n"
        "Not a periodic sector. Not TurboGrid. Do not report head unless p/U parsed from a run.\n",
        encoding="utf-8",
    )
    return {"geometry": geom, "omega": om, "r2": radius}


def run_mrf(status: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    sim = Path((status.get("paths") or {}).get("sim") or ".")
    dest = sim / "fluids" / "mrf"
    files = (status.get("part") or {}).get("files") or {}
    stl = files.get("stl_m")
    stl_path = Path(stl) if stl else None
    env = status.get("environment") or {}
    rho = float(env.get("rho") or (status.get("inputs") or {}).get("rho") or 997.0)
    mu = float(env.get("mu_visc") or 1.0e-3)
    nu = mu / max(rho, 1e-9)
    omega = _omega(status, extra)
    r2 = _r2(status, extra)
    written = write_mrf_case(dest, stl=stl_path if stl_path and stl_path.is_file() else None, omega=omega, r2=r2, rho=rho, nu=nu)
    simple = shutil.which("simpleFoam")
    card = {
        "ok": False,
        "kind": "fluids",
        "solver": "openfoam_mrf",
        "handoff": str(dest),
        "geometry": written.get("geometry"),
        "omega_rad_s": omega,
        "r2_m": r2,
        "probe": {"simpleFoam": simple, "available": bool(simple)},
        "iterate": [],
        "disclaimer": DISCLAIMER,
        "error": None if simple else (
            "No simpleFoam on PATH. MRF template written in sim/fluids/mrf/. "
            "Not inventing head or Cd from external aero."
        ),
        "assumptions": [
            "Full 360° STL, not a periodic blade sector.",
            "MRF cellZone is a cylinder of radius r2 about +Z.",
            "rotatingWallVelocity on patch impeller.",
        ],
    }
    if omega <= 0:
        card["error"] = "n_rpm missing — ask_survey. MRF template written with ω=0."
    # Never copy Cd from a non-rotating simpleFoam case.
    (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    return card
