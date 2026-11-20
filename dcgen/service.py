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
from .catalogue import for_brief, load_catalogue
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
            "transformers", "elec_rooms", "sts_rooms", "substation", "mech_lineups", "mech_per_it_lineup_kw",
            "mech_allowance_kw", "it_lineup_kva", "genset_max_kva") if k in sc.designs["electrical"]},
        "cooling": sc.designs["cooling"],
        "peak_pue": {k: (sc.designs.get("peak_pue") or {}).get(k) for k in (
            "peak_pue", "air_on_c", "chw_flow_c", "chiller_eer", "eer_source", "mechanical_kw", "electrical_losses_kw",
            "facility_kw", "mech_sizing_kw")},
        "plant_data": [f"{u['role']}: {u['id']} ({u['status']})" for u in sc.designs.get("library", {}).get("used", [])],
        "compliance": (sc.designs.get("compliance") or {}).get("summary"),
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
    n_halls, hall, elec, hr = layout.run_engines(st["brief"], for_brief(st["brief"]))
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
    _push_history(project, "generate_options")
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
            _push_history(project, f"select {o.option}")
            st["scene"] = o
            save(project)
            return summary(o)
    raise ServiceError(f"No option {option}; generate options first.")


def compare_topologies(brief: Brief | dict, topologies: Optional[list[str]] = None) -> list[dict]:
    b = brief if isinstance(brief, Brief) else Brief(**brief)
    return layout.compare_topologies(b, topologies or ["4M3", "5M4", "N+1 catcher", "2N"])


HISTORY_CAP = 20


def _push_history(project: str, label: str) -> None:
    """Snapshot the current scene before any edit so it can be undone (kept in memory, capped)."""
    st = state(project)
    if st["scene"] is None:
        return
    st.setdefault("history", []).append((label, st["scene"].model_dump_json()))
    del st["history"][:-HISTORY_CAP]


def history(project: str) -> list[str]:
    return [lbl for lbl, _ in state(project).get("history", [])]


def undo(project: str) -> dict:
    st = state(project)
    if not st.get("history"):
        raise ServiceError("Nothing to undo.")
    label, snap = st["history"].pop()
    sc = Scene.model_validate_json(snap)
    st["scene"] = sc
    st["options"] = [sc if o.option == sc.option else o for o in st["options"]]
    save(project)
    return {"undone": label, "remaining": len(st["history"]), "summary": summary(sc)}


def _set_scene(project: str, sc: Scene) -> None:
    st = state(project)
    st["scene"] = sc
    st["options"] = [sc if o.option == sc.option else o for o in st["options"]]
    save(project)


def move_element(project: str, element_id: str, dx: float, dy: float, whole_group: bool = False) -> dict:
    sc = scene_of(project)
    _push_history(project, f"move {element_id} ({dx:+.1f},{dy:+.1f})")
    try:
        sc = layout.move_element(sc, element_id, dx, dy, whole_group)
    except KeyError as exc:
        state(project)["history"].pop()
        raise ServiceError(f"No element {element_id!r}.") from exc
    save(project)
    return {"moved": element_id, "checks": [c.model_dump() for c in sc.checks if c.severity != "pass"],
            "noise": sc.metrics.get("noise"), "score": sc.score}


def move_compass(project: str, element_id: str, direction: str | float, distance: float, whole_group: bool = False) -> dict:
    """Move by `distance` m towards a true compass direction (N..NW or degrees, 0 = north, clockwise)."""
    sc = scene_of(project)
    try:
        brg = layout.resolve_bearing(direction)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    dx, dy = layout.bearing_to_local(sc, brg, distance)
    out = move_element(project, element_id, dx, dy, whole_group)
    out["bearing_deg"], out["local_move"] = brg, [round(dx, 2), round(dy, 2)]
    return out


def move_towards(project: str, element_id: str, target_id: str, distance: float) -> dict:
    """Move straight towards another element (negative distance = away)."""
    sc = scene_of(project)
    try:
        dx, dy = layout.move_towards(sc, element_id, target_id, distance)
    except KeyError as exc:
        raise ServiceError(f"Unknown element: {exc}") from exc
    out = move_element(project, element_id, dx, dy)
    out["local_move"] = [round(dx, 2), round(dy, 2)]
    return out


def regenerate(project: str) -> dict:
    """Re-run the engines for the current brief on the selected option, keeping moved (locked) elements."""
    sc = scene_of(project)
    st = state(project)
    _push_history(project, "regenerate")
    new = layout.regenerate(sc.model_copy(update={"brief": st["brief"]}))
    if new is None:
        st["history"].pop()
        raise ServiceError("The building no longer fits the redline in this arrangement; the layout is unchanged.")
    _set_scene(project, new)
    return summary(new)


