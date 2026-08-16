// The shortcut sheet, rendered.
//
// `shortcutSheet.test.js` proves the derivation: press the whole key space at
// the real resolvers, hold the result against the copy in both directions.
// What it cannot prove is that any of it is on screen. The sheet is a Radix
// dialog behind a window `keydown` listener, and both halves of that have
// failed in this repo before — a feature built, unit-tested, and never wired
// to the surface a user touches.
//
// The load-bearing assertion here is the second test: every binding the app
// answers has a row in the painted DOM, carrying the key that produces it. Add
// a binding to boardKeymap.js and forget the line of copy, and this goes red.

import assert from "node:assert/strict";
import test from "node:test";

import { key, mount, settle } from "../../../test/render.js";
import { ShortcutSheetHost } from "../ShortcutSheet.jsx";
import { comboParts } from "../shortcutScan.js";
import { SHORTCUT_COPY, allBindings } from "../shortcutSheet.js";

// `Mod` prints as ⌘ on a Mac and Ctrl everywhere else, off `navigator`.
// happy-dom reports "X11; Darwin x64", which is not a Mac by that rule, so
// every Mod row must print Ctrl here. Asserted rather than assumed: if a DOM
// upgrade changes the UA, this one line says so instead of thirty binding
// comparisons failing with an unreadable diff.
const MOD = "Ctrl";
assert.doesNotMatch(String(navigator.platform || navigator.userAgent), /Mac|iPhone|iPad/u);

/** The sheet is a portalled dialog — it is never inside the mount container. */
const sheetEl = () => document.body.querySelector('[data-slot="shortcut-sheet"]');

/**
 * Every row the sheet paints, read the way a person reads it: the caps, and
 * the sentence beside them.
 *
 * The combos come back out of the `<kbd>` elements rather than out of a data
 * attribute, because the caps are the thing a user copies onto their keyboard.
 * A row whose model is right and whose caps render blank is exactly the class
 * of defect this file exists for.
 */
function printedRows() {
  return [...sheetEl().querySelectorAll("section dl > div")].map((row) => ({
    combos: [...row.querySelectorAll("dt > span")].map((combo) =>
      [...combo.querySelectorAll("kbd")].map((cap) => cap.textContent).join("+"),
    ),
    text: row.querySelector("dd").textContent,
  }));
}

/** `Mod+PgDn` as the sheet must print it on this platform. */
const printedCombo = (combo) => comboParts(combo).map((part) => (part === "Mod" ? MOD : part)).join("+");

/**
 * Press Escape the way a person does — at whatever has focus.
 *
 * Not on `window`, and this is not a detail. Radix closes on Escape from a
 * **capture** listener on `document`
 * (`@radix-ui/react-use-escape-keydown`: `ownerDocument.addEventListener
 * ("keydown", …, { capture: true })`). A real Escape is targeted at the focused
 * element — the sheet's search box — so its propagation path runs
 * window → document → … and the capture listener sees it on the way down. An
 * event *dispatched on window* has a path of exactly `[window]`: document is
 * nowhere in it, so that listener never fires and the sheet never closes.
 * Dispatching there tests the harness, not the app.
 *
 * `?` stays on `window` on purpose — `ShortcutSheetHost` really does bind it
 * there, which is what makes it work with the pointer out on the canvas.
 */
function escape() {
  key(document.activeElement ?? sheetEl() ?? window, "Escape");
}

async function openSheet(props = {}) {
  const ui = mount(ShortcutSheetHost, { button: false, ...props });
  assert.equal(sheetEl(), null, "the sheet was up before anyone asked for it");
  key(window, "?");
  await settle();
  return ui;
}

test("the sheet is opened by its own key, on the window, from anywhere on the board", async () => {
  const ui = mount(ShortcutSheetHost, { button: false });
  assert.equal(sheetEl(), null);

  // On `window`, not on the sheet's own button: the point of the key is that
  // it works while the pointer is out on the canvas. A listener that never got
  // attached — the wiring failure this repo keeps shipping — leaves this null.
  key(window, "?");
  await settle();
  assert.ok(sheetEl(), "? did not open the sheet");
  assert.match(sheetEl().textContent, /Keyboard shortcuts/u);

  // Altium's own key for the same list, so an EE's hands already know it.
  key(window, "?");
  await settle();
  assert.equal(sheetEl(), null, "? did not close it again");
  key(window, "F1", { shiftKey: true });
  await settle();
  assert.ok(sheetEl(), "Shift+F1 did not open the sheet");

  // A plain `?` typed into a box is a question mark, not a command.
  escape();
  await settle();
  const box = document.createElement("input");
  document.body.appendChild(box);
  key(box, "?");
  await settle();
  assert.equal(sheetEl(), null, "? opened the sheet from inside a text box");
  box.remove();

  assert.deepEqual(ui.errors, []);
  ui.unmount();
});

test("every key the app answers is printed in the sheet, with the key that produces it", async () => {
  const ui = await openSheet();
  const rows = printedRows();
  assert.ok(rows.length > 20, `the sheet painted ${rows.length} rows`);

  for (const binding of allBindings()) {
    // A binding with no line of copy is dropped by `buildShortcutSheet` rather
    // than painted blank, so on screen it is simply a key nobody is told
    // about. That is the whole failure: the key still works.
    const copy = SHORTCUT_COPY[binding.id];
    assert.ok(copy, `"${binding.id}" answers ${binding.combo} and the sheet has no line of copy for it`);

    // `— <when>` is appended to the label for bindings that only work in one
    // situation, so the row is matched on its label and not on the full text.
    const row = rows.find((one) => one.text === copy.label || one.text.startsWith(`${copy.label} — `));
    assert.ok(row, `"${copy.label}" (${binding.id}) is in the copy and never reached the screen`);
    assert.ok(
      row.combos.includes(printedCombo(binding.combo)),
      `${binding.id} answers ${binding.combo}; the sheet prints ${JSON.stringify(row.combos)}`,
    );
  }

  // And the other direction: a cap on screen for a key nothing answers teaches
  // a shortcut that does nothing, which is worse than not listing it.
  const real = new Set(allBindings().map((binding) => printedCombo(binding.combo)));
  for (const row of rows) {
    for (const combo of row.combos) {
      assert.ok(real.has(combo), `the sheet prints ${combo} for "${row.text}" and no resolver answers it`);
    }
  }

  // The sheet counts itself out loud, and a count that disagrees with the rows
  // under it is the first sign the list has started lying.
  assert.match(sheetEl().textContent, new RegExp(`${rows.length} keys`, "u"));

  assert.deepEqual(ui.errors, []);
  ui.unmount();
});

test("the sheet lists the key that opens it and the key that shuts it", async () => {
  // A list you cannot find your way out of is the joke version of this
  // feature, and `sheet.close` is resolved by a branch that deliberately does
  // nothing at runtime — Radix already closes on Escape — so it can only be
  // checked here.
  const ui = await openSheet();
  const rows = printedRows();
  const help = new Map(rows.map((row) => [row.text.replace(/ — .*$/u, ""), row.combos]));

  assert.deepEqual(help.get("Open this list"), ["?", "Shift+F1"]);
  assert.deepEqual(help.get("Close this list"), ["Esc"]);

  escape();
  await settle();
  assert.equal(sheetEl(), null, "Escape did not close the sheet the sheet says Escape closes");
  ui.unmount();
});
