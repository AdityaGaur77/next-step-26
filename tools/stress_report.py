"""Standalone visual proof for an EcoSlice analysis.

Renders the stress field and the decisions taken from it as two aligned
elevations, so a reader can see *why* material was added where it was — the
explainability step the pitch calls an "interactive proof". Self-contained HTML:
no network, no CDN, opens straight from disk.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass

import numpy as np

# Sequential blue ramp (light -> dark), used for magnitude: stress utilization.
SEQ_LIGHT = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]
# Same ramp for the dark surface; the low end must not vanish into the ground.
SEQ_DARK = SEQ_LIGHT

# Decision categories are identity, not magnitude: categorical slots 2 and 3.
# Validated all-pairs in both modes (CVD dE 9.2 light / 9.4 dark, normal 27.6 / 26.5).
DECISION_COLORS = {
    "reinforce": ("#eb6834", "#d95926"),
    "relax": ("#1baf7a", "#199e70"),
}

MAX_FIGURE_PX = 460
MIN_CELL_PX = 3
MAX_CELL_PX = 18


@dataclass
class Elevation:
    """A part projected onto the x-z plane, ready to draw."""

    utilization: np.ndarray  # (nx, nz) worst-case through the depth
    decision: np.ndarray  # (nx, nz) of -1 relax / 0 neutral / +1 reinforce
    occupied: np.ndarray  # (nx, nz) bool
    cell_mm: float
    origin_mm: tuple[float, float]


def build_elevation(analysis) -> Elevation:
    """Collapse the voxel field along y, keeping the worst stress per (x, z).

    Depth is the least interesting axis for a bracket seen in a slicer preview,
    and taking the maximum rather than the mean keeps a hotspot visible instead
    of averaging it away against the cold material behind it.
    """
    grid = analysis.grid
    vm = analysis.fem.von_mises
    allowable = analysis.plan.allowable_mpa or 1e-9
    mask = grid.mask

    util = np.zeros_like(vm)
    occ = mask & (vm > 0)
    util[occ] = vm[occ] / allowable

    util_xz = util.max(axis=1)
    occ_xz = mask.any(axis=1)

    decision = np.zeros(util_xz.shape, dtype=np.int8)
    h = grid.h
    origin_z = float(grid.origin[2])
    for action in analysis.plan.actions:
        k0 = max(0, int(round((action.z0_mm - origin_z) / h)))
        k1 = min(decision.shape[1], int(round((action.z1_mm - origin_z) / h)))
        if k1 <= k0:
            continue
        if action.reinforce_xy.any():
            cols = action.reinforce_xy.any(axis=1)
            decision[cols, k0:k1] = 1
        if action.relax_xy.any():
            cols = action.relax_xy.any(axis=1)
            # A column already marked for reinforcement wins: never show a cell
            # as relaxed when the plan will actually add material to it.
            take = cols & ~(decision[:, k0:k1] == 1).any(axis=1)
            decision[take, k0:k1] = -1

    return Elevation(
        utilization=util_xz,
        decision=decision,
        occupied=occ_xz,
        cell_mm=h,
        origin_mm=(float(grid.origin[0]), origin_z),
    )


def _cell_px(nx: int, nz: int) -> float:
    span = max(nx, nz)
    return max(MIN_CELL_PX, min(MAX_CELL_PX, MAX_FIGURE_PX / max(span, 1)))


def _seq_index(value: float, vmax: float) -> int:
    if vmax <= 0:
        return 0
    frac = min(max(value / vmax, 0.0), 1.0)
    return int(round(frac * (len(SEQ_LIGHT) - 1)))


def _svg_heatmap(elev: Elevation, mode: str, vmax: float) -> str:
    """One elevation as an SVG grid. `mode` is 'stress' or 'decision'."""
    nx, nz = elev.utilization.shape
    cell = _cell_px(nx, nz)
    width = nx * cell
    height = nz * cell
    x0, z0 = elev.origin_mm

    parts = [
        f'<svg class="map" viewBox="0 0 {width:.1f} {height:.1f}" '
        f'width="{width:.1f}" height="{height:.1f}" role="img" '
        f'aria-label="{"stress field" if mode == "stress" else "reinforcement plan"} elevation">'
    ]
    for i in range(nx):
        for k in range(nz):
            if not elev.occupied[i, k]:
                continue
            u = float(elev.utilization[i, k])
            d = int(elev.decision[i, k])
            # z grows upward in the part, downward in SVG.
            px = i * cell
            py = (nz - 1 - k) * cell
            if mode == "stress":
                fill = f"var(--seq-{_seq_index(u, vmax)})"
            elif d > 0:
                fill = "var(--reinforce)"
            elif d < 0:
                fill = "var(--relax)"
            else:
                fill = "var(--neutral)"
            label = {1: "reinforce", 0: "leave as-is", -1: "relax"}[d]
            tip = (
                f"x {x0 + i * elev.cell_mm:.1f} mm, z {z0 + k * elev.cell_mm:.1f} mm"
                f" · utilisation {u:.2f}x · {label}"
            )
            parts.append(
                f'<rect x="{px:.1f}" y="{py:.1f}" width="{cell:.2f}" height="{cell:.2f}" '
                f'fill="{fill}" data-tip="{html.escape(tip, quote=True)}">'
                f"<title>{html.escape(tip)}</title></rect>"
            )
    parts.append("</svg>")
    return "".join(parts)


def _ramp_legend(vmax: float) -> str:
    swatches = "".join(
        f'<span class="ramp-step" style="background:var(--seq-{i})"></span>'
        for i in range(len(SEQ_LIGHT))
    )
    return (
        '<div class="legend">'
        '<span class="legend-label">utilisation</span>'
        '<span class="ramp-end">0</span>'
        f'<span class="ramp">{swatches}</span>'
        f'<span class="ramp-end">{vmax:.2f}x allowable</span>'
        "</div>"
    )


def _decision_legend(present: set[int] | None = None) -> str:
    present = {-1, 0, 1} if present is None else present
    items = [
        (cls, name, note)
        for value, cls, name, note in (
            (1, "reinforce", "reinforce", "extra walls + solid infill"),
            (0, "neutral", "leave as-is", "profile settings apply"),
            (-1, "relax", "relax", "material recovered"),
        )
        if value in present
    ]
    keys = "".join(
        f'<span class="key"><span class="swatch" style="background:var(--{cls})"></span>'
        f"<b>{html.escape(name)}</b> <span class=\"muted\">{html.escape(note)}</span></span>"
        for cls, name, note in items
    )
    return f'<div class="legend">{keys}</div>'


def _stat_tiles(analysis) -> str:
    plan = analysis.plan
    util_max = plan.max_vm_mpa / plan.allowable_mpa if plan.allowable_mpa else 0.0
    conf = analysis.confidence
    tiles = [
        ("peak von Mises", f"{plan.max_vm_mpa:.1f} MPa", f"allowable {plan.allowable_mpa:.1f} MPa"),
        ("peak utilisation", f"{util_max:.2f}x", "over 1.0 means the part is under-sized"),
        (
            "strength confidence",
            f"{conf.score:.2f}" if conf else "n/a",
            (conf.label + " — heuristic, not a certification") if conf else "",
        ),
        (
            "reinforced / relaxed",
            f"{plan.n_reinforced_layers} / {plan.n_relaxed_layers}",
            "layer bands",
        ),
    ]
    cells = "".join(
        f'<div class="tile"><div class="tile-label">{html.escape(label)}</div>'
        f'<div class="tile-value">{html.escape(value)}</div>'
        f'<div class="tile-note">{html.escape(note)}</div></div>'
        for label, value, note in tiles
    )
    return f'<div class="tiles">{cells}</div>'


def _options_table(analysis) -> str:
    if not analysis.options:
        return ""
    head = (
        "<tr><th>option</th><th>added</th><th>walls</th><th>solid infill</th>"
        "<th>saved vs blanket</th><th>added time</th><th>added energy</th><th>confidence</th></tr>"
    )
    rows = []
    for r in analysis.options:
        m = r.material
        rows.append(
            f"<tr><td><b>{html.escape(r.preset.name)}</b><br>"
            f'<span class="muted">{html.escape(r.preset.blurb)}</span></td>'
            f"<td>{m['added_grams']:.2f} g</td><td>{m['added_wall_grams']:.2f} g</td>"
            f"<td>{m['added_infill_grams']:.2f} g</td>"
            f"<td>{m['saved_vs_uniform_grams']:.2f} g</td>"
            f"<td>{m['added_print_time_s'] / 60.0:.1f} min</td>"
            f"<td>{m['added_energy_kwh']:.4f} kWh</td>"
            f"<td>{r.confidence.score:.2f} {html.escape(r.confidence.label)}</td></tr>"
        )
    return f'<table class="data"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def _band_table(analysis) -> str:
    """The table view the contrast relief rule requires — and the honest detail."""
    head = (
        "<tr><th>z range (mm)</th><th>mean util</th><th>p95 util</th>"
        "<th>reinforced columns</th><th>relaxed columns</th></tr>"
    )
    rows = "".join(
        f"<tr><td>{a.z0_mm:.2f} – {a.z1_mm:.2f}</td><td>{a.mean_utilization:.3f}</td>"
        f"<td>{a.p95_utilization:.3f}</td><td>{int(a.reinforce_xy.sum())}</td>"
        f"<td>{int(a.relax_xy.sum())}</td></tr>"
        for a in analysis.plan.actions
    )
    return f'<table class="data"><thead>{head}</thead><tbody>{rows}</tbody></table>'


def _load_case_summary(lc) -> str:
    forces = ", ".join(
        f"{f.magnitude_n:.1f} N on {html.escape(f.face)}" for f in lc.forces
    )
    held = ", ".join(html.escape(c.face) for c in lc.constraints)
    return (
        f'<dl class="facts">'
        f"<dt>intent</dt><dd>{html.escape(lc.description or '(none given)')}</dd>"
        f"<dt>forces</dt><dd>{forces}</dd>"
        f"<dt>held at</dt><dd>{held}</dd>"
        f"<dt>safety factor</dt><dd>{lc.safety_factor:g}</dd>"
        f"<dt>material</dt><dd>E {lc.young_modulus_mpa:g} MPa · ν {lc.poisson:g} · "
        f"yield {lc.yield_mpa:g} MPa</dd>"
        f"<dt>parsed by</dt><dd>{html.escape(lc.source)}</dd>"
        f"</dl>"
    )


_CSS = """
:root {
  color-scheme: light;
  --surface: #fcfcfb; --plane: #f9f9f7;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --rule: #e1e0d9; --ring: rgba(11,11,11,0.10);
  --reinforce: %(rein_l)s; --relax: %(relax_l)s; --neutral: #d8d7d0;
%(seq_l)s
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --surface: #1a1a19; --plane: #0d0d0d;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --rule: #2c2c2a; --ring: rgba(255,255,255,0.10);
    --reinforce: %(rein_d)s; --relax: %(relax_d)s; --neutral: #3a3a37;
%(seq_d)s
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--plane); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 32px 0 10px; font-weight: 600; }
.sub { color: var(--ink-2); margin: 0 0 24px; }
.muted { color: var(--muted); }
.card { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px;
  padding: 18px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.tile { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 14px; }
.tile-label { color: var(--muted); font-size: 12px; }
.tile-value { font-size: 24px; margin: 2px 0; }
.tile-note { color: var(--ink-2); font-size: 12px; }
.figures { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 16px; }
figure { margin: 0; }
figcaption { color: var(--ink-2); font-size: 13px; margin-bottom: 10px; }
figcaption b { color: var(--ink); }
.map { display: block; max-width: 100%%; height: auto; shape-rendering: crispEdges; }
.map rect:hover { stroke: var(--ink); stroke-width: 1; }
.axis { color: var(--muted); font-size: 12px; display: flex; justify-content: space-between;
  margin-top: 6px; }
.legend { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-top: 12px;
  font-size: 12px; color: var(--ink-2); }
.legend-label { color: var(--muted); }
.ramp { display: flex; border-radius: 3px; overflow: hidden; border: 1px solid var(--ring); }
.ramp-step { width: 14px; height: 12px; }
.ramp-end { color: var(--muted); }
.key { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 12px; height: 12px; border-radius: 3px; border: 1px solid var(--ring); }
dl.facts { display: grid; grid-template-columns: 130px 1fr; gap: 6px 14px; margin: 0; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; }
table.data { width: 100%%; border-collapse: collapse; font-size: 13px;
  font-variant-numeric: tabular-nums; }
table.data th { text-align: left; color: var(--muted); font-weight: 500;
  border-bottom: 1px solid var(--rule); padding: 7px 8px; }
table.data td { border-bottom: 1px solid var(--rule); padding: 7px 8px; vertical-align: top; }
table.data td + td { white-space: nowrap; }
.scroll { overflow-x: auto; }
pre.receipt { background: var(--surface); border: 1px solid var(--ring); border-radius: 10px;
  padding: 14px; overflow-x: auto; font-size: 12px; line-height: 1.45; }
#tip { position: fixed; pointer-events: none; opacity: 0; transition: opacity .08s;
  background: var(--ink); color: var(--surface); padding: 5px 9px; border-radius: 6px;
  font-size: 12px; white-space: nowrap; z-index: 10; }
