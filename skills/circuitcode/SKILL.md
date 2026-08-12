---
name: circuitcode
description: Use when the user wants to create, edit, or fabricate a PCB from a natural-language description — "design me a macropad", "an ESP32 soil-moisture board", "a desk air monitor", "add a second button", "make the board smaller", "fix the DRC error", "regenerate the gerbers", "how much to get 5 made" — or to tweak, re-check, or fix an existing board `.tsx` in an Autonomous Circuit board project (product.json + boards/).
---

# circuitcode — real boards from board programs

## Purpose

A board is **code**: `boards/main.tsx` composes golden blocks into a device,
and the pipeline compiles it to a Circuit JSON, a schematic, a PCB layout, and
a fab packet a manufacturer will actually build. The source is the truth. The
user owns the project; you write the program, run the gauntlet, look at the
pictures, and fix what is wrong.

What comes out the far end is not a design file. It is a zip a person uploads
to JLCPCB, plus a walkthrough, plus a 3D body for the printed enclosure — five
assembled boards for about $75–110, in their hands in one to two weeks.

The reason this can work at all is the block library. **You compose validated
subcircuits; you never invent a circuit from a datasheet.** That is not a
style preference — see *Non-negotiables*.

## Make it buildable and repairable — by default

Four habits, applied without being asked:

1. **Compose, don't invent.** Every IC comes in through a block from
   `circuitlib.blocks`. Glue between blocks (a resistor, a capacitor, an LED, a
   header, a connector) is yours to place. A chip that is not in a block does
   not go on the board — say so and offer the nearest block.
2. **Decouple everything, always.** One 100nF beside every IC power pin, one
   bulk cap per rail. `circuitlib.helpers.decoupling_for()` tells you the count.
   Blocks bring their own; glue ICs do not exist, so this mostly means: don't
   strip a block's caps to save space.
3. **Label every net.** `net.V3_3`, `net.I2C_SDA` — never an anonymous trace.
   An unnamed net is a warning from the pipeline and an unreadable schematic
   for the human who has to debug the board.
4. **Leave the enclosure something to hold.** At least two mounting holes on a
   pitch you state, connectors on one edge, and a board outline inside the
   declared envelope. This board is going inside a 3D-printed body.

## Treat the device as a project

```
<project>/
├── product.json          the bible: device, layout and source/current budget
├── parts.json            the locked BOM — parts-book owns it; you never write it
├── blocks/               the golden-block library, frozen with this project
├── golden-blocks.lock.json  exact selected block bytes + provenance hash
├── boards/
│   ├── main.tsx          your file — the only file you write
│   └── main.circuit.json + main.board.json + main_review/ + main_fab/   generated
├── tsconfig.json, tscircuit.config.json
└── .circuit/             build cache — never edit, never read
```

### Starting a new project

Use the public generator. It resolves the protected USB block plan, measured
placement, board layout, current budget, explicit power trees and ground-plane
policy as one closed machine profile; it also content-locks every selected or
transitively imported golden block. Do not copy the static template and repair
it by hand.

```bash
SKILL=~/.claude/skills/circuitcode
python "$SKILL/scripts/create" /abs/project \
  --name my-device --description "what this device does for its owner"
```

The generated `protected-usb-indicator-v1` project is deliberately narrow and
fail-closed. `product.json` contains its exact non-empty layout, source/current
budget and schematic policy; `boards/main.tsx` consumes the same planner result
and typed attachment datums to compose `VBUS_RAW -> U7 -> V5 -> U2 -> V3_3`,
dual-face ground planes and scoped power vias. An unknown profile, missing
profile contract, changed planner closure or missing imported block refuses
before toolchain launch. **The lock is not optional**: it covers every selected
and relative-import dependency byte, including provenance, so the project keeps
building the same board after the shared catalog moves.

For a different supported capability set, use `board_plan` and a corresponding
machine-resolved generator; do not change only the profile label or copy profile
JSON onto unrelated source. The low-level synchronizer remains available for a
reviewed one-time migration. It refuses to overwrite a locally edited frozen
block. Select every block returned by the planner; transitive golden imports are
added to the lock automatically.

