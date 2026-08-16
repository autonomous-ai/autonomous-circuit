// The other half of every keyboard binding: what the key actually DOES.
//
// `boardKeymap.test.js` proves key → command id over the whole key space, and
// `boardDispatch.test.js` reads the switch and proves each id has a non-empty
// case. Neither one runs the case. Empty the body of `case "units.toggle":`,
// point `layers.show` at the Messages drawer, drop the `event.repeat` guard —
// each of those passes both suites, and each is a bug an EE meets in the first
// minute. `L` toggling Messages is not hypothetical: it shipped, for months.
//
// So this mounts the real workspace over a real example board (the shared
// `openWorkspace` harness — same server, same catalog, same wiring the edit
// loop tests use) and presses keys on `window`, where a browser delivers them.
// What is asserted is the effect: the units chip reading "mil", the drawer's
// own tooltip flipping to "Show", the board file coming back byte-for-byte
// after ⌘Z. Never the command id — that half is already covered.

import assert from "node:assert/strict";
import test from "node:test";

import { click, key, pointer } from "../../../test/render.js";
import { openWorkspace } from "./boardWorkspace.test-helper.js";

/**
 * The state the keyboard tests start from.
 *
 * `openWorkspace` lands on the PCB tab with move mode on, which is where every
 * binding below is reachable. The readers are named for what an EE reads, not
 * for the state behind it — a test that asserts on `messagesOpen` proves the
 * variable moved; one that asserts on the tooltip proves the drawer did.
 */
async function openBoard() {
  const workspace = await openWorkspace();
  const { find, ui } = workspace;
  return Object.assign(workspace, {
    /** The tab the workspace itself says is current. */
    tab: () => find('[data-slot="board-tab"][aria-current="true"]')?.dataset.tab,
    /** "mm" or "mil", off the chip on the layer bar. */
    units: () => find('[data-slot="units-toggle"]')?.textContent,
    /** "Hide" while the drawer is open, "Show" while it is shut. */
    drawer: () => find('[data-slot="messages-toggle"]')?.getAttribute("title")?.split(" ")[0],
    /** How many findings the drawer is actually showing. */
    findings: () => ui.container.querySelectorAll('[data-slot="message-group"]').length,
    /** The Δ readout in the Board Insight HUD, verbatim. */
    delta: () => find('[data-slot="hud-delta-reset"]')?.textContent,
    selectionChip: () => find('[data-slot="selection-chip"]')?.textContent,
    /** Switch tabs the way a user does — by clicking the tab. */
    async openTab(label) {
      const tab = [...ui.container.querySelectorAll('[data-slot="board-tab"]')]
        .find((node) => node.textContent.trim() === label);
      assert.ok(tab, `no ${label} tab`);
      click(tab);
      await workspace.settle();
    },
  });
}

/**
 * Select a real component by clicking down to it in the navigator.
 *
 * The navigator rather than the canvas because what is being set up here is
 * `selection`, and a tree row sets exactly that with no geometry in the way —
 * the canvas hit-testing already has its own file.
 */
async function selectComponent(workspace) {
  const rowEndingIn = (suffix) =>
    [...workspace.ui.container.querySelectorAll('[data-slot="board-tree-row"]')]
      .find((row) => row.dataset.nodeId.endsWith(suffix));
  for (const suffix of ["/components", "/components/R"]) {
    const row = rowEndingIn(suffix);
    assert.ok(row, `no ${suffix} row in the navigator`);
    click(row);
    await workspace.settle(1);
  }
  const component = [...workspace.ui.container.querySelectorAll('[data-slot="board-tree-row"]')]
    .find((row) => row.dataset.kind === "component");
  assert.ok(component, "the navigator listed no components");
  click(component);
  await workspace.settle();
  return component;
}

/**
 * Put one undoable edit in the history.
 *
 * A lock is the cheapest real write there is — one comment above the
 * placement, no geometry — so what comes after can compare the whole file
 * byte-for-byte with nothing to round.
 */
async function lockOnePlacement(workspace) {
  await selectComponent(workspace);
  const lock = workspace.find('[data-slot="placement-lock"]');
  assert.ok(lock && !lock.disabled, "the lock button is not there to press");
  click(lock);
  await workspace.settle();
  assert.equal(workspace.server.writes.length, 1, "the lock wrote nothing");
  assert.notEqual(workspace.server.source, workspace.source, "the write changed no bytes");
  return workspace.server.source;
}

test("⌘Z undoes the last edit to the board file", async () => {
  const board = await openBoard();
  await lockOnePlacement(board);

  key(window, "z", { metaKey: true });
  await board.settle();

  assert.equal(board.server.writes.length, 2, "⌘Z wrote nothing — the key never reached undo");
  assert.equal(board.server.source, board.source, "the file did not come back to what it was");
  assert.deepEqual(board.ui.errors, [], "a handler threw where only the console would have seen it");
  board.close();
});

