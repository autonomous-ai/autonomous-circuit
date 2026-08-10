# sw-tact — one tactile button

**Function:** user input. A 4-pad SMD tactile switch that shorts a signal net to
ground when pressed. Active-low by convention: pull the signal side up (the
RP2040 and ESP32-S3 internal pull-ups are enough — no external resistor is part
of this block).

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending — see the pad-pairing note below).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.BTN1` (default; `signal` prop overrides) | the switched signal — pull it up |
| `net.GND` (default; `to` prop overrides) | the other terminal |

Both pads of each internal terminal are tied: pins 1+2 → `signal`, pins 3+4 →
`to`. A single cracked joint therefore never opens the circuit.

## Rail budget

None — a passive contact. It sinks only the pull-up current of whatever holds
the signal high (tens of µA for an MCU internal pull-up). Debounce is firmware's
job; there is no RC in this block.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| SW1 | TS-1187A-B-A-B | C318884 | SMD-4P, 5.1×5.1mm | yes | $0.018, 918k stock |

## Design-rule notes

- **Pad pairing (1+2 / 3+4) is the standard TS-1187A arrangement but is not yet
  hardware-verified** — confirm on the first article before a key-grid board
  goes out. If the real pairing is 1+3 / 2+4, the block's tie traces become a
  permanent short and every board using it is scrap.
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
