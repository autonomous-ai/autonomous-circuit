import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildBoardIndex } from "../../../lib/boardIndex.js";
import { boardToScreen, screenToBoard } from "../../../lib/boardRender.js";
import { bindPlacements, parseBoardSource } from "../boardSource.js";
import {
  CLICK_SLOP_PX,
  beginMove,
  cancelMove,
  commitMove,
  placementForHit,
  refusalForHit,
  renderOffset,
  screenDeltaToMm,
  trackMove,
} from "../placementDrag.js";

// The real boards, not fixtures. A synthetic index would agree with the
// refusal rules by construction; these three files are what the rules were
// written against and the only place the coverage numbers come from.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const board = (name) => ({
  tsx: path.join(REPO, `examples/${name}/boards/main.tsx`),
  json: path.join(REPO, `examples/${name}/boards/main.circuit.json`),
});
const BOARDS = ["terminal-keyboard", "harness-puck", "hydrate-coaster"].map(board);
const hasExamples = BOARDS.every((b) => fs.existsSync(b.tsx) && fs.existsSync(b.json));

function load(name) {
  const files = board(name);
  const index = buildBoardIndex(JSON.parse(fs.readFileSync(files.json, "utf8")));
  const parsed = parseBoardSource(fs.readFileSync(files.tsx, "utf8"));
  return { index, placements: bindPlacements(parsed.placements, index) };
}

/** A hit shaped like the one `hitTestPcb` returns for a whole component. */
const componentHit = (component) => ({
  elementId: component.pcbId,
  element: component.pcb,
  componentKey: component.key,
  netKey: "",
  layer: component.layer,
});

const VIEW = { scale: 12.5, tx: 300, ty: 220 };

// --- screen ↔ mm -----------------------------------------------------------

test("screenToBoard and boardToScreen are exact inverses", () => {
  const point = boardToScreen(VIEW, 7.5, -3.25);
  assert.deepEqual(point, { x: 393.75, y: 260.625 });
  assert.deepEqual(screenToBoard(VIEW, point.x, point.y), { x: 7.5, y: -3.25 });
});

test("a screen-pixel drag becomes millimetres with y negated", () => {
  // The single most likely way to build a move backwards: board space is y-up,
  // the screen is y-down, so dragging DOWN the screen decreases y in mm.
  assert.deepEqual(screenDeltaToMm(VIEW, 40, 40), { dx: 3.2, dy: -3.2 });
  assert.deepEqual(screenDeltaToMm(VIEW, 0, -25), { dx: 0, dy: 2 });
});

test("screenDeltaToMm agrees with the difference of two screenToBoard calls", () => {
  for (const view of [VIEW, { scale: 2.5, tx: 0, ty: 0 }, { scale: 220, tx: -14, ty: 908.5 }]) {
    for (const [ax, ay, bx, by] of [
      [100, 100, 140, 137],
      [0, 0, -33.5, 12.25],
      [640, 480, 641, 479],
    ]) {
      const a = screenToBoard(view, ax, ay);
      const b = screenToBoard(view, bx, by);
      const delta = screenDeltaToMm(view, bx - ax, by - ay);
      assert.ok(Math.abs(b.x - a.x - delta.dx) < 1e-12, `dx ${b.x - a.x} vs ${delta.dx}`);
      assert.ok(Math.abs(b.y - a.y - delta.dy) < 1e-12, `dy ${b.y - a.y} vs ${delta.dy}`);
    }
  }
});

test("a degenerate view does not divide by zero", () => {
  assert.deepEqual(screenDeltaToMm({ scale: 0 }, 10, 10), { dx: 10, dy: -10 });
  assert.deepEqual(screenDeltaToMm(null, 10, 10), { dx: 10, dy: -10 });
});

// --- snapping --------------------------------------------------------------

const PLACEMENT = Object.freeze({
  id: "StatusLed[1]",
  label: "LED1 +1",
  refdes: ["LED1", "R20"],
  x: -45.8,
  y: -32,
  offset: { dx: 0, dy: 0 },
});

const drag = (session, x, y, options) => trackMove(session, { x, y }, { clientX: 999, clientY: 999, ...options });

test("the drag snaps the movement to the step", () => {
  const start = beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 0, y: 0 });
  assert.deepEqual(pick(drag(start, 1.37, -2.61, { step: 0.5 })), { dx: 1.5, dy: -2.5 });
  assert.deepEqual(pick(drag(start, 1.37, -2.61, { step: 0.25 })), { dx: 1.25, dy: -2.5 });
  assert.deepEqual(pick(drag(start, 1.37, -2.61, { step: 1 })), { dx: 1, dy: -3 });
});

test("the modifier bypasses the grid down to 0.01mm", () => {
  const start = beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 0, y: 0 });
  assert.deepEqual(pick(drag(start, 0.123, -0.456, { step: 0.5 })), { dx: 0, dy: -0.5 });
  assert.deepEqual(pick(drag(start, 0.123, -0.456, { step: 0.5, fine: true })), { dx: 0.12, dy: -0.46 });
});

