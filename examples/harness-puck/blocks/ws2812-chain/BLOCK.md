# ws2812-chain — addressable RGB pixels on one GPIO

**Function:** a chain of `count` WS2812B-B/T 5050 pixels driven from a single
MCU pin. Each pixel passes data to the next via DOUT→DIN, so N colours cost one
GPIO. Parametric: `count`, `dinNet`, `rail`, `startIndex`, `pitch`.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; **not yet
hardware-verified** — the 5050 land pattern below is from the datasheet, not
from a reflowed board. First-article check listed under Provenance.

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.<dinNet>` (default `LED_DATA_5V`) | in: translated 5V-domain data |
| `net.<rail>` (default `V5`) | in: pixel supply |
| `net.GND` | in: ground |
| `net.PX_<n>_DIN` | internal: one net per pixel-to-pixel hop |

The final pixel's DOUT lands on an unused `PX_<start+count>_DIN` net — that is
deliberate (a chain can always be extended) and shows up as an
`unconnected` advisory, not an error.

## Rail budget

A WS2812B draws roughly **1mA idle** and up to **~20mA per channel** — so about
**60mA per pixel at full white**. Budget the 5V chain at `count × 60mA` worst
case and check it against the source with `helpers.power_budget()`.

A 4-pixel ring is ~240mA worst case, which a USB-C source handles comfortably.
Past roughly 20 pixels the rail wants its own bulk capacitance and a wider
supply trace — size it with `helpers.trace_width_for()`.

## Parts (pinned; verified 2026-08-10 via jlcsearch unless noted)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| D`n` | WS2812B-B/T | C2761795 | 5050-4P | no | $0.076, 214k stock (r5 recon) |
| C`4n` | 100nF X7R | C1525 | 0402 | yes | one per pixel, adjacent |
| R30 | 330Ω ±1% | C25104 | 0402 | yes | series damping on the first hop |

## Design-rule notes

- **One 100nF per pixel, at most 2.2mm from its VDD pad.** These parts switch three
  constant-current channels fast; shared bulk alone browns out the far end of
  the chain. The compiled block test measures pad-to-cap distance so a future
  layout change cannot quietly turn the bypass into remote bulk capacitance.
- **Series resistor on the first hop only** (330Ω). It damps the reflection on
  the long run from the MCU and protects the GPIO. Later hops are driven by the
  previous LED's output and do not need one.
- **Data routes default to 0.25mm.** That is the ordinary board-level signal
  width, independent of the wider current-carrying V5 rail class.
- **Rail defaults to 5V and data defaults to `LED_DATA_5V`.** WS2812B wants
  VIH ≥ 0.7 × VDD, so a 5V pixel fed directly from 3.3V logic is marginal
  (needs 3.5V). Compose `ws2812-level-shifter` between the MCU GPIO and this
  block. The translator's Y output feeds this block's existing 330Ω resistor.
- Default pitch is 7mm, which clears the 5×5mm body plus its decoupling cap.

## Provenance

- Land pattern: WS2812B-B/T datasheet (5.0 × 5.0mm body, four 1.5 × 1.4mm pads
  on a 4.95mm span). **Unverified against a real reflow** — first-article check
  is pad-to-pin-1 orientation and paste release on the corner pads.
- Chain topology and the per-pixel decoupling rule follow the WorldSemi
  application note and the pattern used in `seveibar/pico-w-3x5-led-matrix`
  (registry survey r5, 2026-08-10).
- The 0.7 × VDD logic-level constraint is from the WS2812B datasheet's DC
  characteristics table.
