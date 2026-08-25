from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class LayerAction:
    z0_mm: float
    z1_mm: float
    reinforce_xy: np.ndarray
    relax_xy: np.ndarray
    mean_utilization: float
    p95_utilization: float
    n_voxels: int
    median_utilization: float = 0.0


@dataclass
class Plan:
    actions: list[LayerAction] = field(default_factory=list)
    allowable_mpa: float = 0.0
    max_vm_mpa: float = 0.0
    global_mean_utilization: float = 0.0
    n_reinforced_layers: int = 0
    n_relaxed_layers: int = 0
    solver: str = ""
    max_displacement_mm: float = 0.0

    @property
    def has_work(self) -> bool:
        return self.n_reinforced_layers > 0 or self.n_relaxed_layers > 0


def plan_from_stress(
    grid,
    von_mises: np.ndarray,
    *,
    yield_mpa: float,
    safety_factor: float,
    layer_height_mm: float | None = None,
    reinforce_frac: float = 0.60,
    hotspot_frac: float = 1.00,
    relax_frac: float = 0.20,
    min_reinforce_columns_per_layer: int = 2,
) -> Plan:
    mask = grid.mask
    h = grid.h
    allowable = yield_mpa / safety_factor
    util = np.zeros_like(von_mises)
    occ = mask & (von_mises > 0)
    util[occ] = von_mises[occ] / allowable

    if layer_height_mm is None or layer_height_mm <= 0:
        voxels_per_band = 1
    else:
        voxels_per_band = max(1, int(round(layer_height_mm / h)))

    nz = mask.shape[2]
    plan = Plan(allowable_mpa=allowable, max_vm_mpa=float(von_mises.max()))

    k = 0
    while k < nz:
        k_end = min(k + voxels_per_band, nz)
        band = occ[:, :, k:k_end]
        uband = util[:, :, k:k_end]
        n_vox = int(band.sum())
        if n_vox == 0:
            k = k_end
            continue

        vals = uband[band]
        mean_u = float(vals.mean())
        med_u = float(np.median(vals))
        p95_u = float(np.percentile(vals, 95))

        col_hot = (uband >= reinforce_frac).any(axis=2)
        col_cold = (uband <= relax_frac).all(axis=2)

        hot_count = int(col_hot.sum())
        if mean_u >= reinforce_frac or (p95_u >= hotspot_frac and hot_count >= min_reinforce_columns_per_layer):
            reinforce = col_hot
            relax = np.zeros_like(col_hot)
        elif med_u <= relax_frac and not col_hot.any():
            reinforce = np.zeros_like(col_hot)
            relax = col_cold
        else:
            reinforce = np.zeros_like(col_hot)
            relax = np.zeros_like(col_hot)

        z0 = grid.origin[2] + k * h
        z1 = grid.origin[2] + k_end * h
        action = LayerAction(
            z0_mm=float(z0),
            z1_mm=float(z1),
            reinforce_xy=reinforce,
            relax_xy=relax,
            mean_utilization=mean_u,
            p95_utilization=p95_u,
            n_voxels=n_vox,
            median_utilization=med_u,
        )
        plan.actions.append(action)
        if reinforce.any():
            plan.n_reinforced_layers += 1
        if relax.any():
            plan.n_relaxed_layers += 1
        k = k_end

    all_vals = util[occ]
    plan.global_mean_utilization = float(all_vals.mean()) if all_vals.size else 0.0
    return plan


def region_boundary_length_mm(reinforce_xy: np.ndarray, h: float) -> float:
    if not reinforce_xy.any():
        return 0.0
    padded = np.pad(reinforce_xy.astype(np.int8), 1)
    dx = np.abs(np.diff(padded, axis=1)).sum()
    dy = np.abs(np.diff(padded, axis=0)).sum()
    return float(dx + dy) * h


def plan_summary_table(plan: Plan, max_rows: int = 12) -> str:
    lines = [
        f"{'z-range (mm)':>20} {'util mean':>10} {'util p95':>9} {'reinforce':>10} {'relax':>6}",
    ]
    rows = plan.actions
    shown = rows if len(rows) <= max_rows else rows[: max_rows // 2] + [None] + rows[-(max_rows // 2) :]
    for r in shown:
        if r is None:
            lines.append(f"  ... {len(rows) - max_rows} more layers ...")
            continue
        nr = int(r.reinforce_xy.sum())
        nl = int(r.relax_xy.sum())
        lines.append(
            f"{r.z0_mm:8.2f}-{r.z1_mm:7.2f} {r.mean_utilization:10.3f} {r.p95_utilization:9.3f} "
            f"{nr:10d} {nl:6d}"
        )
    return "\n".join(lines)
