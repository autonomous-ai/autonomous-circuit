// placementFields — the editable half of the Properties panel, as data.
//
// Properties is where a coordinate gets typed instead of dragged, and the two
// have to mean the same thing. They do, literally: `placementMoveTo` builds a
// one-step drag session and hands it to `placementDrag.commitMove`, the same
// function the canvas calls on pointerup. There is one edit shape in this app
// because there is one function that makes it, and `handlePlacementMove` in
// BoardWorkspace stays the only caller of `editor.move` — one shape, one write
// path. See `docs/architecture/ide-edit-contract.md`.
//
// Everything else in this module answers the other question a field has to
// answer: *why can I not type in this one*. A field that is simply missing
// reads as a broken tool, so every field is either editable or carries the
// sentence that says why not — and the refusals are not all the same kind of
// thing:
//
//   - **structural** — there is no number in the board file to change. A part
//     placed by a golden block or by `keyCells()` has no literal of its own;
//     100 of terminal-keyboard's 137 parts are in this bucket. Never changing.
//   - **shape of the file** — rotation. `boardSource.rotateEdits` writes
//     `pcbRotation`, but a part whose tag is one of our own components drops an
//     unknown prop in silence, so turning it means wrapping it in a `<group>`
//     first. `placementRotate.rotateRefusal` owns that sentence, and this file
//     asks it rather than restating it.
//   - **state** — move mode is off, an older build is on screen, the file has
//     not been read yet, the placement is locked. All of these go away.
//
// Spending one word ("cannot") on all three would be a lie about two of them.
//
// DOM-free and React-free, so `node:test` covers the validation, the reasons
// and the edit shape directly.

import { formatMm } from "./boardSource.js";
import { commitMove } from "./placementDrag.js";
import { rotateRefusal } from "./placementRotate.js";

/**
 * The same literal grammar `readNumericProp` accepts (`boardSource.js:44`).
 * Deliberately narrower than `Number()`: `1e2` parses to 100 but the board file
 * would then carry an exponent where every other coordinate is a plain decimal,
 * and `NUMBER_LITERAL` would refuse to read it back on the next parse.
 */
const DECIMAL_LITERAL = /^[+-]?(?:\d+\.?\d*|\.\d+)$/;

/** How far off the board a coordinate may sit when the board size is unknown. */
export const FALLBACK_LIMIT_MM = 500;

/** Reason strings, exported so a test asserts on the sentence the user reads. */
export const REASONS = Object.freeze({
  viewing: "this is an older build — switch back to the latest one before moving anything",
  off: "move mode is off — press E, or the move tool on the rail",
  loading: "still reading the board file",
  unbound:
    "nothing in the board file places this directly — it comes from a block or a loop, so there is no number to change",
  // Rotation's own refusals come from `placementRotate.rotateRefusal`, which
  // the canvas and the edit bar already show. Only the reason a *placement* is
  // off-limits altogether is stated here.
  rotationStep: "turn it in steps of 90° — type an exact angle in the board file",
});

/**
 * Why a locked placement's numbers are read-only.
 *
 * The canvas says "unlock it above", meaning the edit bar. Here the unlock sits
 * a few pixels under the numbers it is about, so the sentence points at that
 * one instead — same refusal, each pointing at its own way out.
 */
export function lockedReason(label) {
  return `${label || "this placement"} is locked by hand — unlock it to change these numbers`;
}

/**
 * How far from the origin a placement may be typed.
 *
 * A board's own size is the only honest bound available: on a 100 mm board
 * ±100 mm still allows 50 mm clear of every edge, and it catches the typo this
 * field exists to catch — `-458` where `-45.8` was meant.
 */
export function fieldLimitMm(board) {
  const width = Number(board?.width);
  const height = Number(board?.height);
  const span = Math.max(Number.isFinite(width) ? width : 0, Number.isFinite(height) ? height : 0);
  return span > 0 ? Math.max(span, 10) : FALLBACK_LIMIT_MM;
}

