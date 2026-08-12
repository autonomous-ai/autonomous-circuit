# Harness Puck

A 70mm round USB-C desk controller for the Autonomous Harness. The RP2040
presents a USB device to the host, two front-panel buttons provide delegate and
mode input, and eight WS2812B pixels show agent state.

## Current status

**Do not order this board yet. `fab.ready` remains false.** The source and
reusable contracts have been corrected, but a source-fresh routed artifact,
parsed verification, and KiCad DRC/export must all pass before the example is
fab-ready. A CLI exit code is not evidence: `circuit.json` and asynchronous
router output are the gates.

There is also an unresolved power-entry question: approximately 40uF is
currently connected directly to USB VBUS (C1, C2, C22, and C23). That requires
a verified USB-C inrush argument or a sourced current-limited/load-switch
solution. Pixel bulk will not be silently deleted just to clear the finding.

`DESIGN-REVIEW.md` records the earlier repair loop. Its old two-regulator,
3.3V-pixel, trace-only-GND conclusions are historical and no longer describe
the source.

## Source and generated artifacts

`boards/main.tsx` is the board program. `product.json` is the measurable
product/layout intent, and `parts.json` is generated and wholly owned by
parts-book. Compiled board, review, and fab files are derived artifacts; never
repair them by hand.

Build from the repository root with the pinned offline toolchain:

```bash
CIRCUIT_PARTS_ENGINE=off packages/circuitpy/.venv/bin/python \
  skills/circuitcode/scripts/circuit \
  /absolute/path/to/examples/harness-puck/boards/main.tsx
```

## Architecture

| Function | Reusable composition |
|---|---|
| USB power and data | `usb-c-data`: J1, CC pulldowns, USBLC6 ESD, and 27Ω D+/D− series resistors |
| 3.3V logic | One `ldo-3v3` instance (U2/C2/C3) for the RP2040 domain only |
| MCU | `rp2040-core`: RP2040, QSPI flash, crystal, BOOTSEL/RESET, local bypassing, and reachable SWD pads |
| Pixel logic boundary | `ws2812-level-shifter`: exact C7484 SN74AHCT1G125DBVR footprint, hard-low `/OE`, and local 100nF bypass |
| Fleet display | Eight `Ws2812Pixel` instances on V5, one local 100nF per VDD pad, and one 330Ω first-hop resistor |
| User input | Two `sw-tact` instances for delegate and mode |
| Status | `status-led` on V3_3 |

The pixel architecture is intentionally 5V. A 5V WS2812 input is not
guaranteed by a raw 3.3V GPIO, and generating a separate 3.3V/480mA pixel rail
with an AMS1117 dissipated about 0.82W at full white. The reusable planner now
accounts for loads by supply rail, refuses that topology, and selects the
AHCT-level-shifted V5 chain.

```text
USB-C VBUS / V5
  ├─ U2 AMS1117-3.3 → V3_3 → RP2040 + flash + status LED
  ├─ U6 SN74AHCT1G125 (V5) ← LED_DATA_3V3
  └─ 8 × WS2812B on V5 ← LED_DATA_5V (up to 480mA full white)
```

## Layout contract

The product declares and the artifact verifier measures:

- exact 70.00mm round outline and all-top economic assembly;
- J1 centred on the bottom edge using its cable-insertion datum;
- top and bottom GND pours with 27 explicit stitching vias;
- every one-port GND drop bound to solved material plane copper, with a strict
  maximum fanout length of 2.0mm;
- 0.8mm V5/V3_3 trunks, only short 0.2mm package neck-downs, and 0.8/0.5mm
  power-via outer/drill dimensions;
- 0.25mm ordinary BTN, pixel-data, and pixel-hop signals with no neck-down;
- SWCLK/SWD at 0.25mm after a fixed, perpendicular 0.15mm QFN escape of at
  most 1.0mm;
- USB D+/D−, QSPI, and crystal routes as explicit interface/fine-pitch
  exceptions with their own length/via budgets;
- H1 at `(0, 30)` and the two lower M2 holes at `(−19.75, −11.4)` and
  `(19.75, −11.4)`;
- reachable TP1/TP2/TP3 SWCLK/SWD/GND probe pads. TP8/TP9 are DNP copper-only
  QFN escape boundaries, not user probe points.

Pixel bypass placement is pad-aware, not radial decoration. Each capacitor is
continued 2.0mm beyond its pixel's exact rotated VDD pad; the previous radial
placement was about 6.3mm from VDD. The golden linear chain and this round
composition both have compiled pad-to-cap distance assertions.

## Parts lock

`parts.json` was generated with parts-book and refreshed against the catalog
on 2026-08-11. It contains 22 checked project-library lines: 13 JLC Basic and
9 extended, with no zero-stock entry at refresh time.

The new level-shifter lock is exact:

- LCSC C7484;
- Texas Instruments SN74AHCT1G125DBVR;
- SOT-23-5 / DBV pinout: 1=`/OE`, 2=A, 3=GND, 4=Y, 5=VCC;
- catalog source `jlcsearch`, checked 2026-08-11;
- 1,635 units and $0.091143 unit price at that refresh.

The committed block freezes the supplier-imported five-pad copper geometry;
an assumed generic SOT-23-5 footprint is not accepted.

## Acceptance gates

The board is fab-ready only when one source-fresh run proves all of the
following:

1. zero serialized `*_error`/`*_warning` elements and zero swallowed async
   router errors;
2. all routing phases complete and the RP2040 clock/QSPI/USB/debug budgets pass;
3. every GND fanout is source-bound, no longer than 2.0mm, and connected to a
   solved pour island; top and bottom islands are joined by the declared
   stitches and no different-net pours touch;
4. V5/V3_3 trunk, neck-down, and via dimensions satisfy `product.json`;
5. C7484 pinout, exact footprint, `/OE` tie, bypass distance, and locked BOM
   identity pass;
6. no AMS1117 thermal warning remains and USB VBUS inrush is resolved;
7. KiCad DRC is clean and the Gerber/BOM/CPL packet is regenerated from that
   exact accepted artifact.

## Bring-up intent

After those gates pass and a first article exists: verify V5 at the pixel rail,
V3_3 at C3, and GND at TP3; use TP1/TP2/TP3 for SWD recovery or BOOTSEL/RESET
for UF2 mode. Confirm U6 output reaches a valid 5V-domain high before enabling
the ring, then test pixels in chain order. Hardware fit, USB inrush, crystal
frequency, EMI, thermal rise, switch pad pairing, and enclosure alignment all
remain first-article measurements, not claims inferred from a render.
