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
mesh ──► voxelizer (ray parity, numpy)                   ──► VoxelGrid 32³–64³
grid + load case ──► voxel FEM (trilinear hex Q1, scipy splu / optional pyamg)
von-Mises field ──► layer plan (reinforce / relax per layer×column)
plan ──► posPrepareInfill mutations (Surface.extra_perimeters ↑↓, fill_surfaces → solid)
stats ──► psGCodePostProcess carbon receipt (;ECOSLICE block)
```

Real FEM, honestly labeled: trilinear hexahedra, validated against the closed-form cantilever
solution (`tests/test_fem.py`). 32³–64³ voxels solve in 0.02–2 s via sparse LU.

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
pip install -e .[dev]
python tools/offline_demo.py --resolution 64
python -m pytest            # 37 tests incl. FEM validation & mock-host end-to-end
```

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
| integration | mock-host hooks mutate surfaces exactly as planned | ✅ tests |
| bundle | single-file plugin imports standalone & runs end-to-end | ✅ tests |
| slicer | spike changes exported G-code in a nightly | 🔲 day-1 gate |
| CLI round-trip | `gcode_diff.py --assert-lighter` on baseline vs optimized | 🔲 after gate |
| physical | print optimized part, load to spec | stretch |

## Judging criteria mapping

- **Originality** — among the first plugins on a weeks-old API with essentially zero prior art.
- **Adherence** — waste reduction is the product, not a coat of paint.
- **Completion** — shippable spine: offline analysis + receipts work today; mutation gated by the spike.
- **Learning** — pybind11 plugin API reverse-engineering, voxel FEM from scratch.
- **Design** — one text box in the slicer UI; human-readable G-code receipt.
- **Technology** — real FEA inside a slicer's live slicing graph.

## License

AGPL-3.0-or-later (OrcaSlicer is AGPL-3.0; derivative work inherits it).