test("Ctrl+Z undoes it too — the same edit, the other platform's key", async () => {
  const board = await openBoard();
  await lockOnePlacement(board);

  key(window, "z", { ctrlKey: true });
  await board.settle();

  assert.equal(board.server.source, board.source, "Ctrl+Z did not undo");
  board.close();
});

test("⇧⌘Z and Ctrl+Y both redo", async () => {
  const board = await openBoard();
  const locked = await lockOnePlacement(board);

  key(window, "z", { metaKey: true });
  await board.settle();
  assert.equal(board.server.source, board.source, "the undo this redo needs did not happen");

  key(window, "z", { metaKey: true, shiftKey: true });
  await board.settle();
  assert.equal(board.server.source, locked, "⇧⌘Z did not redo");

  // …and again on the spelling Windows uses. One history, so the file has to
  // make the same round trip both times.
  key(window, "z", { metaKey: true });
  await board.settle();
  assert.equal(board.server.source, board.source, "the second undo did not happen");
  key(window, "y", { ctrlKey: true });
  await board.settle();
  assert.equal(board.server.source, locked, "Ctrl+Y did not redo");

  assert.deepEqual(board.ui.errors, []);
  board.close();
});

test("a HELD ⌘Z undoes once, not once per repeat", async () => {
  const board = await openBoard();
  await lockOnePlacement(board);

  // What a keyboard sends for a key held down: one press, then the OS's
  // auto-repeat at ~25 Hz. Dispatched as one synchronous burst on purpose —
  // that is how they arrive, and it is the only way to test the `repeat` guard
  // on its own, because nothing re-renders in between for `editor.busy` to
  // help.
  key(window, "z", { metaKey: true });
  for (let i = 0; i < 24; i += 1) key(window, "z", { metaKey: true, repeat: true });
  await board.settle(6);

  assert.equal(
    board.server.writes.length,
    2,
    "a held ⌘Z unwound more than one edit — with a 50-entry history that is the whole session, and there is no redo left to get it back",
  );
  assert.equal(board.server.source, board.source);
  board.close();
});

test("L shows the layers and leaves the Messages drawer alone", async () => {
  const board = await openBoard();
  assert.equal(board.drawer(), "Hide", "the drawer must start open, so a stray toggle would show");

  // Overview has no layer bar and no drawer on screen, which is the honest
  // starting point for "L shows the layers": there is something for the key to
  // do. The drawer's state survives the tab — it is read again after.
  await board.openTab("Overview");
  assert.equal(board.tab(), "overview");
  assert.equal(board.find('[data-slot="layer-bar"]'), null, "the layer bar is already up");

  key(window, "l");
  await board.settle();

  assert.equal(board.tab(), "pcb", "L did not put a board on screen to show the layers of");
  assert.ok(board.find('[data-slot="layer-bar"]'), "L did not bring up the layer bar");
  // The second half of Altium's L: the keyboard lands ON the layers, so
  // someone who is not reaching for the mouse can pick one. It runs a frame
  // late, after the tab has painted.
  assert.ok(
    document.activeElement?.closest?.('[data-slot="layer-bar"]'),
    `L left focus on <${document.activeElement?.tagName?.toLowerCase()}> outside the layer bar`,
  );
  // The bug the binding was moved to fix: for months L toggled this drawer,
  // which is neither of the two things Altium's L means.
  assert.equal(board.drawer(), "Hide", "L moved the Messages drawer");
  board.close();
});

test("⇧N toggles the Messages drawer", async () => {
  const board = await openBoard();
  assert.equal(board.drawer(), "Hide");
  assert.ok(board.findings() > 0, "this board has no findings, so hiding them proves nothing");

  key(window, "N", { shiftKey: true });
  await board.settle();
  assert.equal(board.drawer(), "Show", "⇧N did not close the drawer");
  assert.equal(board.findings(), 0, "the drawer says it is shut and its findings are still on screen");

  key(window, "N", { shiftKey: true });
  await board.settle();
  assert.equal(board.drawer(), "Hide", "⇧N did not reopen the drawer");
  assert.ok(board.findings() > 0, "the drawer reopened empty");
  board.close();
});

