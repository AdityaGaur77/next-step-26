# Architecture

## Data flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ OrcaSlicer nightly (≥2.4.2), plugin API: embedded Python, pybind11 graph    │
│                                                                             │
│  posSlice ──► host_bridge ──► mesh (N,3 f32)/(M,3 i32)                      │
│                  │                                                          │
│                  ▼                                                          │
│           voxelize.voxelize          ray-parity along +x, per (y,z) line    │
│                  │                   VoxelGrid{mask,origin,h}               │
│                  ▼                                                          │
│           loadcase.extract         NL ─► {forces[],constraints[],sf,E,ν,σy} │
│                  │                   heuristic regex / Claude (optional)    │
│                  ▼                                                          │
│           fem.solve_voxel_fem        hex8 Q1 @ 2×2×2 GP, K assembled in     │
│                  │                   chunks, splu (pyamg>400k DOF optional)│
│                  ▼                                                          │
│           mapping.plan_from_stress   utilization=VM/(σy/sf); per z-band:    │
│                  │                   reinforce cols ≥0.6·allow or p95 ≥1.0  │
│                  │                   relax cols ≤0.2 when band median cold  │
│                  ▼                                                          │
│  posPrepareInfill ─► mutate.apply_plan_to_object                            │
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
| `fem` | `solve_voxel_fem(grid, fixed_faces, load_face, F, E, ν)` → `FemResult` | active-node compaction; `load_patch="far"` keeps the load away from clamped faces |
| `loadcase` | `LoadCase`, `extract_load_case` | validates schema; LLM path is strict-JSON with heuristic fallback |
| `mapping` | `plan_from_stress` → `Plan(actions[])`; `region_boundary_length_mm` | masks are **x-major** `(nx, ny)` |
| `host_bridge` | defensive adapters: `probe`, `iter_print_objects`, `get_mesh`, surface R/W | every call tries multiple attribute spellings; never raises into the slicer |
| `mutate` | `MutationConfig`, `apply_plan_to_object` | centroid classification; caps extra perimeters |
| `receipt` | constants + `receipt_block(stats)` | cites sources; marks estimates as estimates |
| `pipeline` | `EcoSlicePipeline` — hooks + `analyze_mesh` + offline driver | caches analyses per object key |

## Design decisions

1. **Mutate geometry/classification, not config.** The plugin API exposes live-graph mutation
   (`Surface.extra_perimeters`, `fill_surfaces` reclassification) but not config values
   (`fill_density`, `wall_loops`). We work *with* that: existing config applies to what we produce.
2. **x-major masks everywhere.** One convention (`(nx, ny)`) after an indexing bug taught us why.
3. **Defensive host bridge.** The API is weeks old and barely documented; `host_bridge.probe(ctx)`
   reports which access paths exist on the running build, and the receipt logs capabilities.
4. **Honest economics.** Savings are framed as *"vs blanket-strengthening at equal safety factor"* —
   a like-for-like comparison the FEM justifies — plus authoritative footer diffs from real G-code.
5. **Single-file bundle via AST transform.** `tools/build_plugin.py` strips intra-package imports,
   flattens modules into one namespace, preserves `__future__`, and appends a thin adapter
   (`execute(ctx)` dispatch by hook name). Tests import the bundle standalone.
6. **Never crash the slicer.** Every hook wraps its body in try/except with logged context.

## Performance envelope

| resolution | voxels | DOF (approx) | solve time |
|---|---|---|---|
| 32³ box part | ~300–3k | 2–20k | 0.02 s |
| 48³ | ~10k | ~60k | ~0.5 s |
| 64³ | ~30k | ~180k | ~2–4 s |

splu handles all of these; the pyamg path exists for >400k-DOF future cases (optional dep).
