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
   not go on the board — say so and offer the nearest block. The one way to
   make the catalog longer is `block-source`
   (`~/.claude/skills/block-source`), which sources a passive interconnect or
   a certified module from the supplier with graded provenance. Run it **first
   thing in the build turn**, before you write a line of board source, and
   never inside the edit/build/read loop — it is one network step, taken once.
   It does not loosen this rule: a part whose circuit would be yours to draw is
   still refused.
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
5. **Land the debug interface.** An MCU block brings SWCLK/SWD out as nets and
   terminates neither. If nothing does, the assembled board cannot be halted,
   single-stepped or recovered from a bad image — every part on it correct and
   the product useless. `board_plan().must_expose` names the nets; `DebugPort`
   from `blocks/glue` lands them on three 2.54mm pads. **Any other row of
   labelled pads is the same component under its own name** — `PadHeader`,
   also from `blocks/glue`, takes `nets`, `labels`, `pitch` and `padDiameter`,
   so an off-board strip connector or a bench breakout is one import rather
   than a hand-copy of `DebugPort`'s internals (which is what two engineers
   did on 2026-08-17 before it had a name they would look for). Put it in open board
   space, not inside the MCU block: three pads inside `rp2040-core`'s own box
   route the debug pair through the crystal cluster and the router comes back
   with a via shorted into the QFN pad field (measured 2026-08-11).
