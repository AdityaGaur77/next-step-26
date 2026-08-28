from __future__ import annotations

import struct

import numpy as np
import pytest

from ecoslice.pipeline import EcoSlicePipeline

from make_test_part import PARTS, is_watertight, l_bracket, write_binary_stl

L_DESC = "wall bracket holding 6 kg, load downward at the free end; screwed onto the left wall"
BAR_DESC = "shelf bracket holding 8 kg, load downward at the front edge; screwed onto left wall"


def read_binary_stl(path):
    """Read back exactly as a slicer would, welding duplicate corners."""
    with open(path, "rb") as f:
        f.read(80)
        n = struct.unpack("<I", f.read(4))[0]
        verts = []
        for _ in range(n):
            f.read(12)
            for _ in range(3):
                verts.append(struct.unpack("<3f", f.read(12)))
            f.read(2)
    v = np.array(verts, dtype=np.float64)
    uniq, inv = np.unique(np.round(v, 5), axis=0, return_inverse=True)
    return uniq, inv.reshape(-1, 3).astype(np.int32), n


@pytest.mark.parametrize("key", sorted(PARTS))
def test_every_part_is_watertight(key):
    """A slicer rejects a mesh with unpaired edges outright."""
    _, t = PARTS[key][0]()
    assert is_watertight(t)


@pytest.mark.parametrize("key", sorted(PARTS))
def test_stl_round_trips(tmp_path, key):
    v, t = PARTS[key][0]()
    out = tmp_path / f"{key}.stl"
    write_binary_stl(out, v, t, key)
    rv, rt, n = read_binary_stl(out)
    assert n == t.shape[0]
    assert rt.shape == t.shape
    assert np.allclose(np.sort(rv, axis=0), np.sort(v, axis=0), atol=1e-4)


EXPECTED_VOLUME_MM3 = {
    "cantilever": 100.0 * 10.0 * 10.0,
    # L profile: one full arm plus the remaining leg, extruded.
    "l-bracket": (80.0 * 5.0 + (80.0 - 5.0) * 5.0) * 15.0,
}


@pytest.mark.parametrize("key", sorted(PARTS))
def test_mesh_is_consistently_oriented(key):
    """Two correctly-wound neighbours traverse their shared edge in opposite directions."""
    _, t = PARTS[key][0]()
    directed = set()
    for a, b, c in t:
        for e in ((a, b), (b, c), (c, a)):
            assert e not in directed, f"edge {e} traversed twice the same way"
            directed.add(e)
    for a, b in directed:
        assert (b, a) in directed, f"edge ({a},{b}) has no opposing twin"


@pytest.mark.parametrize("key", sorted(PARTS))
def test_normals_face_outward_by_enclosed_volume(key):
    """The exact criterion: a closed shell wound outward encloses positive volume.

    Checking each normal against the direction away from the centroid looks
    equivalent but is not — the L-bracket's vertex centroid sits in the empty
    notch, outside the solid, so that heuristic rejects correct faces.
    """
    v, t = PARTS[key][0]()
    tri = v[t]
    volume = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0
    assert volume > 0, "shell is inside out"
    assert volume == pytest.approx(EXPECTED_VOLUME_MM3[key], rel=1e-6)


@pytest.mark.parametrize("key", sorted(PARTS))
def test_written_stl_normals_match_the_winding(tmp_path, key):
    """The normals stored in the file must agree with the triangles they describe."""
    v, t = PARTS[key][0]()
    out = tmp_path / f"{key}.stl"
    write_binary_stl(out, v, t, key)
    with out.open("rb") as f:
        f.read(84)
        for a, b, c in t:
            stored = np.array(struct.unpack("<3f", f.read(12)))
            corners = np.array([struct.unpack("<3f", f.read(12)) for _ in range(3)])
            f.read(2)
            computed = np.cross(corners[1] - corners[0], corners[2] - corners[0])
            computed = computed / np.linalg.norm(computed)
            assert np.allclose(stored, computed, atol=1e-5)


def test_orient_outward_repairs_an_inside_out_mesh():
    """The orienter is what makes winding correct, so prove it actually flips one."""
    from make_test_part import orient_outward

    v, t = PARTS["l-bracket"][0]()
    flipped = t[:, ::-1]
    _, fixed = orient_outward(v, flipped)
    tri = v[fixed]
    volume = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0
    assert volume > 0


@pytest.mark.parametrize(
    "key,desc,resolution",
    [("cantilever", BAR_DESC, 40), ("l-bracket", L_DESC, 32)],
)
def test_part_produces_a_plan_worth_demonstrating(key, desc, resolution):
    """A part the planner leaves alone is a true result that looks like a broken install.

    These exist to be demonstrated on, so each must actually reach the reinforce
    threshold under the load case it ships with.
    """
    v, t = PARTS[key][0]()
    pipe = EcoSlicePipeline(description=desc, resolution=resolution, layer_height_mm=0.2)
    a = pipe.analyze_mesh(v, t, desc, "obj")
    util = a.plan.max_vm_mpa / a.plan.allowable_mpa
    assert a.plan.n_reinforced_layers > 0, f"{key} reinforces nothing at {util:.2f}x"
    assert 0.7 <= util <= 2.0, f"{key} sits at {util:.2f}x — not a believable demo load"


def test_l_bracket_thickness_drives_stress():
    """Guards the sizing decision: the stout version really is the boring one."""
    stress = []
    for thickness in (10.0, 5.0):
        v, t = l_bracket(80.0, thickness, 15.0)
        pipe = EcoSlicePipeline(description=L_DESC, resolution=32, layer_height_mm=0.2)
        a = pipe.analyze_mesh(v, t, L_DESC, "obj")
        stress.append(a.plan.max_vm_mpa)
    assert stress[1] > stress[0] * 2, "thinning the section must raise peak stress"
