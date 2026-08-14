# rp2040-core — the RP2040 minimal system

**Function:** the brain. A complete RP2040 minimal design per the Raspberry Pi
hardware design guide: the MCU, its mandatory QSPI boot flash, the 12MHz
crystal, full decoupling, the RUN pull-up with a reset button, and the BOOTSEL
button, plus a three-pad SWD bring-up port. Everything the chip needs to come
up, enumerate, and recover from a bad image; nothing it doesn't.
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
| `net.GND` | ground (QFN thermal pad is the GND pin; TP3 is the debug return) |
| `net.DVDD` | the 1.1V core rail, **generated on-chip** (VREG_VOUT) — never drive it |
| `net.USB_DP` | USB D+ — pair with `usb-c-data`, which drives the same name |
| `net.USB_DM` | USB D− — ditto |
| `net.SWCLK` | SWD clock, physically available at TP1 |
| `net.SWD` | SWD data, physically available at TP2 |

GPIOs are **not** netted by the block — trace them directly off the chip
refdes: `.U3 > .GPIO5`. ADC-capable pins are labelled both ways
(`GPIO26_ADC0` / `GPIO26`). The default chip refdes is `U3`; override via the
`u` prop (and `flash` / `xtal` for U4 / Y1) if a board ever carries two.
Every composition must also provide `debugPortPcbX` / `debugPortPcbY` in the
core's local coordinate system. There is intentionally no default: a parent
group can rotate an apparently outboard default straight into another part.
It must likewise allocate distinct `debugSwclkBoundaryRef` /
`debugSwdBoundaryRef` hidden-copper references such as N1/N2. These are
mask-covered internal nodes, not debug probes. The fixed 0.15mm
segments leave the 0.4mm-pitch QFN row perpendicularly; only after those
boundaries does the route widen to the preferred 0.25mm board-level debug
width (`debugSignalTraceWidthMm`).
For a production native USB pair, set `emitUsbNetLeaves={false}` and compose
the `UsbDeviceDifferentialPair` exported by `usb-c-data`; the helper connects
the package pins directly to the two series resistors instead of selecting an
aggregate named net.
`buttonVariant="compact"` selects the validated 3×2mm two-pin BOOTSEL/RESET
parts when the board needs the smaller footprint; omission retains the Basic
4-pad switches for compatibility.

## Rail budget

- **V3_3:** budget **~100mA** for the core plus flash as a planning figure —
  **an estimate**, not a datasheet number; USB-attached designs are dominated
  by whatever the board hangs off the GPIOs, not by the MCU. `ldo-3v3` (≤500mA
  budget) covers this comfortably.
- **DVDD (1.1V):** supplied by the RP2040's internal regulator out of
  `VREG_IN` (tied to 3V3 here). It feeds only the core; its current ceiling is a
  datasheet limit that is **not re-verified here**. Nothing external may load
  it.
- Decoupling as shipped: 8× 100nF on the 3V3 supply pins (IOVDD1-6,
  USB_VDD, and ADC_AVDD); an independent 1uF at VREG_IN; 100nF at each of
  DVDD1 and DVDD2; 1uF at VREG_OUT; 100nF at the flash; and 10uF bulk on
  3V3. The values and placement are both part of the block contract:
  the routed regression measures the compiled authored pin-to-cap edges and
  keeps every power-consuming U3/U4 pad within 2mm of its same-rail capacitor.
  Capacitor rail pads then enter one acyclic 0.8mm V3_3 tree through bounded
  0.2mm necks; DVDD is a separate local VREG_OUT-to-cap-to-pin tree.

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
| SW2, SW3 | TPT-2C1 | C2828561 | SMD, 3×2mm | no | compact option (`buttonVariant="compact"`); Extended |
| TP1–TP3 | SWCLK / SWD / GND bring-up pads | — | 1.5mm PTH, 0.8mm drill, 2.54mm pitch | — | DNP copper feature; no part to source |
| C4–C11 | CL05B104KO5NNNC, 100nF X7R | C1525 | 0402 | yes | 3V3 decoupling, 8 places |
| C12 | CL05B104KO5NNNC, 100nF | C1525 | 0402 | yes | DVDD2 local bypass |
| C13 | CL05B104KO5NNNC, 100nF | C1525 | 0402 | yes | DVDD1 local bypass |
| C14 | CL05B104KO5NNNC, 100nF | C1525 | 0402 | yes | flash VCC |
| C15, C16 | 0402CG150J500NT, 15pF C0G | C1548 | 0402 | yes | crystal load caps |
| C17 | CL21A106KAYNNNE, 10uF X5R | C15850 | 0805 | yes | 3V3 bulk |
| C25 | CL05A105KA5NQNC, 1uF | C52923 | 0402 | yes | VREG_OUT local bypass |
| C26 | CL05A105KA5NQNC, 1uF | C52923 | 0402 | yes | VREG_IN local bypass |

Three extended lines (U3, Y1, and — via `usb-c-data` — the connector class)
carry the ~$3-per-line JLC loading fee; the flash, every passive, and both
buttons are Basic.

## Design-rule notes

- **The flash is not optional.** The RP2040 has no internal program store; U4
  on QSPI is part of the minimal system, and `QSPI_SS` must be free to float
  high at reset — which is why BOOTSEL goes through R13 and the testbench
  asserts `QSPI_SS` is *not* connected to GND at rest.
