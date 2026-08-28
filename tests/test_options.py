from __future__ import annotations

import numpy as np
import pytest

from ecoslice.mapping import plan_from_stress
from ecoslice.options import (
    OVER_ALLOWABLE_SCORE_CAP,
    PRESETS,
    PRESETS_BY_KEY,
    build_options,
    estimate_material,
    options_table,
    strength_confidence,
)
from ecoslice.voxelize import VoxelGrid


def _grid(nx=20, ny=6, nz=6, h=1.0):
    return VoxelGrid(mask=np.ones((nx, ny, nz), dtype=bool), origin=np.zeros(3), spacing=(h, h, h))


def _stress_field(grid, hot_slice=5, hot=30.0, cold=2.0):
    vm = np.full(grid.mask.shape, cold)
    vm[:hot_slice] = hot
    return vm


def _plan(grid, vm, **kw):
    kw.setdefault("yield_mpa", 45.0)
    kw.setdefault("safety_factor", 2.0)
    kw.setdefault("layer_height_mm", 1.0)
    return plan_from_stress(grid, vm, **kw)


def test_material_estimate_counts_solid_infill_not_just_walls():
    grid = _grid()
    plan = _plan(grid, _stress_field(grid))
    m = estimate_material(plan, grid, add_perimeters=2, layer_height_mm=1.0, infill_density=0.15)
    assert m["added_wall_grams"] > 0
    assert m["added_infill_grams"] > 0, "reclassified sparse->solid infill must be in the total"
    assert m["added_grams"] == pytest.approx(
        m["added_wall_grams"] + m["added_infill_grams"], abs=1e-3
    )
    # The infill term dominates for an area-like hotspot; leaving it out understates badly.
    assert m["added_infill_grams"] > m["added_wall_grams"]


def test_disabling_solid_infill_drops_only_the_infill_term():
    grid = _grid()
    plan = _plan(grid, _stress_field(grid))
    with_solid = estimate_material(plan, grid, add_perimeters=2, layer_height_mm=1.0)
    without = estimate_material(
        plan, grid, add_perimeters=2, layer_height_mm=1.0, enable_solid_infill=False
    )
    assert without["added_infill_grams"] == 0.0
    assert without["added_wall_grams"] == pytest.approx(with_solid["added_wall_grams"])


def test_blanket_baseline_follows_the_part_not_the_bounding_rectangle():
    """A part that occupies half its bounding box must not be charged for the whole box."""
    grid = _grid(nx=20, ny=8, nz=4)
    mask = grid.mask.copy()
    mask[:, 4:, :] = False  # part fills only half the y extent
    grid = VoxelGrid(mask=mask, origin=grid.origin, spacing=grid.spacing)
    vm = _stress_field(grid)
    vm[~mask] = 0.0
    plan = _plan(grid, vm)
    m = estimate_material(plan, grid, add_perimeters=2, layer_height_mm=1.0)

    occupied_cells = sum(int(a.occupied_xy.sum()) for a in plan.actions)
    full_rect_cells = sum(int(a.reinforce_xy.size) for a in plan.actions)
    assert occupied_cells < full_rect_cells
    assert m["uniform_baseline_grams"] > m["added_grams"] > 0


def test_added_time_and_energy_track_added_volume():
    grid = _grid()
    plan = _plan(grid, _stress_field(grid))
    light = estimate_material(plan, grid, add_perimeters=1, layer_height_mm=1.0)
    heavy = estimate_material(plan, grid, add_perimeters=3, layer_height_mm=1.0)
    assert heavy["added_print_time_s"] > light["added_print_time_s"] > 0
    assert heavy["added_energy_kwh"] > light["added_energy_kwh"] > 0


def test_confidence_is_capped_when_the_part_is_over_allowable():
    grid = _grid()
    vm = _stress_field(grid, hot=40.0)  # allowable is 45/2 = 22.5 MPa
    plan = _plan(grid, vm)
    c = strength_confidence(plan, grid, vm)
    assert plan.max_vm_mpa / plan.allowable_mpa > 1.0
    assert c.score <= OVER_ALLOWABLE_SCORE_CAP
    assert c.label != "high", "an over-stressed part must never read high confidence"
    assert any("OVER the allowable" in r for r in c.reasons)


def test_confidence_is_high_for_a_well_covered_part_inside_margin():
    grid = _grid(nx=16, ny=10, nz=10)
    vm = _stress_field(grid, hot=16.0, cold=1.0)  # 16 / 22.5 = 0.71x allowable
    plan = _plan(grid, vm)
    c = strength_confidence(plan, grid, vm)
    assert c.score >= 0.75 and c.label == "high"
    assert any("inside margin" in r for r in c.reasons)
    assert any("nothing at risk" in r for r in c.reasons)


def test_confidence_penalises_an_under_resolved_mesh():
    coarse = _grid(nx=40, ny=2, nz=2)
    fine = _grid(nx=40, ny=10, nz=10)
    scores = []
    for g in (coarse, fine):
        vm = _stress_field(g, hot=16.0, cold=1.0)
        scores.append(strength_confidence(_plan(g, vm), g, vm).score)
    assert scores[1] > scores[0]


def test_three_options_are_ordered_and_distinct():
    grid = _grid()
    vm = _stress_field(grid)
    reports = build_options(grid, vm, yield_mpa=45.0, safety_factor=2.0, layer_height_mm=1.0)
    assert [r.preset.key for r in reports] == ["eco", "balanced", "max_strength"]
    added = [r.material["added_grams"] for r in reports]
    assert added[0] < added[1] < added[2], f"Eco should be lightest, Max heaviest: {added}"
    assert reports[0].material["added_infill_grams"] == 0.0  # Eco leaves infill alone
    table = options_table(reports)
    for r in reports:
        assert r.preset.name in table


def test_presets_are_reachable_by_key():
    assert set(PRESETS_BY_KEY) == {p.key for p in PRESETS}
    assert PRESETS_BY_KEY["max_strength"].enable_relax is False
    assert PRESETS_BY_KEY["eco"].enable_solid_infill is False
