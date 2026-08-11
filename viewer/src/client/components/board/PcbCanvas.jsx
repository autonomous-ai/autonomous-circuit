import { useCallback, useEffect, useId, useImperativeHandle, useMemo, useRef, useState } from "react";
import { cn } from "@/ui/utils";
import { boxIsReal, hitTestPcb, inflateBox, pcbElementBox } from "@/lib/boardIndex.js";
import {
  copperColor,
  elementColor,
  maskedColor,
  palette,
  unselectedIsMonochrome,
  unselectedOpacity,
} from "@/lib/boardPalette.js";
import {
  boardToScreen,
  boxToScreenRect,
  buildDrawList,
  fitView,
  gridStepMm,
  polygonPath,
  polylinePath,
  screenToBoard,
  zoomAt,
} from "@/lib/boardRender.js";

const NUM = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);
const CLICK_SLOP_PX = 4;

const TEXT_ANCHOR = {
  center: "middle",
  top_left: "start",
  top_center: "middle",
  top_right: "end",
  center_left: "start",
  center_right: "end",
  bottom_left: "start",
  bottom_center: "middle",
  bottom_right: "end",
};
const TEXT_BASELINE = {
  center: "middle",
  top_left: "hanging",
  top_center: "hanging",
  top_right: "hanging",
  center_left: "middle",
  center_right: "middle",
  bottom_left: "auto",
  bottom_center: "auto",
  bottom_right: "auto",
};

/**
 * The PCB canvas, drawn from `<stem>.circuit.json` rather than from the
 * pipeline's PNG. Every pad, trace, via, hole and silkscreen stroke is its own
 * SVG node, so everything is hit-testable, colourable by layer, and dimmable
 * for net masking — the Altium behaviours the flat image can never support.
 * The pipeline PNG stays available as `fallbackSrc` for the moment before the
 * JSON lands (and for any element kind we cannot draw yet).
 *
 * Interaction, per ALTIUM-NOTES:
 *   · drag           pan            · wheel        zoom about the cursor
 *   · click          select + cross-probe, camera stays put
 *   · ⌘/Ctrl+click   select + jump (zoom the other pane to it)
 *   · ⇧+click        select the whole net under the cursor
 *   · measure mode   drag to measure, live readout
 */
