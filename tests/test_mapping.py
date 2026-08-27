import numpy as np
import pytest

from ecoslice.mapping import plan_from_stress, region_boundary_length_mm, plan_summary_table
from ecoslice.voxelize import box_mesh, voxelize


def _grid_and_stress():
    v, t = box_mesh(40.0, 10.0, 10.0)
    grid = voxelize(v, t, resolution=20)
    vm = np.zeros(grid.shape)
    x = (np.arange(grid.shape[0]) + 0.5) * grid.h
    decay = np.exp(-x / 12.0)
    vm[:] = 30.0 * decay[:, None, None] * grid.mask
    return grid, vm


def test_hot_root_cold_tip_classification():
    grid, vm = _grid_and_stress()
    plan = plan_from_stress(
        grid, vm, yield_mpa=45.0, safety_factor=2.0, layer_height_mm=0.2,
        reinforce_frac=0.6, relax_frac=0.15,
    )
    allowable = 22.5
    root_util = vm[:, :, 0][grid.mask[:, :, 0]].max() / allowable
    assert root_util > 1.0
    assert plan.n_reinforced_layers > 0
    first = next(a for a in plan.actions if a.reinforce_xy.any())
    last = plan.actions[-1]
    assert first.z0_mm < 10.0
    if last.mean_utilization <= 0.15:
        assert plan.n_relaxed_layers > 0


def test_relax_only_when_uniformly_cold():
    grid, vm = _grid_and_stress()
    vm_low = vm * 0.02
    plan = plan_from_stress(
        grid, vm_low, yield_mpa=45.0, safety_factor=2.0, layer_height_mm=None,
        reinforce_frac=0.6, relax_frac=0.15,
    )
    assert plan.n_reinforced_layers == 0
    assert plan.n_relaxed_layers == len(plan.actions)


def test_layer_height_grouping():
    grid, vm = _grid_and_stress()
    plan_coarse = plan_from_stress(grid, vm, yield_mpa=45.0, safety_factor=2.0, layer_height_mm=10.0)
    plan_fine = plan_from_stress(grid, vm, yield_mpa=45.0, safety_factor=2.0, layer_height_mm=0.05)
    assert len(plan_fine.actions) >= len(plan_coarse.actions)


def test_boundary_length():
    m = np.zeros((10, 10), bool)
    m[2:5, 2:7] = True
    assert region_boundary_length_mm(m, h=1.0) == pytest.approx(16.0)
    single = np.zeros((3, 3), bool)
    single[1, 1] = True
    assert region_boundary_length_mm(single, 1.0) == pytest.approx(4.0)


def test_summary_table_smoke():
    grid, vm = _grid_and_stress()
    plan = plan_from_stress(grid, vm, yield_mpa=45.0, safety_factor=2.0)
    out = plan_summary_table(plan)
    assert "util" in out
