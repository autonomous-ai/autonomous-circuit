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
├── product.json          the bible: name, power, envelopeMm, layers, fab, assembly
├── parts.json            the locked BOM — parts-book owns it; you never write it
├── blocks/               the golden-block library, frozen with this project
├── boards/
│   ├── main.tsx          your file — the only file you write
│   └── main.circuit.json + main.board.json + main_review/ + main_fab/   generated
├── tsconfig.json, tscircuit.config.json
└── .circuit/             build cache — never edit, never read
```

### Starting a new project

The app creates the workspace; you fill it. From the skill's own templates:

```bash
SKILL=~/.claude/skills/circuitcode
cp -R "$SKILL/templates/project_skeleton/." /abs/project/
cp -R "$SKILL/blocks" /abs/project/blocks
```

**Copying the blocks in is not optional** — `boards/main.tsx` imports them by
relative path, and a project that owns its own snapshot keeps building the same
board after the shared library moves on. Then edit `product.json` (name,
description, power, envelope) before you write any board source.

Rules of the project format:

- **Device-wide facts live in `product.json` only.** Power source, envelope,
  layer count, fab profile. Don't restate them in the board file; read them.
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

`boards/main.tsx` default-exports a function returning one `<board>`:

```tsx
import { UsbCPower } from "../blocks/usb-c-power/usb-c-power"
import { Ldo3v3 } from "../blocks/ldo-3v3/ldo-3v3"

export default () => (
  <board width="40mm" height="30mm" thickness={1.6}>
    <UsbCPower pcbX={-14} pcbY={0} schX={-6} schY={0} />
    <Ldo3v3   pcbX={0}   pcbY={6} schX={0}  schY={0} />
    <hole name="H1" diameter="3.2mm" pcbX={-17} pcbY={-12} />
  </board>
)
```

Four things that are not optional:

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

## Plan-phase design discipline

When the app runs you in Plan mode, **write no files.** Produce an engineering
spec the user can approve or redirect:

1. **What it does** — one paragraph, in the user's terms.
2. **Block plan** — the table: capability → block → why. Call
   `circuitlib.helpers.board_plan(capabilities=[...])`; report anything it
   returns in `unavailable` honestly instead of inventing a circuit for it.
3. **Power budget** — source, rail tree, the arithmetic. `V5 @ 1.5A → LDO →
   V3_3`; sum the block draws; state the headroom.
4. **Pin allocation** — every MCU pin you intend to use, and for what.
5. **Size and layout intent** — board outline, what sits on which edge, where
   the mounting holes go, and whether it fits `product.json`'s envelope.
6. **Cost band** — `circuitlib.helpers.estimate_cost()` plus the parts total
   from the lock. Quote the assembled-5x band, not a fantasy unit price.
7. **Safety verdict** — run `circuitlib.safety.safety_gate()` on the ask. If it
   refuses, the plan is the refusal and the reason. Don't design around it.

### Where the numbers come from — the tables own them

| Number you need | Where it lives |
|---|---|
| Rail voltages and tolerances | `circuitlib.tables.RAILS` |
| Trace width for a current | `helpers.trace_width_for(current_a=…)` (IPC-2221) |
| Conductor spacing for a voltage | `helpers.clearance_for(volts=…)` |
| Min trace/space/drill/annular/edge | `tables.MIN_*` |
| Board thickness, layer default | `tables.BOARD_THICKNESS_MM`, `tables.DEFAULT_LAYERS` |
| Decoupling counts | `helpers.decoupling_for(power_pins=…, rails=…)` |
| I2C pull-ups, USB series/CC resistors | `tables.I2C_PULLUP_OHMS`, `tables.USB_*` |
| Assembly fees, cost bands, lead time | `tables.*_USD`, `helpers.estimate_cost()`, `helpers.fab_profile()` |
| Which block does X | `blocks.block_for(capability)`, `blocks.CAPABILITY_INDEX` |
| What a block needs fed | `blocks.BLOCKS[id].requires` / `.provides` |
| Iteration cap | `tables.MAX_REPAIR_ITERATIONS` |

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

`ls` the project. `Read` `product.json` (power, envelope, layers), `parts.json`
if present, the current `boards/main.tsx`, and the `BLOCK.md` of every block you
plan to use — the pin contract and rail budget are in there.

### 3. Block plan

`board_plan(capabilities=[...])`. Check `unmet` (a net nothing provides) and
`unavailable` (a capability with no block). Resolve both *before* writing code.

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

## Non-negotiables

1. **Never invent a circuit from a datasheet.** Compose blocks. No deterministic
   check catches a wrong resistor value, a mirrored pinout, or swapped SDA/SCL —
   every representation agrees because they all inherit the same wrong source.
2. **No mains, ever.** Low-voltage DC only. A refusal with a reason beats a fire.
3. **Battery only through a sealed validated block.** None has hardware sign-off
   yet, so battery asks get the USB variant plus an honest explanation.
4. **Radio only as a certified module.** Never bare-die RF, never a hand-drawn
   antenna or matching network.
5. **Never declare done with an `error`-severity warning outstanding.**
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

Every time you finish, tell the user:

1. **One sentence on what the board does** — in their words, not net names.
2. **Paths** — the board source and the fab packet directory.
3. **The numbers** — board size, part count, estimated cost band, and the
   warning state (`0 blocking, 2 advisory` — not "looks good").
4. **Whether it is orderable** — `fab.ready` true/false, and if false, exactly
   what is missing.
5. **What you decided for them and what you'd tweak next** — the craft calls you
   made silently, and the one or two things worth changing if they care.
