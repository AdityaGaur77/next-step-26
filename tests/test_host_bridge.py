import pytest

from ecoslice import host_bridge as hb
from ecoslice.receipt import co2e_g, grams_from_volume_mm3
from ecoslice.mutate import MutationConfig, apply_to_region

from mocks import (
    MockCtx,
    MockLayer,
    MockLayerRegion,
    MockPrintObject,
    MockSurface,
    rect_pts,
)


def _mini_ctx():
    surf = MockSurface(rect_pts(0, 0, 100, 10), surface_type="internal", extra_perimeters=1)
    layer = MockLayer(print_z_mm=1.0, height_mm=0.5, regions=[MockLayerRegion([surf])])
    obj = MockPrintObject([[0, 0, 0]], [[0, 0, 0]], [layer])
    return MockCtx([obj]), surf


def test_probe():
    ctx, _ = _mini_ctx()
    cap = hb.probe(ctx)
    assert cap.extra_perimeters_mode == "writable"
    assert cap.surface_type_mode == "writable"
    assert "n=1" in cap.fill_surfaces_mode


def test_centroid_scale_guess():
    _, surf = _mini_ctx()
    scale = hb.guess_scale(surf)
    assert scale == pytest.approx(1_000_000.0)
    xy = hb.surface_centroid_xy_mm(surf, scale=scale)
    assert xy[0] == pytest.approx(50.0, abs=0.01)
    assert xy[1] == pytest.approx(5.0, abs=0.01)


def test_surface_type_roundtrip():
    _, surf = _mini_ctx()
    assert hb.is_sparse_like(surf)
    assert hb.set_surface_type(surf, "internal_solid")
    assert not hb.is_sparse_like(surf)


def test_extra_perimeters_rw():
    _, surf = _mini_ctx()
    assert hb.get_extra_perimeters(surf) == 1
    assert hb.set_extra_perimeters(surf, 4)
    assert hb.get_extra_perimeters(surf) == 4


def test_receipt_math():
    assert grams_from_volume_mm3(1000.0) == pytest.approx(1.24)
    assert co2e_g(1000.0) == pytest.approx(5760.0)
    assert co2e_g(1000.0, recycled=True) == pytest.approx(2470.0)
    assert co2e_g(100.0) == pytest.approx(576.0)
