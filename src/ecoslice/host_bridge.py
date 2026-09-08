from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("ecoslice.host")

SCALED_UNITS_PER_MM = 1_000_000.0
SCALE_GUESSES = (1_000_000.0, 1_000.0, 1.0)

SOLID_ENUM_NAMES = ("stInternalSolid", "stSolid", "InternalSolid")
SPARSE_ENUM_NAMES = ("stInternal", "Internal")
DOWNGRADABLE_ENUM_NAMES = ("stInternalSolid",)
SOLID_TYPE_NAMES = ("internal_solid", "internalsolid", "solid", "stinternalsolid")
SPARSE_HINTS = ("internal", "sparse", "infill")


def _first_attr(obj, names, default=None):
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _call_or_attr(obj, name, default=None):
    try:
        v = getattr(obj, name)
    except AttributeError:
        return default
    if callable(v):
        try:
            return v()
        except Exception as exc:
            log.debug("calling %s() failed: %s", name, exc)
            return default
    return v


@dataclass
class HostCapabilities:
    mesh_source: str = "none"
    surface_type_mode: str = "unavailable"
    extra_perimeters_mode: str = "unavailable"
    fill_surfaces_mode: str = "unavailable"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "mesh_source": self.mesh_source,
            "surface_type_mode": self.surface_type_mode,
            "extra_perimeters_mode": self.extra_perimeters_mode,
            "fill_surfaces_mode": self.fill_surfaces_mode,
            "notes": list(self.notes),
        }


def current_object(ctx):
    return getattr(ctx, "object", None)


def iter_print_objects(ctx) -> list:
    obj = current_object(ctx)
    if obj is not None:
        return [obj]
    for attr in ("objects", "print_objects", "model_objects"):
        objs = _call_or_attr(ctx, attr)
        if objs:
            return list(objs)
    po = _call_or_attr(ctx, "print") or _first_attr(ctx, ("print",))
    if po is not None:
        objs = _call_or_attr(po, "objects")
        if objs:
            return list(objs)
    return []


def object_key(obj) -> str:
    oid = _call_or_attr(obj, "id")
    if isinstance(oid, (int, str)):
        return f"obj{oid}"
    return f"obj@{id(obj):x}"


def _apply_affine(verts: np.ndarray, matrix) -> np.ndarray:
    if matrix is None:
        return verts
    m = np.asarray(matrix, dtype=np.float64)
    if m.shape != (4, 4):
        return verts
    return verts @ m[:3, :3].T + m[:3, 3]


def _mesh_arrays(mesh) -> tuple | None:
    verts = _call_or_attr(mesh, "vertices")
    tris = _call_or_attr(mesh, "triangles")
    if tris is None:
        tris = _call_or_attr(mesh, "indices")
    if verts is None or tris is None:
        return None
    v = np.array(verts, dtype=np.float64, copy=True)
    t = np.array(tris, dtype=np.int64, copy=True)
    if v.ndim != 2 or v.shape[1] != 3 or t.ndim != 2 or t.shape[1] != 3:
        return None
    return v, t


def get_mesh(print_object) -> tuple | None:
    """Mesh of a PrintObject in print coordinates (mm).

    OrcaSlicer hands out per-volume meshes in the volume's own local frame
    (PluginHostMesh.cpp), so volume->object (`ModelVolume.matrix()`) and
    object->print (`PrintObject.trafo()`) have to be applied before the geometry
    lines up with the sliced layers.
    """
    trafo = _call_or_attr(print_object, "trafo")
    model_object = _call_or_attr(print_object, "model_object")
    parts: list[tuple[np.ndarray, np.ndarray]] = []

    if model_object is not None:
        volumes = _call_or_attr(model_object, "volumes") or []
        for vol in volumes:
            is_part = _call_or_attr(vol, "is_model_part")
            if is_part is False:
                continue
            mesh = _call_or_attr(vol, "mesh")
            arrays = _mesh_arrays(mesh) if mesh is not None else None
            if arrays is None:
                continue
            v, t = arrays
            parts.append((_apply_affine(v, _call_or_attr(vol, "matrix")), t))

    if not parts:
        arrays = _mesh_arrays(print_object)
        if arrays is None:
            mesh = _first_attr(print_object, ("mesh", "raw_mesh"))
            arrays = _mesh_arrays(mesh) if mesh is not None else None
        if arrays is None:
            return None
        parts.append(arrays)

    verts = []
    tris = []
    offset = 0
    for v, t in parts:
        verts.append(v)
        tris.append(t + offset)
        offset += v.shape[0]
    v_all = _apply_affine(np.vstack(verts), trafo)
    return v_all.astype(np.float32), np.vstack(tris).astype(np.int32)


