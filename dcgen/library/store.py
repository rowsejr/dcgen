"""The plant library on disk: packs of CSV files, one file per equipment type.

  <pack>/chillers.csv, dry_coolers.csv, adiabatic_coolers.csv, gensets.csv, transformers.csv,
         elec_room_typicals.csv                      records (header = records.columns(type))
  <pack>/clients/<id>/profile.yaml                   client profiles (profiles.py)
  <pack>/reference/*.yaml                            reference design cards (reference.py)

Packs are normally separate Git repos (plant library, one per client, reference designs) pointed
to by DCGEN_PACKS (os.pathsep separated). Approval is a human step: `dcgen library approve`.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path
from typing import Iterable, Optional

from .records import TYPES, columns, parse_row, to_catalogue, to_row
from .validate import errors, validate_record

FILE_TO_TYPE = {v["file"]: k for k, v in TYPES.items()}


def pack_roots(paths: Optional[Iterable[str | Path]] = None) -> list[Path]:
    if paths is None:
        env = os.environ.get("DCGEN_PACKS", "")
        paths = [p for p in env.split(os.pathsep) if p]
    return [Path(p).expanduser() for p in paths]


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r and not str(next(iter(r.values())) or "").startswith("#")]


def write_csv(path: Path, rtype: str, recs: list[dict]) -> None:
    cols = columns(rtype)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in recs:
            w.writerow(to_row(rtype, r))


class Library:
    def __init__(self, roots: Optional[Iterable[str | Path]] = None):
        self.roots = pack_roots(roots)
        self.records: dict[str, dict[str, dict]] = {t: {} for t in TYPES}
        self.issues: list[str] = []
        self.load()

    # ---------------------------------------------------------------- loading
    def load(self) -> None:
        for t in self.records:
            self.records[t] = {}
        for root in self.roots:
            for path in sorted(root.rglob("*.csv")):
                rtype = FILE_TO_TYPE.get(path.stem)
                if rtype is None:
                    continue
                for n, row in enumerate(read_csv(path), start=2):
                    rec, issues = parse_row(rtype, row)
                    rid = rec.get("record_id")
                    if not rid or rid.upper().startswith("EXAMPLE"):
                        continue
                    for m in issues:
                        self.issues.append(f"{path.name}:{n} {rid}: {m}")
                    if rid in self.records[rtype]:
                        self.issues.append(f"{path.name}:{n} duplicate record_id {rid} (first one kept)")
                        continue
                    rec.setdefault("status", "draft")
                    rec["_file"] = str(path)
                    self.records[rtype][rid] = rec

    def __len__(self) -> int:
        return sum(len(v) for v in self.records.values())

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for t, recs in self.records.items():
            if recs:
                c: dict[str, int] = {}
                for r in recs.values():
                    c[r["status"]] = c.get(r["status"], 0) + 1
                out[t] = c
        return out

    def get(self, record_id: str) -> Optional[tuple[str, dict]]:
        for t, recs in self.records.items():
            if record_id in recs:
                return t, recs[record_id]
        return None

    # ---------------------------------------------------------------- search (compact rows for agents)
    def search(self, rtype: Optional[str] = None, query: str = "", limit: int = 20, include_drafts: bool = False,
               **ranges) -> list[dict]:
        """Case-insensitive substring match on manufacturer/model/id/label + numeric filters like
        capacity_kw_min=1200, lw_dba_max=90. Short rows only."""
        q = query.lower().split()
        rows = []
        for t, recs in self.records.items():
            if rtype and t != rtype:
                continue
            for rid, r in recs.items():
                if r["status"] not in ("approved", "reviewed") and not include_drafts:
                    continue
                if r["status"] == "superseded":
                    continue
                hay = f"{rid} {t} {r.get('manufacturer', '')} {r.get('model', '')}".lower().replace("_", " ")
                if q and not all(w in hay for w in q):
                    continue
                ok = True
                for k, v in ranges.items():
                    name, _, op = k.rpartition("_")
                    if op in ("min", "max") and name in r:
                        ok &= (r[name] >= v) if op == "min" else (r[name] <= v)
                    elif op in ("min", "max"):
                        ok = False
                if ok:
                    rows.append(self.brief_row(t, r))
        return rows[:limit]

    @staticmethod
    def brief_row(t: str, r: dict) -> dict:
        keys = {"chiller": ("capacity_kw", "lw_dba", "length", "width"), "dry_cooler": ("capacity_kw", "lw_dba", "length", "width"),
                "adiabatic_cooler": ("capacity_kw", "lw_dba", "length", "width"),
                "genset": ("kva", "kwe", "lw_dba", "length", "width"), "transformer": ("kva", "length", "width"),
                "elec_room_typical": ("block_kw", "room_width_m")}[t]
        return {"id": r["record_id"], "type": t, "name": f"{r.get('manufacturer', '')} {r.get('model', '')}".strip(),
                "status": r["status"], **{k: r[k] for k in keys if k in r}}

    # ---------------------------------------------------------------- catalogue sections
    def catalogue_sections(self, *, include_drafts: bool = False, manufacturers: Optional[list[str]] = None,
                           allow_ids: Optional[list[str]] = None, exclude_ids: Optional[list[str]] = None) -> dict:
        """Catalogue-shaped sections built from library records. Approved only unless include_drafts.
        Returned: {"heat_rejection": {id: entry}, "gensets": {...}, "transformers": {...}, "elec_table": [...]}"""
        ok_status = ("approved",) + (("reviewed", "draft") if include_drafts else ())
        mf = {m.lower() for m in manufacturers or []}
        out: dict = {"heat_rejection": {}, "gensets": {}, "transformers": {}, "elec_table": []}
        for t, recs in self.records.items():
            for rid, r in recs.items():
                if r["status"] not in ok_status or (exclude_ids and rid in exclude_ids):
                    continue
                if allow_ids and rid not in allow_ids:
                    continue
                if mf and t != "elec_room_typical" and r.get("manufacturer", "").lower() not in mf:
                    continue
                if validate_errors := errors(validate_record(t, r)):
                    self.issues.append(f"{rid} excluded: {validate_errors[0]}")
                    continue
                entry = to_catalogue(t, r)
                section = TYPES[t]["section"]
                if section == "electrical_rooms":
                    out["elec_table"].append(entry)
                else:
                    out[section][rid] = entry
        out["elec_table"].sort(key=lambda e: e["kw"])
        return out

    # ---------------------------------------------------------------- write / approve
    def add_drafts(self, rtype: str, recs: list[dict], pack_dir: Path) -> tuple[int, list[str]]:
        """Append draft records to <pack>/<type>.csv. Never overwrites an existing id."""
        path = Path(pack_dir) / f"{TYPES[rtype]['file']}.csv"
        existing = [parse_row(rtype, r)[0] for r in read_csv(path)] if path.exists() else []
        have = {r.get("record_id") for r in existing}
        skipped, added = [], 0
        for r in recs:
            if r["record_id"] in have:
                skipped.append(f"{r['record_id']} already exists - not overwritten")
                continue
            r = {k: v for k, v in r.items() if not k.startswith("_")}
            r["status"] = "draft"
            existing.append(r)
            added += 1
        write_csv(path, rtype, existing)
        self.load()
        return added, skipped

    def set_status(self, record_ids: list[str], status: str, checked_by: str = "") -> list[str]:
        """Change status in place. Approval requires validation without errors and a named checker."""
        msgs, by_file = [], {}
        for rid in record_ids:
            hit = self.get(rid)
            if hit is None:
                msgs.append(f"{rid}: not found")
                continue
            t, r = hit
            if status == "approved":
                if not checked_by:
                    msgs.append(f"{rid}: approval needs checked_by")
                    continue
                bad = errors(validate_record(t, r, for_approval=True))
                if bad:
                    msgs.append(f"{rid}: not approved - {bad[0]}")
                    continue
                r["checked_by"], r["checked_date"] = checked_by, dt.date.today().isoformat()
            r["status"] = status
            by_file.setdefault((t, r["_file"]), []).append(rid)
            msgs.append(f"{rid}: {status}")
        for (t, f), _ in by_file.items():
            recs = [{k: v for k, v in r.items() if not k.startswith("_")}
                    for r in self.records[t].values() if r["_file"] == f]
            write_csv(Path(f), t, recs)
        return msgs
