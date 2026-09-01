"""Generate watertight test parts for verifying EcoSlice inside a slicer.

A cube is close to the worst part to test with: stress stays low and uniform, so
the planner can correctly decide to do nothing, which looks exactly like a broken
install. These are shapes with somewhere for stress to concentrate.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ecoslice.voxelize import box_mesh  # noqa: E402


def orient_outward(vertices: np.ndarray, triangles: np.ndarray):
    """Make winding consistent across shared edges, then face every normal outward.

    Getting winding right by hand is error-prone and a wrong normal makes a slicer
    fill the wrong side of a wall, so the orientation is computed instead: walk the
    face adjacency flipping any neighbour that traverses a shared edge the same way
    (two correctly-wound faces always traverse it in opposite directions), then use
    the sign of the enclosed volume to decide whether the whole shell is inside out.
    """
    v = np.asarray(vertices, dtype=np.float64)
    t = np.asarray(triangles, dtype=np.int64).copy()

    edge_faces: dict[tuple[int, int], list[int]] = {}
    for fi, (a, b, c) in enumerate(t):
        for u, w in ((a, b), (b, c), (c, a)):
            edge_faces.setdefault((min(u, w), max(u, w)), []).append(fi)

    def traverses(face, u, w) -> bool:
        a, b, c = t[face]
        return (a, b) == (u, w) or (b, c) == (u, w) or (c, a) == (u, w)

    seen = {0}
    stack = [0]
    while stack:
        fi = stack.pop()
        a, b, c = t[fi]
        for u, w in ((a, b), (b, c), (c, a)):
            for nb in edge_faces.get((min(u, w), max(u, w)), ()):
                if nb in seen:
                    continue
                if traverses(nb, u, w):          # same direction => neighbour is flipped
                    t[nb] = t[nb][::-1]
                seen.add(nb)
                stack.append(nb)

    tri = v[t]
    signed_volume = np.einsum(
        "ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])
    ).sum() / 6.0
    if signed_volume < 0:
        t = t[:, ::-1]
    return v, t.astype(np.int32)


def l_bracket(arm_mm: float = 80.0, thickness_mm: float = 5.0, depth_mm: float = 15.0):
    """An L-profile extruded along y — two arms meeting at a re-entrant corner.

    The inner corner is a genuine stress concentration, so the FEM has something
    to find that a reader can also see by eye, which is what makes it a good part
    to demonstrate on.

    The default proportions are deliberate. At 10 mm thick this shape is stout
    enough that a realistic load barely stresses it (0.28x allowable at 6 kg) and
    the planner correctly reinforces nothing — a true result that looks exactly
    like a broken install. 5 mm thick and 15 mm deep puts the same load at ~1.1x,
    where the plan has to make real decisions.
    """
    t, a = float(thickness_mm), float(arm_mm)
    # L outline in the x-z plane, counter-clockwise from the origin.
    outline = np.array(
        [(0.0, 0.0), (a, 0.0), (a, t), (t, t), (t, a), (0.0, a)], dtype=np.float64
    )
    d = float(depth_mm)

    n = len(outline)
    front = np.column_stack([outline[:, 0], np.zeros(n), outline[:, 1]])
    back = np.column_stack([outline[:, 0], np.full(n, d), outline[:, 1]])
    verts = np.vstack([front, back])

    # The L splits cleanly into two quads, so no general triangulator is needed.
    caps = [(0, 1, 2, 3), (0, 3, 4, 5)]
    tris: list[tuple[int, int, int]] = []
    for a0, b0, c0, d0 in caps:
        tris += [(a0, b0, c0), (a0, c0, d0)]
        tris += [(n + a0, n + c0, n + b0), (n + a0, n + d0, n + c0)]
    for i in range(n):                                   # side walls
        j = (i + 1) % n
        tris += [(i, n + j, j), (i, n + i, n + j)]
    # Winding is not reasoned out by hand: orient_outward makes it consistent and
    # outward-facing whatever order the faces were emitted in.
    return orient_outward(verts, np.array(tris, dtype=np.int32))


PARTS = {
    "cantilever": (
        lambda: box_mesh(100.0, 10.0, 10.0),
        "100x10x10 mm bar — the geometry the FEM is validated against",
    ),
    "l-bracket": (
        lambda: l_bracket(),
        "80 mm arms, 5 mm thick, 15 mm deep — inner corner concentrates stress; a 6 kg "
        "load puts it at ~1.1x allowable, so the planner has real work to do",
    ),
}


def write_binary_stl(path: Path, vertices: np.ndarray, triangles: np.ndarray, name: str) -> None:
    v = np.asarray(vertices, dtype=np.float64)
    t = np.asarray(triangles, dtype=np.int64)
    tri = v[t]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)

    with path.open("wb") as f:
        f.write(name.encode("ascii", "replace")[:80].ljust(80, b"\0"))
        f.write(struct.pack("<I", t.shape[0]))
        for i in range(t.shape[0]):
            f.write(struct.pack("<3f", *normals[i]))
            for corner in tri[i]:
                f.write(struct.pack("<3f", *corner))
            f.write(struct.pack("<H", 0))


def is_watertight(triangles: np.ndarray) -> bool:
    """Every edge shared by exactly two faces — a slicer will reject anything less."""
    t = np.asarray(triangles)
    edges: dict[tuple[int, int], int] = {}
    for a, b, c in t:
        for u, v in ((a, b), (b, c), (c, a)):
            key = (min(u, v), max(u, v))
            edges[key] = edges.get(key, 0) + 1
    return bool(edges) and all(count == 2 for count in edges.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write a test STL for EcoSlice.")
    ap.add_argument("--part", choices=sorted(PARTS), default="cantilever")
    ap.add_argument("--out", help="output path (default <part>.stl)")
    ap.add_argument("--all", action="store_true", help="write every part")
    args = ap.parse_args(argv)

    wanted = sorted(PARTS) if args.all else [args.part]
    for key in wanted:
        builder, blurb = PARTS[key]
        v, t = builder()
        if not is_watertight(t):
            print(f"error: {key} mesh is not watertight", file=sys.stderr)
            return 1
        out = Path(args.out) if (args.out and not args.all) else Path(f"{key}.stl")
        write_binary_stl(out, v, t, f"EcoSlice test part: {key}")
        size = v.max(axis=0) - v.min(axis=0)
        print(f"{out}  {t.shape[0]} triangles  {size[0]:.0f}x{size[1]:.0f}x{size[2]:.0f} mm")
        print(f"  {blurb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
