import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildBoardIndex, elementId } from "../../../lib/boardIndex.js";
import { formatPoint } from "../../../lib/boardRender.js";
import { bindPlacements, parseBoardSource } from "../boardSource.js";
import {
  BOARD_CONTEXT_ACTIONS,
  boardContextMenu,
  findItem,
  flattenItems,
  mmToOffset,
  offsetToMm,
  targetForHit,
  violationsAt,
} from "../boardContextMenu.js";

// The real harness-puck board, for the same reason placementDrag.test.js uses
// it: a synthetic index would agree with the item rules by construction. This
// one has 17 placements the board file owns and 44 components it does not, so
// "enabled" and "disabled with a reason" are both read off real geometry.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const TSX = path.join(REPO, "examples/harness-puck/boards/main.tsx");
const JSON_PATH = path.join(REPO, "examples/harness-puck/boards/main.circuit.json");
const hasExamples = fs.existsSync(TSX) && fs.existsSync(JSON_PATH);

function load() {
  const index = buildBoardIndex(JSON.parse(fs.readFileSync(JSON_PATH, "utf8")));
  const parsed = parseBoardSource(fs.readFileSync(TSX, "utf8"));
  return { index, placements: bindPlacements(parsed.placements, index) };
}

/** A hit shaped like the one the pointer's hit-test returns for a whole part. */
const componentHit = (component) => ({
  elementId: component.pcbId,
  element: component.pcb,
  componentKey: component.key,
  netKey: "",
  layer: component.layer,
});

const traceHit = (index, trace) => ({
  elementId: elementId(trace),
  element: trace,
  componentKey: "",
  netKey: index.netKeyByElementId.get(elementId(trace)) || "",
  layer: String(trace.route?.[0]?.layer || "top"),
});

/** Move mode on, file read, latest build — the state every write item wants. */
const OPEN = { canEdit: true, viewing: false, editor: { ready: true, state: "ready", reason: "" } };

const byRefdes = (index, refdes) => index.components.find((one) => one.refdes === refdes);

/** The same binding with one placement locked, so the lock row can be read in
 *  both states without writing to a real board file. */
function withLocked(placements, placementId) {
  const byId = new Map(placements.byId);
  byId.set(placementId, { ...byId.get(placementId), locked: true });
  return { ...placements, byId };
}

test("a bound part offers every read and every write, and the header echoes the point", { skip: !hasExamples }, () => {
  const { index, placements } = load();
  const hit = componentHit(byRefdes(index, "J1"));
  const menu = boardContextMenu({
    ...OPEN,
    hit,
    index,
    placements,
    point: { x: 0, y: -29 },
    pointLabel: "0.000, -29.000 mm",
  });

  assert.equal(menu.header.label, "J1");
  // Echoed verbatim, never recomputed: the menu and the HUD cannot print two
  // different numbers for one point.
  assert.match(menu.header.detail, /0\.000, -29\.000 mm$/);

  const lock = findItem(menu, "lock");
  assert.equal(lock.disabled, false);
  assert.equal(lock.label, "Lock J1 +6 in place");
  assert.equal(lock.writes, true);
  assert.equal(lock.payload.locked, true);
  assert.equal(lock.payload.placementId, "UsbCData[1]");

  assert.equal(findItem(menu, "move-exact").disabled, false);
  assert.equal(findItem(menu, "ask-agent").disabled, false);
  assert.equal(findItem(menu, "properties").disabled, false);
  assert.equal(findItem(menu, "show-in-schematic").disabled, false);
  assert.ok(findItem(menu, "zoom-here"), "a part has a box to zoom to");
  // No net under a whole-part hit, so no net row rather than a dead one.
  assert.equal(findItem(menu, "select-net"), null);
});

test("the lock row keeps its id and changes only its label", { skip: !hasExamples }, () => {
  const { index, placements } = load();
  const hit = componentHit(byRefdes(index, "J1"));
  const locked = boardContextMenu({ ...OPEN, hit, index, placements: withLocked(placements, "UsbCData[1]") });
  const row = findItem(locked, "lock");
  assert.equal(row.label, "Unlock J1 +6");
  assert.equal(row.payload.locked, false);
  // A locked part can always be unlocked — that refusal is the lock working,
  // not a limit of what the board file can say.
  assert.equal(row.disabled, false);
  // But it still cannot be moved, and the row says so instead of going quiet.
  const move = findItem(locked, "move-exact");
  assert.equal(move.disabled, true);
  assert.match(move.reason, /locked/);
});

