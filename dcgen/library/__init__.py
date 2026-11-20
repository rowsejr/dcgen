"""Plant library, client profiles, reference designs.

The engines never import this package: they receive a catalogue dict. `effective_catalogue` builds
that dict for a project: base catalogue.yaml -> library records (approved only unless the brief opts
into drafts, filtered by the client profile) -> profile overrides -> brief overrides.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

from . import profiles, reference
from .store import Library, pack_roots

_CACHE: dict = {}


def _signature(roots: list[Path]) -> tuple:
    sig = []
    for r in roots:
        files = [p for p in r.rglob("*") if p.suffix in (".csv", ".yaml")] if r.exists() else []
        sig.append((str(r), len(files), max((p.stat().st_mtime_ns for p in files), default=0)))
    return tuple(sig)


def active(roots=None) -> Library:
    """The library for DCGEN_PACKS (or `roots`), cached until a file in the packs changes."""
    rs = pack_roots(roots)
    key = _signature(rs)
    if key not in _CACHE:
        _CACHE.clear()
        lib = Library(rs)
        lib.profiles = profiles.load_profiles(rs)          # type: ignore[attr-defined]
        lib.cards = reference.load_cards(rs)               # type: ignore[attr-defined]
        _CACHE[key] = lib
    return _CACHE[key]


def effective_catalogue(brief, base, roots=None):
    """Catalogue as one project sees it. Plant sections that the library fills REPLACE the
    placeholder entries (a real library should not compete with indicative placeholders)."""
    lib = active(roots)
    profile = None
    if brief.profile:
        profile = lib.profiles.get(brief.profile)           # type: ignore[attr-defined]
        if profile is None:
            raise KeyError(f"Client profile '{brief.profile}' not found in packs {[str(r) for r in lib.roots]}")
    if not len(lib) and profile is None:
        return base
    from ..catalogue import Catalogue, with_overrides
    cat = Catalogue(copy.deepcopy(dict(base)))
    meta = {"packs": [str(r) for r in lib.roots], "replaced": [], "drafts_allowed": bool(brief.use_drafts),
            "profile": ({"id": profile["id"], "revision": profile.get("revision", "")} if profile else None)}
    sec = lib.catalogue_sections(include_drafts=brief.use_drafts, **profiles.catalogue_filters(profile))
    for name in ("heat_rejection", "gensets", "transformers"):
        if sec[name]:
            cat[name] = sec[name]
            meta["replaced"].append(name)
    if sec["elec_table"]:
        cat["electrical_rooms"]["table"] = sec["elec_table"]
        meta["replaced"].append("electrical_rooms.table")
    if profile and profile.get("catalogue_overrides"):
        cat = with_overrides(cat, profile["catalogue_overrides"])
    cat["_library"] = meta
    cat["_profile"] = profile
    cat["_cards"] = lib.cards                                # type: ignore[attr-defined]
    return cat
