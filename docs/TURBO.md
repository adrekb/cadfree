# Impeller / turbo setup

CadQuery still only authors the solid. Meanline Euler head is **always on**.
Gmsh+CalculiX, OpenFOAM MRF, and OpenRadioss are optional rungs: Cadfree probes
PATH, names what is missing, and will not invent a head, von Mises, or burst
factor.

This is not Ansys CFX, not TurboGrid, not a pump test stand.

## 0. What you actually need

| You want | Install | Cadfree looks for |
|---|---|---|
| Geometry you can drag PARAMS on | `pip install cadquery` | `import cadquery` |
| Euler head, Wiesner slip, hoop, NPSHa | nothing extra | formula book `pack=turbo` |
| Spinning-disc stress | `gmsh` + CalculiX `ccx` | `which gmsh ccx` |
| Rotating-frame flow (head/torque) | OpenFOAM | `blockMesh`, `snappyHexMesh`, `topoSet`, `simpleFoam` |
| Burst / containment | OpenRadioss | `starter_linux64_gf` + `engine_linux64_gf` |

Meanline does not need any of the binaries. Start there.

Check what the running server can see:

```bash
curl -s http://127.0.0.1:8181/api/health | python -m json.tool | less
```

Look under `solvers.fea.available`, `solvers.mrf.available`, `solvers.radioss.available`.
If a binary is installed but those are `false`, it is not on **the server's PATH**
(OpenFOAM's `bashrc` is the usual culprit — see below).

## 1. Cadfree + CadQuery

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install cadquery          # impeller solid + Rebuild
python -m cadfree.main        # http://127.0.0.1:8181
```

Settings → API key. Workshop → a machine you own (FDM is fine for a printed
pattern; metal AM / mill if you mean a real impeller).

## 2. Studio loop (this is the product)

1. **New project.** Spec in one sentence, e.g. *water centrifugal impeller, 3000 rpm, 60 L/min, 20 m head*.
2. Agent calls `ask_survey template=impeller`. Answer **n_rpm**, **Q_lpm**, **target_H_m**, fluid. Leave **npshr_m** blank unless you have a catalog number — Cadfree will not invent NPSHr.
3. `draft_impeller` writes a parametric CadQuery script (backplate + hub + blades). `PARAMS` (`r2_mm`, `beta2_deg`, `n_blades`, …) become sliders.
4. **Rebuild** (`build_model`). Without CadQuery this step fails in one sentence.
5. `run_solvers pack=turbo`:
   - **always** — meanline Euler head \(H = U_2 C_{u2}/g\) with Wiesner slip (\(\beta_2\) from the tangential), SI \(n_s\), NPSHa, thin-ring hoop
   - **if gmsh+ccx** — CalculiX `*DLOAD, CENTRIF` (\(\omega^2\) about +Z, hub fixture)
   - **if OpenFOAM** — MRF case in `sim/fluids/mrf/`
   - **if you pass `solvers=['radioss']`** — OpenRadioss `/LOAD/CENTRI` decks
6. Read `results[].iterate` → `set_params` / `optimize_params goal=head` → Rebuild → `run_solvers pack=turbo` again.

`optimize_params goal=head|hoop|turbo` searches impeller PARAMS on **meanline only**. It does not rebuild CadQuery per eval and it does not run CFD inside the loop. After `apply=true`, Rebuild, then `run_solvers pack=turbo` to verify.

Where files land (under the project directory):

```text
sim/status.json          SI snapshot (metres, N, Pa)
sim/fea/                 CalculiX INP / ccx output
sim/fluids/mrf/          OpenFOAM case + ./Allrun
sim/radioss/             OpenRadioss *_0000.rad / *_0001.rad
```

## 3. Optional: Gmsh + CalculiX (CENTRIF)

Debian/Ubuntu:

```bash
sudo apt install gmsh calculix-ccx
which gmsh ccx
```

macOS: `brew install gmsh` and install CalculiX (`ccx` on PATH). Restart Cadfree after installing so the probe sees them.

This is linear static on the tessellated solid: `*DENSITY` + `*DLOAD, CENTRIF` with magnitude \(\omega^2\), fixture `selector=hub`. Not a burst test. Pass `values.modal=true` for a second `*FREQUENCY` step.

## 4. Optional: OpenFOAM MRF

The case is the stock **mixerVessel2D / closedVolumeRotating** chain:

`blockMesh` → `snappyHexMesh` (full 360° STL) → `topoSet` rotor cylinder → `simpleFoam`.

### Ubuntu / Debian (OpenFOAM.com packages)

```bash
curl -s https://dl.openfoam.com/add-debian-repo.sh | sudo bash
sudo apt-get install -y openfoam2412-default
```

**The binaries are not on PATH until you source the environment in the same shell that starts Cadfree:**

```bash
source /usr/lib/openfoam/openfoam2412/etc/bashrc
which blockMesh snappyHexMesh topoSet simpleFoam
# all four must print a path
python -m cadfree.main
```

Put that `source` line in `~/.bashrc` if you do not want to remember it. A systemd unit or IDE run configuration that does not source the file will probe `mrf.available: false` even though `apt` succeeded.

### Other distros

Install any OpenFOAM that ships those four utilities (ESI OpenFOAM.com or Foundation). Names Cadfree probes: `blockMesh`, `snappyHexMesh`, `topoSet`, `simpleFoam`.

### What the numbers mean (and do not)

- **`head_static_m`** — area-averaged kinematic \(\Delta p / g\) inlet→outlet. There is **no volute**; swirl kinetic energy is unrecovered, so this **underestimates** a real pump.
- **`head_shaft_m`** — \(M_z \omega / (\rho g Q)\) from the parsed impeller moment. Includes churning; an **upper bound**.
- Delivered head, if you had a casing, sits between those two. Compare both to the meanline Euler head before changing PARAMS.
- Missing OpenFOAM: Cadfree still writes `sim/fluids/mrf/` with `./Allrun`. Run that yourself after installing. It will not invent a head from the template.

Hand-run the case without the agent:

```bash
cd <project>/sim/fluids/mrf
source /usr/lib/openfoam/openfoam2412/etc/bashrc
./Allrun
# then inspect postProcessing/inletP, outletP, shaft
```

## 5. Optional: OpenRadioss (burst, not CFD)

This is explicit containment / impact after a `/LOAD/CENTRI` pre-load. It is **not** the impeller optimizer.

1. Get Linux binaries from [OpenRadioss](https://github.com/OpenRadioss/OpenRadioss) (releases or a local build).
2. Put **both** on PATH, with one of these names:

   - starter: `starter_linux64_gf` (also `starter_linux64`, `openradioss_starter`)
   - engine: `engine_linux64_gf` (also `engine_linux64`, `openradioss_engine`)

```bash
export PATH="$HOME/OpenRadioss/bin:$PATH"
which starter_linux64_gf engine_linux64_gf
python -m cadfree.main
```

3. Run FEA first so a tet INP exists to convert, then `run_solvers` with `solvers=['radioss']` (or `['analytical','fea','radioss']`). `ok=True` only if the engine ran and an energy line parsed.

## 6. Honesty

- NPSHr and hydraulic efficiency are **never invented**. Survey them or leave them blank.
- Passing meanline / CENTRIF / MRF / Radioss means **not physically impossible on this shop**, not a PE stamp, not a Hydraulic Institute curve.
- OpenRadioss does not compute Euler head. Meanline does not compute burst. MRF without a volute is not a pump curve.
