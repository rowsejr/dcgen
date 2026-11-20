"""Scene schema: the single source of truth.

Every part of the system reads and writes this one document:

    brief + site  ->  engines  ->  Scene  ->  checks / exporters / editor / MCP tools
                                    ^  |
                                    |  v
                           drag edits & agent edits (patches)

Coordinates are metres in the *local layout frame* (x runs along the
building's long axis, z up). `Frame` maps local -> site coordinates, i.e. the
coordinate system the redline was supplied in (OSGB36 / project grid / local).
Exporters (IFC, DXF, glTF) write site coordinates so models land on the survey.
"""
from __future__ import annotations

import json
import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from shapely.geometry import Polygon, Point, box

XY = tuple[float, float]


# --------------------------------------------------------------------------- inputs
class Receptor(BaseModel):
    """Noise-sensitive receptor (NSR), in site coordinates."""
    name: str
    x: float
    y: float
    z: float = 1.5
    limit_dba: float = Field(45.0, description="Rating level limit at the receptor, dB(A)")


class SiteInput(BaseModel):
    name: str = "Site"
    redline: list[XY] = Field(..., description="Planning redline boundary, site coords (m)")
    access_points: list[XY] = Field(default_factory=list,
                                    description="Vehicle access points on the boundary; first = main gate")
    utility_point: Optional[XY] = Field(None, description="Grid connection direction (substation preference)")
    true_north_deg: float = Field(0.0, description="Bearing of site +y from true north, degrees clockwise")
    boundary_setback_m: float = 10.0
    receptors: list[Receptor] = Field(default_factory=list)
    boundary_noise_limit_dba: Optional[float] = None
    building_orientation_deg: Optional[float] = Field(
        None, description="Force the building long axis bearing (site coords, deg CCW from +x)")
    crs: Optional[str] = Field(None, description="e.g. EPSG:27700. Informational; used for IFC georef")


class Brief(BaseModel):
    """Client requirements + design parameters. Every default is a placeholder."""
    project: str = "Data centre test fit"
    client: str = ""
    stage: str = "RIBA Stage 2"
    # --- IT / white space
    it_load_mw: float = 24.0
    hall_it_mw: float = 6.0
    storeys: int = 1
    rack_kw: float = 20.0
    rack_w: float = 0.6
    rack_d: float = 1.2
    max_racks_per_row: int = 30
    cooling: Literal["air_chw", "liquid_dtc", "hybrid"] = "air_chw"
    # --- electrical
    topology: str = Field("4M3", description="xMy distributed redundant (4M3, 5M4...), 'N+1 catcher', '2N'")
    catcher_group: int = Field(4, description="Primary blocks per catcher block")
    halls_per_group: int = Field(1, description="Halls served by one redundancy group")
    max_block_kw: float = Field(3000, description="Largest UPS block (IT kW) allowed")
    blocks_per_elec_room: int = 1
    elec_room_levels: Optional[int] = Field(None, description="None = auto (1-3)")
    genset_tiers: int = 1
    design_pue: Optional[float] = Field(None, description=(
        "Override the computed peak PUE for intake and fuel sizing (None = computed, CUN-CAL-PUE-001 method)"))
    pue_target_peak: Optional[float] = Field(None, description="Client peak PUE target, checked if given")
    mech_lineups: Literal["auto", "shared", "separate"] = Field("auto", description=(
        "Mechanical load on the IT lineups (shared, mech_kw_per_lineup each), on dedicated mechanical "
        "lineups (separate), or shared when it fits (auto)"))
    mech_on_blocks: bool = Field(True, description="Legacy: False is the same as mech_lineups='separate'")
    genset_lp_1m_dba: Optional[float] = Field(None, description="Genset noise at 1 m all round (70 / 75 / 85 dB(A)); None = record value")
    fuel_autonomy_h: float = 24.0
    # --- heat rejection
    heat_rejection: Literal["auto", "air_cooled_chiller", "dry_cooler", "adiabatic_dry_cooler"] = "auto"
    hr_location: Literal["auto", "roof", "ground"] = "auto"
    hr_redundancy: str = Field("N+1", description="'N+1', 'N+2' or 'N+20%'")
    hr_uplift_k: float = Field(5.0, description=(
        "Air-on uplift over design ambient for heat rejection in arrays (recirculation), K. "
        "Applied to capacity and EER. CUN-CAL-PUE-001 used 5 K (DC01), 2 K (project B)"))
    chw_flow_c: float = Field(20.0, description="CHW flow temperature, degC (20-26 typical); sets chiller EER")
    ambient_design_c: float = Field(35.0, description="Design dry bulb, e.g. N=20 extreme + climate uplift")
    ambient_design_wb_c: float = Field(21.0, description="Coincident wet bulb (adiabatic units)")
    fwr_c: float = Field(30.0, description="Facility water return temperature to heat rejection, degC")
    # --- noise
    roof_screen_db: float = Field(5.0, description="Attenuation from roof parapet/acoustic screen")
    yard_barrier_db: float = Field(0.0, description="Attenuation from a barrier round the ground HR yard")
    building_screen_db: float = Field(10.0, description="Extra attenuation when the building blocks the path")
    # --- site
    staff_parking: int = 60
    # --- plant selection and client profile
    profile: str = Field("", description="Client profile id (library pack); sets approved plant, defaults, rules")
    use_drafts: bool = Field(False, description="Allow draft/reviewed library records (exploration only; flagged on outputs)")
    hr_prefer: Literal["balanced", "footprint", "noise", "count"] = "balanced"
    hr_unit: Optional[str] = Field(None, description="Force a heat rejection unit (catalogue key / record id)")
    genset_unit: Optional[str] = None
    tx_unit: Optional[str] = None
    # --- project overrides of catalogue values (client requirements), dotted paths
    overrides: dict[str, Any] = Field(default_factory=dict, description=(
        "Catalogue overrides for this project, e.g. {'halls.corridor_depth': 5.0, "
        "'halls.cross_corridor_every': 1, 'egress.plant_room_exits': true}"))


