from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("ecoslice.host")

SCALE_GUESSES = (1_000_000.0, 1_000.0, 1.0)

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
        except Exception:
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


def iter_print_objects(ctx) -> list:
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


def get_mesh(obj) -> tuple | None:
    for target in (obj, _first_attr(obj, ("mesh", "model_object", "volume", "volumes"))):
        if target is None:
            continue
        verts = None
        tris = None
        for name in ("vertices", "its_vertices", "get_vertices"):
            verts = _call_or_attr(target, name)
            if verts is not None:
                break
        for name in ("triangles", "indices", "its_indices", "get_triangles"):
            tris = _call_or_attr(target, name)
            if tris is not None:
                break
        if verts is not None and tris is not None:
            import numpy as np

            return np.asarray(verts, dtype=np.float32), np.asarray(tris, dtype=np.int32)
    return None


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
    fs = _call_or_attr(region, "fill_surfaces")
    if fs is None:
        return []
    try:
        return list(fs)
    except TypeError:
        return [fs]


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


def surface_centroid_xy_mm(surface, scale: float | None = None) -> tuple[float, float] | None:
    expoly = _first_attr(surface, ("expolygon", "expolygon", "poly"))
    contour = None
    if expoly is not None:
        contour = _first_attr(expoly, ("contour", "outer", "polygon"))
    pts = None
    if contour is not None:
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
    mx, my = sx / count, sy / count
    if scale is not None:
        return (mx / scale, my / scale)
    for s in SCALE_GUESSES:
        if 1e-3 < abs(mx) / s < 1e5:
            return (mx / s, my / s)
    return (mx, my)


def guess_scale(surface) -> float:
    c = surface_centroid_xy_mm(surface, scale=None)
    if c is None:
        return SCALE_GUESSES[0]
    area = _call_or_attr(surface, "area")
    if area and abs(area) > 0:
        for s in SCALE_GUESSES:
            side = (abs(area) / (s * s)) ** 0.5
            if 1e-3 < side < 1e4:
                return s
    return SCALE_GUESSES[0]


def get_surface_type(surface) -> str | None:
    st = _first_attr(surface, ("surface_type", "type_"), None)
    if st is None:
        return None
    return str(st).lower()


def set_surface_type(surface, solid_type: str = SOLID_TYPE_NAMES[0]) -> bool:
    for attr in ("surface_type", "type_"):
        if hasattr(surface, attr):
            try:
                setattr(surface, attr, solid_type)
                return True
            except Exception as exc:
                log.debug("set_surface_type failed on %r: %s", attr, exc)
    return False


def is_sparse_like(surface) -> bool:
    st = get_surface_type(surface)
    if st is None:
        return True
    return any(h in st for h in SPARSE_HINTS) and not any(s in st for s in SOLID_TYPE_NAMES)


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
                cap.surface_type_mode = (
                    "writable" if hasattr(s, "surface_type") or hasattr(s, "type_") else "missing"
                )
                cap.extra_perimeters_mode = (
                    "writable" if hasattr(s, "extra_perimeters") else "missing"
                )
                cap.fill_surfaces_mode = f"n={len(surfaces)}"
                return cap
    cap.notes.append("no fill_surfaces found in first layers")
    return cap
