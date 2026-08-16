import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { key, menu, mount, pointer } from "../../../test/render.js";
import PcbCanvas from "../PcbCanvas.jsx";
import { LOCK_COMMENT, bindPlacements, parseBoardSource } from "../boardSource.js";
import { buildBoardIndex } from "../../../lib/boardIndex.js";
import { boardToScreen } from "../../../lib/boardRender.js";

// The real hydrate-coaster board, not a fixture — same reason the render proof
// uses it: a canvas that silently draws nothing cannot pass by accident, and
// the coordinates below are ones a person can look up in the board file.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const BOARDS = path.join(REPO, "examples/hydrate-coaster/boards");

// Loud, not skipped. A pointer proof that quietly opts out when the board is
// missing is the false floor this suite has shipped once already.
for (const file of ["main.circuit.json", "main.tsx"]) {
  assert.ok(fs.existsSync(path.join(BOARDS, file)), `missing ${path.join(BOARDS, file)}`);
}

const source = fs.readFileSync(path.join(BOARDS, "main.tsx"), "utf8");
const index = buildBoardIndex(JSON.parse(fs.readFileSync(path.join(BOARDS, "main.circuit.json"), "utf8")));
const placements = bindPlacements(parseBoardSource(source).placements, index);

/**
 * The same board with R31 locked, written the way a human locks a part: the
 * lock comment on the line above it. Locking by hand-editing the bound object
 * would prove nothing — it is the parser's `locked` flag reaching the canvas
 * that is under test.
 */
const lockedPlacements = (() => {
  const lines = source.split("\n");
  const at = lines.findIndex((line) => line.includes('name="R31"'));
  assert.ok(at > 0, "R31 is not in the board file any more — retarget this test");
  lines.splice(at, 0, `${lines[at].match(/^\s*/)[0]}${LOCK_COMMENT}`);
  const bound = bindPlacements(parseBoardSource(lines.join("\n")).placements, index);
  assert.equal(bound.byId.get("resistor[2]").locked, true, "the lock comment did not reach the placement");
  return bound;
})();

// R30 · 0402, pcbX=-2, pcbY=-6. Its left pad is the click target: a pad has a
// net and a component, so one press can prove component-select and net-select.
const R30_PAD = { id: "pcb_smtpad_147", x: -2.51, y: -6 };
const R30_KEY = "source_component_32";
const R30_NET = index.netKeyByElementId.get(R30_PAD.id);
// R32 · 0402, pcbX=-8, pcbY=2. The part every drag here carries.
const R32 = { id: "resistor[3]", x: -8, y: 2, padId: "pcb_smtpad_151" };
// R31 · 0402, pcbX=2, pcbY=-6. Locked in `lockedPlacements`, movable in `placements`.
const R31 = { id: "resistor[2]", x: 2, y: -6 };

/** The camera the canvas frames this board with, in a 1200×800 pane. */
const FRAMED = { scale: 9.3, tx: 600, ty: 400 };

function openBoard({ bound = placements, editing = true, ...rest } = {}) {
  const seen = { select: [], picked: [], moved: [], menus: [], views: [] };
  const ui = mount(PcbCanvas, {
    index,
    editing,
    placements: bound,
    snapStepMm: 0.5,
    onViewChange: (next) => seen.views.push(next),
    onSelect: (selection, meta) => seen.select.push([selection, meta]),
    onPlacementSelect: (placement) => seen.picked.push(placement ? placement.id : null),
    onPlacementMove: (placement, move) => seen.moved.push({ id: placement.id, ...move }),
    onContextMenuRequest: (request) => seen.menus.push(request),
    ...rest,
  });
  const svg = ui.root.querySelector("svg");
  assert.ok(svg, "no <svg> — the canvas fell through to its fallback");
  const view = () => seen.views[seen.views.length - 1];
  return {
    ui,
    seen,
    svg,
    view,
    /** Board mm → the client point a pointer would have to be at. The stage
     *  sits at the window origin, so client and stage-local coincide. */
    at: (x, y) => boardToScreen(view(), x, y),
    /** What the board group is transformed by right now, as the DOM has it. */
    camera: () => svg.querySelector("g[transform]").getAttribute("transform"),
    finish() {
      assert.deepEqual(ui.errors, [], "a handler threw where only the console would have seen it");
      ui.unmount();
    },
  };
}