footer { color: var(--muted); font-size: 12px; margin-top: 40px; }
"""

_JS = """
(function () {
  var tip = document.getElementById('tip');
  document.addEventListener('mouseover', function (e) {
    var t = e.target.getAttribute && e.target.getAttribute('data-tip');
    if (!t) { tip.style.opacity = 0; return; }
    tip.textContent = t;
    tip.style.opacity = 1;
  });
  document.addEventListener('mousemove', function (e) {
    if (tip.style.opacity === '0' || tip.style.opacity === '') return;
    var pad = 14;
    var x = Math.min(e.clientX + pad, window.innerWidth - tip.offsetWidth - 6);
    tip.style.left = x + 'px';
    tip.style.top = (e.clientY + pad) + 'px';
  });
})();
"""


def _css() -> str:
    seq_l = "\n".join(f"  --seq-{i}: {c};" for i, c in enumerate(SEQ_LIGHT))
    seq_d = "\n".join(f"    --seq-{i}: {c};" for i, c in enumerate(SEQ_DARK))
    return _CSS % {
        "rein_l": DECISION_COLORS["reinforce"][0],
        "relax_l": DECISION_COLORS["relax"][0],
        "rein_d": DECISION_COLORS["reinforce"][1],
        "relax_d": DECISION_COLORS["relax"][1],
        "seq_l": seq_l,
        "seq_d": seq_d,
    }


def render_report(analysis, part_name: str, receipt_text: str = "") -> str:
    elev = build_elevation(analysis)
    vmax = float(elev.utilization.max()) if elev.utilization.size else 0.0
    nx, nz = elev.utilization.shape
    width_mm = nx * elev.cell_mm
    height_mm = nz * elev.cell_mm

    reinforced = int((elev.decision == 1).sum())
    relaxed = int((elev.decision == -1).sum())

    body = f"""<main>
