"""Orchestrator: brief + site + variant -> Scene. Also option search and edits.

The LLM never computes geometry. It calls these functions (via MCP) and reads
the metrics, checks and log they return.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
from shapely.geometry import Polygon

from .catalogue import Catalogue, for_brief, load_catalogue
from .checks import library_checks, run_checks
from .engines import noise
from .engines import site as site_eng
from .engines.cooling import select_heat_rejection
from .engines.core import build_core, mirror_core
from .engines.electrical import design_electrical
from .engines.halls import design_hall
from .schema import Brief, Scene, SiteInput


@dataclass
class Variant:
    rotation_deg: float
    mirror_x: bool = False
    mirror_y: bool = False
    hr_location: str = "ground"

    def label(self, frame=None, true_north_deg: float = 0.0) -> str:
        """Human description with true compass directions (needs the site frame)."""
        if frame is None:
            return (f"orientation {self.rotation_deg:.0f} deg, mirror_x={self.mirror_x}, mirror_y={self.mirror_y}, "
                    f"heat rejection on {self.hr_location}")
        def bearing(vx, vy):
            t = math.radians(frame.rotation_deg)
            sx, sy = math.cos(t) * vx - math.sin(t) * vy, math.sin(t) * vx + math.cos(t) * vy
            brg = (math.degrees(math.atan2(sx, sy)) + true_north_deg) % 360
            return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((brg + 22.5) // 45) % 8]
        return (f"support {bearing(1 if self.mirror_x else -1, 0)} end, electrical/gen yard "
                f"{bearing(0, 1 if self.mirror_y else -1)}, heat rejection on {self.hr_location}")


def _coords(poly: Polygon) -> list[tuple[float, float]]:
    return [tuple(map(float, p)) for p in list(poly.exterior.coords)[:-1]]


def run_engines(brief: Brief, cat: Catalogue):
    n_halls = math.ceil(brief.it_load_mw / brief.hall_it_mw - 1e-9)
    return n_halls, design_hall(brief, cat), design_electrical(brief, cat, n_halls), select_heat_rejection(brief, cat)


def _designs(hall, elec, hr, core, n_halls) -> dict:
    h = {k: v for k, v in hall.__dict__.items() if k not in ("pod_rects", "aisle_rects")}
    h["n_halls"] = n_halls
    e = elec.summary() | {
        "substation_spec": elec.substation, "x": elec.topology.x, "y": elec.topology.y,
        "pairs": elec.topology.load_pairs(), "block_kw": elec.plan.block_kw,
        "genset_kva": elec.genset["kva"], "tx_kva": elec.transformer["kva"],
    }
    return {"halls": h, "electrical": e, "cooling": hr.summary(), "core": core.info}


def _library_used(elec, hr, cat) -> dict:
    """Which plant records the layout rests on, and whether each is approved (shown on every output)."""
    used = []
    for role, key, d in (("genset", elec.genset_key, elec.genset), ("transformer", elec.tx_key, elec.transformer),
                         ("heat rejection", hr.unit_key, hr.unit)):
        used.append({"role": role, "id": d.get("record_id", key), "label": d["label"],
                     "status": d.get("status", "placeholder"), "source": d.get("source", "catalogue.yaml")})
    meta = cat.get("_library") or {}
    return {"used": used, "profile": meta.get("profile"), "drafts_allowed": meta.get("drafts_allowed", False),
            "packs": [Path(p).name for p in meta.get("packs", [])]}


def generate(brief: Brief, site: SiteInput, variant: Variant, cat: Optional[Catalogue] = None,
             offset_index: int = 0, _prepared=None) -> Optional[Scene]:
    """One layout for one variant. Returns None if the core does not fit the redline."""
    cat = for_brief(brief, cat)
    if _prepared is None:
        _prepared = prepare_variant(brief, site, variant, cat)
    if _prepared is None:
        return None
    base_core, frame, rl_local, buildable, offsets, eng = _prepared
    if not offsets:
        return None
    tx, ty = offsets[offset_index % len(offsets)]
    core = copy.deepcopy(base_core)
    for e in core.elements:
        e.move(tx, ty)
    anchors = {k: (v[0] + tx, v[1] + ty) for k, v in core.anchors.items()}
    x0, y0, x1, y1 = core.bbox
    bbox = (x0 + tx, y0 + ty, x1 + tx, y1 + ty)
    n_halls, hall, elec, hr = eng
    scene = Scene(brief=brief, site=site, frame=frame, redline_local=_coords(rl_local),
                  buildable_local=_coords(buildable), elements=core.elements, arrays=core.arrays,
                  variant=asdict(variant) | {"offset": [float(tx), float(ty)],
                                             "label": variant.label(frame, site.true_north_deg)})
    scene.designs = _designs(hall, elec, hr, core, n_halls)
    scene.designs["library"] = _library_used(elec, hr, cat)
    scene.designs["anchors"] = anchors
    scene.log += core.notes + elec.notes + hr.notes
    site_eng.layout_site(scene, cat, bbox, anchors)
    finalize(scene, cat)
    return scene


def prepare_variant(brief: Brief, site: SiteInput, variant: Variant, cat: Catalogue, k_offsets: int = 5):
    eng = run_engines(brief, cat)
    n_halls, hall, elec, hr = eng
    frame, rl_local = site_eng.make_frame(site, variant.rotation_deg)
    buildable = rl_local.buffer(-site.boundary_setback_m, join_style="mitre")
    if buildable.geom_type != "Polygon":
        buildable = max(buildable.geoms, key=lambda g: g.area)
    core = build_core(brief, cat, hall, elec, hr, n_halls, variant.hr_location)
    core = mirror_core(core, variant.mirror_x, variant.mirror_y)
    rd = cat["site"]["roads"]
    m = rd["clearance"] + rd["width"] + 0.5
    x0, y0, x1, y1 = core.bbox
    env = (x0 - m, y0 - m, x1 + m, y1 + m)
    offs = site_eng.feasible_offsets(env, buildable)
    # keep a gate zone (guardhouse + queuing) between the main access and the core
    if len(offs) and site.access_points:
        ax, ay = frame.to_local(*site.access_points[0])
        gz = cat["site"]["checks"]["gate_zone_depth"]
        dx = np.maximum(0, np.maximum((env[0] + offs[:, 0]) - ax, ax - (env[2] + offs[:, 0])))
        dy = np.maximum(0, np.maximum((env[1] + offs[:, 1]) - ay, ay - (env[3] + offs[:, 1])))
        keep = np.hypot(dx, dy) >= gz
        if keep.any():
            offs = offs[keep]
    reps = site_eng.representative_offsets(offs, k_offsets)
    return core, frame, rl_local, buildable, reps, eng


# ------------------------------------------------------------------ metrics, checks, score
def spare_land(scene: Scene, min_area: float = 2500.0) -> dict | None:
    import shapely
    used = shapely.union_all([e.footprint().buffer(6.0) for e in scene.elements if e.kind != "landscape"])
    free = scene.buildable_poly().difference(used)
    parts = [g for g in getattr(free, "geoms", [free]) if g.geom_type == "Polygon" and g.area >= min_area]
    if not parts:
        return None
    # keep reasonably compact pieces (not slivers along the boundary)
    parts = [g.buffer(-8).buffer(8) for g in parts]
    parts = [g for g in parts if not g.is_empty and g.area >= min_area]
    parts = [p for g in parts for p in getattr(g, "geoms", [g]) if p.area >= min_area]
    if not parts:
        return None
    big = max(parts, key=lambda g: g.area)
    core_pt = big.buffer(-15)
    lab = core_pt.representative_point() if not core_pt.is_empty else big.representative_point()
    return {"area_ha": round(sum(g.area for g in parts) / 1e4, 2), "largest_ha": round(big.area / 1e4, 2),
            "polygons": [[tuple(map(float, c)) for c in g.exterior.coords] for g in parts],
            "label_xy": (float(lab.x), float(lab.y))}


def finalize(scene: Scene, cat: Catalogue) -> Scene:
    scene.designs["spare_land"] = spare_land(scene)
    scene.checks = run_checks(scene, cat)
    scene.metrics = compute_metrics(scene)
    scene.checks += library_checks(scene, cat)             # needs metrics (benchmark), so runs second
    scene.metrics["checks_failed"] = sum(c.severity == "fail" for c in scene.checks)
    scene.metrics["checks_warn"] = sum(c.severity == "warn" for c in scene.checks)
    scene.score = score(scene)
    return scene


def compute_metrics(scene: Scene) -> dict:
    rl = scene.redline_poly()
    bpoly = noise.building_polygon(scene)
    s = max(1, scene.brief.storeys)
    gia = 0.0
    for e in scene.elements:
        if not e.props.get("enclosed"):
            continue
        a = e.w * e.d
        gia += a * (s if e.kind in ("support", "corridor", "mech_gallery") else 1)
    halls = scene.by_kind("data_hall")
    roads = sum(e.footprint().area for e in scene.by_kind("road"))
    an = scene.designs.get("anchors", {})
    def dist(kind, anchor):
        els = scene.by_kind(kind)
        if not els or anchor not in an:
            return None
        return round(math.dist((els[0].cx, els[0].cy), an[anchor]), 1)
    gate = scene.by_kind("gate")
    m = {
        "it_load_mw": scene.brief.it_load_mw,
        "data_halls": len(halls),
        "racks": sum(h.props.get("racks", 0) for h in halls),
        "white_space_m2": round(sum(h.w * h.d for h in halls)),
        "gia_m2": round(gia),
        "footprint_m2": round(bpoly.area) if bpoly is not None else 0,
        "site_area_ha": round(rl.area / 1e4, 2),
        "coverage_pct": round((bpoly.area if bpoly is not None else 0) / rl.area * 100, 1),
        "road_area_m2": round(roads),
        "landscape_m2": round(sum(e.footprint().area for e in scene.by_kind("landscape"))),
        "spare_land_ha": (scene.designs.get("spare_land") or {}).get("area_ha", 0.0),
        "building_length_m": round(scene.designs["core"]["building_length"], 1),
        "building_depth_m": round(scene.designs["core"]["building_depth"], 1),
        "substation_to_elec_m": dist("substation", "elec_centre"),
        "fuel_to_genyard_m": dist("fuel_bund", "gen_centre"),
        "gate_to_entrance_m": (round(math.dist((gate[0].cx, gate[0].cy), an["entrance"]), 1)
                               if gate and "entrance" in an else None),
        "noise": noise.receptor_levels(scene),
        "checks_failed": sum(c.severity == "fail" for c in scene.checks),
        "checks_warn": sum(c.severity == "warn" for c in scene.checks),
    }
    return m


def score(scene: Scene) -> float:
    """Lower is better. Weights are a starting point for the team to tune."""
    m = scene.metrics
    s = 1000.0 * m["checks_failed"] + 40.0 * m["checks_warn"]
    for r in m["noise"]:
        s += 60.0 * max(0.0, -r["margin_db"])
    s += 0.5 * (m["substation_to_elec_m"] or 300) + 0.3 * (m["fuel_to_genyard_m"] or 300)
    s += m["road_area_m2"] / 150.0 + 0.1 * (m["gate_to_entrance_m"] or 300)
    return round(s, 1)


# ------------------------------------------------------------------ options
def enumerate_variants(brief: Brief, site: SiteInput) -> list[Variant]:
    if site.building_orientation_deg is not None:
        rots = [site.building_orientation_deg]
    else:
        a = site_eng.long_axis_angle(Polygon(site.redline))
        rots = [round(a, 1), round(a - 90 if a > 0 else a + 90, 1)]
    hrs = ["ground", "roof"] if brief.hr_location == "auto" else [brief.hr_location]
    return [Variant(r, mx, my, h) for r in rots for mx in (False, True) for my in (False, True) for h in hrs]


def generate_options(brief: Brief, site: SiteInput, cat: Optional[Catalogue] = None,
                     max_options: int = 4, k_offsets: int = 5) -> list[Scene]:
    cat = for_brief(brief, cat)
    best_per_variant: list[Scene] = []
    variants = enumerate_variants(brief, site)
    for v in variants:
        prep = prepare_variant(brief, site, v, cat, k_offsets)
        if prep is None or not prep[4]:
            continue
        cands = [generate(brief, site, v, cat, i, prep) for i in range(len(prep[4]))]
        cands = [c for c in cands if c is not None]
        if cands:
            best_per_variant.append(min(cands, key=lambda c: c.score))
    best_per_variant.sort(key=lambda c: c.score)
    for i, sc in enumerate(best_per_variant[:max_options]):
        sc.option = chr(ord("A") + i)
        sc.metrics["variants_tested"] = len(variants)
    return best_per_variant[:max_options]


def compare_topologies(brief: Brief, topologies: list[str], cat: Optional[Catalogue] = None) -> list[dict]:
    cat = for_brief(brief, cat)
    rows = []
    for t in topologies:
        b = brief.model_copy(update={"topology": t})
        n_halls, _, elec, _ = run_engines(b, cat)
        s = elec.summary()
        rows.append({"topology": s["topology"], "blocks": s["blocks"], "block_kw": s["block_ups_kw"],
                     "installed_mw": s["installed_ups_mw"], "normal_loading_pct": s["normal_loading_pct"],
                     "gensets": s["gensets"], "transformers": s["transformers"], "elec_rooms": s["elec_rooms"],
                     "sts_rooms": s["sts_rooms"], "genset": s["genset"],
                     "elec_room_m2": round(elec.room_width * elec.plan.n_blocks * cat["electrical_rooms"]["depth"])})
    return rows


# ------------------------------------------------------------------ edits (drag / agent)
def move_element(scene: Scene, element_id: str, dx: float, dy: float, whole_group: bool = False,
                 cat: Optional[Catalogue] = None) -> Scene:
    cat = for_brief(scene.brief, cat)
    el = scene.get(element_id)
    targets = [e for e in scene.elements if whole_group and el.group and e.group == el.group] or [el]
    ov = scene.variant.setdefault("overrides", {})
    for e in targets:
        e.move(dx, dy)
        e.locked = True
        ov[e.id] = [e.x, e.y]
    scene.log.append(f"Moved {', '.join(t.id for t in targets)} by ({dx:+.1f}, {dy:+.1f}) m")
    return finalize(scene, cat)
