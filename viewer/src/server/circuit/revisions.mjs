/**
 * Build history for a project — `<workspace>/.circuit/revisions.jsonl`.
 *
 * The app can say "this board is not orderable yet, 1 thing to fix". It cannot
 * say "it had 6 things three builds ago", and that second sentence is the more
 * convincing one: it is the difference between a tool that reports a problem
 * and a tool visibly converging on a board you can order.
 *
 * The review loop (`runReviewFixLoop`) already counts warnings at the top of
 * every round and throws the number away. This keeps it.
 *
 * Append-only JSONL, one line per recorded round. Append is the whole point:
 * a read-modify-write of a JSON array loses rounds when two builds overlap,
 * and a partially written line is skipped by the reader rather than
 * corrupting the file. Recording is best-effort throughout — losing history
 * is a nuisance, losing a build is not, so nothing here may throw into a
 * build.
 */

import fs from "node:fs";
import path from "node:path";

/** Where history lives, relative to a project workspace. */
export const REVISIONS_DIR = ".circuit";
export const REVISIONS_FILE = "revisions.jsonl";

/** Lines returned by default. History is for showing a trend, not an audit. */
export const DEFAULT_REVISION_LIMIT = 50;

/** A line longer than this is treated as corrupt and skipped. */
const MAX_LINE_BYTES = 64 * 1024;

export function revisionsPath(workspace) {
  return path.join(workspace, REVISIONS_DIR, REVISIONS_FILE);
}

/**
 * Split warnings into the buckets the UI actually distinguishes.
 *
 * `blocking` is the only one that decides orderability, so it is counted
 * against the contract's closed severity set rather than a guess about kinds.
 * The caller passes the predicates it already uses, so this module never
 * develops a second opinion about what "blocking" means — one definition,
 * owned by the driver.
 */
export function countWarnings(warnings, { isBlocking, isElectrical } = {}) {
  const list = Array.isArray(warnings) ? warnings : [];
  let blocking = 0;
  let electrical = 0;
  for (const w of list) {
    if (isBlocking?.(w)) {
      blocking += 1;
      continue;
    }
    if (isElectrical?.(w)) {
      electrical += 1;
    }
  }
  return {
    total: list.length,
    blocking,
    electrical,
    other: list.length - blocking - electrical,
  };
}

/**
 * Append one revision. Never throws.
 *
 * `fabReady` is deliberately a tri-state: `true`, `false`, or `null` when the
 * caller does not know. A round recorded mid-loop has not re-run the fab gate,
 * and writing `false` there would draw a history of failures that never
 * happened.
 *
 * Returns true when the line was written.
 */
export function recordRevision(workspace, entry = {}) {
  try {
    const dir = path.join(workspace, REVISIONS_DIR);
    fs.mkdirSync(dir, { recursive: true });
    const line = {
      at: entry.at ?? new Date().toISOString(),
      turnId: String(entry.turnId ?? ""),
      phase: String(entry.phase ?? ""),
      round: Number.isFinite(entry.round) ? Number(entry.round) : 0,
      counts: {
        total: Number(entry.counts?.total ?? 0),
        blocking: Number(entry.counts?.blocking ?? 0),
        electrical: Number(entry.counts?.electrical ?? 0),
        other: Number(entry.counts?.other ?? 0),
      },
      fabReady:
        entry.fabReady === true || entry.fabReady === false
          ? entry.fabReady
          : null,
    };
    fs.appendFileSync(revisionsPath(workspace), `${JSON.stringify(line)}\n`);
    return true;
  } catch {
    return false;
  }
}

/**
 * The most recent revisions, oldest first. Never throws; a missing file is an
 * empty history, which is what a project that has never been built has.
 *
 * Malformed lines are skipped rather than failing the read — a truncated
 * final line is the normal state of a file being appended to while it is read.
 */
export function readRevisions(workspace, { limit = DEFAULT_REVISION_LIMIT } = {}) {
  let raw;
  try {
    raw = fs.readFileSync(revisionsPath(workspace), "utf8");
  } catch {
    return [];
  }
  const out = [];
  for (const line of raw.split("\n")) {
    const text = line.trim();
    if (!text || text.length > MAX_LINE_BYTES) continue;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object") out.push(parsed);
    } catch {
      // Truncated or corrupt line — skip it, keep the rest.
    }
  }
  const n = Math.max(0, Number(limit) || 0);
  return n > 0 ? out.slice(-n) : out;
}

/**
 * The trend, for one sentence in the UI.
 *
 * Compares the newest revision against the oldest one still in history.
 * Returns null when there is nothing honest to say — zero or one revision is
 * not a trend, and claiming one from a single data point is the kind of
 * confident-but-empty statement this app is trying not to make.
 */
export function revisionTrend(revisions) {
  const list = Array.isArray(revisions) ? revisions : [];
  if (list.length < 2) return null;
  const first = list[0];
  const last = list[list.length - 1];
  const from = Number(first?.counts?.blocking ?? 0);
  const to = Number(last?.counts?.blocking ?? 0);
  return {
    builds: list.length,
    from,
    to,
    fixed: Math.max(0, from - to),
    // A board can gain blockers — a fix that moved a part can break clearance
    // somewhere else. Saying so is the point of keeping history.
    worse: to > from,
    fabReady: last?.fabReady === true,
  };
}