test("a part the board file does not place says which block does, and hands off", { skip: !hasExamples }, () => {
  const { index, placements } = load();
  // R30 is inside a golden block. Editing it would mean editing a file other
  // boards share, so the two write rows refuse.
  const menu = boardContextMenu({ ...OPEN, hit: componentHit(byRefdes(index, "R30")), index, placements });

  for (const id of ["lock", "move-exact"]) {
    const row = findItem(menu, id);
    assert.equal(row.disabled, true, `${id} must refuse`);
    assert.match(row.reason, /block/, `${id} must name the block`);
  }
  // The pairing IS the never-silently-discard rule: a refused write always
  // leaves the chat enabled in the same group.
  const ask = findItem(menu, "ask-agent");
  assert.equal(ask.disabled, false);
  assert.equal(ask.action, "prefill");
  assert.match(ask.label, /R30/);
  // And every read is untouched by the refusal.
  assert.equal(findItem(menu, "properties").disabled, false);
});

test("copper refuses with the router's reason, and still offers its net", { skip: !hasExamples }, () => {
  const { index, placements } = load();
  const trace = index.pcbDrawables.find((one) => one.type === "pcb_trace");
  const hit = traceHit(index, trace);
  const menu = boardContextMenu({ ...OPEN, hit, index, placements });

  const lock = findItem(menu, "lock");
  assert.equal(lock.disabled, true);
  assert.match(lock.reason, /router drew this copper/);
  const net = findItem(menu, "select-net");
  assert.ok(net, "a trace carries a net, and selecting it is the point of clicking one");
  assert.equal(net.payload.target.kind, "net");
  assert.equal(net.disabled, false);
});

test("an older build disables every write and leaves every read alone", { skip: !hasExamples }, () => {
  const { index, placements } = load();
  const menu = boardContextMenu({
    hit: componentHit(byRefdes(index, "J1")),
    index,
    placements,
    canEdit: false,
    viewing: true,
    editor: { ready: false, state: "idle", reason: "" },
  });
  for (const item of flattenItems(menu)) {
    if (item.writes) {
      assert.equal(item.disabled, true, `${item.id} writes and must refuse`);
      assert.match(item.reason, /older build/);
    } else {
      assert.equal(item.disabled, false, `${item.id} only reads and must stay live`);
    }
  }
  // No way back into move mode from an old revision, so no row pretending there is.
  assert.equal(findItem(menu, "edit-mode"), null);
});

test("with move mode off the reason is the mode, not the part", { skip: !hasExamples }, () => {
  const { index, placements } = load();
  // The trap this asserts: with move mode off the editor holds no placements,
  // so asking the part first would answer "no line in the board file puts this
  // here" about a part the file plainly places.
  const menu = boardContextMenu({
    hit: componentHit(byRefdes(index, "J1")),
    index,
    placements: { byId: new Map(), byComponentKey: new Map(), byElementId: new Map(), unmatched: [] },
    canEdit: false,
    viewing: false,
    editor: { ready: false, state: "idle", reason: "" },
  });
  const lock = findItem(menu, "lock");
  assert.equal(lock.disabled, true);
  assert.match(lock.reason, /Move parts is off/);
  // And the one click out of that state is in the same group.
  const mode = findItem(menu, "edit-mode");
  assert.equal(mode.disabled, false);
  assert.equal(mode.action, "toggle-edit");
  assert.equal(mode.payload.on, true);
});

