# oled-0.96-i2c-module — a 0.96" 128×64 monochrome OLED on a 4-pin I²C header

**Function:** the face of a small device. A complete module (SSD1306 or SH1106 controller, the shop
listing sold as "man096" does not say which), plugged on a 1×4 2.54 mm header, driven over I²C.
Draws its own charge-pump supply from VCC.

| Header pin | Name | Board side |
|---|---|---|
| 1 | GND | GND |
| 2 | VCC | 3V3 (modules accept 3.3–5 V; feed 3.3 V so SDA/SCL match the MCU) |
| 3 | SCL | GPIO (I²C SCL) |
| 4 | SDA | GPIO (I²C SDA) |

**Pin order varies by seller** — GND/VCC/SCL/SDA is the common one, VCC/GND/SCL/SDA exists. Print the
four names on the silk and tell the reader to match the module's own silk, pin by pin, before
plugging it in; a swapped VCC/GND kills the module.

**Driver is decided on the bench, not from the listing.** SSD1306 (128 columns) and SH1106 (132
columns, 2-column offset) answer at the same address (0x3C, some 0x3D) and differ only in page
addressing; the Deck board's firmware carries both and a serial command to switch (`oled sh1106`),
stored in flash — do the same rather than guessing.

**Electrical:** I²C up to 400 kHz; most modules carry their own 4.7–10 k pull-ups, so board pull-ups
are DNP by default (fit only if a scope shows slow edges). Current ~20–30 mA at full white, ~10 mA
typical — budget it on the 3V3 rail (it is the biggest load the ESP32-C3 SuperMini's LDO sees).

**Face drawing at 128×64:** the Deck board's `face.h` draws pet eyes on this panel; a 1-bit canvas
with page-wise flush is enough and needs no library.

**Measured:** none on hardware.