For a reviewed one-time migration of an older project that already has an
unlocked copied block tree, add `--replace-unlocked`; never use that flag for a
locked project, because a mismatched lock remains a hard refusal.

Rules of the project format:

- **Device-wide facts live in `product.json` only.** Power source, envelope,
  layer count, fab profile, physical layout intent and USB source/current
  budget. Don't restate them in the board file; read them.
- **One file per board.** Most projects have exactly one, `boards/main.tsx`.
- **Every number comes from a table.** `circuitlib.tables` owns the electrical
  law and the fab limits. Retyping `0.127` into a board file makes a number
  nobody can update.
- **Never edit generated artifacts.** `.circuit.json`, `.board.json`, the SVGs,
  the PNGs, the fab packet — all outputs. Editing one gets it overwritten on the
  next run and desynchronises the sidecar.
- **Never edit `parts.json`.** That is `parts-book`'s file. If a part is wrong
  or out of stock, hand off; don't hand-patch the lock.

### The source contract

`boards/main.tsx` default-exports a function returning one `<board>`. The
generated protected profile is the authoritative composition example; the
abridged shape below only illustrates syntax and must not replace its authored
power attachments, planes, phases or product contract:

```tsx
import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { UsbPowerEntry } from "../blocks/usb-power-entry/usb-power-entry"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"
import { GndPlanes, MountingHole } from "../blocks/glue"

export default () => (
  <board
    width="40mm" height="30mm" thickness={1.6}
    minTraceWidth="0.15mm"
    minViaPadDiameter="0.6mm"
    minViaHoleDiameter="0.3mm"
    minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
  >
    <UsbCData pcbX={0} pcbY={-9} schX={-12} schY={0} />
    <UsbPowerEntry pcbX={-9} pcbY={8} schX={-4} schY={0} />
    <Ldo3v3 pcbX={8} pcbY={8} schX={4} schY={0} />
    <GndPlanes layers={["top", "bottom"]} stitchingVias={[]} />
    <MountingHole name="H1" diameter={3.2} pcbX={-17} pcbY={-12} />
  </board>
)
```

That abridged code is intentionally not a buildable substitute for the public
starter. For a new board, generate first and modify only through typed block or
planner abstractions; a missing protector, rail tree, stitch policy or profile
contract must be treated as a design failure.

These source rules are not optional:

- **`thickness={1.6}` explicitly.** The toolchain default is 1.4mm; JLC's
  standard stackup is 1.6mm. Leave it out and you ship the wrong board.
- **Both coordinate systems on every placed thing.** `pcbX/pcbY` put it on the
  board; `schX/schY` put it on the schematic. Skip the schematic coordinates and
  you get a legible board with an unreadable drawing — and you must read that
  drawing later.