test("empty board gets the design-space menu, and nothing on it writes", () => {
  const menu = boardContextMenu({ hit: null, pointLabel: "3.000, 4.000 mm", showGrid: false, units: "mil" });
  const ids = flattenItems(menu).map((item) => item.id);
  assert.deepEqual(ids, ["fit", "grid", "units", "edit-mode", "ask-agent-board"]);
  assert.equal(
    flattenItems(menu).some((item) => item.writes),
    false,
  );
  assert.equal(menu.header.detail, "3.000, 4.000 mm");
  // Imperative, not a state readout: a row labelled "Grid: on" that turns the
  // grid off is a label promising the opposite of what the click does.
  assert.equal(findItem(menu, "grid").label, "Show the grid");
  assert.equal(findItem(menu, "units").label, "Show sizes in mm");
  const other = boardContextMenu({ hit: null, showGrid: true, units: "mm" });
  assert.equal(findItem(other, "grid").label, "Hide the grid");
  assert.equal(findItem(other, "units").label, "Show sizes in mil");
  assert.equal(findItem(other, "edit-mode").label, "Turn on Move parts");

  // Clearing a highlight that does not exist is not an offer worth making.
  const selected = boardContextMenu({ hit: null, selection: { kind: "net", key: "V3_3" } });
  assert.ok(findItem(selected, "clear-highlight"));
});

test("objects-here appears only when there is a choice to make", { skip: !hasExamples }, () => {
  const { index, placements } = load();
  const part = byRefdes(index, "J1");
  const trace = index.pcbDrawables.find((one) => one.type === "pcb_trace");
  const one = componentHit(part);
  const two = traceHit(index, trace);

  assert.equal(findItem(boardContextMenu({ ...OPEN, hit: one, hits: [one], index, placements }), "objects-here"), null);

  const menu = boardContextMenu({ ...OPEN, hit: two, hits: [two, one], index, placements });
  const submenu = findItem(menu, "objects-here");
  assert.equal(submenu.label, "Objects under the cursor (2)");
  assert.equal(submenu.children.length, 2);
  // Ranked order is the caller's; the menu does not re-sort it.
  assert.equal(submenu.children[0].payload.target.kind, "net");
  assert.equal(submenu.children[1].payload.target.key, part.key);
  for (const child of submenu.children) assert.equal(child.action, "select");
});

test("only the findings under the point appear, in the order they arrived", () => {
  const rows = [
    { id: "e1", severity: "error", kind: "hole_clearance", part: "U3", detail: "[hole_clearance] too close", box: { minX: 0, minY: 0, maxX: 10, maxY: 10 } },
    { id: "w1", severity: "warning", kind: "text_height", part: "R1", detail: "[text_height] small", box: { minX: 5, minY: 5, maxX: 20, maxY: 20 } },
    { id: "w2", severity: "warning", kind: "silk_over_copper", part: "R9", detail: "elsewhere", box: { minX: 90, minY: 90, maxX: 99, maxY: 99 } },
  ];
  assert.deepEqual(
    violationsAt(rows, { x: 7, y: 7 }).map((row) => row.id),
    ["e1", "w1"],
  );

  const menu = boardContextMenu({
    ...OPEN,
    hit: { elementId: "pcb_smtpad_1", element: { type: "pcb_smtpad" }, componentKey: "", netKey: "GND", layer: "top" },
    point: { x: 7, y: 7 },
    messageRows: rows,
  });
  const submenu = findItem(menu, "violations");
  assert.equal(submenu.label, "Violations here (2)");
  // Two findings plus the handoff. Error first, because buildMessages already
  // sorted by severity and the filter keeps that order.
  assert.deepEqual(
    submenu.children.map((child) => child.action),
    ["locate", "locate", "prefill"],
  );
  assert.equal(submenu.children[0].payload.row.id, "e1");
  assert.equal(submenu.children[2].payload.text, "U3 (hole_clearance): ");

  // Nothing flagged here → no row. "Violations (0)" is a worse answer than none.
  const clean = boardContextMenu({ ...OPEN, hit: { elementId: "x", element: { type: "pcb_via" }, netKey: "GND" }, point: { x: 50, y: 50 }, messageRows: rows });
  assert.equal(findItem(clean, "violations"), null);
});

