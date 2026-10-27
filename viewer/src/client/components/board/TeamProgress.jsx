import { cn } from "@/ui/utils";

const LABELS = { board: "Board", firmware: "Firmware", case: "Case" };
const STATUS = {
  queued: "Waiting for task",
  draft: "Building draft",
  checking: "Checking",
  passed: "Private check passed",
  needs_revision: "Needs repair",
  blocked: "Blocked",
};

/** Progress is intentionally separate from the accepted board and its verdict. */
export default function TeamProgress({ preview, compact = false, className = "" }) {
  if (!preview || preview.verified !== false) return null;
  return (
    <section data-slot="team-progress" className={cn("border-b border-amber-500/25 bg-amber-500/5 text-foreground", compact ? "px-3 py-2" : "flex min-h-0 flex-1 flex-col overflow-auto p-6", className)}>
      <div className={cn("flex items-baseline gap-3", compact ? "" : "mx-auto w-full max-w-5xl")}>
        <strong className="text-xs font-semibold">Team progress · unverified drafts</strong>
        <span className="font-mono text-[11px] text-muted-foreground">{preview.run}</span>
        <span className="ml-auto text-[11px] text-muted-foreground">{preview.runStage || preview.runStatus}</span>
      </div>
      <div className={cn("grid gap-2", compact ? "mt-2 grid-cols-3" : "mx-auto mt-5 w-full max-w-5xl grid-cols-1 md:grid-cols-3")}>
        {["board", "firmware", "case"].map(role => {
          const team = preview.teams?.[role] || {};
          const media = Array.isArray(team.media) ? team.media : [];
          const files = Array.isArray(team.files) ? team.files : [];
          const passed = team.status === "passed";
          return (
            <article key={role} data-slot={`team-progress-${role}`} data-status={team.status || "queued"} className={cn("min-w-0 rounded-md border p-3", passed ? "border-emerald-500/50 bg-emerald-500/10" : "border-border/70 bg-background/60")}>
              <div className="flex items-baseline gap-2">
                <strong className="text-xs">{LABELS[role]}</strong>
                <span className={cn("ml-auto text-[11px]", passed ? "font-medium text-emerald-500" : "text-muted-foreground")}>{passed ? "✓ " : ""}{STATUS[team.status] || "Waiting"}</span>
              </div>
              {team.stage ? <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">{team.stage}</p> : null}
              {!compact && media.length ? (
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {media.slice(0, 4).map(item => (
                    <div key={item.url} className="block overflow-hidden rounded border border-border/70" title={item.file}>
                      <img src={item.url} alt={`Unverified ${LABELS[role]} draft: ${item.file}`} className="h-28 w-full object-contain" />
                    </div>
                  ))}
                </div>
              ) : null}
              {files.length ? (
                <p className="mt-2 break-words text-[11px] leading-4 text-muted-foreground" title={files.join("\n")}>
                  {files.slice(0, compact ? 2 : 5).join(" · ")}{files.length > (compact ? 2 : 5) ? ` · +${files.length - (compact ? 2 : 5)} file` : ""}
                </p>
              ) : null}
              {team.findings?.length ? <p className="mt-2 text-[11px] leading-4 text-amber-500">{team.findings[0]}</p> : null}
            </article>
          );
        })}
      </div>
      {!compact ? <p className="mx-auto mt-4 w-full max-w-5xl text-xs text-muted-foreground">These files show each team's work in progress. The board, case, and firmware are accepted only after their checks and integration pass.</p> : null}
    </section>
  );
}
