"""Peak (design-point) PUE and the mechanical load that sizes the electrical plant.

Only the peak is needed for a test fit: it sizes gensets, transformers, LV lineups, the intake and the
fuel. Annual PUE belongs to the PUE tool. Method after CUN-CAL-PUE-001 (Cundall PUE calculation),
evaluated at one condition, design ambient + uplift (air-on at the chillers), IT at 100%:

  gross CHW load   Q = (IT x (1 + distribution) + hall lighting) x (1 + fan gains x air share)
                       + electrical rooms
  chillers         Q / EER(T_on), EER at 100% load (record curve, rated EER, or the generic curve)
  fixed mech       fan walls / CRAHs, CDUs, CHW pumps, secondary cooling, ventilation
  electrical       UPS loss on IT (+ pumps on UPS), LV loss on the installed lineup rating plus the
                   actual chiller and pump load, busbar, RMU per lineup, transformers, lighting
  peak PUE         (IT + mechanical + electrical) / IT

Electrical sizing takes the worst case instead: the duty chillers each at full capacity at T_on.
Every coefficient is a catalogue key (peak_pue, electrical). Pure functions, no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .curves import interp


# ------------------------------------------------------------------ chiller EER at the design point
@dataclass
class EER:
    value: float
    source: str            # "curve" | "rated" | "generic"
    note: str
    clamped: bool = False  # air-on temperature outside the data: end value used, flagged


def compressor_key(text: str | None) -> str:
    t = (text or "").lower()
    if "turbo" in t:
        return "turbocor"
    if "centrif" in t:
        return "centrifugal"
    if "scroll" in t:
        return "scroll"
    if "screw" in t:
        return "screw"
    return "default"


def _shape(pe: dict, t: float) -> tuple[float, bool]:
    xs, ys = pe["shape_ambient_c"], pe["shape_ratio"]
    return interp(xs, ys, t), not (xs[0] <= t <= xs[-1])


def chw_ratio(pe: dict, t_on: float, chw_new: float, chw_base: float) -> float:
    """EER multiplier for a change of CHW flow temperature at the same air-on (Carnot, constant eta)."""
    tc = t_on + pe["cond_approach_k"]

    def cop(chw: float) -> float:
        te = chw - pe["evap_approach_k"]
        return (te + 273.15) / max(tc - te, 5.0)

    return cop(chw_new) / cop(chw_base)


def chiller_eer(unit: dict, t_on: float, chw_flow: float, cat: dict) -> EER:
    """EER (incl. fans) at 100% load, air-on t_on, CHW flow chw_flow."""
    pe = cat["peak_pue"]["chiller_eer"]
    rated_chw = unit.get("chw_supply_c")
    curve = unit.get("eer_curve")
    if curve and curve.get("ambient_c"):
        xs = curve["ambient_c"]
        v = interp(xs, curve["eer"], t_on)
        clamped = not (xs[0] <= t_on <= xs[-1])
        note = "manufacturer EER curve"
        if rated_chw is None:
            note += " (CHW temperatures not recorded: taken as the brief's)"
            base = chw_flow
        else:
            base = rated_chw
        src = "curve"
    elif unit.get("eer_rated"):
        s_on, c1 = _shape(pe, t_on)
        s_r, _ = _shape(pe, unit.get("rated_ambient_c", pe["ref_ambient_c"]))
        v = unit["eer_rated"] * s_on / s_r
        clamped = c1
        base = rated_chw if rated_chw is not None else chw_flow
        note = f"rated EER {unit['eer_rated']:g} at {unit.get('rated_ambient_c', pe['ref_ambient_c']):g} C, generic curve shape"
        src = "rated"
    else:
        key = compressor_key(unit.get("compressor"))
        e_ref = pe["eer_at_ref"].get(key, pe["eer_at_ref"]["default"])
        s_on, clamped = _shape(pe, t_on)
        v = e_ref * s_on
        base = pe["ref_chw_flow_c"]
        note = f"generic {key} EER {e_ref:g} at {pe['ref_ambient_c']:g} C / CHW {base:g} C (no EER data)"
        src = "generic"
    if base != chw_flow:
        v *= chw_ratio(pe, t_on, chw_flow, base)
    return EER(round(v, 3), src, note, clamped)


def chiller_capacity_kw(unit: dict, cap_at_t_on: float, chw_flow: float, cat: dict) -> float:
    """Capacity corrected for CHW flow when the record states its rated CHW temperature."""
    rated = unit.get("chw_supply_c")
    if rated is None:
        return cap_at_t_on
    k = cat["peak_pue"]["chiller_eer"].get("capacity_per_k_chw", 0.02)
    return cap_at_t_on * (1 + k * (chw_flow - rated))


# ------------------------------------------------------------------ loads
def liquid_share(brief, cat) -> float:
    return float(cat["peak_pue"]["liquid_share"].get(brief.cooling, 0.0))


def gross_cooling(brief, cat, hall_area_m2: float) -> dict:
    """Gross chilled water (or heat rejection) load, kW, and its parts."""
    P = cat["peak_pue"]
    it = brief.it_load_mw * 1000
    ls = liquid_share(brief, cat)
    hall = it * (1 + P["distribution_gains_frac"]) + P["hall_lighting_w_m2"] * hall_area_m2 / 1000
    fan = hall * P["fan_gains_frac"] * (1 - ls)
    rooms = P["elec_room_cooling_frac"] * it
    return {"q_kw": hall + fan + rooms, "hall_kw": hall, "fan_gains_kw": fan, "elec_rooms_kw": rooms,
            "liquid_share": ls}


def other_mech_kw(brief, cat, gross: dict) -> dict:
    """Mechanical loads that are not the heat rejection plant, at peak (normal duty)."""
    P = cat["peak_pue"]
    it = brief.it_load_mw * 1000
    ls = gross["liquid_share"]
    return {
        "air_fans": P["air_fan_kw_per_kw"] * gross["hall_kw"] * (1 - ls),
        "cdus": P["cdu_kw_per_kw"] * gross["hall_kw"] * ls,
        "chw_pumps": P["chw_pump_kw_per_kw"] * gross["q_kw"],
        "secondary_cooling": P["secondary_cooling_frac"] * it,
        "ventilation": P["ventilation_frac"] * it,
    }


@dataclass
class HRPeak:
    """Heat rejection plant at the design point."""
    t_on_c: float
    eer: EER | None          # None for dry / adiabatic coolers (fans only)
    unit_cap_kw: float       # one unit at t_on (CHW corrected)
    unit_full_kw: float      # one unit's input at full capacity at t_on (electrical sizing)
    normal_kw: float         # whole plant at peak, normal operation (Q / EER)
    sizing_kw: float         # duty units at full capacity (worst case)


def hr_peak(unit: dict, cap_at_t_on: float, n_duty: int, q_kw: float, brief, cat) -> HRPeak:
    t_on = brief.ambient_design_c + brief.hr_uplift_k
    if unit["type"] == "air_cooled_chiller":
        e = chiller_eer(unit, t_on, brief.chw_flow_c, cat)
        cap = chiller_capacity_kw(unit, cap_at_t_on, brief.chw_flow_c, cat)
        full = cap / e.value
        return HRPeak(t_on, e, cap, full, q_kw / e.value, n_duty * full)
    sfp = cat["peak_pue"]["dry_cooler_fan_kw_per_kw"]
    full = sfp * cap_at_t_on
    return HRPeak(t_on, None, cap_at_t_on, full, sfp * q_kw, n_duty * full)


# ------------------------------------------------------------------ lineups
def lineups_per_hall(n_blocks: int, n_halls: int) -> float:
    return n_blocks / max(1, n_halls)


def per_lineup_mech(unit_full_kw: float, n_hr_total: int, n_halls: int, n_blocks: int,
                    other_mech_total_kw: float) -> tuple[float, int]:
    """Mechanical kW on the worst IT lineup when mechanical plant shares the IT lineups.
    Units serving a hall are spread over that hall's lineups; k = units on the busiest lineup."""
    lph = lineups_per_hall(n_blocks, n_halls)
    k = math.ceil(math.ceil(n_hr_total / max(1, n_halls)) / max(1.0, lph) - 1e-9)
    return k * unit_full_kw + other_mech_total_kw / max(1, n_blocks), k