test("snapping the delta keeps a 2.54mm pitch that absolute snapping would eat", () => {
  // A header at 2.54 nudged 1mm east on a 0.5mm grid must land on 3.54, not on
  // 3.5 — the pitch is the thing that matters, not the coordinate.
  const header = { ...PLACEMENT, x: 2.54, y: 7.62 };
  const session = drag(beginMove(header, { x: 0, y: 0 }, { x: 0, y: 0 }), 0.98, 0, { step: 0.5 });
  assert.equal(commitMove(session).x, 3.54);
  assert.equal(commitMove(session).y, 7.62);
});

test("an unsnappable step is left alone rather than turned into NaN", () => {
  const start = beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 0, y: 0 });
  assert.deepEqual(pick(drag(start, 1.37, -2.61, { step: 0 })), { dx: 1.37, dy: -2.61 });
});

// --- click versus drag -----------------------------------------------------

test("a press that travels less than the slop is still a click", () => {
  const start = beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 100, y: 100 });
  const nudged = trackMove(start, { x: 0.5, y: 0 }, { clientX: 100 + CLICK_SLOP_PX, clientY: 100, step: 0.5 });
  assert.equal(nudged.moved, false);
  assert.equal(commitMove(nudged), null, "a click must not emit an edit");

  const dragged = trackMove(nudged, { x: 0.5, y: 0 }, { clientX: 100 + CLICK_SLOP_PX + 1, clientY: 100, step: 0.5 });
  assert.equal(dragged.moved, true);
  assert.ok(commitMove(dragged));
});

test("moved never goes back to false once the pointer has left the slop", () => {
  const start = beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 100, y: 100 });
  const out = trackMove(start, { x: 4, y: 0 }, { clientX: 160, clientY: 100, step: 0.5 });
  const back = trackMove(out, { x: 0, y: 0 }, { clientX: 100, clientY: 100, step: 0.5 });
  assert.equal(back.moved, true);
  // …but it snapped back to where it started, which is not an edit.
  assert.equal(commitMove(back), null);
});

test("trackMove returns the same object when the snapped position has not changed", () => {
  const start = beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 0, y: 0 });
  const first = drag(start, 1.01, 0, { step: 0.5 });
  const again = drag(first, 1.09, 0, { step: 0.5 });
  assert.equal(again, first, "identical snapped state must not re-render the canvas");
});

// --- cancel ----------------------------------------------------------------

test("cancel restores the original position exactly, not approximately", () => {
  // A placement already carrying an earlier, un-rebuilt move: cancelling this
  // drag has to leave that one untouched and unrounded.
  const carried = { ...PLACEMENT, offset: { dx: 0.1, dy: -0.30000000000000004 } };
  let session = beginMove(carried, { x: 0, y: 0 }, { x: 0, y: 0 });
  for (const [x, y] of [[0.2, -0.7], [1.3, 2.9], [-4.05, 0.15], [0.1, 0.2]]) {
    session = drag(session, x, y, { step: 0.05, fine: true });
  }
  assert.notDeepEqual(renderOffset(carried, session), carried.offset);

  const cancelled = cancelMove(session);
  assert.equal(cancelled, null);
  const restored = renderOffset(carried, cancelled);
  assert.ok(Object.is(restored.dx, carried.offset.dx), `${restored.dx} !== ${carried.offset.dx}`);
  assert.ok(Object.is(restored.dy, carried.offset.dy), `${restored.dy} !== ${carried.offset.dy}`);
  assert.equal(commitMove(cancelled), null, "a cancelled drag must emit nothing");
});

test("the subtract-the-delta restore this design avoids really does drift", () => {
  // Why cancelMove returns null instead of undoing the accumulated movement.
  const undone = 0.1 + 0.2 - 0.3;
  assert.notEqual(undone, 0);
  assert.ok(Object.is(renderOffset({ offset: { dx: 0.1, dy: 0.1 } }, null).dx, 0.1));
});

test("renderOffset ignores a session belonging to a different placement", () => {
  const other = { ...PLACEMENT, id: "DebugPort[1]" };
  const session = drag(beginMove(other, { x: 0, y: 0 }, { x: 0, y: 0 }), 5, 5, { step: 1 });
  assert.deepEqual(renderOffset(PLACEMENT, session), { dx: 0, dy: 0 });
});

// --- the emitted edit ------------------------------------------------------

test("the drop emits a semantic edit — refdes and absolute board millimetres", () => {
  const session = drag(beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 0, y: 0 }), 2.8, 0.02, { step: 0.1 });
  assert.deepEqual(commitMove(session), {
    placementId: "StatusLed[1]",
    label: "LED1 +1",
    refdes: ["LED1", "R20"],
    x: -43,
    y: -32,
    dx: 2.8000000000000003,
    dy: 0,
  });
});

test("the emitted refdes list is a copy, so a consumer cannot edit the binding", () => {
  const session = drag(beginMove(PLACEMENT, { x: 0, y: 0 }, { x: 0, y: 0 }), 1, 0, { step: 1 });
  const edit = commitMove(session);
  edit.refdes.push("Q9");
  assert.deepEqual(PLACEMENT.refdes, ["LED1", "R20"]);
});

