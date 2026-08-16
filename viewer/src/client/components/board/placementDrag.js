// placementDrag — the arithmetic of carrying a part across the PCB canvas.
//
// Split out of PcbCanvas because this is the half of move mode that can be
// wrong in a way nobody sees. A pan that is off by a pixel is obvious. A drag
// whose y axis is flipped, whose snap rounds the wrong quantity, or whose
// cancel puts the part back at -45.799999999999997 instead of -45.8 looks
// perfectly fine on screen and lands a wrong number in `boards/<stem>.tsx`,
// which is a file a fab eventually reads.
//
// So: no DOM, no React, no `@/` alias (node:test has no bundler to resolve
// one) — the same posture as boardSource.js, for the same reason. The canvas
// keeps the pointer plumbing; everything a test could disagree with is here.
//
// Three things this module is the single owner of:
//
//   1. **The y negation.** Board space is y-up, the screen is y-down, and the
//      canvas draws through one `scale(s, -s)`. Every place that turns pixels
//      into millimetres has to negate y, and it is stated once, here.
//   2. **What a cancelled drag leaves behind.** Nothing. See `cancelMove`.
//   3. **Why a thing under the pointer cannot be dragged.** A refusal without
//      a reason is indistinguishable from a bug — see `refusalForHit`.

import { FINE_STEP_MM, snapDelta } from "./boardSource.js";

/** Pixels of pointer travel before a press counts as a drag rather than a
 *  click. Shared with pan so the two gestures separate at the same distance. */
export const CLICK_SLOP_PX = 4;

/** Copper the router owns. Never a placement, at any zoom, on any board. */
const ROUTER_TYPES = new Set(["pcb_trace", "pcb_via"]);

/**
 * A screen-pixel drag as a board-millimetre delta.
 *
 * The canvas gets the same answer by subtracting two `screenToBoard` calls and
 * that is what it does — this function exists so the negation has one written
 * home and a test can hold the two against each other. `tx`/`ty` cancel in a
 * difference, so only the scale and the sign survive.
 */
export function screenDeltaToMm(view, dxPx, dyPx) {
  const scale = Number(view?.scale) || 1;
  return { dx: (Number(dxPx) || 0) / scale, dy: -(Number(dyPx) || 0) / scale };
}

/**
 * Start carrying a placement.
 *
 * `point` is where the pointer went down in board mm; `origin` is the same
 * press in client pixels, because click-versus-drag is a screen-distance
 * question. 4 mm of slop at 2 px/mm and at 200 px/mm are not the same gesture,
 * and the one the user meant is the pixel one.
 */
export function beginMove(placement, point, origin) {
  if (!placement || !point) return null;
  return {
    placement,
    from: { x: Number(point.x) || 0, y: Number(point.y) || 0 },
    origin: { x: Number(origin?.x) || 0, y: Number(origin?.y) || 0 },
    dx: 0,
    dy: 0,
    moved: false,
  };
}

/**
 * Advance a drag to the pointer's current position.
 *
 * Returns the SAME object when nothing changed, so the canvas can skip the
 * re-render — at 0.5 mm steps most pointermoves land inside the step they
 * already reported, and repainting a 2,000-element board for them is the
 * difference between a drag that tracks the cursor and one that lags it.
 *
 * `fine` (⌥ on the canvas) is the escape hatch from the grid, the way it is in
 * every EDA tool. The grid snaps the *movement*, never the position — see
 * boardSource.snapDelta for why an absolute grid quietly breaks 2.54 mm pitch.
 */
export function trackMove(session, point, { clientX, clientY, step, fine = false } = {}) {
  if (!session || !point) return session;
  const dx = snapDelta((Number(point.x) || 0) - session.from.x, fine ? FINE_STEP_MM : step);
  const dy = snapDelta((Number(point.y) || 0) - session.from.y, fine ? FINE_STEP_MM : step);
  const moved =
    session.moved ||
    Math.abs(Number(clientX) - session.origin.x) > CLICK_SLOP_PX ||
    Math.abs(Number(clientY) - session.origin.y) > CLICK_SLOP_PX;
  if (dx === session.dx && dy === session.dy && moved === session.moved) return session;
  return { ...session, dx, dy, moved };
}

/**
 * Abandon a drag. Returns null, and the null is the whole design.
 *
 * The part is never "put back": there is nothing to put back, because the
 * drag only ever existed as an offset the painter added on top of the board
 * file's own numbers. Drop the offset and the part is drawn from those numbers
 * again — the identical float, not a float that rounds to the same display.
 * The tempting alternative, subtracting the accumulated delta, does not have
 * that property: 0.1 + 0.2 - 0.3 is 5.55e-17, and a placement bound to its
 * anchor at 0.1 µm (boardSource.KEY) does not survive many of those.
 */
export function cancelMove() {
  return null;
}

