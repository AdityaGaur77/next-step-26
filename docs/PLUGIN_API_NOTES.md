# OrcaSlicer Plugin API — verified notes

Source of truth: shallow clone of `OrcaSlicer/OrcaSlicer` (main, Aug 2026), paths below are
relative to the repo root. Everything here was read from code, not docs.

## Module-level contract (`src/slic3r/plugin/PythonPluginBridge.cpp`)

- The embedded interpreter injects a top-level **`orca`** module.
- Exactly ONE package class per plugin, decorated with **`@orca.plugin`**, deriving from
  **`orca.base`** (bound as `py::class_<PyPluginPackage>(m, "base")`). Multiple `@orca.plugin`
  classes are rejected.
- During load the host calls its **`register_capabilities()`**; inside it you call
  **`orca.register_capability(Cls)`** once per capability. `Cls` must inherit a registered
  capability base, else: "Registered class must inherit from a PluginCapability base".
- Loader refuses `.py` plugins whose PEP 723 metadata lacks **`name`**
  (PluginLoader.cpp: "Side-loaded .py plugin is missing required PEP 723 metadata: 'name'").
  Dependencies come from the PEP 723 `dependencies` list and are installed via bundled **uv**
  (`uv pip install --target`, 120 s timeout). `.whl` files may also be shipped alongside.

## Slicing pipeline capability (`src/slic3r/plugin/pluginTypes/slicingPipeline/`)

- Base class in Python: **`orca.slicing.SlicingPipelineCapabilityBase`**
  (`py::class_<SlicingPipelinePluginCapability,...>(slicing, "SlicingPipelineCapabilityBase")`).
- Override **`get_name()`** and implement **`execute(ctx) -> orca.ExecutionResult`**.
- Step enum: **`orca.slicing.Step`** with values
  `posSlice, posPerimeters, posEstimateCurledExtrusions, posPrepareInfill, posInfill, posIroning,
  posContouring, posSupportMaterial, posDetectOverhangsForLift, posSimplifyPath, psWipeTower,
  psSkirtBrim, psGCodePostProcess`.
  ⭐ Source comment on `posPrepareInfill`: *"after prepare_infill, before make_fills:
  editing fill_surfaces here CASCADES"* — our chosen hook is officially the right one.
  (`posInfill` explicitly does NOT regenerate fills.)
- `orca.slicing.unscale(coord)` helper exists for scaled-coord conversion.

### ctx = `orca.slicing.SlicingPipelineContext` (all readonly)
| member | notes |
|---|---|
| `step` | Step enum |
| `print` | py object of live `Print` (null at psGCodePostProcess) |
| `object` | current `PrintObject` (null for print-wide steps / postprocess) |
| `gcode_path` | path string, ONLY at psGCodePostProcess — edit the file in place |
| `host`, `output_name` | postprocess only ("File", "OctoPrint", …) |
| `config_value(key)` | read-only config access on the live graph |
| `cancelled()` | honor between heavy steps |

### Return value
`orca.ExecutionResult` with statics `.success(message="", data="")`, `.skipped(message)`,
`.failure(status, message, data)`; instances carry `.status/.message/.data`.

## Capability base services (`orca.PythonPluginBase`, inherited by all capabilities)
`get_config()` → JSON string · `save_config(json_str)` · `get_default_config()` ·
`get_config_version()` · `has_config_ui()` / `get_config_ui()` (return HTML to replace the
default JSON editor) · `on_load()` / `on_unload()`.

## Mesh access (`src/slic3r/plugin/host/PluginHostMesh.cpp`)
`orca.host` submodule exposes mesh wrappers with numpy-returning methods:
`vertices()`, `triangles()`, plus `vertex_count/triangle_count/facets_count/volume/
bounding_box/is_manifold/face_normals/vertex(i)/triangle(i)`.
⭐ Open item: exact accessor from a PrintObject/ModelObject to its TriangleMesh wrapper
(try `obj.mesh()`, `obj.raw_mesh()`, or host functions — confirm in spike).

## Remaining runtime unknowns (spike targets)
1. Iterating layers/regions off `ctx.print` / `ctx.object` bindings (attribute spellings,
   scaled units for `print_z`, expolygon point coordinates).
2. Object→mesh accessor name.
3. `surface_type` accepted values as strings vs enum assignment.
4. Whether `ExecutionResult.failure` status arg accepts "error" (enum vs str).
