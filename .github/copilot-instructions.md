# dcgen: instructions for GitHub Copilot

dcgen is a deterministic Python generator for RIBA Stage 2 data centre test fits: data halls, electrical plant by
redundancy topology, heat rejection, peak PUE and LV lineups, and a site layout with roads and ancillaries. It does not
use any AI. Your job is to write and edit its YAML inputs, run it, read its summary, and explain the results. Every
number in the results comes from dcgen. Never invent or adjust plant data yourself.

## Project folders
- `projects/<name>/brief.yaml`: client requirements and design parameters (schema `schemas/brief.schema.json`).
- `projects/<name>/site.yaml`: redline, access, grid connection and noise receptors (schema `schemas/site.schema.json`).
- `projects/<name>/README.md`: decisions and assumptions. Add a line whenever you change an input and say why.
- `projects/<name>/out/summary.md`: the result of the last run. Read it before proposing changes.

## Commands (run them in the terminal from the repository root)
- Start a project: `python -m dcgen.cli project new <name>`
- Check the YAML (fast, no layout): `python -m dcgen.cli project check projects/<name>`. Fix every `error` line before running.
- Run: `python -m dcgen.cli project run projects/<name>` (about 10 s). Add `--formats json,pdf,ifc,dxf,glb,editor` for the deliverables.
- Peak PUE and lineup check only: `python -m dcgen.cli project peak projects/<name>`
- People can do the same with VS Code tasks (Terminal > Run Task > "dcgen: ...").

## Rules
- Use only keys that exist in the schemas. Unknown keys are errors. Units are in the key names: `_mw`, `_kw`, `_c`, `_m`, `_dba`, `_k`.
- Leave a value at its default unless the brief says otherwise. List every assumption you make in the project README.
- Per-project changes to catalogue values go in `brief.yaml` under `overrides:` with dotted paths (for example
  `halls.corridor_depth: 5.0`). Never edit `dcgen/data/catalogue.yaml` or the CSV files under `packs/`. Engineers own those.
- Draft plant records only take part when `use_drafts: true`. Never approve library records: that is an engineer's job.
- Peak PUE is a design-point figure for sizing gensets, transformers, LV lineups, the intake and fuel. Never present it as an
  annual PUE (that comes from the separate PUE tool).
- Catalogue values are placeholders until the team signs them off. Noise is a screening estimate. Say both when you report.
- Keep `site.yaml` coordinates in the project grid (metres). If the user has a DXF, GeoJSON or KML redline, save it next to
  `site.yaml` and set `redline_file:` instead of typing coordinates.

## Skills
- `new-test-fit`: client brief text to a project folder.
- `iterate-test-fit`: fix failing checks or explore options from `out/summary.md`.
- `plant-data-import`: vendor spreadsheet to draft plant records through a mapping.
