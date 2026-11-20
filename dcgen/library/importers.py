"""Import manufacturer selection exports (CSV/XLSX) as DRAFT library records.

A mapping file (YAML) per manufacturer/export format says which source column becomes which field
and how to convert units. With no mapping the file is assumed to already use the template headers.
Imports never approve anything and never overwrite an existing record.

  name: Example selection export
  type: chiller                     # record type (the CLI --type can be left out)
  sheet: ACC                        # worksheet (the header row is found automatically)
  columns: {"Model": model, "Net capacity (MW)": capacity_kw, "Length (mm)": length}
  split: {"Fluid in/out": [rated_fluid_in_c, rated_fluid_out_c]}   # "53/45" -> two fields
  values: {manufacturer: {Stultz: Stulz}}                           # clean typos / synonyms
  numbers: {alternatives: max}      # "15530/16720" or "2532 - 2700" -> max | min | first; "a + b" always sums;
                                    # "1.18 MW" -> 1180 when the field is in kW
  units: {capacity_kw: {factor: 1000}, length: {factor: 0.001}, rated_ambient_c: {fahrenheit: true}}
  defaults: {manufacturer: Example, rated_ambient_c: 35, intake_clear: 3.0, row_clear: 3.5}
  derive:                           # only when the source has no value; no code is evaluated
    lw_dba: {log10: capacity_kw, ref: 1400, base: 92}               # base + 10 log10(capacity / ref)
    max_ambient_c: {add: rated_ambient_c, value: 5}
    model: {format: "{compressor} {capacity_kw:g} kW"}
  assumed: [lw_dba, max_ambient_c]  # flagged on every output when filled by defaults/derive
  caveats: {operating_kg: "source column is 'Weight' (dry or operating not stated)"}   # added to notes
  notes_from: "Link"                # a source column copied into notes
  id_pattern: "CH-{manufacturer}-{model}"
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .records import columns, parse_row
from .store import Library, read_csv
from .validate import errors, validate_record


@dataclass
class ImportReport:
    rows: int = 0
    added: int = 0
    skipped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)       # per-record errors/warnings (drafts are still saved)
    ids: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"rows_read": self.rows, "drafts_added": self.added, "skipped": self.skipped[:10],
                "problems": self.problems[:25], "problem_count": len(self.problems), "ids": self.ids[:50]}


def _header_index(rows: list[tuple]) -> int:
    """First row (of the first 15) with at least 3 text cells: vendor sheets often start lower down."""
    for i, r in enumerate(rows[:15]):
        if sum(1 for c in r if isinstance(c, str) and c.strip()) >= 3:
            return i
    return 0


def read_table(path: str | Path, sheet: Optional[str] = None) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return read_csv(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        if sheet and sheet not in wb.sheetnames:
            raise ValueError(f"No sheet '{sheet}' in {path.name}; sheets are {wb.sheetnames}")
        ws = wb[sheet] if sheet else wb.worksheets[0]
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        if not rows:
            return []
        h = _header_index(rows)
        head = [str(c).strip() if c is not None else "" for c in rows[h]]
        out = []
        for n, r in enumerate(rows[h + 1:], start=h + 2):
            if r is None or all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
                continue
            first = next((c for c in r if c is not None), "")
            if str(first).startswith("#"):
                continue
            row = {hd: ("" if c is None else c) for hd, c in zip(head, r) if hd}
            row["_row"] = n
            out.append(row)
        return out
    raise ValueError(f"Unsupported file type {path.suffix} (use .csv or .xlsx)")


def load_mapping(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


_ALT = {"max": max, "min": min, "first": lambda xs: xs[0]}


def parse_number(v, alternatives: str = "max", unit: str = ""):
    """Read the numbers vendor sheets actually contain. '11800 + 1250' -> 13050 (modules add up);
    '15530/16720/17910' and '2532 - 2700' -> alternatives (max by default); '1.18 MW' -> 1180 if unit is kW."""
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace(",", "")
    if not t:
        return None
    scale = 1.0
    low = t.lower()
    if unit == "kW" and low.endswith("mw"):
        scale, t = 1000.0, t[:-2]
    elif low.endswith(("kw", "mm", "kg")):
        t = t[:-2]
    t = t.strip()
    if "+" in t:
        parts = [_num(p) for p in t.split("+")]
        return None if any(p is None for p in parts) else sum(parts) * scale
    for sep in ("/", " - ", " to "):
        if sep in t:
            parts = [_num(p) for p in t.split(sep)]
            if all(p is not None for p in parts) and parts:
                return _ALT[alternatives](parts) * scale
            return None
    n = _num(t)
    return None if n is None else n * scale


class _Blank(str):
    def __format__(self, spec):           # a missing field formats as "" whatever the format spec
        return ""


class _Fields(dict):
    def __missing__(self, key):
        return _Blank()


def _derive(op: dict, rec: dict):
    try:
        if "log10" in op:
            import math
            return round(op.get("base", 0.0) + 10 * math.log10(float(rec[op["log10"]]) / float(op.get("ref", 1.0))),
                         op.get("round", 1))
        if "add" in op:
            return float(rec[op["add"]]) + float(op.get("value", 0.0))
        if "format" in op:
            return " ".join(op["format"].format_map(_Fields(rec)).split())
        if "value" in op:
            return op["value"]
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        return None
    return None


def apply_mapping(row: dict, mapping: Optional[dict], rtype: Optional[str] = None) -> dict:
    """Source row -> record fields (strings or numbers; parse_row does the final typing).
    Order: columns, split, values, numbers, units, defaults, derive; then assumed/caveat flags."""
    if not mapping:
        return {k: v for k, v in row.items() if not k.startswith("_")}
    from .records import spec
    fspec = {f.name: f for f in spec(rtype)} if rtype else {}
    out: dict = {}
    for src, dst in mapping.get("columns", {}).items():
        if src in row and row[src] not in (None, ""):
            out[dst] = row[src]
    for src, dsts in mapping.get("split", {}).items():
        v = row.get(src)
        if v in (None, ""):
            continue
        parts = [p.strip() for p in str(v).replace("\\", "/").split("/")]
        for d, p in zip(dsts, parts):
            if p:
                out[d] = p
    for field_, table in mapping.get("values", {}).items():
        if field_ in out:
            key = str(out[field_]).strip()
            out[field_] = table.get(key, key)
    alt = mapping.get("numbers", {}).get("alternatives", "max")
    for k, v in list(out.items()):
        f = fspec.get(k)
        if f is not None and f.dtype == "float" and isinstance(v, str):
            n = parse_number(v, alt, f.unit)
            if n is not None:
                out[k] = n
    for field_, op in mapping.get("units", {}).items():
        v = _num(out.get(field_)) if not isinstance(out.get(field_), (int, float)) else out.get(field_)
        if v is None:
            continue
        if op.get("fahrenheit"):
            v = (v - 32) * 5 / 9
        v = v * op.get("factor", 1.0) + op.get("offset", 0.0)
        out[field_] = round(v, 6)
    filled = set()
    for k, v in mapping.get("defaults", {}).items():
        if out.get(k) in (None, ""):
            out[k] = v
            filled.add(k)
    for k, op in mapping.get("derive", {}).items():
        if out.get(k) in (None, ""):
            val = _derive(op, out)
            if val not in (None, ""):
                out[k] = val
                filled.add(k)
    flags = [k for k in mapping.get("assumed", []) if k in filled or k in mapping.get("always_assumed", [])]
    flags += [k for k in mapping.get("always_assumed", []) if k not in flags]
    if flags:
        out["assumed"] = ";".join(flags)
    notes = []
    if mapping.get("notes_from") and row.get(mapping["notes_from"]) not in (None, ""):
        notes.append(str(row[mapping["notes_from"]]).strip())
    notes += [f"{k}: {txt}" for k, txt in mapping.get("caveats", {}).items() if k in out]
    if notes:
        out["notes"] = " | ".join(notes)
    if "_row" in row:
        out["_row"] = row["_row"]
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").upper()


def import_file(path: str | Path, rtype: Optional[str], pack_dir: str | Path, mapping: Optional[dict] = None,
                entered_by: str = "", source: str = "", sheet: Optional[str] = None,
                lib: Optional[Library] = None) -> ImportReport:
    rep = ImportReport()
    rtype = rtype or (mapping or {}).get("type")
    if not rtype:
        raise ValueError("Record type needed: pass --type or put 'type:' in the mapping")
    sheet = sheet or (mapping or {}).get("sheet")
    raw = read_table(path, sheet)
    rep.rows = len(raw)
    pattern = (mapping or {}).get("id_pattern", "{manufacturer}-{model}")
    src_name = source or Path(path).name
    recs, seen = [], {}
    for n, row in enumerate(raw, start=2):
        n = row.get("_row", n)
        mapped = apply_mapping(row, mapping, rtype)
        mapped.pop("_row", None)
        rec, issues = parse_row(rtype, mapped)
        rep.problems += [f"row {n}: {m}" for m in issues if not m.startswith("unknown column")]
        if not rec.get("record_id"):
            try:
                rec["record_id"] = _slug(pattern.format(**{**rec, "type": rtype, "row": n}))
            except (KeyError, ValueError) as exc:
                rep.problems.append(f"row {n}: cannot build record_id, missing {exc}")
                continue
        rec["status"] = "draft"
        rec.setdefault("source", f"{src_name}" + (f" / {sheet} row {n}" if sheet else f" row {n}"))
        rec.setdefault("source_date", dt.date.today().isoformat())
        if entered_by:
            rec.setdefault("entered_by", entered_by)
        # the same selection listed twice: keep one; a variant with the same key gets a suffix
        body = {k: v for k, v in rec.items() if k not in ("record_id", "source")}
        base = rec["record_id"]
        same = seen.setdefault(base, [])
        dup = next((rid for rid, b in same if b == body), None)
        if dup:
            rep.skipped.append(f"row {n}: duplicate of {dup} - skipped")
            continue
        if same:
            rec["record_id"] = f"{base}-{len(same) + 1}"
        same.append((rec["record_id"], body))
        rid = rec["record_id"]
        for lvl, msg in validate_record(rtype, rec):
            if not msg.startswith("assumed, not from the source"):
                rep.problems.append(f"{rid}: {lvl}: {msg}")
        recs.append(rec)
    lib = lib or Library([pack_dir])
    added, skipped = lib.add_drafts(rtype, recs, Path(pack_dir))
    rep.added, rep.skipped = added, rep.skipped + skipped
    rep.ids = [r["record_id"] for r in recs]
    return rep


def import_with_mappings(path: str | Path, mapping_dir: str | Path, pack_dir: str | Path, entered_by: str = "",
                         lib: Optional[Library] = None) -> dict[str, ImportReport]:
    """One workbook, several sheets: import with every *.yaml mapping in a folder (each names its type and sheet)."""
    out = {}
    for mp in sorted(Path(mapping_dir).glob("*.yaml")):
        m = load_mapping(mp)
        out[mp.stem] = import_file(path, m.get("type"), pack_dir, m, entered_by, lib=lib)
    return out
