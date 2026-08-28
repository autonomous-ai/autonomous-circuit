---
name: block-source
description: Bring a missing block in from the supplier — fetch the real footprint, record the provenance, grade it — instead of refusing the ask or inventing a circuit from a datasheet. Use when a board needs a capability the golden-block catalog has no block for.
---

# block-source — the missing block, sourced rather than invented

## Purpose

The catalog is the only list of blocks that exists, and for a long time that
meant one of two answers to "make it wireless": refuse, or invent an RF circuit
from a datasheet. The first fails the person; the second fails the board.

There is a third answer, and one block already shipped through it.
`servo-header` was not authored — it was **fetched**: LCSC C18078126, the
supplier's own EasyEDA land pattern, `tscircuit-cli import --jlcpcb` measuring
footprinter's guess at **95.84% copper IoU**, a cited source for the pin order,
and a `BLOCK.md` that says where every number came from. That is this skill,
made repeatable.

**This skill does not lower the bar. It names which parts clear it already.**

## The one question that decides everything

> **Does the part carry the engineering, or would you have to?**

- A **3-pin servo header** carries no engineering. Three holes on a 2.54mm
  pitch. Fetch the footprint and the block is finished.
- An **ESP32-C3-MINI-1** carries the entire radio — the transceiver, the
  matching network, the antenna, the crystal, the shield, and an FCC ID saying
  a lab measured it. The module *is* the circuit. Fetching it is composition,
  not invention.
- A **bare nRF24L01 die** carries none of it. The balun, the matching network
  and the antenna would be **yours**, drawn from a datasheet, unmeasured. That
  is the thing this repo refuses, and sourcing does not change it.

So: **sourceable = a passive interconnect, or a part whose own certification
covers the circuit you would otherwise have to invent.** Everything else is
still a `gaps` entry.

## Sourceable

1. **Passive interconnect** — headers, sockets, terminal blocks, JST/Molex
   shells, test points, standoffs. No active silicon, no rail, no circuit.
2. **A certified module** — a part carrying a regulatory identifier of its own
   (FCC ID, IC, CE-RED notified-body number, SRRC, TELEC) for the function you
   need. The identifier is the evidence that the hard part is already done and
   measured by someone with a chamber.

## Not sourceable — no exceptions here

- **Bare RF silicon**, matching networks, chip or PCB-trace antennas. Refused
  by `circuitlib.safety.BARE_RF_PATTERNS` at spec time and refused here. A
  module without a certification identifier is bare silicon wearing a
  daughterboard.
- **Anything on the mains side.** No sourcing route exists and none will.
- **Cell charge or protection.** The envelope allows battery only through a
  sealed validated block; that block does not exist yet, and a charger IC is
  exactly the circuit you would be inventing.
- **A part you cannot buy.** No LCSC number, or out of stock with no
  alternate, means the board cannot be assembled. Stop.

If a design seems to need one of these, the answer is the honest `gaps` entry,
not a wider reading of this list. **Never edit `BARE_RF_PATTERNS` or
`circuitpy.spec`'s safety tables to make room** — those two move together and
the carve-out you want (certified modules) already exists in the envelope.

## When to run

**First thing in the build turn, before a line of board source is written —
never in the plan turn, and never inside the generation loop.**

Both halves of that matter, and the first half was got wrong once already. The
plan turn runs `--permission-mode plan`: read-only, no file may be written. An
agent that reads "source it before the plan" there works out that it cannot,
concludes sourcing is impossible, and hands back a board that refuses the ask —
which is exactly what `rc-car-4` did on 2026-08-28, in its own words: *"No
read-only sourcing path exists — only `grade-block.py`, which grades an
already-written BLOCK.md. That settles the WiFi question."* It settled nothing;
it was in the wrong phase.

The second half is the offline rule: `CIRCUIT_PARTS_ENGINE=off` suite-wide, and
a cold `jlcsearch` costs 47–90s. Sourcing is one network step, taken once, at
the top of the turn — not something the edit/build/read loop reaches for.

```
plan turn  (read-only): name the part, its LCSC number, its certification id,
                        its typical/peak current and page → a SOURCE step,
                        first in the plan's build order
   ↓ approved
build turn: block-source — fetch, write blocks/<id>/, grade ok   ← you are here
   ↓
            write boards/main.tsx → circuitcode build loop (offline)
```

Everything this skill needs in the plan turn is read-only: a datasheet is a
page you read, and a part number is a fact you write down. What needs the build
turn is the *writing* — the footprint fetch and the two files.

