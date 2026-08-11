import { useCallback, useEffect, useId, useImperativeHandle, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import { boxIsReal, inflateBox, schematicElementBox } from "@/lib/boardIndex.js";
import { palette, unselectedOpacity } from "@/lib/boardPalette.js";
import {
  parseSchematicTransform,
  schematicBoxToSvgRect,
  schematicToSvg,
  svgToSchematic,
  svgTwin,
} from "@/lib/boardRender.js";

const NUM = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);
const CLICK_SLOP_PX = 4;

/**
 * The schematic pane. The sheet itself is the pipeline's own `_schematic.svg`
 * — real symbols, real pin labels, the drawing an EE expects — and on top of it
 * sits a live overlay built from the circuit JSON: component boxes, net traces,
 * selection and hover, all in schematic world coordinates.
 *
 * The two are aligned by the `data-real-to-screen-transform` matrix the
 * pipeline writes on the SVG root, so the overlay lands on the symbols to the
 * pixel. When that matrix is missing the pane degrades to a plain pan/zoom
 * image and reports itself as non-interactive rather than drawing highlights in
 * the wrong place.
 *
 * Altium's schematic sheet is light while its PCB canvas is black; we keep that
 * split on purpose — it is what the muscle memory expects.
 */
export default function SchematicCanvas({
  index,
  src = "",
  scheme = "studio",
  highlight = null,
  highlightMethod = "dim",
  maskLevel = 3,
  onSelect,
  onHoverChange,
  className,
  viewRef: externalViewRef,
}) {
  const colors = palette(scheme);
  const stageRef = useRef(null);
  const overlayId = useId().replace(/:/g, "");
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [view, setView] = useState({ scale: 1, tx: 0, ty: 0 });
  const [sheet, setSheet] = useState(null); // {transform, url}
  const [status, setStatus] = useState("idle"); // idle | loading | ready | image-only
  const [hover, setHover] = useState(null);
  const dragRef = useRef(null);
  const viewStateRef = useRef(view);
  viewStateRef.current = view;

  const svgUrl = useMemo(() => svgTwin(src), [src]);

  // --- fetch the sheet, read its world→pixel matrix
  useEffect(() => {
    if (!svgUrl) {
      setSheet(null);
      setStatus(src ? "image-only" : "idle");
      return undefined;
    }
    let cancelled = false;
    setStatus("loading");
    fetch(svgUrl)
      .then((response) => (response.ok ? response.text() : Promise.reject(new Error(String(response.status)))))
      .then((text) => {
        if (cancelled) return;
        const transform = parseSchematicTransform(text);
        if (!transform) {
          setSheet(null);
          setStatus("image-only");
          return;
        }
        setSheet({ transform, url: svgUrl });
        setStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setSheet(null);
        setStatus("image-only");
      });
    return () => {
      cancelled = true;
    };
  }, [svgUrl, src]);

  // --- viewport
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

  const sheetWidth = sheet?.transform.width || 1200;
  const sheetHeight = sheet?.transform.height || 600;

  // Fit to the *drawing*, not the sheet. The pipeline lays the symbols out in
  // the middle of a fixed 1200×600 page, so fitting the page leaves the circuit
  // as a stamp in the corner. The circuit JSON knows where the ink actually is.
  const fitToSheet = useCallback(() => {
    if (!size.width || !size.height) return;
    const content = sheet && index && boxIsReal(index.schematicBox)
      ? schematicBoxToSvgRect(sheet.transform, inflateBox(index.schematicBox, 0.5))
      : null;
    const rect =
      content && content.width > 8 && content.height > 8
        ? content
        : { x: 0, y: 0, width: sheetWidth, height: sheetHeight };
    const scale = Math.min((size.width - 28) / rect.width, (size.height - 28) / rect.height);
    const safe = Math.min(Number.isFinite(scale) && scale > 0 ? scale : 1, 12);
    setView({
      scale: safe,
      tx: size.width / 2 - (rect.x + rect.width / 2) * safe,
      ty: size.height / 2 - (rect.y + rect.height / 2) * safe,
    });
  }, [size.width, size.height, sheetWidth, sheetHeight, sheet, index]);

  const framedRef = useRef("");
  useEffect(() => {
    const token = `${svgUrl}:${Math.round(size.width)}x${Math.round(size.height)}:${sheetWidth}:${index?.stats.elements || 0}`;
    if (!size.width || !size.height) return;
    if (framedRef.current === token) return;
    framedRef.current = token;
    fitToSheet();
  }, [fitToSheet, svgUrl, size.width, size.height, sheetWidth, index]);

  /** Zoom the sheet to a schematic-world box. */
  const zoomToBox = useCallback(
    (box) => {
      if (!sheet || !boxIsReal(box) || !size.width || !size.height) return;
      const rect = schematicBoxToSvgRect(sheet.transform, inflateBox(box, 0.4));
      if (!rect || rect.width <= 0) return;
      const scale = Math.min((size.width - 60) / rect.width, (size.height - 60) / rect.height);
      const safe = Math.min(Number.isFinite(scale) && scale > 0 ? scale : 1, 8);
      setView({
        scale: safe,
        tx: size.width / 2 - (rect.x + rect.width / 2) * safe,
        ty: size.height / 2 - (rect.y + rect.height / 2) * safe,
      });
    },
    [sheet, size.width, size.height],
  );

  /** Zoom about the middle of the pane (the toolbar's +/-, not the wheel). */
  const zoomBy = useCallback(
    (factor) => {
      if (!size.width || !size.height) return;
      const px = size.width / 2;
      const py = size.height / 2;
      setView((prev) => {
        const scale = Math.min(40, Math.max(0.08, prev.scale * factor));
        const applied = scale / prev.scale;
        return { scale, tx: px - (px - prev.tx) * applied, ty: py - (py - prev.ty) * applied };
      });
    },
    [size.width, size.height],
  );

  useImperativeHandle(
    externalViewRef,
    () => ({ zoomToBox, fitToSheet, zoomBy, fitToBoard: fitToSheet }),
    [zoomToBox, fitToSheet, zoomBy],
  );

  // --- wheel zoom
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return undefined;
    const onWheel = (event) => {
      event.preventDefault();
      const rect = node.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      setView((prev) => {
        const factor = Math.exp(-event.deltaY * 0.0016);
        const scale = Math.min(40, Math.max(0.08, prev.scale * factor));
        const applied = scale / prev.scale;
        return { scale, tx: px - (px - prev.tx) * applied, ty: py - (py - prev.ty) * applied };
      });
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, []);

  // --- hit testing in schematic world units
  const worldFromEvent = useCallback(
    (event) => {
      const node = stageRef.current;
      if (!node || !sheet) return null;
      const rect = node.getBoundingClientRect();
      const current = viewStateRef.current;
      const svgX = (event.clientX - rect.left - current.tx) / current.scale;
      const svgY = (event.clientY - rect.top - current.ty) / current.scale;
      return svgToSchematic(sheet.transform, svgX, svgY);
    },
    [sheet],
  );

  const hitTest = useCallback(
    (point, pixelsPerUnit) => {
      if (!index || !point) return null;
      const slack = 6 / Math.max(1e-6, pixelsPerUnit);
      // Components first — a symbol box beats a wire running behind it.
      for (const component of index.components) {
        if (!boxIsReal(component.schematicBox)) continue;
        const box = component.schematicBox;
        if (point.x >= box.minX && point.x <= box.maxX && point.y >= box.minY && point.y <= box.maxY) {
          return { kind: "component", key: component.key, label: component.refdes, box };
        }
      }
      // Then wires: proper segment distance, not bounding boxes.
      let best = null;
      let bestDistance = slack;
      for (const element of index.elements) {
        if (element.type !== "schematic_trace") continue;
        for (const edge of element.edges || []) {
          const d = segmentDistance(point.x, point.y, NUM(edge.from?.x), NUM(edge.from?.y), NUM(edge.to?.x), NUM(edge.to?.y));
          if (d < bestDistance) {
            bestDistance = d;
            const netKey = index.netKeyByElementId.get(element.schematic_trace_id) || "";
            const net = index.netByKey.get(netKey);
            best = { kind: "net", key: netKey, label: net?.name || "", box: schematicElementBox(element) };
          }
        }
      }
      if (best?.key) return best;
      // Finally net labels — the small text stubs are a real click target.
      for (const element of index.elements) {
        if (element.type !== "schematic_net_label") continue;
        const box = inflateBox(schematicElementBox(element), slack);
        if (point.x >= box.minX && point.x <= box.maxX && point.y >= box.minY && point.y <= box.maxY) {
          const netKey = index.netKeyByElementId.get(element.schematic_net_label_id) || "";
          if (netKey) {
            return { kind: "net", key: netKey, label: index.netByKey.get(netKey)?.name || "", box };
          }
        }
      }
      return null;
    },
    [index],
  );

  const onPointerDown = useCallback((event) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      origin: viewStateRef.current,
      moved: false,
    };
  }, []);

  const onPointerMove = useCallback(
    (event) => {
      const drag = dragRef.current;
      if (drag) {
        const dx = event.clientX - drag.startX;
        const dy = event.clientY - drag.startY;
        if (Math.abs(dx) > CLICK_SLOP_PX || Math.abs(dy) > CLICK_SLOP_PX) drag.moved = true;
        if (drag.moved) {
          setView({ ...drag.origin, tx: drag.origin.tx + dx, ty: drag.origin.ty + dy });
          return;
        }
      }
      if (!sheet) return;
      const point = worldFromEvent(event);
      const pixelsPerUnit = Math.abs(sheet.transform.a) * viewStateRef.current.scale;
      const hit = hitTest(point, pixelsPerUnit);
      setHover(hit);
      onHoverChange?.(hit ? { ...hit, point } : { point });
    },
    [hitTest, onHoverChange, sheet, worldFromEvent],
  );

  const onPointerUp = useCallback(
    (event) => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (!drag || drag.moved || !sheet) return;
      const point = worldFromEvent(event);
      const pixelsPerUnit = Math.abs(sheet.transform.a) * viewStateRef.current.scale;
      const hit = hitTest(point, pixelsPerUnit);
      const jump = event.metaKey || event.ctrlKey;
      if (!hit) {
        onSelect?.(null, { jump: false, source: "schematic" });
        return;
      }
      onSelect?.({ kind: hit.kind, key: hit.key }, { jump, source: "schematic" });
    },
    [hitTest, onSelect, sheet, worldFromEvent],
  );

  const endDrag = useCallback(() => {
    dragRef.current = null;
  }, []);

  // --- overlay geometry
  const highlightIds = highlight?.schematicIds || null;
  const masking = Boolean(highlightIds && highlightIds.size) && highlightMethod !== "normal";
  const sheetOpacity = masking ? Math.max(0.25, unselectedOpacity(highlightMethod, maskLevel) + 0.18) : 1;

  const highlightPaths = useMemo(() => {
    if (!sheet || !index || !highlightIds || !highlightIds.size) return [];
    const out = [];
    for (const id of highlightIds) {
      const element = index.byId.get(id);
      if (element?.type !== "schematic_trace") continue;
      for (const edge of element.edges || []) {
        const a = schematicToSvg(sheet.transform, NUM(edge.from?.x), NUM(edge.from?.y));
        const b = schematicToSvg(sheet.transform, NUM(edge.to?.x), NUM(edge.to?.y));
        out.push({ key: `${id}:${out.length}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y });
      }
    }
    return out;
  }, [sheet, index, highlightIds]);

  const highlightBoxes = useMemo(() => {
    if (!sheet || !index || !highlight) return [];
    const out = [];
    for (const key of highlight.componentKeys || []) {
      const component = index.componentBySourceId.get(key);
      if (!component || !boxIsReal(component.schematicBox)) continue;
      const rect = schematicBoxToSvgRect(sheet.transform, inflateBox(component.schematicBox, 0.06));
      if (rect) out.push({ key, ...rect });
    }
    return out;
  }, [sheet, index, highlight]);

  const hoverRect = useMemo(() => {
    if (!sheet || !hover?.box || !boxIsReal(hover.box)) return null;
    return schematicBoxToSvgRect(sheet.transform, inflateBox(hover.box, 0.04));
  }, [sheet, hover]);

  const showSheet = Boolean(sheet || src);

  return (
    <div
      ref={stageRef}
      data-slot="schematic-canvas"
      data-interactive={status === "ready" ? "true" : "false"}
      className={cn(
        "relative min-h-0 flex-1 cursor-grab touch-none select-none overflow-hidden active:cursor-grabbing",
        className,
      )}
      style={{ backgroundColor: "#0c0f14" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={endDrag}
      onPointerLeave={() => {
        endDrag();
        setHover(null);
        onHoverChange?.(null);
      }}
      onDoubleClick={fitToSheet}
    >
      {showSheet ? (
        <div
          className="absolute left-0 top-0 origin-top-left"
          style={{ transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})` }}
        >
          <img
            src={sheet?.url || src}
            alt="Schematic"
            draggable={false}
            width={sheetWidth}
            height={sheetHeight}
            // maxWidth:none defeats the CSS reset's `img { max-width: 100% }`,
            // which would otherwise squash the sheet to the pane width and
            // break the overlay's pixel alignment.
            style={{
              width: sheetWidth,
              height: sheetHeight,
              maxWidth: "none",
              opacity: sheetOpacity,
              transition: "opacity 120ms",
            }}
          />
          {sheet ? (
            <svg
              className="pointer-events-none absolute left-0 top-0"
              width={sheetWidth}
              height={sheetHeight}
              viewBox={`0 0 ${sheetWidth} ${sheetHeight}`}
              data-slot="schematic-overlay"
              id={`schematic-overlay-${overlayId}`}
            >
              {highlightPaths.map((line) => (
                <line
                  key={line.key}
                  x1={line.x1}
                  y1={line.y1}
                  x2={line.x2}
                  y2={line.y2}
                  stroke={colors.selection}
                  strokeWidth={3 / Math.max(0.4, view.scale)}
                  strokeLinecap="round"
                  opacity={0.95}
                />
              ))}
              {highlightBoxes.map((rect) => (
                <rect
                  key={rect.key}
                  x={rect.x}
                  y={rect.y}
                  width={rect.width}
                  height={rect.height}
                  fill={colors.selection}
                  fillOpacity={0.1}
                  stroke={colors.selection}
                  strokeWidth={2 / Math.max(0.4, view.scale)}
                  strokeDasharray={`${6 / Math.max(0.4, view.scale)} ${4 / Math.max(0.4, view.scale)}`}
                />
              ))}
              {hoverRect ? (
                <rect
                  x={hoverRect.x}
                  y={hoverRect.y}
                  width={hoverRect.width}
                  height={hoverRect.height}
                  fill="none"
                  stroke="#1a6dd0"
                  strokeWidth={1.5 / Math.max(0.4, view.scale)}
                  opacity={0.8}
                />
              ) : null}
            </svg>
          ) : null}
        </div>
      ) : (
        <div className="grid h-full w-full place-items-center">
          <p className="max-w-xs px-6 text-center text-sm leading-6 text-white/50">
            The schematic lands once the board builds.
          </p>
        </div>
      )}

      {status === "loading" ? (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <Loader2 className="size-5 animate-spin text-white/50" aria-hidden />
        </div>
      ) : null}

      {status === "image-only" && src ? (
        <p className="pointer-events-none absolute bottom-2 left-2 rounded border border-amber-500/40 bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-amber-300/90">
          sheet not aligned — cross-probe off
        </p>
      ) : null}
    </div>
  );
}

function segmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lengthSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}
