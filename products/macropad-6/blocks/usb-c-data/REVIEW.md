# Block sign-off — `usb-c-data`

**This block goes into every board a user generates that needs it**, unchanged —
the AI composes blocks, it never edits them. So an error here is not one bad
board, it is a bad board every time. It is also the specific class of error our
automated checks provably cannot catch, which is why this sheet matters more
than any individual board in the packet. Anything you find gets fixed once and
is then right forever.

Source: [`usb-c-data.tsx`](./usb-c-data.tsx) · Datasheet: [`BLOCK.md`](./BLOCK.md)

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

# usb-c-data — USB-C power + USB 2.0 data

**Function:** power entry **and** the USB 2.0 full-speed data pair. A
**superset of `usb-c-power`**: same receptacle, same 5.1k CC pulldowns, same
VBUS bulk, same default refdes — plus both connector orientations of D+/D−
tied together, run through the USBLC6's two ESD channels, then through 27Ω
series resistors to the MCU-side nets. **Never place both blocks** on one
board; pick this one the moment the MCU speaks USB.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.V5` (default; `vbusNet` prop overrides) | 5V VBUS rail out |
| `net.GND` | ground (both GND pins and all four shell tabs) |
| `net.USB_DP` (default; `dpNet` prop overrides) | MCU-side D+, after the series resistor |
| `net.USB_DM` (default; `dmNet` prop overrides) | MCU-side D−, after the series resistor |

Internal to the block: `USB_DP_CONN` / `USB_DM_CONN` are the connector-side
nets between the receptacle, the ESD array, and the series resistors. Do not
trace to them from the board — they exist so the testbench can prove the series
resistors really separate the connector from the MCU. SBU1/SBU2 are marked
do-not-connect. Pairs directly with `rp2040-core`, which drives the same
`USB_DP`/`USB_DM` net names.

## Rail budget

Same as `usb-c-power`: 5V at up to 3A advertised by the 5.1k/5.1k Rd sink pair
(USB-C spec §4.5.1.2); budget conservatively **5V @ 1.5A** for JLC 1oz 2-layer
boards and feed `ldo-3v3` for logic. VBUS bulk stays at 10uF (USB 2.0 inrush
limit). The data pair carries no meaningful current — its budget is impedance,
not amps.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| J1 | TYPE-C-31-M-12 | C165948 | SMD+TH hybrid | no | $0.16, 336k stock |
| R1, R2 | 0402WGF5101TCE, 5.1kΩ ±1% | C25905 | 0402 | yes | $0.0004, 8.45M stock — CC pulldowns |
| R3, R4 | 0402WGF270JTCE, 27Ω ±5% | C25100 | 0402 | **no** | $0.0005, 273k stock — D± series |
| U1 | USBLC6-2SC6 | C2687116 | SOT-23-6 | no | $0.035, 231k stock |
| C1 | CL21A106KAYNNNE, 10uF X5R | C15850 | 0805 | yes | $0.009 — VBUS bulk |

**Cost flag:** C25100 is an *extended* part — a sub-cent resistor that drags a
~$3 loading fee onto the BOM. A Basic 27Ω 0402 substitute would pay for itself
on any run; finding one is a `parts-book --lookup` job, and swapping it is a
block edit (same 0402 footprint, so layout survives).

## Design-rule notes

- **27Ω vs 27.4Ω:** the RP2040 hardware design guide specifies 27.4Ω
  (`circuitlib.tables.USB_DP_DM_SERIES_OHMS`); this block ships the E24 27Ω
  part. Inside tolerance for full-speed USB, and a deliberate sourcing choice —
  recorded here so the delta is never mistaken for a transcription error.
- Both orientations of the pair are tied at the connector (DP1+DP2, DM1+DM2) —
  that is what makes the cable reversible; it is not a short.
- ESD channel mapping here differs from `usb-c-power`: channel 1 (IO1/IO1B)
  protects D+, channel 2 (IO2/IO2B) protects D−, because the data lines are the
  exposed pins that matter once the port carries data.
- The D± pair should be routed as a differential pair of matched length with
  the series resistors close to the MCU; the netlist cannot express that, so it
  belongs to the craft pass on `_pcb.png`.
- Default refdes J1, R1, R2, U1, C1 are shared with `usb-c-power` (the two are
  mutually exclusive) plus R3, R4 for the series pair.
- The receptacle carries a routing keepout over its own belly (it comes with
  the shared `UsbCConnector` footprint, so it moves and rotates with J1). See
  `usb-c-power/BLOCK.md` — that keepout is what keeps GND off the alignment
  drills, and the same note explains why a board with a copper pour must go
  through `GndPour`/`POUR_CUTOUT_MARGIN_MM` from `blocks/glue` instead of a
  copied margin.

## Provenance

- Connector and ESD components are imported verbatim from
  `blocks/usb-c-power/usb-c-power.tsx` (`UsbCConnector`, `Usblc6`) — one land
  pattern, one pin map, one place to fix.
- Land pattern for C165948: exact EasyEDA footprint imported 2026-08-10 via
  `tscircuit-cli import C165948 --jlcpcb` (best footprinter match was 81.35%
  IoU, so the exact pattern was kept), committed inline.
- Series-resistor value and the ESD-then-series ordering follow the RP2040
  hardware design guide's minimal USB device example (r5 recon read the
  `seveibar/rp2040-module` port of it as reference; nothing is imported).

