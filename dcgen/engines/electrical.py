"""Electrical plant from a redundancy topology: blocks -> rooms, transformers,
gensets, fuel, site intake. Pure functions; geometry is placed in layout.py."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from ..catalogue import Catalogue
from ..schema import Brief
from .topology import BlockPlan, Topology, parse_topology, plan_blocks


@dataclass
class ElecDesign:
    topology: Topology
    plan: BlockPlan
    facility_kw_per_block: float
    genset_key: str
    genset: dict
    gensets_per_block: int
    n_gensets: int
    tx_key: str
    transformer: dict
    tx_per_block: int
    n_transformers: int
    room_width: float              # per block, along the facade
    n_rooms: int
    n_sts_rooms: int
    site_demand_mva: float
    substation: dict
    fuel_m3: float
    fuel_tanks: int
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "topology": self.topology.name,
            "topology_kind": self.topology.kind,
            "blocks": self.plan.n_blocks,
            "groups": self.plan.n_groups,
            "catcher_blocks": self.plan.n_catchers,
            "block_ups_kw": round(self.plan.block_kw),
            "installed_ups_mw": round(self.plan.installed_kw / 1000, 2),
            "normal_loading_pct": round(self.plan.normal_loading * 100),
            "facility_kw_per_block": round(self.facility_kw_per_block),
            "genset": self.genset["label"],
            "gensets": self.n_gensets,
            "transformer": self.transformer["label"],
            "transformers": self.n_transformers,
            "elec_rooms": self.n_rooms,
            "sts_rooms": self.n_sts_rooms,
            "site_demand_mva": round(self.site_demand_mva, 1),
            "substation": self.substation["voltage"],
            "fuel_m3": round(self.fuel_m3),
            "fuel_tanks": self.fuel_tanks,
        }


def design_electrical(brief: Brief, cat: Catalogue, n_halls: int) -> ElecDesign:
    notes: list[str] = []
    top = parse_topology(brief.topology, brief.catcher_group)
    plan = plan_blocks(top, brief.hall_it_mw * 1000, n_halls, brief.halls_per_group, brief.max_block_kw)

    # Each block's genset/transformer carries its IT block + its share of mechanical load
    facility_kw = plan.block_kw * (brief.design_pue if brief.mech_on_blocks else 1.0)
    gkey, gen, gpb = cat.pick_min("gensets", "kwe", facility_kw)
    if gpb > 1:
        notes.append(f"Block facility load {facility_kw:.0f} kW exceeds largest genset; {gpb} sets per block.")
    tkey, tx, tpb = cat.pick_min("transformers", "kva", facility_kw / 0.95)

    er = cat["electrical_rooms"]
    room_w = er["width_base"] + er["width_per_mw"] * plan.block_kw / 1000
    n_rooms = math.ceil(plan.n_blocks / max(1, brief.blocks_per_elec_room))

    site_kw = brief.it_load_mw * 1000 * brief.design_pue
    mva = site_kw / 0.95 / 1000
    sub = cat.substation_for(mva)

    f = cat["site"]["fuel"]
    fuel_m3 = site_kw * gen["fuel_l_per_kwh"] * brief.fuel_autonomy_h / 1000
    tanks = max(1, math.ceil(fuel_m3 / f["tank_m3"]))
    notes.append(
        f"{top.name}: {plan.n_blocks} blocks x {plan.block_kw:.0f} kW UPS "
        f"({plan.n_groups} groups, normal loading {plan.normal_loading:.0%}).")
    if top.kind == "catcher":
        notes.append(f"Catcher: {plan.n_catchers} catcher blocks + STS room per group.")

    return ElecDesign(
        topology=top, plan=plan, facility_kw_per_block=facility_kw,
        genset_key=gkey, genset=gen, gensets_per_block=gpb, n_gensets=gpb * plan.n_blocks,
        tx_key=tkey, transformer=tx, tx_per_block=tpb, n_transformers=tpb * plan.n_blocks,
        room_width=room_w, n_rooms=n_rooms, n_sts_rooms=plan.n_catchers,
        site_demand_mva=mva, substation=sub, fuel_m3=fuel_m3, fuel_tanks=tanks, notes=notes,
    )
