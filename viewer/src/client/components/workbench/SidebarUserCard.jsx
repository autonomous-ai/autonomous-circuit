"use client";

import { HardDrive, Settings } from "lucide-react";
import { useProjectsStore } from "@/store/projects.ts";
import { cn } from "@/ui/utils";

/**
 * Local-only account affordance for the workspace header. v1 is Create-only —
 * there is no account or network: the card shows where the work lives
 * ("Local") and how much of it there is (project count), and clicking it opens
 * the settings screen via `onOpenAccountScreen`. No transport calls.
 */
export default function SidebarUserCard({ onOpenAccountScreen, className }) {
  const projectCount = useProjectsStore((state) => state.projects.length);

  return (
    <button
      type="button"
      onClick={() => onOpenAccountScreen?.()}
      title="Workspace & settings"
      data-slot="sidebar-user-card"
      className={cn(
        "flex items-center gap-2 rounded-full border border-border/60 bg-card/60 py-1 pl-2 pr-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
        className,
      )}
    >
      <HardDrive className="size-3.5 shrink-0" aria-hidden />
      <span
        data-slot="sidebar-user-card-badge"
        className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
      >
        Local
      </span>
      <span data-slot="sidebar-user-card-count" className="whitespace-nowrap">
        {projectCount} {projectCount === 1 ? "project" : "projects"}
      </span>
      <span className="grid size-6 place-items-center rounded-full">
        <Settings className="size-3.5" aria-hidden />
      </span>
    </button>
  );
}
