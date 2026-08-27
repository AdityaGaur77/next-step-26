from __future__ import annotations

import inspect
import warnings
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, cg, splu

try:
    import pyamg

    HAS_PYAMG = True
except ImportError:
    HAS_PYAMG = False

_CG_TOL_KW = "rtol" if "rtol" in inspect.signature(cg).parameters else "tol"

NODE_ORDER = [(dx, dy, dz) for dz in (0, 1) for dy in (0, 1) for dx in (0, 1)]

FACES = {
    "x+": 0,
    "x-": 0,
    "y+": 1,
    "y-": 1,
    "z+": 2,
    "z-": 2,
}
FACE_ALIASES = {
    "right": "x+",
    "left": "x-",
    "back": "y+",
    "front": "y-",
    "top": "z+",
    "bottom": "z-",
}

DIRECT_SOLVE_MAX_DOF = 8_000
CG_MAX_ITER = 5_000
CG_RTOL = 1e-8


def canonical_face(face: str) -> str:
    f = FACE_ALIASES.get(str(face).strip().lower(), str(face).strip().lower())
    if f not in FACES:
        raise ValueError(f"unknown face {face!r}; expected one of {sorted(FACES)} or {sorted(FACE_ALIASES)}")
    return f


def opposite_face(face: str) -> str:
    f = canonical_face(face)
    return f[0] + ("-" if f.endswith("+") else "+")


def _isotropic_D(E: float, nu: float) -> np.ndarray:
    lam = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
    mu = E / (2.0 * (1.0 + nu))
    D = np.zeros((6, 6))
    D[0, 0] = D[1, 1] = D[2, 2] = lam + 2.0 * mu
    D[0, 1] = D[0, 2] = D[1, 0] = D[1, 2] = D[2, 0] = D[2, 1] = lam
    D[3, 3] = D[4, 4] = D[5, 5] = mu
    return D


def _hex8_ke(h: float, E: float, nu: float) -> tuple[np.ndarray, list[np.ndarray]]:
    g = 1.0 / np.sqrt(3.0)
    D = _isotropic_D(E, nu)
    Ke = np.zeros((24, 24))
    B_list = []
    for xi in (-g, g):
        for eta in (-g, g):
            for zeta in (-g, g):
                dN_dg = np.empty((3, 8))
                for a, (dx, dy, dz) in enumerate(NODE_ORDER):
                    sx, sy, sz = dx * 2 - 1, dy * 2 - 1, dz * 2 - 1
                    dN_dg[0, a] = 0.125 * sx * (1 + sy * eta) * (1 + sz * zeta)
                    dN_dg[1, a] = 0.125 * sy * (1 + sx * xi) * (1 + sz * zeta)
                    dN_dg[2, a] = 0.125 * sz * (1 + sx * xi) * (1 + sy * eta)
                J = np.eye(3) * (h / 2.0)
                detJ = (h / 2.0) ** 3
                dN_dx = np.linalg.solve(J, dN_dg)
                B = np.zeros((6, 24))
                for a in range(8):
                    bx, by, bz = dN_dx[:, a]
                    o = 3 * a
                    B[0, o + 0] = bx
                    B[1, o + 1] = by
                    B[2, o + 2] = bz
                    B[3, o + 0] = by
                    B[3, o + 1] = bx
                    B[4, o + 1] = bz
                    B[4, o + 2] = by
                    B[5, o + 0] = bz
                    B[5, o + 2] = bx
                Ke += B.T @ D @ B * detJ
                B_list.append(B)
    return Ke, B_list


@dataclass
class FemResult:
    von_mises: np.ndarray
    max_displacement_mm: float
    solver: str
    face_displacement: dict = field(default_factory=dict)
    n_dof: int = 0
    iterations: int = 0


