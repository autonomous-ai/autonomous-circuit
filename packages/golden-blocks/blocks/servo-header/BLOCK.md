# servo-header — a 3-pin 2.54mm male header per hobby servo

**Function:** brings a servo lead onto the board. One connector, three pins,
nothing else — no capacitance, no regulation, no current limit.

## Ports

| port | meaning |
|---|---|
| `net.<rail>` (default `V5`) | in: servo supply, straight through to pin2 |
| `net.<ground>` (default `GND`) | in: return |
| `net.<signal>` (default `SERVO1`) | in: PWM from the MCU |

## Parts (pinned; verified 2026-08-26 via `tscircuit-cli import --jlcpcb`)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| J10 | BX_PM2_54_1_3PY | C18078126 | 1x3 2.54mm THT | no | male pin header |

## Pin order — the whole safety argument

    pin1  GND     (black or brown)
    pin2  V+      (red)            <- ALWAYS the middle pin
    pin3  SIGNAL  (white, yellow or orange)

Universal across Futaba, JR and Hitec. Source:
`rchelicopterfun.com/rc-servo-connectors.html`, read 2026-08-26 — *"The
positive (red wire) is always in the middle of 3 pin/wire servo connectors."*
The same page records that the shells are otherwise interchangeable; Futaba
adds an alignment rib on the signal side and nothing else changes.

**Why the middle matters.** A lead plugged in backwards then swaps GND and
SIGNAL, which is recoverable. Put V+ on an outer pin and a reversed lead feeds
the rail straight into the servo's signal input, which is not. **Do not move
V+ off pin2.**

The part numbers its own pins with pin2 in the middle (EasyEDA pad table for
C18078126: pin1 at x=−2.54mm, pin2 at 0, pin3 at +2.54mm), so convention and
footprint agree with no translation — unlike `sw-tact`, where they did not and
it cost eight boards.

## Land pattern

The exact EasyEDA footprint for C18078126. `tscircuit-cli import --jlcpcb`
measured footprinter's best guess (`pinrow3_p2.54mm`) at **95.84% copper IoU**
and kept EasyEDA's on that basis. Three plated holes, 2.54mm pitch, **1.0200mm
hole on a 1.5748mm pad** — clearance for the 0.64mm square pins a servo shell
expects.

## Placement — put the supply beside the bank, not across the board

**This block is in `circuitlib.layout.EDGE_BLOCKS`.** Two reasons are the usual
ones: tscircuit reads a connector's facing from **pin1** (not from
`pcbRotation`, measured 2026-08-26 — rotating the part moves its pads and
leaves the reported facing alone), and a servo lead cannot reach a connector in
the middle of a chassis.

The third reason cost a board. **Whatever feeds `rail` belongs immediately
beside this bank, at the same edge.**

`rc-car-2`, 2026-08-26, put the servo bank on the west edge and the RP2040 in
the middle, so `V_SERVO` had to cross the board. Sized correctly for a
four-servo stall, that trunk is **1.2mm — six times a signal trace** — and it
does not travel alone: it needs clearance on both sides, and on two layers with
a ground pour there is nowhere else for the small stuff to go.

Measured against a clean board with the same pin count:

| | area | trace segments >= 0.5mm | result |
|---|---|---|---|
| `weather-badge-30` | 2400mm² | 73 | fab.ready |
| `rc-car-2` | 6084mm² | **241** | 13 blocking, every one in the RP2040's escape |

**2.5x the area and it still jammed**, because the jam was never about area. A
1.2mm rail crossing the board is a highway through the one junction every net
has to pass. Keep the supply pads and the servo bank on the same edge and the
wide run becomes a stub in one corner.

If that is not possible on a given board, the honest options are a 4-layer
stack (nothing in the pipeline forbids it; nothing has ever tried it) or a
smaller declared current — and the second is a question for a hardware
reviewer, not a number to pick here.

## Rail budget — read this before placing more than one

**This block does not solve power.** `rail` is a wire to pin2; there is no
bulk capacitance, no regulator and no current limit in it.

A hobby servo's stall current is **not a number this repo owns** —
`circuitlib/tables.py` has no entry for a servo, a motor or any inductive
load, so nothing downstream will check a servo rail for you. What is on file:
`ldo-3v3` budgets **≤500mA continuous**, which one servo can exceed on its own
and four certainly do.

So: feed `rail` from a supply sized for the servos, not from the 3.3V block,
and treat that sizing as **an open question until somebody measures it**. A
board that puts several of these on USB VBUS is drawing motors through a
connector rated for something else.

## Known limits

- **`part-terminals.py` cannot grade this part.** A 3-pin header has no
  internally tied pads, so the terminal checker skips it entirely. The IoU
  figure above and the visible geometry are the verification there is.
- **No reverse-polarity protection, no flyback path, no decoupling.** All of
  that belongs to whatever feeds `rail`.
- **Through-hole.** JLCPCB economy assembly places SMT only, so these are
  hand-soldered or a separate assembly line item.

## Provenance

Authored 2026-08-26 as the first block sourced by fetching rather than
drawing: part chosen from JLCPCB, footprint pulled from EasyEDA and accepted
because the tool's own IoU comparison against footprinter said the generated
guess was not close enough to substitute.
