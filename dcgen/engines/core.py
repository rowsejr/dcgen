"""Building core assembly: halls + electrical band + plant yards, in a canonical
frame (support block at x=0, electrical side south / -y, mechanical side north).

    y
    ^   [ heat rejection yard (ground option) ...................... ]
    |   [ mech gallery ............................................. ]
    |   [ support ][ DH1 ][ DH2 ][ DH3 ][ DH4 ]
    |   [         ][ corridor ..................................... ]
    |   [         ][ EB01 | EB02 | ... electrical rooms (1-3 levels) ]
    |              [ transformers .................................. ]
    |              [ gensets (perpendicular to facade) ............. ]
    +--------------------------------------------------------------------> x

Mirroring (support at the other end / electrical side north) is applied
afterwards so the site engine can test all four arrangements.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..catalogue import Catalogue
from ..schema import ArrayDef, Brief, Element
from .cooling import HRDesign
from .electrical import ElecDesign
from .egress import build_egress, hall_door_positions
from .halls import HallDesign


@dataclass
class Core:
    elements: list[Element]
    arrays: list[ArrayDef]
    bbox: tuple[float, float, float, float]
    anchors: dict[str, tuple[float, float]]
    info: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _spread(n: int, x0: float, length: float, item_w: float) -> list[float]:
    """Evenly distribute n items of width item_w along [x0, x0+length]; returns min-x of each."""
    if n <= 0:
        return []
    pitch = length / n
    return [x0 + (i + 0.5) * pitch - item_w / 2 for i in range(n)]


def _free_segments(x0: float, length: float, lanes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    segs, cur = [], x0
    for a, b in sorted(lanes):
        if a > cur:
            segs.append((cur, min(a, x0 + length)))
        cur = max(cur, b)
    if cur < x0 + length:
        segs.append((cur, x0 + length))
    return [(a, b) for a, b in segs if b > a]


def row_capacity(x0: float, length: float, pitch: float, lanes: list[tuple[float, float]]) -> int:
    return sum(int((b - a) // pitch) for a, b in _free_segments(x0, length, lanes))


def spread_avoiding(n: int, x0: float, length: float, item_w: float, pitch: float,
                    lanes: list[tuple[float, float]]) -> list[float]:
    """Like _spread, but never inside a lane (exit routes through the yard). Items are shared
    between the free segments in proportion to what each can hold."""
    if not lanes:
        return _spread(n, x0, length, item_w)
    segs = _free_segments(x0, length, lanes)
    caps = [int((b - a) // pitch) for a, b in segs]
    total = sum(caps)
    if n > total:
        raise ValueError("row does not fit between exit lanes")
    share = [n * c / total for c in caps]
    alloc = [int(v) for v in share]
    for i in sorted(range(len(segs)), key=lambda i: share[i] - alloc[i], reverse=True)[: n - sum(alloc)]:
        alloc[i] += 1
    xs: list[float] = []
    for (a, b), k in zip(segs, alloc):
        xs += _spread(k, a, b - a, item_w)
    return xs


@dataclass
class Columns:
    """Hall columns along the building with the gaps between them (x relative to the support block)."""
    xs: list[float]
    cross: list[tuple[float, float]]       # cross corridors
    walls: list[tuple[float, float]]       # fire walls
    length: float
    left_gap: list[str]                    # per column: "wall" | "cross" | "" (none) on its west side
    right_gap: list[str]


def hall_columns(n_cols: int, width: float, H: dict) -> Columns:
    xs, cross, walls, lg, rg = [], [], [], [], []
    x, every = 0.0, int(H.get("cross_corridor_every", 0))
    for c in range(n_cols):
        gap = ""
        if c > 0:
            if every and c % every == 0:
                cross.append((x, x + H["cross_corridor_width"]))
                x += H["cross_corridor_width"]
                gap = "cross"
            else:
                walls.append((x, x + H["fire_wall"]))
                x += H["fire_wall"]
                gap = "wall"
        lg.append(gap)
        if c > 0:
            rg[-1] = gap
        rg.append("")
        xs.append(x)
        x += width
    return Columns(xs, cross, walls, x, lg, rg)


def _place_units(*, n, u, kind, hr, start_id, x0, span, y0, z, on_roof, col, serves, group, els, arrays) -> int:
    """n units stood perpendicular to the facade (long axis along local y), side by side along x,
    rows stacked outwards with end-to-end gaps. Returns the number of rows."""
    uw, ul = u["width"], u["length"]
    ic, rc = u["intake_clear"], u["row_clear"]
    pitch, row_pitch = uw + ic, ul + rc
    per_row = max(1, int((span + ic) // pitch))
    rows = math.ceil(n / per_row)
    ids = []
    for i in range(n):
        r, k = divmod(i, per_row)
        n_in = min(per_row, n - r * per_row)
        block = n_in * uw + (n_in - 1) * ic
        el = Element(id=f"HR{start_id + i + 1:02d}", kind=kind, name=f"HR{start_id + i + 1:02d}",
                     x=x0 + (span - block) / 2 + k * pitch, y=y0 + r * row_pitch, w=uw, d=ul, z=z, h=u["height"],
                     group=group, catalogue_key=hr.unit_key,
                     props={"lw_dba": u["lw_dba"], "capacity_kw": round(hr.cap_kw), "on_roof": on_roof,
                            "operating_kg": u.get("operating_kg"), "hall_col": col, "serves": serves,
                            "clear_xy": [ic / 2, rc / 2]})
        els.append(el)
        ids.append(el.id)
    arrays.append(ArrayDef(id=group, kind=kind, count=n, pitch=(pitch, 0), rows=rows, row_pitch=(0, row_pitch),
                           member_ids=ids))
    return rows


def build_core(brief: Brief, cat: Catalogue, hall: HallDesign, elec: ElecDesign, hr: HRDesign,
               n_halls: int, hr_location: str = "ground") -> Core:
    H, ER, LY, EG = cat["halls"], cat["electrical_rooms"], cat["layout"], cat["egress"]
    notes: list[str] = []
    els: list[Element] = []
    arrays: list[ArrayDef] = []

    s = max(1, brief.storeys)
    hps = math.ceil(n_halls / s)
    S = H["support_length"]
    cols = hall_columns(hps, hall.width_x, H)
    Lh = cols.length
    E, C, M, Dh = ER["depth"], H["corridor_depth"], H["mech_gallery_depth"], hall.depth_y
    D = E + C + Dh + M
    sh = H["storey_height"]
    hall_h = s * sh

    # ---- electrical band: rooms in block order, then STS (catcher) and MV rooms
    blocks = list(range(elec.plan.n_blocks))
    bpr = max(1, brief.blocks_per_elec_room)
    rooms = []
    for r in range(elec.n_rooms):
        b = blocks[r * bpr:(r + 1) * bpr]
        rooms.append(("elec_room", f"EB{r + 1:02d}", elec.room_width * len(b), b))
    for g in range(elec.n_sts_rooms):
        rooms.append(("sts_room", f"STS{g + 1:02d}", ER["sts_room_width"], []))
    for m in range(elec.n_mech_lineups):     # mechanical LV switchrooms (separate mechanical lineups)
        rooms.append(("elec_room", f"ML{m + 1:02d}", cat["electrical"]["mech_room_width"], []))
    rooms.append(("mv_room", "MV01", ER["mv_room_width"], []))
    total_w = sum(r[2] for r in rooms)

    def fit(levels: int, length: float) -> list[tuple[int, float]] | None:
        pos, lvl, cur = [], 0, 0.0
        for _, _, w, _ in rooms:
            if cur + w > length + 1e-6:
                lvl, cur = lvl + 1, 0.0
                if lvl >= levels or w > length:
                    return None
            pos.append((lvl, cur))
            cur += w
        return pos

    step = LY["band_step"]
    levels = brief.elec_room_levels
    band_len = Lh
    placement = None
    if levels:
        placement = fit(levels, band_len)
        while placement is None:
            band_len += step
            placement = fit(levels, band_len)
    else:
        for lv in (1, 2, 3):
            placement = fit(lv, band_len)
            if placement:
                levels = lv
                break
        if placement is None:
            levels = 3
            while placement is None:
                band_len += step
                placement = fit(levels, band_len)
    if band_len > Lh + 1e-6:
        notes.append(f"Electrical rooms need {band_len:.0f} m of facade on {levels} levels; "
                     f"electrical band extends {band_len - Lh:.0f} m beyond the halls.")
    notes.append(f"{len(rooms)} electrical-band rooms on {levels} level(s), "
                 f"{total_w:.0f} m of room frontage.")
    lvl_h = ER["level_height"]
    band_h = levels * lvl_h
    corr_len = max(Lh, band_len)
    L = S + corr_len

    # ---- escape routes (before the corridor is cut for the stair core)
    placed_rooms = [(rid, S + cur, w, lvl) for (_, rid, w, _), (lvl, cur) in zip(rooms, placement)]
    boundaries = [(a + b) / 2 for a, b in cols.cross + cols.walls]
    eg = build_egress(EG=EG, S=S, E=E, C=C, Dh=Dh, M=M, hall_w=hall.width_x, col_x=cols.xs,
                      boundaries=sorted(boundaries), cross_centres=[(a + b) / 2 for a, b in cols.cross], Lh=Lh,
                      corridor_end=L, D=D, hall_h=hall_h, band_h=band_h, rooms=placed_rooms,
                      need_stairs=(s > 1 or levels > 1))
    notes += eg.notes
    lane_margin = max(cat["transformers"][elec.tx_key]["clear"], elec.genset["side_clear"] / 2)
    lanes = ([(rx + rw / 2 - EG["exit_lane_width"] / 2 - lane_margin, rx + rw / 2 + EG["exit_lane_width"] / 2 + lane_margin)
              for _, rx, rw, lvl in placed_rooms if lvl == 0] if EG.get("plant_room_exits") else [])

    # ---- enclosed volumes
    els.append(Element(id="SUP", kind="support", name="Support / admin / loading",
                       x=0, y=0, w=S, d=D, h=max(hall_h, band_h), props={"enclosed": True}))
    els.append(Element(id="COR", kind="corridor", name="Corridor", x=S, y=E, w=corr_len - eg.stair_len, d=C,
                       h=hall_h, props={"enclosed": True}))
    els.append(Element(id="MECH", kind="mech_gallery", name="Mechanical gallery", x=S, y=E + C + Dh,
                       w=Lh, d=M, h=hall_h, props={"enclosed": True}))
    for k, (a, b) in enumerate(cols.cross):
        els.append(Element(id=f"XC{k + 1}", kind="corridor", name=f"Cross corridor {k + 1}", x=S + a, y=E + C,
                           w=b - a, d=Dh, h=hall_h, props={"enclosed": True, "cross": True}))
    for k, (a, b) in enumerate(cols.walls):
        els.append(Element(id=f"FW{k + 1}", kind="fire_wall", name=f"Fire wall {k + 1}", x=S + a, y=E + C,
                           w=b - a, d=Dh, h=hall_h, props={"enclosed": True}))
    doors = {"corridor": hall_door_positions(hall.width_x, EG["doors_per_hall_wall"]),
             "gallery": hall_door_positions(hall.width_x, EG["doors_per_hall_wall"])}
    for i in range(n_halls):
        st, col = divmod(i, hps)
        els.append(Element(
            id=f"DH{i + 1:02d}", kind="data_hall", name=f"Data Hall {i + 1}",
            x=S + cols.xs[col], y=E + C, w=hall.width_x, d=Dh, z=st * sh, h=sh,
            props={"enclosed": True, "it_kw": hall.it_kw, "racks": hall.racks_provided,
                   "rows": hall.rows, "pods": hall.pods, "area_m2": hall.area_m2,
                   "storey": st, "col": col, "pod_rects": hall.pod_rects, "aisle_rects": hall.aisle_rects,
                   "gallery": hall.gallery, "doors": doors}))
    for (kind, rid, w, b), (lvl, cur) in zip(rooms, placement):
        props = {"enclosed": True, "level": lvl, "blocks": b}
        if rid.startswith("ML"):
            props["mech_lineup"] = int(rid[2:]) - 1
        if b:
            props["ups_kw"] = round(elec.plan.block_kw * len(b))
            props["groups"] = sorted({bi // elec.topology.x for bi in b})
        els.append(Element(id=rid, kind=kind, name=rid, x=S + cur, y=0, w=w, d=E, z=lvl * lvl_h,
                           h=lvl_h, props=props))
    els += eg.elements

    # ---- south: transformers then gensets (IT lineups first, then mechanical lineups)
    tx, g = elec.transformer, elec.genset
    mtx, mg = (elec.mech_transformer or tx), (elec.mech_genset or g)
    tx_clear_bldg, gap = LY["tx_to_building"], LY["tx_to_gen_yard"]
    tx_c = max(tx["clear"], mtx["clear"])
    tx_pitch = max(tx["width"], mtx["width"]) + 2 * tx_c
    tx_per_row = max(1, row_capacity(S, band_len, tx_pitch, lanes) if lanes else int(band_len // tx_pitch))
    n_tx = elec.n_transformers
    n_it_tx = elec.n_it_tx
    tx_rows = math.ceil(n_tx / tx_per_row)
    tx_row_d = max(tx["length"], mtx["length"]) + 2 * tx_c
    ids = []
    for r in range(tx_rows):
        n_in = min(tx_per_row, n_tx - r * tx_per_row)
        ytop = -tx_clear_bldg - r * tx_row_d
        for k, x in enumerate(spread_avoiding(n_in, S, band_len, max(tx["width"], mtx["width"]), tx_pitch, lanes)):
            i = r * tx_per_row + k
            this, key = (tx, elec.tx_key) if i < n_it_tx else (mtx, elec.mech_tx_key or elec.tx_key)
            if i < n_it_tx:
                bi = i // elec.tx_per_block
                props = {"kva": this["kva"], "block": bi, "group_no": bi // elec.topology.x}
            else:
                props = {"kva": this["kva"], "mech_lineup": i - n_it_tx}
            props["clear_xy"] = [this["clear"], this["clear"]]
            el = Element(id=f"TX{i + 1:02d}", kind="transformer", name=f"TX{i + 1:02d}",
                         x=x, y=ytop - this["length"], w=this["width"], d=this["length"], h=this["height"],
                         group="tx-array", catalogue_key=key, props=props)
            els.append(el)
            ids.append(el.id)
    arrays.append(ArrayDef(id="tx-array", kind="transformer", count=n_tx, pitch=(tx_pitch, 0),
                           rows=tx_rows, row_pitch=(0, -tx_row_d), member_ids=ids))
    y_gen_top = -tx_clear_bldg - tx_rows * tx_row_d - gap

    tiers = max(1, brief.genset_tiers)
    g_side = max(g["side_clear"], mg["side_clear"])
    g_w = max(g["width"], mg["width"])
    g_pitch = g_w + g_side
    g_per_row = max(1, row_capacity(S, band_len, g_pitch, lanes) if lanes else int(band_len // g_pitch))
    n_gen = elec.n_gensets
    n_it_gen = elec.n_it_gensets
    g_rows = math.ceil(n_gen / (g_per_row * tiers))
    g_row_d = max(g["length"], mg["length"]) + max(g["end_clear"], mg["end_clear"])
    ids = []
    per_level = g_per_row * g_rows
    for i in range(n_gen):
        tier, j = divmod(i, per_level)
        r, k = divmod(j, g_per_row)
        n_in = min(g_per_row, min(n_gen - tier * per_level, per_level) - r * g_per_row)
        xs = spread_avoiding(n_in, S, band_len, g_w, g_pitch, lanes)
        this, key = (g, elec.genset_key) if i < n_it_gen else (mg, elec.mech_genset_key or elec.genset_key)
        props = {"kva": this["kva"], "kwe": this["kwe"], "lw_dba": this["lw_dba"], "tier": tier,
                 "clear_xy": [this["side_clear"] / 2, this["end_clear"] / 2]}
        if i < n_it_gen:
            bi = i // elec.gensets_per_block
            props.update({"block": bi, "group_no": bi // elec.topology.x})
        else:
            props["mech_lineup"] = i - n_it_gen
        el = Element(id=f"GEN{i + 1:02d}", kind="genset", name=f"G{i + 1:02d}",
                     x=xs[k], y=y_gen_top - r * g_row_d - this["length"], w=this["width"], d=this["length"],
                     z=tier * (max(g["height"], mg["height"]) + LY["genset_tier_gap"]), h=this["height"],
                     group="gen-array", catalogue_key=key, props=props)
        els.append(el)
        ids.append(el.id)
    if g_rows > 1:
        notes.append(f"Gen yard needs {g_rows} rows of {g_per_row} sets ({tiers} tier).")
    arrays.append(ArrayDef(id="gen-array", kind="genset", count=n_gen, pitch=(g_pitch, 0),
                           rows=g_rows, row_pitch=(0, -g_row_d), member_ids=ids))
    y_south = y_gen_top - g_rows * g_row_d

    # ---- heat rejection: grouped per hall column, units perpendicular to the facade (north side)
    u = hr.unit
    kind = "chiller" if u["type"] == "air_cooled_chiller" else "dry_cooler"
    uw, ul = u["width"], u["length"]
    n_hr = hr.n_total
    base, extra = divmod(n_hr, hps)
    per_col = [base + (1 if c < extra else 0) for c in range(hps)]
    lane = EG["hr_lane_width"]
    edge = H["roof_edge"]
    ic, rc = u["intake_clear"], u["row_clear"]
    on_roof = n_ground = 0
    rows_max_ground = 0
    next_id = 0
    for c in range(hps):
        serves = [f"DH{st * hps + c + 1:02d}" for st in range(s) if st * hps + c < n_halls]
        cx0 = S + cols.xs[c]
        n_c = per_col[c]
        n_roof_c = 0
        if hr_location == "roof" and n_c:
            rl = hall.width_x - (edge if c == 0 else 0) - (edge if c == hps - 1 else 0)
            rd = Dh + M - 2 * edge
            per_row = max(0, int((rl + ic) // (uw + ic)))
            rows_cap = max(0, int((rd + rc) // (ul + rc)))
            n_roof_c = min(n_c, per_row * rows_cap)
            if n_roof_c:
                rows_n = math.ceil(n_roof_c / per_row)
                span_y = rows_n * ul + (rows_n - 1) * rc
                _place_units(n=n_roof_c, u=u, kind=kind, hr=hr, start_id=next_id, x0=cx0 + (edge if c == 0 else 0),
                             span=rl, y0=E + C + edge + (rd - span_y) / 2, z=hall_h, on_roof=True, col=c,
                             serves=serves, group=f"hr-roof-C{c + 1}", els=els, arrays=arrays)
                next_id += n_roof_c
                on_roof += n_roof_c
        n_g = n_c - n_roof_c
        if n_g:
            inset_l = lane / 2 if cols.left_gap[c] == "wall" else 0.0
            inset_r = lane / 2 if cols.right_gap[c] == "wall" else 0.0
            rows = _place_units(n=n_g, u=u, kind=kind, hr=hr, start_id=next_id, x0=cx0 + inset_l,
                                span=hall.width_x - inset_l - inset_r, y0=D + LY["hr_yard_offset"], z=0.0,
                                on_roof=False, col=c, serves=serves, group=f"hr-ground-C{c + 1}", els=els,
                                arrays=arrays)
            rows_max_ground = max(rows_max_ground, rows)
            next_id += n_g
            n_ground += n_g
    if hr_location == "roof" and on_roof < n_hr:
        notes.append(f"Roof takes {on_roof} of {n_hr} heat rejection units; {n_hr - on_roof} go in a ground yard.")
    notes.append(f"Heat rejection: {n_hr} units in {hps} group(s) of {min(per_col)}-{max(per_col)}, "
                 f"perpendicular to the facade, one group per hall column.")
    y_north = D
    if n_ground:
        y_north = D + LY["hr_yard_offset"] + rows_max_ground * ul + (rows_max_ground - 1) * rc + LY["hr_yard_margin"]

    anchors = {
        "entrance": (0.0, D / 2),
        "elec_centre": (S + band_len / 2, E / 2),
        "gen_centre": (S + band_len / 2, (y_gen_top + y_south) / 2),
        "building_centre": (L / 2, D / 2),
    }
    info = {"building_length": L, "building_depth": D, "halls_per_storey": hps, "hall_height": hall_h,
            "elec_levels": levels, "elec_band_length": band_len, "hr_on_roof": on_roof,
            "hr_on_ground": n_ground, "hr_per_hall": per_col, "gen_rows": g_rows, "tx_rows": tx_rows,
            "cross_corridors": len(cols.cross), "fire_walls": len(cols.walls), "egress": eg.info}
    return Core(els, arrays, (0.0, y_south, L, y_north), anchors, info, notes)


def mirror_core(core: Core, mirror_x: bool, mirror_y: bool) -> Core:
    x0, y0, x1, y1 = core.bbox
    flip = {"e": "w", "w": "e", "n": "s", "s": "n"}
    for e in core.elements:
        if e.kind == "exit":
            d = e.props["lane_dir"]
            if (mirror_x and d in "ew") or (mirror_y and d in "ns"):
                e.props["lane_dir"] = flip[d]
        if mirror_x:
            e.x = x0 + x1 - e.x - e.w
        if mirror_y:
            e.y = y0 + y1 - e.y - e.d
            if e.kind == "data_hall":
                # rack geometry is stored hall-relative; flip it too
                for key in ("pod_rects", "aisle_rects"):
                    e.props[key] = [(a, e.d - d, c, e.d - b) for (a, b, c, d) in e.props[key]]
    for k, (ax, ay) in list(core.anchors.items()):
        core.anchors[k] = (x0 + x1 - ax if mirror_x else ax, y0 + y1 - ay if mirror_y else ay)
    for a in core.arrays:
        a.pitch = (-a.pitch[0] if mirror_x else a.pitch[0], a.pitch[1])
        a.row_pitch = (a.row_pitch[0], -a.row_pitch[1] if mirror_y else a.row_pitch[1])
    return core