def _face_nodes(grid, node_grid_shape, face: str, avoid_faces=(), patch: str = "full"):
    f = canonical_face(face)
    axis = FACES[f]
    n = node_grid_shape
    idx = [np.arange(n[i]) for i in range(3)]
    grids = np.meshgrid(*idx, indexing="ij")
    sel = [grids[i].ravel() for i in range(3)]
    keep = sel[axis] == (n[axis] - 1 if f.endswith("+") else 0)
    nodes = np.stack([s[keep] for s in sel], axis=1)

    if patch == "far" and nodes.shape[0] > 1:
        for af in avoid_faces:
            try:
                a = canonical_face(af)
            except ValueError:
                continue
            ax = FACES[a]
            if ax == axis:
                continue
            hi = a.endswith("+")
            coord = nodes[:, ax]
            if hi:
                far = coord <= (coord.max() - 0.5 * (coord.max() - coord.min()) - 1e-9)
            else:
                far = coord >= (coord.min() + 0.5 * (coord.max() - coord.min()) + 1e-9)
            if far.any():
                nodes = nodes[far]
    return nodes


def _active_positions(active_nodes: np.ndarray, flat_ids: np.ndarray) -> np.ndarray:
    if active_nodes.size == 0 or flat_ids.size == 0:
        return np.empty(0, dtype=np.int64)
    pos = np.clip(np.searchsorted(active_nodes, flat_ids), 0, active_nodes.size - 1)
    return pos[active_nodes[pos] == flat_ids]


