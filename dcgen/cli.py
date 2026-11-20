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
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
