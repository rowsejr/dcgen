# Plant library, client profiles, reference designs

Data that is too big for the catalogue (hundreds of selections, many clients, past schemes) lives in **packs**: plain folders
of CSV / YAML that sit in git or on a shared drive, outside the code.

```
my-pack/
  plant/       chillers.csv  dry_coolers.csv  adiabatic_coolers.csv  gensets.csv  transformers.csv  elec_room_typicals.csv
  clients/<id>/profile.yaml
  reference/<id>.yaml
```
Point the tool at packs with `DCGEN_PACKS` (os.pathsep separated) or `--packs`. `examples/pack/` is a **synthetic** pack that
shows the shape; it is not manufacturer data.

## Plant records
`dcgen library init <folder>` writes empty CSV templates (plus an .xlsx and FIELDS.md); `dcgen library docs` prints every
field, unit and valid range. Each record carries provenance (source, date, entered_by) and a status:

draft -> reviewed -> approved -> superseded

* **Imports always arrive as draft** (`dcgen library import FILE --type chiller --pack my-pack/plant --mapping MAP`).
  Mappings convert units (factor, fahrenheit, offset) from vendor spreadsheets. Optional AI extraction (`library/ai_extract.py`,
  `scripts/batch_extract.py`, untested) turns PDFs into **drafts only**.
* `dcgen library validate` flags out-of-range or inconsistent records. Invalid records never reach the engines.
* `dcgen library approve ID1,ID2 --checked-by "Name"` is a human step: it needs a clean validation and a named checker.
  It is deliberately not an MCP or REST tool.
* Only approved records are used unless `brief.use_drafts` is true; drafts then show as `data.status` WARN on every output.
* Where a section has library records they **replace** the placeholder catalogue entries for that section.

Effective catalogue per project: base `catalogue.yaml` -> library records -> client profile overrides -> `brief.overrides`.

## Client profiles
`clients/<id>/profile.yaml`: `brief_defaults`, `catalogue_overrides`, `approved.manufacturers`, and `rules`, each with a clause
reference. Check types: metric, catalogue, count, element_prop, min_distance, max_distance, noise_margin, manual (needs a human).
Results appear as `client.<id>` checks and as a compliance sheet (`compliance` tool, `/api/projects/{p}/compliance`, CSV).

## Reference designs
`reference/<id>.yaml`: the brief, per-MW KPIs, and `expected` engine output. Used two ways: **benchmark** (a layout whose
KPIs sit outside the range of similar cards by more than 25% raises `bench.outliers`) and **regression**
(`dcgen reference regress` re-runs each card's brief and diffs against `expected`). `expected` values in the example cards are
whatever this version of the engine produced.

## Giving it hundreds of records
Spreadsheet in, CSV drafts out, validate, engineer reviews the validation report and the source documents, approve in batches.
Keep the source datasheet reference in every record; that is what the approver checks against.
