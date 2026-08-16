import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { mount, pointer, wheel } from "../../../test/render.js";
import PcbCanvas from "../PcbCanvas.jsx";
import { bindPlacements, parseBoardSource } from "../boardSource.js";
import { buildBoardIndex } from "../../../lib/boardIndex.js";
import { boardToScreen } from "../../../lib/boardRender.js";

// The real hydrate-coaster board, not a fixture. 737 pcb drawables, 28 bound
// placements — enough copper that a canvas which silently draws nothing cannot
// pass by accident.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const BOARDS = path.join(REPO, "examples/hydrate-coaster/boards");

// Loud, not skipped. A render proof that quietly opts out when the board is
// missing is a false floor, and this suite has shipped one of those already.
for (const file of ["main.circuit.json", "main.tsx"]) {
  assert.ok(fs.existsSync(path.join(BOARDS, file)), `missing ${path.join(BOARDS, file)}`);
}

const index = buildBoardIndex(JSON.parse(fs.readFileSync(path.join(BOARDS, "main.circuit.json"), "utf8")));
const placements = bindPlacements(parseBoardSource(fs.readFileSync(path.join(BOARDS, "main.tsx"), "utf8")).placements, index);

/**
 * The one test the DOM harness exists for.
 *
 * It renders `PcbCanvas` and then drags a part, because those are the two
 * failures the 916 pure-logic tests could not see. `PcbCanvas` never imported
 * `canvasPointer.js`, so a fully tested right-button arbiter never reached the
 * canvas an EE uses; and `snapDelta` was used and never imported (8fb33cd), so
 * the first pointermove of every drag threw, `setMove` never ran, and move
 * mode moved nothing while the suite stayed green.
 *
 * R30 is a real 0402 at (-2, -6) mm on the real board. The drag is +2.000 mm,
 * an exact multiple of the 0.5 mm snap step, so the expected delta is a
 * constant and not `snapDelta` re-run in the test.
 */
test("PcbCanvas draws the hydrate-coaster board and a drag on it moves a real part", () => {
  let view = null;
  const moved = [];

  const ui = mount(PcbCanvas, {
    index,
    editing: true,
    placements,
    snapStepMm: 0.5,
    onViewChange: (next) => { view = next; },
    onPlacementMove: (placement, move) => moved.push({ id: placement.id, ...move }),
  });

  // --- it renders
  assert.equal(ui.root.dataset.slot, "pcb-canvas");
  const svg = ui.root.querySelector("svg");
  assert.ok(svg, "no <svg> — the canvas fell through to its fallback");
  // One drawable can paint more than one node (a pad draws copper and mask),
  // never fewer, so the drawable count is the floor. Measured: 905 for 737.
  const shapes = svg.querySelectorAll("path, rect, circle, text");
  assert.ok(
    shapes.length >= index.pcbDrawables.length,
    `drew ${shapes.length} nodes for ${index.pcbDrawables.length} drawables`,
  );
  // Silkscreen off the board file, so this is the hydrate coaster on screen
  // and not some other board's geometry.
  assert.match(svg.textContent, /AUTONOMOUS HYDRATE/);
  assert.ok(view && view.scale > 0, "the canvas never framed the board");

  // --- and a drag on it moves a part
  const r30 = placements.byId.get("resistor[1]");
  assert.equal(r30.name, "R30");
  const at = boardToScreen(view, r30.x, r30.y);
  const dxPx = 2 * view.scale;

  pointer(ui.root, "down", { clientX: at.x, clientY: at.y });
  pointer(ui.root, "move", { clientX: at.x + dxPx, clientY: at.y });
  // The live readout is part of the wiring: it is how an EE knows the board
  // file is about to change, and it only exists if `setMove` actually ran.
  assert.match(ui.root.textContent, /Δ 2, 0 mm/);
  pointer(ui.root, "up", { clientX: at.x + dxPx, clientY: at.y });

  assert.deepEqual(moved, [{ id: "resistor[1]", x: 0, y: -6, dx: 2, dy: 0 }]);
  assert.deepEqual(ui.errors, [], "a handler threw where only the console would have seen it");

  ui.unmount();
});

test("Shift+wheel pans the real canvas sideways, and a plain wheel zooms it", () => {
  // `wheelAction` is unit-tested; this is the join. The wheel handler is a
  // native non-passive listener attached in an effect, and the whole class of
  // bug this suite exists for is a decision function nothing calls — the right
  // button shipped tested and unwired for a day.
  const ui = mount(PcbCanvas, { index, placements, editing: false });
  try {
    const transform = () =>
      /translate\(([-\d.]+) ([-\d.]+)\) scale\(([-\d.]+) /.exec(
        [...ui.root.querySelectorAll("svg > g")]
          .map((node) => node.getAttribute("transform") || "")
          .find((value) => value.startsWith("translate")) || "",
      );

    const before = transform();
    assert.ok(before, "the canvas painted nothing to pan");

    wheel(ui.root.querySelector("svg"), { deltaY: 120, shiftKey: true, clientX: 300, clientY: 200 });
    const panned = transform();
    assert.equal(Number(panned[3]), Number(before[3]), "Shift+wheel changed the zoom");
    assert.equal(Number(panned[1]), Number(before[1]) - 120, "Shift+wheel did not travel sideways");
    assert.equal(Number(panned[2]), Number(before[2]), "Shift+wheel moved the view vertically");

    wheel(ui.root.querySelector("svg"), { deltaY: -120, clientX: 300, clientY: 200 });
    assert.ok(Number(transform()[3]) > Number(panned[3]), "a plain wheel no longer zooms");

    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});
