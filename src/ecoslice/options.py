from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .mapping import Plan, plan_from_stress, region_boundary_length_mm
from .receipt import co2e_g, energy_kwh, grams_from_volume_mm3

# Extrusion geometry / machine constants used to turn a plan into predicted cost.
DEFAULT_LINE_WIDTH_MM = 0.42
DEFAULT_SPARSE_INFILL_DENSITY = 0.15
# Volumetric throughput of a 0.4 mm nozzle on PLA at typical desktop speeds; used
# only to turn *added* extrusion volume into an added-time estimate.
NOMINAL_FLOW_MM3_S = 8.0

CONFIDENCE_LABELS = ((0.75, "high"), (0.50, "moderate"), (0.0, "low"))
# A part over its allowable stress can never read 'high', whatever the plan does.
OVER_ALLOWABLE_SCORE_CAP = 0.60


@dataclass
class Confidence:
    """Heuristic trust score for a plan — explicitly not an engineering certification."""

    score: float
    label: str
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "confidence": round(self.score, 2),
            "confidence_label": self.label,
            "confidence_reasons": "; ".join(self.reasons),
        }


def _label_for(score: float) -> str:
    for threshold, label in CONFIDENCE_LABELS:
        if score >= threshold:
            return label
    return "low"


def strength_confidence(plan: Plan, grid, von_mises: np.ndarray, at_risk_frac: float = 1.0) -> Confidence:
    """Blend stress margin, hotspot coverage and mesh adequacy into one 0-1 score.

    Three things can make a plan untrustworthy and each is scored separately so
    the receipt can say *which* one is weak: the part may be over its allowable
    stress however it is reinforced, the reinforcement may miss hotspots, or the
    voxel grid may be too coarse through the thin direction to resolve bending
    at all.

    Coverage is measured against voxels *at or over* the allowable stress, not
    against the planner's lower reinforce threshold: a part comfortably inside
    its margin has nothing at risk, and must not be marked down for the planner
    correctly deciding it needs no extra material.
    """
    reasons: list[str] = []
    allowable = plan.allowable_mpa or 1e-9
    util_max = float(plan.max_vm_mpa) / allowable

    over_allowable = util_max > 1.0
    if util_max <= 0.8:
        margin = 1.0
        reasons.append(f"peak stress {util_max:.2f}x allowable - inside margin")
    elif util_max <= 1.0:
        margin = float(1.0 - 0.4 * (util_max - 0.8) / 0.2)
        reasons.append(f"peak stress {util_max:.2f}x allowable - close to the limit")
    elif util_max >= 2.0:
        margin = 0.0
        reasons.append(f"peak stress {util_max:.2f}x allowable - part is under-sized, not a wall problem")
    else:
        margin = float(0.6 * (2.0 - util_max))
        reasons.append(f"peak stress {util_max:.2f}x allowable - OVER the allowable, reinforcement alone will not fix this")

    hot_total = 0
    hot_covered = 0
    h = grid.h
    origin_z = float(grid.origin[2])
    util = np.zeros_like(von_mises)
    occ = grid.mask & (von_mises > 0)
    util[occ] = von_mises[occ] / allowable
    for action in plan.actions:
        k0 = int(round((action.z0_mm - origin_z) / h))
        k1 = int(round((action.z1_mm - origin_z) / h))
        band = util[:, :, k0:k1]
        if band.size == 0:
            continue
        hot = band >= at_risk_frac
        n_hot = int(hot.sum())
        if n_hot == 0:
            continue
        hot_total += n_hot
        hot_covered += int((hot & action.reinforce_xy[:, :, None]).sum())
    if hot_total == 0:
        coverage = 1.0
        reasons.append("no voxel reaches the allowable stress - nothing at risk")
    else:
        coverage = hot_covered / hot_total
        reasons.append(
            f"{coverage * 100:.0f}% of at-risk voxels sit under reinforced columns"
        )

    cells_across = int(min(grid.shape))
    if cells_across >= 8:
        mesh = 1.0
    elif cells_across >= 4:
        mesh = 0.5 + 0.5 * (cells_across - 4) / 4.0
    else:
        mesh = 0.25
        reasons.append(f"only {cells_across} cells across the thinnest axis - bending is under-resolved")
    if cells_across >= 8:
        reasons.append(f"{cells_across} cells across the thinnest axis")
    elif cells_across >= 4:
        reasons.append(f"{cells_across} cells across the thinnest axis - coarse for bending")

    score = 0.45 * margin + 0.35 * coverage + 0.20 * mesh
    if over_allowable:
        # No wall or infill change rescues a part whose peak stress already exceeds
        # yield/sf, so such a plan must never present as high confidence however
        # well the reinforcement is placed. Re-orient, resize, or drop the safety
        # factor instead.
        score = min(score, OVER_ALLOWABLE_SCORE_CAP)
    score = float(min(max(score, 0.0), 1.0))
    return Confidence(score=score, label=_label_for(score), reasons=reasons)


