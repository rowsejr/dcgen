"""Inputs: brief/site YAML, and redlines from GeoJSON, KML (Google Earth) or DXF."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import yaml

from .schema import Brief, SiteInput


def load_yaml(path: str | Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_brief(path: str | Path) -> Brief:
    return Brief(**load_yaml(path))


def load_site(path: str | Path) -> SiteInput:
    d = load_yaml(path)
    if "redline_file" in d:
        d["redline"] = read_redline(Path(path).parent / d.pop("redline_file"))
    return SiteInput(**d)


def _lonlat_to_local(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Equirectangular projection about the centroid - fine for a test fit (<0.1% over 1 km).
    For OSGB use pyproj: Transformer.from_crs(4326, 27700, always_xy=True)."""
    lon0 = sum(c[0] for c in coords) / len(coords)
    lat0 = sum(c[1] for c in coords) / len(coords)
    k = 111_320.0
    return [((lon - lon0) * k * math.cos(math.radians(lat0)), (lat - lat0) * 110_540.0) for lon, lat in coords]


def _looks_lonlat(coords) -> bool:
    return all(abs(x) <= 180 and abs(y) <= 90 for x, y in coords)


def read_redline(path: str | Path) -> list[tuple[float, float]]:
    path = Path(path)
    suf = path.suffix.lower()
    if suf in (".geojson", ".json"):
        g = json.loads(path.read_text())
        feat = g["features"][0]["geometry"] if g.get("type") == "FeatureCollection" else g.get("geometry", g)
        ring = feat["coordinates"][0] if feat["type"] == "Polygon" else feat["coordinates"][0][0]
        coords = [tuple(c[:2]) for c in ring]
    elif suf == ".kml":
        txt = path.read_text()
        m = re.search(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>", txt, re.S)
        if not m:
            m = re.search(r"<coordinates>(.*?)</coordinates>", txt, re.S)
        coords = [tuple(map(float, t.split(",")[:2])) for t in m.group(1).split()]
    elif suf == ".dxf":
        import ezdxf
        doc = ezdxf.readfile(str(path))
        polys = [e for e in doc.modelspace().query("LWPOLYLINE") if e.closed]
        pref = [p for p in polys if re.search(r"red.?line|boundary|site", p.dxf.layer, re.I)]
        pick = max(pref or polys, key=lambda p: abs(_area([tuple(v[:2]) for v in p.get_points()])))
        coords = [tuple(v[:2]) for v in pick.get_points()]
    else:
        raise ValueError(f"Unsupported redline format: {suf}")
    if coords[0] == coords[-1]:
        coords = coords[:-1]
    return _lonlat_to_local(coords) if _looks_lonlat(coords) else coords


def _area(c):
    return 0.5 * sum(c[i][0] * c[(i + 1) % len(c)][1] - c[(i + 1) % len(c)][0] * c[i][1] for i in range(len(c)))
