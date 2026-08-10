// boardRender — turn indexed PCB elements into an ordered list of draw items.
//
// Pure geometry and paint order; no React, no DOM. The canvas component walks
// this list once per render and emits one SVG node per item. Keeping it here
// means paint order and layer splitting are unit-testable, which matters —
// getting copper under silkscreen and pads over traces right is most of what
// makes a board look like a board.
//
// Coordinates stay in board millimetres with **y up** (the Circuit JSON
// convention). The canvas applies a single `scale(s, -s)` so nothing in here
// has to think about screen space.

import { elementId, pcbElementBox } from "./boardIndex.js";
import { LAYER_ORDER, layerOf, objectClassOf } from "./boardPalette.js";

const NUM = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

const LAYER_DEPTH = new Map(LAYER_ORDER.map((layer, index) => [layer, index]));

/**
 * Paint order. Lower draws first. Copper is ordered by layer so bottom sits
 * under top; pads sit over traces on the same layer; drilled things sit over
 * pads; silkscreen sits over everything.
 */
export function paintRank(type, layer) {
  const depth = LAYER_DEPTH.get(String(layer || "top")) ?? LAYER_ORDER.length - 1;
  switch (type) {
    case "pcb_courtyard_rect":
    case "pcb_courtyard_outline":
      return 100;
    case "pcb_solder_paste":
      return 200;
    case "pcb_trace":
      return 300 + depth * 2;
    case "pcb_smtpad":
      return 400 + depth * 2;
    case "pcb_plated_hole":
      return 500;
    case "pcb_via":
      return 520;
    case "pcb_hole":
      return 540;
    case "pcb_silkscreen_path":
    case "pcb_silkscreen_rect":
    case "pcb_silkscreen_circle":
      return 600 + depth;
    case "pcb_silkscreen_text":
      return 620 + depth;
    case "pcb_cutout":
      return 700;
    default:
      return 350;
  }
}

/**
 * A pcb_trace route can change layer mid-route (a via drop). Split it into
 * contiguous same-layer polylines so each piece paints in its own copper
 * colour and hit-tests against the right layer.
 *
 * @returns {Array<{layer: string, width: number, points: Array<{x:number,y:number}>}>}
 */
export function splitTraceByLayer(trace) {
  const route = Array.isArray(trace?.route) ? trace.route : [];
  const runs = [];
  let current = null;
  for (const point of route) {
    if (point?.route_type === "via") {
      // A via point carries no x/y of its own worth drawing here — the
      // pcb_via element does that. Just break the run.
      current = null;
      continue;
    }
    const layer = String(point?.layer || trace?.layer || "top");
    const width = NUM(point?.width, 0.15);
    if (!current || current.layer !== layer) {
      current = { layer, width, points: [] };
      runs.push(current);
      // Bridge the layer change so the polyline does not visually break.
      const previous = runs[runs.length - 2];
      if (previous && previous.points.length) {
        current.points.push(previous.points[previous.points.length - 1]);
      }
    }
    current.width = Math.max(current.width, width);
    current.points.push({ x: NUM(point.x), y: NUM(point.y) });
  }
  return runs.filter((run) => run.points.length > 1);
}

/**
 * Build the ordered draw list.
 *
 * @param {object} index buildBoardIndex output
 * @param {{visibleLayers?: Set<string>|null, visibleClasses?: Set<string>|null}} options
 * @returns {Array<object>} items: {key, id, element, type, layer, klass, rank, run?}
 */
