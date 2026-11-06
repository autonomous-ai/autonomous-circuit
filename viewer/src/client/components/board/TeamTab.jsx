import { cn } from "@/ui/utils";

const ROLES = ["board", "firmware", "case"];
const LABELS = { board: "Board", firmware: "Firmware", case: "Case" };
const STEPS = ["Build", "Test", "Fix", "Approve"];
const RUN_STATUS = {
  running: "Running",
  initialized: "Starting",
  candidate_ready: "Ready",
  needs_revision: "Needs another round",
  planning_failed: "Plan failed",
  print_only: "Print plan",
  interrupted: "Stopped",
  failed: "Failed",
  cancelled: "Stopped",
  blocked: "Blocked",
};
const LIVE_STATUS = {
  queued: "Waiting",
  draft: "Building",
  checking: "Testing",
  needs_revision: "Fixing",
  passed: "Approved by tester",
  blocked: "Blocked",
};
const GOOD = new Set(["candidate_ready"]);
const BAD = new Set(["planning_failed", "failed", "blocked", "interrupted", "cancelled"]);

export function formatSeconds(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

function formatStarted(epoch) {
  if (!Number.isFinite(epoch)) return "—";
  const date = new Date(epoch * 1000);
  return `${date.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${formatClock(epoch)}`;
}

function formatClock(epoch) {
  if (!Number.isFinite(epoch)) return "";
  return new Date(epoch * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export function runStatusLabel(status = "") {
  return RUN_STATUS[status] || status.replace(/_/g, " ");
}

/** "claude-opus-5-5" → "Opus", "gpt-6.1-sol" → "Sol": the names people say. */
export function modelLabel(model) {
  if (!model) return "";
  const known = /(opus|sonnet|haiku|fable|sol)/i.exec(model);
  return known ? known[1][0].toUpperCase() + known[1].slice(1).toLowerCase() : model;
}

/** Which of Build / Test / Fix / Approve are done, and which one is under way. */
export function teamSteps(summary = {}, live = "") {
  const approved = live === "passed" || summary.decision === "approve";
  const done = [
    approved || summary.builds > 0 || live === "checking" || live === "needs_revision",
    approved || summary.tests > 0 || live === "needs_revision",
    approved || summary.repairs > 0,
    approved,
  ];
  let current = -1;
  if (!approved) {
    if (live === "draft") current = summary.tests > 0 ? 2 : 0;
    else if (live === "checking") current = 1;
    else if (live === "needs_revision") current = 2;
  }
  return { done, current, approved };
}

function stageLabel(stage) {
  if (stage === "plan") return "Lead plan";
  if (stage.startsWith("print-plan")) return "Print plan";
  if (stage === "integration-test") return "Integration test";
  const [role, kind] = stage.split("-");
  return `${LABELS[role] || role}${kind === "test" ? " test" : " build"}`;
}

function toneOf(status) {
  return GOOD.has(status) ? "text-emerald-600 dark:text-emerald-400" : BAD.has(status) ? "text-red-600 dark:text-red-400" : "text-amber-600 dark:text-amber-400";
}

function issuesText(count, fixed) {
  if (!count) return "0 issues";
  return `${count} issue${count === 1 ? "" : "s"}${fixed ? ", fixed" : ""}`;
}

/** The last run that built something: a print-plan-only run does not replace it. */
function lastBuildRun(runs) {
  return runs.find(run => run.status !== "print_only") || runs[0] || null;
}

/** One line for the Brief: what the team is doing, a click away from the Team view. */
export function TeamStrip({ teamRuns, teamPreview, onOpen }) {
  const runs = Array.isArray(teamRuns?.runs) ? teamRuns.runs : [];
  const last = lastBuildRun(runs);
  if (!last && !teamPreview) return null;
  const running = Boolean(teamRuns?.running || teamPreview);
  const run = teamPreview?.run || last?.run;
  return (
    <button type="button" data-slot="team-strip" data-running={running ? "true" : "false"} onClick={onOpen}
      className="flex w-full shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-border/60 bg-muted/30 px-3 py-2 text-left text-xs hover:bg-accent">
      <span className={cn("size-2 rounded-full", running ? "bg-emerald-500" : "bg-muted-foreground/50")} aria-hidden />
      <strong className="font-semibold">Team · {run} {running ? "running" : runStatusLabel(last?.status).toLowerCase()}</strong>
      {ROLES.map(role => {
        const live = teamPreview?.teams?.[role]?.status;
        const decision = last?.run === run ? last.teams?.[role]?.decision : null;
        const done = live ? live === "passed" : decision === "approve";
        return <span key={role} className={done ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}>{done ? "✓" : "○"} {LABELS[role]}</span>;
      })}
      <span className="ml-auto text-primary">Open Team →</span>
    </button>
  );
}

function StepBar({ steps }) {
  return (
    <div data-slot="team-steps">
      <div className="grid grid-cols-4 gap-1">
        {STEPS.map((step, index) => (
          <div key={step} data-step={step} data-state={steps.done[index] ? "done" : steps.current === index ? "current" : "todo"}
            className={cn("h-1.5 rounded-full",
              steps.done[index] ? "bg-emerald-500" : steps.current === index ? "animate-pulse bg-primary" : "bg-muted")} />
        ))}
      </div>
      <div className="mt-1 grid grid-cols-4 gap-1 text-[10px] text-muted-foreground">
        {STEPS.map((step, index) => <span key={step} className={steps.current === index ? "font-semibold text-foreground" : ""}>{step}</span>)}
      </div>
    </div>
  );
}

function TeamCard({ role, summary = {}, live = null, finished }) {
  const steps = teamSteps(summary, live?.status || "");
  const carried = finished && !summary.decision && !summary.builds;
  const status = live ? LIVE_STATUS[live.status] || "Waiting"
    : steps.approved ? "Approved by tester" : carried ? "Kept from earlier run" : summary.decision === "revise" ? "Needs repair" : "Not reviewed";
  const media = Array.isArray(live?.media) ? live.media : [];
  const files = Array.isArray(live?.files) ? live.files : [];
  return (
    <article data-slot={`team-run-${role}`} data-decision={summary.decision || "none"} data-status={live?.status || "none"}
      className={cn("flex min-w-0 flex-col gap-3 rounded-xl border p-4",
        steps.approved ? "border-emerald-500/40 bg-emerald-500/5" : steps.current >= 0 ? "border-primary/40 bg-card" : "border-border bg-card")}>
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">{LABELS[role]}</h3>
        <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[11px] font-medium",
          steps.approved ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : steps.current >= 0 ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>
          {steps.approved ? "✓ " : ""}{status}
        </span>
      </div>
      <StepBar steps={steps} />
      <p className="text-xs text-muted-foreground">{formatSeconds(summary.seconds)} · {issuesText(summary.issues, steps.approved)}</p>
      {summary.builderModel || summary.testerModel ? (
        <div className="flex flex-wrap gap-1.5 text-[11px]">
          {summary.builderModel ? <span data-slot="team-model" className="rounded-md bg-muted px-1.5 py-0.5">build · {modelLabel(summary.builderModel)}</span> : null}
          {summary.testerModel ? <span data-slot="team-model" className="rounded-md bg-muted px-1.5 py-0.5">test · {modelLabel(summary.testerModel)}</span> : null}
        </div>
      ) : null}
      {media.length ? (
        <img src={media[0].url} alt={`Unverified ${LABELS[role]} draft: ${media[0].file}`}
          className="h-28 w-full rounded-md border border-border bg-background object-contain" />
      ) : null}
      {live?.findings?.length ? <p className="text-[11px] leading-4 text-amber-600 dark:text-amber-400">{live.findings[0]}</p> : null}
      {files.length ? (
        <details className="text-[11px] text-muted-foreground">
          <summary className="cursor-pointer select-none">Files ({files.length})</summary>
          <ul className="mt-1 flex flex-col gap-0.5 font-mono">{files.slice(0, 20).map(file => <li key={file} className="truncate">{file}</li>)}</ul>
        </details>
      ) : null}
    </article>
  );
}

function RunHeader({ run, running, preview }) {
  const status = running ? "running" : run?.status;
  const seconds = running && Number.isFinite(run?.startedAt) ? Date.now() / 1000 - run.startedAt : run?.seconds;
  return (
    <div className="flex flex-wrap items-baseline gap-3">
      <h2 className="text-lg font-semibold">{running ? "Run" : "Last run"} · {run?.run || preview?.run}</h2>
      <span data-slot="team-last-status" className={cn("text-sm font-medium", running ? "text-primary" : toneOf(status))}>{runStatusLabel(status)}</span>
      <span className="text-xs text-muted-foreground">
        {formatSeconds(seconds)}{run?.startedAt ? ` · started ${formatStarted(run.startedAt)}` : ""}{running && preview?.runStage ? ` · ${preview.runStage}` : ""}
      </span>
    </div>
  );
}

function Activity({ items }) {
  return (
    <aside data-slot="team-activity" className="flex w-full shrink-0 flex-col border-t border-border/60 bg-muted/20 lg:w-80 lg:border-l lg:border-t-0">
      <h2 className="border-b border-border/60 px-4 py-3 text-xs font-semibold">Activity</h2>
      {items.length ? (
        <ol className="flex flex-col gap-3 overflow-auto p-4 text-xs">
          {items.map((item, index) => (
            <li key={`${item.stage}-${item.at}-${index}`} data-slot="team-activity-item" className="flex gap-3">
              <span className="w-10 shrink-0 font-mono text-[11px] text-muted-foreground">{formatClock(item.at)}</span>
              <span className="min-w-0">
                <span className="font-medium">{stageLabel(item.stage)}</span>
                {item.failed ? <span className="text-red-600 dark:text-red-400"> · stopped</span>
                  : item.decision === "approve" ? <span className="text-emerald-600 dark:text-emerald-400"> · approved</span>
                  : item.decision === "revise" ? <span className="text-amber-600 dark:text-amber-400"> · {issuesText(item.issues)}</span>
                  : <span className="text-muted-foreground"> · done</span>}
                <span className="text-muted-foreground"> · {formatSeconds(item.seconds)}{item.model ? ` · ${modelLabel(item.model)}` : ""}</span>
              </span>
            </li>
          ))}
        </ol>
      ) : <p className="p-4 text-xs text-muted-foreground">Nothing has finished yet.</p>}
    </aside>
  );
}

function RunHistory({ runs }) {
  return (
    <section data-slot="team-history" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">Run history</h2>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[520px] border-collapse text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              {["Run", "Result", "Time", "Issues found", "Started"].map(head => <th key={head} className="border-b border-border px-3 py-2 font-medium">{head}</th>)}
            </tr>
          </thead>
          <tbody>
            {runs.map(run => {
              const found = ROLES.reduce((sum, role) => sum + (run.teams?.[role]?.issues || 0), 0) + (run.integration?.issues || 0);
              return (
                <tr key={run.run} data-slot="team-history-row" data-run={run.run} className="border-b border-border/60 last:border-0">
                  <td className="px-3 py-2 font-mono">{run.run}</td>
                  <td className={cn("px-3 py-2", toneOf(run.status))} title={run.reason || undefined}>{runStatusLabel(run.status)}</td>
                  <td className="px-3 py-2">{formatSeconds(run.seconds)}</td>
                  <td className="px-3 py-2">{found}</td>
                  <td className="px-3 py-2 text-muted-foreground">{formatStarted(run.startedAt)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * The Team view, always there once a project has a team run: the run under
 * way (or the last one) as three team cards with their Build / Test / Fix /
 * Approve progress, what finished lately beside them, and every earlier run
 * below. A tester's approval is not a hardware check: the cards say
 * "approved by tester", and drafts stay unverified until integration passes.
 */
export default function TeamTab({ teamRuns = null, teamPreview = null, className }) {
  const runs = Array.isArray(teamRuns?.runs) ? teamRuns.runs : [];
  if (!runs.length && !teamPreview) {
    return (
      <div data-slot="team-empty" className={cn("grid min-h-0 flex-1 place-items-center px-6 text-center text-sm text-muted-foreground", className)}>
        No team run yet. When the lead starts one, each team's progress shows here.
      </div>
    );
  }
  const running = Boolean(teamPreview);
  const current = running ? runs.find(run => run.run === teamPreview.run) || null : lastBuildRun(runs);
  const earlier = runs.filter(run => run !== current);
  const finished = !running && current?.status !== "running";
  return (
    <div data-slot="team-tab" data-running={running ? "true" : "false"} className={cn("flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row lg:overflow-hidden", className)}>
      <div className="flex min-w-0 flex-1 flex-col gap-6 p-6 lg:overflow-auto">
        <RunHeader run={current} running={running} preview={teamPreview} />
        {current?.reason && !running ? <p className="-mt-4 text-xs text-muted-foreground">Stopped on: {current.reason}</p> : null}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {ROLES.map(role => (
            <TeamCard key={role} role={role} summary={current?.teams?.[role]} live={running ? teamPreview.teams?.[role] : null} finished={finished} />
          ))}
        </div>
        {current?.integration ? (
          <p data-slot="team-integration" className={cn("text-xs", current.integration.decision === "approve" ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400")}>
            {current.integration.decision === "approve" ? "✓" : "○"} Integration · {issuesText(current.integration.issues, current.integration.decision === "approve")}
          </p>
        ) : running ? <p className="text-xs text-muted-foreground">Then: integration test, print plan. Drafts are unverified until both pass.</p> : null}
        {earlier.length ? <RunHistory runs={earlier} /> : null}
      </div>
      <Activity items={Array.isArray(current?.activity) ? current.activity : []} />
    </div>
  );
}
