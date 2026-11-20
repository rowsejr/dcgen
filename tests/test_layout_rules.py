"""Layout rules: heat rejection orientation and grouping, clearances from the catalogue,
corridors and escape routes, maintenance envelopes, project overrides, capacity curves."""
import math
from pathlib import Path

import pytest

from dcgen.catalogue import for_brief, load_catalogue, with_overrides
from dcgen.engines.core import build_core, hall_columns, mirror_core, row_capacity, spread_avoiding
from dcgen.engines.cooling import curve_capacity_kw, interp, select_heat_rejection, unit_capacity_kw
from dcgen.engines.egress import hall_door_positions, hall_interior_travel
from dcgen.io import load_brief, load_site
from dcgen.layout import generate_options, move_element, run_engines
from dcgen.schema import Brief

EX = Path(__file__).parents[1] / "examples"
CAT = load_catalogue()


def core_for(brief: Brief, cat=CAT, hr_location="ground", mirror=(False, False)):
    n, hall, elec, hr = run_engines(brief, cat)
    core = build_core(brief, cat, hall, elec, hr, n, hr_location)
    return mirror_core(core, *mirror), hall, hr, n


# --------------------------------------------------------------- heat rejection
@pytest.mark.parametrize("loc", ["ground", "roof"])
def test_heat_rejection_perpendicular_to_facade(loc):
    core, _, hr, _ = core_for(Brief(it_load_mw=24, hall_it_mw=6), hr_location=loc)
    units = [e for e in core.elements if e.kind in ("chiller", "dry_cooler")]
    assert len(units) == hr.n_total
    for u in units:
        assert u.d == pytest.approx(hr.unit["length"]) and u.w == pytest.approx(hr.unit["width"])
        assert u.d > u.w                      # long axis across the building = perpendicular to the facade


def test_heat_rejection_grouped_per_hall():
    core, hall, hr, n = core_for(Brief(it_load_mw=24, hall_it_mw=6))
    halls = {h.id: h for h in core.elements if h.kind == "data_hall"}
    per_col = core.info["hr_per_hall"]
    assert sum(per_col) == hr.n_total and max(per_col) - min(per_col) <= 1
    for u in (e for e in core.elements if e.kind == "chiller"):
        col_halls = [halls[i] for i in u.props["serves"]]
        assert all(h.x - 1e-6 <= u.x and u.x + u.w <= h.x + h.w + 1e-6 for h in col_halls)   # in front of its hall
    ids = {a.id for a in core.arrays}
    assert {f"hr-ground-C{c + 1}" for c in range(n)} <= ids


def test_heat_rejection_groups_leave_escape_lanes():
    core, _, _, _ = core_for(Brief(it_load_mw=24, hall_it_mw=6))
    lane = CAT["egress"]["hr_lane_width"]
    for boundary in [e for e in core.elements if e.kind == "exit" and e.props["lane_dir"] == "n"]:
        cx = boundary.x + boundary.w / 2
        for u in (e for e in core.elements if e.kind == "chiller"):
            assert u.x >= cx + lane / 2 - 1e-6 or u.x + u.w <= cx - lane / 2 + 1e-6


def test_mirror_keeps_exit_lanes_pointing_outwards():
    core, *_ = core_for(Brief(), mirror=(True, True))
    bx0, _, bx1, _ = core.bbox
    mid = (bx0 + bx1) / 2
    for e in core.elements:
        if e.kind == "exit" and e.name == "Support exit":                  # support block is now at the east end
            assert e.x > mid and e.props["lane_dir"] == "e"
        if e.kind == "exit" and e.name == "Corridor end exit":             # far end is now the west end
            assert e.x < mid and e.props["lane_dir"] == "w"
    dirs = {e.props["lane_dir"] for e in core.elements if e.kind == "exit"}
    assert "s" in dirs and "n" not in dirs                                # gallery exits now face south


# --------------------------------------------------------------- capacity curves
def test_interp_clamps_and_interpolates():
    assert interp([20, 40], [100, 60], 30) == pytest.approx(80)
    assert interp([20, 40], [100, 60], 10) == 100 and interp([20, 40], [100, 60], 50) == 60