def object_footprint_mm(print_object) -> tuple | None:
    bb = _call_or_attr(print_object, "bounding_box")
    if bb is None:
        return None
    try:
        values = [float(x) for x in bb]
    except TypeError:
        return None
    if len(values) != 4:
        return None
    min_x, min_y, max_x, max_y = values
    return (
        min_x / SCALED_UNITS_PER_MM,
        min_y / SCALED_UNITS_PER_MM,
        max_x / SCALED_UNITS_PER_MM,
        max_y / SCALED_UNITS_PER_MM,
    )


def iter_layers(print_object) -> list:
    layers = _call_or_attr(print_object, "layers")
    if layers:
        return list(layers)
    return []


def iter_regions(layer) -> list:
    regions = _call_or_attr(layer, "regions")
    if regions:
        return list(regions)
    return []


def get_fill_surfaces(region) -> list:
    """Surfaces of a LayerRegion.

    `LayerRegion.fill_surfaces` is a `SurfaceCollection`, not a sequence: its
    `.surfaces` property is what yields live `Surface` references.
    """
    fs = getattr(region, "fill_surfaces", None)
    if fs is None:
        return []
    surfaces = getattr(fs, "surfaces", None)
    if surfaces is not None and not callable(surfaces):
        return list(surfaces)
    if isinstance(fs, (list, tuple)):
        return list(fs)
    try:
        return list(fs)
    except TypeError:
        log.debug("fill_surfaces of %r is neither a collection nor iterable", type(fs).__name__)
        return []


def get_layer_z(layer) -> tuple[float, float] | None:
    pz = _call_or_attr(layer, "print_z")
    hh = _call_or_attr(layer, "height")
    if pz is None:
        slice_z = _call_or_attr(layer, "slice_z")
        if slice_z is None:
            return None
        z = float(slice_z)
        return (z - 0.1, z + 0.1)
    z = float(pz)
    h = float(hh) if hh else 0.2
    return (z - h, z)


def _contour_of(surface):
    expoly = _first_attr(surface, ("expolygon", "expoly", "poly"))
    if expoly is None:
        return None
    return _first_attr(expoly, ("contour", "outer", "polygon"))


def _contour_centroid_scaled(contour) -> tuple[float, float] | None:
    centroid = _call_or_attr(contour, "centroid")
    if centroid is not None:
        x = getattr(centroid, "x", None)
        y = getattr(centroid, "y", None)
        if x is not None and y is not None:
            return (float(x), float(y))

    arr = _call_or_attr(contour, "as_array")
    if arr is not None:
        a = np.asarray(arr, dtype=np.float64)
        if a.ndim == 2 and a.shape[1] == 2 and a.shape[0] > 0:
            return (float(a[:, 0].mean()), float(a[:, 1].mean()))

    pts = _first_attr(contour, ("points", "pts"))
    if pts is None:
        return None
    sx = sy = 0.0
    count = 0
    for p in pts:
        x = getattr(p, "x", None)
        y = getattr(p, "y", None)
        if x is None:
            try:
                x, y = p[0], p[1]
            except Exception:
                continue
        sx += float(x)
        sy += float(y)
        count += 1
    if count == 0:
        return None
    return (sx / count, sy / count)


