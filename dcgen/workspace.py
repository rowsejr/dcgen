"""File-based projects for VS Code and Git: projects/<name>/brief.yaml + site.yaml -> projects/<name>/out/.

No AI anywhere in here. A person or GitHub Copilot writes the two YAML files; these functions check
them (with file:line:col messages for the VS Code Problems panel), run the deterministic engine, and
write out/summary.md + out/summary.json for the next round of edits ("push" YAML in, "pull" the
summary back). The YAML and the summaries are small text files, so they live in Git; the heavy
deliverables (PDF, IFC, DXF, GLB) stay local unless someone commits them on purpose.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from . import __version__, layout
from .io import load_brief, load_site, read_redline
from .schema import Brief, SiteInput

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"
FORMATS = ("json", "pdf", "ifc", "dxf", "glb", "editor")
SUMMARY_KEYS = ("peak_pue", "hr_units", "gensets", "transformers", "mech_lineups", "fails", "warns", "footprint_m2",
                "spare_land_ha", "worst_noise_margin_db", "score")


# ----------------------------------------------------------------------------- locations
def folder_of(path: str | Path) -> Path:
    """A project folder from the folder itself or any file in it (VS Code passes ${file})."""
    p = Path(path).expanduser().resolve()
    if p.is_file():
        p = p.parent
    if p.name == "out":
        p = p.parent
    return p


def new_project(name: str, root: str | Path = ".", force: bool = False) -> list[Path]:
    """projects/<name>/ with brief.yaml and site.yaml started from the examples, schema-linked for VS Code."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name or ""):
        raise ValueError("Project names: letters, digits, '-', '_' or '.', starting with a letter or digit")
    root = Path(root).resolve()
    d = root / "projects" / name
    if d.exists() and any(d.iterdir()) and not force:
        raise FileExistsError(f"{d} already exists (use --force to overwrite the YAML)")
    d.mkdir(parents=True, exist_ok=True)
    rel = _rel_schemas(d, root)
    brief = (EXAMPLES / "brief.yaml").read_text().splitlines()
    brief = [f'project: "{name}"' if ln.startswith("project:") else ln for ln in brief]
    head_b = [f"# yaml-language-server: $schema={rel}/brief.schema.json",
              "# Brief for one test fit. Hover a key in VS Code for its meaning; every field is in the schema.",
              "# Edit, then run the task 'dcgen: check this project' and 'dcgen: run this project'."]
    site = (EXAMPLES / "site.yaml").read_text().splitlines()
    head_s = [f"# yaml-language-server: $schema={rel}/site.schema.json",
              "# Site in project grid metres (or redline_file: redline.dxf | .geojson | .kml next to this file)."]
    files = {d / "brief.yaml": head_b + [ln for ln in brief if not ln.startswith("# Example client brief")],
             d / "site.yaml": head_s + site}
    readme = d / "README.md"
    files[readme] = [f"# {name}", "", "Test-fit inputs: `brief.yaml`, `site.yaml`. Results: `out/summary.md` "
                     "(committed) and the deliverables in `out/` (not committed).", "",
                     "Decisions and assumptions (keep this up to date; it travels with the project in Git):", "", "- "]
    for p, lines in files.items():
        if p.exists() and p.name == "README.md":
            continue
        p.write_text("\n".join(lines).rstrip() + "\n")
    return sorted(files)


def _rel_schemas(d: Path, root: Path) -> str:
    try:
        rel = Path(*([".."] * len(d.relative_to(root).parts))) / "schemas"
        return rel.as_posix()
    except ValueError:
        return (REPO / "schemas").as_posix()


# ----------------------------------------------------------------------------- checking (VS Code Problems)
@dataclass
class Issue:
    file: Path
    line: int
    col: int
    severity: str        # error | warning | info
    message: str

    def __str__(self) -> str:                       # matched by the problem matcher in .vscode/tasks.json
        return f"{self.file}:{self.line}:{self.col}: {self.severity}: {self.message}"


