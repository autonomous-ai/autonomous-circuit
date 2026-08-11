import { useMemo } from "react";
import { CircleAlert, CircleCheck, CircleDashed, Hammer, Loader2, TriangleAlert, Wrench } from "lucide-react";
import { cn } from "@/ui/utils";
import { boardShapeLine, boardVerdict, groupFixRequest } from "@/lib/plainLanguage.js";
import { formatElapsed } from "./buildStatus.js";
import { useLiveElapsed } from "./useLiveElapsed.js";

const TONE = {
  ready: {
    icon: CircleCheck,
    bar: "border-emerald-500/40 bg-emerald-500/[0.07]",
    dot: "text-emerald-500",
    headline: "text-foreground",
  },
  blocked: {
    icon: TriangleAlert,
    bar: "border-amber-500/40 bg-amber-500/[0.06]",
    dot: "text-amber-500",
    headline: "text-foreground",
  },
  building: {
    icon: Loader2,
    bar: "border-border/60 bg-card/40",
    dot: "text-muted-foreground animate-spin",
    headline: "text-foreground",
  },
  failed: {
    icon: CircleAlert,
    bar: "border-destructive/40 bg-destructive/[0.07]",
    dot: "text-destructive",
    headline: "text-foreground",
  },
  unknown: {
    icon: CircleDashed,
    bar: "border-border/60 bg-card/30",
    dot: "text-muted-foreground",
    headline: "text-muted-foreground",
  },
};

/**
 * The verdict strip — the one line that answers "can I get this made?".
 *
 * It sits under the tab row and stays there on every tab, because that
 * question does not stop mattering when you switch to the BOM. An engineer
 * reads `fab.ready: false` off the Properties panel; everyone else needs a
 * sentence, and the sentence has to be true on both readings.
 *
 * The gate is `fab.ready` alone (see plainLanguage.boardVerdict). Nothing in
 * here can make a board look orderable when the pipeline says it is not — the
 * ready styling, the Order button and the word "ready" are all downstream of
 * that one boolean.
 *
 * Not-ready is written as a state with an exit, not a failure: the headline
 * counts what is left, the line says what the first one is in plain words, and
 * the button hands all of them to the chat.
 */
export default function BoardVerdict({
  sidecar = null,
  index = null,
  groups = [],
  building = false,
  buildLine = null,
  buildStatus = null,
  boardName = "",
  turnActive = false,
  onOpenTab,
  onFix,
  className,
}) {
  const verdict = useMemo(
    () => boardVerdict({ sidecar, groups, building, buildLine, boardName, turnActive }),
    [sidecar, groups, building, buildLine, boardName, turnActive],
  );
  const tone = TONE[verdict.tone] || TONE.unknown;
  const Icon = tone.icon;
  const shape = boardShapeLine({ sidecar, index });

  // The clock. `compile` can hold one stage for fifteen minutes at 5x router
  // effort, and its quiet limit is 45 minutes, so without a number the strip
  // says the same six words for a quarter of an hour.
  const elapsed = useLiveElapsed(buildStatus, verdict.tone === "building");

  // While a build runs, the stage line is more informative than the verdict of
  // the build it is replacing — the numbers on screen are about to change.
  const line =
    verdict.tone === "building" && buildLine?.text
      ? [
          buildLine.text,
          buildLine.detail ? (/^\d+\/\d+$/.test(buildLine.detail) ? `step ${buildLine.detail}` : buildLine.detail) : "",
          elapsed > 0 ? formatElapsed(elapsed) : "",
        ]
          .filter(Boolean)
          .join(" · ")
      : verdict.line;

  const fixAll = () => {
    if (!verdict.blockingGroups.length) return;
    const text = verdict.blockingGroups
      .map((group) => groupFixRequest(group, { board: boardName }))
      .join("\n\n");
    onFix?.(text);
  };

  return (
    <div
      data-slot="board-verdict"
      data-tone={verdict.tone}
      className={cn(
        "flex h-10 shrink-0 items-center gap-2.5 border-b px-3",
        tone.bar,
        className,
      )}
    >
      <Icon className={cn("size-4 shrink-0", tone.dot)} aria-hidden />

      <span className={cn("shrink-0 text-[13px] font-semibold tracking-tight", tone.headline)}>
        {verdict.headline}
      </span>

      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={line}>
        {line}
      </span>

      {verdict.tone === "ready" && shape ? (
        <span className="hidden shrink-0 font-mono text-[11px] text-muted-foreground lg:inline">{shape}</span>
      ) : null}

      {verdict.tone === "blocked" && verdict.blockingCount ? (
        <button
          type="button"
          onClick={fixAll}
          data-slot="verdict-fix"
          title="Hand every blocking finding to the chat as one repair request"
          className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-amber-500/50 bg-amber-500/10 px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-amber-500/20"
        >
          <Wrench className="size-3.5" aria-hidden />
          {verdict.blockingGroups.length === 1 ? "Fix it" : `Fix all ${verdict.blockingGroups.length}`}
        </button>
      ) : null}

      {/* The exit from a dead end. A project can hold a board program with no
          build behind it — the turn wrote the source and stopped — and the
          strip used to describe that state and offer nothing. This puts the
          words in the chat box; the person only has to press send. */}
      {verdict.action ? (
        <button
          type="button"
          onClick={() => onFix?.(verdict.action.request)}
          data-slot="verdict-build"
          title={verdict.action.request}
          className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border bg-foreground/[0.04] px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-foreground/[0.09]"
        >
          <Hammer className="size-3.5" aria-hidden />
          {verdict.action.label}
        </button>
      ) : null}

      {verdict.tone === "ready" ? (
        <button
          type="button"
          onClick={() => onOpenTab?.("fab")}
          data-slot="verdict-order"
          className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-emerald-500/50 bg-emerald-500/10 px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-emerald-500/20"
        >
          Order at JLCPCB
        </button>
      ) : null}

      {verdict.tone !== "building" && verdict.tone !== "failed" ? (
        <button
          type="button"
          onClick={() => onOpenTab?.("overview")}
          data-slot="verdict-explain"
          className="shrink-0 rounded px-1.5 py-1 text-xs text-muted-foreground underline-offset-2 transition-colors hover:text-foreground hover:underline"
        >
          {verdict.tone === "blocked" ? "What's left" : "What is this?"}
        </button>
      ) : null}

      {verdict.tone === "building" ? (
        <span className="h-1 w-28 shrink-0 overflow-hidden rounded-full bg-muted">
          <span
            className="block h-full bg-foreground/60 transition-[width] duration-500"
            style={{ width: `${Math.round((buildLine?.progress || 0) * 100)}%` }}
          />
        </span>
      ) : null}
    </div>
  );
}
