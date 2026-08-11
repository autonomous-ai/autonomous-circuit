import assert from "node:assert/strict";
import test from "node:test";

import {
  activeBoardSide,
  BOARD_SIDES,
  boardSideChange,
  dispatchViewportTool,
  toolState,
  toolsForSurface,
  VIEWPORT_TOOLS,
  ZOOM_STEP,
} from "../viewportTools.js";

test("every tool has a stable id, a group and a state kind", () => {
  const ids = VIEWPORT_TOOLS.map((tool) => tool.id);
  assert.equal(new Set(ids).size, ids.length, "tool ids must be unique");
  for (const tool of VIEWPORT_TOOLS) {
    assert.ok(tool.label, `${tool.id} needs a label`);
    assert.ok(tool.icon, `${tool.id} needs an icon`);
    assert.ok(["view", "inspect", "layers", "out"].includes(tool.group), `${tool.id} group`);
    assert.ok(["action", "toggle", "cycle"].includes(tool.state), `${tool.id} state kind`);
  }
});

test("the schematic pane drops the tools that mean nothing on a sheet", () => {
  const schematic = toolsForSurface("schematic").map((t) => t.id);
  assert.deepEqual(schematic, ["fit", "zoom-out", "zoom-in", "hud", "export", "reset"]);
  assert.equal(toolsForSurface("pcb").length, VIEWPORT_TOOLS.length);
  // Unknown surfaces get the full rail rather than an empty one.
  assert.equal(toolsForSurface("whatever").length, VIEWPORT_TOOLS.length);
});

test("toolState reports pressed state and the cycling tools' current value", () => {
  const byId = (id) => VIEWPORT_TOOLS.find((t) => t.id === id);
  const ctx = {
    measuring: true,
    hudVisible: false,
    showGrid: false,
    singleLayerMode: "grey",
    highlightMethod: "mask",
    maskLevel: 4,
    units: "mil",
  };
  assert.deepEqual(toolState(byId("measure"), ctx), { active: true, value: "" });
  assert.deepEqual(toolState(byId("hud"), ctx), { active: false, value: "" });
  assert.deepEqual(toolState(byId("grid"), ctx), { active: false, value: "" });
  assert.deepEqual(toolState(byId("single-layer"), ctx), { active: true, value: "grey" });
  assert.deepEqual(toolState(byId("highlight"), ctx), { active: true, value: "mask 4" });
  assert.deepEqual(toolState(byId("units"), ctx), { active: false, value: "mil" });
  assert.deepEqual(toolState(byId("fit"), ctx), { active: false, value: "" });
});

test("toolState defaults are the workspace's own defaults", () => {
  const byId = (id) => VIEWPORT_TOOLS.find((t) => t.id === id);
  assert.equal(toolState(byId("grid"), {}).active, true, "grid is on unless told otherwise");
  assert.equal(toolState(byId("single-layer"), {}).active, false);
  assert.deepEqual(toolState(byId("highlight"), {}), { active: true, value: "dim" });
  assert.equal(toolState(byId("units"), {}).value, "mm");
});

function spyContext() {
  const calls = [];
  const record = (name) => (...args) => calls.push([name, ...args]);
  return {
    calls,
    ctx: {
      view: { zoomBy: record("zoomBy") },
      onFit: record("fit"),
      onToggleMeasure: record("measure"),
      onToggleHud: record("hud"),
      onToggleGrid: record("grid"),
      onToggleRegions: record("rooms"),
      onCycleSingleLayer: record("singleLayer"),
      onCycleHighlightMethod: record("highlight"),
      onToggleUnits: record("units"),
      onExport: record("export"),
      onReset: record("reset"),
    },
  };
}

test("every tool in the rail dispatches to exactly one callback", () => {
  for (const tool of VIEWPORT_TOOLS) {
    const { calls, ctx } = spyContext();
    assert.equal(dispatchViewportTool(tool.id, ctx), true, `${tool.id} should dispatch`);
    assert.equal(calls.length, 1, `${tool.id} should fire once`);
  }
});

test("zoom in and out are reciprocal steps through the canvas handle", () => {
  const { calls, ctx } = spyContext();
  dispatchViewportTool("zoom-in", ctx);
  dispatchViewportTool("zoom-out", ctx);
  assert.deepEqual(calls, [
    ["zoomBy", ZOOM_STEP],
    ["zoomBy", 1 / ZOOM_STEP],
  ]);
});

test("a missing callback is a no-op, not a throw — a half-wired pane loses buttons, not the app", () => {
  for (const tool of VIEWPORT_TOOLS) {
    assert.equal(dispatchViewportTool(tool.id, {}), false, `${tool.id} with no context`);
  }
  assert.equal(dispatchViewportTool("nonexistent", spyContext().ctx), false);
});

test("activeBoardSide only reads 'bottom' when bottom copper is actually isolated", () => {
  assert.equal(activeBoardSide({ activeLayer: "bottom", singleLayerMode: "off" }), "top");
  assert.equal(activeBoardSide({ activeLayer: "bottom", singleLayerMode: "grey" }), "bottom");
  assert.equal(activeBoardSide({ activeLayer: "top", singleLayerMode: "hide" }), "top");
  assert.equal(activeBoardSide(), "top");
});

test("picking a side isolates it; picking the side you are on releases the isolation", () => {
  assert.deepEqual(boardSideChange("bottom", { activeLayer: "top", singleLayerMode: "off" }), {
    activeLayer: "bottom",
    singleLayerMode: "grey",
  });
  // Already on the bottom and isolated → back to the full stack.
  assert.deepEqual(boardSideChange("bottom", { activeLayer: "bottom", singleLayerMode: "grey" }), {
    activeLayer: "bottom",
    singleLayerMode: "off",
  });
  // Top while the whole stack is showing is already the resting state, so the
  // click isolates rather than doing nothing.
  assert.deepEqual(boardSideChange("top", { activeLayer: "top", singleLayerMode: "off" }), {
    activeLayer: "top",
    singleLayerMode: "grey",
  });
  // An unknown id falls back to the first face rather than corrupting state.
  assert.deepEqual(boardSideChange("nope", {}), { activeLayer: "top", singleLayerMode: "grey" });
  assert.deepEqual(BOARD_SIDES.map((s) => s.id), ["top", "bottom"]);
});
