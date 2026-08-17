import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Keyboard as KeyboardIcon, Loader2, SendHorizontal } from "lucide-react";
import { cn } from "@/ui/utils";
import ProjectMenu from "@/components/project/ProjectMenu.jsx";
import SidebarUserCard from "@/components/workbench/SidebarUserCard.jsx";
import { setPendingViewContext, setProject as setChatProject, startTurn } from "@/store/chat.js";
import {
  FOCUS_CHAT_INPUT_EVENT,
  prefillChatInput,
} from "@/components/chat/chatInputHelpers.js";
import { boardStatus, boardStem, selectBoardEntries } from "@/lib/boardModel.js";
import { buildBoardIndex, resolveSelection } from "@/lib/boardIndex.js";
import { boardRegions } from "@/lib/boardRegions.js";
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
import { dispatchViewportTool } from "./viewportTools.js";
import useBoardRevisions from "./useBoardRevisions.js";
import useBuildHistory from "./useBuildHistory.js";
import useBuildStatus from "./useBuildStatus.js";
import BoardVerdict from "./BoardVerdict.jsx";
import BomTable from "./BomTable.jsx";
import FabPacketCard from "./FabPacketCard.jsx";
import FunctionTab from "./FunctionTab.jsx";
import OverviewTab from "./OverviewTab.jsx";
import PartsPanel from "./PartsPanel.jsx";
import PcbCanvas from "./PcbCanvas.jsx";
import PlacementEditBar from "./PlacementEditBar.jsx";
import usePlacementEditor from "./usePlacementEditor.js";
import useNetWidths from "./useNetWidths.js";
import { isTypingTarget, resolveBoardKey } from "./boardKeymap.js";
import BoardContextMenu from "./BoardContextMenu.jsx";
import { OPEN_SHORTCUT_SHEET_EVENT, ShortcutSheetHost } from "./ShortcutSheet.jsx";
import { DEFAULT_ROTATION_STEP, commitRotateStep, rotateRefusal } from "./placementRotate.js";
import { describeMove } from "./boardSource.js";
import SchematicCanvas from "./SchematicCanvas.jsx";
import StartHere from "./StartHere.jsx";
import LayerBar from "./LayerBar.jsx";
import BoardInsightHud from "./BoardInsightHud.jsx";
import MessagesPanel from "./MessagesPanel.jsx";
import PropertiesPanel from "./PropertiesPanel.jsx";
import { buildHistoryLine, buildStatusLine } from "./buildStatus.js";
import {
  buildViewContextNote,
  normalizeParts,
  normalizeWarnings,
  partsByLcsc,
  sanitizeProduct,
} from "./boardData.js";

const TABS = Object.freeze([
  // Overview is first and default on purpose: someone who has never opened an
  // EDA tool lands on Split otherwise and sees two drawings with no way in.
  // An engineer is one click (or `0`) from the split view they want.
  { id: "overview", label: "Overview" },
  // Second, because "can I get it made?" and "does it do what I asked?" are
  // the two questions a non-engineer has, in that order, and until now only
  // the first one had an answer anywhere in this app.
  { id: "function", label: "What it does", hint: "Each part, and what it is there for" },
  { id: "split", label: "Side by side", hint: "The wiring diagram and the board, together" },
  { id: "schematic", label: "Schematic", hint: "The wiring diagram — what connects to what" },
  { id: "pcb", label: "PCB", hint: "The board itself, seen from above" },
  { id: "3d", label: "3D", hint: "What the finished board will look like" },
  // "BOM" and "Fab" are the words an engineer uses and the two words a
  // first-timer is most likely to skip past without knowing they matter. The
  // plain label is the tab; the trade term is in the tooltip for anyone who
  // came here from Altium or KiCad looking for it.
  { id: "bom", label: "Parts", hint: "Every part on the board, with what it costs (the BOM)" },
  { id: "fab", label: "Files", hint: "The files a factory needs to build it (the fab packet)" },
]);

