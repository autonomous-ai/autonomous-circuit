// canvasPointer — the pointer and key arithmetic both canvases run on.
//
// Everything here is a misfire regression. Each of these behaviours was, until
// this file existed, either rejected before it ran (button 2) or bound to
// something else (Spacebar), and the audit's rule is that a control which does
// something ELSE is worse than one that does nothing.

import assert from "node:assert/strict";
import test from "node:test";

import {
  PRIMARY_BUTTON,
  SECONDARY_BUTTON,
  canvasKeyAction,
  draggedPast,
  escapeLiveCommand,
  isDragButton,
  isTypingTarget,
  panTo,
  panView,
  pointerPressAction,
  pointerReleaseAction,
  wheelAction,
} from "../canvasPointer.js";

const VIEW = { scale: 8, tx: 120, ty: -40 };

/** A press, in the shape the canvases put on `dragRef.current`. */
function press(button, { mode = "pan", startX = 200, startY = 150 } = {}) {
  return { mode, button, startX, startY, origin: { ...VIEW }, moved: false };
}

// --- buttons

test("both buttons drag; the wheel button is bound to nothing", () => {
  assert.equal(isDragButton(PRIMARY_BUTTON), true);
  assert.equal(isDragButton(SECONDARY_BUTTON), true);
  assert.equal(isDragButton(1), false);
  assert.equal(isDragButton(3), false);
});

// --- pan

test("a right-drag pans by exactly the delta a left-drag would", () => {
  const left = press(PRIMARY_BUTTON);
  const right = press(SECONDARY_BUTTON);
  const leftView = panTo(left, 260, 90);
  const rightView = panTo(right, 260, 90);
  assert.deepEqual(leftView, rightView);
  // And that delta is the press travel, in screen pixels, on top of the camera
  // the press started from.
  assert.deepEqual(leftView, { scale: 8, tx: 120 + 60, ty: -40 + -60 });
  assert.deepEqual(leftView, panView(VIEW, 60, -60));
  assert.equal(left.moved, true);
  assert.equal(right.moved, true);
});

test("a right-click without movement does not pan", () => {
  const drag = press(SECONDARY_BUTTON);
  // 4 px is the slop, and the comparison is strict — 4 is still a click, the
  // same boundary the left button uses.
  assert.equal(draggedPast(4, 4), false);
  assert.equal(panTo(drag, 204, 154), null);
  assert.equal(drag.moved, false);
  assert.equal(pointerReleaseAction(drag), "menu");
  // One more pixel and it is a pan, and then it is not a menu.
  assert.equal(panTo(drag, 205, 154) !== null, true);
  assert.equal(drag.moved, true);
  assert.equal(pointerReleaseAction(drag), "none");
});

test("a left-click without movement does not pan either — it selects", () => {
  const drag = press(PRIMARY_BUTTON);
  assert.equal(panTo(drag, 202, 149), null);
  assert.equal(pointerReleaseAction(drag), "select");
});

test("once a press has travelled it keeps panning back inside the slop", () => {
  const drag = press(PRIMARY_BUTTON);
  panTo(drag, 260, 150);
  assert.deepEqual(panTo(drag, 201, 150), panView(VIEW, 1, 0));
});

test("panTo refuses anything that is not a pan drag", () => {
  assert.equal(panTo(null, 300, 300), null);
  assert.equal(panTo({ mode: "move", session: {} }, 300, 300), null);
  assert.equal(panTo({ mode: "measure", startX: 0, startY: 0 }, 300, 300), null);
});

// --- press routing

test("the right button only ever pans — measure and move are left-button commands", () => {
  assert.equal(pointerPressAction({ button: SECONDARY_BUTTON, measuring: true }), "pan");
  assert.equal(pointerPressAction({ button: SECONDARY_BUTTON, canGrab: true }), "pan");
  assert.equal(pointerPressAction({ button: PRIMARY_BUTTON, measuring: true }), "measure");
  assert.equal(pointerPressAction({ button: PRIMARY_BUTTON, canGrab: true }), "move");
  assert.equal(pointerPressAction({ button: PRIMARY_BUTTON }), "pan");
  // Measure wins over move: turning on the ruler turns off move mode.
  assert.equal(pointerPressAction({ button: PRIMARY_BUTTON, measuring: true, canGrab: true }), "measure");
});

