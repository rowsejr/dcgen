# dcgen: agentic data centre test-fit generator 

Brief + redline -> ranked RIBA Stage 2 layouts -> A3 PDF with plans and 3D views, IFC for
Revit/Navisworks, DXF, GLB, and a drag-and-drop editor. Driven by Claude through MCP.

> All catalogue values (plant sizes, clearances, sound power, derating) are **placeholders**.
> Replace them with checked practice data before using any output.

## Quick start

**VS Code + GitHub Copilot, no Claude:** open the folder, run the task *dcgen: install / update*, then *dcgen: new
project*, edit the YAML (or let Copilot write it), *dcgen: check this project* and Ctrl+Shift+B. See `docs/VSCODE.md`.

```bash
pip install -e ".[all]"                 # Python 3.11+
pytest -q                               # full suite, a few minutes; fast check: pytest -q tests/test_engines.py
dcgen run --brief examples/brief.yaml --site examples/site.yaml --out out
uvicorn dcgen.api:app --reload          # web form at http://localhost:8000, API docs at /docs, MCP at /mcp
```

Outputs in `out/`: `testfit_option_A.pdf` (7 A3 sheets), `.ifc`, `.dxf`, `.glb`, `.json`,
`_editor.html` (open in a browser and drag plant around). A sample run is in `examples/output/`.

## Front doors (all call `dcgen/service.py`; see docs/DEPLOYMENT.md)

* **Web form + REST API** (`dcgen/api.py`): anyone, no AI, no per-run cost. Host behind Entra ID.
* **MCP** (`/mcp` or `dcgen-mcp`): Microsoft 365 Copilot via Copilot Studio, Claude, GitHub Copilot.
* **CLI** (`dcgen run`): batch runs and scripting.

## Use it from Claude (MCP)

Claude Code picks up `.mcp.json` and `CLAUDE.md` automatically when started in the repo.
Claude Desktop (`claude_desktop_config.json`):
```json
{ "mcpServers": { "dcgen": { "command": "dcgen-mcp",
    "env": { "DCGEN_HOME": "C:\\Users\\you\\dcgen-projects" } } } }
```
Microsoft 365 Copilot: host the app, then add `https://<host>/mcp` as an MCP tool in a Copilot
Studio agent with the instructions in `copilot/agent-instructions.md`.

Then ask, for example: *"New project slough-b1: 36 MW, 6 MW halls, 30 kW racks, 5M4, design
ambient 38 C. Redline is in redline.dxf, main gate on the north boundary, nearest houses
200 m east with a 42 dB(A) limit. Give me the best options and the PDF."*

Tools: `create_project`, `set_site`, `update_brief`, `generate_options`, `select_option`,
`compare_topologies`, `get_scene`, `noise_at`, `export`, `open_editor` (renders the editor inside the chat as an MCP App
where the client supports it).
Peak PUE and lineups: `peak_pue` (design-point build-up: sizes gensets, transformers, LV lineups, intake and fuel; annual
PUE stays in the PUE tool). Chillers must fit the IT lineup's 600 kW mechanical allowance or get separate mechanical lineups.
Driving a layout: `preview` (PNG, flagged elements outlined), `suggest_fixes`, `what_if` (try changes without altering the
project), `regenerate` (keeps what you moved), `undo`, `move_element`, `move_compass` ("10 m north"), `move_towards`.
Data: `library_search`, `library_get`, `plant_options`, `list_profiles`, `apply_profile`, `compliance`, `reference_designs`.
Resources: `dcgen://catalogue`, `dcgen://brief-schema`. Agents cannot approve library records.

## Layout

```
dcgen/
  schema.py            Scene / Brief / SiteInput / Element  (single source of truth)
  data/catalogue.yaml  module library - engineers own this
  engines/             halls, topology, electrical, cooling, noise, core (building), egress, site
  library/             plant library, importers, client profiles, reference designs
  catalogue.py         effective catalogue: base + library + profile + brief overrides
  layout.py            orchestration, option search, scoring, edits
  checks.py            rule checks
  service.py           project state + actions shared by every front door
  workspace.py         file-based projects (projects/<name>/brief.yaml + site.yaml -> out/summary.md)
  api.py               web form + REST API + mounted MCP endpoint
  web/index.html       no-AI web form
  render/              A3 PDF sheets, vector axonometric 3D, plans
  export/              IFC, DXF, GLB, drag editor (HTML / MCP App)
  mcp_server.py        MCP tools + MCP App
  cli.py               command line
revit/pyrevit/         "Place from scene" button (untested scaffold) + family map
copilot/               agent instructions for Copilot Studio / declarative agents
docs/VSCODE.md         VS Code tasks, YAML schemas and Copilot skills (no AI in the engine, no Claude needed)
docs/EDITING.md        where to edit what, how things are sized
docs/LIBRARY.md        plant library packs, client profiles, reference designs, approval workflow
docs/HAIKU_TASKS.md    Copilot Studio wiring still to do
docs/ARCHITECTURE.md   design, coordinates, engines, Revit/Navisworks routes, roadmap
docs/DEPLOYMENT.md     hosting, Copilot wiring, Claude Code setup, costs
CLAUDE.md, .mcp.json   Claude Code project setup; .vscode/mcp.json for GitHub Copilot
Dockerfile             one container: web + REST + MCP
examples/              sample brief + site
tests/                 engine + layout tests
```
