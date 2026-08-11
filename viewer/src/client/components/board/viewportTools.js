// viewportTools — the descriptor list and dispatch for the floating rail that
// sits along the bottom of the PCB / Schematic canvases. DOM-free and
// React-free so node:test covers the whole contract directly.
//
// Ported in shape from the donor's viewer3d/ViewerTools.tsx: a click-through
// wrapper, glass pills, and every button either flipping one piece of state or
// calling one imperative canvas method. What is NOT ported is Vibe's four
// global zustand singletons and their hand-rolled mutual-exclusion table —
// their tools genuinely fight (explode's CPU vertex displacement breaks
// section's world-space clip). Ours are orthogonal: a layer filter and a
// measure line do not interact. So there is one descriptor list, one switch,
// and the state stays where it already lives, in BoardWorkspace.
//
// Every tool names its keyboard binding (ALTIUM-NOTES §7). The rail is where
// those bindings become discoverable — before this they were documented and
// invisible.

/** Zoom step per click of the +/- buttons. Matches ~4 wheel notches. */
export const ZOOM_STEP = 1.35;

/**
 * The rail, in render order. Groups are separated by a divider in the UI:
 *   view    — camera
 *   inspect — tools that change what a click does or what you can read
 *   layers  — what is drawn
 *   out     — getting the drawing out of the app
 *
 * `state` is how a button reports pressed/label: "toggle" reads a boolean from
 * the context, "cycle" reads a string, "action" has no state at all.
 */
export const VIEWPORT_TOOLS = Object.freeze([
  { id: "fit", group: "view", label: "Zoom to fit", icon: "fit", key: "F", state: "action" },
  { id: "zoom-out", group: "view", label: "Zoom out", icon: "minus", key: "−", state: "action" },
  { id: "zoom-in", group: "view", label: "Zoom in", icon: "plus", key: "+", state: "action" },
  { id: "measure", group: "inspect", label: "Measure", icon: "ruler", key: "⌘M", state: "toggle" },
  { id: "hud", group: "inspect", label: "Coordinates", icon: "crosshair", key: "⇧H", state: "toggle" },
  { id: "grid", group: "inspect", label: "Grid", icon: "grid", key: "", state: "toggle" },
  { id: "single-layer", group: "layers", label: "Single layer", icon: "layers", key: "⇧S", state: "cycle" },
  { id: "highlight", group: "layers", label: "Highlight method", icon: "contrast", key: "M", state: "cycle" },
  { id: "units", group: "layers", label: "Units", icon: "units", key: "Q", state: "cycle" },
  { id: "export", group: "out", label: "Export view as SVG", icon: "image", key: "", state: "action" },
  { id: "reset", group: "out", label: "Reset view", icon: "reset", key: "", state: "action" },
]);

/** The subset that means anything on the schematic pane (no layers, no units). */
const SCHEMATIC_TOOLS = new Set(["fit", "zoom-out", "zoom-in", "hud", "export", "reset"]);

/**
 * The tools to render for one pane.
 * @param {"pcb"|"schematic"} surface
 */
export function toolsForSurface(surface) {
  if (surface === "schematic") return VIEWPORT_TOOLS.filter((tool) => SCHEMATIC_TOOLS.has(tool.id));
  return VIEWPORT_TOOLS;
}

/**
 * Whether a tool reads as pressed, and the short suffix it shows (the cycling
 * tools carry their current value in the label — "Dim 3", "mm", "Grey others").
 *
 * @param {object} tool
 * @param {object} ctx  the live workspace state
 * @returns {{active: boolean, value: string}}
 */
export function toolState(tool, ctx = {}) {
  switch (tool.id) {
    case "measure":
      return { active: Boolean(ctx.measuring), value: "" };
    case "hud":
      return { active: Boolean(ctx.hudVisible), value: "" };
    case "grid":
      return { active: ctx.showGrid !== false, value: "" };
    case "single-layer": {
      const mode = String(ctx.singleLayerMode || "off");
      return { active: mode !== "off", value: mode === "off" ? "" : mode };
    }
    case "highlight": {
      const method = String(ctx.highlightMethod || "dim");
      return {
        active: method !== "normal",
        value: method === "normal" ? "" : `${method} ${ctx.maskLevel ?? ""}`.trim(),
      };
    }
    case "units":
      return { active: false, value: String(ctx.units || "mm") };
    default:
      return { active: false, value: "" };
  }
}

