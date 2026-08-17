// shortcutSheet — the list of every key the board workspace answers to, read
// out of the code that answers them.
//
// Why this exists. Altium carries something like a hundred bindings and an EE
// picks them up without a learning curve, because the tool will tell you what
// they are: Shift+F1 lists the shortcuts valid right now
// (https://www.altium.com/documentation/altium-designer/shortcut-keys). Dee's
// bar is "no surprises, no learning curve", and the mechanism for that is not
// fewer keys — it is a list you can open.
//
// Why it is not a hand-written list. A written list is true until the next
// person moves a binding, and this workspace has already shipped two keys that
// did the wrong thing. So the combos are not written here at all. Two of the
// three surfaces resolve keys through a PURE FUNCTION — `resolveBoardKey` in
// boardKeymap.js and `canvasKeyAction` in canvasPointer.js — and this module
// derives their bindings by *pressing every key at them* and recording what
// comes back. That cannot drift: it is the same call the app makes.
//
// What is still hand-written, and how it is pinned. Board3DView.jsx and
// PropertiesPanel.jsx keep their key handling in a closure inside a
// `useEffect`/`onKeyDown`, so there is nothing to call. Their combos are
// declared in `INLINE_BINDINGS` below and pinned by shortcutScan.js in the
// test: the scanner reads those two files and the test fails if the declared
// set and the scanned set differ, or if the code behind a binding changed
// (each entry carries a hash of the statement it runs). See
// `docs/architecture/ide-shortcut-sheet.md`.
//
// The words are human either way. Derivation gives us the truth about WHICH
// key does WHAT command; it cannot write "Fit the whole board in the pane".
// Every command id must carry a line of copy or the test fails, so a new
// binding cannot ship undocumented.

import { canvasKeyAction } from "./canvasPointer.js";
import { resolveBoardKey } from "./boardKeymap.js";
import { comboOf } from "./shortcutScan.js";

// --- what the sheet is opened by ---------------------------------------------

/**
 * Resolve a keydown to the sheet's own command.
 *
 * Exported and pure for the same reason boardKeymap is: the sheet's key is a
 * binding like any other, and it has to appear in its own list. A sheet that
 * cannot tell you how to open it is the joke version of this feature.
 *
 * `Shift+F1` is Altium's own: "use the Shift+F1 keyboard shortcut to access a
 * menu listing all valid shortcuts"
 * (https://www.altium.com/documentation/altium-designer/shortcut-keys).
 * `?` is not Altium's — **unverified**, Altium publishes no always-available
 * shortcut-list key — it is the web convention (GitHub, Linear, Gmail) and the
 * one a user who has never opened Altium will try first. Both are bound
 * because our user is both people.
 */
export function shortcutSheetKeyAction(event, { open = false } = {}) {
  if (!event || typeof event.key !== "string") return null;
  if (event.defaultPrevented) return null;
  if (event.altKey) return null;
  if (isTypingTarget(event.target)) return null;
  // An overlay owns the keyboard while it is up — including this sheet, whose
  // own row for `?` reads "Open this list" and means it. Without this, `?`
  // inside the Move-by-exact dialog or an open menu stacked a second modal on
  // top of the first. Same predicate as `boardKeymap.isOverlayTarget`; copied
  // rather than imported for the reason the guard above is copied.
  if (event.target?.closest?.('[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"],[aria-modal="true"]')) {
    return null;
  }
  if (event.key === "Escape") return open ? "sheet.close" : null;
  if (event.metaKey || event.ctrlKey) return null;
  if (event.key === "F1" && event.shiftKey) return "sheet.toggle";
  if (event.key === "?") return "sheet.toggle";
  return null;
}

/** Copied from boardKeymap rather than imported, to keep this module's key
 *  resolution independent of a file another surface owns. `select` is in the
 *  list for the reason boardKeymap gives: native typeahead means a `<select>`
 *  is being typed into even though nothing is typed. */
