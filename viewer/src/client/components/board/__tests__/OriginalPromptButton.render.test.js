// The Prompt button: opens on click, shows the original request with a copy button, lists the
// rest on demand, falls back to the chat's request in the app, and says so when there is none.

import assert from "node:assert/strict";
import test from "node:test";

import { click, flush, mount } from "../../../test/render.js";
import { setTransport, resetTransport } from "../../../lib/transport.ts";
import OriginalPromptButton from "../OriginalPromptButton.jsx";

const DATA = {
  original: { engine: "codex", sessionId: "a", at: "2026-10-06T07:21:00Z", text: "ĐỀ BÀI: DESK PET MINI\nlàm board đi" },
  sessions: [
    { engine: "codex", sessionId: "a", startedAt: "2026-10-06T07:21:00Z", prompts: [{ at: "2026-10-06T07:21:00Z", text: "ĐỀ BÀI: DESK PET MINI\nlàm board đi" }, { at: "2026-10-07T03:14:00Z", text: "rồi fix mấy cái lỗi đi fen." }] },
  ],
};

async function settle() {
  for (let i = 0; i < 4; i += 1) await flush();
}
const q = (ui, slot) => ui.container.querySelector(`[data-slot="${slot}"]`);

test("click opens the original prompt, re-reads each time, and lists the rest on demand", async () => {
  const calls = [];
  setTransport({ workspace_prompts: async (id) => { calls.push(id); return DATA; } });
  const ui = mount(OriginalPromptButton, { projectId: "workspace" });
  try {
    assert.equal(q(ui, "original-prompt-panel"), null, "closed until clicked");
    click(q(ui, "original-prompt-button"));
    await settle();
    assert.deepEqual(calls, ["workspace"]);
    assert.match(q(ui, "original-prompt-text").textContent, /DESK PET MINI\nlàm board đi/);
    assert.match(q(ui, "original-prompt-panel").textContent, /Codex/);
    assert.ok(q(ui, "original-prompt-copy"));

    click(q(ui, "original-prompt-all-toggle"));
    assert.match(q(ui, "original-prompt-all").textContent, /fix mấy cái lỗi/);

    click(q(ui, "original-prompt-button"));   // close
    assert.equal(q(ui, "original-prompt-panel"), null);
    click(q(ui, "original-prompt-button"));   // reopen re-reads
    await settle();
    assert.deepEqual(calls, ["workspace", "workspace"]);
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
    resetTransport();
  }
});

test("no logs: the chat's request is shown in the app; nothing at all says so plainly", async () => {
  setTransport({ workspace_prompts: async () => ({ original: null, sessions: [] }) });
  const ui = mount(OriginalPromptButton, { projectId: "p", fallbackText: "a six-key macropad" });
  try {
    click(q(ui, "original-prompt-button"));
    await settle();
    assert.equal(q(ui, "original-prompt-text").textContent, "a six-key macropad");
    ui.set({ fallbackText: "" });
    assert.match(q(ui, "original-prompt-empty").textContent, /No prompt found/);
  } finally {
    ui.unmount();
    resetTransport();
  }
});
