# How Cadfree simulates — and what it will not fake

Text-to-CAD already exists (Zoo, CadQuery agents, FreeCAD scripts). None of them
ask the only question that matters in a shop: **can this be made on the machines
you own, with the stock you have, and will it do the job you named?**

Cadfree splits that into manufacturing constraints (bed size, nozzle, draft,
sheet-ness) and physics. Physics is layered on purpose. A 50 lb bracket on 100 g
of PLA should fail in milliseconds, not after a 20-minute CalculiX job.

## Rung 0 — first-order (always on)

Treat the part as a cantilever whose span is the longest bounding-box edge and
whose section is the remaining rectangle, shrunk by print infill. Stress is
`M c / I`. Allowable stress is the **printed** XY or Z coupon number, not the
injection-molded datasheet. Mass is volume × density × (shells + infill).

This is the same back-of-the-envelope an ME does before opening Ansys. It is
**not FEA**. It exists to refuse impossible specs and to rank materials.
`optimize_params` runs that same check on a PARAMS-scaled copy of the current
mesh (~40 evals) so the agent does not rewrite CadQuery ten times. FEA is
for verifying the winner.

## Rung 1 — MATLAB / Octave (Agent mode)

Carrot already treated MATLAB as an agent tool, with GNU Octave as the free
stand-in. Cadfree keeps that contract:

- Probe `matlab`, `octave-cli`, then `octave`.
- If none exist, say so in one sentence. Do not throw.
- The agent may run arbitrary `.m` for custom load cases, plots, or the
  generated beam script (`cadfree/simulation/pipeline.py`).

Why MATLAB at all, when Python can do the same maths? Because a lot of
mechanical engineers already think in it, university seats have it, and Agent
mode should meet them there. Octave is accepted so a missing licence is not a
product dead-end.

## Rung 2 — mesh FEA (Gmsh + CalculiX)

The open stack that actually belongs behind CadQuery:

