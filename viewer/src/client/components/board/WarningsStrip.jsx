import { useMemo } from "react";
import { CircleAlert, Info, MessageSquarePlus, TriangleAlert } from "lucide-react";
import { cn } from "@/ui/utils";
import { normalizeWarnings, warningNoteText } from "./boardData.js";

const SEVERITY_ICON = {
  error: CircleAlert,
  warning: TriangleAlert,
  info: Info,
};

const SEVERITY_CHIP = {
  error: "border-destructive/60 bg-destructive/10 text-destructive",
  warning: "border-amber-500/60 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  info: "border-border/60 bg-card/60 text-muted-foreground",
};

/**
 * Horizontal warnings strip under the board stage — the donor StoryboardStrip
 * repurposed for the sidecar's `validation.warnings` (contract §1: severity
 * closed {error, warning, info}, kind open). One chip per finding, colored by
 * severity; clicking a chip pre-fills the chat with
 * "U3.pin7 (source_trace_not_connected_error): " so the user types the fix
 * request against exactly that finding — the chat-driven repair loop, no new
 * commands.
 *
 * @param {{
 *   sidecar?: object|null,             // parsed .board.json (or null while loading)
 *   onPrefillNote?: (text: string) => void,
 *   className?: string,
 * }} props
 */
export default function WarningsStrip({ sidecar, onPrefillNote, className }) {
  const warnings = useMemo(() => normalizeWarnings(sidecar), [sidecar]);

  if (!warnings.length) {
    return (
      <div
        data-slot="warnings-strip"
        className={cn(
          "flex h-14 shrink-0 items-center justify-center border-t border-border/60 text-xs text-muted-foreground",
          className,
        )}
      >
        {sidecar ? "No findings — the board checks out clean." : "Findings appear here once the board builds."}
      </div>
    );
  }

  return (
    <div
      data-slot="warnings-strip"
      className={cn(
        "scrollbar-thin flex h-14 shrink-0 items-center gap-1.5 overflow-x-auto border-t border-border/60 px-3 py-2",
        className,
      )}
    >
      {warnings.map((warning, index) => {
        const Icon = SEVERITY_ICON[warning.severity] || TriangleAlert;
        const noteText = warningNoteText(warning);
        return (
          <button
            key={`${warning.part}-${warning.kind}-${index}`}
            type="button"
            onClick={() => onPrefillNote?.(noteText)}
            title={warning.detail || `${warning.part} — ${warning.kind}`}
            data-slot="warning-chip"
            data-severity={warning.severity}
            className={cn(
              "group flex min-w-0 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-left transition-colors hover:bg-accent/60",
              SEVERITY_CHIP[warning.severity] || SEVERITY_CHIP.info,
            )}
          >
            <Icon className="size-3 shrink-0" aria-hidden />
            <span className="max-w-40 truncate font-mono text-[11px]">
              {warning.part || warning.kind}
            </span>
            <span className="max-w-48 truncate text-[10px] opacity-70">{warning.kind}</span>
            <MessageSquarePlus
              className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-70"
              aria-hidden
            />
          </button>
        );
      })}
    </div>
  );
}
