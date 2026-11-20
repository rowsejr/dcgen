"""Electrical redundancy topologies.

  xMy  distributed redundant ("x make y", e.g. 4M3, 5M4): x blocks per group,
       any y can carry the group's IT load. Racks are dual-corded and the load
       is split across every pair of blocks, so each block runs at y/x in
       normal operation (75% for 4M3, 80% for 5M4) and 100% after one failure.
  N+1 catcher (block / isolated redundant): k primary blocks each carry their
       own load at up to 100%; one catcher block picks up any failed primary via
       static transfer switches (STS). Same block count as (k+1)M(k) but bigger
       groups are practical and STS rooms are needed.
  2N   A and B systems, each rated for the full load (50% normal loading).
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class Topology:
    name: str
    x: int            # blocks per group
    y: int            # blocks needed to carry the group load
    kind: str         # "DR" | "catcher" | "2N"

    @property
    def normal_loading(self) -> float:
        if self.kind == "DR":
            return self.y / self.x
        if self.kind == "2N":
            return 0.5
        return 1.0    # catcher primaries (catcher itself idles)

    def load_pairs(self) -> list[tuple[int, int]]:
        """Rack-group feed pairs within one group (for the topology diagram)."""
        if self.kind == "DR":
            return list(itertools.combinations(range(self.x), 2))
        if self.kind == "2N":
            return [(0, 1)]
        return [(i, self.x - 1) for i in range(self.y)]   # primary i + catcher via STS


def parse_topology(text: str, catcher_group: int = 4) -> Topology:
    s = text.strip().upper().replace(" ", "")
    m = re.fullmatch(r"(\d+)M(\d+)", s)
    if m:
        x, y = int(m[1]), int(m[2])
        if not x > y >= 1:
            raise ValueError(f"{text}: need x > y >= 1")
        return Topology(f"{x}M{y}", x, y, "DR")
    if "CATCHER" in s or s in {"BLOCKREDUNDANT", "ISOLATEDREDUNDANT", "N+1C"}:
        k = int(catcher_group)
        return Topology(f"{k}+1 catcher", k + 1, k, "catcher")
    if s == "2N":
        return Topology("2N", 2, 1, "2N")
    raise ValueError(f"Unknown topology '{text}'. Use e.g. 4M3, 5M4, 'N+1 catcher', 2N")


@dataclass
class BlockPlan:
    topology: Topology
    load_unit_kw: float          # IT load served by one redundancy group set (halls_per_group halls)
    groups_per_unit: int
    n_units: int
    n_groups: int
    block_kw: float              # UPS (IT) rating per block
    n_blocks: int
    n_catchers: int
    installed_kw: float
    normal_loading: float
    groups: list[list[int]] = field(default_factory=list)

    def summary(self) -> dict:
        d = asdict(self)
        d["topology"] = self.topology.name
        d.pop("groups")
        return d


def plan_blocks(top: Topology, hall_it_kw: float, n_halls: int,
                halls_per_group: int = 1, max_block_kw: float = 3000.0) -> BlockPlan:
    load_unit = hall_it_kw * halls_per_group
    n_units = math.ceil(n_halls / halls_per_group)
    if top.kind == "2N":
        groups_per_unit = max(1, math.ceil(load_unit / max_block_kw))
        block_kw = load_unit / groups_per_unit
    else:
        groups_per_unit = max(1, math.ceil(load_unit / (top.y * max_block_kw)))
        block_kw = load_unit / groups_per_unit / top.y
    n_groups = n_units * groups_per_unit
    n_blocks = n_groups * top.x
    groups = [list(range(g * top.x, (g + 1) * top.x)) for g in range(n_groups)]
    return BlockPlan(
        topology=top, load_unit_kw=load_unit, groups_per_unit=groups_per_unit,
        n_units=n_units, n_groups=n_groups, block_kw=block_kw, n_blocks=n_blocks,
        n_catchers=n_groups if top.kind == "catcher" else 0,
        installed_kw=n_blocks * block_kw, normal_loading=top.normal_loading, groups=groups,
    )
