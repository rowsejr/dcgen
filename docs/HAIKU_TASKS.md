# Mechanical wiring tasks (safe to hand to a small model)

Everything below is configuration, not engine logic. Do not change anything under `dcgen/engines/`, `dcgen/data/` or
`dcgen/library/`. Check each result by running the command given. None of this has been tested against a real VS Code or
Copilot Studio tenant.

## 1. VS Code (GitHub Copilot agent mode)
`.vscode/mcp.json` already declares the stdio server `dcgen`. Add:
- an `inputs` entry (`promptString`, id `dcgenPacks`) and set `"DCGEN_PACKS": "${input:dcgenPacks}"` in `env`, so each engineer
  points at their pack folder (e.g. the shared drive copy of the plant library) without editing the file;
- a second server `dcgen-http` of `"type": "http"` pointing at `https://<your-host>/mcp`, commented as the hosted alternative;
- `.vscode/tasks.json` with tasks: "dcgen: fast tests" (`pytest -q tests/test_engines.py`), "dcgen: serve"
  (`uvicorn dcgen.api:app --reload`), "dcgen: example run" (the CLAUDE.md example command).
- `.vscode/settings.json`: `python.testing.pytestEnabled: true`, `python.testing.pytestArgs: ["tests"]`.
Check: `python -m json.tool` on each file (strip comments first if using JSONC); open VS Code, Chat > Agent mode > tools list shows `dcgen`.

## 2. Copilot Studio / declarative agent YAML
`copilot/agent-instructions.md` is the prompt. Create `copilot/agent.yaml` containing: name, description, the instructions
(include the file by pasting its body), and one MCP tool entry pointing at `https://<your-host>/mcp`. Create
`copilot/openapi-actions.md` listing the REST routes from `/openapi.json` (operation ids) as the fallback if MCP is blocked.
Leave `<your-host>` as a visible placeholder. Check: the YAML parses (`python -c "import yaml,sys;yaml.safe_load(open(sys.argv[1]))" copilot/agent.yaml`).
Preview images: Copilot Studio may not render MCP images; note that `GET /api/projects/{p}/preview.png` is the fallback.

## 3. Packs and environment
- `.env.example` with `DCGEN_HOME`, `DCGEN_PACKS`, `DCGEN_PUBLIC_URL`, `ANTHROPIC_API_KEY` (only for `scripts/batch_extract.py`).
- `Dockerfile`: add a commented `VOLUME` / `ENV DCGEN_PACKS=/packs` line. Do not bake packs into the image.
- `.gitignore`: make sure `.dcgen-projects/`, `*.xlsx` import sources and any `client-data/` folder are ignored.

## Not for this list
Catalogue or library values, approval of records, check logic, scoring weights, anything that decides a number on a drawing.
