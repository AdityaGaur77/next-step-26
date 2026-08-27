import numpy as np
import pytest

from ecoslice.fem import cantilever_reference, solve_voxel_fem
from ecoslice.voxelize import box_mesh, voxelize


@pytest.fixture(scope="module")
def cantilever():
    L, b, hh = 100.0, 10.0, 10.0
    v, t = box_mesh(L, b, hh)
    grid = voxelize(v, t, resolution=50)
    E = 2400.0
    P = 49.0
    res = solve_voxel_fem(
        grid,
        fixed_faces=["x-"],
        load_face="x+",
        load_vector_n=(0.0, 0.0, -P),
        young_modulus_mpa=E,
        poisson=0.30,
    )
    delta_ref, sigma_ref = cantilever_reference(L, b, hh, P, E)
    return grid, res, delta_ref, sigma_ref


def test_tip_deflection_matches_closed_form(cantilever):
    _, res, delta_ref, _ = cantilever
    tip_z = abs(res.face_displacement["x+"])
    rel_err = abs(tip_z - delta_ref) / delta_ref
    assert rel_err < 0.45, f"tip deflection {tip_z:.3f} vs {delta_ref:.3f} mm ({rel_err:.1%})"


def test_max_stress_order_of_magnitude(cantilever):
    _, res, _, sigma_ref = cantilever
    smax = res.von_mises.max()
    assert 0.4 * sigma_ref < smax < 2.2 * sigma_ref, (
        f"max VM {smax:.2f} vs beam theory {sigma_ref:.2f} MPa"
    )


def test_stress_concentrated_at_fixed_root(cantilever):
    grid, res, _, _ = cantilever
    nx = grid.shape[0]
    root_mean = res.von_mises[: nx // 5][grid.mask[: nx // 5]].mean()
    tip_mean = res.von_mises[-nx // 5 :][grid.mask[-nx // 5 :]].mean()
    assert root_mean > 3.0 * tip_mean


def test_solver_reports_method(cantilever):
    _, res, _, _ = cantilever
    assert res.solver.startswith(("splu", "jacobi-cg", "pyamg"))
    assert res.n_dof > 0


def test_large_systems_use_an_iterative_solver():
    """Direct LU blows up on 3D elasticity; the ladder must switch to CG."""
    v, t = box_mesh(60.0, 40.0, 30.0)
    grid = voxelize(v, t, resolution=24)
    res = solve_voxel_fem(grid, ["x-"], "x+", (0.0, 0.0, -40.0))
    assert res.n_dof > 8_000
    assert res.solver.startswith(("jacobi-cg", "pyamg")), res.solver
    assert res.iterations > 0


def test_load_face_inside_the_fixture_is_rejected():
    v, t = box_mesh(40.0, 20.0, 20.0)
    grid = voxelize(v, t, resolution=16)
    with pytest.raises(ValueError, match="fully constrained"):
        solve_voxel_fem(grid, ["z-"], "z-", (0.0, 0.0, -30.0))


def test_unsupported_part_is_rejected():
    from ecoslice.voxelize import VoxelGrid

    mask = np.zeros((6, 6, 6), bool)
    mask[1:5, 1:5, 1:5] = True
    grid = VoxelGrid(mask=mask, origin=np.zeros(3), spacing=(1.0, 1.0, 1.0))
    with pytest.raises(ValueError, match="unsupported"):
        solve_voxel_fem(grid, ["z-"], "z+", (0.0, 0.0, -10.0))


def test_opposite_face():
    from ecoslice.fem import opposite_face

    assert opposite_face("z-") == "z+"
    assert opposite_face("bottom") == "z+"
    assert opposite_face("x+") == "x-"


def test_empty_grid_raises():
    from ecoslice.voxelize import VoxelGrid

    empty = VoxelGrid(
        mask=np.zeros((4, 4, 4), bool), origin=np.zeros(3), spacing=(1.0, 1.0, 1.0)
    )
    with pytest.raises(ValueError):
        solve_voxel_fem(empty, ["z-"], "z+", (0, 0, -10))
