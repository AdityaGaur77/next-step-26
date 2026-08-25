from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Force:
    direction: tuple[float, float, float]
    magnitude_n: float
    face: str

    def normalized(self) -> np.ndarray:
        return np_normalize(self.direction)


@dataclass
class Constraint:
    face: str


@dataclass
class LoadCase:
    description: str = ""
    forces: list[Force] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    safety_factor: float = 2.0
    young_modulus_mpa: float = 2400.0
    poisson: float = 0.36
    yield_mpa: float = 45.0
    source: str = "heuristic"

    def to_json(self) -> str:
        return json.dumps(
            {
                "description": self.description,
                "forces": [
                    {"direction": list(f.direction), "magnitude_n": f.magnitude_n, "face": f.face}
                    for f in self.forces
                ],
                "constraints": [{"face": c.face} for c in self.constraints],
                "safety_factor": self.safety_factor,
                "young_modulus_mpa": self.young_modulus_mpa,
                "poisson": self.poisson,
                "yield_mpa": self.yield_mpa,
                "source": self.source,
            },
            indent=2,
        )

    @staticmethod
    def from_json(text: str) -> "LoadCase":
        raw = json.loads(_strip_code_fence(text))
        return LoadCase.from_dict(raw)

    @staticmethod
    def from_dict(raw: dict) -> "LoadCase":
        lc = LoadCase(description=str(raw.get("description", "")))
        for f in raw.get("forces", []):
            d = f.get("direction") or f.get("dir")
            if not d or len(d) != 3:
                raise ValueError("force needs direction of 3 components")
            mag = float(f.get("magnitude_n", f.get("magnitude_N", 0)))
            face = f.get("face", _dominant_face(d))
            lc.forces.append(Force(direction=(float(d[0]), float(d[1]), float(d[2])), magnitude_n=mag, face=str(face)))
        for c in raw.get("constraints", []):
            face = c.get("face") if isinstance(c, dict) else str(c)
            lc.constraints.append(Constraint(face=str(face)))
        lc.safety_factor = float(raw.get("safety_factor", 2.0))
        lc.young_modulus_mpa = float(raw.get("young_modulus_mpa", 2400.0))
        lc.poisson = float(raw.get("poisson", 0.36))
        lc.yield_mpa = float(raw.get("yield_mpa", 45.0))
        lc.source = str(raw.get("source", "llm"))
        lc.validate()
        return lc

    def validate(self) -> None:
        if not self.constraints:
            raise ValueError("load case needs at least one constraint (how is the part held?)")
        if not self.forces:
            raise ValueError("load case needs at least one force")
        for f in self.forces:
            if f.magnitude_n <= 0:
                raise ValueError("force magnitude must be > 0")
        if not (1.0 <= self.safety_factor <= 10.0):
            raise ValueError("safety_factor out of range [1,10]")


def np_normalize(v):
    a = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(a)
    if n < 1e-12:
        return np.array([0.0, 0.0, -1.0])
    return a / n


def _dominant_face(direction) -> str:
    d = np_normalize(direction)
    axis = int(np.argmax(np.abs(d)))
    return ["x", "y", "z"][axis] + ("+" if d[axis] >= 0 else "-")


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1:
        t = t[i : j + 1]
    return t


_MASS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|kgs|kilogram|kilograms|g|gram|grams|lb|lbs|pound|pounds|n|newton|newtons)\b", re.IGNORECASE)
_SF_RE = re.compile(r"safety\s*factor\s*(?:of|:)?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_DIR_WORDS = {
    "down": (0.0, 0.0, -1.0), "downward": (0.0, 0.0, -1.0), "downwards": (0.0, 0.0, -1.0),
    "up": (0.0, 0.0, 1.0), "upward": (0.0, 0.0, 1.0),
    "left": (-1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0),
    "front": (0.0, -1.0, 0.0), "forward": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0), "backward": (0.0, 1.0, 0.0),
}
_ATTACH_RE = re.compile(r"(screw|bolt|mount|attach|clamp|fix|anchor|fasten)\w*", re.IGNORECASE)
_FACE_PATTERNS = [
    (re.compile(r"\bleft\b", re.I), "x-"),
    (re.compile(r"\bright\b", re.I), "x+"),
    (re.compile(r"\bback\b", re.I), "y+"),
    (re.compile(r"\brear\b", re.I), "y+"),
    (re.compile(r"\bfront\b", re.I), "y-"),
    (re.compile(r"\bbottom\b|\bbase\b|\bfloor\b|\bdesk\b", re.I), "z-"),
    (re.compile(r"\btop\b|\bceiling\b", re.I), "z+"),
]


