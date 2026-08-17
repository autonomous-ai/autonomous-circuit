// The app menu's Board items, and the join they have to make.
//
// This file exists because of the defect this repo keeps repeating: a menu
// that lists commands and reaches nothing is worse than no menu, because it
// looks like the feature is there. Three panel rounds reported "no app menu
// off Windows" and, where the bar did render, "no board commands in it — the
// Undo there is text-field undo, not board undo", which is not a gap but a
// trap.
//
// So the load-bearing assertions are: every command the menu offers is one the
// keyboard resolver actually knows, and every key the menu prints is one the
// app answers to. Neither can be satisfied by copy.

import assert from "node:assert/strict";
import test from "node:test";

import { installDom } from "../../../test/dom.js";
import { BOARD_MENU_ITEMS, BOARD_COMMAND_EVENT, runBoardMenuCommand } from "../boardMenuCommands.js";
import { BOARD_COMMANDS } from "../boardKeymap.js";
import { allBindings, comboFor } from "../shortcutSheet.js";

test("every command the menu offers is one the app can run", () => {
  const known = new Set(BOARD_COMMANDS);
  const unknown = BOARD_MENU_ITEMS.filter(Boolean)
    .map((item) => item.command)
    .filter((command) => !known.has(command));
  assert.deepEqual(unknown, [], "the menu offers commands BoardWorkspace has no case for");
});

test("every menu item has a label, and no two rows are the same command", () => {
  const items = BOARD_MENU_ITEMS.filter(Boolean);
  for (const item of items) {
    assert.ok(item.label && item.label.trim(), `${item.command} has no label`);
    assert.ok(!/[._]/.test(item.label), `${item.command}'s label reads like an id: ${item.label}`);
  }
  const ids = items.map((one) => one.command);
  assert.equal(new Set(ids).size, ids.length, "a command appears twice in the menu");
});

test("the key printed beside a menu row is a key the app answers to", () => {
  // `comboFor` reads the same probe the shortcut sheet does — it presses keys
  // at the real resolver — so a printed combo cannot be one nobody bound.
  const live = new Map();
  for (const binding of allBindings()) if (!live.has(binding.id)) live.set(binding.id, binding.combo);
  for (const item of BOARD_MENU_ITEMS.filter(Boolean)) {
    const shown = comboFor(item.command);
    if (!shown) continue; // a command with no key is allowed; a wrong key is not
    assert.equal(shown, live.get(item.command), `${item.command} prints a key it does not have`);
  }
});

test("choosing a menu row dispatches the command the workspace listens for", () => {
  installDom();
  const seen = [];
  const handler = (event) => seen.push(event.detail?.command);
  window.addEventListener(BOARD_COMMAND_EVENT, handler);
  try {
    runBoardMenuCommand("view.fit");
    runBoardMenuCommand("");
    assert.deepEqual(seen, ["view.fit"], "an empty command must not be dispatched");
  } finally {
    window.removeEventListener(BOARD_COMMAND_EVENT, handler);
  }
});

// Undo is the one an engineer will reach for first and the one that was a
// trap: the Edit menu's Undo is the webview's text undo.
test("the board's own undo and redo are on the menu", () => {
  const ids = BOARD_MENU_ITEMS.filter(Boolean).map((one) => one.command);
  assert.ok(ids.includes("edit.undo"), "board undo is not reachable from the menu");
  assert.ok(ids.includes("edit.redo"), "board redo is not reachable from the menu");
  const undo = BOARD_MENU_ITEMS.find((one) => one?.command === "edit.undo");
  assert.match(undo.label, /board/i, "the menu does not distinguish board undo from text undo");
});
