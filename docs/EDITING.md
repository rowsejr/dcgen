# Where to edit what

You (an engineer) can change almost everything without touching Python. Pick the lowest rung that works.

| I want to... | Edit | Who signs off | Test |
|---|---|---|---|
| Try a different clearance, corridor width or door count for **one project** | `brief.overrides` (dotted catalogue paths), e.g. `halls.corridor_depth: 5.0` | You (it is a what-if) | none |
| Change a default for **everyone** | `dcgen/data/catalogue.yaml` | Engineer sign-off | `pytest tests/test_engines.py`; golden metrics change only on purpose |
| Add or update a **chiller / cooler / genset / transformer / electrical room size** | Plant library pack (CSV), see `docs/LIBRARY.md` | Approver (`checked_by`) | `dcgen library validate` |
| Apply a **client's standard** | `clients/<id>/profile.yaml` in a pack | Project lead | `dcgen profile ...` |
| Record a **past scheme** to benchmark against | `reference/<id>.yaml` in a pack | Project lead | `dcgen reference regress` |
| Change how things are **placed** | `dcgen/engines/core.py` (building core, plant rows), `engines/site.py` (roads, ancillaries) | Code review | `tests/test_layout_rules.py` |
| Change a **rule check** | `dcgen/checks.py` | Code review | add a test |
| Change **scoring** weights | `layout.score` | Team | none, but expect golden changes |

Rules that stay fixed (see CLAUDE.md): engines are pure; plant numbers live only in the catalogue or library; `Scene` is the
single source of truth; new behaviour goes in `service.py` or below, never in the front doors.

## How things are sized (so you know which knob moves what)

**Data hall.** `halls.py` takes hall IT MW and rack kW to a rack count, pods and aisles from `halls.*` catalogue keys, then
rounds width and depth **up** to `halls.round_to` (0.6 m module). Change `hall_it_mw`, `rack_kw`, `halls.round_to`.

**Halls in a row.** Separated by a `halls.fire_wall` (0.4 m) wall, or a `halls.cross_corridor_width` cross corridor every
`halls.cross_corridor_every` halls (default 2).

**Heat rejection quantity.** `cooling.select_heat_rejection`: heat load = IT load x heat factor; capacity of each unit
comes from its derate (linear), 1-D curve or 2-D curve (fluid-in and ambient, bilinear) at the design ambient (adiabatic units
read the pre-cooled ambient); duty = ceil(load / capacity); standby from `hr_redundancy` (N+1, N+2...). All workable units of
the first suitable type are ranked on footprint / noise / count with `hr_prefer` (balanced, footprint, noise, count);
`hr_unit` forces a record. Runners-up are in the notes.

**Placement.** Units stand **perpendicular to the facade**, one group per hall column, with a lane (`egress.hr_lane_width`)
at each fire wall. Ground or roof is chosen per column (`hr_location`). Clearances: `intake_clear` (side, for air intake) and
`row_clear` (end to end).

**Gensets / transformers.** `Catalogue.pick_best`: fewest units, then smallest footprint, then quietest. `genset_unit` and
`tx_unit` force a record.

**Padding / clearances.** Every gap is a catalogue key: `layout.*` (plant to building, tiers), `site.clearances.*` (roads,
guardhouse, ancillaries), maintenance envelopes per element (`clear_xy`, checked by `maint.zones`).

**Fire escapes, corridors.** `egress.*` block. Hall doors per wall, final exits at gallery ends and the support block, a stair
core when storeys > 1, travel-distance checks (hall interior, hall to exit, plant room to exit) and a 1.5 x 12 m clear lane
outside every exit. **All limits are placeholders until the fire strategy sets them.** Not yet modelled: vertical escape from
upper-level plant rooms (the default example correctly WARNs on this).

## Driving it as an agent (or by hand)

`preview` (look first) -> `suggest_fixes` -> `what_if` (try 1-6 changes without altering the project) -> `update_brief` +
`regenerate` (keeps anything you moved) -> `undo` if worse. `move_compass` ("fuel store 10 m north"), `move_towards`,
`move_element`. Everything settable is listed in the `dcgen://brief-schema` resource (or `GET /api/schema`).
Agents can never approve library records.
