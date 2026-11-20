# dcgen architecture

Agentic RIBA Stage 2 data centre test-fit generator: client brief + planning redline in,
ranked layout options, A3 PDF (plans + 3D views), IFC/DXF/GLB and an editable scene out.

---

## 1. Principles

1. **Deterministic engine, agentic shell.** The LLM never computes geometry or plant
   numbers. It reads the brief, picks parameters, calls tools, reads checks, proposes
   changes and explains trade-offs. Every number comes from code + catalogue, so results
   are repeatable and auditable (important for PI / QA sign-off).
2. **One scene document is the source of truth.** `scene.json` (pydantic `Scene`) holds
   inputs, elements, arrays, designs, metrics, checks and an engine log. Engines write it;
   the drag editor, the agent and Revit round-trips *patch* it; exporters only read it.
3. **Engineers own the knowledge, not the code.** Plant sizes, clearances, sound power,
   derating, room allowances live in `dcgen/data/catalogue.yaml`, versioned and signed off.
   Code reads it; nothing is hard-coded.
4. **Same functions, three faces.** Python API (scripts, tests) -> MCP tools (agent) ->
   HTTP/UI (editor). No logic lives in the faces.
5. **Stage 2 fidelity.** Massing, plant zones and arrays (LOD ~200), with explicit
   assumptions. Detailed design stays in Revit.

## 2. Layers

```
 Client brief (email/PDF/Excel) ─┐
 Redline (DXF / KML / GeoJSON) ──┤
                                 v
 ┌──────────── Agent (Claude: Desktop / Code / claude.ai connector / own Agent SDK app) ──┐
 │  reads brief -> calls tools -> reads checks -> iterates -> explains -> exports         │
 └───────────────▲──────────────────────────────────────────────────────────────────────┘
                 │ MCP (stdio locally, Streamable HTTP when hosted)
 ┌───────────────┴───────── dcgen MCP server (mcp_server.py) ────────────────────────────┐
 │ create_project · set_site · update_brief · generate_options · select_option           │
 │ compare_topologies · move_element · get_scene · noise_at · export · open_editor (App) │
 └───────────────▲───────────────────────────────────────────────▲──────────────────────┘
                 │ Python API                                    │ ui:// MCP App / HTML
 ┌───────────────┴──────── core (pure Python) ───────────┐   ┌───┴──────── drag editor ──┐
 │ schema.py   Scene / Brief / SiteInput / Element        │   │ SVG plan, arrays drag as   │
 │ catalogue   YAML module library (engineer-owned)       │   │ groups, live checks (JS),  │
 │ engines/    halls · topology · electrical · cooling ·  │   │ authoritative re-check via │
 │             noise · core assembly · site placement     │   │ move_element (MCP)         │
 │ layout.py   orchestration · option search · scoring    │   └────────────────────────────┘
 │ checks.py   rule checks (pass / warn / fail / info)    │
 └───────────────┬───────────────────────────────────────┘
                 v
 render/ (PDF: plans, vector 3D axonometrics, noise map, schedules)
 export/ (IFC4 · DXF · GLB · editor HTML) ──> Revit (link IFC / pyRevit / C# add-in / APS)
                                         ──> Navisworks (append IFC/DXF, clash vs survey)
```

## 3. Coordinates

* **Site coordinates**: whatever the redline came in (project grid, OSGB36 metres...).
  KML / lon-lat GeoJSON is projected locally (swap in pyproj for EPSG:27700).
* **Local layout frame**: origin at the redline centroid, x along the building's long axis.
  Engines work here so everything is axis-aligned and fast. `Frame` maps local <-> site.
* Exports write **site orientation**. With `site.crs` set, IFC is shifted near the origin
  and an `IfcMapConversion` carries eastings/northings (Revit dislikes OSGB-sized coords).
* Plans are drawn **north up** (`true_north_deg` = bearing of site +y from true north).

## 4. Engines (all indicative; replace the models as the team sees fit)

| Engine | In | Out | Model |
|---|---|---|---|
| `halls` | IT/hall, rack kW, rack size, cooling | hall W x D, rows, pods, galleries | rack rows across the building, contained hot aisles, fan-wall/CDU galleries at row ends |
| `topology` | `4M3`, `5M4`, `N+1 catcher`, `2N` | blocks, groups, block kW, loading | xMy: block = group load / y, x blocks per group, normal loading y/x; catcher: k primaries + 1 catcher (STS); 2N: A+B |
| `electrical` | block plan, design PUE | gensets, TX, rooms, STS/MV, fuel, intake | genset/TX sized for block IT x design PUE; room width = base + k x MW; fuel = site kW x L/kWh x hours |
| `cooling` | heat load, design ambient, water temps | unit type, capacity at ambient, duty + standby | chiller: linear derate/K; dry cooler: (T_fluid - T_amb) scaling; adiabatic: wet-bulb depression |
| `noise` | HR units (Lw), receptors | Lp at receptors, map | Lp = Lw - 20 log r - 8 - screening (roof, barrier, building); sieve only, then ISO 9613-2 / CadnaA |
| `core` | the above | building + yards in canonical frame | support end, halls row, corridor, electrical band (1-3 levels), TX + gensets out one side, heat rejection roof/yard other side |
| `site` | redline, setback, access, utility | placed core, roads, gate, guardhouse, ancillaries | vectorised candidate search inside the free area, scored per item (substation to utility + elec rooms, fuel near gen yard with separation, sprinkler near building, parking near entrance), connector roads |
| `layout` | brief, site | ranked options | 2 orientations x 2 support ends x 2 electrical sides x 2 HR locations x 5 positions; score = fails, warns, noise exceedance, cable + road proxies |

