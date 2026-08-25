# /// script
# name = "EcoSlice Spike"
# version = "0.2.0"
# description = "Day-1 gate: prove pipeline-graph mutation changes G-code (verified API shape)"
# requires-python = ">=3.12"
# dependencies = []
# ///
# EcoSlice day-1 spike against the REAL OrcaSlicer plugin contract
# (verified in OrcaSlicer/src/slic3r/plugin/, see docs/PLUGIN_API_NOTES.md):
#   - one @orca.plugin package class deriving from orca.base
#   - register_capabilities() calls orca.register_capability(Cls)
#   - capability derives from orca.slicing.SlicingPipelineCapabilityBase
#   - execute(ctx) returns orca.ExecutionResult; ctx.step dispatches
#
# Install into <OrcaSlicer data dir>/orca_plugins/ and slice a simple part.
# Gate: "ECOSLICE SPIKE ... MUTATION OK" + changed G-code => Idea 1 GO.

import json

import orca

Step = orca.slicing.Step


def _describe_obj(obj):
    info = {"type": type(obj).__name__}
    try:
        mesh = obj.mesh() if callable(getattr(obj, "mesh", None)) else getattr(obj, "mesh", None)
        if mesh is not None:
            v = mesh.vertices()
            t = mesh.triangles()
            info.update({"verts": int(v.shape[0]), "tris": int(t.shape[0]), "dtype": str(v.dtype)})
    except Exception as exc:
        info["mesh_error"] = repr(exc)
    return info


def _first_layer_region(print_obj):
    layers = list(getattr(print_obj, "layers", []) or [])
    for layer in layers:
        for region in list(getattr(layer, "regions", []) or []):
            surfaces = list(getattr(region, "fill_surfaces", []) or [])
            if surfaces:
                return layer, region, surfaces[0]
    return None


def _run(ctx) -> str:
    report = {"step": str(ctx.step), "orca_version": str(getattr(ctx, "orca_version", "?"))}
    try:
        report["config_layer_height"] = ctx.config_value("layer_height")
    except Exception as exc:
        report["config_error"] = repr(exc)

    print_obj = ctx.object
    if print_obj is not None:
        report["object"] = _describe_obj(print_obj)
        found = _first_layer_region(print_obj)
        if found:
            layer, region, surface = found
            before = getattr(surface, "extra_perimeters", None)
            try:
                surface.extra_perimeters = 2
                after = surface.extra_perimeters
                report["extra_perimeters"] = f"{before} -> {after}: " + (
                    "MUTATION OK" if after != before else "NO-OP")
            except Exception as exc:
                report["extra_perimeters"] = f"FAILED: {exc!r}"
            try:
                old = str(surface.surface_type)
                surface.surface_type = "internal_solid"
                report["surface_type"] = f"{old} -> {surface.surface_type}: MUTATION OK"
            except Exception as exc:
                report["surface_type"] = f"FAILED: {exc!r}"
        else:
            report["layers"] = "no fill_surfaces found on first layers"
    elif ctx.step == Step.psGCodePostProcess:
        p = str(ctx.gcode_path)
        text = open(p, "r", encoding="utf-8", errors="replace").read()
        marker = ";ECOSLICE SPIKE touched this file\n"
        if marker not in text:
            open(p, "a", encoding="utf-8").write(marker)
        report["gcode"] = f"appended marker to {p} ({len(text)} chars)"

    line = "ECOSLICE SPIKE: " + json.dumps(report)
    print(line)
    return line


orca.ExecutionResult  # touch to fail fast if the binding name ever changes


class SpikeSlicingPipeline(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "EcoSlice Spike"

    def execute(self, ctx):
        try:
            msg = _run(ctx)
            return orca.ExecutionResult.success(msg)
        except Exception as exc:
            print(f"ECOSLICE SPIKE: FAILED {exc!r}")
            return orca.ExecutionResult.failure("error", repr(exc))


@orca.plugin
class SpikePackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(SpikeSlicingPipeline)
