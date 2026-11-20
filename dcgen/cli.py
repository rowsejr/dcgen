"""Command line: run a test fit from YAML inputs and write every deliverable.

  dcgen run --brief examples/brief.yaml --site examples/site.yaml --out out
  dcgen topologies --brief examples/brief.yaml
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import layout
from .io import load_brief, load_site


def cmd_run(a) -> None:
    from .workspace import write_outputs
    t0 = time.time()
    brief, site = load_brief(a.brief), load_site(a.site)
    out = Path(a.out)
    opts = layout.generate_options(brief, site, max_options=a.options)
    if not opts:
        raise SystemExit("The building does not fit the redline in any arrangement (try storeys: 2).")
    sc = opts[0]
    print(f"{len(opts)} options in {time.time() - t0:.1f}s; best = {sc.option}: {sc.variant['label']} (score {sc.score})")
    for c in sc.checks:
        if c.severity != "pass":
            print(f"  {c.severity.upper():4s} {c.message}")
    fmts = set(a.formats.split(","))
    write_outputs(opts, out, fmts, brief)
    print(f"Wrote {sorted(fmts)} to {out}/ in {time.time() - t0:.1f}s")


def cmd_topologies(a) -> None:
    rows = layout.compare_topologies(load_brief(a.brief), a.list.split(","))
    print(json.dumps(rows, indent=1))


def _packs(a) -> None:
    import os
    if getattr(a, "packs", None):
        os.environ["DCGEN_PACKS"] = os.pathsep.join(a.packs)


def cmd_library(a) -> None:
    from . import service
    _packs(a)
    if a.action == "init":
        from .library.templates import write_templates
        for f in write_templates(a.target):
            print("wrote", f)
    elif a.action == "docs":
        from .library.templates import fields_markdown
        print(fields_markdown())
    elif a.action == "import":
        rep = service.library_import(a.target, a.type, a.pack, a.mapping, a.entered_by, a.sheet)
        print(json.dumps(rep, indent=1))
    elif a.action == "validate":
        rows = service.library_validate()
        for r in rows:
            print(f"{r['id']} [{r['status']}]: " + "; ".join(r["issues"]))
        print(f"{len(rows)} record(s) with issues; library: {json.dumps(service.library_info()['by_type'])}")
    elif a.action == "approve":
        if not a.checked_by:
            raise SystemExit("approve needs --checked-by (the engineer who checked the records against their sources)")
        for m in service.library_approve(a.target.split(","), a.checked_by):
            print(m)
    elif a.action == "search":
        for r in service.library_search(a.type, a.target or "", a.limit, a.drafts):
            print(r)


def cmd_profile(a) -> None:
    _packs(a)
    brief, site = load_brief(a.brief), load_site(a.site)
    from . import service
    from .library import profiles
    lib = service._lib()
    prof = lib.profiles.get(a.profile)
    if prof is None:
        raise SystemExit(f"No profile {a.profile}; have {sorted(lib.profiles)}")
    brief = brief.model_copy(update=profiles.brief_updates(prof))
    opts = layout.generate_options(brief, site, max_options=1)
    if not opts:
        raise SystemExit("Does not fit the redline.")
    c = opts[0].designs["compliance"]
    print(f"{c['profile']} {c['revision']}: {c['summary']}")
    print(profiles.compliance_csv(c["rows"], {"id": c["profile"], "revision": c["revision"]}))


def cmd_preview(a) -> None:
    from .render.preview import preview_png
    brief, site = load_brief(a.brief), load_site(a.site)
    opts = layout.generate_options(brief, site, max_options=1)
    if not opts:
        raise SystemExit("Does not fit the redline.")
    Path(a.out).write_bytes(preview_png(opts[0], a.view, a.px))
    print(f"wrote {a.out}")


def cmd_whatif(a) -> None:
    import yaml
    brief, site = load_brief(a.brief), load_site(a.site)
    changes = yaml.safe_load(a.changes) if not Path(a.changes).exists() else yaml.safe_load(Path(a.changes).read_text())
    if isinstance(changes, dict):
        changes = [changes]
    for ch in changes:
        print(json.dumps(layout.what_if(brief, site, ch), default=str))


def cmd_peak(a) -> None:
    """Peak PUE build-up and lineup check for a brief (no site needed)."""
    from .workspace import peak_text
    _packs(a)
    brief = load_brief(a.brief)
    upd = {k: v for k, v in (("chw_flow_c", a.chw), ("hr_uplift_k", a.uplift), ("mech_lineups", a.mech)) if v is not None}
    if a.drafts:
        upd["use_drafts"] = True
    print(peak_text(brief.model_copy(update=upd)))


def cmd_project(a) -> None:
    """File-based projects for VS Code / Git (no AI): new, check, run, peak, schema."""
    from . import workspace as ws
    _packs(a)
    if a.action == "new":
        if not a.target:
            raise SystemExit("dcgen project new <name>")
        for f in ws.new_project(a.target, a.root, a.force):
            print("wrote", f)
        print(f"Next: edit projects/{a.target}/brief.yaml and site.yaml, then: dcgen project check projects/{a.target}")
    elif a.action == "check":
        issues = ws.check(a.target or ".")
        print(ws.format_issues(issues) or "no issues")
        if any(i.severity == "error" for i in issues):
            raise SystemExit(1)
    elif a.action == "run":
        t0 = time.time()
        folder = ws.folder_of(a.target or ".")
        issues = [i for i in ws.check(folder) if i.severity == "error"]
        if issues:
            print(ws.format_issues(issues))
            raise SystemExit("Fix the errors above first (dcgen project check).")
        s = ws.run(folder, [f for f in a.formats.split(",") if f], a.options)
        for i in ws.problems(s, folder):
            print(i)
        m = s.get("metrics") or {}
        if s.get("fits"):
            print(f"Best = option {s['best']}: {s['arrangement']}; peak PUE {m.get('peak_pue')}; "
                  f"{m.get('fails')} fail / {m.get('warns')} warn  ({time.time() - t0:.1f}s)")
        print(f"Summary: {folder / 'out' / 'summary.md'}")
    elif a.action == "peak":
        print(ws.peak_text(load_brief(ws.folder_of(a.target or ".") / "brief.yaml")))
    elif a.action == "schema":
        for f in ws.write_schemas(a.target or ws.REPO / "schemas"):
            print("wrote", f)


def cmd_reference(a) -> None:
    _packs(a)
    from . import service
    if a.action == "regress":
        bad = 0
        for r in service.regress():
            print(f"{r['id']}: {'ok' if r['ok'] else 'FAIL'}")
            for row in r["rows"]:
                if row["status"] != "pass":
                    print("   ", row)
                    bad += 1
        raise SystemExit(1 if bad else 0)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dcgen", description="Datacentre Stage 2 test-fit generator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="generate options and deliverables")
    r.add_argument("--brief", required=True)
    r.add_argument("--site", required=True)
    r.add_argument("--out", default="out")
    r.add_argument("--options", type=int, default=4)
    r.add_argument("--formats", default="pdf,ifc,glb,dxf,json,editor")
    r.set_defaults(fn=cmd_run)
    t = sub.add_parser("topologies", help="compare redundancy topologies for a brief")
    t.add_argument("--brief", required=True)
    t.add_argument("--list", default="4M3,5M4,N+1 catcher,2N")
    t.set_defaults(fn=cmd_topologies)
    lib = sub.add_parser("library", help="plant library: init templates, import drafts, validate, approve, search")
    lib.add_argument("action", choices=["init", "docs", "import", "validate", "approve", "search"])
    lib.add_argument("target", nargs="?", default="", help="init: folder | import: file | approve: ids (comma) | search: text")
    lib.add_argument("--type", choices=["chiller", "dry_cooler", "adiabatic_cooler", "water_cooled_chiller", "genset", "transformer",
                                        "elec_room_typical"], help="record type (or put 'type:' in the mapping)")
    lib.add_argument("--pack", help="import: pack folder to write drafts into")
    lib.add_argument("--mapping", help="mapping YAML, or a folder of them (one per sheet)"), lib.add_argument("--entered-by", default=""), lib.add_argument("--checked-by", default="")
    lib.add_argument("--sheet"), lib.add_argument("--limit", type=int, default=20)
    lib.add_argument("--drafts", action="store_true"), lib.add_argument("--packs", nargs="*")
    lib.set_defaults(fn=cmd_library)
    pr = sub.add_parser("profile", help="run a client profile's rules against a brief + site and print the compliance sheet")
    pr.add_argument("--brief", required=True), pr.add_argument("--site", required=True), pr.add_argument("--profile", required=True)
    pr.add_argument("--packs", nargs="*"), pr.set_defaults(fn=cmd_profile)
    rf = sub.add_parser("reference", help="reference designs: regress the engine against the recorded schemes")
    rf.add_argument("action", choices=["regress"]), rf.add_argument("--packs", nargs="*"), rf.set_defaults(fn=cmd_reference)
    pj = sub.add_parser("project", help="file-based projects for VS Code / Git: new | check | run | peak | schema (no AI)")
    pj.add_argument("action", choices=["new", "check", "run", "peak", "schema"])
    pj.add_argument("target", nargs="?", default="", help="new: name | check/run/peak: project folder or a file in it | schema: out folder")
    pj.add_argument("--root", default=".", help="new: repository root (projects/ goes under it)")
    pj.add_argument("--force", action="store_true", help="new: overwrite existing YAML")
    pj.add_argument("--formats", default="json", help="run: json,pdf,ifc,dxf,glb,editor")
    pj.add_argument("--options", type=int, default=4), pj.add_argument("--packs", nargs="*")
    pj.set_defaults(fn=cmd_project)
    pk = sub.add_parser("peak", help="peak PUE build-up and LV lineup check for a brief (no site needed)")
    pk.add_argument("--brief", required=True), pk.add_argument("--chw", type=float), pk.add_argument("--uplift", type=float)
    pk.add_argument("--mech", choices=["auto", "shared", "separate"]), pk.add_argument("--drafts", action="store_true")
    pk.add_argument("--packs", nargs="*"), pk.set_defaults(fn=cmd_peak)
    pv = sub.add_parser("preview", help="write a PNG of the best layout (plan | overview | closeup)")
    pv.add_argument("--brief", required=True), pv.add_argument("--site", required=True), pv.add_argument("--out", default="preview.png")
    pv.add_argument("--view", default="plan", choices=["plan", "overview", "closeup"]), pv.add_argument("--px", type=int, default=1200)
    pv.set_defaults(fn=cmd_preview)
    wi = sub.add_parser("whatif", help="try brief changes (YAML string or file: a list of dicts) and print score/check deltas")
    wi.add_argument("--brief", required=True), wi.add_argument("--site", required=True), wi.add_argument("changes")
    wi.set_defaults(fn=cmd_whatif)
    return ap


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