- **A pinned-dialect header comment** naming the tscircuit version the file
  targets (copy the template's). The toolchain ships roughly seven releases a
  day; the file should say what it was written against.
- **Blocks by relative import** from `../blocks/<id>/<id>`. Never an `@tsci/…`
  registry import — those are mutable, unreviewed, and fetched at build time.

## The loop

```
understand ask → inspect project → block plan → edit main.tsx
      ↑                                              ↓
      └── fix ← Read _schematic.png + _pcb.png ← read the JSON verdict ← run scripts/circuit
```

`ok: true` says the pipeline ran. Only your eyes say the board is right.

## Done means orderable

**A board is finished when `fab.ready` is `true`, and at no other time.**
Dee, 2026-08-11: *"All designs generated must be ready to be sent to JLCPCB.
Perfect, no issue, board generated one shot, printed."*

That is one gate, not a scale:

| Sidecar says | What you say |
|---|---|
| `fab.ready: true` | **Done.** Here is the packet, here is what it costs, here is how to order it. |
| `fab.ready: false` | **Not done.** One line on the single thing missing, then keep working or hand back a clear blocker. |

`ok: true`, "zero blocking warnings", "the pictures look right" and "the build
is clean" are all *inputs* to that gate. None of them is the gate. A packet
with unverified gerbers (`gerberSource: "tscircuit"`, kicad-cli absent) is
`fab.ready: false` and therefore unfinished, even with zero warnings — a user
cannot send it to a fab, so it is not a board yet.

**Aim to earn it on build #1.** Every repair round is a defect that should have
been prevented before the first build: read the BLOCK.md files, use
`circuitlib.layout.place_board()` for the outline, placement and mounting
holes instead of guessing coordinates, and `helpers.board_plan()` for the
block set. If you find yourself fixing the same class of thing twice, the real
bug is upstream — say so, because that fix belongs in the block or the library,
not in this board.

## Plan-phase design discipline

When the app runs you in Plan mode, **write no files.** Produce an engineering
spec the user can approve or redirect:

1. **What it does** — one paragraph, in the user's terms.
2. **Block plan** — the table: capability → block → why. Call
   `circuitlib.helpers.board_plan(capabilities=[...])`; report anything it
   returns in `unavailable` honestly instead of inventing a circuit for it.
   An MCU plan is not buildable until its required debug nets are physically
   brought to the board's real connector or probe furniture. Pass those nets
   as `exposed_nets=[...]` only after that composition exists; never clear the
   obligation because you intend to add it later.
3. **Power budget** — source, rail tree, the arithmetic. USB enters as
   `VBUS_RAW → UsbPowerEntry → V5 → LDO → V3_3`; sum the block draws and state
   both the physical peak and normal operating ceiling. For a USB-powered
   product, put the raw-attach capacitance, exact current-limit boundary and
   any firmware-bounded load family in `product.json.powerBudget.usb`.
   `board_plan(..., firmware_load_caps_ma={...})` refuses an uncapped load over
   the limiter's 400.6mA worst-case trip, then
   `usb_power_budget_for_plan(plan, firmware_load_matches={...})` compiles the
   exact `powerBudget` object. The caller supplies only the board's real
   refdes match pattern; it must not retype limiter identity or current
   arithmetic. Firmware limits never erase the physical peak.
4. **Pin allocation** — every MCU pin you intend to use, and for what.
5. **Size and layout intent** — board outline, assembly side for each
   functional population, what sits on which edge, ground-plane layers, power
   trunk and neck-down widths, mounting holes, and whether it fits
   `product.json`'s envelope. Write these decisions into
   `product.json.layout`; prose is not a constraint and cannot be verified.
6. **Cost band** — `circuitlib.helpers.estimate_cost()` plus the parts total
   from the lock. Quote the assembled-5x band, not a fantasy unit price.
7. **Safety verdict** — run `circuitlib.safety.safety_gate()` on the ask. If it
   refuses, the plan is the refusal and the reason. Don't design around it.

### Where the numbers come from — the tables own them

| Number you need | Where it lives |
|---|---|
| Rail voltages and tolerances | `circuitlib.tables.RAILS` |
| Ordinary board-level signal width | `tables.PREFERRED_SIGNAL_TRACE_WIDTH_MM` / `BoardPlan.signal_trace_width_mm` (0.25mm) |
| Trace width for a current | `helpers.trace_width_for(current_a=…)` (IPC-2221) |
| Power trunk, neck-down and via defaults | `tables.POWER_TRUNK_MIN_MM`, `POWER_NECKDOWN_WIDTH_MM`, `POWER_VIA_OUTER_DIAMETER_MM`, `POWER_VIA_HOLE_DIAMETER_MM` (0.8/0.2mm, 0.8/0.5mm via) |
| Conductor spacing for a voltage | `helpers.clearance_for(volts=…)` |
| Min trace/space/drill/annular/edge | `tables.MIN_*` |
| Board thickness, layer default | `tables.BOARD_THICKNESS_MM`, `tables.DEFAULT_LAYERS` |
| Decoupling counts | `helpers.decoupling_for(power_pins=…, rails=…)` |
| I2C pull-ups, USB series/CC resistors | `tables.I2C_PULLUP_OHMS`, `tables.USB_*` |
| Assembly fees, cost bands, lead time | `tables.*_USD`, `helpers.estimate_cost()`, `helpers.fab_profile()` |
| Which block does X | `blocks.block_for(capability)`, `blocks.CAPABILITY_INDEX` |
| A real, stocked glue part | `parts.pick("resistors", resistance=4700)` — JLC Basic/Preferred mirror |
| Is this part costing us a feeder fee | `parts.cheaper_basic_part("C…")` |
| Will the regulator cook | `helpers.regulator_thermal(vin=…, vout=…, current_a=…)` |
| Is this LED resistor sane | `helpers.led_current(rail_v=…, resistance_ohms=…)` |
| What a block needs fed | `blocks.BLOCKS[id].requires` / `.provides` |
| Iteration cap | `tables.MAX_REPAIR_ITERATIONS` |
| How big a block is, and where it sits | `layout.box(block_id)` -> `(min_x, min_y, max_x, max_y)` around its origin |
| **The whole board plan** | `layout.place_board([...])` -> outline + placements + holes + its own warnings |
| **The compiled layout contract** | `layout.product_layout(...)` -> `product.json.layout` (exact size, sides, edge connectors, 2mm authored decoupling loops, planes, per-drop fanout length, trunk/neck-down widths) |
| **The USB source/current contract** | `helpers.usb_power_budget_for_plan(plan, firmware_load_matches=...)` -> `product.json.powerBudget` with raw/protected nets, raw attach-cap limit, exact limiter plus ILIM resistor identity/value/topology, trip range, fixed load and firmware-limited load families |
| Where to put a row of blocks | `layout.place_row([...])` -> `{block: (pcbX, pcbY)}` (the primitive) |
| How big the board must be | `layout.min_board_for([...], columns=n)` |
| Will anything hang off the edge | `layout.board_fits(placements, w, h)` — run it *before* building |

If a number is not in a table and not in a block, it is an **estimate** — say
so out loud rather than presenting it as measured.

## Use this skill when

The user wants a board designed, changed, checked, or made orderable.

**Do not use it for:**

- A vague product idea with no spec — run **circuit-analysis** first; it returns
  a `circuit-brief` you can build from.
- Picking or pinning parts, or a stock problem — that is **parts-book**.
- Showing the user the result in the app — that is **board-viewer**.
- Enclosures, brackets, printed bodies — that is Vibe's job, not this one.

## Default assumptions

2 layers · 1.6mm · HASL · JLCPCB economy assembly, top side · qty 5 · USB-C
power · 3.3V logic · surface-mount, JLC **Basic** parts preferred (each
extended part adds a ~$3 feeder fee) · tactile switches over hot-swap sockets ·
through-hole only where a human must plug something in.

### Ask only about preferences; decide all craft silently

Ask when the answer is genuinely the user's: what the device should do, how big
it may be, which connector they want to live with, budget, battery vs wall
power, how many they want made.

Decide silently, and say what you decided: trace widths, decoupling, pull-up
values, layer count, placement, routing, refdes allocation, silkscreen, test
points, footprint choices. The test: **could a competent EE pick this without
knowing the user?** If yes, pick it and move on.

## Available tools

```bash
# Full build — the normal case. Pass the board file, absolute path.
python ~/.claude/skills/circuitcode/scripts/circuit /abs/project/boards/main.tsx

# Cheap structural check — compile + circuit-json scan + checks library only.
# No kicad, no fab export, artifacts discarded. Use before paying for a full run.
python ~/.claude/skills/circuitcode/scripts/check /abs/project/boards/main.tsx

# Review pass — re-surface warnings and regenerate the review images
# without rebuilding. Returns the PNG paths.
python ~/.claude/skills/circuitcode/scripts/review /abs/project
```

Flags: `--stem NAME` (which board, when a project has several), `--out-dir DIR`,
`--fab jlcpcb`, `--wall-clock-s S`. Each command prints **exactly one JSON line**.

## Running the loop

### 1. Understand the ask

What does the device *do* for its owner? Read any `circuit-brief` block from
circuit-analysis, any attached image. Run the safety gate before designing:
mains, bare RF, and loose battery charging are refusals, not challenges.

### 2. Inspect the project

`ls` the project. `Read` `product.json` (power, envelope, layers, layout and
power budget), `parts.json`
if present, the current `boards/main.tsx`, and the `BLOCK.md` of every block you
plan to use — the pin contract and rail budget are in there.

### 3. Block plan, then place by measurement

`board_plan(capabilities=[...])`. Check `unmet` (a net nothing provides),
`unavailable` (a capability with no block), and `must_expose` (a debug or
service net that reaches no real board connector/probe). Resolve all three and
require `plan.buildable` *before* writing code. When the board actually composes
its SWCLK/SWD probe, call `board_plan(..., exposed_nets=["SWCLK", "SWD"])`;
the list describes compiled physical intent, not a promise to add pads later.
For a high-peak USB load, also require `plan.source_budget["severity"] !=
"error"`; pass a measured `firmware_load_caps_ma` only when the product will
emit the matching load rule. Generate that rule with
`usb_power_budget_for_plan(plan, firmware_load_matches={"block-id":
"actual-refdes-pattern"})`; do not manually copy the source block's limiter
part, trip range, or the plan's fixed-load arithmetic into `product.json`.

Then size and place the board with `circuitlib.layout` rather than by eye. **A
block's copper is not centred on its `pcbX`/`pcbY`** — `usb-c-power` sits
3.29mm above its origin, `usb-c-data` 6.04mm, `rp2040-core` 5.51mm below — so
coordinates guessed from a size are wrong by millimetres, which is how parts
end up over the outline:

```python
from circuitlib import layout

plan = layout.place_board(["usb-c-power", "ldo-3v3", "status-led"])
plan["width_mm"], plan["height_mm"]   # the outline
plan["placements"]                    # {block: (pcbX, pcbY)}
plan["holes"]                         # two M3 holes, clear of every footprint
plan["warnings"]                      # MUST be [] — the plan checking itself
```

Record those decisions beside the electrical product definition. At minimum,
an assembled board declares its exact outline, GND plane layers and power
class. A double-sided product also declares ordered component-side rules and
uses `assemblyTier: "standard"`:

```python
layout.product_layout(
    board_size_mm=(plan["width_mm"], plan["height_mm"]),
    ground_plane_layers=("top", "bottom"),
    min_copper_clearance_mm=board_plan_result.preferred_clearance_mm,
    decoupling_max_distance_mm=2.0,
    # Only a measured non-load rail reference such as an ESD clamp belongs
    # here; never exclude an IC because its bypass placement is inconvenient.
    decoupling_exclude=("U_ESD*",),
    # Use only when a cited manufacturer routed reference establishes a
    # different local envelope. Authored topology and copper still gate it.
    decoupling_overrides=[{
        "match": "U3",
        "maxDistanceMm": 5.0,
        "source": "https://datasheets.raspberrypi.com/rp2040/Minimal-KiCAD.zip",
    }],
    power_trunk_width_mm=board_plan_result.power_trunk_width_mm,
    power_via_outer_diameter_mm=0.8,
    power_via_hole_diameter_mm=0.5,
    component_zones=[
        {
            "match": ["D_RING*", "C_RING*"],
            "containment": "courtyard",
            "shape": {
                "kind": "annulus",
                "center": [0, 0],
                "innerRadiusMm": 22,
                "outerRadiusMm": 32,
            },
        },
    ],
)
```

Component zones are board-coordinate product constraints, not placement hints.
Use `containment: "courtyard"` when the whole rotated footprint must remain in
the declared circle, annulus, or rectangle; use `"center"` only when the
component origin is the intended contract. Every rule must match at least one
populated component, and the compiled-layout gate blocks unmatched rules or
parts outside their zone. Zone checks do not replace overlap or edge checks.

Apply that same clearance to the board's
`minTraceToPadEdgeClearance` and `minViaEdgeToPadEdgeClearance` *before* the
first route. It is a build contract, not a late attempt to make an already
congested route look safer; KiCad independently receives the declared margin.

Do not set one global board width to the power-trunk value. A 0.6–1.0mm trunk
cannot enter a 0.4mm-pitch QFN. Use the declared short neck-down at component
pads, then widen into the trunk; the artifact gate rejects a rail that stays
narrow through the middle of the board.

Use `BoardPlan.signal_trace_width_mm` (0.25mm) for ordinary board-level
signals. Do not confuse the fab minimum with that preferred width, and do not
reuse it blindly for controlled-impedance USB or another constrained
interface. Fine-pitch escapes and impedance-specific widths are allowed only
as explicit, measured exceptions; an escape must remain short. Power vias are
part of the power net class too: the default 0.8mm outer diameter and 0.5mm
drill prevent a 0.8mm trunk from silently bottlenecking through a generic
0.6mm/0.3mm signal via.

`place_board()` already knows the rules a one-shot board needs: the connector
goes on the bottom edge facing out (a USB socket in the middle of the board is
not a product), the mounting holes go in a reserved strip so a drill never
lands on a footprint, and the outline is sized to hold all of it. Deviate from
it when the product needs you to — a round puck, a connector on the top edge —
but then re-check with `layout.board_fits()` and `layout.overlap_warnings()`,
which answer in milliseconds what `pcb_component_outside_board_error` answers
after a ninety-second build.

### 4. Edit `boards/main.tsx`

`Write`/`Edit`, always an absolute path. Compose blocks, place them with both
coordinate systems, name every net, add the holes, keep the header comment true.

### 5. Run `scripts/circuit`

One `Bash` call, one JSON line back.

### 6. Read the verdict — then LOOK

Parse the JSON. On `ok: false`, `error.code` tells you where to look:

| code | what it means | where the fix is |
|---|---|---|
| `COMPILE_ERROR` | the TSX did not evaluate | the board file — syntax, a bad import, a prop that doesn't exist |
| `VALIDATION_FAILED` | spec/envelope/safety rejected it | `product.json`, or the ask itself (safety refusals are final) |
| `TOOLCHAIN_ERROR` | a tool failed or is missing | the environment — report it, don't design around it |
| `EXPORT_ERROR` | artifacts wouldn't write | disk/paths; retry once, then report |
| `BUILD_TIMEOUT` | the build outran its budget | reduce scope (fewer traces to route) or raise `--wall-clock-s` |
| `PART_ERROR` | a part can't be resolved or ordered | hand off to parts-book |
| `RUNTIME_ERROR` | something unexpected | read the traceback |

On `ok: true`, the work is not done. Check `warnings`:

- **severity `error` — blocking.** Never declare done with one outstanding.
  Common kinds: `source_trace_not_connected_error` (a trace names a port that
  doesn't exist — check the block's pin labels), `pcb_missing_footprint_error`,
  `pcb_footprint_overlap_error` / `pcb_pad_pad_clearance_error` (move the parts
  apart), `pcb_autorouting_error` (usually a *consequence* of placement errors —
  fix those first), `safety_envelope`, `board_exceeds_envelope`, `dfm_*`.
- **severity `warning` — fix or justify.** `part_not_orderable`,
  `power_budget`, `missing_decoupling`, `part_drift`, `unverified_gerbers`
  (kicad-cli absent, so the packet is not shippable), `erc_violation`,
  `drc_violation`.
- **severity `info` — note it.** `extended_part` (a Basic alternative would be
  cheaper), `kicad_unavailable`.

Then **LOOK**. Run `scripts/review` and `Read` both images:

- `_review/_schematic.png` — are nets labelled? Do the blocks read as blocks?
  Is anything floating? Would an EE understand this drawing?
- `_review/_pcb.png` — decoupling caps beside their IC, not across the board.
  Connectors on the edge you promised. Silkscreen legible and not under parts.
  Mounting holes clear of copper. Traces that look sane.

### 7. Fix

The **smallest responsible change**. A DRC overlap is a placement nudge, not a
redesign. Re-run; the build cache means an unchanged board re-runs cheaply.

### 8. Iterate, then stop asking yourself

Soft cap: `tables.MAX_REPAIR_ITERATIONS` (4). Past that you are guessing at
taste rather than fixing a fault — go back to the user with what is wrong and
what you would trade to fix it.

### 9. Hand it to the panel — always

The moment `fab.ready` is `true`, run the **design-review** skill. It is the
last stage of every board, not an optional extra: seven lenses, a ship bar, and
must-fix notes routed back here. Finishing a board without a panel verdict is a
defect in your turn, not a shortcut.

## Non-negotiables

1. **Never invent a circuit from a datasheet.** Compose blocks. No deterministic
   check catches a wrong resistor value, a mirrored pinout, or swapped SDA/SCL —
   every representation agrees because they all inherit the same wrong source.
2. **No mains, ever.** Low-voltage DC only. A refusal with a reason beats a fire.
3. **Battery only through a sealed validated block.** None has hardware sign-off
   yet, so battery asks get the USB variant plus an honest explanation.
4. **Radio only as a certified module.** Never bare-die RF, never a hand-drawn
   antenna or matching network.
5. **Done means `fab.ready == true`. There is no other done.** Not "clean
   build", not "no blocking warnings" — the sidecar's `fab.ready` is `true` or
   the board is unfinished. `fab.ready: false` from unverified gerbers counts
   exactly the same as `fab.ready: false` from a DRC error: the user cannot
   send it to JLCPCB, so it is not a board yet. Report it as unfinished and say
   the one thing that is missing. (See *Done means orderable* below.)
6. **Never declare done without having Read both review images.**
7. **Never call a packet orderable when `fab.ready` is false.** No kicad-cli
   means unverified gerbers — say that plainly instead of implying shippable.
8. **Never edit generated artifacts or `parts.json`.**
9. **Never add an `@tsci/…` registry import**, and never a `footprint="jlcpcb:…"`
   or `"kicad:…"` string — both fetch over the network at build time, which
   makes the same source produce different boards on different days.
10. **Absolute paths in every tool call.** Your cwd is not the workspace.
11. **Report estimates as estimates.** Cost, current draw, and lead time are
    modelled, not measured.
12. **Run the generator before you say anything is finished.** A board you have
    not built is a board you have not designed.

## Helper library (`circuitlib`)

```python
from circuitlib import tables, safety
from circuitlib.blocks import BLOCKS, block_for, missing_requirements
from circuitlib.helpers import (
    board_plan, validate_board_law, trace_width_for, clearance_for,
    decoupling_for, power_budget, estimate_cost, fab_profile,
)
```

`tables` owns every number. `blocks` is the registry (and `BLOCK.md` beside each
block is its datasheet). `safety.safety_gate()` returns `pass` / `refuse` /
`not_screened` — and **`not_screened` is not a pass**; if the gate did not run,
you have not cleared anything. `helpers.validate_board_law()` gives you the soft
warnings before the pipeline does. `golden.invariants()` must stay empty.

## Pattern library

Load the pattern file when the trigger matches — they are short and specific.

| The user says | Read |
|---|---|
| "add a button" / "another key" | `references/patterns/add-a-button.md` |
| "USB power" / "plug it in" | `references/patterns/add-usb-power.md` |
| "use an ESP32 instead" / "swap the chip" | `references/patterns/swap-the-mcu.md` |
| "add a sensor" / "read temperature" | `references/patterns/add-i2c-sensor.md` |
| "it needs to mount" / "make it fit the case" | `references/patterns/mounting-holes-and-outline.md` |
| a DRC/ERC error in the verdict | `references/patterns/fix-a-drc.md` |

## Progressive references

**Tier 0 — read before your first board:**
`references/golden-block-composition.md`, `references/safety-envelope.md`.

**Tier 1 — read when the work touches them:**
`references/jlcpcb-fab-profile.md` (ordering, fees, DFM),
`references/tsx-idioms.md` (the dialect, and what not to guess),
`references/schematic-readability.md` and `references/pcb-layout-craft.md`
(before your first review pass).

## Required final response

Every time you finish, **lead with the gate**:

1. **Orderable or not, first line.** `Fab-ready: yes — the packet is ready to
   upload to JLCPCB.` or `Not fab-ready: <the one thing missing>.` Nothing goes
   above this line. If it is `false`, the word "done" does not appear anywhere
   in your reply.
2. **One sentence on what the board does** — in their words, not net names.
3. **Paths** — the board source and the fab packet directory.
4. **The numbers** — board size, part count, estimated cost band, and the
   warning state (`0 blocking, 2 advisory` — not "looks good").
5. **How many builds it took.** One is the target. If it took more, name the
   thing that went wrong on build #1 — that is the signal that fixes the next
   hundred boards.
6. **What you decided for them and what you'd tweak next** — the craft calls you
   made silently, and the one or two things worth changing if they care.
