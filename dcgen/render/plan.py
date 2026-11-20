"""2D site plan (local frame) with north point and scale bar."""
from __future__ import annotations

import math

import numpy as np
from matplotlib.patches import Circle, FancyArrow, Polygon as MPoly, Rectangle
from matplotlib.transforms import Affine2D
from shapely.geometry import Polygon

from ..schema import Scene
from .style import AQUA, BASE, BLUE, INK, INK2, MUTED, ORANGE, PLAN_FILL, REDLINE, shade, tint


def _centroid(els):
    x0 = min(e.x for e in els); x1 = max(e.x + e.w for e in els)
    y0 = min(e.y for e in els); y1 = max(e.y + e.d for e in els)
    return (x0 + x1) / 2, (y0 + y1) / 2, max(e.top for e in els), (x0, y0, x1, y1)


def label_specs(scene: Scene, kinds=None):
    """(text, (x, y, z), kind) for the key objects - shared by plan and axo."""
    d = scene.designs
    e, c = d.get("electrical", {}), d.get("cooling", {})
    out = []
    def add(kind, text, els):
        if els and (kinds is None or kind in kinds):
            x, y, z, _ = _centroid(els)
            out.append((text, (x, y, z), kind))
    halls = scene.by_kind("data_hall")
    for h in halls:
        if h.props.get("storey", 0) == 0 and (kinds is None or "data_hall" in kinds):
            above = [o for o in halls if o.props.get("storey", 0) > 0 and abs(o.x - h.x) < 0.1]
            name = h.id.replace("DH0", "DH") + ("/" + "/".join(o.id.replace("DH0", "DH") for o in above) if above else "")
            tall = max([h] + above, key=lambda o: o.top)
            out.append((f"{name}\n{h.props['it_kw'] / 1000:.0f} MW", (h.cx, h.cy, tall.top), "data_hall"))
    add("support", "Support / admin", scene.by_kind("support"))
    add("elec_room", f"Electrical rooms\n{e.get('blocks')} x {e.get('block_ups_kw', 0) / 1000:.1f} MW blocks ({e.get('topology')})",
        scene.by_kind("elec_room", "sts_room", "mv_room"))
    add("genset", f"{e.get('gensets')} x {e.get('genset', '').replace(' genset', '')} gensets", scene.by_kind("genset"))
    add("transformer", f"{e.get('transformers')} x {e.get('transformer', '').replace(' transformer', '')} TX",
        scene.by_kind("transformer"))
    hr = scene.by_kind("chiller", "dry_cooler")
    if hr:
        typ = "chillers" if hr[0].kind == "chiller" else "dry coolers"
        loc = "roof" if hr[0].props.get("on_roof") else "yard"
        add(hr[0].kind, f"{len(hr)} x {typ} ({loc}, {scene.brief.hr_redundancy})", hr)
    for k, t in (("substation", None), ("guardhouse", "Guardhouse")):
        els = scene.by_kind(k)
        if els:
            add(k, t or els[0].name, els)
    add("sprinkler_tank", "Sprinkler tanks + pump house", scene.by_kind("sprinkler_tank", "pump_house"))
    add("fuel_tank", f"Fuel store {e.get('fuel_m3', 0):.0f} m3", scene.by_kind("fuel_bund"))
    add("parking", f"Parking ({scene.brief.staff_parking})", scene.by_kind("parking"))
    return out


