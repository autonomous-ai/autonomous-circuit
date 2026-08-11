import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CircuitBoard,
  Cpu,
  Folder,
  FolderOpen,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Waypoints,
  X,
} from "lucide-react";
import { cn } from "@/ui/utils";
import {
  ancestorIds,
  buildBoardTree,
  expansionForNode,
  filterTree,
  findSelectionNodeId,
  visibleRows,
} from "./boardTree.js";
import { buildStatusLine } from "./buildStatus.js";

const STATUS_DOT = Object.freeze({
  ready: "bg-emerald-500",
  building: "bg-amber-400 animate-pulse",
  pending: "bg-muted-foreground/40",
});

const NET_CLASS_DOT = Object.freeze({
  power: "bg-[#e05252]",
  ground: "bg-[#8f8f8f]",
  signal: "bg-[#4d7fc4]",
});

const TONE_TEXT = Object.freeze({
  running: "text-amber-500",
  done: "text-emerald-500",
  failed: "text-[#d75b6b]",
  stale: "text-[#ffd042]",
});

const TONE_BAR = Object.freeze({
  running: "bg-amber-400",
  done: "bg-emerald-500",
  failed: "bg-[#d75b6b]",
  stale: "bg-[#ffd042]",
});

const ROW_ICON = Object.freeze({
  component: Cpu,
  net: Waypoints,
});

/** The one line under the active board while the pipeline is working. */
function BuildStatusLine({ line }) {
  if (!line) return null;
  return (
    <div data-slot="build-status" data-tone={line.tone} className="px-2 pb-1 pl-8">
      <div className="flex items-baseline gap-1.5">
        <span className={cn("truncate text-[11px] font-medium", TONE_TEXT[line.tone])}>{line.text}</span>
        {line.detail ? (
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/70">{line.detail}</span>
        ) : null}
      </div>
      <div className="mt-1 h-[3px] overflow-hidden rounded-full bg-border/60">
        <div
          className={cn("h-full rounded-full transition-[width] duration-500", TONE_BAR[line.tone])}
          style={{ width: `${Math.round(line.progress * 100)}%` }}
        />
      </div>
    </div>
  );
}

/**
 * The workspace navigator — Vibe's PROJECTS sidebar, taken two levels deeper
 * because a board has contents a mesh does not.
 *
 *   Project → Board → Components (by refdes prefix) / Nets (by class) → leaf
 *
 * A leaf click is the same gesture as a canvas click: it calls the workspace's
 * `onSelect({kind, key})`, and ⌘/Ctrl-click adds `{jump: true}` so the canvases
 * zoom to it — the modifier rule from ALTIUM-NOTES §1, kept identical here so
 * there is one thing to learn rather than three.
 *
 * Selection flows the other way too: whatever the canvas selects gets its
 * ancestors expanded and is scrolled into view, so the tree is never lying
 * about where you are.
 */
