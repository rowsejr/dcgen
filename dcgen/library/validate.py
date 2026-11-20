"""Engineering sanity checks on library records. Errors block approval; warnings are for the checker.

These catch transcription slips (mm entered as m, MW as kW, curves the wrong way round), not
engineering judgement: a record that passes is still checked by an engineer before approval.
"""
from __future__ import annotations

from .records import STATUSES, TYPES, spec

RANGES = {                      # field: (low, high) plausible bounds, same units as the field
    "length": (0.5, 40.0), "width": (0.5, 20.0), "height": (0.3, 15.0),
    "intake_clear": (0.2, 15.0), "row_clear": (0.2, 20.0), "side_clear": (0.1, 10.0), "end_clear": (0.1, 20.0),
    "clear": (0.1, 10.0), "lw_dba": (55.0, 140.0), "rated_ambient_c": (10.0, 55.0), "max_ambient_c": (20.0, 65.0),
    "rated_fluid_in_c": (20.0, 80.0), "derate_per_k": (0.0, 0.06), "adiabatic_effectiveness": (0.0, 1.0),
    "capacity_kw": (50.0, 6000.0), "kva": (100.0, 12000.0), "kwe": (80.0, 10000.0), "fuel_l_per_kwh": (0.15, 0.45),
    "operating_kg": (200.0, 150000.0), "block_kw": (200.0, 6000.0), "room_width_m": (2.0, 60.0),
    "eer_rated": (1.5, 15.0), "rated_fluid_out_c": (10.0, 75.0), "dry_kg": (100.0, 80000.0), "cond_c": (15.0, 60.0),
    "fc_full_ambient_c": (-25.0, 30.0), "power_kw": (5.0, 3000.0), "lp_1m_dba": (50.0, 110.0), "kw_dcc": (80.0, 10000.0),
}


def validate_record(rtype: str, r: dict, *, for_approval: bool = False) -> list[tuple[str, str]]:
    """Returns [(level, message)] with level 'error' or 'warn'."""
    out: list[tuple[str, str]] = []
    err = lambda m: out.append(("error", m))
    warn = lambda m: out.append(("warn", m))
    for f in spec(rtype):
        if f.required and f.name not in r and f.name != "record_id":
            err(f"{f.name} is required")
    if not r.get("record_id"):
        err("record_id is required")
    if r.get("status", "draft") not in STATUSES:
        err(f"status must be one of {STATUSES}")
    for k, (lo, hi) in RANGES.items():
        if k in r and not (lo <= r[k] <= hi):
            err(f"{k}={r[k]:g} outside plausible range {lo:g}..{hi:g} (units?)")
    if TYPES[rtype]["section"] == "heat_rejection":
        has_curve = bool(r.get("curve_ambient_c")) or bool(r.get("curves_json"))
        if rtype == "chiller" and "derate_per_k" not in r and not has_curve:
            err("chiller needs derate_per_k or a capacity curve")
        xs, ys = r.get("curve_ambient_c"), r.get("curve_capacity_kw")
        if bool(xs) != bool(ys):
            err("curve_ambient_c and curve_capacity_kw must be given together")
        if xs and ys:
            if len(xs) != len(ys):
                err(f"curve lengths differ ({len(xs)} vs {len(ys)})")
            elif len(xs) < 3:
                warn("curve has fewer than 3 points")
            if any(b <= a for a, b in zip(xs, xs[1:])):
                err("curve_ambient_c must be strictly ascending")
            if ys and any(b > a + 1e-9 for a, b in zip(ys, ys[1:])):
                warn("capacity rises with ambient somewhere on the curve - check the order of the columns")
            if xs and ys and r.get("rated_ambient_c") is not None:
                lo, hi = xs[0], xs[-1]
                if not lo <= r["rated_ambient_c"] <= hi:
                    warn("rated_ambient_c lies outside the curve range")
        ex, ey = r.get("eer_curve_ambient_c"), r.get("eer_curve")
        if bool(ex) != bool(ey):
            err("eer_curve_ambient_c and eer_curve must be given together")
        if ex and ey:
            if len(ex) != len(ey):
                err(f"EER curve lengths differ ({len(ex)} vs {len(ey)})")
            if any(b <= a for a, b in zip(ex, ex[1:])):
                err("eer_curve_ambient_c must be strictly ascending")
            if any(b > a + 1e-9 for a, b in zip(ey, ey[1:])):
                warn("EER rises with ambient somewhere on the curve - check the order of the columns")
        if r.get("power_kw") and r.get("capacity_kw") and r.get("eer_rated"):
            if abs(r["capacity_kw"] / r["power_kw"] - r["eer_rated"]) > 0.05 * r["eer_rated"]:
                warn(f"capacity/power = {r['capacity_kw'] / r['power_kw']:.2f} but eer_rated is {r['eer_rated']:g}")
        if rtype == "chiller" and r.get("capacity_kw") and r.get("length"):
            kwm = r["capacity_kw"] / r["length"]
            if kwm > 250:
                err(f"{kwm:.0f} kW per metre of length is implausible for an air-cooled chiller (typical 80-170): "
                    "check the length (module only? transcription?)")
        if "max_ambient_c" in r and "rated_ambient_c" in r and r["max_ambient_c"] < r["rated_ambient_c"]:
            err("max_ambient_c is below rated_ambient_c")
        if r.get("length") and r.get("width") and r["width"] > r["length"] * 1.0001:
            warn("width > length: heat rejection is placed with the LONG side perpendicular to the facade")
        bands = [r.get(k) for k in ("lw_63", "lw_125", "lw_250", "lw_500", "lw_1k", "lw_2k", "lw_4k", "lw_8k")]
        if all(b is not None for b in bands) and "lw_dba" in r:
            # A-weighting per octave band (IEC 61672), summed logarithmically
            import math
            aw = (-26.2, -16.1, -8.6, -3.2, 0.0, 1.2, 1.0, -1.1)
            total = 10 * math.log10(sum(10 ** ((b + a) / 10) for b, a in zip(bands, aw)))
            if abs(total - r["lw_dba"]) > 2.0:
                warn(f"octave bands sum to {total:.1f} dB(A) but lw_dba is {r['lw_dba']:g}")
    if rtype == "genset" and "lw_dba" not in r and "lp_1m_dba" not in r:
        err("genset needs lw_dba or lp_1m_dba")
    if rtype == "genset" and "kva" in r and "kwe" in r:
        pf = r["kwe"] / r["kva"]
        if not 0.6 <= pf <= 1.0:
            err(f"kwe/kva = {pf:.2f}: expected about 0.8")
    if r.get("assumed"):
        warn(f"assumed, not from the source: {r['assumed']}")
    if for_approval:
        for k in ("source", "manufacturer", "model"):
            if not r.get(k):
                err(f"{k} is required to approve")
        if not r.get("source_date"):
            warn("source_date missing")
    return out


def errors(issues) -> list[str]:
    return [m for lvl, m in issues if lvl == "error"]
