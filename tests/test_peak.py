"""Peak (design-point) PUE, chiller EER at the design air-on, LV lineup limits, and the plant-data import.

Hand calculations follow CUN-CAL-PUE-001 (Cundall PUE calc). The DC01 cross-check uses that workbook's
method and inputs with a synthetic EER curve (no vendor data in the repository)."""
import math
from pathlib import Path

import pytest

from dcgen.catalogue import load_catalogue, with_overrides
from dcgen.engines import peak
from dcgen.engines.cooling import select_heat_rejection
from dcgen.engines.electrical import design_electrical
from dcgen.engines.noise import lw_from_lp1m
from dcgen.io import load_brief
from dcgen.layout import run_engines
from dcgen.schema import Brief

CAT = load_catalogue()
EX = Path(__file__).resolve().parents[1] / "examples"
PACK = Path(__file__).resolve().parents[1] / "packs" / "cundall"


# --------------------------------------------------------------- gross load and EER
def test_gross_cooling_reproduces_the_cun_cal_pue_001_hall_load():
    # DC01: 6 halls x 9 MW, 4,117 m2 each. Hall = 9,000 x 1.01 + 8 W/m2 x 4,117 = 9,122.9 kW, x 1.05 fan gains
    # = 9,579.1 kW per hall -> 57,474.5 kW for the building (the workbook's E.Loading profile C23)
    g = peak.gross_cooling(Brief(it_load_mw=54, hall_it_mw=9), CAT, 6 * 4117)
    assert g["hall_kw"] + g["fan_gains_kw"] == pytest.approx(57474.5, abs=0.1)
    assert g["elec_rooms_kw"] == pytest.approx(5400)                 # 10% of IT on the CHW system


def test_generic_eer_follows_the_vendor_shape_and_chw():
    u = {"type": "air_cooled_chiller", "compressor": "Screw"}
    e35 = peak.chiller_eer(u, 35, 20, CAT)
    e41 = peak.chiller_eer(u, 41, 20, CAT)
    assert e35.value == pytest.approx(4.0) and e35.source == "generic"
    assert e41.value == pytest.approx(4.0 * (0.86 + 0.824) / 2, rel=1e-3)   # shape interpolated at 41 C
    assert peak.chiller_eer(u, 41, 24, CAT).value > e41.value > peak.chiller_eer(u, 45, 20, CAT).value
    assert peak.chiller_eer({"type": "air_cooled_chiller", "compressor": "Turbocor"}, 35, 20, CAT).value == pytest.approx(4.3)
    assert peak.chiller_eer(u, 55, 20, CAT).clamped


def test_record_eer_curve_and_rated_eer_take_precedence():
    curve = {"type": "air_cooled_chiller", "eer_curve": {"ambient_c": [35, 41, 45], "eer": [4.9, 4.12, 3.7]}}
    e = peak.chiller_eer(curve, 41, 20, CAT)
    assert e.value == pytest.approx(4.12) and e.source == "curve" and "not recorded" in e.note
    assert peak.chiller_eer(dict(curve, chw_supply_c=20), 41, 24, CAT).value > 4.12     # CHW correction applied
    assert peak.chiller_eer(curve, 47, 20, CAT).clamped
    rated = {"type": "air_cooled_chiller", "eer_rated": 4.5, "rated_ambient_c": 35}
    assert peak.chiller_eer(rated, 40, 20, CAT).value == pytest.approx(4.5 * 0.86, rel=1e-3)