/**
 * Run one tool.
 *
 * `ctx` carries the callbacks BoardWorkspace already owns plus a `view`
 * handle — the imperative surface published by whichever canvas the rail is
 * mounted on. Every branch is a no-op when its callback is absent, so a
 * partially-wired pane degrades to fewer working buttons rather than throwing
 * out of an onClick.
 *
 * @returns {boolean} true when the tool did something — the return is what the
 *   tests assert on, and what stops a dead button from silently looking alive.
 */
export function dispatchViewportTool(id, ctx = {}) {
  const view = ctx.view || null;
  switch (id) {
    case "fit":
      if (!ctx.onFit) return false;
      ctx.onFit();
      return true;
    case "zoom-in":
      if (!view?.zoomBy) return false;
      view.zoomBy(ZOOM_STEP);
      return true;
    case "zoom-out":
      if (!view?.zoomBy) return false;
      view.zoomBy(1 / ZOOM_STEP);
      return true;
    case "measure":
      if (!ctx.onToggleMeasure) return false;
      ctx.onToggleMeasure();
      return true;
    case "hud":
      if (!ctx.onToggleHud) return false;
      ctx.onToggleHud();
      return true;
    case "grid":
      if (!ctx.onToggleGrid) return false;
      ctx.onToggleGrid();
      return true;
    case "single-layer":
      if (!ctx.onCycleSingleLayer) return false;
      ctx.onCycleSingleLayer();
      return true;
    case "highlight":
      if (!ctx.onCycleHighlightMethod) return false;
      ctx.onCycleHighlightMethod();
      return true;
    case "units":
      if (!ctx.onToggleUnits) return false;
      ctx.onToggleUnits();
      return true;
    case "export":
      if (!ctx.onExport) return false;
      ctx.onExport();
      return true;
    case "reset":
      if (!ctx.onReset) return false;
      ctx.onReset();
      return true;
    default:
      return false;
  }
}

// --- board side (the 2D analogue of Vibe's view cube) -----------------------

/**
 * The two faces of a PCB. Vibe's cube snaps a camera; ours has no camera to
 * snap, so the honest question a flat board answers is "which side am I
 * looking at". `layer` is the layer that becomes active; `isolate` says whether
 * the other copper is hidden — flipping to the bottom of a board and still
 * seeing the top copper on top of it is how you misread a footprint.
 */
export const BOARD_SIDES = Object.freeze([
  { id: "top", label: "TOP", layer: "top" },
  { id: "bottom", label: "BOTTOM", layer: "bottom" },
]);

/**
 * Which side the workspace currently reads as. A board is "on its bottom" only
 * when bottom is the active layer AND single-layer mode is isolating it —
 * otherwise you are looking at the whole stack from the top, which is the
 * default and the truth.
 */
export function activeBoardSide({ activeLayer = "top", singleLayerMode = "off" } = {}) {
  if (singleLayerMode === "off") return "top";
  return activeLayer === "bottom" ? "bottom" : "top";
}

/**
 * The state change for picking a side: activate that copper and isolate it.
 * Picking the side you are already on is the way back out — it releases the
 * isolation and returns to the full stack, so the control is never a trap.
 *
 * @returns {{activeLayer: string, singleLayerMode: "off"|"grey"|"hide"}}
 */
export function boardSideChange(sideId, current = {}) {
  const side = BOARD_SIDES.find((entry) => entry.id === sideId) || BOARD_SIDES[0];
  const isolating = String(current.singleLayerMode || "off") !== "off";
  if (isolating && activeBoardSide(current) === side.id) {
    return { activeLayer: side.layer, singleLayerMode: "off" };
  }
  return { activeLayer: side.layer, singleLayerMode: "grey" };
}
