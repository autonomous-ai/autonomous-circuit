import { cn } from "@/ui/utils";
import TeamProgress from "./TeamProgress.jsx";

const LABELS = { board: "Board", firmware: "Firmware", case: "Case" };
const RUN_STATUS = {
  running: "Running",
  candidate_ready: "Ready",
  needs_revision: "Needs another round",
  planning_failed: "Plan failed",
  failed: "Failed",
  cancelled: "Stopped",
  blocked: "Blocked",
};
const GOOD = new Set(["candidate_ready"]);
const BAD = new Set(["planning_failed", "failed", "blocked"]);

export function formatSeconds(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

function formatStarted(epoch) {
  if (!Number.isFinite(epoch)) return "—";
  const date = new Date(epoch * 1000);
  return `${date.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

export function runStatusLabel(status) {
  return RUN_STATUS[status] || status.replace(/_/g, " ");
}

function toneOf(status) {
  return GOOD.has(status) ? "text-emerald-600 dark:text-emerald-400" : BAD.has(status) ? "text-red-600 dark:text-red-400" : "text-amber-600 dark:text-amber-400";
}

function issuesText(count, fixed) {
  if (!count) return "0 issues";
  return `${count} issue${count === 1 ? "" : "s"}${fixed ? ", fixed" : ""}`;
}

/** One line for the Brief: what the team is doing, a click away from the Team view. */
export function TeamStrip({ teamRuns, teamPreview, onOpen, className }) {
  const last = teamRuns?.runs?.[0];
  if (!last && !teamPreview) return null;
  const running = Boolean(teamRuns?.running || teamPreview);
  const run = teamPreview?.run || teamRuns?.activeRun || last?.run;
  return (
    <button type="button" data-slot="team-strip" data-running={running ? "true" : "false"} onClick={onOpen}
      className="flex w-full shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-border/60 bg-muted/30 px-3 py-2 text-left text-xs hover:bg-accent">
      <span className={cn("size-2 rounded-full", running ? "bg-emerald-500" : "bg-muted-foreground/50")} aria-hidden />
      <strong className="font-semibold">Team · {run} {running ? "running" : runStatusLabel(last?.status || "").toLowerCase()}</strong>
      {["board", "firmware", "case"].map(role => {
        const live = teamPreview?.teams?.[role]?.status;
        const decision = last?.run === run ? last.teams?.[role]?.decision : null;
        const done = live ? live === "passed" : decision === "approve";
        return <span key={role} className={done ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}>{done ? "✓" : "○"} {LABELS[role]}</span>;
      })}
      <span className="ml-auto text-primary">Open Team →</span>
    </button>
  );
}

function LastRun({ run }) {
  const done = run.status !== "running";
  return (
    <section data-slot="team-last-run" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-lg font-semibold">Last run · {run.run}</h2>
        <span data-slot="team-last-status" className={cn("text-sm font-medium", toneOf(run.status))}>{runStatusLabel(run.status)}</span>
        <span className="text-xs text-muted-foreground">{formatSeconds(run.seconds)} · started {formatStarted(run.startedAt)}</span>
      </div>
      {run.reason ? <p className="text-xs text-muted-foreground">Stopped on: {run.reason}</p> : null}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {["board", "firmware", "case"].map(role => {
          const team = run.teams?.[role] || {};
          const approved = team.decision === "approve";
          const carried = done && !team.decision && !team.seconds;
          return (
            <article key={role} data-slot={`team-run-${role}`} data-decision={team.decision || "none"}
              className={cn("rounded-lg border p-3", approved ? "border-emerald-500/50 bg-emerald-500/10" : "border-border bg-card")}>
              <div className="flex items-baseline gap-2">
                <strong className="text-sm">{LABELS[role]}</strong>
                <span className={cn("ml-auto text-xs", approved ? "font-medium text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>
                  {approved ? "✓ Approved by tester" : carried ? "Kept from earlier run" : team.decision === "revise" ? "Needs repair" : "Not reviewed"}
                </span>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">{formatSeconds(team.seconds)} · {issuesText(team.issues, approved)}</p>
            </article>
          );
        })}
      </div>
      {run.integration ? (
        <p data-slot="team-integration" className={cn("text-xs", run.integration.decision === "approve" ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400")}>
          {run.integration.decision === "approve" ? "✓" : "○"} Integration · {issuesText(run.integration.issues, run.integration.decision === "approve")}
        </p>
      ) : null}
    </section>
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
              const found = ["board", "firmware", "case"].reduce((sum, role) => sum + (run.teams?.[role]?.issues || 0), 0) + (run.integration?.issues || 0);
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
 * The Team view, always there once a project has a team run: the live drafts
 * while a run works, then the last run and every earlier one. Tester approval
 * is not a check of the hardware; the labels say "approved by tester".
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
  const live = teamPreview ? runs.find(run => run.run === teamPreview.run) : null;
  const last = teamPreview ? null : runs[0];
  const earlier = runs.filter(run => run !== live && run !== last);
  return (
    <div data-slot="team-tab" className={cn("flex min-h-0 flex-1 flex-col overflow-auto", className)}>
      {teamPreview ? <TeamProgress preview={teamPreview} className="flex-none" /> : null}
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-6">
        {last ? <LastRun run={last} /> : null}
        {earlier.length ? <RunHistory runs={earlier} /> : null}
      </div>
    </div>
  );
}
