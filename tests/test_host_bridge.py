import numpy as np
import pytest

from ecoslice import host_bridge as hb
from ecoslice.receipt import co2e_g, grams_from_volume_mm3

from mocks import (
    Ctx,
    Layer,
    LayerRegion,
    LegacyCtx,
    LegacyLayer,
    LegacyObject,
    LegacyRegion,
    LegacySurface,
    ModelObject,
    ModelVolume,
    PrintObject,
    Surface,
    SurfaceType,
    TriangleMesh,
    rect_pts,
)
from ecoslice.voxelize import box_mesh


def _mini_object(trafo=None, footprint=None):
    surf = Surface(rect_pts(0, 0, 100, 10))
    layer = Layer(print_z_mm=1.0, height_mm=0.5, regions=[LayerRegion([surf])])
    v, t = box_mesh(100.0, 10.0, 10.0)
    volume = ModelVolume(TriangleMesh(v, t))
    obj = PrintObject(ModelObject([volume]), [layer], trafo=trafo, footprint_mm=footprint)
    return obj, surf


def _mini_ctx():
    obj, surf = _mini_object()
    return Ctx([obj]), surf


def test_probe_reports_binding_shapes():
    ctx, _ = _mini_ctx()
    cap = hb.probe(ctx)
    assert cap.extra_perimeters_mode == "writable"
    assert cap.surface_type_mode == "SurfaceType"
    assert "n=1" in cap.fill_surfaces_mode
    assert cap.mesh_source.endswith("+mesh")


def test_fill_surfaces_unwraps_surface_collection():
    surf = Surface(rect_pts(0, 0, 10, 10))
    region = LayerRegion([surf])
    assert not isinstance(region.fill_surfaces, list)
    got = hb.get_fill_surfaces(region)
    assert got == [surf]


def test_fill_surfaces_accepts_plain_list():
    surf = LegacySurface(rect_pts(0, 0, 10, 10))
    assert hb.get_fill_surfaces(LegacyRegion([surf])) == [surf]


def test_surface_type_enum_is_resolved_not_stringified():
    _, surf = _mini_ctx()
    assert hb.is_sparse_like(surf)
    assert hb.set_surface_type(surf, "internal_solid")
    assert surf.surface_type is SurfaceType.stInternalSolid
    assert not hb.is_sparse_like(surf)


def test_surface_type_string_hosts_still_supported():
    surf = LegacySurface(rect_pts(0, 0, 10, 10))
    assert hb.is_sparse_like(surf)
    assert hb.set_surface_type(surf, "internal_solid")
    assert surf.surface_type == "internal_solid"
    assert not hb.is_sparse_like(surf)


def test_centroid_is_area_weighted():
    _, surf = _mini_ctx()
    scale = hb.guess_scale(surf)
    assert scale == pytest.approx(1_000_000.0)
    xy = hb.surface_centroid_xy_mm(surf, scale=scale)
    assert xy[0] == pytest.approx(50.0, abs=0.01)
    assert xy[1] == pytest.approx(5.0, abs=0.01)


def test_centroid_of_l_shape_uses_polygon_centroid():
    pts = [(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)]
    surf = Surface(pts)
    xy = hb.surface_centroid_xy_mm(surf, scale=1_000_000.0)
    assert xy[0] == pytest.approx(11.0, abs=0.2)
    assert xy[1] == pytest.approx(11.0, abs=0.2)
    vertex_mean = 40.0 / 3.0
    assert abs(xy[0] - vertex_mean) > 1.0


def test_extra_perimeters_rw():
    _, surf = _mini_ctx()
    assert hb.get_extra_perimeters(surf) == 1
    assert hb.set_extra_perimeters(surf, 4)
    assert hb.get_extra_perimeters(surf) == 4


def test_mesh_comes_back_in_print_coordinates():
    trafo = np.eye(4)
    trafo[:3, 3] = (120.0, 90.0, 0.0)
    obj, _ = _mini_object(trafo=trafo)
    v, t = hb.get_mesh(obj)
    assert t.shape[1] == 3
    assert v[:, 0].min() == pytest.approx(120.0, abs=1e-3)
    assert v[:, 1].min() == pytest.approx(90.0, abs=1e-3)
    assert v[:, 0].max() == pytest.approx(220.0, abs=1e-3)


def test_mesh_falls_back_to_direct_accessors():
    v0, t0 = box_mesh(10.0, 10.0, 10.0)
    layer = LegacyLayer(0.5, 0.5, [LegacyRegion([LegacySurface(rect_pts(0, 0, 10, 10))])])
    obj = LegacyObject(v0, t0, [layer])
    v, t = hb.get_mesh(obj)
    assert v.shape == (8, 3)
    assert t.shape == (12, 3)


def test_object_key_uses_stable_id():
    obj, _ = _mini_object()
    assert hb.object_key(obj) == "obj1"


def test_ctx_object_is_preferred_over_whole_print():
    obj_a, _ = _mini_object()
    obj_b, _ = _mini_object()
    obj_b._id = 2
    ctx = Ctx([obj_a, obj_b], current=obj_b)
    assert hb.iter_print_objects(ctx) == [obj_b]


def test_legacy_ctx_lists_objects():
    ctx = LegacyCtx(["a", "b"])
    assert hb.iter_print_objects(ctx) == ["a", "b"]


def test_footprint_is_unscaled():
    obj, _ = _mini_object(footprint=(120.0, 90.0, 220.0, 100.0))
    assert hb.object_footprint_mm(obj) == pytest.approx((120.0, 90.0, 220.0, 100.0))


def test_receipt_math():
    assert grams_from_volume_mm3(1000.0) == pytest.approx(1.24)
    assert co2e_g(1000.0) == pytest.approx(5760.0)
    assert co2e_g(1000.0, recycled=True) == pytest.approx(2470.0)
    assert co2e_g(100.0) == pytest.approx(576.0)
