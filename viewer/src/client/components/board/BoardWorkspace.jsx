import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Crosshair, Loader2, Ruler, SendHorizontal } from "lucide-react";
import { cn } from "@/ui/utils";
import ProjectMenu from "@/components/project/ProjectMenu.jsx";
import SidebarUserCard from "@/components/workbench/SidebarUserCard.jsx";
import { setPendingViewContext } from "@/store/chat.js";
import {
  FOCUS_CHAT_INPUT_EVENT,
  prefillChatInput,
} from "@/components/chat/chatInputHelpers.js";
import { boardLabel, boardStatus, boardStem } from "@/lib/boardModel.js";
import { buildBoardIndex, resolveSelection } from "@/lib/boardIndex.js";
import { defaultObjectClasses, nextHighlightMethod, nextSingleLayerMode } from "@/lib/boardPalette.js";
import BomTable from "./BomTable.jsx";
import FabPacketCard from "./FabPacketCard.jsx";
import PartsPanel from "./PartsPanel.jsx";
import PcbCanvas from "./PcbCanvas.jsx";
import SchematicCanvas from "./SchematicCanvas.jsx";
import LayerBar from "./LayerBar.jsx";
import BoardInsightHud from "./BoardInsightHud.jsx";
import MessagesPanel from "./MessagesPanel.jsx";
import PropertiesPanel from "./PropertiesPanel.jsx";
import { buildViewContextNote, normalizeParts, partsByLcsc } from "./boardData.js";

const STATUS_DOT = Object.freeze({
  ready: "bg-emerald-500",
  building: "bg-amber-400 animate-pulse",
  pending: "bg-muted-foreground/40",
});

const TABS = Object.freeze([
  { id: "split", label: "Split" },
  { id: "schematic", label: "Schematic" },
  { id: "pcb", label: "PCB" },
  { id: "bom", label: "BOM" },
  { id: "fab", label: "Fab" },
]);

const CANVAS_TABS = new Set(["split", "schematic", "pcb"]);

