// boardKeymap — one arbiter for every key the board workspace answers to.
//
// DOM-free and React-free, like viewportTools.js and boardSource.js, so
// node:test covers the whole contract without a browser. The point is not
// tidiness: two `window` keydown listeners already see every key (this
// workspace and PcbCanvas), and a binding split across two files is how the
// last collision got built — `L` toggled the Messages drawer for months while
// Altium's own `L` (Layers And Colors) was unreachable.
//
// The rule this module exists to enforce: a key that does something ELSE is
// worse than a key that does nothing. A no-op reads as "not built yet"; a
// misfire reads as "this is not a real EDA tool". So every key here either
// resolves to the command an Altium user expects, or resolves to `null` and is
// left to the browser.
//
// Bindings follow Altium's published PCB shortcut table where one exists:
// https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors
// Where Altium publishes nothing the comment says **unverified** and names what
// we chose instead. Nothing here is invented from memory.

/**
 * Every command id this arbiter can return. The workspace switch must handle
 * all of them; the test sweeps the key space and fails on anything not listed.
 */
export const BOARD_COMMANDS = Object.freeze([
  "edit.undo",
  "edit.redo",
  "measure.toggle",
  "view.fit",
  "view.zoom-in",
  "view.zoom-out",
  "selection.clear",
  "filter.clear",
  "single-layer.cycle",
  "hud.toggle",
  "messages.toggle",
  "tab.schematic",
  "tab.pcb",
  "tab.3d",
  "tab.split",
  "edit-mode.toggle",
  "nudge.left",
  "nudge.right",
  "nudge.up",
  "nudge.down",
  "units.toggle",
  "highlight.cycle",
  "mask.decrease",
  "mask.increase",
  "layers.show",
  "regions.toggle",
]);

const COMMAND_SET = new Set(BOARD_COMMANDS);

/**
 * The commands a held key may repeat.
 *
 * Everything else is a toggle or a one-shot, and auto-repeat turns those into
 * either a flicker (a toggle at ~25 Hz) or data loss (⌘Z unwinding the whole
 * 50-entry history in two seconds, with no redo to get it back). The mask
 * level is the one binding whose meaning really is "keep going" — Altium steps
 * it live while the filter is applied, `[` / `]` (ALTIUM-NOTES §2).
 *
 * The sibling arbiters made the same call for the same reason:
 * `canvasPointer.js` drops `event.repeat` outright.
 */
const REPEATABLE = new Set([
  "mask.decrease",
  "mask.increase",
  "view.zoom-in",
  "view.zoom-out",
  // A nudge is the one *edit* a held key may repeat, and it has to: one press
  // is one snap step, so moving a part 5mm at 0.5mm steps is ten of them.
  // Every repeat is its own write and its own undo entry, which is the same
  // bargain Altium makes.
  "nudge.left",
  "nudge.right",
  "nudge.up",
  "nudge.down",
]);

/** True when `id` is something the workspace switch has to handle. */
export function isBoardCommand(id) {
  return COMMAND_SET.has(id);
}

/**
 * True when the event came from somewhere the user is typing.
 *
 * `select` is in the list because a `<select>` is a typing target even though
 * nothing is typed into it: native keyboard typeahead picks an option by its
 * first character, so `3` on the `Turn by` dropdown means "30°" to the browser
 * and would mean "the 3D tab" to us — the tab switching out from under a
 * rotation being set is exactly the misfire class this module exists to kill.
 * Every sibling guard in this codebase already includes it (`Board3DView.jsx`,
 * `ui/dom.js` `isEditableTarget`).
 */
