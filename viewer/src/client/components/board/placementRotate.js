// placementRotate — turning a placement, as data.
//
// The rotate command surface. There is exactly one command shape in this app
// for turning a part, and it is the object `commitRotate` returns; the key
// handler, the edit bar and any menu built later all produce that object and
// hand it to `usePlacementEditor.rotate`. Two producers of two shapes is how
// the panel and the keyboard end up meaning slightly different things by the
// same gesture, which is worse than either of them being missing.
//
// Split out of the canvas for the same reason `placementDrag.js` was: this is
// the half of the gesture that can be wrong in a way nobody sees. A turn in the
// wrong direction, a step that accumulates a rounding error, or an angle that
// creeps past 360 all look fine on screen and land a wrong number in
// `boards/<stem>.tsx`, which is a file a fab eventually reads.
//
// No DOM, no React, no `@/` alias — the same posture as boardSource.js, for the
// same reason: `node:test` covers it directly.
//
// Two things this module is the single owner of:
//
//   1. **Which way is which.** `pcbRotation` is counterclockwise-positive
//      (measured — see `describeRotate` in boardSource.js), and Altium's
//      Spacebar turns counterclockwise while Shift+Spacebar turns clockwise
//      (https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors).
//      Those two facts meet here and nowhere else.
//   2. **Why a placement will not turn.** A refusal with no reason is
//      indistinguishable from a bug — see `rotateRefusal`.

import { ROTATION_STEPS, normalizeDeg } from "./boardSource.js";

/**
 * Altium's own default rotation step: "the amount of rotation, in degrees,
 * applied to objects floating on the cursor when the Spacebar is pressed",
 * default 90.
 * https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences?version=22
 */
export const DEFAULT_ROTATION_STEP = ROTATION_STEPS[0];

/** The two directions, spelled the way Altium's shortcut table spells them. */
export const CCW = "ccw";
export const CW = "cw";

/**
 * The angle one step in `direction` from where the board file says the
 * placement is now.
 *
 * Stepping from the file's angle rather than accumulating a running total is
 * deliberate: four taps of Space is a full turn back to where it started, to
 * the byte, and not `359.99999999999994`.
 */
export function rotateTarget(placement, direction, step = DEFAULT_ROTATION_STEP) {
  const from = normalizeDeg(placement?.rotation);
  const size = Number(step);
  const by = Number.isFinite(size) && size > 0 ? size : DEFAULT_ROTATION_STEP;
  return normalizeDeg(direction === CW ? from - by : from + by);
}

/**
 * Why this placement will not turn, or null when it will.
 *
 * Ordered by what the user can act on soonest. A lock is a decision someone
 * made and can unmake from the button a few pixels away; `rotateVia === "no"`
 * is a property of how the board file is written, and the reason names the file
 * rather than the app.
 *
 * @returns {{kind: string, reason: string}|null}
 */
export function rotateRefusal(placement) {
  if (!placement) {
    return { kind: "unbound", reason: "nothing in the board file places this directly, so there is no angle to change" };
  }
  if (placement.locked) {
    return { kind: "locked", reason: `${placement.label} is locked. Unlock it to turn it.` };
  }
  if (placement.rotateVia === "no") {
    return {
      kind: placement.rotateBlock || "no",
      reason: placement.rotateReason || `${placement.label} cannot be turned from this app.`,
    };
  }
  return null;
}

/**
 * What a turn means, in the board's vocabulary rather than the keyboard's.
 *
 * Absolute, not a delta, for the same reason `commitMove` is: the file holds an
 * absolute angle, so handing a delta upward would make every consumer redo the
 * same addition and give one of them the chance to get it wrong.
 *
 * Null when nothing would change — a turn back onto the angle already written
 * is not an edit, exactly as a drag that snaps back to its own start is a
 * click. Null is **not** how a refusal is reported: call `rotateRefusal` for
 * that, and show what it says. A caller that writes nothing and says nothing
 * has discarded the user's action.
 *
 * `confirm` is true for the wrap path, where the edit is four lines of new
 * structure rather than one changed literal. The caller shows
 * `boardSource.wrapPreview` and asks before writing.
 *
 * @param {{id, label, refdes, rotation, rotateVia}} placement
 * @param {number} degrees absolute angle, any real number
 * @returns {{placementId, label, refdes, from, to, via, confirm}|null}
 */
export function commitRotate(placement, degrees) {
  if (!placement) return null;
  const from = normalizeDeg(placement.rotation);
  const to = normalizeDeg(degrees);
  if (to === from) return null;
  return {
    placementId: placement.id,
    label: placement.label,
    refdes: Array.isArray(placement.refdes) ? [...placement.refdes] : [],
    from,
    to,
    via: placement.rotateVia || "no",
    confirm: placement.rotateVia === "wrap",
  };
}

/**
 * The command for one step in `direction` — the shape a key press produces.
 *
 * A convenience over `commitRotate(placement, rotateTarget(…))`, and the one
 * the keymap should call, so that "Space turns it one step" is written down
 * once instead of being re-derived at each call site.
 */
export function commitRotateStep(placement, direction, step = DEFAULT_ROTATION_STEP) {
  return commitRotate(placement, rotateTarget(placement, direction, step));
}