1. CadQuery solid (OCCT) or STEP
2. **Gmsh** tetrahedral mesh, faces tagged for loads/fixtures
   ([CadQuery assembly-mesh-plugin](https://github.com/CadQuery/assembly-mesh-plugin))
3. **CalculiX `ccx`** linear static ([PyCCX](https://github.com/drlukeparry/pyccx),
   FreeCAD FEM, or a raw INP)
4. von Mises / displacement; compare to yield with a safety factor

Wiring the INP writer is in `cadfree/physics/fea.py`: the SI mesh copy
always lands in `sim/fea/`. If `gmsh` and `ccx` are on PATH, Cadfree tet-meshes
**quadratic C3D10** (linear C3D4 is the named fallback if second-order meshing
fails) and runs a linear static. Pass `values.converge=true` / `mesh_levels=3`
for a coarse→fine family plus Richardson; a single mesh is never called
"converged". Fixtures and loads come from `set_load_path`
(pick-ids on the millimetre STL, PARAMS holes, or a named bbox-face fallback).
FDM gets a tensile_z/tensile_xy knockdown on isotropic E — not mapped
orthotropic. If gmsh/ccx are missing, the copy is still there and Agent mode
will not invent von Mises. Closed-form Roark / NAFEMS-cousin identities live
in `cadfree/physics/validation.py` and run in CI without gmsh.

## Rung 2b — thermal (lumped, then CalculiX heat)

`service_temp_c` is now a feasibility check: surveyed `operating_temp_c`, or a
**named** 80 °C assumption when environment is `hot / near motors`. Unsurveyed
indoor temperature is a warning, not a pass.

Handbook: Newton film (h = 10 W/(m²·K) still air unless surveyed), gray-body
radiation to a large enclosure, lumped τ and Tss. `pack=heat` /
`solvers=['thermal']`. If gmsh/ccx exist, `physics/thermal.py` writes a
`*HEAT TRANSFER, STEADY STATE` deck (fixed NT or nodal CFLUX on the load set,
`*FILM` on the fixture set). Not a cavity, not UL.

## CadQuery → SI copy → solvers → iterate

CadQuery will not run friction, FEA, or CFD. After `build_model`:

1. Snapshot the part in SI (`sim/status.json`) — metres, newtons, pascals —
   from the mesh, PARAMS, and spec. CadQuery millimetres are converted here.
2. Copy `part_si.stl` (and STEP when CadQuery exported one) into `sim/fea/` and
   `sim/fluids/`.
3. Dispatch `run_solvers`:
   - **analytical** — formula book (friction, PV, wear, pipe, aero, beams, heat).
     LaTeX steps in the studio. Coefficients come from the book or the survey,
     not the LLM.
   - **fea** — Gmsh + CalculiX C3D10 if installed; otherwise the handoff folder only.
   - **thermal** — lumped Tss vs service_temp_c; CalculiX heat if installed.
   - **fluids** — handbook drag/Re from the same snapshot; an OpenFOAM
     `simpleFoam` + `snappyHexMesh` template always lands in
     `sim/fluids/openfoam/`. If `simpleFoam` is on PATH we try a short run and
     parse `forceCoeffs`. Cd is reported only when that file exists.
   - **dynamics** — 1-DOF RK4 when k/m exist; Exudyn rigid DAE if
     `pip install 'cadfree[motion]'` (`import exudyn`) works.
4. `iterate` hints go back to PARAMS (`thickness_mm`, …). Rebuild, run again.

## Generative design (SIMP, not Fusion)

Autodesk Generative Design is a cloud product. Cadfree’s analogue is the
research method that look comes from:

- Sigmund 2001, 99-line MATLAB SIMP
- Liu & Tovar 2014, 3-D SIMP
- mill 2.5-D extrusion filter; crude AM overhang squeeze

`generate_designs` (studio **Generate**) voxelizes the SI mesh copy, runs a
short optimality-criteria loop, and registers organic STL candidates as
`kind=imported` parts. CadQuery is not rewritten. Scipy is required
(`pip install 'cadfree[physics]'`). Compliance is the voxel FEM model, not
CalculiX. Missing scipy is named in one sentence — we never invent it.

## Formula book

Friction, maintenance (PV, Archard, L10), fluids, aero, beams. All SI. The
studio typesets the book equation, the substitution, and the result. This is
not CFD and not a Motion study.

## Linkages (not CadQuery)

OCCT will not tell you if a four-bar locks. Cadfree adds joints on assembly
instances and a **planar four-bar** solver (Grashof + two-circle intersection),
a **slider-crank**, open revolute/prismatic chains, and a spur-gear **pitch-diameter**
check. Sweeping the input reports lock-up, **convex-hull SAT** clashes of posed
meshes, and the min **transmission angle** (below 40° is awkward). Pins that
share a joint are ignored. This is not contact dynamics and not a Motion study.

**Springs and pin loads** are the next honest rung, not Adams:

- A `spring` / `torsion` joint is a **force element**. Pose still comes from
  revolute/prismatic. Catalog coils (`cots_springs`) are whoop-vs-5-inch data.
- At a pose: Hooke `F = kx`, coil rate `G d⁴ / (8 D³ n)`, Wahl shear, solid
  height, spring energy. Latch work is ΔU.
- Planar **quasi-static** pin forces (no inertia, no friction unless μ is given).
  Four-bar: 9×9 holding torque + four pins. Slider-crank: two-force rod.
  “What force does this linkage put into the pin?” is that number, compared to
  pin shear if `pin_d_mm` is set.
- 1-DOF `ωn = √(k/m)` plus RK4 of that oscillator when k and m exist. If
  `import exudyn` works (`pip install 'cadfree[motion]'`), `check_mechanism`
  also runs a short rigid-body DAE (bbox cuboid inertia at 1000 kg/m³ — not
  the mesh density). Not a quarter-car, not Adams Flex.

The studio Play/Check strip and the agent `check_mechanism` tool share the
kinematic verdict; pin/spring numbers ride on `loads`; RK4/Exudyn ride on
`dynamics`. `check_feasibility`
uses the same overlay as a 50 lb bracket (catalog + Wahl + solid height).

## What we will not do

- Call a bounding-box check "FEA"
- Silently skip a solver the user asked for
- Treat TPU as a structural filament
- Certify a part. Passing checks means **not physically impossible on this
  shop**, not a lab coupon, not a PE stamp.

## Editing the CAD yourself

The studio is not chat-only:

- A top-level `PARAMS = { ... }` dict becomes sliders. Drag, rebuild, no LLM.
- Monaco (the same editor as Carrot's Code tab) edits CadQuery directly.
- Plan vs Agent is Carrot's switch: Plan cannot write; Agent can write
  CadQuery and run MATLAB.
