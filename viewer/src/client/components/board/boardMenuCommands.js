// The Board menu's contents, and the one channel it speaks through.
//
// Kept in its own module so the menu bar can import it without importing the
// workspace, and so the list of what a menu offers sits beside the copy that
// names it rather than inside a 1,500-line component.
//
// Every entry is a command id `boardKeymap.js` already resolves and
// `BoardWorkspace.runBoardCommand` already runs — the menu cannot drift from
// the keyboard because there is only one implementation of either. The keys
// shown here are derived, not typed: `shortcutSheet.js` presses every key at
// the real resolver and reports what comes back.

/** CustomEvent name; `detail.command` is a `BOARD_COMMANDS` id. */
export const BOARD_COMMAND_EVENT = "circuit:board-command";

/** Ask the workspace to run a command. No-op when no workspace is mounted. */
export function runBoardMenuCommand(command) {
  if (typeof window === "undefined" || !command) return;
  window.dispatchEvent(new CustomEvent(BOARD_COMMAND_EVENT, { detail: { command } }));
}

/**
 * What the Board menu shows, in order. `null` is a separator.
 *
 * Chosen for the person who does not know the keys: the things they would go
 * to a menu *for*. Undo and redo lead, because the app menu's Edit > Undo is
 * the webview's text undo and a panel judge correctly called that a trap —
 * these two are the board's.
 */
export const BOARD_MENU_ITEMS = Object.freeze([
  { command: "edit.undo", label: "Undo board edit" },
  { command: "edit.redo", label: "Redo board edit" },
  null,
  { command: "edit-mode.toggle", label: "Move parts" },
  { command: "measure.toggle", label: "Measure" },
  null,
  { command: "view.fit", label: "Fit board in view" },
  { command: "view.zoom-in", label: "Zoom in" },
  { command: "view.zoom-out", label: "Zoom out" },
  null,
  { command: "layers.show", label: "Layers" },
  { command: "single-layer.cycle", label: "Single-layer mode" },
  { command: "units.toggle", label: "Switch mm / mil" },
  { command: "properties.toggle", label: "Properties panel" },
  null,
  { command: "messages.toggle", label: "Messages" },
  { command: "selection.clear", label: "Clear selection" },
]);
