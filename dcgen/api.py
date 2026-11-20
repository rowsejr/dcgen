"""Web + REST front door (no AI needed), with the MCP endpoint mounted alongside.

  uvicorn dcgen.api:app --reload            # local
  docker build -t dcgen . && docker run -p 8000:8000 dcgen

  /                     web form: brief + redline upload -> options, downloads, editor
  /docs, /openapi.json  REST API (OpenAPI) - for Power Automate, Excel, M365 declarative-agent API plugins
  /mcp                  MCP (Streamable HTTP) - for Copilot Studio, Claude connectors, VS Code agents

Put the whole app behind your identity provider (e.g. Entra ID on Azure App Service /
Container Apps) before it leaves your machine.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__, service
from .catalogue import DEFAULT_PATH
from .export.editor import editor_html
from .schema import SiteInput

WEB = Path(__file__).parent / "web"
MAX_UPLOAD = 5 * 1024 * 1024


# ----------------------------------------------------------------------------- MCP mounted in the same process
def _mcp_app():
    try:
        from .mcp_server import mcp
        return mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")
    except Exception as exc:  # MCP extra not installed: web + REST still work
        print("MCP endpoint disabled:", exc)
        return None


MCP_APP = _mcp_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if MCP_APP is not None:
        async with MCP_APP.router.lifespan_context(MCP_APP):
            yield
    else:
        yield


app = FastAPI(title="dcgen", version=__version__, lifespan=lifespan,
              description="Datacentre RIBA Stage 2 test-fit generator. Deterministic engine: the same request "
                          "always gives the same layout. Catalogue values are placeholders until signed off.")


@app.exception_handler(service.ServiceError)
async def _service_error(_: Request, exc: service.ServiceError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ----------------------------------------------------------------------------- request models
class CreateProject(BaseModel):
    name: str = Field(..., description="Letters, digits, - and _ (e.g. slough-b1)")
    brief: dict = Field(default_factory=dict, description="Any Brief fields, e.g. it_load_mw, topology")


class BriefChanges(BaseModel):
    changes: dict = Field(..., description='e.g. {"topology": "5M4", "ambient_design_c": 38}')


class MoveRequest(BaseModel):
    element_id: str
    dx: float
    dy: float
    whole_group: bool = False


class TopologyRequest(BaseModel):
    brief: dict = Field(default_factory=dict)
    topologies: Optional[list[str]] = None


# ----------------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return (WEB / "index.html").read_text()


@app.get("/editor/{project}", response_class=HTMLResponse, include_in_schema=False)
def editor(project: str) -> str:
    html = editor_html(service.scene_of(project))
    hook = f'<script>window.DCGEN_API = ""; window.DCGEN_PROJECT = {json.dumps(project)};</script>'
    return html.replace("<script>", hook + "\n<script>", 1)


# ----------------------------------------------------------------------------- REST API
@app.get("/api/health", operation_id="health")
def health() -> dict:
    return {"ok": True, "version": __version__, "mcp": MCP_APP is not None}


@app.post("/api/projects", operation_id="createProject", summary="Create or reset a project")
def create_project(req: CreateProject) -> dict:
    return service.create_project(req.name, req.brief)


@app.put("/api/projects/{project}/site", operation_id="setSite", summary="Set the site (redline in site coordinates)")
def set_site(project: str, site: SiteInput) -> dict:
    return service.set_site(project, site)


@app.post("/api/projects/{project}/site/upload", operation_id="uploadRedline",
          summary="Upload a redline (.dxf/.geojson/.kml) plus site options as JSON")
async def upload_redline(project: str, file: UploadFile = File(...), options: str = Form("{}")) -> dict:
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Redline file larger than 5 MB")
    try:
        opts = json.loads(options or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"options is not valid JSON: {exc}") from exc
    return service.set_site_from_file(project, file.filename or "redline.dxf", data, **opts)


@app.patch("/api/projects/{project}/brief", operation_id="updateBrief", summary="Change brief parameters")
def update_brief(project: str, req: BriefChanges) -> dict:
    return service.update_brief(project, req.changes)


@app.post("/api/projects/{project}/options", operation_id="generateOptions",
          summary="Generate and rank layout options (no AI; ~10-20 s on one CPU)")
def generate_options(project: str, max_options: int = 4) -> dict:
    return service.generate_options(project, max_options)


@app.post("/api/projects/{project}/options/{option}/select", operation_id="selectOption")
def select_option(project: str, option: str) -> dict:
    return service.select_option(project, option)


@app.get("/api/projects/{project}", operation_id="getProject", summary="Current option: metrics, plant, checks")
def get_project(project: str) -> dict:
    st = service.state(project)
    out = {"project": project, "brief": st["brief"].model_dump(), "has_site": st["site"] is not None}
    if st["scene"] is not None:
        out["summary"] = service.summary(st["scene"])
        out["options"] = [service.option_row(o) for o in st["options"]]
    return out


@app.post("/api/projects/{project}/move", operation_id="moveElement",
          summary="Move an element or its array, lock it and re-run the checks")
def move(project: str, req: MoveRequest) -> dict:
    return service.move_element(project, req.element_id, req.dx, req.dy, req.whole_group)


class CompassRequest(BaseModel):
    element_id: str
    direction: str = Field(description="N..NW or degrees (0 = north, clockwise)")
    distance: float
    whole_group: bool = False


class WhatIfRequest(BaseModel):
    changes: list[dict] = Field(description="1-6 sets of brief changes, e.g. [{\"topology\": \"2N\"}]")


@app.post("/api/projects/{project}/move_compass", operation_id="moveCompass", summary="Move towards a true compass direction")
def move_compass(project: str, req: CompassRequest) -> dict:
    return service.move_compass(project, req.element_id, req.direction, req.distance, req.whole_group)


@app.post("/api/projects/{project}/undo", operation_id="undo", summary="Undo the last edit")
def undo(project: str) -> dict:
    return service.undo(project)


@app.get("/api/projects/{project}/history", operation_id="history", summary="Undo stack (oldest first)")
def history(project: str) -> list[str]:
    return service.history(project)


@app.post("/api/projects/{project}/regenerate", operation_id="regenerate", summary="Re-run engines keeping moved elements")
def regenerate(project: str) -> dict:
    return service.regenerate(project)


@app.post("/api/projects/{project}/what_if", operation_id="whatIf", summary="Try brief changes without altering the project")
def what_if(project: str, req: WhatIfRequest) -> list[dict]:
    return service.what_if(project, req.changes)


@app.get("/api/projects/{project}/peak_pue", operation_id="peakPue", summary="Peak PUE build-up (design point)")
def peak(project: str) -> dict:
    return service.peak_pue(project)


@app.get("/api/projects/{project}/fixes", operation_id="suggestFixes", summary="Levers for each failing check")
def fixes(project: str) -> list[dict]:
    return service.suggest_fixes(project)


@app.get("/api/projects/{project}/preview.png", operation_id="preview", summary="PNG preview (plan | overview | closeup)")
def preview(project: str, view: Literal["plan", "overview", "closeup"] = "plan", px: int = 1200):
    from fastapi.responses import Response
    return Response(service.preview(project, view, px), media_type="image/png")


@app.get("/api/schema", operation_id="briefSchema", summary="Brief fields and catalogue override keys")
def schema() -> dict:
    return service.brief_schema()


@app.get("/api/projects/{project}/files/{fmt}", operation_id="getFile",
         summary="Download a deliverable for the current option (generated on demand)")
def get_file(project: str, fmt: Literal["pdf", "ifc", "glb", "dxf", "json", "editor"]):
    path = service.export(project, [fmt])[fmt]
    media = {"pdf": "application/pdf", "ifc": "application/x-step", "glb": "model/gltf-binary",
             "dxf": "image/vnd.dxf", "json": "application/json", "editor": "text/html"}[fmt]
    return FileResponse(path, media_type=media, filename=Path(path).name)


@app.post("/api/topologies", operation_id="compareTopologies",
          summary="Plant consequences of 4M3 / 5M4 / catcher / 2N for a brief (stateless)")
def topologies(req: TopologyRequest) -> list[dict]:
    return service.compare_topologies(req.brief, req.topologies)


@app.get("/api/catalogue", operation_id="getCatalogue", response_class=PlainTextResponse)
def catalogue() -> str:
    return Path(DEFAULT_PATH).read_text()


@app.get("/api/library", operation_id="libraryInfo", summary="Plant library packs loaded and record counts")
def library_info():
    return service.library_info()


@app.get("/api/library/search", operation_id="librarySearch", summary="Search plant records (short rows)")
def library_search(type: Optional[str] = None, q: str = "", limit: int = 15, drafts: bool = False,
                   capacity_kw_min: Optional[float] = None, lw_dba_max: Optional[float] = None):
    ranges = {k: v for k, v in (("capacity_kw_min", capacity_kw_min), ("lw_dba_max", lw_dba_max)) if v is not None}
    return service.library_search(type, q, limit, drafts, **ranges)


@app.get("/api/library/records/{record_id}", operation_id="libraryGet", summary="One plant record in full")
def library_get(record_id: str):
    return service.library_get(record_id)


@app.get("/api/projects/{project}/plant", operation_id="plantOptions", summary="Ranked heat rejection options and plant picked")
def plant_options(project: str, top: int = 5):
    return service.plant_options(project, top)


@app.get("/api/profiles", operation_id="listProfiles", summary="Client profiles")
def list_profiles():
    return service.profiles_list()


@app.post("/api/projects/{project}/profile/{profile_id}", operation_id="applyProfile", summary="Apply a client profile to the brief")
def apply_profile(project: str, profile_id: str):
    return service.apply_profile(project, profile_id)


@app.get("/api/projects/{project}/compliance", operation_id="compliance", summary="Client rule results with clause references")
def compliance(project: str, csv: bool = False):
    if csv:
        return PlainTextResponse(service.compliance_csv(project), media_type="text/csv")
    return service.compliance(project)


@app.get("/api/example", operation_id="getExample", summary="The example brief and site, to try the tool")
def example() -> dict:
    from .io import load_brief, load_site
    ex = Path(os.environ.get("DCGEN_EXAMPLES", Path(__file__).parents[1] / "examples"))
    if not ex.exists():
        raise HTTPException(404, "examples folder not deployed")
    return {"brief": load_brief(ex / "brief.yaml").model_dump(), "site": load_site(ex / "site.yaml").model_dump()}


# MCP last, so the routes above take precedence; it only answers /mcp
if MCP_APP is not None:
    app.mount("/", MCP_APP)
