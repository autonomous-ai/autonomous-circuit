// The one-tap starter gallery that replaces the blank prompt box. Each starter
// is a proven board archetype with a ready brief — tapping one sends a complete
// engineering request to the chat (which drives circuit-analysis → circuitcode),
// so a first-time user never faces an empty box. Kept JSX/dep-free so Node's
// --test runner can exercise the prompt builder.
//
// **Every starter must be buildable out of the released golden blocks.** This
// shelf is the front door: a card that cannot be built is a promise the tool
// breaks ninety seconds later, and the person who taps it has no way to know
// that was our fault rather than theirs. Two of the original four cards named
// parts that do not exist in the library — a DRV8833 motor driver (no block,
// and the README's own example of the thing Circuit refuses) and an ESP32-S3
// (radio block not released) — so a first-timer had a 50% chance of picking a
// dead end on their very first tap. Each starter now declares the block ids it
// needs and `starters.test.js` checks them against the real
// `packages/golden-blocks/blocks/` directory, so the catalog stays the one
// owner and the test fails the day a card drifts off it.

export const STARTERS = Object.freeze([
  {
    id: "big_button",
    title: "One big button",
    tag: "Simplest",
    pitch: "One button your computer sees as a key — mute, panic, whatever you map it to.",
    // `parts` is the honest preview: the actual components the brief asks
    // for, so tapping a card is not a leap of faith. Same strings an
    // engineer would recognise, short enough that a non-engineer skims past.
    parts: "RP2040 · USB-C · 1 button · 1 LED",
    blocks: ["usb-c-data", "ldo-3v3", "rp2040-core", "sw-tact", "status-led"],
    brief:
      "Design a single-button USB gadget: one tactile button and one status " +
      "LED, an RP2040 on USB-C, showing up to a computer as a keyboard key.",
  },
  {
    id: "macropad",
    title: "Macropad",
    tag: "Popular",
    pitch: "Nine keys and per-key RGB — your shortcuts as hardware.",
    parts: "RP2040 · USB-C · 9 keys · RGB per key",
    blocks: ["usb-c-data", "ldo-3v3", "rp2040-core", "sw-tact", "ws2812-chain"],
    brief:
      "Design a 3×3 macropad: RP2040, USB-C, nine tactile switches, " +
      "per-key WS2812B LEDs, USB HID keyboard.",
  },
  {
    id: "air_monitor",
    title: "Desk air monitor",
    tag: "Popular",
    pitch: "Know when to open a window — temperature, humidity, pressure.",
    // Was ESP32-S3. There is no released radio block, so that card asked for
    // a part the library does not have and the plan came back with a gap.
    parts: "RP2040 · USB-C · BME280 sensor",
    blocks: ["usb-c-data", "ldo-3v3", "rp2040-core", "i2c-bus", "sensor-bme280", "status-led"],
    brief:
      "Design a USB-C powered desk air monitor: an RP2040 reading a BME280 " +
      "temperature/humidity/pressure sensor over I2C, plus a status LED.",
  },
  {
    id: "blinky_badge",
    title: "Blinky badge",
    pitch: "A wearable LED badge — the classic first solder job.",
    parts: "RP2040 · USB-C · 8 RGB LEDs · 1 button",
    blocks: ["usb-c-data", "ldo-3v3", "rp2040-core", "ws2812-chain", "sw-tact"],
    brief:
      "Design a USB-C powered blinky badge: a ring of eight WS2812B LEDs, " +
      "one tactile button to switch patterns, RP2040 to drive them.",
  },
]);

/**
 * The boundary, said out loud on the front door instead of discovered five
 * minutes into a build.
 *
 * One owner: the rows live in `lib/catalogBoundary.js` because the question
 * card needs the same list — an option offering Wi-Fi is the same promise as a
 * starter card offering Wi-Fi, and two copies of the boundary would disagree
 * within a week. Re-exported here so the front door keeps its own import.
 */
export { CANT_BUILD_YET } from "../../lib/catalogBoundary.js";

export function starterById(id) {
  return STARTERS.find((s) => s.id === id) || null;
}

/**
 * Build the engineering brief a starter sends to the chat. Natural language a
 * non-EE would recognize — the archetype brief plus the standing ask: spec it,
 * build it, and end at the fab packet.
 */
export function buildStarterPrompt(starter) {
  if (!starter) return "";
  return [
    starter.brief,
    "Spec the circuit first, then build the board and show me the schematic,",
    "the layout, and what it would cost to have five made at JLCPCB.",
  ].join(" ");
}
