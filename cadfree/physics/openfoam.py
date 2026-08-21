"""OpenFOAM external-aero case from the SI STL. CadQuery does not run CFD.

Always writes a simpleFoam/snappyHexMesh template around the part. If
`simpleFoam` is on PATH we try a short steady run and parse forceCoeffs.
Missing engines, failed snappy, or a missing Cd are named — never invented.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any


def probe_openfoam() -> dict[str, Any]:
    names = {
        "blockMesh": shutil.which("blockMesh"),
        "snappyHexMesh": shutil.which("snappyHexMesh"),
        "simpleFoam": shutil.which("simpleFoam"),
        "potentialFoam": shutil.which("potentialFoam"),
        "surfaceFeatureExtract": shutil.which("surfaceFeatureExtract"),
    }
    present = {k: v for k, v in names.items() if v}
    return {
        "available": bool(names["simpleFoam"] or names["potentialFoam"]),
        "can_mesh": bool(names["blockMesh"] and names["snappyHexMesh"]),
        "engines": present,
        "all": names,
        "label": "OpenFOAM simpleFoam external aero (k-ω SST) if installed; else the case template only",
        "install_hint": "No simpleFoam on PATH. Handbook Cd still uses the SI snapshot. Templates land in sim/fluids/openfoam/.",
    }


def _num(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _domain(bbox_m: list[float], v_dir: tuple[float, float, float] = (1.0, 0.0, 0.0)) -> dict[str, Any]:
    sx, sy, sz = (float(bbox_m[0] or 0.04), float(bbox_m[1] or 0.04), float(bbox_m[2] or 0.02))
    span = max(sx, sy, sz, 0.01)
    # Far-field ~5× the part, extra wake downstream of +x flow.
    xmin, xmax = -3.0 * span, 8.0 * span
    ymin, ymax = -4.0 * span, 4.0 * span
    zmin, zmax = -4.0 * span, 4.0 * span
    _ = v_dir
    return {
        "xmin": xmin,
        "xmax": xmax,
        "ymin": ymin,
        "ymax": ymax,
        "zmin": zmin,
        "zmax": zmax,
        "span": span,
        "nx": 20,
        "ny": 16,
        "nz": 16,
        "lRef": span,
        "Aref": max(sy * sz, 1e-6),
    }


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip("\n"), encoding="utf-8")


def write_openfoam_case(
    dest: Path,
    *,
    stl: Path | None,
    bbox_m: list[float],
    v_ms: float,
    rho: float,
    nu: float,
    end_time: int = 50,
) -> dict[str, Any]:
    dest.mkdir(parents=True, exist_ok=True)
    tri = dest / "constant" / "triSurface"
    tri.mkdir(parents=True, exist_ok=True)
    geom = None
    if stl and stl.is_file():
        target = tri / "part.stl"
        if stl.resolve() != target.resolve():
            shutil.copy2(stl, target)
        geom = str(target)
    dom = _domain(bbox_m)
    u = max(float(v_ms), 0.1)
    nu = max(float(nu), 1e-7)
    foam_files = {
        "system/controlDict": f"""
FoamFile {{ version 2.0; format ascii; class dictionary; object controlDict; }}
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {int(end_time)};
deltaT          1;
writeControl    timeStep;
writeInterval   {int(end_time)};
purgeWrite      1;
writeFormat     ascii;
writePrecision  6;
runTimeModifiable true;
functions
{{
    forceCoeffs1
    {{
        type            forceCoeffs;
        libs            (forces);
        patches         (part);
        rho             rhoInf;
        rhoInf          {rho:.6g};
        CofR            (0 0 0);
        liftDir         (0 0 1);
        dragDir         (1 0 0);
        pitchAxis       (0 1 0);
        magUInf         {u:.6g};
        lRef            {dom['lRef']:.6g};
        Aref            {dom['Aref']:.6g};
        writeControl    timeStep;
        writeInterval   1;
    }}
}}
""",
        "system/fvSchemes": """
FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,k)      bounded Gauss upwind;
    div(phi,omega)  bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
""",
        "system/fvSolution": """
FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }
solvers
{
    p { solver GAMG; tolerance 1e-5; relTol 0.1; smoother GaussSeidel; }
    "(U|k|omega)" { solver smoothSolver; smoother GaussSeidel; tolerance 1e-5; relTol 0.1; }
}
SIMPLE { nNonOrthogonalCorrectors 0; residualControl { p 1e-3; U 1e-4; "(k|omega)" 1e-4; } }
relaxationFactors { equations { U 0.7; k 0.7; omega 0.7; } fields { p 0.3; } }
""",
        "system/blockMeshDict": f"""
FoamFile {{ version 2.0; format ascii; class dictionary; object blockMeshDict; }}
convertToMeters 1;
vertices
(
    ({dom['xmin']:.6g} {dom['ymin']:.6g} {dom['zmin']:.6g})
    ({dom['xmax']:.6g} {dom['ymin']:.6g} {dom['zmin']:.6g})
    ({dom['xmax']:.6g} {dom['ymax']:.6g} {dom['zmin']:.6g})
    ({dom['xmin']:.6g} {dom['ymax']:.6g} {dom['zmin']:.6g})
    ({dom['xmin']:.6g} {dom['ymin']:.6g} {dom['zmax']:.6g})
    ({dom['xmax']:.6g} {dom['ymin']:.6g} {dom['zmax']:.6g})
    ({dom['xmax']:.6g} {dom['ymax']:.6g} {dom['zmax']:.6g})
    ({dom['xmin']:.6g} {dom['ymax']:.6g} {dom['zmax']:.6g})
);
blocks ( hex (0 1 2 3 4 5 6 7) ({dom['nx']} {dom['ny']} {dom['nz']}) simpleGrading (1 1 1) );
boundary
(
    inlet  {{ type patch; faces ((0 4 7 3)); }}
    outlet {{ type patch; faces ((1 2 6 5)); }}
    ground {{ type wall;  faces ((0 1 5 4)); }}
    sky    {{ type patch; faces ((3 7 6 2)); }}
    sides  {{ type patch; faces ((0 3 2 1) (4 5 6 7)); }}
);
""",
        "system/snappyHexMeshDict": f"""
FoamFile {{ version 2.0; format ascii; class dictionary; object snappyHexMeshDict; }}
castellatedMesh true;
snap            true;
addLayers       false;
geometry {{ part.stl {{ type triSurfaceMesh; name part; }} }}
castellatedMeshControls
{{
    maxLocalCells 200000;
    maxGlobalCells 200000;
    minRefinementCells 0;
    nCellsBetweenLevels 2;
    resolveFeatureAngle 30;
    locationInMesh ({0.6 * dom['span']:.6g} {0.6 * dom['span']:.6g} {0.6 * dom['span']:.6g});
    features ();
    refinementSurfaces {{ part {{ level (1 2); }} }}
    refinementRegions {{}}
    allowFreeStandingZoneFaces true;
}}
snapControls {{ nSmoothPatch 3; tolerance 2.0; nSolveIter 30; nRelaxIter 5; }}
addLayersControls {{ relativeSizes true; layers {{}} expansionRatio 1.0; finalLayerThickness 0.3; minThickness 0.1; }}
meshQualityControls {{ maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; minVol 1e-13; minTetQuality 1e-9; minTwist 0.02; minDeterminant 0.001; minFaceWeight 0.05; minVolRatio 0.01; minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75; }}
mergeTolerance 1e-6;
""",
        "constant/transportProperties": f"""
FoamFile {{ version 2.0; format ascii; class dictionary; object transportProperties; }}
transportModel  Newtonian;
nu              [0 2 -1 0 0 0 0] {nu:.6g};
""",
        "constant/turbulenceProperties": """
FoamFile { version 2.0; format ascii; class dictionary; object turbulenceProperties; }
simulationType RAS;
RAS { RASModel kOmegaSST; turbulence on; printCoeffs on; }
""",
        "0/U": f"""
FoamFile {{ version 2.0; format ascii; class volVectorField; object U; }}
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform ({u:.6g} 0 0);
boundaryField
{{
    inlet  {{ type fixedValue; value uniform ({u:.6g} 0 0); }}
    outlet {{ type inletOutlet; inletValue uniform (0 0 0); value uniform ({u:.6g} 0 0); }}
    ground {{ type noSlip; }}
    sky    {{ type slip; }}
    sides  {{ type slip; }}
    part   {{ type noSlip; }}
}}
""",
        "0/p": """
