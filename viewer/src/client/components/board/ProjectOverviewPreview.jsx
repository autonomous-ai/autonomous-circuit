import { useState } from "react";
import { cn } from "@/ui/utils";
import { FullView } from "./CaseOverviewTab.jsx";

/** Accept only this project's HTML served by the viewer, never a remote URL. */
export function safeProjectOverviewUrl(value, projectId) {
  const raw = String(value || "");
  if (!raw.startsWith("/") || raw.startsWith("//") || !projectId) return "";
  try {
    const url = new URL(raw, "http://viewer.invalid");
    const projectPath = `/projects/${encodeURIComponent(projectId)}/`;
    if (url.origin !== "http://viewer.invalid" || !url.pathname.startsWith(projectPath) || !url.pathname.endsWith(".html")) return "";
    return `${url.pathname}${url.search}`;
  } catch {
    return "";
  }
}

export default function ProjectOverviewPreview({ url, className }) {
  const [full, setFull] = useState(false);
  return (
    <section data-slot="project-overview-preview" className={cn("flex min-h-0 flex-col", className)}>
      <div className="flex shrink-0 items-center gap-3 border-b border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs">
        <span className="font-medium text-amber-700">Reference layout — unverified; no checked KiCad board yet</span>
        <button type="button" data-slot="project-open-overview" onClick={() => setFull(true)} className="ml-auto shrink-0 text-primary underline">
          Open full overview
        </button>
      </div>
      <iframe
        title="Unverified project overview"
        src={url}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        className="min-h-0 flex-1 border-0 bg-background"
      />
      {full ? <FullView view={{ url, title: "Project overview" }} onClose={() => setFull(false)} /> : null}
    </section>
  );
}
