# Running EcoSlice

Everything in this repo, in the order you would actually do it. Part 1 needs nothing but
Python. Part 2 needs an OrcaSlicer nightly. Part 3 is the measurement that turns the
material claim from modelled into demonstrated.

---

## 1. Offline — no OrcaSlicer required

### 1.1 Install

```bash
git clone https://github.com/AdityaGaur77/next-step-26
cd next-step-26
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Python ≥3.10 for the library. CI covers 3.12 and 3.13. `pyamg` is optional
(`pip install -e ".[amg]"`) and only changes which fallback the FEM solver reaches for.

### 1.2 Run the analysis

```bash
python tools/offline_demo.py --resolution 40
```

Prints the parsed load case, the voxel grid, which solver ran, peak von Mises against the
allowable stress, the per-band plan, the confidence score, and the `;ECOSLICE` receipt.

Two built-in parts:

| flag | part | why |
|---|---|---|
| `--part cantilever` (default) | 100×10×10 mm bar | matches the closed-form case the FEM is validated against |
| `--part bracket` | 120×20×8 mm bar with a spherical cavity | proves nested shells read as a hole under parity fill |

### 1.3 The three options

```bash
python tools/offline_demo.py --resolution 40 --options
```

Eco / Balanced / Maximum Strength side by side: grams added (split into walls vs solid
infill), grams saved against blanket-strengthening, added print time, added energy, and a
confidence score. One FEM solve, re-thresholded three ways — not three solves.

### 1.4 The visual proof

```bash
python tools/offline_demo.py --resolution 80 --html proof.html
```

Open `proof.html`. Two aligned elevations of the same part: the stress field, and what
EcoSlice did about it. Hover any cell for its coordinates, utilisation and decision.

**Use `--resolution 80` for anything you are filming.** The default gives only four cells
through the thickness and the map looks squat; 80 gives eight and the gradient is obvious.
The file is self-contained — no network, no CDN — so it opens off a USB stick.

### 1.5 Machine-readable output

```bash
python tools/offline_demo.py --resolution 40 --json
```

`--json` and `--html` can be combined; `--json` suppresses the human-readable output only.

### 1.6 Tests

```bash
python -m pytest                   # 113 tests, ~100 s
python -m pytest tests/test_fem.py -v      # just the FEM validation
```

`pythonpath` is set in `pyproject.toml`, so this works straight from a clone.

Lint, as CI does not (yet) run it but reviewers will:

```bash
pip install pyflakes && python -m pyflakes src tests tools plugin
```

### 1.7 Rebuild the single-file plugin

```bash
python tools/build_plugin.py
```

Flattens `src/ecoslice/` into `plugin/ecoslice_core.py`. **Only commit a bundle this script
actually wrote.** It refuses to run on interpreters whose `ast.unparse` emits the
non-canonical f-string form (some CPython 3.12 patch releases) — that output is byte-different
from every other interpreter's and CI's staleness gate rejects it. If you see that error, build
on a different Python (3.11, 3.13, or a current 3.12).

---

## 2. Inside OrcaSlicer

Requires a nightly ≥ 2.4.2. Verified working on `2.5.0-dev` (2026-08-26).

> **Do not copy the file into `orca_plugins/` by hand.** Discovery finds zero manifests that
> way — the host has to create the folder and its manifest sidecar itself. Use Local install.

### 2.1 Install

1. **Plugins dialog → Local install**, and pick `plugin/ecoslice_core.py`.
2. **Activate** the plugin with the toggle in the Plugins dialog. Installed is not enabled.
3. **Select the capability per process profile**: Process settings → **Others** page →
   **Slicing Pipeline Plugin** group → tick EcoSlice. This drives the
   `slicing_pipeline_plugin` config option, and the C++ hook **skips everything if it is
   empty** — this is the step people miss, and the symptom is a plugin that installs, activates,
   and silently does nothing.

Dependencies (`numpy`, `scipy`) are installed by the host from the PEP 723 header using its
bundled `uv`, with a 120 s timeout, the first time the plugin loads. Give it a moment.

### 2.2 Describe the part's job

Plugins dialog → EcoSlice → config. It is a JSON editor:

```json
{
  "description": "shelf bracket holding 8 kg, load downward at the front edge; screwed onto left wall",
  "option": "balanced",
  "resolution": 32,
  "add_perimeters": 2,
  "max_extra_perimeters": 4,
  "enable_solid_infill": true,
  "enable_relax": true,
  "enable_solid_downgrade": false
}
```

- `description` is the whole point — plain language, mention the load, the direction, and how
  the part is held. The deterministic parser understands masses (`8 kg`, `20 N`, `5 lb`),
  directions (`down`, `left`, `front`…), attachment words (`screwed`, `bolted`, `mounted`) and
  `safety factor 2.5`.
- `option` seeds the mutation strength from a preset. Any explicit key you also set wins over
  the preset, so editing one value does not silently pull in the rest.
- `enable_solid_downgrade` is the only lever that removes material on a stock profile. It thins
  density-driven internal solid infill back to sparse in cold columns and never touches top,
  bottom or bridge surfaces. Off by default because it thins shells the slicer chose to add.

Optional LLM parsing: set `ANTHROPIC_API_KEY` in the environment **before launching
OrcaSlicer**. Without it the deterministic parser runs, which is fine and reproducible.

### 2.3 Confirm it actually ran

Slice a part, then check, in order:

1. **Console / log** for `ecoslice` lines — the capability probe, then
   `analyzed obj… maxVM=… allow=… reinforced=… relaxed=…`, then
   `mutations: +N perimeters added, M removed; K solidified`.
2. **Preview** — extra perimeters and solid infill should bloom where the stress is, not
   everywhere.
3. **Exported G-code** for the `;ECOSLICE BEGIN … ;ECOSLICE END` block.

**`+0 perimeters added` while the log says `reinforced=2` means the plan is landing off the
part.** That is the frame-alignment failure described in `docs/PLUGIN_API_NOTES.md`: the mesh
arrives in plate coordinates while `fill_surfaces` stay in the object-local frame. It is
handled, but if it regresses that is the signature.

---

## 3. The measurement that is still missing

Everything above produces *modelled* numbers. This produces measured ones, and it is the one
step no amount of code in this repo can do for you.

### 3.1 Slice twice, changing exactly one thing

```
baseline.gcode    EcoSlice deselected in Process settings → Others → Slicing Pipeline Plugin
ecoslice.gcode    EcoSlice selected
```

Same STL, same printer, same filament, same layer height, same infill, same everything else.
Deselecting the capability is the clean toggle — it is what the C++ hook reads. Uninstalling
the plugin also works but changes more than one variable at a time.

### 3.2 Diff the footers

```bash
python tools/gcode_diff.py baseline.gcode ecoslice.gcode
python tools/gcode_diff.py baseline.gcode ecoslice.gcode --json
python tools/gcode_diff.py baseline.gcode ecoslice.gcode --assert-lighter   # exit 1 unless lighter
```

It reads the slicer's own `; filament used [g]` and `; estimated printing time` footers, so the
numbers are the slicer's, not EcoSlice's. `;ECOSLICE` lines are skipped, so it never reads our
own output.

### 3.3 Reading the result honestly

`--assert-lighter` compares against a **plain default slice**, and against that baseline
EcoSlice is expected to be *heavier* — it adds walls and solid infill where stress demands
them. The savings claim in the receipt is against a different baseline: blanket-strengthening
the whole part to the same safety factor.

So there are two honest experiments, and they answer different questions:

| baseline | question | expected |
|---|---|---|
| default profile | "what does the strength cost?" | EcoSlice heavier — that is the point |
| whole part at `add_perimeters` + solid infill everywhere | "is targeting better than blanketing?" | EcoSlice lighter — this is the claim |

The second is the one worth filming. Build the blanket baseline by raising wall loops and infill
density profile-wide to roughly what EcoSlice applies locally, then diff that against the
EcoSlice slice. If it comes out lighter at equal safety factor, the pitch is proven rather than
asserted.

If you only have time for one: run the first, and say plainly on camera that the added mass is
the price of the strength and the saving is against blanketing. That framing survives a judge's
question. An unproven savings number does not.
