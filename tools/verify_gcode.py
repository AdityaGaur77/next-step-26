"""Tell me whether EcoSlice actually ran on this G-code — and if not, what to fix.

Reading a receipt by eye is easy to get wrong: "2 layers planned" and
"2 layers, 84 perimeter-lines added" look alike at a glance and mean opposite
things. This reads the exported file and names the failure mode.

    python tools/verify_gcode.py Cube_PLA_34m37s.gcode

Exit code 0 when the plugin ran AND changed the print; 1 otherwise, so it can
gate a demo checklist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ecoslice.receipt import format_duration, parse_gcode_footer  # noqa: E402

# G-code runs to hundreds of MB. The receipt sits near the top and the slicer's
# totals at the very bottom, so read both ends rather than the whole file.
HEAD_BYTES = 512 * 1024
TAIL_BYTES = 128 * 1024

SPIKE_MARKER = ";ECOSLICE SPIKE"
RECEIPT_BEGIN = ";ECOSLICE BEGIN"
RECEIPT_END = ";ECOSLICE END"

PLANNED_RE = re.compile(r";ECOSLICE (reinforced|relaxed)\s*:\s*(\d+) layers planned")
APPLIED_RE = re.compile(r";ECOSLICE (reinforced|relaxed)\s*:\s*(\d+) layers, (-?\d+) ")
FIELD_RE = re.compile(r";ECOSLICE ([a-zA-Z][a-zA-Z \-]*?)\s*:\s*(.+)")

# verdict -> (headline, what it means, what to do)
DIAGNOSES = {
    "ok": (
        "EcoSlice ran and changed the print.",
        "The analysis ran and the mutations were applied during slicing.",
        "Nothing to fix. Slice the same part with the capability deselected to get a "
        "baseline, then: python tools/gcode_diff.py baseline.gcode this.gcode",
    ),
    "no_marker": (
        "EcoSlice did not run at all.",
        "No ;ECOSLICE markers anywhere in this file.",
        "Almost always the capability is not selected. In OrcaSlicer: Process settings "
        "-> Others -> Slicing Pipeline Plugin -> tick EcoSlice. The C++ hook returns "
        "early while that option is empty and reports nothing. Also confirm the plugin "
        "is Activated in the Plugins dialog — installed is not enabled.",
    ),
    "spike": (
        "That is the spike, not the plugin.",
        "Found the spike's marker instead of a receipt. spike_extra_perimeters.py is "
        "the day-1 gate script: it proves mutation works but has no config and writes "
        "no receipt.",
        "Install plugin/ecoslice_core.py via Plugins dialog -> Local install, activate "
        "it, and select it in Process settings -> Others -> Slicing Pipeline Plugin.",
    ),
    "analysis_only": (
        "Analysis ran, but nothing was mutated.",
        "The receipt says layers were 'planned (applied during slicing)', which means "
        "the posPrepareInfill hook never recorded any mutation.",
        "The plugin loaded and solved, so installation is fine. Check the console for "
        "'posPrepareInfill hook failed' lines. If the print has several objects, note "
        "that ctx.object is only populated on object-scoped steps.",
    ),
    "no_op": (
        "The mutation hook ran but changed nothing.",
        "The plan was applied but zero perimeter-lines were added or removed.",
        "Two causes. (1) The plan landed off the part — the frame-alignment failure in "
        "docs/PLUGIN_API_NOTES.md, where the mesh is in plate coordinates and "
        "fill_surfaces are object-local. (2) The plan genuinely had nothing to do, "
        "which is normal on a short stubby part like a cube: stress stays low and "
        "uniform. Re-slice a 100x10x10 mm bar before assuming it is broken.",
    ),
}


def read_ends(path: Path) -> tuple[str, str]:
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(min(HEAD_BYTES, size))
        if size > HEAD_BYTES:
            f.seek(max(size - TAIL_BYTES, HEAD_BYTES), os.SEEK_SET)
            tail = f.read()
        else:
            tail = b""
    dec = lambda b: b.decode("utf-8", errors="replace")  # noqa: E731
    return dec(head), dec(tail)


def extract_receipt(text: str) -> list[str]:
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith(RECEIPT_BEGIN))
    except StopIteration:
        return []
    out = []
    for line in lines[start:]:
        out.append(line)
        if line.startswith(RECEIPT_END):
            break
    return out


def diagnose(receipt: list[str], head: str) -> tuple[str, dict]:
    if not receipt:
        return ("spike" if SPIKE_MARKER in head else "no_marker"), {}

    body = "\n".join(receipt)
    applied = {m.group(1): int(m.group(3)) for m in APPLIED_RE.finditer(body)}
    planned = {m.group(1): int(m.group(2)) for m in PLANNED_RE.finditer(body)}

    fields = {}
    for line in receipt:
        m = FIELD_RE.match(line)
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()

    detail = {"applied": applied, "planned": planned, "fields": fields}

    if not applied and planned:
        return "analysis_only", detail
    if applied and sum(abs(v) for v in applied.values()) == 0:
        return "no_op", detail
    if applied:
        return "ok", detail
    return "analysis_only", detail


def _fmt_footer(footer: dict) -> list[str]:
    rows = []
    if footer.get("filament_g") is not None:
        rows.append(f"    filament        {footer['filament_g']:.2f} g")
    if footer.get("filament_cm3") is not None:
        rows.append(f"    volume          {footer['filament_cm3']:.2f} cm3")
    if footer.get("print_time_s") is not None:
        rows.append(f"    print time      {format_duration(footer['print_time_s'])}")
    return rows


def _wrap(text: str, width: int = 76, indent: str = "  ") -> str:
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(indent + line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(indent + line)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Check whether EcoSlice ran on an exported G-code file."
    )
    ap.add_argument("gcode", help="exported .gcode file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    path = Path(args.gcode)
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 2

    head, tail = read_ends(path)
    receipt = extract_receipt(head)
    verdict, detail = diagnose(receipt, head)
    footer = parse_gcode_footer(tail or head)
    headline, meaning, fix = DIAGNOSES[verdict]
    ok = verdict == "ok"

    if args.json:
        print(json.dumps({
            "file": str(path),
            "verdict": verdict,
            "ok": ok,
            "headline": headline,
            "applied": detail.get("applied", {}),
            "planned": detail.get("planned", {}),
            "receipt_fields": detail.get("fields", {}),
            "footer": footer,
        }, indent=2))
        return 0 if ok else 1

    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {path.name}")
    print(f"       {headline}\n")
    print("  what this means")
    print(_wrap(meaning, indent="    "))
    print("\n  what to do")
    print(_wrap(fix, indent="    "))

    applied = detail.get("applied") or {}
    if applied:
        print("\n  mutations applied")
        for key in ("reinforced", "relaxed"):
            if key in applied:
                print(f"    {key:<14}{applied[key]} perimeter-lines")

    fields = detail.get("fields") or {}
    interesting = ("load case", "max von Mises", "allowable stress", "confidence", "solver")
    shown = [(k, v) for k, v in fields.items() if k in interesting]
    if shown:
        print("\n  from the receipt")
        for k, v in shown:
            print(f"    {k:<18}{v[:70]}")

    rows = _fmt_footer(footer)
    if rows:
        print("\n  this print costs")
        print("\n".join(rows))

    if ok:
        print("\n  next: slice again with EcoSlice deselected, then")
        print(f"    python tools/gcode_diff.py baseline.gcode {path.name}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
