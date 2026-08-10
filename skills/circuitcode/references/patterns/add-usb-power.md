# Pattern: add USB power

**Trigger:** "power it over USB", "plug it in", "it should charge/run from my
laptop", any board with no declared power source.

**Why this exists:** picking the wrong one of the two USB blocks either
double-populates the connector or leaves the MCU without data lines.

**Choose one, never both:**

| The board… | Use |
|---|---|
| only needs 5V | `usb-c-power` |
| also needs USB data (native-USB MCU, HID, serial, flashing over USB) | `usb-c-data` |

`usb-c-data` is a **superset** of `usb-c-power` — it has the same connector, CC
resistors and ESD, plus the 27.4Ω series resistors on D+/D−. Placing both puts
two connectors on the board and is a blocking-grade mistake the board-law check
warns about.

```tsx
import { UsbCData } from "../blocks/usb-c-data/usb-c-data"
import { Ldo3v3 }   from "../blocks/ldo-3v3/ldo-3v3"

<UsbCData pcbX={-14} pcbY={0} schX={-6} schY={0} />
<Ldo3v3   pcbX={0}   pcbY={6} schX={0}  schY={0} />
```

**The rail chain:** USB gives you `V5`. Almost nothing on our boards runs at 5V,
so `ldo-3v3` follows immediately and everything else feeds from `V3_3`.

**Budget:** the CC pulldowns advertise a 5V sink; budget conservatively at
**1.5A** (`helpers.power_budget()` knows this). The LDO is linear — its heat is
`(5 − 3.3) × I`, so past roughly 500mA a linear part is the wrong answer and the
honest reply is that we don't have a buck block yet.

**Pitfalls:**

- Put the connector on a board edge, overhanging correctly, and tell the user
  which edge — the printed enclosure needs a matching cutout.
- Don't add your own ESD or CC resistors; the block has them.
- USB-C receptacles are the part most likely to be rotated wrong at assembly.
  Repeat the placement-preview warning when you hand over the packet.