export default function BoardTreeSidebar({
  projects = [],
  currentProjectId = "",
  boardEntries = [],
  selectedFile = "",
  index = null,
  selection = null,
  buildStatus = null,
  boardStatusOf,
  boardLabelOf,
  onSelectBoard,
  onOpenProject,
  onSelect,
  onReadProjectCatalog,
  open = true,
  onToggleOpen,
  className,
}) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(() => new Set());
  const [catalogs, setCatalogs] = useState(() => new Map());
  const listRef = useRef(null);

  const queryActive = query.trim().length > 0;

  const roots = useMemo(
    () =>
      buildBoardTree({
        projects,
        currentProjectId,
        boardEntries,
        projectCatalogs: catalogs,
        selectedFile,
        index,
        boardStatusOf,
        boardLabelOf,
      }),
    [projects, currentProjectId, boardEntries, catalogs, selectedFile, index, boardStatusOf, boardLabelOf],
  );

  const shown = useMemo(() => filterTree(roots, query), [roots, query]);
  const rows = useMemo(
    () => visibleRows(shown, expanded, { forceExpand: queryActive }),
    [shown, expanded, queryActive],
  );

  // The active project and its selected board open themselves — landing in a
  // workspace whose tree is entirely shut is a worse first frame than one
  // that shows what you are already looking at.
  const activeBoardId = useMemo(() => {
    const project = roots.find((entry) => entry.active);
    return project?.children.find((board) => board.selected)?.id || "";
  }, [roots]);
  useEffect(() => {
    if (!activeBoardId) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of ancestorIds(activeBoardId)) {
        if (!next.has(id)) {
          next.add(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [activeBoardId]);

  // Follow the canvas: expand to whatever is selected and scroll it in.
  const selectionNodeId = useMemo(() => findSelectionNodeId(roots, selection), [roots, selection]);
  useEffect(() => {
    if (!selectionNodeId) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of expansionForNode(selectionNodeId)) {
        if (!next.has(id)) {
          next.add(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [selectionNodeId]);
  useEffect(() => {
    if (!selectionNodeId || !listRef.current) return;
    const node = listRef.current.querySelector(`[data-node-id="${CSS.escape(selectionNodeId)}"]`);
    node?.scrollIntoView({ block: "nearest" });
  }, [selectionNodeId, rows.length]);

  // A foreign project's catalog is fetched the first time it is opened, then
  // cached — same lazy contract as the donor's useProjectsFileTree.
  const loadCatalog = useCallback(
    async (projectId) => {
      if (!projectId || !onReadProjectCatalog) return;
      setCatalogs((prev) => {
        if (prev.has(projectId)) return prev;
        const next = new Map(prev);
        next.set(projectId, { status: "loading", boards: [] });
        return next;
      });
      try {
        const boards = await onReadProjectCatalog(projectId);
        setCatalogs((prev) => new Map(prev).set(projectId, { status: "ready", boards: boards || [] }));
      } catch (error) {
        setCatalogs((prev) =>
          new Map(prev).set(projectId, {
            status: "error",
            boards: [],
            error: error instanceof Error ? error.message : "Could not read this project",
          }),
        );
      }
    },
    [onReadProjectCatalog],
  );

  const toggle = useCallback(
    (row) => {
      const { node } = row;
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(node.id)) next.delete(node.id);
        else next.add(node.id);
        return next;
      });
      if (node.kind === "project" && !node.active && !catalogs.has(node.projectId)) {
        void loadCatalog(node.projectId);
      }
    },
    [catalogs, loadCatalog],
  );

  const activate = useCallback(
    (row, event) => {
      const { node } = row;
      if (node.kind === "project") {
        if (!node.active) onOpenProject?.(node.projectId);
        else toggle(row);
        return;
      }
      if (node.kind === "board") {
        if (node.projectId !== currentProjectId) onOpenProject?.(node.projectId, node.boardFile);
        else onSelectBoard?.(node.boardFile);
        return;
      }
      if (node.select) {
        // Same modifier rule as the canvases: plain click stays put, ⌘/Ctrl jumps.
        onSelect?.(node.select, { jump: event?.metaKey || event?.ctrlKey, source: "tree" });
        return;
      }
      toggle(row);
    },
    [currentProjectId, onOpenProject, onSelectBoard, onSelect, toggle],
  );

  const statusLine = useMemo(() => buildStatusLine(buildStatus), [buildStatus]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={onToggleOpen}
        title="Show project tree"
        aria-label="Show project tree"
        data-slot="board-tree-toggle"
        className="flex w-9 shrink-0 flex-col items-center gap-2 border-r border-border/60 py-3 text-muted-foreground transition-colors hover:text-foreground"
      >
        <PanelLeftOpen className="size-4" aria-hidden />
        <CircuitBoard className="size-4" aria-hidden />
      </button>
    );
  }

  return (
    <aside
      data-slot="board-tree"
      className={cn("flex w-64 shrink-0 flex-col border-r border-border/60", className)}
    >
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-border/60 px-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Projects</span>
        <button
          type="button"
          onClick={onToggleOpen}
          title="Hide project tree"
          aria-label="Hide project tree"
          className="ml-auto grid size-6 place-items-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <PanelLeftClose className="size-3.5" aria-hidden />
        </button>
      </header>

      <div className="relative shrink-0 border-b border-border/60 px-2 py-1.5">
        <Search className="pointer-events-none absolute left-4 top-1/2 size-3 -translate-y-1/2 text-muted-foreground/60" aria-hidden />
        <input
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search parts, nets, boards"
          data-slot="board-tree-search"
          className="h-7 w-full rounded-md border border-border/50 bg-background/60 pl-7 pr-6 text-[12px] text-foreground outline-none placeholder:text-muted-foreground/60 focus:border-ring/50"
        />
        {queryActive ? (
          <button
            type="button"
            onClick={() => setQuery("")}
            title="Clear search"
            aria-label="Clear search"
            className="absolute right-3.5 top-1/2 -translate-y-1/2 text-muted-foreground/60 hover:text-foreground"
          >
            <X className="size-3" aria-hidden />
          </button>
        ) : null}
      </div>

      <div ref={listRef} className="scrollbar-thin min-h-0 flex-1 overflow-y-auto py-1">
        {!rows.length ? (
          <p className="px-3 py-6 text-center text-[11px] leading-5 text-muted-foreground">
            {queryActive ? `Nothing matches “${query.trim()}”` : "No projects yet"}
          </p>
        ) : null}

        {rows.map((row) => {
          const { node, depth, expanded: isOpen } = row;
          const isSelectionLeaf = Boolean(node.select) && node.id === selectionNodeId;
          const isSelectedBoard = node.kind === "board" && node.selected;
          const LeafIcon = ROW_ICON[node.kind];
          return (
            <div key={node.id}>
              <div
                data-slot="board-tree-row"
                data-node-id={node.id}
                data-kind={node.kind}
                aria-current={isSelectionLeaf || isSelectedBoard ? "true" : undefined}
                onClick={(event) => activate(row, event)}
                className={cn(
                  "group flex h-6 cursor-default items-center gap-1 rounded pr-2 text-[12px] transition-colors",
                  isSelectionLeaf || isSelectedBoard
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )}
                style={{ paddingLeft: `${6 + depth * 11}px` }}
              >
                {node.expandable ? (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      if (!queryActive) toggle(row);
                    }}
                    aria-label={isOpen ? "Collapse" : "Expand"}
                    className={cn(
                      "grid size-4 shrink-0 place-items-center text-muted-foreground/70 hover:text-foreground",
                      queryActive && "cursor-default",
                    )}
                  >
                    {isOpen ? (
                      <ChevronDown className="size-3" aria-hidden />
                    ) : (
                      <ChevronRight className="size-3" aria-hidden />
                    )}
                  </button>
                ) : (
                  <span className="size-4 shrink-0" aria-hidden />
                )}

                {node.kind === "project" ? (
                  node.loading ? (
                    <Loader2 className="size-3 shrink-0 animate-spin text-muted-foreground/70" aria-hidden />
                  ) : isOpen ? (
                    <FolderOpen className="size-3 shrink-0 text-muted-foreground/70" aria-hidden />
                  ) : (
                    <Folder className="size-3 shrink-0 text-muted-foreground/70" aria-hidden />
                  )
                ) : null}
                {node.kind === "board" ? (
                  <CircuitBoard className="size-3 shrink-0 text-muted-foreground/70" aria-hidden />
                ) : null}
                {LeafIcon ? <LeafIcon className="size-3 shrink-0 text-muted-foreground/50" aria-hidden /> : null}
                {node.kind === "net" ? (
                  <span
                    aria-hidden
                    className={cn("size-1.5 shrink-0 rounded-full", NET_CLASS_DOT[node.netClass] || NET_CLASS_DOT.signal)}
                  />
                ) : null}

                <span
                  className={cn(
                    "min-w-0 flex-1 truncate",
                    node.kind === "project" && "font-medium",
                    (node.kind === "component" || node.kind === "net") && "font-mono text-[11px]",
                    node.unnamed && "italic opacity-70",
                  )}
                  title={node.sublabel ? `${node.label} — ${node.sublabel}` : node.label}
                >
                  {node.label}
                </span>

                {node.sublabel ? (
                  <span className="shrink-0 truncate font-mono text-[10px] text-muted-foreground/50 group-hover:text-muted-foreground/80">
                    {node.sublabel}
                  </span>
                ) : null}
                {Number.isFinite(node.count) ? (
                  <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/50">
                    {node.count}
                  </span>
                ) : null}
                {node.kind === "board" ? (
                  <span
                    aria-hidden
                    className={cn("size-1.5 shrink-0 rounded-full", STATUS_DOT[node.status] || STATUS_DOT.pending)}
                  />
                ) : null}
              </div>

              {isSelectedBoard ? <BuildStatusLine line={statusLine} /> : null}

              {node.kind === "project" && node.error ? (
                <p className="px-3 pb-1 pl-8 text-[10px] leading-4 text-destructive">{node.error}</p>
              ) : null}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
