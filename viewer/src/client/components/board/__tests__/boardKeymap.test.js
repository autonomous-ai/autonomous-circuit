import assert from "node:assert/strict";
import test from "node:test";

import { BOARD_COMMANDS, isBoardCommand, isOverlayTarget, isTypingTarget, resolveBoardKey } from "../boardKeymap.js";

/** A DOM-ish node with just the `closest` the overlay guard calls. */
function node(tagName, { roles = [], isContentEditable = false } = {}) {
  const owned = new Set(roles);
  return {
    tagName,
    isContentEditable,
    closest(selector) {
      return selector
        .split(",")
        .some((part) => owned.has(part.trim()))
        ? { tagName: "DIV" }
        : null;
    },
  };
}

/** A keydown with the fields the arbiter reads and nothing else. */
function press(key, options = {}) {
  return {
    key,
    shiftKey: false,
    metaKey: false,
    ctrlKey: false,
    altKey: false,
    defaultPrevented: false,
    ...options,
  };
}

// The whole keymap, written out. Every row is one keystroke an engineer will
// actually press; the sweep below proves nothing outside this table fires.
const TABLE = Object.freeze([
  { key: "z", mods: { metaKey: true }, mode: { canUndo: true }, command: "edit.undo" },
  { key: "z", mods: { ctrlKey: true }, mode: { canUndo: true }, command: "edit.undo" },
  { key: "z", mods: { metaKey: true, shiftKey: true }, mode: { canRedo: true }, command: "edit.redo" },
  { key: "y", mods: { ctrlKey: true }, mode: { canRedo: true }, command: "edit.redo" },
  { key: "m", mods: { metaKey: true }, command: "measure.toggle" },
  { key: "m", mods: { ctrlKey: true }, command: "measure.toggle" },
  { key: "PageDown", mods: { ctrlKey: true }, command: "view.fit" },
  { key: "Escape", command: "selection.clear" },
  { key: "c", mods: { shiftKey: true }, command: "filter.clear" },
  { key: "s", mods: { shiftKey: true }, command: "single-layer.cycle" },
  { key: "h", mods: { shiftKey: true }, command: "hud.toggle" },
  { key: "n", mods: { shiftKey: true }, command: "messages.toggle" },
  { key: "1", command: "tab.schematic" },
  { key: "2", command: "tab.pcb" },
  { key: "3", command: "tab.3d" },
  { key: "0", command: "tab.split" },
  { key: "e", command: "edit-mode.toggle" },
  { key: "f", command: "view.fit" },
  { key: "q", command: "units.toggle" },
  // Altium's own two: F11 shows/hides the Properties panel, Tab opens
  // properties-on-the-fly while placing. Tab only binds when there is a part
  // to type coordinates for, so tabbing through the page still works.
  { key: "F11", command: "properties.toggle" },
  { key: "Tab", command: "properties.focus", mode: { canNudge: true } },
  { key: "m", command: "highlight.cycle" },
  { key: "[", command: "mask.decrease" },
  { key: "]", command: "mask.increase" },
  { key: "l", command: "layers.show" },
  { key: "r", command: "regions.toggle" },
  // Zoom, on the keys the rail already prints beside its two buttons. All four
  // spellings, because `+` is Shift+`=` on most layouts and `_` is Shift+`-`:
  // a zoom key that only answers unshifted answers half the keyboards.
  // Altium: Ctrl+arrows move the selection by one snap grid unit. Plain arrows
  // are Altium's *cursor* keys and stay unbound here (see the resolver).
  { key: "ArrowLeft", mods: { ctrlKey: true }, mode: { canNudge: true }, command: "nudge.left" },
  { key: "ArrowRight", mods: { metaKey: true }, mode: { canNudge: true }, command: "nudge.right" },
  { key: "ArrowUp", mods: { ctrlKey: true }, mode: { canNudge: true }, command: "nudge.up" },
  { key: "ArrowDown", mods: { ctrlKey: true }, mode: { canNudge: true }, command: "nudge.down" },
  { key: "+", mods: { shiftKey: true }, command: "view.zoom-in" },
  { key: "=", command: "view.zoom-in" },
  { key: "-", command: "view.zoom-out" },
  { key: "_", mods: { shiftKey: true }, command: "view.zoom-out" },
]);

