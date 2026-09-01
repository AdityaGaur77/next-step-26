"""One command that produces everything needed to demo EcoSlice.

Running four tools in the right order with the right flags is friction you do not
want at a podium. This writes the parts, the analyses and the proof pages into a
single folder and prints the terminal narrative as it goes.

    python tools/demo.py                 # writes ./demo/
    python tools/demo.py --out ~/pitch   # anywhere else
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from ecoslice.mapping import plan_summary_table  # noqa: E402
from ecoslice.options import options_table  # noqa: E402
from ecoslice.pipeline import EcoSlicePipeline  # noqa: E402
from ecoslice.receipt import receipt_block  # noqa: E402
from make_test_part import PARTS, write_binary_stl  # noqa: E402
from stress_report import render_report  # noqa: E402

# Resolution is a live-demo trade-off: high enough that the proof map reads well,
# low enough that nobody watches a progress bar. Both measured on this machine.
SCENES = [
    {
        "key": "cantilever",
        "title": "Cantilever bar",
        "resolution": 80,
        "description": (
            "shelf bracket holding 8 kg, load downward at the front edge; "
            "screwed onto left wall"
        ),
        "say": "The slicer knows this is 100x10x10 mm. It has no idea it is holding 8 kg.",
    },
    {
        "key": "l-bracket",
        "title": "L-bracket",
        "resolution": 48,
        "description": (
            "wall bracket holding 6 kg, load downward at the free end; "
            "screwed onto the left wall"
        ),
        "say": "A real bracket. Watch where the stress goes, and where the material follows.",
    },
]

RULE = "=" * 72


def _run_scene(scene: dict, out: Path) -> dict:
    part_path = out / f"{scene['key']}.stl"
    v, t = PARTS[scene["key"]][0]()
    write_binary_stl(part_path, v, t, f"EcoSlice demo: {scene['key']}")

    pipe = EcoSlicePipeline(
        description=scene["description"], resolution=scene["resolution"], layer_height_mm=0.2
    )
    t0 = time.perf_counter()
    analysis = pipe.analyze_mesh(v, t, scene["description"], scene["key"])
    elapsed = time.perf_counter() - t0

    receipt = receipt_block(analysis.stats(pipe.cfg))
    proof = out / f"{scene['key']}-proof.html"
    proof.write_text(render_report(analysis, scene["key"], receipt), encoding="utf-8")

    util = analysis.plan.max_vm_mpa / analysis.plan.allowable_mpa
    print(f"\n{RULE}\n  {scene['title']}\n{RULE}")
    print(f'  "{scene["say"]}"\n')
    print(f"  intent      {scene['description']}")
    lc = analysis.load_case
    print(f"  parsed as   {lc.forces[0].magnitude_n:.1f} N on {lc.forces[0].face}, "
          f"held at {[c.face for c in lc.constraints]}, safety factor {lc.safety_factor:g}")
    print(f"  solved      {int(analysis.grid.mask.sum())} voxels, {analysis.fem.n_dof} dof, "
          f"{analysis.fem.solver} in {elapsed:.2f} s")
    print(f"  result      peak {analysis.plan.max_vm_mpa:.1f} MPa = {util:.2f}x allowable")
    print(f"  confidence  {analysis.confidence.score:.2f} {analysis.confidence.label}")
    print(f"\n{plan_summary_table(analysis.plan, max_rows=8)}")
    print(f"\n{options_table(analysis.options)}")
    print(f"\n  part   {part_path}")
    print(f"  proof  {proof}")
    return {"analysis": analysis, "stl": part_path, "proof": proof}


CHECKLIST = """EcoSlice demo — run order
=========================

Everything in this folder was produced by:  python tools/demo.py

OFFLINE (safe — works with no slicer, no network)
  1. Show a proof page:            open {proof}
     Two elevations of the same part: the stress field, and what EcoSlice did
     about it. Hover any cell for coordinates, utilisation and decision.
     The point to make out loud: reinforcement lands on the top and bottom
     fibres near the clamped root and the neutral axis is relaxed. That is
     textbook bending — it falls out of the FEM, nothing arranges it.

  2. Show the terminal output above: intent in plain language -> parsed load
     case -> real FEM solve -> three options with a confidence score.

LIVE IN ORCASLICER (the wow, and the risky part)
  3. Import {stl}
  4. Process settings -> Others -> Slicing Pipeline Plugin -> EcoSlice
     (installed AND activated AND selected — the third is the one people miss)
  5. Slice. Preview shows reinforcement where the stress was.
  6. Prove it ran, on camera:
       python tools/verify_gcode.py <exported>.gcode

MEASURED (the strongest evidence you can show)
  7. Deselect EcoSlice, slice the same part again -> baseline.gcode
  8. python tools/gcode_diff.py baseline.gcode ecoslice.gcode

     Read it honestly: against a DEFAULT profile EcoSlice is heavier — that is
     the cost of the strength. The savings claim is against blanket-strengthening
     the whole part to the same safety factor. Say which baseline you are using.

IF THE SLICER MISBEHAVES ON THE DAY
  Steps 1 and 2 need nothing but Python and always work. Lead with them, and
  say plainly that live mutation is verified (docs/SPIKE.md, 2026-08-26) and
  this footage is the offline driver. Do not fake a number.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Produce every EcoSlice demo asset in one folder.")
    ap.add_argument("--out", default="demo", help="output folder (default: ./demo)")
    ap.add_argument("--only", choices=[s["key"] for s in SCENES], help="just one scene")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    scenes = [s for s in SCENES if not args.only or s["key"] == args.only]
    results = [_run_scene(s, out) for s in scenes]

    checklist = out / "RUN-ORDER.txt"
    checklist.write_text(
        CHECKLIST.format(proof=results[0]["proof"].name, stl=results[-1]["stl"].name),
        encoding="utf-8",
    )

    print(f"\n{RULE}\n  Ready.\n{RULE}")
    print(f"  Everything is in  {out.resolve()}")
    print(f"  Run order         {checklist}")
    print("  Open the proof pages in a browser; import the .stl into OrcaSlicer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
