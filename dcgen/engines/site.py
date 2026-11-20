"""Site engine: fit the core inside the redline, then roads, gate, guardhouse
and ancillaries (substation, sprinkler tanks + pump house, fuel store, parking).

Placement is a candidate search: every candidate footprint on a grid inside the
free area is scored (vectorised with shapely 2) and the best one is taken, in
priority order. It is deliberately simple and explainable; options.py layers a
search over orientations/mirrors on top, and an optimiser can replace it later.
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points

from ..catalogue import Catalogue
from ..schema import Element, Frame, Scene, SiteInput


# ------------------------------------------------------------------ frame / orientation
def long_axis_angle(poly: Polygon) -> float:
    mrr = poly.minimum_rotated_rectangle
    c = list(mrr.exterior.coords)[:4]
    edges = [(c[i], c[(i + 1) % 4]) for i in range(4)]
    (x0, y0), (x1, y1) = max(edges, key=lambda e: math.dist(*e))
    a = math.degrees(math.atan2(y1 - y0, x1 - x0))
    while a <= -90:
        a += 180
    while a > 90:
        a -= 180
    return a


def make_frame(site: SiteInput, rotation_deg: float) -> tuple[Frame, Polygon]:
    rl = Polygon(site.redline)
    if not rl.is_valid:
        rl = rl.buffer(0)
    c = rl.centroid
    frame = Frame(rotation_deg=rotation_deg, origin=(c.x, c.y))
    local = Polygon([frame.to_local(x, y) for x, y in rl.exterior.coords])
    return frame, local


# ------------------------------------------------------------------ core placement
def feasible_offsets(env: tuple[float, float, float, float], buildable: Polygon,
                     step: float = 4.0) -> np.ndarray:
    bx0, by0, bx1, by1 = buildable.bounds
    ex0, ey0, ex1, ey1 = env
    txs = np.arange(bx0 - ex0, bx1 - ex1 + 1e-6, step)
    tys = np.arange(by0 - ey0, by1 - ey1 + 1e-6, step)
    if len(txs) == 0 or len(tys) == 0:
        return np.empty((0, 2))
    TX, TY = np.meshgrid(txs, tys)
    tx, ty = TX.ravel(), TY.ravel()
    shapely.prepare(buildable)
    ok = shapely.contains(buildable, shapely.box(ex0 + tx, ey0 + ty, ex1 + tx, ey1 + ty))
    return np.column_stack([tx[ok], ty[ok]])


def representative_offsets(off: np.ndarray, k: int = 5) -> list[tuple[float, float]]:
    """A few spread-out feasible positions: centre plus extremes."""
    if len(off) == 0:
        return []
    picks = {tuple(off[np.argmin(np.linalg.norm(off - off.mean(axis=0), axis=1))])}
    for f in (off[:, 0], -off[:, 0], off[:, 1], -off[:, 1], off.sum(1), -off.sum(1)):
        picks.add(tuple(off[np.argmin(f)]))
        if len(picks) >= k:
            break
    return list(picks)


# ------------------------------------------------------------------ helpers
def _rect_candidates(free: Polygon, w: float, d: float, step: float = 4.0):
    x0, y0, x1, y1 = free.bounds
    xs = np.arange(x0, x1 - w + 1e-6, step)
    ys = np.arange(y0, y1 - d + 1e-6, step)
    if len(xs) == 0 or len(ys) == 0:
        return None, None
    X, Y = np.meshgrid(xs, ys)
    X, Y = X.ravel(), Y.ravel()
    boxes = shapely.box(X, Y, X + w, Y + d)
    ok = shapely.contains(free, boxes)
    if not ok.any():
        return None, None
    return np.column_stack([X[ok], Y[ok]]), boxes[ok]


def _best(free: Polygon, sizes: list[tuple[float, float]], score_fn, step: float = 4.0):
    shapely.prepare(free)
    best = None
    for w, d in sizes:
        xy, boxes = _rect_candidates(free, w, d, step)
        if xy is None:
            continue
        s = score_fn(xy + np.array([w / 2, d / 2]), boxes)
        i = int(np.argmin(s))
        if best is None or s[i] < best[0]:
            best = (float(s[i]), float(xy[i, 0]), float(xy[i, 1]), w, d)
    return best


def _poly_el(id_, kind, name, poly: Polygon, h=0.0, z=0.0, solid=False, **props) -> Element:
    poly = poly if poly.geom_type == "Polygon" else max(poly.geoms, key=lambda g: g.area)
    ext = [tuple(map(float, p)) for p in list(poly.exterior.coords)[:-1]]
    holes = [[tuple(map(float, p)) for p in list(r.coords)[:-1]] for r in poly.interiors] or None
    x0, y0, x1, y1 = poly.bounds
    return Element(id=id_, kind=kind, name=name, shape="poly", polygon=ext, holes=holes,
                   x=x0, y=y0, w=x1 - x0, d=y1 - y0, z=z, h=h, solid=solid, props=props)


# ------------------------------------------------------------------ main
def layout_site(scene: Scene, cat: Catalogue, core_bbox, anchors: dict) -> None:
    """Adds roads, gate, guardhouse and ancillaries to a scene whose core is already placed."""
    S = cat["site"]
    rd = S["roads"]
    rl = scene.redline_poly()
    buildable = scene.buildable_poly()
    log = scene.log

    x0, y0, x1, y1 = core_bbox
    inner = box(x0, y0, x1, y1).buffer(rd["clearance"], join_style="mitre")
    outer = inner.buffer(rd["width"], join_style="mitre")
    ring = outer.difference(inner)
    roads = [ _poly_el("RD-RING", "road", "Perimeter road", ring) ]

    # ---- access spur(s) from the boundary to the ring road
    pts = [scene.frame.to_local(*p) for p in scene.site.access_points]
    if not pts:
        coords = list(rl.exterior.coords)
        e = max(zip(coords[:-1], coords[1:]), key=lambda s: math.dist(*s))
        pts = [((e[0][0] + e[1][0]) / 2, (e[0][1] + e[1][1]) / 2)]
        log.append("No access point given: assumed mid-point of the longest boundary edge.")
    spurs = []
    gate_info = None
    for k, p in enumerate(pts):
        a = nearest_points(rl.exterior, Point(p))[0]
        b = nearest_points(outer.exterior, a)[0]
        v = np.array([b.x - a.x, b.y - a.y])
        L = float(np.linalg.norm(v))
        if L < 1e-6:
            continue
        u = v / L
        a_ext = (a.x - u[0] * 2, a.y - u[1] * 2)
        b_in = (b.x + u[0] * 1, b.y + u[1] * 1)
        spur = LineString([a_ext, b_in]).buffer(rd["width"] / 2, cap_style="flat").intersection(rl)
        spurs.append(spur)
        roads.append(_poly_el(f"RD-SPUR{k + 1}", "road", "Main access" if k == 0 else f"Access {k + 1}", spur))
        if k == 0:
            gate_info = (np.array([a.x, a.y]), u, L)

    # ---- guardhouse + gate on the main spur
    blocked = [outer.buffer(2.0)] + [s.buffer(1.0) for s in spurs]
    if gate_info is not None:
        a, u, L = gate_info
        n = np.array([-u[1], u[0]])
        g = S["guardhouse"]
        dist = min(g["from_gate"], max(8.0, L - g["length"] - 6))
        for side in (1, -1):
            c = a + u * (dist + g["length"] / 2) + side * n * (rd["width"] / 2 + 2.0 + g["width"] / 2)
            hl, hw = u * g["length"] / 2, n * g["width"] / 2
            corners = [c - hl - hw, c + hl - hw, c + hl + hw, c - hl + hw]
            gh = Polygon(corners)
            if rl.contains(gh) and not gh.intersects(outer):
                axis_aligned = abs(u[0]) > 0.999 or abs(u[1]) > 0.999
                if axis_aligned:
                    bx0, by0, bx1, by1 = gh.bounds
                    el = Element(id="GH", kind="guardhouse", name="Guardhouse", x=bx0, y=by0,
                                 w=bx1 - bx0, d=by1 - by0, h=g["height"])
                else:
                    el = _poly_el("GH", "guardhouse", "Guardhouse", gh, h=g["height"], solid=True)
                scene.elements.append(el)
                blocked.append(el.footprint().buffer(3.0))
                gp = a + u * (dist + g["length"] + 4)
                gate = LineString([gp - n * rd["width"] / 2, gp + n * rd["width"] / 2]).buffer(0.4)
                scene.elements.append(_poly_el("GATE1", "gate", "Security gate / HVM line", gate, h=1.2))
                break
        else:
            log.append("FAIL: guardhouse did not fit beside the main access spur.")

    free = buildable.difference(shapely.union_all(blocked))
    road_union = shapely.union_all([r.footprint() for r in roads])

    def road_dist(boxes):
        return shapely.distance(road_union, boxes)

    ent = np.array(anchors["entrance"])
    elec_c = np.array(anchors["elec_centre"])
    gen_c = np.array(anchors["gen_centre"])
    building = shapely.union_all([e.footprint() for e in scene.elements if e.props.get("enclosed")])
    plant = shapely.union_all([e.footprint() for e in scene.elements
                               if e.kind in ("genset", "transformer") or e.props.get("enclosed")])
    connectors = []

    def place(kind, sizes, score_fn, clearance=3.0, region=None):
        nonlocal free, road_union
        area = free if region is None else free.intersection(region)
        best = _best(area, sizes, score_fn)
        if best is None:
            log.append(f"FAIL: could not place {kind} ({sizes[0][0]:.0f} x {sizes[0][1]:.0f} m).")
            return None
        _, bx, by, w, d = best
        fp = box(bx, by, bx + w, by + d)
        free = free.difference(fp.buffer(clearance))
        if road_union.distance(fp) > 0.5:
            p_item, p_road = nearest_points(fp, road_union)
            conn = LineString([p_item, p_road]).buffer(rd["connector_width"] / 2, cap_style="flat")
            conn = conn.union(Point(p_road.x, p_road.y).buffer(rd["connector_width"] / 2)).intersection(rl)
            connectors.append((kind, conn))
            road_union = road_union.union(conn)
            free = free.difference(conn.buffer(1.0))
        return (bx, by, w, d)

    # ---- substation (towards the utility, near the electrical rooms)
    e = scene.designs["electrical"]
    sub = e["substation_spec"]
    util = (np.array(scene.frame.to_local(*scene.site.utility_point))
            if scene.site.utility_point else None)
    def sub_score(c, boxes):
        s = 0.5 * np.linalg.norm(c - elec_c, axis=1) + 3.0 * road_dist(boxes)
        if util is not None:
            s += np.linalg.norm(c - util, axis=1)
        return s
    r = place("substation", [(sub["length"], sub["width"]), (sub["width"], sub["length"])], sub_score)
    if r:
        bx, by, w, d = r
        scene.elements.append(Element(id="SUB", kind="substation", name=f"{sub['voltage']} substation",
                                      x=bx, y=by, w=w, d=d, h=sub["height"],
                                      props={"voltage": sub["voltage"], "mva": round(e["site_demand_mva"], 1)}))

    # ---- sprinkler tanks + pump house (one group, near the building, on a road)
    sp = S["sprinkler"]
    dia, gap = sp["tank_diameter"], sp["gap"]
    ph = sp["pump_house"]
    gw = sp["tanks"] * dia + sp["tanks"] * gap + ph["length"]
    gd = max(dia, ph["width"])
    def spr_score(c, boxes):
        return shapely.distance(building, boxes) + 2.5 * road_dist(boxes)
    r = place("sprinkler_group", [(gw, gd), (gd, gw)], spr_score)
    if r:
        bx, by, w, d = r
        horiz = w >= d
        for t in range(sp["tanks"]):
            off = t * (dia + gap)
            tx_, ty_ = (bx + off, by + (d - dia) / 2) if horiz else (bx + (w - dia) / 2, by + off)
            scene.elements.append(Element(id=f"SPR-T{t + 1}", kind="sprinkler_tank",
                                          name=f"Sprinkler tank {t + 1}", shape="cyl", x=tx_, y=ty_, group="sprinkler",
                                          w=dia, d=dia, h=sp["tank_height"],
                                          props={"volume_m3": round(math.pi * (dia / 2) ** 2 * sp["tank_height"])}))
        off = sp["tanks"] * (dia + gap)
        if horiz:
            scene.elements.append(Element(id="SPR-PH", kind="pump_house", name="Sprinkler pump house",
                                          group="sprinkler", x=bx + off, y=by + (d - ph["width"]) / 2, w=ph["length"],
                                          d=ph["width"], h=ph["height"]))
        else:
            scene.elements.append(Element(id="SPR-PH", kind="pump_house", name="Sprinkler pump house",
                                          group="sprinkler", x=bx + (w - ph["width"]) / 2, y=by + off, w=ph["width"],
                                          d=ph["length"], h=ph["height"]))

    # ---- fuel store (bunded), separated from building and boundary
    f = S["fuel"]
    nt = e["fuel_tanks"]
    tl, tw = f["tank"]["length"], f["tank"]["width"]
    bw = nt * tw + (nt - 1) * f["tank_gap"] + 2 * f["bund_margin"]
    bd = tl + 2 * f["bund_margin"]
    region = rl.buffer(-f["min_to_boundary"]).difference(plant.buffer(f["min_to_building"]))
    def fuel_score(c, boxes):
        return 0.6 * np.linalg.norm(c - gen_c, axis=1) + 2.5 * road_dist(boxes)
    r = place("fuel_bund", [(bw, bd), (bd, bw)], fuel_score, region=region)
    if r:
        bx, by, w, d = r
        scene.elements.append(Element(id="FUEL-BUND", kind="fuel_bund", name="Bulk fuel store (bunded)",
                                      x=bx, y=by, w=w, d=d, h=1.2, solid=False, group="fuel-store",
                                      props={"volume_m3": round(e["fuel_m3"]), "tanks": nt}))
        horiz = w >= d
        for t in range(nt):
            off = f["bund_margin"] + t * (tw + f["tank_gap"])
            if horiz:
                tx_, ty_, ww, dd = bx + off, by + f["bund_margin"], tw, tl
            else:
                tx_, ty_, ww, dd = bx + f["bund_margin"], by + off, tl, tw
            scene.elements.append(Element(id=f"FUEL-T{t + 1}", kind="fuel_tank", name=f"Fuel tank {t + 1}",
                                          x=tx_, y=ty_, w=ww, d=dd, z=0.3, h=f["tank"]["height"], group="fuel-store",
                                          props={"volume_m3": f["tank_m3"]}))

    # ---- parking near the entrance
    p = S["parking"]
    n = scene.brief.staff_parking
    sizes = []
    for m in (1, 2, 3):
        per_side = math.ceil(n / (2 * m))
        L = per_side * p["bay_w"]
        D = m * (2 * p["bay_d"] + p["aisle"])
        sizes += [(L, D), (D, L)]
    def park_score(c, boxes):
        return np.linalg.norm(c - ent, axis=1) + 2.0 * road_dist(boxes)
    r = place("parking", sizes, park_score, clearance=2.0)
    if r:
        bx, by, w, d = r
        scene.elements.append(Element(id="PARK", kind="parking", name=f"Parking ({n} spaces)", x=bx, y=by,
                                      w=w, d=d, h=0.0, solid=False, props={"spaces": n, **p}))

    for k, (kind, conn) in enumerate(connectors):
        roads.append(_poly_el(f"RD-C{k + 1}", "road", f"Access to {kind.replace('_', ' ')}", conn))

    scene.elements.extend(roads)
    land = rl.difference(buildable)
    scene.elements.append(_poly_el("LAND", "landscape", "Landscape buffer", land))
