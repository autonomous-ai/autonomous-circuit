# Block sign-off — `sw-tact`

**This block goes into every board a user generates that needs it**, unchanged —
the AI composes blocks, it never edits them. So an error here is not one bad
board, it is a bad board every time. It is also the specific class of error our
automated checks provably cannot catch, which is why this sheet matters more
than any individual board in the packet. Anything you find gets fixed once and
is then right forever.

Source: [`sw-tact.tsx`](./sw-tact.tsx) · Datasheet: [`BLOCK.md`](./BLOCK.md)

## Check these against the part datasheets, not against our documentation

Our `BLOCK.md` and our source can be wrong in the same way at the same time —
they were written together. Please check against the manufacturer's datasheet
and the LCSC listing.

| # | Question | Verdict |
|---|---|---|
| 1 | Is **every component value** correct for this circuit — not merely plausible? | pass / **fail** |
| 2 | Is **every polarity** right? Diodes, electrolytics, ICs. | pass / **fail** |
| 3 | Does **every pin number** match the datasheet's pinout, in the datasheet's own numbering? | pass / **fail** |
| 4 | Is the **land pattern** right for the package actually ordered (IPC density, thermal pad, paste)? | pass / **fail** |
| 5 | Is each **LCSC part** the right part — and a sane choice for cost, stock and lifecycle? | pass / **fail** |
| 6 | Is the **decoupling** adequate in value, count and placement? | pass / **fail** |
| 7 | Does the block behave at its **stated limits** — the rail budget and current draw in `BLOCK.md`? | pass / **fail** |
| 8 | What does this block do that is **wrong at the edges** — brown-out, inrush, hot-plug, ESD, thermal? | notes |

## Anything you would have done differently

Not a defect, but worth recording — if it is a real preference we should encode
it as a default, because a user will never know to ask for it.

```
```

## Verdict

- [ ] **Approved** — safe to compose into user boards as-is
- [ ] **Approved with changes** — listed above, must land before release
- [ ] **Rejected** — do not release with this block in the catalog

Reviewer: ______________________  Date: ____________

---

## The block's own datasheet, for reference

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

