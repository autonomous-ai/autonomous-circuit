// buildStatus — pure presentation logic for the pipeline's live build stage.
// The transport call and the React hook live in useBuildStatus.js; everything
// here is DOM-free and fetch-free so node:test covers it directly.
//
// The record comes from `POST /api/build_status {id}` and is written by the
// pipeline once per stage transition (7 per build, no heartbeat). A board
// build runs 45–90s, which is exactly long enough that a bare spinner reads as
// "possibly hung" — the stage label is the difference.

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
