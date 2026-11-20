"""Data hall sizing from rack count and an aisle module.

Rack rows run across the building (local y). Pods = 2 rack rows + a contained
hot aisle, separated by cold aisles. Fan-wall / CDU galleries sit at both row
ends, i.e. against the corridor (south) and the mechanical gallery (north).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..schema import Brief


@dataclass
class HallDesign:
    it_kw: float
    racks_required: int
    racks_per_row: int
    rows: int
    pods: int
    racks_provided: int
    width_x: float                 # along the building
    depth_y: float                 # across the building
    gallery: float
    area_m2: float
    density_kw_m2: float
    pod_rects: list[tuple[float, float, float, float]] = field(default_factory=list)   # x0,y0,x1,y1 rel. hall
    aisle_rects: list[tuple[float, float, float, float]] = field(default_factory=list)


def design_hall(brief: Brief, cat: dict) -> HallDesign:
    h = cat["halls"]
    it_kw = brief.hall_it_mw * 1000
    racks = math.ceil(it_kw / brief.rack_kw)
    rpr = max(1, min(brief.max_racks_per_row, racks))
    rows = math.ceil(racks / rpr)
    rows += rows % 2                                   # pods need pairs of rows
    rpr = math.ceil(racks / rows)                      # rebalance row length
    pods = rows // 2
    gallery = h["gallery_air"] if brief.cooling == "air_chw" else h["gallery_liquid"]

    pod_w = 2 * brief.rack_d + h["hot_aisle"]
    width_x = 2 * h["perimeter"] + pods * pod_w + (pods + 1) * h["cold_aisle"]
    row_len = rpr * brief.rack_w
    depth_y = 2 * gallery + 2 * h["end_aisle"] + row_len

    pod_rects, aisle_rects = [], []
    y0 = gallery + h["end_aisle"]
    for p in range(pods):
        x0 = h["perimeter"] + h["cold_aisle"] + p * (pod_w + h["cold_aisle"])
        pod_rects.append((x0, y0, x0 + pod_w, y0 + row_len))
        aisle_rects.append((x0 + brief.rack_d, y0, x0 + brief.rack_d + h["hot_aisle"], y0 + row_len))

    rt = h.get("round_to", 0)
    if rt:
        width_x = math.ceil(width_x / rt - 1e-9) * rt      # planning module
        depth_y = math.ceil(depth_y / rt - 1e-9) * rt
    area = width_x * depth_y
    return HallDesign(
        it_kw=it_kw, racks_required=racks, racks_per_row=rpr, rows=rows, pods=pods,
        racks_provided=rows * rpr, width_x=round(width_x, 2), depth_y=round(depth_y, 2),
        gallery=gallery, area_m2=round(area, 1), density_kw_m2=round(it_kw / area, 2),
        pod_rects=pod_rects, aisle_rects=aisle_rects,
    )
