from __future__ import annotations

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


def format_receipt(stats: dict) -> list[str]:
    lines = [
        RECEIPT_BEGIN,
        f";ECOSLICE mode            : {stats.get('mode', 'n/a')}",
        f";ECOSLICE load case       : {stats.get('load_case', 'n/a')}",
        f";ECOSLICE safety factor   : {_num(stats.get('safety_factor'))}",
        f";ECOSLICE allowable stress: {_num(stats.get('allowable_mpa'), '{:.1f}')} MPa (yield/sf)",
        f";ECOSLICE max von Mises   : {_num(stats.get('max_vm_mpa'), '{:.1f}')} MPa",
        f";ECOSLICE solver          : {stats.get('solver', 'n/a')} | voxels {stats.get('voxels', 'n/a')}",
        _mutation_line(
            stats, "reinforced      ", "reinforced_layers", "perimeters_added",
            "extra perimeter-lines added",
        ),
        _mutation_line(
            stats, "relaxed         ", "relaxed_layers", "perimeters_removed",
            "perimeter-lines removed",
        ),
        f";ECOSLICE reinforcement   : +{_num(stats.get('added_grams'))} g localized",
        f";ECOSLICE vs blanket-strengthened baseline: -{_num(stats.get('saved_vs_uniform_grams'))} g "
        f"(-{_num(stats.get('co2e_saved_vs_uniform_g'), '{:.1f}')} gCO2e virgin PLA)",
        ";ECOSLICE sources: " + " | ".join(CITATIONS),
        ";ECOSLICE estimate-only   : true (authoritative numbers come from G-code footers)",
        RECEIPT_END,
    ]
    return lines


def receipt_block(stats: dict) -> str:
    return "\n".join(format_receipt(stats)) + "\n"
