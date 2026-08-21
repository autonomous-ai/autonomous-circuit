# sw-tact — one tactile button

**Function:** user input. A 4-pad SMD tactile switch that shorts a signal net to
ground when pressed. Active-low by convention: pull the signal side up (the
RP2040 and ESP32-S3 internal pull-ups are enough — no external resistor is part
of this block).

**Status:** v1.1 (2026-08-15, from the first human EE review). Diagonal
wiring + declared internal pairing; compile-verified against
tscircuit@0.0.2279; not yet hardware-verified (first article pending — see
the pad-pairing note below).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.BTN1` (default; `signal` prop overrides) | the switched signal — pull it up |
| `net.GND` (default; `to` prop overrides) | the other terminal |

**The pairing is by row, and the footprint numbers by column.** Corrected
2026-08-21 after an outside hardware review read the fault off a board image —
*"the two terminals are tied together, so it reads as permanently pressed."*
Both halves measured:

| | one terminal | the other |
|---|---|---|
| the part — LCSC's footprint for C318884, EasyEDA API | `pad1` ── `pad2` (a row, 6.00mm) | `pad3` ── `pad4` |
| our land pattern `dfn4_p3.6998mm_w7mm_pw0.75mm`, off a built board | `pin1` top-left ── `pin4` top-right | `pin2` ── `pin3` (bottom row) |

The footprint numbers down the left column and up the right, DFN convention, so
`pin1`/`pin2` are one **column** — one pad from each terminal. Tying them to the
same net ties signal to ground through the switch body and the button can never
do anything. Both earlier wirings did exactly that: the four-trace version tied
`pin1`+`pin2`, and the "diagonal" version tied `pin1`+`pin4`, which are the same
*row* in this footprint and therefore the same terminal.

Wiring is now both pads of each terminal: `{pin1, pin4}` → `signal`,
`{pin2, pin3}` → `to`. The internal pairing is declared on the component
(`internallyConnectedPins`) in the footprint's own numbering, which is what
makes every schematic export draw a working switch instead of a same-net tie
looping across the symbol (ledger #29 — the first human EE review read that
loop as "every key is dead").

**First-article continuity is the check that closes this**, and the only one
that catches the footprint being renumbered upstream: with the button up,
`pin1`–`pin4` reads closed and `pin1`–`pin2` reads open.

## Rail budget

None — a passive contact. It sinks only the pull-up current of whatever holds
the signal high (tens of µA for an MCU internal pull-up). Debounce is firmware's
job; there is no RC in this block.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| SW1 | TS-1187A-B-A-B | C318884 | SMD-4P, 5.1×5.1mm | yes | $0.018, 918k stock |

## Design-rule notes

- **Pad pairing evidence (2026-08-15):** LCSC's own schematic symbol for
  C318884 (EasyEDA API) draws internal bars joining pins 1–2 and 3–4 with the
  actuator between the rows — the standard TS-1187A arrangement. That is
  strong evidence, not a measurement: **first-article continuity remains the
  final check.** The stakes are lower than they were: since v1.1 the copper is
  diagonal (pin 1 → pin 4), which is correct under either side-pairing, so a
  wrong pairing no longer scraps the board — it only makes the declared
  `internallyConnectedPins` symbol annotation wrong on paper.
- Instantiate per key by overriding `name` (SW10, SW11, … for grids) and
  `signal` (one net per key, or a row/column net for a matrix).
- Default refdes SW1 is the global v1 allocation; `rp2040-core` already uses SW2
  (BOOTSEL) and SW3 (RESET) — do not reuse them.
- Buttons that the user must reach belong on the enclosure edge or under a
  printed cap; the enclosure interface is declared in the board brief, not here.

## Provenance

- Land pattern: footprinter `dfn4_p3.6998mm_w7mm_pw0.75mm` — 98.81% copper IoU
  against the EasyEDA pattern for C318884 (`tscircuit-cli import C318884`,
  2026-08-10). The builtin was kept over the imported pattern at that IoU.
- Part choice from the r5 recon sourcing pass: TS-1187A is the JLC Basic tactile
  switch; MX/Choc hotswap sockets are v1.1 (no registry package, needs its own
  sourcing pass).
