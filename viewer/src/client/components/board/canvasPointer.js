// canvasPointer — which button means what, and which key the canvas may take.
//
// Split out of PcbCanvas and SchematicCanvas for the reason placementDrag.js
// gives: no DOM, no React, no `@/` alias, so `node:test` can hold the decisions
// against each other without a browser. What lives here is only the part that
// can be *wrong* rather than merely ugly — which button pans, what a press that
// never travelled means, and whether a keystroke belongs to the board or to the
// text field the user is typing in.
//
// The rule this module exists to enforce, from the audit:
//
//   **A key or a button that does something ELSE outranks one that does
//   nothing.** A no-op is legible — the EE concludes "not built yet". A misfire
//   is a broken promise — the EE concludes "this is not a real EDA tool".
//
// Both defects it was written for were misfires: button 2 was rejected before
// pan could see it, and Spacebar zeroed the delta origin (KiCad's gesture)
// where an EE's hands expect a rotation — including while they were typing a
// space into the chat composer.

import { CLICK_SLOP_PX } from "./placementDrag.js";

/** `PointerEvent.button` values. 1 (middle/wheel) is bound nowhere, on purpose. */
export const PRIMARY_BUTTON = 0;
export const SECONDARY_BUTTON = 2;

/** Only these two begin a drag. Altium pans on both, and so do we.
 *  Right-Click, Hold&Drag: "Display the slider (panning) hand cursor then drag
 *  to move your view of the design space."
 *  https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors */
export function isDragButton(button) {
  return button === PRIMARY_BUTTON || button === SECONDARY_BUTTON;
}

/** Has this press travelled far enough to be a drag rather than a click?
 *  One threshold for both buttons, so left and right agree on what a click is. */
export function draggedPast(dxPx, dyPx, slop = CLICK_SLOP_PX) {
  return Math.abs(Number(dxPx) || 0) > slop || Math.abs(Number(dyPx) || 0) > slop;
}

/**
 * The camera after a pan of `dxPx`/`dyPx` screen pixels.
 *
 * Stated once so the two canvases cannot drift, and so a test can prove the
 * thing the audit asked for directly: a right-drag pans by exactly the delta a
 * left-drag would. Pixels, not millimetres — panning is a screen gesture and
 * the scale is untouched by it.
 */
export function panView(origin, dxPx, dyPx) {
  return {
    ...origin,
    tx: (Number(origin?.tx) || 0) + (Number(dxPx) || 0),
    ty: (Number(origin?.ty) || 0) + (Number(dyPx) || 0),
  };
}

/**
 * Hand the keyboard to the board when someone presses on it.
 *
 * The chat composer takes focus when the app loads (`ChatInput.jsx` `autoFocus`)
 * and `isTypingTarget` — correctly — refuses to take any key out of a text box,
 * or typing "make the LED green" would toggle move mode, switch tabs and flip
 * the units mid-sentence. The cost of those two true things together is that
 * **every board key is dead until the composer loses focus**, and nothing on
 * screen says so: an engineer opens the app, presses `E`, and the tool does
 * nothing at all. Reported from the outside within a minute of first use.
 *
 * A press on the board is an unambiguous "I am working on the board now", which
 * is exactly how Altium and KiCad decide where the keyboard points. Only a
 * typing target is blurred — clicking the canvas must not steal focus from a
 * dialog or a menu that is deliberately holding it.
 *
 * @returns {boolean} whether focus was taken off a text field.
 */
export function takeKeyboardFromTyping(activeElement) {
  if (!activeElement || !isTypingTarget(activeElement)) return false;
  if (typeof activeElement.blur !== "function") return false;
  activeElement.blur();
  return true;
}

