// Lens 1 (Familiarity) round-4 probes. Same harness rounds 1-3 used
// (openWorkspace against a real BoardWorkspace + real planSourceWrite).
//
// Two things this file exists to settle with a live render rather than a
// grep:
//   1. Round 2 flagged double-click firing fit-to-board instead of opening
//      Properties. Since then Properties became a permanently docked panel
//      (BoardWorkspace.jsx:1424-1439) that already updates on a plain single
//      click — so the Altium reflex is mostly served by click, not
//      double-click. What double-click still does, unconditionally
//      (PcbCanvas.jsx:1117 `onDoubleClick={fitToBoard}`, no hit test), is
//      force the camera back to a full-board fit even when the user meant to
//      zoom in further on the part they just double-clicked. Proven here by
//      zooming in first, then double-clicking the part, and checking the
//      camera snapped back out.
//   2. A plain, unmodified ArrowRight must NOT move a selected part (Altium's
//      plain arrow moves the cursor, which this app has none of); Ctrl+Arrow
//      must.

import assert from "node:assert/strict";
import test from "node:test";

import { key, pointer, wheel } from "../../../../viewer/src/client/test/render.js";
import { openWorkspace } from "../../../../viewer/src/client/components/board/__tests__/boardWorkspace.test-helper.js";
import { parseBoardSource } from "../../../../viewer/src/client/components/board/boardSource.js";

function findPlacement(w, name) {
  for (const [id, p] of w.placements.byId) {
    if (p.name === name || p.label?.startsWith(name)) return { id, ...p };
  }
  throw new Error(`${name} not found among draggable placements`);
}

test("single click already updates the docked Properties panel — the Altium reflex mostly works without double-click", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  const target = findPlacement(w, "R30");
  const spot = w.at(target.x, target.y);

  pointer(w.canvas, "down", spot);
  pointer(w.canvas, "up", spot);
  await w.settle();

  const panel = w.find('[data-slot="properties-panel"]');
  assert.ok(panel, "properties panel is not on screen");
  console.log("[single click] properties panel text:", panel.textContent.slice(0, 80));
  assert.match(panel.textContent, /R30/, "clicking R30 did not update the docked Properties panel");
  w.close();
});

test("double-click on a part still forces fit-to-board — an unwanted camera jump, not a missing Properties panel", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  const target = findPlacement(w, "R30");
  const spot0 = w.at(target.x, target.y);

  // Zoom in around the part so "fit" and "current" scale differ.
  wheel(w.canvas, { clientX: spot0.clientX, clientY: spot0.clientY, deltaY: -1200 });
  await w.settle();
  const zoomedScale = w.view.scale;

  const spot = w.at(target.x, target.y);
  pointer(w.canvas, "down", spot);
  pointer(w.canvas, "up", spot);
  await w.settle();

  const dbl = new (globalThis.MouseEvent || Event)("dblclick", {
    bubbles: true,
    cancelable: true,
    clientX: spot.clientX,
    clientY: spot.clientY,
  });
  w.canvas.dispatchEvent(dbl);
  await w.settle();

  const afterScale = w.view.scale;
  console.log(`[dblclick] zoomed scale=${zoomedScale} -> after dblclick scale=${afterScale}`);
  assert.notEqual(afterScale, zoomedScale, "double-click on the part did not change the camera at all");
  assert.ok(afterScale < zoomedScale, "expected double-click to zoom back OUT to fit, not stay zoomed in");
  w.close();
});

test("a plain ArrowRight does not move a selected part; Ctrl+ArrowRight does", async () => {
  const w = await openWorkspace({ example: "hydrate-coaster" });
  const target = findPlacement(w, "R30");
  const spot = w.at(target.x, target.y);
  pointer(w.canvas, "down", spot);
  pointer(w.canvas, "up", spot);
  await w.settle();

  key(window, "ArrowRight");
  await w.settle();
  let placement = parseBoardSource(w.server.source).placements.find((p) => p.id === target.id);
  assert.equal(placement.x, target.x, "a PLAIN ArrowRight moved the part; it must not");

  key(window, "ArrowRight", { ctrlKey: true });
  await w.settle();
  placement = parseBoardSource(w.server.source).placements.find((p) => p.id === target.id);
  assert.notEqual(placement.x, target.x, "Ctrl+ArrowRight was expected to nudge the selected part");
  console.log(`[nudge] plain ArrowRight: no-op (correct). Ctrl+ArrowRight: x ${target.x} -> ${placement.x}`);
  w.close();
});
