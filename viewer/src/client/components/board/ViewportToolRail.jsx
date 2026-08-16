import {
  Contrast,
  Crosshair,
  Grid3x3,
  ImageDown,
  Layers,
  Maximize2,
  Minus,
  Move,
  Plus,
  RotateCcw,
  Ruler,
  SquareDashed,
} from "lucide-react";
import { cn } from "@/ui/utils";
import { dispatchViewportTool, toolState, toolsForSurface } from "./viewportTools.js";

const ICONS = Object.freeze({
  fit: Maximize2,
  minus: Minus,
  move: Move,
  plus: Plus,
  ruler: Ruler,
  crosshair: Crosshair,
  grid: Grid3x3,
  rooms: SquareDashed,
  layers: Layers,
  contrast: Contrast,
  image: ImageDown,
  reset: RotateCcw,
});

/** Units is the one tool whose *value* is the icon — "mm" reads faster than a glyph. */
function ToolGlyph({ tool, value }) {
  if (tool.id === "units") {
    return <span className="font-mono text-[10px] leading-none tabular-nums">{value || "mm"}</span>;
  }
  const Icon = ICONS[tool.icon] || Crosshair;
  return <Icon className="size-3.5" aria-hidden />;
}

/**
 * The floating tool rail — Vibe's glass cluster, with EDA tools in it.
 *
 * The wrapper is `pointer-events-none` and each pill re-enables pointer events,
 * which is the donor's trick and the reason the canvas stays draggable in the
 * gaps between buttons. `bottom` is offset by whatever chrome sits under the
 * pane, the same way CadWorkspace threads `viewportFrameInsets` into its
 * overlays, so opening the Messages panel slides the rail up instead of
 * burying it.
 */
export default function ViewportToolRail({
  surface = "pcb",
  context = {},
  bottomInset = 0,
  rightInset = 0,
  className,
}) {
  const tools = toolsForSurface(surface);
  let lastGroup = "";

  return (
    <div
      data-slot="viewport-tool-rail"
      data-surface={surface}
      className={cn("pointer-events-none absolute left-1/2 z-20 max-w-full px-2", className)}
      style={{
        bottom: `${bottomInset + 12}px`,
        transform: `translateX(calc(-50% - ${rightInset / 2}px))`,
      }}
    >
      <div className="scrollbar-thin pointer-events-auto flex max-w-full items-center gap-0.5 overflow-x-auto rounded-lg border border-white/10 bg-black/70 p-1 text-white/85 shadow-lg backdrop-blur-md">
        {tools.map((tool) => {
          const { active, value } = toolState(tool, context);
          const divider = lastGroup && tool.group !== lastGroup;
          lastGroup = tool.group;
          return (
            <span key={tool.id} className="flex items-center">
              {divider ? <span aria-hidden className="mx-1 h-4 w-px bg-white/15" /> : null}
              <button
                type="button"
                onClick={() => dispatchViewportTool(tool.id, context)}
                data-slot="viewport-tool"
                data-tool={tool.id}
                aria-pressed={tool.state === "action" ? undefined : active}
                aria-label={tool.label}
                title={tool.key ? `${tool.label} (${tool.key})` : tool.label}
                className={cn(
                  "flex h-7 min-w-7 items-center justify-center gap-1 rounded-md px-1.5 transition-colors",
                  active ? "bg-white/90 text-neutral-900" : "hover:bg-white/15",
                )}
              >
                <ToolGlyph tool={tool} value={value} />
                {tool.state === "cycle" && tool.id !== "units" && value ? (
                  <span className="font-mono text-[10px] leading-none">{value}</span>
                ) : null}
              </button>
            </span>
          );
        })}
      </div>
    </div>
  );
}
