import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { flush, menu, mount, pointer } from "../../../test/render.js";
import SchematicCanvas from "../SchematicCanvas.jsx";
import { buildBoardIndex } from "../../../lib/boardIndex.js";
import { parseSchematicTransform, schematicToSvg } from "../../../lib/boardRender.js";

// The real hydrate-coaster sheet and the real circuit JSON behind it. The
// overlay only lines up because the pipeline writes its world→pixel matrix on
// the SVG root, so a fixture SVG would test nothing that matters here.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const BOARDS = path.join(REPO, "examples/hydrate-coaster/boards");
const SHEET = path.join(BOARDS, "main_review/_schematic.svg");

// Loud, not skipped — same reason as the PCB pointer suite.
for (const file of [path.join(BOARDS, "main.circuit.json"), SHEET]) {
  assert.ok(fs.existsSync(file), `missing ${file}`);
}

const index = buildBoardIndex(JSON.parse(fs.readFileSync(path.join(BOARDS, "main.circuit.json"), "utf8")));
const sheetText = fs.readFileSync(SHEET, "utf8");
const transform = parseSchematicTransform(sheetText);
assert.ok(transform, "the pipeline sheet lost its data-real-to-screen-transform");

// `svgTwin` turns the PNG the workspace hands this pane into the SVG it wants.
const PNG_URL = "/boards/main_review/_schematic.png";
const SVG_URL = "/boards/main_review/_schematic.svg";

// R30 · a 1M sense resistor at schX=30, schY=10. The only symbol whose box
// contains that point, so a click there can only mean this part.
const R30 = { key: "source_component_32", x: 30, y: 10 };

/**
 * Mount the sheet with the network answered from disk.
 *
 * The pane is useless until its sheet arrives — no transform, no hit testing —
 * so the fetch is the setup, not a detail. It is stubbed rather than served
 * because a test that needs a listening socket is a test that gets skipped.
 */
async function openSheet(props = {}) {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) =>
    String(url) === SVG_URL
      ? { ok: true, text: async () => sheetText }
      : { ok: false, status: 404, text: async () => "" };

  const seen = { select: [], menus: [] };
  const ui = mount(SchematicCanvas, {
    index,
    src: PNG_URL,
    onSelect: (selection, meta) => seen.select.push([selection, meta]),
    onContextMenuRequest: (request) => seen.menus.push(request),
    ...props,
  });
  await flush();

  assert.equal(ui.root.dataset.interactive, "true", "the sheet never aligned — cross-probe would be off");
  const stage = ui.root.querySelector("img").parentElement;

  /** The camera, read back off the DOM the way the sheet is actually drawn. */
  const camera = () => {
    const found = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)\s*scale\(([-\d.]+)\)/.exec(stage.style.transform);
    assert.ok(found, `unreadable sheet transform: ${stage.style.transform}`);
    return { tx: Number(found[1]), ty: Number(found[2]), scale: Number(found[3]) };
  };

  return {
    ui,
    seen,
    camera,
    /** Schematic world units → the client point a pointer would have to be at. */
    at(x, y) {
      const { tx, ty, scale } = camera();
      const svg = schematicToSvg(transform, x, y);
      return { x: svg.x * scale + tx, y: svg.y * scale + ty };
    },
    finish() {
      globalThis.fetch = realFetch;
      assert.deepEqual(ui.errors, [], "a handler threw where only the console would have seen it");
      ui.unmount();
    },
  };
}

test("a left drag pans the sheet by exactly the pointer travel", async () => {
  const sheet = await openSheet();
  const before = sheet.camera();

  pointer(sheet.ui.root, "down", { clientX: 500, clientY: 300 });
  pointer(sheet.ui.root, "move", { clientX: 620, clientY: 255 });
  pointer(sheet.ui.root, "up", { clientX: 620, clientY: 255 });

  assert.deepEqual(sheet.camera(), { tx: before.tx + 120, ty: before.ty - 45, scale: before.scale });
  assert.deepEqual(sheet.seen.select, [], "a drag is not a click");
  sheet.finish();
});

