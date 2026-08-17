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

test("a turn the gate could not grade is on the chip, not only inside it", async () => {
  // The gate translates elements; it cannot rotate them. So after a turn the
  // verdict is about a board missing that turn — and "legal" and "legal,
  // except for the thing you just did" have to look different at a glance or
  // the disclosure is decoration.
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    const r30 = w.placements.byId.get("resistor[1]");
    const at = w.at(r30.x, r30.y);
    pointer(w.canvas, "down", at);
    pointer(w.canvas, "up", at);
    await w.settle();

    click(w.find('[data-slot="placement-rotate-cw"]'));
    await w.settle(6);

    const chip = w.find('[data-slot="placement-verdict"]');
    assert.equal(chip.dataset.state, "legal");
    assert.equal(chip.dataset.turnsUnchecked, "1");
    assert.match(chip.textContent, /legal · 1 turn unchecked/);

    // A second turn counts, and undoing one takes the count back down: the
    // number follows the geometry, not the number of gestures.
    click(w.find('[data-slot="placement-rotate-cw"]'));
    await w.settle(6);
    assert.match(w.find('[data-slot="placement-verdict"]').textContent, /2 turns unchecked/);
    click(w.find('[data-slot="placement-undo"]'));
    await w.settle(6);
    assert.match(w.find('[data-slot="placement-verdict"]').textContent, /1 turn unchecked/);
  } finally {
    w.close();
  }
});

test("a trace width is set from Properties and undone like any other edit", async () => {
  // Round 3's finding: `setNetWidth` wrote the file and pushed no history, so
  // ⌘Z after Set did nothing — the only edit on this strip that was not
  // undoable. A width edit can add a whole line to the board file (a net wired
  // inside a block gets a board-level trace), so its inverse is the recorded
  // bytes, the same way a rotation wrap is undone.
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    const before = w.server.source;

    // Select a part, then one of its nets — the way a person reaches a net.
    const r30 = w.placements.byId.get("resistor[1]");
    const at = w.at(r30.x, r30.y);
    pointer(w.canvas, "down", at);
    pointer(w.canvas, "up", at);
    await w.settle(4);

    const netLink = [...w.ui.container.querySelectorAll('[data-slot="property-net-link"]')].find(
      (node) => node.textContent.trim() === "CAP_DRIVE",
    );
    assert.ok(netLink, "no way to reach a net from the part that is on it");
    click(netLink);
    await w.settle(6);

    const row = w.find('[data-slot="net-width"]');
    assert.ok(row, "no width row for a selected net");
    assert.match(row.textContent, /can take 0\.4mm/, "the measured ceiling is not on screen");

    const input = w.find('[data-slot="net-width-input"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(input, "0.4");
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
    await w.settle();
    click(w.find('[data-slot="net-width-apply"]'));
    await w.settle(8);

    assert.match(w.server.source, /to="net\.CAP_DRIVE" thickness="0\.4mm"/, "the width never reached the file");

    const undo = w.find('[data-slot="placement-undo"]');
    assert.ok(undo && !undo.disabled, "a width edit left nothing to undo");
    click(undo);
    await w.settle(8);
    assert.equal(w.server.source, before, "undo did not restore the file byte for byte");
  } finally {
    w.close();
  }
});

// A gate that answers "1 blocking" on a board the build says has 3 is not
// wrong — it cannot run KiCad — but a number with no scope on it reads as the
// whole truth. The engineer who built `two-key-footswitch` (2026-08-17) got
// exactly these numbers from the command line and only caught the gap because
// they happened to diff the gate against the full build. These are that
// board's real figures.
test("the chip never shows its own count as the whole truth when the build knows a bigger one", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    w.server.nextCheck = {
      ...w.server.nextCheck,
      counts: { error: 1, warning: 2, info: 7 },
      warnings: [{ part: "U3", kind: "dfm_hole_clearance", severity: "error", detail: "a pad passes 0.150mm from a via" }],
      lastBuild: {
        atEpochS: Math.round(Date.now() / 1000) - 12 * 60,
        blocking: 3,
        invisibleHere: 2,
        warnings: 159,
        invisibleWarningsHere: 145,
        invisibleKinds: ["drc_violation"],
        fabReady: false,
      },
    };

    dragR30East(w);
    await w.settle(6);

    const chip = w.find('[data-slot="placement-verdict-chip"]');
    assert.ok(chip, "no verdict chip after the drag");
    assert.match(chip.textContent, /1 blocking/, "the gate's own count went missing");
    assert.match(chip.textContent, /2 unseen/, "the two findings this gate cannot produce are not on the chip");

    click(chip);
    await w.settle();
    const detail = w.text('[data-slot="placement-verdict-detail"]');
    assert.match(detail, /last full build \(12 min ago\) found 3 blocking findings/,
      `the age of the other ruler's answer is not shown: ${detail}`);
    assert.match(detail, /drc_violation/, "the detail does not name the kind the gate is blind to");
    assert.match(detail, /one ruler out of two/);
    // The warning tier, which is where most of KiCad's findings land. Counting
    // only the blocking tier hid 141 of these behind a chip that said nothing.
    assert.match(detail, /159 at warning level/, `the warning tier is still silent: ${detail}`);
    assert.match(detail, /145 of those invisible here too/);
  } finally {
    w.close();
  }
});

// The other half, and the one that matters more: a *clean* answer on a board
// whose last build had KiCad-only blockers must not look like a clean board.
// Green here would be the whole defect, restated in a colour.
test("a legal answer is not green while the build's KiCad findings stand", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    w.server.nextCheck = {
      ...w.server.nextCheck,
      counts: { error: 0, warning: 1, info: 3 },
      warnings: [],
      lastBuild: {
        atEpochS: Math.round(Date.now() / 1000) - 60,
        blocking: 2,
        invisibleHere: 2,
        warnings: 0,
        invisibleWarningsHere: 0,
        invisibleKinds: ["drc_violation"],
        fabReady: false,
      },
    };

    dragR30East(w);
    await w.settle(6);

    const chip = w.find('[data-slot="placement-verdict-chip"]');
    assert.match(chip.textContent, /legal here · 2 unseen/, `chip read: ${chip.textContent}`);
    assert.ok(
      /amber/.test(chip.className),
      `a legal-but-unseen chip is painted like a clean one: ${chip.className}`,
    );

    // And a board the build called clean keeps its plain green word.
    w.server.nextCheck = {
      ...w.server.nextCheck,
      lastBuild: {
        atEpochS: Math.round(Date.now() / 1000) - 60,
        blocking: 0,
        invisibleHere: 0,
        warnings: 0,
        invisibleWarningsHere: 0,
        invisibleKinds: [],
        fabReady: true,
      },
    };
    click(w.find('[data-slot="placement-verdict-recheck"]'));
    await w.settle(6);
    const clean = w.find('[data-slot="placement-verdict-chip"]');
    assert.equal(clean.textContent.trim(), "legal", `a clean board no longer reads clean: ${clean.textContent}`);
    assert.ok(/emerald/.test(clean.className), `a clean board lost its green: ${clean.className}`);
  } finally {
    w.close();
  }
});