def _contour_points_scaled(contour) -> np.ndarray | None:
    arr = _call_or_attr(contour, "as_array")
    if arr is not None:
        a = np.asarray(arr, dtype=np.float64)
        if a.ndim == 2 and a.shape[1] == 2 and a.shape[0] > 0:
            return a
    pts = _first_attr(contour, ("points", "pts"))
    if pts is None:
        return None
    rows = []
    for p in pts:
        x = getattr(p, "x", None)
        y = getattr(p, "y", None)
        if x is None:
            try:
                x, y = p[0], p[1]
            except Exception:
                continue
        rows.append((float(x), float(y)))
    if not rows:
        return None
    return np.asarray(rows, dtype=np.float64)


def slice_bbox_mm(print_object, max_layers: int = 16) -> tuple | None:
    """Bounding box of the sliced fill polygons, in mm, in the frame the host
    keeps layer geometry in.

    OrcaSlicer keeps fill_surfaces in the object-local frame while the mesh
    (after `trafo()`) sits in the plate frame; this bbox is the ground truth
    for anchoring the analysis grid onto the slices regardless of either
    frame's origin.
    """
    min_pt = None
    max_pt = None
    layers_with_points = 0
    for layer in iter_layers(print_object):
        if layers_with_points >= max_layers:
            break
        lo = hi = None
        for region in iter_regions(layer):
            for surface in get_fill_surfaces(region):
                contour = _contour_of(surface)
                if contour is None:
                    continue
                a = _contour_points_scaled(contour)
                if a is None:
                    continue
                lo = a.min(axis=0) if lo is None else np.minimum(lo, a.min(axis=0))
                hi = a.max(axis=0) if hi is None else np.maximum(hi, a.max(axis=0))
        if lo is None or hi is None:
            continue
        layers_with_points += 1
        min_pt = lo if min_pt is None else np.minimum(min_pt, lo)
        max_pt = hi if max_pt is None else np.maximum(max_pt, hi)
    if min_pt is None or max_pt is None:
        return None
    extent = float(((max_pt - min_pt) ** 2).sum() ** 0.5)
    scale = SCALED_UNITS_PER_MM
    for s in SCALE_GUESSES:
        if 1e-2 < extent / s < 1e5:
            scale = s
            break
    return (
        float(min_pt[0]) / scale,
        float(min_pt[1]) / scale,
        float(max_pt[0]) / scale,
        float(max_pt[1]) / scale,
    )


def surface_outline_mm(surface, scale: float | None = None) -> np.ndarray | None:
    """Contour points of a fill surface as (N,2) mm coordinates."""
    contour = _contour_of(surface)
    if contour is None:
        return None
    a = _contour_points_scaled(contour)
    if a is None:
        return None
    if scale is None:
        scale = guess_scale(surface)
    return a / scale


def surface_centroid_xy_mm(surface, scale: float | None = None) -> tuple[float, float] | None:
    contour = _contour_of(surface)
    if contour is None:
        return None
    c = _contour_centroid_scaled(contour)
    if c is None:
        return None
    mx, my = c
    if scale is not None:
        return (mx / scale, my / scale)
    for s in SCALE_GUESSES:
        if 1e-3 < abs(mx) / s < 1e5:
            return (mx / s, my / s)
    return (mx, my)


def guess_scale(surface) -> float:
    area = _call_or_attr(surface, "area")
    if area and abs(area) > 0:
        for s in SCALE_GUESSES:
            side = (abs(area) / (s * s)) ** 0.5
            if 1e-3 < side < 1e4:
                return s
    return SCALED_UNITS_PER_MM


def get_surface_type(surface):
    return _first_attr(surface, ("surface_type", "type_"), None)


def solid_surface_type(surface, fallback: str = "internal_solid"):
    """The value to assign to `Surface.surface_type` for solid infill.

    Inside OrcaSlicer the field is a `SurfaceType` pybind enum, so the enum
    member is resolved from the live value's own type; string mode is only for
    hosts (and tests) that model the field as text.
    """
    current = get_surface_type(surface)
    if current is None or isinstance(current, str):
        return fallback
    cls = type(current)
    for name in SOLID_ENUM_NAMES:
        member = getattr(cls, name, None)
        if member is not None:
            return member
    return fallback