def _to_newtons(value: float, unit: str) -> float:
    u = unit.lower()
    if u.startswith(("kg", "kilo")):
        return value * 9.80665
    if u == "g" or u.startswith("gram"):
        return value * 9.80665e-3
    if u.startswith("lb") or u.startswith("pound"):
        return value * 4.44822
    return value


def parse_description(text: str) -> LoadCase:
    desc = text.strip()
    forces: list[Force] = []
    constraints: dict[str, Constraint] = {}

    attach_zone = None
    m_attach = _ATTACH_RE.search(desc)
    tail = desc[m_attach.start() :] if m_attach else desc
    for pat, face in _FACE_PATTERNS:
        if pat.search(tail):
            constraints.setdefault(face, Constraint(face=face))

    segs = [s for s in re.split(r"[;\n]|\band then\b", desc) if s.strip()]
    pool = segs if len(segs) > 1 else [desc]
    for seg in pool:
        m = _MASS_RE.search(seg)
        if not m:
            continue
        mag = _to_newtons(float(m.group(1).replace(",", ".")), m.group(2))
        direction = None
        for w, v in _DIR_WORDS.items():
            if re.search(rf"\b{w}\b", seg, re.IGNORECASE):
                direction = v
                break
        if direction is None:
            after = seg[m.end() :]
            for w, v in _DIR_WORDS.items():
                if re.search(rf"\b{w}\b", after, re.IGNORECASE):
                    direction = v
                    break
        if direction is None:
            direction = (0.0, 0.0, -1.0)
        face = _dominant_face(direction)
        forces.append(Force(direction=direction, magnitude_n=round(mag, 3), face=face))

    sf_match = _SF_RE.search(desc)
    sf = float(sf_match.group(1)) if sf_match else 2.0

    mat = {}
    m_e = re.search(r"(\d{3,5})\s*mpa[^\n]{0,20}?modul|modulus[^\n]{0,20}?(\d{3,5})\s*mpa", desc, re.IGNORECASE)
    if m_e:
        mat["young_modulus_mpa"] = float(m_e.group(1) or m_e.group(2))
    m_y = re.search(r"yield[^\d]{0,15}(\d{2,3})\s*mpa", desc, re.IGNORECASE)
    if m_y:
        mat["yield_mpa"] = float(m_y.group(1))

    if not constraints:
        constraints["z-"] = Constraint(face="z-")
    if not forces:
        forces.append(Force(direction=(0.0, 0.0, -1.0), magnitude_n=20.0, face="z-"))

    lc = LoadCase(
        description=desc,
        forces=forces,
        constraints=list(constraints.values()),
        safety_factor=sf,
        source="heuristic",
        **mat,
    )
    lc.validate()
    return lc


_LLM_PROMPT = """You convert natural-language descriptions of a mechanical part's job into a strict JSON load case for FEA.

Schema:
{
  "description": string,
  "forces": [{"direction": [x,y,z] unit-ish vector, "magnitude_n": number>0, "face": one of "x+","x-","y+","y-","z+","z-"}],
  "constraints": [{"face": same enum}],
  "safety_factor": number in [1,10],
  "young_modulus_mpa": number, "poisson": number, "yield_mpa": number
}
Frame: x right, y back (depth), z up. Gravity pulls -z.
Convert masses to newtons (kg -> *9.81). Faces describe where loads attach / where the part is held.

Part description:
"""


def llm_parse_description(text: str, model: str = "claude-sonnet-4-20250514") -> Optional[LoadCase]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import urllib.request

        body = json.dumps(
            {
                "model": model,
                "max_tokens": 700,
                "messages": [{"role": "user", "content": _LLM_PROMPT + text}],
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        content = "".join(b.get("text", "") for b in data.get("content", []))
        lc = LoadCase.from_json(content)
        lc.description = text.strip()
        return lc
    except Exception:
        return None


def extract_load_case(text: str, prefer_llm: bool = True) -> LoadCase:
    if prefer_llm:
        lc = llm_parse_description(text)
        if lc is not None:
            return lc
    return parse_description(text)
