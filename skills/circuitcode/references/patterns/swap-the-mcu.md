# Pattern: swap the MCU

**Trigger:** "use an ESP32 instead", "does it need WiFi?", "make it cheaper",
"swap the chip".

**Why this exists:** the MCU choice drives the power block, the rail budget, the
board size, and the price — it is not a drop-in substitution.

**What exists today:** `rp2040-core` only (RP2040 + W25Q128 flash + 12MHz
crystal + decoupling + BOOTSEL). An ESP32-S3 core block is planned and **not yet
authored**, so a WiFi ask today gets an honest "not yet", not an improvised
module footprint.

**The decision:**

| Need | Answer |
|---|---|
| USB HID, lots of GPIO, cheap, no radio | `rp2040-core` |
| WiFi / BLE | ESP32-S3 module — **no block yet**, say so |
| Battery-powered anything | out of envelope until the sealed battery block is signed off |

**When the swap is real, it is four edits, not one:**

1. The MCU block itself.
2. The USB block — RP2040 needs `usb-c-data` (native USB); a module with its own
   USB-serial may only need `usb-c-power`.
3. The pin allocation — every peripheral net moves. Redo the allocation table in
   the plan before touching code.
4. The board size and placement — a module has a keep-out region under and
   around its antenna that nothing may sit in.

**Pitfalls:**

- Radio is **certified modules only**. Never place a bare ESP32 die, never draw
  a matching network or a PCB antenna. This is an envelope rule, not a
  preference (`references/safety-envelope.md`).
- The RP2040's flash, crystal, and decoupling are part of the block for a
  reason — the RPi hardware design guide is specific about them. Don't thin them
  out to save board area.
- Re-run `board_plan()` after the swap; `missing_requirements()` catches the net
  you forgot to re-provide.
