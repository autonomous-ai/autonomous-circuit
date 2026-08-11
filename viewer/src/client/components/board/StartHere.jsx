import { Check, CircleAlert, Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import { buildProgress, buildStageChecklist, formatElapsed } from "./buildStatus.js";

/**
 * The stage before there is a board: the first sixty seconds.
 *
 * Two jobs, and it does whichever one applies.
 *
 * **Nothing running.** Say what is about to happen and how long it takes. A
 * first-time user is looking at a chat box on the right and a black rectangle
 * on the left; the black rectangle should explain the deal, including the part
 * nobody else says out loud — that the output is files a factory takes, and
 * that we will not call a board orderable until it is.
 *
 * **A build running.** A board build is 45–90 seconds. That is long enough
 * that a spinner reads as a hang. The pipeline already reports which of seven
 * stages it is on, so the wait is a checklist that fills in — you can watch it
 * work, and if it stops you can see exactly where.
 */
export default function StartHere({ status = null, building = false, className }) {
  const running = building || String(status?.state || "") === "running";
  const failed = String(status?.state || "") === "failed";
  const stale = String(status?.state || "") === "stale";

  if (!running && !failed && !stale) return <Pitch className={className} />;

  const stages = buildStageChecklist(status);
  const pct = Math.round(buildProgress(status) * 100);
  const elapsed = Number(status?.elapsedS);

  return (
    <div
      data-slot="board-building"
      className={cn("grid min-h-0 flex-1 place-items-center", className)}
      style={{ backgroundColor: "var(--ui-viewer-bg)" }}
    >
      <div className="flex w-full max-w-md flex-col gap-4 px-6">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold tracking-tight text-white">
            {failed ? "The build stopped" : stale ? "The build stopped responding" : "Building your board"}
          </span>
          <span className="ml-auto font-mono text-[11px] tabular-nums text-white/40">
            {Number.isFinite(elapsed) && elapsed > 0 ? formatElapsed(elapsed) : ""}
          </span>
        </div>

        <div className="h-px w-full bg-white/10">
          <div
            className={cn("h-px transition-[width] duration-700", failed || stale ? "bg-amber-400" : "bg-white/70")}
            style={{ width: `${pct}%` }}
          />
        </div>

        <ol className="flex flex-col gap-1.5" data-slot="build-stages">
          {stages.map((stage) => (
            <li
              key={stage.key}
              data-stage={stage.key}
              data-state={stage.state}
              className="flex items-start gap-2.5"
            >
              <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
                {stage.state === "done" ? (
                  <Check className="size-3.5 text-emerald-400" aria-hidden />
                ) : stage.state === "active" ? (
                  failed || stale ? (
                    <CircleAlert className="size-3.5 text-amber-400" aria-hidden />
                  ) : (
                    <Loader2 className="size-3.5 animate-spin text-white/70" aria-hidden />
                  )
                ) : (
                  <span className="size-1.5 rounded-full bg-white/20" />
                )}
              </span>
              <span className="min-w-0">
                <span
                  className={cn(
                    "block text-[13px] leading-5",
                    stage.state === "pending" ? "text-white/30" : "text-white/90",
                  )}
                >
                  {stage.label}
                </span>
                {stage.state === "active" ? (
                  <span className="block text-[11px] leading-4 text-white/45">{stage.plain}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ol>

        {failed || stale ? (
          <p className="text-xs leading-5 text-white/50">
            {String(status?.detail || "").trim() ||
              "No reason was recorded. Ask the chat to build it again — it will pick up from the source file."}
          </p>
        ) : (
          <p className="text-xs leading-5 text-white/40">
            Choosing parts and writing the board is the slow part — a few minutes on a new design. The seven checks
            after it take about ninety seconds. The schematic and the layout appear here the moment they are drawn.
          </p>
        )}
      </div>
    </div>
  );
}

const STEPS = [
  {
    n: "1",
    title: "Describe the thing you want",
    body: "Plain words are enough — \"a coaster that knows when my glass is empty\". No part numbers, no schematic.",
  },
  {
    n: "2",
    title: "Circuit designs and checks it",
    body: "It picks real, orderable parts, draws the schematic, lays out the copper, then runs the same seven checks a factory does. A few minutes on a new design.",
  },
  {
    n: "3",
    title: "You get files a factory can build",
    body: "Gerbers, a parts list and a placement file — the three things JLCPCB asks for. Five boards is the usual first run.",
  },
];

/** The idle stage: what this tool does, in three steps and one promise. */
function Pitch({ className }) {
  return (
    <div
      data-slot="board-empty-state"
      className={cn("grid min-h-0 flex-1 place-items-center", className)}
      style={{ backgroundColor: "var(--ui-viewer-bg)" }}
    >
      <div className="flex w-full max-w-lg flex-col gap-6 px-8">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-white/35">Autonomous Circuit</p>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-white">
            Describe a device. Get a board a factory will build.
          </h1>
        </div>

        <ol className="flex flex-col gap-3.5">
          {STEPS.map((step) => (
            <li key={step.n} className="flex gap-3">
              <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded border border-white/15 font-mono text-[11px] text-white/50">
                {step.n}
              </span>
              <span className="min-w-0">
                <span className="block text-[13px] font-medium leading-5 text-white/90">{step.title}</span>
                <span className="block text-xs leading-5 text-white/45">{step.body}</span>
              </span>
            </li>
          ))}
        </ol>

        {/* The honesty note, stated up front rather than discovered at the
            point of payment. The fab-ready gate is the product's promise; a
            first-time user should know it exists before they trust it. */}
        <p className="border-t border-white/10 pt-4 text-xs leading-5 text-white/40">
          A board only reads as <span className="text-white/70">ready to order</span> once every check has passed and
          the factory files have been verified. Until then it says what is missing.
        </p>
      </div>
    </div>
  );
}