FoamFile { version 2.0; format ascii; class volScalarField; object p; }
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    inlet  { type zeroGradient; }
    outlet { type fixedValue; value uniform 0; }
    ground { type zeroGradient; }
    sky    { type zeroGradient; }
    sides  { type zeroGradient; }
    part   { type zeroGradient; }
}
""",
        "0/k": f"""
FoamFile {{ version 2.0; format ascii; class volScalarField; object k; }}
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform {max(1.5 * (0.05 * u) ** 2, 1e-4):.6g};
boundaryField
{{
    inlet  {{ type fixedValue; value $internalField; }}
    outlet {{ type inletOutlet; inletValue $internalField; value $internalField; }}
    ground {{ type kqRWallFunction; value $internalField; }}
    sky    {{ type zeroGradient; }}
    sides  {{ type zeroGradient; }}
    part   {{ type kqRWallFunction; value $internalField; }}
}}
""",
        "0/omega": f"""
FoamFile {{ version 2.0; format ascii; class volScalarField; object omega; }}
dimensions      [0 0 -1 0 0 0 0];
internalField   uniform {max(math.sqrt(max(1.5 * (0.05 * u) ** 2, 1e-4)) / max(0.09 * dom['span'], 1e-4), 1.0):.6g};
boundaryField
{{
    inlet  {{ type fixedValue; value $internalField; }}
    outlet {{ type inletOutlet; inletValue $internalField; value $internalField; }}
    ground {{ type omegaWallFunction; value $internalField; }}
    sky    {{ type zeroGradient; }}
    sides  {{ type zeroGradient; }}
    part   {{ type omegaWallFunction; value $internalField; }}
}}
""",
        "0/nut": """
FoamFile { version 2.0; format ascii; class volScalarField; object nut; }
dimensions      [0 2 -1 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    inlet  { type calculated; value uniform 0; }
    outlet { type calculated; value uniform 0; }
    ground { type nutkWallFunction; value uniform 0; }
    sky    { type calculated; value uniform 0; }
    sides  { type calculated; value uniform 0; }
    part   { type nutkWallFunction; value uniform 0; }
}
""",
        "Allrun": """#!/bin/sh
cd "${0%/*}" || exit 1
blockMesh || exit 1
if command -v snappyHexMesh >/dev/null 2>&1 && [ -f constant/triSurface/part.stl ]; then
    snappyHexMesh -overwrite || exit 1
fi
if command -v simpleFoam >/dev/null 2>&1; then
    simpleFoam
else
    echo "simpleFoam not on PATH" >&2
    exit 2