def _locate(text: str, loc: tuple) -> tuple[int, int]:
    """Line/column (1-based) of a key path in YAML text, falling back to the nearest parent."""
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return 1, 1
    best = (1, 1)
    for key in loc:
        if node is None:
            break
        if isinstance(node, yaml.MappingNode):
            hits = [(k, v) for k, v in node.value if str(k.value) == str(key)]
            hit = hits[-1] if hits else None                   # YAML keeps the last of duplicate keys
            if hit is None:
                break
            best = (hit[0].start_mark.line + 1, hit[0].start_mark.column + 1)
            node = hit[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(key, int) and key < len(node.value):
            node = node.value[key]
            best = (node.start_mark.line + 1, node.start_mark.column + 1)
        else:
            break
    return best


def _load_yaml(path: Path, issues: list[Issue]) -> tuple[Optional[dict], str]:
    if not path.exists():
        issues.append(Issue(path, 1, 1, "error", f"{path.name} is missing (dcgen project new <name> starts one)"))
        return None, ""
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        issues.append(Issue(path, (mark.line + 1) if mark else 1, (mark.column + 1) if mark else 1, "error",
                            f"YAML syntax: {getattr(exc, 'problem', exc)}"))
        return None, text
    if not isinstance(data, dict):
        issues.append(Issue(path, 1, 1, "error", "expected a mapping of keys to values"))
        return None, text
    root = yaml.compose(text)
    if isinstance(root, yaml.MappingNode):
        seen: dict[str, int] = {}
        for k, _ in root.value:
            if str(k.value) in seen:
                issues.append(Issue(path, k.start_mark.line + 1, k.start_mark.column + 1, "warning",
                                    f"duplicate key '{k.value}' (line {seen[str(k.value)]} is ignored: the last one wins)"))
            seen[str(k.value)] = k.start_mark.line + 1
    return data, text


def _unknown(path: Path, text: str, data: dict, known: set[str], issues: list[Issue]) -> None:
    for k in data:
        if k not in known:
            near = difflib.get_close_matches(str(k), sorted(known), n=1)
            hint = f" - did you mean '{near[0]}'?" if near else ""
            ln, col = _locate(text, (k,))
            issues.append(Issue(path, ln, col, "error", f"unknown key '{k}' would be ignored{hint}"))


def _validation(path: Path, text: str, exc, issues: list[Issue]) -> None:
    for e in exc.errors():
        loc = tuple(e.get("loc", ()))
        ln, col = _locate(text, loc)
        where = ".".join(str(x) for x in loc) or "(file)"
        issues.append(Issue(path, ln, col, "error", f"{where}: {e.get('msg')}"))


def check(path: str | Path) -> list[Issue]:
    """Everything that can be said about a project's YAML without laying it out (fast, < 1 s)."""
    from pydantic import ValidationError
    folder = folder_of(path)
    bp, sp = folder / "brief.yaml", folder / "site.yaml"
    issues: list[Issue] = []
    if not bp.exists() and not sp.exists():
        return [Issue(folder / "brief.yaml", 1, 1, "error", "no brief.yaml or site.yaml here: open a file inside "
                      "projects/<name>/ and run the task again, or start one with 'dcgen: new project'")]

    bdata, btext = _load_yaml(bp, issues)
    brief = None
    if bdata is not None:
        _unknown(bp, btext, bdata, set(Brief.model_fields), issues)
        try:
            brief = Brief(**{k: v for k, v in bdata.items() if k in Brief.model_fields})
        except ValidationError as exc:
            _validation(bp, btext, exc, issues)

    sdata, stext = _load_yaml(sp, issues)
    site = None
    if sdata is not None:
        _unknown(sp, stext, sdata, set(SiteInput.model_fields) | {"redline_file"}, issues)
        d = {k: v for k, v in sdata.items() if k in SiteInput.model_fields}
        if "redline_file" in sdata:
            rf = folder / str(sdata["redline_file"])
            ln, col = _locate(stext, ("redline_file",))
            if not rf.exists():
                issues.append(Issue(sp, ln, col, "error", f"redline_file {rf.name} not found next to site.yaml"))
            else:
                try:
                    d["redline"] = read_redline(rf)
                except Exception as exc:                 # noqa: BLE001 - any reader failure is a user-facing issue
                    issues.append(Issue(sp, ln, col, "error", f"could not read a closed polygon from {rf.name}: {exc}"))
        elif "redline" not in sdata:
            issues.append(Issue(sp, 1, 1, "error", "give either redline (list of [x, y]) or redline_file"))
        if "redline" in d:
            try:
                site = SiteInput(**d)
            except ValidationError as exc:
                _validation(sp, stext, exc, issues)
    if site is not None:
        _site_geometry(sp, stext, site, issues)

    if brief is not None:
        _engine_feedback(bp, btext, brief, issues)
    return issues


def _site_geometry(sp: Path, text: str, site: SiteInput, issues: list[Issue]) -> None:
    from shapely.geometry import Point, Polygon
    rl_line = _locate(text, ("redline",))
    if len(site.redline) < 3:
        issues.append(Issue(sp, *rl_line, "error", "redline needs at least 3 points"))
        return
    poly = Polygon(site.redline)
    if not poly.is_valid:
        issues.append(Issue(sp, *rl_line, "error", "redline crosses itself - put the points in order round the boundary"))
        return
    ha = poly.area / 1e4
    if ha < 0.5 or ha > 500:
        issues.append(Issue(sp, *rl_line, "warning", f"redline encloses {ha:.2f} ha - metres expected (project grid / OSGB)"))
    for i, (x, y) in enumerate(site.access_points):
        dist = poly.exterior.distance(Point(x, y))
        if dist > 25:
            issues.append(Issue(sp, *_locate(text, ("access_points", i)), "warning",
                                f"access point {i + 1} is {dist:.0f} m from the redline (expected on the boundary)"))
    for i, r in enumerate(site.receptors):
        if poly.contains(Point(r.x, r.y)):
            issues.append(Issue(sp, *_locate(text, ("receptors", i)), "warning",
                                f"receptor '{r.name}' is inside the redline"))


def _engine_feedback(bp: Path, text: str, brief: Brief, issues: list[Issue]) -> None:
    """Catalogue resolution (overrides, profile, forced units) and the plant engines: fast feedback in the editor."""
    from .catalogue import for_brief
    try:
        cat = for_brief(brief)
    except KeyError as exc:
        key = "profile" if "profile" in str(exc).lower() else "overrides"
        issues.append(Issue(bp, *_locate(text, (key,)), "error", str(exc).strip("'\"")))
        return
    for field, section in (("hr_unit", "heat_rejection"), ("genset_unit", "gensets"), ("tx_unit", "transformers")):
        val = getattr(brief, field)
        if val and val not in cat[section]:
            near = difflib.get_close_matches(val, list(cat[section]), n=1)
            issues.append(Issue(bp, *_locate(text, (field,)), "error",
                                f"{field} '{val}' is not in this project's catalogue" + (f" - did you mean '{near[0]}'?" if near else "")
                                + ("" if brief.use_drafts else " (draft records need use_drafts: true)")))
            return
    try:
        n, hall, elec, hr = layout.run_engines(brief, cat)
    except (ValueError, KeyError) as exc:
        issues.append(Issue(bp, *_locate(text, ("ambient_design_c",)), "error", str(exc)))
        return
    pk = hr.peak.summary() if hr.peak else {}
    e = elec.summary()
    eer = f", EER {pk['chiller_eer']:.2f}" if pk.get("chiller_eer") else ""
    issues.append(Issue(bp, *_locate(text, ("it_load_mw",)), "info",
                        f"{n} halls; {hr.n_total} x {hr.unit['label']}{eer}; mechanical lineups {e['mech_lineups']}; "
                        f"{e['gensets']} gensets; peak PUE {pk.get('peak_pue', 0):.3f}"))
    if e["mech_lineups"] == "separate" and brief.mech_lineups == "auto":
        issues.append(Issue(bp, *_locate(text, ("mech_lineups",)), "info",
                            "chillers do not fit the IT lineups' mechanical allowance: separate mechanical lineups "
                            f"({hr.shared_why})"[:300]))


def format_issues(issues: list[Issue]) -> str:
    return "\n".join(str(i) for i in issues)


# ----------------------------------------------------------------------------- running
def write_outputs(opts: list, out_dir: str | Path, formats, brief: Brief) -> list[Path]:
    """Deliverables for the best option (and the option JSONs). Shared by `dcgen run` and `dcgen project run`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sc = opts[0]
    stem = out / f"testfit_option_{sc.option}"
    fmts = set(formats)
    files: list[Path] = []
    if "json" in fmts:
        sc.to_json(f"{stem}.json")
        files.append(Path(f"{stem}.json"))
        for o in opts:
            o.to_json(str(out / f"option_{o.option}.json"))
    if "pdf" in fmts:
        from .render.report import build_report
        topo = layout.compare_topologies(brief, ["4M3", "5M4", "N+1 catcher", "2N"])
        build_report(sc, opts, f"{stem}.pdf", topo)
        files.append(Path(f"{stem}.pdf"))
    if "ifc" in fmts:
        from .export.ifc import export_ifc
        export_ifc(sc, f"{stem}.ifc")
        files.append(Path(f"{stem}.ifc"))
    if "glb" in fmts:
        from .export.mesh import export_glb
        export_glb(sc, f"{stem}.glb")
        files.append(Path(f"{stem}.glb"))
    if "dxf" in fmts:
        from .export.mesh import export_dxf
        export_dxf(sc, f"{stem}.dxf")
        files.append(Path(f"{stem}.dxf"))
    if "editor" in fmts:
        from .export.editor import export_editor
        export_editor(sc, f"{stem}_editor.html")
        files.append(Path(f"{stem}_editor.html"))
    return files


def _metrics(sc) -> dict:
    m, d = sc.metrics, sc.designs
    noise = [r["margin_db"] for r in m.get("noise", [])]
    e = d.get("electrical", {})
    return {
        "peak_pue": (d.get("peak_pue") or {}).get("peak_pue"),
        "hr_units": d.get("cooling", {}).get("total"),
        "gensets": e.get("gensets"), "transformers": e.get("transformers"), "mech_lineups": e.get("mech_lineups"),
        "fails": m.get("checks_failed"), "warns": m.get("checks_warn"),
        "footprint_m2": m.get("footprint_m2"), "spare_land_ha": m.get("spare_land_ha"),
        "worst_noise_margin_db": min(noise) if noise else None, "score": sc.score,
    }


def run(path: str | Path, formats=("json",), max_options: int = 4) -> dict:
    """Lay the project out and write out/summary.json + out/summary.md (and the chosen deliverables)."""
    from . import service
    folder = folder_of(path)
    brief, site = load_brief(folder / "brief.yaml"), load_site(folder / "site.yaml")
    out = folder / "out"
    out.mkdir(parents=True, exist_ok=True)
    prev = None
    if (out / "summary.json").exists():
        try:
            prev = json.loads((out / "summary.json").read_text())
        except json.JSONDecodeError:
            prev = None
    opts = layout.generate_options(brief, site, max_options=max_options)
    now = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    s: dict[str, Any] = {"project": brief.project, "folder": folder.name, "run_at": now, "dcgen": __version__,
                         "inputs": _inputs(brief, site), "fits": bool(opts)}
    if not opts:
        s["message"] = ("The building does not fit the redline in any arrangement. Try storeys: 2, fewer halls, a "
                        "smaller boundary_setback_m or a larger site.")
    else:
        sc = opts[0]
        files = write_outputs(opts, out, formats, brief)
        s.update({
            "best": sc.option, "arrangement": sc.variant.get("label"), "variants_tested": sc.metrics.get("variants_tested"),
            "metrics": _metrics(sc), "summary": service.summary(sc),
            "options": [service.option_row(o) for o in opts],
            "levers": layout.suggest_fixes(sc),
            "files": [p.name for p in files],
        })
    if prev and prev.get("metrics") and s.get("metrics"):
        s["changes"] = {k: [prev["metrics"].get(k), s["metrics"].get(k)] for k in SUMMARY_KEYS
                        if prev["metrics"].get(k) != s["metrics"].get(k)}
        s["previous_run_at"] = prev.get("run_at")
    (out / "summary.json").write_text(json.dumps(s, indent=1, default=str))
    (out / "summary.md").write_text(summary_md(s))
    return s


def _inputs(brief: Brief, site: SiteInput) -> dict:
    keys = ("it_load_mw", "hall_it_mw", "storeys", "rack_kw", "cooling", "topology", "ambient_design_c", "hr_uplift_k",
            "chw_flow_c", "mech_lineups", "hr_redundancy", "hr_location", "profile", "use_drafts")
    from shapely.geometry import Polygon
    return {**{k: getattr(brief, k) for k in keys}, "site_ha": round(Polygon(site.redline).area / 1e4, 2),
            "receptors": len(site.receptors)}


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        t = f"{v:,.3f}" if abs(v) < 10 else f"{v:,.1f}"
        return t.rstrip("0").rstrip(".") if "." in t else t
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def summary_md(s: dict) -> str:
    """The page Copilot (or a colleague) reads before the next edit."""
    L = [f"# {s['project']} - test fit summary", "",
         f"Run {s['run_at']} with dcgen {s['dcgen']} (no AI involved). Inputs: `brief.yaml`, `site.yaml`.", ""]
    if not s.get("fits"):
        L += ["**No arrangement fits.** " + s.get("message", ""), ""]
        return "\n".join(L) + "\n"
    m, sm = s["metrics"], s["summary"]
    c, e, pk = sm["cooling"], sm["electrical"], sm.get("peak_pue") or {}
    noise_txt = ", ".join("%s %+.1f" % (r["name"], r["margin_db"]) for r in sm["noise"]) or "-"
    L += [f"Best of {s.get('variants_tested', '?')} arrangements: **option {s['best']}**, {s['arrangement']}.", "",
          "## Result", "", "| Item | Value |", "|---|---|",
          f"| Peak PUE (design point) | {_fmt(pk.get('peak_pue'))} at {_fmt(pk.get('air_on_c'))} °C air-on, CHW {_fmt(pk.get('chw_flow_c'))} °C |",
          f"| Heat rejection | {c['total']} x {c['unit']} ({c['duty']} duty), EER {_fmt(c.get('eer_at_design'))} "
          f"({c.get('eer_source') or '-'}), {_fmt(c.get('unit_input_at_design_kw'))} kW input each |",
          f"| Mechanical lineups | {e.get('mech_lineups')}"
          + (f", {_fmt(e.get('mech_per_it_lineup_kw'))} kW on busiest IT lineup (allowance {_fmt(e.get('mech_allowance_kw'))})"
             if e.get("mech_lineups") == "shared" else "") + " |",
          f"| Electrical | {e['topology']}, {e['blocks']} x {e['block_ups_kw']:,} kW blocks, {e['gensets']} x {e['genset']}, "
          f"{e['transformers']} transformers, {e['substation']} intake |",
          f"| Site | {_fmt(sm['metrics'].get('site_area_ha'))} ha, coverage {_fmt(sm['metrics'].get('coverage_pct'))} %, "
          f"spare {_fmt(sm['metrics'].get('spare_land_ha'))} ha |",
          f"| Noise margins (dB) | {noise_txt} |",
          f"| Checks | {m['fails']} fail, {m['warns']} warn |", ""]
    bad = [x for x in sm["checks"] if x["severity"] in ("fail", "warn")]
    if bad:
        L += ["## Checks needing attention", ""] + [f"- **{x['severity'].upper()}** `{x['id']}`: {x['message']}" for x in bad] + [""]
    if s.get("changes"):
        L += [f"## Changes since the last run ({s.get('previous_run_at', '?')})", "", "| Metric | Before | Now |", "|---|---|---|"]
        L += [f"| {k} | {_fmt(a)} | {_fmt(b)} |" for k, (a, b) in s["changes"].items()] + [""]
    L += ["## Options", "", "| Opt | Arrangement | Fails | Warns | Noise margins dB | Spare ha | Score |", "|---|---|---|---|---|---|---|"]
    for o in s["options"]:
        margins = ", ".join("%+.1f" % x for x in o["noise_margins_db"])
        L.append(f"| {o['option']} | {o['arrangement']} | {o['fails']} | {o['warns']} | "
                 f"{margins} | {_fmt(o['spare_land_ha'])} | {_fmt(o['score'])} |")
    L.append("")
    if s.get("levers"):
        L += ["## Levers to try next", ""]
        for lv in s["levers"]:
            if lv["levers"]:
                L.append(f"- `{lv['check']}`: " + "; ".join(lv["levers"][:3]))
        L.append("")
    plant = sm.get("plant_data") or []
    if plant:
        L += ["## Plant data used", ""] + [f"- {p}" for p in plant] + [""]
    L += ["## Files", ""] + [f"- `out/{f}`" for f in s.get("files", [])] + ["",
          "_Indicative RIBA Stage 2 test fit. Catalogue values are placeholders until the team signs them off; noise is a "
          "screening estimate; peak PUE is a design-point figure for plant sizing (annual PUE: the PUE tool)._", ""]
    return "\n".join(L)


def problems(s: dict, folder: Path) -> list[Issue]:
    """Layout check results as Problems-panel entries anchored to brief.yaml."""
    bp = folder / "brief.yaml"
    out = []
    for x in (s.get("summary") or {}).get("checks", []):
        if x["severity"] in ("fail", "warn"):
            out.append(Issue(bp, 1, 1, "error" if x["severity"] == "fail" else "warning", f"[{x['id']}] {x['message']}"))
    if not s.get("fits"):
        out.append(Issue(folder / "site.yaml", 1, 1, "error", s.get("message", "does not fit")))
    return out


# ----------------------------------------------------------------------------- peak PUE report (no site needed)
def peak_text(brief: Brief) -> str:
    from .catalogue import for_brief
    n, hall, elec, hr = layout.run_engines(brief, for_brief(brief))
    pk = hr.peak.summary()
    e = elec.summary()
    lines = [f"Peak PUE {pk['peak_pue']:.3f} at {pk['air_on_c']:g} C air-on, CHW {pk['chw_flow_c']:g} C",
             f"  heat rejection: {hr.n_total} x {hr.unit['label']} ({hr.unit.get('record_id', hr.unit_key)})"
             + (f", EER {pk['chiller_eer']:.2f} ({pk['eer_source']}), {pk['unit_full_input_kw']:.0f} kW input each"
                if pk["chiller_eer"] else ""),
             f"  mechanical lineups: {e['mech_lineups']}" + (
                 f", {e['mech_per_it_lineup_kw']} kW on busiest IT lineup (allowance {e['mech_allowance_kw']})"
                 if e["mech_lineups"] == "shared" else f", {e.get('mech_lineup_count', 0)} x {e.get('mech_genset', '')}"),
             f"  gensets {e['gensets']} x {e['genset']}; IT lineup {e['it_lineup_kva']} kVA (max genset {e['genset_max_kva']} kVA)"]
    for k, v in pk["breakdown_kw"].items():
        if v:
            lines.append(f"    {k:20s} {v:10,.0f} kW   pPUE {1 + v / pk['it_kw']:.4f}")
    lines += [f"  - {x}" for x in hr.notes]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- JSON schemas for the YAML (VS Code)
def brief_schema() -> dict:
    s = Brief.model_json_schema()
    s.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "dcgen brief",
              "description": "Client requirements and design parameters for one test fit. Every default is a placeholder.",
              "additionalProperties": False})
    return s


def site_schema() -> dict:
    s = SiteInput.model_json_schema()
    s["properties"]["redline_file"] = {"type": "string", "title": "Redline File",
                                       "description": "DXF / GeoJSON / KML with the redline polygon, next to site.yaml "
                                                      "(instead of redline)"}
    s["required"] = [r for r in s.get("required", []) if r != "redline"]
    s["anyOf"] = [{"required": ["redline"]}, {"required": ["redline_file"]}]
    s.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "dcgen site",
              "description": "Site boundary, access, grid connection and noise receptors in project grid metres.",
              "additionalProperties": False})
    return s


def write_schemas(out_dir: str | Path = REPO / "schemas") -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for name, sch in (("brief", brief_schema()), ("site", site_schema())):
        p = out / f"{name}.schema.json"
        p.write_text(json.dumps(sch, indent=1) + "\n")
        files.append(p)
    return files
