import logging

import pytest

from ecoslice.mutate import MutationConfig, compute_alignment
from ecoslice.pipeline import EcoSlicePipeline
from ecoslice.voxelize import box_mesh

from mocks import SurfaceType, cantilever_ctx

DESC = "shelf bracket holding 8 kg, load downward; screwed onto left wall"


@pytest.fixture(scope="module")
def analysis():
    pipe = EcoSlicePipeline(description=DESC, resolution=40)
    v, t = box_mesh(100.0, 10.0, 10.0)
    return pipe.analyze_mesh(v, t, DESC, "obj")


def test_end_to_end_analysis(analysis):
    assert analysis.plan.max_vm_mpa > 0
    assert analysis.plan.allowable_mpa == pytest.approx(45.0 / 2.0)
    assert analysis.wall_seconds < 60
    assert analysis.grid.volume_mm3 == pytest.approx(10_000.0, rel=0.01)


def _surface_positions(obj, bed_offset=(120.0, 90.0)):
    ox, oy = bed_offset
    out = []
    for layer in obj.layers():
        z_mid = layer.print_z - layer.height / 2
        for region in layer.regions():
            for surf in region.fill_surfaces.surfaces:
                pts = surf.expolygon.contour.points
                x_mid = (pts[0].x + pts[2].x) / 2 / 1e6 - ox
                out.append((x_mid, z_mid, surf))
    return out


def test_hooks_reinforce_root_and_relax_tip():
    pipe = EcoSlicePipeline(description=DESC, resolution=40)
    ctx, obj = cantilever_ctx()
    pipe.on_pos_slice(ctx)
    assert pipe.analyses, "posSlice should have analyzed the object"

    pipe.on_pos_prepare_infill(ctx)

    reinforced = relaxed = 0
    for x_mid, z_mid, surf in _surface_positions(obj):
        if x_mid < 25.0 and z_mid <= 3.0:
            if surf.extra_perimeters >= 3 and surf.surface_type is SurfaceType.stInternalSolid:
                reinforced += 1
        elif x_mid > 85.0 and 3.0 < z_mid < 7.0:
            if surf.extra_perimeters == 0:
                relaxed += 1
    assert reinforced > 0, "root layers should gain extra perimeters + solid infill"
    assert relaxed > 0, "low-stress tip layers should be relaxed"


def test_plan_is_aligned_to_the_bed_position():
    """Mesh arrives in object coords; slices sit at the object's bed position."""
    pipe = EcoSlicePipeline(description=DESC, resolution=40)
    ctx, obj = cantilever_ctx(bed_offset=(120.0, 90.0))
    pipe.on_pos_slice(ctx)
    analysis = next(iter(pipe.analyses.values()))
    alignment = compute_alignment(obj, analysis.grid)
    assert alignment.dx == pytest.approx(120.0, abs=0.5)
    assert alignment.dy == pytest.approx(90.0, abs=0.5)

    pipe.on_pos_prepare_infill(ctx)
    root = [s for x, z, s in _surface_positions(obj) if x < 15.0 and z <= 2.0]
    tip = [s for x, z, s in _surface_positions(obj) if x > 90.0 and z <= 2.0]
    assert max(s.extra_perimeters for s in root) > max(s.extra_perimeters for s in tip)


def test_unaligned_plan_would_miss_every_surface():
    """Without alignment the plan lands off the part — the bug this guards."""
    from ecoslice.mutate import FrameAlignment, apply_plan_to_object

    pipe = EcoSlicePipeline(description=DESC, resolution=40)
    ctx, obj = cantilever_ctx()
    pipe.on_pos_slice(ctx)
    analysis = next(iter(pipe.analyses.values()))
    stats = apply_plan_to_object(
        obj, analysis.plan, analysis.grid, pipe.cfg, FrameAlignment(0.0, 0.0, 0.0)
    )
    assert stats.perimeters_added == 0


def test_real_host_frames_mesh_on_plate_slices_local():
    """The real OrcaSlicer split observed on the nightly: get_mesh() comes back
    in plate coordinates (trafo applied) while fill_surfaces stay in the
    object-local frame. Alignment must anchor on the slice bbox, or every
    surface classifies as 'outside' and nothing is mutated (receipt says
    reinforced N layers, 0 perimeter-lines added)."""
    pipe = EcoSlicePipeline(description=DESC, resolution=40)
    ctx, obj = cantilever_ctx(bed_offset=(120.0, 90.0), slices_frame="local")
    pipe.on_pos_slice(ctx)
    analysis = next(iter(pipe.analyses.values()))
    alignment = compute_alignment(obj, analysis.grid)
    assert alignment.dx == pytest.approx(-120.0, abs=0.5)
    assert alignment.dy == pytest.approx(-90.0, abs=0.5)

    pipe.on_pos_prepare_infill(ctx)
    assert pipe._last_mutation.perimeters_added > 0, "plan must actually land on local-frame surfaces"
    positions = _surface_positions(obj, bed_offset=(0.0, 0.0))
    root = [s for x, z, s in positions if x < 15.0 and z <= 2.0]
    tip = [s for x, z, s in positions if x > 90.0 and z <= 2.0]
    assert max(s.extra_perimeters for s in root) > max(s.extra_perimeters for s in tip)