fi
""",
        "README.txt": (
            "Cadfree OpenFOAM external-aero template. Steady simpleFoam, k-ω SST, "
            "coarse snappy around part.stl. Not a wind-tunnel certificate. "
            "Source OpenFOAM, then ./Allrun from this folder.\n"
        ),
    }
    for rel, text in foam_files.items():
        _write(dest / rel, text)
    allrun = dest / "Allrun"
    allrun.chmod(allrun.stat().st_mode | stat.S_IEXEC)
    card = {
        "units": "SI",
        "geometry": geom,
        "U_ms": u,
        "rho": rho,
        "nu": nu,
        "domain": dom,
        "end_time": end_time,
        "disclaimer": "Coarse RANS template. Cd is only real if forceCoeffs was parsed from a completed simpleFoam run.",
    }
    (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    return {"dir": str(dest), "geometry": geom, "domain": dom, "files": sorted(foam_files)}


def _parse_force_coeffs(case: Path) -> dict[str, Any] | None:
    # OpenFOAM writes postProcessing/forceCoeffs1/<time>/coefficient.dat
    root = case / "postProcessing"
    if not root.is_dir():
        return None
    files = sorted(root.rglob("coefficient.dat")) + sorted(root.rglob("forceCoeffs.dat"))
    if not files:
        return None
    last = files[-1]
    rows = []
    for raw in last.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        parts = raw.split()
        nums = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                continue
        if len(nums) >= 3:
            rows.append(nums)
    if not rows:
        return None
    # Typical columns: Time Cd Cl Cm ...
    last_row = rows[-1]
    cd = last_row[1] if len(last_row) > 1 else None
    cl = last_row[2] if len(last_row) > 2 else None
    return {"Cd": cd, "Cl": cl, "file": str(last), "n_samples": len(rows)}


def run_openfoam(
    status: dict[str, Any],
    extra: dict[str, Any] | None = None,
    *,
    timeout: int = 90,
    try_run: bool = True,
) -> dict[str, Any]:
    extra = extra or {}
    probe = probe_openfoam()
    sim = Path((status.get("paths") or {}).get("sim") or ".")
    dest = sim / "fluids" / "openfoam"
    files = (status.get("part") or {}).get("files") or {}
    stl = Path(files["stl_m"]) if files.get("stl_m") else None
    bbox = (status.get("part") or {}).get("bbox_m") or [0.04, 0.04, 0.02]
    env = status.get("environment") or {}
    inputs = status.get("inputs") or {}
    v = _num(extra.get("v") or env.get("v_ms") or inputs.get("v"), 10.0)
    rho = _num(extra.get("rho") or env.get("rho") or inputs.get("rho"), 1.225)
    mu = _num(extra.get("mu_visc") or env.get("mu_visc") or inputs.get("mu_visc"), 1.81e-5)
    nu = mu / max(rho, 1e-9)
    written = write_openfoam_case(dest, stl=stl, bbox_m=list(bbox), v_ms=v, rho=rho, nu=nu)
    payload: dict[str, Any] = {
        "ok": False,
        "kind": "cfd",
        "solver": "openfoam_simpleFoam",
        "handoff": str(dest),
        "geometry": written.get("geometry"),
        "probe": probe,
        "template": {"files": written.get("files"), "domain": written.get("domain")},
        "disclaimer": (
            "OpenFOAM simpleFoam k-ω SST on a coarse snappy mesh of the SI STL. "
            "Not a wind tunnel, not a converged y+ study. Cd is reported only if forceCoeffs exists."
        ),
    }
    if not stl or not stl.is_file():
        payload["error"] = "No SI STL — template written without geometry. build_model first."
        return payload
    if not try_run or not probe["available"]:
        payload["ok"] = True
        payload["ran"] = False
        payload["error"] = None if probe["available"] else probe["install_hint"]
        return payload
    env_os = os.environ.copy()
    logs: dict[str, str] = {}
    steps: list[tuple[str, list[str]]] = []
    if probe["engines"].get("blockMesh"):
        steps.append(("blockMesh", [probe["engines"]["blockMesh"]]))
    if probe["engines"].get("snappyHexMesh") and written.get("geometry"):
        steps.append(("snappyHexMesh", [probe["engines"]["snappyHexMesh"], "-overwrite"]))
    solver = probe["engines"].get("simpleFoam") or probe["engines"].get("potentialFoam")
    if solver:
        steps.append(("solver", [solver]))
    ran_ok = True
    for name, cmd in steps:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(dest),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env_os,
            )
        except subprocess.TimeoutExpired:
            payload["error"] = f"{name} timed out after {timeout}s"
            payload["ran"] = True
            payload["logs"] = logs
            return payload
        logs[name] = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-2500:]
        if proc.returncode != 0:
            ran_ok = False
            payload["error"] = f"{name} exited {proc.returncode}. See logs. Template is still in {dest}."
            break
    coeffs = _parse_force_coeffs(dest)
    payload["ran"] = True
    payload["logs"] = logs
    payload["forceCoeffs"] = coeffs
    if coeffs and coeffs.get("Cd") is not None:
        payload["ok"] = True
        payload["Cd"] = coeffs["Cd"]
        payload["Cl"] = coeffs.get("Cl")
        payload["error"] = None
    else:
        payload["ok"] = ran_ok
        if ran_ok and not coeffs:
            payload["error"] = (
                "simpleFoam finished but forceCoeffs was not parsed — Cd is not reported. "
                "Do not invent it; use the handbook drag_force worksheet."
            )
    return payload
