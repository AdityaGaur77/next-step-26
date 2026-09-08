from __future__ import annotations

import re

import pytest

import build_site


@pytest.fixture(scope="module")
def built(tmp_path_factory, monkeypatch_module=None):
    """Build the site once, at a low resolution, into a temp directory."""
    out = tmp_path_factory.mktemp("site")
    orig = (build_site.OUT_DIR, build_site.INDEX, build_site.PROOF)
    build_site.OUT_DIR = out
    build_site.INDEX = out / "index.html"
    build_site.PROOF = out / "proof-example.html"
    try:
        info = build_site.build(resolution=24, test_count=99)
        yield out, info
    finally:
        build_site.OUT_DIR, build_site.INDEX, build_site.PROOF = orig


def test_both_pages_are_written(built):
    out, _ = built
    index = (out / "index.html").read_text(encoding="utf-8")
    proof = (out / "proof-example.html").read_text(encoding="utf-8")
    assert index.startswith("<!doctype html>")
    assert proof.startswith("<!doctype html>")
    assert "<title>" in index


def test_no_template_tokens_survive(built):
    """An unfilled __TOKEN__ on a public page is the most embarrassing possible bug."""
    out, _ = built
    leftovers = set(re.findall(r"__[A-Z_]+__", (out / "index.html").read_text(encoding="utf-8")))
    assert not leftovers, f"unfilled tokens: {sorted(leftovers)}"


def test_numbers_on_the_page_come_from_the_run(built):
    out, info = built
    index = (out / "index.html").read_text(encoding="utf-8")
    assert f"{info['peak_utilization']:.2f}" in index
    assert f"{info['added_grams']:.2f}" in index
    assert ">99<" in index, "injected test count should appear"


def test_index_links_to_the_proof_page_relatively(built):
    """GitHub Pages serves docs/ as the site root, so the link must be relative."""
    out, _ = built
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="proof-example.html"' in index
    assert "docs/proof-example.html" not in index
    assert (out / "proof-example.html").is_file()


def test_maps_are_real_svg_not_a_placeholder(built):
    out, _ = built
    index = (out / "index.html").read_text(encoding="utf-8")
    rects = re.findall(r'<rect [^>]*fill="var\(--(?:s\d+|rein|relax|idle)\)"', index)
    assert len(rects) > 200, f"expected a rendered field, got {len(rects)} cells"
    assert 'class="map"' in index


def test_page_carries_the_scope_caveats(built):
    """The honest framing is the point; a rebuild must not quietly drop it."""
    out, _ = built
    index = (out / "index.html").read_text(encoding="utf-8")
    for phrase in ("Not built", "Adaptive support generation", "heavier", "blanket-strengthening"):
        assert phrase in index, f"missing {phrase!r}"


def test_test_count_falls_back_to_none_without_pytest(monkeypatch):
    """The page drops the claim rather than printing a guessed number."""
    import builtins

    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "pytest":
            raise ImportError("no pytest")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert build_site.count_tests() is None