def test_single_surface_spanning_whole_part_is_still_mutated():
    """Real parts slice into few large surfaces: one rectangle per layer
    spanning hot AND cold zones. Centroid-only classification reads 'neutral'
    and mutates nothing (the second bug found on the real host) — outline
    overlap must catch the hot end."""
    pipe = EcoSlicePipeline(description=DESC, resolution=40)
    ctx, obj = cantilever_ctx(segments_x=1)
    pipe.on_pos_slice(ctx)
    pipe.on_pos_prepare_infill(ctx)
    assert pipe._last_mutation.perimeters_added > 0
    positions = _surface_positions(obj)
    root_eps = [s.extra_perimeters for x, z, s in positions if z <= 2.0]
    tip_eps = [s.extra_perimeters for x, z, s in positions if 3.0 < z < 7.0]
    assert max(root_eps) >= 3, "layers overlapping the hot columns must gain perimeters"
    assert min(tip_eps) == 0, "cold layers spanning only relax columns must relax"


def test_legacy_duck_typed_host_still_mutates():
    pipe = EcoSlicePipeline(description=DESC, resolution=32)
    ctx, obj = cantilever_ctx(legacy=True, bed_offset=(0.0, 0.0))
    pipe.on_pos_slice(ctx)
    pipe.on_pos_prepare_infill(ctx)
    eps = [s.extra_perimeters for layer in obj.layers for r in layer.regions for s in r.fill_surfaces]
    assert max(eps) >= 3


def test_postprocess_inserts_receipt_after_header():
    pipe = EcoSlicePipeline(resolution=24)
    v, t = box_mesh(60.0, 10.0, 6.0)
    pipe.analyze_mesh(v, t, "small bracket 4kg down, mounted on left wall", "obj0")
    gcode = "; generated by OrcaSlicer\nG28\n"
    out = pipe.on_gcode_postprocess(gcode)
    lines = out.splitlines()
    assert lines[0] == "; generated by OrcaSlicer"
    assert lines[1].startswith(";ECOSLICE BEGIN")
    assert lines.index("G28") > max(i for i, l in enumerate(lines) if l.startswith(";ECOSLICE"))


def test_postprocess_is_idempotent():
    """psGCodePostProcess can fire more than once per slice."""
    pipe = EcoSlicePipeline(resolution=24)
    v, t = box_mesh(60.0, 10.0, 6.0)
    pipe.analyze_mesh(v, t, "small bracket 4kg down, mounted on left wall", "obj0")
    once = pipe.on_gcode_postprocess("; generated by OrcaSlicer\nG28\n")
    twice = pipe.on_gcode_postprocess(once)
    assert twice.count(";ECOSLICE BEGIN") == 1
    assert twice == once


def test_receipt_reports_applied_mutations():
    pipe = EcoSlicePipeline(description=DESC, resolution=40)
    ctx, _ = cantilever_ctx()
    pipe.on_pos_slice(ctx)
    pipe.on_pos_prepare_infill(ctx)
    assert pipe._last_mutation.perimeters_added > 0
    receipt = pipe.on_gcode_postprocess("; generated by OrcaSlicer\nG28\n")
    added = pipe._last_mutation.perimeters_added
    removed = pipe._last_mutation.perimeters_removed
    assert f"{added} extra perimeter-lines added" in receipt
    assert f"{removed} perimeter-lines removed" in receipt


def test_mutation_config_gates():
    cfg_off = MutationConfig(add_perimeters=2, enable_relax=False)
    pipe = EcoSlicePipeline(cfg=cfg_off, description=DESC, resolution=32)
    ctx, obj = cantilever_ctx()
    pipe.on_pos_slice(ctx)
    pipe.on_pos_prepare_infill(ctx)
    eps = {s.extra_perimeters for _, _, s in _surface_positions(obj)}
    assert 0 not in eps, "relax disabled -> baseline extra_perimeters must survive"


