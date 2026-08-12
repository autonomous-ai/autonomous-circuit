# usb-c-data — USB-C power + USB 2.0 data

**Function:** power entry **and** the USB 2.0 full-speed data pair. A
**superset of `usb-c-power`**: same receptacle, same 5.1k CC pulldowns, same
raw-VBUS capacitor, same default refdes — plus both connector orientations of D+/D−
tied together, run through the USBLC6's two ESD channels, then through 27Ω
series resistors to the MCU-side interface. **Never place both blocks** on one
board; pick this one the moment the MCU speaks USB.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.VBUS_RAW` (default; `vbusNet` prop overrides) | connector-side VBUS; feed `usb-power-entry` before bulk loads |
| `net.GND` | ground (both GND pins and all four shell tabs) |
| `net.USB_DP` (legacy default; `dpNet` prop overrides) | MCU-side D+, emitted only when `emitMcuNetLeaves` is not false |
| `net.USB_DM` (legacy default; `dmNet` prop overrides) | MCU-side D−, emitted only when `emitMcuNetLeaves` is not false |

There are no connector-side aggregate nets. Each reversible pair is an
explicit physical tree: the connector orientation pads share one deliberate
manual crossover, the selected pad enters the first USBLC6 package pad, and
the internally connected mate pad exits to the series resistor. No PCB trace
bypasses the clamp. SBU1/SBU2 are marked do-not-connect.

Production compositions set `emitMcuNetLeaves={false}` here and
`emitUsbNetLeaves={false}` on `Rp2040Core`, then instantiate the exported
`UsbDeviceDifferentialPair`. That helper authors two direct resistor-to-MCU
traces and a native `<differentialpair>` constraint; a named-net selector is
intentionally forbidden because it is not point-to-point. `pairRules` is
required and carries the board-stack-up gap, routed skew, and maximum
uncoupled-length limits. The block applies the same rules to the two direct
connector-to-ESD and ESD-to-resistor sections.

For deterministic dense-port routing, a product may assign five ordered
phases: `connectorPairRoutingPhaseIndex`, `seriesPairRoutingPhaseIndex`,
`cc1RoutingPhaseIndex`, `cc2RoutingPhaseIndex`, then
`powerRoutingPhaseIndex`. Each falls back to the older shared
`pairRoutingPhaseIndex` or `localRoutingPhaseIndex`, so existing callers keep
their API behavior. The reviewed routed proof uses phases 0–4 in exactly that
order; combining the two CC routes with the local power portfolio is not an
equivalent layout contract.

The board also allocates three globally unique hidden nodes through
`vbusBoundaryRefs` and `vbusRailNodeRef`. They form one authored raw-VBUS tree:
short 0.2mm connector/clamp/cap leaves, a 0.8mm top-bottom-top crossover with
0.8/0.5mm vias, and one 0.8mm boundary to `net.VBUS_RAW`.

The two connector VBUS nodes sit at block-local `(±3.2, 3.4)`. Their fixed
two-segment pad-to-node doglegs are about 1.78mm, below the 2mm limit. R1/R2
sit at `(∓5.2, 10.5)`, keeping both 0.25mm CC escapes outside the VBUS nodes,
wide rail, and R3/R4 package copper. This
spacing is electrical geometry, not decorative placement: do not pull the
pulldowns back toward the receptacle without rerunning the routed-clearance
proof.

The two 0.25mm CC routes are authored from the exact-clean top solution and
mirrored with the block. CC1 stays on its pad centerline through local
`(-1.25, 2.92)` before turning through `(-5.71, 7.38)`; CC2 stays at local
X `1.75` through Y `7.56`. Fixing these two ordinary-signal escapes avoids a
bottom-only search choice that otherwise grazes the adjacent unused Type-C
pad even though the exact X-mirror is clear.

R3/R4 sit at local `(∓1.44, 8.95)` with their clamp-side pin-1 pads facing
inward, retaining symmetric direct ESD-to-series pair sections.

C1 sits at local `(-4.4, 9.15)` and is rotated 180° so its GND pad opens
directly into the material plane and its VBUS pad faces the local rail. This
avoids a floating pour island, clears the 0.25mm CC1 diagonal, and stays inside the 3mm
local-cap neck without adding a via.

The connector-orientation crossovers start at local `(∓0.38, 3.6)` and return
through `(±1.6, 1.0)`, below the fine-pitch pad row and between the two exact
alignment-drill guards. They then enter DP2/DM2 vertically on each pad's own
centerline. The central return pocket leaves both outer 0.25mm CC escapes and
the direct DP/DM lanes open while maintaining at least 0.20mm copper-to-drill
clearance from both NPTHs.

`layer="bottom"` preserves this reviewed geometry as an exact block-local X
mirror. Component centers exchange left/right, the USBLC6's 90° rotation is
complemented to 270°, and every authored DP/DM and VBUS path vertex is
mirrored before compilation. The signal crossovers still start and finish on
the component face and use 0.6/0.3mm vias; the power crossovers still use
0.8/0.5mm vias. Callers always pass the same top-authored hidden-node
coordinates—the block owns the face transform.

## Rail budget

Rd only declares this board as a sink; without measuring a Type-C current
advertisement, the board must budget the USB 2.0 default 500mA after
enumeration. The connector-side 1uF plus `usb-power-entry`'s 100nF local input
bypass totals 1.1uF, below the 10uF raw-attach limit. All LDO and load bulk
belongs after the controlled-rise switch. The data pair carries no meaningful
current — its budget is impedance, not amps.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| J1 | TYPE-C-31-M-12 | C165948 | SMD+TH hybrid | no | $0.16, 336k stock |
| R1, R2 | 0402WGF5101TCE, 5.1kΩ ±1% | C25905 | 0402 | yes | $0.0004, 8.45M stock — CC pulldowns |
| R3, R4 | 0402WGF270JTCE, 27Ω ±5% | C25100 | 0402 | **no** | $0.0005, 273k stock — D± series |
| U1 | USBLC6-2SC6 | C2687116 | SOT-23-6 | no | $0.035, 231k stock |
| C1 | 1uF X5R | C52923 | 0402 | yes | raw attach capacitance |

**Cost flag:** C25100 is an *extended* part — a sub-cent resistor that drags a
~$3 loading fee onto the BOM. A Basic 27Ω 0402 substitute would pay for itself
on any run; finding one is a `parts-book --lookup` job, and swapping it is a
block edit (same 0402 footprint, so layout survives).

## Design-rule notes

- **27Ω vs 27.4Ω:** the RP2040 hardware design guide specifies 27.4Ω
  (`circuitlib.tables.USB_DP_DM_SERIES_OHMS`); this block ships the E24 27Ω
  part. Inside tolerance for full-speed USB, and a deliberate sourcing choice —
  recorded here so the delta is never mistaken for a transcription error.
- Both orientations of the pair are tied by the explicit connector-local
  crossovers (DP1+DP2, DM1+DM2) — that is what makes the cable reversible.
- ESD channel mapping here differs from `usb-c-power`: channel 1 (IO1/IO1B)
  protects D+, channel 2 (IO2/IO2B) protects D−, because the data lines are the
  exposed pins that matter once the port carries data.
- The USBLC6 is rotated as a flow-through package: IO1/IO2 face the receptacle,
  IO1B/IO2B face a symmetric R3/R4 row, and both lane orders are preserved.
  This relative package geometry is part of the block contract; a consumer
  may translate or rotate the whole block but must not independently scatter
  the clamp and series parts.
- Top and bottom instances are one geometry contract, not two independently
  routed layouts: for equal group origins every compiled bottom port and
  authored copper vertex is `(-topX, topY)` in block-local coordinates.
- The block and `UsbDeviceDifferentialPair` express direct physical sections
  with native differential-pair constraints. A product still derives its
  board-global routing regions and gap from its placed geometry and stack-up,
  then verifies total connector-to-MCU routed length/skew across all sections.
- Default refdes J1, R1, R2, U1, C1 are shared with `usb-c-power` (the two are
  mutually exclusive) plus R3, R4 for the series pair.
- Each receptacle alignment drill carries its own exact local guard in the
  shared `UsbCConnector` footprint, so holes and guards move, rotate, and
  mirror with J1. The guards hold every trace and via at least 0.20mm from the
  NPTH drill edges without erasing the central reversible-data routing pocket.
  See `usb-c-power/BLOCK.md`; its separate pour note still applies to boards
  that add copper pours.

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
