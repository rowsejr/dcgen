"""Loads the module catalogue (YAML). Engineers own the YAML; code only reads it."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).parent / "data" / "catalogue.yaml"


class Catalogue(dict):
    """Thin dict wrapper with helpers for selecting plant."""

    def pick_min(self, section: str, attr: str, required: float) -> tuple[str, dict, int]:
        """Smallest item in `section` with item[attr] >= required.
        If none is big enough, use the largest and return how many are needed."""
        items = sorted(self[section].items(), key=lambda kv: kv[1][attr])
        for key, item in items:
            if item[attr] >= required:
                return key, item, 1
        key, item = items[-1]
        n = int(-(-required // item[attr]))
        return key, item, n

    def pick_best(self, section: str, attr: str, required: float, forced: str | None = None,
                  gap_keys: tuple[str, str] = ("side_clear", "end_clear")) -> tuple[str, dict, int]:
        """Best item to cover `required` of `attr`: fewest units, then smallest footprint, then
        quietest, then key (deterministic). `forced` names one item to use regardless."""
        import math
        items = self[section]
        if forced:
            if forced not in items:
                raise KeyError(f"{forced} is not in catalogue section '{section}' for this project")
            it = items[forced]
            return forced, it, max(1, math.ceil(required / it[attr]))
        scored = []
        for key, it in items.items():
            if it.get(attr, 0) <= 0:
                continue
            n = max(1, math.ceil(required / it[attr]))
            gx = it.get(gap_keys[0], 2 * it.get("clear", 0.0))
            gy = it.get(gap_keys[1], 2 * it.get("clear", 0.0))
            fp = n * (it["width"] + gx) * (it["length"] + gy)
            scored.append(((n, round(fp, 3), it.get("lw_dba", 0.0), key), key, it, n))
        if not scored:
            raise ValueError(f"No usable items in catalogue section '{section}'")
        _, key, it, n = min(scored, key=lambda t: t[0])
        return key, it, n

    def substation_for(self, mva: float) -> dict:
        for s in self["site"]["substation"]:
            if mva <= s["max_mva"]:
                return s
        return self["site"]["substation"][-1]


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def load_catalogue(path: str | Path | None = None, overrides: dict | None = None) -> Catalogue:
    import copy
    cat = Catalogue(copy.deepcopy(_load(str(path or DEFAULT_PATH))))
    return with_overrides(cat, overrides) if overrides else cat


def with_overrides(cat: Catalogue, overrides: dict | None) -> Catalogue:
    """Project-level overrides of catalogue values, keyed by dotted path, e.g.
    {"halls.corridor_depth": 5.0, "egress.plant_room_exits": True}. The path must already exist
    (typos fail loudly). The catalogue file itself is never changed: overrides live in the brief."""
    import copy
    if not overrides:
        return cat
    out = Catalogue(copy.deepcopy(dict(cat)))
    for path, value in overrides.items():
        keys = path.split(".")
        node = out
        for k in keys[:-1]:
            if not isinstance(node, dict) or k not in node:
                raise KeyError(f"Override path not in catalogue: {path}")
            node = node[k]
        if not isinstance(node, dict) or keys[-1] not in node:
            raise KeyError(f"Override path not in catalogue: {path}")
        node[keys[-1]] = value
    return out


def for_brief(brief, cat: Catalogue | None = None) -> Catalogue:
    """The catalogue as this project sees it: base catalogue plus the brief's overrides."""
    from .library import effective_catalogue
    base = cat or load_catalogue()
    if "_library" in base:                                   # already resolved for a project
        return with_overrides(base, getattr(brief, "overrides", None))
    return with_overrides(effective_catalogue(brief, base), getattr(brief, "overrides", None))
