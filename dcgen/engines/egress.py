"""Escape routes, screening level. Pure geometry + arithmetic, no I/O.

Model (canonical frame, support block at x=0):

  * every hall has `doors_per_hall_wall` doors to the main corridor (south) and to the
    mechanical gallery (north), spaced evenly along the wall;
  * the gallery has a final exit on its north wall at every hall boundary (fire wall or
    cross corridor line) and at its east end; the west end opens into the support block;
  * the main corridor ends in a final exit (east) and the support block (west); cross
    corridors link corridor and gallery;
  * where there are upper floors/levels a stair core closes the east end of the corridor.

Limits come from the catalogue `egress:` block and are PLACEHOLDERS until the project
fire strategy sets them. Travel distances are straight-run screening numbers along
aisles and corridors, not a fire engineer's calculation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import Element


@dataclass
class Egress:
    elements: list[Element] = field(default_factory=list)
    stair_len: float = 0.0
    info: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def hall_door_positions(width: float, n: int) -> list[float]:
    """Door centres along a hall wall, relative to the hall's min x."""
    n = max(1, n)
    return [(i + 0.5) * width / n for i in range(n)]


def hall_interior_travel(width: float, depth: float, n_doors: int) -> float:
    """Worst single-direction run from inside a hall to its nearest door:
    mid-depth along an aisle to the end aisle, then along it to the nearest door."""
    return depth / 2 + width / (2 * max(1, n_doors))


def build_egress(*, EG: dict, S: float, E: float, C: float, Dh: float, M: float, hall_w: float,
                 col_x: list[float], boundaries: list[float], cross_centres: list[float], Lh: float,
                 corridor_end: float, D: float, hall_h: float, band_h: float, rooms: list[tuple[str, float, float, int]],
                 need_stairs: bool) -> Egress:
    """col_x: hall column min-x (relative to S). boundaries: x (relative to S) of internal hall
    boundaries (fire wall / cross corridor centres). rooms: (id, abs x, width, level)."""
    eg = Egress()
    dw = EG["door_width"]
    nd = int(EG["doors_per_hall_wall"])
    n_ex = 0

    def exit_el(x, y, w, d, name, **props):
        nonlocal n_ex
        n_ex += 1
        eg.elements.append(Element(id=f"EX{n_ex:02d}", kind="exit", name=name, x=x, y=y, w=w, d=d, h=2.4,
                                   solid=False, props={"lane_dir": props.pop("lane_dir"), **props}))

    y_gal_n = D
    y_gal_mid = E + C + Dh + M / 2
    # gallery north-wall exits at every internal boundary
    for xb in boundaries:
        exit_el(S + xb - dw / 2, y_gal_n - 0.3, dw, 0.3, "Gallery exit", lane_dir="n")
    # east ends
    x_east_gal = S + Lh
    exit_el(x_east_gal - 0.3, y_gal_mid - dw / 2, 0.3, dw, "Gallery end exit", lane_dir="e")
    exit_el(corridor_end - 0.3, E + C / 2 - dw / 2, 0.3, dw, "Corridor end exit", lane_dir="e")
    # support block exits on its west wall
    for k in range(int(EG["support_exits"])):
        yy = D * (k + 1) / (EG["support_exits"] + 1)
        exit_el(0.0, yy - dw / 2, 0.3, dw, "Support exit", lane_dir="w")
    # optional direct exits from ground-level electrical rooms (south wall)
    if EG.get("plant_room_exits"):
        for rid, rx, rw, lvl in rooms:
            if lvl == 0:
                exit_el(rx + rw / 2 - dw / 2, 0.0, dw, 0.3, f"{rid} exit", lane_dir="s")

    if need_stairs:
        sl = EG["stair"]["length"]
        eg.stair_len = sl
        eg.elements.append(Element(id="STAIR1", kind="stair", name="Escape stair / lift core",
                                   x=corridor_end - sl, y=E, w=sl, d=C, h=band_h if band_h > hall_h else hall_h,
                                   props={"enclosed": True, "serves": "upper levels"}))
        eg.notes.append("Upper floors/levels: east stair core modelled; west stair assumed in the support block. "
                        "Gallery vertical escape is not modelled.")

    # ---- travel distances (screening)
    doors = hall_door_positions(hall_w, nd)
    interior = hall_interior_travel(hall_w, Dh, nd)
    gal_run = lambda dx: min(dx, hall_w - dx)                        # door -> nearest boundary exit in the gallery
    worst_total = 0.0
    steps = 40
    for i in range(steps + 1):
        px = hall_w * i / steps
        best = min(abs(px - dx) + gal_run(dx) for dx in doors)
        worst_total = max(worst_total, Dh / 2 + best + M)
    corr_nodes = [S, corridor_end]
    cross_penalty = Dh + M
    room_worst, room_id = 0.0, ""
    for rid, rx, rw, lvl in rooms:
        xc = rx + rw / 2
        if EG.get("plant_room_exits") and lvl == 0:
            t = E / 2 + rw / 2
        else:
            run = min([abs(xc - n) for n in corr_nodes] + [abs(xc - (S + c)) + cross_penalty for c in cross_centres])
            t = E + run
        if t > room_worst:
            room_worst, room_id = t, rid
    eg.info = {
        "doors_per_hall_wall": nd, "hall_interior_travel_m": round(interior, 1),
        "hall_to_exit_m": round(worst_total, 1), "plant_room_travel_m": round(room_worst, 1),
        "plant_room_worst": room_id, "exits": n_ex, "stairs": 1 if need_stairs else 0,
        "cross_corridors": len(cross_centres), "gallery_exits": len(boundaries) + 1, "dead_end_m": 0.0,
        "limits": {k: EG[k] for k in ("travel_one_dir_m", "travel_two_dir_m", "dead_end_m")},
    }
    return eg
