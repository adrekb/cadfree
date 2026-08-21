"""OpenFOAM MRF rotating-frame case for an impeller — runnable, not a fragment.

Follows the stock simpleFoam MRF pattern (mixerVessel2D / closedVolumeRotating):
blockMesh box domain → snappyHexMesh castellate+snap on the full 360° STL →
topoSet cylinderToCell rotor zone → simpleFoam with MRFProperties. Inlet flow
is the surveyed Q (flowRateInletVelocity); head is the area-averaged static
pressure rise parsed from surfaceFieldValue output; shaft torque is the forces
moment about +Z. Numbers are reported only if the run produced them.

Named assumptions: no volute (open radial discharge — impeller-only head,
optimistic vs a real casing), coarse castellated mesh without boundary layers,
laminar. Not TurboGrid, not a periodic sector, not a pump test stand.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cadfree.physics.book import G

DISCLAIMER = (
    "OpenFOAM MRF on the full impeller STL: blockMesh + snappyHexMesh + "
    "topoSet rotor + simpleFoam. No volute — open radial discharge, so head "
    "is impeller-only and optimistic. Coarse castellated mesh, laminar, "
    "static head from area-averaged p. Not a pump curve, not a test stand."
)


def probe_mrf() -> dict[str, Any]:
    names = {
        "blockMesh": shutil.which("blockMesh"),
        "snappyHexMesh": shutil.which("snappyHexMesh"),
        "topoSet": shutil.which("topoSet"),
        "simpleFoam": shutil.which("simpleFoam"),
    }
    present = {k: v for k, v in names.items() if v}
    return {
        "available": all(names.values()),
        "engines": present,
        "all": names,
        "label": "blockMesh → snappyHexMesh → topoSet → simpleFoam MRF if all four are on PATH",
        "install_hint": (
            "OpenFOAM chain incomplete (need blockMesh, snappyHexMesh, topoSet, simpleFoam). "
            "The full case + Allrun still land in sim/fluids/mrf/."
        ),
    }


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


def _flow_q(status: dict[str, Any], extra: dict[str, Any] | None) -> float | None:
    extra = extra or {}
    for src in (extra, status.get("inputs") or {}, status.get("constraints") or {}):
        if not isinstance(src, dict):
            continue
        for key, scale in (("Q_m3s", 1.0), ("Q", 1.0), ("Q_lpm", 1.0 / 60000.0), ("Q_gpm", 6.309e-5)):
            val = _num(src.get(key))
            if val is not None:
                return val * scale
    return None


def _r2(status: dict[str, Any], extra: dict[str, Any] | None) -> float:
    extra = extra or {}
    params = (status.get("cadquery") or {}).get("params_mm") or {}
    for src in (extra, params):
        if not isinstance(src, dict):
            continue
        mm = _num(src.get("r2_mm"))
        if mm is not None:
            return mm / 1000.0
    for src in (extra, status.get("inputs") or {}):
        if not isinstance(src, dict):
            continue
        r = _num(src.get("r2") if src.get("r2") is not None else src.get("r2_m"))
        if r is not None:
            return r
    bbox = (status.get("part") or {}).get("bbox_m") or [0.1, 0.1, 0.02]
    return max(float(bbox[0] or 0.05), float(bbox[1] or 0.05)) / 2.0


def _blade_height(status: dict[str, Any], r2: float) -> float:
    params = (status.get("cadquery") or {}).get("params_mm") or {}
    h = _num(params.get("hub_h_mm"))
    if h:
        return h / 1000.0
    bbox = (status.get("part") or {}).get("bbox_m") or []
    if len(bbox) >= 3 and bbox[2]:
        return float(bbox[2])
    return 0.3 * r2


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip("\n"), encoding="utf-8")


def _hdr(cls: str, obj: str, location: str = "") -> str:
    loc = f'    location    "{location}";\n' if location else ""
    return (
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        f"    class       {cls};\n{loc}    object      {obj};\n}}\n"
    )


def write_mrf_case(
    dest: Path,
    *,
    stl: Path | None,
    omega: float,
    r2: float,
    blade_h: float,
    q_m3s: float | None,
    rho: float,
    nu: float,
    iterations: int = 200,
) -> dict[str, Any]:
    """Write the complete runnable case. Returns paths + named gaps."""
    dest.mkdir(parents=True, exist_ok=True)
    geom = None
    if stl and stl.is_file():
        target = dest / "constant" / "triSurface" / "impeller.stl"
        target.parent.mkdir(parents=True, exist_ok=True)
        if stl.resolve() != target.resolve():
            shutil.copy2(stl, target)
        geom = str(target)
    om = max(float(omega), 0.0)
    r = max(float(r2), 1e-3)
    bh = max(float(blade_h), 1e-3)
    # Box domain: 2.5 r2 half-width, eye inlet on top, radial discharge on sides.
    half = 2.5 * r
    zmin, zmax = -1.5 * bh, 4.0 * bh
    rotor_r = 1.15 * r
    n_iter = max(int(iterations), 50)

    _write(
        dest / "system" / "blockMeshDict",
        f"""
{_hdr("dictionary", "blockMeshDict", "system")}
scale 1;
vertices
(
    (-{half:.6g} -{half:.6g} {zmin:.6g})
    ( {half:.6g} -{half:.6g} {zmin:.6g})
    ( {half:.6g}  {half:.6g} {zmin:.6g})
    (-{half:.6g}  {half:.6g} {zmin:.6g})
    (-{half:.6g} -{half:.6g} {zmax:.6g})
    ( {half:.6g} -{half:.6g} {zmax:.6g})
    ( {half:.6g}  {half:.6g} {zmax:.6g})
    (-{half:.6g}  {half:.6g} {zmax:.6g})
);
blocks
(
    hex (0 1 2 3 4 5 6 7) (24 24 20) simpleGrading (1 1 1)
);
boundary
(
    inlet
    {{
        type patch;
        faces ((4 5 6 7));
    }}
    outlet
    {{
        type patch;
        faces
        (
            (0 1 5 4)
            (1 2 6 5)
            (2 3 7 6)
            (3 0 4 7)
        );
    }}
    walls
    {{
        type wall;
        faces ((0 3 2 1));
    }}
);
""",
    )
    _write(
        dest / "system" / "snappyHexMeshDict",
        f"""
{_hdr("dictionary", "snappyHexMeshDict", "system")}
castellatedMesh true;
snap            true;
addLayers       false;