test("every published binding resolves to its command, upper and lower case", () => {
  for (const row of TABLE) {
    assert.equal(resolveBoardKey(press(row.key, row.mods), row.mode || {}), row.command, `${row.key} ${JSON.stringify(row.mods || {})}`);
    if (row.key.length === 1) {
      const upper = row.key.toUpperCase();
      if (upper !== row.key) {
        assert.equal(resolveBoardKey(press(upper, row.mods), row.mode || {}), row.command, `${upper} must match ${row.key}`);
      }
    }
  }
});

test("every command in BOARD_COMMANDS is reachable from some keystroke", () => {
  const reached = new Set(TABLE.map((row) => row.command));
  for (const id of BOARD_COMMANDS) assert.ok(reached.has(id), `${id} has no binding`);
  assert.equal(new Set(BOARD_COMMANDS).size, BOARD_COMMANDS.length, "command ids must be unique");
});

// The regression this module exists for: `L` used to toggle the Messages
// drawer, which is neither of Altium's two meanings for the key.
test("L never toggles Messages, in any state", () => {
  const modes = [{}, { typing: false }, { canUndo: true }, { canUndo: false, typing: false }];
  const mods = [
    {},
    { shiftKey: true },
    { ctrlKey: true },
    { metaKey: true },
    { altKey: true },
    { shiftKey: true, metaKey: true },
  ];
  for (const mode of modes) {
    for (const modifiers of mods) {
      for (const key of ["l", "L"]) {
        assert.notEqual(resolveBoardKey(press(key, modifiers), mode), "messages.toggle");
      }
    }
  }
  assert.equal(resolveBoardKey(press("l"), {}), "layers.show");
  assert.equal(resolveBoardKey(press("n", { shiftKey: true }), {}), "messages.toggle");
});

// The same mistake one key over. ALTIUM-NOTES §3: `Shift+M` shows Altium's
// Board Insight Lens, `Shift+Ctrl+M` auto-zooms it. Spending it on our drawer
// would have moved the misfire rather than fixed it.
test("Shift+M is Altium's Insight Lens and stays unbound", () => {
  for (const key of ["m", "M"]) {
    assert.equal(resolveBoardKey(press(key, { shiftKey: true }), {}), null, key);
    assert.equal(resolveBoardKey(press(key, { shiftKey: true, ctrlKey: true }), {}), "measure.toggle", key);
  }
});

// Auto-repeat: a held key is the same event over and over. Only the mask
// steppers mean anything by the second one — a held ⌘Z unwound the whole
// 50-entry history at ~25/sec into a hook with no redo stack.
test("a held key repeats only where repeating is the meaning", () => {
  assert.equal(resolveBoardKey(press("z", { metaKey: true, repeat: true }), { canUndo: true }), null);
  assert.equal(resolveBoardKey(press("e", { repeat: true }), {}), null);
  assert.equal(resolveBoardKey(press("3", { repeat: true }), {}), null);
  assert.equal(resolveBoardKey(press("Escape", { repeat: true }), {}), null);
  assert.equal(resolveBoardKey(press("[", { repeat: true }), {}), "mask.decrease");
  assert.equal(resolveBoardKey(press("]", { repeat: true }), {}), "mask.increase");
  // The first press of every binding still lands.
  for (const row of TABLE) {
    assert.equal(
      resolveBoardKey(press(row.key, { ...(row.mods || {}), repeat: false }), row.mode || {}),
      row.command,
      row.key,
    );
  }
});

