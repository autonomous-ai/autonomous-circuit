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

Wiring is **diagonal**: `signal` → pin 1, pin 4 → `to`. Pins 1 and 4 are on
opposite terminals under either side-pairing a 4-pad tact switch can have, so
the block is correct even if the pairing evidence below is wrong. The internal
pairing itself is declared on the component (`internallyConnectedPins`), which
is what makes every schematic export draw a working switch instead of a
same-net tie looping across the symbol (ledger #29 — the first human EE review
read that loop as "every key is dead").

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
