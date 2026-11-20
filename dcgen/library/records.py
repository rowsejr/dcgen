"""Record types for the plant library: field specs, parsing, units, mapping to catalogue entries.

Spec-driven (no class per type) so a new equipment type is one entry in TYPES plus a catalogue
section name. Every record carries provenance; the engines only ever see the catalogue-shaped dict
produced by `to_catalogue`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

STATUSES = ("draft", "reviewed", "approved", "superseded")
HR_TYPES = ("air_cooled_chiller", "dry_cooler", "adiabatic_dry_cooler")


@dataclass(frozen=True)
class F:
    name: str
    dtype: str = "float"            # str | float | int | list_float | json | date | enum | bool
    unit: str = ""
    required: bool = False
    desc: str = ""
    example: Any = None
    enum: tuple = ()


PROVENANCE = [
    F("record_id", "str", required=True, desc="Unique, stable id, e.g. CH-TRN-CXAF-1400", example="EXAMPLE-DELETE-ME"),
    F("manufacturer", "str", required=True, example="Example Manufacturer"),
    F("model", "str", required=True, example="EX-1400"),
    F("status", "enum", enum=STATUSES, desc="Imports always arrive as draft; only `dcgen library approve` sets approved", example="draft"),
    F("source", "str", required=True, desc="Selection output / datasheet / reference, with revision", example="Selection run 2026-11-01 rev A.pdf"),
    F("source_date", "date", desc="YYYY-MM-DD of the source document", example="2026-11-01"),
    F("conditions", "str", desc="Selection conditions: ambient, water temps, glycol, altitude, fouling", example="35C amb, 18/24C CHW, 30% glycol"),
    F("entered_by", "str", example="initials"),
    F("checked_by", "str", desc="Engineer who checked it (set on approval)"),
    F("checked_date", "date"),
    F("notes", "str"),
]

DIMS = [
    F("length", "float", "m", True, "Long dimension. Heat rejection stands with this perpendicular to the facade", 12.0),
    F("width", "float", "m", True, example=2.3),
    F("height", "float", "m", True, example=2.6),
]

TYPES: dict[str, dict] = {
    "chiller": {
        "section": "heat_rejection", "file": "chillers", "unit_type": "air_cooled_chiller",
        "title": "Air-cooled chillers (free-cooling or not)",
        "fields": PROVENANCE + [
            F("capacity_kw", "float", "kW", True, "Cooling capacity at the rated conditions", 1400),
            F("rated_ambient_c", "float", "degC", True, "Ambient at which capacity_kw applies", 35),
            F("max_ambient_c", "float", "degC", True, "Highest ambient the unit runs at", 48),
            F("derate_per_k", "float", "1/K", False, "Linear capacity loss per K above rated ambient (use this OR a curve)", 0.012),
            F("curve_ambient_c", "list_float", "degC", False, "Capacity curve x: ascending ambient, ';' separated", "25;35;40;45"),
            F("curve_capacity_kw", "list_float", "kW", False, "Capacity curve y: same length as curve_ambient_c", "1500;1400;1250;1050"),
            F("chw_supply_c", "float", "degC"), F("chw_return_c", "float", "degC"), F("glycol_pct", "float", "%"),
            F("altitude_m", "float", "m"), F("power_kw", "float", "kW", False, "Input power at rated duty"),
            *DIMS,
            F("intake_clear", "float", "m", True, "Gap between long sides of adjacent units (air intake)", 3.0),
            F("row_clear", "float", "m", True, "End-to-end gap between rows (discharge, maintenance)", 3.5),
            F("lw_dba", "float", "dB(A)", True, "Total sound power level", 92),
            F("lw_63", "float", "dB"), F("lw_125", "float", "dB"), F("lw_250", "float", "dB"), F("lw_500", "float", "dB"),
            F("lw_1k", "float", "dB"), F("lw_2k", "float", "dB"), F("lw_4k", "float", "dB"), F("lw_8k", "float", "dB"),
            F("operating_kg", "float", "kg", False, "Operating weight (roof load check)", 13000),
        ],
    },
    "dry_cooler": {
        "section": "heat_rejection", "file": "dry_coolers", "unit_type": "dry_cooler",
        "title": "Dry coolers (warm-water / liquid-cooled halls)",
        "fields": PROVENANCE + [
            F("capacity_kw", "float", "kW", True, "Capacity at the rated ambient and fluid-in temperature", 1100),
            F("rated_ambient_c", "float", "degC", True, example=35), F("rated_fluid_in_c", "float", "degC", True, example=45),
            F("max_ambient_c", "float", "degC", True, example=42),
            F("curve_ambient_c", "list_float", "degC"), F("curve_capacity_kw", "list_float", "kW"),
            F("curves_json", "json", "", False, "2-D curves: [{\"fluid_in_c\":45,\"ambient_c\":[..],\"capacity_kw\":[..]}, ...]"),
            F("fluid_glycol_pct", "float", "%"), F("altitude_m", "float", "m"), F("power_kw", "float", "kW"),
            *DIMS,
            F("intake_clear", "float", "m", True, example=3.0), F("row_clear", "float", "m", True, example=3.5),
            F("lw_dba", "float", "dB(A)", True, example=88),
            F("lw_63", "float", "dB"), F("lw_125", "float", "dB"), F("lw_250", "float", "dB"), F("lw_500", "float", "dB"),
            F("lw_1k", "float", "dB"), F("lw_2k", "float", "dB"), F("lw_4k", "float", "dB"), F("lw_8k", "float", "dB"),
            F("operating_kg", "float", "kg"),
        ],
    },
    "adiabatic_cooler": {
        "section": "heat_rejection", "file": "adiabatic_coolers", "unit_type": "adiabatic_dry_cooler",
        "title": "Adiabatic dry coolers",
        "fields": PROVENANCE + [
            F("capacity_kw", "float", "kW", True, example=1300), F("rated_ambient_c", "float", "degC", True, example=35),
            F("rated_fluid_in_c", "float", "degC", True, example=40), F("max_ambient_c", "float", "degC", True, example=46),
            F("adiabatic_effectiveness", "float", "-", True, "Wet-bulb depression achieved, 0..1", 0.75),
            F("curve_ambient_c", "list_float", "degC"), F("curve_capacity_kw", "list_float", "kW"),
            F("curves_json", "json"), F("water_l_per_h", "float", "l/h", False, "Peak adiabatic water use"),
            *DIMS,
            F("intake_clear", "float", "m", True, example=3.0), F("row_clear", "float", "m", True, example=3.5),
            F("lw_dba", "float", "dB(A)", True, example=89), F("operating_kg", "float", "kg"),
        ],
    },
    "genset": {
        "section": "gensets", "file": "gensets", "title": "Standby generator sets (containerised)",
        "fields": PROVENANCE + [
            F("kva", "float", "kVA", True, "Standby rating", 3000), F("kwe", "float", "kWe", True, "Standby kW", 2400),
            *DIMS,
            F("side_clear", "float", "m", True, "Gap between adjacent sets", 1.5),
            F("end_clear", "float", "m", True, "Maintenance / fuel aisle at the outer end", 4.0),
            F("lw_dba", "float", "dB(A)", True, "Enclosure sound power", 104),
            F("fuel_l_per_kwh", "float", "l/kWh", True, "At the design load", 0.26),
            F("emissions", "str", desc="e.g. Stage V, TA-Luft, EPA T2"), F("exhaust_height_m", "float", "m"),
        ],
    },
    "transformer": {
        "section": "transformers", "file": "transformers", "title": "Outdoor transformers",
        "fields": PROVENANCE + [
            F("kva", "float", "kVA", True, example=3150),
            *DIMS,
            F("clear", "float", "m", True, "Clearance each side (fire separation / maintenance)", 1.5),
            F("impedance_pct", "float", "%"), F("no_load_loss_kw", "float", "kW"), F("load_loss_kw", "float", "kW"),
            F("cooling", "str", desc="ONAN, KNAN, dry-type..."), F("oil_litres", "float", "l"),
        ],
    },
    "elec_room_typical": {
        "section": "electrical_rooms", "file": "elec_room_typicals", "title": "Electrical room typicals (UPS + batteries + LV per block)",
        "fields": PROVENANCE + [
            F("block_kw", "float", "kW", True, "UPS block rating (IT kW)", 2000),
            F("room_width_m", "float", "m", True, "Room width along the facade for one block", 12.0),
            F("room_depth_m", "float", "m", False, "Informational; the building depth comes from the catalogue", 16.0),
            F("battery_type", "str", example="VRLA / Li-ion"), F("reference", "str", desc="Reference design id this typical comes from"),
        ],
    },
}


def spec(rtype: str) -> list[F]:
    return TYPES[rtype]["fields"]


def columns(rtype: str) -> list[str]:
    return [f.name for f in spec(rtype)]


# ---------------------------------------------------------------- parsing
_NUM = re.compile(r"^\s*-?\d+(\.\d+)?([eE]-?\d+)?\s*$")


def _coerce(f: F, raw: Any) -> Any:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if f.dtype in ("str", "date"):
        return str(raw).strip()
    if f.dtype == "enum":
        v = str(raw).strip().lower()
        if f.enum and v not in f.enum:
            raise ValueError(f"{f.name}: '{raw}' not one of {list(f.enum)}")
        return v
    if f.dtype == "float":
        if isinstance(raw, (int, float)):
            return float(raw)
        s = str(raw).replace(",", "").strip()
        if not _NUM.match(s):
            raise ValueError(f"{f.name}: '{raw}' is not a number")
        return float(s)
    if f.dtype == "int":
        return int(round(float(raw)))
    if f.dtype == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "y")
    if f.dtype == "list_float":
        if isinstance(raw, (list, tuple)):
            return [float(x) for x in raw]
        return [float(x) for x in str(raw).replace(",", ";").split(";") if x.strip()]
    if f.dtype == "json":
        return raw if isinstance(raw, (list, dict)) else json.loads(str(raw))
    raise ValueError(f"unknown dtype {f.dtype}")


def parse_row(rtype: str, row: dict[str, Any]) -> tuple[dict, list[str]]:
    """Coerce a raw row (CSV/XLSX strings) into a typed record. Returns (record, issues)."""
    rec, issues = {}, []
    known = {f.name: f for f in spec(rtype)}
    for k, v in row.items():
        if k not in known:
            if v not in (None, ""):
                issues.append(f"unknown column '{k}' ignored")
            continue
        try:
            val = _coerce(known[k], v)
        except (ValueError, json.JSONDecodeError) as exc:
            issues.append(str(exc))
            continue
        if val is not None:
            rec[k] = val
    return rec, issues


def to_row(rtype: str, rec: dict) -> dict[str, str]:
    """Typed record -> CSV strings (round-trips with parse_row)."""
    out = {}
    for f in spec(rtype):
        v = rec.get(f.name)
        if v is None:
            out[f.name] = ""
        elif f.dtype == "list_float":
            out[f.name] = ";".join(f"{x:g}" for x in v)
        elif f.dtype == "json":
            out[f.name] = json.dumps(v, separators=(",", ":"))
        elif f.dtype == "float":
            out[f.name] = f"{v:g}"
        else:
            out[f.name] = str(v)
    return out


# ---------------------------------------------------------------- catalogue shape
def label_of(rtype: str, r: dict) -> str:
    kind = {"chiller": "chiller", "dry_cooler": "dry cooler", "adiabatic_cooler": "adiabatic cooler",
            "genset": "genset", "transformer": "transformer", "elec_room_typical": "electrical room"}[rtype]
    size = ""
    if "capacity_kw" in r:
        size = f" {r['capacity_kw']:g} kW"
    elif "kva" in r:
        size = f" {r['kva']:g} kVA"
    elif "block_kw" in r:
        size = f" {r['block_kw']:g} kW block"
    return f"{r.get('manufacturer', '')} {r.get('model', '')} {kind}{size}".replace("  ", " ").strip()


_ENGINE_FIELDS = {
    "heat_rejection": ("capacity_kw", "rated_ambient_c", "max_ambient_c", "derate_per_k", "rated_fluid_in_c",
                       "adiabatic_effectiveness", "length", "width", "height", "intake_clear", "row_clear", "lw_dba",
                       "operating_kg", "power_kw"),
    "gensets": ("kva", "kwe", "length", "width", "height", "side_clear", "end_clear", "lw_dba", "fuel_l_per_kwh"),
    "transformers": ("kva", "length", "width", "height", "clear"),
}


def to_catalogue(rtype: str, r: dict) -> dict:
    """Record -> the dict shape the engines read (same keys as catalogue.yaml entries) + provenance."""
    section = TYPES[rtype]["section"]
    if section == "electrical_rooms":
        return {"kw": r["block_kw"], "width": r["room_width_m"]}
    e = {k: r[k] for k in _ENGINE_FIELDS[section] if k in r}
    if section == "heat_rejection":
        e["type"] = TYPES[rtype]["unit_type"]
        if r.get("curve_ambient_c") and r.get("curve_capacity_kw"):
            e["curve"] = {"ambient_c": r["curve_ambient_c"], "capacity_kw": r["curve_capacity_kw"]}
        if r.get("curves_json"):
            e["curves"] = r["curves_json"]
    e["label"] = label_of(rtype, r)
    for k in ("record_id", "manufacturer", "model", "status", "source"):
        if k in r:
            e[k] = r[k]
    return e
