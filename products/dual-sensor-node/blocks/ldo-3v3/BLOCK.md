# ldo-3v3 — 3.3V logic rail from 5V

**Function:** rail conversion. An AMS1117-3.3 linear regulator with 10uF input
and output bulk, turning `net.V5` (from `usb-c-power` / `usb-c-data`) into the
`net.V3_3` logic rail every digital block on the board runs on.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.V5` (default; `vinNet` prop overrides) | 5V input — the rail to regulate |
| `net.V3_3` (default; `voutNet` prop overrides) | 3.3V logic rail out |
| `net.GND` | ground |

The SOT-223 tab (`TAB`, pin 4) is tied to `VOUT` — it is the package's output
pad, not a thermal-only pad, so it carries rail current and must be poured, not
just stitched.

## Rail budget

Input 5V, output 3.3V — the regulator burns `(5 − 3.3) × I` as heat. At 300mA
that is **0.51W** on a SOT-223 tab; at 500mA it is 0.85W (both derived from the
rail table, not measured). Budget **≤500mA continuous** on a JLC 1oz 2-layer
board with a poured tab, and treat anything above that as needing a switcher
block rather than a bigger copper pour. The AMS1117 family is rated 1A with a
~1.3V dropout at full load (datasheet figure — **not re-verified today**);
dropout is irrelevant from a 5V source but decides whether this block can ever
run from a battery (it cannot — that is the sealed battery block's job).

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| U2 | AMS1117-3.3 | C6186 | SOT-223 | yes | $0.15, 1.49M stock |
| C2 | CL21A106KAYNNNE, 10uF X5R | C15850 | 0805 | yes | $0.009 — input bulk |
| C3 | CL21A106KAYNNNE, 10uF X5R | C15850 | 0805 | yes | $0.009 — output bulk |

## Design-rule notes

- Both bulk caps are required: the AMS1117 needs output capacitance for loop
  stability, and the input cap keeps the USB inrush edge off the regulator.
- The exposed tab is `VOUT` — a copper pour on the tab is the heatsink and it
  is at 3.3V, so keep it clear of GND fill.
- One instance per board. A second 3V3 source fighting this one on the same net
  is a latent short; give a second domain its own net name via `voutNet`.
- Default refdes U2/C2/C3 are the global v1 allocation.

## Provenance

- Land pattern for C6186: exact EasyEDA footprint, imported 2026-08-10 via
  `tscircuit-cli import C6186 --jlcpcb`, committed inline (zero network at
  build time).
- Pin map (1 GND, 2 VOUT, 3 VIN, 4 TAB/VOUT) from the AMS1117 datasheet
  SOT-223 pinout; the tab-is-VOUT rule is why `TAB` is traced to the output
  rather than left floating.
- Part choice (Basic, high stock) follows the r5 recon rule: prefer JLC Basic
  parts — every extended line adds a ~$3 loading fee.
