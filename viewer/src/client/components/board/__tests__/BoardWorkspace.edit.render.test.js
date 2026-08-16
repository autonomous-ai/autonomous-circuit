// The edit loop, end to end: a pointer on the canvas, a request on the wire, a
// file on the other side, and what the screen says about all three.
//
// Everything below drives the whole `BoardWorkspace` — the PCB tab and move
// mode are *clicked*, the part is dragged at a client pixel derived from the
// transform the canvas actually painted, and the answer is read out of the DOM
// and out of the request body. Nothing here composes the pieces itself. That
// is the point: `usePlacementEditor` is well covered, `placementDrag` is well
// covered, `planSourceWrite` is well covered, and this repo has twice shipped a
// broken app made of covered pieces (8fb33cd `snapDelta`, e7f1434 the right
// button) because no test ever ran the *join*.
//
// The server is the real `planSourceWrite`, not a yes-man — see
// boardWorkspace.test-helper.js. So a client edit the shipped server would
// refuse fails here too, and the SOURCE_CHANGED case below is produced by
// actually changing the file under the client rather than by hand-writing a
// 409.

import assert from "node:assert/strict";
import test from "node:test";

import { click, pointer } from "../../../test/render.js";
import { openWorkspace } from "./boardWorkspace.test-helper.js";
import { parseBoardSource } from "../boardSource.js";
import { REASONS } from "../placementFields.js";

/** Every placement's position, keyed by id — for "nothing else moved". */
function positions(source) {
  const map = new Map();
  for (const placement of parseBoardSource(source).placements) {
    map.set(placement.id, `${placement.x},${placement.y}`);
  }
  return map;
}

/**
 * Grab R30 and carry it 2 mm east.
 *
 * R30 is a real 0402 at (-2, -6) on the real hydrate-coaster board, and 2 mm is
 * an exact multiple of the 0.5 mm default snap step, so the expected result is
 * a constant the test writes out rather than `snapDelta` re-run against itself.
 */
function dragR30East(w) {
  const r30 = w.placements.byId.get("resistor[1]");
  assert.equal(r30.name, "R30");
  assert.deepEqual({ x: r30.x, y: r30.y }, { x: -2, y: -6 }, "the board moved under this test");

  const start = w.at(r30.x, r30.y);
  const dxPx = 2 * w.view.scale;
  pointer(w.canvas, "down", start);
  pointer(w.canvas, "move", { clientX: start.clientX + dxPx, clientY: start.clientY });
  // The live readout only exists if the drag reached state — this is the line
  // that goes quiet when `snapDelta` is missing from PcbCanvas again.
  assert.match(w.text('[data-slot="pcb-move-readout"]'), /R30\s*Δ 2, 0 mm/);
  pointer(w.canvas, "up", { clientX: start.clientX + dxPx, clientY: start.clientY });
  return r30;
}

test("a drag writes the board file's own byte range, in the board file it names", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    const before = positions(w.source);
    dragR30East(w);
    await w.settle();

    const posts = w.server.requests.filter((one) => one.method === "POST");
    // One write, and one question about it. The write is the invariant this
    // line has always guarded — not none, and never two, because a second
    // write of the same drag would double the move. The verdict request beside
    // it is the other half of the gesture (BoardWorkspace.verdict.render.test.js
    // owns what it carries); asserting both here keeps a *third* request from
    // arriving unnoticed.
    assert.deepEqual(
      posts.map((one) => one.command),
      ["board_source_write", "board_fast_check"],
    );

    const body = w.server.writes[0];
    assert.equal(body.id, "p1");
    // The file, not "a" file: the workspace has one board open and the write
    // has to name that board's source, not the sidecar and not the IR.
    assert.equal(body.file, "boards/main.tsx");
    assert.equal(body.sourceLength, w.source.length, "the compare-and-swap token is the file that was read");

    assert.equal(body.edits.length, 1, "moving one part in x is one literal");
    const [edit] = body.edits;
    // The offsets are R30's own `pcbX` literal, checked against the bytes on
    // disk rather than written down — an edit that lands two lines away would
    // otherwise pass on a coincidence of `-2` appearing somewhere.
    assert.equal(w.source.slice(edit.start - 6, edit.end + 1), "pcbX={-2}");
    assert.equal(edit.expected, "-2");
    assert.equal(edit.text, "0");

    // And what the server ended up holding. This is the assertion that would
    // survive the whole request shape being redesigned: R30 is at 0, -6 in the
    // file now, and every other placement is exactly where it was.
    const after = positions(w.server.source);
    assert.equal(after.get("resistor[1]"), "0,-6");
    for (const [id, at] of before) {
      if (id === "resistor[1]") continue;
      assert.equal(after.get(id), at, `${id} moved and nobody asked it to`);
    }

    assert.deepEqual(w.ui.errors, [], "a handler threw where only the console would have seen it");
  } finally {
    w.close();
  }
});

