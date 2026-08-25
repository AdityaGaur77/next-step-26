from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .host_bridge import (
    HostCapabilities,
    get_mesh,
    iter_print_objects,
    probe,
)
from .receipt import (
    co2e_g,
    grams_from_volume_mm3,
    receipt_block,
)
from .fem import solve_voxel_fem
from .loadcase import LoadCase, extract_load_case
from .mapping import Plan, plan_from_stress, region_boundary_length_mm
from .mutate import MutationConfig, MutationStats, apply_plan_to_object
from .voxelize import voxelize

log = logging.getLogger("ecoslice")

DEFAULT_RESOLUTION = 32


@dataclass
class Analysis:
    object_key: str
    load_case: LoadCase
    grid: object
    fem: object
    plan: Plan
    bbox: tuple
    wall_seconds: float
    savings: dict = field(default_factory=dict)

    def stats(self, cfg: MutationConfig, mutation: MutationStats | None = None) -> dict:
        d = {
            "mode": "load-aware walls & infill",
            "load_case": self.load_case.description[:60] or "(unnamed)",
            "safety_factor": self.load_case.safety_factor,
            "allowable_mpa": self.plan.allowable_mpa,
            "max_vm_mpa": self.plan.max_vm_mpa,
            "solver": self.fem.solver,
            "voxels": f"{self.grid.shape[0]}x{self.grid.shape[1]}x{self.grid.shape[2]}",
            "reinforced_layers": self.plan.n_reinforced_layers,
            "relaxed_layers": self.plan.n_relaxed_layers,
        }
        if mutation is not None:
            d["perimeters_added"] = mutation.perimeters_added
            d["perimeters_removed"] = mutation.perimeters_removed
        d.update(self.savings)
        d.update({f"est_{k}": v for k, v in self.savings.items()})
        return d