test("a right drag pans the sheet the same way — the gesture crosses the splitter unchanged", async () => {
  const sheet = await openSheet();
  const before = sheet.camera();

  pointer(sheet.ui.root, "down", { clientX: 500, clientY: 300, button: 2 });
  pointer(sheet.ui.root, "move", { clientX: 620, clientY: 255, button: 2 });
  pointer(sheet.ui.root, "up", { clientX: 620, clientY: 255, button: 2 });

  assert.deepEqual(sheet.camera(), { tx: before.tx + 120, ty: before.ty - 45, scale: before.scale });
  assert.deepEqual(sheet.seen.menus, [], "a right drag is a pan, not a menu");
  sheet.finish();
});

test("a right press on the sheet that does not travel asks for the context menu and moves nothing", async () => {
  const sheet = await openSheet();
  const before = sheet.camera();
  const on = sheet.at(R30.x, R30.y);

  pointer(sheet.ui.root, "down", { clientX: on.x, clientY: on.y, button: 2 });
  menu(sheet.ui.root, { clientX: on.x, clientY: on.y });
  pointer(sheet.ui.root, "up", { clientX: on.x, clientY: on.y, button: 2 });

  assert.equal(sheet.seen.menus.length, 1);
  const request = sheet.seen.menus[0];
  assert.equal(request.source, "schematic");
  assert.deepEqual(request.client, { x: on.x, y: on.y });
  assert.equal(request.hit.kind, "component");
  assert.equal(request.hit.key, R30.key);
  assert.equal(request.hit.label, "R30");
  assert.deepEqual(sheet.camera(), before, "a right click panned the sheet");
  // Altium leaves the selection alone on a right click, and so do we.
  assert.deepEqual(sheet.seen.select, []);
  sheet.finish();
});

test("a right press on the sheet that travels pans and asks for no menu", async () => {
  const sheet = await openSheet();
  const before = sheet.camera();
  const on = sheet.at(R30.x, R30.y);

  pointer(sheet.ui.root, "down", { clientX: on.x, clientY: on.y, button: 2 });
  pointer(sheet.ui.root, "move", { clientX: on.x + 80, clientY: on.y + 30, button: 2 });
  pointer(sheet.ui.root, "up", { clientX: on.x + 80, clientY: on.y + 30, button: 2 });

  assert.deepEqual(sheet.camera(), { tx: before.tx + 80, ty: before.ty + 30, scale: before.scale });
  assert.deepEqual(sheet.seen.menus, [], "the pan ended in a context menu");
  sheet.finish();
});

test("a click selects the symbol under it and leaves the sheet where it was", async () => {
  const sheet = await openSheet();
  const before = sheet.camera();
  const on = sheet.at(R30.x, R30.y);

  // 2 px of travel: inside the 4 px slop, so this is still a click.
  pointer(sheet.ui.root, "down", { clientX: on.x, clientY: on.y });
  pointer(sheet.ui.root, "move", { clientX: on.x + 2, clientY: on.y });
  pointer(sheet.ui.root, "up", { clientX: on.x + 2, clientY: on.y });

  assert.deepEqual(sheet.seen.select, [
    [{ kind: "component", key: R30.key }, { jump: false, source: "schematic" }],
  ]);
  assert.deepEqual(sheet.camera(), before, "a click moved the sheet");
  sheet.finish();
});

test("⌘-click selects the same symbol and asks the other pane to jump to it", async () => {
  const sheet = await openSheet();
  const on = sheet.at(R30.x, R30.y);

  pointer(sheet.ui.root, "down", { clientX: on.x, clientY: on.y, metaKey: true });
  pointer(sheet.ui.root, "up", { clientX: on.x, clientY: on.y, metaKey: true });

  assert.deepEqual(sheet.seen.select, [
    [{ kind: "component", key: R30.key }, { jump: true, source: "schematic" }],
  ]);
  sheet.finish();
});
