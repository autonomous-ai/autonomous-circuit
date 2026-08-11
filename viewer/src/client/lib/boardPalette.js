// boardPalette — the PCB canvas colour system.
//
// Two schemes, same semantic assignment (top is red, bottom is blue, silk is
// pale yellow, courtyard is magenta) because that assignment is the thing an
// EE's eye already knows:
//
//   "studio"  — KiCad 9's calibrated default theme values (F.Cu #C83434,
//               B.Cu #4D7FC4, background #001023). Altium's layout language at
//               values that survive a backlit LCD for an hour.
//   "altium"  — the real classic set decoded from shipped .PCBSysColors files:
//               pure #FF0000 / #0000FF / #FFFF00 on #000000.
//
// Sources and the full derivation are in
// components/board/ALTIUM-NOTES.md §4. Every hex here is copied from that
// table, not invented.

/** Layer ids the Circuit JSON actually emits, in painter order (last on top). */
export const LAYER_ORDER = Object.freeze([
  "bottom",
  "inner1",
  "inner2",
  "inner3",
  "inner4",
  "top",
]);

/** Human labels for the layer bar tabs. */
export const LAYER_LABELS = Object.freeze({
  top: "Top Cu",
  bottom: "Bottom Cu",
  inner1: "In1 Cu",
  inner2: "In2 Cu",
  inner3: "In3 Cu",
  inner4: "In4 Cu",
});

const STUDIO = Object.freeze({
  id: "studio",
  label: "Studio",
  background: "#001023",
  boardFill: "#06192e",
  boardEdge: "#d0d2cd",
  grid: "#1b3350",
  gridCoarse: "#27476b",
  copper: {
    top: "#c83434",
    bottom: "#4d7fc4",
    inner1: "#7fc87f",
    inner2: "#ce7d2c",
    inner3: "#4fcbcb",
    inner4: "#db628b",
  },
  silkTop: "#f2eda1",
  silkBottom: "#e8b2a7",
  pasteTop: "#b4a09a",
  pasteBottom: "#00c2c2",
  maskTop: "#d864ff",
  maskBottom: "#02ffee",
  courtyardTop: "#ff26e2",
  courtyardBottom: "#26e9ff",
  hole: "#0d1b28",
  holeRing: "#9aa4ae",
  via: "#c9b458",
  ratsnest: "#00f8ff",
  selection: "#04ff43",
  hover: "#ffffff",
  measure: "#ffd042",
  drcError: "#d75b6b",
  drcWarning: "#ffd042",
  cursor: "#ffffff",
  // Rooms — Altium's name for a named area of board that belongs to one block.
  // Deliberately not any copper colour: a room is an annotation over the
  // drawing, and a reader must never mistake its outline for a track.
  room: "#9fb3c8",
  roomText: "#dbe6f0",
});

const ALTIUM_CLASSIC = Object.freeze({
  id: "altium",
  label: "Altium Classic",
  background: "#000000",
  boardFill: "#000000",
  boardEdge: "#ffffff",
  grid: "#4d4d5c",
  gridCoarse: "#918d90",
  copper: {
    top: "#ff0000",
    bottom: "#0000ff",
    inner1: "#bc8e00",
    inner2: "#70dbfa",
    inner3: "#00cc66",
    inner4: "#9966ff",
  },
  silkTop: "#ffff00",
  silkBottom: "#808000",
  pasteTop: "#808080",
  pasteBottom: "#800000",
  maskTop: "#800080",
  maskBottom: "#ff00ff",
  courtyardTop: "#ff00ff",
  courtyardBottom: "#808000",
  hole: "#000000",
  holeRing: "#c0c0c0",
  via: "#c0c0c0",
  ratsnest: "#9ea175",
  selection: "#ffffff",
  hover: "#ffffff",
  measure: "#ffff00",
  drcError: "#00ff00",
  drcWarning: "#ffd042",
  cursor: "#ffffff",
  room: "#8a8a8a",
  roomText: "#d0d0d0",
});

export const PALETTES = Object.freeze({ studio: STUDIO, altium: ALTIUM_CLASSIC });

export function palette(id) {
  return PALETTES[String(id || "")] || STUDIO;
}

/** The copper colour for a layer id, falling back to top. */
export function copperColor(scheme, layer) {
  const set = palette(scheme).copper;
  return set[String(layer || "top")] || set.top;
}

/**
 * The colour one drawable element paints in. `layer` overrides the element's
 * own layer (a trace route point can change layer mid-route).
 */
export function elementColor(scheme, element, layer) {
  const colors = palette(scheme);
  const side = String(layer || element?.layer || "top");
  const isBottom = side === "bottom";
  switch (element?.type) {
    case "pcb_silkscreen_path":
    case "pcb_silkscreen_text":
    case "pcb_silkscreen_rect":
    case "pcb_silkscreen_circle":
      return isBottom ? colors.silkBottom : colors.silkTop;
    case "pcb_solder_paste":
      return isBottom ? colors.pasteBottom : colors.pasteTop;
    case "pcb_courtyard_rect":
    case "pcb_courtyard_outline":
      return isBottom ? colors.courtyardBottom : colors.courtyardTop;
    case "pcb_via":
      return colors.via;
    case "pcb_hole":
      return colors.holeRing;
    case "pcb_plated_hole":
      return colors.holeRing;
    default:
      return copperColor(scheme, side);
  }
}

