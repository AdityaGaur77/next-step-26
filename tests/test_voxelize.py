import numpy as np
import pytest

from ecoslice.voxelize import box_mesh, boundary_voxels, uv_sphere_mesh, voxelize


def test_unit_cube_full_occupancy():
    v, t = box_mesh(10.0, 10.0, 10.0)
    grid = voxelize(v, t, resolution=20)
    assert grid.mask.all()
    assert abs(grid.volume_mm3 - 1000.0) < 1e-6


def test_sphere_volume_within_tolerance():
    v, t = uv_sphere_mesh(radius=8.0, segments=32, rings=16)
    grid = voxelize(v, t, resolution=24)
    true_v = 4.0 / 3.0 * np.pi * 8.0**3
    err = abs(grid.volume_mm3 - true_v) / true_v
    assert err < 0.08, f"volume error {err:.3f}"


def test_grid_geometry():
    v, t = box_mesh(40.0, 8.0, 8.0)
    grid = voxelize(v, t, resolution=40)
    assert grid.shape == (40, 8, 8)
    assert grid.h == pytest.approx(1.0)
    cell = grid.cell_center(5, 2, 2)
    assert cell[0] == pytest.approx(5.5)


def test_boundary_detection():
    v, t = box_mesh(10.0, 10.0, 10.0)
    grid = voxelize(v, t, resolution=10)
    b = boundary_voxels(grid)
    assert b.sum() == 1000 - 8**3