const CANVAS_TABS = new Set(["split", "schematic", "pcb"]);
// Tabs that explain the board in words. They carry their own findings list and
// their own inspector, so the EDA panels along the bottom and the right would
// be a second copy of the same numbers in the vocabulary the tab is avoiding.
const PLAIN_TABS = new Set(["overview", "function"]);

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
  // True only while something is genuinely on its way to this pane. Owned by
  // main.jsx because it needs the projects store; see stageState.js for the
  // fresh-install spinner it exists to stop.
  stagePending = false,
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
  const [product, setProduct] = useState(null);
  const [activeTab, setActiveTab] = useState("overview");
  const [regionsVisible, setRegionsVisible] = useState(true);

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
  // --- move mode: the canvas as an editor of the board source
  const [editing, setEditing] = useState(false);
  const [snapStep, setSnapStep] = useState(0.5);
  // Lifted out of PlacementEditBar so the strip and the Space key turn a part
  // by the same amount. Two owners of one step is how a dropdown and a
  // keystroke end up meaning different things by the same gesture.
  const [rotationStep, setRotationStep] = useState(DEFAULT_ROTATION_STEP);
  // The right-click menu: what was under the press, and whether it is up.
  // Frozen at the release (canvasPointer.pointerReleaseAction) — the hit is
  // resolved once so a header cannot disagree with the row under it.
  const [contextRequest, setContextRequest] = useState(null);
  const [rebuilding, setRebuilding] = useState(false);
  // Which placement the edit bar acts on. Separate from `selection` because a
  // mounting hole and a silkscreen label are placements with no component and
  // no net — nothing `selection` can hold.
  const [activePlacementId, setActivePlacementId] = useState("");

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
  const chatHistory = useChatStore((state) => state.history);
  const buildStatus = useBuildStatus(currentProjectId || "", turnInProgress);
  const buildHistory = useBuildHistory(currentProjectId || "", turnInProgress);

  // The first thing the user typed for this project, verbatim. It is the only
  // record of the request in their own words, and quoting it is honest in a
  // way that paraphrasing it would not be. Absent for a project that was
  // imported rather than chatted into existence — the tab copes.
  // When the live turn started. The pipeline reports its own elapsed time only
  // once it exists, and the minutes before that are the ones that look hung.
  const runningTurnStartedAt = useMemo(() => {
    const list = Array.isArray(chatHistory) ? chatHistory : [];
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const turn = list[i];
      if (turn.role === "assistant" && turn.status === "running" && Number(turn.startedAt)) {
        return Number(turn.startedAt);
      }
    }
    return 0;
  }, [chatHistory]);

  // The plan the user approved, verbatim. Quoting it is not a derivation and
  // is not presented as one — it is the other half of the comparison this tab
  // exists for: here is what you agreed to, here is what the netlist says got
  // built. The plan is never written to a file, so the chat history is the
  // only place it survives.
  const approvedPlan = useMemo(() => {
    const list = Array.isArray(chatHistory) ? chatHistory : [];
    for (let i = list.length - 1; i >= 0; i -= 1) {
      for (const block of list[i]?.blocks || []) {
        if (block.kind === "plan" && block.status === "approved" && String(block.plan || "").trim()) {
          return String(block.plan).trim();
        }
      }
    }
    return "";
  }, [chatHistory]);

  const requestText = useMemo(() => {
    const first = (Array.isArray(chatHistory) ? chatHistory : []).find(
      (turn) => turn.role === "user" && String(turn.userText || "").trim(),
    );
    return String(first?.userText || "").trim();
  }, [chatHistory]);

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

  // product.json — the recorded brief. Two tabs read it, so it is fetched once
  // here rather than twice in the tabs that want it.
  const productUrl = currentProjectId ? `/projects/${currentProjectId}/product.json?v=${manifestRevision}` : "";
  useEffect(() => {
    if (!productUrl) {
      setProduct(null);
      return undefined;
    }
    let cancelled = false;
    fetch(productUrl)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        // Sanitized here, once, rather than in each tab that reads it: a fresh
        // project ships the skeleton's instructions-to-the-model in these
        // fields, and rendering those as the answer is worse than silence.
        if (!cancelled) setProduct(sanitizeProduct(data));
      })
      .catch(() => {
        if (!cancelled) setProduct(null);
      });
    return () => {
      cancelled = true;
    };
  }, [productUrl]);

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

  // Named areas of the board — one per golden block the composition used, plus
  // one for whatever the board file wired itself. Drawn on the PCB canvas and
  // listed on the What-it-does tab, from the same derivation.
  const regions = useMemo(() => boardRegions(index), [index]);

  // `turnActive` is what separates "quiet" from "dead": the pipeline reports
  // only between stages, so a long compile looks stale while the agent is
  // plainly still working.
  const buildLine = useMemo(
    () => buildStatusLine(buildStatus, { turnActive: turnInProgress, hasBoard: Boolean(metadataUrl) }),
    [buildStatus, turnInProgress, metadataUrl],
  );
  // Where the board came from. Null unless there is something honest to say —
  // one recorded round is not a trend.
  const historyLine = useMemo(() => buildHistoryLine(buildHistory), [buildHistory]);
  const building = buildLine?.tone === "running" || buildLine?.tone === "quiet";
  // The live turn's phase. Only an `implement` turn ends in a board, so it is
  // the only one the stage checklist may claim to be watching.
  const activePhase = useMemo(() => {
    for (let i = chatHistory.length - 1; i >= 0; i -= 1) {
      if (chatHistory[i]?.role === "assistant") return String(chatHistory[i].phase || "");
    }
    return "";
  }, [chatHistory]);

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

  // --- the board source, behind the canvas.
  //
  // Never while an older build is on screen: those coordinates describe a file
  // that has since been rewritten, and a drag would move the wrong element by
  // exactly the amount the board has changed since.
  const canEdit = editing && !viewing;
  // What a net can be routed at, measured on demand. Keyed to the build,
  // because a ceiling is a property of the placement.
  const netWidths = useNetWidths({
    projectId: currentProjectId || "",
    stem: selectedStem,
    buildKey: circuitJsonUrl,
    enabled: !viewing,
  });
  const editor = usePlacementEditor({
    projectId: currentProjectId || "",
    stem: selectedStem,
    index,
    buildKey: circuitJsonUrl,
    enabled: canEdit,
    revision: manifestRevision,
  });
  useEffect(() => {
    if (viewing) setEditing(false);
  }, [viewing]);
  useEffect(() => {
    if (!canEdit) setActivePlacementId("");
  }, [canEdit, selectedFile]);

  /** A drop: write the new coordinates, then say what changed. */
  const handlePlacementMove = useCallback(
    (placement, next) => {
      editor.move(
        placement.id,
        next.x,
        next.y,
        describeMove(placement.label, { x: placement.x, y: placement.y }, next),
      );
      // Light the part up in the other panes too, but only when there is a
      // part — a mounting hole has no component to cross-probe to.
      if (placement.componentKeys.length) {
        setSelection({ kind: "component", key: placement.componentKeys[0] });
      }
    },
    [editor],
  );

  /**
   * A turn: Space and Shift+Space while dragging, and the strip's two buttons,
   * through ONE command.
   *
   * `commitRotateStep` (placementRotate.js) is the only producer of a rotate in
   * this app and `editor.rotate` the only consumer, so the keyboard and the
   * panel cannot come to mean slightly different things by the same gesture —
   * which is the seam this workspace exists to close. `rotateRefusal` is the
   * only wording for a "no", so a locked part is explained the same way
   * wherever the user asks.
   *
   * @returns {string} the reason nothing happened, or "" when it did. The
   *   canvas prints it in the drag readout: a gesture that vanishes is how a
   *   user learns the app loses their input.
   */
  const handlePlacementRotate = useCallback(
    (placement, { direction }) => {
      const refusal = rotateRefusal(placement);
      if (refusal) return refusal.reason;
      const command = commitRotateStep(placement, direction, rotationStep);
      // Null means the angle would not change — a turn onto the angle already
      // written is not an edit, the same way a drag that snaps back is a click.
      if (!command) return "";
      // A wrap is four lines of new structure and gets a confirmation, which
      // the strip owns; the key press says where to find it rather than
      // writing structure nobody approved.
      if (command.confirm) return `${placement.label} needs a wrapper to turn — use the ↺ button to see the change first.`;
      // By step, not to an angle: Space is a key people hold down and tap in
      // fours, and an absolute target computed from the angle on screen is
      // stale the moment a second tap lands before the first write returns.
      editor.turnBy(command.placementId, direction, rotationStep);
      return "";
    },
    [editor, rotationStep],
  );

  /**
   * Ask for a rebuild. A build is minutes, so it is never a side effect of a
   * drag — it is this button, and it goes through the same chat turn every
   * other change to this board goes through. The instruction names the file
   * and the lock convention so the agent does not helpfully re-place what a
   * person just placed.
   */
  const handleRebuild = useCallback(async () => {
    if (!selectedStem || rebuilding) return;
    setRebuilding(true);
    try {
      const changed = editor.changes;
      await startTurn(
        [
          `I moved ${changed === 1 ? "a part" : "parts"} by hand on the PCB canvas, so`,
          `boards/${selectedStem}.tsx now has ${changed === 1 ? "a new pcbX/pcbY pair" : "new pcbX/pcbY values"}.`,
          "Rebuild the board from the file exactly as it stands.",
          "Keep every placement I set — do not re-place parts to make routing easier,",
          "and never move a placement that carries a `locked:` comment above it.",
          "If a placement I chose makes the board unroutable, say so and show me the evidence",
          "rather than moving it back.",
        ].join(" "),
      );
      editor.markBuilding();
    } finally {
      setRebuilding(false);
    }
  }, [editor, rebuilding, selectedStem]);

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
      if (!CANVAS_TABS.has(activeTab)) setActiveTab("split");
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
   * Zoom whichever pane is on screen, through the rail's own dispatcher so the
   * key and the button take the same step.
   *
   * Nothing happens on the 3D tab: its camera is not this viewport, and a key
   * that silently moves a pane nobody is looking at is the misfire class the
   * keymap exists to avoid.
   */
  const zoomActivePane = useCallback(
    (toolId) => {
      const surface =
        activeTab === "schematic" ? schematicRef.current : activeTab === "3d" ? null : pcbRef.current;
      dispatchViewportTool(toolId, { view: surface });
    },
    [activeTab],
  );

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
      editing: canEdit,
      hudVisible,
      showGrid,
      showRegions: regionsVisible,
      singleLayerMode,
      highlightMethod,
      maskLevel,
      units,
      onFit: fitAll,
      // Move mode and measure mode both own the drag, so one turns the other
      // off rather than the two fighting over a pointerdown.
      onToggleEditing: () => {
        if (viewing) return;
        setEditing((value) => {
          if (!value) setMeasuring(false);
          return !value;
        });
      },
      onToggleMeasure: () =>
        setMeasuring((value) => {
          if (!value) setEditing(false);
          return !value;
        }),
      onToggleHud: () => setHudVisible((value) => !value),
      onToggleGrid: () => setShowGrid((value) => !value),
      onToggleRegions: () => setRegionsVisible((value) => !value),
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
    [
      measuring,
      canEdit,
      viewing,
      hudVisible,
      showGrid,
      regionsVisible,
      singleLayerMode,
      highlightMethod,
      maskLevel,
      units,
      fitAll,
      handleExportView,
    ],
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

  // Undo, or null when there is nothing this key press could honestly do.
  //
  // Three conditions, not one. The history survives leaving move mode but the
  // placement binding does not (`enabled: canEdit` at the hook), so an undo
  // offered outside move mode resolves no placement and fails into an error
  // strip that is not even on screen. Held in a ref rather than the effect's
  // deps because `editor` is a fresh object every render and the listener
  // would re-subscribe on each one.
  //
  // `busy` is the fourth: a write is in flight, and a second undo computed
  // against source the server has not returned yet lands as a SOURCE_CHANGED
  // refusal (`http.mjs` planSourceWrite). The Undo *button* has always been
  // guarded this way (`PlacementEditBar.jsx` `disabled={!canUndo || busy}`);
  // the key duplicates the button, so it duplicates the guard.
  const undoRef = useRef(null);
  undoRef.current = canEdit && editor.ready && editor.canUndo && !editor.busy ? editor.undo : null;
  const redoRef = useRef(null);
  // The nudge, set below once the selected placement is known. A ref for the
  // same reason undo and redo are: the key effect must not re-subscribe on
  // every selection change, and the placement is computed far below it.
  const nudgeRef = useRef(null);
  redoRef.current = canEdit && editor.ready && editor.canRedo && !editor.busy ? editor.redo : null;

  // --- keyboard, honouring Altium's bindings where a browser lets us.
  //
  // The arbiter is `boardKeymap.js` — pure, and tested over the whole key
  // space, because the two bugs this replaced were both resolution bugs rather
  // than dispatch bugs: `L` reaching the wrong surface, and Ctrl+Z reaching
  // nothing at all. This effect is now only the dispatch.
  useEffect(() => {
    const onKey = (event) => {
      const command = resolveBoardKey(event, {
        typing: isTypingTarget(event.target),
        canUndo: Boolean(undoRef.current),
        canRedo: Boolean(redoRef.current),
        canNudge: Boolean(nudgeRef.current),
      });
      if (!command) return;
      // Only the modified bindings are worth taking off the browser: ⌘M
      // minimizes a window and ⌘Z runs the webview's own text undo. The plain
      // letters collide with nothing.
      if (event.metaKey || event.ctrlKey) event.preventDefault();
      switch (command) {
        case "edit.undo":
          undoRef.current?.();
          break;
        case "edit.redo":
          redoRef.current?.();
          break;
        case "measure.toggle":
          // Measure and move both own the drag; whichever is asked for last wins.
          setMeasuring((value) => {
            if (!value) setEditing(false);
            return !value;
          });
          break;
        case "view.fit":
          // Board3DView owns `F` on its own tab (`3d.home`, back to the
          // starting camera). Both listeners are on `window`, so without this
          // one key ran two handlers and the shortcut sheet printed two rows
          // for `F` that read as a contradiction. The 3D camera reset IS the
          // fit on that tab; fitting the 2D panes nobody is looking at is the
          // half that does nothing visible.
          if (activeTab !== "3d") fitAll();
          break;
        // Zoom the pane the user is looking at, through the same step the
        // rail's own buttons take (`viewportTools.ZOOM_STEP`) so the key and
        // the button cannot drift apart. Nothing happens on the 3D tab: its
        // camera is not this viewport, and a key that silently moves an
        // invisible pane is the misfire the keymap exists to avoid.
        case "view.zoom-in":
          zoomActivePane("zoom-in");
          break;
        case "view.zoom-out":
          zoomActivePane("zoom-out");
          break;
        case "selection.clear":
          setSelection(null);
          setMeasuring(false);
          break;
        case "filter.clear":
          setSelection(null);
          break;
        case "single-layer.cycle":
          setSingleLayerMode(nextSingleLayerMode(singleLayerMode));
          break;
        case "hud.toggle":
          setHudVisible((value) => !value);
          break;
        case "messages.toggle":
          setMessagesOpen((value) => !value);
          break;
        case "tab.schematic":
          setActiveTab("schematic");
          break;
        case "tab.pcb":
          setActiveTab("pcb");
          break;
        case "tab.3d":
          setActiveTab("3d");
          break;
        case "tab.split":
          setActiveTab("split");
          break;
        case "edit-mode.toggle":
          if (!viewing) {
            // Move mode only exists on the board, and everything that says it
            // is on — the amber strip, the lit tool, the move cursor — is on
            // the board too. Turning it on from the BOM or the schematic
            // changed a state nobody could see, which from the outside is a
            // key that does nothing. `E` means "I want to move parts", so show
            // the parts. Decided here rather than inside the updater: a
            // `setState` call in another updater's body is a side effect in a
            // function React may run twice.
            if (!editing && (!CANVAS_TABS.has(activeTab) || activeTab === "schematic")) {
              setActiveTab("pcb");
            }
            setEditing((value) => {
              if (!value) setMeasuring(false);
              return !value;
            });
          }
          break;
        // One snap step per press, the step the strip is set to, in the
        // direction the arrow points — board coordinates, so up is +y even
        // though the screen counts down. Every press is its own write and its
        // own undo entry: ten taps back is ten taps forward.
        case "nudge.left":
          nudgeRef.current?.(-snapStep, 0);
          break;
        case "nudge.right":
          nudgeRef.current?.(snapStep, 0);
          break;
        case "nudge.up":
          nudgeRef.current?.(0, snapStep);
          break;
        case "nudge.down":
          nudgeRef.current?.(0, -snapStep);
          break;
        case "units.toggle":
          setUnits((value) => (value === "mm" ? "mil" : "mm"));
          break;
        case "highlight.cycle":
          setHighlightMethod((value) => nextHighlightMethod(value));
          break;
        case "mask.decrease":
          setMaskLevel((value) => Math.max(0, value - 1));
          break;
        case "mask.increase":
          setMaskLevel((value) => Math.min(5, value + 1));
          break;
        // Altium's `L` opens the Layers And Colors panel. Ours is not a panel:
        // the layer chips live permanently on the bar under the canvas, so the
        // key puts that bar on screen and then puts the keyboard ON it. The
        // second half matters — with the bar already visible the key otherwise
        // looked broken, and moving focus is what "opens the layers" means for
        // someone who is not reaching for the mouse.
        //
        // The richer popover from ide-altium-parity.md ("Layers And Colors:
        // what opens, and what is in it") is still LayerBar's file, not this
        // one. This is the honest version until it lands.
        case "layers.show":
          if (!(CANVAS_TABS.has(activeTab) && activeTab !== "schematic")) setActiveTab("pcb");
          // After the tab has painted, not during this handler.
          requestAnimationFrame(() => {
            const bar = document.querySelector('[data-slot="layer-bar"]');
            bar?.querySelector("button")?.focus();
          });
          break;
        case "regions.toggle":
          setRegionsVisible((value) => !value);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fitAll, singleLayerMode, viewing, activeTab, editing, zoomActivePane, snapStep]);

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

  // The named area the hovered part lives in — same derivation the rooms are
  // drawn from, so the HUD and the overlay can never disagree.
  const hoverArea = useMemo(() => {
    if (!hoverPart) return "";
    const region = regions.find((entry) => entry.componentKeys.includes(hoverPart.key));
    return region ? region.label : "";
  }, [hoverPart, regions]);

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
        onContextMenuRequest={setContextRequest}
        viewRef={schematicRef}
      />
      <ViewportToolRail surface="schematic" context={schematicToolContext} />
    </div>
  );

  // The placement the edit bar acts on: whatever was last clicked or dragged
  // in move mode, falling back to whatever the current selection resolves to.
  const selectedPlacement = useMemo(() => {
    if (!canEdit) return null;
    const byClick = activePlacementId ? editor.placements.byId.get(activePlacementId) : null;
    if (byClick) return byClick;
    if (selection?.kind !== "component") return null;
    const id = editor.placements.byComponentKey.get(selection.key);
    return id ? editor.placements.byId.get(id) || null : null;
  }, [canEdit, activePlacementId, selection, editor.placements]);

  // What Ctrl+arrow does, or null when there is nothing for it to do — which is
  // also what tells the arbiter to leave the key to the browser rather than
  // eat it. Locked is excluded on purpose: `editor.move` writes whatever it is
  // given, and the canvas refuses a locked part before the drag starts, so the
  // keyboard has to refuse it here or the lock would hold for the mouse and not
  // for the arrows.
  // The delta, never an absolute target: a held key repeats faster than a round
  // trip, and a target computed from the position on screen is stale for every
  // repeat that lands mid-flight — which silently ate keystrokes until round 3
  // caught it. `editor.nudgeBy` applies the delta inside the edit queue, to
  // whatever the file says by then.
  nudgeRef.current =
    canEdit && editor.ready && selectedPlacement && !selectedPlacement.locked
      ? (dx, dy) => editor.nudgeBy(selectedPlacement.id, dx, dy)
      : null;

  const pcbPane = (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      {canEdit ? (
        <PlacementEditBar
          editor={editor}
          placement={selectedPlacement}
          snapStep={snapStep}
          onSnapStep={setSnapStep}
          rotationStep={rotationStep}
          onRotationStep={setRotationStep}
          onRebuild={handleRebuild}
          rebuilding={rebuilding}
          canRebuild={!turnInProgress}
          onClose={() => setEditing(false)}
        />
      ) : null}
      {/* The overlays (HUD, rail, cube) are positioned against the drawing,
          not against the pane — otherwise the edit bar pushes the canvas down
          and the coordinate readout stays behind, printed over the bar. */}
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
          regions={regions}
          showRegions={regionsVisible}
          flash={flash}
          fallbackSrc={String(artifact.pcbUrl || "")}
          editing={canEdit && editor.ready}
          placements={editor.placements}
          snapStepMm={snapStep}
          onPlacementMove={handlePlacementMove}
          onPlacementRotate={handlePlacementRotate}
          onPlacementSelect={(placement) => setActivePlacementId(placement?.id || "")}
          onSelect={handleSelect}
          onHoverChange={setHover}
          onContextMenuRequest={setContextRequest}
          onExitMeasure={() => setMeasuring(false)}
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
          partArea={hoverArea}
          visible={hudVisible}
          measuring={measuring}
          // The Δ readout is the only way to zero the delta origin on a
          // MacBook — Space was handed to rotation and Insert is not on the
          // keyboard. Without this the Δ column reads 0,0 forever.
          onResetDelta={() => pcbRef.current?.resetDelta?.({ x: 0, y: 0 })}
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
          buildLine={buildLine}
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
              <div className="scrollbar-none flex h-9 shrink-0 items-center gap-1 overflow-x-auto border-b border-border/60 px-2">
                {TABS.map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    data-slot="board-tab"
                    data-tab={tab.id}
                    title={tab.hint || undefined}
                    aria-current={activeTab === tab.id ? "true" : undefined}
                    className={cn(
                      "shrink-0 whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
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

                {/* The way in to the key list, on the board itself.
                    Altium carries ~100 bindings without a learning curve
                    because the tool will tell you what they are; ours was
                    derived from the resolvers, listed under `?` and Shift+F1 —
                    and reachable only by guessing one of those two keys, since
                    the Help menu that also opens it renders on Windows only. A
                    list nobody can find is a list nobody has.

                    An event, not a second `ShortcutSheetHost`: the host owns a
                    window `keydown` listener, so mounting two would answer `?`
                    twice and put two dialogs on screen. */}
                <button
                  type="button"
                  onClick={() => window.dispatchEvent(new Event(OPEN_SHORTCUT_SHEET_EVENT))}
                  data-slot="board-shortcuts"
                  title="Keyboard shortcuts (? or Shift+F1)"
                  aria-label="Keyboard shortcuts"
                  className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <KeyboardIcon className="size-3.5" aria-hidden />
                  <kbd className="font-mono text-[10px] opacity-70">?</kbd>
                </button>

                <button
                  type="button"
                  onClick={handleSendToAI}
                  title="Send this view to the chat"
                  data-slot="board-send-to-ai"
                  className="ml-1 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
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
                buildStatus={buildStatus}
                turnActive={turnInProgress}
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
                      buildStatus={buildStatus}
                      turnActive={turnInProgress}
                      historyLine={historyLine}
                      boardName={selectedStem}
                      product={product}
                      artifact={artifact}
                      onFix={handlePrefillNote}
                      onLocate={handleLocate}
                      onSelect={handleSelect}
                      onOpenTab={setActiveTab}
                      className="min-h-0 flex-1"
                    />
                  ) : null}
                  {activeTab === "function" ? (
                    <FunctionTab
                      index={index}
                      product={product}
                      regions={regions}
                      requestText={requestText}
                      planText={approvedPlan}
                      boardName={selectedStem}
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
                  {PLAIN_TABS.has(activeTab) ? null : (
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

                {/* Properties is an inspector for the drawing panes. On the
                    plain-language tabs it duplicates the same numbers in EDA
                    words and steals a third of the width from the thing that
                    is trying to explain them. */}
                {PLAIN_TABS.has(activeTab) ? null : (
                  <PropertiesPanel
                    index={index}
                    sidecar={effectiveSidecar}
                    selection={selection}
                    partsByLcscMap={partsMap}
                    units={units}
                    onSelect={handleSelect}
                    // The typing half of the same edit the canvas drags. Both
                    // land in handlePlacementMove, so there is one write path.
                    editor={editor}
                    canEdit={canEdit}
                    viewing={Boolean(viewing)}
                    activePlacementId={activePlacementId}
                    onPlacementMove={handlePlacementMove}
                    onPlacementRotate={handlePlacementRotate}
                    netWidths={netWidths}
                  />
                )}
              </div>
            </>
          ) : (
            !stagePending ? (
              <StartHere
              status={buildStatus}
              building={building || turnInProgress}
              buildLine={buildLine}
              phase={activePhase}
              startedAt={runningTurnStartedAt}
              className="min-h-0 flex-1"
            />
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

      {/* Right-click, decided on release by the canvas and rendered here.
          It sits at the workspace root rather than inside a pane because both
          canvases feed it and it is portalled to the body either way. */}
      <BoardContextMenu
        open={Boolean(contextRequest)}
        onOpenChange={(next) => {
          if (!next) setContextRequest(null);
        }}
        request={contextRequest}
        index={index}
        placements={editor.placements}
        editor={editor}
        messageRows={messageRows}
        selection={selection}
        canEdit={canEdit}
        viewing={Boolean(viewing)}
        showGrid={showGrid}
        units={units}
        boardName={boardName}
        onSelect={handleSelect}
        onJump={handleSelect}
        onLocate={handleLocate}
        onZoomBox={(box) => pcbRef.current?.zoomToBox?.(box)}
        onLock={(placementId, locked) => editor.setLock(placementId, locked)}
        onMoveExact={(next) => {
          const placement = editor.placements.byId.get(next.placementId);
          if (placement) handlePlacementMove(placement, next);
        }}
        onPrefill={handlePrefillNote}
        onFit={fitAll}
        onClearSelection={() => setSelection(null)}
        onToggleGrid={() => setShowGrid((value) => !value)}
        onToggleUnits={() => setUnits((value) => (value === "mm" ? "mil" : "mm"))}
        onToggleEdit={(on) => {
          if (viewing) return;
          if (on) setMeasuring(false);
          setEditing(Boolean(on));
        }}
      />

      {/* Altium's Shift+F1 — "a menu listing all valid shortcuts". Ours is
          derived from the resolvers rather than written by hand, so it cannot
          drift from the keys that actually work.

          This mount owns the key and the dialog for the whole workspace,
          including the states where the tab strip is not on screen (no board
          selected, still building). The visible button lives up in the tab
          strip beside "Send to AI"; the Help menu in the window menu bar
          reaches the same dialog by event — but that bar only renders on
          Windows, which is why a board-level button had to exist at all. */}
      <ShortcutSheetHost button={false} />
    </div>
  );
}
