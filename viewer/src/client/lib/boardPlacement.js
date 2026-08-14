// boardPlacement — pure placement-edit helpers for the PCB canvas.
//
// The rendered circuit.json is a build artifact, not the board source. A drag
// therefore creates a precise placement *request* that the chat agent applies
// to TSX before rebuilding. Keeping the geometry math here makes the preview,
// the model-facing context and node:test agree about what was requested.

import { boxIsReal, distance } from "./boardIndex.js";

export const PLACEMENT_GRID_MM = 0.25;
export const PLACEMENT_CLEARANCE_MM = 0.2;
export const MAX_RATLINES = 12;

const NUM = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

export function componentCenter(component) {
  if (!component?.pcb?.center) return null;
  const x = Number(component.pcb.center.x);
  const y = Number(component.pcb.center.y);
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
}

export function snapPlacementPoint(point, gridMm = PLACEMENT_GRID_MM) {
  const grid = Math.abs(NUM(gridMm, PLACEMENT_GRID_MM)) || PLACEMENT_GRID_MM;
  return {
    x: Math.round(NUM(point?.x) / grid) * grid,
    y: Math.round(NUM(point?.y) / grid) * grid,
  };
}

export function nudgePlacementPoint(
  point,
  direction,
  { gridMm = PLACEMENT_GRID_MM, steps = 1 } = {},
) {
  const grid = Math.abs(NUM(gridMm, PLACEMENT_GRID_MM)) || PLACEMENT_GRID_MM;
  const amount = grid * Math.max(1, Math.abs(Math.trunc(NUM(steps, 1))));
  const delta = {
    ArrowLeft: { x: -amount, y: 0 },
    ArrowRight: { x: amount, y: 0 },
    ArrowUp: { x: 0, y: amount },
    ArrowDown: { x: 0, y: -amount },
  }[direction];
  if (!delta) return snapPlacementPoint(point, grid);
  return snapPlacementPoint(
    { x: NUM(point?.x) + delta.x, y: NUM(point?.y) + delta.y },
    grid,
  );
}

export function translateBox(box, delta) {
  if (!boxIsReal(box)) return box;
  const dx = NUM(delta?.x);
  const dy = NUM(delta?.y);
  return {
    minX: box.minX + dx,
    minY: box.minY + dy,
    maxX: box.maxX + dx,
    maxY: box.maxY + dy,
  };
}

function boxesNear(a, b, clearance) {
  if (!boxIsReal(a) || !boxIsReal(b)) return false;
  const gap = Math.max(0, NUM(clearance));
  return !(
    a.maxX + gap <= b.minX ||
    a.minX - gap >= b.maxX ||
    a.maxY + gap <= b.minY ||
    a.minY - gap >= b.maxY
  );
}

function boxOutside(inner, outer) {
  if (!boxIsReal(inner) || !boxIsReal(outer)) return false;
  return (
    inner.minX < outer.minX ||
    inner.minY < outer.minY ||
    inner.maxX > outer.maxX ||
    inner.maxY > outer.maxY
  );
}

function ratlinesFor(index, component, center, limit) {
  const candidates = [];
  const seen = new Set();
  for (const netKey of component.netKeys || []) {
    const net = index?.netByKey?.get(netKey);
    if (!net) continue;
    for (const componentKey of net.componentKeys || []) {
      if (componentKey === component.key) continue;
      const other = index?.componentBySourceId?.get(componentKey);
      const to = componentCenter(other);
      if (!other || !to) continue;
      const token = `${netKey}:${componentKey}`;
      if (seen.has(token)) continue;
      seen.add(token);
      candidates.push({
        key: token,
        netKey,
        netName: net.name || "",
        componentKey,
        refdes: other.refdes || "",
        from: center,
        to,
        power: Boolean(net.isGround || net.isPower),
        distanceMm: distance(center, to),
      });
    }
  }
  // Signal connections are the placement-critical ones. Power/ground still
  // appear when room remains, instead of turning a decoupling move into a
  // starburst that hides every other relationship.
  candidates.sort((a, b) => Number(a.power) - Number(b.power) || a.distanceMm - b.distanceMm);
  return candidates.slice(0, Math.max(0, NUM(limit, MAX_RATLINES)));
}

/**
 * Build the complete live preview for one component drag.
 *
 * "nearby" is deliberately advisory. Circuit JSON has footprint bodies and
 * generated courtyard/silk extents, but source rules and the post-route DRC
 * remain the authority. The UI warns; the rebuild decides.
 */