test("a right press inside a live command cancels it instead of starting anything", () => {
  assert.equal(pointerPressAction({ button: SECONDARY_BUTTON, liveCommand: true }), "cancel");
  // The left button does not — a second left press cannot happen without a
  // release, and treating it as a cancel would break pointer capture.
  assert.equal(pointerPressAction({ button: PRIMARY_BUTTON, liveCommand: true }), "pan");
});

// The branch above is correct and, for a mouse, unreachable: "pointerdown
// events fire only upon the first button press; subsequent button presses
// don't fire pointerdown events"
// (https://developer.mozilla.org/en-US/docs/Web/API/Element/pointerdown_event).
// So the gesture it was written for — mid-drag, right-click to get out — went
// through no handler at all and the move committed on release. `contextmenu`
// is the event that does fire, and this is what the canvas asks it.
test("contextmenu escapes a left-button command and never a pan", () => {
  assert.equal(escapeLiveCommand({ mode: "move", button: PRIMARY_BUTTON }), true);
  assert.equal(escapeLiveCommand({ mode: "measure", button: PRIMARY_BUTTON }), true);
  // The one that would break right-drag pan on macOS, where `contextmenu`
  // fires on PRESS — immediately after the pan's own pointerdown.
  assert.equal(escapeLiveCommand({ mode: "pan", button: SECONDARY_BUTTON, moved: false }), false);
  assert.equal(escapeLiveCommand({ mode: "pan", button: SECONDARY_BUTTON, moved: true }), false);
  // A left-button pan is the empty canvas: nothing to escape from.
  assert.equal(escapeLiveCommand({ mode: "pan", button: PRIMARY_BUTTON }), false);
  assert.equal(escapeLiveCommand(null), false);
  assert.equal(escapeLiveCommand(undefined), false);
});

test("the wheel button starts nothing at all", () => {
  assert.equal(pointerPressAction({ button: 1, measuring: true, canGrab: true }), "ignore");
});

// --- release routing

test("a release means one of exactly four things", () => {
  assert.equal(pointerReleaseAction(null), "none");
  assert.equal(pointerReleaseAction({ mode: "measure", button: PRIMARY_BUTTON, moved: true }), "none");
  assert.equal(pointerReleaseAction({ mode: "move", button: PRIMARY_BUTTON, moved: true }), "drop");
  assert.equal(pointerReleaseAction({ mode: "pan", button: PRIMARY_BUTTON, moved: false }), "select");
  assert.equal(pointerReleaseAction({ mode: "pan", button: SECONDARY_BUTTON, moved: false }), "menu");
});

test("a pan never selects and never opens a menu", () => {
  assert.equal(pointerReleaseAction({ mode: "pan", button: PRIMARY_BUTTON, moved: true }), "none");
  assert.equal(pointerReleaseAction({ mode: "pan", button: SECONDARY_BUTTON, moved: true }), "none");
});

// --- keys

test("Space during a drag does not move the delta origin", () => {
  const action = canvasKeyAction({ key: " " }, { dragging: true, hasCursor: true });
  assert.deepEqual(action, { type: "rotate", direction: "ccw" });
});

test("no state whatsoever makes Space zero the delta origin", () => {
  for (const dragging of [true, false]) {
    for (const hasCursor of [true, false]) {
      for (const shiftKey of [true, false]) {
        for (const key of [" ", "Spacebar"]) {
          const action = canvasKeyAction({ key, shiftKey }, { dragging, hasCursor });
          assert.notEqual(action?.type, "delta-origin", `${key} shift=${shiftKey} dragging=${dragging}`);
        }
      }
    }
  }
});

test("Shift+Space turns the other way — Shift is read, so it is no longer shadowed", () => {
  assert.deepEqual(
    canvasKeyAction({ key: " ", shiftKey: true }, { dragging: true }),
    { type: "rotate", direction: "cw" },
  );
});

test("Space outside a drag does nothing — Altium's Space acts on the object on the cursor", () => {
  assert.equal(canvasKeyAction({ key: " " }, { dragging: false, hasCursor: true }), null);
});

