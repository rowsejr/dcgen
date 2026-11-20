"""Client profiles and clause-referenced rules -> compliance sheet.

A profile is a YAML file (`<pack>/clients/<id>/profile.yaml`) that captures one client standard at one
revision. It does three jobs: sets defaults for the brief, restricts which plant may be used, and
lists rules that are checked against every layout and reported with their clause reference.

  id: acme-standard
  revision: "Rev 7, 2025-03"
  brief_defaults: {topology: "5M4", fuel_autonomy_h: 48, hr_redundancy: "N+2"}
  catalogue_overrides: {"halls.corridor_depth": 4.5, "egress.plant_room_exits": true}
  approved: {manufacturers: [Acme Cooling], record_ids: [], exclude_ids: []}
  rules:
    - {id: R1, clause: "ACME-STD 4.2.3", text: "Gensets 15 m from the boundary", severity: fail,
       check: {type: min_distance, a: {kind: genset}, b: boundary, min: 15}}
    - {id: R2, clause: "ACME-STD 9.1", text: "Fuel delivery turning circle", check: {type: manual}}
"""
from __future__ import annotations

import csv
import io
import operator
from pathlib import Path
from typing import Any, Optional

import numpy as np
import shapely
import yaml
from shapely.geometry import Polygon

OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
       "==": operator.eq, "!=": operator.ne}


def load_profiles(roots) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for root in roots:
        for path in sorted(Path(root).rglob("*.yaml")):
            if path.name != "profile.yaml" and not path.name.endswith(".profile.yaml"):
                continue
            d = yaml.safe_load(path.read_text()) or {}
            if "id" in d:
                d["_file"] = str(path)
                out[d["id"]] = d
    return out


def catalogue_filters(profile: Optional[dict]) -> dict:
    a = (profile or {}).get("approved", {}) or {}
    return {"manufacturers": a.get("manufacturers") or None, "allow_ids": a.get("record_ids") or None,
            "exclude_ids": a.get("exclude_ids") or None}


def brief_updates(profile: dict) -> dict:
    """Fields to set on the brief when a profile is applied (the brief stays the visible truth)."""
    up = dict(profile.get("brief_defaults", {}))
    up["profile"] = profile["id"]
    if profile.get("catalogue_overrides"):
        up["overrides"] = dict(profile["catalogue_overrides"])
    return up


# ---------------------------------------------------------------- rule evaluation
def _get(path: str, ctx: dict) -> Any:
    node: Any = ctx
    for k in path.split("."):
        node = node[int(k)] if isinstance(node, list) else node[k]
    return node


def _select(scene, sel: dict):
    kinds = sel.get("kinds") or ([sel["kind"]] if "kind" in sel else None)
    out = [e for e in scene.elements if (not kinds or e.kind in kinds) and (not sel.get("id") or e.id == sel["id"])]
    return out


def _geom(scene, b) -> Optional[Any]:
    if b == "boundary":
        return scene.redline_poly().exterior
    if b == "building":
        from ..engines.noise import building_polygon
        return building_polygon(scene)
    if b == "receptors":
        pts = [scene.frame.to_local(r.x, r.y) for r in scene.site.receptors]
        return shapely.MultiPoint(pts) if pts else None
    if isinstance(b, dict):
        els = _select(scene, b)
        return shapely.union_all([e.footprint() for e in els]) if els else None
    raise ValueError(f"unknown distance target {b!r}")


def _cmp(value: float, op: str, limit: float) -> bool:
    return OPS[op](value, limit)


def _run(chk: dict, scene, cat) -> tuple[str, Any, Any, list[str]]:
    t = chk.get("type", "manual")
    if t == "manual":
        return "manual", None, None, []
    if t == "metric":
        ctx = {"designs": scene.designs, "metrics": scene.metrics, "brief": scene.brief.model_dump()}
        v = _get(chk["path"], ctx)
        return ("pass" if _cmp(v, chk["op"], chk["value"]) else "fail"), v, f"{chk['op']} {chk['value']}", []
    if t == "catalogue":
        v = _get(chk["path"], cat)
        return ("pass" if _cmp(v, chk["op"], chk["value"]) else "fail"), v, f"{chk['op']} {chk['value']}", []
    if t == "count":
        n = len(_select(scene, chk))
        return ("pass" if _cmp(n, chk["op"], chk["value"]) else "fail"), n, f"{chk['op']} {chk['value']}", []
    if t == "element_prop":
        els = [e for e in _select(scene, chk) if chk["prop"] in e.props]
        if not els:
            return "na", None, None, []
        vals = [e.props[chk["prop"]] for e in els]
        v = max(vals) if chk.get("agg", "max") == "max" else min(vals)
        bad = [e.id for e in els if not _cmp(e.props[chk["prop"]], chk["op"], chk["value"])]
        return ("fail" if bad else "pass"), v, f"{chk['op']} {chk['value']}", bad
    if t in ("min_distance", "max_distance"):
        a = _select(scene, chk["a"])
        g = _geom(scene, chk["b"])
        if not a or g is None:
            return "na", None, None, []
        d = {e.id: float(e.footprint().distance(g)) for e in a}
        if t == "min_distance":
            v = min(d.values())
            bad = [i for i, x in d.items() if x < chk["min"] - 1e-6]
            return ("fail" if bad else "pass"), round(v, 1), f">= {chk['min']}", bad[:8]
        v = max(d.values())
        bad = [i for i, x in d.items() if x > chk["max"] + 1e-6]
        return ("fail" if bad else "pass"), round(v, 1), f"<= {chk['max']}", bad[:8]
    if t == "noise_margin":
        rows = scene.metrics.get("noise") or []
        if not rows:
            return "na", None, None, []
        v = min(r["margin_db"] for r in rows)
        return ("pass" if v >= chk["min"] else "fail"), v, f">= {chk['min']} dB", []
    raise ValueError(f"unknown check type '{t}'")


def evaluate(scene, profile: dict, cat=None) -> list[dict]:
    rows = []
    for rule in profile.get("rules", []):
        chk = rule.get("check") or {"type": "manual"}
        try:
            status, value, limit, ev = _run(chk, scene, cat or {})
        except Exception as exc:                       # a broken rule must show up, not vanish
            status, value, limit, ev = "error", None, str(exc), []
        if status == "fail" and rule.get("severity", "fail") == "warn":
            status = "warn"
        rows.append({"id": rule["id"], "clause": rule.get("clause", ""), "text": rule.get("text", ""),
                     "status": status, "value": value, "limit": limit, "evidence": ev, "check": chk.get("type", "manual")})
    return rows


def compliance_csv(rows: list[dict], profile: Optional[dict] = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    if profile:
        w.writerow([f"Profile: {profile['id']}", f"Revision: {profile.get('revision', '')}"])
    w.writerow(["Rule", "Clause", "Requirement", "Status", "Value", "Limit", "Evidence"])
    for r in rows:
        w.writerow([r["id"], r["clause"], r["text"], r["status"], "" if r["value"] is None else r["value"],
                    r["limit"] or "", " ".join(r["evidence"])])
    return buf.getvalue()


def summary(rows: list[dict]) -> dict:
    c: dict[str, int] = {}
    for r in rows:
        c[r["status"]] = c.get(r["status"], 0) + 1
    return c