geometry
{{
    impeller
    {{
        type triSurfaceMesh;
        file "impeller.stl";
    }}
}}

castellatedMeshControls
{{
    maxLocalCells 200000;
    maxGlobalCells 1000000;
    minRefinementCells 0;
    nCellsBetweenLevels 2;
    features ();
    refinementSurfaces
    {{
        impeller
        {{
            level (1 2);
        }}
    }}
    resolveFeatureAngle 30;
    refinementRegions {{}}
    locationInMesh ({1.8 * r:.6g} {1.8 * r:.6g} {0.6 * zmax:.6g});
    allowFreeStandingZoneFaces true;
}}

snapControls
{{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 30;
    nRelaxIter 5;
}}

addLayersControls
{{
    relativeSizes true;
    layers {{}}
    expansionRatio 1.2;
    finalLayerThickness 0.3;
    minThickness 0.1;
    nGrow 0;
    featureAngle 60;
    nRelaxIter 3;
    nSmoothSurfaceNormals 1;
    nSmoothNormals 3;
    nSmoothThickness 10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.3;
    minMedianAxisAngle 90;
    nBufferCellsNoExtrude 0;
    nLayerIter 50;
}}

meshQualityControls
{{
    maxNonOrtho 65;
    maxBoundarySkewness 20;
    maxInternalSkewness 4;
    maxConcave 80;
    minVol 1e-13;
    minTetQuality 1e-15;
    minArea -1;
    minTwist 0.02;
    minDeterminant 0.001;
    minFaceWeight 0.02;
    minVolRatio 0.01;
    minTriangleTwist -1;
    nSmoothScale 4;
    errorReduction 0.75;
}}

