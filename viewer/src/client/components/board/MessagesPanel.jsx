import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  Crosshair,
  HelpCircle,
  Info,
  MessageSquarePlus,
  TriangleAlert,
} from "lucide-react";
import { cn } from "@/ui/utils";
import { buildMessages, messageCounts } from "@/lib/boardViolations.js";
import { IMPACT, groupFindings, groupFixRequest, impactCounts } from "@/lib/plainLanguage.js";
import { helpCards, helpRequestAll, readRoutingHelp } from "@/lib/routingHelp.js";
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

// What each impact bucket is called in the grouped view. The label answers
// "does this stop me getting boards?" before it answers "what rule fired?".
const IMPACT_LABEL = {
  [IMPACT.BLOCKS]: "stops the order",
  [IMPACT.QUALITY]: "worth fixing",
  [IMPACT.COSMETIC]: "looks only",
  [IMPACT.TOOLING]: "checker setup",
};

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
  rows: rowsProp = null,
  groups: groupsProp = null,
  boardName = "",
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
  // Grouped is the default. A DRC panel that opens on 694 individual rows is
  // a wall; the same findings as nine named issues is a to-do list. "Every"
  // is one click away and is what an engineer reaching for a specific
  // violation wants.
  const [mode, setMode] = useState("grouped");
  const built = useMemo(
    () => (rowsProp ? null : buildMessages(index, normalizeWarnings(sidecar))),
    [rowsProp, index, sidecar],
  );
  const rows = rowsProp || built || [];
  const counts = useMemo(() => messageCounts(rows), [rows]);
  const groups = useMemo(() => groupsProp || groupFindings(rows), [groupsProp, rows]);
  const impact = useMemo(() => impactCounts(groups), [groups]);
  const shown = useMemo(
    () => (filter === "all" ? rows : rows.filter((row) => row.severity === filter)),
    [rows, filter],
  );
  const shownGroups = useMemo(
    () => (filter === "all" ? groups : groups.filter((group) => group.rows.some((row) => row.severity === filter))),
    [groups, filter],
  );
  // The requests sit above the findings, because they are about the findings.
  // Seven "a connection was never drawn" rows and one "move U3 0.2mm up the
  // board" are the same event, and only one of them can be acted on.
  const requests = useMemo(
    () => helpCards(readRoutingHelp(sidecar), { board: boardName }),
    [sidecar, boardName],
  );

  const header = (
    <div className="flex h-8 shrink-0 items-center gap-2 border-t border-border/60 bg-card/40 px-2">
      <button
        type="button"
        onClick={onToggleOpen}
        data-slot="messages-toggle"
        // The key is printed here because it moved twice. It was `L` (Altium's
        // Layers And Colors), then briefly Shift+M (Altium's Board Insight
        // Lens); Shift+N is the first spelling that collides with nothing
        // Altium publishes. A binding that moves without saying so is a
        // binding the user has to rediscover.
        title={`${open ? "Hide" : "Show"} the messages drawer (⇧N)`}
        className="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
      >
        {open ? <ChevronDown className="size-3" aria-hidden /> : <ChevronUp className="size-3" aria-hidden />}
        Messages
      </button>
      {/* Grouped mode counts by what a finding DOES; Every mode counts by
          severity, which is what someone reading individual rows is sorting
          on. The two never disagree — they are two cuts of the same list. */}
      {mode === "grouped" ? (
        <div data-slot="messages-impact" className="flex items-center gap-2.5 text-[11px] tabular-nums">
          <span className={cn(impact.blocks ? "text-[#d75b6b]" : "text-muted-foreground/40")}>
            <span className="font-mono">{impact.blocks}</span> stop the order
          </span>
          <span className={cn(impact.quality ? "text-[#ffd042]" : "text-muted-foreground/40")}>
            <span className="font-mono">{impact.quality}</span> worth fixing
          </span>
          <span className="text-muted-foreground/50">
            <span className="font-mono">{impact.cosmetic + impact.tooling}</span> cosmetic or checker noise
          </span>
        </div>
      ) : (
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
      )}
      {open ? (
        <div className="ml-auto flex items-center gap-0.5">
          <div className="mr-1.5 flex items-center gap-0.5 rounded border border-border/60 p-0.5">
            {[
              { id: "grouped", label: "Grouped" },
              { id: "every", label: "Every" },
            ].map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => setMode(entry.id)}
                data-slot="messages-mode"
                data-mode={entry.id}
                aria-current={mode === entry.id ? "true" : undefined}
                title={
                  entry.id === "grouped"
                    ? "One row per kind of problem, in plain words"
                    : "One row per individual violation"
                }
                className={cn(
                  "rounded px-1.5 py-0.5 text-[11px] transition-colors",
                  mode === entry.id ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {entry.label}
              </button>
            ))}
          </div>
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
        {requests.length ? (
          <div data-slot="routing-help" className="border-b border-border/60 bg-primary/[0.04]">
            <div className="flex items-center gap-2 px-3 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              <HelpCircle className="size-3" aria-hidden />
              What the wiring needs from you
              {requests.length > 1 ? (
                <button
                  type="button"
                  onClick={() => onPrefillNote?.(helpRequestAll(readRoutingHelp(sidecar), { board: boardName }))}
                  data-slot="routing-help-all"
                  className="ml-auto rounded px-1.5 py-0.5 text-[11px] normal-case tracking-normal text-muted-foreground/70 transition-colors hover:bg-primary/15 hover:text-foreground"
                >
                  Ask about all {requests.length}
                </button>
              ) : null}
            </div>
            {requests.map((card) => (
              <RoutingRequest key={card.id} card={card} onPrefillNote={onPrefillNote} onLocate={onLocate} />
            ))}
          </div>
        ) : null}

        {!shown.length ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            {sidecar ? "No findings at this severity — the board checks out." : "Findings appear here once the board builds."}
          </p>
        ) : null}

        {mode === "grouped"
          ? shownGroups.map((group) => (
              <MessageGroupRow
                key={group.code}
                group={group}
                boardName={boardName}
                onSelect={onSelect}
                onLocate={onLocate}
                onPrefillNote={onPrefillNote}
              />
            ))
          : null}

        {mode === "grouped" ? null : shown.map((row) => {
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
                title={
                  row.locatable
                    ? row.where === "schematic"
                      ? "Show this on the schematic"
                      : "Zoom to this violation"
                    : row.unlocatableReason || "This finding has no location on the board"
                }
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

/**
 * One request for a decision.
 *
 * A finding tells you something is wrong. This tells you what to change and
 * what the measurement was — and where the measurement supports nothing, it
 * says that in the same shape rather than going quiet. The evidence is folded
 * away because it is the *proof*, not the message: a person who wants to check
 * the numbers opens it, and nobody has to read it to act.
 */
function RoutingRequest({ card, onPrefillNote, onLocate }) {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div data-slot="routing-request" data-kind={card.kind} data-tone={card.tone} className="px-3 py-2">
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="mt-0.5 shrink-0 text-muted-foreground transition-colors hover:text-foreground"
          title={open ? "Hide what was measured" : "Show what was measured"}
        >
          <Chevron className="size-3" aria-hidden />
        </button>
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-medium text-foreground">{card.title}</p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{card.body}</p>
          {open && card.evidence.length ? (
            <ul className="mt-1.5 space-y-0.5 border-l border-border/60 pl-2">
              {card.evidence.map((line, index) => (
                <li key={index} className="text-[11px] leading-relaxed text-muted-foreground/80">
                  {line}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        {card.at ? (
          <button
            type="button"
            // A 1mm window on the pinch, the same box shape a finding row
            // carries (lib/boardViolations.js `findingBox`). Without it the
            // handler has nothing to zoom to and the button does nothing.
            onClick={() =>
              onLocate?.({
                locatable: true,
                target: { kind: "point", key: "" },
                box: {
                  minX: card.at.x - 0.5,
                  minY: card.at.y - 0.5,
                  maxX: card.at.x + 0.5,
                  maxY: card.at.y + 0.5,
                },
              })
            }
            title="Show me where on the board"
            data-slot="routing-request-locate"
            className="shrink-0 rounded p-0.5 text-muted-foreground/60 transition-colors hover:bg-accent hover:text-foreground"
          >
            <Crosshair className="size-3" aria-hidden />
          </button>
        ) : null}
        {card.action ? (
          <button
            type="button"
            onClick={() => onPrefillNote?.(card.action.request)}
            data-slot="routing-request-action"
            title={card.action.request}
            className="flex shrink-0 items-center gap-1 rounded border border-border bg-foreground/[0.04] px-1.5 py-0.5 text-[11px] text-foreground transition-colors hover:bg-primary/15"
          >
            <MessageSquarePlus className="size-3" aria-hidden />
            {card.action.label}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/**
 * One kind of problem, not one instance of it. The plain title carries the
 * meaning; the code stays on the row in mono so an engineer can still search
 * for `hole_clearance`. Expanding shows the individual violations, which
 * cross-probe exactly as they do in the flat list.
 */
function MessageGroupRow({ group, boardName, onSelect, onLocate, onPrefillNote }) {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  const Icon = SEVERITY_ICON[group.severity] || TriangleAlert;
  return (
    <div data-slot="message-group" data-code={group.code} data-blocking={group.blocking ? "true" : "false"}>
      <div
        className={cn(
          "flex cursor-default items-center gap-2 border-b border-border/30 px-3 py-1.5 text-[12px] transition-colors hover:bg-accent/40",
          group.blocking ? "bg-amber-500/[0.06]" : "",
        )}
        onClick={() => setOpen((value) => !value)}
      >
        <Chevron className="size-3 shrink-0 text-muted-foreground" aria-hidden />
        <Icon className={cn("size-3 shrink-0", SEVERITY_TEXT[group.severity])} aria-hidden />
        <span className="w-8 shrink-0 text-right font-mono text-[11px] tabular-nums text-foreground">
          {group.count}
        </span>
        <span className="min-w-0 flex-1 truncate text-foreground" title={group.meaning || group.title}>
          {group.title}
        </span>
        <span className="hidden w-24 shrink-0 truncate text-right text-[11px] text-muted-foreground/70 lg:inline">
          {IMPACT_LABEL[group.blocking ? IMPACT.BLOCKS : group.impact]}
        </span>
        <span
          className="hidden w-36 shrink-0 truncate font-mono text-[11px] text-muted-foreground/60 xl:inline"
          title={group.code}
        >
          {group.code}
        </span>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onPrefillNote?.(groupFixRequest(group, { board: boardName }));
          }}
          title={`Ask the chat to fix all ${group.count}`}
          data-slot="message-group-fix"
          className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground/60 transition-colors hover:bg-primary/15 hover:text-foreground"
        >
          <MessageSquarePlus className="size-3" aria-hidden />
          Fix all
        </button>
      </div>
      {open
        ? group.rows.slice(0, 60).map((row) => (
            <div
              key={row.id}
              data-slot="message-row"
              data-severity={row.severity}
              data-locatable={row.locatable ? "true" : "false"}
              onClick={() => {
                if (row.target.kind === "component" || row.target.kind === "net") {
                  onSelect?.({ kind: row.target.kind, key: row.target.key });
                }
              }}
              onDoubleClick={() => onLocate?.(row)}
              className="flex cursor-default items-start gap-2 border-b border-border/20 bg-background/40 py-1 pl-9 pr-3 text-[11px] transition-colors hover:bg-accent/40"
            >
              <span className="w-28 shrink-0 truncate font-mono text-muted-foreground" title={row.part}>
                {row.part || "board"}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground/80" title={row.detail}>
                {row.detail}
              </span>
              <button
                type="button"
                disabled={!row.locatable}
                onClick={(event) => {
                  event.stopPropagation();
                  onLocate?.(row);
                }}
                title={
                  row.locatable
                    ? row.where === "schematic"
                      ? "Show this on the schematic"
                      : "Zoom to this violation"
                    : row.unlocatableReason || "This finding has no location on the board"
                }
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
            </div>
          ))
        : null}
      {open && group.rows.length > 60 ? (
        <p className="border-b border-border/20 bg-background/40 py-1 pl-9 text-[11px] text-muted-foreground/60">
          +{group.rows.length - 60} more — switch to Every to page through them all.
        </p>
      ) : null}
    </div>
  );
}
