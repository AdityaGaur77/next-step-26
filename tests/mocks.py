"""Mocks mirroring OrcaSlicer's embedded-Python bindings.

Shapes follow `src/slic3r/plugin/host/PluginHost*.cpp` in OrcaSlicer/OrcaSlicer:
`LayerRegion.fill_surfaces` is a `SurfaceCollection` (not a list), `Surface.surface_type`
is a `SurfaceType` enum (not a string), meshes come from `ModelVolume.mesh()` in the
volume's own frame, and layers/regions are methods rather than attributes.
"""

from __future__ import annotations

import enum

import numpy as np

UM = 1_000_000.0


class SurfaceType(enum.Enum):
    stTop = 0
    stBottom = 1
    stBottomBridge = 2
    stInternalAfterExternalBridge = 3
    stInternal = 4
    stInternalSolid = 5
    stInternalBridge = 6
    stSecondInternalBridge = 7
    stInternalVoid = 8
    stPerimeter = 9


SOLID_TYPES = {
    SurfaceType.stTop,
    SurfaceType.stBottom,
    SurfaceType.stBottomBridge,
    SurfaceType.stInternalAfterExternalBridge,
    SurfaceType.stInternalSolid,
    SurfaceType.stInternalBridge,
    SurfaceType.stSecondInternalBridge,
}
INTERNAL_TYPES = {
    SurfaceType.stInternal,
    SurfaceType.stInternalSolid,
    SurfaceType.stInternalVoid,
    SurfaceType.stInternalBridge,
    SurfaceType.stSecondInternalBridge,
    SurfaceType.stInternalAfterExternalBridge,
}


class Point:
    def __init__(self, x_mm: float, y_mm: float):
        self.x = int(round(x_mm * UM))
        self.y = int(round(y_mm * UM))


class Polygon:
    def __init__(self, points):
        self.points = points

    def as_array(self):
        return np.array([[p.x, p.y] for p in self.points], dtype=np.int64)

    def centroid(self):
        a = self.as_array().astype(np.float64)
        x, y = a[:, 0], a[:, 1]
        cross = x * np.roll(y, -1) - np.roll(x, -1) * y
        area = cross.sum() / 2.0
        if abs(area) < 1e-9:
            return Point(x.mean() / UM, y.mean() / UM)
        cx = ((x + np.roll(x, -1)) * cross).sum() / (6.0 * area)
        cy = ((y + np.roll(y, -1)) * cross).sum() / (6.0 * area)
        return Point(cx / UM, cy / UM)

    def area(self):
        a = self.as_array().astype(np.float64)
        x, y = a[:, 0], a[:, 1]
        return abs((x * np.roll(y, -1) - np.roll(x, -1) * y).sum()) / 2.0


class ExPolygon:
    def __init__(self, contour: Polygon, holes=None):
        self.contour = contour
        self.holes = holes or []


class Surface:
    def __init__(self, polygon_pts_mm, surface_type=SurfaceType.stInternal, extra_perimeters=1):
        self.surface_type = surface_type
        self.extra_perimeters = extra_perimeters
        self.thickness = 0.2
        self.expolygon = ExPolygon(Polygon([Point(x, y) for x, y in polygon_pts_mm]))

    def area(self):
        return self.expolygon.contour.area()

    def is_solid(self):
        return self.surface_type in SOLID_TYPES

    def is_internal(self):
        return self.surface_type in INTERNAL_TYPES

    def is_external(self):
        return self.surface_type in (SurfaceType.stTop, SurfaceType.stBottom)


class SurfaceCollection:
    def __init__(self, surfaces):
        self._surfaces = list(surfaces)

    @property
    def surfaces(self):
        return list(self._surfaces)

    def size(self):
        return len(self._surfaces)

    def empty(self):
        return not self._surfaces


class LayerRegion:
    def __init__(self, surfaces):
        self.fill_surfaces = SurfaceCollection(surfaces)
        self.slices = SurfaceCollection(surfaces)


class Layer:
    def __init__(self, print_z_mm: float, height_mm: float, regions):
        self.print_z = print_z_mm
        self.slice_z = print_z_mm - height_mm / 2
        self.height = height_mm
        self._regions = list(regions)

    def regions(self):
        return list(self._regions)


class TriangleMesh:
    def __init__(self, vertices, triangles):
        self._v = np.asarray(vertices, dtype=np.float32)
        self._t = np.asarray(triangles, dtype=np.int32)

    def vertices(self):
        return self._v

    def triangles(self):
        return self._t

    def vertex_count(self):
        return int(self._v.shape[0])

    def triangle_count(self):
        return int(self._t.shape[0])


