import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";

const MIN_SCALE = 0.2;
const MAX_SCALE = 24;

/**
 * The one pan/zoom image container both the Schematic and PCB tabs render —
 * wheel zoom (about the cursor), pointer-drag pan, double-click reset. No
 * dependencies: a transformed <img> inside an overflow-hidden stage. The `src`
 * carries the ?v= cache-bust token, so a rebuilt board swaps the image on its
 * own; the view resets per `src` so a new render is never shown half-off-screen.
 *
 * @param {{
 *   src: string,                     // image URL (PNG/SVG, ?v= verbatim)
 *   alt?: string,
 *   emptyText?: string,              // shown when src is ""
 *   overlay?: import("react").ReactNode, // pinned top-right (e.g. layer toggle)
 *   className?: string,
 * }} props
 */
export default function PanZoomView({ src, alt = "", emptyText = "Nothing to show yet.", overlay = null, className }) {
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const stageRef = useRef(null);
  const dragRef = useRef(null);

  // Fresh artifact → fresh framing.
  useEffect(() => {
    setView({ x: 0, y: 0, scale: 1 });
    setLoaded(false);
    setFailed(false);
  }, [src]);

  // React's synthetic wheel listener is passive, so preventDefault (needed to
  // keep the page from scrolling/back-swiping) must attach natively.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    const onWheel = (event) => {
      event.preventDefault();
      const rect = stage.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      setView((prev) => {
        const factor = Math.exp(-event.deltaY * 0.0015);
        const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
        const applied = scale / prev.scale;
        // Keep the point under the cursor fixed while the scale changes.
        return {
          scale,
          x: px - (px - prev.x) * applied,
          y: py - (py - prev.y) * applied,
        };
      });
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, []);

  // Latest view, readable from pointer handlers without re-binding them.
  const viewRef = useRef(view);
  viewRef.current = view;

  const onPointerDown = useCallback((event) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = { startX: event.clientX, startY: event.clientY, origin: viewRef.current };
  }, []);

  const onPointerMove = useCallback((event) => {
    const drag = dragRef.current;
    if (!drag?.origin) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    setView({ ...drag.origin, x: drag.origin.x + dx, y: drag.origin.y + dy });
  }, []);

  const endDrag = useCallback(() => {
    dragRef.current = null;
  }, []);

  const reset = useCallback(() => setView({ x: 0, y: 0, scale: 1 }), []);

  if (!src) {
    return (
      <div className={cn("grid min-h-0 flex-1 place-items-center", className)}>
        <p className="max-w-xs px-6 text-center text-sm leading-6 text-muted-foreground">{emptyText}</p>
      </div>
    );
  }

  return (
    <div
      ref={stageRef}
      data-slot="pan-zoom-view"
      className={cn(
        "relative min-h-0 flex-1 cursor-grab touch-none select-none overflow-hidden active:cursor-grabbing",
        className,
      )}
      style={{ backgroundColor: "var(--ui-viewer-bg)" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={reset}
      title="Scroll to zoom · drag to pan · double-click to reset"
    >
      <div
        className="h-full w-full"
        style={{
          transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
          transformOrigin: "0 0",
        }}
      >
        <img
          src={src}
          alt={alt}
          draggable={false}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
          className="h-full w-full object-contain"
        />
      </div>
      {!loaded && !failed ? (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <Loader2 className="size-5 animate-spin text-white/60" aria-hidden />
        </div>
      ) : null}
      {failed ? (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <p className="text-sm text-white/60">Could not load this view.</p>
        </div>
      ) : null}
      {overlay ? (
        <div className="absolute right-2 top-2 flex items-center gap-1.5">{overlay}</div>
      ) : null}
      {view.scale !== 1 ? (
        <button
          type="button"
          onClick={reset}
          data-slot="pan-zoom-reset"
          className="absolute bottom-2 right-2 rounded-md bg-background/85 px-2 py-1 font-mono text-[11px] text-muted-foreground shadow transition-colors hover:text-foreground"
        >
          {Math.round(view.scale * 100)}% · reset
        </button>
      ) : null}
    </div>
  );
}
