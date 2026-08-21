# Cadfree

**Cursor for CAD** — with one extra job that text-to-CAD tools skip: *can this even be made, on the machines you own, and will it do what you asked?*

You enter the shop (Prusa Mini, Bambu X1C, a mill, a laser, injection molding, wood cutting, metal AM). You start a project: *“I want a bracket that supports 50 pounds and uses no more than 100 g of filament.”* Cadfree writes CadQuery, does the maths, and will tell you **no** — or that you need PETG, or a bigger bed, or a mill.

The UI is **Carrot’s** glass workspace (same stylesheet, Monaco editor, Plan/Agent bar, accent palette). The agent runtime is **DeepSeek Harness-shaped**: every capability is a plugin. CAD, DFM, MATLAB/Octave, and simulation are plugins, not a special case in the loop. A `dsh-plugin/` package mounts the same tools inside [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) if you already run `dsh`.

## What you get

- **Workshop** — printers, mills, lasers, molds, wood, metal AM, with real envelopes and the filaments/stock you have
- **Agent mode** — writes CadQuery, rebuilds, checks feasibility, can run **MATLAB or GNU Octave** (same contract as Carrot’s academia pack)
- **You can edit the CAD** — `PARAMS` sliders rebuild without the model; Monaco edits the script directly; Plan mode cannot write
- **Honest simulation rungs** — first-order always; MATLAB beam theory if installed; Gmsh+CalculiX probed, never faked. See [docs/SIMULATION.md](docs/SIMULATION.md)
- **Survey before CAD** — the agent must not guess load direction, fasteners, environment, or which ISO/ASTM applies. It opens a form in the studio and waits.
- **Standards search** — ISO, ASTM, ASME, DIN, SAE, MIL-STD, NAS, IPC, plus manufacturer datasheets. Official bodies rank first. Paywalled PDFs are cited, never invented. Optional Brave Search key in Settings (`pip install -e ".[search]"` for the DuckDuckGo library).
- **Adjustable thinking** — Think **off / low / high / max**. High is the default. DeepSeek is **not vision-native**.
- **Vision** — OpenAI (`gpt-4.1`, `gpt-4o`), Anthropic, Gemini (`gemini-2.5-flash` / `pro`), and OpenRouter vision models read drawings/photos attached in chat. Default provider is OpenAI, not DeepSeek.
- **Assemblies** — shop-scale: unique parts (≤32 solids) + GPU-instanced placements with linear/grid/circular/mirror patterns (cap 400). Nested `parent_id` multiplies children. Fasteners are BOM-only.
- **Linkages** — CadQuery will not simulate motion. Joints (revolute / prismatic / gear) + planar four-bar + slider-crank + convex-hull SAT along a drive sweep. Transmission angle and Grashof for comfort. Gear check is pitch diameters. Studio **Play** / **Check**. Agent `check_mechanism`. Not SolidWorks Motion.
- **Physics loop** — CadQuery authors the solid. `run_solvers` snapshots it in SI, copies the mesh into packaged solvers (formula book always; Gmsh+CalculiX if present; fluids/aero handbook + CFD handoff), then `iterate` PARAMS and rebuild. Math is LaTeX in the studio. Not Ansys.
- **COTS vehicles** — skills, not a drone product. The model surveys (speed, cost, range…), refuses an impossible spec (`$80` will not buy a 50 mph 5-inch), searches a street-typical parts catalog + vendor pages, then designs a printable frame around those envelopes. Electronics stay purchased. `template=drone` is a form shortcut.
- **MCP** — the same tools on `POST /mcp`, protocol **2026-07-28**, Streamable HTTP, **no sessions**. Pass `project_id` on every `tools/call`. Surveys use `input_required` (MRTR). Not the 2024 initialize handshake.
- **Generative design** — Fusion Generative Design analogue: Sigmund / Liu–Tovar **SIMP** on a voxel copy of that SI mesh (`generate_designs` / studio **Generate**). Mill 2.5-D and AM overhang filters. Organic STL candidates are imported meshes — CadQuery is not rewritten. Needs `pip install 'cadfree[physics]'` (scipy). Not Autodesk cloud, not nTopology.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# optional geometry kernel:
pip install cadquery
# optional formula CAS + SIMP generative design:
pip install -e ".[physics]"
# optional MATLAB stand-in:
# sudo apt install octave

python -m cadfree.main
# open http://127.0.0.1:8181
```

1. **Settings** — paste an API key. Prefer **OpenAI, Anthropic, Gemini, or OpenRouter** if you will attach drawings (DeepSeek cannot see images). Optionally a Brave Search key for stronger standards lookup.
2. **Workshop** — add the machines you actually own
3. **Projects** — name the spec (load, mass budget)
4. **Studio** — talk, answer the survey form when it appears, or drag PARAMS / edit CadQuery yourself, then Rebuild

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