class ModelVolume:
    def __init__(self, mesh: TriangleMesh, matrix=None, model_part=True):
        self._mesh = mesh
        self._matrix = np.eye(4) if matrix is None else np.asarray(matrix, dtype=np.float64)
        self._model_part = model_part

    def mesh(self):
        return self._mesh

    def matrix(self):
        return self._matrix

    def is_model_part(self):
        return self._model_part

    def is_modifier(self):
        return not self._model_part


class ModelObject:
    def __init__(self, volumes):
        self._volumes = list(volumes)

    def volumes(self):
        return list(self._volumes)


class PrintObject:
    def __init__(self, model_object: ModelObject, layers, trafo=None, footprint_mm=None, oid=1):
        self._model_object = model_object
        self._layers = list(layers)
        self._trafo = np.eye(4) if trafo is None else np.asarray(trafo, dtype=np.float64)
        self._footprint_mm = footprint_mm
        self._id = oid

    def id(self):
        return self._id

    def layers(self):
        return list(self._layers)

    def model_object(self):
        return self._model_object

    def trafo(self):
        return self._trafo

    def bounding_box(self):
        if self._footprint_mm is None:
            return None
        return tuple(int(round(v * UM)) for v in self._footprint_mm)


class Print:
    def __init__(self, objects):
        self._objects = list(objects)

    def objects(self):
        return list(self._objects)


class Ctx:
    """SlicingPipelineContext: `object` is set on per-object steps."""

    def __init__(self, objects, current=None, config=None):
        self.print = Print(objects)
        self.object = current if current is not None else (objects[0] if objects else None)
        self._config = config or {}

    def config_value(self, key):
        return self._config.get(key)

    def cancelled(self):
        return False


class LegacyCtx:
    """Duck-typed host used to prove the fallback access paths still work."""

    def __init__(self, objects):
        self.objects = objects


class LegacySurface:
    def __init__(self, polygon_pts_mm, surface_type="internal", extra_perimeters=1):
        self.surface_type = surface_type
        self.extra_perimeters = extra_perimeters
        self.expolygon = ExPolygon(Polygon([Point(x, y) for x, y in polygon_pts_mm]))

    def area(self):
        return self.expolygon.contour.area()


class LegacyRegion:
    def __init__(self, surfaces):
        self.fill_surfaces = list(surfaces)


class LegacyLayer:
    def __init__(self, print_z_mm, height_mm, regions):
        self.print_z = print_z_mm
        self.height = height_mm
        self.regions = list(regions)


class LegacyObject:
    def __init__(self, vertices, triangles, layers):
        self._v = np.asarray(vertices, dtype=np.float32)
        self._t = np.asarray(triangles, dtype=np.int32)
        self.layers = list(layers)

    def vertices(self):
        return self._v

    def triangles(self):
        return self._t


def rect_pts(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def cantilever_ctx(
    length_mm=100.0,
    width_mm=10.0,
    height_mm=10.0,
    layer_h=0.5,
    baseline_extra_perimeters=1,
    segments_x=10,
    bed_offset=(120.0, 90.0),
    legacy=False,
):
    """A cantilever sliced on a bed, mesh in local coords and slices offset by `bed_offset`."""
    from ecoslice.voxelize import box_mesh

    v, t = box_mesh(length_mm, width_mm, height_mm)
    ox, oy = bed_offset
    n_layers = max(2, int(round(height_mm / layer_h)))
    seg_w = length_mm / segments_x

    surface_cls = LegacySurface if legacy else Surface
    region_cls = LegacyRegion if legacy else LayerRegion
    layer_cls = LegacyLayer if legacy else Layer

    layers = []
    for i in range(n_layers):
        pz = (i + 1) * layer_h
        surfaces = [
            surface_cls(
                rect_pts(ox + s * seg_w, oy, ox + (s + 1) * seg_w, oy + width_mm),
                extra_perimeters=baseline_extra_perimeters,
            )
            for s in range(segments_x)
        ]
        layers.append(layer_cls(pz, layer_h, [region_cls(surfaces)]))

    if legacy:
        obj = LegacyObject(v, t, layers)
        return LegacyCtx([obj]), obj

    volume = ModelVolume(TriangleMesh(v, t))
    obj = PrintObject(
        ModelObject([volume]),
        layers,
        footprint_mm=(ox, oy, ox + length_mm, oy + width_mm),
    )
    return Ctx([obj], config={"layer_height": layer_h}), obj