def test_peak_pue_matches_the_dc01_workbook_at_its_36c_bin():
    """DC01 at 36 C + 5 K: chillers EER 4.12, fixed fans/pumps, UPS 2.5%, LV 1.5% on the lineup rating (36 x
    1,500 + 900 kW) plus chillers and pumps, 36 x 21 kW transformers. The workbook gives 1.421 for this bin."""
    b = Brief(it_load_mw=54, hall_it_mw=9, ambient_design_c=36, hr_uplift_k=5)
    g = peak.gross_cooling(b, CAT, 6 * 4117)
    other = peak.other_mech_kw(b, CAT, g)
    assert other["air_fans"] == pytest.approx(1912.6, rel=0.01)       # workbook FWU 1,912.6 kW
    assert other["chw_pumps"] == pytest.approx(1128.5, rel=0.01)      # workbook pumps 1,128.5 kW
    unit = {"type": "air_cooled_chiller", "eer_curve": {"ambient_c": [35, 41, 45], "eer": [4.9, 4.119, 3.7]}}
    hr = peak.hr_peak(unit, 1750, 42, g["q_kw"], b, CAT)
    p = peak.peak_pue(b, CAT, g, other, hr, n_it_lineups=36, lineup_mech_rating_kw=36 * 900,
                      transformers_kva=[2800] * 36, hall_area_m2=6 * 4117)
    assert p.elec["ups_losses"] == pytest.approx(1378.2, rel=0.005)  # 1,350 fixed + 2.5% of pumps
    assert p.elec["transformer_losses"] == pytest.approx(756)
    assert p.pue == pytest.approx(1.421, abs=0.01)


# --------------------------------------------------------------- lineups
def test_units_per_lineup():
    assert peak.per_lineup_mech(400, 20, 4, 16, 0)[1] == 2          # 5 per hall on 4 lineups
    kw, k = peak.per_lineup_mech(400, 16, 4, 16, 1600)
    assert k == 1 and kw == pytest.approx(400 + 100)


@pytest.fixture(scope="module")
def example():
    return load_brief(EX / "brief.yaml")


def test_example_needs_separate_mechanical_lineups(example):
    n, hall, elec, hr = run_engines(example, CAT)
    assert hr.mech_mode == "separate" and elec.n_mech_lineups == 6 and elec.n_mech_duty == 5
    assert elec.n_gensets == 16 + 6 and elec.n_transformers == 16 + 6 and elec.single_source_ok
    assert elec.lineup_need_kva == pytest.approx(2000 / 0.97, abs=1)   # IT lineups carry the UPS only
    assert hr.peak.pue == pytest.approx(1.48, abs=0.005)
    assert elec.site_demand_mva == pytest.approx(hr.peak.facility_kw / 0.95 / 1000)


def test_high_eer_chiller_keeps_mechanical_on_the_it_lineups(example):
    import copy
    from dcgen.catalogue import Catalogue
    cat = Catalogue(copy.deepcopy(dict(CAT)))
    cat["heat_rejection"]["hi_eer_1900"] = dict(cat["heat_rejection"]["acc_fc_1400"], label="High-EER 1.9 MW",
                                                capacity_kw=1900, rated_ambient_c=41, max_ambient_c=48,
                                                eer_curve={"ambient_c": [35, 41, 45], "eer": [7.0, 6.2, 5.5]})
    b = example.model_copy(update={"hr_unit": "hi_eer_1900"})
    n, hall, elec, hr = run_engines(b, cat)
    # 16 units = 4 per hall on 4 lineups: one 1,900/6.2 = 306 kW unit per lineup + share of fans and pumps
    assert hr.n_total == 16 and hr.k_per_lineup == 1 and hr.mech_mode == "shared"
    assert hr.per_lineup_kw <= CAT["electrical"]["mech_kw_per_lineup"]
    assert elec.n_mech_lineups == 0 and elec.n_gensets == 16


def test_forcing_shared_when_it_does_not_fit_fails_the_check(example):
    from dcgen.layout import generate_options
    from dcgen.io import load_site
    b = example.model_copy(update={"mech_lineups": "shared"})
    n, hall, elec, hr = run_engines(b, CAT)
    assert hr.mech_mode == "shared" and not hr.shared_ok and "> 600 kW" in hr.shared_why
    sc = generate_options(b, load_site(EX / "site.yaml"), max_options=1)[0]
    sev = {c.id: c.severity for c in sc.checks}
    assert sev["elec.mech_lineup"] == "fail" and sev["cooling.lineup_loss"] == "warn"


