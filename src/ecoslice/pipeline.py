from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from .host_bridge import (
    HostCapabilities,
    get_mesh,
    iter_print_objects,
    object_key,
    probe,
)
from .receipt import (
    parse_gcode_footer,
    receipt_block,
)
from .options import (
    DEFAULT_SPARSE_INFILL_DENSITY,
    Confidence,
    OptionReport,
    build_options,
    estimate_material,
    strength_confidence,
)
from .fem import canonical_face, opposite_face, solve_voxel_fem
from .loadcase import LoadCase, extract_load_case
from .mapping import Plan, plan_from_stress
from .mutate import MutationConfig, MutationStats, apply_plan_to_object, compute_alignment
from .voxelize import voxelize

log = logging.getLogger("ecoslice")

DEFAULT_RESOLUTION = 32
MAX_ELEMENTS = 150_000


@dataclass
class Analysis:
    object_key: str
    load_case: LoadCase
    grid: object
    fem: object
    plan: Plan
    wall_seconds: float
    savings: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    confidence: Confidence | None = None
    options: list[OptionReport] = field(default_factory=list)

    def stats(self, cfg: MutationConfig, mutation: MutationStats | None = None) -> dict:
        d = {
            "mode": "load-aware walls & infill",
            "load_case": self.load_case.description[:60] or "(unnamed)",
            "load_case_source": self.load_case.source,
            "safety_factor": self.load_case.safety_factor,
            "allowable_mpa": self.plan.allowable_mpa,
            "max_vm_mpa": self.plan.max_vm_mpa,
            "solver": f"{self.fem.solver} @ {self.fem.n_dof} dof",
            "voxels": f"{self.grid.shape[0]}x{self.grid.shape[1]}x{self.grid.shape[2]}",
            "reinforced_layers": self.plan.n_reinforced_layers,
            "relaxed_layers": self.plan.n_relaxed_layers,
            "analysis_seconds": round(self.wall_seconds, 2),
        }
        if self.confidence is not None:
            d.update(self.confidence.as_dict())
        if self.notes:
            d["notes"] = "; ".join(self.notes)
        if mutation is not None:
            d["perimeters_added"] = mutation.perimeters_added
            d["perimeters_removed"] = mutation.perimeters_removed
            d["surfaces_solidified"] = mutation.surfaces_solidified
            d["surfaces_desolidified"] = mutation.surfaces_desolidified
        d.update(self.savings)
        return d