/**
 * What one wheel event means: zoom, or pan sideways.
 *
 * Plain wheel zooms. That is a deliberate deviation — Altium's plain wheel
 * scrolls the sheet vertically and `Shift`+wheel scrolls it horizontally
 * (shortcut-keys/pcb-editors) — and it is the deviation every browser-based
 * canvas has already trained into the same hands, so it stays.
 *
 * `Shift`+wheel is Altium's, and it was dead: the wheel handler never read
 * `shiftKey`, so on a board wider than the pane the one gesture an EE has for
 * "look further along" did nothing at all, several times per session. It pans
 * horizontally, by the dominant axis, so a trackpad that reports the shove on
 * `deltaX` behaves the same as a wheel that reports it on `deltaY`.
 *
 * Returned as data rather than applied, for the reason the rest of this module
 * exists: which gesture means what is the part that can be wrong invisibly.
 *
 * @returns {{kind: "zoom", factor: number}|{kind: "pan", dx: number, dy: number}}
 */
export function wheelAction(event, { zoomRate = 0.0016 } = {}) {
  const dy = Number(event?.deltaY) || 0;
  const dx = Number(event?.deltaX) || 0;
  if (event?.shiftKey) {
    // One axis, and the screen moves the way the content should: a shove that
    // scrolls a page down moves the board left, which reads as travelling
    // right along it.
    const amount = Math.abs(dx) > Math.abs(dy) ? dx : dy;
    return { kind: "pan", dx: -amount, dy: 0 };
  }
  return { kind: "zoom", factor: Math.exp(-dy * zoomRate) };
}

/**
 * Advance a pan, or refuse to.
 *
 * Returns null while the press is still inside the slop — a press that has not
 * travelled is a click, and a click must not move the camera by a pixel. That
 * is what makes "a right-click without movement does not pan" true rather than
 * approximately true. Mutates `drag.moved` once the slop is crossed, the same
 * latch both canvases already kept by hand.
 */
export function panTo(drag, clientX, clientY) {
  if (!drag || drag.mode !== "pan") return null;
  const dx = (Number(clientX) || 0) - drag.startX;
  const dy = (Number(clientY) || 0) - drag.startY;
  if (!drag.moved && !draggedPast(dx, dy)) return null;
  drag.moved = true;
  return panView(drag.origin, dx, dy);
}

/**
 * What a press starts.
 *
 *   ignore   not a button we bind
 *   cancel   Altium: right-click "If currently within an interactive command,
 *            will escape from the current operation." The press is consumed —
 *            no pan begins and no menu follows it.
 *            https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors
 *   pan      both buttons, and the right button *only* ever this: measure and
 *            move are left-button commands, so a right press over an armed
 *            ruler or a draggable part still pans.
 *   measure  left button with the ruler armed
 *   move     left button over a placement the board file can address
 */
export function pointerPressAction({
  button = PRIMARY_BUTTON,
  liveCommand = false,
  measuring = false,
  canGrab = false,
} = {}) {
  if (!isDragButton(button)) return "ignore";
  if (button === SECONDARY_BUTTON) return liveCommand ? "cancel" : "pan";
  if (measuring) return "measure";
  if (canGrab) return "move";
  return "pan";
}

/**
 * Should a `contextmenu` event abandon the drag that is running?
 *
 * This is the *reachable* half of `pointerPressAction`'s `"cancel"`. A mouse
 * does not deliver a second `pointerdown` while a button is already held —
 * "pointerdown events fire only upon the first button press; subsequent button
 * presses don't fire pointerdown events"
 * (https://developer.mozilla.org/en-US/docs/Web/API/Element/pointerdown_event)
 * — so the one gesture the cancel branch was written for, an EE mid-drag who
 * right-clicks to get out, never reached it and the move committed on release
 * instead. `contextmenu` does fire: on press on macOS, on release on Windows.
 * Either way the user has asked for the right button and Altium's answer is
 * "escape from the current operation".
 *
 * Only a **left-button** command is escapable, and that is the whole subtlety.
 * On macOS the right press that *starts a pan* fires `contextmenu` immediately
 * after its own `pointerdown`, so a naive "cancel whatever is live" would kill
 * every right-drag pan on the first frame. A pan is not an interactive command
 * being escaped — it is the gesture itself.
 */