def test_curve_capacity_overrides_linear_model():
    u = dict(CAT["heat_rejection"]["acc_fc_1400"], curve={"ambient_c": [25, 35, 45], "capacity_kw": [1600, 1400, 1000]})
    assert unit_capacity_kw(u, Brief(ambient_design_c=40, hr_uplift_k=0)) == pytest.approx(1200)


def test_two_d_curves_interpolate_on_fluid_temperature():
    u = dict(CAT["heat_rejection"]["dc_vbank_1100"], curves=[
        {"fluid_in_c": 40, "ambient_c": [20, 40], "capacity_kw": [1000, 500]},
        {"fluid_in_c": 50, "ambient_c": [20, 40], "capacity_kw": [1400, 900]}])
    assert curve_capacity_kw(u, 30, 45) == pytest.approx((750 + 1150) / 2)


# --------------------------------------------------------------- columns, corridors, egress
def test_hall_columns_cross_corridor_every_two():
    cols = hall_columns(4, 31.2, CAT["halls"])
    assert len(cols.cross) == 1 and len(cols.walls) == 2
    assert cols.length == pytest.approx(4 * 31.2 + CAT["halls"]["cross_corridor_width"] + 2 * CAT["halls"]["fire_wall"])
    assert cols.right_gap[0] == "wall" and cols.right_gap[1] == "cross" and cols.left_gap[3] == "wall"


def test_cross_corridors_and_fire_walls_become_elements():
    core, *_ = core_for(Brief(it_load_mw=24, hall_it_mw=6))
    kinds = [e.id for e in core.elements]
    assert "XC1" in kinds and "FW1" in kinds and "FW2" in kinds
    halls = sorted((e for e in core.elements if e.kind == "data_hall"), key=lambda e: e.x)
    for a, b in zip(halls, halls[1:]):
        assert b.x - (a.x + a.w) > 0.3                  # never abutting: wall or corridor between


def test_hall_interior_travel_formula():
    assert hall_interior_travel(30, 28, 2) == pytest.approx(14 + 7.5)
    assert hall_interior_travel(30, 28, 1) == pytest.approx(14 + 15)
    assert hall_door_positions(30, 2) == [7.5, 22.5]


def test_more_doors_shorten_travel():
    a, _, _, _ = core_for(Brief(), cat=with_overrides(CAT, {"egress.doors_per_hall_wall": 1}))
    b, _, _, _ = core_for(Brief(), cat=with_overrides(CAT, {"egress.doors_per_hall_wall": 3}))
    assert a.info["egress"]["hall_interior_travel_m"] > b.info["egress"]["hall_interior_travel_m"]


def test_exits_and_stairs():
    core, *_ = core_for(Brief(it_load_mw=24, hall_it_mw=6))
    exits = [e for e in core.elements if e.kind == "exit"]
    assert len(exits) == core.info["egress"]["exits"] == 3 + 1 + 1 + 2   # 3 internal boundaries, gallery end, corridor end, 2 support
    assert any(e.kind == "stair" for e in core.elements) == (core.info["egress"]["stairs"] == 1)
    cor = next(e for e in core.elements if e.id == "COR")
    stair = next((e for e in core.elements if e.kind == "stair"), None)
    if stair:
        assert cor.x + cor.w == pytest.approx(stair.x)  # corridor stops where the stair core starts


# --------------------------------------------------------------- overrides, no hard-coded clearances
def test_override_changes_geometry_not_catalogue_file():
    b = Brief(it_load_mw=24, hall_it_mw=6)
    base, *_ = core_for(b)
    wide, *_ = core_for(b, cat=with_overrides(CAT, {"halls.corridor_depth": 6.0}))
    assert wide.info["building_depth"] - base.info["building_depth"] == pytest.approx(2.0)
    assert load_catalogue()["halls"]["corridor_depth"] == 4.0


def test_override_path_typos_fail_loudly():
    with pytest.raises(KeyError):
        with_overrides(CAT, {"halls.corridor_dept": 6.0})
    with pytest.raises(KeyError):
        for_brief(Brief(overrides={"nope.x": 1}))


