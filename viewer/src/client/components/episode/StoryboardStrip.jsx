import { useMemo } from "react";
import { CircleAlert, MessageSquarePlus } from "lucide-react";
import { cn } from "@/ui/utils";
import { formatTimecode } from "@/lib/frameContext.js";
import { normalizeShots, shotOffsets, shotIndexAtTime } from "./storyboardModel.js";

/**
 * Horizontal shot strip under the player. Shots come from the selected
 * episode's `.episode.json` sidecar (ids, durations, status when present),
 * falling back to the catalog entry's `artifact.shots[]` before the sidecar
 * fetch lands. Clicking a shot seeks the player to its cumulative start
 * offset; a failed shot is a red chip whose click pre-fills the chat with a
 * failure note instead. Hovering any chip reveals a note affordance that
 * pre-fills "Shot <id> (at <mm:ss>): " so the creator can type taste notes and
 * send them as a normal turn — the chat-driven "regenerate only the affected
 * shots" loop, no new commands.
 *
 * @param {{
 *   sidecar?: object|null,             // parsed .episode.json (or null while loading)
 *   artifactShots?: Array<object>,     // entry.artifact.shots fallback
 *   episodeDurationS?: number,
 *   currentTimeS?: number,
 *   onSeek?: (seconds: number) => void,
 *   onPrefillNote?: (text: string) => void,
 *   className?: string,
 * }} props
 */
export default function StoryboardStrip({
  sidecar,
  artifactShots,
  episodeDurationS,
  currentTimeS = 0,
  onSeek,
  onPrefillNote,
  className,
}) {
  const shots = useMemo(
    () => normalizeShots(sidecar, artifactShots),
    [sidecar, artifactShots],
  );
  const offsets = useMemo(
    () => shotOffsets(shots, episodeDurationS),
    [shots, episodeDurationS],
  );
  const activeIndex = shotIndexAtTime(offsets, currentTimeS);

  if (!shots.length) {
    return (
      <div
        data-slot="storyboard-strip"
        className={cn(
          "flex h-20 shrink-0 items-center justify-center border-t border-border/60 text-xs text-muted-foreground",
          className,
        )}
      >
        Shots appear here once the episode renders.
      </div>
    );
  }

  return (
    <div
      data-slot="storyboard-strip"
      className={cn(
        "scrollbar-thin flex h-20 shrink-0 items-stretch gap-1.5 overflow-x-auto border-t border-border/60 px-3 py-2.5",
        className,
      )}
    >
      {shots.map((shot, index) => {
        const failed = shot.status === "failed";
        const startS = offsets[index]?.startS ?? 0;
        const noteText = failed
          ? `Shot ${shot.id} failed — `
          : `Shot ${shot.id} (at ${formatTimecode(startS)}): `;
        return (
          <div
            key={shot.id}
            data-slot="storyboard-shot"
            data-status={shot.status}
            className={cn(
              "group relative flex min-w-24 shrink-0 flex-col justify-between rounded-lg border px-2.5 py-1.5 text-left transition-colors",
              failed
                ? "border-destructive/60 bg-destructive/10"
                : index === activeIndex
                  ? "border-primary/60 bg-primary/10"
                  : "border-border/60 bg-card/60 hover:bg-accent/60",
            )}
          >
            <button
              type="button"
              onClick={() => (failed ? onPrefillNote?.(noteText) : onSeek?.(startS))}
              title={failed ? `Tell Video what to fix in ${shot.id}` : `Play from ${shot.id}`}
              data-slot="storyboard-shot-button"
              className="flex min-w-0 flex-1 flex-col items-start justify-between gap-1 text-left outline-none"
            >
              <span
                className={cn(
                  "flex items-center gap-1 truncate font-mono text-[11px]",
                  failed ? "text-destructive" : "text-foreground/90",
                )}
              >
                {failed ? <CircleAlert className="size-3 shrink-0" aria-hidden /> : null}
                {shot.id}
              </span>
              <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <span>{formatTimecode(startS)}</span>
                {shot.durationS != null ? <span>· {Math.round(shot.durationS)}s</span> : null}
                {failed ? (
                  <span className="font-medium uppercase tracking-wide text-destructive">failed</span>
                ) : shot.status === "cached" ? (
                  <span className="uppercase tracking-wide">cached</span>
                ) : null}
              </span>
            </button>
            {/* Per-shot note: pre-fill the composer so the creator can give
                taste notes on exactly this shot. */}
            <button
              type="button"
              onClick={() => onPrefillNote?.(noteText)}
              title={`Give a note on ${shot.id}`}
              aria-label={`Give a note on ${shot.id}`}
              data-slot="storyboard-shot-note"
              className="absolute right-1 top-1 grid size-5 place-items-center rounded-full bg-background/85 text-muted-foreground opacity-0 shadow transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
            >
              <MessageSquarePlus className="size-3" aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