/** True when the event came from somewhere the user is typing. */
function isTypingTarget(target) {
  const tag = String(target?.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || target?.isContentEditable === true;
}

/**
 * The board workspace — an Altium-shaped PCB tool with our chat on the side.
 *
 * Layout: header · board rail · parts panel · stage (Split / Schematic / PCB /
 * BOM / Fab) · Properties on the right · layer bar and Messages along the
 * bottom. The chat sidebar stays exactly where AppRoot puts it — prompt on one
 * side, board on the other.
 *
 * Everything interactive hangs off ONE piece of state: `selection`, a
 * `{kind: "component"|"net", key}` resolved through `boardIndex.resolveSelection`
 * into element-id sets for both canvases plus a refdes set for the BOM. That is
 * what makes cross-probing real rather than three views that happen to agree.
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
  const [circuit, setCircuit] = useState(null);
  const [circuitState, setCircuitState] = useState("idle"); // idle | loading | ready | failed
  const [parts, setParts] = useState([]);
  const [activeTab, setActiveTab] = useState("split");

  // --- editor state (all Altium analogues; see ALTIUM-NOTES.md)
  const [selection, setSelection] = useState(null);
  const [scheme, setScheme] = useState("studio");
  const [hiddenLayers, setHiddenLayers] = useState(() => new Set());
  const [visibleClasses, setVisibleClasses] = useState(() => defaultObjectClasses());
  const [activeLayer, setActiveLayer] = useState("top");
  const [singleLayerMode, setSingleLayerMode] = useState("off");
  const [highlightMethod, setHighlightMethod] = useState("dim");
  const [maskLevel, setMaskLevel] = useState(3);
  const [units, setUnits] = useState("mm");
  const [measuring, setMeasuring] = useState(false);
  const [hudVisible, setHudVisible] = useState(true);
  const [messagesOpen, setMessagesOpen] = useState(true);
  const [hover, setHover] = useState(null);
  const [flash, setFlash] = useState(null);
  const [pcbView, setPcbView] = useState({ scale: 0 });

  const pcbRef = useRef(null);
  const schematicRef = useRef(null);

  useEffect(() => {
    onModelsSidebarChange?.(false, 0);
    onToolsSheetChange?.(false, 0);
  }, [onModelsSidebarChange, onToolsSheetChange]);

  const [lastCloseSignal, setLastCloseSignal] = useState(closeLeftSidebarSignal);
  useEffect(() => {
    if (closeLeftSidebarSignal !== lastCloseSignal) {
      setLastCloseSignal(closeLeftSidebarSignal);
      setPartsOpen(false);
    }
  }, [closeLeftSidebarSignal, lastCloseSignal]);

  const selectedEntry = useMemo(
    () => boardEntries.find((entry) => entry.file === selectedFile) || null,
    [boardEntries, selectedFile],
  );
  useEffect(() => {
    if (!selectedEntry && boardEntries.length) {
      setSelectedFile(boardEntries[boardEntries.length - 1].file);
    }
  }, [selectedEntry, boardEntries]);

  useEffect(() => {
    setSidecar(null);
    setCircuit(null);
    setCircuitState("idle");
    setSelection(null);
    setSingleLayerMode("off");
    setHiddenLayers(new Set());
  }, [selectedFile]);

  // Sidecar (.board.json) — keyed on metadataUrl, which carries ?v=<mtime>-<size>,
  // so a rebuild refetches on its own. NEVER strip the query.
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

  // Circuit JSON — the artifact of record, and the thing that makes every
  // element hit-testable. Same ?v= discipline.
  const circuitJsonUrl = String(selectedEntry?.artifact?.circuitJsonUrl || "");
  useEffect(() => {
    if (!circuitJsonUrl) {
      setCircuit(null);
      setCircuitState("idle");
      return undefined;
    }
    let cancelled = false;
    setCircuitState("loading");
    fetch(circuitJsonUrl)
      .then((response) => {
        if (!response.ok) throw new Error(`circuit.json read failed (${response.status})`);
        return response.json();
      })
      .then((data) => {
        if (cancelled) return;
        setCircuit(data);
        setCircuitState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setCircuit(null);
        setCircuitState("failed");
      });
    return () => {
      cancelled = true;
    };
  }, [circuitJsonUrl, manifestRevision]);

  // parts.json enrichment for Properties and BOM — best effort.
  const partsUrl = String(partsEntry?.url || "");
  useEffect(() => {
    if (!partsUrl) {
      setParts([]);
      return undefined;
    }
    let cancelled = false;
    fetch(partsUrl)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled) setParts(data ? normalizeParts(data) : []);
      })
      .catch(() => {
        if (!cancelled) setParts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [partsUrl]);

  const index = useMemo(() => (circuit ? buildBoardIndex(circuit) : null), [circuit]);
  const partsMap = useMemo(() => partsByLcsc(parts), [parts]);
  const highlight = useMemo(() => (index ? resolveSelection(index, selection) : null), [index, selection]);

  const layers = useMemo(() => {
    const list = index?.layers || ["top", "bottom"];
    // Present them the way a layer bar does: top first, then inners, bottom last.
    const order = ["top", "inner1", "inner2", "inner3", "inner4", "bottom"];
    return [...list].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  }, [index]);

  const visibleLayers = useMemo(() => {
    const set = new Set(layers);
    for (const layer of hiddenLayers) set.delete(layer);
    return set;
  }, [layers, hiddenLayers]);

  useEffect(() => {
    if (layers.length && !layers.includes(activeLayer)) setActiveLayer(layers[0]);
  }, [layers, activeLayer]);

  const selectedStem = boardStem(selectedEntry?.file);
  const boardName = String(sidecar?.board?.name || "").trim() || selectedStem;
  const artifact = selectedEntry?.artifact || {};

  // --- selection + cross-probe
  const handleSelect = useCallback(
    (next, options = {}) => {
      setSelection(next);
      if (!next || !options.jump || !index) return;
      const resolved = resolveSelection(index, next);
      // Altium's rule: plain click stays put, Ctrl/⌘ jumps. On a jump we move
      // the OTHER pane, and in Split we move both so the two stay in step.
      if (options.source !== "pcb" || activeTab === "split") pcbRef.current?.zoomToBox?.(resolved.pcbBox);
      if (options.source !== "schematic" || activeTab === "split") {
        schematicRef.current?.zoomToBox?.(resolved.schematicBox);
      }
    },
    [index, activeTab],
  );

  const handleLocate = useCallback(
    (row) => {
      if (!row) return;
      if (row.target.kind === "component" || row.target.kind === "net") {
        setSelection({ kind: row.target.kind, key: row.target.key });
      }
      if (!row.box) return;
      if (activeTab === "bom" || activeTab === "fab") setActiveTab("split");
      pcbRef.current?.zoomToBox?.(row.box);
      setFlash({ box: row.box, token: Date.now() });
    },
    [activeTab],
  );

  // Flash decays on its own — a marker that never clears becomes furniture.
  useEffect(() => {
    if (!flash) return undefined;
    const timer = setTimeout(() => setFlash(null), 1600);
    return () => clearTimeout(timer);
  }, [flash]);

  const handlePrefillNote = useCallback((text) => {
    prefillChatInput(text);
  }, []);

  const handleSendToAI = useCallback(() => {
    if (!selectedStem) return;
    setPendingViewContext(buildViewContextNote({ board: selectedStem, tab: activeTab, selection, index }));
    if (typeof window !== "undefined") {
      window.dispatchEvent(new Event(FOCUS_CHAT_INPUT_EVENT));
    }
  }, [selectedStem, activeTab, selection, index]);

  const fitAll = useCallback(() => {
    pcbRef.current?.fitToBoard?.();
    schematicRef.current?.fitToSheet?.();
  }, []);

  // --- keyboard, honouring Altium's bindings where a browser lets us
  useEffect(() => {
    const onKey = (event) => {
      if (isTypingTarget(event.target)) return;
      const key = event.key;
      if (key === "Escape") {
        setSelection(null);
        setMeasuring(false);
        return;
      }
      if (event.metaKey || event.ctrlKey) {
        if (key.toLowerCase() === "m") {
          event.preventDefault();
          setMeasuring((value) => !value);
        }
        if (key === "PageDown") {
          event.preventDefault();
          fitAll();
        }
        return;
      }
      if (event.shiftKey) {
        if (key.toLowerCase() === "c") setSelection(null);
        if (key.toLowerCase() === "s") setSingleLayerMode(nextSingleLayerMode(singleLayerMode));
        if (key.toLowerCase() === "h") setHudVisible((value) => !value);
        return;
      }
      switch (key) {
        case "1":
          setActiveTab("schematic");
          break;
        case "2":
          setActiveTab("pcb");
          break;
        case "3":
          setActiveTab("pcb");
          break;
        case "0":
          setActiveTab("split");
          break;
        case "f":
        case "F":
          fitAll();
          break;
        case "q":
        case "Q":
          setUnits((value) => (value === "mm" ? "mil" : "mm"));
          break;
        case "m":
        case "M":
          setHighlightMethod((value) => nextHighlightMethod(value));
          break;
        case "[":
          setMaskLevel((value) => Math.max(0, value - 1));
          break;
        case "]":
          setMaskLevel((value) => Math.min(5, value + 1));
          break;
        case "l":
        case "L":
          setMessagesOpen((value) => !value);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fitAll, singleLayerMode]);

  const hasBoards = boardEntries.length > 0;
  const hoverNetName = useMemo(() => {
    if (!hover?.netKey || !index) return hover?.label || "";
    return index.netByKey.get(hover.netKey)?.name || "";
  }, [hover, index]);

  const schematicPane = (
    <SchematicCanvas
      index={index}
      src={String(artifact.schematicUrl || "")}
      scheme={scheme}
      highlight={highlight}
      highlightMethod={highlightMethod}
      maskLevel={maskLevel}
      onSelect={handleSelect}
      onHoverChange={setHover}
      viewRef={schematicRef}
    />
  );

  const pcbPane = (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      <PcbCanvas
        index={index}
        scheme={scheme}
        visibleLayers={visibleLayers}
        visibleClasses={visibleClasses}
        activeLayer={activeLayer}
        singleLayerMode={singleLayerMode}
        highlight={highlight}
        selection={selection}
        highlightMethod={highlightMethod}
        maskLevel={maskLevel}
        units={units}
        measuring={measuring}
        flash={flash}
        fallbackSrc={String(artifact.pcbUrl || "")}
        onSelect={handleSelect}
        onHoverChange={setHover}
        onViewChange={setPcbView}
        viewRef={pcbRef}
      />
      <BoardInsightHud
        hover={hover}
        activeLayer={activeLayer}
        units={units}
        scale={pcbView.scale}
        netName={hoverNetName}
        visible={hudVisible}
        measuring={measuring}
      />
      {circuitState === "loading" ? (
        <span className="pointer-events-none absolute right-2 top-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[10px] text-white/60">
          indexing circuit.json…
        </span>
      ) : null}
      {circuitState === "failed" ? (
        <span className="pointer-events-none absolute right-2 top-2 rounded border border-amber-500/40 bg-black/70 px-1.5 py-0.5 font-mono text-[10px] text-amber-300/90">
          circuit.json unreadable — showing the rendered image
        </span>
      ) : null}
    </div>
  );

  return (
    <div data-slot="board-workspace" className="flex h-full w-full flex-col bg-background">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-2.5">
        <span className="px-1 text-sm font-semibold tracking-tight text-foreground">Autonomous Circuit</span>
        <ProjectMenu />
        {catalogRefreshing ? (
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Refreshing catalog" />
        ) : null}
        <div className="ml-auto flex items-center gap-1">
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
                  className={cn("absolute right-1 top-1 size-1.5 rounded-full", STATUS_DOT[status] || STATUS_DOT.pending)}
                />
              </button>
            );
          })}
          {!hasBoards && catalogHydrated ? (
            <span className="px-1 text-center text-[10px] leading-4 text-muted-foreground/70">No boards yet</span>
          ) : null}
        </nav>

        <PartsPanel partsEntry={partsEntry} open={partsOpen} onToggle={() => setPartsOpen((value) => !value)} />

        <div className="flex min-w-0 flex-1 flex-col">
          {selectedEntry ? (
            <>
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
                      activeTab === tab.id ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {tab.label}
                  </button>
                ))}
                <span className="ml-2 truncate font-mono text-[11px] text-muted-foreground">{boardName}</span>

                {CANVAS_TABS.has(activeTab) ? (
                  <div className="ml-3 flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setMeasuring((value) => !value)}
                      data-slot="measure-toggle"
                      aria-pressed={measuring}
                      title="Measure (Ctrl+M)"
                      className={cn(
                        "flex items-center gap-1 rounded px-1.5 py-1 text-[11px] transition-colors",
                        measuring ? "bg-primary/20 text-foreground" : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <Ruler className="size-3" aria-hidden />
                      Measure
                    </button>
                    <button
                      type="button"
                      onClick={fitAll}
                      title="Zoom to fit (F)"
                      data-slot="fit-button"
                      className="flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                    >
                      <Crosshair className="size-3" aria-hidden />
                      Fit
                    </button>
                    {selection ? (
                      <span
                        data-slot="selection-chip"
                        className="ml-1 flex items-center gap-1 rounded border border-primary/40 bg-primary/10 px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                      >
                        {selection.kind === "net"
                          ? index?.netByKey.get(selection.key)?.name || "net"
                          : index?.componentBySourceId.get(selection.key)?.refdes || "part"}
                        <button
                          type="button"
                          onClick={() => setSelection(null)}
                          title="Clear filter (Shift+C or Esc)"
                          className="text-muted-foreground hover:text-foreground"
                        >
                          ×
                        </button>
                      </span>
                    ) : null}
                  </div>
                ) : null}

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

              <div className="flex min-h-0 flex-1">
                <div className="flex min-w-0 flex-1 flex-col">
                  {activeTab === "split" ? (
                    <div className="flex min-h-0 flex-1">
                      <div className="flex min-w-0 flex-1 flex-col border-r border-border/60">{schematicPane}</div>
                      {pcbPane}
                    </div>
                  ) : null}
                  {activeTab === "schematic" ? schematicPane : null}
                  {activeTab === "pcb" ? pcbPane : null}
                  {activeTab === "bom" ? (
                    <BomTable
                      bomUrl={String(artifact.bomUrl || "")}
                      partsUrl={partsUrl}
                      index={index}
                      highlight={highlight}
                      onSelect={handleSelect}
                      className="min-h-0 flex-1"
                    />
                  ) : null}
                  {activeTab === "fab" ? (
                    <FabPacketCard stem={selectedStem} artifact={artifact} sidecar={sidecar} className="min-h-0 flex-1" />
                  ) : null}

                  {CANVAS_TABS.has(activeTab) && activeTab !== "schematic" ? (
                    <LayerBar
                      layers={layers}
                      activeLayer={activeLayer}
                      visibleLayers={visibleLayers}
                      onToggleLayer={(layer) =>
                        setHiddenLayers((prev) => {
                          const next = new Set(prev);
                          if (next.has(layer)) next.delete(layer);
                          else next.add(layer);
                          return next;
                        })
                      }
                      onActivateLayer={setActiveLayer}
                      visibleClasses={visibleClasses}
                      onToggleClass={(id) =>
                        setVisibleClasses((prev) => {
                          const next = new Set(prev);
                          if (next.has(id)) next.delete(id);
                          else next.add(id);
                          return next;
                        })
                      }
                      singleLayerMode={singleLayerMode}
                      onCycleSingleLayer={() => setSingleLayerMode(nextSingleLayerMode(singleLayerMode))}
                      scheme={scheme}
                      onCycleScheme={() => setScheme((value) => (value === "studio" ? "altium" : "studio"))}
                      highlightMethod={highlightMethod}
                      onCycleHighlightMethod={() => setHighlightMethod(nextHighlightMethod(highlightMethod))}
                      maskLevel={maskLevel}
                      units={units}
                      onToggleUnits={() => setUnits((value) => (value === "mm" ? "mil" : "mm"))}
                    />
                  ) : null}

                  <MessagesPanel
                    index={index}
                    sidecar={sidecar}
                    selection={selection}
                    onSelect={handleSelect}
                    onLocate={handleLocate}
                    onPrefillNote={handlePrefillNote}
                    open={messagesOpen}
                    onToggleOpen={() => setMessagesOpen((value) => !value)}
                  />
                </div>

                <PropertiesPanel
                  index={index}
                  sidecar={sidecar}
                  selection={selection}
                  partsByLcscMap={partsMap}
                  units={units}
                  onSelect={handleSelect}
                />
              </div>
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
                    <span className="text-5xl font-semibold tracking-tight text-white">Circuit</span>
                    <span className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
                      Autonomous Circuit
                    </span>
                    <p className="max-w-xs text-sm leading-6 text-white/60">
                      Chat a circuit board into existence. Describe the device in the panel on the right — the
                      schematic, layout, and fab packet land here.
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