def sparse_surface_type(surface, fallback: str = "internal"):
    """The value meaning ordinary sparse internal infill, resolved like `solid_surface_type`."""
    current = get_surface_type(surface)
    if current is None or isinstance(current, str):
        return fallback
    cls = type(current)
    for name in SPARSE_ENUM_NAMES:
        member = getattr(cls, name, None)
        if member is not None:
            return member
    return fallback


def is_downgradable_solid(surface) -> bool:
    """True only for density-driven internal solid infill (`stInternalSolid`).

    Top, bottom and every bridge variant are load-bearing or visible shells that
    the slicer created for a reason, so they are never candidates for being
    thinned back out to sparse — only the internal solid the shell-thickness
    logic added is.
    """
    st = get_surface_type(surface)
    if st is None:
        return False
    if isinstance(st, str):
        return st.strip().lower().replace("_", "") in ("internalsolid", "stinternalsolid")
    name = getattr(st, "name", None) or str(st).rsplit(".", 1)[-1]
    return name in DOWNGRADABLE_ENUM_NAMES


def set_surface_sparse(surface) -> bool:
    """Reclassify a solid internal surface back to sparse infill."""
    if not is_downgradable_solid(surface):
        return False
    value = sparse_surface_type(surface)
    for attr in ("surface_type", "type_"):
        if hasattr(surface, attr):
            try:
                setattr(surface, attr, value)
                return True
            except Exception as exc:
                log.debug("set_surface_sparse failed on %r: %s", attr, exc)
    return False


def set_surface_type(surface, solid_type=None) -> bool:
    value = solid_type
    if value is None or isinstance(value, str):
        resolved = solid_surface_type(surface, fallback=value or "internal_solid")
        value = resolved
    for attr in ("surface_type", "type_"):
        if hasattr(surface, attr):
            try:
                setattr(surface, attr, value)
                return True
            except Exception as exc:
                log.debug("set_surface_type failed on %r: %s", attr, exc)
    return False


def is_sparse_like(surface) -> bool:
    is_solid = _call_or_attr(surface, "is_solid")
    is_internal = _call_or_attr(surface, "is_internal")
    if isinstance(is_solid, bool) and isinstance(is_internal, bool):
        return is_internal and not is_solid
    if isinstance(is_solid, bool):
        return not is_solid
    st = get_surface_type(surface)
    if st is None:
        return True
    name = str(st).lower()
    return any(h in name for h in SPARSE_HINTS) and not any(s in name for s in SOLID_TYPE_NAMES)


def get_extra_perimeters(surface) -> int:
    ep = _first_attr(surface, ("extra_perimeters",), None)
    try:
        return int(ep) if ep is not None else 0
    except (TypeError, ValueError):
        return 0


def set_extra_perimeters(surface, value: int) -> bool:
    if hasattr(surface, "extra_perimeters"):
        try:
            setattr(surface, "extra_perimeters", int(value))
            return True
        except Exception as exc:
            log.debug("set_extra_perimeters failed: %s", exc)
    return False


def probe(ctx) -> HostCapabilities:
    cap = HostCapabilities()
    objs = iter_print_objects(ctx)
    if not objs:
        cap.notes.append("no print objects found via known ctx paths")
        return cap
    mesh = get_mesh(objs[0])
    cap.mesh_source = type(objs[0]).__name__ + ("+mesh" if mesh else "-no-mesh")
    layers = iter_layers(objs[0])
    for layer in layers[:5]:
        for region in iter_regions(layer):
            surfaces = get_fill_surfaces(region)
            if surfaces:
                s = surfaces[0]
                st = get_surface_type(s)
                cap.surface_type_mode = (
                    "unavailable"
                    if st is None
                    else ("string" if isinstance(st, str) else type(st).__name__)
                )
                cap.extra_perimeters_mode = (
                    "writable" if hasattr(s, "extra_perimeters") else "missing"
                )
                cap.fill_surfaces_mode = f"n={len(surfaces)}"
                return cap
    cap.notes.append("no fill_surfaces found in first layers")
    return cap
