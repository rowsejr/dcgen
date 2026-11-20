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
    return with_overrides(cat or load_catalogue(), getattr(brief, "overrides", None))
