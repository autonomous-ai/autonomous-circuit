import { ChevronLeft, ChevronRight, History } from "lucide-react";
import { cn } from "@/ui/utils";
import { describeRevision, dotWindow, stepIndex, worstSeverity } from "./boardRevisions.js";

// The severity colours are the ones the Messages panel already uses (KiCad's
// documented DRC pair, ALTIUM-NOTES §5) so a red dot here and a red row there
// mean the same thing.
const DOT_TONE = Object.freeze({
  error: "bg-[#d75b6b]",
  warning: "bg-[#ffd042]",
  clean: "bg-emerald-500",
});

/**
 * The build pager — Vibe's `1/9` stepper, pointed at a board's own history.
 *
 * Vibe steps meshes; we step *builds*. Each dot is tinted by that build's worst
 * severity, which turns the strip into the shape of the repair loop:
 * red · red · amber · amber · green reads as convergence from across the room,
 * and that is the single most useful thing this workspace can show about an
 * agent that fixes its own board.
 *
 * Windowing (7 dots, shrunken edge dot when more exist beyond) is ported from
 * `SlideDots.tsx` so the control is a fixed width however deep the ring gets.
 */
export default function RevisionPager({
  revisions = [],
  activeIndex = 0,
  onSelect,
  className,
}) {
  const count = revisions.length;
  if (count < 2) return null;

  const active = Math.min(Math.max(activeIndex, 0), count - 1);
  const win = dotWindow(count, active);
  const isLatest = active === count - 1;
  const current = revisions[active];
  const previous = active > 0 ? revisions[active - 1] : null;

  const go = (index) => onSelect?.(stepIndex(index, 0, count));

  return (
    <div
      data-slot="revision-pager"
      data-latest={isLatest ? "true" : "false"}
      className={cn(
        "flex h-7 shrink-0 items-center gap-2 rounded-md border px-2 transition-colors",
        isLatest ? "border-border/50 bg-background/40" : "border-amber-500/50 bg-amber-500/10",
        className,
      )}
      title={describeRevision(current, previous)}
    >
      <History className="size-3 shrink-0 text-muted-foreground" aria-hidden />

      <button
        type="button"
        onClick={() => go(stepIndex(active, -1, count))}
        data-slot="revision-prev"
        aria-label="Previous build"
        className="grid size-4 place-items-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <ChevronLeft className="size-3" aria-hidden />
      </button>

      <span className="shrink-0 font-mono text-[11px] tabular-nums text-foreground">
        {active + 1}/{count}
      </span>

      <button
        type="button"
        onClick={() => go(stepIndex(active, 1, count))}
        data-slot="revision-next"
        aria-label="Next build"
        className="grid size-4 place-items-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <ChevronRight className="size-3" aria-hidden />
      </button>

      <span className="flex items-center gap-1" data-slot="revision-dots">
        {win.indices.map((index) => {
          const revision = revisions[index];
          const tone = worstSeverity(revision?.summary);
          const isActive = index === active;
          const edge = win.isEdge(index);
          return (
            <button
              key={revision?.token || index}
              type="button"
              onClick={() => go(index)}
              data-slot="revision-dot"
              data-tone={tone}
              aria-label={`Build ${index + 1} of ${count}`}
              aria-current={isActive ? "true" : undefined}
              title={describeRevision(revision, index > 0 ? revisions[index - 1] : null)}
              className={cn(
                "rounded-full transition-all",
                DOT_TONE[tone],
                isActive ? "h-2 w-5" : edge ? "size-1 opacity-55" : "size-2 opacity-55 hover:opacity-90",
              )}
            />
          );
        })}
      </span>

      {!isLatest ? (
        <button
          type="button"
          onClick={() => go(count - 1)}
          data-slot="revision-latest"
          className="shrink-0 rounded px-1 text-[10px] font-medium uppercase tracking-wide text-amber-600 transition-colors hover:bg-accent dark:text-amber-400"
        >
          Latest
        </button>
      ) : null}
    </div>
  );
}
