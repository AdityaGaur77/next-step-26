from __future__ import annotations

import re

import pytest

from ecoslice.pipeline import EcoSlicePipeline
from ecoslice.voxelize import box_mesh

from stress_report import build_elevation, render_report, report_summary

DESC = "shelf bracket holding 8 kg, load downward; screwed onto left wall"


@pytest.fixture(scope="module")
def analysis():
    pipe = EcoSlicePipeline(description=DESC, resolution=40, layer_height_mm=0.2)
    v, t = box_mesh(100.0, 10.0, 10.0)
    return pipe.analyze_mesh(v, t, DESC, "obj")


def test_elevation_matches_the_grid_footprint(analysis):
    elev = build_elevation(analysis)
    nx, _, nz = analysis.grid.shape
    assert elev.utilization.shape == (nx, nz)
    assert elev.decision.shape == (nx, nz)
    assert elev.occupied.shape == (nx, nz)
    assert elev.cell_mm == pytest.approx(analysis.grid.h)


def test_elevation_keeps_the_worst_stress_through_the_depth(analysis):
    """Projection must not average a hotspot away against cold material behind it."""
    elev = build_elevation(analysis)
    allowable = analysis.plan.allowable_mpa
    expected_peak = analysis.fem.von_mises.max() / allowable
    assert elev.utilization.max() == pytest.approx(expected_peak, rel=1e-6)


def test_reinforce_wins_over_relax_in_a_shared_column(analysis):
    """A cell must never be drawn 'relaxed' when the plan will add material to it."""
    elev = build_elevation(analysis)
    for action in analysis.plan.actions:
        if not (action.reinforce_xy.any() and action.relax_xy.any()):
            continue
        h = analysis.grid.h
        k0 = int(round((action.z0_mm - float(analysis.grid.origin[2])) / h))
        k1 = int(round((action.z1_mm - float(analysis.grid.origin[2])) / h))
        rein = action.reinforce_xy.any(axis=1)
        assert not (elev.decision[rein, k0:k1] == -1).any()


def test_empty_columns_are_not_drawn():
    """A part that does not fill its bounding box must leave those cells blank."""
    pipe = EcoSlicePipeline(description=DESC, resolution=24, layer_height_mm=0.2)
    v, t = box_mesh(80.0, 20.0, 8.0)
    a = pipe.analyze_mesh(v, t, DESC, "obj")
    elev = build_elevation(a)
    assert elev.occupied.all(), "a solid box fills its box"
    # Blank a slab and confirm the projection follows the mask, not the array shape.
    mask = a.grid.mask.copy()
    mask[:5] = False
    object.__setattr__(a.grid, "mask", mask)
    assert not build_elevation(a).occupied[:5].any()


def test_report_is_self_contained(analysis):
    html_text = render_report(analysis, "cantilever", "; receipt line\n")
    assert html_text.startswith("<!doctype html>")
    assert "<title>" in html_text
    # No network dependencies: the report has to open from a USB stick mid-demo.
    for pattern in ("http://", "https://", "src=\"//"):
        assert pattern not in html_text, f"external reference {pattern!r} in report"


def test_report_carries_the_numbers_a_reader_needs(analysis):
    html_text = render_report(analysis, "cantilever", "; ECOSLICE receipt\n")
    for name in ("Eco", "Balanced", "Maximum Strength"):
        assert name in html_text
    assert "ECOSLICE receipt" in html_text
    assert f"{analysis.plan.max_vm_mpa:.1f} MPa" in html_text
    assert "not a certification" in html_text


def test_report_declares_both_colour_schemes(analysis):
    html_text = render_report(analysis, "cantilever")
    assert "prefers-color-scheme: dark" in html_text
    assert "--seq-0" in html_text and "--seq-12" in html_text
    assert "--reinforce" in html_text and "--relax" in html_text


def test_report_escapes_the_description(analysis):
    """The intent string is user input and lands in the page — it must not inject markup."""
    hostile = '<img src=x onerror="alert(1)">'
    analysis.load_case.description = hostile
    try:
        html_text = render_report(analysis, "cantilever")
        assert hostile not in html_text
        assert "&lt;img src=x" in html_text
        assert "onerror=\"alert(1)\"" not in html_text
    finally:
        analysis.load_case.description = DESC


def test_report_escapes_the_part_name(analysis):
    html_text = render_report(analysis, '<script>bad()</script>')
    assert "<script>bad()</script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_summary_counts_agree_with_the_drawing(analysis):
    elev = build_elevation(analysis)
    s = report_summary(analysis)
    assert s["cells_reinforced"] == int((elev.decision == 1).sum())
    assert s["cells_relaxed"] == int((elev.decision == -1).sum())
    assert s["cells_total"] == int(elev.occupied.sum())
    assert s["options"] == ["eco", "balanced", "max_strength"]


def test_every_drawn_cell_has_a_hover_tooltip(analysis):
    html_text = render_report(analysis, "cantilever")
    rects = re.findall(r"<rect [^>]*>", html_text)
    assert rects, "the maps must draw cells"
    assert all("data-tip=" in r for r in rects)


def _data_rows(table: str) -> int:
    """Band rows only — the elision notice is also a <tr><td>."""
    return len(re.findall(r"<tr><td>[\d.]+ – [\d.]+</td>", table))


# The cantilever fixture yields 4 bands; caps below that force the elision path,
# including the slice arithmetic where a zero-length tail would show everything.
@pytest.mark.parametrize("max_rows", [2, 3])
def test_band_table_elides_rather_than_burying_the_report(analysis, max_rows):
    """A tall part slices into dozens of idle bands. Small caps also exercise the
    slice arithmetic, where a zero-length tail would silently show every row."""
    from stress_report import _band_table

    n = len(analysis.plan.actions)
    assert n > max_rows, "fixture must have more bands than the cap for this to mean anything"
    table = _band_table(analysis, max_rows=max_rows)
    rendered = _data_rows(table)
    assert rendered <= max_rows, f"cap {max_rows} but rendered {rendered}"
    assert rendered < n, "nothing was actually elided"
    assert "further bands" in table and "mm …" in table


def test_band_table_shows_every_row_when_it_fits(analysis):
    from stress_report import _band_table

    n = len(analysis.plan.actions)
    table = _band_table(analysis, max_rows=n + 5)
    assert table.count("<tr><td>") == n
    assert "further bands" not in table
