// Mount a real component into a real DOM and poke it with real events.
//
// Three functions on purpose. A harness with its own vocabulary — `userEvent`,
// `screen`, `getByRole` — is a second thing to learn and the reason people go
// back to asserting regexes over source. Everything here takes a DOM node and
// DOM event fields, so what a test writes is what the browser sees.
//
//   const ui = mount(PcbCanvas, { index, editing: true });
//   pointer(ui.root, "down", { clientX: 100, clientY: 80, button: 0 });
//   key(window, "Escape");
//   ui.root.querySelectorAll("svg path");
//
import React from "react";
import { installDom } from "./dom.js";

// Before react-dom, always: react-dom reads `document` at module scope to
// decide whether it is in a browser, and gets that answer exactly once.
installDom();
const { createRoot } = await import("react-dom/client");

export { setBox } from "./dom.js";

/** `button` → the `buttons` bitmask a browser reports while it is held. */
const BUTTONS_MASK = [1, 4, 2, 8, 16];

/**
 * Render `Component` with `props` into a detached container.
 *
 * Returns the container, the component's own root element, a `set` that
 * re-renders with new props, and `errors` — anything an event handler threw.
 * That last one is not decoration: a `ReferenceError` inside a React event
 * handler reaches the console and nothing else, which is exactly how move mode
 * shipped broken for a day.
 */
export function mount(Component, props = {}) {
  installDom();
  const container = document.createElement("div");
  document.body.appendChild(container);

  const errors = [];
  const onError = (event) => errors.push(event.error || event.message);
  window.addEventListener("error", onError);

  const root = createRoot(container, {
    onUncaughtError: (error) => errors.push(error),
  });
  let current = props;
  act(() => root.render(React.createElement(Component, current)));

  return {
    container,
    /** The component's own outermost element — what a user would point at. */
    get root() {
      return container.firstElementChild;
    },
    errors,
    set(nextProps) {
      current = { ...current, ...nextProps };
      act(() => root.render(React.createElement(Component, current)));
    },
    unmount() {
      window.removeEventListener("error", onError);
      act(() => root.unmount());
      container.remove();
    },
  };
}

/**
 * Dispatch a pointer event. `type` is the short name (`"down"`, `"move"`,
 * `"up"`, `"cancel"`, `"leave"`) or the full one.
 *
 * `buttons` is derived rather than asked for, because the pair a browser sends
 * is not obvious and getting it wrong is silent: a press reports the mask of
 * the button going down, a release reports what is *still* down, which for a
 * one-button drag is nothing. Pass `buttons` to override.
 */
export function pointer(target, type, init = {}) {
  const name = type.startsWith("pointer") ? type : `pointer${type}`;
  const button = init.button ?? 0;
  const held = name === "pointerup" || name === "pointercancel" ? 0 : BUTTONS_MASK[button] ?? 0;
  dispatch(target, new window.PointerEvent(name, {
    bubbles: true,
    cancelable: true,
    composed: true,
    pointerId: 1,
    pointerType: "mouse",
    isPrimary: true,
    button,
    buttons: held,
    ...init,
  }));
}

/** Dispatch the browser's context-menu event — a MouseEvent, not a pointer one. */
export function menu(target, init = {}) {
  dispatch(target, new window.MouseEvent("contextmenu", {
    bubbles: true,
    cancelable: true,
    composed: true,
    button: 2,
    buttons: 0,
    ...init,
  }));
}

/**
 * Click something — a button, a tab, a tool.
 *
 * A separate function from `pointer` because React's `onClick` listens for the
 * browser's `click`, which no sequence of pointer events synthesises here: a
 * real browser derives it from the press/release pair, a headless DOM does
 * not. Dispatching down/up at a button and expecting `onClick` is a test that
 * quietly does nothing.
 */
export function click(target, init = {}) {
  dispatch(target, new window.MouseEvent("click", {
    bubbles: true,
    cancelable: true,
    composed: true,
    button: 0,
    detail: 1,
    ...init,
  }));
}

/**
 * Dispatch a wheel event — a shove of the wheel or two fingers on a trackpad.
 *
 * The fields are forced on after construction because happy-dom's
 * `WheelEvent` constructor drops the `MouseEvent` half of its init: measured
 * 2026-08-16, `new WheelEvent("wheel", {shiftKey: true}).shiftKey` is
 * `undefined`, which would make every modifier-gesture test pass for the wrong
 * reason. Everything the canvases read is set here explicitly.
 */
export function wheel(target, init = {}) {
  const event = new window.WheelEvent("wheel", { bubbles: true, cancelable: true, composed: true });
  for (const [field, fallback] of [
    ["deltaX", 0],
    ["deltaY", 0],
    ["clientX", 0],
    ["clientY", 0],
    ["shiftKey", false],
    ["ctrlKey", false],
    ["metaKey", false],
    ["altKey", false],
  ]) {
    Object.defineProperty(event, field, { value: init[field] ?? fallback, configurable: true });
  }
  dispatch(target, event);
}

/** Dispatch a key event. `type` defaults to `keydown`. */
export function key(target, keyName, init = {}) {
  dispatch(target, new window.KeyboardEvent(init.type || "keydown", {
    bubbles: true,
    cancelable: true,
    composed: true,
    key: keyName,
    ...init,
  }));
}

/**
 * Let queued timers run, then flush whatever React work they scheduled.
 *
 * `act` only drains React's own queue. An overlay's behaviour lives in
 * `setTimeout(…, 0)`: Radix moves roving focus on a timer, and arms its
 * dismiss-on-outside-click listener on another, both so the press that opened
 * a menu cannot immediately close it. Without a turn of the macrotask queue
 * every arrow key and every click-away silently does nothing — a test that
 * asserts on the state *before* the timer fires proves the opposite of what it
 * claims.
 */
export async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  act(() => {});
}

/**
 * Let effects that wait on a promise settle — a `fetch` for a sheet, a chain of
 * `.then`s. `mount` is synchronous, so a component that loads something is
 * still loading on the line after it; without this the test asserts against the
 * empty first frame and calls it the answer.
 */
export async function flush() {
  await React.act(async () => {});
}

/** Every dispatch is act-wrapped so React has flushed before the next line. */
function dispatch(target, event) {
  act(() => target.dispatchEvent(event));
}

function act(fn) {
  // React 18.3 exposes `act` on React itself; `react-dom/test-utils.act` is
  // the same function behind a deprecation warning.
  React.act(fn);
}