test("no enabled row is inert, and no disabled row is a mystery", { skip: !hasExamples }, () => {
  const { index, placements } = load();
  const trace = index.pcbDrawables.find((one) => one.type === "pcb_trace");
  const states = [
    { ...OPEN, hit: componentHit(byRefdes(index, "J1")), index, placements },
    { ...OPEN, hit: componentHit(byRefdes(index, "R30")), index, placements },
    { ...OPEN, hit: traceHit(index, trace), index, placements },
    { hit: componentHit(byRefdes(index, "J1")), index, placements, canEdit: false, viewing: true, editor: null },
    { hit: componentHit(byRefdes(index, "J1")), index, placements, canEdit: false, editor: { ready: false } },
    { hit: null },
    { hit: null, selection: { kind: "component", key: "source_component_0" }, viewing: true },
  ];
  for (const ctx of states) {
    const menu = boardContextMenu(ctx);
    const items = flattenItems(menu);
    const ids = items.map((item) => item.id);
    assert.equal(new Set(ids).size, ids.length, `duplicate row id in ${JSON.stringify(ids)}`);
    for (const item of items) {
      assert.ok(item.label, `${item.id} needs a label`);
      if (item.children) {
        assert.ok(item.children.length, `${item.id} is an empty submenu`);
        continue;
      }
      if (item.disabled) {
        // The whole point of the disabled state: a reason, in plain words.
        assert.ok(item.reason, `${item.id} is disabled with no reason`);
      } else {
        // And the whole point of the enabled state: it does something. This is
        // the assertion that stops us shipping WindowMenuBar's Undo again.
        assert.ok(BOARD_CONTEXT_ACTIONS.has(item.action), `${item.id} is enabled and does nothing`);
      }
    }
  }
});

test("nothing offers an edit the board file cannot express yet", { skip: !hasExamples }, () => {
  // boardSource.js writes pcbX/pcbY literals and the lock comment. There is no
  // rotateEdits, no flipEdits and no way to delete an element, so those rows
  // are absent and the intent rides on ask-agent. Delete this the day those
  // land, and not before.
  const { index, placements } = load();
  const menu = boardContextMenu({ ...OPEN, hit: componentHit(byRefdes(index, "J1")), index, placements });
  for (const item of flattenItems(menu)) {
    assert.doesNotMatch(item.id, /rotate|flip|mirror|delete|align/i, `${item.id} has no write path behind it`);
  }
});

test("targetForHit selects a part first and a net second, and nothing else", () => {
  assert.deepEqual(targetForHit({ componentKey: "c1", netKey: "n1" }), { kind: "component", key: "c1" });
  assert.deepEqual(targetForHit({ componentKey: "", netKey: "n1" }), { kind: "net", key: "n1" });
  // A bare silkscreen stroke is not selectable, so it gets no Properties row
  // rather than one that lights up and does nothing.
  assert.equal(targetForHit({ componentKey: "", netKey: "" }), null);
  assert.equal(targetForHit(null), null);
});

test("a typed offset is taken exactly, in whichever unit the field is showing", () => {
  assert.equal(offsetToMm("0.35", "mm"), 0.35);
  assert.equal(offsetToMm(" -2 ", "mm"), -2);
  assert.equal(offsetToMm("100", "mil"), 2.54);
  assert.equal(mmToOffset(2.54, "mil"), 100);
  assert.equal(mmToOffset(2.54, "mm"), 2.54);
  // Junk is null, never 0: moving a part by "abc" millimetres must not quietly
  // move it by nothing and report success.
  assert.equal(offsetToMm("", "mm"), null);
  assert.equal(offsetToMm("abc", "mm"), null);
  assert.equal(offsetToMm(null, "mm"), null);
});

// The emitter's payload is `{point, hit, placement, client, source}`. When
// `pointLabel` was the caller's job, the header lost the coordinate and the
// ask-the-agent prefill shipped as "J1 at : ".
test("the coordinate is derived from the point, not left to the caller", () => {
  const menu = boardContextMenu({ hit: null, point: { x: 3, y: 4 }, units: "mm" });
  assert.equal(menu.header.detail, "3.000, 4.000 mm");
  assert.equal(findItem(menu, "ask-agent-board").payload.text, "This board at 3.000, 4.000 mm: ");
  // mil is the same number through the same formatter the HUD uses.
  const mil = boardContextMenu({ hit: null, point: { x: 2.54, y: 0 }, units: "mil" });
  assert.equal(mil.header.detail, formatPoint(2.54, 0, "mil"));
  // An explicit label still wins, and no point at all is not a crash.
  assert.equal(boardContextMenu({ hit: null, point: { x: 3, y: 4 }, pointLabel: "here" }).header.detail, "here");
  assert.equal(boardContextMenu({ hit: null }).header.detail, "");
});