export function isTypingTarget(target) {
  const tag = String(target?.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target?.isContentEditable === true;
}

/**
 * True when the event came from inside something laid over the board.
 *
 * A dialog, a menu or a popover owns the keyboard while it is up. Without this,
 * every plain-letter binding leaks straight through to the board behind the
 * overlay: click a heading inside the shortcut sheet (Radix's focus scope is a
 * `tabIndex=-1` div, so focus leaves the search box but stays trapped) and `E`
 * turns on move mode, `3` switches tab, all behind the modal.
 *
 * `Escape` is the sharp end. Radix dismisses on a **capture-phase document**
 * listener and never calls `preventDefault`, so `defaultPrevented` above is
 * still false when this window listener runs — closing a dialog would also drop
 * the user's selection and disarm measure. Matching on the role rather than on
 * a Radix-specific attribute keeps this true for any overlay we mount later.
 */
export function isOverlayTarget(target) {
  if (!target || typeof target.closest !== "function") return false;
  return Boolean(target.closest('[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"],[aria-modal="true"]'));
}

/**
 * Resolve one keydown to a workspace command.
 *
 * @param {{key: string, shiftKey?: boolean, metaKey?: boolean, ctrlKey?: boolean,
 *          altKey?: boolean, defaultPrevented?: boolean}} event
 * @param {{typing?: boolean, canUndo?: boolean, canRedo?: boolean}} mode
 *   `typing` — focus is in a field, computed by the caller with isTypingTarget.
 *   `canUndo` / `canRedo` — the board file has an edit waiting in that
 *   direction AND it can be applied right now. The caller owns that
 *   conjunction (see BoardWorkspace): the history survives leaving move mode
 *   but the placement binding does not, so an undo offered outside move mode
 *   would fail into a strip that is not on screen — and neither may fire while
 *   a write is in flight.
 * @returns {string|null} a command id, or null when this key is not ours.
 */
export function resolveBoardKey(event, mode = {}) {
  const command = resolveBoardKeyRaw(event, mode);
  if (!command) return null;
  // Auto-repeat is decided once, here, rather than at each binding: a held key
  // is the same event over and over and only the mask steppers mean anything
  // by the second one.
  if (event.repeat && !REPEATABLE.has(command)) return null;
  return command;
}

function resolveBoardKeyRaw(event, mode = {}) {
  if (!event || typeof event.key !== "string") return null;

  // Something already handled it — a dialog closing on Escape, a menu, a
  // dropdown. Clearing the user's selection as a side effect of dismissing a
  // popover is exactly the kind of misfire this module exists to stop.
  if (event.defaultPrevented) return null;

  if (mode.typing) return null;

  // The board is behind an overlay: the overlay has the keyboard. Checked here
  // rather than left to each caller so a surface mounted later cannot forget.
  if (isOverlayTarget(event.target)) return null;

  // Alt is the OS's modifier: Alt+F is the browser's File menu on Windows and
  // Option+letter types a different character on macOS. Nothing on this board
  // is worth fighting a window manager over, and a key that fires under Alt
  // fires while the user is talking to their OS.
  if (event.altKey) return null;

  const key = event.key;
  const lower = key.length === 1 ? key.toLowerCase() : key;

  if (event.metaKey || event.ctrlKey) {
    // Undo the last edit to the board file. Ctrl/⌘+Z is not in Altium's PCB
    // shortcut table (that page lists no Edit-menu accelerators) — it is the
    // platform binding every application on both platforms already owns, and
    // an EDA tool that ignores it is the single loudest "this is a toy" signal
    // we ship. Gated on `canUndo` so the key falls through to the browser when
    // there is nothing of ours to undo, instead of eating it silently.
    if (lower === "z" && !event.shiftKey) return mode.canUndo ? "edit.undo" : null;
    // Redo, on both spellings every application on both platforms already
    // owns: ⇧⌘Z on macOS, Ctrl+Y on Windows. Gated on `canRedo` for the same
    // reason undo is gated on `canUndo` — with nothing to redo the key falls
    // through to the browser rather than being eaten silently.
    if (lower === "z" && event.shiftKey) return mode.canRedo ? "edit.redo" : null;
    if (lower === "y") return mode.canRedo ? "edit.redo" : null;
    // Altium: "Ctrl+Arrow Keys: Move selected objects in corresponding
    // directions by one snap grid unit"
    // (https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors).
    // Plain arrows are deliberately NOT bound: in Altium they move the *cursor*,
    // which this app does not have, and binding them to the selection would
    // make an Altium hand move a part while expecting to move a crosshair —
    // a silent geometry change, the worst misfire this arbiter can produce.
    //
    // `canNudge` is the caller's conjunction, the same shape `canUndo` uses:
    // move mode is on, a placement is selected, the file can express its
    // position, and no write is in flight. Without it the key is left to the
    // browser, which scrolls — visible, harmless, and honest about the fact
    // that nothing was selected.
    if (key === "ArrowLeft") return mode.canNudge ? "nudge.left" : null;
    if (key === "ArrowRight") return mode.canNudge ? "nudge.right" : null;
    if (key === "ArrowUp") return mode.canNudge ? "nudge.up" : null;
    if (key === "ArrowDown") return mode.canNudge ? "nudge.down" : null;
    if (lower === "m") return "measure.toggle"; // ALTIUM-NOTES §7: Ctrl+M measures
    if (key === "PageDown") return "view.fit"; // Altium: Ctrl+PgDn zoom to fit
    return null;
  }

  // Altium does NOT clear a filter with Escape (Shift+C does) — but on the web
  // Escape is what a stuck user presses, so we honour both. ALTIUM-NOTES §2.
  //
  // What Escape does NOT do here: cancel a live placement drag. PcbCanvas owns
  // that on its own listener and does not publish the drag, so both handlers
  // fire and cancelling a drag also drops the selection. Fixing it needs the
  // `onMoveChange` prop specified in ide-altium-parity.md ("Publishing the live
  // move to the workspace"), which is PcbCanvas's file, not this one. When it
  // lands, this becomes the last case of an ordered stack rather than the only
  // one.
  if (key === "Escape") return "selection.clear";

  // Zoom, before the Shift branch: `+` IS Shift+`=` on most layouts, so a zoom
  // key that only answers unshifted answers half the keyboards in the world.
  //
  // The rail has printed `+` and `−` beside its two zoom buttons since it was
  // built and nothing answered either — the app's own tooltip teaching a
  // shortcut that does not exist. Bound to the buttons they are printed on,
  // which is also KiCad's binding (View » Zoom In / Zoom Out). **A deliberate
  // divergence from Altium**, where `+` and `−` step the layer stack
  // (ALTIUM-NOTES §7); the shortcut sheet says so in the row's own copy, and
  // stepping layers lives in the layer bar `L` opens. Between teaching a wrong
  // reflex and honouring the label already on screen, the label wins.
  if (lower === "+" || lower === "=") return "view.zoom-in";
  if (lower === "-" || lower === "_") return "view.zoom-out";

  if (event.shiftKey) {
    if (lower === "c") return "filter.clear"; // Altium: Shift+C clears the filter
    if (lower === "s") return "single-layer.cycle"; // Altium: Shift+S single-layer cycle
    if (lower === "h") return "hud.toggle"; // Altium: Shift+H heads-up display
    // Messages moved off `L` (Altium's Layers And Colors) — and then off
    // `Shift+M`, which is not free either: Altium's Board Insight **Lens** is
    // `Shift+M`, with `Alt`+wheel for its zoom and `Shift+Ctrl+M` for auto-zoom
    // (ALTIUM-NOTES §3,
    // https://www.altium.com/documentation/altium-designer/pcb/board-insight-system).
    // Landing our drawer on a published Altium binding is the same sin as `L`.
    //
    // Altium publishes no shortcut for its Messages panel — **unverified** — so
    // the key is our own call, taken from what §3's table and the PCB shortcut
    // page leave unspent: `Shift+N` (nothing in Altium's PCB editor answers to
    // it), keeping the Shift+<letter> show-or-hide-a-surface family it shares
    // with Shift+S and Shift+H. N for notes — the drawer is our DRC findings.
    // The MessagesPanel chevron prints it, so the move is learnable rather than
    // silent.
    if (lower === "n") return "messages.toggle";
    return null;
  }

  switch (lower) {
    // Altium's 1/2/3 are Board Planning / 2D / 3D. We have no board-planning
    // mode, and a schematic pane Altium's PCB editor does not have, so 1 opens
    // the schematic. A stated difference, not a copy.
    case "1":
      return "tab.schematic";
    case "2":
      return "tab.pcb";
    case "3":
      return "tab.3d";
    case "0":
      return "tab.split";
    case "e":
      return "edit-mode.toggle";
    // Altium fits with Ctrl+PgDn (bound above). `F` is ours as well because a
    // browser eats PageDown in too many contexts. ALTIUM-NOTES §7.
    case "f":
      return "view.fit";
    case "q":
      return "units.toggle"; // Altium: Q toggles mm ↔ mil
    case "m":
      return "highlight.cycle";
    case "[":
      return "mask.decrease"; // Altium: [ / ] step the mask level live
    case "]":
      return "mask.increase";
    // Altium's L is Layers And Colors. It used to toggle our Messages drawer,
    // which is neither of the two things Altium's L means and cost us the top
    // key an EE presses in the first minute.
    case "l":
      return "layers.show";
    case "r":
      return "regions.toggle"; // rooms — Altium's own word for a named board area
    default:
      return null;
  }
}
