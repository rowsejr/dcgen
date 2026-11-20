"""IFC4 export (IfcOpenShell). Link into Revit (Insert > Link IFC) or append in
Navisworks. Geometry is written in site orientation; if `site.crs` is set the
model is shifted near the origin and an IfcMapConversion carries the eastings/
northings (keeps Revit happy with OSGB-sized coordinates).

Every element carries Pset_DCGen (dcgen_id, kind, group, catalogue key, ratings)
so a Revit/Dynamo script can map elements to real families later.
"""
from __future__ import annotations

import math

import numpy as np

from ..render.style import BASE
from ..schema import Scene

IFC_MAP = {
    "data_hall": ("IfcSpace", "SPACE"), "elec_room": ("IfcSpace", "SPACE"), "sts_room": ("IfcSpace", "SPACE"),
    "mv_room": ("IfcSpace", "SPACE"), "support": ("IfcSpace", "SPACE"), "corridor": ("IfcSpace", "SPACE"),
    "mech_gallery": ("IfcSpace", "SPACE"),
    "genset": ("IfcElectricGenerator", "ENGINEGENERATOR"),
    "transformer": ("IfcTransformer", "VOLTAGE"),
    "chiller": ("IfcChiller", "AIRCOOLED"),
    "dry_cooler": ("IfcUnitaryEquipment", "USERDEFINED"),
    "sprinkler_tank": ("IfcTank", "STORAGE"), "fuel_tank": ("IfcTank", "STORAGE"),
    "road": ("IfcCivilElement", None), "parking": ("IfcCivilElement", None),
    "landscape": ("IfcGeographicElement", "TERRAIN"),
}
OBJECT_TYPE = {"dry_cooler": "DryCooler", "substation": "Substation compound", "pump_house": "Sprinkler pump house",
               "guardhouse": "Guardhouse", "fuel_bund": "Fuel bund", "gate": "Security gate"}


def export_ifc(scene: Scene, path: str, spaces_as_proxy: bool = False) -> str:
    import ifcopenshell
    import ifcopenshell.api.aggregate as agg
    import ifcopenshell.api.context as ctx
    import ifcopenshell.api.geometry as geo
    import ifcopenshell.api.project as prj
    import ifcopenshell.api.pset as pset
    import ifcopenshell.api.root as root
    import ifcopenshell.api.spatial as spatial
    import ifcopenshell.api.style as style
    import ifcopenshell.api.unit as unit
    from ifcopenshell.util.shape_builder import ShapeBuilder

    f = prj.create_file(version="IFC4")
    project = root.create_entity(f, ifc_class="IfcProject", name=scene.brief.project)
    unit.assign_unit(f, length={"is_metric": True, "raw": "METERS"},
                     area={"is_metric": True, "raw": "SQUARE_METRE"},
                     volume={"is_metric": True, "raw": "CUBIC_METRE"})
    model = ctx.add_context(f, context_type="Model")
    body = ctx.add_context(f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model)
    site = root.create_entity(f, ifc_class="IfcSite", name=scene.site.name)
    bldg = root.create_entity(f, ifc_class="IfcBuilding", name=f"{scene.brief.project} - option {scene.option}")
    storey = root.create_entity(f, ifc_class="IfcBuildingStorey", name="Ground")
    agg.assign_object(f, products=[site], relating_object=project)
    agg.assign_object(f, products=[bldg], relating_object=site)
    agg.assign_object(f, products=[storey], relating_object=bldg)
    for p in (site, bldg, storey):
        geo.edit_object_placement(f, product=p)

    shift = np.array(scene.frame.origin) if scene.site.crs else np.zeros(2)
    if scene.site.crs:
        try:
            import ifcopenshell.api.georeference as gr
            gr.add_georeferencing(f, name=scene.site.crs)
            gr.edit_georeferencing(f, coordinate_operation={"Eastings": float(shift[0]), "Northings": float(shift[1]),
                                                           "OrthogonalHeight": 0.0})
        except Exception as exc:   # georef API differs between IfcOpenShell versions
            print("IFC georeferencing skipped:", exc)

    sb = ShapeBuilder(f)
    t = math.radians(scene.frame.rotation_deg)
    c, s = math.cos(t), math.sin(t)
    styles = {}

    def style_for(kind):
        if kind not in styles:
            h = BASE.get(kind, "#cccccc").lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
            st = style.add_style(f, name=f"dcgen-{kind}")
            style.add_surface_style(f, style=st, ifc_class="IfcSurfaceStyleShading", attributes={
                "SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b},
                "Transparency": 0.35 if kind in ("data_hall", "elec_room", "support", "corridor", "mech_gallery") else 0.0})
            styles[kind] = st
        return styles[kind]

    for e in scene.elements:
        cls, ptype = IFC_MAP.get(e.kind, ("IfcBuildingElementProxy", None))
        if cls == "IfcSpace" and spaces_as_proxy:
            cls, ptype = "IfcBuildingElementProxy", None
        el = root.create_entity(f, ifc_class=cls, predefined_type=ptype, name=e.name or e.id)
        if e.kind in OBJECT_TYPE or ptype == "USERDEFINED":
            el.ObjectType = OBJECT_TYPE.get(e.kind, e.kind)
        if el.is_a("IfcElement"):
            el.Tag = e.id
        # placement: element min corner in site orientation
        X, Y = scene.frame.to_site(e.x, e.y)
        M = np.eye(4)
        M[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
        M[:3, 3] = [X - shift[0], Y - shift[1], e.z]
        geo.edit_object_placement(f, product=el, matrix=M)
        h = max(e.h, 0.05)
        if e.shape == "cyl":
            prof = sb.profile(sb.circle(center=(e.w / 2, e.w / 2), radius=e.w / 2))
        elif e.shape == "poly" and e.polygon:
            outer = sb.polyline([(px - e.x, py - e.y) for px, py in e.polygon], closed=True)
            inner = [sb.polyline([(px - e.x, py - e.y) for px, py in ring], closed=True) for ring in (e.holes or [])]
            prof = sb.profile(outer, inner_curves=inner)
        else:
            prof = sb.profile(sb.polyline([(0, 0), (e.w, 0), (e.w, e.d), (0, e.d)], closed=True))
        rep = sb.get_representation(body, [sb.extrude(prof, magnitude=h)])
        geo.assign_representation(f, product=el, representation=rep)
        style.assign_representation_styles(f, shape_representation=rep, styles=[style_for(e.kind)])
        if cls == "IfcSpace":
            agg.assign_object(f, products=[el], relating_object=storey)
        else:
            spatial.assign_container(f, products=[el], relating_structure=storey)
        props = {"dcgen_id": e.id, "kind": e.kind, "group": e.group or "", "catalogue_key": e.catalogue_key or "",
                 "option": scene.option}
        for k, v in e.props.items():
            if isinstance(v, (int, float, str, bool)) and k not in ("enclosed",):
                props[k] = v
        ps = pset.add_pset(f, product=el, name="Pset_DCGen")
        pset.edit_pset(f, pset=ps, properties=props)
    f.write(path)
    return path
