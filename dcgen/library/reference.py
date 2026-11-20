"""Reference designs: metrics cards, benchmarking and regression.

A card (`<pack>/reference/<id>.yaml`) records one delivered or approved scheme:

  id: REF-001
  name: "Example campus B1"
  topology: 4M3
  brief: {it_load_mw: 24, hall_it_mw: 6, rack_kw: 20, topology: 4M3}        # to regenerate it
  expected: {halls: 4, hall_w_m: 31.2, hall_d_m: 28.2, gensets: 16, transformers: 16, hr_units: 20, elec_rooms: 16}
  tolerance: {default_pct: 5, hall_w_m: 0.6}                                 # optional
  metrics: {it_mw: 24, gia_m2: 9800, footprint_m2: 8400, site_ha: 7.9, gensets: 16, transformers: 16, hr_units: 20, elec_rooms: 16, white_space_m2: 3500}
  files: ["drawings/REF-001-GA.pdf", "models/REF-001.ifc"]

`regress` re-runs the engines on the card's brief and compares with `expected` (the tool must reproduce
what was built before anyone trusts it). `benchmark` compares a new layout's per-MW numbers with similar cards.
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Optional

import yaml

KPIS = {
    "gia_per_mw": ("m2/MW", lambda m: m["gia_m2"] / m["it_mw"]),
    "footprint_per_mw": ("m2/MW", lambda m: m["footprint_m2"] / m["it_mw"]),
    "white_space_per_mw": ("m2/MW", lambda m: m["white_space_m2"] / m["it_mw"]),
    "mw_per_ha": ("MW/ha", lambda m: m["it_mw"] / m["site_ha"]),
    "gensets_per_mw": ("/MW", lambda m: m["gensets"] / m["it_mw"]),
    "transformers_per_mw": ("/MW", lambda m: m["transformers"] / m["it_mw"]),
    "hr_units_per_mw": ("/MW", lambda m: m["hr_units"] / m["it_mw"]),
    "elec_rooms_per_mw": ("/MW", lambda m: m["elec_rooms"] / m["it_mw"]),
}


def load_cards(roots) -> list[dict]:
    cards = []
    for root in roots:
        for path in sorted(Path(root).rglob("*.yaml")):
            if path.parent.name != "reference":
                continue
            d = yaml.safe_load(path.read_text()) or {}
            if "id" in d:
                d["_file"] = str(path)
                cards.append(d)
    return cards


def scene_metrics(scene) -> dict:
    m, d = scene.metrics, scene.designs
    return {"it_mw": scene.brief.it_load_mw, "gia_m2": m["gia_m2"], "footprint_m2": m["footprint_m2"],
            "site_ha": m["site_area_ha"], "white_space_m2": m["white_space_m2"],
            "gensets": d["electrical"]["gensets"], "transformers": d["electrical"]["transformers"],
            "hr_units": d["cooling"]["total"], "elec_rooms": d["electrical"]["elec_rooms"] + d["electrical"]["sts_rooms"]}


def kpis(metrics: dict) -> dict:
    out = {}
    for k, (_, fn) in KPIS.items():
        try:
            out[k] = fn(metrics)
        except (KeyError, ZeroDivisionError, TypeError):
            pass
    return out


def similar(brief, cards: list[dict], n: int = 3) -> list[dict]:
    def dist(c):
        b = c.get("brief", {})
        d = abs(b.get("it_load_mw", brief.it_load_mw) - brief.it_load_mw) / max(brief.it_load_mw, 1)
        d += 0.0 if b.get("topology", brief.topology) == brief.topology else 0.5
        d += abs(b.get("rack_kw", brief.rack_kw) - brief.rack_kw) / max(brief.rack_kw, 1) * 0.5
        d += 0.0 if b.get("cooling", brief.cooling) == brief.cooling else 0.5
        return d
    return sorted(cards, key=dist)[:n]


def benchmark(scene, cards: list[dict], tol: float = 0.25) -> dict:
    """Compare the layout's per-MW KPIs with similar cards. 'outlier' = outside the cards' range +/- tol."""
    refs = [c for c in similar(scene.brief, cards, 5) if c.get("metrics")]
    mine = kpis(scene_metrics(scene))
    ref_k = [kpis(c["metrics"]) for c in refs]
    rows = []
    for k, v in mine.items():
        vals = [r[k] for r in ref_k if k in r]
        if not vals:
            continue
        lo, hi, med = min(vals), max(vals), statistics.median(vals)
        status = "outlier" if (v < lo * (1 - tol) or v > hi * (1 + tol)) else "in range"
        rows.append({"kpi": k, "unit": KPIS[k][0], "value": round(v, 3), "ref_min": round(lo, 3),
                     "ref_median": round(med, 3), "ref_max": round(hi, 3), "vs_median_pct": round((v / med - 1) * 100, 1),
                     "status": status})
    return {"refs": [c["id"] for c in refs], "rows": rows, "outliers": [r["kpi"] for r in rows if r["status"] == "outlier"]}


# ---------------------------------------------------------------- regression
def engine_outputs(brief, cat) -> dict:
    from ..engines.core import build_core
    from ..layout import run_engines
    n, hall, elec, hr = run_engines(brief, cat)
    s = elec.summary()
    return {"halls": n, "hall_w_m": hall.width_x, "hall_d_m": hall.depth_y, "racks_per_hall": hall.racks_provided,
            "block_kw": s["block_ups_kw"], "installed_ups_mw": s["installed_ups_mw"], "gensets": s["gensets"],
            "transformers": s["transformers"], "elec_rooms": s["elec_rooms"] + s["sts_rooms"], "hr_units": hr.n_total}


def regress_card(card: dict, cat=None) -> dict:
    from ..catalogue import for_brief
    from ..schema import Brief
    brief = Brief(**card.get("brief", {}))
    got = engine_outputs(brief, for_brief(brief, cat))
    tol = card.get("tolerance", {})
    rows = []
    for k, want in (card.get("expected") or {}).items():
        have = got.get(k)
        if have is None:
            rows.append({"key": k, "expected": want, "got": None, "status": "unknown key"})
            continue
        if k in tol:
            ok = abs(have - want) <= tol[k]
        else:
            pct = tol.get("default_pct", 0.0)
            ok = abs(have - want) <= abs(want) * pct / 100 + 1e-9
        rows.append({"key": k, "expected": want, "got": have, "status": "pass" if ok else "FAIL"})
    return {"id": card["id"], "name": card.get("name", ""), "ok": all(r["status"] == "pass" for r in rows), "rows": rows}