def test_load_face_held_by_fixture_is_moved():
    pipe = EcoSlicePipeline(resolution=24)
    v, t = box_mesh(60.0, 20.0, 20.0)
    a = pipe.analyze_mesh(v, t, "shelf holding 5 kg downward, sitting on the desk", "obj0")
    assert a.plan.max_vm_mpa > 0, "a fully constrained load face must not silently zero the solve"
    assert any("load applied on" in n for n in a.notes)


def test_resolution_is_capped_for_bulky_parts():
    pipe = EcoSlicePipeline(resolution=96)
    v, t = box_mesh(80.0, 60.0, 40.0)
    a = pipe.analyze_mesh(v, t, "bracket 5kg down, bolted to left wall", "obj0")
    assert int(a.grid.mask.sum()) <= 150_000
    assert any("resolution reduced" in n for n in a.notes)


def test_never_crashes_on_bad_ctx(caplog):
    pipe = EcoSlicePipeline(resolution=16)

    class BadCtx:
        object = None
        objects = None

    with caplog.at_level(logging.WARNING):
        pipe.on_pos_slice(BadCtx())
        pipe.on_pos_prepare_infill(BadCtx())


def test_stats_aggregation_for_receipt():
    pipe = EcoSlicePipeline(resolution=20)
    v, t = box_mesh(50.0, 12.0, 8.0)
    a = pipe.analyze_mesh(v, t, "bracket 3kg down, mounted on left wall", "obj0")
    stats = pipe._aggregate_stats()
    assert stats["max_vm_mpa"] == pytest.approx(a.plan.max_vm_mpa, abs=0.5)
    assert "saved_vs_uniform_grams" in stats
    assert stats["load_case_source"] == a.load_case.source


def test_postprocess_quotes_the_slicers_own_footer():
    """The receipt must carry measured mass/time/energy, not only its own model."""
    pipe = EcoSlicePipeline(resolution=24)
    v, t = box_mesh(60.0, 10.0, 6.0)
    pipe.analyze_mesh(v, t, "small bracket 4kg down, mounted on left wall", "obj0")
    gcode = (
        "; generated by OrcaSlicer\n"
        "G28\n"
        "; filament used [g] = 12.34\n"
        "; estimated printing time (normal mode) = 1h 54m 12s\n"
    )
    out = pipe.on_gcode_postprocess(gcode)
    assert ";ECOSLICE measured mass   : 12.34 g" in out
    assert ";ECOSLICE measured time   : 1h54m" in out
    assert "kWh at 100 W" in out


def test_postprocess_stays_idempotent_with_a_footer_present():
    pipe = EcoSlicePipeline(resolution=24)
    v, t = box_mesh(60.0, 10.0, 6.0)
    pipe.analyze_mesh(v, t, "small bracket 4kg down, mounted on left wall", "obj0")
    gcode = (
        "; generated by OrcaSlicer\nG28\n"
        "; filament used [g] = 12.34\n"
        "; estimated printing time (normal mode) = 1h 54m 12s\n"
    )
    once = pipe.on_gcode_postprocess(gcode)
    twice = pipe.on_gcode_postprocess(once)
    assert twice.count(";ECOSLICE BEGIN") == 1
    assert twice == once


def test_savings_split_walls_from_solid_infill():
    pipe = EcoSlicePipeline(description=DESC, resolution=32, layer_height_mm=0.2)
    v, t = box_mesh(100.0, 10.0, 10.0)
    a = pipe.analyze_mesh(v, t, DESC, "obj0")
    s = a.savings
    assert s["added_wall_grams"] > 0
    assert s["added_infill_grams"] > 0
    assert s["added_grams"] == pytest.approx(
        s["added_wall_grams"] + s["added_infill_grams"], abs=1e-3
    )


def test_analysis_carries_confidence_and_three_options():
    pipe = EcoSlicePipeline(description=DESC, resolution=32, layer_height_mm=0.2)
    v, t = box_mesh(100.0, 10.0, 10.0)
    a = pipe.analyze_mesh(v, t, DESC, "obj0")
    assert a.confidence is not None and 0.0 <= a.confidence.score <= 1.0
    assert [o.preset.key for o in a.options] == ["eco", "balanced", "max_strength"]
    assert "confidence" in pipe._aggregate_stats()


def test_infill_density_is_read_from_the_profile():
    """A denser profile leaves less room for sparse->solid to add material."""
    pipe = EcoSlicePipeline(description=DESC, resolution=32)
    ctx, _ = cantilever_ctx()
    ctx._config = {"layer_height": 0.5, "sparse_infill_density": 40.0}
    pipe.on_pos_slice(ctx)
    assert pipe.infill_density == pytest.approx(0.40)
