// Lens 3 (Editing), round 4 — building the case on round-3's carried finding:
// placement ids are positional (`tag[ordinal]`, boardSource.js:585), so an
// agent inserting an EARLIER element of the same tag renames every later one.
//
// hydrate-coaster has four resistors in file order R30, R31, R32, R33 (ids
// resistor[1..4]). A human selects R32 (resistor[3]). An agent — writing the
// same file, no rebuild in between (the case that matters: a rebuild refreshes
// the geometry snapshot and self-heals; the gap is the window before one) —
// inserts a new resistor ahead of R30. In the file's new order:
//   resistor[1] = R99 (new)   resistor[2] = R30   resistor[3] = R31
//   resistor[4] = R32         resistor[5] = R33
// The geometry snapshot is still keyed by the OLD ids (resistor[1]=R30's
// geometry, [2]=R31's, [3]=R32's, [4]=R33's) because `buildKey` hasn't moved.
// `rebindPlacements` (boardSource.js:1129) looks up each FRESH placement's id
// in that stale, id-keyed map — so every resistor except the last is bound to
// its NEIGHBOUR's built geometry, silently. Nothing here is a rename in the
// text (R30..R33 keep their own names) — the misbinding is purely a property
// of the id scheme colliding with a concurrent insert.

import assert from "node:assert/strict";
import test from "node:test";

import { click, key, pointer } from "../../../../viewer/src/client/test/render.js";
import { openWorkspace } from "../../../../viewer/src/client/components/board/__tests__/boardWorkspace.test-helper.js";
import { parseBoardSource } from "../../../../viewer/src/client/components/board/boardSource.js";

function findPlacement(w, name) {
  for (const [id, p] of w.placements.byId) {
    if (p.name === name) return { id, ...p };
  }
  throw new Error(`${name} not found`);
}

test("agent inserts an earlier resistor: selection silently rebinds to a neighbour's geometry, not to R32", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    const r32 = findPlacement(w, "R32");
    assert.equal(r32.id, "resistor[3]", "R32 is not resistor[3] before the insert — test's premise is wrong");
    assert.equal(r32.x, -8);
    assert.equal(r32.y, 2);

    // The human clicks R32 and it is genuinely selected — before anything else
    // happens.
    const spot = w.at(r32.x, r32.y);
    pointer(w.canvas, "down", spot);
    pointer(w.canvas, "up", spot);
    await w.settle();
    const summaryBefore = w.text('[data-slot="property-placement-summary"]');
    assert.match(summaryBefore, /^R32/, `selecting R32 did not show R32 in Properties: "${summaryBefore}"`);

    // The independently-computed answer for what SHOULD be true after the
    // insert, from the real parser/binder run fresh (this is the ground truth
    // the running app is compared against, not the app's own opinion of
    // itself).
    const inserted = w.server.source.replace(
      '<resistor name="R30"',
      '<resistor name="R99" resistance="1M" footprint="0402" pcbX={99} pcbY={99} schX={99} schY={99} />\n    <resistor name="R30"',
    );
    const freshPlacements = parseBoardSource(inserted).placements;
    const r32AfterInsert = freshPlacements.find((p) => p.name === "R32");
    assert.equal(r32AfterInsert.id, "resistor[4]", "R32 should now be resistor[4] after one earlier insert");
    const r31AfterInsert = freshPlacements.find((p) => p.name === "R31");
    assert.equal(r31AfterInsert.id, "resistor[3]", "R31 now occupies the id R32 used to have: resistor[3]");

    // The agent writes it. No rebuild happens — `buildKey` does not move — so
    // the geometry snapshot the app is holding is still keyed by the OLD ids.
    w.server.agentWrites(inserted);
    w.ui.set({ manifestRevision: 99 });
    await w.settle(6);

    // What is selected now, from the human's point of view, is still "the
    // thing I clicked" as far as the UI's selection state is concerned — the
    // id resistor[3] was never told to deselect. Read what Properties shows
    // for it now.
    const summaryAfter = w.text('[data-slot="property-placement-summary"]');

    console.log("BEFORE insert, selected:", summaryBefore);
    console.log("AFTER insert, resistor[3] in the fresh text is:", r31AfterInsert.name, "at", r31AfterInsert.x, r31AfterInsert.y);
    console.log("AFTER insert, Properties panel now shows:", summaryAfter);

    // The claim under test: the panel now describes R31 (the part that now
    // sits at id resistor[3]), NOT R32, and the human was given no signal that
    // their selection quietly became a different part.
    assert.match(
      summaryAfter,
      /^R31/,
      `expected the id resistor[3] to now silently describe R31, got: "${summaryAfter}"`,
    );

    // Now drag. The canvas's own selection box is driven independently (it
    // resolves the componentKey captured at click time straight against the
    // compiled board — `resolveSelection` in BoardWorkspace.jsx:407 — which is
    // real geometry and never goes stale), so the box the human sees, and the
    // copper the drag visually carries, is still genuinely R32's. The
    // question is what the drag actually WRITES: R32's line, or R31's.
    const dragTo = w.at(r32.x + 5, r32.y + 5);
    pointer(w.canvas, "down", spot);
    await w.settle(2);
    console.log("  mid-drag Properties label:", w.text('[data-slot="property-placement-summary"]'));
    pointer(w.canvas, "move", dragTo);
    pointer(w.canvas, "up", dragTo);
    await w.settle(6);
    console.log("  last write edit:", JSON.stringify(w.server.writes.at(-1)));

    const finalText = w.server.source;
    const finalR31 = parseBoardSource(finalText).placements.find((p) => p.name === "R31");
    const finalR32 = parseBoardSource(finalText).placements.find((p) => p.name === "R32");
    console.log("AFTER drag (dragged what LOOKED like R32 on screen):");
    console.log("  R31 in file now at:", finalR31.x, finalR31.y, "(was", r31AfterInsert.x, r31AfterInsert.y, ")");
    console.log("  R32 in file now at:", finalR32.x, finalR32.y, "(was", r32.x, r32.y, ")");

    // Surprising, and worth recording precisely: the MOUSE DRAG self-corrects.
    // A new pointer-down re-hit-tests the compiled board directly (componentKey
    // under the cursor is real, immutable geometry), so `handlePlacementMove`
    // receives a placement resolved fresh off that hit — not off the stale
    // `activePlacementId`. The edit lands on R32's real line and its own
    // `summary` correctly says "R32 moved 5, 5 mm" (quoted above), even though
    // the Properties panel was showing "R31" the entire time the drag was live.
    // The label is wrong; this particular write is not. See the next test —
    // the keyboard path has no re-hit-test and is not so lucky.
    assert.equal(finalR32.x, r32.x + 5, "expected the drag to have landed on R32's own line (it re-hit-tests, unlike the keyboard path)");
    assert.equal(finalR32.y, r32.y + 5, "expected the drag to have landed on R32's own line (it re-hit-tests, unlike the keyboard path)");
    assert.equal(finalR31.x, 2, "R31 should be untouched by a drag that started on R32's copper");
    assert.equal(finalR31.y, -6, "R31 should be untouched by a drag that started on R32's copper");
  } finally {
    w.close();
  }
});

