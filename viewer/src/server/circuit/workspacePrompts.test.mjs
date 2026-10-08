// The Prompt button's reader: finds this workspace's sessions in each engine's own logs and
// returns only what the person typed, oldest first. Every log here is a fixture in a temp HOME.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { claudeProjectSlug, cleanPrompt, isInjected, readWorkspacePrompts } from "./workspacePrompts.mjs";

function tmp(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}
const jl = (rows) => rows.map((r) => JSON.stringify(r)).join("\n") + "\n";
const LONG = "x".repeat(40000); // the real session_meta carries the whole system prompt

function codexLog(home, day, name, cwd, rows) {
  const dir = path.join(home, ".codex", "sessions", "2026", "10", day);
  fs.mkdirSync(dir, { recursive: true });
  const meta = { timestamp: `2026-10-${day}T07:00:00Z`, type: "session_meta", payload: { id: name, timestamp: `2026-10-${day}T07:00:00Z`, cwd, base_instructions: { text: LONG } } };
  fs.writeFileSync(path.join(dir, `rollout-${name}.jsonl`), jl([meta, ...rows]));
}
const codexUser = (at, text) => ({ timestamp: at, type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text }] } });

test("codex: this workspace's sessions only; injected context and approval-reviewer transcripts dropped", () => {
  const home = tmp("wp-home-");
  const ws = tmp("wp-ws-");
  codexLog(home, "06", "a", ws, [
    codexUser("2026-10-06T07:00:01Z", "# AGENTS.md instructions for /x\n<INSTRUCTIONS>…"),
    codexUser("2026-10-06T07:00:02Z", "<environment_context>…</environment_context>"),
    codexUser("2026-10-06T07:21:00Z", "ĐỀ BÀI: DESK PET MINI\nlàm board đi"),
    { timestamp: "2026-10-06T07:22:00Z", type: "response_item", payload: { type: "message", role: "assistant", content: [{ type: "output_text", text: "ok" }] } },
    codexUser("2026-10-06T08:00:00Z", '<image name=[Image #1] path="/tmp/a.png"></image> đây fen'),
  ]);
  codexLog(home, "06", "reviewer", ws, [codexUser("2026-10-06T07:30:00Z", "The following is the Codex agent history whose request action you are assessing.")]);
  codexLog(home, "06", "other", "/somewhere/else", [codexUser("2026-10-06T06:00:00Z", "not this board")]);

  const r = readWorkspacePrompts({ dir: ws, env: { HOME: home } });
  assert.equal(r.sessions.length, 1, "the reviewer and the other workspace are not sessions of this board");
  assert.deepEqual(r.sessions[0].prompts.map((p) => p.text), ["ĐỀ BÀI: DESK PET MINI\nlàm board đi", "[image] đây fen"]);
  assert.equal(r.original.engine, "codex");
  assert.equal(r.original.text, "ĐỀ BÀI: DESK PET MINI\nlàm board đi");
});

test("claude: the project folder slug, real prompts only, tool results and command echoes dropped", () => {
  const home = tmp("wp-home-");
  const ws = tmp("wp-ws-");
  const folder = path.join(home, ".claude", "projects", claudeProjectSlug(ws));
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, "s1.jsonl"), jl([
    { type: "permission-mode", permissionMode: "auto" },
    { type: "user", cwd: ws, sessionId: "s1", timestamp: "2026-10-01T03:51:00Z", message: { role: "user", content: "Đọc hết rồi mới làm. Plan ≤ 20 dòng rồi build luôn." } },
    { type: "user", cwd: ws, sessionId: "s1", timestamp: "2026-10-01T03:52:00Z", message: { role: "user", content: [{ type: "tool_result", content: "ok" }] } },
    { type: "user", cwd: ws, sessionId: "s1", timestamp: "2026-10-01T03:53:00Z", isMeta: true, message: { role: "user", content: "<local-command-caveat>…" } },
    { type: "user", cwd: ws, sessionId: "s1", timestamp: "2026-10-01T03:54:00Z", message: { role: "user", content: [{ type: "text", text: "<command-name>/model</command-name>" }] } },
    { type: "user", cwd: ws, sessionId: "s1", timestamp: "2026-10-02T03:39:00Z", message: { role: "user", content: [{ type: "text", text: "chơi pin 18650 được không?" }] } },
  ]));
  const r = readWorkspacePrompts({ dir: ws, env: { HOME: home } });
  assert.equal(r.sessions.length, 1);
  assert.deepEqual(r.sessions[0].prompts.map((p) => p.text), ["Đọc hết rồi mới làm. Plan ≤ 20 dòng rồi build luôn.", "chơi pin 18650 được không?"]);
  assert.equal(r.original.engine, "claude");
});

test("grok: url-encoded cwd folder, synthetic reminders dropped, the user_query wrapper stripped", () => {
  const home = tmp("wp-home-");
  const ws = tmp("wp-ws-");
  const sdir = path.join(home, ".grok", "sessions", encodeURIComponent(path.resolve(ws)), "01a0");
  fs.mkdirSync(sdir, { recursive: true });
  fs.writeFileSync(path.join(sdir, "summary.json"), JSON.stringify({ info: { cwd: ws }, created_at: "2026-09-23T07:08:09Z" }));
  fs.writeFileSync(path.join(sdir, "chat_history.jsonl"), jl([
    { type: "system", content: "You are Grok" },
    { type: "user", synthetic_reason: "system_reminder", content: [{ type: "text", text: "<system-reminder>skills</system-reminder>" }] },
    { type: "user", content: [{ type: "text", text: "<user_query>CHẠY THỬ TILE KICAD</user_query>" }] },
  ]));
  const r = readWorkspacePrompts({ dir: ws, env: { HOME: home } });
  assert.equal(r.original.engine, "grok");
  assert.equal(r.original.text, "CHẠY THỬ TILE KICAD");
});

test("engines mix oldest first; the original is the first ever even past the session cap; empty is null", () => {
  const home = tmp("wp-home-");
  const ws = tmp("wp-ws-");
  codexLog(home, "07", "later", ws, [codexUser("2026-10-07T09:00:00Z", "v2 prompt")]);
  const folder = path.join(home, ".claude", "projects", claudeProjectSlug(ws));
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, "s.jsonl"), jl([{ type: "user", cwd: ws, timestamp: "2026-10-01T00:00:00Z", message: { content: "first ever" } }]));
  for (let i = 0; i < 55; i += 1) codexLog(home, "08", `n${i}`, ws, [codexUser(`2026-10-08T${String(10 + (i % 10)).padStart(2, "0")}:${String(i).padStart(2, "0")}:00Z`, `follow-up ${i}`)]);
  const r = readWorkspacePrompts({ dir: ws, env: { HOME: home } });
  assert.equal(r.original.text, "first ever");
  assert.equal(r.sessions.length, 50, "the list is capped, the original is not lost to the cap");

  assert.deepEqual(readWorkspacePrompts({ dir: tmp("wp-empty-"), env: { HOME: tmp("wp-home-") } }), { original: null, sessions: [] });
});

test("isInjected / cleanPrompt", () => {
  assert.equal(isInjected("  "), true);
  assert.equal(isInjected("<system-reminder>x"), true);
  assert.equal(isInjected("làm board đi"), false);
  assert.equal(cleanPrompt("a [Image: source: /tmp/x.png] b"), "a  b");
  assert.equal(cleanPrompt("y".repeat(25000)).length, 20001);
});
