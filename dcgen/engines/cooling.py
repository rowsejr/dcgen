"""Heat rejection selection at the design point.

The design point is the air-on temperature at the units: design ambient + hr_uplift_k (recirculation
in plant arrays). Capacity models (indicative; swap for manufacturer selection data / curves):
  air_cooled_chiller   cap = rated * (1 - k * (T_on - T_rated))   (0 above max ambient)
  dry_cooler           cap = rated * (T_fluid_in - T_on) / (T_fluid_in_rated - T_amb_rated)
  adiabatic_dry_cooler as dry cooler with T_eff = T_on - eff * (T_on - T_wb)
The load is the gross cooling load (peak.gross_cooling, CUN-CAL-PUE-001). Each candidate also carries
its input power at full capacity at the design point (EER at 100% load), because a chiller fed from an
IT lineup must fit that lineup's mechanical allowance: the EER limits the chiller size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..catalogue import Catalogue
from ..schema import Brief
from .curves import interp  # noqa: F401  (re-exported: other modules import interp from here)


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


def air_on_c(brief: Brief) -> float:
    return brief.ambient_design_c + brief.hr_uplift_k


def unit_capacity_kw(unit: dict, brief: Brief) -> float:
    """Capacity of one unit at the design air-on temperature (ambient + uplift)."""
    t = air_on_c(brief)
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


def default_load_kw(brief: Brief, cat: Catalogue) -> float:
    """Gross cooling load when the caller has not worked it out (needs the hall area)."""
    from .halls import design_hall
    from .peak import gross_cooling
    n_halls = math.ceil(brief.it_load_mw / brief.hall_it_mw - 1e-9)
    return gross_cooling(brief, cat, design_hall(brief, cat).area_m2 * n_halls)["q_kw"]


@dataclass
class Lineups:
    """What the IT lineups can take if mechanical plant shares them (from the block plan)."""
    allowance_kw: float          # electrical.mech_kw_per_lineup
    n_blocks: int
    n_halls: int
    block_kw: float
    other_mech_kw: float         # fans, pumps, CDUs, secondary cooling, ventilation (spread over all lineups)
    genset_max_kva: float
    ups_eff: float
    mech_pf: float

    def need_kva(self, mech_kw: float) -> float:
        return self.block_kw / self.ups_eff + mech_kw / self.mech_pf


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
    peak: object = None          # peak.HRPeak
    per_lineup_kw: float = 0.0   # mechanical kW on the worst IT lineup if shared
    k_per_lineup: int = 0        # units on the busiest lineup
    shared_ok: bool = True
    shared_why: str = ""

    @property
    def n_total(self) -> int:
        return self.n_duty + self.n_standby


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
    peak_hr: object = None       # peak.HRPeak
    mech_mode: str = "shared"    # "shared" | "separate"
    per_lineup_kw: float = 0.0
    k_per_lineup: int = 0
    shared_ok: bool = True
    shared_why: str = ""
    peak: object = None          # peak.PeakPUE, attached by layout.run_engines

    def summary(self) -> dict:
        p = self.peak_hr
        return {
            "unit": self.unit["label"], "type": self.unit["type"],
            "heat_load_mw": round(self.q_kw / 1000, 2),
            "unit_capacity_at_design_kw": round(self.cap_kw),
            "duty": self.n_duty, "standby": self.n_standby, "total": self.n_total,
            "capacity_margin_pct": round(self.margin_pct, 1),
            "air_on_c": round(p.t_on_c, 1) if p else None,
            "eer_at_design": (p.eer.value if p and p.eer else None),
            "eer_source": (p.eer.source if p and p.eer else ("fans only" if p else None)),
            "unit_input_at_design_kw": round(p.unit_full_kw, 1) if p else None,
            "mech_lineups": self.mech_mode,
            "mech_per_it_lineup_kw": round(self.per_lineup_kw) if self.mech_mode == "shared" else None,
            "units_on_busiest_lineup": self.k_per_lineup if self.mech_mode == "shared" else None,
        }


_WEIGHTS = {"balanced": (0.5, 0.3, 0.2), "footprint": (1.0, 0.0, 0.0), "noise": (0.0, 1.0, 0.0), "count": (0.0, 0.0, 1.0)}


def rank_heat_rejection(brief: Brief, cat: Catalogue, types: list[str] | None = None, q_kw: float | None = None,
                        lineups: Lineups | None = None) -> list[HRCandidate]:
    """All workable units of the first suitable type, best first. Deterministic."""
    from .peak import hr_peak, per_lineup_mech
    q = q_kw if q_kw is not None else default_load_kw(brief, cat)
    units = cat["heat_rejection"]
    if brief.hr_unit:
        if brief.hr_unit not in units:
            raise KeyError(f"hr_unit '{brief.hr_unit}' is not in the catalogue for this project")
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
            pk = hr_peak(u, cap, 0, q, brief, cat)
            cap = pk.unit_cap_kw                       # CHW-corrected where the record states its CHW
            n = math.ceil(q / cap)
            sb = _standby(n, brief.hr_redundancy)
            nt = n + sb
            pk.sizing_kw = n * pk.unit_full_kw         # duty units at full capacity (electrical sizing)
            fp = nt * (u["width"] + u["intake_clear"]) * (u["length"] + u["row_clear"])
            c = HRCandidate(key, u, cap, n, sb, fp, u["lw_dba"] + 10 * math.log10(nt), (n * cap - q) / q * 100, peak=pk)
            if lineups is not None:
                c.per_lineup_kw, c.k_per_lineup = per_lineup_mech(pk.unit_full_kw, nt, lineups.n_halls,
                                                                  lineups.n_blocks, lineups.other_mech_kw)
                why = []
                if c.per_lineup_kw > lineups.allowance_kw + 1e-6:
                    why.append(f"{c.per_lineup_kw:.0f} kW on the busiest IT lineup ({c.k_per_lineup} x "
                               f"{pk.unit_full_kw:.0f} kW + share of pumps/fans) > {lineups.allowance_kw:.0f} kW")
                need = lineups.need_kva(c.per_lineup_kw)
                if need > lineups.genset_max_kva + 1e-6:
                    why.append(f"lineup needs {need:.0f} kVA > {lineups.genset_max_kva:.0f} kVA largest genset")
                c.shared_ok, c.shared_why = not why, "; ".join(why)
            cands.append(c)
        if cands:
            fp0 = min(c.footprint_m2 for c in cands)
            lw0 = min(c.lw_total_dba for c in cands)
            n0 = min(c.n_total for c in cands)
            wf, wn, wc = _WEIGHTS[brief.hr_prefer]
            for c in cands:
                c.score = wf * c.footprint_m2 / fp0 + wn * (c.lw_total_dba - lw0) / 3.0 + wc * c.n_total / n0
            return sorted(cands, key=lambda c: (round(c.score, 6), c.key))
    return []


def requested_mech_mode(brief: Brief) -> str:
    return "separate" if not brief.mech_on_blocks else brief.mech_lineups


def select_heat_rejection(brief: Brief, cat: Catalogue, q_kw: float | None = None,
                          lineups: Lineups | None = None) -> HRDesign:
    q = q_kw if q_kw is not None else default_load_kw(brief, cat)
    ranked = rank_heat_rejection(brief, cat, q_kw=q, lineups=lineups)
    if not ranked:
        raise ValueError("No heat rejection unit in the catalogue works at this design air-on temperature "
                         f"({air_on_c(brief):g} degC = {brief.ambient_design_c:g} + {brief.hr_uplift_k:g} K uplift) "
                         f"for {brief.heat_rejection} / {brief.cooling}.")
    want = requested_mech_mode(brief)
    notes: list[str] = []
    if lineups is None or want == "separate":
        c, mode = ranked[0], ("separate" if want == "separate" else "shared")
    else:
        ok = [x for x in ranked if x.shared_ok]
        if ok:
            c, mode = ok[0], "shared"
            if c is not ranked[0]:
                notes.append(f"{ranked[0].unit['label']} ranks first but cannot share the IT lineups "
                             f"({ranked[0].shared_why}); {c.unit['label']} keeps mechanical plant on the IT lineups.")
        elif want == "shared":
            c, mode = ranked[0], "shared"
            notes.append(f"No unit fits the IT lineups ({c.shared_why}): use separate mechanical lineups, "
                         "a higher CHW flow temperature, smaller blocks or a higher-EER chiller.")
        else:
            c, mode = ranked[0], "separate"
            notes.append(f"Mechanical plant cannot share the IT lineups ({c.shared_why}): separate mechanical "
                         "lineups used.")
    u = c.unit
    pk = c.peak
    eer = f", EER {pk.eer.value:.2f} at 100% load -> {pk.unit_full_kw:.0f} kW input each ({pk.eer.source})" if pk.eer else ""
    notes.insert(0, f"Selected {u['label']}: {c.cap_kw:.0f} kW each at {pk.t_on_c:g} degC air-on "
                    f"({brief.ambient_design_c:g} + {brief.hr_uplift_k:g} K uplift){eer}; {c.n_duty} duty + "
                    f"{c.n_standby} standby ({brief.hr_redundancy}); preference '{brief.hr_prefer}'.")
    alts = [a for a in ranked if a is not c][:2]
    if alts:
        notes.append("Runners-up: " + "; ".join(
            f"{a.unit['label']} ({a.n_total} units, {a.footprint_m2:.0f} m2, {a.lw_total_dba:.0f} dB(A) total"
            + (f", {a.peak.unit_full_kw:.0f} kW input each" if a.peak and a.peak.eer else "") + ")" for a in alts) + ".")
    return HRDesign(c.key, u, q, c.cap_kw, c.n_duty, c.n_standby, c.n_total, c.margin_pct, notes, peak_hr=pk,
                    mech_mode=mode, per_lineup_kw=c.per_lineup_kw, k_per_lineup=c.k_per_lineup,
                    shared_ok=c.shared_ok, shared_why=c.shared_why)
