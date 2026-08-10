# usb-c-power — USB-C 5V power input

**Function:** power entry. A USB-C receptacle wired as a UFP power sink:
5.1k CC pulldowns request default 5V/up-to-3A from any source, ESD protection
on the exposed CC lines, 10uF bulk on VBUS.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.V5` (default; `vbusNet` prop overrides) | 5V VBUS rail out |
| `net.GND` | ground (shell tied to GND) |

D+/D− and SBU pins are left unconnected — this is the **power-only** variant.
Use `usb-c-data` when the MCU needs the USB data pairs (it is a superset;
never place both).

## Rail budget

Source: 5V at up to 3A advertised (5.1k/5.1k Rd per USB-C spec §4.5.1.2);
budget conservatively 5V @ 1.5A for JLC 1oz 2-layer boards — feed `ldo-3v3`
for logic rails. VBUS bulk capacitance kept at 10uF (USB 2.0 inrush limit).

## Parts (pinned; verified 2026-08-10 via jlcsearch unless noted)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| J1 | TYPE-C-31-M-12 | C165948 | SMD+TH hybrid | no | $0.16, 336k stock (r5 recon) |
| R1, R2 | 5.1kΩ ±1% | C25905 | 0402 | yes | CC pulldowns |
| U1 | USBLC6-2SC6 | C2687116 | SOT-23-6 | no | $0.035, 231k stock (r5 recon) |
| C1 | 10uF X5R | C15850 | 0805 | yes | VBUS bulk |

## Design-rule notes

- CC1/CC2 each run through one USBLC6 channel (IO1/IO1B, IO2/IO2B) — the
  connector-facing pins are the protected side.
- Both VBUS pins (A4B9/B4A9) and both GND pins are tied — never single-pin.
- Shell (EH1-4) ties to GND.

## Provenance

- Land pattern for C165948: exact EasyEDA footprint, imported 2026-08-10 via
  `tscircuit-cli import C165948 --jlcpcb` (exact footprint kept — best
  footprinter match was 81.35% IoU), committed inline.
- CC/ESD pattern follows the seveibar `usb-c-flashlight` example family
  (registry survey r5, 2026-08-10) and the USB-C spec's sink-Rd rule.
- USBLC6 channel mapping from the ST USBLC6-2SC6 datasheet (SOT-23-6).
