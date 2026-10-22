// The Prompt button: opens the saved original, without exposing unrelated agent history.

import assert from "node:assert/strict";
import test from "node:test";

import { click, flush, mount } from "../../../test/render.js";
import { setTransport, resetTransport } from "../../../lib/transport.ts";
import OriginalPromptButton from "../OriginalPromptButton.jsx";

const DATA = {
  original: { source: "file", file: "prompts/original.md", at: "2026-10-06T07:21:00Z", text: "ĐỀ BÀI: DESK PET MINI\nlàm board đi" },
  sessions: [],
};

async function settle() {
  for (let i = 0; i < 4; i += 1) await flush();
}
const q = (ui, slot) => ui.container.querySelector(`[data-slot="${slot}"]`);

test("click opens only the saved original and re-reads it each time", async () => {
  const calls = [];
  setTransport({ workspace_prompts: async (id) => { calls.push(id); return DATA; } });
  const ui = mount(OriginalPromptButton, { projectId: "workspace" });
  try {
    assert.equal(q(ui, "original-prompt-panel"), null, "closed until clicked");
    click(q(ui, "original-prompt-button"));
    await settle();
    assert.deepEqual(calls, ["workspace"]);
    assert.match(q(ui, "original-prompt-text").textContent, /DESK PET MINI\nlàm board đi/);
    assert.match(q(ui, "original-prompt-panel").textContent, /prompts\/original\.md/);
    assert.ok(q(ui, "original-prompt-copy"));
    assert.equal(q(ui, "original-prompt-all-toggle"), null);

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

test("no saved file: the selected app chat may supply the request", async () => {
  setTransport({ workspace_prompts: async () => ({ original: null, sessions: [] }) });
  const ui = mount(OriginalPromptButton, { projectId: "p", fallbackText: "a six-key macropad" });
  try {
    click(q(ui, "original-prompt-button"));
    await settle();
    assert.equal(q(ui, "original-prompt-text").textContent, "a six-key macropad");
    ui.set({ fallbackText: "" });
    assert.match(q(ui, "original-prompt-empty").textContent, /No original prompt saved/);
  } finally {
    ui.unmount();
    resetTransport();
  }
});

test("changing projects closes the old prompt panel", async () => {
  setTransport({ workspace_prompts: async () => DATA });
  const ui = mount(OriginalPromptButton, { projectId: "first" });
  try {
    click(q(ui, "original-prompt-button"));
    await settle();
    assert.match(q(ui, "original-prompt-text").textContent, /DESK PET MINI/);
    ui.set({ projectId: "second" });
    await settle();
    assert.equal(q(ui, "original-prompt-panel"), null);
  } finally {
    ui.unmount();
    resetTransport();
  }
});