test("beginMove refuses to start without a placement or a point", () => {
  assert.equal(beginMove(null, { x: 0, y: 0 }, { x: 0, y: 0 }), null);
  assert.equal(beginMove(PLACEMENT, null, { x: 0, y: 0 }), null);
  assert.equal(trackMove(null, { x: 1, y: 1 }, { step: 1 }), null);
  assert.equal(commitMove(null), null);
});

// --- what cannot be dragged, and why ---------------------------------------

test("every real board reports the same reachable count the contract measured", { skip: !hasExamples }, () => {
  const expected = {
    "terminal-keyboard": { draggable: 37, computed: 50, block: 50 },
    "harness-puck": { draggable: 44, block: 17 },
    "hydrate-coaster": { draggable: 44 },
  };
  for (const [name, want] of Object.entries(expected)) {
    const { index, placements } = load(name);
    const tally = {};
    for (const component of index.components) {
      const refusal = refusalForHit(index, placements, componentHit(component));
      const kind = refusal ? refusal.kind : "draggable";
      tally[kind] = (tally[kind] || 0) + 1;
    }
    assert.deepEqual(tally, want, name);
  }
});

test("copper is refused with the router named, on both traces and vias", { skip: !hasExamples }, () => {
  const { index, placements } = load("terminal-keyboard");
  for (const type of ["pcb_trace", "pcb_via"]) {
    const element = index.pcbDrawables.find((e) => e.type === type);
    assert.ok(element, type);
    const refusal = refusalForHit(index, placements, { elementId: element[`${type}_id`], element, componentKey: "" });
    assert.equal(refusal.kind, "copper");
    assert.match(refusal.reason, /router/);
    // No outline: a trace's bounding box is most of the board, and drawing a
    // rectangle round it would say the wrong thing about what was refused.
    assert.equal(refusal.box, null);
  }
});

test("a loop-placed part says the board file places it in code", { skip: !hasExamples }, () => {
  const { index, placements } = load("terminal-keyboard");
  // D1 is one of the 50 diodes `keyCells()` emits directly into <board>.
  const diode = index.componentByRefdes.get("D1");
  const refusal = refusalForHit(index, placements, componentHit(diode));
  assert.equal(refusal.kind, "computed");
  assert.equal(refusal.reason, "D1 has no pcbX/pcbY in the board file — it is placed in code");
  assert.ok(refusal.box, "a part refusal outlines the part, so the reason has something to point at");
});

test("a part inside an unplaced block says so, and names the part", { skip: !hasExamples }, () => {
  const { index, placements } = load("terminal-keyboard");
  const key = index.componentByRefdes.get("SW10");
  const refusal = refusalForHit(index, placements, componentHit(key));
  assert.equal(refusal.kind, "block");
  assert.match(refusal.reason, /^SW10 moves with its block/);
});

test("a part inside a block the file DOES place is draggable by the block's frame", { skip: !hasExamples }, () => {
  const { index, placements } = load("terminal-keyboard");
  // LED1 lives inside <StatusLed pcbX={-45.8} pcbY={-32}>; dragging it moves
  // the block, which is R20 as well — and the label says so before the drop.
  const led = index.componentByRefdes.get("LED1");
  assert.equal(refusalForHit(index, placements, componentHit(led)), null);
  const placement = placementForHit(placements, componentHit(led));
  assert.equal(placement.label, "LED1 +1");
  assert.deepEqual(placement.refdes, ["LED1", "R20"]);
});

test("a locked placement is refused, and the reason says how to unlock it", { skip: !hasExamples }, () => {
  const { index, placements } = load("terminal-keyboard");
  const led = index.componentByRefdes.get("LED1");
  const hit = componentHit(led);
  const id = placements.byComponentKey.get(led.key);
  // Lock it the way `readLock` would after the comment lands in the file.
  placements.byId.set(id, { ...placements.byId.get(id), locked: true });
  const refusal = refusalForHit(index, placements, hit);
  assert.equal(refusal.kind, "locked");
  assert.match(refusal.reason, /LED1 \+1 is locked by hand/);
  assert.match(refusal.reason, /unlock/);
});

test("nothing under the pointer is not a refusal — empty board still pans", () => {
  assert.equal(refusalForHit(null, null, null), null);
});

test("a hit on something with no component and no placement still gets a reason", () => {
  const refusal = refusalForHit(
    { groups: [], components: [], componentBySourceId: new Map() },
    { byId: new Map(), byComponentKey: new Map(), byElementId: new Map() },
    { elementId: "pcb_silkscreen_path_9", element: { type: "pcb_silkscreen_path" }, componentKey: "" },
  );
  assert.equal(refusal.kind, "unplaced");
  assert.ok(refusal.reason.length > 0);
});

/** dx/dy only — the rest of a session is bookkeeping. */
function pick(session) {
  return { dx: session.dx, dy: session.dy };
}
