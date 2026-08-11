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
 * The router-retry row, inserted only when a retry is actually happening.
 *
 * Stage 0b of the pipeline re-runs the compile at `autorouterEffortLevel="5x"`
 * when the first attempt left routing errors. It reports itself by going back
 * to `compile`, and it can run for twenty minutes — harness-puck took 1240s at
 * 5x against about 90s at the default. Without a row of its own the checklist
 * silently rewinds two steps and then sits there, which is the exact "reads as
 * hung" failure the checklist exists to prevent.
 */
export const ROUTER_RETRY_STAGE = Object.freeze({
  key: "reroute",
  label: "Trying harder to route the board",
  plain:
    "The first attempt left tracks the factory would reject, so the router is running again at five times the effort. This one can take fifteen minutes or more.",
  retry: true,
});

/**
 * The stage list with each entry marked `done` / `active` / `pending` against
 * a live status record. Position comes from the stage KEY when we recognise
 * it and from `stageIndex` otherwise, so an unknown stage still advances the
 * list rather than freezing it at the top.
 */
export function buildStageChecklist(status) {
  const key = String(status?.stage || "");
  // A router retry replays `compile` and `scan`. Showing the checklist walk
  // backwards would read as a bug, so once a retry is detected the replayed
  // stages belong to the retry row, not to the originals.
  const retrying = Boolean(status?.routerRetry);
  const stages = retrying
    ? [...BUILD_STAGES.slice(0, 3), ROUTER_RETRY_STAGE, ...BUILD_STAGES.slice(3)]
    : BUILD_STAGES;
  const known = stages.findIndex((stage) => stage.key === key);
  const reported = Number(status?.stageIndex);
  // The prelude occupies index 0, so a pipeline stage reported as index N sits
  // at N in this list rather than N-1.
  let current =
    known >= 0 ? known : Number.isFinite(reported) && reported > 0 ? Math.min(reported, stages.length - 1) : 0;
  // While the retry runs the pipeline reports `compile` or `scan` again; the
  // honest position is the retry row itself.
  if (retrying && (key === "compile" || key === "scan")) {
    current = stages.findIndex((stage) => stage.key === ROUTER_RETRY_STAGE.key);
  }
  const finished = String(status?.state || "") === "done";
  return stages.map((stage, i) => ({
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
 * Where the board came from — one sentence, or null.
 *
 * "Not orderable yet, 1 thing to fix" is a complaint. "Six things stopped the
 * order three builds ago; one does now" is a tool visibly converging, and it is
 * the more convincing sentence by a distance. The server keeps the history the
 * review loop used to throw away (`build_revisions`); this turns it into words.
 *
 * Three honesty rules, all of which the data is shaped to support:
 *   · **one data point is not a trend.** The server returns `trend: null` below
 *     two revisions and we render nothing rather than "no change yet".
 *   · **getting worse is a real outcome.** A fix that moves a part can break
 *     clearance somewhere else. Hiding that would make the line dishonest in
 *     exactly the case the user most needs it.
 *   · **`fabReady` is tri-state.** `null` is a mid-loop round that never re-ran
 *     the fab gate; only an explicit `true` earns the first-try sentence.
 *
 * @param {{revisions?: Array<object>, trend?: object|null}|null} history
 * @returns {{tone: "first-try"|"better"|"worse"|"flat", text: string}|null}
 */
export function buildHistoryLine(history) {
  const revisions = Array.isArray(history?.revisions) ? history.revisions : [];
  if (!revisions.length) return null;
  const trend = history?.trend || null;

  if (!trend) {
    // A board that built clean the first time leaves exactly one entry and no
    // trend. That is the outcome the whole pipeline is aiming at, so it reads
    // as a win rather than as an absence of history.
    const only = revisions[revisions.length - 1];
    if (only?.fabReady === true && Number(only?.counts?.blocking || 0) === 0) {
      return { tone: "first-try", text: "Clean on the first build — no repair rounds." };
    }
    return null;
  }

  const { builds, from, to, worse } = trend;
  // "findings", not "things": the verdict above counts grouped *issues* and
  // this counts individual findings, and two different nouns is the only way
  // the two numbers do not read as a contradiction.
  const found = (n) => `${n} ${n === 1 ? "finding" : "findings"}`;
  if (worse) {
    return {
      tone: "worse",
      text: `${found(from)} stopped the order ${builds} builds ago; ${to} do now — the last changes made it worse.`,
    };
  }
  if (to < from) {
    return {
      tone: "better",
      text:
        to === 0
          ? `${found(from)} stopped the order ${builds} builds ago. Now none do.`
          : `${found(from)} stopped the order ${builds} builds ago; ${to} still ${to === 1 ? "does" : "do"}.`,
    };
  }
  return {
    tone: "flat",
    text:
      to === 0
        ? `Nothing has stopped the order for ${builds} builds.`
        : `Still ${found(to)} stopping the order, unchanged across ${builds} builds.`,
  };
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
    if (status.routerRetry) {
      return {
        tone: "running",
        text: ROUTER_RETRY_STAGE.label,
        detail: "5× router effort — this one is slow",
        progress,
      };
    }
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
