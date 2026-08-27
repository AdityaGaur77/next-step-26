# /// script
# name = "EcoSlice Spike"
# version = "0.3.0"
# description = "Day-1 gate: prove pipeline-graph mutation changes G-code (verified API shape)"
# requires-python = ">=3.12"
# dependencies = []
# ///
# EcoSlice day-1 spike against the REAL OrcaSlicer plugin contract
# (verified in OrcaSlicer/src/slic3r/plugin/, see docs/PLUGIN_API_NOTES.md):
#   - one @orca.plugin package class deriving from orca.base
#   - register_capabilities() calls orca.register_capability(Cls)
#   - capability derives from orca.slicing.SlicingPipelineCapabilityBase
#   - execute(ctx) is called for EVERY step; dispatch on ctx.step
#   - LayerRegion.fill_surfaces is a SurfaceCollection -> use .surfaces
#   - Surface.surface_type is the SurfaceType enum, not a string
#
# Install into <OrcaSlicer data dir>/orca_plugins/ and slice a simple part.
# Gate: "ECOSLICE SPIKE ... MUTATION OK" + changed G-code => Idea 1 GO.

import json

import orca

Step = orca.slicing.Step


def _describe_mesh(print_obj):
    """PrintObject -> ModelObject -> ModelVolume.mesh() (local mm frame)."""
    info = {"type": type(print_obj).__name__}
    try:
        model_object = print_obj.model_object()
        volumes = list(model_object.volumes())
        info["volumes"] = len(volumes)
        mesh = volumes[0].mesh()
        v = mesh.vertices()
        t = mesh.triangles()
        info.update({"verts": int(v.shape[0]), "tris": int(t.shape[0]), "dtype": str(v.dtype)})
        info["volume_matrix_ok"] = tuple(volumes[0].matrix().shape) == (4, 4)
        info["trafo_ok"] = tuple(print_obj.trafo().shape) == (4, 4)
        info["bbox_scaled"] = list(print_obj.bounding_box())
    except Exception as exc:
        info["mesh_error"] = repr(exc)
    return info


def _surfaces_of(region):
    """Report which access path works, so the spike documents reality."""
    fs = region.fill_surfaces
    try:
        return list(fs.surfaces), "collection.surfaces"
    except AttributeError:
        pass
    try:
        return list(fs), "iterable"
    except TypeError as exc:
        return [], f"UNREACHABLE: {exc!r}"


def _first_fill_surface(print_obj):
    for layer in list(print_obj.layers()):
        for region in list(layer.regions()):
            surfaces, how = _surfaces_of(region)
            if surfaces:
                return layer, region, surfaces[0], how
    return None


def _solid_enum(surface):
    """orca.host.SurfaceType.stInternalSolid, resolved from the live value's own type."""
    return getattr(type(surface.surface_type), "stInternalSolid", "internal_solid")


def _probe(ctx, report):
    print_obj = ctx.object
    if print_obj is None:
        report["object"] = "None (print-wide step)"
        return
    report["object"] = _describe_mesh(print_obj)
    found = _first_fill_surface(print_obj)
    if not found:
        report["layers"] = "no fill_surfaces found on any layer"
        return
    layer, region, surface, how = found
    report["fill_surfaces_via"] = how
    report["layer_print_z"] = float(layer.print_z)

    before = surface.extra_perimeters
    try:
        surface.extra_perimeters = before + 2
        after = surface.extra_perimeters
        report["extra_perimeters"] = f"{before} -> {after}: " + (
            "MUTATION OK" if after != before else "NO-OP"
        )
    except Exception as exc:
        report["extra_perimeters"] = f"FAILED: {exc!r}"

    old = str(surface.surface_type)
    try:
        surface.surface_type = _solid_enum(surface)
        report["surface_type"] = f"{old} -> {surface.surface_type}: MUTATION OK"
        report["is_solid_now"] = bool(surface.is_solid())
    except Exception as exc:
        report["surface_type"] = f"FAILED (from {old}): {exc!r}"

    try:
        centroid = surface.expolygon.contour.centroid()
        report["centroid_mm"] = [
            orca.slicing.unscale(centroid.x),
            orca.slicing.unscale(centroid.y),
        ]
    except Exception as exc:
        report["centroid_error"] = repr(exc)


def _mark_gcode(ctx, report):
    path = str(ctx.gcode_path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    marker = ";ECOSLICE SPIKE touched this file\n"
    if marker not in text:
        with open(path, "a", encoding="utf-8") as f:
            f.write(marker)
    report["gcode"] = f"appended marker to {path} ({len(text)} chars)"


def _run(ctx) -> str:
    report = {"step": str(ctx.step), "orca_version": str(getattr(ctx, "orca_version", "?"))}
    try:
        report["config_layer_height"] = ctx.config_value("layer_height")
    except Exception as exc:
        report["config_error"] = repr(exc)

    if ctx.step == Step.psGCodePostProcess:
        _mark_gcode(ctx, report)
    else:
        _probe(ctx, report)

    line = "ECOSLICE SPIKE: " + json.dumps(report, default=str)
    print(line)
    return line


class SpikeSlicingPipeline(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "EcoSlice Spike"

    def get_default_config(self) -> dict:
        return {"enabled": True}

    def execute(self, ctx):
        if ctx.step not in (Step.posSlice, Step.posPrepareInfill, Step.psGCodePostProcess):
            return orca.ExecutionResult.skipped("step not probed by the spike")
        try:
            return orca.ExecutionResult.success(_run(ctx))
        except Exception as exc:
            print(f"ECOSLICE SPIKE: FAILED {exc!r}")
            return orca.ExecutionResult.failure("error", repr(exc))


@orca.plugin
class SpikePackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(SpikeSlicingPipeline)