test("a held Space is one turn, not a spin", () => {
  assert.equal(canvasKeyAction({ key: " ", repeat: true }, { dragging: true }), null);
});

test("a space typed into the chat composer never reaches the board", () => {
  const state = { dragging: true, hasCursor: true };
  assert.equal(canvasKeyAction({ key: " ", target: { tagName: "TEXTAREA" } }, state), null);
  assert.equal(canvasKeyAction({ key: " ", target: { tagName: "INPUT" } }, state), null);
  assert.equal(canvasKeyAction({ key: " ", target: { tagName: "SELECT" } }, state), null);
  assert.equal(canvasKeyAction({ key: " ", target: { isContentEditable: true } }, state), null);
  // Lowercase tag names arrive from some serializers; the guard is not picky.
  assert.equal(canvasKeyAction({ key: " ", target: { tagName: "textarea" } }, state), null);
});

test("isTypingTarget says no to the board itself", () => {
  assert.equal(isTypingTarget(null), false);
  assert.equal(isTypingTarget({ tagName: "DIV" }), false);
  assert.equal(isTypingTarget({ tagName: "BODY" }), false);
  assert.equal(isTypingTarget({ tagName: "TEXTAREA" }), true);
});

test("Insert keeps the delta origin — Altium's own key for it", () => {
  assert.deepEqual(canvasKeyAction({ key: "Insert" }, { hasCursor: true }), { type: "delta-origin" });
  assert.deepEqual(canvasKeyAction({ key: "Insert" }, { dragging: true, hasCursor: true }), { type: "delta-origin" });
  // No cursor means no point to zero at.
  assert.equal(canvasKeyAction({ key: "Insert" }, { hasCursor: false }), null);
  // And it is off while typing, for the same reason Space is.
  assert.equal(canvasKeyAction({ key: "Insert", target: { tagName: "INPUT" } }, { hasCursor: true }), null);
});

test("Escape abandons a drag and is answered even with focus in a text field", () => {
  assert.deepEqual(canvasKeyAction({ key: "Escape" }, { dragging: true }), { type: "cancel-drag" });
  assert.deepEqual(
    canvasKeyAction({ key: "Escape", target: { tagName: "TEXTAREA" } }, { dragging: true }),
    { type: "cancel-drag" },
  );
  // With nothing being dragged, Escape belongs to the workspace, not here.
  assert.equal(canvasKeyAction({ key: "Escape" }, { dragging: false }), null);
});

test("keys the canvas does not own are left alone", () => {
  for (const key of ["l", "L", "m", "q", "2", "3", "f", "Enter", "Tab"]) {
    assert.equal(canvasKeyAction({ key }, { dragging: true, hasCursor: true }), null, key);
  }
  assert.equal(canvasKeyAction({}, { dragging: true }), null);
});

test("Shift+wheel travels sideways; a plain wheel still zooms", () => {
  // The gesture was dead: the wheel handler never read `shiftKey`, so on a
  // board wider than its pane the one thing an EE does to look further along
  // did nothing, several times a session. Altium's own binding
  // (shortcut-keys/pcb-editors: Shift+wheel scrolls horizontally).
  assert.deepEqual(wheelAction({ deltaY: 120, shiftKey: true }), { kind: "pan", dx: -120, dy: 0 });
  assert.deepEqual(wheelAction({ deltaY: -120, shiftKey: true }), { kind: "pan", dx: 120, dy: 0 });

  // A trackpad reports the same shove on the other axis; the dominant one wins
  // so both hardware kinds feel the same.
  assert.deepEqual(wheelAction({ deltaX: 40, deltaY: 5, shiftKey: true }), { kind: "pan", dx: -40, dy: 0 });

  // Plain wheel zooms — a deliberate deviation from Altium (whose plain wheel
  // scrolls) and the one every browser canvas has already trained.
  const zoomIn = wheelAction({ deltaY: -100 });
  assert.equal(zoomIn.kind, "zoom");
  assert.ok(zoomIn.factor > 1, `zoom in gave ${zoomIn.factor}`);
  assert.ok(wheelAction({ deltaY: 100 }).factor < 1);
  // Nothing moved, nothing happens: a wheel event with no delta is a no-op
  // zoom, not a jump.
  assert.equal(wheelAction({}).factor, 1);
});