test("the moved part is redrawn where the file now says, and the screen says the copper has not followed", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    dragR30East(w);
    await w.settle();

    // The part is repainted at its new home by an offset group — the copper
    // around it stays where the last router run put it, which is the honest
    // state until a rebuild.
    const pending = [...w.ui.container.querySelectorAll('[data-slot="pcb-pending-move"]')];
    assert.equal(pending.length, 1, "nothing on the canvas is drawn away from its built position");
    assert.equal(pending[0].dataset.placement, "resistor[1]");
    assert.equal(pending[0].getAttribute("transform"), "translate(2 0)");

    // Properties is the typed half of the same edit and must agree with the
    // canvas: the file says one thing, the built board still says the other.
    assert.equal(
      w.text('[data-slot="property-placement-pending"]'),
      "the file says 0, -6 — the board below is still the last build at -2, -6",
    );

    // And the strip: what changed, and that a rebuild is owed for it.
    assert.equal(
      w.text('[data-slot="placement-edit-note"]'),
      "R30 moved 2, 0 mm." +
        " The board below is still the last build — the copper has not moved with the part," +
        " and a turn does not show on screen until you rebuild.",
    );
    assert.equal(w.text('[data-slot="placement-rebuild"]'), "Rebuild (1 change)");
    assert.equal(w.text('[data-slot="placement-edit-error"]'), "", "a clean write reported an error");

    assert.deepEqual(w.ui.errors, []);
  } finally {
    w.close();
  }
});

test("undo sends the inverse edit, and the board file comes back byte for byte", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    dragR30East(w);
    await w.settle();

    const undo = w.find('[data-slot="placement-undo"]');
    assert.equal(undo.disabled, false, "a drag left nothing to undo");
    click(undo);
    await w.settle();

    assert.equal(w.server.writes.length, 2, "undo is a write of its own");
    const [forward, back] = w.server.writes;
    // Not merely "an edit that restores -2": the inverse. It expects to find
    // exactly what the forward edit wrote, at the offset that edit left behind
    // — one character shorter, because `0` is one character shorter than `-2`.
    assert.equal(back.file, forward.file);
    assert.equal(back.sourceLength, w.source.length - 1);
    assert.deepEqual(back.edits, [{ start: forward.edits[0].start, end: forward.edits[0].start + 1, text: "-2", expected: "0" }]);

    // The only test of an undo that cannot be argued with.
    assert.equal(w.server.source, w.source, "the board file did not come back to what it was");

    assert.equal(w.text('[data-slot="placement-edit-note"]'), "undid: R30.");
    // Nothing is owed to a rebuild any more: the file on disk is the file the
    // board on screen was built from.
    assert.equal(w.text('[data-slot="placement-rebuild"]'), "Rebuild");
    assert.equal(w.find('[data-slot="placement-undo"]').disabled, true, "one drag, one undo");
    assert.equal(w.find('[data-slot="placement-redo"]').disabled, false, "undo without redo is a one-way door");
    assert.deepEqual(w.ui.errors, []);
  } finally {
    w.close();
  }
});