<h1>EcoSlice — why this part was reinforced here</h1>
<p class="sub">{html.escape(part_name)} · elevation looking along the depth axis,
worst stress through the depth shown at each point.</p>

{_stat_tiles(analysis)}

<h2>The proof</h2>
<div class="figures">
  <figure>
    <figcaption><b>Stress field.</b> von Mises as a fraction of the allowable
    stress (yield ÷ safety factor).</figcaption>
    {_svg_heatmap(elev, "stress", vmax)}
    <div class="axis"><span>x 0 mm</span><span>x {width_mm:.0f} mm</span></div>
    {_ramp_legend(vmax)}
  </figure>
  <figure>
    <figcaption><b>What EcoSlice did about it.</b> {reinforced} cells reinforced,
    {relaxed} relaxed — the same geometry, so the two read against each other.</figcaption>
    {_svg_heatmap(elev, "decision", vmax)}
    <div class="axis"><span>x 0 mm</span><span>x {width_mm:.0f} mm</span></div>
    {_decision_legend(set(int(v) for v in np.unique(elev.decision)))}
  </figure>
</div>
<p class="muted">Vertical axis is z, 0 – {height_mm:.0f} mm, build plate at the bottom.
Hover any cell for its coordinates, utilisation and decision.</p>

<h2>Load case</h2>
<div class="card">{_load_case_summary(analysis.load_case)}</div>

