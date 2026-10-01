# esp32-c3-supermini — the ESP32-C3 "SuperMini" dev module on two 1×8 sockets

**Function:** the brain of a board that wants Wi-Fi/BLE without a bare-chip RF design. A complete
module — ESP32-C3, its antenna, USB-C, 3.3 V LDO, BOOT and RESET buttons — plugged into two 1×8
2.54 mm female headers so it can be pulled. Certified radio on a bought module is the only radio the
safety envelope allows; the board never carries RF of its own.

**Mechanical (supplier drawing, pages 2–3; read 2026-09-28):** module 18.00 × 22.52 mm, two rows of
8 pins at 2.54 mm pitch, **row centres 15.24 mm apart**. Page 2 draws the pinout as a *bottom* view,
page 3 the component side; lay the sockets out from page 3 and say so. The USB-C sits at one short
end; the PCB antenna is at the other — keep copper off both layers under it (the Deck board enforced a
~10 × 6.5 mm no-copper window under the antenna end).

| Socket | Pin 1 → 8, top view, pin 1 at the USB end |
|---|---|
| left row | IO5, IO6, IO7, IO8, IO9, IO10, IO20, IO21 |
| right row | 5V, GND, 3V3, IO4, IO3, IO2, IO1, IO0 |

**Pins to avoid / respect:** IO2, IO8, IO9 are strapping pins (boot mode) — leave them to the module's
own buttons. IO18/IO19 are USB D−/D+ and are not on the header. IO20/IO21 are UART0 RX/TX: the ROM
prints its boot log on IO21 at reset, so never hold IO21 low with a switch and expect a clean boot;
an I²C SCL on IO21 is fine (SDA idle-high means the log never forms a START).

**Power, the three rules.**
1. `3V3` is the module's own LDO output, already feeding the C3 (~80 mA with radio). Budget what the
   board hangs on it: the Deck board allowed **≤ 40 mA external** (OLED + pull-ups + a touch module).
2. `5V` is the module's USB VBUS brought out — on the common clones **straight through, no diode**.
   A board that feeds 5 V *into* this pin (power bank, servo rail) must never be plugged into a host
   over the module's USB-C at the same time, or must put a Schottky/ideal diode between its rail and
   the pin and say which. Write the rule on the silk next to the socket.
3. Nothing that draws real current (servos, LED strings) may be fed from the module's `5V` pin when
   the module's USB is the source: that path is the Mac's port, 500 mA, through the module's trace.

**Firmware contract:** USB Serial/JTAG (CDC) over the module's USB-C, `CDCOnBoot=cdc`; VID 0x303a.
Boards that also carry a separate 5 V input must say which plug programs and which plug powers.

**Traps on record:** the supplied clone has not been measured against the drawing (Deck B, 2026-09-28
and the Opus Deck, 2026-09-29 both carry that as the first bench question: put the real module on the
socket and read the silk against the socket numbers before paying for a board).

**Measured:** none on hardware.