- **Flash placement and routing are one cluster.** U4 sits beyond the QFN's
  north edge, where pins 51–56 leave the package. The QSPI clock and both
  oscillator nets route first. The five remaining edges then accumulate in
  the measured IO3→IO2→IO1→IO0→CS order, one board-owned phase per edge and
  one shared board-global corridor. This avoids asking one portfolio solve to
  discover a route order that exact replay already proved. Later rail/GPIO
  copper routes around all six critical phases. The compiled regression holds
  QSPI clock to ≤25mm/≤1 via and every QSPI data or select net to ≤35mm/≤2
  vias.
- **The local placement is layer-relative.** Selecting `layer="bottom"`
  mirrors every block-owned X coordinate and footprint rotation together with
  the fixed QFN escape geometry.  This keeps each decoupler beside the same
  physical supply-pad edge instead of merely moving the footprints to the
  other copper face. The placement-owned VREG_OUT→C25, DVDD1→C13, and
  DVDD2→C12 branches are explicit 0.2mm top/bottom copper (matching the
  selected layer), contain no vias, and stay within 2mm; VREG_IN→C26 has the
  same local contract on V3_3. Their
  compiled top- and bottom-side regressions check the real pad endpoints and
  first perpendicular escape segment. Board-owned
  coordinates such as the outboard debug port and absolute phase regions are
  intentionally not mirrored by this block.
- **The board owns every routing-phase region.** An
  `<autoroutingphase>` `region` uses absolute board coordinates; it is not
  translated or rotated with this block. Each composition should declare a
  board-global rectangle for each critical phase, enclosing that phase's
  endpoints and a measured turn corridor. `criticalRoutingPhaseIndices`
  assigns the clock plus five ordered QSPI edges; each QSPI phase uses the same
  consumer-authored endpoint corridor and exact-checks the accumulated copper.
  The explicit V3_3 tree can additionally take board-owned geographical branch,
  trunk, and neck phases through `powerRoutingPhaseIndices`; this partitions
  already-authored two-port copper, never an inferred Steiner net.
  BOOTSEL/RUN/SWD use `controlRoutingPhaseIndex`. Do not hide any bounds inside
  `Rp2040Core`: a second instance or a
  rotated parent needs different boxes.
  The routed golden bench demonstrates the contract; product boards choose and
  regression-test their own collision-free global coordinates.
- **BOOTSEL is press-to-boot:** QSPI_SS → 1k (R13) → SW2 → GND. Holding it
  during reset pulls SS low through the resistor and the chip enumerates as a
  mass-storage device. The 1k is what keeps that from being a hard short on the
  live QSPI bus.
- **RESET:** R12 holds RUN at 3V3; SW3 shorts RUN to ground. RUN must never be
  left floating (the pin is marked `mustBeConnected`).
- **Button variant changes topology as well as area.** The default switch has
  redundant 1+2 / 3+4 terminals; TPT-2C1 has only pin1/pin2. A compiled
  compact-core regression proves BOOTSEL and RUN remain isolated from GND at
  rest and both two-pin parts resolve to C2828561.
- **TESTEN is tied to GND** — a factory-test pin; floating it is a latent
  bring-up failure.
- **SWD is physically reachable:** TP1/TP2/TP3 expose SWCLK/SWD/GND on
  2.54mm pitch for pogo tooling or temporary wire leads. They are marked DNP and
  intentionally carry no LCSC number. The board must place them outboard via
  the required `debugPortPcbX` / `debugPortPcbY` props; this cannot safely be
  defaulted inside a block that may itself be rotated. The board also owns the
  globally unique copper-boundary refs. Their 0.15mm QFN escapes are explicit,
  measured fine-pitch exceptions; the boundary-to-probe segments default to
  0.25mm.
- **Crystal:** XIN is driven directly from Y1.pin1; XOUT returns through the 1kΩ
  series resistor R11 to Y1.pin3 (drive-level limiting per the design guide).
  Y1 is rotated below the QFN, C15 branches at Y1.pin1, and C16/R11 flank the
  return pad; the compiled regression holds both crystal-terminal nets to
  ≤10mm with zero vias.
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
- Default refdes U3, U4, Y1, R11–R13, SW2, SW3, TP1–TP3, C4–C17, C25,
  and C26 are the global v1 allocation. `sw-tact` defaults to SW1 precisely
  so it never collides.

## Provenance

- Design ported from `seveibar/rp2040-module` (the one registry package that is
  a real, design-guide-faithful RP2040 board — 2 stars, written in the old
  hooks API and dependent on the cloud autorouter) to the current JSX API,
  2026-08-10. **Read as a reference, never imported** (r5 §5: registry packages
  are mutable, unsigned, and network-fetched).
- Topology follows "Hardware design with RP2040", minimal design example:
  decoupling per supply pin, 1k XOUT series, RUN pull-up, BOOTSEL via 1k.
- RP2040 datasheet, sections 2.9.2–2.9.4 (separate DVDD bypassing and the
  on-chip regulator): <https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf>
- Hardware Design with RP2040, sections 2.1.2–2.1.3 (1uF at VREG_IN and
  VREG_OUT): <https://datasheets.raspberrypi.com/rp2040/hardware-design-with-rp2040.pdf>
- Land patterns: footprinter builtins matched to the imported EasyEDA patterns
  — `qfn56_thermalpad3.1mmx3.1mm_p0.4mm…` for C2040 and
  `soic8_pillpads_w9.3102mm…pin1location(leftside,bottom)` for C97521
  (`tscircuit-cli import`, 2026-08-10); no network at build time.
