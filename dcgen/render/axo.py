"""Vector axonometric renderer for massing models (no OpenGL needed).

Every solid is a vertical prism (box, n-gon cylinder or extruded polygon).
Objects are ordered with a painter's algorithm: for each pair whose screen
boxes overlap, a separating axis of their 3D bounds decides which is behind
(camera direction sign on that axis), then a topological sort. Faces are
back-face culled and Lambert-shaded; shadows are projected onto the ground and
onto roofs (as flat objects that join the sort). Output is pure vector, so it
stays crisp in the PDF at any zoom.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import shapely
from matplotlib.patches import Polygon as MPoly
from shapely.geometry import Polygon

from ..schema import Scene
from .style import BASE, INK, INK2, REDLINE, shade, tint

GROUND = ("road", "parking", "landscape", "gate")


@dataclass
class Obj:
    ring: np.ndarray            # (n, 2) ccw footprint
    z0: float
    z1: float
    color: str
    id: str = ""
    kind: str = ""
    flat: bool = False          # shadow / ground decal
    alpha: float = 1.0
    holes: list = field(default_factory=list)

    @property
    def aabb(self):
        return (*self.ring.min(0), self.z0, *self.ring.max(0), self.z1)


def camera(az_deg: float, el_deg: float):
    az, el = math.radians(az_deg), math.radians(el_deg)
    c = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    right = np.cross(-c, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, -c)
    return c, right, up


def _ccw(ring: np.ndarray) -> np.ndarray:
    x, y = ring[:, 0], ring[:, 1]
    a = np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)
    return ring if a > 0 else ring[::-1]


def _ring(poly: Polygon) -> np.ndarray:
    return _ccw(np.asarray(poly.exterior.coords)[:-1])


def scene_objects(scene: Scene, sun=(-0.35, -0.75, 1.0)):
    """Solids, ground decals and shadow objects for a scene."""
    solids, ground = [], []
    for e in scene.elements:
        fp = e.footprint()
        if e.kind in GROUND or e.h <= 0:
            ground.append((e, fp))
            continue
        solids.append(Obj(_ring(fp.simplify(0.01)), e.z, e.top, BASE.get(e.kind, "#cccccc"), e.id, e.kind))

    # shadows: project each solid along the sun onto the plane it stands on
    s = np.array(sun) / np.linalg.norm(sun)
    by_plane = defaultdict(list)
    for o in solids:
        k = (o.z1 - o.z0) / s[2]
        top = Polygon(o.ring - s[:2] * k)
        sh = shapely.union_all([Polygon(o.ring), top]).convex_hull
        by_plane[round(o.z0, 2)].append((o, sh))
    shadows = []
    supports = [o for o in solids]
    for z, items in by_plane.items():
        u = shapely.union_all([sh for _, sh in items])
        if z <= 0.01:
            shadows.append(("ground", u))
            continue
        # clip roof shadows to the top faces at this height
        tops = [Polygon(o.ring) for o in supports if abs(o.z1 - z) < 0.05]
        if not tops:
            continue
        for t in tops:
            part = u.intersection(t).difference(shapely.union_all([Polygon(o.ring) for o, _ in items]))
            for g in getattr(part, "geoms", [part]):
                if g.geom_type == "Polygon" and g.area > 0.5:
                    shadows.append((z, g))
    flats = []
    for z, g in shadows:
        if z == "ground":
            continue
        flats.append(Obj(_ring(g), z, z, "#000000", flat=True, alpha=0.16))
    ground_shadow = [g for z, g in shadows if z == "ground"]
    return solids + flats, ground, ground_shadow


def _order(objs: list[Obj], c, right, up) -> list[int]:
    n = len(objs)
    scr = []
    for o in objs:
        P = np.concatenate([np.column_stack([o.ring, np.full(len(o.ring), o.z0)]),
                            np.column_stack([o.ring, np.full(len(o.ring), o.z1)])])
        u, v = P @ right, P @ up
        scr.append((u.min(), u.max(), v.min(), v.max()))
    scr = np.array(scr)
    bb = np.array([o.aabb for o in objs])
    depth = np.array([((b[:3] + b[3:]) / 2) @ c for b in bb])
    after = defaultdict(set)     # i -> set of j that must be drawn after i
    indeg = np.zeros(n, int)
    eps = 1e-6
    for i in range(n):
        ov = ((scr[:, 0] < scr[i, 1] - eps) & (scr[:, 1] > scr[i, 0] + eps) &
              (scr[:, 2] < scr[i, 3] - eps) & (scr[:, 3] > scr[i, 2] + eps))
        for j in np.where(ov)[0]:
            if j <= i:
                continue
            a, b = bb[i], bb[j]
            first = None
            for k in (2, 0, 1):                     # z first: stacking is unambiguous
                if abs(c[k]) < 1e-9:
                    continue
                if a[3 + k] <= b[k] + 1e-6:          # i below/behind j on axis k
                    first = (i, j) if c[k] > 0 else (j, i)
                    break
                if b[3 + k] <= a[k] + 1e-6:
                    first = (j, i) if c[k] > 0 else (i, j)
                    break
            if first is None:
                first = (i, j) if depth[i] < depth[j] else (j, i)
            p, q = first
            if q not in after[p]:
                after[p].add(q)
                indeg[q] += 1
    # Kahn, tie-break by depth (far first); break cycles by depth
    order, done = [], np.zeros(n, bool)
    ready = sorted([i for i in range(n) if indeg[i] == 0], key=lambda i: depth[i])
    while len(order) < n:
        if not ready:
            i = min((k for k in range(n) if not done[k]), key=lambda k: depth[k])
        else:
            i = ready.pop(0)
        if done[i]:
            continue
        done[i] = True
        order.append(i)
        for j in after[i]:
            indeg[j] -= 1
            if indeg[j] <= 0 and not done[j]:
                ready.append(j)
        ready.sort(key=lambda k: depth[k])
    return order


def draw_axo(ax, scene: Scene, az: float = -50, el: float = 32, labels: bool = True,
             light=(-0.3, -0.85, 1.25), sun=(-0.35, -0.75, 1.0), apron: float = 15.0,
             label_kinds=None, fontsize: float = 6.0, focus: str = "site"):
    c, right, up = camera(az, el)
    L = np.array(light) / np.linalg.norm(light)

    def P2(ring, z):
        P = np.column_stack([ring, np.full(len(ring), z)])
        return np.column_stack([P @ right, P @ up])

    objs, ground, gshadow = scene_objects(scene, sun)

    # ---- ground: apron, site, decals, shadows, redline
    rl = scene.redline_poly()
    x0, y0, x1, y1 = rl.bounds
    apr = np.array([[x0 - apron, y0 - apron], [x1 + apron, y0 - apron], [x1 + apron, y1 + apron],
                    [x0 - apron, y1 + apron]])
    ax.add_patch(MPoly(P2(apr, 0), closed=True, fc="#f1f0ec", ec="none", zorder=0))
    ax.add_patch(MPoly(P2(_ring(rl), 0), closed=True, fc="#f8f7f4", ec="none", zorder=0))
    for e, fp in sorted(ground, key=lambda t: ("landscape", "road", "parking", "gate").index(t[0].kind)
                        if t[0].kind in ("landscape", "road", "parking", "gate") else 9):
        col = BASE.get(e.kind, "#dddddd")
        for g in getattr(fp, "geoms", [fp]):
            path = P2(_ring(g), 0)
            ax.add_patch(MPoly(path, closed=True, fc=col, ec=shade(col, 0.12), lw=0.25, zorder=1))
            for h in g.interiors:
                hole = Polygon(h)
                ax.add_patch(MPoly(P2(_ring(hole), 0), closed=True, fc="#f8f7f4", ec=shade(col, 0.12),
                                   lw=0.25, zorder=1))
        if e.kind == "parking":
            _parking_lines(ax, e, P2)
    for g in gshadow:
        for p in getattr(g, "geoms", [g]):
            ax.add_patch(MPoly(P2(_ring(p), 0), closed=True, fc="#000000", alpha=0.13, ec="none", zorder=2))
            for h in p.interiors:   # re-light courtyards
                ax.add_patch(MPoly(P2(_ring(Polygon(h)), 0), closed=True, fc="#f8f7f4", ec="none", zorder=2))
    ax.plot(*np.vstack([P2(_ring(rl), 0), P2(_ring(rl), 0)[:1]]).T, color=REDLINE, lw=1.0,
            ls=(0, (6, 3)), zorder=3)

    # ---- solids in painter's order
    order = _order(objs, c, right, up)
    z = 10
    for i in order:
        o = objs[i]
        if o.flat:
            ax.add_patch(MPoly(P2(o.ring, o.z1), closed=True, fc="#000000", alpha=o.alpha, ec="none", zorder=z))
            z += 1
            continue
        base = np.array([int(o.color[k:k + 2], 16) / 255 for k in (1, 3, 5)])
        ring = o.ring
        nxt = np.roll(ring, -1, axis=0)
        faces = []
        for a, b in zip(ring, nxt):
            d = b - a
            nrm = np.array([d[1], -d[0], 0.0])
            nn = np.linalg.norm(nrm)
            if nn < 1e-9:
                continue
            nrm /= nn
            if nrm @ c <= 1e-6:
                continue
            quad = np.array([[*a, o.z0], [*b, o.z0], [*b, o.z1], [*a, o.z1]])
            faces.append((quad, nrm))
        for quad, nrm in sorted(faces, key=lambda f: f[0].mean(0) @ c):
            k = 0.60 + 0.40 * max(0.0, nrm @ L)
            col = np.clip(base * k, 0, 1)
            pts = np.column_stack([quad @ right, quad @ up])
            ax.add_patch(MPoly(pts, closed=True, fc=col, ec=np.clip(base * 0.52, 0, 1), lw=0.3,
                               joinstyle="round", zorder=z))
        top = P2(ring, o.z1)
        k = 0.60 + 0.40 * max(0.0, L[2])
        ax.add_patch(MPoly(top, closed=True, fc=np.clip(base * k + 0.04, 0, 1),
                           ec=np.clip(base * 0.52, 0, 1), lw=0.3, joinstyle="round", zorder=z))
        z += 1

    # ---- framing
    if focus == "core":
        solid_xy = np.vstack([o.ring for o in objs if not o.flat])
        fx0, fy0 = solid_xy.min(0) - 25
        fx1, fy1 = solid_xy.max(0) + 25
        box_ = np.array([[fx0, fy0], [fx1, fy0], [fx1, fy1], [fx0, fy1]])
        allp = np.vstack([P2(box_, 0), P2(box_, max(o.z1 for o in objs))])
    else:
        allp = np.vstack([P2(_ring(rl), 0)] + [P2(o.ring, o.z1) for o in objs])
    pad = 8
    ax.set_xlim(allp[:, 0].min() - pad, allp[:, 0].max() + pad)
    ax.set_ylim(allp[:, 1].min() - pad, allp[:, 1].max() + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    if labels:
        _labels(ax, scene, P2, label_kinds, fontsize)


def _parking_lines(ax, e, P2):
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
                    seg = np.array([[e.x + s, e.y + off], [e.x + s, e.y + off + p["bay_d"]]])
                else:
                    seg = np.array([[e.x + off, e.y + s], [e.x + off + p["bay_d"], e.y + s]])
                q = P2(seg, 0.02)
                ax.plot(q[:, 0], q[:, 1], color="#ffffff", lw=0.35, zorder=1.5)


def _labels(ax, scene: Scene, P2, kinds=None, fs=6.0):  # noqa: D401
    from .plan import LabelPlacer, label_specs
    lp = LabelPlacer(ax, fontsize=fs)
    offs = [(0, 14), (0, 28), (24, 18), (-24, 18), (0, 42), (40, 30), (-40, 30), (0, 56), (50, 6), (-50, 6)]
    xl, yl = ax.get_xlim(), ax.get_ylim()
    for text, (x, y, h), _ in label_specs(scene, kinds):
        q = P2(np.array([[x, y]]), h)[0]
        if not (xl[0] < q[0] < xl[1] and yl[0] < q[1] < yl[1]):
            continue
        lp.add(text, tuple(q), offsets=offs, leader=True)
