// What this library cannot build yet — in one place, in plain words, with a
// way forward for every row.
//
// The defect this exists for, watched twice in one sitting. Asked for a
// ceiling-light dimmer, the app opened with an excellent refusal — "I can't
// design anything that touches mains AC" — and then, one screen later, offered
// "Mains fixture (120/230V)", "Wi-Fi app / Home Assistant" and "Rotary knob"
// as options on the preference card. Asked for "a nightlight that comes on
// when it gets dark", it offered "AA/AAA batteries", "Add motion sensing" and
// "Wi-Fi / app control". No radio block exists. No battery block exists. No
// light or motion sensor exists. No rotary encoder exists.
//
// Every one of those is a trap: the person picks it, waits, and gets a refusal
// for a choice the app itself put in front of them. The refusal taught, and
// then the next screen reopened the same door.
//
// The questions are written by a model in the planning turn, so the option
// text is never going to be constrained by construction — a prompt is advice.
// This is the part that is not advice: an option naming something outside the
// catalog is not selectable, and it says why and what we can do instead.
//
// The authority for what exists is `packages/golden-blocks/blocks/`. The test
// beside this file reads that directory, so the day a block lands, the row
// that says we can't build it fails and has to be deleted.

/**
 * One row per part class the released catalog does not cover.
 *
 * - `ask` — the boundary as a person would say it
 * - `why` — checkable against the repo, not reassurance
 * - `instead` — the nearest thing we can build, so nobody is left at a dead end
 * - `match` — what an option label naming this class looks like. Deliberately
 *   narrow: a false positive disables a good answer, which is the same dead
 *   end from the other side. Bare "light" is not here — it is in "nightlight",
 *   "LED light" and "light up"; only "light sensor"/"ambient light" is.
 * - `blocks` — the golden block ids that, once released, retire this row
 */
export const CANT_BUILD_YET = Object.freeze([
  {
    id: "motors",
    ask: "Anything that moves",
    why: "No motor, pump or servo driver has been proven yet, so we would be guessing at the parts.",
    instead: "A board that switches a ready-made motor module on and off.",
    match: /\b(motors?|servos?|pumps?|solenoids?|steppers?|actuators?|vibration motor)\b/i,
    blocks: ["motor-driver"],
  },
  {
    id: "mains",
    ask: "Wall power",
    why: "Mains voltage is outside what these checks can verify, and getting it wrong is dangerous.",
    instead: "A USB-C wall adapter feeding the board 5 volts.",
    match:
      /\b(mains|line voltage|wall socket|ac line|triac|phase[- ]?cut|1[12]0\s?v(ac)?|2[234]0\s?v(ac)?)\b/i,
    blocks: [],
  },
  {
    id: "battery",
    ask: "Batteries",
    why: "Charging and protecting a battery safely needs a circuit we have not finished.",
    instead: "USB-C power, or a ready-made battery pack with a USB socket.",
    match: /\b(batter(y|ies)|li-?po|li-?ion|lifepo4|18650|coin cell|cr20\d\d|aaa?)\b/i,
    blocks: ["battery-charge-protect"],
  },
  {
    id: "radio",
    ask: "Wi-Fi and Bluetooth",
    why: "The radio module has to be a certified part, and that one is not released yet.",
    instead: "Plug it into a computer over USB.",
    match:
      /\b(wi-?fi|bluetooth|ble|zigbee|z-wave|lora|thread|matter|esp32\S*|nrf52\S*|home ?assistant|homekit|phone app|app control|wireless(ly)?|over the air|smart ?home|cloud)\b/i,
    blocks: ["esp32-module"],
  },
  {
    id: "display",
    ask: "Screens",
    why: "No display has been proven yet.",
    instead: "Lights — one indicator, or a strip of colour-changing LEDs.",
    match: /\b(screens?|displays?|oled|lcd|tft|e-?ink|e-?paper|seven[- ]segment|7[- ]segment)\b/i,
    blocks: ["display-i2c"],
  },
  {
    id: "sensing",
    ask: "Sensing light, motion or sound",
    why: "The one sensor proven so far reads temperature, humidity and air pressure. Light, motion and sound have no proven part yet.",
    instead: "Temperature, humidity and pressure — or a button you press yourself.",
    match:
      /\b(light[- ]sens\w*|ambient[- ]light|photo(resistor|transistor|diode)|ldr|lux|motion[- ]?(sens\w*|detect\w*)?|pir|occupancy|microphone|sound[- ]sens\w*|accelerometer|imu|gyro)\b/i,
    blocks: ["sensor-ldr", "sensor-pir"],
  },
  {
    id: "knob",
    ask: "Knobs and sliders",
    why: "No rotary encoder or potentiometer has been proven yet — only push buttons.",
    instead: "Buttons: one per setting, or two to step a value up and down.",
    match: /\b(rotary|encoder|potentiometer|knobs?|dials?|sliders?|thumbwheel)\b/i,
    blocks: ["rotary-encoder"],
  },
]);

/**
 * Which boundary rows an option label runs into, if any.
 *
 * Reads the label and its description together — the trap is often in the
 * small print ("Recommended — controls it from your phone").
 *
 * @param {{label?: string, description?: string}} option
 * @returns {Array<(typeof CANT_BUILD_YET)[number]>} matching rows, possibly empty
 */
export function boundariesHit(option) {
  const text = `${option?.label || ""} ${option?.description || ""}`;
  if (!text.trim()) return [];
  return CANT_BUILD_YET.filter((row) => row.match.test(text));
}

/**
 * The one sentence shown under a question when an option is not selectable.
 * Names the option, why not, and the nearest thing we can build — the refusal
 * has to leave the reader somewhere.
 *
 * @param {{label?: string}} option
 * @param {(typeof CANT_BUILD_YET)[number]} row
 */
export function boundaryNote(option, row) {
  return `${option?.label || row.ask} — not yet. ${row.why} Nearest thing we can build: ${row.instead}`;
}

/** The delegate option is the escape hatch on every question; it is never a
 *  promise about a part, so it is never screened out. */
const DELEGATE_LABEL = /^let circuit choose$/i;

/**
 * Screen one question's options against the boundary.
 *
 * Returns every option with the row it runs into (or `null`), plus one note
 * per blocked option — named, explained, and pointed somewhere. Order is
 * preserved: the card still shows what the model offered, so the reader can
 * see their idea was understood and see why it is not on the menu yet.
 *
 * @param {Array<{label?: string, description?: string}>} options
 */
export function screenOptions(options) {
  const screened = (Array.isArray(options) ? options : []).map((opt) => ({
    ...opt,
    blockedBy: DELEGATE_LABEL.test(String(opt?.label || "").trim())
      ? null
      : boundariesHit(opt)[0] || null,
  }));
  const notes = [];
  const seen = new Set();
  for (const opt of screened) {
    if (!opt.blockedBy) continue;
    const key = `${opt.label}::${opt.blockedBy.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    notes.push({ id: key, text: boundaryNote(opt, opt.blockedBy) });
  }
  return { options: screened, notes };
}