test("Q toggles the units the whole board is read in", async () => {
  const board = await openBoard();
  assert.equal(board.units(), "mm");
  // The HUD prints "—" until the pointer is over the board, and a coordinate
  // readout is the thing units are actually FOR — so put the cursor on the
  // board before asking what units it is reading in.
  const hud = () => {
    pointer(board.canvas, "move", board.at(6, -4));
    return board.text('[data-slot="board-insight-hud"]');
  };
  assert.match(hud(), /mm/);

  key(window, "q");
  await board.settle();
  assert.equal(board.units(), "mil", "Q did not switch units");
  // The chip is not the only thing that has to move. A chip that changes on
  // its own is a label, not a setting.
  assert.match(hud(), /mil/, "the chip says mil and the coordinate readout does not");

  key(window, "q");
  await board.settle();
  assert.equal(board.units(), "mm", "Q is one-way");
  assert.doesNotMatch(hud(), /mil/);
  board.close();
});

test("Escape clears the selection", async () => {
  const board = await openBoard();
  await selectComponent(board);
  assert.match(board.selectionChip() || "", /R1/, "clicking a component in the navigator did not select it");

  key(window, "Escape");
  await board.settle();

  assert.equal(board.selectionChip(), undefined, "Escape left the selection on screen");
  board.close();
});

test("a key typed into the chat composer never reaches the board", async () => {
  const board = await openBoard();

  // Park the cursor on the board so the HUD has a Δ to print and the canvas
  // has a cursor for `Insert` to zero the origin at.
  const canvas = board.canvas;
  const at = board.at(6, -4);
  pointer(canvas, "move", at);
  await board.settle(1);
  const before = board.delta();
  assert.match(before, /^Δ -?\d/, `the HUD is not reporting a delta: ${before}`);
  assert.notEqual(before, "Δ 0.000, 0.000 mm", "the probe point must not already be the origin");

  // The chat composer is a sibling of the workspace under AppRoot, not a child
  // of it — which is exactly why this bug was possible. Both key handlers are
  // on `window`, so the only thing between a keystroke and the board is the
  // typing-target guard, and the old one tested `tagName !== "INPUT"`. A
  // `<textarea>` is not an INPUT, so every space typed into the composer moved
  // the delta origin and every Δ in the HUD changed with nothing to say why.
  const composer = document.createElement("textarea");
  document.body.appendChild(composer);
  key(composer, " ");
  key(composer, "Insert");
  await board.settle(1);
  pointer(canvas, "move", at);
  await board.settle(1);
  assert.equal(board.delta(), before, "typing into the composer moved the delta origin");

  // The same key from the board does move it — without this the assertion
  // above would also pass on a binding that had simply been deleted.
  key(window, "Insert");
  await board.settle(1);
  pointer(canvas, "move", at);
  await board.settle(1);
  assert.equal(board.delta(), "Δ 0.000, 0.000 mm", "Insert did not zero the delta at the cursor");

  composer.remove();
  board.close();
});

test("a key typed into a <select> does not switch tabs", async () => {
  const board = await openBoard();
  await selectComponent(board);
  const turnBy = board.find('[data-slot="placement-rotate-step"]');
  assert.equal(turnBy?.tagName, "SELECT", "no Turn by dropdown to type into");
  assert.equal(board.tab(), "pcb");

  // A `<select>` is a typing target even though nothing is typed into it:
  // native keyboard typeahead picks an option by its first character, so `3`
  // on `Turn by` means 30° to the browser. The 3D tab opening out from under a
  // rotation being set is the misfire this guard exists to stop.
  for (const stroke of ["3", "1", "0", "2"]) {
    key(turnBy, stroke);
    await board.settle(1);
    assert.equal(board.tab(), "pcb", `"${stroke}" in the Turn by dropdown switched tabs`);
  }
  board.close();
});

test("the key list has a way in that is not a key", async () => {
  // Discoverability, as a test. The sheet is derived from the resolvers and is
  // the app's answer to "an EE carries a hundred bindings without a learning
  // curve because the tool tells them" — and it was reachable only by guessing
  // `?` or Shift+F1, since the Help menu that also opens it renders on Windows
  // only. A list nobody can find is a list nobody has.
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    const button = w.find('[data-slot="board-shortcuts"]');
    assert.ok(button, "no visible way into the shortcut list anywhere on the board");
    assert.match(button.textContent, /\?/, "the button does not print the key it stands for");

    assert.equal(document.body.querySelector('[data-slot="shortcut-sheet"]'), null);
    click(button);
    await w.settle();
    const sheet = document.body.querySelector('[data-slot="shortcut-sheet"]');
    assert.ok(sheet, "the button did not open the sheet");
    assert.match(sheet.textContent, /Keyboard shortcuts/u);

    // One dialog, not two: the workspace mounts the host once and the button
    // asks it by event, so a second host cannot answer `?` alongside it.
    assert.equal(document.body.querySelectorAll('[data-slot="shortcut-sheet"]').length, 1);
  } finally {
    w.close();
  }
});
