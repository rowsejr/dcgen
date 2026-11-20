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


def build_core(brief: Brief, cat: Catalogue, hall: HallDesign, elec: ElecDesign, hr: HRDesign,
               n_halls: int, hr_location: str = "ground") -> Core:
    H, ER = cat["halls"], cat["electrical_rooms"]
    notes: list[str] = []
    els: list[Element] = []
    arrays: list[ArrayDef] = []

    s = max(1, brief.storeys)
    hps = math.ceil(n_halls / s)
    S = H["support_length"]
    Lh = hps * hall.width_x
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

    levels = brief.elec_room_levels
    band_len = Lh
    placement = None
    if levels:
        placement = fit(levels, band_len)
        while placement is None:
            band_len += 2.0
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
                band_len += 2.0
                placement = fit(levels, band_len)
    if band_len > Lh + 1e-6:
        notes.append(f"Electrical rooms need {band_len:.0f} m of facade on {levels} levels; "
                     f"electrical band extends {band_len - Lh:.0f} m beyond the halls.")
    notes.append(f"{len(rooms)} electrical-band rooms on {levels} level(s), "
                 f"{total_w:.0f} m of room frontage.")
    lvl_h = ER["level_height"]
    band_h = levels * lvl_h
    L = S + max(Lh, band_len)

    # ---- enclosed volumes
    els.append(Element(id="SUP", kind="support", name="Support / admin / loading",
                       x=0, y=0, w=S, d=D, h=max(hall_h, band_h), props={"enclosed": True}))
    els.append(Element(id="COR", kind="corridor", name="Corridor", x=S, y=E, w=max(Lh, band_len), d=C,
                       h=hall_h, props={"enclosed": True}))
    els.append(Element(id="MECH", kind="mech_gallery", name="Mechanical gallery", x=S, y=E + C + Dh,
                       w=Lh, d=M, h=hall_h, props={"enclosed": True}))
    for i in range(n_halls):
        st, col = divmod(i, hps)
        els.append(Element(
            id=f"DH{i + 1:02d}", kind="data_hall", name=f"Data Hall {i + 1}",
            x=S + col * hall.width_x, y=E + C, w=hall.width_x, d=Dh, z=st * sh, h=sh,
            props={"enclosed": True, "it_kw": hall.it_kw, "racks": hall.racks_provided,
                   "rows": hall.rows, "pods": hall.pods, "area_m2": hall.area_m2,
                   "storey": st, "pod_rects": hall.pod_rects, "aisle_rects": hall.aisle_rects,
                   "gallery": hall.gallery}))
    for (kind, rid, w, b), (lvl, cur) in zip(rooms, placement):
        props = {"enclosed": True, "level": lvl, "blocks": b}
        if b:
            props["ups_kw"] = round(elec.plan.block_kw * len(b))
            props["groups"] = sorted({bi // elec.topology.x for bi in b})
        els.append(Element(id=rid, kind=kind, name=rid, x=S + cur, y=0, w=w, d=E, z=lvl * lvl_h,
                           h=lvl_h, props=props))

    # ---- south: transformers then gensets
    tx, g = elec.transformer, elec.genset
    tx_clear_bldg, gap = 2.0, 3.0
    tx_pitch = tx["width"] + 2 * tx["clear"]
    tx_per_row = max(1, int(band_len // tx_pitch))
    n_tx = elec.n_transformers
    tx_rows = math.ceil(n_tx / tx_per_row)
    tx_row_d = tx["length"] + 2 * tx["clear"]
    ids = []
    for r in range(tx_rows):
        n_in = min(tx_per_row, n_tx - r * tx_per_row)
        ytop = -tx_clear_bldg - r * tx_row_d
        for k, x in enumerate(_spread(n_in, S, band_len, tx["width"])):
            i = r * tx_per_row + k
            bi = i // elec.tx_per_block
            el = Element(id=f"TX{i + 1:02d}", kind="transformer", name=f"TX{i + 1:02d}",
                         x=x, y=ytop - tx["length"], w=tx["width"], d=tx["length"], h=tx["height"],
                         group="tx-array", catalogue_key=elec.tx_key,
                         props={"kva": tx["kva"], "block": bi, "group_no": bi // elec.topology.x})
            els.append(el)
            ids.append(el.id)
    arrays.append(ArrayDef(id="tx-array", kind="transformer", count=n_tx, pitch=(tx_pitch, 0),
                           rows=tx_rows, row_pitch=(0, -tx_row_d), member_ids=ids))
    y_gen_top = -tx_clear_bldg - tx_rows * tx_row_d - gap

    tiers = max(1, brief.genset_tiers)
    g_pitch = g["width"] + g["side_clear"]
    g_per_row = max(1, int(band_len // g_pitch))
    n_gen = elec.n_gensets
    g_rows = math.ceil(n_gen / (g_per_row * tiers))
    g_row_d = g["length"] + g["end_clear"]
    ids = []
    per_level = g_per_row * g_rows
    for i in range(n_gen):
        tier, j = divmod(i, per_level)
        r, k = divmod(j, g_per_row)
        n_in = min(g_per_row, min(n_gen - tier * per_level, per_level) - r * g_per_row)
        xs = _spread(n_in, S, band_len, g["width"])
        bi = i // elec.gensets_per_block
        el = Element(id=f"GEN{i + 1:02d}", kind="genset", name=f"G{i + 1:02d}",
                     x=xs[k], y=y_gen_top - r * g_row_d - g["length"], w=g["width"], d=g["length"],
                     z=tier * (g["height"] + 0.8), h=g["height"], group="gen-array",
                     catalogue_key=elec.genset_key,
                     props={"kva": g["kva"], "kwe": g["kwe"], "lw_dba": g["lw_dba"], "block": bi,
                            "group_no": bi // elec.topology.x, "tier": tier})
        els.append(el)
        ids.append(el.id)
    if g_rows > 1:
        notes.append(f"Gen yard needs {g_rows} rows of {g_per_row} sets ({tiers} tier).")
    arrays.append(ArrayDef(id="gen-array", kind="genset", count=n_gen, pitch=(g_pitch, 0),
                           rows=g_rows, row_pitch=(0, -g_row_d), member_ids=ids))
    y_south = y_gen_top - g_rows * g_row_d

    # ---- heat rejection: roof and/or ground yard (north)
    u = hr.unit
    kind = "chiller" if u["type"] == "air_cooled_chiller" else "dry_cooler"
    ul, uw = u["length"], u["width"]
    n_hr = hr.n_total
    on_roof = 0
    if hr_location == "roof":
        edge = H["roof_edge"]
        rl = Lh - 2 * edge
        rd = Dh + M - 2 * edge
        per_row = max(0, int((rl + u["side_clear"]) // (ul + u["side_clear"])))
        rows_max = max(0, int((rd + u["row_clear"]) // (uw + u["row_clear"])))
        cap = per_row * rows_max
        on_roof = min(n_hr, cap)
        if on_roof < n_hr:
            notes.append(f"Roof takes {on_roof} of {n_hr} heat rejection units; "
                         f"{n_hr - on_roof} go in a ground yard.")
        rows = math.ceil(on_roof / per_row) if per_row else 0
        span = rows * uw + (rows - 1) * u["row_clear"] if rows else 0
        y0 = E + C + edge + (rd - span) / 2
        ids = []
        for i in range(on_roof):
            r, k = divmod(i, per_row)
            n_in = min(per_row, on_roof - r * per_row)
            x = _spread(n_in, S + edge, rl, ul)[k]
            el = Element(id=f"HR{i + 1:02d}", kind=kind, name=f"HR{i + 1:02d}", x=x,
                         y=y0 + r * (uw + u["row_clear"]), w=ul, d=uw, z=hall_h, h=u["height"],
                         group="hr-roof", catalogue_key=hr.unit_key,
                         props={"lw_dba": u["lw_dba"], "capacity_kw": round(hr.cap_kw), "on_roof": True,
                                "operating_kg": u.get("operating_kg")})
            els.append(el)
            ids.append(el.id)
        arrays.append(ArrayDef(id="hr-roof", kind=kind, count=on_roof, pitch=(ul + u["side_clear"], 0),
                               rows=rows, row_pitch=(0, uw + u["row_clear"]), member_ids=ids))
    n_ground = n_hr - on_roof
    y_north = D
    if n_ground:
        per_row = max(1, int((Lh + u["side_clear"]) // (ul + u["side_clear"])))
        rows = math.ceil(n_ground / per_row)
        y0 = D + 3.0
        ids = []
        for j in range(n_ground):
            r, k = divmod(j, per_row)
            n_in = min(per_row, n_ground - r * per_row)
            x = _spread(n_in, S, Lh, ul)[k]
            i = on_roof + j
            el = Element(id=f"HR{i + 1:02d}", kind=kind, name=f"HR{i + 1:02d}", x=x,
                         y=y0 + r * (uw + u["row_clear"]), w=ul, d=uw, h=u["height"],
                         group="hr-ground", catalogue_key=hr.unit_key,
                         props={"lw_dba": u["lw_dba"], "capacity_kw": round(hr.cap_kw), "on_roof": False,
                                "operating_kg": u.get("operating_kg")})
            els.append(el)
            ids.append(el.id)
        arrays.append(ArrayDef(id="hr-ground", kind=kind, count=n_ground, pitch=(ul + u["side_clear"], 0),
                               rows=rows, row_pitch=(0, uw + u["row_clear"]), member_ids=ids))
        y_north = y0 + rows * uw + (rows - 1) * u["row_clear"] + 2.0

    anchors = {
        "entrance": (0.0, D / 2),
        "elec_centre": (S + band_len / 2, E / 2),
        "gen_centre": (S + band_len / 2, (y_gen_top + y_south) / 2),
        "building_centre": (L / 2, D / 2),
    }
    info = {"building_length": L, "building_depth": D, "halls_per_storey": hps, "hall_height": hall_h,
            "elec_levels": levels, "elec_band_length": band_len, "hr_on_roof": on_roof,
            "hr_on_ground": n_ground, "gen_rows": g_rows, "tx_rows": tx_rows}
    return Core(els, arrays, (0.0, y_south, L, y_north), anchors, info, notes)


def mirror_core(core: Core, mirror_x: bool, mirror_y: bool) -> Core:
    x0, y0, x1, y1 = core.bbox
    for e in core.elements:
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