def what_if(project: str, changes: list[dict]) -> list[dict]:
    """Try brief changes (each a dict of brief fields, incl. overrides) WITHOUT altering the project.
    Returns compact deltas against the selected option's brief; use update_brief to apply one."""
    st = state(project)
    if st["site"] is None:
        raise ServiceError("No site yet - set the redline first.")
    if not changes or len(changes) > 6:
        raise ServiceError("Give between 1 and 6 change sets.")
    base = layout.generate_options(st["brief"], st["site"], max_options=1, k_offsets=2)
    base_sc = base[0] if base else None
    rows = []
    for ch in changes:
        try:
            rows.append(layout.what_if(st["brief"], st["site"], ch, base_sc))
        except Exception as exc:                          # bad override key, invalid field
            rows.append({"changes": ch, "fits": None, "error": str(exc)[:200]})
    return rows


def peak_pue(project: str) -> dict:
    """Peak (design-point) PUE build-up for the selected option: what sizes the electrical plant.
    Annual PUE is not calculated here (that is the PUE tool's job)."""
    sc = scene_of(project)
    pk = sc.designs.get("peak_pue")
    if not pk:
        raise ServiceError("No peak PUE on this layout - regenerate it.")
    e = sc.designs["electrical"]
    return {**pk, "mech_lineups": e.get("mech_lineups"), "mech_per_it_lineup_kw": e.get("mech_per_it_lineup_kw"),
            "mech_allowance_kw": e.get("mech_allowance_kw"), "it_lineup_kva": e.get("it_lineup_kva"),
            "genset_max_kva": e.get("genset_max_kva"), "site_demand_mva": e.get("site_demand_mva"),
            "method": "CUN-CAL-PUE-001 at the design point: chillers at 100% load at design ambient + uplift"}


def suggest_fixes(project: str) -> list[dict]:
    return layout.suggest_fixes(scene_of(project))


def preview(project: str, view: str = "plan", px: int = 1200, highlight: Optional[list[str]] = None) -> bytes:
    from .render.preview import VIEWS, preview_png
    if view not in VIEWS:
        raise ServiceError(f"view must be one of {VIEWS}")
    return preview_png(scene_of(project), view, max(600, min(px, 2400)), highlight)


def brief_schema() -> dict:
    """What an agent or form may set: brief fields with types/defaults, plus every override key (dotted path)."""
    cat = load_catalogue()

    def walk(d: Any, pre: str = "") -> dict:
        out = {}
        for k, v in d.items():
            if k.startswith("_"):
                continue
            path = f"{pre}{k}"
            if isinstance(v, dict):
                out |= walk(v, path + ".")
            elif isinstance(v, (int, float, bool, str)):
                out[path] = v
        return out

    fields = {n: {"type": str(f.annotation).replace("typing.", ""), "default": f.default if f.default is not None else None,
                  "description": f.description or ""} for n, f in Brief.model_fields.items()}
    flat = {k: v for k, v in walk(dict(cat)).items()
            if k.split(".")[0] in ("halls", "layout", "egress", "site", "electrical_rooms", "electrical", "peak_pue")}
    return {"brief_fields": fields, "override_keys": flat,
            "note": "Override keys are catalogue paths (brief.overrides). Values shown are the shipped placeholders; "
                    "changing them is a what-if, not a sign-off."}


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


# ----------------------------------------------------------------------------- plant library, profiles, reference designs
def _lib():
    from . import library
    return library.active()


def library_info() -> dict:
    """Which packs are loaded and what is in them (counts by type and status)."""
    lib = _lib()
    return {"packs": [str(r) for r in lib.roots], "records": len(lib), "by_type": lib.counts(),
            "profiles": sorted(lib.profiles), "reference_designs": [c["id"] for c in lib.cards],
            "issues": lib.issues[:10]}


def library_search(rtype: Optional[str] = None, query: str = "", limit: int = 15, include_drafts: bool = False,
                   **ranges: float) -> list[dict]:
    """Short rows (id, name, status, key numbers). Numeric filters: capacity_kw_min=1200, lw_dba_max=90."""
    return _lib().search(rtype, query, min(max(limit, 1), 50), include_drafts, **ranges)


def library_get(record_id: str) -> dict:
    hit = _lib().get(record_id)
    if hit is None:
        raise ServiceError(f"No library record '{record_id}'")
    t, r = hit
    return {"type": t, **{k: v for k, v in r.items() if not k.startswith("_")}}


def library_import(path: str, rtype: Optional[str], pack_dir: str, mapping: Optional[str] = None, entered_by: str = "",
                   sheet: Optional[str] = None) -> dict:
    """Import a CSV/XLSX as DRAFT records into a pack. Human/CLI action; never approves.
    mapping may be a folder of mappings (one per sheet, each naming its type and sheet)."""
    from .library.importers import import_file, import_with_mappings, load_mapping
    if mapping and Path(mapping).is_dir():
        return {k: r.summary() for k, r in import_with_mappings(path, mapping, pack_dir, entered_by).items()}
    rep = import_file(path, rtype, pack_dir, load_mapping(mapping) if mapping else None, entered_by, sheet=sheet)
    return rep.summary()


