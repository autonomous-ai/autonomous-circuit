// After the edit: does the app ask whether the board is still legal, and does
// it say what came back?
//
// This file exists because of a specific failure, and the failure is the one
// this repo keeps repeating in a new place. `board_fast_check` and
// `board_edit_apply` were built on the server, tested, documented as landed —
// and `grep -rn "board_fast_check" viewer/src/client/` returned nothing. Every
// edit an engineer made got the word "saved" and no verdict at all, while a
// working gate sat one HTTP call away.
//
// So the load-bearing assertion here is not about copy or colour. It is:
// **a drag causes a request for a verdict.** Delete the call in
// `usePlacementEditor.write` and this file goes red on the first test.
//
// The whole workspace is driven — tab clicked, move mode clicked, the part
// dragged at a real client pixel — for the reason given in
// BoardWorkspace.edit.render.test.js: every bug worth catching here lives in a
// join, and a test that composes the pieces itself cannot see a join.

import assert from "node:assert/strict";
import test from "node:test";

import { click, pointer } from "../../../test/render.js";
import { openWorkspace } from "./boardWorkspace.test-helper.js";

/** Grab R30 (a real 0402 at (-2,-6) on hydrate-coaster) and carry it 2mm east. */
function dragR30East(w) {
  const r30 = w.placements.byId.get("resistor[1]");
  assert.equal(r30.name, "R30");
  const start = w.at(r30.x, r30.y);
  const dxPx = 2 * w.view.scale;
  pointer(w.canvas, "down", start);
  pointer(w.canvas, "move", { clientX: start.clientX + dxPx, clientY: start.clientY });
  pointer(w.canvas, "up", { clientX: start.clientX + dxPx, clientY: start.clientY });
  return r30;
}

test("a drag asks the gate for a verdict, and carries the move it made", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    assert.deepEqual(w.server.checks, [], "something asked for a verdict before anyone edited anything");

    const r30 = dragR30East(w);
    await w.settle();

    assert.equal(w.server.checks.length, 1, "the drag wrote the file and never asked whether it was legal");
    const [check] = w.server.checks;
    assert.equal(check.file, w.boardFile);

    // The move goes with the request or the gate grades the board on disk —
    // which is the OLD position, i.e. an answer about a board nobody is
    // looking at. One entry, anchored where the build put the part, offset by
    // the drag.
    assert.equal(check.moves.length, 1, `moves sent: ${JSON.stringify(check.moves)}`);
    assert.deepEqual(check.moves[0].anchor, { x: r30.anchor.x, y: r30.anchor.y });
    assert.equal(Math.round(check.moves[0].dx * 1000) / 1000, 2);
    assert.equal(Math.round(check.moves[0].dy * 1000) / 1000, 0);
  } finally {
    w.close();
  }
});

test("the strip prints the verdict, and prints \"not checked\" until there is one", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    // Null is not clean. Before anyone asks, the chip says so — the state this
    // panel most needs to distinguish from a green one.
    const chip = () => w.find('[data-slot="placement-verdict"]');
    assert.ok(chip(), "no verdict chip on the edit strip");
    assert.equal(chip().dataset.state, "unknown");
    assert.match(chip().textContent, /not checked/);

    w.server.nextCheck = {
      ...w.server.nextCheck,
      status: "blocked",
      counts: { error: 2, warning: 5, info: 9 },
      geometry: "predicted",
      warnings: [
        { part: "R30", kind: "trace_left_its_pad", detail: "R30.pin1 is 1.500mm outside its pad", severity: "error" },
        { part: "board", kind: "dfm_hole_clearance", detail: "track 0.074mm from a via", severity: "warning" },
      ],
      elapsedMs: 340,
    };
    dragR30East(w);
    await w.settle(6);

    assert.equal(chip().dataset.state, "blocked");
    assert.match(chip().textContent, /2 blocking/);

    // And the detail, which is where the honesty lives: what geometry was
    // graded, and what the gate could not see at all.
    click(w.find('[data-slot="placement-verdict-chip"]'));
    await w.settle();
    const detail = w.text('[data-slot="placement-verdict-detail"]');
    assert.match(detail, /1 move applied/);
    assert.match(detail, /nothing has been recompiled/);
    assert.match(detail, /R30\.pin1 is 1\.500mm outside its pad/);
    assert.match(detail, /Not checked: the copper pour/);
  } finally {
    w.close();
  }
});

test("a gate that is down says so, instead of leaving the last green answer up", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    dragR30East(w);
    await w.settle(6);
    assert.equal(w.find('[data-slot="placement-verdict"]').dataset.state, "legal");

    // Re-asked rather than re-dragged: the second drag would have to grab the
    // part at its new position, and what is under test here is the answer, not
    // the gesture.
    w.server.nextCheck = { throws: "the gate fell over" };
    click(w.find('[data-slot="placement-verdict-recheck"]'));
    await w.settle(6);

    const chip = w.find('[data-slot="placement-verdict"]');
    assert.equal(chip.dataset.state, "unavailable", "a failed check left the previous verdict standing");
    assert.match(chip.textContent, /check unavailable/);
  } finally {
    w.close();
  }
});
