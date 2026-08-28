from __future__ import annotations

import numpy as np
import pytest

from ecoslice.host_bridge import is_downgradable_solid, set_surface_sparse
from ecoslice.mapping import LayerAction
from ecoslice.mutate import MutationConfig, apply_to_region
from ecoslice.voxelize import VoxelGrid

from mocks import LayerRegion, Surface, SurfaceType, rect_pts


def _grid(nx=4, ny=2, h=10.0):
    return VoxelGrid(mask=np.ones((nx, ny, 2), dtype=bool), origin=np.zeros(3), spacing=(h, h, h))


def _relax_everywhere(nx=4, ny=2):
    """A band whose columns are all cold, so every surface classifies as 'relax'."""
    return LayerAction(
        z0_mm=0.0,
        z1_mm=10.0,
        reinforce_xy=np.zeros((nx, ny), dtype=bool),
        relax_xy=np.ones((nx, ny), dtype=bool),
        mean_utilization=0.05,
        p95_utilization=0.1,
        n_voxels=16,
        occupied_xy=np.ones((nx, ny), dtype=bool),
    )


def _region(surface_type, extra_perimeters=0):
    return LayerRegion(
        [Surface(rect_pts(0.0, 0.0, 40.0, 20.0), surface_type=surface_type,
                 extra_perimeters=extra_perimeters)]
    )


def test_relax_is_a_no_op_on_a_stock_profile_without_the_downgrade():
    """Extra perimeters start at 0 on a default profile, so there is nothing to remove."""
    region = _region(SurfaceType.stInternalSolid, extra_perimeters=0)
    stats = apply_to_region(region, _relax_everywhere(), _grid(), MutationConfig())
    assert stats.perimeters_removed == 0
    assert stats.surfaces_desolidified == 0, "downgrade must stay off unless asked for"
    assert region.fill_surfaces.surfaces[0].surface_type is SurfaceType.stInternalSolid


def test_downgrade_thins_internal_solid_back_to_sparse_in_cold_columns():
    region = _region(SurfaceType.stInternalSolid)
    cfg = MutationConfig(enable_solid_downgrade=True)
    stats = apply_to_region(region, _relax_everywhere(), _grid(), cfg)
    assert stats.surfaces_desolidified == 1
    assert region.fill_surfaces.surfaces[0].surface_type is SurfaceType.stInternal


@pytest.mark.parametrize(
    "st",
    [
        SurfaceType.stTop,
        SurfaceType.stBottom,
        SurfaceType.stBottomBridge,
        SurfaceType.stInternalBridge,
        SurfaceType.stSecondInternalBridge,
        SurfaceType.stInternalAfterExternalBridge,
    ],
)
def test_downgrade_never_touches_shells_or_bridges(st):
    """Only density-driven internal solid may be thinned; visible and bridging shells stay."""
    region = _region(st)
    cfg = MutationConfig(enable_solid_downgrade=True)
    stats = apply_to_region(region, _relax_everywhere(), _grid(), cfg)
    assert stats.surfaces_desolidified == 0
    assert region.fill_surfaces.surfaces[0].surface_type is st


def test_downgrade_still_removes_extra_perimeters_when_both_apply():
    region = _region(SurfaceType.stInternalSolid, extra_perimeters=3)
    cfg = MutationConfig(enable_solid_downgrade=True)
    stats = apply_to_region(region, _relax_everywhere(), _grid(), cfg)
    assert stats.perimeters_removed == 3
    assert stats.surfaces_desolidified == 1


def test_is_downgradable_solid_accepts_string_typed_hosts():
    class StringSurface:
        surface_type = "internal_solid"

    assert is_downgradable_solid(StringSurface())
    s = StringSurface()
    assert set_surface_sparse(s)
    assert s.surface_type == "internal"
