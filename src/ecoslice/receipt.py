from __future__ import annotations

import re
from dataclasses import dataclass

PLA_DENSITY_G_CM3 = 1.24
PLA_VIRGIN_KG_CO2E_PER_KG = 5.76
PLA_RECYCLED_KG_CO2E_PER_KG = 2.47
PRINTER_WATTS_DEFAULT = 100.0
PRINTER_WATTS_RANGE = (80.0, 125.0)
KWH_PER_HOUR_AT_DEFAULT_W = PRINTER_WATTS_DEFAULT / 1000.0

CITATIONS = (
    "FDM power draw 80-125W on PLA (measured desktop FDM range)",
    "Prusa Material LCA: virgin PLA 5.76 kgCO2e/kg; recycled PLA 2.47 kgCO2e/kg",
)


def grams_from_volume_mm3(volume_mm3: float, density_g_cm3: float = PLA_DENSITY_G_CM3) -> float:
    return volume_mm3 * 1e-3 * density_g_cm3


def co2e_g(grams_filament: float, recycled: bool = False) -> float:
    factor = PLA_RECYCLED_KG_CO2E_PER_KG if recycled else PLA_VIRGIN_KG_CO2E_PER_KG
    return grams_filament / 1000.0 * factor * 1000.0


def energy_kwh(print_hours: float, watts: float = PRINTER_WATTS_DEFAULT) -> float:
    return print_hours * watts / 1000.0


FILAMENT_G_RE = re.compile(r";\s*filament used \[g\]\s*[:=]?\s*([\d.,]+)", re.IGNORECASE)
FILAMENT_CM3_RE = re.compile(r";\s*filament used \[cm3\]\s*[:=]?\s*([\d.,]+)", re.IGNORECASE)
PRINT_TIME_RE = re.compile(
    r";\s*estimated printing time \(normal mode\)\s*[:=]?\s*(.+)", re.IGNORECASE
)
_TIME_UNITS_RE = re.compile(
    r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+(?:\.\d+)?)\s*s)?",
    re.IGNORECASE,
)