class LabelPlacer:
    """Greedy screen-space label placement: tries offsets round each anchor and
    skips positions that collide with labels already placed. Call after limits are set."""
    OFFSETS = [(0, 0), (0, 12), (0, -12), (16, 8), (-16, 8), (16, -8), (-16, -8), (0, 24), (0, -24),
               (30, 0), (-30, 0), (0, 36)]

    def __init__(self, ax, fontsize=5.8, leader_from=None):
        self.ax, self.fs, self.boxes = ax, fontsize, []
        self.dpi = ax.figure.dpi
        ax.apply_aspect()           # final axes box, so screen distances are the real ones

    def _size(self, text):
        lines = text.split("\n")
        w = max(len(l) for l in lines) * self.fs * 0.60 * self.dpi / 72 + 8
        h = len(lines) * self.fs * 1.3 * self.dpi / 72 + 4
        return w, h

    def add(self, text, xy, offsets=None, leader=False, **kw):
        tr = self.ax.transData
        px, py = tr.transform(xy)
        w, h = self._size(text)
        for ox, oy in (offsets or self.OFFSETS):
            cx, cy = px + ox * self.dpi / 72, py + oy * self.dpi / 72
            b = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            if all(b[2] < q[0] or b[0] > q[2] or b[3] < q[1] or b[1] > q[3] for q in self.boxes):
                self.boxes.append(b)
                style = dict(ha="center", va="center", fontsize=self.fs, color=INK, zorder=10000,
                             bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#c3c2b7", lw=0.3, alpha=0.92))
                style.update(kw)
                if leader and (ox or oy):
                    style["arrowprops"] = dict(arrowstyle="-", color=INK2, lw=0.35, shrinkA=0, shrinkB=0)
                self.ax.annotate(text, xy=xy, xytext=(ox, oy), textcoords="offset points", **style)
                return True
        return False


def north_angle_local(scene: Scene) -> float:
    """Angle (deg, CCW from local +x) of true north in the local frame."""
    b = math.radians(scene.site.true_north_deg)
    n_site = np.array([-math.sin(b), math.cos(b)])
    t = math.radians(scene.frame.rotation_deg)
    n_loc = np.array([math.cos(t) * n_site[0] + math.sin(t) * n_site[1],
                      -math.sin(t) * n_site[0] + math.cos(t) * n_site[1]])
    return math.degrees(math.atan2(n_loc[1], n_loc[0]))


def draw_north(ax, x, y, size, ang_deg):
    a = math.radians(ang_deg)
    u = np.array([math.cos(a), math.sin(a)])
    n = np.array([-u[1], u[0]])
    tip = np.array([x, y]) + u * size
    tail = np.array([x, y]) - u * size * 0.6
    ax.add_patch(MPoly([tip, tail + n * size * 0.35, np.array([x, y]) - u * size * 0.25], closed=True,
                       fc=INK, ec=INK, lw=0.6, zorder=50))
    ax.add_patch(MPoly([tip, tail - n * size * 0.35, np.array([x, y]) - u * size * 0.25], closed=True,
                       fc="white", ec=INK, lw=0.6, zorder=50))
    lp = tip + u * size * 0.35
    ax.text(lp[0], lp[1], "N", ha="center", va="center", fontsize=8, fontweight="bold", zorder=50)


def draw_scalebar(ax, x, y, length=100.0, h=3.0):
    seg = length / 4
    for i in range(4):
        ax.add_patch(Rectangle((x + i * seg, y), seg, h, fc=INK if i % 2 == 0 else "white", ec=INK, lw=0.5,
                               zorder=50))
    for i, t in enumerate([0, seg, 2 * seg, 4 * seg]):
        xx = x + (t if i < 3 else length)
        ax.text(xx, y - h * 0.9, f"{t:.0f}", ha="center", va="top", fontsize=5.5, color=INK2, zorder=50)
    ax.text(x + length + 4, y + h / 2, "m", va="center", fontsize=5.5, color=INK2, zorder=50)


def _poly_patch(ax, poly: Polygon, **kw):
    for g in getattr(poly, "geoms", [poly]):
        ax.add_patch(MPoly(np.asarray(g.exterior.coords), closed=True, **kw))
        for h in g.interiors:
            ax.add_patch(MPoly(np.asarray(h.coords), closed=True, fc="white", ec=kw.get("ec", "none"),
                               lw=kw.get("lw", 0.3), zorder=kw.get("zorder", 1),
                               transform=kw.get("transform", ax.transData)))


def plan_rotation(scene: Scene, north_up: bool = True) -> float:
    """Degrees to rotate local-frame geometry so true north points up the sheet."""
    return (scene.frame.rotation_deg - scene.site.true_north_deg) if north_up else 0.0


def draw_plan(ax, scene: Scene, muted: bool = False, labels: bool = True, show_racks: bool = True,
              margin: float = 18.0, north_up: bool = True, extent=None):
    """Site plan. Geometry is in the local frame; a rotation transform puts north up.
    muted=True draws a greyscale base for overlays (noise map)."""
    rot = plan_rotation(scene, north_up)
    tr = Affine2D().rotate_deg(rot) + ax.transData
    R = Affine2D().rotate_deg(rot)

    def fc(kind):
        col = PLAN_FILL.get(kind, "#dddddd")
        return tint("#9a978f", 0.75) if muted and kind not in ("landscape", "road", "parking") else col

    rl = scene.redline_poly()
    ax.add_patch(MPoly(np.asarray(rl.exterior.coords), closed=True, fc="white", ec="none", zorder=0, transform=tr))
    order = ["landscape", "road", "parking"]
    for kind in order:
        for e in scene.by_kind(kind):
            col = BASE[kind] if not muted else tint(BASE[kind], 0.5)
            _poly_patch(ax, e.footprint(), fc=col, ec=shade(col, 0.15), lw=0.3, zorder=1, transform=tr)
            if kind == "parking" and not muted:
                _plan_bays(ax, e, tr)
    # ring road centreline
    for e in scene.by_kind("road"):
        if e.id == "RD-RING" and e.holes:
            inner = Polygon(e.holes[0])
            cl = inner.buffer(3.65, join_style="mitre").exterior
            ax.plot(*np.asarray(cl.coords).T, color="white", lw=0.5, ls=(0, (4, 4)), zorder=1.2, transform=tr)

    solids = [e for e in scene.elements if e.kind not in order and e.kind != "gate"]
    # draw ground-level first, then upper levels dashed (roof plant, level-2 rooms)
    for e in sorted(solids, key=lambda e: (e.z > 0.01, e.z)):
        if e.z > 0.01 and e.kind in ("elec_room", "sts_room", "mv_room", "data_hall", "genset"):
            continue                     # stacked duplicates: labelled, not redrawn
        col = fc(e.kind)
        ec = shade(BASE.get(e.kind, "#999999"), 0.45)
        upper = e.z > 0.01
        kw = dict(fc=col, ec=ec, lw=0.45 if not upper else 0.35, zorder=5 if not upper else 8,
                  ls="-" if not upper else (0, (2, 1)), alpha=1.0 if not upper else 0.85, transform=tr)
        if e.shape == "cyl":
            ax.add_patch(Circle((e.cx, e.cy), e.w / 2, **kw))
        elif e.shape == "poly":
            _poly_patch(ax, e.footprint(), **kw)
        else:
            ax.add_patch(Rectangle((e.x, e.y), e.w, e.d, **kw))
        if e.kind == "fuel_bund":
            ax.add_patch(Rectangle((e.x, e.y), e.w, e.d, fc="none", ec=INK2, lw=0.5, ls=(0, (3, 2)), zorder=6, transform=tr))
        if e.kind == "substation" and not muted:
            ax.add_patch(Rectangle((e.x, e.y), e.w, e.d, fc="none", ec=shade(ORANGE, 0.3), lw=0, hatch="////",
                                   zorder=6, transform=tr))
        if e.kind == "data_hall" and show_racks and not muted:
            g = e.props.get("gallery", 3.5)
            for (gx0, gy0, gx1, gy1) in ((0, 0, e.w, g), (0, e.d - g, e.w, e.d)):
                ax.add_patch(Rectangle((e.x + gx0, e.y + gy0), gx1 - gx0, gy1 - gy0, fc=tint(AQUA, 0.75),
                                       ec="none", zorder=5.5, transform=tr))
            for (a, b, c, d) in e.props.get("pod_rects", []):
                ax.add_patch(Rectangle((e.x + a, e.y + b), c - a, d - b, fc=tint(BLUE, 0.15), ec="none", lw=0,
                                       zorder=6, transform=tr))
            for (a, b, c, d) in e.props.get("aisle_rects", []):
                ax.add_patch(Rectangle((e.x + a, e.y + b), c - a, d - b, fc=tint(BLUE, 0.7), ec="none", zorder=6.5, transform=tr))
    for e in scene.by_kind("gate"):
        _poly_patch(ax, e.footprint(), fc=INK, ec=INK, lw=0.4, zorder=9, transform=tr)

    # electrical room ids (level 1, with the room above)
    rooms = [r for r in scene.by_kind("elec_room", "sts_room", "mv_room")]
    if labels and not muted:
        for r in [r for r in rooms if r.z < 0.01]:
            above = [o.id for o in rooms if o.z > 0.01 and o.x < r.x + r.w - 0.5 and o.x + o.w > r.x + 0.5]
            txt = r.id + ("\n" + "\n".join(above) if above else "")
            px, py = R.transform((r.cx, r.cy))
            ax.text(px, py, txt, ha="center", va="center", fontsize=3.4, color=INK2, zorder=12,
                    rotation=(90 if r.w < 8 else 0) + rot, linespacing=0.95, rotation_mode="anchor")

    ax.plot(*np.asarray(rl.exterior.coords).T, color=REDLINE, lw=1.1, ls=(0, (7, 3)), zorder=20, transform=tr)
    bl = scene.buildable_poly()
    ax.plot(*np.asarray(bl.exterior.coords).T, color=MUTED, lw=0.35, ls=(0, (2, 2)), zorder=19, transform=tr)
    spare = scene.designs.get("spare_land")
    if spare and not muted:
        for ring in spare["polygons"]:
            ax.add_patch(MPoly(np.asarray(ring), closed=True, fc="none", ec="#9a978f", lw=0.4, hatch="....",
                               ls=(0, (1, 2)), zorder=2, transform=tr))

    rpts = R.transform(np.asarray(rl.exterior.coords))
    x0, y0 = rpts.min(0)
    x1, y1 = rpts.max(0)
    if extent is not None:
        x0, y0, x1, y1 = extent
    ax.set_xlim(x0 - margin, x1 + margin)
    ax.set_ylim(y0 - margin, y1 + margin)
    ax.set_aspect("equal")
    ax.axis("off")
    if labels:
        draw_labels(ax, scene, north_up)

    # access arrows
    for k, p in enumerate(scene.site.access_points):
        x, y = R.transform(scene.frame.to_local(*p))
        c = np.array(R.transform(rl.centroid.coords[0]))
        v = c - np.array([x, y])
        v /= np.linalg.norm(v)
        start = np.array([x, y]) - v * 16
        ax.add_patch(FancyArrow(*start, *(v * 14), width=2.2, head_width=6, head_length=5, fc=INK, ec=INK,
                                zorder=30, length_includes_head=True))
        tp = np.array([x, y]) + v * 26
        ax.text(*tp, "Main access" if k == 0 else f"Access {k + 1}", ha="center", va="center",
                fontsize=6, fontweight="bold", zorder=30,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))