<h2>Options</h2>
<div class="card scroll">{_options_table(analysis)}</div>

<h2>Per-band detail</h2>
<div class="card scroll">{_band_table(analysis)}</div>
"""
    if receipt_text:
        body += f"\n<h2>Receipt</h2>\n<pre class=\"receipt\">{html.escape(receipt_text)}</pre>\n"
    body += (
        '<footer>Stress is a voxel FEM result (trilinear hexahedra), not a certification. '
        "Material figures are modelled; the receipt's measured lines, when present, come from "
        "the slicer's own export footer.</footer>\n</main>\n<div id=\"tip\"></div>"
    )

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>EcoSlice — {html.escape(part_name)}</title>\n"
        f"<style>{_css()}</style>\n</head>\n<body>\n{body}\n"
        f"<script>{_JS}</script>\n</body>\n</html>\n"
    )


def report_summary(analysis) -> dict:
    """Machine-readable twin of the report, for tests and CI smoke."""
    elev = build_elevation(analysis)
    return {
        "cells_total": int(elev.occupied.sum()),
        "cells_reinforced": int((elev.decision == 1).sum()),
        "cells_relaxed": int((elev.decision == -1).sum()),
        "peak_utilization": round(float(elev.utilization.max()), 4),
        "options": [o.preset.key for o in analysis.options],
    }


def dumps_summary(analysis) -> str:
    return json.dumps(report_summary(analysis), indent=2)
