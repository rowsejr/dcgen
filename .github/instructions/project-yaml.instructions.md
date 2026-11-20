---
name: dcgen project YAML
description: How to write dcgen brief.yaml and site.yaml files
applyTo: "projects/**/*.yaml,examples/*.yaml"
---
# Writing brief.yaml and site.yaml

The schemas in `schemas/` hold every key, its default and its meaning; VS Code shows them on hover. The keys used most:

**brief.yaml**
| Key | Meaning | Typical |
|---|---|---|
| `it_load_mw`, `hall_it_mw` | building IT load and IT per hall (halls = ratio, rounded up) | 24 / 6 |
| `rack_kw`, `storeys` | average rack load; 2 storeys stacks the halls | 10-50 / 1-2 |
| `cooling` | `air_chw`, `liquid_dtc` or `hybrid` | air_chw |
| `topology` | `4M3`, `5M4`, `N+1 catcher`, `2N` | 4M3 |
| `ambient_design_c` | design dry bulb (e.g. N=20 extreme + climate allowance) | 35-40 UK/EU |
| `hr_uplift_k` | air-on uplift for recirculation in chiller arrays | 5 (2 with CFD evidence) |
| `chw_flow_c` | CHW flow temperature: higher raises chiller EER and lowers peak PUE | 20-26 |
| `mech_lineups` | `auto` (shared IT lineups if the chillers fit 600 kW each), `shared`, `separate` | auto |
| `hr_redundancy`, `hr_location` | `N+1` / `N+2` / `N+20%`; `auto`, `roof`, `ground` | N+1 / auto |
| `fuel_autonomy_h` | fuel stored at peak load | 24-72 |
| `pue_target_peak` | client's peak PUE target, checked | e.g. 1.45 |
| `profile`, `use_drafts` | client profile id; allow draft plant records | |
| `hr_unit`, `genset_unit`, `tx_unit` | force a plant record id (see `python -m dcgen.cli library search`) | |
| `overrides` | dotted catalogue paths for this project only | `{halls.corridor_depth: 5.0}` |

**site.yaml**
- `redline`: list of `[x, y]` points in metres, in order round the boundary (or `redline_file: redline.dxf`).
- `access_points`: points on the boundary. The first one is the main gate.
- `utility_point`: direction of the grid connection (the substation goes towards it).
- `true_north_deg`: bearing of the site's +y axis from true north, degrees clockwise.
- `receptors`: `{name, x, y, limit_dba}` for each noise-sensitive receptor, outside the redline.
- `boundary_setback_m`: no buildings or plant within this distance of the redline.

After editing, run `python -m dcgen.cli project check <folder>` and fix every error line (each one names the file, line and key).
