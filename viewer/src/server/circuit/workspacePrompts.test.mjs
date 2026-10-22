import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { readWorkspacePrompts } from "./workspacePrompts.mjs";

const tmp = (prefix) => fs.mkdtempSync(path.join(os.tmpdir(), prefix));
const write = (root, member, text) => {
  const file = path.join(root, member);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
  return file;
};

test("private agent conversations are never used as a project prompt", () => {
  const home = tmp("wp-home-");
  const ws = tmp("wp-ws-");
  const codex = path.join(home, ".codex", "sessions", "2026", "10", "22");
  fs.mkdirSync(codex, { recursive: true });
  write(codex, "rollout-private.jsonl", [
    { type: "session_meta", payload: { cwd: ws } },
    { type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text: "PRIVATE CHAT SENTINEL" }] } },
  ].map((row) => JSON.stringify(row)).join("\n"));

  assert.deepEqual(readWorkspacePrompts({ dir: ws, env: { HOME: home, CODEX_HOME: path.join(home, ".codex") } }),
    { original: null, current: null, sessions: [] });
});

test("only explicit files in the selected project are returned, without later chat updates", () => {
  const ws = tmp("wp-ws-");
  const other = tmp("wp-other-");
  write(ws, "prompts/original.md", "Original spider request\n");
  write(ws, "prompts/current.md", "Current spider brief\n");
  write(other, "prompts/current.md", "PRIVATE OTHER PROJECT\n");

  const result = readWorkspacePrompts({ dir: ws });
  assert.equal(result.original.text, "Original spider request");
  assert.equal(result.original.file, "prompts/original.md");
  assert.equal(result.current.text, "Current spider brief");
  assert.equal(result.current.source, "file");
  assert.deepEqual(result.sessions, []);
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE OTHER PROJECT/);
  assert.equal(readWorkspacePrompts({ dir: other }).current.text, "PRIVATE OTHER PROJECT");
});

test("legacy PROMPT.md and a single dated original are explicit project-owned sources", () => {
  const legacy = tmp("wp-legacy-");
  write(legacy, "PROMPT.md", "Original and current legacy prompt");
  const legacyResult = readWorkspacePrompts({ dir: legacy });
  assert.equal(legacyResult.original.text, "Original and current legacy prompt");
  assert.equal(legacyResult.current.text, "Original and current legacy prompt");

  const spider = tmp("wp-spider-");
  write(spider, "prompts/history/2026-10-22-original.md", "Original spider prompt");
  write(spider, "prompts/current.md", "Current spider brief");
  assert.equal(readWorkspacePrompts({ dir: spider }).original.text, "Original spider prompt");
  write(spider, "prompts/history/2026-10-23-original.md", "Another original");
  assert.equal(readWorkspacePrompts({ dir: spider }).original, null, "ambiguous history is not guessed");
});

test("symlinks outside the project and oversized files are not displayed", () => {
  const ws = tmp("wp-ws-");
  const outside = write(tmp("wp-private-"), "secret.md", "PRIVATE SECRET");
  fs.mkdirSync(path.join(ws, "prompts"));
  fs.symlinkSync(outside, path.join(ws, "prompts", "original.md"));
  fs.symlinkSync(outside, path.join(ws, "prompts", "current.md"));
  const outsideHistory = tmp("wp-private-history-");
  write(outsideHistory, "2026-10-22-original.md", "PRIVATE HISTORY");
  fs.symlinkSync(outsideHistory, path.join(ws, "prompts", "history"));
  assert.deepEqual(readWorkspacePrompts({ dir: ws }), { original: null, current: null, sessions: [] });

  fs.unlinkSync(path.join(ws, "prompts", "current.md"));
  write(ws, "prompts/current.md", "x".repeat(1024 * 1024 + 1));
  assert.equal(readWorkspacePrompts({ dir: ws }).current, null);
});
