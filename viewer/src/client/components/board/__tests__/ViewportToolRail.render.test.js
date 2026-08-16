// The rail is a row of glyphs. Does it say what they are?
//
// Written from a question asked thirty seconds into someone's first use:
// "what does the move icon look like?" Every pill carried a `title`, which the
// OS shows after about a second in its own styling, and a row of unlabelled
// icons is a guessing game until then.
//
// The other test here is the one that came out of the same session: pressing
// `E` did nothing, because the chat composer holds focus from load and the
// keymap refuses — correctly — to take a key out of a text box. A press on the
// board is where the keyboard should point, and this proves it goes there.

import assert from "node:assert/strict";
import test from "node:test";

import { mount, pointer, key } from "../../../test/render.js";
import ViewportToolRail from "../ViewportToolRail.jsx";
import { takeKeyboardFromTyping } from "../canvasPointer.js";
import { VIEWPORT_TOOLS } from "../viewportTools.js";

const hint = (ui) => ui.container.querySelector('[data-slot="viewport-tool-hint"]');
const pill = (ui, id) => ui.container.querySelector(`[data-tool="${id}"]`);

test("hovering a tool says what it is, and which key does it", () => {
  const ui = mount(ViewportToolRail, { surface: "pcb", context: {} });
  try {
    assert.equal(hint(ui), null, "the hint is up before anyone points at anything");

    // `pointerover`, not `pointerenter`: React synthesises enter/leave from the
    // bubbling pair, and a non-bubbling `pointerenter` dispatched at the element
    // never reaches a delegated handler at all.
    pointer(pill(ui, "edit"), "over");
    assert.ok(hint(ui), "hovering the move tool said nothing");
    // The words an EE is looking for, and the warning that comes with them:
    // this is the one tool on the rail that writes their file.
    assert.match(hint(ui).textContent, /Move parts/);
    assert.match(hint(ui).textContent, /edits your board file/);
    assert.match(hint(ui).textContent, /E/);

    pointer(pill(ui, "edit"), "out", { relatedTarget: document.body });
    assert.equal(hint(ui), null, "the hint stayed up after the pointer left");

    // A tool with no key prints no keycap rather than an empty one.
    pointer(pill(ui, "grid"), "over");
    assert.ok(hint(ui));
    assert.equal(hint(ui).querySelector("kbd"), null);

    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("every tool on the rail has words to show", () => {
  // A hint that comes up empty is worse than no hint: it teaches that hovering
  // tells you nothing.
  for (const tool of VIEWPORT_TOOLS) {
    assert.ok(tool.label && tool.label.trim().length > 2, `${tool.id} has no label`);
  }
});

test("a press on the board takes the keyboard back from the chat box", () => {
  // Two true things whose product is a dead keyboard: the composer autofocuses
  // on load, and the keymap never steals a key from a text box. Until the board
  // takes focus, `E` does nothing and nothing on screen says why.
  const box = document.createElement("textarea");
  document.body.appendChild(box);
  try {
    box.focus();
    assert.equal(document.activeElement, box);
    assert.equal(takeKeyboardFromTyping(document.activeElement), true);
    assert.notEqual(document.activeElement, box, "the composer kept the keyboard");

    // And it only ever takes focus off a text field — a dialog or a menu that
    // is deliberately holding focus is left alone.
    const button = document.createElement("button");
    document.body.appendChild(button);
    button.focus();
    assert.equal(takeKeyboardFromTyping(document.activeElement), false);
    assert.equal(document.activeElement, button);
    button.remove();
  } finally {
    box.remove();
  }
});
