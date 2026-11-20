# -*- coding: utf-8 -*-
"""pyRevit button: place a dcgen scene (scene.json) as native Revit elements.

UNTESTED SCAFFOLD - written against the public Revit API, not run in Revit here.

* Plant (gensets, transformers, chillers, tanks...) -> FamilyInstances of the
  family types named in family_map.json (use your practice families).
* Volumes (halls, electrical rooms, support...) -> DirectShape massing on
  Generic Models (or swap to Mass / Rooms later).
* Every element gets the dcgen id in its Comments (or a shared parameter
  'DCGen_ID' if you add one) so re-running updates instead of duplicating, and
  'Export positions' can write moves back to dcgen as a patch.

Coordinates: scene local frame -> site coordinates via scene['frame'];
place relative to Survey Point / shared coordinates as your template requires.
"""
import json
import math
import os

from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, CurveLoop, DirectShape, ElementId,
                               FamilySymbol, FilteredElementCollector, GeometryCreationUtilities, Line,
                               Transaction, UnitTypeId, UnitUtils, XYZ)
from Autodesk.Revit.DB import CurveLoop as _CL, GeometryObject
from Autodesk.Revit.DB.Structure import StructuralType
from pyrevit import forms, revit
from System.Collections.Generic import List

doc = revit.doc
HERE = os.path.dirname(__file__)
M = lambda v: UnitUtils.ConvertToInternalUnits(v, UnitTypeId.Meters)   # metres -> feet

VOLUME_KINDS = {"data_hall", "elec_room", "sts_room", "mv_room", "support", "corridor", "mech_gallery",
                "substation", "pump_house", "guardhouse", "fuel_bund"}


def load_family_map():
    p = os.path.join(HERE, "family_map.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def find_symbol(family_name, type_name):
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if s.FamilyName == family_name and s.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString() == type_name:
            return s
    return None


def to_site(frame, x, y):
    t = math.radians(frame["rotation_deg"])
    ox, oy = frame["origin"]
    return ox + math.cos(t) * x - math.sin(t) * y, oy + math.sin(t) * x + math.cos(t) * y


def box_solid(frame, e):
    pts = [(e["x"], e["y"]), (e["x"] + e["w"], e["y"]), (e["x"] + e["w"], e["y"] + e["d"]), (e["x"], e["y"] + e["d"])]
    xyz = [XYZ(M(a), M(b), M(e["z"])) for a, b in (to_site(frame, *p) for p in pts)]
    loop = CurveLoop()
    for i in range(4):
        loop.Append(Line.CreateBound(xyz[i], xyz[(i + 1) % 4]))
    return GeometryCreationUtilities.CreateExtrusionGeometry(List[_CL]([loop]), XYZ.BasisZ, M(max(e["h"], 0.1)))


def main():
    path = forms.pick_file(file_ext="json", title="Pick dcgen scene.json")
    if not path:
        return
    with open(path) as f:
        scene = json.load(f)
    frame, fmap = scene["frame"], load_family_map()
    placed, skipped = 0, []
    with Transaction(doc, "dcgen: place test fit") as t:
        t.Start()
        for e in scene["elements"]:
            if e["kind"] in ("road", "parking", "landscape", "gate"):
                continue                        # site works: use toposolid sub-regions / floors later
            if e["kind"] in VOLUME_KINDS or e["kind"] not in fmap:
                ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
                ds.SetShape(List[GeometryObject]([box_solid(frame, e)]))
                ds.Name = e["id"]
                ds.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).Set("dcgen:" + e["id"])
                placed += 1
                continue
            fam, typ = fmap[e["kind"]]["family"], fmap[e["kind"]]["type"]
            sym = find_symbol(fam, typ)
            if sym is None:
                skipped.append(e["id"])
                continue
            if not sym.IsActive:
                sym.Activate()
            cx, cy = to_site(frame, e["x"] + e["w"] / 2, e["y"] + e["d"] / 2)
            inst = doc.Create.NewFamilyInstance(XYZ(M(cx), M(cy), M(e["z"])), sym, StructuralType.NonStructural)
            ang = math.radians(frame["rotation_deg"]) + (math.pi / 2 if e["d"] > e["w"] else 0)
            axis = Line.CreateBound(XYZ(M(cx), M(cy), 0), XYZ(M(cx), M(cy), 1))
            inst.Location.Rotate(axis, ang)
            inst.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).Set("dcgen:" + e["id"])
            placed += 1
        t.Commit()
    forms.alert("Placed {} elements. Missing family types for: {}".format(placed, ", ".join(skipped[:20]) or "none"))


main()