// An overlay owns the keyboard while it is up. The sharp case is Escape:
// Radix dismisses on a capture-phase document listener and never calls
// preventDefault, so without this the sheet closing also dropped the selection
// and disarmed measure.
test("a key pressed inside an overlay never reaches the board", () => {
  const inDialog = node("DIV", { roles: ['[role="dialog"]'] });
  const inMenu = node("DIV", { roles: ['[role="menu"]'] });
  for (const target of [inDialog, inMenu]) {
    for (const row of TABLE) {
      assert.equal(
        resolveBoardKey(press(row.key, { ...(row.mods || {}), target }), row.mode || {}),
        null,
        `${row.key} inside an overlay`,
      );
    }
  }
  // The same key over the board still works — the guard is about where the
  // press came from, not about the key.
  assert.equal(resolveBoardKey(press("e", { target: node("svg") }), {}), "edit-mode.toggle");
});

test("isOverlayTarget only fires for a real overlay ancestor", () => {
  assert.equal(isOverlayTarget(node("DIV", { roles: ['[role="dialog"]'] })), true);
  assert.equal(isOverlayTarget(node("DIV", { roles: ['[aria-modal="true"]'] })), true);
  assert.equal(isOverlayTarget(node("svg")), false);
  // A bare object with no `closest` is what the probe sweep passes.
  assert.equal(isOverlayTarget({ tagName: "DIV" }), false);
  assert.equal(isOverlayTarget(null), false);
});

test("Ctrl/⌘+Z undoes the board edit, and only when there is one to undo", () => {
  assert.equal(resolveBoardKey(press("z", { metaKey: true }), { canUndo: true }), "edit.undo");
  assert.equal(resolveBoardKey(press("z", { ctrlKey: true }), { canUndo: true }), "edit.undo");
  // Nothing to undo: the key belongs to the browser, not to us.
  assert.equal(resolveBoardKey(press("z", { metaKey: true }), { canUndo: false }), null);
  assert.equal(resolveBoardKey(press("z", { metaKey: true }), {}), null);
  // Plain Z is not undo.
  assert.equal(resolveBoardKey(press("z"), { canUndo: true }), null);
});

// Redo is bound on both spellings every application already owns, and gated
// the same way undo is: with nothing to redo the key belongs to the browser,
// because a redo item that did nothing is the same broken promise as the Undo
// this module was written to fix.
test("redo answers ⇧⌘Z and Ctrl+Y, and only when there is something to redo", () => {
  for (const event of [
    press("z", { metaKey: true, shiftKey: true }),
    press("Z", { ctrlKey: true, shiftKey: true }),
    press("y", { ctrlKey: true }),
    press("Y", { metaKey: true }),
  ]) {
    assert.equal(resolveBoardKey(event, { canRedo: true }), "edit.redo");
    assert.equal(resolveBoardKey(event, { canRedo: false, canUndo: true }), null);
  }
  // Undo and redo never answer the same press.
  assert.equal(resolveBoardKey(press("z", { metaKey: true }), { canUndo: true, canRedo: true }), "edit.undo");
  assert.equal(
    resolveBoardKey(press("z", { metaKey: true, shiftKey: true }), { canUndo: true, canRedo: true }),
    "edit.redo",
  );
  // Held, it does not run away either.
  assert.equal(resolveBoardKey(press("y", { ctrlKey: true, repeat: true }), { canRedo: true }), null);
});

test("typing anywhere silences the whole keymap", () => {
  for (const row of TABLE) {
    assert.equal(resolveBoardKey(press(row.key, row.mods), { ...(row.mode || {}), typing: true }), null);
  }
});

test("a key another handler already claimed is left alone", () => {
  // Escape dismissing a dropdown must not also clear the user's selection.
  assert.equal(resolveBoardKey(press("Escape", { defaultPrevented: true }), {}), null);
  assert.equal(resolveBoardKey(press("f", { defaultPrevented: true }), {}), null);
});

test("Alt belongs to the window manager, not to the board", () => {
  for (const key of ["f", "l", "e", "q", "1"]) {
    assert.equal(resolveBoardKey(press(key, { altKey: true }), { canUndo: true }), null);
  }
});

test("a modifier changes the command, it does not stack two of them", () => {
  assert.equal(resolveBoardKey(press("m"), {}), "highlight.cycle");
  assert.equal(resolveBoardKey(press("n", { shiftKey: true }), {}), "messages.toggle");
  assert.equal(resolveBoardKey(press("m", { metaKey: true }), {}), "measure.toggle");
  assert.notEqual(resolveBoardKey(press("Escape"), {}), resolveBoardKey(press("c", { shiftKey: true }), {}));
});

