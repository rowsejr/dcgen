"""dcgen MCP server: the agent front door. Same service layer as the web/REST
front door, so Claude, Microsoft 365 Copilot (Copilot Studio or a declarative
agent) and GitHub Copilot in VS Code all drive the identical engine.

  stdio (local, Claude Code / Claude Desktop / VS Code):  dcgen-mcp
  Streamable HTTP (hosted, Copilot Studio / connectors): served at /mcp by api.py,
                                                          or standalone: dcgen-mcp --http
The agent reasons and chooses parameters; every number comes from the tools.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Optional

from mcp.server.apps import Apps, client_supports_apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from . import service
from .catalogue import DEFAULT_PATH
from .engines import noise
from .export.editor import editor_html
from .io import read_redline
from .schema import Receptor, SiteInput

INSTRUCTIONS = """dcgen generates RIBA Stage 2 data centre test fits (data halls, electrical plant by
redundancy topology, heat rejection vs ambient and noise, site roads and ancillaries).
Workflow: create_project -> set_site (redline) -> update_brief -> generate_options ->
read checks/metrics -> iterate (update_brief, move_element, select_option) -> export.
Rules: never invent geometry or plant numbers - quote tool results. Catalogue values are
placeholders until the engineering team signs them off; say so when presenting results.
When a check fails, propose a concrete change (brief parameter or move) and re-run."""

apps = Apps()


# ----------------------------------------------------------------------------- MCP App (registered before the server is built)
@apps.tool(resource_uri="ui://dcgen/editor.html",
           description="Open the interactive layout editor for the current option (drag plant, live checks).")
def open_editor(project: str, ctx: Context) -> Any:
    sc = service.scene_of(project)
    if not client_supports_apps(ctx):
        public = os.environ.get("DCGEN_PUBLIC_URL", "").rstrip("/")
        if public:
            return f"Open the drag editor here: {public}/editor/{project}"
        f = service.export(project, ["editor"])["editor"]
        return f"This client cannot render apps; standalone editor written to {f}"
    # keep the model's context small: the view fetches the full scene through the app-only tool
    return {"project": project, "option": sc.option, "elements": len(sc.elements),
            "message": "Editor opened. Drag plant; each move calls move_element and re-runs the checks."}


@apps.tool(resource_uri="ui://dcgen/editor.html", visibility=["app"],
           description="(Editor only) full scene JSON for the layout editor view.")
def editor_scene(project: str) -> dict:
    return {"project": project, "scene": service.scene_json(project)}


apps.add_html_resource("ui://dcgen/editor.html", editor_html(None), title="dcgen layout editor")

mcp = MCPServer("dcgen", instructions=INSTRUCTIONS, extensions=[apps])


# ----------------------------------------------------------------------------- tools
@mcp.tool()
def create_project(name: str, brief: Optional[dict] = None) -> dict:
    """Create (or reset) a project. `brief` takes any Brief fields, e.g.
    {"it_load_mw": 36, "hall_it_mw": 6, "rack_kw": 30, "topology": "5M4", "ambient_design_c": 38}."""
    return service.create_project(name, brief) | {"next": "set_site"}


@mcp.tool()
def set_site(project: str, redline: Optional[list[list[float]]] = None, redline_file: Optional[str] = None,
             access_points: Optional[list[list[float]]] = None, utility_point: Optional[list[float]] = None,
             receptors: Optional[list[dict]] = None, setback_m: float = 10.0, true_north_deg: float = 0.0,
             boundary_noise_limit_dba: Optional[float] = None, crs: Optional[str] = None) -> dict:
    """Set the planning redline (site coordinates in metres, or a .dxf/.geojson/.kml path),
    vehicle access points, utility connection direction and noise-sensitive receptors
    [{"name": "NSR1", "x": .., "y": .., "limit_dba": 45}]."""
    pts = read_redline(redline_file) if redline_file else [tuple(p) for p in (redline or [])]
    site = SiteInput(name=project, redline=pts, access_points=[tuple(p) for p in (access_points or [])],
                     utility_point=tuple(utility_point) if utility_point else None,
                     receptors=[Receptor(**r) for r in (receptors or [])], boundary_setback_m=setback_m,
                     true_north_deg=true_north_deg, boundary_noise_limit_dba=boundary_noise_limit_dba, crs=crs)
    return service.set_site(project, site) | {"next": "update_brief or generate_options"}


@mcp.tool()
def update_brief(project: str, changes: dict) -> dict:
    """Change brief parameters, e.g. {"topology": "N+1 catcher", "catcher_group": 6} or
    {"hr_location": "roof", "roof_screen_db": 8}. Returns the derived plant sizing."""
    return service.update_brief(project, changes)


@mcp.tool()
def generate_options(project: str, max_options: int = 4) -> dict:
    """Search orientations, mirrored arrangements and heat-rejection locations; return the
    best options ranked (lower score = better). The best option becomes the current scene."""
    out = service.generate_options(project, max_options)
    if "selected_summary" in out:      # agents only need the non-passing checks
        s = out["selected_summary"]
        s["checks"] = [c for c in s["checks"] if c["severity"] != "pass"] or "all checks pass"
    return out


@mcp.tool()
def select_option(project: str, option: str) -> dict:
    """Make option A/B/C... the current scene."""
    return service.select_option(project, option)


@mcp.tool()
def compare_topologies(project: str, topologies: Optional[list[str]] = None) -> dict:
    """Plant consequences of different redundancy topologies for the same brief."""
    return {"project": project, "rows": service.compare_topologies(service.state(project)["brief"], topologies)}


@mcp.tool()
def move_element(project: str, element_id: str, dx: float, dy: float, whole_group: bool = False) -> dict:
    """Move an element (or its whole array/group) by dx, dy metres in the layout frame,
    lock it, and re-run all checks. Same operation as dragging in the editor."""
    return service.move_element(project, element_id, dx, dy, whole_group)


@mcp.tool()
def get_scene(project: str, kinds: Optional[list[str]] = None, full: bool = False) -> dict:
    """Current scene: summary, or elements (optionally filtered by kind, e.g. ["genset","substation"])."""
    sc = service.scene_of(project)
    out = service.summary(sc)
    if full or kinds:
        out["elements"] = [{"id": e.id, "kind": e.kind, "name": e.name, "x": round(e.x, 2), "y": round(e.y, 2),
                            "w": e.w, "d": e.d, "z": e.z, "h": e.h, "group": e.group, "locked": e.locked}
                           for e in sc.elements if not kinds or e.kind in kinds]
    return out


@mcp.tool()
def noise_at(project: str, points: list[list[float]]) -> dict:
    """Screening Lp dB(A) at site-coordinate points [[x, y], ...] (1.5 m height)."""
    import numpy as np
    sc = service.scene_of(project)
    loc = np.array([[*sc.frame.to_local(p[0], p[1]), 1.5] for p in points])
    lp = noise.lp_at(loc, sc, building=noise.building_polygon(sc))
    return {"points": [{"x": p[0], "y": p[1], "lp_dba": round(float(v), 1)} for p, v in zip(points, lp)]}


@mcp.tool()
def export(project: str, formats: Optional[list[str]] = None) -> dict:
    """Write deliverables for the current scene: pdf (A3 sheets incl. 3D views), ifc (Revit/
    Navisworks), glb (web 3D), dxf (CAD), json (scene), editor (standalone drag editor)."""
    sc = service.scene_of(project)
    files = service.export(project, formats)
    out = {"project": project, "option": sc.option, "files": files}
    public = os.environ.get("DCGEN_PUBLIC_URL", "").rstrip("/")
    if public:   # hosted: give people links they can click (Teams / Copilot / claude.ai)
        out["links"] = {f: f"{public}/api/projects/{project}/files/{f}" for f in files}
        out["links"]["editor_page"] = f"{public}/editor/{project}"
    return out


# ----------------------------------------------------------------------------- resources + prompts
@mcp.resource("dcgen://catalogue", mime_type="text/yaml", description="Module catalogue (placeholders until signed off)")
def catalogue_resource() -> str:
    return Path(DEFAULT_PATH).read_text()


@mcp.prompt()
def test_fit_from_brief(brief_text: str) -> str:
    """Turn a client brief (pasted text) into a test-fit run."""
    return (f"Client brief:\n{brief_text}\n\nExtract Brief parameters (IT MW, hall size, rack density, cooling, "
            "topology, ambient, autonomy, parking). List anything missing and the assumption you'll use. Then call "
            "create_project, set_site (ask for the redline if not provided), generate_options, explain the top "
            "options' trade-offs from the tool results, and export the PDF and IFC.")


def main() -> None:
    ap = argparse.ArgumentParser(description="dcgen MCP server")
    ap.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    if a.http:
        mcp.run(transport="streamable-http", port=a.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
