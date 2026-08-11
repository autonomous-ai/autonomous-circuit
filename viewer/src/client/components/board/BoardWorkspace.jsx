import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, SendHorizontal } from "lucide-react";
import { cn } from "@/ui/utils";
import ProjectMenu from "@/components/project/ProjectMenu.jsx";
import SidebarUserCard from "@/components/workbench/SidebarUserCard.jsx";
import { setPendingViewContext, setProject as setChatProject } from "@/store/chat.js";
import {
  FOCUS_CHAT_INPUT_EVENT,
  prefillChatInput,
} from "@/components/chat/chatInputHelpers.js";
import { boardStatus, boardStem, selectBoardEntries } from "@/lib/boardModel.js";
import { buildBoardIndex, resolveSelection } from "@/lib/boardIndex.js";
import { buildMessages } from "@/lib/boardViolations.js";
import { groupFindings, partPlainName } from "@/lib/plainLanguage.js";
import { defaultObjectClasses, nextHighlightMethod, nextSingleLayerMode } from "@/lib/boardPalette.js";
import { transport } from "@/lib/transport.ts";
import { useProjectsStore } from "@/store/projects.ts";
import { useChatStore } from "@/store/chat.js";
import { triggerBlobDownload } from "@/ui/download.js";
import Board3DView from "./Board3DView.jsx";
import BoardActions from "./BoardActions.jsx";
import BoardOrientationCube from "./BoardOrientationCube.jsx";
import BoardTreeSidebar from "./BoardTreeSidebar.jsx";
import RevisionPager from "./RevisionPager.jsx";
import ViewportToolRail from "./ViewportToolRail.jsx";
import useBoardRevisions from "./useBoardRevisions.js";
import useBuildStatus from "./useBuildStatus.js";
import BoardVerdict from "./BoardVerdict.jsx";
import BomTable from "./BomTable.jsx";
import FabPacketCard from "./FabPacketCard.jsx";
import OverviewTab from "./OverviewTab.jsx";
import PartsPanel from "./PartsPanel.jsx";
import PcbCanvas from "./PcbCanvas.jsx";
import SchematicCanvas from "./SchematicCanvas.jsx";
import StartHere from "./StartHere.jsx";
import LayerBar from "./LayerBar.jsx";
import BoardInsightHud from "./BoardInsightHud.jsx";
import MessagesPanel from "./MessagesPanel.jsx";
import PropertiesPanel from "./PropertiesPanel.jsx";
import { buildStatusLine } from "./buildStatus.js";
import { buildViewContextNote, normalizeParts, normalizeWarnings, partsByLcsc } from "./boardData.js";

