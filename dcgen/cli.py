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
    t0 = time.time()
    brief, site = load_brief(a.brief), load_site(a.site)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    opts = layout.generate_options(brief, site, max_options=a.options)
    if not opts:
        raise SystemExit("The building does not fit the redline in any arrangement (try storeys: 2).")
    sc = opts[0]
    print(f"{len(opts)} options in {time.time() - t0:.1f}s; best = {sc.option}: {sc.variant['label']} (score {sc.score})")
    for c in sc.checks:
        if c.severity != "pass":
            print(f"  {c.severity.upper():4s} {c.message}")
    stem = out / f"testfit_option_{sc.option}"
    fmts = set(a.formats.split(","))
    if "json" in fmts:
        sc.to_json(f"{stem}.json")
        for o in opts:
            o.to_json(str(out / f"option_{o.option}.json"))
    if "pdf" in fmts:
        from .render.report import build_report
        topo = layout.compare_topologies(brief, ["4M3", "5M4", "N+1 catcher", "2N"])
        build_report(sc, opts, f"{stem}.pdf", topo)
    if "ifc" in fmts:
        from .export.ifc import export_ifc
        export_ifc(sc, f"{stem}.ifc")
    if "glb" in fmts:
        from .export.mesh import export_glb
        export_glb(sc, f"{stem}.glb")
    if "dxf" in fmts:
        from .export.mesh import export_dxf
        export_dxf(sc, f"{stem}.dxf")
    if "editor" in fmts:
        from .export.editor import export_editor
        export_editor(sc, f"{stem}_editor.html")
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


def main() -> None:
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
    lib.add_argument("--type", choices=["chiller", "dry_cooler", "adiabatic_cooler", "genset", "transformer", "elec_room_typical"])
    lib.add_argument("--pack", help="import: pack folder to write drafts into")
    lib.add_argument("--mapping"), lib.add_argument("--entered-by", default=""), lib.add_argument("--checked-by", default="")
    lib.add_argument("--sheet"), lib.add_argument("--limit", type=int, default=20)
    lib.add_argument("--drafts", action="store_true"), lib.add_argument("--packs", nargs="*")
    lib.set_defaults(fn=cmd_library)
    pr = sub.add_parser("profile", help="run a client profile's rules against a brief + site and print the compliance sheet")
    pr.add_argument("--brief", required=True), pr.add_argument("--site", required=True), pr.add_argument("--profile", required=True)
    pr.add_argument("--packs", nargs="*"), pr.set_defaults(fn=cmd_profile)
    rf = sub.add_parser("reference", help="reference designs: regress the engine against the recorded schemes")
    rf.add_argument("action", choices=["regress"]), rf.add_argument("--packs", nargs="*"), rf.set_defaults(fn=cmd_reference)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
