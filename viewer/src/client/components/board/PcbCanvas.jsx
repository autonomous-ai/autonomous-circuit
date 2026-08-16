import { useCallback, useEffect, useId, useImperativeHandle, useMemo, useRef, useState } from "react";
import { cn } from "@/ui/utils";
import {
  boxIsReal,
  elementId as elementIdOf,
  hitTestPcb,
  inflateBox,
  pcbElementBox,
  pcbElementContains,
} from "@/lib/boardIndex.js";
import { FINE_STEP_MM, formatMm, snapDelta } from "./boardSource.js";
import {
  canvasKeyAction,
  escapeLiveCommand,
  panView,
  pointerPressAction,
  pointerReleaseAction,
  takeKeyboardFromTyping,
  wheelAction,
} from "./canvasPointer.js";
import {
  CLICK_SLOP_PX,
  beginMove,
  cancelMove,
  commitMove,
  placementForHit as placementForHitOf,
  refusalForHit,
  renderOffset,
  trackMove,
} from "./placementDrag.js";
import { LABEL_CHAR_PX, LABEL_PAD_PX, roomOverlay } from "@/lib/boardRegions.js";
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

/** Pick order inside one placement — pads beat silkscreen beats courtyard. */
const PLACEMENT_HIT_RANK = {
  pcb_smtpad: 40,
  pcb_plated_hole: 40,
  pcb_hole: 30,
  pcb_silkscreen_text: 12,
  pcb_silkscreen_path: 10,
  pcb_silkscreen_rect: 10,
  pcb_silkscreen_circle: 10,
  pcb_cutout: 8,
  pcb_courtyard_rect: 5,
  pcb_courtyard_outline: 5,
};

/**
 * Hit-test a point against ONE placement's own geometry.
 *
 * `hitTestPcb` ranks across the whole board, which is right for selection and
 * wrong here: when a moved part is probed at its pre-move coordinates, a trace
 * that happens to run over that spot outranks the part's own pad and the
 * probe reports "not this placement". The part then became undraggable after
 * its first move — visible only by dragging the same part twice in the real
 * app. Restricting the search to what the placement owns removes the contest.
 */
