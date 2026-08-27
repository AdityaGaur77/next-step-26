# OrcaSlicer Plugin API — verified notes

Source of truth: `OrcaSlicer/OrcaSlicer` (main, Aug 2026), paths relative to the repo root.
Everything here was read from the C++ bindings, not from the wiki. Items marked ⚠️ are the
ones that bit us: the obvious Python-shaped guess is wrong.

## Module-level contract (`src/slic3r/plugin/PythonPluginBridge.cpp`)

- The embedded interpreter injects a top-level **`orca`** module.
- Exactly ONE package class per plugin, decorated with **`@orca.plugin`**, deriving from
  **`orca.base`**. Multiple `@orca.plugin` classes are rejected.
- During load the host calls **`register_capabilities()`**; inside it call
  **`orca.register_capability(Cls)`** once per capability.
- Loader refuses `.py` plugins whose PEP 723 metadata lacks **`name`** (PluginLoader.cpp).
  ⚠️ Identity keys live in a **`[tool.orcaslicer.plugin]`** TOML table inside the PEP 723
  block (PluginFsUtils.cpp `parse_pep723_toml`) — top-level `name`/`version` are IGNORED:
  ```python
  # /// script
  # requires-python = ">=3.12"
  # dependencies = []
  #
  # [tool.orcaslicer.plugin]
  # name = "My Plugin"      # REQUIRED or install fails
  # version = "1.0.0"
  # description = "..."
  # author = "..."
  # ///
  ```
  Dependencies come from the PEP 723 `dependencies` list, installed with bundled **uv**
  (`uv pip install --target`, 120 s timeout) — keep the list short.
- ⚠️ **`get_default_config()` must return a dict**, not a JSON string. The trampoline
  (`PyPluginTrampoline.hpp`) logs `returned <type>, not an object; restoring an empty config`
  and discards anything else. `get_config()` on the other hand *returns* a JSON string —
  `json.loads()` it. `save_config()` takes a JSON string.
- `has_config_ui()` / `get_config_ui()` replace the default JSON editor with an HTML page;
  inside it, `window.orca.getConfig()` / `window.orca.saveConfig()` reach the same config.

## Slicing pipeline capability (`src/slic3r/plugin/pluginTypes/slicingPipeline/`)

- Base class: **`orca.slicing.SlicingPipelineCapabilityBase`**; override **`get_name()`** and
  **`execute(ctx) -> orca.ExecutionResult`**.
- ⚠️ **There is no step subscription.** `execute(ctx)` is called for *every* step in the enum;
  the plugin must dispatch on `ctx.step` and return `skipped` for steps it does not handle.
- Step enum **`orca.slicing.Step`**: `posSlice, posPerimeters, posEstimateCurledExtrusions,
  posPrepareInfill, posInfill, posIroning, posContouring, posSupportMaterial,
  posDetectOverhangsForLift, posSimplifyPath, psWipeTower, psSkirtBrim, psGCodePostProcess`.
  Source comment on `posPrepareInfill`: *"after prepare_infill, before make_fills: editing
  fill_surfaces here CASCADES"* — that is our mutation hook. `posInfill` does NOT regenerate fills.
- ⚠️ `psGCodePostProcess` *"may fire more than once per slice (file export and/or upload)"* —
  the post-processor must be idempotent.
- `orca.slicing.unscale(coord)` converts scaled coords to mm (libslic3r scale: 1 mm = 1e6 units).

### ctx = `orca.slicing.SlicingPipelineContext` (readonly)
| member | notes |
|---|---|
| `step` | Step enum |
| `print` | live `Print`, or None at psGCodePostProcess |
| `object` | current `PrintObject` on object-scoped steps, None for print-wide steps. const_cast'd to mutable on the way out |
| `gcode_path` | path string, ONLY at psGCodePostProcess — edit the file in place |
| `host`, `output_name`, `orca_version` | postprocess metadata |
| `config_value(key)` | live config on in-pipeline steps; export config at postprocess |
| `cancelled()` | honor between heavy steps |

`orca.ExecutionResult` statics: `.success(message="", data="")`, `.skipped(message)`,
`.failure(status, message, data="")`.

## Slicing graph (`src/slic3r/plugin/host/PluginHostSlicing.cpp`)

| object | accessor | shape |
|---|---|---|
| `Print` | `objects()` | **method** → [PrintObject] |
| `PrintObject` | `id()`, `layers()`, `support_layers()`, `model_object()`, `trafo()`, `bounding_box()`, `config_value(k)` | all **methods** |
| `Layer` | `print_z`, `slice_z`, `height` (attrs); `regions()`, `lslices()`, `make_slices()` (methods) | |
| `LayerRegion` | `slices`, `fill_surfaces` (readonly attrs), `perimeters`, `fills`, `layer()`, `region()` | ⚠️ `fill_surfaces` is a **SurfaceCollection**, not a list |
| `SurfaceCollection` | `.surfaces` (property → [Surface] live refs), `size()`, `filter_by_type(t)`, `set()/append()/clear()` | `list(collection)` raises TypeError |
| `Surface` | `surface_type`, `extra_perimeters`, `thickness`, `bridge_angle` (read/write), `expolygon`, `area()`, `is_solid()`, `is_internal()`, `is_top()`… | ⚠️ `surface_type` is the **`SurfaceType` enum**, not a string |
| `SurfaceType` | `stTop, stBottom, stBottomBridge, stInternalAfterExternalBridge, stInternal, stInternalSolid, stInternalBridge, stSecondInternalBridge, stInternalVoid, stPerimeter` (`export_values()`) | |
| `ExPolygon` | `contour` (live Polygon), `holes`, `area()`, `contains(pt)` | |
| `Polygon` | `points` ([Point] refs), `as_array()` (writable int64 (N,2) view), `centroid()`, `area()` | `centroid()` beats averaging vertices |
| `Point` | `.x` / `.y` int properties in scaled units | mm = value / 1e6 |

**Lifetime rule (from the source):** every object handed out is a non-owning reference into the
live slicing graph, valid *only* while `execute(ctx)` runs. Container-replacing mutators
(`SurfaceCollection.set/append/clear`, `Polygon.set_points`, `ExPolygon.set_holes`) invalidate
previously obtained references. Do not stash references or numpy views across calls — copy.

## Mesh access (`PluginHostMesh.cpp`, `PluginHostModel.cpp`)

`PrintObject.model_object()` → `ModelObject.volumes()` → `ModelVolume.mesh()` → `TriangleMesh`
with `vertices()` (N,3 float32) / `triangles()` (M,3 int32), plus `volume()`, `bounding_box()`,
`is_manifold()`. Meshes are immutable copy-on-write snapshots.

⚠️ **Frames.** `TriangleMesh` is in the volume's *local, untransformed* mm frame. Getting to the
frame the sliced polygons live in takes `ModelVolume.matrix()` (volume→object) and then
`PrintObject.trafo()` (object→print). `PrintObject.bounding_box()` returns the object's XY
bbox in scaled coords and the source states *"the sliced polygons live in this same frame"* —
so EcoSlice anchors its analysis grid by matching that bounding box rather than trusting any
single origin (`ecoslice.mutate.compute_alignment`).

## Still to confirm at runtime (spike)
1. That an `extra_perimeters` / `surface_type` write at `posPrepareInfill` visibly changes the
   exported G-code on the pinned nightly.
2. Whether `ctx.object` is populated at `posPrepareInfill` for every object (we handle both).
3. Config-UI HTML plumbing (`window.orca.saveConfig`) — untested, `has_config_ui()` is False.
