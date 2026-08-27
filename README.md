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
- **Two-sided savings:** reinforce the root of a cantilever (+0.4 g), skip the blanket
  strengthening everywhere else (−1.8 g vs uniform extra walls) — same safety factor, less material.
- **Measured, not vibes:** baseline-vs-optimized numbers come from the real slicer footers
  (`; filament used [g]`, `; estimated printing time`), diffed with `tools/gcode_diff.py`.

## Architecture in one paragraph

```
description ──► load case (NL parser / optional Claude)  ──► structured JSON (forces, constraints, sf)
mesh ──► voxelizer (ray parity, numpy)                   ──► VoxelGrid, element-budgeted
grid + load case ──► voxel FEM (trilinear hex Q1; direct LU small, Jacobi-CG large, AMG optional)
von-Mises field ──► layer plan (reinforce / relax per layer×column)
plan ──► posPrepareInfill mutations (Surface.extra_perimeters ↑↓, fill_surfaces → solid)
stats ──► psGCodePostProcess carbon receipt (;ECOSLICE block)
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
| `src/ecoslice/` | library: voxelize, fem, loadcase, mapping, host_bridge, mutate, receipt, pipeline |
| `plugin/ecoslice_core.py` | generated **single-file plugin** (PEP 723) — drop into `orca_plugins/` |
| `spike/spike_extra_perimeters.py` | day-1 gate: prove graph mutation changes G-code |
| `tools/build_plugin.py` | regenerates `plugin/ecoslice_core.py` from `src/` |
| `tools/offline_demo.py` | run the full analysis without OrcaSlicer (great for the video) |
| `tools/gcode_diff.py` | footer diff + CO₂e math + `--assert-lighter` CI gate |
| `docs/` | architecture, spike protocol, demo script |

## Quick start (no OrcaSlicer needed)

```bash
pip install -e ".[dev]"          # library needs Python ≥3.10; the plugin targets the host's 3.12
python tools/offline_demo.py --resolution 40
python tools/offline_demo.py --part bracket --json
python -m pytest                 # 63 tests: FEM validation, host-binding shapes, bundle checks
```

`pytest` works straight from a clone (`pythonpath` is set in `pyproject.toml`); `pyamg` is an
optional extra (`pip install -e ".[amg]"`) that only changes which fallback the solver uses.

## Install into OrcaSlicer (nightly ≥ 2.4.2)

1. Run `python tools/build_plugin.py` (or use the committed bundle).
2. Copy `plugin/ecoslice_core.py` into `<OrcaSlicer data dir>/orca_plugins/`
   (GUI: Help → Show Configuration Folder → `orca_plugins`).
3. Slice a part; check console for `ecoslice` log lines and the G-code for the `;ECOSLICE` block.
4. Optional LLM parsing: `set ANTHROPIC_API_KEY=...` before launching OrcaSlicer;
   otherwise the deterministic heuristic parser is used.

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
| slicer | spike changes exported G-code in a nightly | 🔲 day-1 gate |
| CLI round-trip | `gcode_diff.py --assert-lighter` on baseline vs optimized | 🔲 after gate |
| physical | print optimized part, load to spec | stretch |

## Judging criteria mapping

- **Originality** — among the first plugins on a weeks-old API with essentially zero prior art.
- **Adherence** — waste reduction is the product, not a coat of paint.
- **Completion** — shippable spine: offline analysis + receipts work today; mutation gated by the spike.
- **Learning** — pybind11 plugin API read from OrcaSlicer's C++ (see `docs/PLUGIN_API_NOTES.md`),
  voxel FEM and its solver ladder written from scratch.
- **Design** — plain-language intent instead of settings archaeology; a human-readable
  `;ECOSLICE` receipt in the G-code. (Config is the slicer's JSON editor today — an HTML
  config UI via `get_config_ui()` is the next design step.)
- **Technology** — real FEA inside a slicer's live slicing graph.

## License

AGPL-3.0-or-later (OrcaSlicer is AGPL-3.0; derivative work inherits it).
