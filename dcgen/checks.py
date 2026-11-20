"""Rule checks. Each returns Check objects the agent can read and act on.
Limits marked placeholder must be replaced with project/insurer/code values."""
from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import Polygon, box
from shapely.strtree import STRtree

from .catalogue import Catalogue
from .engines import noise
from .schema import Check, Scene

ANCILLARY = ("substation", "pump_house", "sprinkler_tank", "fuel_bund", "guardhouse")


def run_checks(scene: Scene, cat: Catalogue) -> list[Check]:
    out: list[Check] = []
    rl = scene.redline_poly()
    bl = scene.buildable_poly()
    solids = [e for e in scene.elements if e.solid and e.kind not in ("road", "landscape", "gate")]
    roads = [e for e in scene.elements if e.kind == "road"]
    road_u = shapely.union_all([r.footprint() for r in roads]) if roads else Polygon()

    # 1. inside redline / buildable
    outside = [e.id for e in solids if not rl.buffer(0.01).contains(e.footprint())]
    in_setback = [e.id for e in solids if e.id not in outside and not bl.buffer(0.01).contains(e.footprint())]
    out.append(Check(id="fit.redline", severity="fail" if outside else "pass",
                     message=(f"{len(outside)} element(s) outside the redline" if outside
                              else "All plant and buildings inside the redline"), element_ids=outside))
    if in_setback:
        out.append(Check(id="fit.setback", severity="warn", element_ids=in_setback,
                         message=f"{len(in_setback)} element(s) inside the {scene.site.boundary_setback_m:.0f} m boundary setback"))

    # 2. clashes between solids (3D: plan overlap and z-overlap)
    fps = [e.footprint() for e in solids]
    tree = STRtree(fps)
    clashes = []
    for i, j in zip(*tree.query(fps, predicate="intersects")):
        if i >= j:
            continue
        a, b = solids[i], solids[j]
        if min(a.top, b.top) - max(a.z, b.z) <= 0.05:
            continue
        if fps[i].intersection(fps[j]).area > 0.5:
            clashes.append((a.id, b.id))
    out.append(Check(id="clash.solids", severity="fail" if clashes else "pass",
                     message=(f"{len(clashes)} clash(es): " + ", ".join(f"{a}/{b}" for a, b in clashes[:6])
                              if clashes else "No clashes between buildings and plant"),
                     element_ids=sorted({x for c in clashes for x in c})))

    # 3. roads through solids
    hits = [e.id for e in solids if e.z < 0.5 and road_u.intersection(e.footprint()).area > 1.0]
    out.append(Check(id="clash.roads", severity="fail" if hits else "pass", element_ids=hits,
                     message=(f"Road runs through {', '.join(hits)}" if hits else "Roads clear of buildings and plant")))

    # 4. ancillaries served by a road
    lim = cat["site"]["checks"]["max_road_distance"]
    far = [e.id for e in scene.elements if e.kind in ANCILLARY and road_u.distance(e.footprint()) > lim]
    out.append(Check(id="access.ancillaries", severity="warn" if far else "pass", element_ids=far, limit=lim,
                     message=(f"{', '.join(far)} more than {lim:.0f} m from a road" if far
                              else f"Every ancillary within {lim:.0f} m of a road")))

    # 5. fire appliance access round the building (placeholder rule)
    bpoly = noise.building_polygon(scene)
    if bpoly is not None and not road_u.is_empty:
        fd = cat["site"]["checks"]["fire_access_distance"]
        ring = bpoly.exterior if bpoly.geom_type == "Polygon" else bpoly.convex_hull.exterior
        pts = [ring.interpolate(t, normalized=True) for t in np.linspace(0, 1, 200, endpoint=False)]
        frac = float(np.mean(shapely.distance(road_u, pts) <= fd))
        out.append(Check(id="fire.access", severity="pass" if frac > 0.98 else "warn", value=round(frac * 100),
                         limit=100, message=f"{frac:.0%} of the facade within {fd:.0f} m of a road "
                                            "(placeholder rule - set from the fire strategy)"))

    # 6. fuel separation
    f = cat["site"]["fuel"]
    for bund in scene.by_kind("fuel_bund"):
        fp = bund.footprint()
        plant = [e for e in scene.elements if e.props.get("enclosed") or e.kind in ("genset", "transformer")]
        dmin = min(fp.distance(p.footprint()) for p in plant) if plant else 1e9
        dbound = rl.exterior.distance(fp)
        bad = dmin < f["min_to_building"] - 0.01 or dbound < f["min_to_boundary"] - 0.01
        out.append(Check(id="fuel.separation", severity="fail" if bad else "pass", element_ids=[bund.id],
                         value=round(dmin, 1), limit=f["min_to_building"],
                         message=f"Fuel store {dmin:.0f} m from building/plant (min {f['min_to_building']:.0f}), "
                                 f"{dbound:.0f} m from boundary (min {f['min_to_boundary']:.0f}) - placeholder limits"))

    # 7. noise at receptors and boundary
    for r in noise.receptor_levels(scene):
        sev = "pass" if r["margin_db"] >= 0 else "fail"
        out.append(Check(id=f"noise.{r['name']}", severity=sev, value=r["lp_dba"], limit=r["limit_dba"],
                         message=f"{r['name']}: {r['lp_dba']:.1f} dB(A) vs limit {r['limit_dba']:.0f} "
                                 f"({'+' if r['margin_db'] >= 0 else ''}{r['margin_db']:.1f} dB margin)"))
    if scene.site.boundary_noise_limit_dba is not None:
        ring = rl.exterior
        pts = np.array([[*ring.interpolate(t, normalized=True).coords[0], 1.5]
                        for t in np.linspace(0, 1, 240, endpoint=False)])
        lp = noise.lp_at(pts, scene, building=bpoly)
        lim2 = scene.site.boundary_noise_limit_dba
        out.append(Check(id="noise.boundary", severity="pass" if lp.max() <= lim2 else "fail",
                         value=round(float(lp.max()), 1), limit=lim2,
                         message=f"Max at redline {lp.max():.1f} dB(A) vs {lim2:.0f}"))

    # 7b. maintenance envelopes (half the catalogue gap each side, so neighbours just touch)
    plant = [e for e in solids if "clear_xy" in e.props]
    bad_zone: dict[str, list[str]] = {}
    for p in plant:
        cx, cy = p.props["clear_xy"]
        zone = box(p.x - cx, p.y - cy, p.x + p.w + cx, p.y + p.d + cy)
        for j in tree.query(zone, predicate="intersects"):
            o = solids[j]
            if o is p or min(o.top, p.top) - max(o.z, p.z) <= 0.05:
                continue
            if fps[j].intersection(zone).area > 0.05:
                bad_zone.setdefault(p.id, []).append(o.id)
    out.append(Check(id="maint.zones", severity="warn" if bad_zone else "pass",
                     element_ids=sorted(bad_zone),
                     message=(f"{len(bad_zone)} unit(s) have another element inside their maintenance clearance: "
                              + ", ".join(f"{k}/{v[0]}" for k, v in list(bad_zone.items())[:5])
                              if bad_zone else "Maintenance clearances clear on all plant")))

    # 7c. escape routes (placeholder limits from the catalogue egress block)
    eg = scene.designs.get("core", {}).get("egress")
    if eg:
        lim = eg["limits"]
        for cid, val, key, what in (
                ("egress.hall", eg["hall_interior_travel_m"], "travel_one_dir_m", "Hall: deepest point to a hall door"),
                ("egress.hall_exit", eg["hall_to_exit_m"], "travel_two_dir_m", "Hall: deepest point to a final exit"),
                ("egress.plant_rooms", eg["plant_room_travel_m"], "travel_two_dir_m",
                 f"Electrical rooms: worst ({eg['plant_room_worst']}) to a final exit")):
            ok = val <= lim[key] + 1e-6
            direct = cat["egress"].get("plant_room_exits")
            hint = "" if ok else (" - add cross corridors (halls.cross_corridor_every) or doors" if cid != "egress.plant_rooms"
                                  else (" - upper-level rooms need more stair/exit cores" if direct else
                                        " - consider direct plant-room exits (egress.plant_room_exits)"))
            out.append(Check(id=cid, severity="pass" if ok else "warn", value=val, limit=lim[key],
                             message=f"{what}: {val:.0f} m vs {lim[key]:.0f} m (placeholder limit){hint}"))
        ex = scene.by_kind("exit")
        out.append(Check(id="egress.exits", severity="pass" if len(ex) >= 2 else "fail", value=len(ex), limit=2,
                         message=f"{len(ex)} final exits, {eg['cross_corridors']} cross corridor(s), {eg['stairs']} stair core(s)"))
        lw, ll = cat["egress"]["exit_lane_width"], cat["egress"]["exit_lane_length"]
        blockers = [o for o in solids if not o.props.get("enclosed") and o.kind != "exit"]
        blocked = []
        for x in ex:
            d = x.props.get("lane_dir", "n")
            cxm, cym = x.x + x.w / 2, x.y + x.d / 2
            lane = {"n": box(cxm - lw / 2, x.y + x.d, cxm + lw / 2, x.y + x.d + ll),
                    "s": box(cxm - lw / 2, x.y - ll, cxm + lw / 2, x.y),
                    "e": box(x.x + x.w, cym - lw / 2, x.x + x.w + ll, cym + lw / 2),
                    "w": box(x.x - ll, cym - lw / 2, x.x, cym + lw / 2)}[d]
            hit = [o.id for o in blockers if o.z < 2.4 and o.footprint().intersection(lane).area > 0.01]
            if hit:
                blocked.append((x.id, hit[0]))
        out.append(Check(id="egress.lanes", severity="warn" if blocked else "pass",
                         element_ids=sorted({a for a, _ in blocked} | {b for _, b in blocked}),
                         message=(f"Exit lane blocked: " + ", ".join(f"{a} by {b}" for a, b in blocked[:5])
                                  if blocked else f"All final exits have a clear {lw:.1f} m x {ll:.0f} m lane outside")))

    # 8. plant capacity
    c = scene.designs.get("cooling", {})
    if c:
        out.append(Check(id="hr.capacity", severity="pass" if c["capacity_margin_pct"] >= 0 else "fail",
                         value=c["capacity_margin_pct"],
                         message=f"{c['duty']} duty x {c['unit_capacity_at_design_kw']} kW at "
                                 f"{scene.brief.ambient_design_c:.0f} degC = {c['capacity_margin_pct']:+.1f}% on "
                                 f"{c['heat_load_mw']} MW, plus {c['standby']} standby"))
    e = scene.designs.get("electrical", {})
    if e:
        out.append(Check(id="elec.topology", severity="info",
                         message=f"{e['topology']}: {e['blocks']} blocks x {e['block_ups_kw']} kW, "
                                 f"{e['installed_ups_mw']} MW installed, {e['normal_loading_pct']}% normal loading"))
    hd = scene.designs.get("halls", {})
    if hd:
        ok = hd["racks_provided"] >= hd["racks_required"]
        out.append(Check(id="halls.racks", severity="pass" if ok else "fail",
                         message=f"{hd['racks_provided']} racks per hall for {hd['racks_required']} required "
                                 f"({scene.brief.rack_kw:.0f} kW/rack)"))
    roof = [x for x in scene.elements if x.props.get("on_roof")]
    if roof:
        kg = roof[0].props.get("operating_kg") or 0
        kpa = kg * 9.81 / 1000 / (roof[0].w * roof[0].d)
        out.append(Check(id="roof.load", severity="warn", value=round(kpa, 1),
                         message=f"{len(roof)} roof units at ~{kpa:.1f} kN/m2 local load - confirm with structures"))
    for msg in scene.log:
        if msg.startswith("FAIL"):
            out.append(Check(id="placement", severity="fail", message=msg[6:]))
    return out