# ------------------------------------------------------------------ the peak PUE build-up
@dataclass
class PeakPUE:
    it_kw: float
    t_on_c: float
    chw_flow_c: float
    gross: dict
    mech: dict                         # kW by item, normal operation at peak
    elec: dict                         # kW by item
    mech_kw: float
    elec_kw: float
    facility_kw: float
    pue: float
    mech_sizing_kw: float              # duty units at full capacity + other mech: sizes the electrical plant
    hr: HRPeak
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        e = self.hr.eer
        return {
            "peak_pue": round(self.pue, 3),
            "air_on_c": round(self.t_on_c, 1), "chw_flow_c": self.chw_flow_c,
            "it_kw": round(self.it_kw), "gross_cooling_kw": round(self.gross["q_kw"]),
            "mechanical_kw": round(self.mech_kw), "electrical_losses_kw": round(self.elec_kw),
            "facility_kw": round(self.facility_kw), "mech_sizing_kw": round(self.mech_sizing_kw),
            "chiller_eer": e.value if e else None, "eer_source": e.source if e else "fans only",
            "eer_note": e.note if e else "", "eer_clamped": bool(e and e.clamped),
            "unit_full_input_kw": round(self.hr.unit_full_kw, 1),
            "unit_capacity_kw": round(self.hr.unit_cap_kw),
            "breakdown_kw": {k: round(v, 1) for k, v in {**self.mech, **self.elec}.items()},
            "ppue": {k: round(1 + v / self.it_kw, 4) for k, v in {**self.mech, **self.elec}.items() if v},
        }