class EcoSlicePipeline:
    def __init__(
        self,
        cfg: MutationConfig | None = None,
        description: str | None = None,
        load_case: LoadCase | None = None,
        resolution: int = DEFAULT_RESOLUTION,
        layer_height_mm: float | None = None,
    ):
        self.cfg = cfg or MutationConfig()
        self.description = description or ""
        self._explicit_load_case = load_case
        self.resolution = resolution
        self.layer_height_mm = layer_height_mm
        self.analyses: dict[str, Analysis] = {}
        self.capabilities: HostCapabilities | None = None
        self._last_mutation: MutationStats | None = None

    def load_case_for(self, description: str) -> LoadCase:
        if self._explicit_load_case is not None:
            return self._explicit_load_case
        return extract_load_case(description)

    def analyze_mesh(
        self,
        vertices: np.ndarray,
        triangles: np.ndarray,
        description: str = "",
        object_key: str = "obj",
    ) -> Analysis:
        t0 = time.perf_counter()
        lc = self.load_case_for(description)
        grid = voxelize(vertices, triangles, resolution=self.resolution)
        bbox = (tuple(grid.origin.tolist()), tuple((grid.origin + np.array(grid.shape) * grid.h).tolist()))

        total_force = np.zeros(3)
        primary_face = "z-"
        max_mag = -1.0
        for f in lc.forces:
            d = np.asarray(f.normalized()) * f.magnitude_n
            total_force += d
            if f.magnitude_n > max_mag:
                max_mag = f.magnitude_n
                primary_face = f.face
        constraint_face = lc.constraints[0].face

        fem = solve_voxel_fem(
            grid,
            fixed_faces=[constraint_face],
            load_face=primary_face,
            load_vector_n=total_force,
            young_modulus_mpa=lc.young_modulus_mpa,
            poisson=lc.poisson,
        )
        plan = plan_from_stress(
            grid,
            fem.von_mises,
            yield_mpa=lc.yield_mpa,
            safety_factor=lc.safety_factor,
            layer_height_mm=self.layer_height_mm,
        )

        analysis = Analysis(
            object_key=object_key,
            load_case=lc,
            grid=grid,
            fem=fem,
            plan=plan,
            bbox=bbox,
            wall_seconds=time.perf_counter() - t0,
        )
        analysis.savings = self._estimate_savings(plan, grid)
        self.analyses[object_key] = analysis
        return analysis

    def _estimate_savings(self, plan: Plan, grid) -> dict:
        line_width = 0.42
        lh = self.layer_height_mm or 0.2
        h = grid.h
        added_vol = 0.0
        uniform_vol = 0.0
        for a in plan.actions:
            n_print_layers = max(1, int(round((a.z1_mm - a.z0_mm) / lh)))
            hot_len = region_boundary_length_mm(a.reinforce_xy, h)
            all_len = region_boundary_length_mm(np.ones_like(a.reinforce_xy), h)
            added_vol += hot_len * self.cfg.add_perimeters * line_width * lh * n_print_layers
            uniform_vol += all_len * self.cfg.add_perimeters * line_width * lh * n_print_layers
        added_g = grams_from_volume_mm3(added_vol)
        uniform_g = grams_from_volume_mm3(uniform_vol)
        saved = max(uniform_g - added_g, 0.0)
        return {
            "added_grams": round(added_g, 3),
            "uniform_baseline_grams": round(uniform_g, 3),
            "saved_vs_uniform_grams": round(saved, 3),
            "co2e_saved_vs_uniform_g": round(co2e_g(saved), 2),
        }

    def run_offline(self, vertices, triangles, description: str, object_key: str = "obj") -> Analysis:
        analysis = self.analyze_mesh(vertices, triangles, description, object_key)
        return analysis

    def apply_mutations(self, ctx) -> MutationStats:
        total = MutationStats()
        objs = iter_print_objects(ctx)
        for idx, obj in enumerate(objs):
            key = f"obj{idx}"
            analysis = self.analyses.get(key)
            if analysis is None:
                mesh = get_mesh(obj)
                if mesh is None:
                    continue
                analysis = self.analyze_mesh(mesh[0], mesh[1], self.description, key)
            try:
                total.merge(apply_plan_to_object(obj, analysis.plan, analysis.grid, analysis.bbox, self.cfg))
            except Exception as exc:
                log.warning("mutation failed on %s: %s", key, exc)
        return total

    def on_pos_slice(self, ctx):
        try:
            if self.capabilities is None:
                self.capabilities = probe(ctx)
                log.info("host capabilities: %s", self.capabilities.as_dict())
            objs = iter_print_objects(ctx)
            for idx, obj in enumerate(objs):
                key = f"obj{idx}"
                if key in self.analyses:
                    continue
                mesh = get_mesh(obj)
                if mesh is None:
                    log.warning("no mesh access for %s", key)
                    continue
                a = self.analyze_mesh(mesh[0], mesh[1], self.description, key)
                log.info(
                    "analyzed %s in %.2fs: maxVM=%.1f MPa allow=%.1f MPa reinforced=%d relaxed=%d",
                    key,
                    a.wall_seconds,
                    a.plan.max_vm_mpa,
                    a.plan.allowable_mpa,
                    a.plan.n_reinforced_layers,
                    a.plan.n_relaxed_layers,
                )
        except Exception as exc:
            log.exception("posSlice hook failed (non-fatal): %s", exc)
        return ctx

    def on_pos_prepare_infill(self, ctx):
        try:
            stats = self.apply_mutations(ctx)
            self._last_mutation = stats
            log.info(
                "mutations: +%d perimeters added, %d removed; %d solidified",
                stats.perimeters_added,
                stats.perimeters_removed,
                stats.surfaces_solidified,
            )
        except Exception as exc:
            log.exception("posPrepareInfill hook failed (non-fatal): %s", exc)
        return ctx

    def on_gcode_postprocess(self, gcode_text: str) -> str:
        try:
            agg = self._aggregate_stats()
            block = receipt_block(agg)
            lines = gcode_text.splitlines()
            insert_at = 0
            for i, ln in enumerate(lines[:50]):
                if ln.startswith(";") and "generated" in ln.lower():
                    insert_at = i + 1
                    break
            return "\n".join(lines[:insert_at] + block.splitlines() + lines[insert_at:])
        except Exception as exc:
            log.exception("postprocess hook failed (non-fatal): %s", exc)
            return gcode_text

    def _aggregate_stats(self) -> dict:
        if not self.analyses:
            return {"mode": "eco-slice idle (no analysis ran)"}
        keys = sorted(self.analyses)
        a = self.analyses[keys[0]]
        return a.stats(self.cfg)


def default_pipeline(description: str = "", **kw) -> EcoSlicePipeline:
    return EcoSlicePipeline(description=description, **kw)
