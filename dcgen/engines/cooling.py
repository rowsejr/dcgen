"""Heat rejection selection against design ambient.

Capacity models (indicative; swap for manufacturer selection data / curves):
  air_cooled_chiller   cap = rated * (1 - k * (T_amb - T_rated))   (0 above max ambient)
  dry_cooler           cap = rated * (T_fluid_in - T_amb) / (T_fluid_in_rated - T_amb_rated)
  adiabatic_dry_cooler as dry cooler with T_eff = T_db - eff * (T_db - T_wb)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..catalogue import Catalogue
from ..schema import Brief


def unit_capacity_kw(unit: dict, brief: Brief) -> float:
    t = brief.ambient_design_c
    if t > unit.get("max_ambient_c", 99):
        return 0.0
    typ = unit["type"]
    if typ == "air_cooled_chiller":
        return unit["capacity_kw"] * (1 - unit["derate_per_k"] * max(0.0, t - unit["rated_ambient_c"]))
    if typ == "adiabatic_dry_cooler":
        t = t - unit["adiabatic_effectiveness"] * (t - brief.ambient_design_wb_c)
    rated_dt = unit["rated_fluid_in_c"] - unit["rated_ambient_c"]
    return max(0.0, unit["capacity_kw"] * (brief.fwr_c - t) / rated_dt)


def _standby(n: int, rule: str) -> int:
    r = rule.replace(" ", "").upper()
    if r.startswith("N+") and r.endswith("%"):
        return math.ceil(n * float(r[2:-1]) / 100)
    if r.startswith("N+"):
        return int(r[2:])
    return 0


@dataclass
class HRDesign:
    unit_key: str
    unit: dict
    q_kw: float
    cap_kw: float
    n_duty: int
    n_standby: int
    n_total: int
    margin_pct: float
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "unit": self.unit["label"], "type": self.unit["type"],
            "heat_load_mw": round(self.q_kw / 1000, 2),
            "unit_capacity_at_design_kw": round(self.cap_kw),
            "duty": self.n_duty, "standby": self.n_standby, "total": self.n_total,
            "capacity_margin_pct": round(self.margin_pct, 1),
        }


def select_heat_rejection(brief: Brief, cat: Catalogue) -> HRDesign:
    q = brief.it_load_mw * 1000 * (1 + brief.hr_overhead_frac)
    units = cat["heat_rejection"]
    notes: list[str] = []
    if brief.heat_rejection == "auto":
        prefs = (["dry_cooler", "adiabatic_dry_cooler", "air_cooled_chiller"]
                 if brief.cooling == "liquid_dtc" else ["air_cooled_chiller"])
    else:
        prefs = [brief.heat_rejection]

    for typ in prefs:
        for key, u in units.items():
            if u["type"] != typ:
                continue
            cap = unit_capacity_kw(u, brief)
            if cap <= 0.25 * u["capacity_kw"]:
                notes.append(f"{u['label']}: only {cap:.0f} kW at {brief.ambient_design_c} degC - rejected.")
                continue
            n = math.ceil(q / cap)
            sb = _standby(n, brief.hr_redundancy)
            margin = (n * cap - q) / q * 100
            notes.append(f"Selected {u['label']}: {cap:.0f} kW each at {brief.ambient_design_c} degC "
                         f"design ambient; {n} duty + {sb} standby ({brief.hr_redundancy}).")
            return HRDesign(key, u, q, cap, n, sb, n + sb, margin, notes)
    raise ValueError("No heat rejection unit in the catalogue works at this design ambient: " + " ".join(notes))