// PcbCanvas owns Space (delta origin today, rotation in the spec) on its own
// listener. Two handlers answering one key is the collision this module exists
// to prevent, so the workspace must not claim it.
test("keys another surface owns resolve to null here", () => {
  for (const key of [" ", "Insert", "Enter", "Tab", "ArrowUp", "PageUp", "F5", "Delete"]) {
    assert.equal(resolveBoardKey(press(key), { canUndo: true }), null, key);
  }
});

test("no keystroke in the whole space returns anything the workspace cannot dispatch", () => {
  const keys = [
    ..."abcdefghijklmnopqrstuvwxyz",
    ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    ..."0123456789",
    ..."[]{}()/\\,.;'`-=+*!@#$%^&",
    " ",
    "Escape",
    "Enter",
    "Tab",
    "Insert",
    "Delete",
    "Backspace",
    "PageUp",
    "PageDown",
    "Home",
    "End",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "F1",
    "F2",
    "F5",
    "F11",
  ];
  const modifierSets = [];
  for (const shiftKey of [false, true]) {
    for (const ctrlKey of [false, true]) {
      for (const metaKey of [false, true]) {
        for (const altKey of [false, true]) {
          modifierSets.push({ shiftKey, ctrlKey, metaKey, altKey });
        }
      }
    }
  }
  let hits = 0;
  for (const key of keys) {
    for (const modifiers of modifierSets) {
      for (const canUndo of [false, true]) {
        const command = resolveBoardKey(press(key, modifiers), { canUndo });
        if (command === null) continue;
        hits += 1;
        assert.ok(isBoardCommand(command), `${key} ${JSON.stringify(modifiers)} → unknown command ${command}`);
      }
    }
  }
  assert.ok(hits > 0, "the sweep must actually hit bindings");
});

test("a malformed event resolves to nothing rather than throwing", () => {
  assert.equal(resolveBoardKey(null, {}), null);
  assert.equal(resolveBoardKey({}, {}), null);
  assert.equal(resolveBoardKey(press("f")), "view.fit");
});

test("isTypingTarget catches the chat composer, not the canvas", () => {
  assert.equal(isTypingTarget({ tagName: "TEXTAREA" }), true);
  assert.equal(isTypingTarget({ tagName: "INPUT" }), true);
  assert.equal(isTypingTarget({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(isTypingTarget({ tagName: "svg" }), false);
  assert.equal(isTypingTarget(null), false);
  // A <select> types too: native typeahead picks an option by its first
  // character, so `3` on the `Turn by` dropdown must not also switch tabs.
  assert.equal(isTypingTarget({ tagName: "SELECT" }), true);
  assert.equal(resolveBoardKey(press("3"), { typing: isTypingTarget({ tagName: "SELECT" }) }), null);
  assert.equal(resolveBoardKey(press("1"), { typing: isTypingTarget({ tagName: "SELECT" }) }), null);
});

test("arrows are left to the browser when there is nothing to nudge", () => {
  // A key that eats a scroll and does nothing is worse than one that scrolls.
  for (const key of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) {
    assert.equal(resolveBoardKey(press(key, { ctrlKey: true }), {}), null, key);
    assert.equal(resolveBoardKey(press(key, { ctrlKey: true }), { canNudge: false }), null, key);
    // And plain arrows are never ours, even with something selected: in Altium
    // they move the cursor, and moving a part instead is a silent geometry
    // change nobody asked for.
    assert.equal(resolveBoardKey(press(key), { canNudge: true }), null, `plain ${key}`);
  }
});

test("a held nudge repeats, because ten steps is ten presses otherwise", () => {
  assert.equal(
    resolveBoardKey(press("ArrowLeft", { ctrlKey: true, repeat: true }), { canNudge: true }),
    "nudge.left",
  );
  // The toggles do not: a held `E` at 25Hz is a flicker, not a gesture.
  assert.equal(resolveBoardKey(press("e", { repeat: true }), {}), null);
});
