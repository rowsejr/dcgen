"""Agent-driver tools: undo, compass moves, regenerate keeping locks, what-if, fixes, preview, schema."""
import math

import pytest

from dcgen import layout, service
from dcgen.io import load_brief, load_site

PNG = b"\x89PNG"


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "HOME", tmp_path)
    service.STATE.clear()
    name = "agt"
    service.create_project(name, load_brief("examples/brief.yaml").model_dump() | {"project": name})
    service.set_site(name, load_site("examples/site.yaml"))
    service.generate_options(name, max_options=1)
    return name


def _pos(p, eid):
    e = service.scene_of(p).get(eid)
    return e.x, e.y


def _some_id(p, kind="fuel_bund"):
    return service.scene_of(p).by_kind(kind)[0].id


def test_undo_restores_position(proj):
    eid = _some_id(proj)
    before = _pos(proj, eid)
    service.move_element(proj, eid, 5, 0)
    assert _pos(proj, eid) != before
    assert service.history(proj)[-1].startswith("move")
    service.undo(proj)
    assert _pos(proj, eid) == pytest.approx(before)


def test_undo_empty_after_exhaustion(proj):
    while service.history(proj):
        service.undo(proj)
    with pytest.raises(service.ServiceError):
        service.undo(proj)


def test_history_capped(proj):
    eid = _some_id(proj)
    for _ in range(service.HISTORY_CAP + 5):
        service.move_element(proj, eid, 0.1, 0)
    assert len(service.history(proj)) == service.HISTORY_CAP


def test_unknown_element_does_not_leave_history(proj):
    n = len(service.history(proj))
    with pytest.raises(service.ServiceError):
        service.move_element(proj, "nope", 1, 1)
    assert len(service.history(proj)) == n


def test_compass_move_matches_bearing(proj):
    sc = service.scene_of(proj)
    eid = _some_id(proj)
    dx, dy = layout.bearing_to_local(sc, 90, 10)           # 10 m east
    assert math.hypot(dx, dy) == pytest.approx(10)
    # round trip: convert back to a true bearing with the same maths as Variant.label
    t = math.radians(sc.frame.rotation_deg)
    sx, sy = math.cos(t) * dx - math.sin(t) * dy, math.sin(t) * dx + math.cos(t) * dy
    brg = (math.degrees(math.atan2(sx, sy)) + sc.site.true_north_deg) % 360
    assert brg == pytest.approx(90, abs=1e-6)
    out = service.move_compass(proj, eid, "E", 10)
    assert out["bearing_deg"] == 90 and out["moved"] == eid


def test_opposite_compass_cancels(proj):
    eid = _some_id(proj)
    p0 = _pos(proj, eid)
    service.move_compass(proj, eid, "N", 7)
    service.move_compass(proj, eid, 180, 7)
    assert _pos(proj, eid) == pytest.approx(p0, abs=1e-6)


def test_bad_direction(proj):
    with pytest.raises(service.ServiceError):
        service.move_compass(proj, _some_id(proj), "up", 3)


def test_move_towards_reduces_distance(proj):
    sc = service.scene_of(proj)
    a, b = sc.by_kind("fuel_bund")[0], sc.by_kind("substation")[0]
    d0 = math.dist((a.cx, a.cy), (b.cx, b.cy))
    service.move_towards(proj, a.id, b.id, 4)
    sc = service.scene_of(proj)
    a2, b2 = sc.get(a.id), sc.get(b.id)
    assert math.dist((a2.cx, a2.cy), (b2.cx, b2.cy)) == pytest.approx(d0 - 4, abs=1e-6)


def test_regenerate_keeps_locked_moves(proj):
    eid = _some_id(proj)
    service.move_element(proj, eid, 3, 0)
    moved = _pos(proj, eid)
    service.update_brief(proj, {"overrides": {"halls.cross_corridor_every": 1}})
    service.regenerate(proj)
    assert _pos(proj, eid) == pytest.approx(moved)
    assert service.scene_of(proj).get(eid).locked
    assert any("Regenerated" in line for line in service.scene_of(proj).log)
    service.undo(proj)                                     # regenerate is undoable
    assert service.scene_of(proj).brief.overrides == {}


def test_regenerate_applies_new_brief(proj):
    before = service.scene_of(proj).designs["halls"]["n_halls"]
    service.update_brief(proj, {"it_load_mw": 30.0})
    service.regenerate(proj)
    assert service.scene_of(proj).designs["halls"]["n_halls"] != before


def test_what_if_does_not_change_project(proj):
    before = service.scene_of(proj).model_dump_json()
    rows = service.what_if(proj, [{"topology": "2N"}, {"overrides": {"egress.plant_room_exits": True}}])
    assert len(rows) == 2 and all(r["fits"] for r in rows)
    assert "delta" in rows[0] and "fixed" in rows[0]
    assert service.scene_of(proj).model_dump_json() == before
    assert service.state(proj)["brief"].topology != "2N"


def test_what_if_bad_key_reported_not_raised(proj):
    rows = service.what_if(proj, [{"overrides": {"halls.no_such_key": 1}}])
    assert rows[0]["fits"] is None and "error" in rows[0]


def test_what_if_limits(proj):
    with pytest.raises(service.ServiceError):
        service.what_if(proj, [])
    with pytest.raises(service.ServiceError):
        service.what_if(proj, [{}] * 7)


def test_suggest_fixes_covers_non_passing(proj):
    fx = service.suggest_fixes(proj)
    sc = service.scene_of(proj)
    assert {f["check"] for f in fx} == {c.id for c in sc.checks if c.severity in ("fail", "warn")}
    assert all(f["levers"] for f in fx if f["check"].startswith("egress"))


def test_every_override_in_levers_is_real(proj):
    schema = service.brief_schema()["override_keys"]
    import re
    for levers in layout.FIX_LEVERS.values():
        for text in levers:
            for key in re.findall(r"overrides: ([a-z_]+\.[a-z_.]+)", text):
                key = key.rstrip(".")
                assert any(k == key or k.startswith(key.split("<")[0]) for k in schema), key


def test_preview_png_all_views(proj):
    for v in ("plan", "overview", "closeup"):
        assert service.preview(proj, v, 700)[:4] == PNG
    with pytest.raises(service.ServiceError):
        service.preview(proj, "side")


def test_brief_schema_shape(proj):
    s = service.brief_schema()
    assert {"hr_prefer", "overrides", "profile"} <= set(s["brief_fields"])
    assert "halls.round_to" in s["override_keys"] and "egress.doors_per_hall_wall" in s["override_keys"]


def test_approve_is_not_an_agent_tool():
    from dcgen import mcp_server
    names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
    assert not any("approve" in n for n in names)
    assert {"undo", "what_if", "preview", "move_compass", "regenerate", "suggest_fixes"} <= names
