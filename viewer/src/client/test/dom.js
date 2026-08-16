// A browser on globalThis, for tests that need one.
//
// happy-dom rather than jsdom, measured 2026-08-16 against jsdom 30.0.1:
// jsdom has no `ResizeObserver` and no `Element.setPointerCapture`, and
// PcbCanvas uses both. It returns early from its size effect when
// `ResizeObserver` is undefined — so under jsdom the canvas never learns its
// own width, `fitView` never runs, and no board coordinate is ever exercised —
// and it captures the pointer on press so a drag survives leaving the element.
// Both would have to be hand-written under jsdom, which means testing our
// shims instead of the component. happy-dom ships all three, plus PointerEvent
// with a real `button`/`buttons`.
import vm from "node:vm";

import { Window } from "happy-dom";

// The JavaScript language itself, which happy-dom carries a second copy of.
//
// happy-dom runs in its own `vm` context, so its `window.JSON`, `window.Object`
// and `window.Map` are a different realm's. Copying those onto `globalThis`
// makes `JSON.parse` return objects whose prototype is not this realm's
// `Object.prototype`, and `assert.deepStrictEqual` then fails with "same
// structure but not reference-equal" against an object literal written in the
// test file. `instanceof` disagrees the same way. Nothing about a DOM needs a
// second copy of `Map`, so the language stays Node's.
//
// Derived rather than listed: a bare `vm` context has exactly the ECMAScript
// intrinsics and nothing else — no Node globals, no DOM — so this set cannot
// drift out of date, and cannot accidentally hold back `Event` or `Node`.
const ES_INTRINSICS = new Set(vm.runInNewContext("Object.getOwnPropertyNames(globalThis)"));

// Names Node owns. Replacing them with the browser's version breaks the test
// runner itself (its own timers, its own fetch, its own AbortSignal), and
// nothing in the viewer needs the DOM's copy.
const KEEP_NODE = new Set([
  "AbortController",
  "AbortSignal",
  "Blob",
  "Buffer",
  "ByteLengthQueuingStrategy",
  "CompressionStream",
  "CountQueuingStrategy",
  "DecompressionStream",
  // Node 26 exposes DOMException through a lazy native getter, and merely
  // redefining it crashes the process with an isolate assertion. Nothing here
  // needs the browser's copy.
  "DOMException",
  "File",
  "FormData",
  "Headers",
  "ReadableStream",
  "Request",
  "Response",
  "TextDecoder",
  "TextEncoder",
  "TransformStream",
  "URL",
  "URLSearchParams",
  "WritableStream",
  "clearImmediate",
  "clearInterval",
  "clearTimeout",
  "console",
  "crypto",
  "fetch",
  "global",
  "globalThis",
  "performance",
  "process",
  "queueMicrotask",
  "setImmediate",
  "setInterval",
  "setTimeout",
  "structuredClone",
]);

let installed = null;

/**
 * Put a DOM on globalThis and return its window. Idempotent.
 *
 * Must run before `react-dom` is first imported: react-dom decides whether it
 * is in a browser at module scope (`canUseDOM`), and a react-dom that loaded
 * without a `document` renders nothing and reports no error.
 */
export function installDom() {
  if (installed) return installed;

  const window = new Window({ url: "http://localhost/", width: 1200, height: 800 });
  for (const key of Object.getOwnPropertyNames(window)) {
    if (KEEP_NODE.has(key) || ES_INTRINSICS.has(key)) continue;
    if (key.startsWith("_")) continue;
    const descriptor = Object.getOwnPropertyDescriptor(window, key);
    if (!descriptor) continue;
    Object.defineProperty(globalThis, key, { ...descriptor, configurable: true });
  }
  // Assignment is not enough for these: Node defines `navigator` as a
  // getter-only global, so `globalThis.navigator = …` throws.
  for (const [name, value] of [
    ["window", window],
    ["self", window],
    ["document", window.document],
    ["navigator", window.navigator],
  ]) {
    Object.defineProperty(globalThis, name, { value, writable: true, configurable: true });
  }

  // React 18 refuses to batch test updates unless it is told it is in a test.
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;

  installLayout(window);
  installed = window;
  return window;
}

/**
 * Give every element a box.
 *
 * No headless DOM does layout, so `getBoundingClientRect()` and `clientWidth`
 * are zero for everything — and a canvas that fits a board to its viewport
 * with a zero-width viewport draws nothing and reports no coordinates. The
 * default here is the honest one for a full-bleed pane: the element fills the
 * window, at the origin. `setBox` overrides it per element for anything else.
 */
function installLayout(window) {
  const boxes = new WeakMap();
  const { Element, HTMLElement } = window;

  const boxOf = (element) =>
    boxes.get(element) || { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight };

  Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
    const { x, y, width, height } = boxOf(this);
    return { x, y, width, height, top: y, left: x, right: x + width, bottom: y + height, toJSON: () => ({}) };
  };
  for (const [prop, side] of [["clientWidth", "width"], ["clientHeight", "height"]]) {
    Object.defineProperty(HTMLElement.prototype, prop, {
      configurable: true,
      get() {
        return Math.round(boxOf(this)[side]);
      },
    });
  }

  installLayout.setBox = (element, box) => {
    boxes.set(element, { x: 0, y: 0, width: 0, height: 0, ...box });
  };
}

/** Pin one element's box: `setBox(pane, { x: 40, y: 0, width: 600, height: 400 })`. */
export function setBox(element, box) {
  installDom();
  installLayout.setBox(element, box);
}