def draw_labels(ax, scene: Scene, north_up: bool = True):
    """Object labels, placed in screen space - call after the axes limits are final."""
    R = Affine2D().rotate_deg(plan_rotation(scene, north_up))
    lp = LabelPlacer(ax, fontsize=5.6)
    specs = label_specs(scene)
    for text, (x, y, _), kind in specs:          # halls first, centred
        if kind == "data_hall":
            lp.add(text, tuple(R.transform((x, y))), offsets=[(0, 0)], fontsize=6, fontweight="bold",
                   bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))
    for text, (x, y, _), kind in specs:
        if kind not in ("data_hall", "elec_room"):
            lp.add(text, tuple(R.transform((x, y))), leader=True)
    spare = scene.designs.get("spare_land")
    if spare:
        lp.add(f"Spare land ~{spare['area_ha']:.1f} ha\n(future phase)", tuple(R.transform(spare["label_xy"])),
               offsets=[(0, 0), (0, 14), (0, -14)], color=INK2, fontsize=5.6)


def _plan_bays(ax, e, tr=None):
    p = e.props
    horiz = e.w >= e.d
    L, D = (e.w, e.d) if horiz else (e.d, e.w)
    mod = 2 * p["bay_d"] + p["aisle"]
    nm = max(1, round(D / mod))
    for m in range(nm):
        for side in (0, 1):
            off = m * mod + (0 if side == 0 else p["bay_d"] + p["aisle"])
            for k in range(int(L // p["bay_w"]) + 1):
                s = k * p["bay_w"]
                if horiz:
                    ax.plot([e.x + s, e.x + s], [e.y + off, e.y + off + p["bay_d"]], color="white", lw=0.4,
                            zorder=1.5, transform=tr or ax.transData)
                else:
                    ax.plot([e.x + off, e.x + off + p["bay_d"]], [e.y + s, e.y + s], color="white", lw=0.4,
                            zorder=1.5, transform=tr or ax.transData)
