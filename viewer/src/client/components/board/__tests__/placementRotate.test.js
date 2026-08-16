import assert from "node:assert/strict";
import test from "node:test";

import {
  CCW,
  CW,
  DEFAULT_ROTATION_STEP,
  commitRotate,
  commitRotateStep,
  rotateRefusal,
  rotateTarget,
} from "../placementRotate.js";

const placement = (over = {}) => ({
  id: "Ldo3v3[1]",
  label: "U2 +2",
  refdes: ["C2", "C3", "U2"],
  rotation: 0,
  rotateVia: "prop",
  rotateBlock: "",
  rotateReason: "",
  locked: false,
  ...over,
});

// --- direction and step ------------------------------------------------------

test("Space turns counterclockwise and Shift+Space clockwise, at Altium's default step", () => {
  assert.equal(DEFAULT_ROTATION_STEP, 90);
  assert.equal(rotateTarget(placement(), CCW), 90);
  assert.equal(rotateTarget(placement(), CW), 270);
});

test("a step is taken from the file's angle, so four taps return to the byte it started on", () => {
  let angle = 337.5;
  for (let i = 0; i < 4; i += 1) angle = rotateTarget(placement({ rotation: angle }), CCW);
  assert.equal(angle, 337.5);
});

test("the step is honoured, and nonsense falls back rather than writing NaN", () => {
  assert.equal(rotateTarget(placement(), CCW, 15), 15);
  assert.equal(rotateTarget(placement({ rotation: 15 }), CW, 45), 330);
  for (const bad of [0, -90, NaN, null, "wide"]) {
    assert.equal(rotateTarget(placement(), CCW, bad), 90, String(bad));
  }
});

// --- the command shape -------------------------------------------------------

test("the command is absolute, names what moves, and says whether it needs a confirm", () => {
  assert.deepEqual(commitRotateStep(placement({ rotation: 180 }), CCW), {
    placementId: "Ldo3v3[1]",
    label: "U2 +2",
    refdes: ["C2", "C3", "U2"],
    from: 180,
    to: 270,
    via: "prop",
    confirm: false,
  });
});

test("a wrap asks for a confirm; a prop edit does not", () => {
  assert.equal(commitRotateStep(placement({ rotateVia: "wrap" }), CCW).confirm, true);
  assert.equal(commitRotateStep(placement({ rotateVia: "prop" }), CCW).confirm, false);
});

test("a turn back onto the angle already written is not a command", () => {
  assert.equal(commitRotate(placement({ rotation: 90 }), 90), null);
  assert.equal(commitRotate(placement({ rotation: 90 }), 450), null);
  assert.equal(commitRotate(placement({ rotation: 0 }), -0), null);
  assert.equal(commitRotate(null, 90), null);
});

test("the command carries a copy of refdes, not the placement's own array", () => {
  const source = placement();
  const command = commitRotate(source, 90);
  command.refdes.push("X9");
  assert.deepEqual(source.refdes, ["C2", "C3", "U2"]);
});

test("a refused placement still produces a command, so the caller has to answer for it", () => {
  // `null` means "nothing changed", never "this was refused". A caller that
  // treated the two the same would drop the user's keystroke in silence, which
  // is the failure this whole path exists to prevent.
  const command = commitRotateStep(placement({ rotateVia: "no", rotateBlock: "drill" }), CCW);
  assert.ok(command);
  assert.equal(command.via, "no");
});

// --- refusals ----------------------------------------------------------------

test("nothing selected refuses with a reason rather than nothing at all", () => {
  const refusal = rotateRefusal(null);
  assert.equal(refusal.kind, "unbound");
  assert.ok(refusal.reason.length > 0);
});

test("a lock is the first refusal, because it is the one the user can undo right there", () => {
  const refusal = rotateRefusal(placement({ locked: true, rotateVia: "no", rotateBlock: "drill", rotateReason: "H1 is a round drill." }));
  assert.equal(refusal.kind, "locked");
  assert.equal(refusal.reason, "U2 +2 is locked. Unlock it to turn it.");
});

test("an unwritable angle refuses with the parser's own sentence, kind and all", () => {
  const refusal = rotateRefusal(
    placement({ rotateVia: "no", rotateBlock: "expression", rotateReason: "U2's angle is written as an expression." }),
  );
  assert.deepEqual(refusal, { kind: "expression", reason: "U2's angle is written as an expression." });
});

test("a placement that can turn refuses nothing", () => {
  assert.equal(rotateRefusal(placement()), null);
  assert.equal(rotateRefusal(placement({ rotateVia: "wrap" })), null);
});