const TABS = Object.freeze([
  // Overview is first and default on purpose: someone who has never opened an
  // EDA tool lands on Split otherwise and sees two drawings with no way in.
  // An engineer is one click (or `0`) from the split view they want.
  { id: "overview", label: "Overview" },
  { id: "split", label: "Split" },
  { id: "schematic", label: "Schematic" },
  { id: "pcb", label: "PCB" },
  { id: "3d", label: "3D" },
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
  const [treeOpen, setTreeOpen] = useState(true);
  const [partsOpen, setPartsOpen] = useState(false);
  const [sidecar, setSidecar] = useState(null);
  const [circuit, setCircuit] = useState(null);
  const [circuitState, setCircuitState] = useState("idle"); // idle | loading | ready | failed
  const [parts, setParts] = useState([]);
  const [activeTab, setActiveTab] = useState("overview");

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
  const [showGrid, setShowGrid] = useState(true);
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
      setTreeOpen(false);
    }
  }, [closeLeftSidebarSignal, lastCloseSignal]);

  // --- the navigator's world: every project, not just the open one.
  const projects = useProjectsStore((state) => state.projects);
  const currentProjectId = useProjectsStore((state) => state.currentProjectId);
  const openProject = useProjectsStore((state) => state.open);
  const turnInProgress = useChatStore((state) => state.turnInProgress);
  const buildStatus = useBuildStatus(currentProjectId || "", turnInProgress);

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

  // parts.json enrichment for Properties, BOM and the cost line — best effort.
  //
  // The catalog deliberately does not list parts.json (catalog.mjs: it is
  // served, not catalogued), so `partsEntry` is null for every project and the
  // whole supply column — Basic/Extended, stock, unit price, the per-board
  // total — was rendering as dashes. The asset route serves anything under the
  // project root with an allowed extension, so we address the file directly and
  // hang the cache-bust off `manifestRevision`, which ticks whenever the
  // project's files change.
  const partsUrl =
    String(partsEntry?.url || "") ||
    (currentProjectId ? `/projects/${currentProjectId}/parts.json?v=${manifestRevision}` : "");
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

  const liveIndex = useMemo(() => (circuit ? buildBoardIndex(circuit) : null), [circuit]);

  // --- build history. Recording is a side effect of looking; `viewing` is
  // non-null only while an OLDER build is on screen, and everything downstream
  // (index, sidecar, schematic sheet) is switched at this one seam so no pane
  // can end up half in the past.
  const revisionsApi = useBoardRevisions({
    projectId: currentProjectId || "",
    file: selectedEntry?.file || "",
    circuitJsonUrl,
    schematicUrl: String(selectedEntry?.artifact?.schematicUrl || ""),
    circuit,
    sidecar,
    index: liveIndex,
  });
  const viewing = revisionsApi.viewing;

  const historicIndex = useMemo(
    () => (viewing?.circuit ? buildBoardIndex(viewing.circuit) : null),
    [viewing],
  );
  const index = historicIndex || (viewing ? null : liveIndex);
  const effectiveSidecar = viewing ? viewing.sidecar : sidecar;

  // Element ids are per-build, so a selection cannot survive a step through
  // history — it would resolve to nothing, or worse, to the wrong pad.
  const viewingToken = viewing?.token || "";
  useEffect(() => {
    setSelection(null);
  }, [viewingToken]);

  // The on-disk `_schematic.svg` was overwritten by the build that followed
  // this one, so a historic sheet is served from its stored copy.
  const [historicSheetUrl, setHistoricSheetUrl] = useState("");
  useEffect(() => {
    const svg = viewing?.schematicSvg || "";
    if (!svg || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
      setHistoricSheetUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
    setHistoricSheetUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [viewing]);

  const partsMap = useMemo(() => partsByLcsc(parts), [parts]);
  const highlight = useMemo(() => (index ? resolveSelection(index, selection) : null), [index, selection]);

  // Findings, twice: once as rows (Messages, cross-probing) and once collapsed
  // into plain-language issues (Overview, the verdict strip). Both read the
  // same sidecar list, so the two panels can never disagree about a count.
  const messageRows = useMemo(
    () => buildMessages(index, normalizeWarnings(effectiveSidecar)),
    [index, effectiveSidecar],
  );
  const findingGroups = useMemo(() => groupFindings(messageRows), [messageRows]);
  const buildLine = useMemo(() => buildStatusLine(buildStatus), [buildStatus]);
  const building = buildLine?.tone === "running";

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
  const boardName = String(effectiveSidecar?.board?.name || "").trim() || selectedStem;
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

  // --- navigator callbacks
  const boardStatusOf = useCallback(
    (entry) => boardStatus(entry, { activity: artifactActivity }),
    [artifactActivity],
  );
  const boardLabelOf = useCallback((entry) => boardStem(entry.file), []);

  /**
   * Open another project, optionally landing on one of its boards. The
   * catalog is scoped to the open project on the backend, so main.jsx re-reads
   * it on the id change; setting `selectedFile` up front means the board is
   * already chosen by the time those entries arrive.
   */
  const handleOpenProject = useCallback(
    (projectId, boardFile = "") => {
      if (!projectId || projectId === currentProjectId) {
        if (boardFile) setSelectedFile(boardFile);
        return;
      }
      if (boardFile) setSelectedFile(boardFile);
      openProject(projectId)
        .then(() => setChatProject(projectId))
        .catch((err) => console.warn("Failed to open project", err));
    },
    [currentProjectId, openProject],
  );

  /** Boards of a project we do NOT have open — read lazily when it is expanded. */
  const readProjectCatalog = useCallback(async (projectId) => {
    const catalog = await transport.project_catalog_read(projectId);
    return selectBoardEntries(catalog);
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

  /**
   * Export the live PCB drawing. SVG rather than a PNG screenshot on purpose:
   * the canvas *is* vector, every pad and trace is already a node, and an SVG
   * drops into a datasheet, an issue, or Illustrator at any zoom. A raster of
   * a vector drawing throws away the thing that made it worth having.
   */
  const handleExportView = useCallback(() => {
    const svg = pcbRef.current?.exportSvg?.();
    if (!svg) return;
    try {
      triggerBlobDownload(new Blob([svg], { type: "image/svg+xml" }), {
        filename: `${selectedStem || "board"}-view.svg`,
      });
    } catch {
      /* a blocked download leaves the button usable */
    }
  }, [selectedStem]);

  /** Everything the floating rail dispatches into — see viewportTools.js. */
  const pcbToolContext = useMemo(
    () => ({
      // A getter, not a snapshot: the canvas publishes its imperative handle
      // after mount and republishes it on every zoom, and a ref change does not
      // re-run this memo. Reading it lazily is what keeps +/- alive.
      get view() {
        return pcbRef.current;
      },
      measuring,
      hudVisible,
      showGrid,
      singleLayerMode,
      highlightMethod,
      maskLevel,
      units,
      onFit: fitAll,
      onToggleMeasure: () => setMeasuring((value) => !value),
      onToggleHud: () => setHudVisible((value) => !value),
      onToggleGrid: () => setShowGrid((value) => !value),
      onCycleSingleLayer: () => setSingleLayerMode(nextSingleLayerMode(singleLayerMode)),
      onCycleHighlightMethod: () => setHighlightMethod(nextHighlightMethod(highlightMethod)),
      onToggleUnits: () => setUnits((value) => (value === "mm" ? "mil" : "mm")),
      onExport: handleExportView,
      onReset: () => {
        setSelection(null);
        setSingleLayerMode("off");
        setHiddenLayers(new Set());
        setMeasuring(false);
        fitAll();
      },
    }),
    [measuring, hudVisible, showGrid, singleLayerMode, highlightMethod, maskLevel, units, fitAll, handleExportView],
  );

  const schematicToolContext = useMemo(
    () => ({
      get view() {
        return schematicRef.current;
      },
      hudVisible,
      onFit: () => schematicRef.current?.fitToSheet?.(),
      onToggleHud: () => setHudVisible((value) => !value),
      onExport: handleExportView,
      onReset: () => {
        setSelection(null);
        schematicRef.current?.fitToSheet?.();
      },
    }),
    [hudVisible, handleExportView],
  );

  const handleBoardSide = useCallback((change) => {
    setActiveLayer(change.activeLayer);
    setSingleLayerMode(change.singleLayerMode);
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
          setActiveTab("3d");
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

  const hoverNetName = useMemo(() => {
    if (!hover?.netKey || !index) return hover?.label || "";
    return index.netByKey.get(hover.netKey)?.name || "";
  }, [hover, index]);

  // The component under the cursor, named the way a person would name it.
  // `hitTestPcb` already resolves a pad or a silkscreen line back to its owner
  // (`componentKey`), so this is a lookup, not a search.
  const hoverPart = useMemo(() => {
    if (!index || !hover?.componentKey) return null;
    return index.componentBySourceId?.get(hover.componentKey) || null;
  }, [hover, index]);

  const schematicPane = (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      <SchematicCanvas
        index={index}
        src={String(artifact.schematicUrl || "")}
        svgSrc={historicSheetUrl}
        scheme={scheme}
        highlight={highlight}
        highlightMethod={highlightMethod}
        maskLevel={maskLevel}
        onSelect={handleSelect}
        onHoverChange={setHover}
        viewRef={schematicRef}
      />
      <ViewportToolRail surface="schematic" context={schematicToolContext} />
    </div>
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
        showGrid={showGrid}
        flash={flash}
        fallbackSrc={String(artifact.pcbUrl || "")}
        onSelect={handleSelect}
        onHoverChange={setHover}
        onViewChange={setPcbView}
        viewRef={pcbRef}
      />
      <ViewportToolRail surface="pcb" context={pcbToolContext} />
      {/* Lifted clear of the rail rather than parked beside it — the same call
          drei's viewcube makes with `margin={[60, 120]}`. In Split the PCB pane
          is half-width, and a rail centred in that half already reaches the
          right edge. */}
      <BoardOrientationCube
        scheme={scheme}
        activeLayer={activeLayer}
        singleLayerMode={singleLayerMode}
        hasBottom={layers.includes("bottom")}
        onChange={handleBoardSide}
        bottomInset={48}
      />
      <BoardInsightHud
        hover={hover}
        activeLayer={activeLayer}
        units={units}
        scale={pcbView.scale}
        netName={hoverNetName}
        partName={hoverPart ? partPlainName(hoverPart) : ""}
        partRefdes={hoverPart?.refdes || ""}
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
        {/* Packet actions live on the app header, not the board tab row: the
            tab row already carries six tabs, the board name, the revision
            pager and the selection chip, and at Split width that row runs out
            before these do. Vibe puts the same cluster at the top of the stage
            for the same reason. */}
        {selectedEntry ? (
          <BoardActions
            className="ml-auto"
            stem={selectedStem}
            artifact={artifact}
            sidecar={effectiveSidecar}
            onOpenTab={setActiveTab}
          />
        ) : null}
        <div className={cn("flex items-center gap-1", selectedEntry ? "" : "ml-auto")}>
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
        <BoardTreeSidebar
          projects={projects}
          currentProjectId={currentProjectId || ""}
          boardEntries={boardEntries}
          selectedFile={selectedEntry?.file || ""}
          index={index}
          selection={selection}
          buildStatus={buildStatus}
          boardStatusOf={boardStatusOf}
          boardLabelOf={boardLabelOf}
          onSelectBoard={setSelectedFile}
          onOpenProject={handleOpenProject}
          onSelect={handleSelect}
          onReadProjectCatalog={readProjectCatalog}
          open={treeOpen}
          onToggleOpen={() => setTreeOpen((value) => !value)}
        />

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

                <RevisionPager
                  className="ml-2"
                  revisions={revisionsApi.revisions}
                  activeIndex={revisionsApi.activeIndex}
                  onSelect={revisionsApi.select}
                />

                {/* Measure and Fit used to live here; they are viewport tools,
                    so they moved into the floating rail on the canvas itself
                    (see ViewportToolRail). What stays is the selection chip —
                    it is state, not a tool. */}
                {CANVAS_TABS.has(activeTab) ? (
                  <div className="ml-3 flex items-center gap-1">
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

              {/* The verdict lives above the panes and outside the tab switch:
                  "can I get this made?" does not stop mattering when you look
                  at the BOM. It is the only place in the workspace that
                  answers it in a sentence. */}
              <BoardVerdict
                sidecar={effectiveSidecar}
                index={index}
                groups={findingGroups}
                building={building}
                buildLine={buildLine}
                boardName={selectedStem}
                onOpenTab={setActiveTab}
                onFix={handlePrefillNote}
              />

              <div className="flex min-h-0 flex-1">
                <div className="flex min-w-0 flex-1 flex-col">
                  {activeTab === "overview" ? (
                    <OverviewTab
                      sidecar={effectiveSidecar}
                      index={index}
                      groups={findingGroups}
                      parts={parts}
                      building={building}
                      buildLine={buildLine}
                      boardName={selectedStem}
                      productUrl={currentProjectId ? `/projects/${currentProjectId}/product.json` : ""}
                      artifact={artifact}
                      onFix={handlePrefillNote}
                      onLocate={handleLocate}
                      onSelect={handleSelect}
                      onOpenTab={setActiveTab}
                      className="min-h-0 flex-1"
                    />
                  ) : null}
                  {activeTab === "split" ? (
                    <div className="flex min-h-0 flex-1">
                      <div className="flex min-w-0 flex-1 flex-col border-r border-border/60">{schematicPane}</div>
                      {pcbPane}
                    </div>
                  ) : null}
                  {activeTab === "schematic" ? schematicPane : null}
                  {activeTab === "pcb" ? pcbPane : null}
                  {activeTab === "3d" ? (
                    <Board3DView
                      glbUrl={String(artifact.glbUrl || "")}
                      stem={selectedStem}
                      scheme={scheme}
                      className="min-h-0 flex-1"
                    />
                  ) : null}
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
                    <FabPacketCard
                      stem={selectedStem}
                      artifact={artifact}
                      sidecar={effectiveSidecar}
                      groups={findingGroups}
                      onOpenTab={setActiveTab}
                      className="min-h-0 flex-1"
                    />
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

                  {/* Overview carries the same findings, already grouped and
                      in plain words — two lists of the same thing on one
                      screen is how a panel becomes furniture. */}
                  {activeTab === "overview" ? null : (
                  <MessagesPanel
                    rows={messageRows}
                    groups={findingGroups}
                    boardName={selectedStem}
                    index={index}
                    sidecar={effectiveSidecar}
                    selection={selection}
                    onSelect={handleSelect}
                    onLocate={handleLocate}
                    onPrefillNote={handlePrefillNote}
                    open={messagesOpen}
                    onToggleOpen={() => setMessagesOpen((value) => !value)}
                  />
                  )}
                </div>

                {/* Properties is an inspector for the drawing panes. On
                    Overview it duplicates the same numbers in EDA words and
                    steals a third of the width from the thing that is trying
                    to explain them. */}
                {activeTab === "overview" ? null : (
                  <PropertiesPanel
                    index={index}
                    sidecar={effectiveSidecar}
                    selection={selection}
                    partsByLcscMap={partsMap}
                    units={units}
                    onSelect={handleSelect}
                  />
                )}
              </div>
            </>
          ) : (
            catalogHydrated || catalogError ? (
              <StartHere status={buildStatus} building={building || turnInProgress} className="min-h-0 flex-1" />
            ) : (
              <div
                data-slot="board-empty-state"
                className="grid min-h-0 flex-1 place-items-center"
                style={{ backgroundColor: "var(--ui-viewer-bg)" }}
              >
                <Loader2 className="size-5 animate-spin text-white/60" aria-hidden />
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
