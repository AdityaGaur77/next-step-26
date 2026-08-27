# Architecture

## Data flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ OrcaSlicer nightly (≥2.4.2), plugin API: embedded Python, pybind11 graph    │
│                                                                             │
│  posSlice ──► host_bridge ──► ctx.object → model_object().volumes()         │
│                  │            → mesh() (local mm) × volume matrix × trafo()  │
│                  ▼                                                          │
│           voxelize.voxelize          ray-parity along +x, per (y,z) line    │
│                  │                   VoxelGrid{mask,origin,h}               │
│                  ▼                                                          │
│           loadcase.extract         NL ─► {forces[],constraints[],sf,E,ν,σy} │
│                  │                   heuristic regex / Claude (optional)    │
│                  ▼                                                          │
│           fem.solve_voxel_fem        hex8 Q1 @ 2×2×2 GP; K assembled        │
│                  │                   directly on free DOFs; ≤8k DOF splu,   │
│                  │                   else Jacobi-CG → AMG → LU              │
│                  ▼                                                          │
│           mapping.plan_from_stress   utilization=VM/(σy/sf); per z-band:    │
│                  │                   reinforce cols ≥0.6·allow or p95 ≥1.0  │
│                  │                   relax cols ≤0.2 when band median cold  │
│                  ▼                                                          │
│  posPrepareInfill ─► mutate.apply_plan_to_object                            │
│                  │     compute_alignment: grid bbox ↔ PrintObject.bbox      │
│                  │     surface centroid → cell → reinforce/relax/neutral    │
│                  │     reinforce: extra_perimeters += cfg.add (≤cap)        │
│                  │                + fill_surfaces reclass → internal_solid  │
│                  │     relax: extra_perimeters → 0                          │
│                  ▼                                                          │
│  psGCodePostProcess ─► receipt.receipt_block  ;ECOSLICE … ;ECOSLICE END     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Module contracts

| module | exports | notes |
|---|---|---|
| `voxelize` | `VoxelGrid`, `voxelize`, `boundary_voxels`, `box_mesh`, `uv_sphere_mesh` | dedupes coincident ray hits (shared triangle edges) before parity pairing |
| `fem` | `solve_voxel_fem(grid, fixed_faces, load_face, F, E, ν)` → `FemResult` | active-node compaction; constrained DOFs dropped at assembly; `load_patch="far"` keeps the load away from clamped faces; rejects an unsupported part or a load face swallowed by the fixture |
| `loadcase` | `LoadCase`, `extract_load_case` | validates schema; LLM path is strict-JSON with heuristic fallback |
| `mapping` | `plan_from_stress` → `Plan(actions[])`; `region_boundary_length_mm` | masks are **x-major** `(nx, ny)` |
| `host_bridge` | `probe`, `current_object`/`iter_print_objects`, `object_key`, `get_mesh`, `object_footprint_mm`, surface R/W | verified binding paths first (`SurfaceCollection.surfaces`, `SurfaceType` enum, `Polygon.centroid()`), duck-typed fallbacks behind them; never raises into the slicer |
| `mutate` | `MutationConfig`, `FrameAlignment`, `compute_alignment`, `apply_plan_to_object` | area-centroid classification in the aligned frame; caps extra perimeters |
| `receipt` | constants + `receipt_block(stats)` | cites sources; marks estimates as estimates |
| `pipeline` | `EcoSlicePipeline` — hooks + `analyze_mesh` + offline driver | caches analyses per `PrintObject.id()`; element budget; idempotent receipt |

## Design decisions

1. **Mutate geometry/classification, not config.** The plugin API exposes live-graph mutation
   (`Surface.extra_perimeters`, `fill_surfaces` reclassification) but not config values
   (`fill_density`, `wall_loops`). We work *with* that: existing config applies to what we produce.
2. **x-major masks everywhere.** One convention (`(nx, ny)`) after an indexing bug taught us why.
2b. **Anchor by bounding box, not by origin.** Meshes arrive in the volume's local frame and
   slices live in the print object's frame; `compute_alignment` matches the two bounding boxes
   so the plan lands on the part wherever it sits on the bed.
3. **Defensive host bridge.** The API is weeks old and barely documented; `host_bridge.probe(ctx)`
   reports which access paths exist on the running build, and the receipt logs capabilities.
4. **Honest economics.** Savings are framed as *"vs blanket-strengthening at equal safety factor"* —
   a like-for-like comparison the FEM justifies — plus authoritative footer diffs from real G-code.
5. **Single-file bundle via AST transform.** `tools/build_plugin.py` strips intra-package imports,
   flattens modules into one namespace, preserves `__future__`, and appends a thin adapter
   (`execute(ctx)` dispatch on `ctx.step` — the host calls it for *every* step). Tests import
   the bundle standalone and assert the default config is a dict, as the host requires.
6. **Never crash the slicer.** Every hook wraps its body in try/except with logged context.

## Performance envelope

Measured (this machine, `analyze_mesh` end to end: voxelize + solve + plan):

| part | resolution | voxels | free DOF | time | solver |
|---|---|---|---|---|---|
| 100×10×10 bracket | 40 | 640 | 3.0k | 0.05 s | splu |
| 80×60×40 block | 32 | 12.3k | 40.8k | 2.4 s | jacobi-cg (356 it) |
| 80×60×40 block | 48 | 41.5k | 133k | 13.4 s | jacobi-cg (534 it) |

The ladder matters: on the 40.8k-DOF case SuperLU took 21.5 s and 1.06 GB against CG's 0.6 s,
and the gap widens with size — direct factorization of 3-D elasticity fills in badly. `pyamg`,
when installed, is tried before falling back to LU if CG fails to converge. `MAX_ELEMENTS`
(150k) lowers the resolution instead of letting a dense model run away.
