"""Generate the GitHub Pages site in docs/ from a real analysis run.

The landing page's stress maps are not a mockup — they are rendered from an
actual FEM solve at build time, so the page cannot drift from what the tool
does. Regenerate with:

    python tools/build_site.py

Serve it by enabling GitHub Pages on the default branch, folder /docs.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from ecoslice.pipeline import EcoSlicePipeline  # noqa: E402
from ecoslice.receipt import receipt_block  # noqa: E402
from make_test_part import PARTS  # noqa: E402
from stress_report import SEQ_LIGHT, build_elevation, render_report  # noqa: E402

TEMPLATE = ROOT / "tools" / "site_template.html"
OUT_DIR = ROOT / "docs"
INDEX = OUT_DIR / "index.html"
PROOF = OUT_DIR / "proof-example.html"

# The bar fills its bounding box completely, so the strips read as a beam rather
# than as a shape floating in empty space — and it is the geometry the FEM is
# validated against.
PART = "cantilever"
DESCRIPTION = "shelf bracket holding 8 kg, load downward at the front edge; screwed onto left wall"
RESOLUTION = 100
CELL = 10.0


def _svg(elev, mode: str, vmax: float) -> str:
    nx, nz = elev.utilization.shape
    w, h = nx * CELL, nz * CELL
    out = [
        f'<svg viewBox="0 0 {w:.0f} {h:.0f}" class="map" preserveAspectRatio="none" '
        f'role="img" aria-label="{mode} map">'
    ]
    for i in range(nx):
        for k in range(nz):
            if not elev.occupied[i, k]:
                continue
            if mode == "stress":
                u = float(elev.utilization[i, k])
                idx = int(round(min(u / vmax, 1.0) * (len(SEQ_LIGHT) - 1))) if vmax > 0 else 0
                fill = f"var(--s{idx})"
            else:
                fill = {1: "var(--rein)", -1: "var(--relax)", 0: "var(--idle)"}[
                    int(elev.decision[i, k])
                ]
            out.append(
                f'<rect x="{i * CELL:.0f}" y="{(nz - 1 - k) * CELL:.0f}" '
                f'width="{CELL:.0f}" height="{CELL:.0f}" fill="{fill}"/>'
            )
    out.append("</svg>")
    return "".join(out)


def count_tests() -> int | None:
    """Exact test count, from pytest's own collector.

    Counting `def test_` by hand undercounts every parametrised case — it read 145
    against a real 147 — and a page that exists to be believed cannot carry a
    number that is quietly wrong. Returns None if pytest is unavailable, and the
    caller drops the claim rather than guessing.
    """
    try:
        import pytest
    except ImportError:
        return None

    collected: list[int] = []

    class _Counter:
        def pytest_collection_modifyitems(self, items):
            collected.append(len(items))

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        status = pytest.main(
            ["--collect-only", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")],
            plugins=[_Counter()],
        )
    if int(status) != 0 or not collected:
        return None
    return collected[0]


def build(
    resolution: int = RESOLUTION,
    test_count: int | None = -1,
    out_dir: Path | None = None,
) -> dict:
    # test_count=-1 means "go and count them"; tests pass a value instead, so the
    # suite never re-enters pytest to build a page. out_dir lets a smoke run build
    # somewhere scratch instead of overwriting the committed pages.
    v, t = PARTS[PART][0]()
    pipe = EcoSlicePipeline(description=DESCRIPTION, resolution=resolution, layer_height_mm=0.2)
    analysis = pipe.analyze_mesh(v, t, DESCRIPTION, PART)

    elev = build_elevation(analysis)
    vmax = float(elev.utilization.max())
    material = analysis.options[1].material  # Balanced

    out = out_dir or OUT_DIR
    index_path, proof_path = out / INDEX.name, out / PROOF.name
    out.mkdir(parents=True, exist_ok=True)
    proof_path.write_text(
        render_report(analysis, "shelf bracket", receipt_block(analysis.stats(pipe.cfg))),
        encoding="utf-8",
    )

    n_tests = count_tests() if test_count == -1 else test_count
    tests_label = str(n_tests) if n_tests else "—"

    html = TEMPLATE.read_text(encoding="utf-8")
    for token, value in (
        ("__STRESS__", _svg(elev, "stress", vmax)),
        ("__DECISION__", _svg(elev, "decision", vmax)),
        ("__PEAK__", f"{vmax:.2f}"),
        ("__TESTS__", tests_label),
        ("__ADDED__", f"{material['added_grams']:.2f}"),
        ("__WALLS__", f"{material['added_wall_grams']:.2f}"),
        ("__INFILL__", f"{material['added_infill_grams']:.2f}"),
        ("__SAVED__", f"{material['saved_vs_uniform_grams']:.1f}"),
    ):
        html = html.replace(token, value)
    if "__" in re.sub(r"[a-z]__[a-z]", "", html):
        leftovers = set(re.findall(r"__[A-Z_]+__", html))
        if leftovers:
            raise SystemExit(f"unfilled template tokens: {sorted(leftovers)}")
    index_path.write_text(html, encoding="utf-8")

    return {
        "peak_utilization": round(vmax, 3),
        "added_grams": material["added_grams"],
        "tests": n_tests,
        "index": index_path,
        "index_bytes": index_path.stat().st_size,
        "proof": proof_path,
        "proof_bytes": proof_path.stat().st_size,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the GitHub Pages site into docs/.")
    ap.add_argument("--resolution", type=int, default=RESOLUTION)
    ap.add_argument("--out", help="build somewhere other than docs/ (for smoke runs)")
    args = ap.parse_args(argv)
    info = build(args.resolution, out_dir=Path(args.out) if args.out else None)
    for key in ("index", "proof"):
        path = info[key]
        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"wrote {shown} ({info[key + '_bytes'] / 1024:.0f} KB)")
    print(f"peak utilisation {info['peak_utilization']}x, balanced adds {info['added_grams']} g")
    print(f"test count on the page: {info['tests'] if info['tests'] else 'omitted (pytest unavailable)'}")
    print("\nPublish: repo Settings -> Pages -> Deploy from a branch -> main / docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
