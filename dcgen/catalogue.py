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


def load_catalogue(path: str | Path | None = None) -> Catalogue:
    import copy
    return Catalogue(copy.deepcopy(_load(str(path or DEFAULT_PATH))))