function isTypingTarget(target) {
  const tag = String(target?.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target?.isContentEditable === true;
}

// --- the key space we press at the arbiters ----------------------------------

const LETTERS = "abcdefghijklmnopqrstuvwxyz".split("");
const DIGITS = "0123456789".split("");
const PUNCTUATION = "`-=[]\\;',./~!@#$%^&*()_+{}|:\"<>?".split("");
const NAMED = [
  "Escape",
  "Enter",
  "Tab",
  " ",
  "Spacebar",
  "Backspace",
  "Delete",
  "Insert",
  "Home",
  "End",
  "PageUp",
  "PageDown",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  ...Array.from({ length: 12 }, (unused, i) => `F${i + 1}`),
];

/** Every `event.key` value we press at an arbiter. */
export const KEY_SPACE = Object.freeze([
  ...LETTERS,
  ...LETTERS.map((letter) => letter.toUpperCase()),
  ...DIGITS,
  ...PUNCTUATION,
  ...NAMED,
]);

/**
 * The modifier states we press each key in.
 *
 * Ctrl and Meta are swept separately because a handler is free to treat them
 * differently even though ours do not; the results collapse to one `Mod` row.
 * Alt is swept so an Alt binding could never hide from the sheet, even though
 * boardKeymap deliberately answers nothing under it.
 */
const MODIFIER_STATES = Object.freeze([
  { id: "plain", flags: {} },
  { id: "shift", flags: { shiftKey: true } },
  { id: "ctrl", flags: { ctrlKey: true } },
  { id: "meta", flags: { metaKey: true } },
  { id: "ctrl-shift", flags: { ctrlKey: true, shiftKey: true } },
  { id: "meta-shift", flags: { metaKey: true, shiftKey: true } },
  { id: "alt", flags: { altKey: true } },
  { id: "alt-shift", flags: { altKey: true, shiftKey: true } },
]);

const MODIFIER_FLAGS = Object.freeze(["ctrlKey", "metaKey", "altKey", "shiftKey"]);

/**
 * Legacy `event.key` spellings some WebViews still send. canvasPointer accepts
 * `"Spacebar"` alongside `" "`; that is a compatibility shim, not a second key
 * to teach anyone.
 */
const KEY_ALIASES = Object.freeze({ Spacebar: " " });

function modifierTokens(flags) {
  const mods = new Set();
  if (flags.ctrlKey || flags.metaKey) mods.add("Mod");
  if (flags.altKey) mods.add("Alt");
  if (flags.shiftKey) mods.add("Shift");
  return mods;
}

function pressEvent(key, flags) {
  return {
    key,
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    repeat: false,
    defaultPrevented: false,
    target: null,
    ...flags,
  };
}

// --- the surfaces that resolve keys by running a function --------------------

/**
 * Each probe names one pure resolver and the states it can be in.
 *
 * Contexts run widest-first. A binding is reported against the FIRST context
 * that produces it, so `Insert` reads as always-on rather than "with the
 * cursor over the board" only if it genuinely resolves with no cursor.
 */
export const PROBES = Object.freeze([
  {
    surface: "workspace",
    file: "boardKeymap.js",
    resolve: (event, state) => resolveBoardKey(event, state),
    contexts: [
      { id: "always", state: { typing: false, canUndo: false }, when: "" },
      { id: "undo-waiting", state: { typing: false, canUndo: true }, when: "after you move a part" },
      { id: "redo-waiting", state: { typing: false, canRedo: true }, when: "after you undo something" },
      {
        id: "nudging",
        state: { typing: false, canNudge: true },
        when: "with a part selected in move mode",
      },
    ],
  },
  {
    surface: "pcb",
    file: "canvasPointer.js",
    resolve: (event, state) => {
      const action = canvasKeyAction(event, state);
      if (!action) return null;
      return action.direction ? `canvas.${action.type}.${action.direction}` : `canvas.${action.type}`;
    },
    contexts: [
      { id: "always", state: { dragging: false, hasCursor: false }, when: "" },
      { id: "cursor", state: { dragging: false, hasCursor: true }, when: "with the pointer on the board" },
      { id: "dragging", state: { dragging: true, hasCursor: true }, when: "while dragging a part" },
    ],
  },
  {
    surface: "sheet",
    file: "shortcutSheet.js",
    resolve: (event, state) => shortcutSheetKeyAction(event, state),
    contexts: [
      { id: "always", state: { open: false }, when: "" },
      { id: "sheet-open", state: { open: true }, when: "while this list is open" },
    ],
  },
]);

/**
 * True when a simpler press already produces this command.
 *
 * Our handlers only test the modifiers they care about, so Ctrl+Shift+M
 * measures exactly as Ctrl+M does and Shift+Escape clears the selection.
 * Those presses really do work, but printing them teaches a modifier that
 * carries no meaning, and a sheet padded with synonyms is the learning curve
 * this feature exists to remove. So a combo is printed only when dropping a
 * modifier — or the legacy spelling of the key — changes the answer.
 */
function isRedundantPress(probe, key, flags, contextState, command) {
  for (const flag of MODIFIER_FLAGS) {
    if (!flags[flag]) continue;
    const simpler = { ...flags, [flag]: false };
    if (probe.resolve(pressEvent(key, simpler), contextState) === command) return true;
  }
  const canonical = KEY_ALIASES[key];
  if (canonical && probe.resolve(pressEvent(canonical, flags), contextState) === command) return true;
  return false;
}

/**
 * Press the whole key space at one probe.
 * @returns {Array<{id, combo, surface, file, context, when}>}
 */
export function probeSurface(probe) {
  const rows = new Map();
  for (const context of probe.contexts) {
    for (const key of KEY_SPACE) {
      for (const state of MODIFIER_STATES) {
        const command = probe.resolve(pressEvent(key, state.flags), context.state);
        if (!command) continue;
        if (isRedundantPress(probe, key, state.flags, context.state, command)) continue;
        const combo = comboOf(modifierTokens(state.flags), key, key.length === 1);
        const rowKey = `${command} ${combo}`;
        if (rows.has(rowKey)) continue;
        rows.set(rowKey, {
          id: command,
          combo,
          surface: probe.surface,
          file: probe.file,
          context: context.id,
          when: context.when,
        });
      }
    }
  }
  return [...rows.values()];
}

/** Every binding reachable by calling a resolver, across all probes. */
export function probeAll() {
  return PROBES.flatMap((probe) => probeSurface(probe));
}

// --- the surfaces that keep their keys in a closure --------------------------

/**
 * Bindings this module cannot call, only read.
 *
 * `effect` is a hash of the exact statement the key runs, produced by
 * shortcutScan.js. It is here so that changing what a key DOES — not just
 * which key it is — breaks the test and puts the wording back in front of a
 * human. Regenerate with `node scripts/shortcut-report.mjs`.
 *
 * Every entry here is a file that should eventually resolve keys through a
 * pure function like the probes above; see the doc's "What would make this
 * unnecessary".
 */
export const INLINE_BINDINGS = Object.freeze([
  {
    id: "3d.home",
    surface: "3d",
    file: "Board3DView.jsx",
    combo: "F",
    when: "on the 3D tab",
    effect: "c8f2a2c9",
  },
  {
    id: "3d.rotate",
    surface: "3d",
    file: "Board3DView.jsx",
    combo: "9",
    when: "on the 3D tab",
    effect: "33d175e0",
  },
  {
    id: "3d.flip",
    surface: "3d",
    file: "Board3DView.jsx",
    combo: "Mod+F",
    when: "on the 3D tab",
    effect: "90c306ad",
  },
  {
    id: "field.commit",
    surface: "properties",
    file: "PropertiesPanel.jsx",
    combo: "Enter",
    when: "in a Properties value box",
    effect: "622f16dc",
  },
  {
    id: "field.cancel",
    surface: "properties",
    file: "PropertiesPanel.jsx",
    combo: "Esc",
    when: "in a Properties value box",
    effect: "25a7a750",
  },
  {
    id: "net-width.commit",
    surface: "net-width",
    file: "NetWidthRow.jsx",
    combo: "Enter",
    when: "in the net Width box",
    effect: "3a007515",
  },
  {
    id: "move-exact.units",
    surface: "move-exact",
    file: "BoardContextMenu.jsx",
    // Ctrl, never Cmd. On macOS the app's own Quit accelerator is Cmd+Q and
    // AppKit consumes it before WKWebView sees the key, so publishing it here
    // would teach a Mac user a keystroke that closes the app with a half-typed
    // offset in the box. Altium's own binding is Ctrl+Q
    // (https://www.altium.com/documentation/cstu/get-x-y-offsets).
    combo: "Ctrl+Q",
    when: "in the Move-by-exact-amount box",
    effect: "ba602e52",
  },
]);

/** The files INLINE_BINDINGS claims to describe, for the test to scan. */
export const INLINE_SOURCES = Object.freeze([
  { surface: "3d", file: "Board3DView.jsx" },
  { surface: "properties", file: "PropertiesPanel.jsx" },
  { surface: "move-exact", file: "BoardContextMenu.jsx" },
  { surface: "net-width", file: "NetWidthRow.jsx" },
]);

// --- the words ---------------------------------------------------------------

/** Sheet sections, in the order a person looks for them. */
export const SHORTCUT_GROUPS = Object.freeze([
  { id: "navigate", title: "Move around" },
  { id: "select", title: "Select and highlight" },
  { id: "view", title: "Look at the board" },
  { id: "edit", title: "Change the board" },
  { id: "help", title: "This list" },
]);

const GROUP_IDS = new Set(SHORTCUT_GROUPS.map((group) => group.id));

/**
 * One line of copy per command id.
 *
 * Written by hand — a machine can tell you `view.fit` is on `F`, not what to
 * call it. `order` sorts within a group; ties fall back to the combo.
 *
 * Every id a probe or an inline binding produces must appear here. The test
 * enforces both directions, so an unlisted binding cannot ship and a line of
 * copy cannot outlive the key it describes.
 */
export const SHORTCUT_COPY = Object.freeze({
  // navigate
  "tab.schematic": { group: "navigate", order: 10, label: "Schematic" },
  "tab.pcb": { group: "navigate", order: 20, label: "Board layout" },
  "tab.3d": { group: "navigate", order: 30, label: "3D" },
  "tab.split": { group: "navigate", order: 40, label: "Schematic and board side by side" },
  "layers.show": { group: "navigate", order: 50, label: "Show the layer bar" },
  "messages.toggle": { group: "navigate", order: 60, label: "Show or hide the messages drawer" },

  // select
  "selection.clear": { group: "select", order: 10, label: "Drop the selection" },
  "filter.clear": { group: "select", order: 20, label: "Clear the highlight filter" },
  "highlight.cycle": { group: "select", order: 30, label: "Cycle how the rest of the board is dimmed" },
  "mask.decrease": { group: "select", order: 40, label: "Show more of the dimmed board" },
  "mask.increase": { group: "select", order: 50, label: "Show less of the dimmed board" },

  // view
  // `F` appears twice in this group, and that is not a contradiction: it means
  // "fit the view" on both tabs, handled by whichever view is on screen.
  // BoardWorkspace stands down on the 3D tab so only one handler ever runs.
  "view.fit": { group: "view", order: 10, label: "Fit the whole board in the pane" },
  // The `when` is the honest half of a deliberate divergence: these are KiCad's
  // zoom keys, and in Altium the same two keys step the layer stack. Someone
  // whose hands come from Altium should read that here rather than discover it.
  "view.zoom-in": {
    group: "view",
    order: 12,
    label: "Zoom in",
    when: "in Altium these step the layer stack; here they zoom, and L opens the layers",
  },
  "view.zoom-out": { group: "view", order: 13, label: "Zoom out" },
  "single-layer.cycle": { group: "view", order: 20, label: "Cycle single-layer mode" },
  "hud.toggle": { group: "view", order: 30, label: "Show or hide the coordinate readout" },
  "units.toggle": { group: "view", order: 40, label: "Switch between mm and mil" },
  "regions.toggle": { group: "view", order: 50, label: "Show or hide the block areas" },
  "measure.toggle": { group: "view", order: 60, label: "Measure between two points" },
  "canvas.delta-origin": { group: "view", order: 70, label: "Zero the coordinate readout here" },
  "3d.home": { group: "view", order: 11, label: "Fit the board in the 3D view" },
  "3d.rotate": { group: "view", order: 90, label: "Turn the board 90°" },
  "3d.flip": { group: "view", order: 100, label: "Look at the other side" },

  // edit
  "edit-mode.toggle": { group: "edit", order: 10, label: "Move parts — this writes your board file" },
  "net-width.commit": { group: "edit", order: 46, label: "Apply the trace width you typed" },
  // Four rows rather than one, because four keys is what a hand learns. The
  // `when` is the whole rule: it is the selection that moves, by the step the
  // strip is set to, and holding the key repeats it.
  "nudge.left": { group: "edit", order: 12, label: "Nudge left one step", when: "with a part selected in move mode" },
  "nudge.right": { group: "edit", order: 13, label: "Nudge right one step", when: "with a part selected in move mode" },
  "nudge.up": { group: "edit", order: 14, label: "Nudge up one step", when: "with a part selected in move mode" },
  "nudge.down": { group: "edit", order: 15, label: "Nudge down one step", when: "with a part selected in move mode" },
  "edit.undo": { group: "edit", order: 20, label: "Undo the last move" },
  "edit.redo": { group: "edit", order: 25, label: "Redo it" },
  "canvas.rotate.ccw": { group: "edit", order: 30, label: "Turn the part you are dragging left" },
  "canvas.rotate.cw": { group: "edit", order: 40, label: "Turn the part you are dragging right" },
  "canvas.cancel-drag": { group: "edit", order: 50, label: "Abandon the move and put the part back" },
  "field.commit": { group: "edit", order: 60, label: "Apply the value you typed" },
  "field.cancel": { group: "edit", order: 70, label: "Throw the value away and leave it as it was" },
  "move-exact.units": { group: "edit", order: 80, label: "Switch the offset boxes between mm and mil" },

  // help. The resolver returns a toggle, but the sheet autofocuses its search
  // box and no key fires while you are typing, so in practice `?` only ever
  // opens. The row says the thing that is always true.
  "sheet.toggle": { group: "help", order: 10, label: "Open this list" },
  "sheet.close": { group: "help", order: 20, label: "Close this list" },
});

// --- assembly ----------------------------------------------------------------

/** Every binding the app has: probed where we can call it, declared where we cannot. */
export function allBindings() {
  return [...probeAll(), ...INLINE_BINDINGS];
}

/** Binding ids with no line of copy. The test's teeth in one direction. */
export function bindingsMissingCopy(bindings = allBindings()) {
  return [...new Set(bindings.filter((row) => !SHORTCUT_COPY[row.id]).map((row) => row.id))].sort();
}

/** Copy for ids nothing resolves to any more. The test's teeth in the other. */
export function copyWithoutBindings(bindings = allBindings()) {
  const live = new Set(bindings.map((row) => row.id));
  return Object.keys(SHORTCUT_COPY)
    .filter((id) => !live.has(id))
    .sort();
}

/**
 * The sheet, grouped and sorted, ready to render.
 *
 * A command reachable by two keys (fit is `F` and `Mod+PgDn`) is one row with
 * two combos, because that is one thing to learn, not two.
 *
 * @returns {Array<{id, title, rows: Array<{id, label, combos, when, file}>}>}
 */
export function buildShortcutSheet(bindings = allBindings()) {
  const byId = new Map();
  for (const binding of bindings) {
    const copy = SHORTCUT_COPY[binding.id];
    if (!copy) continue; // reported by bindingsMissingCopy, not rendered as a blank
    const row = byId.get(binding.id) || {
      id: binding.id,
      label: copy.label,
      order: copy.order ?? 999,
      group: copy.group,
      combos: [],
      when: binding.when || "",
      file: binding.file,
    };
    if (!row.combos.includes(binding.combo)) row.combos.push(binding.combo);
    // First condition seen wins. Contexts are swept widest-first, so a binding
    // that works unconditionally can never pick up a qualifier from a narrower
    // sweep of the same key.
    if (binding.when && !row.when) row.when = binding.when;
    byId.set(binding.id, row);
  }

  return SHORTCUT_GROUPS.map((group) => ({
    id: group.id,
    title: group.title,
    rows: [...byId.values()]
      .filter((row) => row.group === group.id)
      .sort((a, b) => a.order - b.order || a.id.localeCompare(b.id))
      .map((row) => ({
        id: row.id,
        label: row.label,
        combos: [...row.combos].sort((a, b) => a.length - b.length || a.localeCompare(b)),
        when: row.when,
        file: row.file,
      })),
  })).filter((group) => group.rows.length > 0);
}

/** Group ids used by copy that the sheet has no section for. */
export function copyWithUnknownGroup() {
  return Object.entries(SHORTCUT_COPY)
    .filter(([, copy]) => !GROUP_IDS.has(copy.group))
    .map(([id]) => id)
    .sort();
}