/**
 * Altium's three highlight methods (ALTIUM-NOTES §2). `normal` leaves the rest
 * of the board alone; `dim` keeps colour and drops opacity; `mask` desaturates
 * to grey and drops opacity further. The opacities are ours — Altium does not
 * publish its slider defaults.
 */
export const HIGHLIGHT_METHODS = Object.freeze(["normal", "dim", "mask"]);

export const HIGHLIGHT_METHOD_LABEL = Object.freeze({
  normal: "Normal",
  dim: "Dim",
  mask: "Mask",
});

/** Base opacity for an unselected element at a given method + mask level. */
export function unselectedOpacity(method, level = 3) {
  if (method === "normal") return 1;
  const steps = [0.55, 0.42, 0.3, 0.22, 0.14, 0.08];
  const clamped = Math.min(steps.length - 1, Math.max(0, Math.round(level)));
  const base = steps[clamped];
  return method === "mask" ? base * 0.6 : base;
}

/** Whether unselected elements should also lose their colour (Altium "Mask"). */
export function unselectedIsMonochrome(method) {
  return method === "mask";
}

/** Grey used for masked (monochrome) elements. */
export function maskedColor(scheme) {
  return palette(scheme).id === "altium" ? "#5a5a5a" : "#4a5a6b";
}

/** The next highlight method in the cycle (bound to `M`). */
export function nextHighlightMethod(method) {
  const at = HIGHLIGHT_METHODS.indexOf(method);
  return HIGHLIGHT_METHODS[(at + 1) % HIGHLIGHT_METHODS.length];
}

/**
 * Single-layer mode states, on Altium's `Shift+S` cycle: everything → other
 * layers greyed → other layers hidden → everything.
 */
export const SINGLE_LAYER_MODES = Object.freeze(["off", "grey", "hide"]);

export function nextSingleLayerMode(mode) {
  const at = SINGLE_LAYER_MODES.indexOf(mode);
  return SINGLE_LAYER_MODES[(at + 1) % SINGLE_LAYER_MODES.length];
}

/** What kind of thing the cursor is over, in the words a PCB tool uses. */
export function objectLabel(element) {
  switch (element?.type) {
    case "pcb_smtpad":
      return "SMD pad";
    case "pcb_plated_hole":
      return "Plated hole";
    case "pcb_hole":
      return "Hole";
    case "pcb_via":
      return "Via";
    case "pcb_trace":
      return "Track";
    case "pcb_silkscreen_text":
      return "Silk text";
    case "pcb_silkscreen_path":
      return "Silk line";
    case "pcb_courtyard_rect":
    case "pcb_courtyard_outline":
      return "Courtyard";
    case "pcb_solder_paste":
      return "Paste";
    default:
      return element?.type ? String(element.type).replace(/^pcb_/, "").replace(/_/g, " ") : "";
  }
}

/** The layer a non-copper element belongs to for visibility purposes. */
export function layerOf(element) {
  if (!element) return "top";
  if (typeof element.layer === "string" && element.layer) return element.layer;
  const layers = Array.isArray(element.layers) ? element.layers : [];
  if (layers.includes("top")) return "top";
  return String(layers[0] || "top");
}

/**
 * Object classes the layer bar can toggle independently, KiCad's "Objects" tab
 * idea (ALTIUM-NOTES §9). Keys are stable; the UI renders the labels.
 */
export const OBJECT_CLASSES = Object.freeze([
  { id: "traces", label: "Tracks", types: ["pcb_trace"] },
  { id: "pads", label: "Pads", types: ["pcb_smtpad", "pcb_plated_hole"] },
  { id: "vias", label: "Vias", types: ["pcb_via"] },
  { id: "holes", label: "Holes", types: ["pcb_hole"] },
  {
    id: "silkscreen",
    label: "Silkscreen",
    types: ["pcb_silkscreen_path", "pcb_silkscreen_text", "pcb_silkscreen_rect", "pcb_silkscreen_circle"],
  },
  { id: "paste", label: "Paste", types: ["pcb_solder_paste"] },
  { id: "courtyard", label: "Courtyard", types: ["pcb_courtyard_rect", "pcb_courtyard_outline"] },
]);

const CLASS_BY_TYPE = new Map();
for (const entry of OBJECT_CLASSES) for (const type of entry.types) CLASS_BY_TYPE.set(type, entry.id);

/** Which object class an element belongs to ("" when it has none). */
export function objectClassOf(element) {
  return CLASS_BY_TYPE.get(String(element?.type || "")) || "";
}

/** The default visible set: everything except paste and courtyard. */
export function defaultObjectClasses() {
  return new Set(OBJECT_CLASSES.filter((entry) => entry.id !== "paste" && entry.id !== "courtyard").map((e) => e.id));
}