/**
 * Where a placement's geometry should be painted: the offset the board file
 * already carries (an earlier drag the board has not been rebuilt for) plus
 * whatever this drag has added. With no session — the state after Escape, and
 * the state before any drag — it is the file's offset and nothing else.
 */
export function renderOffset(placement, session) {
  const dx = Number(placement?.offset?.dx) || 0;
  const dy = Number(placement?.offset?.dy) || 0;
  if (!session || session.placement?.id !== placement?.id) return { dx, dy };
  return { dx: dx + session.dx, dy: dy + session.dy };
}

/**
 * What the drop means, in the board's vocabulary rather than the canvas's:
 * which parts moved, and where the board file must now say they are.
 *
 * Absolute, not a delta. The file holds absolute coordinates, so handing a
 * delta upward would make every consumer redo the same addition and give one
 * of them the chance to get it wrong. `refdes` rides along because that is the
 * only name in here a person or an agent recognises — `Ldo3v3[1]` is ours,
 * `["C2","U2","C3"]` is the board's.
 *
 * Null for a press that never travelled, and null for a drag that snapped back
 * to where it started: both of those are clicks, and a click selects.
 */
export function commitMove(session) {
  if (!session || !session.moved) return null;
  if (session.dx === 0 && session.dy === 0) return null;
  const placement = session.placement;
  return {
    placementId: placement.id,
    label: placement.label,
    refdes: Array.isArray(placement.refdes) ? [...placement.refdes] : [],
    x: placement.x + session.dx,
    y: placement.y + session.dy,
    dx: session.dx,
    dy: session.dy,
  };
}

/**
 * The placement a hit belongs to, or null.
 *
 * Element id first: a mounting hole and a silkscreen label are placements with
 * no component behind them, so a component-key lookup alone would miss the two
 * safest edits on the board.
 */
export function placementForHit(placements, hit) {
  if (!hit || !placements) return null;
  const id =
    placements.byElementId?.get(hit.elementId) ||
    (hit.componentKey ? placements.byComponentKey?.get(hit.componentKey) : "");
  return id ? placements.byId?.get(id) || null : null;
}

/** The group the board file itself owns — the one nothing else contains. */
function rootGroupId(index) {
  return (index?.groups || []).find((group) => group.isRoot)?.id || "";
}

/**
 * Why the thing under the pointer cannot be dragged, or null when it can be.
 *
 * This is the half of move mode that makes it honest. 100 of terminal-keyboard's
 * 137 parts have no literal in the board file (they come out of `keyCells()`),
 * and every trace and via on every board belongs to the router. Without a
 * reason the user drags one of those, the board pans, and the only available
 * conclusion is that the app is broken — when in fact the app is refusing to
 * write a number that does not exist in any file.
 *
 * The reasons are ordered by what the user is most likely pointing at, and
 * each names the thing that would have to change instead:
 *
 *   locked    a person decided this on purpose; the bar above undoes that
 *   copper    the router drew it, and the next build will draw it again
 *   block     it moves with a block, and no line in this file places that block
 *   computed  the board file places it in code — a loop, a trig call
 *   unplaced  nothing in the file puts anything here
 *
 * `block` and `computed` both mean "there is no literal to write", and they
 * are split because the unit that would have to gain one differs: the block
 * instance in the first case, the part itself in the second. Measured on
 * terminal-keyboard, the 100 unreachable parts divide 50/50 between them —
 * the diodes `keyCells()` emits directly, and the switches it emits as
 * `<KeySwitch>` block instances. A part sitting inside a block the file DOES
 * place never reaches here: `bindPlacements` maps every component of a bound
 * group to that group's placement, so it is draggable, by its block's frame.
 *
 * @returns {{kind: string, reason: string, box: object|null}|null}
 */
export function refusalForHit(index, placements, hit) {
  if (!hit) return null;
  const placement = placementForHit(placements, hit);
  if (placement) {
    if (!placement.locked) return null;
    return {
      kind: "locked",
      reason: `${placement.label} is locked by hand — unlock it above to move it`,
      box: null,
    };
  }
  if (ROUTER_TYPES.has(String(hit.element?.type || ""))) {
    return {
      kind: "copper",
      reason: "the router drew this copper — move a part and rebuild, and it will be drawn again",
      box: null,
    };
  }
  const component = hit.componentKey ? index?.componentBySourceId?.get(hit.componentKey) || null : null;
  if (component) {
    const name = component.refdes || "this part";
    const root = rootGroupId(index);
    if (root && component.groupId && component.groupId !== root) {
      return {
        kind: "block",
        reason: `${name} moves with its block, and no line in this board file places that block`,
        box: component.pcbBox || null,
      };
    }
    return {
      kind: "computed",
      reason: `${name} has no pcbX/pcbY in the board file — it is placed in code`,
      box: component.pcbBox || null,
    };
  }
  return { kind: "unplaced", reason: "no line in the board file puts this here", box: null };
}
