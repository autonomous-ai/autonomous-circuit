// boardRevisions — the model behind the revision pager: what one build of a
// board is worth remembering, how the ring is kept, and how the dot strip
// windows. DOM-free and storage-free so node:test covers it directly; the
// IndexedDB half lives in lib/revisionStore.js.
//
// Vibe's `1/9` steps *meshes* in a project (ModelViewer.tsx + SlideDots.tsx).
// Ours steps *builds of one board*, because that is where our interesting
// history is: every rebuild rewrites `<stem>.circuit.json` in place, and the
// repair loop converging from 6 errors to 0 is precisely the story that
// overwrite destroys. The dot-windowing behaviour is ported from SlideDots
// verbatim; the severity tint on each dot is ours, and it is what makes the
// convergence readable at a glance.

/** How many builds we keep per board. Eight covers a full repair loop. */
export const REVISION_LIMIT = 8;

/** Max dots before the strip becomes a sliding window (donor's MAX_DOTS). */
export const MAX_DOTS = 7;

/**
 * The identity of one build, taken from the artifact URL's `?v=<mtime>-<size>`
 * cache-bust — the same token that already makes the workspace refetch. Two
 * builds that produced byte-identical output share a token, and that is
 * correct: they are the same revision.
 */
export function revisionToken(url) {
  const query = String(url || "").split("?")[1] || "";
  const match = /(?:^|&)v=([^&]+)/.exec(query);
  return match ? decodeURIComponent(match[1]) : "";
}

/**
 * True when an artifact URL really belongs to `projectId`.
 *
 * Needed because a project switch changes `projectId` a render before the new
 * project's artifacts arrive: for that one frame the workspace is holding the
 * OUTGOING board's circuit.json under the INCOMING project's id, and recording
 * it would file one board's history under another's name. Asset URLs are
 * rooted at `/projects/<id>/` (http.mjs `assetPathForRequest`), so the URL
 * itself settles the question.
 */
export function urlBelongsToProject(url, projectId) {
  const id = String(projectId || "");
  if (!id) return false;
  return String(url || "").includes(`/projects/${id}/`);
}

/**
 * The one-line summary a revision is remembered by. Everything here comes from
 * artifacts we already parse, so recording a revision costs no extra work.
 *
 * @param {{sidecar?: object|null, index?: object|null}} input
 */
export function summarizeRevision({ sidecar = null, index = null } = {}) {
  const warnings = Array.isArray(sidecar?.validation?.warnings) ? sidecar.validation.warnings : [];
  let errors = 0;
  let warns = 0;
  let infos = 0;
  for (const warning of warnings) {
    const severity = String(warning?.severity || "warning").toLowerCase();
    if (severity === "error") errors += 1;
    else if (severity === "info") infos += 1;
    else warns += 1;
  }
  return {
    components: Number(index?.stats?.components) || 0,
    nets: Number(index?.stats?.nets) || 0,
    elements: Number(index?.stats?.elements) || 0,
    errors,
    warnings: warns,
    infos,
    fabReady: sidecar?.fab?.ready === true,
    widthMm: Number(sidecar?.board?.widthMm) || 0,
    heightMm: Number(sidecar?.board?.heightMm) || 0,
  };
}

/** "error" | "warning" | "clean" — what tints the dot. */
export function worstSeverity(summary) {
  if (!summary) return "clean";
  if (Number(summary.errors) > 0) return "error";
  if (Number(summary.warnings) > 0) return "warning";
  return "clean";
}

/**
 * Fold a freshly observed build into the ring: oldest first, newest last,
 * deduped by token, capped at `limit`.
 *
 * A token we already hold is a re-read of the same build (a refetch, a tab
 * switch), not a new revision — its `capturedAt` is left alone so the ring
 * keeps recording when a build first appeared rather than when we last looked
 * at it. Its summary IS refreshed, because the sidecar can land before the IR.
 *
 * @returns {{list: object[], added: boolean}} `list` is a new array only when
 *   something actually changed, so React can compare by identity.
 */
export function mergeRevision(list, entry, limit = REVISION_LIMIT) {
  const current = Array.isArray(list) ? list : [];
  const token = String(entry?.token || "");
  if (!token) return { list: current, added: false };

  const existingIndex = current.findIndex((item) => item.token === token);
  if (existingIndex >= 0) {
    const existing = current[existingIndex];
    const merged = { ...existing, ...entry, capturedAt: existing.capturedAt };
    if (JSON.stringify(merged) === JSON.stringify(existing)) return { list: current, added: false };
    const next = [...current];
    next[existingIndex] = merged;
    return { list: next, added: false };
  }

  const next = [...current, { ...entry, token }];
  const capped = next.length > limit ? next.slice(next.length - limit) : next;
  return { list: capped, added: true };
}

/** Wrapping step, the donor's `(i + count) % count`. Returns 0 for an empty ring. */
export function stepIndex(index, delta, count) {
  const total = Number(count) || 0;
  if (total <= 0) return 0;
  const raw = (Number(index) || 0) + (Number(delta) || 0);
  return ((raw % total) + total) % total;
}

/**
 * The dot strip, ported from `SlideDots.tsx`: every dot up to MAX_DOTS, and
 * past that a sliding window centred on the active dot with the edge dot
 * shrunk when more revisions exist beyond it. Fixed width whatever the count,
 * which is the whole point — the strip must never grow into the tool rail.
 *
 * @returns {{indices: number[], hasBefore: boolean, hasAfter: boolean, isEdge: (i:number)=>boolean}}
 */
export function dotWindow(count, active, max = MAX_DOTS) {
  const total = Math.max(0, Number(count) || 0);
  const current = Math.min(Math.max(Number(active) || 0, 0), Math.max(total - 1, 0));
  const half = Math.floor(max / 2);
  const start = total <= max ? 0 : Math.min(Math.max(current - half, 0), total - max);
  const end = Math.min(start + max, total);
  const hasBefore = start > 0;
  const hasAfter = end < total;
  const indices = [];
  for (let i = start; i < end; i += 1) indices.push(i);
  return {
    indices,
    hasBefore,
    hasAfter,
    isEdge: (i) => (i === start && hasBefore) || (i === end - 1 && hasAfter),
  };
}

/** "now" / "4m" / "2h" / "3d" — the age of a build, short enough for a tooltip. */
export function formatRevisionAge(capturedAt, now = Date.now()) {
  const then = Number(capturedAt) || 0;
  if (!then) return "";
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 45) return "now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

/**
 * The tooltip / caption for one revision — what changed, in the terms an
 * engineer cares about. Compared against the revision *before* it so the
 * pager reads as a story rather than a list of absolutes.
 */
export function describeRevision(revision, previous = null, now = Date.now()) {
  const summary = revision?.summary || {};
  const age = formatRevisionAge(revision?.capturedAt, now);
  const parts = [];
  if (summary.errors) parts.push(`${summary.errors} error${summary.errors === 1 ? "" : "s"}`);
  else parts.push(summary.fabReady ? "fab-ready" : "no errors");
  if (summary.warnings) parts.push(`${summary.warnings} warning${summary.warnings === 1 ? "" : "s"}`);

  const before = previous?.summary || null;
  if (before) {
    const delta = (Number(before.errors) || 0) - (Number(summary.errors) || 0);
    if (delta > 0) parts.push(`−${delta} vs previous`);
    else if (delta < 0) parts.push(`+${-delta} vs previous`);
  }
  return [age, parts.join(" · ")].filter(Boolean).join(" — ");
}
