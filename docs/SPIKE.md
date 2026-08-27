# Day-1 Spike — the GO/NO-GO gate

**Question:** can a pipeline plugin mutate the live slicing graph and change exported G-code?

The plugin *contract* is no longer a guess: `docs/PLUGIN_API_NOTES.md` documents the real API,
read from OrcaSlicer's source (`@orca.plugin` package → `register_capabilities()` →
`orca.register_capability`, `SlicingPipelineCapabilityBase.execute(ctx)`, ctx with
`step/print/object/gcode_path/config_value`). The spike now only needs to confirm **runtime**
behavior on the installed nightly.

## Steps (30–60 min)

1. **Pin a nightly.** Download the latest OrcaSlicer nightly (≥ 2.4.2). Record the exact
   build hash here: `nightly: ________ (date: ____)`. Do not chase `main` after this.
2. **Locate the plugin dir.** GUI: Help → Show Configuration Folder → create/open `orca_plugins/`.
   CLI fallback: `%APPDATA%/OrcaSlicer/...` — confirm from the folder the GUI opens.
3. **Drop the spike.** Copy `spike/spike_extra_perimeters.py` into `orca_plugins/`.
4. **Slice a simple tall box** (e.g. 100×10×10 mm cantilever, 2 walls, 15% grid infill).
5. **Read the console/log** for `ECOSLICE SPIKE:` lines and record:

| probe | result |
|---|---|
| mesh via `model_object().volumes()[0].mesh()` | ☐ ok ☐ shape/dtype differs: ______ |
| `fill_surfaces.surfaces` yields Surface refs | ☐ ok ☐ differs: ______ |
| `extra_perimeters` write | ☐ MUTATION OK ☐ NO-OP ☐ FAILED |
| `surface_type = SurfaceType.stInternalSolid` | ☐ MUTATION OK ☐ FAILED |
| plan lands on the part (root thicker, not tip) | ☐ yes ☐ no — check `compute_alignment` |
| preview shows thicker walls near spike layer | ☐ yes ☐ no |

6. **G-code diff.** Slice once with the spike disabled (rename file), once enabled:
   `python tools/gcode_diff.py baseline.gcode spiked.gcode`
7. **Record which `host_bridge` paths matched** (the plugin logs its capability probe).

## Decision matrix

- extra_perimeters changes G-code → **GO for full Idea 1** as built.
- Only reclassification works → flip `MutationConfig(enable_solid_infill=True,
  add_perimeters=0)` emphasis; solid-infill becomes the primary lever.
- Neither works → Ideas 1 falls back to *advisory mode*: FEM plan + receipt + overlay guidance
  while EcoSupport/EcoLedger carry mutation value. Core analysis code is unchanged.

## Update targets after the spike

- `src/ecoslice/host_bridge.py`: prune access paths that don't exist; add discovered spellings.
- `plugin/ecoslice_core.py` adapter: replace hook-name sniffing with the real registration API
  observed in the nightly (update `tools/build_plugin.py` ADAPTER section accordingly).
- Add the observed ctx shape to `tests/mocks.py` so mocks mirror reality.

## Result — 2026-08-26 — **GO**

nightly: `2.5.0-dev` (date: 2026-08-26), test part: 100×10×10 mm cube, 0.16 mm layers

| probe | result |
|---|---|
| mesh via `ModelVolume.mesh()` | needs `numpy` declared in plugin deps (`ImportError` otherwise) |
| `fill_surfaces.surfaces` yields Surface refs | ok — via `collection.surfaces` (layer z=0.2) |
| `extra_perimeters` write | **MUTATION OK** (0 -> 2) |
| `surface_type = SurfaceType.stInternalSolid` | **MUTATION OK** (stBottom -> stInternalSolid, `is_solid_now: true`) |
| plan lands on the part (root thicker, not tip) | n/a — single-surface probe; mutation confirmed live |
| G-code changes | yes — 402 KB -> 1.14 MB (~2.8x toolpaths), est. 40m -> 1h54m |

Decision matrix row 1 applies: **extra_perimeters changes G-code -> GO for full Idea 1 as built.**

Hard-won host facts (now in `docs/PLUGIN_API_NOTES.md`):
- Manual copy into `orca_plugins/` no longer works — discovery finds 0 manifests. Install via
  **Plugins dialog -> Local install** (host creates the folder + manifest sidecar itself).
- PEP 723 identity keys (`name`/`version`/...) must sit in a **`[tool.orcaslicer.plugin]`**
  TOML table; top-level keys are ignored and missing `name` fails the install.
- Installed plugins need the **Activate toggle** in the Plugins dialog (plugin-level
  `enabled` in `.install_state.json`).
- Capabilities are picker-selected per process profile: Process settings -> **Others** page ->
  **Slicing Pipeline Plugin** group (drives the `slicing_pipeline_plugin` config option; the
  C++ hook skips everything if it is empty).
- `psGCodePostProcess` rewrites a temp `.pp` copy before it becomes the exported file —
  appending markers works and survives export; idempotency still required.