test("same setup, but Ctrl+arrow nudge (not a fresh mouse hit-test) writes to the WRONG neighbour's line", async () => {
  // The mouse-drag path re-hit-tests the compiled board by componentKey at
  // drag start, which happens to self-correct the id staleness (previous
  // test). Ctrl+arrow does not: BoardWorkspace.jsx:1006-1013 resolves
  // `selectedPlacement` from `editor.placements.byId.get(activePlacementId)`
  // — the stale, id-keyed, now-misbound map — with no re-hit-test at all.
  const w = await openWorkspace({ example: "hydrate-coaster" });
  try {
    const r32 = findPlacement(w, "R32");
    const spot = w.at(r32.x, r32.y);
    pointer(w.canvas, "down", spot);
    pointer(w.canvas, "up", spot);
    await w.settle();
    assert.match(w.text('[data-slot="property-placement-summary"]'), /^R32/);

    const inserted = w.server.source.replace(
      '<resistor name="R30"',
      '<resistor name="R99" resistance="1M" footprint="0402" pcbX={99} pcbY={99} schX={99} schY={99} />\n    <resistor name="R30"',
    );
    w.server.agentWrites(inserted);
    w.ui.set({ manifestRevision: 99 });
    await w.settle(6);
    assert.match(
      w.text('[data-slot="property-placement-summary"]'),
      /^R31/,
      "setup check: panel should show R31 after the insert, same as the drag test",
    );

    key(window, "ArrowRight", { ctrlKey: true });
    await w.settle(6);

    const after = parseBoardSource(w.server.source);
    const r31After = after.placements.find((p) => p.name === "R31");
    const r32After = after.placements.find((p) => p.name === "R32");
    console.log("Ctrl+ArrowRight after the insert:");
    console.log("  R31 now at:", r31After.x, r31After.y, "(nudge step is 0.5mm)");
    console.log("  R32 now at:", r32After.x, r32After.y, "(should be unchanged: -8, 2)");
    console.log("  last write:", JSON.stringify(w.server.writes.at(-1)));

    // The human clicked R32, saw R32 in the panel, watched the file change out
    // from under them, and pressed one nudge key. This asserts which part's
    // line actually moved: R32's is untouched, and R31 — a part the human
    // never selected and has no on-screen indication is even involved — moved
    // instead. The write's own summary claims "R31 moved 0.5, 0 mm", which is
    // true of the file and false of what the human thinks just happened.
    const r31Before = findPlacement(w, "R31");
    assert.equal(r32After.x, r32.x, "if this fails, R32 moved — the nudge landed on the part the human actually clicked");
    assert.equal(r31After.x, r31Before.x + 0.5, "expected the nudge to have landed on R31's own line instead");
  } finally {
    w.close();
  }
});
