# Pattern: add USB power

**Trigger:** "power it over USB", "plug it in", "it should charge/run from my
laptop", any board with no declared power source.

**Why this exists:** picking the wrong one of the two USB blocks either
double-populates the connector or leaves the MCU without data lines.

**Choose one connector, never both, then always add the protected entry:**

| The board… | Use |
|---|---|
| only needs 5V | `usb-c-power` |
| also needs USB data (native-USB MCU, HID, serial, flashing over USB) | `usb-c-data` |

`usb-c-data` is a **superset** of `usb-c-power` — it has the same connector, CC
resistors and ESD, plus the 27.4Ω series resistors on D+/D−. Placing both puts
two connectors on the board and is a blocking-grade mistake the board-law check
warns about. Both connector blocks expose only `VBUS_RAW`; neither is a safe
downstream `V5` source by itself. Compose `usb-power-entry` exactly once.

```tsx
import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { UsbPowerEntry } from "../blocks/usb-power-entry/usb-power-entry"
import { Ldo3v3 }   from "../blocks/ldo-3v3/ldo-3v3"

<UsbCData /* direct-pair, hidden-node and phase props are board-owned */ />
<UsbPowerEntry rawNet="VBUS_RAW" outputNet="V5" />
<Ldo3v3 vinNet="V5" voutNet="V3_3" />
```

**The rail chain:** the connector gives `VBUS_RAW` with 1uF local attach
capacitance. `usb-power-entry` adds a controlled-rise, current-limited boundary
and gives the board `V5`; only then may bulk capacitance, 5V loads, and the
`ldo-3v3` input attach. Logic usually feeds from the LDO's `V3_3`.

**Budget:** this catalog deliberately assumes an unadvertised 500mA USB
source, not 1.5A. TPS2553's frozen 59k network guarantees a 400.6mA minimum and
500mA maximum trip. `board_plan()` makes a physical peak above 400.6mA
unbuildable unless `firmware_load_caps_ma` declares an operational ceiling;
pass the resulting plan and the board's actual load refdes pattern to
`usb_power_budget_for_plan()` to compile `product.json.powerBudget`. Do not
copy the limiter identity or current arithmetic by hand. The LDO is linear — its heat is
`(5 − 3.3) × I`, and the planner charges it only for V3_3 loads.

```python
from circuitlib.helpers import board_plan, usb_power_budget_for_plan

plan = board_plan(
    capabilities=["mcu", "rgb-pixels"],
    counts={"ws2812-chain": 8},
    firmware_load_caps_ma={"ws2812-chain": 280},
    # This is valid only because the consuming board actually instantiates
    # the SWCLK/SWD debug probe. Do not use it to waive missing furniture.
    exposed_nets=["SWCLK", "SWD"],
)
assert plan.buildable
product_power_budget = usb_power_budget_for_plan(
    plan,
    firmware_load_matches={"ws2812-chain": "D1[0-7]"},
)
```

The match is the board's real populated refdes set, not a block-library
placeholder. Missing and extra families fail closed so a firmware limit cannot
silently describe different hardware from the compiled board.
The generated limiter contract also names U7's `ILIM` pin and the exact
R31/C32297 59kΩ return to GND; the artifact gate measures that part, value and
topology because the TPS2553 package by itself does not establish its trip
range.

**Pitfalls:**

- Put the connector on a board edge, overhanging correctly, and tell the user
  which edge — the printed enclosure needs a matching cutout.
- Don't add your own ESD or CC resistors; the block has them.
- Keep raw attach capacitance at or below the declared 10uF limit and keep all
  bulk capacitors downstream of `UsbPowerEntry`.
- Route/probe `USB_POWER_FAULT`; a current limiter whose fault output is
  invisible is not a diagnosable product.
- USB-C receptacles are the part most likely to be rotated wrong at assembly.
  Repeat the placement-preview warning when you hand over the packet.
