"""Screening-level noise: point sources, hemispherical spreading.

  Lp = Lw - 20 log10(r) - 8 - A_screen        (summed logarithmically)

A_screen = roof parapet/screen, yard barrier, plus a building-screening term
when the straight line from a ground source to the receptor crosses the
building. This is a Stage 2 sieve only; replace with ISO 9613-2 (ground,
barriers, air absorption, directivity) or a CadnaA/SoundPLAN run before
anything goes to an acoustician or a planning submission (BS 4142 context).
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import Polygon

from ..schema import Brief, Scene

NOISE_KINDS = ("chiller", "dry_cooler")



def lw_from_lp1m(lp_1m: float, length: float, width: float, height: float) -> float:
    """Sound power from a 1 m sound pressure all round a box on the ground (ISO 3744 parallelepiped surface)."""
    a, b, c = length + 2.0, width + 2.0, height + 1.0
    s = a * b + 2 * a * c + 2 * b * c
    return round(lp_1m + 10 * math.log10(s), 1)

def sources(scene: Scene, include_gensets: bool = False):
    b = scene.brief
    pts, lw, att, ground = [], [], [], []
    kinds = NOISE_KINDS + (("genset",) if include_gensets else ())
    for e in scene.by_kind(*kinds):
        pts.append((e.cx, e.cy, e.top))
        lw.append(e.props.get("lw_dba", 90.0))
        on_roof = e.props.get("on_roof", False)
        att.append(b.roof_screen_db if on_roof else b.yard_barrier_db)
        ground.append(not on_roof)
    return np.array(pts).reshape(-1, 3), np.array(lw), np.array(att), np.array(ground, bool)


def lp_at(points: np.ndarray, scene: Scene, include_gensets: bool = False,
          building: Polygon | None = None) -> np.ndarray:
    """points: (N, 3) local coords -> Lp dB(A) at each point."""
    src, lw, att, ground = sources(scene, include_gensets)
    if len(src) == 0:
        return np.full(len(points), -np.inf)
    d = points[:, None, :] - src[None, :, :]
    r = np.maximum(np.linalg.norm(d, axis=2), 1.0)
    level = lw[None, :] - 20 * np.log10(r) - 8.0 - att[None, :]
    if building is not None and ground.any():
        # building screening for ground sources only (roof units see over it)
        gi = np.where(ground)[0]
        a = np.repeat(points[:, None, :2], len(gi), axis=1).reshape(-1, 2)
        bb = np.repeat(src[None, gi, :2], len(points), axis=0).reshape(-1, 2)
        lines = shapely.linestrings(np.stack([a, bb], axis=1))
        blocked = shapely.intersects(lines, building.buffer(-0.5)).reshape(len(points), len(gi))
        level[:, gi] -= blocked * scene.brief.building_screen_db
    return 10 * np.log10(np.sum(10 ** (level / 10), axis=1))


def building_polygon(scene: Scene) -> Polygon | None:
    parts = [e.footprint() for e in scene.elements if e.props.get("enclosed")]
    return shapely.union_all(parts) if parts else None


def receptor_levels(scene: Scene) -> list[dict]:
    out = []
    bpoly = building_polygon(scene)
    for r in scene.site.receptors:
        x, y = scene.frame.to_local(r.x, r.y)
        lp = float(lp_at(np.array([[x, y, r.z]]), scene, building=bpoly)[0])
        out.append({"name": r.name, "lp_dba": round(lp, 1), "limit_dba": r.limit_dba,
                    "margin_db": round(r.limit_dba - lp, 1), "x_local": x, "y_local": y})
    return out


def noise_grid(scene: Scene, pad: float = 150.0, step: float = 6.0):
    rl = scene.redline_poly()
    x0, y0, x1, y1 = rl.bounds
    pts_local = [scene.frame.to_local(r.x, r.y) for r in scene.site.receptors]
    for px, py in pts_local:
        x0, y0, x1, y1 = min(x0, px - 30), min(y0, py - 30), max(x1, px + 30), max(y1, py + 30)
    xs = np.arange(x0 - pad, x1 + pad, step)
    ys = np.arange(y0 - pad, y1 + pad, step)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, 1.5)])
    lp = lp_at(pts, scene, building=building_polygon(scene)).reshape(gx.shape)
    return gx, gy, lp
