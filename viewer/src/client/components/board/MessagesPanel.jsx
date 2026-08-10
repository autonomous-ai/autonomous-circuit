import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, CircleAlert, Crosshair, Info, MessageSquarePlus, TriangleAlert } from "lucide-react";
import { cn } from "@/ui/utils";
import { buildMessages, messageCounts } from "@/lib/boardViolations.js";
import { normalizeWarnings, warningNoteText } from "./boardData.js";

const SEVERITY_ICON = { error: CircleAlert, warning: TriangleAlert, info: Info };

// KiCad's documented DRC colours stand in for Altium's, which are unpublished
// (ALTIUM-NOTES §5).
const SEVERITY_TEXT = {
  error: "text-[#d75b6b]",
  warning: "text-[#ffd042]",
  info: "text-muted-foreground",
};
const SEVERITY_DOT = {
  error: "bg-[#d75b6b]",
  warning: "bg-[#ffd042]",
  info: "bg-muted-foreground/50",
};

const FILTERS = [
  { id: "all", label: "All" },
  { id: "error", label: "Errors" },
  { id: "warning", label: "Warnings" },
  { id: "info", label: "Info" },
];

/**
 * Messages — the DRC panel. Rows come from the sidecar's `validation.warnings`
 * (the contract's severity authority); locations come from joining them against
 * the circuit JSON's own `*_error` / `*_warning` elements, which carry the ids
 * and sometimes an explicit centre (see lib/boardViolations.js).
 *
 * Altium's gesture: single click selects the offender, double click zooms the
 * canvas to it and flashes it. Ours adds the thing Altium cannot do — every row
 * has "Fix", which hands the finding to the chat as a repair request. That
 * button is the whole point of this tool and it is deliberately the last thing
 * in the row, where the eye lands after reading the message.
 */
export default function MessagesPanel({
  index = null,
  sidecar = null,
  selection = null,
  onSelect,
  onLocate,
  onPrefillNote,
  open = true,
  onToggleOpen,
  className,
}) {
  const [filter, setFilter] = useState("all");
  const rows = useMemo(() => buildMessages(index, normalizeWarnings(sidecar)), [index, sidecar]);
  const counts = useMemo(() => messageCounts(rows), [rows]);
  const shown = useMemo(
    () => (filter === "all" ? rows : rows.filter((row) => row.severity === filter)),
    [rows, filter],
  );

  const header = (
    <div className="flex h-8 shrink-0 items-center gap-2 border-t border-border/60 bg-card/40 px-2">
      <button
        type="button"
        onClick={onToggleOpen}
        data-slot="messages-toggle"
        className="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
      >
        {open ? <ChevronDown className="size-3" aria-hidden /> : <ChevronUp className="size-3" aria-hidden />}
        Messages
      </button>
      <div className="flex items-center gap-2 font-mono text-[11px] tabular-nums">
        <span className={cn("flex items-center gap-1", counts.error ? SEVERITY_TEXT.error : "text-muted-foreground/40")}>
          <span className={cn("size-1.5 rounded-full", counts.error ? SEVERITY_DOT.error : "bg-muted-foreground/30")} />
          {counts.error}
        </span>
        <span className={cn("flex items-center gap-1", counts.warning ? SEVERITY_TEXT.warning : "text-muted-foreground/40")}>
          <span className={cn("size-1.5 rounded-full", counts.warning ? SEVERITY_DOT.warning : "bg-muted-foreground/30")} />
          {counts.warning}
        </span>
        <span className="flex items-center gap-1 text-muted-foreground/60">
          <span className="size-1.5 rounded-full bg-muted-foreground/40" />
          {counts.info}
        </span>
      </div>
      {open ? (
        <div className="ml-auto flex items-center gap-0.5">
          {FILTERS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => setFilter(entry.id)}
              data-slot="messages-filter"
              data-filter={entry.id}
              aria-current={filter === entry.id ? "true" : undefined}
              className={cn(
                "rounded px-1.5 py-0.5 text-[11px] transition-colors",
                filter === entry.id ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {entry.label}
            </button>
          ))}
        </div>
      ) : (
        <span className="ml-auto truncate font-mono text-[11px] text-muted-foreground/70">
          {counts.total ? `${counts.total} findings` : sidecar ? "clean" : ""}
        </span>
      )}
    </div>
  );

  if (!open) return <div data-slot="messages-panel">{header}</div>;

  return (
    <div data-slot="messages-panel" className={cn("flex h-52 shrink-0 flex-col", className)}>
      {header}
      <div className="scrollbar-thin min-h-0 flex-1 overflow-auto">
        {!shown.length ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            {sidecar ? "No findings at this severity — the board checks out." : "Findings appear here once the board builds."}
          </p>
        ) : null}
        {shown.map((row) => {
          const Icon = SEVERITY_ICON[row.severity] || TriangleAlert;
          const active =
            selection && row.target.kind === selection.kind && row.target.key && row.target.key === selection.key;
          return (
            <div
              key={row.id}
              data-slot="message-row"
              data-severity={row.severity}
              data-locatable={row.locatable ? "true" : "false"}
              aria-current={active ? "true" : undefined}
              onClick={() => {
                if (row.target.kind === "component" || row.target.kind === "net") {
                  onSelect?.({ kind: row.target.kind, key: row.target.key });
                }
              }}
              onDoubleClick={() => onLocate?.(row)}
              className={cn(
                "group flex cursor-default items-start gap-2 border-b border-border/30 px-3 py-1.5 text-[12px] transition-colors",
                active ? "bg-accent/70" : "hover:bg-accent/40",
              )}
            >
              <Icon className={cn("mt-0.5 size-3 shrink-0", SEVERITY_TEXT[row.severity])} aria-hidden />
              <span className="w-28 shrink-0 truncate font-mono text-[11px] text-foreground" title={row.part}>
                {row.part || "board"}
              </span>
              <span className="w-44 shrink-0 truncate font-mono text-[11px] text-muted-foreground/80" title={row.kind}>
                {row.kind}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground" title={row.detail}>
                {row.detail}
              </span>
              <button
                type="button"
                disabled={!row.locatable}
                onClick={(event) => {
                  event.stopPropagation();
                  onLocate?.(row);
                }}
                title={row.locatable ? "Zoom to this violation" : "This finding has no location on the board"}
                data-slot="message-locate"
                className={cn(
                  "shrink-0 rounded p-0.5 transition-colors",
                  row.locatable
                    ? "text-muted-foreground/60 hover:bg-accent hover:text-foreground"
                    : "cursor-not-allowed text-muted-foreground/20",
                )}
              >
                <Crosshair className="size-3" aria-hidden />
              </button>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onPrefillNote?.(warningNoteText(row));
                }}
                title="Ask the chat to fix this"
                data-slot="message-fix"
                className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground/60 transition-colors hover:bg-primary/15 hover:text-foreground"
              >
                <MessageSquarePlus className="size-3" aria-hidden />
                Fix
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
