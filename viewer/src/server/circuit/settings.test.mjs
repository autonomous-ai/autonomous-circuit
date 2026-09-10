import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { DEFAULT_PROVIDER, PROVIDERS, createSettingsStore } from "./settings.mjs";

test("a fresh install runs on the Claude arm, the one every teammate has", () => {
  // The default was codex from 2026-09-07 to 2026-09-09 for the Astra
  // comparison, and only worked on the machine running it: `gpt-6-astra` is
  // gated to a ChatGPT team login, and a clone without Codex.app resolves no
  // executable at all. The team pulls main and runs it locally this weekend.
  assert.equal(DEFAULT_PROVIDER, "claude");
  assert.ok(PROVIDERS.includes("codex"), "the comparison arm stays available per machine");

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "circuit-settings-"));
  const store = createSettingsStore({ filePath: path.join(dir, "settings.json") });
  assert.equal(store.read().provider, "claude", "no settings file → claude");
});
