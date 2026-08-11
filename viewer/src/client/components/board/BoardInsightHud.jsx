import { cn } from "@/ui/utils";
import { LAYER_LABELS, objectLabel } from "@/lib/boardPalette.js";
import { formatPoint } from "@/lib/boardRender.js";

const NUM = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

/**
 * Board Insight — Altium's translucent heads-up display, pinned rather than
 * chasing the cursor (KiCad's better call for a small pane: a HUD that follows
 * the pointer covers the thing you are trying to read).
 *
 * Shows cursor X/Y, the delta from the last origin reset, the object and net
 * under the cursor, and the active layer. `Shift+H` toggles it; `Space` or
 * `Insert` zeroes the delta origin.
 *
 * Parked **top**-left, not bottom-left: the bottom edge now belongs to the
 * floating tool rail and the board-side widget, and two overlapping glass
 * panels in one corner is worse than either. The permanent shortcut legend
 * that used to sit under it is gone too — the rail's tooltips carry those
 * bindings now, and a legend nobody reads twice is furniture.
 */
export default function BoardInsightHud({
  hover = null,
  activeLayer = "top",
  units = "mm",
  scale = 0,
  netName = "",
  visible = true,
  measuring = false,
  className,
}) {
  if (!visible) return null;
  const point = hover?.point || null;
  const delta = hover?.delta || null;
  const element = hover?.element || null;

  return (
    <div
      data-slot="board-insight-hud"
      className={cn(
        "pointer-events-none absolute left-2 top-2 rounded border border-white/10 bg-black/72 px-2 py-1.5 font-mono text-[11px] leading-[1.35] text-white/75 backdrop-blur-sm",
        className,
      )}
    >
      <div className="flex items-center gap-3 tabular-nums">
        <span>
          <span className="text-white/35">X/Y </span>
          {point ? formatPoint(point.x, point.y, units) : "—"}
        </span>
        <span>
          <span className="text-white/35">Δ </span>
          {delta ? formatPoint(delta.x, delta.y, units) : "—"}
        </span>
      </div>
      <div className="mt-0.5 flex items-center gap-3">
        <span>
          <span className="text-white/35">Layer </span>
          {LAYER_LABELS[activeLayer] || activeLayer}
        </span>
        <span>
          <span className="text-white/35">Net </span>
          <span className={netName ? "text-emerald-300/90" : ""}>{netName || "—"}</span>
        </span>
        <span>
          <span className="text-white/35">Obj </span>
          {objectLabel(element) || "—"}
        </span>
        {scale ? <span className="text-white/35">{Math.round(NUM(scale) * 10) / 10} px/mm</span> : null}
      </div>
      {measuring ? (
        <div className="mt-0.5 text-[10px] text-amber-300/70">measure — drag across the board · ⌘M off</div>
      ) : null}
    </div>
  );
}
