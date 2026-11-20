# example - test fit summary

Run 2026-11-20 16:45:23 with dcgen 0.1.0 (no AI involved). Inputs: `brief.yaml`, `site.yaml`.

Best of 16 arrangements: **option A**, support W end, electrical/gen yard N, heat rejection on roof.

## Result

| Item | Value |
|---|---|
| Peak PUE (design point) | 1.48 at 41 °C air-on, CHW 20 °C |
| Heat rejection | 23 x Air-cooled free-cooling chiller ~1.4 MW (22 duty), EER 3.368 (generic), 385.7 kW input each |
| Mechanical lineups | separate |
| Electrical | 4M3, 16 x 2,000 kW blocks, 22 x 2,500 kVA genset (2.0 MW standby), 22 transformers, 33 kV intake |
| Site | 7.92 ha, coverage 10.7 %, spare 4.1 ha |
| Noise margins (dB) | NSR1 Farmhouse +0.6, NSR2 Village edge +0.9 |
| Checks | 0 fail, 2 warn |

## Checks needing attention

- **WARN** `egress.plant_rooms`: Electrical rooms: worst (EB07) to a final exit: 66 m vs 45 m (placeholder limit) - consider direct plant-room exits (egress.plant_room_exits)
- **WARN** `roof.load`: 23 roof units at ~4.6 kN/m2 local load - confirm with structures

## Options

| Opt | Arrangement | Fails | Warns | Noise margins dB | Spare ha | Score |
|---|---|---|---|---|---|---|
| A | support W end, electrical/gen yard N, heat rejection on roof | 0 | 2 | +0.6, +0.9 | 4.1 | 175.2 |
| B | support W end, electrical/gen yard N, heat rejection on ground | 0 | 2 | +3.7, +5.8 | 3.46 | 181.6 |
| C | support E end, electrical/gen yard N, heat rejection on roof | 0 | 2 | +1.2, +0.8 | 3.85 | 192.6 |
| D | support E end, electrical/gen yard N, heat rejection on ground | 0 | 2 | +4.0, +4.6 | 3.57 | 197.2 |

## Levers to try next

- `egress.plant_rooms`: overrides: egress.plant_room_exits=true (adds south exits to ground plant rooms); storeys / elec levels: fewer upper levels shortens vertical escape routes; overrides: egress.travel_two_dir_m only with fire engineer sign-off
- `roof.load`: hr_location=ground (heat rejection in the yard); confirm the roof design load with structures

## Plant data used

- genset: gen_2500 (placeholder)
- transformer: tx_2500 (placeholder)
- heat rejection: acc_fc_1400 (placeholder)

## Files

- `out/testfit_option_A.json`

_Indicative RIBA Stage 2 test fit. Catalogue values are placeholders until the team signs them off; noise is a screening estimate; peak PUE is a design-point figure for plant sizing (annual PUE: the PUE tool)._
