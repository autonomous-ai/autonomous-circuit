// Tests for the pure helpers in chatInputHelpers.js. The rendered React
// component is exercised indirectly via the chat store integration test
// (chatStore.test.js) — this file pins down the constants and the composer
// pre-fill contract the component depends on.

import assert from "node:assert/strict";
import test from "node:test";

import {
  PLACEHOLDER_PROJECT_NAME,
  PREFILL_CHAT_INPUT_EVENT,
  prefillChatInput,
} from "../chatInputHelpers.js";

test("PLACEHOLDER_PROJECT_NAME is the neutral name a lazily-created project carries", () => {
  // Lazily-created projects are never named by the user; Claude's AI title
  // replaces this in place once available (server-side self-heal).
  assert.equal(PLACEHOLDER_PROJECT_NAME, "New project");
});

test("prefillChatInput dispatches the composer pre-fill event with the text", () => {
  // Minimal window shim: node:test has no DOM. The helper only needs
  // dispatchEvent + CustomEvent.
  const events = [];
  const previousWindow = globalThis.window;
  const previousCustomEvent = globalThis.CustomEvent;
  globalThis.CustomEvent = class {
    constructor(type, init) {
      this.type = type;
      this.detail = init?.detail;
    }
  };
  globalThis.window = {
    dispatchEvent(event) {
      events.push(event);
      return true;
    },
  };
  try {
    prefillChatInput("Shot s1_02 (at 00:14): ");
    assert.equal(events.length, 1);
    assert.equal(events[0].type, PREFILL_CHAT_INPUT_EVENT);
    assert.equal(events[0].detail.text, "Shot s1_02 (at 00:14): ");
  } finally {
    globalThis.window = previousWindow;
    globalThis.CustomEvent = previousCustomEvent;
  }
});

test("prefillChatInput is a no-op without a window (tests, SSR)", () => {
  const previousWindow = globalThis.window;
  // eslint-disable-next-line no-undefined
  globalThis.window = undefined;
  try {
    assert.doesNotThrow(() => prefillChatInput("anything"));
  } finally {
    globalThis.window = previousWindow;
  }
});