/** Every transform between an element and the SVG root, innermost first. */
function chainOf(svg, elementId) {
  const nodes = svg.querySelectorAll(`[data-element-id="${elementId}"]`);
  assert.equal(nodes.length, 1, `expected one ${elementId}, found ${nodes.length}`);
  const out = [];
  for (let node = nodes[0]; node && node !== svg; node = node.parentElement) {
    const transform = node.getAttribute("transform");
    if (transform) out.push(transform);
  }
  return out;
}

test("a left drag pans the board by exactly the pointer travel", () => {
  const board = openBoard({ editing: false });
  assert.deepEqual(board.view(), FRAMED, "the canvas did not frame the board");

  pointer(board.ui.root, "down", { clientX: 600, clientY: 400 });
  pointer(board.ui.root, "move", { clientX: 720, clientY: 355 });
  pointer(board.ui.root, "up", { clientX: 720, clientY: 355 });

  assert.deepEqual(board.view(), { scale: 9.3, tx: 720, ty: 355 });
  assert.equal(board.camera(), "translate(720 355) scale(9.3 -9.3)");
  // A drag is not a click, so nothing may be selected by it.
  assert.deepEqual(board.seen.select, []);
  board.finish();
});

test("a right drag pans the same board by the same amount, and macOS's contextmenu-on-press does not kill it", () => {
  const board = openBoard({ editing: false });

  pointer(board.ui.root, "down", { clientX: 600, clientY: 400, button: 2 });
  // macOS fires this on the press, before the pointer can travel. Cancelling a
  // pan here would break every right-drag; `escapeLiveCommand` refuses to.
  menu(board.ui.root, { clientX: 600, clientY: 400 });
  pointer(board.ui.root, "move", { clientX: 720, clientY: 355, button: 2 });
  pointer(board.ui.root, "up", { clientX: 720, clientY: 355, button: 2 });

  assert.deepEqual(board.view(), { scale: 9.3, tx: 720, ty: 355 });
  assert.equal(board.camera(), "translate(720 355) scale(9.3 -9.3)");
  assert.deepEqual(board.seen.menus, [], "a right drag is a pan, not a menu");
  board.finish();
});

test("a right press that does not travel asks for the context menu and moves nothing", () => {
  const board = openBoard();
  const on = board.at(R30_PAD.x, R30_PAD.y);

  pointer(board.ui.root, "down", { clientX: on.x, clientY: on.y, button: 2 });
  menu(board.ui.root, { clientX: on.x, clientY: on.y });
  pointer(board.ui.root, "up", { clientX: on.x, clientY: on.y, button: 2 });

  assert.equal(board.seen.menus.length, 1);
  const request = board.seen.menus[0];
  assert.equal(request.source, "pcb");
  assert.deepEqual(request.client, { x: on.x, y: on.y });
  // The menu is asked for at a place on the board, not just at a place on the
  // screen: the items it offers are the ones this part answers to.
  assert.equal(request.hit.elementId, R30_PAD.id);
  assert.equal(request.placement.label, "R30");
  assert.deepEqual(board.view(), FRAMED, "a right click panned the camera");
  // Altium leaves the selection alone on a right click, and so do we.
  assert.deepEqual(board.seen.select, []);
  board.finish();
});

test("a right press that travels pans and asks for no menu", () => {
  const board = openBoard();
  const on = board.at(R30_PAD.x, R30_PAD.y);

  pointer(board.ui.root, "down", { clientX: on.x, clientY: on.y, button: 2 });
  pointer(board.ui.root, "move", { clientX: on.x + 80, clientY: on.y + 30, button: 2 });
  pointer(board.ui.root, "up", { clientX: on.x + 80, clientY: on.y + 30, button: 2 });

  assert.deepEqual(board.view(), { scale: 9.3, tx: 680, ty: 430 });
  assert.deepEqual(board.seen.menus, [], "the pan ended in a context menu");
  assert.deepEqual(board.seen.moved, [], "the right button grabbed a part");
  board.finish();
});

