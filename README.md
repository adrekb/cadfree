# Cadfree

**Cursor for CAD** — with one extra job that text-to-CAD tools skip: *can this even be made, on the machines you own, and will it do what you asked?*

You enter the shop (Prusa Mini, Bambu X1C, a mill, a laser, injection molding, wood cutting, metal AM). You start a project: *“I want a bracket that supports 50 pounds and uses no more than 100 g of filament.”* Cadfree writes CadQuery, does the maths, and will tell you **no** — or that you need PETG, or a bigger bed, or a mill.

The UI is **Carrot’s** glass workspace (same stylesheet, Monaco editor, Plan/Agent bar, accent palette). The agent runtime is **DeepSeek Harness-shaped**: every capability is a plugin. CAD, DFM, MATLAB/Octave, and simulation are plugins, not a special case in the loop. A `dsh-plugin/` package mounts the same tools inside [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) if you already run `dsh`.

## What you get

- **Workshop** — printers, mills, lasers, molds, wood, metal AM, with real envelopes and the filaments/stock you have
- **Agent mode** — writes CadQuery, rebuilds, checks feasibility, can run **MATLAB or GNU Octave** (same contract as Carrot’s academia pack)
- **You can edit the CAD** — `PARAMS` sliders rebuild without the model; Monaco edits the script directly; Plan mode cannot write
- **Honest simulation rungs** — first-order always; MATLAB beam theory if installed; Gmsh+CalculiX **C3D10** (+ optional mesh Richardson); lumped thermal vs `service_temp_c`; OpenFOAM aero template **or** impeller MRF (`pack=turbo`); OpenRadioss `/LOAD/CENTRI` if installed; 1-DOF RK4 / Exudyn if installed. Missing solvers are named, never faked. See [docs/SIMULATION.md](docs/SIMULATION.md). **Impeller walkthrough + installs:** [docs/TURBO.md](docs/TURBO.md)
- **Survey before CAD** — the agent must not guess load direction, fasteners, environment, or which ISO/ASTM applies. It opens a form in the studio and waits. Impellers: `template=impeller` (`n_rpm`, `Q_lpm`, `target_H_m`).
- **Standards search** — ISO, ASTM, ASME, DIN, SAE, MIL-STD, NAS, IPC, plus manufacturer datasheets. Official bodies rank first. Paywalled PDFs are cited, never invented. Optional Brave Search key in Settings (`pip install -e ".[search]"` for the DuckDuckGo library).
- **Adjustable thinking** — Think **off / low / high / max**. High is the default. DeepSeek is **not vision-native**.
- **Vision** — OpenAI (`gpt-4.1`, `gpt-4o`), Anthropic, Gemini (`gemini-2.5-flash` / `pro`), and OpenRouter vision models read drawings/photos attached in chat. Default provider is OpenAI, not DeepSeek.
- **Assemblies** — shop-scale: unique parts (≤32 solids) + GPU-instanced placements with linear/grid/circular/mirror patterns (cap 400). Nested `parent_id` multiplies children. Fasteners are BOM-only.
- **Linkages** — CadQuery will not simulate motion. Joints (revolute / prismatic / gear / spring / torsion) + planar four-bar + slider-crank + convex-hull SAT along a drive sweep. Pin forces are planar quasi-static (holding torque, Wahl, solid height). 1-DOF ωn + RK4; Exudyn rigid DAE if `cadfree[motion]` is installed. Not SolidWorks Motion, not Adams.
- **Physics loop** — CadQuery authors the solid. `set_load_path` names fixtures (picked faces / holes; bbox is the named fallback). `optimize_params` searches PARAMS against first-order feasibility (no CadQuery rebuild per eval); for impellers `goal=head` is meanline-only. FEA / MRF verify the winner. `run_solvers` snapshots it in SI, copies the mesh into packaged solvers (formula book always; Gmsh+CalculiX C3D10 if present; thermal; fluids handbook + OpenFOAM; `pack=turbo` meanline + CENTRIF + MRF). Math is LaTeX in the studio. Not Ansys.
- **One feasibility loop** — survey what you don't know, `check_feasibility`, refuse or design. Whoop vs 5-inch is catalog rows (`cots_classes`), the same way PETG vs PLA is catalog data — not a special agent. `$80` will not buy a 50 mph 5-inch; the tool names the smallest change. Then `search_parts` and CadQuery around those envelopes. Electronics stay purchased. `template=drone` is a form shortcut.
- **MCP** — the same tools on `POST /mcp`, protocol **2026-07-28**, Streamable HTTP, **no sessions**. Pass `project_id` on every `tools/call`. Surveys use `input_required` (MRTR). Not the 2024 initialize handshake.
- **Generative design** — Fusion Generative Design analogue: Sigmund / Liu–Tovar **SIMP** on a voxel copy of that SI mesh (`generate_designs` / studio **Generate**). Mill 2.5-D and AM overhang filters. Organic STL candidates are imported meshes — CadQuery is not rewritten. Needs `pip install 'cadfree[physics]'` (scipy). Not Autodesk cloud, not nTopology.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# optional geometry kernel (needed to Rebuild an impeller):
pip install cadquery
# optional formula CAS + SIMP generative design:
pip install -e ".[physics]"
# optional Exudyn rigid-body dynamics:
pip install -e ".[motion]"
# optional MATLAB stand-in:
# sudo apt install octave
# optional FEA (CENTRIF / von Mises):
# sudo apt install gmsh calculix-ccx
# optional OpenFOAM MRF — see docs/TURBO.md (you MUST source the OpenFOAM bashrc
# in the same shell as cadfree, or the probe will say mrf.available=false)
# optional OpenRadioss burst: put starter_linux64_gf + engine_linux64_gf on PATH

python -m cadfree.main
# open http://127.0.0.1:8181
```

1. **Settings** — paste an API key. Prefer **OpenAI, Anthropic, Gemini, or OpenRouter** if you will attach drawings (DeepSeek cannot see images). Optionally a Brave Search key for stronger standards lookup.
2. **Workshop** — add the machines you actually own
3. **Projects** — name the spec (load, mass budget). For a pump: *water centrifugal impeller, 3000 rpm, 60 L/min, 20 m head*
4. **Studio** — talk, answer the survey form when it appears (`template=impeller` asks rpm / Q / head), or drag PARAMS / edit CadQuery yourself, then Rebuild

**Impeller, OpenFOAM, CalculiX, OpenRadioss:** copy-paste installs and the agent loop are in **[docs/TURBO.md](docs/TURBO.md)**. After starting the server, `curl -s http://127.0.0.1:8181/api/health` → `solvers.mrf` / `solvers.fea` / `solvers.radioss` tells you what is actually on PATH.

The server binds to `127.0.0.1:8181` (Carrot’s port on purpose).

## DeepSeek Harness

Do not fork 100k lines of `dsh` into this repo. Add the plugin:

```text
dsh-plugin/          # Cordis tool plugin
cadfree/agent/       # the same tools in Python
```

Set `CADFREE_PROJECT_ID` and `CADFREE_ROOT`, then mount `@cadfree/dsh-plugin` in your harness profile. The loop stays replaceable; Cadfree stays the shop/geometry/solver process.

## Tests

```bash
pytest -q
```

Feasibility tests do not need CadQuery, MATLAB, or an API key.