export function buildDrawList(index, { visibleLayers = null, visibleClasses = null } = {}) {
  const items = [];
  for (const element of index?.pcbDrawables || []) {
    const klass = objectClassOf(element);
    if (visibleClasses && klass && !visibleClasses.has(klass)) continue;
    const id = elementId(element);

    if (element.type === "pcb_trace") {
      const runs = splitTraceByLayer(element);
      runs.forEach((run, order) => {
        if (visibleLayers && !visibleLayers.has(run.layer)) return;
        items.push({
          key: `${id}:${order}`,
          id,
          element,
          type: element.type,
          layer: run.layer,
          klass,
          rank: paintRank(element.type, run.layer),
          run,
        });
      });
      continue;
    }

    const layer = layerOf(element);
    // Vias and through-holes belong to every layer they span — never hide them
    // just because one side is off.
    const spansAll = element.type === "pcb_via" || element.type === "pcb_hole" || element.type === "pcb_plated_hole";
    if (visibleLayers && !spansAll && !visibleLayers.has(layer)) continue;
    items.push({
      key: id || `${element.type}:${items.length}`,
      id,
      element,
      type: element.type,
      layer,
      klass,
      rank: paintRank(element.type, layer),
    });
  }
  items.sort((a, b) => a.rank - b.rank);
  return items;
}

// --- view maths ------------------------------------------------------------

/**
 * The view that fits `box` (board mm) into a `width`×`height` px viewport with
 * `padding` px of margin. Returns pixels-per-mm and the screen-space
 * translation of board origin (0,0).
 */
export function fitView(box, width, height, padding = 24) {
  const safeW = Math.max(1, NUM(width, 1));
  const safeH = Math.max(1, NUM(height, 1));
  if (!box || !Number.isFinite(box.minX) || box.maxX <= box.minX) {
    return { scale: 8, tx: safeW / 2, ty: safeH / 2 };
  }
  const boxW = Math.max(0.001, box.maxX - box.minX);
  const boxH = Math.max(0.001, box.maxY - box.minY);
  const scale = Math.min((safeW - padding * 2) / boxW, (safeH - padding * 2) / boxH);
  const safeScale = Number.isFinite(scale) && scale > 0 ? scale : 8;
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  return { scale: safeScale, tx: safeW / 2 - cx * safeScale, ty: safeH / 2 + cy * safeScale };
}

/** Screen px → board mm. y is flipped: board y grows up, screen y grows down. */
export function screenToBoard(view, px, py) {
  const scale = NUM(view?.scale, 1) || 1;
  return { x: (NUM(px) - NUM(view?.tx)) / scale, y: -(NUM(py) - NUM(view?.ty)) / scale };
}

/** Board mm → screen px. */
export function boardToScreen(view, x, y) {
  const scale = NUM(view?.scale, 1) || 1;
  return { x: NUM(x) * scale + NUM(view?.tx), y: -NUM(y) * scale + NUM(view?.ty) };
}

/** Zoom about a screen point, keeping the board point under it fixed. */
export function zoomAt(view, px, py, factor, { min = 0.5, max = 4000 } = {}) {
  const scale = Math.min(max, Math.max(min, NUM(view?.scale, 1) * NUM(factor, 1)));
  const applied = scale / (NUM(view?.scale, 1) || 1);
  return {
    scale,
    tx: NUM(px) - (NUM(px) - NUM(view?.tx)) * applied,
    ty: NUM(py) - (NUM(py) - NUM(view?.ty)) * applied,
  };
}

/** The SVG `d` for a polyline through board-mm points. */
export function polylinePath(points) {
  const list = Array.isArray(points) ? points : [];
  if (!list.length) return "";
  let d = `M ${NUM(list[0].x)} ${NUM(list[0].y)}`;
  for (let i = 1; i < list.length; i += 1) d += ` L ${NUM(list[i].x)} ${NUM(list[i].y)}`;
  return d;
}

/** A closed polygon `d`. */
export function polygonPath(points) {
  const d = polylinePath(points);
  return d ? `${d} Z` : "";
}

/** The screen rect (px) of a board-mm box, for overlay drawing. */
export function boxToScreenRect(view, box) {
  if (!box || !Number.isFinite(box.minX)) return null;
  const a = boardToScreen(view, box.minX, box.maxY);
  const b = boardToScreen(view, box.maxX, box.minY);
  return { x: a.x, y: a.y, width: Math.max(1, b.x - a.x), height: Math.max(1, b.y - a.y) };
}

