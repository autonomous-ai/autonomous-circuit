import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, SendHorizontal } from "lucide-react";
import { cn } from "@/ui/utils";
import ProjectMenu from "@/components/project/ProjectMenu.jsx";
import SidebarUserCard from "@/components/workbench/SidebarUserCard.jsx";
import { setPendingViewContext } from "@/store/chat.js";
import {
  FOCUS_CHAT_INPUT_EVENT,
  prefillChatInput,
} from "@/components/chat/chatInputHelpers.js";
import { boardLabel, boardStatus, boardStem } from "@/lib/boardModel.js";
import PanZoomView from "./PanZoomView.jsx";
import BomTable from "./BomTable.jsx";
import FabPacketCard from "./FabPacketCard.jsx";
import WarningsStrip from "./WarningsStrip.jsx";
import PartsPanel from "./PartsPanel.jsx";
import { buildViewContextNote } from "./boardData.js";

const STATUS_DOT = Object.freeze({
  ready: "bg-emerald-500",
  building: "bg-amber-400 animate-pulse",
  pending: "bg-muted-foreground/40",
});

const TABS = Object.freeze([
  { id: "schematic", label: "Schematic" },
  { id: "pcb", label: "PCB" },
  { id: "bom", label: "BOM" },
  { id: "fab", label: "Fab" },
]);

/**
 * The board workspace — replaces the donor's EpisodeWorkspace behind the same
 * seam from main.jsx. Layout: header (project name + settings) · left board
 * rail (one chip per board with status dots) · collapsible parts panel ·
 * center stage with Schematic / PCB / BOM / Fab tabs · bottom warnings strip.
 *
 * The layout-coordination props (`onModelsSidebarChange`, `onToolsSheetChange`,
 * `closeLeftSidebarSignal`) are kept from the donor seam so AppRoot's
 * chat-width clamp math is unchanged: the board rail is fixed-width and part
 * of the workspace floor, so both panels report closed/0; the close signal
 * collapses the parts panel (the only reclaimable width here).
 */
