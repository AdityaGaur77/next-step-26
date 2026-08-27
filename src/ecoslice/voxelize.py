from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VoxelGrid:
    mask: np.ndarray
    origin: np.ndarray
    spacing: tuple[float, float, float]

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(self.mask.shape)

    @property
    def h(self) -> float:
        return self.spacing[0]

    @property
    def volume_mm3(self) -> float:
        return float(self.mask.sum()) * self.h ** 3

    def cell_center(self, i: int, j: int, k: int) -> np.ndarray:
        return self.origin + (np.array([i, j, k], dtype=np.float64) + 0.5) * self.h

    def world_to_cell(self, pts_mm: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts_mm, dtype=np.float64)
        return np.floor((pts - self.origin) / self.h).astype(np.int64)


def _triangle_hit_xs(tri2d: np.ndarray, tri_x: np.ndarray, p: np.ndarray) -> np.ndarray:
    a, b, c = tri2d[:, 0], tri2d[:, 1], tri2d[:, 2]

    def cross(o, u, v):
        return (u[..., 0] - o[..., 0]) * (v[..., 1] - o[..., 1]) - (
            u[..., 1] - o[..., 1]
        ) * (v[..., 0] - o[..., 0])

    area = cross(a, b, c)
    valid = np.abs(area) > 1e-12
    w0 = cross(a, b, p)
    w1 = cross(b, c, p)
    w2 = cross(c, a, p)
    inside = valid & (((w0 >= 0) & (w1 >= 0) & (w2 >= 0)) | ((w0 <= 0) & (w1 <= 0) & (w2 <= 0)))
    if not inside.any():
        return np.empty(0, dtype=np.float64)
    lam0 = w1[inside] / area[inside]
    lam1 = w2[inside] / area[inside]
    lam2 = 1.0 - lam0 - lam1
    xa, xb, xc = tri_x[:, 0], tri_x[:, 1], tri_x[:, 2]
    return lam0 * xa[inside] + lam1 * xb[inside] + lam2 * xc[inside]


MIN_CELLS_ACROSS_THINNEST = 4


def voxelize(
    vertices: np.ndarray,
    triangles: np.ndarray,
    resolution: int = 32,
    min_cells_across: int = MIN_CELLS_ACROSS_THINNEST,
) -> VoxelGrid:
    """Solid voxelization by ray parity along +x.

    Cell size is driven by the longest axis, but never coarser than
    `min_cells_across` cells through the thinnest one: a plate or bracket with
    two elements through its thickness cannot represent bending at all.
    """
    v = np.asarray(vertices, dtype=np.float64)
    t = np.asarray(triangles, dtype=np.int64)
    if v.ndim != 2 or v.shape[1] != 3:
        raise ValueError("vertices must be (N,3)")
    if t.ndim != 2 or t.shape[1] != 3:
        raise ValueError("triangles must be (M,3)")
    mn, mx = v.min(axis=0), v.max(axis=0)
    ext = np.maximum(mx - mn, 1e-9)
    h = float(ext.max()) / float(resolution)
    if min_cells_across > 0:
        h = min(h, float(ext.min()) / float(min_cells_across))
    n = np.maximum(np.ceil(ext / h).astype(np.int64), 1)

    tri = v[t]
    tri_yz = tri[:, :, 1:3]
    tri_x = tri[:, :, 0]

    cy = mn[1] + (np.arange(n[1]) + 0.5) * h
    cz = mn[2] + (np.arange(n[2]) + 0.5) * h
    cx_centers = mn[0] + (np.arange(n[0]) + 0.5) * h

    mask = np.zeros(tuple(int(x) for x in n), dtype=bool)
    tol = max(1e-9, h * 1e-9)
    for j, y in enumerate(cy):
        for k, z in enumerate(cz):
            xs = _triangle_hit_xs(tri_yz, tri_x, np.array([y, z]))
            if xs.size < 2:
                continue
            xs.sort()
            merged = []
            for x in xs:
                if not merged or x - merged[-1] > tol:
                    merged.append(x)
            xs = np.asarray(merged)
            if xs.size < 2:
                continue
            for a in range(0, xs.size - 1, 2):
                lo, hi = xs[a], xs[a + 1]
                if hi - lo <= tol:
                    continue
                i0 = int(np.searchsorted(cx_centers, lo, side="right"))
                i1 = int(np.searchsorted(cx_centers, hi, side="left"))
                if i1 > i0:
                    mask[i0:i1, j, k] = True
    return VoxelGrid(mask=mask, origin=mn.copy(), spacing=(h, h, h))


def boundary_voxels(grid: VoxelGrid) -> np.ndarray:
    m = grid.mask
    interior = np.zeros_like(m)
    interior[1:-1, 1:-1, 1:-1] = (
        m[1:-1, 1:-1, 1:-1]
        & m[:-2, 1:-1, 1:-1]
        & m[2:, 1:-1, 1:-1]
        & m[1:-1, :-2, 1:-1]
        & m[1:-1, 2:, 1:-1]
        & m[1:-1, 1:-1, :-2]
        & m[1:-1, 1:-1, 2:]
    )
    return m & ~interior


def box_mesh(lx: float, ly: float, lz: float, ox: float = 0.0, oy: float = 0.0, oz: float = 0.0):
    corners = np.array(
        [
            [ox, oy, oz],
            [ox + lx, oy, oz],
            [ox + lx, oy + ly, oz],
            [ox, oy + ly, oz],
            [ox, oy, oz + lz],
            [ox + lx, oy, oz + lz],
            [ox + lx, oy + ly, oz + lz],
            [ox, oy + ly, oz + lz],
        ],
        dtype=np.float64,
    )
    quads = [
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]
    tris = []
    for a, b, c, d in quads:
        tris.append((a, b, c))
        tris.append((a, c, d))
    return corners, np.array(tris, dtype=np.int32)


def uv_sphere_mesh(radius: float, segments: int = 24, rings: int = 12, center=(0.0, 0.0, 0.0)):
    lat = np.linspace(-np.pi / 2, np.pi / 2, rings + 2)[1:-1]
    lon = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    verts = [np.array([0.0, 0.0, radius])]
    for phi in lat:
        for theta in lon:
            verts.append(
                np.array(
                    [
                        radius * np.cos(phi) * np.cos(theta),
                        radius * np.cos(phi) * np.sin(theta),
                        radius * np.sin(phi),
                    ]
                )
            )
    south = np.array([0.0, 0.0, -radius])
    verts.append(south)
    v = np.array(verts) + np.asarray(center)
    tris = []
    top_ring = 1 + np.arange(segments)
    for s in range(segments):
        tris.append((0, top_ring[s], top_ring[(s + 1) % segments]))
    for r in range(rings - 1):
        base = 1 + r * segments
        nxt = base + segments
        for s in range(segments):
            s2 = (s + 1) % segments
            tris.append((base + s, nxt + s, nxt + s2))
            tris.append((base + s, nxt + s2, base + s2))
    last = len(v) - 1
    base_last = 1 + (rings - 1) * segments
    for s in range(segments):
        s2 = (s + 1) % segments
        tris.append((last, base_last + s2, base_last + s))
    return v, np.array(tris, dtype=np.int32)