# --------------------------------------------------------------------------- scene
class Frame(BaseModel):
    """local (x, y) -> site (X, Y): rotate by rotation_deg about local origin, then translate."""
    rotation_deg: float = 0.0
    origin: XY = (0.0, 0.0)

    def to_site(self, x: float, y: float) -> XY:
        t = math.radians(self.rotation_deg)
        c, s = math.cos(t), math.sin(t)
        return (self.origin[0] + c * x - s * y, self.origin[1] + s * x + c * y)

    def to_local(self, X: float, Y: float) -> XY:
        t = math.radians(self.rotation_deg)
        c, s = math.cos(t), math.sin(t)
        dx, dy = X - self.origin[0], Y - self.origin[1]
        return (c * dx + s * dy, -s * dx + c * dy)


class Element(BaseModel):
    id: str
    kind: str                     # data_hall, elec_room, genset, transformer, chiller, ...
    name: str = ""
    x: float = 0.0                # min corner, local frame
    y: float = 0.0
    w: float = 0.0                # extent along local x
    d: float = 0.0                # extent along local y
    z: float = 0.0
    h: float = 0.0
    shape: Literal["box", "cyl", "poly"] = "box"
    polygon: Optional[list[XY]] = None          # shape == "poly" (roads, parking, landscape)
    holes: Optional[list[list[XY]]] = None
    group: Optional[str] = None                 # array / block id, e.g. "gen-array", "block-07"
    catalogue_key: Optional[str] = None
    locked: bool = False                        # user-moved: regeneration must respect it
    solid: bool = True                          # False for ground surfaces (roads, parking...)
    props: dict[str, Any] = Field(default_factory=dict)

    # geometry helpers -------------------------------------------------------
    def footprint(self) -> Polygon:
        if self.shape == "poly" and self.polygon:
            return Polygon(self.polygon, self.holes or None)
        if self.shape == "cyl":
            r = self.w / 2
            return Point(self.x + r, self.y + r).buffer(r, quad_segs=8)
        return box(self.x, self.y, self.x + self.w, self.y + self.d)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.d / 2

    @property
    def top(self) -> float:
        return self.z + self.h

    def move(self, dx: float, dy: float) -> None:
        self.x += dx
        self.y += dy
        if self.polygon:
            self.polygon = [(px + dx, py + dy) for px, py in self.polygon]
        if self.holes:
            self.holes = [[(px + dx, py + dy) for px, py in ring] for ring in self.holes]


class ArrayDef(BaseModel):
    """A parametric array of modules. Members can be dragged individually (they
    then become locked overrides) or the whole array can be moved."""
    id: str
    kind: str
    count: int
    pitch: XY
    rows: int = 1
    row_pitch: XY = (0.0, 0.0)
    member_ids: list[str] = Field(default_factory=list)


class Check(BaseModel):
    id: str
    severity: Literal["pass", "warn", "fail", "info"]
    message: str
    element_ids: list[str] = Field(default_factory=list)
    value: Optional[float] = None
    limit: Optional[float] = None


class Scene(BaseModel):
    schema_version: str = "0.1"
    option: str = "A"
    variant: dict[str, Any] = Field(default_factory=dict)
    brief: Brief
    site: SiteInput
    frame: Frame = Field(default_factory=Frame)
    redline_local: list[XY] = Field(default_factory=list)
    buildable_local: list[XY] = Field(default_factory=list)
    elements: list[Element] = Field(default_factory=list)
    arrays: list[ArrayDef] = Field(default_factory=list)
    designs: dict[str, Any] = Field(default_factory=dict)   # engine outputs (halls, electrical, cooling...)
    metrics: dict[str, Any] = Field(default_factory=dict)
    checks: list[Check] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)            # engine decisions, for the agent to explain
    score: Optional[float] = None

    # convenience -----------------------------------------------------------
    def get(self, element_id: str) -> Element:
        for e in self.elements:
            if e.id == element_id:
                return e
        raise KeyError(element_id)

    def by_kind(self, *kinds: str) -> list[Element]:
        return [e for e in self.elements if e.kind in kinds]

    def redline_poly(self) -> Polygon:
        return Polygon(self.redline_local)

    def buildable_poly(self) -> Polygon:
        return Polygon(self.buildable_local) if self.buildable_local else self.redline_poly()

    def to_json(self, path: str | None = None) -> str:
        s = self.model_dump_json(indent=1)
        if path:
            with open(path, "w") as f:
                f.write(s)
        return s

    @classmethod
    def from_json(cls, path_or_text: str) -> "Scene":
        text = path_or_text
        if not path_or_text.lstrip().startswith("{"):
            with open(path_or_text) as f:
                text = f.read()
        return cls.model_validate(json.loads(text))