/**
 * Read one typed millimetre value.
 *
 * Never coerces: a value the board file cannot hold comes back `ok: false` with
 * the reason, and a value that will be written with fewer decimals than typed
 * comes back `ok: true` with `rounded` set so the field can show what it is
 * about to write. Silently turning `-12.3456` into `-12.346` inside a tool that
 * writes source code is how a user stops trusting the numbers on screen.
 *
 * @param {string} raw
 * @param {{limitMm?: number}} options
 * @returns {{ok: boolean, value: number, written: string, rounded: boolean, reason: string}}
 */
export function parseMmField(raw, { limitMm = FALLBACK_LIMIT_MM } = {}) {
  const fail = (reason) => ({ ok: false, value: NaN, written: "", rounded: false, reason });
  const text = String(raw ?? "").trim();
  if (!text) return fail("type a number in millimetres");
  if (!DECIMAL_LITERAL.test(text)) return fail(`"${text}" is not a number — millimetres, like -12.7`);
  const value = Number(text);
  if (!Number.isFinite(value)) return fail(`"${text}" is not a number — millimetres, like -12.7`);
  const limit = Number.isFinite(Number(limitMm)) && Number(limitMm) > 0 ? Number(limitMm) : FALLBACK_LIMIT_MM;
  if (Math.abs(value) > limit) {
    return fail(`${formatMm(value)} mm is off the board — ±${formatMm(limit)} mm is as far as this one goes`);
  }
  const written = formatMm(value);
  return { ok: true, value, written, rounded: Number(written) !== value, reason: "" };
}

/**
 * A typed coordinate as the same edit a drop produces.
 *
 * Not a parallel implementation of the drag's arithmetic — an actual one-step
 * drag session handed to `commitMove`, so the edit the panel emits and the edit
 * the canvas emits come out of the same function and cannot drift. That also
 * inherits `commitMove`'s null: typing back the number that is already there is
 * a no-op, exactly as a drag that snaps back to its own start is a click.
 *
 * Either coordinate may be omitted — a field commits one axis, and the other
 * stays whatever the board file says.
 *
 * @param {{x: number, y: number, id: string, label: string, refdes: string[]}} placement
 * @param {{x?: number, y?: number}} next
 * @returns {{placementId, label, refdes, x, y, dx, dy}|null}
 */
export function placementMoveTo(placement, next) {
  if (!placement) return null;
  const from = { x: Number(placement.x) || 0, y: Number(placement.y) || 0 };
  const wantX = Number(next?.x);
  const wantY = Number(next?.y);
  const x = Number.isFinite(wantX) ? wantX : from.x;
  const y = Number.isFinite(wantY) ? wantY : from.y;
  return commitMove({ placement, dx: x - from.x, dy: y - from.y, moved: true });
}

/**
 * Why no placement on this selection can be edited right now, or "" when one
 * can. Ordered most-immediate-first: telling someone their part is unreachable
 * when the real answer is that move mode is off sends them to the wrong fix.
 *
 * @param {{viewing?: boolean, editing?: boolean, ready?: boolean, reason?: string}} editor
 */
export function editBlockedReason({ viewing = false, editing = false, ready = false, reason = "" } = {}) {
  if (viewing) return REASONS.viewing;
  if (!editing) return REASONS.off;
  if (!ready) return String(reason || REASONS.loading);
  return "";
}

/**
 * Describe every field the Properties panel offers for one selection.
 *
 * `placement` is the bound placement that owns the selection — the block frame
 * for a part inside a block instance, the part's own tag for one the board file
 * placed itself, the hole or the label for loose geometry. It is null whenever
 * the selection has no line of source behind it.
 *
 * `refusal` is `placementDrag.refusalForHit`'s answer for the same thing, when
 * the panel could get one. Reusing it is the point: the sentence a user reads
 * for a part they cannot type into is the sentence they would have read for
 * dragging it — "R7 moves with its block", not a house style of our own.
 *
 * @param {{
 *   placement?: object|null,
 *   editor?: {viewing?: boolean, editing?: boolean, ready?: boolean, reason?: string, busy?: boolean},
 *   board?: object|null,
 *   builtRotation?: number|null,
 *   refusal?: {kind: string, reason: string}|null,
 * }} options
 */
