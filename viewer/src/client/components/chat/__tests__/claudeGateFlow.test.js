// Integration: the chat store must not start a turn while the `claude` CLI is
// missing — the send parks behind the setup dialog (manual install
// instructions in v1; no in-app installer), and fires automatically once a
// re-check finds the CLI. Also covers the fallback: a driver-reported
// CLAUDE_NOT_INSTALLED chat error opens the same dialog.

import assert from "node:assert/strict";
import test from "node:test";

import {
  __resetEffortForTesting,
  __setTransportForTesting,
  attachChatEventStream,
  detachChatEventStream,
  getChatState,
  resetChatStore,
  setProject,
  startTurn,
} from "../../../store/chat.js";
import {
  getClaudeSetupState,
  recheckClaude,
  resetClaudeSetupStore,
} from "../../../store/claudeSetup.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// The effort directive is appended to every sent message; pin it to the level
// that says nothing so this file keeps asserting the gate, not the prompt.
__resetEffortForTesting("medium");

function makeMockEvents() {
  const handlers = new Map();
  return {
    bus: {
      subscribe(kind, handler) {
        if (!handlers.has(kind)) handlers.set(kind, new Set());
        handlers.get(kind).add(handler);
        return () => handlers.get(kind).delete(handler);
      },
    },
    emit(kind, payload) {
      for (const handler of handlers.get(kind) || []) {
        handler(payload);
      }
    },
  };
}

test("startTurn parks behind the setup dialog when the CLI is missing, then auto-sends after a re-check", async (t) => {
  resetChatStore();
  resetClaudeSetupStore();
  setProject("proj-1");

  const turns = [];
  let found = false;
  const mock = {
    async app_prereq_check() {
      return { claudeCli: { found } };
    },
    async chat_start_turn(req) {
      turns.push(req);
      return { turnId: "turn-9" };
    },
  };
  const restore = __setTransportForTesting(mock);
  t.after(() => {
    restore();
    resetChatStore();
    resetClaudeSetupStore();
  });

  const send = startTurn("write me a revenge drama");
  await tick();

  assert.equal(turns.length, 0, "no inference reaches the backend without the CLI");
  assert.equal(getClaudeSetupState().open, true, "setup dialog opened instead");
  assert.equal(getClaudeSetupState().phase, "instructions", "manual instructions — no installer");

  // The user installs Claude Code in a terminal, then clicks Re-check.
  found = true;
  await recheckClaude(mock);
  const response = await send;

  assert.deepEqual(turns, [{ projectId: "proj-1", userMessage: "write me a revenge drama" }]);
  assert.equal(response?.turnId, "turn-9", "the original send resolves normally");
  const state = getChatState();
  assert.equal(state.history.at(-1)?.userText, "write me a revenge drama");
});

test("startTurn proceeds untouched when the CLI is present", async (t) => {
  resetChatStore();
  resetClaudeSetupStore();
  setProject("proj-1");

  const turns = [];
  const restore = __setTransportForTesting({
    async app_prereq_check() {
      return { claudeCli: { found: true, version: "2.1.0" } };
    },
    async chat_start_turn(req) {
      turns.push(req);
      return { turnId: "turn-1" };
    },
  });
  t.after(() => {
    restore();
    resetChatStore();
    resetClaudeSetupStore();
  });

  const response = await startTurn("hello");
  assert.equal(response?.turnId, "turn-1");
  assert.equal(turns.length, 1);
  assert.equal(getClaudeSetupState().open, false);
});

test("a CLAUDE_NOT_INSTALLED chat error opens the setup dialog as a fallback", async (t) => {
  resetChatStore();
  resetClaudeSetupStore();
  setProject("proj-1");

  const events = makeMockEvents();
  const restore = __setTransportForTesting({
    events: events.bus,
  });
  const detach = attachChatEventStream();
  t.after(() => {
    detach();
    detachChatEventStream();
    restore();
    resetChatStore();
    resetClaudeSetupStore();
  });

  events.emit("chat_event", { kind: "turn_start", turnId: "t1", phase: "plan" });
  events.emit("chat_event", {
    kind: "error",
    turnId: "t1",
    message: "`claude` CLI not found. Install Claude Code (https://claude.ai/install).",
  });
  await tick();

  const state = getClaudeSetupState();
  assert.equal(state.open, true, "dialog opens instead of leaving only the raw error");
  assert.equal(state.phase, "instructions");

  // Unrelated errors never open it.
  resetClaudeSetupStore();
  events.emit("chat_event", { kind: "error", turnId: "t2", message: "boom" });
  await tick();
  assert.equal(getClaudeSetupState().open, false);
});
