# st7789-1.54-module — a 1.54" 240×240 IPS module on an 8-pin header

**Function:** the face of a desk device. A complete module (QDtech/LCDWIKI MSP1541 class), plugged
on a 1×8 2.54 mm header, driven over SPI0 from an RP2040. The module carries its own ST7789
controller, backlight LED string and, on most boards, a backlight transistor with a pull-up.

| Header pin | Name | Board side | Notes |
|---|---|---|---|
| 1 | GND | GND | |
| 2 | VCC | 3V3 | module regulator-less variants take 3.3 V only; never 5 V |
| 3 | SCL | GPIO SCK (SPI0) | |
| 4 | SDA | GPIO MOSI (SPI0) | |
| 5 | RES | GPIO | active-low reset; 10 k pull-up to 3V3 is optional, RP2040 drives it |
| 6 | DC | GPIO | data/command |
| 7 | CS | GPIO | active-low |
| 8 | BLK | see below | backlight |

**BLK is an input that wants a driven HIGH, or a pull-up, to light.** On the MSP1541 the pin
feeds an S8050 base through the module's 10 k pull-up: left open = on, pulled low = off. An
open-collector/NPN low-side driver on the board can only turn it OFF; to switch it from a GPIO
use a PNP high-side from 3V3 (base via 1 k to the GPIO, 4.7 k base pull-up, LCD_BLK active-low)
or drive the pin directly from a GPIO when the module datasheet shows a plain logic input.
harness-12 (Grok, 2026-09-23) put an NPN low-side there: the backlight could never light;
harness-14's review replaced it with the PNP.

**Current:** backlight ~45 mA nominal (MSP1541 documented), controller < 10 mA. Budget 120 mA
on the 3V3 rail for the module and you are safe with a margin.

**Mechanical:** the module is 32 × 43.7 mm and stands off the board; the header is the only
fixation. The board outline follows the module, not the other way round.

**Measured:** none. `hardwareTested: false` for every board that used this block so far.
