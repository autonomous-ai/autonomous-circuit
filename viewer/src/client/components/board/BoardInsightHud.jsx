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
 * under the cursor, and the active layer. `Shift+H` toggles it.
 *
 * The delta origin is zeroed by `Insert` — Altium's own key, "Insert: Resets
 * the Delta Origin point for the Heads Up Display feature to 0,0"
 * (https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)
 * — **or by clicking the Δ readout**, which is not Altium's and is not
 * decoration: Space used to do this job and was handed to rotation, and a
 * MacBook keyboard has no Insert key, so without the click half our own team
 * would have a Δ frozen at 0,0 forever.
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
  partName = "",
  partRefdes = "",
  partArea = "",
  visible = true,
  measuring = false,
  onResetDelta,
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
        {/* The wrapper is `pointer-events-none` so the HUD never eats a drag
            on the board underneath it; this one control opts back in. */}
        <button
          type="button"
          data-slot="hud-delta-reset"
          onClick={() => onResetDelta?.()}
          disabled={!onResetDelta}
          // Not "here": clicking the HUD means the pointer left the board, so
          // there is no cursor to zero at. The click puts the origin back to
          // the board's own 0,0 — deterministic, and the reading you want when
          // you have lost track of where the delta is measured from. `Insert`
          // keeps Altium's meaning, which is "zero it at the cursor".
          title="Put the delta origin back to the board origin — Insert zeroes it at the cursor"
          className="pointer-events-auto -mx-1 rounded px-1 text-left transition-colors hover:bg-white/10 disabled:pointer-events-none"
        >
          <span className="text-white/35">Δ </span>
          {delta ? formatPoint(delta.x, delta.y, units) : "—"}
        </button>
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
      {/* The one line that says what the thing under the cursor IS. Every
          other field here is a coordinate or an id; this is the only one a
          non-engineer can use, so it gets its own row and the brightest ink. */}
      {partName ? (
        <div data-slot="hud-part" className="mt-0.5 truncate text-[11px] text-white/90">
          <span className="text-white/35">{partRefdes || "Part"} </span>
          {partName}
          {/* Which named area of the board it belongs to. On a layout you have
              never seen, "in The brain" is the difference between a refdes and
              knowing where you are. */}
          {partArea ? <span className="text-white/45"> · in {partArea}</span> : null}
        </div>
      ) : null}
      {measuring ? (
        <div className="mt-0.5 text-[10px] text-amber-300/70">measure — drag across the board · ⌘M off</div>
      ) : null}
    </div>
  );
}
