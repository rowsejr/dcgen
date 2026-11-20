# Running dcgen from VS Code with GitHub Copilot (no Claude)

## What runs where

- **dcgen** is deterministic Python running on your machine. It uses no AI and no tokens, and the same inputs always
  give the same layout.
- **GitHub Copilot** (Chat, in agent mode) writes and edits the two YAML files, runs the dcgen commands and reads the
  summary back. Any model in Copilot's picker works. Nothing in this setup calls Claude or the Anthropic API.
- **Git** carries projects between colleagues. The YAML, the project README and `out/summary.md` are committed. The
  heavy deliverables (PDF, IFC, DXF, GLB) stay local unless someone commits them on purpose.

## One-off setup

1. Install Python 3.11 or later and Git, clone the repository and open the folder in VS Code.
2. Accept the recommended extensions: Python, YAML (Red Hat), GitHub Copilot Chat, a PDF viewer and Live Preview.
3. Make a virtual environment: Command Palette > **Python: Create Environment** > Venv. VS Code selects it.
4. **Terminal > Run Task > dcgen: install / update**.
5. Optional: `dcgen.packs` in `.vscode/settings.json` names the plant packs (default `packs/cundall`). Draft records in
   a pack are only used by projects that set `use_drafts: true`.

## The loop: YAML in, summary out

1. **Start a project:** run the task **dcgen: new project**, or ask Copilot: *"Use the new-test-fit skill. Brief: 36 MW,
   6 MW halls, 5M4, CHW 24 °C, redline in redline.dxf, nearest houses 200 m east at 42 dB(A)."*
2. **Edit the YAML:** Copilot, or you, edits `projects/<name>/brief.yaml` and `site.yaml`. The YAML extension checks them
   as you type against `schemas/`: unknown keys and wrong types are underlined, and hovering a key shows its meaning
   and default.
3. **Check:** with either file open, run **dcgen: check this project**. Errors appear in the Problems panel with their
   file and line. Info lines show the plant the engine picked and the peak PUE, in under a second.
4. **Run:** press **Ctrl+Shift+B** (the default build task, **dcgen: run this project**). It writes `out/summary.md`.
   Use **dcgen: run this project (all deliverables)** for the A3 PDF, IFC, DXF, GLB and the drag editor. Failing and
   warning checks also appear in the Problems panel.
5. **Iterate:** Copilot reads `out/summary.md` and proposes the next change (skill `iterate-test-fit`). The summary's
   "Changes since the last run" table shows what each edit did.
6. **Share:** commit `brief.yaml`, `site.yaml`, `README.md` and `out/summary.*`, then push. A colleague pulls and runs
   the same task and gets the same result.

## Copilot files in the repository

| File | Used for |
|---|---|
| `.github/copilot-instructions.md` | Always included: what dcgen is, the commands, and the rules (no invented numbers, overrides not catalogue edits, no approvals, peak PUE is not annual PUE). |
| `.github/instructions/project-yaml.instructions.md` | Added when Copilot works on `projects/**/*.yaml`: the keys that matter and typical values. |
| `.github/instructions/plant-mapping.instructions.md` | Added for `packs/**/mappings/*.yaml`: the import mapping language. |
| `.github/skills/new-test-fit/` | Client brief to a project folder, checked and run. |
| `.github/skills/iterate-test-fit/` | Clear failing checks and compare variants from the summary. |
| `.github/skills/plant-data-import/` | Vendor spreadsheet to draft plant records through a mapping. |
| `.vscode/mcp.json` | Optional: lets Copilot call dcgen's MCP tools (preview images, what-if) instead of the CLI. Needs `dcgen-mcp` on the PATH (open a terminal with the venv active). |

VS Code's default Copilot harness (since VS Code 1.143) uses skills. Prompt files belong to the older Local harness,
which VS Code removes in February 2027, so there are none here.

## Microsoft 365 Copilot instead of GitHub Copilot

Paste `.github/skills/new-test-fit/SKILL.md` and the client brief into Microsoft 365 Copilot and ask for `brief.yaml`
and `site.yaml`. Save them into `projects/<name>/`, then use the tasks above. The check task catches anything it got
wrong. Paste `out/summary.md` back to ask what to change next.

## Tasks

| Task | What it does |
|---|---|
| dcgen: new project | `projects/<name>/` with schema-linked `brief.yaml`, `site.yaml` and a README |
| dcgen: check this project | YAML and engine checks, shown in the Problems panel (under 1 s) |
| dcgen: run this project | Layout options plus `out/summary.md` / `summary.json` (about 10 s) |
| dcgen: run this project (all deliverables) | Adds the PDF, IFC, DXF, GLB and editor |
| dcgen: peak PUE for this project | Peak PUE build-up, chiller EER and the 600 kW lineup check |
| dcgen: validate plant library | Checks the records in `dcgen.packs` |
| dcgen: refresh YAML schemas | Regenerates `schemas/` after a change to the brief or site model |
| dcgen: web app (no AI) | Form, REST API and MCP at http://localhost:8000 |
| dcgen: fast tests / full tests | pytest |
| dcgen: install / update | `pip install -e .[all]` into the selected interpreter |

The tasks use the Python interpreter selected in VS Code, so no environment activation is needed. They find the
project from the open file, so keep `brief.yaml` or `site.yaml` in focus. The same commands work in any terminal:
`python -m dcgen.cli project new | check | run | peak <folder>`.
