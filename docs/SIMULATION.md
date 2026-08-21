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

Cadfree **probes** for `gmsh` and `ccx`. If they are missing, Agent mode will
not claim a mesh solve ran. Wiring the INP writer is the next increment — the
rung is named and detected so the UI can be honest today.

Anisotropic FDM (layer lines, infill pattern) is a further rung: voxel or
orthotropic material cards. Do not pretend isotropic PETG is a printed part.

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
