"""glTF/GLB (web viewers, Blender, PowerPoint 3D) and DXF (AutoCAD / Revit CAD link)."""
from __future__ import annotations

import logging
import math

import numpy as np

logging.getLogger("ezdxf").setLevel(logging.WARNING)

from ..render.style import BASE
from ..schema import Scene


def _site_xy(scene: Scene, pts, shift):
    return np.array([np.array(scene.frame.to_site(x, y)) - shift for x, y in pts])


def export_glb(scene: Scene, path: str) -> str:
    """Y-up GLB centred on the redline centroid, site orientation."""
    import trimesh
    from shapely.geometry import Polygon

    shift = np.array(scene.frame.origin)
    meshes = trimesh.Scene()
    for e in scene.elements:
        h = max(e.h, 0.05)
        fp = e.footprint()
        poly = Polygon(_site_xy(scene, fp.exterior.coords, shift),
                       [_site_xy(scene, r.coords, shift) for r in fp.interiors])
        m = trimesh.creation.extrude_polygon(poly, h)
        m.apply_translation([0, 0, e.z])
        col = BASE.get(e.kind, "#cccccc").lstrip("#")
        m.visual.face_colors = [int(col[i:i + 2], 16) for i in (0, 2, 4)] + [255]
        meshes.add_geometry(m, node_name=e.id, geom_name=e.id)
    meshes.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    meshes.export(path)
    return path


LAYERS = {
    "data_hall": ("DC-HALL", 5), "support": ("DC-BLDG", 8), "corridor": ("DC-BLDG", 8), "mech_gallery": ("DC-MECH", 4),
    "fire_wall": ("DC-BLDG", 8), "stair": ("DC-BLDG", 8), "exit": ("DC-FIRE", 3),
    "elec_room": ("DC-ELEC", 30), "sts_room": ("DC-ELEC", 30), "mv_room": ("DC-ELEC", 30),
    "transformer": ("DC-ELEC-TX", 30), "genset": ("DC-ELEC-GEN", 30), "substation": ("DC-ELEC-SUB", 30),
    "chiller": ("DC-MECH-HR", 4), "dry_cooler": ("DC-MECH-HR", 4),
    "sprinkler_tank": ("DC-FIRE", 1), "pump_house": ("DC-FIRE", 1), "fuel_bund": ("DC-FUEL", 6), "fuel_tank": ("DC-FUEL", 6),
    "guardhouse": ("DC-SEC", 8), "gate": ("DC-SEC", 8), "road": ("DC-ROAD", 9), "parking": ("DC-ROAD", 9),
    "landscape": ("DC-LAND", 3),
}


def export_dxf(scene: Scene, path: str) -> str:
    """2D plan in site coordinates; closed polylines carry thickness = height (extrudes in 3D views)."""
    import ezdxf

    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6   # metres
    msp = doc.modelspace()
    for name, colour in set(LAYERS.values()) | {("DC-REDLINE", 1), ("DC-SETBACK", 8), ("DC-TEXT", 7)}:
        if name not in doc.layers:
            doc.layers.add(name, color=colour)
    zero = np.zeros(2)
    rl = _site_xy(scene, scene.redline_local, zero)
    msp.add_lwpolyline(rl.tolist(), close=True, dxfattribs={"layer": "DC-REDLINE", "lineweight": 50})
    msp.add_lwpolyline(_site_xy(scene, scene.buildable_local, zero).tolist(), close=True,
                       dxfattribs={"layer": "DC-SETBACK"})
    for e in scene.elements:
        layer = LAYERS.get(e.kind, ("DC-MISC", 7))[0]
        fp = e.footprint()
        if e.shape == "cyl":
            cx, cy = scene.frame.to_site(e.cx, e.cy)
            msp.add_circle((cx, cy), e.w / 2, dxfattribs={"layer": layer, "thickness": e.h, "elevation": e.z})
        else:
            for ring in [fp.exterior, *fp.interiors]:
                pts = _site_xy(scene, list(ring.coords)[:-1], zero).tolist()
                msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer, "thickness": e.h, "elevation": e.z})
        if e.kind in ("data_hall", "substation", "guardhouse", "pump_house") and e.z < 0.01:
            cx, cy = scene.frame.to_site(e.cx, e.cy)
            msp.add_text(e.name, height=2.5, dxfattribs={"layer": "DC-TEXT"}).set_placement(
                (cx, cy), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(path)
    return path
