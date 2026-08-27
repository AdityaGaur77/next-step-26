from __future__ import annotations

from dataclasses import dataclass

from .host_bridge import (
    get_extra_perimeters,
    get_fill_surfaces,
    get_layer_z,
    guess_scale,
    is_sparse_like,
    iter_layers,
    iter_regions,
    object_footprint_mm,
    set_extra_perimeters,
    set_surface_type,
    slice_bbox_mm,
    surface_centroid_xy_mm,
)
from .mapping import LayerAction


@dataclass
class MutationConfig:
    add_perimeters: int = 2
    max_extra_perimeters: int = 4
    enable_solid_infill: bool = True
    enable_relax: bool = True
    solid_type: str = "internal_solid"


@dataclass
class MutationStats:
    surfaces_reinforced: int = 0
    surfaces_solidified: int = 0
    surfaces_relaxed: int = 0
    surfaces_skipped_no_xy: int = 0
    perimeters_added: int = 0
    perimeters_removed: int = 0

    def merge(self, other: "MutationStats") -> None:
        self.surfaces_reinforced += other.surfaces_reinforced
        self.surfaces_solidified += other.surfaces_solidified
        self.surfaces_relaxed += other.surfaces_relaxed
        self.surfaces_skipped_no_xy += other.surfaces_skipped_no_xy
        self.perimeters_added += other.perimeters_added
        self.perimeters_removed += other.perimeters_removed


@dataclass(frozen=True)
class FrameAlignment:
    """Translation from slicer coordinates into the analysis grid's frame.

    The analysed mesh and the sliced polygons need not share an origin (the
    object is placed on the bed, and libslic3r keeps slices in the print
    object's own frame), so the plan is anchored by matching bounding boxes
    rather than by trusting either origin.
    """

    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0

    def to_grid_xy(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        return (x_mm - self.dx, y_mm - self.dy)

    def to_grid_z(self, z_mm: float) -> float:
        return z_mm - self.dz


def compute_alignment(print_object, grid, layers=None) -> FrameAlignment:
    dx = dy = dz = 0.0
    # Anchor xy on the slice polygons themselves: in OrcaSlicer the mesh comes
    # back in plate coordinates (trafo applied) while fill_surfaces stay in the
    # object-local frame, so footprint/grid origins alone leave the shift
    # unknown. Matching the slice bbox onto the grid footprint works whichever
    # frame either side is in.
    bbox = slice_bbox_mm(print_object)
    if bbox is not None and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
        dx = bbox[0] - float(grid.origin[0])
        dy = bbox[1] - float(grid.origin[1])
    else:
        footprint = object_footprint_mm(print_object)
        if footprint is not None:
            min_x, min_y, max_x, max_y = footprint
            if max_x > min_x and max_y > min_y:
                dx = min_x - float(grid.origin[0])
                dy = min_y - float(grid.origin[1])

    layers = iter_layers(print_object) if layers is None else layers
    for layer in layers:
        zr = get_layer_z(layer)
        if zr is not None:
            dz = zr[0] - float(grid.origin[2])
            break
    return FrameAlignment(dx=dx, dy=dy, dz=dz)


def _classify_xy(x_mm: float, y_mm: float, action: LayerAction, grid) -> str:
    x0, y0 = float(grid.origin[0]), float(grid.origin[1])
    h = grid.h
    i = int((x_mm - x0) / h)
    j = int((y_mm - y0) / h)
    nx, ny = action.reinforce_xy.shape
    if not (0 <= i < nx and 0 <= j < ny):
        return "outside"
    if action.reinforce_xy[i, j]:
        return "reinforce"
    if action.relax_xy[i, j]:
        return "relax"
    return "neutral"


def apply_to_region(
    region,
    action: LayerAction,
    grid,
    cfg: MutationConfig,
    alignment: FrameAlignment | None = None,
) -> MutationStats:
    stats = MutationStats()
    align = alignment or FrameAlignment()
    scale = None
    for surface in get_fill_surfaces(region):
        if scale is None:
            scale = guess_scale(surface)
        xy = surface_centroid_xy_mm(surface, scale=scale)
        if xy is None:
            stats.surfaces_skipped_no_xy += 1
            continue
        gx, gy = align.to_grid_xy(xy[0], xy[1])
        cls = _classify_xy(gx, gy, action, grid)

        if cls == "reinforce":
            cur = get_extra_perimeters(surface)
            target = min(cur + cfg.add_perimeters, cfg.max_extra_perimeters)
            if target != cur and set_extra_perimeters(surface, target):
                stats.surfaces_reinforced += 1
                stats.perimeters_added += target - cur
            if cfg.enable_solid_infill and is_sparse_like(surface):
                if set_surface_type(surface, cfg.solid_type):
                    stats.surfaces_solidified += 1

        elif cls == "relax" and cfg.enable_relax:
            cur = get_extra_perimeters(surface)
            if cur > 0 and set_extra_perimeters(surface, 0):
                stats.surfaces_relaxed += 1
                stats.perimeters_removed += cur

    return stats


def apply_plan_to_object(
    print_object,
    plan,
    grid,
    cfg: MutationConfig,
    alignment: FrameAlignment | None = None,
) -> MutationStats:
    total = MutationStats()
    layers = iter_layers(print_object)
    align = alignment if alignment is not None else compute_alignment(print_object, grid, layers)
    for layer in layers:
        zr = get_layer_z(layer)
        if zr is None:
            continue
        z_mid = align.to_grid_z((zr[0] + zr[1]) * 0.5)
        for action in plan.actions:
            if action.z0_mm <= z_mid < action.z1_mm or (
                action is plan.actions[-1] and z_mid == action.z1_mm
            ):
                for region in iter_regions(layer):
                    total.merge(apply_to_region(region, action, grid, cfg, align))
                break
    return total
