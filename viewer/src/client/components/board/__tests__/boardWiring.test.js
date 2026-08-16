// boardWiring — the props that have to be connected for a gesture to exist.
//
// Every defect this file guards was the same shape: a module did its half
// correctly, shipped, and nothing happened, because the two halves were never
// joined. Both canvases emitted `onContextMenuRequest` and the workspace passed
// it to neither, so right-click was a guaranteed dead click. `PcbCanvas`
// consumed `onPlacementRotate` and nobody supplied one, so Space during a drag
// printed "not wired up yet" three inches from a working rotate button.
//
// None of that was catchable by the existing suite: the runner is bare
// `node:test` with no DOM, so nothing imports a `.jsx` component and every one
// of those defects survives its own test file. Reading the call site is the
// derivation that is available — the same technique `shortcutSheet.test.js`
// uses on the handlers it cannot call.
//
// This is a floor, not a substitute for a render test. It proves the prop is
// written at the call site; it cannot prove the value behind it is the right
// one. That is why the dispatch scan (boardDispatch.test.js) exists beside it.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (file) => fs.readFileSync(path.join(here, "..", file), "utf8");

/** The text of one JSX element's opening tag, from `<Name` to its `>`. */
function openingTag(source, name) {
  const at = source.indexOf(`<${name}`);
  assert.notEqual(at, -1, `${name} is never rendered`);
  const end = source.indexOf("\n        />", at);
  const close = source.indexOf(">", at);
  return source.slice(at, end === -1 ? close + 1 : end);
}

test("both canvases can actually deliver a right-click to the menu", () => {
  const workspace = read("BoardWorkspace.jsx");
  for (const canvas of ["PcbCanvas", "SchematicCanvas"]) {
    assert.match(
      openingTag(workspace, canvas),
      /onContextMenuRequest=/u,
      `${canvas} emits onContextMenuRequest and the workspace ignores it — right-click is a dead click`,
    );
  }
  // …and something is mounted to receive it.
  assert.match(workspace, /<BoardContextMenu/u, "the menu is never rendered");
  assert.match(workspace, /import BoardContextMenu from/u);
});

test("Space during a drag reaches the same rotate command the buttons use", () => {
  const workspace = read("BoardWorkspace.jsx");
  assert.match(
    openingTag(workspace, "PcbCanvas"),
    /onPlacementRotate=/u,
    "the canvas prints a refusal instead of turning when this is missing",
  );
  assert.match(openingTag(workspace, "PropertiesPanel"), /onPlacementRotate=/u);
  // One producer of a turn, three surfaces. Every one of them either builds
  // the command with `commitRotateStep` or hands the gesture to the workspace
  // handler that does — nobody works out an angle for themselves. Two
  // producers is how a keyboard and a panel come to disagree about which way
  // is which, and the disagreement lands in a file a fab eventually reads.
  for (const file of ["BoardWorkspace.jsx", "PlacementEditBar.jsx"]) {
    assert.match(read(file), /commitRotateStep/u, `${file} must build its turn with the shared command`);
  }
  assert.match(read("PropertiesPanel.jsx"), /onPlacementRotate\?\.\(/u, "Properties delegates rather than duplicating");

  // Nobody outside placementRotate.js does angle arithmetic. `normalizeDeg`
  // and the ±step live in exactly one file so "which way is counterclockwise"
  // is answered once.
  for (const file of ["PlacementEditBar.jsx", "PropertiesPanel.jsx", "BoardWorkspace.jsx", "PcbCanvas.jsx"]) {
    assert.doesNotMatch(
      read(file),
      /rotation\s*[+-]\s*(?:step|turnBy|90)/u,
      `${file} works out an angle of its own instead of asking placementRotate`,
    );
  }
});

test("the shortcut sheet is mounted and reachable from the menu bar", () => {
  assert.match(read("BoardWorkspace.jsx"), /<ShortcutSheetHost/u, "the sheet has no mount, so no key opens it");
  const bar = read("../WindowMenuBar.jsx");
  assert.match(bar, /OPEN_SHORTCUT_SHEET_EVENT/u, "the Help menu cannot open the sheet");
  assert.match(bar, /Keyboard Shortcuts/u);
});

test("the delta origin is reachable without an Insert key", () => {
  // Space was handed to rotation, and a MacBook keyboard has no Insert. With
  // no click path the Δ column is frozen at 0,0 for half the people using it.
  assert.match(read("BoardWorkspace.jsx"), /onResetDelta=/u);
  assert.match(read("BoardInsightHud.jsx"), /data-slot="hud-delta-reset"/u);
  assert.match(read("PcbCanvas.jsx"), /resetDelta:/u);
});

test("the right button still pans, and a right-click can still escape a command", () => {
  for (const file of ["PcbCanvas.jsx", "SchematicCanvas.jsx"]) {
    const source = read(file);
    // Either guard is fine — what is not fine is deciding it inline. Both
    // canvases must ask the shared arbiter which button means what, or they
    // drift apart, which is exactly how PcbCanvas kept `event.button !== 0`
    // for a day after SchematicCanvas had been fixed and the whole
    // right-button surface was missing on the canvas people actually use.
    assert.match(
      source,
      /isDragButton\(event\.button\)|pointerPressAction\(\{\s*button: event\.button/u,
      `${file} must route the button through canvasPointer so button 2 can pan`,
    );
    assert.doesNotMatch(source, /event\.button !== 0/u, `${file} rejects the right button before pan sees it`);
  }
  // `pointerdown` never fires for a second button, so the escape has to hang
  // off `contextmenu`. See canvasPointer.escapeLiveCommand.
  assert.match(read("PcbCanvas.jsx"), /escapeLiveCommand\(dragRef\.current\)/u);
});