def estimate_material(
    plan: Plan,
    grid,
    *,
    add_perimeters: int,
    layer_height_mm: float,
    enable_solid_infill: bool = True,
    infill_density: float = DEFAULT_SPARSE_INFILL_DENSITY,
    line_width_mm: float = DEFAULT_LINE_WIDTH_MM,
) -> dict:
    """Material a plan adds, split into wall lines and solid-infill fill-in.

    Both levers are counted: extra perimeters trace the *boundary* of each
    reinforced region, while reclassifying sparse infill to solid fills its
    *area* from the profile's sparse density up to 100%. The solid term is
    usually the larger of the two, which is why it cannot be left out.

    The "uniform" figure is the same treatment applied to the whole occupied
    footprint of every band — the blanket-strengthening baseline the savings
    claim is measured against.
    """
    lh = layer_height_mm if layer_height_mm and layer_height_mm > 0 else 0.2
    h = grid.h
    fill_fraction = max(0.0, 1.0 - float(infill_density)) if enable_solid_infill else 0.0

    wall_vol = uniform_wall_vol = 0.0
    infill_vol = uniform_infill_vol = 0.0
    cell_area = h * h

    for a in plan.actions:
        n_print_layers = max(1, int(round((a.z1_mm - a.z0_mm) / lh)))
        occupied = a.occupied_xy if a.occupied_xy is not None else np.ones_like(a.reinforce_xy)

        hot_len = region_boundary_length_mm(a.reinforce_xy, h)
        all_len = region_boundary_length_mm(occupied, h)
        wall_vol += hot_len * add_perimeters * line_width_mm * lh * n_print_layers
        uniform_wall_vol += all_len * add_perimeters * line_width_mm * lh * n_print_layers

        hot_area = float(a.reinforce_xy.sum()) * cell_area
        all_area = float(occupied.sum()) * cell_area
        infill_vol += hot_area * fill_fraction * lh * n_print_layers
        uniform_infill_vol += all_area * fill_fraction * lh * n_print_layers

    added_wall_g = grams_from_volume_mm3(wall_vol)
    added_infill_g = grams_from_volume_mm3(infill_vol)
    added_g = added_wall_g + added_infill_g
    uniform_g = grams_from_volume_mm3(uniform_wall_vol + uniform_infill_vol)
    saved = max(uniform_g - added_g, 0.0)

    added_volume = wall_vol + infill_vol
    added_time_s = added_volume / NOMINAL_FLOW_MM3_S if NOMINAL_FLOW_MM3_S > 0 else 0.0

    return {
        "added_wall_grams": round(added_wall_g, 3),
        "added_infill_grams": round(added_infill_g, 3),
        "added_grams": round(added_g, 3),
        "uniform_baseline_grams": round(uniform_g, 3),
        "saved_vs_uniform_grams": round(saved, 3),
        "co2e_saved_vs_uniform_g": round(co2e_g(saved), 2),
        "added_co2e_g": round(co2e_g(added_g), 2),
        "added_print_time_s": round(added_time_s, 1),
        "added_energy_kwh": round(energy_kwh(added_time_s / 3600.0), 4),
    }