export default function BoardWorkspace({
  manifestRevision = 0,
  boardEntries = [],
  partsEntry = null,
  artifactActivity = {},
  catalogHydrated = false,
  catalogRefreshing = false,
  catalogError = "",
  onModelsSidebarChange,
  onToolsSheetChange,
  closeLeftSidebarSignal = 0,
  onOpenAccountScreen,
}) {
  const [selectedFile, setSelectedFile] = useState("");
  const [partsOpen, setPartsOpen] = useState(false);
  const [sidecar, setSidecar] = useState(null);
  const [activeTab, setActiveTab] = useState("schematic");
  const [pcbSide, setPcbSide] = useState("top");

  // The donor's collapsible panels don't exist here; report them closed once
  // so AppRoot's chat clamp treats the whole workspace as the viewer floor.
  useEffect(() => {
    onModelsSidebarChange?.(false, 0);
    onToolsSheetChange?.(false, 0);
  }, [onModelsSidebarChange, onToolsSheetChange]);

  // Chat dragging wide asks the workspace to give space back — collapse the
  // parts panel (the only optional width).
  const [lastCloseSignal, setLastCloseSignal] = useState(closeLeftSidebarSignal);
  useEffect(() => {
    if (closeLeftSidebarSignal !== lastCloseSignal) {
      setLastCloseSignal(closeLeftSidebarSignal);
      setPartsOpen(false);
    }
  }, [closeLeftSidebarSignal, lastCloseSignal]);

  // Keep a valid selection: follow the newest board until the user picks one;
  // if the selected file leaves the catalog, fall back to the last one.
  const selectedEntry = useMemo(
    () => boardEntries.find((entry) => entry.file === selectedFile) || null,
    [boardEntries, selectedFile],
  );
  useEffect(() => {
    if (!selectedEntry && boardEntries.length) {
      setSelectedFile(boardEntries[boardEntries.length - 1].file);
    }
  }, [selectedEntry, boardEntries]);

  // Reset per-board transient state when the selection moves.
  useEffect(() => {
    setSidecar(null);
    setPcbSide("top");
  }, [selectedFile]);

  // Sidecar (.board.json) fetch — keyed on metadataUrl, which carries the
  // ?v=<mtime>-<size> token, so a rebuild of the board refetches on its own.
  // NEVER strip the query.
  const metadataUrl = String(selectedEntry?.artifact?.metadataUrl || "");
  useEffect(() => {
    if (!metadataUrl) {
      setSidecar(null);
      return undefined;
    }
    let cancelled = false;
    fetch(metadataUrl)
      .then((response) => {
        if (!response.ok) throw new Error(`sidecar read failed (${response.status})`);
        return response.json();
      })
      .then((data) => {
        if (!cancelled) setSidecar(data);
      })
      .catch(() => {
        if (!cancelled) setSidecar(null);
      });
    return () => {
      cancelled = true;
    };
  }, [metadataUrl, manifestRevision]);

  const selectedStem = boardStem(selectedEntry?.file);
  const boardName = String(sidecar?.board?.name || "").trim() || selectedStem;
  const artifact = selectedEntry?.artifact || {};

  const handlePrefillNote = useCallback((text) => {
    prefillChatInput(text);
  }, []);

  // "Send to AI": set the model-facing view-context note (current tab + board)
  // and focus the composer — the repurposed pendingViewContext plumbing.
  const handleSendToAI = useCallback(() => {
    if (!selectedStem) return;
    setPendingViewContext(buildViewContextNote({ board: selectedStem, tab: activeTab }));
    if (typeof window !== "undefined") {
      window.dispatchEvent(new Event(FOCUS_CHAT_INPUT_EVENT));
    }
  }, [selectedStem, activeTab]);

  const hasBoards = boardEntries.length > 0;
  const pcbBottomUrl = String(artifact.pcbBottomUrl || "");

  return (
    <div data-slot="board-workspace" className="flex h-full w-full flex-col bg-background">
      {/* Header: project name + settings. */}
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-2.5">
        <span className="px-1 text-sm font-semibold tracking-tight text-foreground">Autonomous Circuit</span>
        <ProjectMenu />
        {catalogRefreshing ? (
          <Loader2
            className="size-3.5 animate-spin text-muted-foreground"
            aria-label="Refreshing catalog"
          />
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          {/* Local-only workspace card doubles as the settings affordance. */}
          <SidebarUserCard onOpenAccountScreen={onOpenAccountScreen} />
        </div>
      </header>

      {catalogError ? (
        <p
          data-slot="board-catalog-error"
          className="border-b border-destructive/40 bg-destructive/10 px-3 py-1 text-xs text-destructive"
        >
          {catalogError}
        </p>
      ) : null}

      <div className="flex min-h-0 flex-1">
        {/* Board rail. */}
        <nav
          data-slot="board-rail"
          aria-label="Boards"
          className="scrollbar-thin flex w-20 shrink-0 flex-col items-center gap-1.5 overflow-y-auto border-r border-border/60 py-3"
        >
          {boardEntries.map((entry) => {
            const stem = boardStem(entry.file);
            const status = boardStatus(entry, { activity: artifactActivity });
            const active = entry.file === selectedEntry?.file;
            return (
              <button
                key={entry.file}
                type="button"
                onClick={() => setSelectedFile(entry.file)}
                title={`${stem} — ${status}`}
                data-slot="board-chip"
                data-status={status}
                aria-current={active ? "true" : undefined}
                className={cn(
                  "relative flex h-9 w-16 shrink-0 items-center justify-center rounded-lg border px-1 font-mono text-[11px] transition-colors",
                  active
                    ? "border-primary/60 bg-primary/10 text-foreground"
                    : "border-border/60 bg-card/60 text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <span className="truncate">{boardLabel(stem)}</span>
                <span
                  aria-hidden
                  className={cn(
                    "absolute right-1 top-1 size-1.5 rounded-full",
                    STATUS_DOT[status] || STATUS_DOT.pending,
                  )}
                />
              </button>
            );
          })}
          {!hasBoards && catalogHydrated ? (
            <span className="px-1 text-center text-[10px] leading-4 text-muted-foreground/70">
              No boards yet
            </span>
          ) : null}
        </nav>

        <PartsPanel
          partsEntry={partsEntry}
          open={partsOpen}
          onToggle={() => setPartsOpen((value) => !value)}
        />

        {/* Stage + warnings. */}
        <div className="flex min-w-0 flex-1 flex-col">
          {selectedEntry ? (
            <>
              {/* Tab bar. */}
              <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border/60 px-2">
                {TABS.map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    data-slot="board-tab"
                    data-tab={tab.id}
                    aria-current={activeTab === tab.id ? "true" : undefined}
                    className={cn(
                      "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                      activeTab === tab.id
                        ? "bg-accent text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {tab.label}
                  </button>
                ))}
                <span className="ml-2 truncate font-mono text-[11px] text-muted-foreground">
                  {boardName}
                </span>
                <button
                  type="button"
                  onClick={handleSendToAI}
                  title="Send this view to the chat"
                  data-slot="board-send-to-ai"
                  className="ml-auto inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <SendHorizontal className="size-3" aria-hidden />
                  Send to AI
                </button>
              </div>

              {activeTab === "schematic" ? (
                <PanZoomView
                  src={String(artifact.schematicUrl || "")}
                  alt={`Schematic of ${boardName}`}
                  emptyText="The schematic lands once the board builds."
                  className="min-h-0 flex-1"
                />
              ) : null}
              {activeTab === "pcb" ? (
                <PanZoomView
                  src={
                    pcbSide === "bottom" && pcbBottomUrl
                      ? pcbBottomUrl
                      : String(artifact.pcbUrl || "")
                  }
                  alt={`PCB ${pcbSide} of ${boardName}`}
                  emptyText="The board layout lands once the board builds."
                  className="min-h-0 flex-1"
                  overlay={
                    pcbBottomUrl ? (
                      <div className="flex overflow-hidden rounded-md border border-border/60 bg-background/85 shadow">
                        {["top", "bottom"].map((side) => (
                          <button
                            key={side}
                            type="button"
                            onClick={() => setPcbSide(side)}
                            data-slot="pcb-side-toggle"
                            data-side={side}
                            className={cn(
                              "px-2 py-1 text-[11px] font-medium capitalize transition-colors",
                              pcbSide === side
                                ? "bg-accent text-foreground"
                                : "text-muted-foreground hover:text-foreground",
                            )}
                          >
                            {side}
                          </button>
                        ))}
                      </div>
                    ) : null
                  }
                />
              ) : null}
              {activeTab === "bom" ? (
                <BomTable
                  bomUrl={String(artifact.bomUrl || "")}
                  partsUrl={String(partsEntry?.url || "")}
                  className="min-h-0 flex-1"
                />
              ) : null}
              {activeTab === "fab" ? (
                <FabPacketCard
                  stem={selectedStem}
                  artifact={artifact}
                  sidecar={sidecar}
                  className="min-h-0 flex-1"
                />
              ) : null}

              <WarningsStrip sidecar={sidecar} onPrefillNote={handlePrefillNote} />
            </>
          ) : (
            <div
              data-slot="board-empty-state"
              className="grid min-h-0 flex-1 place-items-center"
              style={{ backgroundColor: "var(--ui-viewer-bg)" }}
            >
              <div className="flex flex-col items-center gap-3 px-6 text-center">
                {catalogHydrated || catalogError ? (
                  <>
                    <span className="text-5xl font-semibold tracking-tight text-white">
                      Circuit
                    </span>
                    <span className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
                      Autonomous Circuit
                    </span>
                    <p className="max-w-xs text-sm leading-6 text-white/60">
                      Chat a circuit board into existence. Describe the device
                      in the panel on the right — the schematic, layout, and
                      fab packet land here.
                    </p>
                  </>
                ) : (
                  <Loader2 className="size-5 animate-spin text-white/60" aria-hidden />
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
