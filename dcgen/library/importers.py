"""Import manufacturer selection exports (CSV/XLSX) as DRAFT library records.

A mapping file (YAML) per manufacturer/export format says which source column becomes which field
and how to convert units. With no mapping the file is assumed to already use the template headers.
Imports never approve anything and never overwrite an existing record.

  name: Example selection export
  type: chiller
  columns: {"Model": model, "Net capacity (MW)": capacity_kw, "Length (mm)": length}
  units: {capacity_kw: {factor: 1000}, length: {factor: 0.001}, rated_ambient_c: {fahrenheit: true}}
  defaults: {manufacturer: Example, rated_ambient_c: 35, intake_clear: 3.0, row_clear: 3.5}
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


def read_table(path: str | Path, sheet: Optional[str] = None) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return read_csv(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        head = [str(h).strip() if h is not None else "" for h in rows[0]]
        out = []
        for r in rows[1:]:
            if r is None or all(c is None for c in r) or str(r[0] or "").startswith("#"):
                continue
            out.append({h: ("" if c is None else c) for h, c in zip(head, r) if h})
        return out
    raise ValueError(f"Unsupported file type {path.suffix} (use .csv or .xlsx)")


def load_mapping(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def apply_mapping(row: dict, mapping: Optional[dict]) -> dict:
    if not mapping:
        return dict(row)
    out = dict(mapping.get("defaults", {}))
    for src, dst in mapping.get("columns", {}).items():
        if src in row and row[src] not in (None, ""):
            out[dst] = row[src]
    for field_, op in mapping.get("units", {}).items():
        v = _num(out.get(field_))
        if v is None:
            continue
        if op.get("fahrenheit"):
            v = (v - 32) * 5 / 9
        v = v * op.get("factor", 1.0) + op.get("offset", 0.0)
        out[field_] = round(v, 6)
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").upper()


def import_file(path: str | Path, rtype: str, pack_dir: str | Path, mapping: Optional[dict] = None,
                entered_by: str = "", source: str = "", sheet: Optional[str] = None,
                lib: Optional[Library] = None) -> ImportReport:
    rep = ImportReport()
    raw = read_table(path, sheet)
    rep.rows = len(raw)
    pattern = (mapping or {}).get("id_pattern", "{manufacturer}-{model}")
    recs = []
    for n, row in enumerate(raw, start=2):
        mapped = apply_mapping(row, mapping)
        rec, issues = parse_row(rtype, mapped)
        rep.problems += [f"row {n}: {m}" for m in issues if not m.startswith("unknown column")]
        if not rec.get("record_id"):
            try:
                rec["record_id"] = _slug(pattern.format(**{**rec, "type": rtype}))
            except KeyError as exc:
                rep.problems.append(f"row {n}: cannot build record_id, missing {exc}")
                continue
        rec["status"] = "draft"
        rec.setdefault("source", source or Path(path).name)
        rec.setdefault("source_date", dt.date.today().isoformat())
        if entered_by:
            rec.setdefault("entered_by", entered_by)
        for lvl, msg in validate_record(rtype, rec):
            rep.problems.append(f"{rec['record_id']}: {lvl}: {msg}")
        recs.append(rec)
    lib = lib or Library([pack_dir])
    rep.added, rep.skipped = lib.add_drafts(rtype, recs, Path(pack_dir))
    rep.ids = [r["record_id"] for r in recs]
    return rep
