import pytest

from ecoslice.loadcase import LoadCase, parse_description, extract_load_case, _dominant_face


def test_mass_and_direction():
    lc = parse_description("shelf bracket holding 8 kg, load downward at front edge; screwed onto left wall")
    assert len(lc.forces) == 1
    f = lc.forces[0]
    assert f.magnitude_n == pytest.approx(8 * 9.80665, rel=1e-3)
    assert f.direction[2] < 0
    assert any(c.face == "x-" for c in lc.constraints)


def test_pounds_and_right_wall():
    lc = parse_description("arm holding 5 lbs pulling down; bolted to right wall")
    assert lc.forces[0].magnitude_n == pytest.approx(5 * 4.44822, rel=1e-3)
    assert any(c.face == "x+" for c in lc.constraints)


def test_default_bottom_constraint():
    lc = parse_description("vase with 2kg inside pressing downward")
    assert any(c.face == "z-" for c in lc.constraints)
    assert lc.forces[0].magnitude_n == pytest.approx(19.6133, rel=1e-3)


def test_safety_factor_extraction():
    lc = parse_description("hook for 10kg load downward; wall mounted on back; safety factor 3")
    assert lc.safety_factor == pytest.approx(3.0)


def test_defaults_when_sparse():
    lc = parse_description("some part")
    assert lc.forces[0].magnitude_n > 0
    assert lc.constraints


def test_json_roundtrip():
    lc = parse_description("bracket 6kg downward; mounted left wall")
    j = lc.to_json()
    lc2 = LoadCase.from_json(j)
    assert lc2.to_json() == j


def test_llm_fallback_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lc = extract_load_case("bracket 4kg down; screwed to left wall")
    assert lc.source == "heuristic"


def test_validation_rejects_bad():
    with pytest.raises(ValueError):
        LoadCase.from_dict({"forces": [], "constraints": []})
    with pytest.raises(ValueError):
        LoadCase.from_dict({"description": "x", "forces": [{"direction": [0, 0, -1], "magnitude_n": -5}], "constraints": [{"face": "z-"}]})


def test_dominant_face():
    assert _dominant_face([0, -1, 0]) == "y-"
    assert _dominant_face([1, 0, 0]) == "x+"