export function escapeLiveCommand(drag) {
  if (!drag) return false;
  if (drag.button !== PRIMARY_BUTTON) return false;
  return drag.mode === "move" || drag.mode === "measure";
}

/**
 * What a release means, given the drag it ends.
 *
 *   none     nothing more to do — the pan/measure already happened
 *   drop     commit the move
 *   select   a left press that never travelled: a click, and a click selects
 *   menu     a right press that never travelled: Altium's context menu
 *
 * The menu is decided here, on release, rather than on the browser's own
 * `contextmenu` event, because macOS fires that event on *press* — before the
 * pointer has had a chance to travel. Deciding there would make "hold and drag
 * to pan" impossible to distinguish from "click for a menu", which is the one
 * distinction Altium's right button is built on.
 */
export function pointerReleaseAction(drag) {
  if (!drag) return "none";
  if (drag.mode === "measure") return "none";
  if (drag.mode === "move") return "drop";
  if (drag.moved) return "none";
  return drag.button === SECONDARY_BUTTON ? "menu" : "select";
}

/**
 * Is the keyboard currently pointed at text rather than at the board?
 *
 * `<textarea>` is the whole reason this exists: the chat composer is one
 * (`chat/ChatInput.jsx`), and the guard it replaces tested `tagName !== "INPUT"`
 * only — so typing a space into chat with the pointer over the PCB silently
 * moved the delta origin and every Δ in the HUD changed with nothing to say why.
 *
 * Kept local rather than imported from `BoardWorkspace`: that file holds the
 * same predicate and is being rewritten by another workstream, and a canvas
 * that cannot decide this on its own has to wait for it.
 */
export function isTypingTarget(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = String(target.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select";
}

/**
 * What a keystroke over a canvas means.
 *
 * Spacebar, per Altium: "Rotates the object being placed/moved
 * counterclockwise… Shift+Spacebar: Rotates the object being placed/moved
 * clockwise."
 * https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors
 *
 * Three decisions in here, each stated because none of them is obvious:
 *
 *   1. **Space never zeroes the delta origin.** `Insert` does, which is
 *      Altium's own key for it — "Insert: Resets the Delta Origin point for the
 *      Heads Up Display feature to 0,0" (same page). Space costs nothing to
 *      hand over, and holding both meanings on one key is what made Shift+Space
 *      unreachable.
 *   2. **Outside a drag, Space does nothing.** Altium's wording is "the object
 *      being placed/moved", and a drag is the only object-on-the-cursor state
 *      this canvas has. What Altium's Space does with an object merely selected
 *      is **unverified** — no page states it — so we do not invent a meaning
 *      for it here. (`docs/architecture/ide-altium-parity.md` proposes
 *      Space-on-selection as a deliberate deviation; it needs a rotation write
 *      path this canvas does not own.)
 *   3. **Auto-repeat is ignored.** Whether Altium repeats a held Space is
 *      **unverified**; one press, one step is the safer half of the guess,
 *      because the other one spins a part while a key sticks.
 *
 * `Escape` is resolved before the typing guard so a live drag can always be
 * abandoned — that is today's behaviour and losing it would strand a user
 * mid-move.
 */
export function canvasKeyAction(event, { dragging = false, hasCursor = false } = {}) {
  const key = event?.key;
  if (!key) return null;
  if (key === "Escape") return dragging ? { type: "cancel-drag" } : null;
  if (isTypingTarget(event.target)) return null;
  if (key === "Insert") return hasCursor ? { type: "delta-origin" } : null;
  // "Spacebar" is the legacy IE/Edge spelling; both still reach some WebViews.
  if (key === " " || key === "Spacebar") {
    if (event.repeat) return null;
    if (!dragging) return null;
    return { type: "rotate", direction: event.shiftKey ? "cw" : "ccw" };
  }
  return null;
}