writeFlags (scalarLevels);
mergeTolerance 1e-6;
""",
    )
    _write(
        dest / "system" / "topoSetDict",
        f"""
{_hdr("dictionary", "topoSetDict", "system")}
// Rotor = cylinder 1.15 x r2 about +Z spanning the blades. Not a snapped
// rotatingZone surface — a named approximation.
actions
(
    {{
        name    rotor;
        type    cellSet;
        action  new;
        source  cylinderToCell;
        p1      (0 0 {zmin:.6g});
        p2      (0 0 {1.5 * bh:.6g});
        radius  {rotor_r:.6g};
    }}
    {{
        name    rotor;
        type    cellZoneSet;
        action  new;
        source  setToCellZone;
        set     rotor;
    }}
);
""",
    )
    _write(
        dest / "constant" / "MRFProperties",
        f"""
{_hdr("dictionary", "MRFProperties", "constant")}
MRF1
{{
    cellZone    rotor;
    active      yes;
    nonRotatingPatches ();
    origin      (0 0 0);
    axis        (0 0 1);
    omega       {om:.6g};
}}
""",
    )
    _write(
        dest / "constant" / "transportProperties",
        f"""
{_hdr("dictionary", "transportProperties", "constant")}
transportModel  Newtonian;
nu              {max(float(nu), 1e-7):.6g};
""",
    )
    _write(
        dest / "constant" / "turbulenceProperties",
        f"""
{_hdr("dictionary", "turbulenceProperties", "constant")}
// Laminar on a coarse castellated mesh — named. kOmegaSST without boundary
// layers would be theatre, not turbulence modelling.
simulationType laminar;
""",
    )
    q = q_m3s if q_m3s and q_m3s > 0 else None
    inlet_u = (
        f"""
    inlet
    {{
        type            flowRateInletVelocity;
        volumetricFlowRate constant {q:.6g};
        value           uniform (0 0 0);
    }}"""
        if q
        else """
    inlet
    {
        type            pressureInletOutletVelocity;
        value           uniform (0 0 0);
    }"""
    )
    _write(
        dest / "0" / "U",
        f"""
{_hdr("volVectorField", "U", "0")}
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);
boundaryField
{{{inlet_u}
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
    impeller
    {{
        type            rotatingWallVelocity;
        origin          (0 0 0);
        axis            (0 0 1);
        omega           {om:.6g};
    }}
}}
""",
    )
    _write(
        dest / "0" / "p",
        f"""
{_hdr("volScalarField", "p", "0")}
// Kinematic pressure p/rho (m^2/s^2), incompressible simpleFoam convention.
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{{
    inlet
    {{
        type            zeroGradient;
    }}
    outlet
    {{
        type            fixedValue;
        value           uniform 0;
    }}
    walls
    {{
        type            zeroGradient;
    }}
    impeller
    {{
        type            zeroGradient;
    }}
}}
""",
    )
    _write(
        dest / "system" / "fvSchemes",
        f"""
{_hdr("dictionary", "fvSchemes", "system")}
ddtSchemes {{ default steadyState; }}
gradSchemes {{ default Gauss linear; }}
divSchemes
{{
    default         none;
    div(phi,U)      bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}}
laplacianSchemes {{ default Gauss linear corrected; }}
interpolationSchemes {{ default linear; }}
snGradSchemes {{ default corrected; }}
""",
    )
    _write(
        dest / "system" / "fvSolution",
        f"""
{_hdr("dictionary", "fvSolution", "system")}
solvers
{{
    p
    {{
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-6;
        relTol          0.05;
    }}
    U
    {{
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-6;
        relTol          0.1;
    }}
}}
SIMPLE
{{
    nNonOrthogonalCorrectors 1;
    consistent      yes;
    residualControl
    {{
        p               1e-4;
        U               1e-4;
    }}
}}
relaxationFactors
{{
    equations
    {{
        U               0.7;
        ".*"            0.5;
    }}
}}
""",
    )
    _write(
        dest / "system" / "controlDict",
        f"""
{_hdr("dictionary", "controlDict", "system")}
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {n_iter};
deltaT          1;
writeControl    timeStep;
writeInterval   {n_iter};
purgeWrite      1;
writeFormat     ascii;
writePrecision  8;
runTimeModifiable true;

