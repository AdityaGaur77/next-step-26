from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FILAMENT_USED_RE = re.compile(r";\s*filament used \[g\]\s*[:=]?\s*([\d.,]+)", re.IGNORECASE)
FILAMENT_CM3_RE = re.compile(r";\s*filament used \[cm3\]\s*[:=]?\s*([\d.,]+)", re.IGNORECASE)
TIME_MODES = [
    ("normal", r";\s*estimated printing time \(normal mode\)\s*[:=]?\s*(.+)"),
    ("silent", r";\s*estimated printing time \(silent mode\)\s*[:=]?\s*(.+)"),
]
TIME_UNIT_RE = re.compile(
    r"(?:(\d+)\s*d(?:ay)?)?\s*(?:(\d+)\s*h(?:our)?)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+(?:\.\d+)?)\s*s(?:ec)?)?",
    re.IGNORECASE,
)

PLA_VIRGIN_KG_CO2E_PER_KG = 5.76
PRINTER_WATTS_DEFAULT = 100.0


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_gcode(path: str | Path) -> dict:
    grams = None
    cm3 = None
    times = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(";ECOSLICE"):
                continue
            m = FILAMENT_USED_RE.match(line.strip())
            if m and grams is None:
                grams = _num(m.group(1))
                continue
            m = FILAMENT_CM3_RE.match(line.strip())
            if m and cm3 is None:
                cm3 = _num(m.group(1))
                continue
            for mode, pattern in TIME_MODES:
                m = re.match(pattern, line.strip(), re.IGNORECASE)
                if m:
                    t = m.group(1)
                    um = TIME_UNIT_RE.match(t)
                    if um:
                        d, h, mi, s = (float(x) if x else 0.0 for x in um.groups())
                        times[mode] = d * 86400 + h * 3600 + mi * 60 + s
    return {
        "file": str(path),
        "filament_g": grams,
        "filament_cm3": cm3,
        "time_normal_s": times.get("normal"),
        "time_silent_s": times.get("silent"),
    }


def compare(baseline: dict, optimized: dict) -> dict:
    dg = (
        optimized["filament_g"] - baseline["filament_g"]
        if baseline["filament_g"] is not None and optimized["filament_g"] is not None
        else None
    )
    dt = (
        optimized["time_normal_s"] - baseline["time_normal_s"]
        if baseline["time_normal_s"] is not None and optimized["time_normal_s"] is not None
        else None
    )
    out = {
        "delta_filament_g": round(dg, 3) if dg is not None else None,
        "delta_time_normal_s": round(dt, 1) if dt is not None else None,
    }
    if dg is not None:
        out["delta_co2e_g_virgin_pla"] = round(dg * PLA_VIRGIN_KG_CO2E_PER_KG, 2)
    if dt is not None:
        out["energy_saved_kwh_at_100w"] = round(-dt / 3600.0 * PRINTER_WATTS_DEFAULT / 1000.0, 4)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diff OrcaSlicer G-code footers: filament, time, CO2e.")
    ap.add_argument("baseline")
    ap.add_argument("optimized")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--assert-lighter",
        action="store_true",
        help="exit 1 unless optimized uses strictly less filament than baseline",
    )
    args = ap.parse_args(argv)

    b = parse_gcode(args.baseline)
    o = parse_gcode(args.optimized)
    result = {"baseline": b, "optimized": o, "comparison": compare(b, o)}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for key in ("filament_g", "filament_cm3", "time_normal_s"):
            print(f"{key:18} baseline={b[key]}  optimized={o[key]}")
        for k, v in result["comparison"].items():
            print(f"{k:32} {v}")

    if args.assert_lighter:
        dg = result["comparison"]["delta_filament_g"]
        if dg is None or dg >= 0:
            print("FAIL: optimized output is not lighter", file=sys.stderr)
            return 1
        print(f"OK: saved {-dg:.3f} g")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