def library_validate(include_ok: bool = False) -> list[dict]:
    from .library.validate import validate_record
    out = []
    for t, recs in _lib().records.items():
        for rid, r in recs.items():
            issues = validate_record(t, r)
            if issues or include_ok:
                out.append({"id": rid, "type": t, "status": r["status"], "issues": [f"{lvl}: {m}" for lvl, m in issues]})
    return out


def library_approve(record_ids: list[str], checked_by: str, status: str = "approved") -> list[str]:
    """Engineer sign-off. Deliberately not exposed as an agent tool."""
    msgs = _lib().set_status(record_ids, status, checked_by)
    from . import library
    library._CACHE.clear()
    return msgs


def plant_options(project: str, top: int = 5) -> dict:
    """Heat rejection candidates ranked for this project's brief (runners-up included) plus the genset and
    transformer the electrical engine would pick, from the catalogue this project actually sees."""
    from .engines.cooling import rank_heat_rejection
    from .engines.electrical import design_electrical
    from .engines.halls import design_hall  # noqa: F401
    b = state(project)["brief"]
    cat = for_brief(b)
    n_halls, _, elec, hr = layout.run_engines(b, cat)
    lu = layout.lineup_limits(cat, elec.plan, n_halls, sum(v for k, v in hr.peak.mech.items() if k != "heat_rejection"))
    ranked = rank_heat_rejection(b, cat, q_kw=hr.q_kw, lineups=lu)
    meta = cat.get("_library") or {}
    return {"project": project, "preference": b.hr_prefer, "profile": meta.get("profile"),
            "drafts_allowed": b.use_drafts, "library_sections": meta.get("replaced", []),
            "heat_rejection": [{"id": c.unit.get("record_id", c.key), "label": c.unit["label"], "status": c.unit.get("status", "placeholder"),
                                "units": c.n_total, "capacity_kw_each": round(c.cap_kw), "footprint_m2": round(c.footprint_m2),
                                "total_lw_dba": round(c.lw_total_dba, 1), "margin_pct": round(c.margin_pct, 1),
                                "eer_at_design": c.peak.eer.value if c.peak and c.peak.eer else None,
                                "input_kw_each": round(c.peak.unit_full_kw) if c.peak else None,
                                "fits_it_lineups": c.shared_ok, "why_not": c.shared_why or None,
                                "score": round(c.score, 3)} for c in ranked[:top]],
            "selected": {"id": hr.unit.get("record_id", hr.unit_key), "mech_lineups": hr.mech_mode},
            "genset": {"id": elec.genset.get("record_id", elec.genset_key), "label": elec.genset["label"], "per_block": elec.gensets_per_block},
            "transformer": {"id": elec.transformer.get("record_id", elec.tx_key), "label": elec.transformer["label"],
                            "per_block": elec.tx_per_block}}


def profiles_list() -> list[dict]:
    return [{"id": p["id"], "revision": p.get("revision", ""), "rules": len(p.get("rules", [])),
             "approved_manufacturers": (p.get("approved") or {}).get("manufacturers")} for p in _lib().profiles.values()]


def apply_profile(project: str, profile_id: str) -> dict:
    """Set the brief from a client profile (defaults + project catalogue overrides + plant filter)."""
    from .library import profiles
    prof = _lib().profiles.get(profile_id)
    if prof is None:
        raise ServiceError(f"No client profile '{profile_id}'. Available: {sorted(_lib().profiles)}")
    up = profiles.brief_updates(prof)
    up["overrides"] = {**state(project)["brief"].overrides, **up.get("overrides", {})}
    return update_brief(project, up) | {"profile": {"id": prof["id"], "revision": prof.get("revision", "")}}


def compliance(project: str) -> dict:
    sc = scene_of(project)
    c = sc.designs.get("compliance")
    if not c:
        raise ServiceError("No client profile is set on this project (apply_profile first), or no layout yet.")
    return c


def compliance_csv(project: str) -> str:
    from .library import profiles
    c = compliance(project)
    return profiles.compliance_csv(c["rows"], {"id": c["profile"], "revision": c["revision"]})


def reference_find(project: str, n: int = 3) -> list[dict]:
    from .library import reference
    cards = _lib().cards
    return [{"id": c["id"], "name": c.get("name", ""), "metrics": c.get("metrics", {}), "files": c.get("files", [])}
            for c in reference.similar(state(project)["brief"], cards, n)]


def benchmark(project: str) -> dict:
    from .library import reference
    cards = _lib().cards
    if not cards:
        raise ServiceError("No reference designs loaded (add a pack with reference/*.yaml).")
    return reference.benchmark(scene_of(project), cards)


def regress(ids: Optional[list[str]] = None) -> list[dict]:
    from .library import reference
    cards = [c for c in _lib().cards if not ids or c["id"] in ids]
    return [reference.regress_card(c) for c in cards]
