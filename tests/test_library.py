"""Plant library, import/approval workflow, selection, client profiles, reference designs."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from dcgen import library, service
from dcgen.catalogue import for_brief, load_catalogue
from dcgen.engines.cooling import rank_heat_rejection, select_heat_rejection
from dcgen.io import load_brief, load_site
from dcgen.layout import generate_options
from dcgen.library import ai_extract, profiles, reference
from dcgen.library.importers import apply_mapping, import_file
from dcgen.library.records import parse_row, to_catalogue, to_row
from dcgen.library.store import Library, read_csv
from dcgen.library.templates import fields_markdown, write_templates
from dcgen.library.validate import errors, validate_record
from dcgen.schema import Brief

EX = Path(__file__).parents[1] / "examples"


@pytest.fixture()
def pack(tmp_path, monkeypatch):
    dst = tmp_path / "pack"
    shutil.copytree(EX / "pack", dst)
    monkeypatch.setenv("DCGEN_PACKS", str(dst))
    library._CACHE.clear()
    yield dst
    library._CACHE.clear()


# --------------------------------------------------------------- records, templates, validation
def test_templates_cover_every_type(tmp_path):
    files = write_templates(tmp_path)
    assert any(f.endswith("plant_library_template.xlsx") for f in files)
    import openpyxl
    wb = openpyxl.load_workbook(tmp_path / "plant_library_template.xlsx")
    assert {"README", "chillers", "gensets", "transformers", "elec_room_typicals"} <= set(wb.sheetnames)
    assert "capacity_kw*" in fields_markdown()


def test_csv_roundtrip_keeps_curves_and_units(pack):
    row = next(r for r in read_csv(pack / "plant" / "chillers.csv") if r["record_id"] == "EX-CH-1800")
    rec, issues = parse_row("chiller", row)
    assert not issues and rec["curve_ambient_c"] == [25, 35, 40, 45]
    assert parse_row("chiller", to_row("chiller", rec))[0] == rec
    cat = to_catalogue("chiller", rec)
    assert cat["type"] == "air_cooled_chiller" and cat["curve"]["capacity_kw"][-1] == 1400 and cat["record_id"] == "EX-CH-1800"


def test_validation_catches_transcription_slips():
    good = {"record_id": "X", "manufacturer": "M", "model": "m", "capacity_kw": 1400, "rated_ambient_c": 35, "max_ambient_c": 48,
            "derate_per_k": 0.012, "length": 12, "width": 2.3, "height": 2.6, "intake_clear": 3, "row_clear": 3.5, "lw_dba": 92,
            "source": "s", "status": "draft"}
    assert not errors(validate_record("chiller", good))
    assert any("length" in m for m in errors(validate_record("chiller", good | {"length": 12000})))        # mm as m
    assert any("derate_per_k or a capacity curve" in m for m in errors(validate_record("chiller", {k: v for k, v in good.items() if k != "derate_per_k"})))
    assert any("ascending" in m for m in errors(validate_record("chiller", good | {"curve_ambient_c": [35, 25, 45], "curve_capacity_kw": [1, 2, 3]})))
    bad_order = validate_record("chiller", good | {"curve_ambient_c": [25, 35, 45], "curve_capacity_kw": [1000, 1400, 1800]})
    assert any(l == "warn" and "rises" in m for l, m in bad_order)
    assert any("kwe/kva" in m for m in errors(validate_record("genset", {"record_id": "G", "manufacturer": "M", "model": "m", "kva": 3000, "kwe": 900,
        "length": 13, "width": 3, "height": 4, "side_clear": 1.5, "end_clear": 4, "lw_dba": 100, "fuel_l_per_kwh": 0.26, "source": "s"})))
    bands = good | {k: 80.0 for k in ("lw_63", "lw_125", "lw_250", "lw_500", "lw_1k", "lw_2k", "lw_4k", "lw_8k")}
    assert any("octave bands" in m for _, m in validate_record("chiller", bands))


# --------------------------------------------------------------- import, drafts, approval
MAPPING = {"columns": {"Model": "model", "Net cooling (MW)": "capacity_kw", "Length (mm)": "length", "Width (mm)": "width",
                       "Height (mm)": "height", "Rated ambient (F)": "rated_ambient_c", "Sound power (dBA)": "lw_dba"},
           "units": {"capacity_kw": {"factor": 1000}, "length": {"factor": 0.001}, "width": {"factor": 0.001},
                     "height": {"factor": 0.001}, "rated_ambient_c": {"fahrenheit": True}},
           "defaults": {"manufacturer": "OtherCo", "max_ambient_c": 46, "derate_per_k": 0.011, "intake_clear": 3.0, "row_clear": 3.5},
           "id_pattern": "CH-{manufacturer}-{model}"}


def _export(tmp_path):
    f = tmp_path / "selection.csv"
    f.write_text("Model,Net cooling (MW),Length (mm),Width (mm),Height (mm),Rated ambient (F),Sound power (dBA)\n"
                 "OC 1500,1.5,12500,2300,2650,95,93\n"
                 "OC 900,0.9,9800,2200,2500,95,89\n")
    return f


def test_mapping_converts_units():
    row = apply_mapping({"Model": "X", "Net cooling (MW)": "1.5", "Length (mm)": "12500", "Rated ambient (F)": "95"}, MAPPING)
    assert row["capacity_kw"] == 1500 and row["length"] == 12.5 and row["rated_ambient_c"] == pytest.approx(35.0)


def test_import_makes_drafts_and_never_overwrites(pack, tmp_path):
    rep = import_file(_export(tmp_path), "chiller", pack / "plant", MAPPING, entered_by="JR")
    assert rep.added == 2 and "CH-OTHERCO-OC-1500" in rep.ids
    lib = Library([pack])
    rec = lib.records["chiller"]["CH-OTHERCO-OC-1500"]
    assert rec["status"] == "draft" and rec["capacity_kw"] == 1500 and rec["entered_by"] == "JR" and rec["source"] == "selection.csv"
    again = import_file(_export(tmp_path), "chiller", pack / "plant", MAPPING)
    assert again.added == 0 and len(again.skipped) == 2


def test_drafts_stay_out_of_client_runs_unless_allowed(pack, tmp_path):
    import_file(_export(tmp_path), "chiller", pack / "plant", MAPPING)
    library._CACHE.clear()
    cat = for_brief(Brief())
    assert "CH-OTHERCO-OC-1500" not in cat["heat_rejection"] and "EX-CH-1200Q" not in cat["heat_rejection"]
    cat = for_brief(Brief(use_drafts=True))
    assert {"CH-OTHERCO-OC-1500", "EX-CH-1200Q"} <= set(cat["heat_rejection"])


def test_approval_needs_a_named_checker_and_clean_record(pack, tmp_path):
    import_file(_export(tmp_path), "chiller", pack / "plant", MAPPING)
    lib = Library([pack])
    assert "needs checked_by" in lib.set_status(["CH-OTHERCO-OC-1500"], "approved", "")[0]
    assert lib.get("CH-OTHERCO-OC-1500")[1]["status"] == "draft"
    msgs = service.library_approve(["CH-OTHERCO-OC-1500", "NOPE"], "J Rowse")
    assert msgs[0].endswith("approved") and "not found" in msgs[1]
    rec = Library([pack]).get("CH-OTHERCO-OC-1500")[1]
    assert rec["status"] == "approved" and rec["checked_by"] == "J Rowse" and rec["checked_date"]


def test_invalid_record_cannot_be_approved(pack, tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("Model,Net cooling (MW),Length (mm),Width (mm),Height (mm),Rated ambient (F),Sound power (dBA)\n"
                 "BAD 1,1.5,12500000,2300,2650,95,93\n")
    import_file(f, "chiller", pack / "plant", MAPPING)
    msgs = Library([pack]).set_status(["CH-OTHERCO-BAD-1"], "approved", "JR")
    assert "not approved" in msgs[0] and "length" in msgs[0]
    library._CACHE.clear()
    assert "CH-OTHERCO-BAD-1" not in for_brief(Brief(use_drafts=True))["heat_rejection"]       # invalid records never reach the engines


def test_duplicate_ids_reported(pack):
    p = pack / "plant" / "gensets.csv"
    rows = p.read_text().splitlines()
    p.write_text("\n".join(rows + [rows[1]]) + "\n")
    lib = Library([pack])
    assert any("duplicate record_id" in i for i in lib.issues)


# --------------------------------------------------------------- catalogue merge and selection
def test_library_replaces_placeholders_and_feeds_room_table(pack):
    cat = for_brief(Brief())
    assert set(cat["heat_rejection"]) == {"EX-CH-1400", "EX-CH-1800", "EX-DC-1100"}
    assert set(cat["gensets"]) == {"EX-GEN-2500", "EX-GEN-3500"} and "acc_fc_1400" not in cat["heat_rejection"]
    assert [t["kw"] for t in cat["electrical_rooms"]["table"]] == [1000, 2000, 3000]
    from dcgen.engines.electrical import design_electrical
    e = design_electrical(Brief(it_load_mw=24, hall_it_mw=6), cat, 4)
    assert e.room_width == pytest.approx(11.0) and e.genset_key == "EX-GEN-3500"
    assert any("reference typicals" in n for n in e.notes)


def test_selection_follows_preference(pack):
    sel = lambda **kw: select_heat_rejection(Brief(ambient_design_c=36, **kw), for_brief(Brief(**kw))).unit_key
    assert sel() == "EX-CH-1400"
    assert sel(hr_prefer="footprint") == "EX-CH-1800"
    assert sel(hr_prefer="count") == "EX-CH-1800"
    assert sel(hr_prefer="noise", use_drafts=True) == "EX-CH-1200Q"
    assert sel(hr_unit="EX-CH-1800") == "EX-CH-1800"


def test_selection_lists_runners_up_and_uses_curves(pack):
    b = Brief(ambient_design_c=36)
    ranked = rank_heat_rejection(b, for_brief(b))
    assert [c.key for c in ranked] == ["EX-CH-1400", "EX-CH-1800"]
    big = next(c for c in ranked if c.key == "EX-CH-1800")
    assert big.cap_kw == pytest.approx(1764)                          # interpolated on the curve, not a linear derate
    d = select_heat_rejection(b, for_brief(b))
    assert any("Runners-up" in n and "EX-1800" in n for n in d.notes)


def test_forced_unit_must_exist(pack):
    with pytest.raises(KeyError):
        rank_heat_rejection(Brief(hr_unit="NOPE"), for_brief(Brief(hr_unit="NOPE")))


def test_no_packs_means_placeholder_catalogue(monkeypatch):
    monkeypatch.delenv("DCGEN_PACKS", raising=False)
    library._CACHE.clear()
    assert for_brief(Brief())["heat_rejection"].keys() == load_catalogue()["heat_rejection"].keys()


# --------------------------------------------------------------- client profiles
def test_profile_updates_and_filters(pack):
    prof = library.active().profiles["exampleco-standard"]
    up = profiles.brief_updates(prof)
    assert up["topology"] == "5M4" and up["profile"] == "exampleco-standard" and up["overrides"]["halls.corridor_depth"] == 4.5
    # a second manufacturer's approved record is excluded for this client
    f = pack / "plant" / "chillers.csv"
    lib = Library([pack])
    rec = dict(lib.records["chiller"]["EX-CH-1400"], record_id="OC-1", manufacturer="OtherCo")
    rec = {k: v for k, v in rec.items() if not k.startswith("_")}
    lib.add_drafts("chiller", [rec], pack / "plant")
    service.library_approve(["OC-1"], "JR")
    assert "OC-1" in for_brief(Brief())["heat_rejection"]
    assert "OC-1" not in for_brief(Brief(profile="exampleco-standard"))["heat_rejection"]
    with pytest.raises(KeyError):
        for_brief(Brief(profile="missing"))


@pytest.fixture(scope="module")
def profiled():
    """One 10 s layout shared by the profile/benchmark tests (module scope)."""
    mp = pytest.MonkeyPatch()
    mp.setenv("DCGEN_PACKS", str(EX / "pack"))
    library._CACHE.clear()
    b = load_brief(EX / "brief.yaml").model_copy(update=profiles.brief_updates(library.active().profiles["exampleco-standard"]))
    sc = generate_options(b, load_site(EX / "site.yaml"), max_options=1)[0]
    yield sc
    mp.undo()
    library._CACHE.clear()


def test_compliance_sheet_with_clause_references(profiled):
    c = profiled.designs["compliance"]
    assert c["profile"] == "exampleco-standard" and c["summary"] == {"pass": 5, "manual": 1}
    row = next(r for r in c["rows"] if r["id"] == "R-4.2.3")
    assert row["clause"] == "EXAMPLE-STD 4.2.3" and row["status"] == "pass" and row["value"] >= 15
    assert next(r for r in c["rows"] if r["id"] == "R-9.1")["status"] == "manual"
    csv = profiles.compliance_csv(c["rows"], {"id": c["profile"], "revision": c["revision"]})
    assert "EXAMPLE-STD 5.1" in csv and csv.splitlines()[0].startswith("Profile: exampleco-standard")


def test_data_status_check_reflects_provenance(profiled):
    chk = next(c for c in profiled.checks if c.id == "data.status")
    assert chk.severity == "pass" and "EX-GEN-2500 (approved)" in chk.message   # 5M4: 1.5 MW blocks fit the smaller set
    assert {u["role"] for u in profiled.designs["library"]["used"]} == {"genset", "transformer", "heat rejection"}


def test_failing_client_rule_becomes_a_fail_check(profiled):
    prof = {"rules": [{"id": "R-X", "clause": "STD 9.9", "text": "Gensets 500 m from the boundary", "severity": "fail",
                      "check": {"type": "min_distance", "a": {"kind": "genset"}, "b": "boundary", "min": 500}},
                     {"id": "R-Y", "clause": "STD 9.8", "text": "Soft rule", "severity": "warn",
                      "check": {"type": "count", "kind": "genset", "op": "<", "value": 1}},
                     {"id": "R-Z", "clause": "STD 9.7", "text": "Broken", "check": {"type": "metric", "path": "metrics.nope", "op": "<", "value": 1}}]}
    rows = profiles.evaluate(profiled, prof)
    assert [r["status"] for r in rows] == ["fail", "warn", "error"]
    assert rows[0]["evidence"]                                           # the offending gensets are named


def test_draft_plant_raises_a_warning(pack):
    b = load_brief(EX / "brief.yaml").model_copy(update={"use_drafts": True, "hr_prefer": "noise"})
    o = generate_options(b, load_site(EX / "site.yaml"), max_options=1)[0]
    chk = next(c for c in o.checks if c.id == "data.status")
    assert chk.severity == "warn" and "EX-CH-1200Q (draft)" in chk.message and "not for client issue" in chk.message


# --------------------------------------------------------------- reference designs
def test_reference_designs_regress_cleanly(pack):
    out = service.regress()
    assert {r["id"] for r in out} == {"REF-001", "REF-002"} and all(r["ok"] for r in out)


def test_regression_fails_when_the_engine_drifts(pack):
    card = yaml.safe_load((pack / "reference" / "REF-001.yaml").read_text())
    card["expected"]["gensets"] += 2
    card["expected"]["hr_units"] = 30
    r = reference.regress_card(card)
    assert not r["ok"] and {x["key"] for x in r["rows"] if x["status"] == "FAIL"} == {"gensets", "hr_units"}


def test_benchmark_flags_outliers(profiled):
    m = reference.scene_metrics(profiled)
    same = [{"id": "SELF", "brief": {"it_load_mw": 24}, "metrics": m}]
    assert reference.benchmark(profiled, same)["outliers"] == []
    skew = [{"id": "SKEW", "brief": {"it_load_mw": 24}, "metrics": dict(m, gia_m2=m["gia_m2"] * 3)}]
    assert reference.benchmark(profiled, skew)["outliers"] == ["gia_per_mw"]


def test_benchmark_runs_inside_every_layout(profiled):
    """The shipped cards are 4M3/2N schemes; a 5M4 layout carries more blocks, so plant per MW is flagged."""
    bm = profiled.designs["benchmark"]
    assert set(bm["refs"]) == {"REF-001", "REF-002"}
    assert {"gensets_per_mw", "transformers_per_mw"} <= set(bm["outliers"])
    assert any(c.id == "bench.outliers" and c.severity == "warn" for c in profiled.checks)


def test_service_search_and_get(pack):
    rows = service.library_search("chiller", capacity_kw_min=1500)
    assert [r["id"] for r in rows] == ["EX-CH-1800"]
    assert service.library_search("chiller", "1200", include_drafts=True)[0]["status"] == "draft"
    assert service.library_get("EX-GEN-3500")["kwe"] == 2800
    with pytest.raises(service.ServiceError):
        service.library_get("NOPE")


def test_service_apply_profile_and_plant_options(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "HOME", tmp_path / "proj")
    service.STATE.clear()
    service.create_project("p1", {"it_load_mw": 24, "ambient_design_c": 36})
    out = service.apply_profile("p1", "exampleco-standard")
    assert out["profile"]["id"] == "exampleco-standard" and service.state("p1")["brief"].topology == "5M4"
    po = service.plant_options("p1")
    assert po["profile"]["id"] == "exampleco-standard" and po["heat_rejection"][0]["id"] == "EX-CH-1400"
    assert po["genset"]["id"] == "EX-GEN-2500" and "heat_rejection" in po["library_sections"]


# --------------------------------------------------------------- AI extraction request builder
def test_extraction_request_shape_and_parse():
    schema = ai_extract.tool_schema("chiller")
    assert "capacity_kw" in schema["input_schema"]["required"] and "status" not in schema["input_schema"]["properties"]
    req = ai_extract.batch_request("sheet1", "datasheet text", "chiller", "claude-haiku-5-5")
    assert req["custom_id"] == "sheet1" and req["params"]["tool_choice"]["name"] == "record_plant"
    msg = {"content": [{"type": "tool_use", "name": "record_plant", "input": {"model": "X", "capacity_kw": 1400, "lw_dba": None}}]}
    assert ai_extract.parse_result(msg) == {"model": "X", "capacity_kw": 1400}
    with pytest.raises(ValueError):
        ai_extract.parse_result({"content": []})
