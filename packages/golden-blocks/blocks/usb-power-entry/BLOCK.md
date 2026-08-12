# usb-power-entry — current-limited USB VBUS to board V5

**Function:** bridges connector-side `VBUS_RAW` to the board's `V5` rail with
a TPS2553DBVR precision current-limited power-distribution switch. Its built-in
soft start controls inrush into downstream bulk capacitance. The fixed 59kΩ 1%
ILIM resistor guarantees that the maximum trip threshold does not exceed
500mA; the same TI equations give a 400.6mA minimum threshold, which is the
normal-operation budget consumers must stay below.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; **not yet
hardware-verified**. U7 uses the exact C55266 supplier footprint.

## Pin contract

| Net | Meaning |
|---|---|
| `net.VBUS_RAW` | in: connector-side USB VBUS, raw attach capacitance kept ≤10uF |
| `net.V5` | out: controlled-rise, hardware-current-limited board rail |
| `net.V3_3` | in: 100kΩ pull-up for open-drain FAULT |
| `net.USB_POWER_FAULT` | out: pulled-up active-low fault indication, also on DNP TP10 |
| `net.GND` | in: ground |

Props: `u`, `cIn`, `rIlim`, `rFault`, `faultTestpoint`, `rawNet`, `outputNet`,
`faultNet`, `externalPowerTrunkPort`, `externalRawPowerTrunkPort`,
`externalFaultPullupPort`, `layer`, and group PCB/schematic coordinates. The
current-limit value and FAULT pull-up topology are frozen.

For one board-authored VBUS_RAW distribution tree, set
`externalRawPowerTrunkPort="IN"` and attach a bounded local neck at
`.C24 > .pin1` (or the overridden `cIn` ref). This suppresses only C24's
ordinary named-net boundary; U7 IN/EN and the required 100nF bypass remain
one local tree. The board must join that physical pad to its wide raw-power
tree and retain one sole marked VBUS_RAW boundary.

For one board-authored V3_3 distribution tree, set
`externalFaultPullupPort="R32"` and attach the tree at `.R32 > .pin2` (or the
overridden `rFault` ref). This suppresses only the ordinary named-net pull-up
leaf; R32 remains 100kΩ and its 0.25mm FAULT/probe branch is unchanged. The
board must supply the missing physical attachment and retain one sole marked
V3_3 boundary. `externalPowerTrunkPort="OUT"` remains independently usable for
the protected V5 output.

`layer="bottom"` is an exact block-local X mirror: the asymmetric C55266
footprint remains at the origin while C24, R31, R32, and TP10 exchange sides;
C24 remains at complementary 0° rotation and the 180° resistor rotations
remain 180°.
Consequently IN→C24 and ILIM→R31 retain their bounded local geometry, and the
0.15mm FAULT toe still reaches TP10 before widening to the ordinary 0.25mm
signal class. EN→C24 uses a mirrored, block-local 0.20mm dogleg that clears
U7's intervening GND pad at the board's 0.15mm trace-to-pad rule while
remaining within its 3mm authored bound.

## Frozen topology

- U7 pin 1 = IN, pin 2 = GND, pin 3 = active-high EN, pin 4 = open-drain
  FAULT, pin 5 = ILIM, pin 6 = OUT.
- EN is tied to IN, so the load switch always starts through its built-in soft
  start when a source attaches.
- C24 is 100nF directly at IN, satisfying TI's ≥0.1uF local input-bypass rule.
- R31 is exactly 59kΩ ±1% from ILIM to GND. TI section 10.2.1.2.2 calculates
  this as a 500mA maximum and 400.6mA minimum current-limit threshold.
- FAULT has a 100kΩ pull-up to V3_3, a named net, and a DNP copper probe pad;
  it is never a semantically invisible floating output.
- TP10 is the explicit authored-tree boundary for `net.USB_POWER_FAULT`, so
  the 0.15mm U7 escape and 0.25mm R32/probe branches cannot be re-MST'd into
  one silently narrowed aggregate route.
- Bulk capacitance for the LDO and pixel ring belongs downstream on V5. The
  USB connector block's raw capacitor plus C24 must total no more than 10uF.

## Parts (pinned; verified 2026-08-12)

| Refdes | Part | LCSC | Package | Note |
|---|---|---|---|---|
| U7 | Texas Instruments TPS2553DBVR | C55266 | DBV / SOT-23-6 | ACTIVE; exact imported copper |
| C24 | 100nF X7R | C1525 | 0402 | local IN bypass |
| R31 | 0402WGF5902TCE, 59kΩ ±1% | C32297 | 0402 | frozen ILIM network; 634,619 stock checked 2026-08-12 |
| R32 | 100kΩ ±1% | C25741 | 0402 | FAULT pull-up |
| TP10 | DNP copper pad | — | 0.8mm pad | fault probe, BOM-exempt |

## Provenance

- TI product/lifecycle: <https://www.ti.com/product/TPS2553>
- TI datasheet (pinout, built-in soft start, input bypass, ILIM equations and
  59kΩ worked example): <https://www.ti.com/lit/ds/symlink/tps2553.pdf>
- LCSC exact part: <https://www.lcsc.com/product-detail/C55266.html>
- R31 sourcing lock: <https://www.lcsc.com/product-detail/C32297.html>. The
  earlier exact-value C163459 fell to 17 units on 2026-08-12; C32297 preserves
  59kΩ ±1%, 0402, 50V, 62.5mW, and ±100ppm/°C with 634,619 units checked.
- Exact C55266 footprint imported on 2026-08-11 with
  `tscircuit-cli import C55266 --jlcpcb --use-exact-footprint`; compiled tests
  freeze all six pad centres, sizes, and pin hints.
