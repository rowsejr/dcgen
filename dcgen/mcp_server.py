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
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.context import Context

from . import service
from .catalogue import DEFAULT_PATH
from .engines import noise
from .export.editor import editor_html
from .io import read_redline
from .schema import Receptor, SiteInput

INSTRUCTIONS = """dcgen generates RIBA Stage 2 data centre test fits (data halls, electrical plant by
redundancy topology, heat rejection vs ambient and noise, site roads and ancillaries).
Look at preview before reporting; use what_if to compare options without changing the project, undo to step back,
move_compass for 'move X 10 m north'. Brief fields and override keys: resource dcgen://brief-schema.
Peak PUE only (design point) - it sizes the electrical plant; annual PUE belongs to the PUE tool.
Chillers fed from IT lineups must fit the lineup's mechanical allowance (600 kW default): their EER at the
design air-on limits their size; otherwise mechanical plant gets separate lineups (mech_lineups).
Workflow: create_project -> set_site (redline) -> update_brief -> generate_options ->
read checks/metrics -> iterate (update_brief, move_element, select_option) -> export.
Rules: never invent geometry or plant numbers - quote tool results. Catalogue values are
placeholders until the engineering team signs them off; say so when presenting results.
When a check fails, propose a concrete change (brief parameter or move) and re-run.
Plant comes from the library (library_search, plant_options); only approved records are used unless the brief allows
drafts. You cannot approve records: that is an engineer's sign-off. Client rules come from profiles (apply_profile,
compliance) with clause references; quote the clause when reporting a failure."""

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
def move_compass(project: str, element_id: str, direction: str, distance: float, whole_group: bool = False) -> dict:
    """Move an element `distance` m towards a TRUE compass direction (N, NE, ... NW, or degrees 0=north clockwise).
    Use this instead of dx/dy when the instruction is "move the fuel store 10 m north"."""
    return service.move_compass(project, element_id, direction, distance, whole_group)


@mcp.tool()
def move_towards(project: str, element_id: str, target_id: str, distance: float) -> dict:
    """Move an element straight towards another element by `distance` m (negative = away)."""
    return service.move_towards(project, element_id, target_id, distance)


@mcp.tool()
def undo(project: str) -> dict:
    """Undo the last edit (move / regenerate / option change). Up to 20 steps."""
    return service.undo(project)


@mcp.tool()
def regenerate(project: str) -> dict:
    """Re-run the engines for the current brief on the selected option, keeping elements you moved."""
    return service.regenerate(project)


@mcp.tool()
def what_if(project: str, changes: list[dict]) -> list[dict]:
    """Try 1-6 alternative brief changes WITHOUT altering the project, e.g.
    [{"topology": "2N"}, {"overrides": {"halls.cross_corridor_every": 1}}]. Returns score / fail / warn
    deltas, which checks they fix or introduce, and plant counts. Apply the winner with update_brief."""
    return service.what_if(project, changes)


@mcp.tool()
def peak_pue(project: str) -> dict:
    """Peak (design-point) PUE build-up for the selected option: chillers at 100% load at design ambient +
    uplift, fans, pumps and electrical losses (CUN-CAL-PUE-001 method). It sizes gensets, transformers, LV
    lineups, the intake and fuel. Annual PUE is not calculated by this tool."""
    return service.peak_pue(project)


@mcp.tool()
def suggest_fixes(project: str) -> list[dict]:
    """For each failing or warning check, the levers (brief fields or override keys) worth trying. Advisory only:
    test them with what_if before applying."""
    return service.suggest_fixes(project)


@mcp.tool()
def preview(project: str, view: str = "plan", px: int = 1200, highlight: Optional[list[str]] = None) -> Image:
    """PNG of the current layout: view = plan | overview | closeup. The plan outlines elements flagged by failed or
    warning checks in red (or pass element ids in `highlight`). Look at it before reporting a layout."""
    return Image(data=service.preview(project, view, px, highlight), format="png")


@mcp.resource("dcgen://brief-schema", mime_type="application/json",
              description="Every brief field and every catalogue override key you may set, with shipped values")
def brief_schema_resource() -> str:
    import json
    return json.dumps(service.brief_schema(), indent=1, default=str)


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


# ----------------------------------------------------------------------------- plant library, client profiles, reference designs
@mcp.tool()
def library_search(type: Optional[str] = None, query: str = "", limit: int = 15, include_drafts: bool = False,
                   capacity_kw_min: Optional[float] = None, lw_dba_max: Optional[float] = None,
                   kva_min: Optional[float] = None) -> dict:
    """Search the approved plant library (type: chiller, dry_cooler, adiabatic_cooler, genset, transformer,
    elec_room_typical). Returns short rows with record ids; use library_get for one record's full data.
    Drafts are excluded unless include_drafts (exploration only)."""
    ranges = {k: v for k, v in (("capacity_kw_min", capacity_kw_min), ("lw_dba_max", lw_dba_max), ("kva_min", kva_min)) if v is not None}
    rows = service.library_search(type, query, limit, include_drafts, **ranges)
    return {"count": len(rows), "rows": rows, "library": service.library_info()["by_type"]}


@mcp.tool()
def library_get(record_id: str) -> dict:
    """One library record in full: dimensions, clearances, sound data, curves, source and approval status."""
    return service.library_get(record_id)


@mcp.tool()
def plant_options(project: str, top: int = 5) -> dict:
    """Ranked heat rejection candidates for the project (units needed, footprint, total sound power, runners-up)
    and the genset/transformer picked. Change the choice with update_brief: hr_prefer (balanced|footprint|noise|count)
    or hr_unit / genset_unit / tx_unit set to a record id."""
    return service.plant_options(project, top)


@mcp.tool()
def list_profiles() -> list[dict]:
    """Client profiles in the loaded packs (id, revision, number of rules, approved manufacturers)."""
    return service.profiles_list()


@mcp.tool()
def apply_profile(project: str, profile_id: str) -> dict:
    """Apply a client profile to the brief: its defaults, catalogue overrides and approved-plant filter."""
    return service.apply_profile(project, profile_id)


@mcp.tool()
def compliance(project: str, only_issues: bool = True) -> dict:
    """Client rule results with clause references for the current layout (pass/fail/warn/manual)."""
    c = service.compliance(project)
    rows = [r for r in c["rows"] if r["status"] != "pass"] if only_issues else c["rows"]
    return {"profile": c["profile"], "revision": c["revision"], "summary": c["summary"], "rows": rows}


@mcp.tool()
def reference_designs(project: str, benchmark: bool = False) -> dict:
    """Reference designs closest to this brief; with benchmark=True, how the current layout's per-MW numbers
    compare (outliers flagged)."""
    out = {"similar": service.reference_find(project)}
    if benchmark:
        out["benchmark"] = service.benchmark(project)
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
