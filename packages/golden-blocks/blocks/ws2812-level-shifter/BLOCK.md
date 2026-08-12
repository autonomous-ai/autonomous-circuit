# ws2812-level-shifter — valid 3.3V data for a 5V WS2812 chain

**Function:** translates one 3.3V CMOS GPIO into the 5V logic domain required
by a WS2812 chain, using a SN74AHCT1G125DBVR non-inverting buffer. `/OE` is
hard-low, so the buffer is always enabled, and the block owns its required
100nF bypass capacitor.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; **not yet
hardware-verified**. The C7484 land pattern is the exact supplier footprint,
not a package-name guess.

## Pin contract

| Net | Meaning |
|---|---|
| `net.<inputNet>` (default `LED_DATA_3V3`) | in: 3.3V MCU data |
| `net.<outputNet>` (default `LED_DATA_5V`) | out: 5V-domain data to the chain's 330Ω resistor |
| `net.V5` | in: 4.5–5.5V buffer supply |
| `net.GND` | in: ground and permanent `/OE` low |

Props: `u`, `c`, `inputNet`, `outputNet`, `signalTraceWidthMm`,
`localPowerWidthMm`, `railTrunkWidthMm`, `maxDecouplingLengthMm`, `layer`, and
group PCB/schematic coordinates. The A and Y routes default to the preferred
0.25mm ordinary signal width. VCC→C20 is an authored 0.2mm local path bounded
to 2mm; C20 then exposes one marked 0.8mm V5 boundary. The VCC rail name is
deliberately not configurable: this is the validated 5V WS2812 interface, not
a generic logic-gate escape hatch. `layer="bottom"` mirrors the complete
block-local placement and authored path, so C20 pin 1 continues to face VCC
after the package pad offsets reverse.

## Why this block exists

WS2812B specifies input-high at 0.7 × VDD. At a 5V pixel supply that is 3.5V,
so a 3.3V GPIO is outside the guaranteed input range. Running eight pixels
from an AMS1117-generated 3.3V rail also dissipates about 0.82W in the
regulator at full-white current. This block makes the correct architecture
structural: pixels stay on V5, and only their data crosses voltage domains.

SN74AHCT1G125 at 4.5–5.5V has a 2.0V minimum input-high threshold, so 3.3V is
valid. It is non-inverting: A high produces Y high while `/OE` is low. The
existing 330Ω series resistor belongs **after Y**, before the first pixel.

## Parts (pinned; verified 2026-08-11)

| Refdes | Part | LCSC | Package | Note |
|---|---|---|---|---|
| U6 | Texas Instruments SN74AHCT1G125DBVR | C7484 | DBV / SOT-23-5 | LCSC showed 1,635 in stock at the 2026-08-11 lock refresh |
| C20 | 100nF X7R | C1525 | 0402 | within 1.9mm of U6.VCC |

## Layout contract

- C20 pin 1 faces U6.VCC. The compiled artifact must contain the direct
  U6.VCC→C20.pin1 source edge and pass the product decoupling gate at 2mm.
- The local bypass edge defaults to 0.2mm; the sole named V5 boundary defaults
  to 0.8mm and is marked as an authored rail-tree boundary. The router may
  connect that boundary to the board backbone, but may not re-solve the local
  bypass as part of a generic V5 MST.
- C20 ground remains a direct plane fanout. Signal widths do not determine
  power widths.
- Dedicated top and bottom compiled benches require exact mirrored endpoints,
  identical path length, 0.2mm same-layer copper, and no local via.

## Provenance and frozen decisions

- TI product page and lifecycle: <https://www.ti.com/product/SN74AHCT1G125>
- TI datasheet (pinout, 4.5–5.5V operation, 2.0V VIH minimum, 100nF bypass
  recommendation): <https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf>
- LCSC part lock: <https://www.lcsc.com/product-detail/C7484.html>
- Exact footprint imported on 2026-08-11 with
  `tscircuit-cli import C7484 --jlcpcb --use-exact-footprint`; compiled tests
  freeze the five pad centres, sizes, and pin hints.
- `/OE` is tied directly to GND. This block is intentionally always enabled;
  no floating enable pin and no software-controlled startup state are allowed.
