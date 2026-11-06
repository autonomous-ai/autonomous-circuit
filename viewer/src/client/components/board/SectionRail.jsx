import { Box, CircuitBoard, FileText, Users } from "lucide-react";
import { cn } from "@/ui/utils";

const ICONS = { prompts: FileText, electric: CircuitBoard, case: Box, team: Users };

/**
 * The four sections as a slim icon rail beside the board, so the top row only
 * carries the views of the section you are in.
 */
export default function SectionRail({ groups, activeGroup, onSelect, teamRunning = false }) {
  return (
    <nav aria-label="Sections" data-slot="section-rail"
      className="flex w-16 shrink-0 flex-col items-center gap-1 border-r border-border/60 bg-muted/20 py-2">
      {groups.map(group => {
        const Icon = ICONS[group.id] || FileText;
        const active = activeGroup === group.id;
        return (
          <button key={group.id} type="button" data-slot="board-group" data-group={group.id}
            onClick={() => onSelect(group.id)}
            aria-current={active ? "true" : undefined}
            className={cn("relative flex w-14 flex-col items-center gap-1 rounded-lg py-2 text-[11px] font-medium transition-colors",
              active ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
            <Icon className="size-5" aria-hidden />
            {group.label}
            {group.id === "team" && teamRunning ? (
              <span data-slot="team-running-dot" aria-label="running"
                className="absolute right-3 top-1.5 size-2 rounded-full bg-emerald-500 ring-2 ring-background" />
            ) : null}
          </button>
        );
      })}
    </nav>
  );
}
