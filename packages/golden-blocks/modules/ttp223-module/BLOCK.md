# ttp223-module — a capacitive touch module on a 3-pin header

**Function:** the "belly" a desk device can be poked. A complete TTP223 module (DFRobot DFR0030
class), plugged on a 1×3 2.54 mm header; the touch pad is on the module.

| Header pin (board) | Name | Board side | DFR0030 pin |
|---|---|---|---|
| 1 | VCC | 3V3 | 2 |
| 2 | GND | GND | 3 |
| 3 | OUT | GPIO input | 1 |

**The cable must be mapped, not straight:** DFR0030's own order is OUT, VCC, GND, so a board
header laid out VCC, GND, OUT needs a crossed cable (board 1 → module 2, 2 → 3, 3 → 1). Say so in
the README or lay the header out in the module's order.

**Electrical:** OUT is a push-pull logic output at VCC level, active-high by default on most
modules (DFR0030: high while touched). No series resistor needed into an RP2040 GPIO; a 100 nF
on VCC at the header is enough. Current < 5 mA.

**Measured:** none.
