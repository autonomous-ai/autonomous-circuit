/**
 * A drag arrives here as a sentence, not as byte offsets.
 *
 * `board_source_write` already exists and takes byte ranges: the client parses
 * the file, computes the splice, and the server compare-and-swaps it. That is
 * the right primitive and it stays. This is the semantic layer above it —
 * "move the placement called `StatusLed[1]` 2.8mm east" — and it exists for
 * three reasons the byte-range command cannot serve:
 *
 * 1. **The parse happens on the file the server is about to write.** A client
 *    that read the file, and an agent that rewrote it in between, produce byte
 *    offsets into two different files. The compare-and-swap catches that and
 *    refuses; parsing server-side means the common case does not have to be a
 *    refusal at all — only a genuinely concurrent write is.
 * 2. **Two edits from one user cannot interleave.** A semantic edit is a
 *    read-modify-write, and two of them racing inside one process would both
 *    read the pre-edit file. `serialize()` below is a per-project promise chain
 *    so the second waits for the first, then re-reads.
 * 3. **The verdict belongs with the write.** Saving and grading are one round
 *    trip, so the UI cannot show "saved" and then forget to ask whether it was
 *    legal.
 *
 * The edit engine itself is not reimplemented here. `boardSource.js` is pure
 * text with no DOM and no fetch (that is why it is testable at all), so the
 * server imports the same parser, the same `formatMm`, the same `moveEdits` and
 * `lockEdits` the canvas uses. One definition of what a placement is.
 */

import fs from "node:fs";
import path from "node:path";

import {
  LOCK_COMMENT,
  formatMm,
  lockEdits,
  moveEdits,
  parseBoardSource,
  placementRangeReason,
} from "../../client/components/board/boardSource.js";

/** Refusals a caller can act on, mapped to HTTP by the command wrapper. */
export const EDIT_ERROR = Object.freeze({
  INVALID: "INVALID_ARGUMENT",
  SOURCE_CHANGED: "SOURCE_CHANGED",
  NO_PLACEMENT: "PLACEMENT_NOT_FOUND",
  NO_CHANGE: "NO_CHANGE",
});

class EditError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

/**
 * Per-project serialisation.
 *
 * A `Map` of tails, one promise chain per project. Deliberately in-process and
 * deliberately not a lock file: the thing this protects against is two requests
 * to *this* server interleaving their read and their write. A second process —
 * the agent — is not covered here and is not meant to be; that is what the
 * compare-and-swap and the mid-turn refusal are for, and a lock file would add
 * a way for a crashed process to wedge the board file shut forever.
 */
export function createEditQueue() {
  const tails = new Map();
  return function serialize(key, work) {
    const previous = tails.get(key) || Promise.resolve();
    // `.catch` on the tail, not on the returned promise: a failed edit must not
    // poison the queue for the next one, but its rejection still reaches the
    // caller.
    const next = previous.then(work, work);
    tails.set(
      key,
      next.catch(() => {}),
    );
    return next;
  };
}

/** One placement, as the UI names it in a history line. */
function placementLabel(placement) {
  const name = String(placement?.name || "").trim();
  return name ? `${placement.tag} ${name}` : String(placement?.tag || placement?.id || "a placement");
}

/**
 * Turn a semantic edit into byte-range edits against `source`.
 *
 * Returns `{edits, placement, summary, delta}`. Throws `EditError` when the
 * placement cannot be found or the edit changes nothing — "nothing changed" is
 * a refusal rather than a silent success because the caller is about to record
 * a revision and rebuild a board on the strength of the answer.
 */
/**
 * Why this write must not land, or "".
 *
 * `board_source_write` takes byte ranges, so it has no coordinate to check —
 * and it is the command every real gesture in the app goes through (drag,
 * Properties, undo, redo). A bound that lives only in `planPlacementEdit` is
 * therefore a bound on the endpoint nothing calls: measured on the running
 * server, `pcbX={1e30}` spliced through here was written to disk without
 * complaint, twice.
 *
 * So the *result* is what gets checked, two ways, because the obvious way
 * misses the interesting half:
 *
 * 1. **A placement nowhere a board could be.** ±1000mm, the same rule the
 *    semantic endpoint applies, from the same function.
 * 2. **A placement that has stopped being readable.** `1e30` is not caught by
 *    (1) at all — the parser's number grammar has no exponent, so the part
 *    does not come back as out of range, it does not come back *at all*. A
 *    board whose editor can no longer see a part it could see a moment ago is
 *    a worse outcome than a refused write, and it is invisible until someone
 *    tries to drag that part. Counting rather than comparing ids on purpose:
 *    the wrap edit deliberately renames a placement (an `<Ldo3v3>` becomes a
 *    `<group>`) and must keep working.
 *
 * A file that does not parse at all, before or after, is left alone. This is a
 * guard against a number that is not a position, not a second opinion about
 * what a board file may contain.
 */