export function previewComponentPlacement(
  index,
  componentKey,
  requestedCenter,
  { gridMm = PLACEMENT_GRID_MM, clearanceMm = PLACEMENT_CLEARANCE_MM, ratlineLimit = MAX_RATLINES } = {},
) {
  const component = index?.componentBySourceId?.get(componentKey) || null;
  const originalCenter = componentCenter(component);
  if (!component || !originalCenter || !boxIsReal(component.pcbBox)) return null;

  const center = snapPlacementPoint(requestedCenter, gridMm);
  const delta = { x: center.x - originalCenter.x, y: center.y - originalCenter.y };
  const movedBox = translateBox(component.pcbBox, delta);
  const componentLayer = String(component.layer || component.pcb?.layer || "top");
  const nearby = (index.components || [])
    // Bodies on opposite assembly faces may intentionally share XY space—a
    // core reason to use Standard two-sided assembly on compact products.
    // Warn only about same-face body/courtyard proximity here; the rebuild's
    // copper and drill checks remain authoritative across both layers.
    .filter(
      (other) =>
        other.key !== component.key &&
        String(other.layer || other.pcb?.layer || "top") === componentLayer &&
        boxesNear(movedBox, other.pcbBox, clearanceMm),
    )
    .map((other) => ({ key: other.key, refdes: other.refdes || "", box: other.pcbBox }));

  return {
    component,
    componentKey: component.key,
    refdes: component.refdes || component.key,
    groupId: component.groupId || "",
    originalCenter,
    center,
    delta,
    movedBox,
    nearby,
    outsideBoard: boxOutside(movedBox, index.boardBox),
    ratlines: ratlinesFor(index, component, center, ratlineLimit),
    elementIds: new Set(component.pcbElementIds || []),
    gridMm: Math.abs(NUM(gridMm, PLACEMENT_GRID_MM)) || PLACEMENT_GRID_MM,
  };
}

function signed(value) {
  const n = NUM(value);
  return `${n >= 0 ? "+" : ""}${n.toFixed(3)}`;
}

export function placementDirection(delta) {
  const dx = NUM(delta?.x);
  const dy = NUM(delta?.y);
  const epsilon = 1e-9;
  if (Math.abs(dy) <= epsilon && Math.abs(dx) > epsilon) {
    return `${Math.abs(dx).toFixed(3)} mm ${dx > 0 ? "right" : "left"}`;
  }
  if (Math.abs(dx) <= epsilon && Math.abs(dy) > epsilon) {
    return `${Math.abs(dy).toFixed(3)} mm ${dy > 0 ? "up" : "down"}`;
  }
  return `by Δx ${signed(dx)} mm, Δy ${signed(dy)} mm`;
}

/** Visible composer text: short enough to review before the user sends it. */
export function placementRequestText(preview, { board = "" } = {}) {
  if (!preview) return "";
  const boardText = board ? ` on board ${board}` : "";
  const warnings = [];
  if (preview.outsideBoard) warnings.push("the preview crosses the board edge");
  if (preview.nearby?.length) {
    warnings.push(`the preview is near ${preview.nearby.map((item) => item.refdes || item.key).join(", ")}`);
  }
  return [
    `Move ${preview.refdes} ${placementDirection(preview.delta)}${boardText}.`,
    `Set its PCB centre from (${preview.originalCenter.x.toFixed(3)}, ${preview.originalCenter.y.toFixed(3)}) mm to (${preview.center.x.toFixed(3)}, ${preview.center.y.toFixed(3)}) mm.`,
    "Update the board TSX/source placement, not generated circuit.json; preserve its layer and rotation, reroute, then rerun DRC and fabrication verification.",
    warnings.length ? `Check placement carefully: ${warnings.join("; ")}.` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

/** Hidden model context: stable IDs remove ambiguity when refdes is repeated in blocks. */
export function placementContextNote(preview, { board = "" } = {}) {
  if (!preview) return "";
  const nearby = (preview.nearby || []).map((item) => item.refdes || item.key).filter(Boolean);
  return `[Placement edit: board=${board || "unknown"}; source_component_id=${preview.componentKey}; group_id=${preview.groupId || "unknown"}; requested_center_mm=${preview.center.x.toFixed(3)},${preview.center.y.toFixed(3)}; delta_mm=${signed(preview.delta.x)},${signed(preview.delta.y)}; snap_grid_mm=${preview.gridMm.toFixed(3)}; outside_board=${preview.outsideBoard}; nearby=${nearby.join(",") || "none"}. Apply at source, rebuild routing, and accept only if electrical-layout and fabrication checks do not regress.]`;
}