/** Format a millimetre value the way a PCB tool does — mm or mil, 3/1 places. */
export function formatLength(mm, units = "mm") {
  const value = NUM(mm);
  if (units === "mil") return `${(value / 0.0254).toFixed(1)} mil`;
  return `${value.toFixed(3)} mm`;
}

/** Compact coordinate pair for the HUD. */
export function formatPoint(x, y, units = "mm") {
  if (units === "mil") return `${(NUM(x) / 0.0254).toFixed(0)}, ${(NUM(y) / 0.0254).toFixed(0)} mil`;
  return `${NUM(x).toFixed(3)}, ${NUM(y).toFixed(3)} mm`;
}

/**
 * Swap a review artifact's `.png` for its `.svg` twin, keeping the `?v=`
 * cache-bust intact. The catalog surfaces only the PNG, but the pipeline writes
 * both side by side and the SVG is the one that carries the coordinate matrix.
 */
export function svgTwin(pngUrl) {
  const url = String(pngUrl || "");
  if (!url) return "";
  const [path, query] = url.split("?");
  if (!path.endsWith(".png")) return "";
  return `${path.slice(0, -4)}.svg${query ? `?${query}` : ""}`;
}

/**
 * The pipeline's `_schematic.svg` carries its own world→pixel mapping on the
 * root tag:
 *
 *   data-real-to-screen-transform="matrix(75.66,0,0,-75.66,636.31,251.76)"
 *
 * That is the bridge that lets us overlay live, hit-testable geometry from the
 * circuit JSON exactly on top of the pipeline's rendered symbols — we get
 * proper resistor zigzags AND true cross-probing, instead of choosing. Only the
 * axis-aligned form (b = c = 0) is produced, and only that form is accepted;
 * anything else returns null and the pane degrades to a plain image.
 *
 * @param {string} svgText
 * @returns {{a:number,d:number,e:number,f:number,width:number,height:number}|null}
 */
export function parseSchematicTransform(svgText) {
  const text = String(svgText || "");
  const matrix = /data-real-to-screen-transform\s*=\s*"matrix\(([^)]+)\)"/.exec(text);
  if (!matrix) return null;
  const parts = matrix[1].split(",").map((piece) => Number(piece.trim()));
  if (parts.length < 6 || parts.some((value) => !Number.isFinite(value))) return null;
  const [a, b, c, d, e, f] = parts;
  if (b !== 0 || c !== 0 || a === 0 || d === 0) return null;
  const width = Number(/\bwidth="(\d+(?:\.\d+)?)"/.exec(text)?.[1]);
  const height = Number(/\bheight="(\d+(?:\.\d+)?)"/.exec(text)?.[1]);
  return {
    a,
    d,
    e,
    f,
    width: Number.isFinite(width) ? width : 1200,
    height: Number.isFinite(height) ? height : 600,
  };
}

/** Schematic world units → SVG pixel space. */
export function schematicToSvg(transform, x, y) {
  if (!transform) return { x: NUM(x), y: NUM(y) };
  return { x: transform.a * NUM(x) + transform.e, y: transform.d * NUM(y) + transform.f };
}

/** SVG pixel space → schematic world units. */
export function svgToSchematic(transform, px, py) {
  if (!transform) return { x: NUM(px), y: NUM(py) };
  return { x: (NUM(px) - transform.e) / transform.a, y: (NUM(py) - transform.f) / transform.d };
}

/** The SVG-pixel rect of a schematic-world box. */
export function schematicBoxToSvgRect(transform, box) {
  if (!box || !Number.isFinite(box.minX)) return null;
  const a = schematicToSvg(transform, box.minX, box.maxY);
  const b = schematicToSvg(transform, box.maxX, box.minY);
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(b.x - a.x),
    height: Math.abs(b.y - a.y),
  };
}

/**
 * A grid step that stays readable at the current zoom: the smallest step from
 * the ladder that is at least `minPx` pixels apart on screen.
 */
export function gridStepMm(scale, minPx = 9) {
  const ladder = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25, 50];
  for (const step of ladder) if (step * NUM(scale, 1) >= minPx) return step;
  return ladder[ladder.length - 1];
}