@pytest.mark.parametrize("key,delta", [("layout.hr_yard_offset", 2.0), ("layout.tx_to_building", 1.0)])
def test_layout_gaps_come_from_catalogue(key, delta):
    b = Brief(it_load_mw=24, hall_it_mw=6)
    base, *_ = core_for(b)
    sec, name = key.split(".")
    moved, *_ = core_for(b, cat=with_overrides(CAT, {key: CAT[sec][name] + delta}))
    y0, y1 = base.bbox[1], base.bbox[3]
    z0, z1 = moved.bbox[1], moved.bbox[3]
    assert (z1 - z0) - (y1 - y0) == pytest.approx(delta)


# --------------------------------------------------------------- lanes through the yard
def test_spread_avoiding_keeps_out_of_lanes():
    lanes = [(20.0, 26.0), (50.0, 56.0)]
    xs = spread_avoiding(10, 0, 80, 3.0, 5.0, lanes)
    assert len(xs) == 10
    for x in xs:
        assert all(x + 3.0 <= a + 1e-6 or x >= b - 1e-6 for a, b in lanes)
    assert row_capacity(0, 80, 5.0, lanes) == 4 + 4 + 4
    with pytest.raises(ValueError):
        spread_avoiding(40, 0, 80, 3.0, 5.0, lanes)


# --------------------------------------------------------------- whole-site checks
@pytest.fixture(scope="module")
def best():
    return generate_options(load_brief(EX / "brief.yaml"), load_site(EX / "site.yaml"), max_options=1)[0]


def test_example_golden_layout(best):
    """Golden numbers for the example. Change only on purpose and say why in the commit message
    (v2: perpendicular heat rejection per hall, fire walls, cross corridor, 0.6 m planning module;
    v3: gross CHW load and 5 K air-on uplift per CUN-CAL-PUE-001 -> 23 chillers; chillers do not fit the
    600 kW IT lineup allowance -> 6 separate mechanical lineups; benchmark genset sizes)."""
    h = best.designs["halls"]
    assert (h["width_x"], h["depth_y"]) == (31.2, 28.2)
    assert best.designs["cooling"]["total"] == 23 and sorted(best.designs["core"]["hr_per_hall"]) == [5, 6, 6, 6]
    e = best.designs["electrical"]
    assert e["mech_lineups"] == "separate" and e["mech_lineup_count"] == 6 and e["gensets"] == 16 + 6
    assert best.designs["peak_pue"]["peak_pue"] == pytest.approx(1.48, abs=0.005)


def test_example_egress_and_clearance_checks(best):
    sev = {c.id: c.severity for c in best.checks}
    for cid in ("egress.hall", "egress.hall_exit", "egress.exits", "egress.lanes", "maint.zones"):
        assert sev[cid] == "pass", cid


def test_drag_unit_into_neighbour_clearance_warns(best):
    sc = best.model_copy(deep=True)
    gens = [e for e in sc.elements if e.kind == "genset"]
    a, b = gens[0], gens[1]
    gap = b.x - (a.x + a.w)
    sc = move_element(sc, b.id, -(gap - 0.2), 0.0)       # leave 0.2 m instead of the 1.5 m maintenance gap
    chk = next(c for c in sc.checks if c.id == "maint.zones")
    assert chk.severity == "warn" and {a.id, b.id} & set(chk.element_ids)


def test_blocking_an_exit_lane_warns(best):
    sc = best.model_copy(deep=True)
    ex = next(e for e in sc.elements if e.kind == "exit" and e.props["lane_dir"] in ("n", "s"))
    unit = next(e for e in sc.elements if e.kind in ("chiller", "genset", "transformer") and not e.props.get("on_roof"))
    sc = move_element(sc, unit.id, ex.x + ex.w / 2 - unit.cx,
                      (ex.y + (6 if ex.props["lane_dir"] == "n" else -6)) - unit.cy)
    assert next(c for c in sc.checks if c.id == "egress.lanes").severity == "warn"


def test_overrides_flow_through_generate_options():
    b = load_brief(EX / "brief.yaml").model_copy(update={"overrides": {"halls.cross_corridor_every": 1}})
    o = generate_options(b, load_site(EX / "site.yaml"), max_options=1)[0]
    assert o.designs["core"]["cross_corridors"] == 3
