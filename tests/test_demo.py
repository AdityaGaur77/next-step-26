from __future__ import annotations

import pytest

from demo import SCENES, main


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    assert main(["--out", str(out)]) == 0
    return out


def test_every_scene_produces_a_part_and_a_proof(demo_dir):
    for scene in SCENES:
        stl = demo_dir / f"{scene['key']}.stl"
        proof = demo_dir / f"{scene['key']}-proof.html"
        assert stl.is_file() and stl.stat().st_size > 0
        assert proof.is_file()
        assert proof.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_run_order_names_files_that_exist(demo_dir):
    """A checklist pointing at a file that was never written is worse than none."""
    text = (demo_dir / "RUN-ORDER.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        for token in line.split():
            if token.endswith((".stl", ".html")):
                assert (demo_dir / token).is_file(), f"RUN-ORDER names missing {token}"


def test_run_order_keeps_the_two_baselines_distinct(demo_dir):
    """The demo falls apart if the presenter conflates them, so the sheet must not."""
    text = (demo_dir / "RUN-ORDER.txt").read_text(encoding="utf-8")
    assert "heavier" in text and "blanket" in text


def test_only_flag_runs_one_scene(tmp_path):
    assert main(["--out", str(tmp_path), "--only", "cantilever"]) == 0
    assert (tmp_path / "cantilever.stl").is_file()
    assert not (tmp_path / "l-bracket.stl").exists()


def test_scene_descriptions_match_their_geometry():
    """A description that clamps and loads the same axis yields pure compression
    and a plan with nothing to do — the failure that made the cube useless."""
    for scene in SCENES:
        assert "kg" in scene["description"]
        assert any(w in scene["description"] for w in ("screwed", "bolted", "mounted"))