test("a click selects the component under it and leaves the camera where it was", () => {
  const board = openBoard();
  const on = board.at(R30_PAD.x, R30_PAD.y);

  // 2 px of travel: inside the 4 px slop, so this is still a click.
  pointer(board.ui.root, "down", { clientX: on.x, clientY: on.y });
  pointer(board.ui.root, "move", { clientX: on.x + 2, clientY: on.y });
  pointer(board.ui.root, "up", { clientX: on.x + 2, clientY: on.y });

  assert.deepEqual(board.seen.select, [[{ kind: "component", key: R30_KEY }, { jump: false, source: "pcb" }]]);
  // In move mode the click also picks what the edit bar acts on.
  assert.deepEqual(board.seen.picked, ["resistor[1]"]);
  assert.deepEqual(board.seen.moved, [], "a click committed a move");
  assert.deepEqual(board.view(), FRAMED, "a click moved the camera");
  board.finish();
});

test("⌘-click selects the same part and asks the other pane to jump to it", () => {
  const board = openBoard();
  const on = board.at(R30_PAD.x, R30_PAD.y);

  pointer(board.ui.root, "down", { clientX: on.x, clientY: on.y, metaKey: true });
  pointer(board.ui.root, "up", { clientX: on.x, clientY: on.y, metaKey: true });

  assert.deepEqual(board.seen.select, [[{ kind: "component", key: R30_KEY }, { jump: true, source: "pcb" }]]);
  board.finish();
});

test("⇧-click selects the whole net the pad belongs to", () => {
  const board = openBoard();
  const on = board.at(R30_PAD.x, R30_PAD.y);

  pointer(board.ui.root, "down", { clientX: on.x, clientY: on.y, shiftKey: true });
  pointer(board.ui.root, "up", { clientX: on.x, clientY: on.y, shiftKey: true });

  assert.ok(R30_NET, "R30's pad has no net — retarget this test");
  assert.deepEqual(board.seen.select, [[{ kind: "net", key: R30_NET }, { jump: false, source: "pcb" }]]);
  board.finish();
});

test("a locked placement says so under the pointer and refuses the drag", () => {
  const board = openBoard({ bound: lockedPlacements });

  const over = (x, y) => {
    const on = board.at(x, y);
    pointer(board.ui.root, "move", { clientX: on.x, clientY: on.y, buttons: 0 });
    return board.svg.querySelector('[data-slot="pcb-move-target"]');
  };

  // The control: R32 is not locked, so the same overlay must say so.
  assert.equal(over(R32.x, R32.y).getAttribute("data-locked"), "false");
  const marker = over(R31.x, R31.y);
  assert.equal(marker.getAttribute("data-placement"), R31.id);
  assert.equal(marker.getAttribute("data-locked"), "true");

  const from = board.at(R31.x, R31.y);
  const to = board.at(R31.x + 3, R31.y);
  pointer(board.ui.root, "down", { clientX: from.x, clientY: from.y });
  pointer(board.ui.root, "move", { clientX: to.x, clientY: to.y });
  pointer(board.ui.root, "up", { clientX: to.x, clientY: to.y });

  assert.deepEqual(board.seen.moved, [], "a locked part was dragged");
  assert.equal(board.svg.querySelector('[data-slot="pcb-move-ghost"]'), null);
  // A press that cannot grab pans instead — 3 mm right at 9.3 px/mm.
  assert.deepEqual(board.view(), { scale: 9.3, tx: 600 + 3 * 9.3, ty: 400 });
  board.finish();
});

