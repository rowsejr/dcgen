# Cundall plant pack (DRAFT records)

Everything here is **draft**. The engines only use these records when a brief sets `use_drafts: true`, and every
output then carries a `data.status` warning. An engineer approves records after checking them against the source
selections (`dcgen library approve ID,... --checked-by "Name"`). Agents cannot approve.

Use the pack with `DCGEN_PACKS=packs/cundall` (or `--packs packs/cundall`).

| File | Records | Source |
|---|---|---|
| `plant/chillers.csv` | 55 air-cooled chillers (one duplicate row skipped) | Quick comparison workbook, ACC sheet |
| `plant/water_cooled_chillers.csv` | 4 water-cooled chillers | Quick comparison workbook, WCC sheet (kept for later: no water-cooled plant rooms in the layout engine yet) |
| `plant/dry_coolers.csv` | 22 dry coolers | Quick comparison workbook, DAC sheet |
| `plant/gensets.csv` | 8 gensets | Project benchmarks (electrical OneNote dimensions page, specs, tender summaries, strategy reports), client and project names left out |

## Regenerate the chillers and coolers

```bash
dcgen library import Quick_comparison_information.xlsx --pack packs/cundall/plant --mapping packs/cundall/mappings/
```
One mapping per sheet in `mappings/` (type, sheet, column map, unit conversion, value clean-up, derived estimates).
Imports never overwrite an existing record id. Delete the CSV first to re-import from scratch.

## What the workbook does not give (filled in, flagged as `assumed` on every output)

- **EER / input power**: none. Peak PUE and each chiller's input use the generic EER curve for its compressor type
  (catalogue `peak_pue.chiller_eer`, shaped on four 1,750 kW vendor selections). Add `eer_rated` or
  `eer_curve_ambient_c` + `eer_curve` from the selection outputs to replace it; that matters for the 600 kW lineup limit.
- **CHW temperatures**: not recorded per selection (20-26 degC flow typical). The brief's `chw_flow_c` is used.
- **Noise**: `lw_dba` estimated as 92 dB(A) + 10 log10(capacity / 1,400 kW) for chillers and 88 + 10 log10(capacity /
  1,100 kW) for dry coolers. "With noise treatment" variants are not distinguished.
- **Clearances** 3.0 m intake / 3.5 m row, **max ambient** rated + 5 K, **derate** 1.2 %/K, **free cooling** true.
- **Weight**: the sheet's "Weight" column (dry or operating not stated) is taken as operating weight.

## Data issues found on import (check against the selections)

- `QC-ACC-CARRIER-2169-42-9`: length given as options 15530/16720/17910 mm (longest used), no width or height. Invalid until completed.
- `QC-ACC-CARRIER-684-36`, `QC-ACC-CARRIER-670-42`: no dimensions. Invalid until completed.
- `QC-ACC-AIREDALE-1440-37-3`: width 220 mm (probably 2,200). Invalid until corrected.
- `QC-ACC-AERMEC-1656-9-42`: length 5,160 mm for 1,657 kW (321 kW/m, typical 80-170): probably a module length. Invalid until corrected.
- `QC-ACC-AIREDALE-1600-48-5`: length and weight both 20,165 (copy-paste?).
- `QC-ACC-UNIFLAIR-1600-37`: load 1,600 kW but the label says 1,900 kW.
- DAC sheet: several link labels are copied from other rows (not carried into notes). The tall BAC/Evapco 2.4-2.5 MW
  units look like adiabatic/hybrid coolers: move them to `adiabatic_coolers.csv` if so.
- The workbook's SharePoint links are not stored here.

## Gensets

Rule of thumb from the benchmarks: a 2.8-3.2 MW LV set at 75 dB(A) @ 1 m needs about 18-19 m x 4 m x 5.5 m including
attenuators and SCR, 70-100 t depending on the belly tank. Noise is stored as `lp_1m_dba`; sound power is derived from
it and the dimensions. The project limit on set size is `electrical.genset_max_kva` in the catalogue (3,200 kVA for the
single-source Kohler framework; 3,500 kVA where Cummins C3500D5e / CAT sets are allowed).

Check Cundall policy before keeping project or vendor data in this repository.
