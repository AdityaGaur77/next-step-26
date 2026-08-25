from __future__ import annotations

import numpy as np

UM = 1_000_000.0


class MockPoint:
    def __init__(self, x_mm: float, y_mm: float):
        self.x = int(round(x_mm * UM))
        self.y = int(round(y_mm * UM))


class MockContour:
    def __init__(self, points):
        self.points = points


class MockExpolygon:
    def __init__(self, contour: MockContour):
        self.contour = contour


class MockSurface:
    def __init__(self, polygon_pts_mm, surface_type="internal", extra_perimeters=1):
        self.surface_type = surface_type
        self.extra_perimeters = extra_perimeters
        self.expolygon = MockExpolygon(MockContour([MockPoint(x, y) for x, y in polygon_pts_mm]))

    @property
    def area(self):
        pts = self.expolygon.contour.points
        a = 0.0
        n = len(pts)
        for i in range(n):
            x0, y0 = pts[i].x, pts[i].y
            x1, y1 = pts[(i + 1) % n].x, pts[(i + 1) % n].y
            a += x0 * y1 - x1 * y0
        return abs(a) / 2.0


class MockLayerRegion:
    def __init__(self, surfaces):
        self.fill_surfaces = surfaces


class MockLayer:
    def __init__(self, print_z_mm: float, height_mm: float, regions):
        self.print_z = print_z_mm
        self.height = height_mm
        self.regions = regions


class MockPrintObject:
    def __init__(self, vertices, triangles, layers):
        self._v = np.asarray(vertices, dtype=np.float32)
        self._t = np.asarray(triangles, dtype=np.int32)
        self.layers = layers

    def vertices(self):
        return self._v

    def triangles(self):
        return self._t


class MockCtx:
    def __init__(self, objects):
        self.objects = objects


def rect_pts(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def cantilever_ctx(
    length_mm=100.0,
    width_mm=10.0,
    height_mm=10.0,
    layer_h=0.5,
    baseline_extra_perimeters=1,
    segments_x=10,
):
    from ecoslice.voxelize import box_mesh

    v, t = box_mesh(length_mm, width_mm, height_mm)
    n_layers = max(2, int(round(height_mm / layer_h)))
    layers = []
    seg_w = length_mm / segments_x
    for i in range(n_layers):
        pz = (i + 1) * layer_h
        surfaces = []
        for s in range(segments_x):
            surf = MockSurface(
                rect_pts(s * seg_w, 0.0, (s + 1) * seg_w, width_mm),
                surface_type="internal",
                extra_perimeters=baseline_extra_perimeters,
            )
            surfaces.append(surf)
        layers.append(MockLayer(pz, layer_h, [MockLayerRegion(surfaces)]))
    obj = MockPrintObject(v, t, layers)
    return MockCtx([obj]), obj
