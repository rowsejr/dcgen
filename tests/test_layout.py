"""Golden-path layout test on the example site: options fit, pass hard checks,
and edits (drag) are re-checked."""
from pathlib import Path

import pytest

from dcgen.io import load_brief, load_site
from dcgen.layout import generate_options, move_element

EX = Path(__file__).parents[1] / "examples"


@pytest.fixture(scope="module")
def options():
    return generate_options(load_brief(EX / "brief.yaml"), load_site(EX / "site.yaml"), max_options=3)


def test_options_fit_and_rank(options):
    assert options, "no arrangement fitted the example redline"
    assert [o.score for o in options] == sorted(o.score for o in options)
    best = options[0]
    hard = {c.id: c.severity for c in best.checks}
    assert hard["fit.redline"] == "pass" and hard["clash.solids"] == "pass" and hard["clash.roads"] == "pass"


def test_every_site_item_present(options):
    kinds = {e.kind for e in options[0].elements}
    for k in ("data_hall", "elec_room", "genset", "transformer", "substation", "sprinkler_tank", "pump_house",
              "fuel_bund", "guardhouse", "gate", "road", "parking"):
        assert k in kinds, k


def test_drag_into_building_is_flagged(options):
    sc = options[0].model_copy(deep=True)
    sub = sc.get("SUB")
    hall = sc.by_kind("data_hall")[0]
    sc = move_element(sc, "SUB", hall.cx - sub.cx, hall.cy - sub.cy)
    assert any(c.id == "clash.solids" and c.severity == "fail" for c in sc.checks)
    assert sc.get("SUB").locked