@dataclass(frozen=True)
class OptionPreset:
    """One of the three transparent choices offered for a part."""

    key: str
    name: str
    blurb: str
    add_perimeters: int
    max_extra_perimeters: int
    enable_solid_infill: bool
    enable_relax: bool
    reinforce_frac: float
    relax_frac: float


PRESETS: tuple[OptionPreset, ...] = (
    OptionPreset(
        key="eco",
        name="Eco",
        blurb="reinforce only true hotspots, relax aggressively elsewhere",
        add_perimeters=1,
        max_extra_perimeters=2,
        enable_solid_infill=False,
        enable_relax=True,
        reinforce_frac=0.80,
        relax_frac=0.35,
    ),
    OptionPreset(
        key="balanced",
        name="Balanced",
        blurb="walls and solid infill where stress demands, relax cold bands",
        add_perimeters=2,
        max_extra_perimeters=4,
        enable_solid_infill=True,
        enable_relax=True,
        reinforce_frac=0.60,
        relax_frac=0.20,
    ),
    OptionPreset(
        key="max_strength",
        name="Maximum Strength",
        blurb="widen the reinforced zone, no relaxation anywhere",
        add_perimeters=3,
        max_extra_perimeters=6,
        enable_solid_infill=True,
        enable_relax=False,
        reinforce_frac=0.40,
        relax_frac=0.0,
    ),
)

PRESETS_BY_KEY = {p.key: p for p in PRESETS}


@dataclass
class OptionReport:
    preset: OptionPreset
    plan: Plan
    confidence: Confidence
    material: dict

    def as_dict(self) -> dict:
        d = {
            "key": self.preset.key,
            "name": self.preset.name,
            "blurb": self.preset.blurb,
            "reinforced_layers": self.plan.n_reinforced_layers,
            "relaxed_layers": self.plan.n_relaxed_layers,
        }
        d.update(self.material)
        d.update(self.confidence.as_dict())
        return d


def build_options(
    grid,
    von_mises: np.ndarray,
    *,
    yield_mpa: float,
    safety_factor: float,
    layer_height_mm: float | None = None,
    infill_density: float = DEFAULT_SPARSE_INFILL_DENSITY,
    presets: tuple[OptionPreset, ...] = PRESETS,
) -> list[OptionReport]:
    """Eco / Balanced / Maximum Strength from a single FEM solve.

    Only the plan thresholds and mutation strength change between options, so the
    stress field is solved once and re-thresholded — three options cost three
    cheap passes over the von Mises field, not three solves.
    """
    reports: list[OptionReport] = []
    for preset in presets:
        plan = plan_from_stress(
            grid,
            von_mises,
            yield_mpa=yield_mpa,
            safety_factor=safety_factor,
            layer_height_mm=layer_height_mm,
            reinforce_frac=preset.reinforce_frac,
            relax_frac=preset.relax_frac,
        )
        material = estimate_material(
            plan,
            grid,
            add_perimeters=preset.add_perimeters,
            layer_height_mm=layer_height_mm or 0.2,
            enable_solid_infill=preset.enable_solid_infill,
            infill_density=infill_density,
        )
        confidence = strength_confidence(plan, grid, von_mises)
        reports.append(OptionReport(preset=preset, plan=plan, confidence=confidence, material=material))
    return reports


def options_table(reports: list[OptionReport]) -> str:
    header = (
        f"{'option':<18} {'+g':>7} {'wall':>7} {'infill':>7} {'-g vs blanket':>14} "
        f"{'+time':>8} {'+kWh':>7} {'conf':>6}"
    )
    lines = [header, "-" * len(header)]
    for r in reports:
        m = r.material
        lines.append(
            f"{r.preset.name:<18} {m['added_grams']:>7.2f} {m['added_wall_grams']:>7.2f} "
            f"{m['added_infill_grams']:>7.2f} {m['saved_vs_uniform_grams']:>14.2f} "
            f"{m['added_print_time_s'] / 60.0:>7.1f}m {m['added_energy_kwh']:>7.4f} "
            f"{r.confidence.score:>5.2f} {r.confidence.label}"
        )
    return "\n".join(lines)
