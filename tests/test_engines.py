"""Engine tests: the numbers engineers will check first. Run: pytest -q"""
import math

import pytest

from dcgen.catalogue import load_catalogue
from dcgen.engines.cooling import select_heat_rejection, unit_capacity_kw
from dcgen.engines.halls import design_hall
from dcgen.engines.topology import parse_topology, plan_blocks
from dcgen.schema import Brief

CAT = load_catalogue()


@pytest.mark.parametrize("text,x,y,load", [("4M3", 4, 3, 0.75), ("5M4", 5, 4, 0.80), ("3M2", 3, 2, 2 / 3)])
def test_distributed_redundant(text, x, y, load):
    t = parse_topology(text)
    assert (t.x, t.y, t.kind) == (x, y, "DR")
    assert math.isclose(t.normal_loading, load)
    assert len(t.load_pairs()) == x * (x - 1) // 2


def test_dr_block_sizing_one_group_per_hall():
    p = plan_blocks(parse_topology("4M3"), hall_it_kw=6000, n_halls=4, max_block_kw=3000)
    assert p.block_kw == 2000 and p.n_blocks == 16 and p.installed_kw == 32000


def test_dr_survives_one_block_failure():
    """After losing one block, its partners must not exceed 100% of rating."""
    t = parse_topology("4M3")
    group_load, block_rating = 6000, 2000
    pairs = t.load_pairs()
    share = group_load / len(pairs)
    for failed in range(t.x):
        load = [0.0] * t.x
        for a, b in pairs:
            alive = [k for k in (a, b) if k != failed]
            for k in alive:
                load[k] += share / len(alive)
        assert max(load) <= block_rating + 1e-6


def test_catcher_and_2n():
    c = parse_topology("N+1 catcher", catcher_group=6)
    assert (c.x, c.y, c.kind) == (7, 6, "catcher")
    p = plan_blocks(parse_topology("2N"), hall_it_kw=6000, n_halls=2, max_block_kw=3000)
    assert p.n_blocks == 8 and p.installed_kw == 24000


def test_hall_fits_racks():
    h = design_hall(Brief(hall_it_mw=6, rack_kw=20), CAT)
    assert h.racks_provided >= h.racks_required == 300
    assert h.rows % 2 == 0


def test_chiller_derates_above_rated_ambient():
    u = CAT["heat_rejection"]["acc_fc_1400"]
    assert unit_capacity_kw(u, Brief(ambient_design_c=35)) == pytest.approx(1400)
    assert unit_capacity_kw(u, Brief(ambient_design_c=40)) == pytest.approx(1400 * (1 - 0.012 * 5))


def test_heat_rejection_has_redundancy():
    hr = select_heat_rejection(Brief(it_load_mw=24, ambient_design_c=36, hr_redundancy="N+1"), CAT)
    assert hr.n_duty * hr.cap_kw >= hr.q_kw and hr.n_standby == 1