export function placementFields({
  placement = null,
  editor = {},
  board = null,
  builtRotation = null,
  refusal = null,
} = {}) {
  const limitMm = fieldLimitMm(board);
  const blocked = editBlockedReason(editor) || (placement ? "" : refusal?.reason || REASONS.unbound);
  const lockedOut = !blocked && placement?.locked ? lockedReason(placement.label) : "";
  const moveReason = blocked || lockedOut;
  const canMove = !moveReason;

  return {
    limitMm,
    busy: Boolean(editor?.busy),
    x: {
      value: placement ? Number(placement.x) : null,
      text: placement ? formatMm(placement.x) : "",
      editable: canMove,
      reason: moveReason,
    },
    y: {
      value: placement ? Number(placement.y) : null,
      text: placement ? formatMm(placement.y) : "",
      editable: canMove,
      reason: moveReason,
    },
    // Two angles, and they are allowed to differ. `value` is what the last
    // BUILD drew, which is what the pads on screen are actually at; `fileValue`
    // is what `boards/<stem>.tsx` says now. After a turn and before a rebuild
    // those disagree, and that disagreement IS the state — the same honesty
    // the X/Y rows keep about a moved part whose copper has not followed.
    //
    // Editable through the step buttons rather than a text box: `rotateEdits`
    // writes whole angles fine, but the refusal a part earns (locked, or one of
    // our own components needing a `<group>` wrapper first) is the same refusal
    // the canvas and the edit bar show, so the panel asks the same function.
    rotation: {
      value: Number.isFinite(Number(builtRotation)) ? Number(builtRotation) : null,
      fileValue: placement ? Number(placement.rotation) || 0 : null,
      editable: canMove && !rotateRefusal(placement),
      reason: moveReason || rotateRefusal(placement)?.reason || "",
    },
    // The lock stays reachable while the placement is locked — it is the way
    // back out. Everything else that blocks a move blocks it too.
    lock: {
      value: Boolean(placement?.locked),
      editable: !blocked && Boolean(placement),
      reason: blocked || (placement ? "" : refusal?.reason || REASONS.unbound),
    },
  };
}

/**
 * What a move here will actually move, in one line, before it commits.
 *
 * Not a nicety: on terminal-keyboard no placement owns a single part — the
 * smallest handle on the board moves two. A field that says "X" and moves an
 * LED plus its resistor has to say so where the number is typed.
 */
export function placementSummary(placement) {
  if (!placement) return null;
  const refdes = Array.isArray(placement.refdes) ? placement.refdes.filter(Boolean) : [];
  const parts = refdes.length;
  const offset = placement.offset || { dx: 0, dy: 0 };
  const moved = Number(offset.dx) !== 0 || Number(offset.dy) !== 0;
  return {
    label: String(placement.label || placement.tag || "placement"),
    tag: String(placement.tag || ""),
    parts,
    refdes,
    // A hole and a silkscreen label own no parts at all; saying "0 parts"
    // would read as a bug rather than as the truth about a drill.
    moves: parts
      ? `moves ${parts} ${parts === 1 ? "part" : "parts"}${parts > 1 ? ` — ${refdes.join(", ")}` : ""}`
      : "moves the drawing on this line, no parts",
    pending: moved
      ? `the file says ${formatMm(placement.x)}, ${formatMm(placement.y)} — the board below is still the last build at ${formatMm(placement.anchor?.x)}, ${formatMm(placement.anchor?.y)}`
      : "",
  };
}