functions
{{
    inletP
    {{
        type            surfaceFieldValue;
        libs            (fieldFunctionObjects);
        writeControl    timeStep;
        writeInterval   10;
        log             false;
        writeFields     false;
        regionType      patch;
        name            inlet;
        operation       areaAverage;
        fields          (p);
    }}
    outletP
    {{
        type            surfaceFieldValue;
        libs            (fieldFunctionObjects);
        writeControl    timeStep;
        writeInterval   10;
        log             false;
        writeFields     false;
        regionType      patch;
        name            outlet;
        operation       areaAverage;
        fields          (p);
    }}
    shaft
    {{
        type            forces;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   10;
        log             false;
        patches         (impeller);
        rho             rhoInf;
        rhoInf          {max(float(rho), 1.0):.6g};
        CofR            (0 0 0);
    }}
}}
""",
    )
    allrun = dest / "Allrun"
    allrun.write_text(
        """#!/bin/sh
# Cadfree MRF case. Full 360 STL, no volute (named). Head/torque only if parsed.
cd "$(dirname "$0")" || exit 1
blockMesh > log.blockMesh 2>&1 || exit 1
snappyHexMesh -overwrite > log.snappyHexMesh 2>&1 || exit 1
topoSet > log.topoSet 2>&1 || exit 1
simpleFoam > log.simpleFoam 2>&1 || exit 1
""",
        encoding="utf-8",
    )
    allrun.chmod(0o755)
    gaps = []
    if not geom:
        gaps.append("no SI STL — build_model first")
    if om <= 0:
        gaps.append("n_rpm missing — ask_survey")
    if not q:
        gaps.append("Q missing — inlet left pressure-driven; head will not be a duty-point head")
    return {
        "geometry": geom,
        "omega": om,
        "r2": r,
        "rotor_r": rotor_r,
        "q_m3s": q,
        "iterations": n_iter,
        "allrun": str(allrun),
        "gaps": gaps,
    }


def _last_value(dat: Path) -> float | None:
    """Last data row of a surfaceFieldValue .dat → the value column."""
    if not dat.is_file():
        return None
    last: float | None = None
    for raw in dat.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        nums = []
        for tok in line.replace("(", " ").replace(")", " ").split():
            try:
                nums.append(float(tok))
            except ValueError:
                continue
        if len(nums) >= 2:
            last = nums[-1]
    return last


def _find_dat(case: Path, fo_name: str, filename: str) -> Path | None:
    root = case / "postProcessing" / fo_name
    if not root.is_dir():
        return None
    hits = sorted(root.rglob(filename))
    return hits[-1] if hits else None


def _moment_z(dat: Path | None) -> float | None:
    """Total moment z from a forces moment.dat: time (Mx My Mz) (...) (...)."""
    if dat is None or not dat.is_file():
        return None
    last: list[float] | None = None
    for raw in dat.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        nums = []
        for tok in line.replace("(", " ").replace(")", " ").split():
            try:
                nums.append(float(tok))
            except ValueError:
                continue
        if len(nums) >= 4:
            last = nums
    if last is None:
        return None
    return last[3]


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    log = cwd / f"log.{cmd[0]}"
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        log.write_text(f"timed out after {timeout}s\n", encoding="utf-8")
        return False, f"{cmd[0]} timed out after {timeout}s"
    log.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or f"{cmd[0]} failed")[-2000:]
    return True, (proc.stdout or "")[-500:]


def run_mrf(status: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    probe = probe_mrf()
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
    blade_h = _blade_height(status, r2)
    q = _flow_q(status, extra)
    try:
        n_iter = int(extra.get("iterations") or 300)
    except (TypeError, ValueError):
        n_iter = 300
    written = write_mrf_case(
        dest,
        stl=stl_path if stl_path and stl_path.is_file() else None,
        omega=omega,
        r2=r2,
        blade_h=blade_h,
        q_m3s=q,
        rho=rho,
        nu=nu,
        iterations=n_iter,
    )
    card: dict[str, Any] = {
        "ok": False,
        "kind": "fluids",
        "solver": "openfoam_mrf",
        "handoff": str(dest),
        "geometry": written.get("geometry"),
        "omega_rad_s": omega,
        "r2_m": r2,
        "Q_m3s": q,
        "probe": probe,
        "case": {
            "chain": "blockMesh → snappyHexMesh → topoSet(rotor) → simpleFoam",
            "allrun": written.get("allrun"),
            "rotor_r_m": written.get("rotor_r"),
            "iterations": written.get("iterations"),
        },
        "iterate": [],
        "assumptions": [
            "Full 360° STL, no volute — open radial discharge; head is impeller-only and optimistic.",
            "Rotor cellZone is topoSet cylinderToCell 1.15×r2 about +Z, not a snapped rotatingZone surface.",
            "Laminar on a coarse castellated mesh (no boundary layers) — named, not hidden.",
            "simpleFoam p is kinematic (p/ρ); head H = Δp/g in metres of the working fluid.",
        ],
        "disclaimer": DISCLAIMER,
        "error": None,
    }
    gaps = written.get("gaps") or []
    hard_gaps = [g for g in gaps if "STL" in g or "n_rpm" in g]
    if hard_gaps:
        card["error"] = "; ".join(gaps)
        (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
        return card
    if gaps:
        card["assumptions"].extend(gaps)
    if not probe["available"]:
        card["error"] = probe["install_hint"] + " Run ./Allrun after installing."
        (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
        return card

    try:
        timeout = int(extra.get("timeout") or 300)
    except (TypeError, ValueError):
        timeout = 300
    steps = (
        (["blockMesh"], min(timeout, 120)),
        (["snappyHexMesh", "-overwrite"], timeout),
        (["topoSet"], min(timeout, 120)),
        (["simpleFoam"], timeout),
    )
    log: list[str] = []
    for cmd, t_out in steps:
        ok, msg = _run(cmd, dest, t_out)
        log.append(f"{cmd[0]}: {'ok' if ok else msg}")
        if not ok:
            card["error"] = f"{cmd[0]} failed — case is in sim/fluids/mrf/ for inspection. {msg}"
            card["run_log"] = log
            (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
            return card
    card["run_log"] = log

    p_in = _last_value(_find_dat(dest, "inletP", "surfaceFieldValue.dat") or Path("/nonexistent"))
    p_out = _last_value(_find_dat(dest, "outletP", "surfaceFieldValue.dat") or Path("/nonexistent"))
    mz = _moment_z(_find_dat(dest, "shaft", "moment.dat"))
    if p_in is None or p_out is None:
        card["error"] = (
            "simpleFoam ran but surfaceFieldValue output did not parse — "
            "not inventing a head. Inspect postProcessing/ in the handoff."
        )
        (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
        return card
    head_m = (float(p_out) - float(p_in)) / G  # kinematic p → metres of fluid
    card["ok"] = True
    card["p_inlet_kinematic"] = p_in
    card["p_outlet_kinematic"] = p_out
    card["head_static_m"] = head_m
    card["assumptions"].append(
        "head_static_m is the boundary-to-boundary static rise. With no volute the swirl "
        "kinetic energy is unrecovered, so this underestimates the impeller."
    )
    if mz is not None and omega > 0:
        card["moment_z_Nm"] = mz
        card["shaft_power_W"] = abs(mz) * omega
        if q:
            # Work head from parsed torque: everything the shaft puts in per unit
            # weight flow, churning losses included — an upper bound on delivered head.
            card["head_shaft_m"] = abs(mz) * omega / (rho * G * q)
            card["assumptions"].append(
                "head_shaft_m = Mz·ω/(ρgQ) from the parsed impeller moment — includes churning "
                "losses, so delivered head lies between head_static_m and head_shaft_m."
            )
    if q and head_m:
        card["hydraulic_power_W"] = rho * G * q * head_m
    target = _num((status.get("inputs") or {}).get("target_H"))
    params_mm = (status.get("cadquery") or {}).get("params_mm") or {}
    if target and card.get("head_shaft_m") is not None and float(card["head_shaft_m"]) < float(target) and "r2_mm" in params_mm:
        card["iterate"].append(
            {
                "param": "r2_mm",
                "reason": (
                    f"Even the shaft work head {card['head_shaft_m']:.3g} m < target {target:g} m — "
                    "raise r2 or n_rpm. Compare with the meanline Euler head first."
                ),
            }
        )
    elif target and card.get("head_shaft_m") is None and head_m < 0.85 * float(target) and "r2_mm" in params_mm:
        card["iterate"].append(
            {
                "param": "r2_mm",
                "reason": (
                    f"MRF static head {head_m:.3g} m < target {target:g} m on the coarse mesh — "
                    "raise r2 or n_rpm, then re-run. Compare with the meanline Euler head first."
                ),
            }
        )
    (dest / "case.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    return card
