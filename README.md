# EcoSlice

[![CI](https://github.com/AdityaGaur77/next-step-26/actions/workflows/ci.yml/badge.svg)](https://github.com/AdityaGaur77/next-step-26/actions/workflows/ci.yml)

**Load-aware walls & infill for OrcaSlicer.** Slicers know the *shape* of a part — never its *job*.
EcoSlice closes that gap: you describe what the part must do ("shelf bracket holding 8 kg, screwed
to the left wall"), it runs a real voxel FEM simulation on your model, and locally reinforces
exactly where stress demands it — extra perimeters and solid infill at hotspots — while relaxing
material where the structure is loafing. Every print ships with a carbon receipt.

> NextStep Hacks 2026 — *"Earth Forward"* track. Native OrcaSlicer plugin built on the
> embedded-Python plugin API (`SlicingPipeline` capability). AGPL-3.0.

## Why it matters

- **Support/waste framing:** every gram of filament is manufactured, shipped, melted and often
  binned. Blanket "more strength" settings over-build entire parts to protect a single hotspot.
  EcoSlice puts strength only where physics asks for it.
- **Two-sided savings:** reinforce the root of a cantilever (+2.4 g on the demo bracket:
  +0.5 g of walls, +1.9 g of solid infill), skip the blanket strengthening everywhere else
  (−10.0 g vs strengthening the whole part the same way) — same safety factor, less material.
  Both levers are counted; the solid-infill term is the larger one and omitting it understates
  the added mass roughly fivefold.
- **Measured, not vibes:** the receipt carries EcoSlice's own model *and* the slicer's export
  footer (`; filament used [g]`, `; estimated printing time`) — mass, time and kWh as the
  slicer reported them — with `tools/gcode_diff.py` for baseline-vs-optimized diffs.

## Architecture in one paragraph

```
description ──► load case (NL parser / optional Claude)  ──► structured JSON (forces, constraints, sf)
mesh ──► voxelizer (ray parity, numpy)                   ──► VoxelGrid, element-budgeted
grid + load case ──► voxel FEM (trilinear hex Q1; direct LU small, Jacobi-CG large, AMG optional)
von-Mises field ──► layer plan (reinforce / relax per layer×column)
same field ──► Eco / Balanced / Maximum Strength options + strength-confidence score
plan ──► posPrepareInfill mutations (Surface.extra_perimeters ↑↓, fill_surfaces → solid)
stats + G-code footer ──► psGCodePostProcess receipt (;ECOSLICE block, modelled AND measured)
```

Real FEM, honestly labeled: trilinear hexahedra, validated against the closed-form cantilever
solution (`tests/test_fem.py`). Measured on this machine: a 100×10×10 bracket at resolution 40
(3 k DOF) solves in **0.05 s** by direct LU; a bulky 80×60×40 part is 41 k DOF at resolution 32
(**2.4 s**) and 133 k DOF at resolution 48 (**13 s**) via Jacobi-preconditioned CG — direct LU
was 10–40× slower and needed >1 GB at those sizes, which is why the solver ladder exists. A
150 k-element budget reduces resolution automatically rather than hanging on a dense model.

## Repository layout

| path | purpose |
|---|---|
| `src/ecoslice/` | library: voxelize, fem, loadcase, mapping, host_bridge, mutate, receipt, options, pipeline |
| `tools/offline_demo.py` | run the full analysis without OrcaSlicer (`--options`, `--html`, `--json`) |
| `plugin/ecoslice_core.py` | generated **single-file plugin** (PEP 723) — drop into `orca_plugins/` |
| `spike/spike_extra_perimeters.py` | day-1 gate: prove graph mutation changes G-code |
| `tools/build_plugin.py` | regenerates `plugin/ecoslice_core.py` from `src/` (refuses on a non-canonical `ast.unparse`) |
| `tools/stress_report.py` | self-contained HTML proof: stress field vs the decisions taken from it |
| `tools/demo.py` | one command: parts + analyses + proof pages + a run-order sheet |
| `tools/make_test_part.py` | watertight test STLs sized to actually stress under a realistic load |
| `tools/verify_gcode.py` | did EcoSlice actually run on this export? names the failure mode |
| `tools/gcode_diff.py` | footer diff + CO₂e math + `--assert-lighter` CI gate |
| `docs/` | [running guide](docs/RUNNING.md), architecture, plugin API notes, spike protocol, demo script |

## Quick start (no OrcaSlicer needed)

```bash
pip install -e ".[dev]"          # library needs Python ≥3.10; the plugin targets the host's 3.12
python tools/demo.py             # ← everything for a demo, in ./demo/, in ~10 s
python tools/offline_demo.py --resolution 40 --options   # Eco / Balanced / Maximum Strength
python tools/offline_demo.py --resolution 80 --html proof.html   # visual why-here report
python tools/offline_demo.py --part bracket --json
python -m pytest                 # 147 tests: FEM validation, host-binding shapes, bundle checks
```

`pytest` works straight from a clone (`pythonpath` is set in `pyproject.toml`); `pyamg` is an
optional extra (`pip install -e ".[amg]"`) that only changes which fallback the solver uses.
The library supports Python ≥3.10 and the whole suite, bundle import included, runs on all of
them; CI covers 3.12 and 3.13.

## Install into OrcaSlicer (nightly ≥ 2.4.2)

0. `python tools/make_test_part.py --part l-bracket` for a part that will actually show the
   effect — a cube is too stubby to stress, so the planner correctly does nothing.
1. Run `python tools/build_plugin.py` (or use the committed bundle). Some CPython 3.12 patch
   releases unparse f-strings in a form no other interpreter emits, which would fail CI's
   staleness gate; the builder probes for that behaviour and refuses to run on such an
   interpreter rather than writing a bundle the gate will reject.
2. **Plugins dialog → Local install**, and pick `plugin/ecoslice_core.py`. Copying the file
   into `orca_plugins/` by hand does *not* work — discovery finds zero manifests; the host has
   to create the folder and manifest sidecar itself.
3. **Activate** it with the toggle in the Plugins dialog, then select the capability per process
   profile: Process settings → **Others** → **Slicing Pipeline Plugin** → EcoSlice. The C++ hook
   skips everything while that option is empty, so a plugin that is installed and activated but
   not selected does nothing at all.
4. Slice a part, then `python tools/verify_gcode.py <exported>.gcode` — it reports whether the
   plugin ran, whether it changed anything, and what to fix if not.
5. Optional LLM parsing: `set ANTHROPIC_API_KEY=...` before launching OrcaSlicer;
   otherwise the deterministic heuristic parser is used.

Full step-by-step, including the measurement protocol: **[docs/RUNNING.md](docs/RUNNING.md)**.

**Day-1 gate first:** follow [docs/SPIKE.md](docs/SPIKE.md) with `spike/spike_extra_perimeters.py`.
The plugin's host-facing calls are deliberately defensive (multiple attribute paths, never raises
into the slicer), but the spike confirms which paths the current nightly actually supports.

## Verification ladder

| level | proof | status |
|---|---|---|
| unit | voxelize volume error <8%; cantilever deflection/stress vs closed form | ✅ tests |
| binding shapes | mocks mirror the real pybind classes (`SurfaceCollection`, `SurfaceType` enum, `ModelVolume.mesh()`, `trafo()`) read out of OrcaSlicer's C++ | ✅ tests |
| integration | mock-host hooks mutate surfaces exactly as planned, on a part placed off-origin on the bed | ✅ tests |
| bundle | single-file plugin imports standalone, default config is a valid dict, no intra-package imports | ✅ tests |
| slicer | spike changes exported G-code in a nightly | ✅ GO, 2026-08-26, nightly 2.5.0-dev ([docs/SPIKE.md](docs/SPIKE.md)) |
| CLI round-trip | `gcode_diff.py --assert-lighter` on baseline vs optimized | 🔲 **not yet run** — see *Scope* below |
| physical | print optimized part, load to spec | stretch |

## Scope — what this is, and what it is not

EcoSlice is **one vertical slice** of the larger product concept: load-aware walls and infill,
driven by real FEM, applied live inside OrcaSlicer's slicing graph. That slice is built and
tested. The rest of the concept is not, and this section says so plainly so nobody has to
discover it in a demo.

**Built and verified**

- voxel FEM (validated against the closed-form cantilever) driven by a plain-language load case
- live mutation of `extra_perimeters` and `fill_surfaces` at `posPrepareInfill` (confirmed on a nightly)
- Eco / Balanced / Maximum Strength options with a strength-confidence score, from a single solve
- a `;ECOSLICE` receipt carrying both the model and the slicer's measured footer numbers
- a standalone HTML proof (`--html`) putting the stress field and the resulting plan side by
  side, so the reinforcement can be checked against the physics that motivated it

**Not built** — no code for any of these; they are concept, not product:

- adaptive/custom **support generation** (`posSupportMaterial` is not hooked; 3 of 13 steps are)
- orientation search, overhang maps, thin-feature detection, support-volume analysis
- **interactive UI inside the slicer**: no build-plate placement, no dashboard, and the proof
  report is a generated page rather than a live 3-D view (`has_config_ui()` is `False`;
  configuration is still the slicer's JSON editor)
- **3MF export** — EcoSlice mutates the live graph and annotates G-code; it writes no 3MF
- printer / nozzle / material / quality **profiles** (material is three constants in `loadcase.py`)
- the **hardware node**: accelerometer, thermal camera and filament load-cell loops
- **PINN / NPU** stress inference — the solver is classical scipy on CPU
- vision-based failed-print feedback, and any slicer other than OrcaSlicer

**Known gap in the evidence.** The savings figures above are a *model*, compared against a
blanket-strengthening baseline. The one end-to-end measurement taken so far (the spike, which
mutated a probe surface rather than following a plan) made the print **heavier**: 402 KB → 1.14 MB
of toolpaths, 40 m → 1 h 54 m. A real baseline-vs-optimized slice through
`tools/gcode_diff.py --assert-lighter` has **not** been run. Until it is, treat the material
claim as modelled, not demonstrated.

**Relaxation caveat.** Removing extra perimeters only recovers material on a profile that
already adds them; on a stock profile `extra_perimeters` starts at 0 and relaxation is a no-op.
`MutationConfig(enable_solid_downgrade=True)` is the lever that removes material either way — it
thins density-driven internal solid infill back to sparse in cold columns, never touching top,
bottom or bridge surfaces. It is **off by default** because it thins shells the slicer chose to add.

## Judging criteria mapping

- **Originality** — among the first plugins on a weeks-old API with essentially zero prior art.
- **Adherence** — waste reduction is the product, not a coat of paint.
- **Completion** — shippable spine: offline analysis, options and receipts work today; live
  mutation confirmed on a nightly. Scope is deliberately one slice — see *Scope* above.
- **Learning** — pybind11 plugin API read from OrcaSlicer's C++ (see `docs/PLUGIN_API_NOTES.md`),
  voxel FEM and its solver ladder written from scratch.
- **Design** — plain-language intent instead of settings archaeology; three transparent options
  with a confidence score; a human-readable `;ECOSLICE` receipt in the G-code. (Config is the
  slicer's JSON editor today — an HTML config UI via `get_config_ui()` is the next design step.)
- **Technology** — real FEA inside a slicer's live slicing graph.

## License

AGPL-3.0-or-later (OrcaSlicer is AGPL-3.0; derivative work inherits it).
