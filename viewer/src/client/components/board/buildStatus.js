// buildStatus — pure presentation logic for the pipeline's live build stage.
// The transport call and the React hook live in useBuildStatus.js; everything
// here is DOM-free and fetch-free so node:test covers it directly.
//
// The record comes from `POST /api/build_status {id}` and is written by the
// pipeline once per stage transition (7 per build, no heartbeat). A board
// build runs 45–90s, which is exactly long enough that a bare spinner reads as
// "possibly hung" — the stage label is the difference.

/**
 * The pipeline's seven stages, mirrored client-side so the wait can be shown
 * as a checklist instead of a spinner. Keys and labels are verbatim from
 * `circuitpy/status.py` STAGES; the `plain` line is ours, and says what the
 * stage is doing in words that mean something to someone who has never run a
 * DRC. If the pipeline adds a stage, an unknown key still renders — the list
 * is a hint about what is coming, and the live record is the authority for
 * where we are.
 */
export const BUILD_STAGES = Object.freeze([
  // Not one of the pipeline's stages — it is what happens BEFORE the pipeline
  // exists to report anything. The model spends most of the wall clock here
  // choosing parts and writing the board program, and without a row for it the
  // checklist sits entirely grey for a minute and reads as a hang, which is the
  // exact failure the checklist was built to prevent.
  {
    key: "design",
    label: "Choosing parts and writing the board",
    plain: "Picking real, orderable parts and turning your description into a board program.",
    prelude: true,
  },
  { key: "compile", label: "Compiling the board", plain: "Turning the description into a real netlist and layout." },
  { key: "scan", label: "Reading the compiler's findings", plain: "Collecting everything the compiler complained about." },
  { key: "checks", label: "Running the independent checks", plain: "A second opinion on the copper, separate from the compiler." },
  { key: "substrate", label: "Cross-checking with KiCad", plain: "The industry tool re-runs the electrical and spacing rules." },
  { key: "dfm", label: "Checking it can be manufactured", plain: "Against the factory's own limits — track widths, holes, edges." },
  { key: "export", label: "Writing the fab packet", plain: "Gerbers, the parts list and the placement file." },
  { key: "render", label: "Drawing the schematic and board", plain: "The pictures you are about to look at." },
]);

/**
 * The stage list with each entry marked `done` / `active` / `pending` against
 * a live status record. Position comes from the stage KEY when we recognise
 * it and from `stageIndex` otherwise, so an unknown stage still advances the
 * list rather than freezing it at the top.
 */
export function buildStageChecklist(status) {
  const key = String(status?.stage || "");
  const known = BUILD_STAGES.findIndex((stage) => stage.key === key);
  const reported = Number(status?.stageIndex);
  // The prelude occupies index 0, so a pipeline stage reported as index N sits
  // at N in this list rather than N-1.
  const current =
    known >= 0 ? known : Number.isFinite(reported) && reported > 0 ? Math.min(reported, BUILD_STAGES.length - 1) : 0;
  const finished = String(status?.state || "") === "done";
  return BUILD_STAGES.map((stage, i) => ({
    ...stage,
    state: finished || i < current ? "done" : i === current ? "active" : "pending",
  }));
}

/** States that mean nothing more is coming; polling stops on any of them. */
export const TERMINAL_STATES = Object.freeze(new Set(["done", "failed", "stale"]));

/** True while the record describes work still in flight. */
export function isRunning(status) {
  return String(status?.state || "") === "running";
}

/** True once the record can no longer change on its own. */
export function isTerminal(status) {
  return TERMINAL_STATES.has(String(status?.state || ""));
}

/**
 * Fraction complete, 0–1, from `stageIndex` / `stageCount`. A finished build
 * reads 1 regardless of where its index stopped; a malformed record reads 0
 * rather than throwing a NaN width into a style attribute.
 */
export function buildProgress(status) {
  if (!status) return 0;
  if (status.state === "done") return 1;
  const count = Number(status.stageCount);
  const index = Number(status.stageIndex);
  if (!Number.isFinite(count) || count <= 0 || !Number.isFinite(index) || index < 0) return 0;
  return Math.min(1, index / count);
}

/** "3m 05s" / "48s" — compact enough for a one-line strip. */
export function formatElapsed(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  return `${minutes}m ${String(total % 60).padStart(2, "0")}s`;
}

/**
 * The single line the tree renders under the active board, or null when there
 * is nothing worth saying.
 *
 * Deliberately quiet on `done`: a green "Built" line that never leaves becomes
 * furniture, so a finished build only speaks while it is fresh (the caller
 * passes `now`; `doneWindowMs` is how long it lingers). `failed` and `stale`
 * never expire — an unexplained stop is exactly the thing a user needs to see.
 *
 * @returns {{tone: "running"|"done"|"failed"|"stale", text: string, detail: string, progress: number}|null}
 */
export function buildStatusLine(status, { now = Date.now(), doneWindowMs = 20_000 } = {}) {
  if (!status || !status.state) return null;
  const progress = buildProgress(status);
  const stage = String(status.stageLabel || status.stage || "").trim();
  const index = Number(status.stageIndex);
  const count = Number(status.stageCount);
  const steps = Number.isFinite(index) && Number.isFinite(count) && count > 0 ? `${index}/${count}` : "";

  if (status.state === "running") {
    return {
      tone: "running",
      text: stage || "Building",
      detail: steps,
      progress,
    };
  }
  if (status.state === "failed") {
    return {
      tone: "failed",
      text: "Build failed",
      detail: String(status.detail || stage || "").trim(),
      progress,
    };
  }
  if (status.state === "stale") {
    return {
      tone: "stale",
      text: "Build stopped responding",
      detail: stage ? `last stage: ${stage}` : "",
      progress,
    };
  }
  if (status.state === "done") {
    const updatedAt = Number(status.updatedAt) || 0;
    // updatedAt is epoch ms if it is large enough to be one, else seconds.
    const updatedMs = updatedAt > 1e11 ? updatedAt : updatedAt * 1000;
    if (updatedMs && now - updatedMs > doneWindowMs) return null;
    const elapsed = Number.isFinite(Number(status.elapsedS)) ? formatElapsed(status.elapsedS) : "";
    return {
      tone: "done",
      text: "Built",
      detail: [elapsed, String(status.detail || "").trim()].filter(Boolean).join(" · "),
      progress: 1,
    };
  }
  return null;
}
