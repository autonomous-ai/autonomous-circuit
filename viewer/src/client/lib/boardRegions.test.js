import assert from "node:assert/strict";
import test from "node:test";

import { buildBoardIndex, boxIsReal } from "./boardIndex.js";
import {
  MERGE_THRESHOLD,
  ROOM_MAX_BOARD_FRACTION,
  boardRegions,
  boxArea,
  drawableRegions,
  groupSignature,
  leadComponent,
  roomOverlay,
} from "./boardRegions.js";
import { fixtureBoard } from "./boardFixture.test-helper.js";

const index = () => buildBoardIndex(fixtureBoard());
const byLabel = (regions, label) => regions.filter((region) => region.label === label);

test("the index carries every group a part belongs to, with its anchor and box", () => {
  const idx = index();
  const usb = idx.groupById.get("g_usb");
  assert.ok(usb, "g_usb is indexed");
  assert.deepEqual(usb.anchor, { x: -14, y: -10 });
  assert.deepEqual(usb.componentKeys.sort(), ["sc_j1", "sc_r3"]);
  assert.equal(boxIsReal(usb.pcbBox), true);
  // The board's own group is the one nothing contains — board-file glue lands
  // there, and telling it apart from a block's group is the whole point.
  assert.equal(idx.groupById.get("g_root").isRoot, true);
  assert.equal(idx.groupById.get("g_usb").isRoot, false);
});

test("a component knows its pins by name, and which net each one is on", () => {
  const brain = index().componentBySourceId.get("sc_u3");
  assert.equal(brain.ports.length, 8);
  const netKey = [...brain.netKeys].find((key) => key.endsWith("SIG_LED"));
  assert.deepEqual(brain.portNamesByNetKey.get(netKey), ["GPIO0"]);
});

test("a region is named after what is inside it, not after a guessed block name", () => {
  const regions = boardRegions(index());
  const labels = regions.map((region) => region.label);
  assert.ok(labels.includes("The brain"), labels.join(","));
  assert.ok(labels.includes("Power in"));
  assert.ok(labels.includes("Power supply"));
  assert.ok(labels.includes("Sensor"));
  // Two lights, wired completely differently, are still two areas.
  assert.equal(byLabel(regions, "Light").length, 2);
});

test("the brain's area is named for the RP2040 in it and carries its whole part list", () => {
  const brain = boardRegions(index()).find((region) => region.label === "The brain");
  assert.match(brain.detail, /RP2040/);
  assert.deepEqual(brain.refdes, ["U3", "U4"]);
  assert.equal(brain.fromBoardFile, false);
});

test("three identical siblings read as one repeated area, with a count", () => {
  const regions = boardRegions(index());
  const buttons = regions.filter((region) => region.role === "control");
  assert.equal(buttons.length, 1, "three identical button groups merge into one");
  assert.equal(buttons[0].instances, 3);
  assert.equal(buttons[0].label, "Buttons");
  assert.deepEqual(buttons[0].refdes, ["SW1", "SW2", "SW3"]);
  // …and the merged box spans all three, so the room drawn on the canvas
  // actually covers the parts it names.
  assert.ok(buttons[0].box.minX <= -15 && buttons[0].box.maxX >= -1, JSON.stringify(buttons[0].box));
});

test("two of a thing stay two things — the threshold is three", () => {
  assert.equal(MERGE_THRESHOLD, 3);
  const lights = byLabel(boardRegions(index()), "Light");
  assert.equal(lights.length, 2);
  assert.equal(lights[0].instances, 1);
});

test("parts written into the board file are marked as such", () => {
  const glue = boardRegions(index()).find((region) => region.fromBoardFile);
  assert.ok(glue, "the board's own group becomes an area");
  assert.deepEqual(glue.refdes, ["R30"]);
});

test("an empty or absent index yields no regions rather than throwing", () => {
  assert.deepEqual(boardRegions(null), []);
  assert.deepEqual(boardRegions(buildBoardIndex([])), []);
});

test("leadComponent prefers the loudest role, then the part with a real MPN", () => {
  const idx = index();
  const brain = idx.componentBySourceId.get("sc_u3");
  const cap = idx.componentBySourceId.get("sc_c2");
  assert.equal(leadComponent([cap, brain]).refdes, "U3");
  assert.equal(leadComponent([]), null);
});

test("the signature ignores order, so sibling groups compare on content alone", () => {
  const idx = index();
  const a = idx.componentBySourceId.get("sc_led1");
  const b = idx.componentBySourceId.get("sc_r20");
  assert.equal(groupSignature([a, b]), groupSignature([b, a]));
});

test("boxArea is zero for a box nothing grew", () => {
  assert.equal(boxArea(null), 0);
  assert.equal(boxArea({ minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity }), 0);
  assert.equal(boxArea({ minX: 0, minY: 0, maxX: 2, maxY: 3 }), 6);
});

// --- the drawn overlay ------------------------------------------------------

const VIEW = { scale: 10, tx: 200, ty: 200 };

test("rooms are painted largest first so a small label is never buried", () => {
  const rooms = roomOverlay(boardRegions(index()), { view: VIEW, boardBox: null });
  const areas = rooms.map((room) => room.area);
  assert.deepEqual(areas, [...areas].sort((a, b) => b - a));
});

test("a room covering essentially the whole board is dropped, not drawn as a frame", () => {
  const regions = boardRegions(index());
  const board = { minX: -20, minY: -15, maxX: 20, maxY: 15 };
  const whole = {
    id: "everything",
    label: "Parts",
    instances: 1,
    box: { minX: -19.9, minY: -14.9, maxX: 19.9, maxY: 14.9 },
  };
  const drawn = roomOverlay([...regions, whole], { view: VIEW, boardBox: board });
  assert.equal(drawn.some((room) => room.id === "everything"), false);
  // …and the fraction is a real threshold, not "anything large".
  assert.ok(ROOM_MAX_BOARD_FRACTION > 0.5 && ROOM_MAX_BOARD_FRACTION < 1);
  assert.ok(drawn.length > 0, "the ordinary rooms still draw");
});

test("a room too small for its label keeps the outline and loses the text", () => {
  const tiny = { id: "t", label: "Light", instances: 1, box: { minX: 0, minY: 0, maxX: 0.2, maxY: 0.2 } };
  const [room] = roomOverlay([tiny], { view: { scale: 2, tx: 0, ty: 0 }, boardBox: null });
  assert.equal(room.showLabel, false);
  assert.ok(room.rect.width > 0, "the outline is still there");
});

test("a merged room carries its count into the label", () => {
  const rooms = roomOverlay(boardRegions(index()), { view: VIEW, boardBox: null });
  assert.ok(rooms.some((room) => room.label === "Buttons ×3"), rooms.map((r) => r.label).join(","));
});

test("no regions, no view, no boxes — nothing to draw and nothing thrown", () => {
  assert.deepEqual(roomOverlay([], { view: VIEW }), []);
  assert.deepEqual(roomOverlay(boardRegions(index()), {}), []);
  assert.deepEqual(drawableRegions([{ box: null }]), []);
});