export default function PcbCanvas({
  index,
  scheme = "studio",
  visibleLayers = null,
  visibleClasses = null,
  activeLayer = "top",
  singleLayerMode = "off",
  highlight = null,
  selection = null,
  highlightMethod = "dim",
  maskLevel = 3,
  units = "mm",
  measuring = false,
  showGrid = true,
  flash = null,
  fallbackSrc = "",
  onSelect,
  onHoverChange,
  onMeasureChange,
  onViewChange,
  className,
  viewRef: externalViewRef,
}) {
  const colors = palette(scheme);
  const stageRef = useRef(null);
  const svgRef = useRef(null);
  const gradientId = useId().replace(/:/g, "");
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [view, setView] = useState({ scale: 8, tx: 0, ty: 0 });
  const [hover, setHover] = useState(null);
  const [cursor, setCursor] = useState(null);
  const [measure, setMeasure] = useState(null);
  const [deltaOrigin, setDeltaOrigin] = useState({ x: 0, y: 0 });
  const dragRef = useRef(null);
  const viewStateRef = useRef(view);
  viewStateRef.current = view;
  const deltaOriginRef = useRef(deltaOrigin);
  deltaOriginRef.current = deltaOrigin;

  // --- viewport size
  useEffect(() => {
    const node = stageRef.current;
    if (!node || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) setSize({ width: rect.width, height: rect.height });
    });
    observer.observe(node);
    setSize({ width: node.clientWidth, height: node.clientHeight });
    return () => observer.disconnect();
  }, []);

  const fitBox = useMemo(() => {
    if (!index) return null;
    return boxIsReal(index.boardBox) ? index.boardBox : index.pcbBox;
  }, [index]);

  const fitToBoard = useCallback(() => {
    if (!fitBox || !size.width || !size.height) return;
    setView(fitView(fitBox, size.width, size.height, 28));
  }, [fitBox, size.width, size.height]);

  // Fresh board or first real viewport → frame it.
  const framedRef = useRef("");
  useEffect(() => {
    const token = `${index?.stats?.elements || 0}:${Math.round(size.width)}x${Math.round(size.height)}`;
    if (!size.width || !size.height || !fitBox) return;
    const boardToken = String(index?.stats?.elements || 0);
    if (framedRef.current.startsWith(`${boardToken}:`) && framedRef.current !== token) return;
    if (framedRef.current === token) return;
    framedRef.current = token;
    setView(fitView(fitBox, size.width, size.height, 28));
  }, [index, fitBox, size.width, size.height]);

  useEffect(() => {
    onViewChange?.(view);
  }, [view, onViewChange]);

  /** Zoom the camera to a board-mm box, imperatively (DRC jump, cross-probe). */
  const zoomToBox = useCallback(
    (box, { margin = 1.5 } = {}) => {
      if (!boxIsReal(box) || !size.width || !size.height) return;
      const padded = inflateBox(box, margin);
      const next = fitView(padded, size.width, size.height, 40);
      // Never zoom in past a sane working scale — landing at 900 px/mm on a
      // single pad is disorienting.
      setView({ ...next, scale: Math.min(next.scale, 220) });
    },
    [size.width, size.height],
  );

  /** Zoom about the middle of the pane — what a toolbar +/- means, as opposed
   *  to the wheel, which zooms about the cursor. */
  const zoomBy = useCallback(
    (factor) => {
      if (!size.width || !size.height) return;
      setView((prev) => zoomAt(prev, size.width / 2, size.height / 2, factor));
    },
    [size.width, size.height],
  );

  /** The live board drawing as standalone SVG text, or "" before it renders. */
  const exportSvg = useCallback(() => {
    const node = svgRef.current;
    if (!node) return "";
    const clone = node.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", String(size.width || 1));
    clone.setAttribute("height", String(size.height || 1));
    // The pane's background lives on the parent div, so paint it in or the
    // exported file opens as dark copper on a white page.
    const background = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    background.setAttribute("width", "100%");
    background.setAttribute("height", "100%");
    background.setAttribute("fill", colors.background);
    clone.insertBefore(background, clone.firstChild);
    return new XMLSerializer().serializeToString(clone);
  }, [size.width, size.height, colors.background]);

  useImperativeHandle(
    externalViewRef,
    () => ({
      zoomToBox,
      fitToBoard,
      zoomBy,
      exportSvg,
      resetDelta: () => setDeltaOrigin(cursor || { x: 0, y: 0 }),
    }),
    [zoomToBox, fitToBoard, zoomBy, exportSvg, cursor],
  );

  // Leaving measure mode clears the dimension — a stale line over a board you
  // are now editing is worse than no line.
  useEffect(() => {
    if (!measuring) setMeasure(null);
  }, [measuring]);

  // --- wheel zoom (native, non-passive so the page never scrolls)
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return undefined;
    const onWheel = (event) => {
      event.preventDefault();
      const rect = node.getBoundingClientRect();
      const factor = Math.exp(-event.deltaY * 0.0016);
      setView((prev) => zoomAt(prev, event.clientX - rect.left, event.clientY - rect.top, factor));
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, []);

  // --- draw list
  const layerFilter = useMemo(() => {
    if (singleLayerMode === "hide") return new Set([activeLayer]);
    return visibleLayers;
  }, [singleLayerMode, activeLayer, visibleLayers]);

  const drawList = useMemo(
    () => (index ? buildDrawList(index, { visibleLayers: layerFilter, visibleClasses }) : []),
    [index, layerFilter, visibleClasses],
  );

  const highlightIds = highlight?.pcbIds || null;
  const masking = Boolean(highlightIds && highlightIds.size) && highlightMethod !== "normal";
  const dimOpacity = unselectedOpacity(highlightMethod, maskLevel);
  const monochrome = unselectedIsMonochrome(highlightMethod);
  const mutedColor = maskedColor(scheme);

  // --- pointer
  const boardPointFromEvent = useCallback((event) => {
    const node = stageRef.current;
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    return screenToBoard(viewStateRef.current, event.clientX - rect.left, event.clientY - rect.top);
  }, []);

  const onPointerDown = useCallback(
    (event) => {
      if (event.button !== 0) return;
      const point = boardPointFromEvent(event);
      event.currentTarget.setPointerCapture?.(event.pointerId);
      if (measuring && point) {
        // The anchor lives on the drag ref, not in state: a pointermove can
        // arrive before React has re-rendered with the new state, and reading a
        // stale `measure` there makes every fast drag measure zero.
        setMeasure({ from: point, to: point });
        dragRef.current = { mode: "measure", from: point, startX: event.clientX, startY: event.clientY };
        return;
      }
      dragRef.current = {
        mode: "pan",
        startX: event.clientX,
        startY: event.clientY,
        origin: viewStateRef.current,
        moved: false,
      };
    },
    [boardPointFromEvent, measuring],
  );

  const onPointerMove = useCallback(
    (event) => {
      const point = boardPointFromEvent(event);
      if (point) setCursor(point);
      const drag = dragRef.current;

      if (drag?.mode === "measure" && point) {
        const next = { from: drag.from || point, to: point };
        setMeasure(next);
        onMeasureChange?.(next);
        // Keep the HUD live while measuring — the coordinate readout is half
        // the reason you reached for the ruler.
        onHoverChange?.({
          point,
          delta: { x: point.x - next.from.x, y: point.y - next.from.y },
          scale: viewStateRef.current.scale,
        });
        return;
      }
      if (drag?.mode === "pan") {
        const dx = event.clientX - drag.startX;
        const dy = event.clientY - drag.startY;
        if (Math.abs(dx) > CLICK_SLOP_PX || Math.abs(dy) > CLICK_SLOP_PX) drag.moved = true;
        if (drag.moved) {
          setView({ ...drag.origin, tx: drag.origin.tx + dx, ty: drag.origin.ty + dy });
          return;
        }
      }

      if (!index || !point) return;
      const tolerance = 3 / Math.max(1, viewStateRef.current.scale);
      const hit = hitTestPcb(index, point.x, point.y, { visibleLayers: layerFilter, tolerance });
      setHover(hit);
      const origin = deltaOriginRef.current;
      const delta = { x: point.x - origin.x, y: point.y - origin.y };
      onHoverChange?.({ ...(hit || {}), point, delta, scale: viewStateRef.current.scale });
    },
    [boardPointFromEvent, index, layerFilter, onHoverChange, onMeasureChange],
  );

  const onPointerUp = useCallback(
    (event) => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (!drag) return;
      if (drag.mode === "measure") return;
      if (drag.moved) return;

      // A click that did not pan is a selection.
      const point = boardPointFromEvent(event);
      if (!point || !index) return;
      const tolerance = 4 / Math.max(1, viewStateRef.current.scale);
      const hit = hitTestPcb(index, point.x, point.y, { visibleLayers: layerFilter, tolerance });
      const jump = event.metaKey || event.ctrlKey;
      if (!hit) {
        onSelect?.(null, { jump: false, source: "pcb" });
        return;
      }
      const wantNet = event.shiftKey || !hit.componentKey;
      if (wantNet && hit.netKey) onSelect?.({ kind: "net", key: hit.netKey }, { jump, source: "pcb" });
      else if (hit.componentKey) onSelect?.({ kind: "component", key: hit.componentKey }, { jump, source: "pcb" });
      else onSelect?.(null, { jump: false, source: "pcb" });
    },
    [boardPointFromEvent, index, layerFilter, onSelect],
  );

  const endDrag = useCallback(() => {
    dragRef.current = null;
  }, []);

  const onPointerLeave = useCallback(() => {
    dragRef.current = null;
    setHover(null);
    setCursor(null);
    onHoverChange?.(null);
  }, [onHoverChange]);

  // Space zeroes the delta origin (KiCad's relative-origin gesture).
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === " " && cursor && document.activeElement?.tagName !== "INPUT") {
        setDeltaOrigin(cursor);
      }
      if (event.key === "Insert" && cursor) setDeltaOrigin(cursor);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cursor]);

  // --- element painters
  const paint = useCallback(
    (item) => {
      const { element, layer, run } = item;
      const selected = highlightIds ? highlightIds.has(item.id) : false;
      const isHovered = hover?.elementId === item.id;
      const base = elementColor(scheme, element, layer);
      const color = masking && !selected && monochrome ? mutedColor : base;
      const opacity = masking && !selected ? dimOpacity : 1;
      const dimmedByLayer =
        singleLayerMode === "grey" && layer !== activeLayer && element.type !== "pcb_via" && element.type !== "pcb_hole";
      const finalOpacity = dimmedByLayer ? Math.min(opacity, 0.25) : opacity;
      const finalColor = dimmedByLayer ? mutedColor : color;
      const common = {
        opacity: finalOpacity,
        "data-element-id": item.id,
        "data-layer": layer,
      };
      // No drop-shadow on the highlighted set. A per-node SVG filter on a few
      // hundred elements is both slow and visually muddy — the contrast against
      // dimmed neighbours is already the whole signal, and Altium's own "Mask"
      // works exactly that way.
      const emphasis = null;

      switch (element.type) {
        case "pcb_trace":
          return (
            <path
              key={item.key}
              d={polylinePath(run.points)}
              fill="none"
              stroke={finalColor}
              strokeWidth={run.width}
              strokeLinecap="round"
              strokeLinejoin="round"
              style={emphasis}
              {...common}
            />
          );
        case "pcb_smtpad":
        case "pcb_solder_paste": {
          if (element.shape === "circle") {
            const r = NUM(element.radius, NUM(element.width) / 2);
            return <circle key={item.key} cx={NUM(element.x)} cy={NUM(element.y)} r={r} fill={finalColor} style={emphasis} {...common} />;
          }
          if (element.shape === "polygon") {
            return <path key={item.key} d={polygonPath(element.points || [])} fill={finalColor} style={emphasis} {...common} />;
          }
          const w = NUM(element.width);
          const h = NUM(element.height);
          const rotation = NUM(element.ccw_rotation);
          const rect = (
            <rect
              x={-w / 2}
              y={-h / 2}
              width={w}
              height={h}
              rx={Math.min(w, h) * 0.12}
              fill={finalColor}
              style={emphasis}
            />
          );
          return (
            <g key={item.key} transform={`translate(${NUM(element.x)} ${NUM(element.y)}) rotate(${rotation})`} {...common}>
              {rect}
            </g>
          );
        }
        case "pcb_plated_hole": {
          const outerW = NUM(element.outer_width, NUM(element.outer_diameter, 1));
          const outerH = NUM(element.outer_height, NUM(element.outer_diameter, outerW));
          const holeW = NUM(element.hole_width, NUM(element.hole_diameter, outerW * 0.6));
          const holeH = NUM(element.hole_height, NUM(element.hole_diameter, holeW));
          const pad = copperColor(scheme, layer === "bottom" ? "bottom" : "top");
          const padColor = masking && !selected && monochrome ? mutedColor : pad;
          return (
            <g key={item.key} transform={`translate(${NUM(element.x)} ${NUM(element.y)}) rotate(${NUM(element.ccw_rotation)})`} {...common}>
              <rect
                x={-outerW / 2}
                y={-outerH / 2}
                width={outerW}
                height={outerH}
                rx={Math.min(outerW, outerH) / 2}
                fill={padColor}
                style={emphasis}
              />
              <rect
                x={-holeW / 2}
                y={-holeH / 2}
                width={holeW}
                height={holeH}
                rx={Math.min(holeW, holeH) / 2}
                fill={colors.hole}
              />
            </g>
          );
        }
        case "pcb_via": {
          const outer = NUM(element.outer_diameter, 0.6) / 2;
          const inner = NUM(element.hole_diameter, outer) / 2;
          return (
            <g key={item.key} {...common}>
              <circle cx={NUM(element.x)} cy={NUM(element.y)} r={outer} fill={finalColor} style={emphasis} />
              <circle cx={NUM(element.x)} cy={NUM(element.y)} r={inner} fill={colors.hole} />
            </g>
          );
        }
        case "pcb_hole": {
          const r = NUM(element.hole_diameter, NUM(element.hole_width, 1)) / 2;
          return (
            <g key={item.key} {...common}>
              <circle cx={NUM(element.x)} cy={NUM(element.y)} r={r} fill={colors.hole} stroke={finalColor} strokeWidth={r * 0.12} />
            </g>
          );
        }
        case "pcb_silkscreen_path":
          return (
            <path
              key={item.key}
              d={polylinePath(element.route || [])}
              fill="none"
              stroke={finalColor}
              strokeWidth={NUM(element.stroke_width, 0.1)}
              strokeLinecap="round"
              strokeLinejoin="round"
              {...common}
            />
          );
        case "pcb_silkscreen_text": {
          const size = NUM(element.font_size, 0.4);
          const x = NUM(element.anchor_position?.x);
          const y = NUM(element.anchor_position?.y);
          const alignment = String(element.anchor_alignment || "center");
          return (
            <g key={item.key} transform={`translate(${x} ${y}) scale(1 -1) rotate(${-NUM(element.ccw_rotation)})`} {...common}>
              <text
                x={0}
                y={0}
                fill={finalColor}
                fontSize={size}
                textAnchor={TEXT_ANCHOR[alignment] || "middle"}
                dominantBaseline={TEXT_BASELINE[alignment] || "middle"}
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                style={{ pointerEvents: "none" }}
              >
                {String(element.text || "")}
              </text>
            </g>
          );
        }
        case "pcb_silkscreen_rect":
          return (
            <rect
              key={item.key}
              x={NUM(element.center?.x) - NUM(element.width) / 2}
              y={NUM(element.center?.y) - NUM(element.height) / 2}
              width={NUM(element.width)}
              height={NUM(element.height)}
              fill="none"
              stroke={finalColor}
              strokeWidth={NUM(element.stroke_width, 0.1)}
              {...common}
            />
          );
        case "pcb_silkscreen_circle":
          return (
            <circle
              key={item.key}
              cx={NUM(element.center?.x)}
              cy={NUM(element.center?.y)}
              r={NUM(element.radius)}
              fill="none"
              stroke={finalColor}
              strokeWidth={NUM(element.stroke_width, 0.1)}
              {...common}
            />
          );
        case "pcb_courtyard_rect":
          return (
            <rect
              key={item.key}
              x={NUM(element.center?.x) - NUM(element.width) / 2}
              y={NUM(element.center?.y) - NUM(element.height) / 2}
              width={NUM(element.width)}
              height={NUM(element.height)}
              fill="none"
              stroke={finalColor}
              strokeWidth={0.05}
              strokeDasharray="0.3 0.2"
              {...common}
            />
          );
        case "pcb_courtyard_outline":
          return (
            <path
              key={item.key}
              d={polygonPath(element.outline || [])}
              fill="none"
              stroke={finalColor}
              strokeWidth={0.05}
              strokeDasharray="0.3 0.2"
              {...common}
            />
          );
        case "pcb_cutout":
          return (
            <path
              key={item.key}
              d={element.shape === "circle" ? "" : polygonPath(element.points || [])}
              fill={colors.background}
              stroke={colors.boardEdge}
              strokeWidth={0.08}
              {...common}
            />
          );
        default:
          return null;
      }
    },
    [
      activeLayer,
      colors,
      dimOpacity,
      highlightIds,
      hover,
      masking,
      monochrome,
      mutedColor,
      scheme,
      singleLayerMode,
    ],
  );

  const board = index?.board;
  const boardBox = index?.boardBox;
  const gridStep = gridStepMm(view.scale);
  const gridPx = gridStep * view.scale;

  // Selection outline in screen space so it never scales into a fat blob.
  const selectionRect = useMemo(() => {
    if (!highlight || !boxIsReal(highlight.pcbBox)) return null;
    return boxToScreenRect(view, inflateBox(highlight.pcbBox, 0.25));
  }, [highlight, view]);

  const flashRect = useMemo(() => {
    if (!flash?.box || !boxIsReal(flash.box)) return null;
    return boxToScreenRect(view, inflateBox(flash.box, 0.4));
  }, [flash, view]);

  const hoverRect = useMemo(() => {
    if (!hover?.element) return null;
    const box = pcbElementBox(hover.element);
    if (!boxIsReal(box)) return null;
    return boxToScreenRect(view, inflateBox(box, 0.08));
  }, [hover, view]);

  const measureScreen = useMemo(() => {
    if (!measure?.from || !measure?.to) return null;
    const a = boardToScreen(view, measure.from.x, measure.from.y);
    const b = boardToScreen(view, measure.to.x, measure.to.y);
    return { a, b };
  }, [measure, view]);

  const hasGeometry = Boolean(index && index.pcbDrawables.length);

  return (
    <div
      ref={stageRef}
      data-slot="pcb-canvas"
      className={cn(
        "relative min-h-0 flex-1 touch-none select-none overflow-hidden",
        measuring ? "cursor-crosshair" : "cursor-grab active:cursor-grabbing",
        className,
      )}
      style={{ backgroundColor: colors.background }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={endDrag}
      onPointerLeave={onPointerLeave}
      onDoubleClick={fitToBoard}
    >
      {!hasGeometry && fallbackSrc ? (
        <img
          src={fallbackSrc}
          alt="PCB layout"
          draggable={false}
          className="pointer-events-none absolute inset-0 h-full w-full object-contain opacity-90"
        />
      ) : null}

      {hasGeometry ? (
        <svg
          ref={svgRef}
          className="absolute inset-0 h-full w-full"
          width={size.width || 1}
          height={size.height || 1}
          shapeRendering="geometricPrecision"
        >
          <defs>
            <pattern
              id={`grid-${gradientId}`}
              width={gridPx}
              height={gridPx}
              patternUnits="userSpaceOnUse"
              patternTransform={`translate(${view.tx % gridPx} ${view.ty % gridPx})`}
            >
              <path d={`M ${gridPx} 0 L 0 0 0 ${gridPx}`} fill="none" stroke={colors.grid} strokeWidth={1} opacity={0.5} />
            </pattern>
          </defs>

          {showGrid && gridPx >= 6 ? <rect width="100%" height="100%" fill={`url(#grid-${gradientId})`} /> : null}

          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.scale} ${-view.scale})`}>
            {board && boxIsReal(boardBox) ? (
              <rect
                x={boardBox.minX}
                y={boardBox.minY}
                width={boardBox.maxX - boardBox.minX}
                height={boardBox.maxY - boardBox.minY}
                fill={colors.boardFill}
                stroke={colors.boardEdge}
                strokeWidth={0.12}
                opacity={0.95}
              />
            ) : null}
            {drawList.map(paint)}
          </g>

          {/* Screen-space overlays: constant stroke width at any zoom. */}
          {selectionRect ? (
            <rect
              x={selectionRect.x}
              y={selectionRect.y}
              width={selectionRect.width}
              height={selectionRect.height}
              fill="none"
              stroke={colors.selection}
              strokeWidth={1.25}
              strokeDasharray="5 3"
              opacity={0.9}
              data-slot="pcb-selection-outline"
            />
          ) : null}
          {hoverRect && !measuring ? (
            <rect
              x={hoverRect.x}
              y={hoverRect.y}
              width={hoverRect.width}
              height={hoverRect.height}
              fill="none"
              stroke={colors.hover}
              strokeWidth={1}
              opacity={0.45}
            />
          ) : null}
          {flashRect ? (
            <rect
              x={flashRect.x}
              y={flashRect.y}
              width={flashRect.width}
              height={flashRect.height}
              fill="none"
              stroke={colors.drcError}
              strokeWidth={2}
              data-slot="pcb-violation-flash"
              className="board-flash"
            />
          ) : null}
          {measureScreen ? (
            <g data-slot="pcb-measure">
              <line
                x1={measureScreen.a.x}
                y1={measureScreen.a.y}
                x2={measureScreen.b.x}
                y2={measureScreen.b.y}
                stroke={colors.measure}
                strokeWidth={1.25}
              />
              <circle cx={measureScreen.a.x} cy={measureScreen.a.y} r={3} fill="none" stroke={colors.measure} strokeWidth={1.25} />
              <circle cx={measureScreen.b.x} cy={measureScreen.b.y} r={3} fill="none" stroke={colors.measure} strokeWidth={1.25} />
            </g>
          ) : null}
        </svg>
      ) : null}

      {!hasGeometry && !fallbackSrc ? (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <p className="max-w-xs px-6 text-center text-sm leading-6 text-white/50">
            The board layout lands once the board builds.
          </p>
        </div>
      ) : null}

      {/* Live measurement readout follows the second point. */}
      {measureScreen ? (
        <div
          className="pointer-events-none absolute rounded border border-white/20 bg-black/80 px-1.5 py-0.5 font-mono text-[11px] tabular-nums"
          style={{
            left: Math.min(measureScreen.b.x + 12, Math.max(0, size.width - 140)),
            top: Math.max(0, measureScreen.b.y - 26),
            color: colors.measure,
          }}
        >
          {formatMeasure(measure, units)}
        </div>
      ) : null}
    </div>
  );
}

function formatMeasure(measure, units) {
  if (!measure?.from || !measure?.to) return "";
  const dx = measure.to.x - measure.from.x;
  const dy = measure.to.y - measure.from.y;
  const dist = Math.hypot(dx, dy);
  if (units === "mil") {
    const k = 1 / 0.0254;
    return `${(dist * k).toFixed(1)} mil · Δ ${(dx * k).toFixed(1)}, ${(dy * k).toFixed(1)}`;
  }
  return `${dist.toFixed(3)} mm · Δ ${dx.toFixed(3)}, ${dy.toFixed(3)}`;
}
