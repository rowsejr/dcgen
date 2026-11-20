#!/usr/bin/env python3
"""Submit datasheet PDFs for draft extraction through the Message Batches API. UNTESTED against the live API.

  python scripts/batch_extract.py submit --type chiller --pdfs datasheets/*.pdf        # prints batch id
  python scripts/batch_extract.py collect --type chiller --batch msgbatch_... --pack packs/plant

Needs: pip install anthropic; ANTHROPIC_API_KEY (the work key); poppler's pdftotext on PATH.
DCGEN_EXTRACT_MODEL overrides the model (default claude-haiku-5-5: table extraction does not need a large model).
Drafts land in <pack>/<type>.csv; an engineer checks each against the PDF, then `dcgen library approve`.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dcgen.library.ai_extract import batch_request, parse_result
from dcgen.library.importers import import_file
from dcgen.library.records import columns, to_row
from dcgen.library.store import write_csv

MODEL = os.environ.get("DCGEN_EXTRACT_MODEL", "claude-haiku-5-5")


def pdf_text(path: str) -> str:
    return subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True).stdout


def submit(a) -> None:
    import anthropic
    client = anthropic.Anthropic()
    reqs = [batch_request(Path(p).stem[:60], pdf_text(p), a.type, MODEL) for p in a.pdfs]
    batch = client.messages.batches.create(requests=reqs)
    print(batch.id, len(reqs), "requests")


def collect(a) -> None:
    import anthropic
    client = anthropic.Anthropic()
    rows, errs = [], []
    for r in client.messages.batches.results(a.batch):
        if r.result.type != "succeeded":
            errs.append(f"{r.custom_id}: {r.result.type}")
            continue
        try:
            rec = parse_result(r.result.message.model_dump())
        except ValueError as exc:
            errs.append(f"{r.custom_id}: {exc}")
            continue
        rec.setdefault("source", r.custom_id)
        rows.append(to_row(a.type, rec))
    tmp = Path(a.pack) / f"_extracted_{a.type}.csv"
    import csv
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns(a.type))
        w.writeheader()
        w.writerows(rows)
    rep = import_file(tmp, a.type, a.pack, entered_by="batch-extract")
    tmp.unlink()
    print(json.dumps(rep.summary(), indent=1))
    for e in errs:
        print("ERROR", e, file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit"); s.add_argument("--type", required=True); s.add_argument("--pdfs", nargs="+", required=True); s.set_defaults(fn=submit)
    c = sub.add_parser("collect"); c.add_argument("--type", required=True); c.add_argument("--batch", required=True); c.add_argument("--pack", required=True); c.set_defaults(fn=collect)
    a = ap.parse_args()
    a.fn(a)
