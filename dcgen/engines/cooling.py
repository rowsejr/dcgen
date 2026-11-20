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


def interp(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear lookup, clamped at the ends (xs ascending)."""
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for i in range(1, len(xs)):
        if x <= xs[i]:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return float(ys[i - 1] + f * (ys[i] - ys[i - 1]))
    return float(ys[-1])


def curve_capacity_kw(unit: dict, t_amb: float, fluid_in_c: float) -> float | None:
    """Capacity from manufacturer curves if the unit carries them.
      curve:  {ambient_c: [...], capacity_kw: [...]}                     (one set of conditions)
      curves: [{fluid_in_c: 45, ambient_c: [...], capacity_kw: [...]}]   (bilinear on fluid-in and ambient)
    Curves are read at the effective ambient (after adiabatic pre-cooling, for adiabatic units)."""
    if "curves" in unit and unit["curves"]:
        cs = sorted(unit["curves"], key=lambda c: c["fluid_in_c"])
        vals = [interp(c["ambient_c"], c["capacity_kw"], t_amb) for c in cs]
        return max(0.0, interp([c["fluid_in_c"] for c in cs], vals, fluid_in_c))
    if "curve" in unit and unit["curve"]:
        c = unit["curve"]
        return max(0.0, interp(c["ambient_c"], c["capacity_kw"], t_amb))
    return None


def unit_capacity_kw(unit: dict, brief: Brief) -> float:
    t = brief.ambient_design_c
    if t > unit.get("max_ambient_c", 99):
        return 0.0
    typ = unit["type"]
    if typ == "adiabatic_dry_cooler" and ("curve" in unit or "curves" in unit):
        t = t - unit.get("adiabatic_effectiveness", 0.0) * (t - brief.ambient_design_wb_c)
    cap = curve_capacity_kw(unit, t, brief.fwr_c)
    if cap is not None:
        return cap
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


@dataclass
class HRCandidate:
    key: str
    unit: dict
    cap_kw: float
    n_duty: int
    n_standby: int
    footprint_m2: float
    lw_total_dba: float
    margin_pct: float
    score: float = 0.0

    @property
    def n_total(self) -> int:
        return self.n_duty + self.n_standby


_WEIGHTS = {"balanced": (0.5, 0.3, 0.2), "footprint": (1.0, 0.0, 0.0), "noise": (0.0, 1.0, 0.0), "count": (0.0, 0.0, 1.0)}


def rank_heat_rejection(brief: Brief, cat: Catalogue, types: list[str] | None = None) -> list[HRCandidate]:
    """All workable units of the first suitable type, best first. Deterministic."""
    q = brief.it_load_mw * 1000 * (1 + brief.hr_overhead_frac)
    units = cat["heat_rejection"]
    if brief.hr_unit:
        if brief.hr_unit not in units:
            raise KeyError(f"hr_unit '{brief.hr_unit}' is not in the catalogue for this project")
        keys = [brief.hr_unit]
        groups = [[brief.hr_unit]]
    else:
        if types is None:
            if brief.heat_rejection == "auto":
                types = (["dry_cooler", "adiabatic_dry_cooler", "air_cooled_chiller"]
                         if brief.cooling == "liquid_dtc" else ["air_cooled_chiller"])
            else:
                types = [brief.heat_rejection]
        groups = [[k for k, u in units.items() if u["type"] == t] for t in types]
    for keys in groups:
        cands = []
        for key in keys:
            u = units[key]
            cap = unit_capacity_kw(u, brief)
            if cap <= 0.25 * u["capacity_kw"]:
                continue
            n = math.ceil(q / cap)
            sb = _standby(n, brief.hr_redundancy)
            nt = n + sb
            fp = nt * (u["width"] + u["intake_clear"]) * (u["length"] + u["row_clear"])
            cands.append(HRCandidate(key, u, cap, n, sb, fp, u["lw_dba"] + 10 * math.log10(nt), (n * cap - q) / q * 100))
        if cands:
            fp0 = min(c.footprint_m2 for c in cands)
            lw0 = min(c.lw_total_dba for c in cands)
            n0 = min(c.n_total for c in cands)
            wf, wn, wc = _WEIGHTS[brief.hr_prefer]
            for c in cands:
                c.score = wf * c.footprint_m2 / fp0 + wn * (c.lw_total_dba - lw0) / 3.0 + wc * c.n_total / n0
            return sorted(cands, key=lambda c: (round(c.score, 6), c.key))
    return []


def select_heat_rejection(brief: Brief, cat: Catalogue) -> HRDesign:
    q = brief.it_load_mw * 1000 * (1 + brief.hr_overhead_frac)
    ranked = rank_heat_rejection(brief, cat)
    if not ranked:
        raise ValueError("No heat rejection unit in the catalogue works at this design ambient "
                         f"({brief.ambient_design_c} degC) for {brief.heat_rejection} / {brief.cooling}.")
    c = ranked[0]
    u = c.unit
    notes = [f"Selected {u['label']}: {c.cap_kw:.0f} kW each at {brief.ambient_design_c} degC design ambient; "
             f"{c.n_duty} duty + {c.n_standby} standby ({brief.hr_redundancy}); preference '{brief.hr_prefer}'."]
    if len(ranked) > 1:
        alts = "; ".join(f"{a.unit['label']} ({a.n_total} units, {a.footprint_m2:.0f} m2, {a.lw_total_dba:.0f} dB(A) total)"
                         for a in ranked[1:3])
        notes.append(f"Runners-up: {alts}.")
    return HRDesign(c.key, u, q, c.cap_kw, c.n_duty, c.n_standby, c.n_total, c.margin_pct, notes)
