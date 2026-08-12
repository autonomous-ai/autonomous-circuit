# usb-c-power — USB-C 5V power input

**Function:** raw connector entry. A USB-C receptacle wired as a UFP power
sink: 5.1k CC pulldowns, ESD protection on the exposed CC lines, and 1uF of
raw attach capacitance. Pair it with `usb-power-entry`; never place LDO/load
bulk directly on this net.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.VBUS_RAW` (default; `vbusNet` prop overrides) | connector-side VBUS; feed `usb-power-entry` |
| `net.GND` | ground (shell tied to GND) |

D+/D− and SBU pins are left unconnected — this is the **power-only** variant.
Use `usb-c-data` when the MCU needs the USB data pairs (it is a superset;
never place both).

For a wide board-level VBUS entry, set
`externalPowerTrunkPort="VBUS1"` (or `VBUS2`) and use that exact J1 pad as
golden `PowerTrunk.source`. The selected pad's ordinary source-to-rail edge is
omitted so the trunk is not a redundant same-net cycle.

## Rail budget

Without measuring the Type-C source-current advertisement, budget no more than
the USB 2.0 default 500mA after enumeration. C1 is 1uF; with the required
100nF bypass in `usb-power-entry`, raw attach capacitance is 1.1uF and remains
below 10uF. Downstream LDO/load bulk is isolated by the controlled-rise switch.

## Parts (pinned; verified 2026-08-10 via jlcsearch unless noted)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| J1 | TYPE-C-31-M-12 | C165948 | SMD+TH hybrid | no | $0.16, 336k stock (r5 recon) |
| R1, R2 | 5.1kΩ ±1% | C25905 | 0402 | yes | CC pulldowns |
| U1 | USBLC6-2SC6 | C2687116 | SOT-23-6 | no | $0.035, 231k stock (r5 recon) |
| C1 | 1uF X5R | C52923 | 0402 | yes | raw attach capacitance |

## Design-rule notes

- `layer="bottom"` mirrors the entire authored block, not only each isolated
  footprint. R1/R2 and C1 exchange physical X sides, all rotations are
  complemented, and `UsbRawVbusTree` mirrors both its hidden nodes and every
  relative crossover vertex. Its 0.2mm necks, 0.8mm trunk, and 0.8/0.5mm vias
  therefore retain the same endpoints and clearances on either face.
- `UsbRawVbusTree` places its two connector nodes at block-local
  `(±3.2, 3.4)`. Each 0.2mm neck first rises at local X `±2.4` to Y `3.1`,
  then turns into its node. The dogleg is about 1.78mm long, below the 2mm
  limit, clears the adjacent GND pad, and reserves a real signal escape
  corridor around the 0.8mm nodes.

- CC1/CC2 each run through one USBLC6 channel (IO1/IO1B, IO2/IO2B) — the
  connector-facing pins are the protected side.
- Both VBUS pins (A4B9/B4A9) and both GND pins are tied — never single-pin.
- Shell (EH1-4) ties to GND.
- **Each alignment drill has an exact component-local keepout.** The two
  0.6mm NPTHs each carry a 1.02 x 1.02mm guard centered on the drill. Its edge
  is 0.2100006mm beyond the actual 0.5999988mm drill, clearing the 0.20mm fab
  floor without intersecting any imported connector pad. The guards close the
  illegal narrow channels beside the holes while preserving the legal central
  routing pocket used by the reversible data trees. They live inside J1's
  footprint, so translation, rotation, and top/bottom placement transform the
  holes and guards together; a composer has nothing extra to place.
- **If your board pours copper, use the shared `GndPlanes` helper or derive the
  pour cutout from `POUR_CUTOUT_MARGIN_MM`.** The constant accounts for the
  chord error in the 32-sided hole cutout; do not copy its current numeric
  value into a board. This applies to every NPTH, including mounting holes.

## Provenance

- Land pattern for C165948: exact EasyEDA footprint, imported 2026-08-10 via
  `tscircuit-cli import C165948 --jlcpcb` (exact footprint kept — best
  footprinter match was 81.35% IoU), committed inline.
- CC/ESD pattern follows the seveibar `usb-c-flashlight` example family
  (registry survey r5, 2026-08-10) and the USB-C spec's sink-Rd rule.
- USBLC6 channel mapping from the ST USBLC6-2SC6 datasheet (SOT-23-6).
