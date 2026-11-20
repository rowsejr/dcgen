# dcgen: Claude Code project notes

Data centre RIBA Stage 2 test-fit generator. A deterministic Python engine; the web form,
REST API, MCP server and CLI are thin front doors onto `dcgen/service.py`.

## Commands
- Install: `pip install -e ".[all]"`
- Fast check after engine edits: `pytest -q tests/test_engines.py` (about 1 s)
- Full suite: `pytest -q` (about 20 s)
- Example run, no AI: `dcgen run --brief examples/brief.yaml --site examples/site.yaml --out out --formats json`
- Web app + REST + MCP: `uvicorn dcgen.api:app --reload`, then http://localhost:8000 (OpenAPI at /docs)
- MCP over stdio (agent testing only): `dcgen-mcp`

## Rules (do not break)
1. Engines in `dcgen/engines/` are pure and deterministic: no I/O, no randomness, no LLM calls.
2. Plant data lives only in `dcgen/data/catalogue.yaml`. Never hard-code sizes, clearances, sound
   power or derating in code; add a catalogue key. Catalogue changes need engineer sign-off.
3. `Scene` in `dcgen/schema.py` is the single source of truth. Front doors change it only through
   `service.py`; exporters only read it.
4. New behaviour goes in `service.py` or below, never in `api.py`, `mcp_server.py` or `cli.py`.
5. Keep API/tool results compact (summaries, non-passing checks). Full scenes go to files or the
   app-only `editor_scene` tool.
6. Every engine or rule change needs a test in `tests/`. Golden metrics in `tests/test_layout.py`
   change only on purpose, with the reason in the commit message.

## Map
- `engines/`: halls, topology (xMy, catcher, 2N), electrical, cooling, noise, core (building), site
- `layout.py` option search and scoring; `checks.py` rule checks; `service.py` project state/actions
- `render/` PDF sheets (`axo.py` = vector 3D); `export/` IFC, DXF, GLB, `editor.html`
- `web/index.html` no-AI form; `copilot/` Copilot Studio agent instructions; `revit/` pyRevit button

## Keep token use down
- Verify with pytest and the CLI with `--formats json`; render PDFs only when `render/` changed.
- Do not read generated files in full (`examples/output/*`, scene `*.json`, `*.ifc`); inspect
  specific fields with a short `python -c`.
- Small, test-first changes; one task per session.

# Compact instructions
When compacting, keep failing test names, files changed and open TODOs; drop tool output.
