---
name: iterate-test-fit
description: Improve an existing dcgen test fit from its out/summary.md - clear failing checks, try options such as CHW temperature, topology or heat rejection location, and compare runs. Use when someone asks why a check fails, what to change, or to try a variant of a dcgen project.
---
# Iterate on a test fit

## Loop
1. Read `projects/<name>/out/summary.md` (and `out/summary.json` if you need exact values). If it is missing or older
   than `brief.yaml`, run `python -m dcgen.cli project run projects/<name>` first.
2. Pick one to three changes. Each one must come from a FAIL/WARN check and its listed levers, or from the user's request.
   Explain each in one line. Typical levers:
   - chillers cannot share the IT lineups (`elec.mech_lineup`): raise `chw_flow_c`, use `topology: 5M4`, force a
     higher-EER `hr_unit`, or accept `mech_lineups: separate`;
   - peak PUE over target (`pue.peak`): `chw_flow_c`, a higher-EER chiller, `cooling: hybrid`;
   - noise (`noise.*`): `hr_location: ground` or `roof`, `hr_prefer: noise`, screens (`roof_screen_db`, `yard_barrier_db`);
   - escape distances (`egress.*`): `overrides: {egress.plant_room_exits: true}` or fewer electrical room levels;
   - does not fit: `storeys: 2`, smaller `boundary_setback_m`, fewer halls.
3. Edit `brief.yaml` (or `site.yaml`), add a line to the project README saying what changed and why, then run
   `python -m dcgen.cli project check projects/<name>` and `python -m dcgen.cli project run projects/<name>`.
4. Read the summary's "Changes since the last run" table and report the effect of each change in numbers.
5. Stop when the checks pass or the user is happy. Revert a change that made things worse (Git keeps the history).

## Do not
- Edit `dcgen/data/catalogue.yaml`, the CSV files in `packs/`, or engine code to make a check pass.
- Raise placeholder limits in `overrides` (escape distances, noise limits, fuel separation) without saying that it
  needs the fire engineer's, acoustician's or client's agreement.
- Quote numbers that are not in the summary.