A project's `blocks/` is its own frozen copy, and a directory the golden
library does not have is **not** flagged as drift (`blocklib.drift_warnings`
reports `changed` and `missing`, never `extra`). So a sourced block lives with
the board that needed it, builds normally, and costs the library nothing until
someone promotes it.

## The procedure

### 1. Name the part, and prove you may source it

Write down the MPN, the LCSC C-number, and **which of the two sourceable
classes it is in**. For a module, write the certification identifier. If you
cannot find one, the part is not a certified module — stop and file the gap.

### 2. Fetch the supplier's own land pattern

Ask `circuitpy` where the toolchain is rather than guessing a path — it is
the only module that names binaries, and it resolves `CIRCUIT_TOOLCHAIN`
before the repo default:

```bash
CLI=$(python3 -c "from circuitpy.toolchain import toolchain_dir; \
print(toolchain_dir() / 'node_modules/.bin/tscircuit-cli')")
cd "$PROJECT" && "$CLI" import --jlcpcb C<number>
```

The CLI fetches the EasyEDA footprint **and** reports copper IoU against
footprinter's generated guess. Record the number. Keep the supplier's pattern
unless the IoU says the generated one is identical: the supplier's pattern is
what the assembler's machine expects.

**Never trust the exit code** — read what it produced. That rule is not
decoration here: `tscircuit-cli build` exits 0 with real errors, and there is
no reason to believe `import` is stricter.

### 3. Get the numbers the board needs to compose it

A footprint alone is not a block. Composing means budgeting, and a rail cannot
be sized from a pin map. From the **datasheet**, with the page number:

| Number | Why the board stops without it |
|---|---|
| `typical_ma` | the steady rail load |
| `peak_ma` | a radio's transmit burst is 5–10× typical; the LDO and the bulk cap are sized on this, not on the average |
| `v_in` range | decides whether it hangs off 3V3, 5V, or its own regulator |
| pin map | every pin, with the datasheet's own name and number |
| keep-out | any module with an antenna has a copper-free zone the datasheet draws; it is a placement rule, not advice |

**`peak_ma` is the one that blocks.** Without it the power budget cannot be
stated, and a plan that cannot state its power budget is not a plan. Missing
this number is a legitimate reason to stop and say so.

### 4. Write the block

`blocks/<id>/<id>.tsx` plus `blocks/<id>/BLOCK.md`, matching the shape of an
existing block — read `servo-header` first; it is the shortest, and
`sensor-bme280` if the part has a rail and decoupling.

The block owns its own decoupling. It declares its nets. It never reaches
outside itself.

### 5. Grade it before you use it

```bash
python3 ~/.claude/skills/block-source/scripts/grade-block.py <path-to-BLOCK.md>
```

One JSON line: `{"ok": true|false, "id": ..., "missing": [...], "class": ...}`.
It checks that the provenance fields below are present and non-empty — it
cannot check that they are *true*, which is why every one of them cites a
source. **A block that does not grade `ok` does not go on a board.**

## The provenance block — required, and checked

Every sourced `BLOCK.md` carries this, verbatim keys, near the top:

```
## Provenance

| field | value |
|---|---|
| `class` | `interconnect` or `certified-module` |
| `mpn` | manufacturer part number |
| `lcsc` | C-number |
| `certification` | FCC ID / IC / CE-RED / SRRC / TELEC, or `n/a — passive interconnect` |
| `footprint_source` | `easyeda:C<number>` (or `footprinter:<name>` when IoU says identical) |
| `footprint_iou` | the number `tscircuit-cli import --jlcpcb` printed |
| `typical_ma` | steady current, with datasheet page |
| `peak_ma` | worst-case burst, with datasheet page |
| `v_in` | supply range, with datasheet page |
| `keepout` | antenna keep-out, or `n/a` |
| `pin_source` | where the pin order came from — datasheet page or a cited URL and the date read |
| `verified` | ISO date this was fetched and graded |
```

`n/a` is a legal value only where the table says so. Everywhere else, a blank
is a missing number, and a missing number is the block not being finished.

## What this skill never does

- It does not write `parts.json`. **parts-book owns that file wholly** — hand
  it the LCSC number afterwards and let it write the record.
- It does not touch `packages/golden-blocks/`. A sourced block lives in the
  project. Promotion to the golden library is a human's call, made after a
  board built with it comes back from the fab and works.
- It does not claim `hardware-verified`. A fetched footprint and a cited
  datasheet make a block `compile-verified`, the same as everything else in
  the catalog. Say that in `BLOCK.md` and let the reviewer decide.