6. **Say what routing effort the board needs.** `autorouterEffortLevel="5x"` is
   the floor on every board. The same rp2040-core board is `fab.ready: false`
   with five blocking KiCad findings at the default effort and `fab.ready:
   true` with zero at `"5x"` — same design, only this prop changed.

   **Your number is a floor, not a ceiling.** When the circuit.json scan shows
   routing-class blockers the pipeline now climbs one rung on its own (5x →
   10x), keeps the harder result only if it is strictly better, and says in the
   sidecar what it tried and what it got. So do not hand-rebuild at a higher
   effort to see: read the finding. If it says the retry ran and did not help,
   the remaining lever is the placement.

   **The one case it still cannot reach**: findings that only KiCad can see.
   The escalation decides off the circuit.json scan, and `drc_violation` —
   clearance, shorting, hole-clearance — is produced by the KiCad cross-check
   several stages later, so a board whose *only* blockers are DRC ones gets no
   retry. That is the case where declaring `"10x"` yourself is still the move,
   and it is why the fleet's RP2040 boards were rebuilt by hand.

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
```

`boards/main.tsx` imports blocks by relative path, and a project that owns its
own snapshot keeps building the same board after the shared library moves on.
**You do not copy them yourself — the build seeds `blocks/` from the library on
a project that has none**, and reports what it wrote. Then edit `product.json`
(name, description, power, envelope) before you write any board source.

**Never copy `blocks/` from another project.** It is the one way this has gone
wrong: on 2026-08-21, weather-badge-16 through -25 — eight boards over two and
a half days — were found holding byte-identical blocks inherited from each
other rather than from the library, and a fix that unshorted every button on
every board with one reached none of them. A build now says so, at `warning`,
whenever a board that has never been built already disagrees with the library.

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

### `fab.ready: true` is a floor. Never trade it for a tidier verdict.

**Once a build reports `fab.ready: true`, no later change may lose it.** If a
fix for a non-blocking finding costs readiness, **revert the fix** and report
the finding as accepted, with its measurement, in the final response.

This is not hypothetical. On the 2026-08-11 agent eval, `macropad-6` was
`fab.ready: true` on its **first** build and `false` after five repair rounds:
the loop chased findings that were never blocking anything and gave away the
board the user had already got. `usb-blinky` on the same run went ready →
ready over three rounds, so it is not inevitable — but once is enough, because
it is the worst failure available to this skill.

The router has had the right instinct all along: stage 0b keeps an escalated
route **only when it has strictly fewer blocking warnings**. Apply the same
rule one level up.

- After every build, compare `fab.ready` with the previous build's.
- `true` → `false` is a **regression, not progress**. Undo the change that
  caused it before doing anything else, and say what you reverted and why.
- A warning or an info finding is never worth an orderable board. "Zero
  findings" is not the goal; **orderable** is the goal, and the finding count
  is only ever evidence about it.

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
3. **Power budget** — source, rail tree, the arithmetic. `V5 @ 1.5A → LDO →
   V3_3`; sum the block draws; state the headroom.
4. **Pin allocation** — every MCU pin you intend to use, and for what.
5. **Size and layout intent** — board outline, what sits on which edge, where
   the mounting holes go, and whether it fits `product.json`'s envelope.
6. **Cost band** — `circuitlib.helpers.estimate_cost()` plus the parts total
   from the lock. Quote the assembled-5x band, not a fantasy unit price.
8. **Safety verdict** — run `circuitlib.safety.safety_gate()` on the ask. If it
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
| A real, stocked glue part | `parts.pick("resistors", resistance=4700)` — JLC Basic/Preferred mirror |
| Is this part costing us a feeder fee | `parts.cheaper_basic_part("C…")` |
| Will the regulator cook | `helpers.regulator_thermal(vin=…, vout=…, current_a=…)` |
| Is this LED resistor sane | `helpers.led_current(rail_v=…, resistance_ohms=…)` |
| What a block needs fed | `blocks.BLOCKS[id].requires` / `.provides` |
| Iteration cap | `tables.MAX_REPAIR_ITERATIONS` |
| How big a block is, and where it sits | `layout.box(block_id)` -> `(min_x, min_y, max_x, max_y)` around its origin |
| **The whole board plan** | `layout.place_board([...])` -> outline + placements + holes + its own warnings |
| Room for something that is not a block | `layout.reserve(name, w, h)` / `layout.pad_header_extent(pads)` — pass the name in with the blocks |
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

# Structural check — compile + circuit-json scan + checks library. The fab
# packet is built and discarded, so this costs about what a full build costs
# and is NOT a cheap pre-flight (measured 2026-08-17: same findings, same
# minutes). Use it when you want a verdict without a packet, not to save time.
python ~/.claude/skills/circuitcode/scripts/check /abs/project/boards/main.tsx

# PRE-FLIGHT — the placement verdict, without paying for routing. ~17s on a
# dense board against 20-40 minutes for a build, because it compiles with
# `routingDisabled` and grades what is left: overlapping parts, a footprint
# that is not the part, a component off the board, a hole in a pad, pad-to-pad
# clearance, assembly risks, board size and price tier, decoupling distance.
# It sees NOTHING about copper and says so. Use it every time you move a part;
# use `circuit` when you want a board.
python -m circuitpy.preflight /abs/project --board boards/main.tsx

# ~1s verdict on a board that has ALREADY been built, with optional
# placement moves applied in memory. This is the fast gate: ~0.5-0.9s on the
# boards we ship, no compile at all. It cannot see anything a rebuild would
# change — the copper pour, what the router will do next, the fab packet — and
# it says so in `not_checked`.
python -m circuitpy.fastcheck /abs/project --board boards/main.circuit.json

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

### 3. Block plan, then place by measurement

`board_plan(capabilities=[...])`. Check `unmet` (a net nothing provides) and
`unavailable` (a capability with no block). Resolve both *before* writing code.

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

`place_board()` already knows the rules a one-shot board needs: the connector
goes on the bottom edge facing out (a USB socket in the middle of the board is
not a product), the mounting holes go in a reserved strip so a drill never
lands on a footprint, and the outline is sized to hold all of it. Deviate from
it when the product needs you to — a round puck, a connector on the top edge —
but then re-check with `layout.board_fits()` and `layout.overlap_warnings()`,
which answer in milliseconds what `pcb_component_outside_board_error` answers
after a ninety-second build.

**Anything that is not a block needs a `reserve`.** A `PadHeader`, a testpoint
band, a display window, a battery clip — the planner cannot make room for
content it has never heard of, and four boards grew their outline and re-seated
every coordinate by hand before this existed. Do not do that arithmetic:

```python
header = layout.reserve("i2c-header", *layout.pad_header_extent(4))
plan = layout.place_board(["usb-c-data", "ldo-3v3", "rp2040-core", header])
x, y = plan["placements"]["i2c-header"]   # then <PadHeader pcbX={x} pcbY={y} />
```

`pad_header_extent(pads, pitch=2.54, pad_diameter=1.0)` is derived from the
component's own numbers, including the silkscreen label below the row, so the
two cannot drift. For anything else, pass the size directly:
`layout.reserve("oled-window", 27.0, 19.0)`. A reserve is a box in the same
table as a block, so row wrapping, gaps, `board_fits` and `overlap_warnings`
all treat it as one.

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

**And a hard floor underneath the cap: a round may leave the board no worse
than it found it.** Once any build has reported `fab.ready: true`, the moment
a later build reports `false` you have made the board worse — stop, revert
that change, and report the finding you were chasing as accepted. `macropad-6`
went ready → not-ready over five rounds on the 2026-08-11 eval doing exactly
this. Polishing is not free; it is paid for with the thing the user wanted.

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
6. **Never lose a `fab.ready: true` you already had.** It is a floor, not a
   score to improve on. A change that turns orderable into not-orderable gets
   reverted, and the finding it was chasing gets reported as accepted.
7. **Never declare done without having Read both review images.**
8. **Never call a packet orderable when `fab.ready` is false.** No kicad-cli
   means unverified gerbers — say that plainly instead of implying shippable.
9. **Never edit generated artifacts or `parts.json`.**
10. **Never add an `@tsci/…` registry import**, and never a `footprint="jlcpcb:…"`
   or `"kicad:…"` string — both fetch over the network at build time, which
   makes the same source produce different boards on different days.
11. **Absolute paths in every tool call.** Your cwd is not the workspace.
12. **Report estimates as estimates.** Cost, current draw, and lead time are
    modelled, not measured.
13. **Run the generator before you say anything is finished.** A board you have
    not built is a board you have not designed.
14. **Never move a placement that carries a `locked:` comment above it.** A
    human moved that part by hand in the IDE and asked for it to stay:

    ```tsx
    {/* locked: placed by hand - do not move this without asking */}
    <StatusLed rail="V3_3" pcbX={-43} pcbY={-32} />
    ```

    This is the whole agent/human merge rule, and it is a **convention, not a
    lock** — nothing in the compiler or the pipeline enforces it, which is
    exactly why it has to be written here. The one mechanical guard is a
    compare-and-swap on write, and that only catches a simultaneous edit, not a
    later one. If a `locked:` placement genuinely has to move — it blocks a
    route, it collides with a part you are adding — **move it and say so in one
    line**, naming the part and the reason. Silently relocating it is the
    failure this rule exists to prevent: the human loses work they cannot see
    they lost, and the next thing they stop trusting is the tool.
    (`docs/architecture/ide-edit-contract.md`)

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
