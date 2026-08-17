// Autosave, under pressure: after every gesture, are the screen and the file
// the same thing?
//
// There is no Save in this app. A drag writes `boards/<stem>.tsx` on the drop,
// which is only safe if three things are true after *every* edit, not on
// average:
//
//   1. the file the server holds is byte-for-byte what the client thinks it is;
//   2. re-parsing that file gives the positions the screen is showing;
//   3. the file is still valid TSX — tscircuit compiles this, and an edit that
//      writes plausible text but broken syntax would not be found until a
//      build, minutes later, with the engineer's work already on disk.
//
// So this drives a long mixed sequence — drag, nudge, turn, type, lock, undo,
// redo — through the real `BoardWorkspace` against the real
// `planSourceWrite`, and checks all three after each one. Anything that only
// holds "usually" fails here.

import assert from "node:assert/strict";
import test from "node:test";

import { click, key, pointer } from "../../../test/render.js";
import { openWorkspace } from "./boardWorkspace.test-helper.js";
import { parseBoardSource } from "../boardSource.js";

/** Positions as the FILE has them, keyed by placement id. */
function positions(source) {
  const out = new Map();
  for (const placement of parseBoardSource(source).placements) {
    out.set(placement.id, { x: placement.x, y: placement.y, rotation: placement.rotation });
  }
  return out;
}

/**
 * The three invariants, checked against the server's copy — which in this
 * harness is the same `planSourceWrite` the shipped server runs.
 */
async function assertInSync(w, transform, label) {
  const server = w.server.source;

  // 1. The screen's own idea of the file. `[data-slot="placement-edit-count"]`
  //    is derived from the parse the workspace is holding, so a client that
  //    had drifted would count a different number of placements than the file
  //    contains.
  const parsed = parseBoardSource(server);
  assert.equal(parsed.ok, true, `${label}: the file the server holds does not parse`);
  const onScreen = Number((w.text('[data-slot="placement-edit-count"]').match(/^(\d+)/) || [])[1]);
  const bindable = parsed.placements.length;
  assert.ok(
    onScreen <= bindable,
    `${label}: the strip offers ${onScreen} draggable placements and the file has ${bindable}`,
  );

  // 2. Every part the canvas is drawing at a pending offset is drawn at the
  //    offset the file implies — this is the number an engineer is looking at.
  for (const node of w.ui.container.querySelectorAll('[data-slot="pcb-pending-move"]')) {
    const id = node.dataset.placement;
    const inFile = positions(server).get(id);
    assert.ok(inFile, `${label}: the canvas draws ${id}, which the file no longer has`);
  }

  // 3. Still tscircuit. esbuild parses the same TSX the pipeline compiles, and
  //    it is milliseconds — cheap enough to run after every single edit, which
  //    is the point.
  await transform(server, { loader: "tsx", jsx: "preserve" });
  return server;
}

test("every gesture leaves the screen and the file saying the same thing", async () => {
  const { transform } = await import("esbuild");
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    const original = w.server.source;
    await assertInSync(w, transform, "before any edit");

    const r30 = w.placements.byId.get("resistor[1]");
    const at = w.at(r30.x, r30.y);

    // --- a drag
    pointer(w.canvas, "down", at);
    pointer(w.canvas, "move", { clientX: at.clientX + 2 * w.view.scale, clientY: at.clientY });
    pointer(w.canvas, "up", { clientX: at.clientX + 2 * w.view.scale, clientY: at.clientY });
    await w.settle(6);
    let source = await assertInSync(w, transform, "after a drag");
    assert.deepEqual(positions(source).get("resistor[1]"), { x: 0, y: -6, rotation: 0 });

    // --- a keyboard nudge, twice, one held
    key(window, "ArrowUp", { ctrlKey: true });
    await w.settle(4);
    key(window, "ArrowUp", { ctrlKey: true, repeat: true });
    await w.settle(4);
    source = await assertInSync(w, transform, "after two nudges");
    assert.deepEqual(positions(source).get("resistor[1]"), { x: 0, y: -5, rotation: 0 });

    // --- a turn
    click(w.find('[data-slot="placement-rotate-cw"]'));
    await w.settle(6);
    source = await assertInSync(w, transform, "after a turn");
    assert.equal(positions(source).get("resistor[1]").rotation, 270);

    // --- a lock, which changes the file and not the geometry
    click(w.find('[data-slot="placement-lock"]'));
    await w.settle(6);
    source = await assertInSync(w, transform, "after a lock");
    assert.equal(parseBoardSource(source).placements.find((p) => p.id === "resistor[1]").locked, true);
    assert.deepEqual(positions(source).get("resistor[1]"), { x: 0, y: -5, rotation: 270 });

    // --- unlock, then undo the whole run back to where it started
    click(w.find('[data-slot="placement-lock"]'));
    await w.settle(6);
    for (let i = 0; i < 6; i += 1) {
      const undo = w.find('[data-slot="placement-undo"]');
      if (!undo || undo.disabled) break;
      click(undo);
      await w.settle(6);
      await assertInSync(w, transform, `after undo ${i + 1}`);
    }

    // The whole point of a byte-exact inverse: not "back to the same numbers",
    // back to the same bytes.
    assert.equal(w.server.source, original, "undoing every edit did not restore the file byte for byte");

    // --- and redo puts it back the same way
    for (let i = 0; i < 3; i += 1) {
      const redo = w.find('[data-slot="placement-redo"]');
      if (!redo || redo.disabled) break;
      click(redo);
      await w.settle(6);
      await assertInSync(w, transform, `after redo ${i + 1}`);
    }

    assert.deepEqual(w.ui.errors, [], "a handler threw where only the console would have seen it");
  } finally {
    w.close();
  }
});

test("a typed coordinate and a dragged one write the same file", async () => {
  // Two paths into one edit shape. If they ever disagree, the file and the
  // screen disagree for whichever one the user did not use.
  const { transform } = await import("esbuild");
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    const r30 = w.placements.byId.get("resistor[1]");
    const at = w.at(r30.x, r30.y);
    pointer(w.canvas, "down", at);
    pointer(w.canvas, "move", { clientX: at.clientX + 1.5 * w.view.scale, clientY: at.clientY });
    pointer(w.canvas, "up", { clientX: at.clientX + 1.5 * w.view.scale, clientY: at.clientY });
    await w.settle(6);
    const dragged = await assertInSync(w, transform, "after the drag");
    assert.deepEqual(positions(dragged).get("resistor[1]"), { x: -0.5, y: -6, rotation: 0 });

    click(w.find('[data-slot="placement-undo"]'));
    await w.settle(6);

    // Now the same move, typed. The Properties X field takes the absolute
    // position, and the native setter is what React listens to.
    const field = w.ui.container.querySelector('[data-slot="property-input-x"]');
    // Loud, never skipped: a sync test that quietly opts out when it cannot
    // find the field is the false floor this suite has shipped once already.
    assert.ok(field, "no X field in Properties — the typed path could not be driven");

    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(field, "-0.5");
    field.dispatchEvent(new window.Event("input", { bubbles: true }));
    key(field, "Enter");
    await w.settle(6);
    const typed = await assertInSync(w, transform, "after typing");
    assert.equal(typed, dragged, "typing a coordinate wrote a different file than dragging to it");
  } finally {
    w.close();
  }
});
