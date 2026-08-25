from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ecoslice.mapping import plan_summary_table
from ecoslice.pipeline import EcoSlicePipeline
from ecoslice.receipt import receipt_block
from ecoslice.voxelize import box_mesh, uv_sphere_mesh


def cantilever_mesh():
    return box_mesh(100.0, 10.0, 10.0)


def bracket_with_hole():
    v, t = uv_sphere_mesh(radius=4.0, segments=16, rings=8, center=(50.0, 5.0, 5.0))
    vb, tb = box_mesh(100.0, 10.0, 10.0)
    import numpy as np

    return np.vstack([vb, v]), np.vstack([tb, tb.max() + 1 + t])


DESCRIPTIONS = {
    "cantilever": "shelf bracket holding 8 kg, load downward at the front edge; screwed onto left wall",
    "bracket": "mounting bracket for a 3 kg camera; pulls downward; bolted to right wall; safety factor 2.5",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EcoSlice offline demo: FEM-driven walls/infill on synthetic parts.")
    ap.add_argument("--part", choices=list(DESCRIPTIONS), default="cantilever")
    ap.add_argument("--resolution", type=int, default=48)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    desc = DESCRIPTIONS[args.part]
    pipe = EcoSlicePipeline(description=desc, resolution=args.resolution, layer_height_mm=0.2)
    v, t = cantilever_mesh()
    analysis = pipe.analyze_mesh(v, t, desc, "demo")

    if args.json:
        print(
            json.dumps(
                {
                    "load_case": json.loads(analysis.load_case.to_json()),
                    "max_vm_mpa": round(analysis.plan.max_vm_mpa, 2),
                    "allowable_mpa": round(analysis.plan.allowable_mpa, 2),
                    "tip_deflection_mm": round(abs(list(analysis.fem.face_displacement.values())[0]), 3),
                    "reinforced_layers": analysis.plan.n_reinforced_layers,
                    "relaxed_layers": analysis.plan.n_relaxed_layers,
                    "solver": analysis.fem.solver,
                    "wall_seconds": round(analysis.wall_seconds, 2),
                    "savings": analysis.savings,
                },
                indent=2,
            )
        )
        return 0

    print("EcoSlice offline demo")
    print("=" * 60)
    print(f"part          : {args.part}")
    print(f"description   : {desc}")
    lc = analysis.load_case
    print(f"load case     : {len(lc.forces)} force(s), constraints on "
          f"{[c.face for c in lc.constraints]}, sf={lc.safety_factor}, source={lc.source}")
    print(f"grid          : {analysis.grid.shape} @ {analysis.grid.h:.2f} mm "
          f"({int(analysis.grid.mask.sum())} voxels)")
    print(f"solver        : {analysis.fem.solver} in {analysis.wall_seconds:.2f}s")
    print(f"max von Mises : {analysis.plan.max_vm_mpa:.1f} MPa vs allowable "
          f"{analysis.plan.allowable_mpa:.1f} MPa (yield/sf)")
    disp = list(analysis.fem.face_displacement.values())[0]
    print(f"load-face defl: {abs(disp):.2f} mm")
    print()
    print(plan_summary_table(analysis.plan))
    print()
    print(receipt_block(analysis.stats(pipe.cfg)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
