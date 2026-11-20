"""Electrical plant from a redundancy topology: blocks -> lineups, rooms, transformers, gensets,
mechanical lineups, fuel, site intake. Pure functions; geometry is placed in core.py.

An IT lineup is one block: UPS (<= electrical.ups_kva_per_lineup), transformer and one genset no larger
than electrical.genset_max_kva (single-source framework limit). Mechanical plant either shares the IT
lineups (each takes up to electrical.mech_kw_per_lineup) or gets its own mechanical lineups.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..catalogue import Catalogue
from ..schema import Brief
from .curves import interp
from .noise import lw_from_lp1m
from .topology import BlockPlan, Topology, parse_topology, plan_blocks


def max_block_kw(brief: Brief, cat: Catalogue) -> float:
    E = cat.get("electrical", {})
    cap = E.get("ups_kva_per_lineup", 1e9) * E.get("ups_pf", 1.0)
    return min(brief.max_block_kw, cap)


def block_plan(brief: Brief, cat: Catalogue, n_halls: int) -> tuple[Topology, BlockPlan]:
    top = parse_topology(brief.topology, brief.catcher_group)
    return top, plan_blocks(top, brief.hall_it_mw * 1000, n_halls, brief.halls_per_group, max_block_kw(brief, cat))


@dataclass
class ElecDesign:
    topology: Topology
    plan: BlockPlan
    facility_kw_per_block: float     # IT lineup demand: UPS input + mechanical share (kW)
    genset_key: str
    genset: dict
    gensets_per_block: int
    n_gensets: int                   # all gensets (IT + mechanical lineups)
    tx_key: str
    transformer: dict
    tx_per_block: int
    n_transformers: int              # all transformers (IT + mechanical lineups)
    room_width: float                # per block, along the facade
    n_rooms: int
    n_sts_rooms: int
    site_demand_mva: float
    substation: dict
    fuel_m3: float
    fuel_tanks: int
    notes: list[str] = field(default_factory=list)
    # lineups
    mech_mode: str = "shared"
    mech_per_lineup_kw: float = 0.0
    mech_allowance_kw: float = 0.0
    lineup_need_kva: float = 0.0
    genset_max_kva: float = 0.0
    single_source_ok: bool = True
    n_mech_lineups: int = 0
    n_mech_duty: int = 0
    mech_genset_key: str = ""
    mech_genset: dict = field(default_factory=dict)
    mech_tx_key: str = ""
    mech_transformer: dict = field(default_factory=dict)
    mech_sizing_kw: float = 0.0
    facility_kw: float = 0.0

    @property
    def n_it_gensets(self) -> int:
        return self.gensets_per_block * self.plan.n_blocks

    @property
    def n_it_tx(self) -> int:
        return self.tx_per_block * self.plan.n_blocks

    def summary(self) -> dict:
        s = {
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
            "mech_lineups": self.mech_mode,
            "mech_per_it_lineup_kw": round(self.mech_per_lineup_kw),
            "mech_allowance_kw": round(self.mech_allowance_kw),
            "it_lineup_kva": round(self.lineup_need_kva),
            "genset_max_kva": round(self.genset_max_kva),
            "single_source": self.single_source_ok,
        }
        if self.n_mech_lineups:
            s.update({"mech_lineup_count": self.n_mech_lineups, "mech_lineup_duty": self.n_mech_duty,
                      "mech_genset": self.mech_genset["label"], "mech_transformer": self.mech_transformer["label"]})
        return s

    def set_demand(self, facility_kw: float, cat: Catalogue, brief: Brief) -> None:
        """Intake and fuel from the peak facility load (all of it runs on the gensets in a mains failure)."""
        self.facility_kw = facility_kw
        self.site_demand_mva = facility_kw / 0.95 / 1000
        self.substation = cat.substation_for(self.site_demand_mva)
        f = cat["site"]["fuel"]
        self.fuel_m3 = facility_kw * self.genset["fuel_l_per_kwh"] * brief.fuel_autonomy_h / 1000
        self.fuel_tanks = max(1, math.ceil(self.fuel_m3 / f["tank_m3"]))


def _limited(cat: Catalogue, max_kva: float) -> Catalogue:
    sets = {k: v for k, v in cat["gensets"].items() if v["kva"] <= max_kva + 1e-6}
    return Catalogue({**cat, "gensets": sets or cat["gensets"]})


def _noise(gen: dict, brief: Brief) -> dict:
    if brief.genset_lp_1m_dba is None:
        return gen
    g = dict(gen)
    g["lw_dba"] = lw_from_lp1m(brief.genset_lp_1m_dba, g["length"], g["width"], g["height"])
    g["lp_1m_dba"] = brief.genset_lp_1m_dba
    return g


def _standby(n: int, rule: str) -> int:
    r = str(rule).replace(" ", "").upper()
    if r.startswith("N+") and r.endswith("%"):
        return math.ceil(n * float(r[2:-1]) / 100)
    return int(r[2:]) if r.startswith("N+") else 0


def design_electrical(brief: Brief, cat: Catalogue, n_halls: int, mech: dict | None = None) -> ElecDesign:
    """mech: {"mode": "shared"|"separate", "per_lineup_kw": worst IT lineup mech kW (shared),
    "sizing_kw": design mechanical load, duty units at full capacity}. None = shared at the full allowance."""
    notes: list[str] = []
    E = cat.get("electrical", {})
    allowance = E.get("mech_kw_per_lineup", 600.0)
    max_kva = E.get("genset_max_kva", 1e9)
    ups_eff, mech_pf = E.get("ups_eff_full", 0.97), E.get("mech_pf", 0.9)
    mech = mech or {"mode": "shared", "per_lineup_kw": allowance, "sizing_kw": 0.0}
    top, plan = block_plan(brief, cat, n_halls)
    free = plan_blocks(top, brief.hall_it_mw * 1000, n_halls, brief.halls_per_group, brief.max_block_kw)
    if free.n_blocks != plan.n_blocks:
        notes.append(f"Blocks limited to {max_block_kw(brief, cat):.0f} kW by the {E.get('ups_kva_per_lineup', 0):.0f} kVA "
                     f"UPS per lineup ({plan.n_blocks} blocks instead of {free.n_blocks}).")

    shared = mech["mode"] == "shared"
    per_lineup = mech["per_lineup_kw"] if shared else 0.0
    facility_kw = plan.block_kw / ups_eff + per_lineup
    need_kva = plan.block_kw / ups_eff + per_lineup / mech_pf
    gcat = _limited(cat, max_kva)
    gkey, gen, gpb = gcat.pick_best("gensets", "kva", need_kva, brief.genset_unit)
    gen = _noise(gen, brief)
    single = gpb == 1 and gen["kva"] <= max_kva + 1e-6
    if not single:
        notes.append(f"IT lineup needs {need_kva:.0f} kVA: more than one {max_kva:.0f} kVA genset per block "
                     "(not single source).")
    tkey, tx, tpb = cat.pick_best("transformers", "kva", need_kva, brief.tx_unit, gap_keys=("clear", "clear"))

    n_mech = n_duty = 0
    mgkey, mgen, mtkey, mtx = "", {}, "", {}
    if not shared and mech.get("sizing_kw", 0) > 0:
        lineup_kw = E.get("mech_lineup_kw", 2300.0)
        n_duty = max(1, math.ceil(mech["sizing_kw"] / lineup_kw - 1e-9))
        n_mech = n_duty + _standby(n_duty, E.get("mech_lineup_redundancy", "N+1"))
        each_kva = mech["sizing_kw"] / n_duty / mech_pf
        mgkey, mgen, mg = gcat.pick_best("gensets", "kva", each_kva)
        mgen = _noise(mgen, brief)
        mtkey, mtx, _ = cat.pick_best("transformers", "kva", each_kva, gap_keys=("clear", "clear"))
        if mg > 1:
            notes.append(f"Mechanical lineup needs {each_kva:.0f} kVA: more than one genset per lineup.")
        notes.append(f"Separate mechanical lineups: {n_mech} ({n_duty} duty, {E.get('mech_lineup_redundancy', 'N+1')}) "
                     f"x {mgen['label']} + {mtx['label']} for {mech['sizing_kw'] / 1000:.1f} MW design mechanical load.")
    elif shared:
        notes.append(f"Mechanical plant on the IT lineups: up to {per_lineup:.0f} kW per lineup "
                     f"(allowance {allowance:.0f} kW); lineup demand {need_kva:.0f} kVA on a {gen['label']}.")

    er = cat["electrical_rooms"]
    table = er.get("table")
    if table:      # reference-design typicals: room width per block rating, interpolated
        room_w = interp([t["kw"] for t in table], [t["width"] for t in table], plan.block_kw)
        notes.append(f"Electrical room width {room_w:.1f} m per {plan.block_kw:.0f} kW block from reference typicals.")
    else:
        room_w = er["width_base"] + er["width_per_mw"] * plan.block_kw / 1000
    n_rooms = math.ceil(plan.n_blocks / max(1, brief.blocks_per_elec_room))

    notes.append(
        f"{top.name}: {plan.n_blocks} blocks x {plan.block_kw:.0f} kW UPS "
        f"({plan.n_groups} groups, normal loading {plan.normal_loading:.0%}).")
    if top.kind == "catcher":
        notes.append(f"Catcher: {plan.n_catchers} catcher blocks + STS room per group.")

    provisional = brief.it_load_mw * 1000 * (brief.design_pue or 1.3)
    d = ElecDesign(
        topology=top, plan=plan, facility_kw_per_block=facility_kw,
        genset_key=gkey, genset=gen, gensets_per_block=gpb, n_gensets=gpb * plan.n_blocks + n_mech,
        tx_key=tkey, transformer=tx, tx_per_block=tpb, n_transformers=tpb * plan.n_blocks + n_mech,
        room_width=room_w, n_rooms=n_rooms, n_sts_rooms=plan.n_catchers,
        site_demand_mva=0.0, substation={}, fuel_m3=0.0, fuel_tanks=1, notes=notes,
        mech_mode=mech["mode"], mech_per_lineup_kw=per_lineup, mech_allowance_kw=allowance, lineup_need_kva=need_kva,
        genset_max_kva=max_kva, single_source_ok=single, n_mech_lineups=n_mech, n_mech_duty=n_duty,
        mech_genset_key=mgkey, mech_genset=mgen, mech_tx_key=mtkey, mech_transformer=mtx,
        mech_sizing_kw=mech.get("sizing_kw", 0.0),
    )
    d.set_demand(provisional, cat, brief)
    return d
