"""Service layer: project state and actions, independent of any front door.

The web form / REST API (api.py), the MCP server (mcp_server.py) and scripts
all call these functions, so a test fit behaves identically whether it was
started from a browser, Microsoft 365 Copilot, Claude, or the command line.
No AI is involved anywhere in here.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from . import layout
from .catalogue import load_catalogue
from .io import read_redline
from .schema import Brief, Receptor, Scene, SiteInput

HOME = Path(os.environ.get("DCGEN_HOME", "~/dcgen-projects")).expanduser()
STATE: dict[str, dict[str, Any]] = {}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FORMATS = ("pdf", "ifc", "glb", "dxf", "json", "editor")


class ServiceError(ValueError):
    """Bad request from any front door (unknown project, no site, bad name...)."""


def safe_name(project: str) -> str:
    if not _NAME.match(project or ""):
        raise ServiceError("Project names: letters, digits, '-' and '_' only (max 64), starting with a letter/digit.")
    return project


def project_dir(project: str) -> Path:
    d = HOME / safe_name(project)
    d.mkdir(parents=True, exist_ok=True)
    return d


def state(project: str) -> dict[str, Any]:
    safe_name(project)
    if project not in STATE:
        d = HOME / project
        st: dict[str, Any] = {"brief": Brief(project=project), "site": None, "options": [], "scene": None}
        if (d / "brief.json").exists():
            st["brief"] = Brief.model_validate_json((d / "brief.json").read_text())
        if (d / "site.json").exists():
            st["site"] = SiteInput.model_validate_json((d / "site.json").read_text())
        if (d / "scene.json").exists():
            st["scene"] = Scene.from_json(str(d / "scene.json"))
        STATE[project] = st
    return STATE[project]


def save(project: str) -> None:
    st, d = STATE[project], project_dir(project)
    (d / "brief.json").write_text(st["brief"].model_dump_json(indent=1))
    if st["site"] is not None:
        (d / "site.json").write_text(st["site"].model_dump_json(indent=1))
    if st["scene"] is not None:
        st["scene"].to_json(str(d / "scene.json"))


def scene_of(project: str) -> Scene:
    sc = state(project)["scene"]
    if sc is None:
        raise ServiceError("No layout yet - generate options first.")
    return sc


# ----------------------------------------------------------------------------- summaries (compact, for agents and UIs)
def summary(sc: Scene) -> dict:
    m = sc.metrics
    keys = ("it_load_mw", "data_halls", "racks", "gia_m2", "footprint_m2", "site_area_ha", "coverage_pct",
            "spare_land_ha", "substation_to_elec_m", "fuel_to_genyard_m", "road_area_m2")
    return {
        "option": sc.option, "arrangement": sc.variant.get("label"), "score": sc.score,
        "metrics": {k: m[k] for k in keys if k in m},
        "noise": [{k: r[k] for k in ("name", "lp_dba", "limit_dba", "margin_db")} for r in m.get("noise", [])],
        "electrical": {k: sc.designs["electrical"][k] for k in (
            "topology", "blocks", "block_ups_kw", "installed_ups_mw", "normal_loading_pct", "gensets", "genset",
            "transformers", "elec_rooms", "sts_rooms", "substation")},
        "cooling": sc.designs["cooling"],
        "checks": [{"id": c.id, "severity": c.severity, "message": c.message} for c in sc.checks],
        "log": sc.log[-8:],
    }


def option_row(o: Scene) -> dict:
    m = o.metrics
    return {"option": o.option, "score": o.score, "arrangement": o.variant.get("label"),
            "fails": m["checks_failed"], "warns": m["checks_warn"],
            "noise_margins_db": [r["margin_db"] for r in m["noise"]], "spare_land_ha": m["spare_land_ha"],
            "road_area_m2": m["road_area_m2"]}


# ----------------------------------------------------------------------------- actions
def create_project(name: str, brief: Optional[dict] = None) -> dict:
    safe_name(name)
    STATE[name] = {"brief": Brief(**({"project": name} | (brief or {}))), "site": None, "options": [], "scene": None}
    save(name)
    return {"project": name, "brief": STATE[name]["brief"].model_dump()}


def set_site(project: str, site: SiteInput | dict) -> dict:
    st = state(project)
    site = site if isinstance(site, SiteInput) else SiteInput(**({"name": project} | site))
    if len(site.redline) < 3:
        raise ServiceError("Redline needs at least 3 points.")
    st["site"] = site
    save(project)
    from shapely.geometry import Polygon
    return {"project": project, "site_area_ha": round(Polygon(site.redline).area / 1e4, 2),
            "points": len(site.redline), "receptors": len(site.receptors)}


def set_site_from_file(project: str, filename: str, data: bytes, **site_fields) -> dict:
    """Redline from an uploaded .dxf / .geojson / .json / .kml (the file is kept with the project)."""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".dxf", ".geojson", ".json", ".kml"):
        raise ServiceError("Redline must be .dxf, .geojson, .json or .kml")
    path = project_dir(project) / f"redline{suffix}"
    path.write_bytes(data)
    try:
        pts = read_redline(path)
    except Exception as exc:
        raise ServiceError(f"Could not read a closed redline polygon from {filename}: {exc}") from exc
    site_fields["receptors"] = [r if isinstance(r, Receptor) else Receptor(**r) for r in site_fields.get("receptors", [])]
    return set_site(project, SiteInput(name=project, redline=pts, **site_fields))


def update_brief(project: str, changes: dict) -> dict:
    st = state(project)
    st["brief"] = Brief.model_validate(st["brief"].model_dump() | changes)
    n_halls, hall, elec, hr = layout.run_engines(st["brief"], load_catalogue())
    save(project)
    return {"brief": st["brief"].model_dump(), "halls": n_halls, "hall_m": [hall.width_x, hall.depth_y],
            "racks_per_hall": hall.racks_provided, "electrical": elec.summary(), "cooling": hr.summary(),
            "notes": elec.notes + hr.notes}


def generate_options(project: str, max_options: int = 4) -> dict:
    st = state(project)
    if st["site"] is None:
        raise ServiceError("No site yet - set the redline first.")
    opts = layout.generate_options(st["brief"], st["site"], max_options=max_options)
    if not opts:
        return {"project": project, "options": [], "message": "The building does not fit the redline in any "
                "arrangement. Try storeys=2, fewer halls, a smaller setback or a larger site."}
    st["options"], st["scene"] = opts, opts[0]
    save(project)
    for o in opts:
        o.to_json(str(project_dir(project) / f"option_{o.option}.json"))
    return {"project": project, "selected": opts[0].option, "options": [option_row(o) for o in opts],
            "selected_summary": summary(opts[0])}


def select_option(project: str, option: str) -> dict:
    st = state(project)
    if not st["options"]:
        d = project_dir(project)
        st["options"] = [Scene.from_json(str(p)) for p in sorted(d.glob("option_*.json"))]
    for o in st["options"]:
        if o.option == option.upper():
            st["scene"] = o
            save(project)
            return summary(o)
    raise ServiceError(f"No option {option}; generate options first.")


def compare_topologies(brief: Brief | dict, topologies: Optional[list[str]] = None) -> list[dict]:
    b = brief if isinstance(brief, Brief) else Brief(**brief)
    return layout.compare_topologies(b, topologies or ["4M3", "5M4", "N+1 catcher", "2N"])


def move_element(project: str, element_id: str, dx: float, dy: float, whole_group: bool = False) -> dict:
    sc = layout.move_element(scene_of(project), element_id, dx, dy, whole_group)
    save(project)
    return {"moved": element_id, "checks": [c.model_dump() for c in sc.checks], "noise": sc.metrics.get("noise"),
            "score": sc.score}


def export(project: str, formats: Optional[list[str]] = None) -> dict[str, str]:
    sc = scene_of(project)
    st = state(project)
    formats = [f for f in (formats or list(FORMATS)) if f in FORMATS]
    base = str(project_dir(project) / f"{project}_option_{sc.option}")
    out: dict[str, str] = {}
    if "json" in formats:
        sc.to_json(base + ".json")
        out["json"] = base + ".json"
    if "pdf" in formats:
        from .render.report import build_report
        build_report(sc, st["options"] or [sc], base + ".pdf", compare_topologies(st["brief"]))
        out["pdf"] = base + ".pdf"
    if "ifc" in formats:
        from .export.ifc import export_ifc
        out["ifc"] = export_ifc(sc, base + ".ifc")
    if "glb" in formats:
        from .export.mesh import export_glb
        out["glb"] = export_glb(sc, base + ".glb")
    if "dxf" in formats:
        from .export.mesh import export_dxf
        out["dxf"] = export_dxf(sc, base + ".dxf")
    if "editor" in formats:
        from .export.editor import export_editor
        out["editor"] = export_editor(sc, base + "_editor.html")
    return out


def scene_json(project: str) -> dict:
    return json.loads(scene_of(project).model_dump_json())