test("a write the server refuses reaches the screen, not the console", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    // The agent edits the same file — the whole reason `board_source_write` is
    // a compare-and-swap. The offsets the canvas is holding now describe a file
    // that no longer exists, and the real `planSourceWrite` refuses them.
    const agentText = `${w.source}\n// the agent was here\n`;
    w.server.agentWrites(agentText);

    dragR30East(w);
    await w.settle();

    assert.equal(w.server.writes.length, 1, "the drag never reached the server");
    const refused = "the board file changed since it was read — reopen the board and try again";
    // Both places a user could be looking: the strip above the canvas and the
    // panel where the same number can be typed instead of dragged.
    assert.equal(w.text('[data-slot="placement-edit-error"]'), refused);
    assert.equal(w.text('[data-slot="property-placement-error"]'), refused);

    assert.equal(w.server.source, agentText, "a refused write changed the file anyway");
    // A failed write is not history and is not a change owed to a rebuild.
    assert.equal(w.find('[data-slot="placement-undo"]').disabled, true, "a refused write left an undo entry");
    assert.equal(w.text('[data-slot="placement-rebuild"]'), "Rebuild");
    assert.equal(w.text('[data-slot="placement-edit-note"]'), "", "a refused write reported as a change");

    // The failure is a rejection, not a crash: `errors` holds anything a React
    // handler threw, and a swallowed throw here is how the last one hid.
    assert.deepEqual(w.ui.errors, []);
  } finally {
    w.close();
  }
});

test("a part the board file places in code cannot be dragged, and the panel says which part and why", async () => {
  // terminal-keyboard, because 100 of its 137 parts have no literal in the
  // board file — 50 emitted by `keyCells()` directly and 50 as block instances.
  // On hydrate-coaster every part is placed by hand and this case cannot arise.
  const w = await openWorkspace({ example: "terminal-keyboard" });
  try {
    const d1 = w.index.components.find((one) => one.refdes === "D1");
    assert.ok(d1, "D1 is not on this board");
    const centre = {
      x: (d1.pcbBox.minX + d1.pcbBox.maxX) / 2,
      y: (d1.pcbBox.minY + d1.pcbBox.maxY) / 2,
    };
    assert.equal(w.placements.byComponentKey.get(d1.key), undefined, "D1 is placed by a literal after all");

    // A click still selects it — refusing to move a part is not refusing to
    // talk about it.
    const spot = w.at(centre.x, centre.y);
    pointer(w.canvas, "down", spot);
    pointer(w.canvas, "up", spot);
    await w.settle();
    assert.match(w.text('[data-slot="properties-panel"]'), /D1/);

    // The reason, in the DOM, naming the part. Not "unavailable", and not the
    // generic sentence for a placement with nothing behind it: this one tells
    // an EE the thing they have to change instead.
    const why = "D1 has no pcbX/pcbY in the board file — it is placed in code";
    const reasons = [...w.ui.container.querySelectorAll('[data-slot="property-locked-reason"]')].map(
      (node) => node.textContent,
    );
    assert.deepEqual(reasons, [why, why, why], "X, Y and Rotation should each carry the reason");
    assert.notEqual(why, REASONS.unbound);
    // And the coordinate is genuinely not typeable — a reason printed beside a
    // live input would be a different lie.
    assert.equal(w.find('[data-slot="property-input-x"]'), null);
    assert.equal(w.find('[data-slot="property-input-y"]'), null);

    // Now drag it. The gesture is not swallowed: with nothing to grab the
    // canvas pans, which is what the pointer arbiter says a left press over
    // un-grabbable board means.
    const before = w.view;
    const from = w.at(centre.x, centre.y);
    const travelPx = 3 * before.scale;
    pointer(w.canvas, "down", from);
    pointer(w.canvas, "move", { clientX: from.clientX + travelPx, clientY: from.clientY });
    assert.equal(w.text('[data-slot="pcb-move-readout"]'), "", "a part with no literal started a move");
    pointer(w.canvas, "up", { clientX: from.clientX + travelPx, clientY: from.clientY });
    await w.settle();

    assert.equal(w.server.writes.length, 0, "the board file was written for a part it does not place");
    const after = w.view;
    assert.equal(after.scale, before.scale);
    // Within a thousandth of a pixel, because the client coordinate this test
    // pressed at was itself computed through the transform — `(tx + x·s) - tx`
    // does not come back as `x·s` in binary floating point, and the claim here
    // is "it panned by the travel", not "it panned by that exact bit pattern".
    assert.ok(
      Math.abs(after.tx - before.tx - travelPx) < 1e-3,
      `the drag did nothing at all — not even pan (moved ${after.tx - before.tx} of ${travelPx})`,
    );
    assert.deepEqual(w.ui.errors, []);
  } finally {
    w.close();
  }
});