def parse_duration_seconds(text: str) -> float | None:
    """Seconds from an OrcaSlicer/PrusaSlicer duration footer ("1h 54m 12s")."""
    m = _TIME_UNITS_RE.match(text.strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
    return d * 86400.0 + h * 3600.0 + mi * 60.0 + sec


def parse_gcode_footer(gcode_text: str) -> dict:
    """Authoritative filament/time numbers straight out of the exported G-code.

    These are the slicer's own totals for the print EcoSlice just shaped, so the
    receipt can quote measured mass, time and energy instead of only a model.
    Our own ``;ECOSLICE`` lines are skipped so a re-run never reads its own output.
    """
    out: dict = {"filament_g": None, "filament_cm3": None, "print_time_s": None}
    for line in gcode_text.splitlines():
        line = line.strip()
        if not line.startswith(";") or line.startswith(";ECOSLICE"):
            continue
        if out["filament_g"] is None:
            m = FILAMENT_G_RE.match(line)
            if m:
                out["filament_g"] = float(m.group(1).replace(",", "."))
                continue
        if out["filament_cm3"] is None:
            m = FILAMENT_CM3_RE.match(line)
            if m:
                out["filament_cm3"] = float(m.group(1).replace(",", "."))
                continue
        if out["print_time_s"] is None:
            m = PRINT_TIME_RE.match(line)
            if m:
                out["print_time_s"] = parse_duration_seconds(m.group(1))
    return out


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(round(seconds)), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


@dataclass
class SavingsEstimate:
    added_grams: float
    uniform_baseline_grams: float
    saved_vs_uniform_grams: float
    co2e_saved_vs_uniform_g: float

    def as_dict(self) -> dict:
        return {
            "added_grams": round(self.added_grams, 3),
            "uniform_baseline_grams": round(self.uniform_baseline_grams, 3),
            "saved_vs_uniform_grams": round(self.saved_vs_uniform_grams, 3),
            "co2e_saved_vs_uniform_g": round(self.co2e_saved_vs_uniform_g, 3),
        }


def _num(value, spec="{:.2f}") -> str:
    try:
        return spec.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _mutation_line(stats: dict, label: str, layers_key: str, count_key: str, noun: str) -> str:
    """Distinguish "no mutations ran" (analysis only) from "ran and changed nothing"."""
    layers = stats.get(layers_key, 0)
    if count_key not in stats:
        return f";ECOSLICE {label}: {layers} layers planned (applied during slicing)"
    return f";ECOSLICE {label}: {layers} layers, {stats[count_key]} {noun}"


RECEIPT_BEGIN = ";ECOSLICE BEGIN ----------------------------------------------------------"
RECEIPT_END = ";ECOSLICE END ------------------------------------------------------------"


def _measured_lines(stats: dict) -> list[str]:
    """Lines quoting the slicer's own export footer, when the receipt has it.

    Everything above these lines is EcoSlice's model of the print; these are what
    the print actually costs, so they are labelled measured and kept separate.
    """
    lines: list[str] = []
    grams = stats.get("measured_filament_g")
    seconds = stats.get("measured_print_time_s")
    if grams is None and seconds is None:
        return lines
    if grams is not None:
        lines.append(
            f";ECOSLICE measured mass   : {_num(grams)} g "
            f"({_num(co2e_g(grams), '{:.1f}')} gCO2e virgin PLA)"
        )
    if seconds is not None:
        kwh = energy_kwh(seconds / 3600.0)
        lines.append(
            f";ECOSLICE measured time   : {format_duration(seconds)} "
            f"= {kwh:.3f} kWh at {PRINTER_WATTS_DEFAULT:.0f} W"
        )
    return lines


def format_receipt(stats: dict) -> list[str]:
    lines = [
        RECEIPT_BEGIN,
        f";ECOSLICE mode            : {stats.get('mode', 'n/a')}",
        f";ECOSLICE load case       : {stats.get('load_case', 'n/a')}",
        f";ECOSLICE safety factor   : {_num(stats.get('safety_factor'))}",
        f";ECOSLICE allowable stress: {_num(stats.get('allowable_mpa'), '{:.1f}')} MPa (yield/sf)",
        f";ECOSLICE max von Mises   : {_num(stats.get('max_vm_mpa'), '{:.1f}')} MPa",
        f";ECOSLICE solver          : {stats.get('solver', 'n/a')} | voxels {stats.get('voxels', 'n/a')}",
    ]
    if "confidence" in stats:
        lines.append(
            f";ECOSLICE confidence      : {_num(stats.get('confidence'))} "
            f"({stats.get('confidence_label', 'n/a')}) - heuristic, not a certification"
        )
        if stats.get("confidence_reasons"):
            lines.append(f";ECOSLICE confidence why  : {stats['confidence_reasons']}")
    lines += [
        _mutation_line(
            stats, "reinforced      ", "reinforced_layers", "perimeters_added",
            "extra perimeter-lines added",
        ),
        _mutation_line(
            stats, "relaxed         ", "relaxed_layers", "perimeters_removed",
            "perimeter-lines removed",
        ),
        f";ECOSLICE reinforcement   : +{_num(stats.get('added_grams'))} g localized "
        f"(walls +{_num(stats.get('added_wall_grams'))} g, solid infill "
        f"+{_num(stats.get('added_infill_grams'))} g)",
        f";ECOSLICE vs blanket-strengthened baseline: -{_num(stats.get('saved_vs_uniform_grams'))} g "
        f"(-{_num(stats.get('co2e_saved_vs_uniform_g'), '{:.1f}')} gCO2e virgin PLA)",
    ]
    lines += _measured_lines(stats)
    lines += [
        ";ECOSLICE sources: " + " | ".join(CITATIONS),
        ";ECOSLICE model-vs-measured: the +g / -g lines above are EcoSlice's model; "
        "'measured' lines are the slicer's own export footer",
        RECEIPT_END,
    ]
    return lines


def receipt_block(stats: dict) -> str:
    return "\n".join(format_receipt(stats)) + "\n"