test("a drag on a movable placement writes the snapped delta, not the raw one", () => {
  const board = openBoard();
  const from = board.at(R32.x, R32.y);
  // 1.3 mm right and 0.7 mm up. Neither is a multiple of the 0.5 mm step, so
  // the numbers below can only come from the snap: 1.3 → 1.5, 0.7 → 0.5.
  const to = board.at(R32.x + 1.3, R32.y + 0.7);

  pointer(board.ui.root, "down", { clientX: from.x, clientY: from.y });
  pointer(board.ui.root, "move", { clientX: to.x, clientY: to.y });

  const ghost = board.svg.querySelector('[data-slot="pcb-move-ghost"]');
  assert.ok(ghost, "the part being carried is not drawn anywhere");
  assert.equal(ghost.getAttribute("transform"), "translate(1.5 0.5)");
  // The readout IS the edit: after the drop these are the numbers in the file.
  const readout = board.ui.root.querySelector('[data-slot="pcb-move-readout"]');
  assert.match(readout.textContent, /R32/);
  assert.match(readout.textContent, /Δ 1\.5, 0\.5 mm/);
  assert.match(readout.textContent, /pcbX=-6\.5 pcbY=2\.5/);

  pointer(board.ui.root, "up", { clientX: to.x, clientY: to.y });

  assert.deepEqual(board.seen.moved, [{ id: R32.id, x: -6.5, y: 2.5, dx: 1.5, dy: 0.5 }]);
  assert.equal(board.svg.querySelector('[data-slot="pcb-move-readout"]'), null);
  board.finish();
});

test("Escape mid-drag puts the part back exactly where the board file has it", () => {
  const board = openBoard();
  const resting = chainOf(board.svg, R32.padId);
  const from = board.at(R32.x, R32.y);
  const to = board.at(R32.x + 1.5, R32.y);

  pointer(board.ui.root, "down", { clientX: from.x, clientY: from.y });
  pointer(board.ui.root, "move", { clientX: to.x, clientY: to.y });
  assert.equal(
    board.svg.querySelector('[data-slot="pcb-move-ghost"]').getAttribute("transform"),
    "translate(1.5 0)",
  );

  key(window, "Escape");

  // Exactly where it was, not merely near it: same transform chain, character
  // for character. The drag only ever existed as an offset the painter added,
  // so dropping it restores the file's own floats rather than a subtraction
  // that lands on 5.55e-17.
  assert.deepEqual(chainOf(board.svg, R32.padId), resting);
  assert.equal(board.svg.querySelector('[data-slot="pcb-move-ghost"]'), null);
  assert.equal(board.svg.querySelector('[data-slot="pcb-move-origin"]'), null);
  assert.equal(board.ui.root.querySelector('[data-slot="pcb-move-readout"]'), null);

  // The release that follows must not resurrect the abandoned move.
  pointer(board.ui.root, "up", { clientX: to.x, clientY: to.y });
  assert.deepEqual(board.seen.moved, []);
  board.finish();
});

test("a right click mid-drag abandons the move and commits nothing", () => {
  const board = openBoard();
  const resting = chainOf(board.svg, R32.padId);
  const from = board.at(R32.x, R32.y);
  const to = board.at(R32.x + 1.5, R32.y);

  pointer(board.ui.root, "down", { clientX: from.x, clientY: from.y });
  pointer(board.ui.root, "move", { clientX: to.x, clientY: to.y });
  assert.ok(board.svg.querySelector('[data-slot="pcb-move-ghost"]'), "the drag never started");

  // The gesture the old test could not reach: `pointerdown` does not fire for
  // a second button while one is held, so the arbiter's "cancel" branch was
  // unreachable from a press and the move committed on release anyway.
  // `contextmenu` is the event that does arrive.
  menu(board.ui.root, { clientX: to.x, clientY: to.y });

  assert.equal(board.svg.querySelector('[data-slot="pcb-move-ghost"]'), null);
  assert.equal(board.ui.root.querySelector('[data-slot="pcb-move-readout"]'), null);
  assert.deepEqual(chainOf(board.svg, R32.padId), resting);

  pointer(board.ui.root, "up", { clientX: to.x, clientY: to.y });

  assert.deepEqual(board.seen.moved, [], "the abandoned move was written to the board file");
  assert.deepEqual(board.seen.select, [], "the abandoned move ended in a selection");
  assert.deepEqual(board.view(), FRAMED, "the abandoned move panned the camera");
  board.finish();
});