function hitWithinPlacement(index, placement, x, y, tolerance, visibleLayers) {
  let best = null;
  let bestRank = -1;
  for (const id of placement.pcbIds) {
    const element = index.byId.get(id);
    if (!element) continue;
    const rank = PLACEMENT_HIT_RANK[element.type];
    if (rank === undefined || rank < bestRank) continue;
    if (visibleLayers && element.layer && !visibleLayers.has(element.layer)) continue;
    if (!pcbElementContains(element, x, y, tolerance, visibleLayers)) continue;
    best = element;
    bestRank = rank;
  }
  if (best) {
    return {
      elementId: elementIdOf(best),
      element: best,
      netKey: index.netKeyByElementId.get(elementIdOf(best)) || "",
      componentKey: index.componentKeyByElementId.get(elementIdOf(best)) || "",
      layer: String(best.layer || "top"),
    };
  }
  // The empty middle of a connector or a QFN still belongs to the part, the
  // same last resort `hitTestPcb` takes.
  for (const key of placement.componentKeys) {
    const component = index.componentBySourceId.get(key);
    const pcb = component?.pcb;
    if (!pcb) continue;
    if (visibleLayers && pcb.layer && !visibleLayers.has(pcb.layer)) continue;
    const box = pcbElementBox(pcb);
    if (!boxIsReal(box)) continue;
    if (x < box.minX || x > box.maxX || y < box.minY || y > box.maxY) continue;
    return {
      elementId: component.pcbId,
      element: pcb,
      netKey: "",
      componentKey: component.key,
      layer: String(pcb.layer || "top"),
    };
  }
  return null;
}

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
 *   · move mode      drag a part; on release the board SOURCE is rewritten
 *
 * Move mode is the one interaction here that changes something. It moves only
 * what the board file can move (see boardSource.js) and it moves only the
 * part — the copper stays where the router left it, because after the drag it
 * *is* where the router left it, and drawing it snapped to the new position
 * would be the canvas telling a story the board cannot back up.
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
  regions = null,
  showRegions = false,
  flash = null,
  fallbackSrc = "",
  // --- move mode
  editing = false,
  placements = null,
  snapStepMm = 0.5,
  onPlacementMove,
  onPlacementRotate,
  onPlacementSelect,
  onContextMenuRequest,
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
  // The live move session (placementDrag.beginMove). Null whenever nothing is
  // being dragged — which is also what a cancelled drag leaves behind.
  const [move, setMove] = useState(null);
  const dragRef = useRef(null);
  const viewStateRef = useRef(view);
  viewStateRef.current = view;
  const deltaOriginRef = useRef(deltaOrigin);
  deltaOriginRef.current = deltaOrigin;

  /** The placement a hit belongs to, or null. Element id first: a mounting
   *  hole and a silkscreen label have no component behind them. */
  const placementForHit = useCallback(
    (hit) => {
      if (!editing || !hit || !placements) return null;
      const id =
        placements.byElementId?.get(hit.elementId) ||
        (hit.componentKey ? placements.byComponentKey?.get(hit.componentKey) : "");
      return id ? placements.byId?.get(id) || null : null;
    },
    [editing, placements],
  );


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
      const action = wheelAction(event);
      if (action.kind === "pan") {
        setView((prev) => panView(prev, action.dx, action.dy));
        return;
      }
      const rect = node.getBoundingClientRect();
      setView((prev) => zoomAt(prev, event.clientX - rect.left, event.clientY - rect.top, action.factor));
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

  /**
   * Placements the board file has already moved and the board has not been
   * rebuilt for.
   *
   * They are drawn at the position the FILE gives, because the file is the
   * board; their copper stays where the last router run left it, because that
   * is the only place copper has ever been. The two visibly disagreeing is the
   * point — it is what "rebuild" means, drawn.
   */
  const shifted = useMemo(() => {
    if (!editing || !placements?.byId) return [];
    return [...placements.byId.values()].filter((p) => p.offset && (p.offset.dx || p.offset.dy));
  }, [editing, placements]);

  /**
   * Hit-test, honouring pending offsets.
   *
   * A moved part is drawn `offset` away from its own geometry, so the pointer
   * is un-shifted before it is tested against that geometry — and the spot the
   * part used to occupy is empty board now. Without this a part could be
   * picked up exactly once: the second grab would land where it used to be.
   */
  const hitAt = useCallback(
    (point) => {
      if (!index || !point) return null;
      const tolerance = 4 / Math.max(1, viewStateRef.current.scale);
      for (const placement of shifted) {
        const hit = hitWithinPlacement(
          index,
          placement,
          point.x - placement.offset.dx,
          point.y - placement.offset.dy,
          tolerance,
          layerFilter,
        );
        if (hit) return hit;
      }
      const hit = hitTestPcb(index, point.x, point.y, { visibleLayers: layerFilter, tolerance });
      if (!hit) return null;
      for (const placement of shifted) if (placement.pcbIds.has(hit.elementId)) return null;
      return hit;
    },
    [index, layerFilter, shifted],
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
      // First, the keyboard. See `takeKeyboardFromTyping`: the composer holds
      // focus from load, so until this runs every board key is dead and
      // nothing says why.
      takeKeyboardFromTyping(typeof document === "undefined" ? null : document.activeElement);
      const point = boardPointFromEvent(event);
      // What this press means is decided in one place for both canvases —
      // `canvasPointer.pointerPressAction` — rather than by a chain of `if`s
      // that drifts apart between them. This canvas rejected `button !== 0`
      // outright, which is why right-drag-to-pan and the context menu, both
      // shipped in the arbiter and both tested, never reached the surface an
      // EE actually uses.
      const canGrab = Boolean(
        editing && point && index && (() => {
          const placement = placementForHit(hitAt(point));
          return Boolean(placement) && !placement.locked;
        })(),
      );
      const action = pointerPressAction({
        button: event.button,
        measuring: Boolean(measuring && point),
        canGrab,
      });
      if (action === "ignore") return;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      if (action === "pan") {
        dragRef.current = {
          mode: "pan",
          button: event.button,
          startX: event.clientX,
          startY: event.clientY,
          origin: viewStateRef.current,
          moved: false,
        };
        return;
      }
      if (measuring && point) {
        // The anchor lives on the drag ref, not in state: a pointermove can
        // arrive before React has re-rendered with the new state, and reading a
        // stale `measure` there makes every fast drag measure zero.
        setMeasure({ from: point, to: point });
        dragRef.current = {
          mode: "measure", button: event.button, from: point,
          startX: event.clientX, startY: event.clientY,
        };
        return;
      }
      if (editing && point && index) {
        const placement = placementForHit(hitAt(point));
        // A locked placement is a decision someone made on purpose; dragging
        // it by accident is exactly what the lock is for. It still selects.
        if (placement && !placement.locked) {
          dragRef.current = {
            mode: "move",
            button: event.button,
            placement,
            from: point,
            startX: event.clientX,
            startY: event.clientY,
            moved: false,
          };
          setMove({ placement, dx: 0, dy: 0 });
          return;
        }
      }
      dragRef.current = {
        mode: "pan",
        button: event.button,
        startX: event.clientX,
        startY: event.clientY,
        origin: viewStateRef.current,
        moved: false,
      };
    },
    [boardPointFromEvent, editing, hitAt, index, measuring, placementForHit],
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
      if (drag?.mode === "move" && point) {
        if (
          Math.abs(event.clientX - drag.startX) > CLICK_SLOP_PX ||
          Math.abs(event.clientY - drag.startY) > CLICK_SLOP_PX
        ) {
          drag.moved = true;
        }
        // Alt is the escape hatch from the grid, the way it is in every EDA
        // tool. The grid snaps the *movement*, not the position — see
        // boardSource.snapDelta for why an absolute grid breaks 2.54mm pitch.
        const step = event.altKey ? FINE_STEP_MM : snapStepMm;
        const dx = snapDelta(point.x - drag.from.x, step);
        const dy = snapDelta(point.y - drag.from.y, step);
        setMove((prev) =>
          prev && prev.dx === dx && prev.dy === dy ? prev : { placement: drag.placement, dx, dy },
        );
        onHoverChange?.({
          point,
          delta: { x: dx, y: dy },
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
      const hit = hitAt(point);
      setHover(hit);
      const origin = deltaOriginRef.current;
      const delta = { x: point.x - origin.x, y: point.y - origin.y };
      onHoverChange?.({ ...(hit || {}), point, delta, scale: viewStateRef.current.scale });
    },
    [boardPointFromEvent, hitAt, index, onHoverChange, onMeasureChange, snapStepMm],
  );

  /**
   * The right button, mid-command.
   *
   * `pointerdown` never fires for a second button while one is held, so the
   * arbiter's `"cancel"` was unreachable from a press and an EE who
   * right-clicked to get out of a drag watched the move commit anyway.
   * `contextmenu` does fire. `escapeLiveCommand` decides, and it refuses to
   * cancel a *pan*, because on macOS a right-drag fires this event on its own
   * first frame and killing it there would break every pan.
   */
  const onContextMenu = useCallback((event) => {
    if (!escapeLiveCommand(dragRef.current)) return;
    event.preventDefault();
    dragRef.current = null;
    setMove(null);
    setMeasure(null);
    onMeasureChange?.(null);
  }, [onMeasureChange]);

  const onPointerUp = useCallback(
    (event) => {
      const drag = dragRef.current;
      dragRef.current = null;
      const release = pointerReleaseAction(drag);
      if (release === "none") return;

      // A right press that never travelled is Altium's context menu; one that
      // travelled was a pan and is already done. Deciding it here rather than
      // on the browser's `contextmenu` event is what keeps the two apart —
      // macOS fires `contextmenu` on press, before the pointer can travel.
      if (release === "menu") {
        const point = boardPointFromEvent(event);
        onContextMenuRequest?.({
          point,
          hit: point && index ? hitAt(point) : null,
          placement: point && index ? placementForHit(hitAt(point)) : null,
          client: { x: event.clientX, y: event.clientY },
          source: "pcb",
        });
        return;
      }
      if (drag.mode === "move") {
        const dropped = move;
        setMove(null);
        if (drag.moved && dropped && (dropped.dx !== 0 || dropped.dy !== 0)) {
          onPlacementSelect?.(drag.placement);
          onPlacementMove?.(drag.placement, {
            x: drag.placement.x + dropped.dx,
            y: drag.placement.y + dropped.dy,
            dx: dropped.dx,
            dy: dropped.dy,
          });
          return;
        }
        // A press that went nowhere is still a click, and a click selects.
      } else if (drag.moved) {
        return;
      }

      // A click that did not pan is a selection.
      const point = boardPointFromEvent(event);
      if (!point || !index) return;
      const hit = hitAt(point);
      // In move mode a click also picks the placement the edit bar acts on.
      // Selection cannot carry that on its own: a mounting hole and a
      // silkscreen label have no component and no net to select.
      if (editing) onPlacementSelect?.(placementForHit(hit));
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
    [
      boardPointFromEvent,
      editing,
      hitAt,
      index,
      move,
      onPlacementMove,
      onPlacementSelect,
      onSelect,
      placementForHit,
    ],
  );

  const endDrag = useCallback(() => {
    dragRef.current = null;
    setMove(null);
  }, []);

  const onPointerLeave = useCallback(() => {
    dragRef.current = null;
    setMove(null);
    setHover(null);
    setCursor(null);
    onHoverChange?.(null);
  }, [onHoverChange]);

  // Every key this canvas answers is decided by `canvasKeyAction`, never here.
  //
  // The handler this replaces got two things wrong that only a real keyboard
  // finds. It guarded on `activeElement?.tagName !== "INPUT"`, so typing a
  // space into the chat composer — a `<textarea>` — silently moved the delta
  // origin and every Δ in the HUD changed with nothing to say why. And it
  // spent Space on the delta origin, which is Altium's rotate-while-dragging
  // key; `Insert` is Altium's own key for the origin and is now the only one.
  //
  // Routing through the arbiter is also what lets the shortcut sheet stay
  // honest: the sheet probes that pure function, so a key answered here can
  // never be a key the sheet has never heard of.
  useEffect(() => {
    const onKey = (event) => {
      const action = canvasKeyAction(event, {
        dragging: Boolean(dragRef.current),
        hasCursor: Boolean(cursor),
      });
      if (!action) return;
      if (action.type === "cancel-drag") {
        // Abandon the move and keep the selection: the part the user grabbed
        // is still the part they are thinking about.
        dragRef.current = null;
        setMove(null);
        setMeasure(null);
        onMeasureChange?.(null);
        return;
      }
      if (action.type === "delta-origin" && cursor) {
        setDeltaOrigin(cursor);
        return;
      }
      if (action.type === "rotate") {
        // Altium's own gesture: Spacebar turns whatever is on the cursor,
        // Shift+Spacebar turns it the other way. Always `preventDefault` —
        // a spacebar that pages the board down mid-drag is worse than one
        // that does nothing — and then actually turn the part.
        //
        // The key was answered by the arbiter and swallowed here for a day,
        // with a comment saying this canvas had no callback to reach the
        // editor. It had one: `BoardWorkspace` was already passing
        // `onPlacementRotate` and this file never declared the prop. Two
        // halves of one wire, each complete, neither connected — the same
        // shape as the right button and as `board_fast_check`.
        event.preventDefault();
        const drag = dragRef.current;
        if (drag?.mode !== "move" || !drag.placement) return;
        const refusal = onPlacementRotate?.(drag.placement, { direction: action.direction });
        // The drag keeps going either way: the turn is its own edit, and the
        // position still commits on mouse-up. A refusal (locked, or a part
        // whose source cannot carry an angle) is shown where the user is
        // already looking — the live readout under the cursor.
        setMove((prev) => (prev ? { ...prev, note: refusal || "" } : prev));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cursor, onMeasureChange]);

  // Leaving move mode must not leave a half-finished drag behind it.
  useEffect(() => {
    if (!editing) {
      dragRef.current = null;
      setMove(null);
    }
  }, [editing]);

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

  // --- move mode overlays
  //
  // Three sets, not one. Copper is in none of them: the traces were routed to
  // the old position and they still are, and letting them follow a part would
  // be the canvas promising a board nobody has built.
  //
  //   still     everything the last build placed and nobody has moved
  //   pending   parts the file has moved, drawn where the file puts them
  //   moving    the part under the pointer right now
  const paintSets = useMemo(() => {
    const movingIds = move ? move.placement.pcbIds : null;
    const still = [];
    const moving = [];
    const pending = new Map();
    for (const item of drawList) {
      if (movingIds?.has(item.id)) {
        moving.push(item);
        continue;
      }
      const owner = shifted.find((placement) => placement.pcbIds.has(item.id));
      if (!owner) {
        still.push(item);
        continue;
      }
      const bucket = pending.get(owner.id);
      if (bucket) bucket.items.push(item);
      else pending.set(owner.id, { offset: owner.offset, items: [item] });
    }
    return { still, moving, pending: [...pending.entries()].map(([id, value]) => ({ id, ...value })) };
  }, [drawList, move, shifted]);

  // Where the part being dragged will land, including any move it was already
  // carrying from an earlier drag.
  const moveDelta = move
    ? { dx: (move.placement.offset?.dx || 0) + move.dx, dy: (move.placement.offset?.dy || 0) + move.dy }
    : null;
  const moveTargetRect = useMemo(() => {
    if (!move || !moveDelta || !boxIsReal(move.placement.box)) return null;
    const box = move.placement.box;
    return boxToScreenRect(view, {
      minX: box.minX + moveDelta.dx - 0.2,
      minY: box.minY + moveDelta.dy - 0.2,
      maxX: box.maxX + moveDelta.dx + 0.2,
      maxY: box.maxY + moveDelta.dy + 0.2,
    });
  }, [move, moveDelta?.dx, moveDelta?.dy, view]);

  // What the pointer would pick up if it were pressed now. Editing without
  // this is a guessing game: most of a board is copper nobody can drag.
  const hoverPlacement = useMemo(() => (move ? null : placementForHit(hover)), [move, hover, placementForHit]);
  const hoverPlacementRect = useMemo(() => {
    if (!hoverPlacement || !boxIsReal(hoverPlacement.box)) return null;
    const { dx = 0, dy = 0 } = hoverPlacement.offset || {};
    const box = hoverPlacement.box;
    return boxToScreenRect(
      view,
      inflateBox({ minX: box.minX + dx, minY: box.minY + dy, maxX: box.maxX + dx, maxY: box.maxY + dy }, 0.2),
    );
  }, [hoverPlacement, view]);

  // Rooms — a named rectangle per area of the board. Altium draws exactly this
  // and calls it a room; without it the layout is two hundred pads and no way
  // to tell the power supply from the radio. The rules that keep it a map
  // rather than clutter live in boardRegions.roomOverlay, where node:test can
  // reach them.
  const roomRects = useMemo(
    () => (showRegions ? roomOverlay(regions, { view, boardBox }) : []),
    [showRegions, regions, view, boardBox],
  );

  return (
    <div
      ref={stageRef}
      data-slot="pcb-canvas"
      data-editing={editing ? "true" : undefined}
      className={cn(
        "relative min-h-0 flex-1 touch-none select-none overflow-hidden",
        measuring
          ? "cursor-crosshair"
          : move || (editing && hoverPlacement && !hoverPlacement.locked)
            ? "cursor-move"
            : "cursor-grab active:cursor-grabbing",
        className,
      )}
      style={{ backgroundColor: colors.background }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onContextMenu={onContextMenu}
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
            {/* Everything not moving drops back while a drag is live. On a
                dense board the copper is most of the ink, and without this the
                part you are carrying is invisible inside it. */}
            {move ? <g opacity={0.3}>{paintSets.still.map(paint)}</g> : paintSets.still.map(paint)}
            {paintSets.pending.map((group) => (
              <g
                key={group.id}
                data-slot="pcb-pending-move"
                data-placement={group.id}
                opacity={move ? 0.3 : 1}
                transform={`translate(${group.offset.dx} ${group.offset.dy})`}
              >
                {group.items.map(paint)}
              </g>
            ))}
            {paintSets.moving.length ? (
              <g data-slot="pcb-move-origin" opacity={0.18}>
                {paintSets.moving.map(paint)}
              </g>
            ) : null}
            {paintSets.moving.length ? (
              <g data-slot="pcb-move-ghost" transform={`translate(${moveDelta.dx} ${moveDelta.dy})`}>
                {paintSets.moving.map(paint)}
              </g>
            ) : null}
          </g>

          {/* Screen-space overlays: constant stroke width at any zoom. */}
          {roomRects.length ? (
            <g data-slot="pcb-rooms" style={{ pointerEvents: "none" }}>
              {roomRects.map((room) => (
                <g key={room.id} data-slot="pcb-room" data-label={room.label}>
                  <rect
                    x={room.rect.x}
                    y={room.rect.y}
                    width={room.rect.width}
                    height={room.rect.height}
                    fill={colors.room}
                    fillOpacity={0.05}
                    stroke={colors.room}
                    strokeWidth={1}
                    strokeDasharray="4 3"
                    opacity={0.6}
                    rx={2}
                  />
                  {room.showLabel ? (
                    <>
                      <rect
                        x={room.rect.x}
                        y={Math.max(0, room.labelY)}
                        width={room.label.length * LABEL_CHAR_PX + LABEL_PAD_PX}
                        height={13}
                        fill={colors.background}
                        fillOpacity={0.82}
                        rx={2}
                      />
                      <text
                        x={room.rect.x + 4}
                        y={Math.max(0, room.labelY) + 9.5}
                        fill={colors.roomText}
                        fontSize={10}
                        fontFamily="ui-sans-serif, system-ui, -apple-system, sans-serif"
                        opacity={0.95}
                      >
                        {room.label}
                      </text>
                    </>
                  ) : null}
                </g>
              ))}
            </g>
          ) : null}
          {hoverPlacementRect ? (
            <rect
              x={hoverPlacementRect.x}
              y={hoverPlacementRect.y}
              width={hoverPlacementRect.width}
              height={hoverPlacementRect.height}
              fill="none"
              stroke={hoverPlacement.locked ? colors.drcError : colors.measure}
              strokeWidth={1.25}
              strokeDasharray={hoverPlacement.locked ? "2 2" : undefined}
              opacity={0.8}
              data-slot="pcb-move-target"
              data-placement={hoverPlacement.id}
              data-locked={hoverPlacement.locked ? "true" : "false"}
            />
          ) : null}
          {moveTargetRect ? (
            <rect
              x={moveTargetRect.x}
              y={moveTargetRect.y}
              width={moveTargetRect.width}
              height={moveTargetRect.height}
              fill="none"
              stroke={colors.measure}
              strokeWidth={1.5}
              strokeDasharray="5 3"
              data-slot="pcb-move-outline"
            />
          ) : null}
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

      {/* What this drag will write, in the units the source is written in.
          Shown while dragging because the number IS the edit — after the drop
          it is a line in the board file. */}
      {move && moveTargetRect ? (
        <div
          data-slot="pcb-move-readout"
          className="pointer-events-none absolute rounded border border-white/20 bg-black/85 px-2 py-1 font-mono text-[11px] leading-4 tabular-nums text-white"
          style={{
            left: Math.min(moveTargetRect.x + moveTargetRect.width + 10, Math.max(0, size.width - 190)),
            top: Math.max(0, moveTargetRect.y - 8),
          }}
        >
          <span className="text-white/60">{move.placement.label}</span>
          {"  "}
          <span style={{ color: colors.measure }}>
            Δ {formatMm(move.dx)}, {formatMm(move.dy)} mm
          </span>
          <br />
          <span className="text-white/50">
            pcbX={formatMm(move.placement.x + move.dx)} pcbY={formatMm(move.placement.y + move.dy)}
          </span>
          {/* Why a turn did not happen, under the cursor rather than in a
              panel the user is not looking at mid-drag. */}
          {move.note ? (
            <>
              <br />
              <span data-slot="pcb-move-note" className="text-amber-300">
                {move.note}
              </span>
            </>
          ) : null}
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
