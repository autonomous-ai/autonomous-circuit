# Block sign-off — `usb-c-power`

**This block goes into every board a user generates that needs it**, unchanged —
the AI composes blocks, it never edits them. So an error here is not one bad
board, it is a bad board every time. It is also the specific class of error our
automated checks provably cannot catch, which is why this sheet matters more
than any individual board in the packet. Anything you find gets fixed once and
is then right forever.

Source: [`usb-c-power.tsx`](./usb-c-power.tsx) · Datasheet: [`BLOCK.md`](./BLOCK.md)

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
