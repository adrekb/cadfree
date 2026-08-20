# Cadfree

**Cursor for CAD** — with one extra job that text-to-CAD tools skip: *can this even be made, on the machines you own, and will it do what you asked?*

You enter the shop (Prusa Mini, Bambu X1C, a mill, a laser, injection molding, wood cutting, metal AM). You start a project: *“I want a bracket that supports 50 pounds and uses no more than 100 g of filament.”* Cadfree writes CadQuery, does the maths, and will tell you **no** — or that you need PETG, or a bigger bed, or a mill.

The UI is **Carrot’s** glass workspace (same stylesheet, Monaco editor, Plan/Agent bar, accent palette). The agent runtime is **DeepSeek Harness-shaped**: every capability is a plugin. CAD, DFM, MATLAB/Octave, and simulation are plugins, not a special case in the loop. A `dsh-plugin/` package mounts the same tools inside [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) if you already run `dsh`.

## What you get

- **Workshop** — printers, mills, lasers, molds, wood, metal AM, with real envelopes and the filaments/stock you have
- **Agent mode** — writes CadQuery, rebuilds, checks feasibility, can run **MATLAB or GNU Octave** (same contract as Carrot’s academia pack)
- **You can edit the CAD** — `PARAMS` sliders rebuild without the model; Monaco edits the script directly; Plan mode cannot write
- **Honest simulation rungs** — first-order always; MATLAB beam theory if installed; Gmsh+CalculiX probed, never faked. See [docs/SIMULATION.md](docs/SIMULATION.md)

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# optional geometry kernel:
pip install cadquery
# optional MATLAB stand-in:
# sudo apt install octave

python -m cadfree.main
# open http://127.0.0.1:8181
```

1. **Settings** — paste an API key (OpenAI, Anthropic, OpenRouter, DeepSeek, Ollama)
2. **Workshop** — add the machines you actually own
3. **Projects** — name the spec (load, mass budget)
4. **Studio** — talk, or drag PARAMS, or edit CadQuery yourself, then Rebuild

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