class EcoSlicePipeline:
    def __init__(
        self,
        cfg: MutationConfig | None = None,
        description: str | None = None,
        load_case: LoadCase | None = None,
        resolution: int = DEFAULT_RESOLUTION,
        layer_height_mm: float | None = None,
        infill_density: float = DEFAULT_SPARSE_INFILL_DENSITY,
    ):
        self.cfg = cfg or MutationConfig()
        self.description = description or ""
        self._explicit_load_case = load_case
        self.resolution = resolution
        self.layer_height_mm = layer_height_mm
        self.infill_density = infill_density
        self.analyses: dict[str, Analysis] = {}
        self.capabilities: HostCapabilities | None = None
        self._last_mutation: MutationStats | None = None

    def load_case_for(self, description: str) -> LoadCase:
        if self._explicit_load_case is not None:
            return self._explicit_load_case
        return extract_load_case(description)

    def _voxelize_within_budget(self, vertices, triangles, notes: list[str]):
        resolution = self.resolution
        previous = None
        while True:
            grid = voxelize(vertices, triangles, resolution=resolution)
            elements = int(grid.mask.sum())
            if elements == 0:
                raise ValueError("voxelization produced no solid cells — is the mesh closed?")
            if elements <= MAX_ELEMENTS or resolution <= 12 or elements == previous:
                return grid
            previous = elements
            resolution = max(12, int(resolution * (MAX_ELEMENTS / elements) ** (1 / 3)))
            notes.append(f"resolution reduced to {resolution} to stay under {MAX_ELEMENTS} elements")

    def _solver_faces(self, lc: LoadCase, notes: list[str]) -> tuple[list[str], str, np.ndarray]:
        constraint_faces = [c.face for c in lc.constraints]
        constrained = {canonical_face(f) for f in constraint_faces}

        total_force = np.zeros(3)
        primary = lc.forces[0]
        for f in lc.forces:
            total_force += np.asarray(f.normalized()) * f.magnitude_n
            if f.magnitude_n > primary.magnitude_n:
                primary = f
        if np.linalg.norm(total_force) < 1e-9:
            total_force = np.asarray(primary.normalized()) * primary.magnitude_n
            notes.append("opposing forces cancelled; solved for the dominant force only")

        load_face = canonical_face(primary.face)
        if load_face in constrained:
            load_face = opposite_face(load_face)
            notes.append(
                f"load face {primary.face} is held by the fixture; "
                f"load applied on {load_face} instead"
            )
        return constraint_faces, load_face, total_force

    def analyze_mesh(
        self,
        vertices: np.ndarray,
        triangles: np.ndarray,
        description: str = "",
        object_id: str = "obj",
    ) -> Analysis:
        t0 = time.perf_counter()
        notes: list[str] = []
        lc = self.load_case_for(description)
        grid = self._voxelize_within_budget(vertices, triangles, notes)
        constraint_faces, load_face, total_force = self._solver_faces(lc, notes)

        fem = solve_voxel_fem(
            grid,
            fixed_faces=constraint_faces,
            load_face=load_face,
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
            object_key=object_id,
            load_case=lc,
            grid=grid,
            fem=fem,
            plan=plan,
            wall_seconds=time.perf_counter() - t0,
            notes=notes,
        )
        analysis.confidence = strength_confidence(plan, grid, fem.von_mises)
        analysis.savings = self._estimate_savings(plan, grid)
        analysis.options = build_options(
            grid,
            fem.von_mises,
            yield_mpa=lc.yield_mpa,
            safety_factor=lc.safety_factor,
            layer_height_mm=self.layer_height_mm,
            infill_density=self.infill_density,
        )
        self.analyses[object_id] = analysis
        return analysis

    def _estimate_savings(self, plan: Plan, grid) -> dict:
        return estimate_material(
            plan,
            grid,
            add_perimeters=self.cfg.add_perimeters,
            layer_height_mm=self.layer_height_mm or 0.2,
            enable_solid_infill=self.cfg.enable_solid_infill,
            infill_density=self.infill_density,
        )

    def run_offline(self, vertices, triangles, description: str, object_id: str = "obj") -> Analysis:
        return self.analyze_mesh(vertices, triangles, description, object_id)

    def analysis_for(self, obj) -> Analysis | None:
        key = object_key(obj)
        analysis = self.analyses.get(key)
        if analysis is not None:
            return analysis
        mesh = get_mesh(obj)
        if mesh is None:
            log.warning("no mesh access for %s", key)
            return None
        try:
            return self.analyze_mesh(mesh[0], mesh[1], self.description, key)
        except Exception as exc:
            log.warning("analysis failed for %s: %s", key, exc)
            return None

    def apply_mutations(self, ctx) -> MutationStats:
        total = MutationStats()
        for obj in iter_print_objects(ctx):
            analysis = self.analysis_for(obj)
            if analysis is None:
                continue
            try:
                alignment = compute_alignment(obj, analysis.grid)
                total.merge(
                    apply_plan_to_object(obj, analysis.plan, analysis.grid, self.cfg, alignment)
                )
            except Exception as exc:
                log.warning("mutation failed on %s: %s", analysis.object_key, exc)
        return total

    def on_pos_slice(self, ctx):
        try:
            if self.capabilities is None:
                self.capabilities = probe(ctx)
                log.info("host capabilities: %s", self.capabilities.as_dict())
            if self.layer_height_mm is None:
                self.layer_height_mm = _config_float(ctx, "layer_height")
            density = _config_float(ctx, "sparse_infill_density")
            if density is None:
                density = _config_float(ctx, "fill_density")
            if density is not None and 0.0 <= density <= 100.0:
                # OrcaSlicer reports this as a percentage; the material model wants a fraction.
                self.infill_density = density / 100.0 if density > 1.0 else density
            for obj in iter_print_objects(ctx):
                if object_key(obj) in self.analyses:
                    continue
                a = self.analysis_for(obj)
                if a is None:
                    continue
                log.info(
                    "analyzed %s in %.2fs: maxVM=%.1f MPa allow=%.1f MPa reinforced=%d relaxed=%d",
                    a.object_key,
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
            if self._last_mutation is None:
                self._last_mutation = stats
            else:
                self._last_mutation.merge(stats)
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
            stats = self._aggregate_stats()
            stats.update(self._measured_stats(gcode_text))
            block = receipt_block(stats)
            lines = [ln for ln in gcode_text.splitlines() if not ln.startswith(";ECOSLICE")]
            insert_at = 0
            for i, ln in enumerate(lines[:50]):
                if ln.startswith(";") and "generated" in ln.lower():
                    insert_at = i + 1
                    break
            return "\n".join(lines[:insert_at] + block.splitlines() + lines[insert_at:])
        except Exception as exc:
            log.exception("postprocess hook failed (non-fatal): %s", exc)
            return gcode_text

    def _measured_stats(self, gcode_text: str) -> dict:
        """Filament mass and print time as the slicer itself reported them.

        Read from the export footer of the very file being post-processed, so the
        receipt can put a measured number next to its own estimate instead of
        leaving the reader to trust the model alone.
        """
        try:
            footer = parse_gcode_footer(gcode_text)
        except Exception as exc:
            log.debug("footer parse failed: %s", exc)
            return {}
        out = {}
        if footer.get("filament_g") is not None:
            out["measured_filament_g"] = footer["filament_g"]
        if footer.get("print_time_s") is not None:
            out["measured_print_time_s"] = footer["print_time_s"]
        return out

    def _aggregate_stats(self) -> dict:
        if not self.analyses:
            return {"mode": "eco-slice idle (no analysis ran)"}
        keys = sorted(self.analyses)
        stats = self.analyses[keys[0]].stats(self.cfg, self._last_mutation)
        if len(keys) > 1:
            stats["objects_analyzed"] = len(keys)
        return stats


def _config_float(ctx, key: str) -> float | None:
    getter = getattr(ctx, "config_value", None)
    if not callable(getter):
        return None
    try:
        value = getter(key)
    except Exception:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def default_pipeline(description: str = "", **kw) -> EcoSlicePipeline:
    return EcoSlicePipeline(description=description, **kw)