def _assemble_free(dof_map: np.ndarray, Ke: np.ndarray, free_index: np.ndarray, n_free: int):
    rows, cols, vals = [], [], []
    ke_flat = Ke.ravel()
    ne = dof_map.shape[0]
    chunk = 4096
    for s in range(0, ne, chunk):
        dm = free_index[dof_map[s : s + chunk]]
        m = dm.shape[0]
        r = np.repeat(dm, 24, axis=1).ravel()
        c = np.tile(dm, (1, 24)).ravel()
        keep = (r >= 0) & (c >= 0)
        if not keep.any():
            continue
        rows.append(r[keep].astype(np.int32))
        cols.append(c[keep].astype(np.int32))
        vals.append(np.tile(ke_flat, m)[keep])
    if not rows:
        raise ValueError("no free degrees of freedom left after applying constraints")
    return sp.coo_matrix(
        (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
        shape=(n_free, n_free),
    ).tocsr()


def _solve_linear_system(K: sp.spmatrix, f: np.ndarray) -> tuple[np.ndarray, str, int]:
    n = f.size
    if n <= DIRECT_SOLVE_MAX_DOF:
        return splu(K.tocsc()).solve(f), "splu", 0

    diag = K.diagonal()
    diag = np.where(np.abs(diag) > 0, diag, 1.0)
    jacobi = LinearOperator(K.shape, matvec=lambda x: x / diag)
    iters = 0

    def _count(_xk):
        nonlocal iters
        iters += 1

    u, info = cg(K, f, M=jacobi, maxiter=CG_MAX_ITER, callback=_count, **{_CG_TOL_KW: CG_RTOL})
    if info == 0:
        return u, f"jacobi-cg({iters}it)", iters

    if HAS_PYAMG:
        try:
            ml = pyamg.smoothed_aggregation_solver(K)
            residuals: list[float] = []
            u = ml.solve(f, tol=CG_RTOL, accel="cg", residuals=residuals)
            return u, f"pyamg-cg({len(ml.levels)}lv,{max(len(residuals) - 1, 0)}it)", len(residuals)
        except Exception as exc:
            warnings.warn(f"pyamg path failed ({exc}); falling back to direct solve")

    warnings.warn(f"CG did not converge (info={info}); falling back to direct solve — this may be slow")
    return splu(K.tocsc()).solve(f), "splu-fallback", iters


def solve_voxel_fem(
    grid,
    fixed_faces,
    load_face,
    load_vector_n,
    young_modulus_mpa: float = 2400.0,
    poisson: float = 0.3,
    load_patch: str = "far",
):
    mask = grid.mask
    nx, ny, nz = mask.shape
    h = grid.h
    elems = np.argwhere(mask)
    ne = elems.shape[0]
    if ne == 0:
        raise ValueError("empty voxel grid")

    node_grid = (nx + 1, ny + 1, nz + 1)
    nid = np.arange(node_grid[0] * node_grid[1] * node_grid[2]).reshape(node_grid)

    conn_global = np.empty((ne, 8), dtype=np.int64)
    for a, (dx, dy, dz) in enumerate(NODE_ORDER):
        conn_global[:, a] = nid[elems[:, 0] + dx, elems[:, 1] + dy, elems[:, 2] + dz]

    active_nodes, active_inv = np.unique(conn_global, return_inverse=True)
    conn_local = active_inv.reshape(ne, 8)
    n_active = active_nodes.shape[0]
    ndof_full = n_active * 3
    dof_map = ((conn_local * 3)[:, :, None] + np.arange(3)[None, None, :]).reshape(ne, 24)

    fixed_mask = np.zeros(n_active, dtype=bool)
    for face in fixed_faces:
        fn = _face_nodes(grid, node_grid, face)
        flat = nid[fn[:, 0], fn[:, 1], fn[:, 2]]
        fixed_mask[_active_positions(active_nodes, flat)] = True
    if not fixed_mask.any():
        raise ValueError(
            f"constraint faces {list(fixed_faces)} touch no material — the part is unsupported"
        )

    ln = _face_nodes(grid, node_grid, load_face, avoid_faces=fixed_faces, patch=load_patch)
    lflat = nid[ln[:, 0], ln[:, 1], ln[:, 2]]
    lpos = _active_positions(active_nodes, lflat)
    if lpos.size == 0:
        raise ValueError(f"load face {load_face!r} touches no material")
    if fixed_mask[lpos].all():
        raise ValueError(
            f"load face {load_face!r} is fully constrained by {list(fixed_faces)} — "
            "the load would be carried straight into the fixture (no stress to solve for)"
        )

    free = np.flatnonzero(~fixed_mask)
    free_dofs = (free[:, None] * 3 + np.arange(3)).ravel()
    free_index = np.full(ndof_full, -1, dtype=np.int64)
    free_index[free_dofs] = np.arange(free_dofs.size)

    force_full = np.zeros((n_active, 3))
    force_full[lpos] = np.asarray(load_vector_n, dtype=np.float64) / lpos.size
    f_free = force_full.reshape(-1)[free_dofs]

    Ke, B_list = _hex8_ke(h, young_modulus_mpa, poisson)
    K = _assemble_free(dof_map, Ke, free_index, free_dofs.size)
    u_f, used_solver, iters = _solve_linear_system(K, f_free)

    u_full = np.zeros(ndof_full)
    u_full[free_dofs] = u_f

    D = _isotropic_D(young_modulus_mpa, poisson)
    vm = np.zeros(ne)
    u_e = u_full[dof_map]
    for B in B_list:
        eps = np.einsum("ij,ej->ei", B, u_e)
        sig = eps @ D.T
        sx, sy, sz, txy, tyz, txz = sig[:, 0], sig[:, 1], sig[:, 2], sig[:, 3], sig[:, 4], sig[:, 5]
        vm += (
            0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
            + 3.0 * (txy**2 + tyz**2 + txz**2)
        )
    vm = np.sqrt(vm / len(B_list))

    vm_grid = np.zeros(mask.shape)
    vm_grid[elems[:, 0], elems[:, 1], elems[:, 2]] = vm

    u_vec = u_full.reshape(-1, 3)
    dirn = np.asarray(load_vector_n, dtype=np.float64)
    norm = np.linalg.norm(dirn)
    dirn = dirn / norm if norm > 0 else np.array([0.0, 0.0, -1.0])
    disp_out = {canonical_face(load_face): float(np.mean(u_vec[lpos] @ dirn))}

    return FemResult(
        von_mises=vm_grid,
        max_displacement_mm=float(np.abs(u_vec).max()),
        solver=used_solver,
        face_displacement=disp_out,
        n_dof=int(free_dofs.size),
        iterations=iters,
    )


def cantilever_reference(L: float, b: float, hh: float, P: float, E: float) -> tuple[float, float]:
    I = b * hh**3 / 12.0
    delta = P * L**3 / (3.0 * E * I)
    sigma = P * L * (hh / 2.0) / I
    return delta, sigma
