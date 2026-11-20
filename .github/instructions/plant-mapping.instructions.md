---
name: dcgen import mappings
description: How to write a mapping that turns a vendor spreadsheet into draft plant records
applyTo: "packs/**/mappings/*.yaml"
---
# Import mappings

A mapping says how one sheet of a vendor spreadsheet becomes draft records (schema `schemas/mapping.schema.json`,
full description in `docs/LIBRARY.md`). Keys: `type`, `sheet`, `columns` (source header -> field), `split` ("53/45" -> two
fields), `values` (clean typos), `numbers.alternatives` (how "a/b" options and "a - b" ranges are read; "a + b" sums),
`units` (`factor`, `offset`, `fahrenheit`), `defaults`, `derive` (`log10`, `add`, `format`; no code), `assumed` and
`always_assumed` (fields flagged as assumptions), `caveats`, `notes_from`, `id_pattern`.

Field names come from `python -m dcgen.cli library docs`. Anything not in the source must be either left out or filled
by `defaults`/`derive` and listed in `assumed`, so the approver can see it. Imports only ever create drafts.
