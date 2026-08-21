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
and runs a linear static with bbox-face BCs. If they are missing, the copy is
still there and Agent mode will not invent von Mises.

## CadQuery → SI copy → solvers → iterate

CadQuery will not run friction, FEA, or CFD. After `build_model`:

1. Snapshot the part in SI (`sim/status.json`) — metres, newtons, pascals —
   from the mesh, PARAMS, and spec. CadQuery millimetres are converted here.
2. Copy `part_si.stl` (and STEP when CadQuery exported one) into `sim/fea/` and
   `sim/fluids/`.
3. Dispatch `run_solvers`:
   - **analytical** — formula book (friction, PV, wear, pipe, aero, beams).
     LaTeX steps in the studio. Coefficients come from the book or the survey,
     not the LLM.
   - **fea** — Gmsh + CalculiX if installed; otherwise the handoff folder only.
   - **fluids** — handbook drag/Re from the same snapshot; OpenFOAM/Elmer/SU2
     probed, never faked as a RANS field.
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
open revolute/prismatic chains, and a spur-gear **pitch-diameter** check.
Sweeping the input reports lock-up and AABB clashes of posed meshes. Pins that
share a joint are ignored. This is not contact dynamics and not a Motion study.

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
