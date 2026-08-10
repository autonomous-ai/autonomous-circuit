// The one-tap starter gallery that replaces the blank prompt box. Each starter
// is a proven board archetype (recon: the v1 demo boards) with a ready brief —
// tapping one sends a complete engineering request to the chat (which drives
// circuit-analysis → circuitcode), so a first-time user never faces an empty
// box. Kept JSX/dep-free so Node's --test runner can exercise the prompt
// builder. Every brief stays inside the safety envelope: USB-C or low-voltage
// DC power only, no mains, no bare-die RF.

export const STARTERS = Object.freeze([
  {
    id: "macropad",
    emoji: "⌨️",
    title: "Macropad",
    pitch: "Nine keys and per-key RGB — your shortcuts as hardware.",
    brief:
      "Design a 3×3 macropad: RP2040, USB-C, nine tactile switches, " +
      "per-key WS2812B LEDs, USB HID keyboard.",
    popular: true,
  },
  {
    id: "air_monitor",
    emoji: "🌫️",
    title: "Desk air monitor",
    pitch: "Know when to open a window — temperature, humidity, pressure.",
    brief:
      "Design a USB-C powered desk air quality monitor: ESP32-S3 module " +
      "with a BME280 temperature/humidity/pressure sensor on I2C.",
    popular: true,
  },
  {
    id: "motor_driver",
    emoji: "⚙️",
    title: "Motor driver",
    pitch: "Two DC motors for a small robot, driven from any dev board.",
    brief:
      "Design a low-voltage dual DC motor driver board: DRV8833, JST power " +
      "input (5–10V), control pin header, no microcontroller on board.",
  },
  {
    id: "blinky_badge",
    emoji: "✨",
    title: "Blinky badge",
    pitch: "A wearable LED badge — the classic first solder job.",
    brief:
      "Design a USB-C powered blinky badge: a ring of eight WS2812B LEDs, " +
      "one tactile button to switch patterns, RP2040 to drive them.",
  },
]);

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
