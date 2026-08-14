import assert from "node:assert/strict";
import test from "node:test";

import {
  componentCenter,
  placementContextNote,
  placementDirection,
  placementRequestText,
  previewComponentPlacement,
  nudgePlacementPoint,
  snapPlacementPoint,
  translateBox,
} from "./boardPlacement.js";

function component(key, refdes, x, y, netKeys = [], layer = "top") {
  return {
    key,
    refdes,
    groupId: `${key}_group`,
    layer,
    pcb: { center: { x, y }, layer, rotation: 0 },
    pcbBox: { minX: x - 0.5, minY: y - 0.5, maxX: x + 0.5, maxY: y + 0.5 },
    pcbElementIds: [`${key}_pad_1`, `${key}_silk`],
    netKeys: new Set(netKeys),
  };
}

function index() {
  const u1 = component("sc_u1", "U1", 0, 0, ["signal", "gnd"]);
  const r1 = component("sc_r1", "R1", 2, 0, ["signal"]);
  const c1 = component("sc_c1", "C1", 0, 3, ["gnd"]);
  return {
    components: [u1, r1, c1],
    componentBySourceId: new Map([
      [u1.key, u1],
      [r1.key, r1],
      [c1.key, c1],
    ]),
    netByKey: new Map([
      ["signal", { key: "signal", name: "USB_DP", componentKeys: new Set([u1.key, r1.key]) }],
      ["gnd", { key: "gnd", name: "GND", isGround: true, componentKeys: new Set([u1.key, c1.key]) }],
    ]),
    boardBox: { minX: -5, minY: -5, maxX: 5, maxY: 5 },
  };
}

test("placement coordinates snap to the explicit edit grid", () => {
  assert.deepEqual(snapPlacementPoint({ x: 1.13, y: -0.88 }, 0.25), { x: 1.25, y: -1 });
  assert.deepEqual(componentCenter(index().components[0]), { x: 0, y: 0 });
  assert.equal(componentCenter({}), null);
});

test("arrow nudges use one grid step and Shift-style nudges use ten", () => {
  assert.deepEqual(nudgePlacementPoint({ x: 1, y: 2 }, "ArrowLeft"), { x: 0.75, y: 2 });
  assert.deepEqual(
    nudgePlacementPoint({ x: 1, y: 2 }, "ArrowUp", { steps: 10 }),
    { x: 1, y: 4.5 },
  );
});

test("preview translates the footprint, keeps stable identity and draws signal ratlines first", () => {
  const preview = previewComponentPlacement(index(), "sc_u1", { x: 1.13, y: 1.12 });
  assert.ok(preview);
  assert.deepEqual(preview.center, { x: 1.25, y: 1 });
  assert.deepEqual(preview.delta, { x: 1.25, y: 1 });
  assert.deepEqual(preview.movedBox, { minX: 0.75, minY: 0.5, maxX: 1.75, maxY: 1.5 });
  assert.equal(preview.componentKey, "sc_u1");
  assert.ok(preview.elementIds.has("sc_u1_pad_1"));
  assert.equal(preview.ratlines[0].netName, "USB_DP", "signal relationships precede power-plane noise");
  assert.equal(preview.ratlines[1].netName, "GND");
});

test("preview warns about nearby footprints and board-edge crossings without pretending to run DRC", () => {
  const near = previewComponentPlacement(index(), "sc_u1", { x: 1, y: 0 });
  assert.deepEqual(near.nearby.map((item) => item.refdes), ["R1"]);
  assert.equal(near.outsideBoard, false);

  const outside = previewComponentPlacement(index(), "sc_u1", { x: 5, y: 0 });
  assert.equal(outside.outsideBoard, true);
  assert.equal(previewComponentPlacement(index(), "missing", { x: 0, y: 0 }), null);
});

test("opposite-side bodies may share XY without a false placement warning", () => {
  const idx = index();
  const back = component("sc_back", "U2", 1, 0, [], "bottom");
  idx.components.push(back);
  idx.componentBySourceId.set(back.key, back);

  const preview = previewComponentPlacement(idx, "sc_u1", { x: 1, y: 0 });
  assert.deepEqual(preview.nearby.map((item) => item.refdes), ["R1"]);
});

test("direction text makes left/right nudges readable", () => {
  assert.equal(placementDirection({ x: 0.25, y: 0 }), "0.250 mm right");
  assert.equal(placementDirection({ x: -0.5, y: 0 }), "0.500 mm left");
  assert.equal(placementDirection({ x: 0, y: 0.25 }), "0.250 mm up");
  assert.equal(placementDirection({ x: 0.25, y: -0.5 }), "by Δx +0.250 mm, Δy -0.500 mm");
});

test("the staged request names source-of-truth, rebuild and stable component id", () => {
  const preview = previewComponentPlacement(index(), "sc_u1", { x: 0.25, y: 0 });
  const request = placementRequestText(preview, { board: "main" });
  assert.match(request, /Move U1 0\.250 mm right on board main/);
  assert.match(request, /board TSX\/source placement, not generated circuit\.json/);
  assert.match(request, /rerun DRC and fabrication verification/);

  const context = placementContextNote(preview, { board: "main" });
  assert.match(context, /source_component_id=sc_u1/);
  assert.match(context, /group_id=sc_u1_group/);
  assert.match(context, /requested_center_mm=0\.250,0\.000/);
});

test("translateBox preserves unreal boxes and shifts real ones", () => {
  const unreal = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  assert.equal(translateBox(unreal, { x: 1, y: 1 }), unreal);
  assert.deepEqual(
    translateBox({ minX: 0, minY: 1, maxX: 2, maxY: 3 }, { x: -1, y: 2 }),
    { minX: -1, minY: 3, maxX: 1, maxY: 5 },
  );
});
