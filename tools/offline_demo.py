from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ecoslice.mapping import plan_summary_table
from ecoslice.options import options_table
from stress_report import render_report
from ecoslice.pipeline import EcoSlicePipeline
from ecoslice.receipt import receipt_block
from ecoslice.voxelize import box_mesh, uv_sphere_mesh


def cantilever_mesh():
    return box_mesh(100.0, 10.0, 10.0)


def bracket_with_hole():
    """Box with a spherical cavity: nested closed shells read as a hole under parity fill."""
    import numpy as np

    vb, tb = box_mesh(120.0, 20.0, 8.0)
    vs, ts = uv_sphere_mesh(radius=3.0, segments=20, rings=10, center=(85.0, 10.0, 4.0))
    return np.vstack([vb, vs]), np.vstack([tb, ts + len(vb)])


PART_BUILDERS = {
    "cantilever": cantilever_mesh,
    "bracket": bracket_with_hole,
}


DESCRIPTIONS = {
    "cantilever": "shelf bracket holding 8 kg, load downward at the front edge; screwed onto left wall",
    "bracket": "monitor-arm bracket carrying 12 kg downward at the free end; bolted to right wall; safety factor 2.5",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EcoSlice offline demo: FEM-driven walls/infill on synthetic parts.")
    ap.add_argument("--part", choices=list(PART_BUILDERS), default="cantilever")
    ap.add_argument("--resolution", type=int, default=48)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--options", action="store_true", help="show the Eco / Balanced / Maximum Strength table")
    ap.add_argument("--html", metavar="PATH", help="write a visual stress/decision report to PATH")
    args = ap.parse_args(argv)

    desc = DESCRIPTIONS[args.part]
    pipe = EcoSlicePipeline(description=desc, resolution=args.resolution, layer_height_mm=0.2)
    v, t = PART_BUILDERS[args.part]()
    analysis = pipe.analyze_mesh(v, t, desc, "demo")

    if args.json and args.html:
        Path(args.html).write_text(
            render_report(analysis, args.part, receipt_block(analysis.stats(pipe.cfg))),
            encoding="utf-8",
        )

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
                    "confidence": analysis.confidence.as_dict() if analysis.confidence else None,
                    "options": [o.as_dict() for o in analysis.options],
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
    if analysis.confidence is not None:
        c = analysis.confidence
        print(f"confidence    : {c.score:.2f} ({c.label}) - {'; '.join(c.reasons)}")
    print()
    print(plan_summary_table(analysis.plan))
    if args.options:
        print()
        print("Options (one FEM solve, three thresholdings):")
        print(options_table(analysis.options))
    print()
    receipt = receipt_block(analysis.stats(pipe.cfg))
    print(receipt)
    if args.html:
        out = Path(args.html)
        out.write_text(render_report(analysis, args.part, receipt), encoding="utf-8")
        print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