export function refuseWrittenBoard(text, previousText) {
  let after;
  try {
    after = parseBoardSource(String(text || ""));
  } catch {
    return "";
  }
  if (!after?.ok) return "";
  for (const placement of after.placements || []) {
    const reason = placementRangeReason(placement.x, placement.y);
    if (reason) return `${placement.name || placement.id}: ${reason}`;
  }
  if (previousText === undefined || previousText === null) return "";
  let before;
  try {
    before = parseBoardSource(String(previousText));
  } catch {
    return "";
  }
  if (!before?.ok) return "";
  const lost = (before.placements?.length || 0) - (after.placements?.length || 0);
  if (lost > 0) {
    return (
      `this edit makes ${lost} placement${lost === 1 ? "" : "s"} unreadable — ` +
      "the board file would still compile, and the app could no longer see the part to move it"
    );
  }
  return "";
}

export function planPlacementEdit(source, edit) {
  const kind = String(edit?.kind || "move");
  const parsed = parseBoardSource(source);
  if (!parsed.ok) {
    throw new EditError(EDIT_ERROR.INVALID, parsed.reason || "this board file cannot be parsed");
  }
  const placementId = String(edit?.placementId || "");
  const placement = parsed.placements.find((candidate) => candidate.id === placementId);
  if (!placement) {
    throw new EditError(
      EDIT_ERROR.NO_PLACEMENT,
      `no placement called ${placementId || "(unnamed)"} in this board file`,
    );
  }

  if (kind === "lock") {
    const locked = Boolean(edit?.locked);
    const edits = lockEdits(source, placement, locked);
    if (!edits.length) {
      throw new EditError(EDIT_ERROR.NO_CHANGE, `${placementLabel(placement)} is already ${locked ? "locked" : "unlocked"}`);
    }
    return {
      edits,
      placement,
      // A lock moves nothing, so the fast gate has nothing to predict and the
      // history line must not imply a geometry change.
      delta: { dx: 0, dy: 0 },
      summary: `${locked ? "locked" : "unlocked"} ${placementLabel(placement)}`,
      next: { x: placement.x, y: placement.y, locked },
    };
  }

  if (kind !== "move") {
    throw new EditError(EDIT_ERROR.INVALID, `unknown edit kind: ${kind}`);
  }

  // Either an absolute destination or a delta. The canvas sends a delta because
  // that is what a drag is; a keyboard nudge sends one too.
  const hasAbsolute = Number.isFinite(Number(edit?.x)) && Number.isFinite(Number(edit?.y));
  const x = hasAbsolute ? Number(edit.x) : placement.x + Number(edit?.dx || 0);
  const y = hasAbsolute ? Number(edit.y) : placement.y + Number(edit?.dy || 0);
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    throw new EditError(EDIT_ERROR.INVALID, "the move has no destination");
  }
  // Finite is not the same as plausible. `x: 1e30` is finite, writes
  // `pcbX={1e+30}` into the board source without complaint, and comes back from
  // the gate as `legal` — measured 2026-08-16 — because nothing downstream
  // measures a placement against anything. One owner for the rule, shared with
  // the client that types the number in (`boardSource.placementRangeReason`).
  const outOfRange = placementRangeReason(x, y);
  if (outOfRange) {
    throw new EditError(EDIT_ERROR.INVALID, outOfRange);
  }
  const edits = moveEdits(source, placement, x, y);
  if (!edits.length) {
    throw new EditError(EDIT_ERROR.NO_CHANGE, `${placementLabel(placement)} is already there`);
  }
  // The delta reported back is measured from what `formatMm` actually wrote,
  // not from what was asked for. A 0.0004mm nudge rounds away in the file, and
  // a gate told about a move the file does not contain grades geometry nobody
  // will ever build.
  const wroteX = Number(formatMm(x));
  const wroteY = Number(formatMm(y));
  return {
    edits,
    placement,
    // Rounded through `formatMm` again, not just subtracted: -43 − -45.8 is
    // 2.799999999999997 in binary floating point, and that dust ends up in a
    // CLI argument and in a history line where it reads as a measurement.
    delta: {
      dx: Number(formatMm(wroteX - placement.x)),
      dy: Number(formatMm(wroteY - placement.y)),
    },
    summary: `moved ${placementLabel(placement)} from (${formatMm(placement.x)}, ${formatMm(placement.y)}) to (${formatMm(wroteX)}, ${formatMm(wroteY)})`,
    next: { x: wroteX, y: wroteY, locked: placement.locked },
  };
}

/**
 * Write `text` where `file` is, atomically.
 *
 * Temp file plus `rename`, because the catalog watcher can start a build the
 * instant it notices the change and a half-written board source is one the next
 * build cannot compile. Same discipline as `board_source_write`.
 */
export function writeAtomic(file, text) {
  const tmp = path.join(path.dirname(file), `.${path.basename(file)}.${process.pid}.tmp`);
  fs.writeFileSync(tmp, text, "utf8");
  fs.renameSync(tmp, file);
}

export { EditError, LOCK_COMMENT, placementLabel };
