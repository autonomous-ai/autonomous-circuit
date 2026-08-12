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

VOUT and TAB are declared as the package's internal connection, so no invented
PCB trace runs between them. The adjacent C2/C3 bulk capacitors are part of two
authored rail trees: pin-to-cap links are bounded to 3mm and the sole board
boundary is 0.8mm. For a board-level 3V3 `PowerTrunk`, set
`externalPowerTrunkPort="TAB"` and start the trunk at `.U2 > .TAB`. That
suppresses the block's C3→V3_3 boundary, leaving exactly one output-tree
boundary and preventing the former VOUT/TAB duplicate cycle. VOUT is not an
external trunk option; TAB is the wide electrical/thermal pad.

When the protected 5V trunk is board-owned, set
`externalInputPowerTrunkPort="VIN"` and attach its wide tree at
`.C2 > .pin1` (or `.<cin> > .pin1` when the ref is overridden). This suppresses
only the C2→V5 named boundary. The required U2.VIN→C2 local 0.2mm branch
remains bounded to 3mm, and the board must provide exactly one 0.8mm V5
boundary in the replacement tree. A cross-face replacement owns its explicit
0.8/0.5mm off-pad transition; do not raise a phase-wide via minimum, because
that also reinterprets already-authored 0.6/0.3mm signal vias elsewhere on the
board.

`layer="bottom"` mirrors the complete local placement around the asymmetric
SOT-223 copper. U2 stays at the block origin, C2/C3 exchange X sides, and all
rotations are complemented. The mirrored VIN→C2 and TAB→C3 traces retain the
same endpoints, lengths, widths, and component face as the reviewed top
instance.

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

- Both bulk caps are required and are placed beside their pins: the AMS1117
  needs output capacitance for loop stability, and the input cap keeps the
  protected 5V edge off the regulator.
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
