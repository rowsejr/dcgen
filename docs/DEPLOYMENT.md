# Deploying dcgen: one engine, three front doors

The engine needs no AI. AI front ends are optional, thin, and swappable.

| Front door | For | AI cost per test fit | What it is |
|---|---|---|---|
| Web form + REST API | everyone | none | `uvicorn dcgen.api:app` (`/`, `/api`, `/docs`) |
| Microsoft 365 Copilot agent | staff with M365 Copilot | covered by their Copilot licence when used in M365 apps (fair use); others use Copilot Credits | Copilot Studio agent calling `/mcp` |
| Claude (Code, Desktop, connector) | the few with Claude seats | Claude tokens | `.mcp.json` (stdio) or remote `/mcp` |

All three call `dcgen/service.py`, so results are identical whichever door is used.

## 1. Develop: VS Code + Claude Code

* Open the repo in VS Code and use Claude Code there (the VS Code extension or the CLI in the
  terminal). VS Code is where the code and server run; Claude Code is the agent working on them.
* `CLAUDE.md` gives Claude the commands and rules; `.mcp.json` registers the dcgen MCP server for
  testing the agent experience (Claude Code asks you to approve it once).
* Spend: use Sonnet by default and Opus only for hard design questions, `/clear` between unrelated
  tasks, plan mode for bigger changes, and let the tests verify work. If the firm has an Azure
  agreement, Claude Code can run through Microsoft Foundry and bill per token to Azure.
* If you have GitHub Copilot, `.vscode/mcp.json` lets its agent mode use the same server.

## 2. Host the web app (no AI)

1. `docker build -t dcgen .` (see `Dockerfile`; one container serves web, REST and MCP).
2. Run on Azure Container Apps or App Service. Turn on the built-in authentication with Entra ID so
   only staff can reach it. Mount Azure Files at `/data` so projects persist.
3. Set `DCGEN_PUBLIC_URL=https://<host>` so agents return clickable download links.
4. Sizing: one option search is about 10-20 s on one CPU core. No GPU.

## 3. Microsoft 365 Copilot via Copilot Studio

1. The hosted app's `/mcp` must be reachable over HTTPS by Microsoft's cloud, with authentication
   (start with an API key behind the platform's auth; move to OAuth with Entra ID for production).
2. Copilot Studio: create an agent, turn on generative orchestration, then Tools > Add a tool >
   Model Context Protocol and point a new MCP connector at `https://<host>/mcp`.
3. Paste `copilot/agent-instructions.md` into the agent's instructions.
4. Optional: if your admin has enabled Anthropic models for the tenant, pick Claude as the agent's
   model. No individual Claude seats are needed for this.
5. Publish to Teams / Microsoft 365 Copilot and pilot with 3-5 engineers.

Notes:
* Copilot Studio uses the MCP tools. The drag editor (an MCP App) will not render there, so the
  agent hands out the web app's editor link instead (`open_editor` does this when
  `DCGEN_PUBLIC_URL` is set).
* A declarative agent built with Microsoft 365 Agents Toolkit (in VS Code) can use the MCP server or
  the REST API (`/openapi.json`) as a plugin; Microsoft also documents MCP Apps in declarative
  agents, which is the route to the editor inside Copilot. FastAPI serves OpenAPI 3.1; check the
  version the Microsoft tooling accepts before importing.
* Confirm billing with your Microsoft licensing contact: the inclusion applies to licensed users
  inside Microsoft 365 apps, subject to fair-use limits.

## 4. Claude power users

* Claude Code / Claude Desktop: stdio via `.mcp.json` or the desktop config (see README).
* claude.ai organisation connector: point it at `https://<host>/mcp` (set up OAuth first).

## Security checklist

* Everything behind Entra ID; nothing public.
* Projects on access-controlled storage; no client data in logs.
* Agents receive compact summaries; full scenes and drawings stay on your server.
* Review which LLM sees what: Copilot's model (or Claude if enabled) sees tool results, not files.