def test_genset_limit_and_noise_target(example):
    e = design_electrical(example, with_overrides(CAT, {"electrical.genset_max_kva": 2500}), 4,
                          {"mode": "shared", "per_lineup_kw": 600, "sizing_kw": 0})
    assert not e.single_source_ok and e.gensets_per_block == 2               # 2,062 + 667 kVA > 2,500 kVA
    q = design_electrical(example.model_copy(update={"genset_lp_1m_dba": 85}), CAT, 4,
                          {"mode": "separate", "per_lineup_kw": 0, "sizing_kw": 5000})
    g = q.genset
    assert g["lw_dba"] == pytest.approx(lw_from_lp1m(85, g["length"], g["width"], g["height"]))
    assert lw_from_lp1m(75, 18.5, 4.0, 5.5) == pytest.approx(101.7, abs=0.05)   # 75 dB(A) @ 1 m ~ 102 dB(A) Lw


def test_ups_caps_the_block_and_design_pue_override(example):
    b = example.model_copy(update={"topology": "2N"})
    n, hall, elec, hr = run_engines(b, CAT)
    assert elec.plan.block_kw <= CAT["electrical"]["ups_kva_per_lineup"] and any("UPS per lineup" in x for x in elec.notes)
    n, hall, e2, hr2 = run_engines(example.model_copy(update={"design_pue": 1.30}), CAT)
    assert e2.site_demand_mva == pytest.approx(24000 * 1.30 / 0.95 / 1000)


# --------------------------------------------------------------- plant data: workbook import
def _workbook(path: Path) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ACC"
    ws.append([])                                                           # data starts at B2 like the real sheet
    ws.append([None, "load (kW)", "Refrigerant", "Compressor type", "Manufacturer", "Length", "Width", "Height",
               "Weight", "Ambient Tempersture", "Link"])
    ws.append([None, 1900, "R1234ze", "Screw", "Synthco", "15530/16720/17910", 2261, 2324, 17364, 42, "1900 kW - std"])
    ws.append([None, 1900, "R1234ze", "Screw", "Synthco", 15530, 2261, 2324, 17364, 42, "1900 kW - low noise"])
    ws.append([None, 1900, "R1234ze", "Screw", "Synthco", 15530, 2261, 2324, 17364, 42, "1900 kW - low noise"])
    ws.append([None, 1300, "R1234ze", "Turbocor", "Synthkor", "11800 + 1250", 2260, "2532 - 2700", None, 37, "x"])
    ws.append([None, 1650, "R513a", "Screw", "Stultz", 5160, 2200, 2450, None, 42, "module length?"])
    p = path / "qc.xlsx"
    wb.save(p)
    return p


def test_quick_comparison_mapping_imports_drafts_with_assumptions(tmp_path):
    from dcgen.library.importers import import_file, load_mapping
    from dcgen.library.store import Library
    rep = import_file(_workbook(tmp_path), None, tmp_path / "pack", load_mapping(PACK / "mappings" / "qc_acc.yaml"))
    s = rep.summary()
    assert s["rows_read"] == 5 and s["drafts_added"] == 4 and any("duplicate" in x for x in s["skipped"])
    recs = Library([tmp_path / "pack"]).records["chiller"]
    a = recs["QC-ACC-SYNTHCO-1900-42"]
    assert a["length"] == pytest.approx(17.91) and a["status"] == "draft"          # longest of the length options
    assert "lw_dba" in a["assumed"] and "eer" in a["assumed"] and a["compressor"] == "Screw"
    assert "QC-ACC-SYNTHCO-1900-42-2" in recs                                     # low-noise variant kept apart
    t = recs["QC-ACC-SYNTHKOR-1300-37"]
    assert t["length"] == pytest.approx(13.05) and t["height"] == pytest.approx(2.7)   # "a + b" sums, ranges take max
    assert "QC-ACC-STULZ-1650-42" in recs                                         # typo cleaned by the mapping
    assert any("kW per metre" in p for p in s["problems"])                        # 5.16 m for 1.65 MW flagged
