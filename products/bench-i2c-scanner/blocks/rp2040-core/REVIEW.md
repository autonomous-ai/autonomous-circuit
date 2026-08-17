# Block sign-off — `rp2040-core`

**This block goes into every board a user generates that needs it**, unchanged —
the AI composes blocks, it never edits them. So an error here is not one bad
board, it is a bad board every time. It is also the specific class of error our
automated checks provably cannot catch, which is why this sheet matters more
than any individual board in the packet. Anything you find gets fixed once and
is then right forever.

Source: [`rp2040-core.tsx`](./rp2040-core.tsx) · Datasheet: [`BLOCK.md`](./BLOCK.md)

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

# rp2040-core — the RP2040 minimal system

**Function:** the brain. A complete RP2040 minimal design per the Raspberry Pi
hardware design guide: the MCU, its mandatory QSPI boot flash, the 12MHz
crystal, full decoupling, the RUN pull-up with a reset button, and the BOOTSEL
button. Everything the chip needs to come up and enumerate; nothing it doesn't.
Pick this brain when the device is **USB-attached only** (HID, serial, a device
a computer drives) — anything needing Wi-Fi or BLE wants a certified radio
module instead.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending — the crystal load and the tactile pad
pairing are the two things to confirm).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.V3_3` | logic supply in — feed it from `ldo-3v3` |
| `net.GND` | ground (QFN thermal pad is the GND pin) |
| `net.DVDD` | the 1.1V core rail, **generated on-chip** (VREG_VOUT) — never drive it |
| `net.USB_DP` | USB D+ — pair with `usb-c-data`, which drives the same name |
| `net.USB_DM` | USB D− — ditto |
| `net.SWCLK` | SWD clock, brought out for a debug header |
| `net.SWD` | SWD data, ditto |

GPIOs are **not** netted by the block — trace them directly off the chip
refdes: `.U3 > .GPIO5`. ADC-capable pins are labelled both ways
(`GPIO26_ADC0` / `GPIO26`). The default chip refdes is `U3`; override via the
`u` prop (and `flash` / `xtal` for U4 / Y1) if a board ever carries two.

## Rail budget

- **V3_3:** budget **~100mA** for the core plus flash as a planning figure —
  **an estimate**, not a datasheet number; USB-attached designs are dominated
  by whatever the board hangs off the GPIOs, not by the MCU. `ldo-3v3` (≤500mA
  budget) covers this comfortably.
- **DVDD (1.1V):** supplied by the RP2040's internal regulator out of
  `VREG_IN` (tied to 3V3 here). It feeds only the core; its current ceiling is a
  datasheet limit that is **not re-verified here**. Nothing external may load
  it.
- Decoupling as shipped: 8× 100nF on the 3V3 rail (one per supply pin class —
  IOVDD1-6, USB_VDD, ADC_AVDD), 100nF + 1uF on DVDD, 100nF at the flash, 10uF
  bulk on 3V3. The *values* are frozen here; **which cap sits next to which
  pin is a layout property the netlist cannot express** — that is checked on
  `_pcb.png` in the craft pass.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| U3 | RP2040 | C2040 | LQFN-56 (7×7) | no | $0.99, 54.0k stock — extended, ~$3 loading fee |
| U4 | W25Q128JVSIQ, 128Mbit QSPI flash | C97521 | SOIC-8-208mil | yes | $2.45, 95.0k stock |
| Y1 | ABM8-272-T3, 12MHz | C20625731 | SMD3225-4P | no | $0.33, 15.7k stock — extended |
| R11 | 0402WGF1001TCE, 1kΩ | C11702 | 0402 | yes | XOUT series |
| R12 | 0402WGF1002TCE, 10kΩ | C25744 | 0402 | yes | RUN pull-up |
| R13 | 0402WGF1001TCE, 1kΩ | C11702 | 0402 | yes | BOOTSEL series into QSPI_SS |
| SW2 | TS-1187A-B-A-B | C318884 | SMD-4P, 5.1×5.1mm | yes | BOOTSEL |
| SW3 | TS-1187A-B-A-B | C318884 | SMD-4P, 5.1×5.1mm | yes | RESET (pulls RUN low) |
| C4–C11 | CL05B104KO5NNNC, 100nF X7R | C1525 | 0402 | yes | 3V3 decoupling, 8 places |
| C12 | CL05A105KA5NQNC, 1uF | C52923 | 0402 | yes | DVDD |
| C13 | CL05B104KO5NNNC, 100nF | C1525 | 0402 | yes | DVDD |
| C14 | CL05B104KO5NNNC, 100nF | C1525 | 0402 | yes | flash VCC |
| C15, C16 | 0402CG150J500NT, 15pF C0G | C1548 | 0402 | yes | crystal load caps |
| C17 | CL21A106KAYNNNE, 10uF X5R | C15850 | 0805 | yes | 3V3 bulk |

Three extended lines (U3, Y1, and — via `usb-c-data` — the connector class)
carry the ~$3-per-line JLC loading fee; the flash, every passive, and both
buttons are Basic.

## Design-rule notes

- **The flash is not optional.** The RP2040 has no internal program store; U4
  on QSPI is part of the minimal system, and `QSPI_SS` must be free to float
  high at reset — which is why BOOTSEL goes through R13 and the testbench
  asserts `QSPI_SS` is *not* connected to GND at rest.
- **BOOTSEL is press-to-boot:** QSPI_SS → 1k (R13) → SW2 → GND. Holding it
  during reset pulls SS low through the resistor and the chip enumerates as a
  mass-storage device. The 1k is what keeps that from being a hard short on the
  live QSPI bus.
- **RESET:** R12 holds RUN at 3V3; SW3 shorts RUN to ground. RUN must never be
  left floating (the pin is marked `mustBeConnected`).
- **TESTEN is tied to GND** — a factory-test pin; floating it is a latent
  bring-up failure.
- **Crystal:** XIN is driven directly from Y1.pin1; XOUT returns through the 1kΩ
  series resistor R11 to Y1.pin3 (drive-level limiting per the design guide).
  The testbench asserts XIN and Y1.pin3 stay isolated — the series resistor
  must be in the path. Two 15pF loads in series (≈7.5pF) plus a few pF of stray
  land near the crystal's declared 10pF load; **whether that matches the real
  ABM8-272-T3 specified load capacitance is unverified** — check the first
  article's frequency before a production run.
- **Y1's four_pin variant:** pin2 and pin4 are the ground/case pads, both tied
  to GND.
- Board-level: the GPIO fan-out, USB pair routing, and mounting holes are the
  board's job. Pair with `usb-c-data` for a USB device; `usb-c-power` alone
  leaves the MCU with no host connection.
- Default refdes U3, U4, Y1, R11–R13, SW2, SW3, C4–C17 are the global v1
  allocation. `sw-tact` defaults to SW1 precisely so it never collides.

## Provenance

- Design ported from `seveibar/rp2040-module` (the one registry package that is
  a real, design-guide-faithful RP2040 board — 2 stars, written in the old
  hooks API and dependent on the cloud autorouter) to the current JSX API,
  2026-08-10. **Read as a reference, never imported** (r5 §5: registry packages
  are mutable, unsigned, and network-fetched).
- Topology follows "Hardware design with RP2040", minimal design example:
  decoupling per supply pin, 1k XOUT series, RUN pull-up, BOOTSEL via 1k.
- Land patterns: footprinter builtins matched to the imported EasyEDA patterns
  — `qfn56_thermalpad3.1mmx3.1mm_p0.4mm…` for C2040 and
  `soic8_pillpads_w9.3102mm…pin1location(leftside,bottom)` for C97521
  (`tscircuit-cli import`, 2026-08-10); no network at build time.

