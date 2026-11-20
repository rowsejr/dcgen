---
name: plant-data-import
description: Turn a vendor or in-house plant spreadsheet (chillers, dry coolers, gensets, transformers) into draft dcgen library records by writing an import mapping, importing and validating. Use when someone has selection data, a comparison sheet or datasheets to add to the plant library.
---
# Plant data import

Imports only ever create **draft** records. An engineer checks them against the source documents and approves them with
`python -m dcgen.cli library approve <ids> --checked-by "<name>"`. Never run the approve command yourself.

1. Look at the spreadsheet: sheet names, the header row, units (mm or m, kW or MW, °F), and values such as
   "15530/16720", "11800 + 1250" or "1.18 MW".
2. List the record fields for the type: `python -m dcgen.cli library docs`.
3. Write a mapping per sheet in `packs/<pack>/mappings/<name>.yaml` (schema `schemas/mapping.schema.json`; see
   `packs/cundall/mappings/qc_acc.yaml` for a worked example). Map only what the source has. Fill required fields it
   lacks with `defaults` or `derive`, and list them in `assumed` (EER with no data goes in `always_assumed: [eer]`).
4. Import and validate:
   ```
   python -m dcgen.cli library import <file.xlsx> --pack packs/<pack>/plant --mapping packs/<pack>/mappings/
   python -m dcgen.cli library validate --packs packs/<pack>
   ```
5. Report the counts, every validation error (records with errors never reach the engines), and the suspicious values
   you noticed. Put them in `packs/<pack>/README.md` under "Data issues found on import".
6. To try the drafts in a project, set `use_drafts: true` in its `brief.yaml`. Outputs then carry a data-status warning.

Keep client names, project names and file-share links out of the records and the README.
