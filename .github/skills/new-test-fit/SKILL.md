---
name: new-test-fit
description: Turn a client brief (pasted text, email, meeting notes or a document) into a dcgen project folder with brief.yaml and site.yaml, then check and run it. Use when someone asks to start a test fit, set up a dcgen project, or write the brief or site YAML.
---
# New test fit from a client brief

dcgen does all the engineering. You only translate the brief into its YAML, run it and report back. Do not
calculate plant sizes, PUE or noise yourself.

## 1. Read the brief and list the inputs
Pull out these values and show them as a short table with the source of each (quote the brief, or say "default"):
- IT load (MW), MW per hall, rack density (kW), storeys
- cooling (`air_chw`, `liquid_dtc`, `hybrid`), CHW flow temperature (`chw_flow_c`, 20-26 °C typical)
- electrical topology (`4M3`, `5M4`, `N+1 catcher`, `2N`), fuel autonomy (hours)
- design ambient dry bulb (`ambient_design_c`), recirculation uplift (`hr_uplift_k`, 5 K unless CFD says otherwise)
- heat rejection redundancy and location, noise limits, client profile, peak PUE target
- site: redline (coordinates or a DXF/GeoJSON/KML file), main gate, grid connection side, true north, receptors

If the IT load, the topology or the site boundary is missing, ask once before going on. For anything else use the
default and say so.

## 2. Create the project
```
python -m dcgen.cli project new <short-name>
```
Edit `projects/<short-name>/brief.yaml` and `site.yaml`. Keep the first comment line (it links the schema). Use only keys
from `schemas/brief.schema.json` and `schemas/site.schema.json`. A redline file goes next to `site.yaml` with
`redline_file: <file>`. Write the assumptions into `projects/<short-name>/README.md`.

## 3. Check, then run
```
python -m dcgen.cli project check projects/<short-name>
python -m dcgen.cli project run projects/<short-name>
```
Fix every `error` line from the check (each names the file, line and key) and check again. Info lines tell you the plant
the engine picked.

## 4. Report
Read `projects/<short-name>/out/summary.md` and give the user:
- the best option and its arrangement;
- peak PUE (design point) with the air-on temperature and CHW flow;
- the chillers and whether they share the IT lineups or need separate mechanical lineups (and why);
- gensets, transformers, intake, site coverage and noise margins;
- every FAIL or WARN check, each with one concrete lever from the summary's "Levers to try next".

Say that catalogue values are placeholders until the team signs them off, that noise is a screening estimate, and that
peak PUE is not an annual PUE. Offer to iterate (skill `iterate-test-fit`) or to produce the deliverables
(`--formats json,pdf,ifc,dxf,glb,editor`). Suggest committing `brief.yaml`, `site.yaml`, `README.md` and
`out/summary.md` so colleagues can pull the project.