def peak_pue(brief, cat, gross: dict, other: dict, hr: HRPeak, *, n_it_lineups: int, lineup_mech_rating_kw: float,
             transformers_kva: list[float], hall_area_m2: float) -> PeakPUE:
    """lineup_mech_rating_kw: installed mechanical capacity on the lineups (allowance x IT lineups when
    shared, mechanical lineup rating when separate) - CUN-CAL-PUE-001 applies LV loss to the installed
    LV rating plus the actual chiller and pump load."""
    P = cat["peak_pue"]
    it = brief.it_load_mw * 1000
    mech = {"heat_rejection": hr.normal_kw, **other}
    mech_kw = sum(mech.values())
    pumps_on_ups = P.get("pumps_on_ups", True)
    elec = {
        "ups_losses": P["ups_loss_frac"] * (it + (other["chw_pumps"] if pumps_on_ups else 0.0)),
        "lv_losses": P["lv_loss_frac"] * (it + lineup_mech_rating_kw + hr.normal_kw + other["chw_pumps"]),
        "busbar_losses": P["busbar_loss_frac"] * it,
        "rmu_losses": P["rmu_loss_kw"] * n_it_lineups,
        "transformer_losses": P["tx_loss_frac"] * sum(transformers_kva),
        "lighting": P["lighting_w_m2_gia"] * hall_area_m2 * 2.0 / 1000,     # GIA ~ 2 x white space
    }
    elec_kw = sum(elec.values())
    fac = it + mech_kw + elec_kw
    sizing = hr.sizing_kw + sum(other.values())
    notes = []
    if hr.eer is not None:
        notes.append(f"Peak PUE {fac / it:.3f} at {hr.t_on_c:g} C air-on ({brief.ambient_design_c:g} C + "
                     f"{brief.hr_uplift_k:g} K uplift), CHW flow {brief.chw_flow_c:g} C: chiller EER {hr.eer.value:.2f} "
                     f"({hr.eer.note}).")
        if hr.eer.clamped:
            notes.append(f"Air-on {hr.t_on_c:g} C is outside the chiller's EER data; the end value was used.")
    else:
        notes.append(f"Peak PUE {fac / it:.3f} at {hr.t_on_c:g} C air-on (dry/adiabatic coolers: fans only).")
    return PeakPUE(it, hr.t_on_c, brief.chw_flow_c, gross, mech, elec, mech_kw, elec_kw, fac, fac / it, sizing, hr,
                   notes)
