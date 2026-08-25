from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .host_bridge import (
    get_extra_perimeters,
    get_fill_surfaces,
    get_layer_z,
    guess_scale,
    is_sparse_like,
    iter_layers,
    iter_regions,
    set_extra_perimeters,
    set_surface_type,
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


def _classify_xy(x_mm: float, y_mm: float, action: LayerAction, grid, bbox) -> str:
    x0, y0, _ = bbox[0]
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


def apply_to_region(region, action: LayerAction, grid, bbox, cfg: MutationConfig) -> MutationStats:
    stats = MutationStats()
    scale = None
    for surface in get_fill_surfaces(region):
        if scale is None:
            scale = guess_scale(surface)
        xy = surface_centroid_xy_mm(surface, scale=scale)
        if xy is None:
            stats.surfaces_skipped_no_xy += 1
            continue
        cls = _classify_xy(xy[0], xy[1], action, grid, bbox)

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


def apply_plan_to_object(print_object, plan, grid, bbox, cfg: MutationConfig) -> MutationStats:
    total = MutationStats()
    for layer in iter_layers(print_object):
        zr = get_layer_z(layer)
        if zr is None:
            continue
        z_mid = (zr[0] + zr[1]) * 0.5
        for action in plan.actions:
            if action.z0_mm <= z_mid <= action.z1_mm or (
                z_mid >= action.z0_mm and z_mid < action.z1_mm
            ):
                for region in iter_regions(layer):
                    total.merge(apply_to_region(region, action, grid, bbox, cfg))
                break
    return total