Checks today: inside redline / setback, 3D clashes, roads through plant, ancillary road
access, facade fire access (placeholder), fuel separation (placeholder), receptor and
boundary noise, heat rejection capacity, rack count, roof plant load warning.

## 5. Agent layer

* **MCP server** (`dcgen-mcp`). stdio for Claude Desktop / Claude Code on an engineer's PC;
  Streamable HTTP (`--http`) when hosted internally, added in claude.ai as a custom connector
  (put it behind SSO).
* **MCP App** (`open_editor`, `ui://dcgen/editor.html`): the drag editor renders *inside the
  chat*. Each drag calls `move_element`, so the model sees what the engineer changed and the
  server re-runs the checks. The full scene goes to the view through an app-only tool
  (`editor_scene`), keeping the model's context small.
* **Prompt** `test_fit_from_brief`: turns a pasted brief into a run plan.
* Typical loop: "36 MW, 6 halls, 5M4, liquid cooled, site attached" -> create_project ->
  set_site -> generate_options -> "NSR2 fails by 2 dB" -> agent tries `hr_location: roof` +
  `roof_screen_db: 8`, or moves the HR array -> re-checks -> export PDF + IFC.
* Later: the Claude Agent SDK can host the same tools in-process behind your own web app.

## 6. Drag and drop

Arrays (gensets, transformers, roof/yard heat rejection) and multi-part items (sprinkler
tanks + pump house, fuel bund + tanks) share a `group`. Dragging moves the whole group by
default, or one member with the toggle off. Moved elements are `locked` and recorded in
`scene.variant.overrides` so a regenerate can respect them. The browser runs the same
clash / redline / fuel / noise maths for instant feedback; the server result is authoritative.

## 7. Outputs

* **PDF (A3, vector)**: option study, 3D overview, site plan to scale, 3D close-up,
  electrical strategy (topology diagram + comparison), heat rejection + noise map, schedules +
  checks. 3D views come from `render/axo.py`: prisms, painter's ordering by separating
  axis + topological sort, Lambert shading, projected shadows (ground and roof). Pure vector,
  no GPU, crisp at any zoom.
  * Photoreal option later: render the GLB in Blender headless (`bpy`, Cycles) or from Revit.
  * Avoid interactive 3D PDF (U3D/PRC): it only works properly in Adobe Acrobat/Reader on
    desktop. Send a GLB / web viewer link instead.
* **IFC4**: IfcSpace for halls/rooms, IfcElectricGenerator, IfcTransformer, IfcChiller,
  IfcTank, IfcCivilElement (roads), proxies for compounds; colours; `Pset_DCGen` on everything.
* **DXF**: layered plan in site coordinates, polylines with thickness = height.
* **GLB**: Y-up, centred on the site, for web viewers / Blender / PowerPoint 3D.

## 8. Revit and Navisworks

| Route | What you get | Effort | When |
|---|---|---|---|
| Link the IFC | Massing + plant in Revit / Navisworks, with Pset_DCGen data; not native families | none | now |
| pyRevit button (`revit/pyrevit`) | Native family instances from `family_map.json`, DirectShape massing for volumes, dcgen ids in Comments | days | next |
| C# add-in | Same as pyRevit, production-grade, round-trip (export moved positions back as a patch) | weeks | phase 2 |
| APS Automation API (Revit in the cloud) | Run that C# add-in headless to produce an RVT from the web app, no local Revit | weeks + metered cloud cost | phase 3 |
| Revit MCP | Claude drives a running Revit. Autodesk's official server (Revit 2027, tech preview) is read-only today; community servers add write tools | low, but local Windows + Revit | try now for QA / queries |
| Speckle | Hub between Python, Revit, Rhino, Navisworks; Revit loads as DirectShapes by default; Navisworks connector is publish-only | days | if the team already uses it |
| Navisworks | Append IFC/DXF, federate with survey/utilities/architect, clash detect; community Navisworks MCP servers exist | none | now |

## 9. Roadmap

* **Phase 0 (this scaffold)**: engines, options, checks, PDF/IFC/DXF/GLB, MCP server + App, tests.
* **Phase 1**: team-owned catalogue v1 (real modules, sources), weather files (CIBSE TRY/DSY or
  ASHRAE) for free-cooling hours and annualised PUE, phasing (build halls in stages),
  multi-storey + stacked gensets, second access/emergency exit, swept-path checks.
* **Phase 2**: optimiser (NSGA-II via pymoo or CP-SAT for packing), cost (GBP/MW) and embodied
  carbon, C# Revit add-in with round-trip, ISO 9613-2 noise, hosted HTTP server with SSO.
* **Phase 3**: APS Automation for cloud RVT, portfolio runs (many sites x briefs), learned
  priors from past Cundall schemes.

## 10. QA

* Unit tests for each engine (e.g. 4M3 partners never exceed 100% after a block failure).
* Golden layouts: fixed brief + site -> expected metrics, so changes are visible in review.
* Every exported number traces to a catalogue entry + version; sheets carry "indicative" status.
* Engineer sign-off is required for catalogue changes; agents cannot edit the catalogue.

## 11. Build vs buy

TestFit for Data Center already does real-estate-led generative feasibility (MW targets,
power density, design PUE, yard depths, wind orientation). Build where you are different:
topology-driven electrical plant, heat rejection vs ambient, noise, practice standards and a
clean BIM handover. Check whether TestFit outputs can feed `set_site` / the brief rather than
competing on site massing.
