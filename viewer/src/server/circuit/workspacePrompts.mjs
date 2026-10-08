// What the person asked for, read back from the engine's own session logs.
//
// In a Harness pane the conversation lives in the agent's terminal, not in this app, so the board
// view has no chat history to quote. After a long run the original request is hundreds of turns
// up in a terminal scrollback (2026-10-08: "context dài quá nó mất prompt kím mệt quá"). Every
// engine already writes the request to disk; this module finds the sessions whose working
// directory is exactly this workspace and returns what the person typed, oldest first.
//
//   Codex        $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl   first line session_meta.cwd
//   Claude Code  $CLAUDE_CONFIG_DIR/projects/<cwd with / and . → ->/*.jsonl, entries with cwd
//   Grok Build   ~/.grok/sessions/<urlencoded cwd>/<id>/chat_history.jsonl + summary.json
//
// Only text the person typed is returned: injected instructions (AGENTS.md, environment context,
// skill lists, system reminders), Codex's approval-reviewer transcripts, tool results and slash
// command echoes are dropped. Read-only; nothing here writes.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const MAX_PROMPT_CHARS = 20000;
const MAX_SESSIONS = 50;

/** Text a person did not type: harness/engine scaffolding that rides in the user role. */
const INJECTED = [
  /^# AGENTS\.md instructions/,
  /^<environment_context>/,
  /^<user_instructions>/,
  /^<permissions instructions>/,
  /^<system-reminder>/,
  /^<local-command-caveat>/,
  /^<command-name>/,
  /^<command-message>/,
  /^<local-command-stdout>/,
  /^<bash-input>/,
  /^<bash-stdout>/,
  /^The following is the Codex agent history/,
  /^Caveat: The messages below were generated/,
  /^\[Request interrupted by user/,
];

export function isInjected(text) {
  const t = String(text || "").trim();
  return !t || INJECTED.some((re) => re.test(t));
}

/** Strip attachment markers and the Grok query wrapper; keep the words. */
export function cleanPrompt(text) {
  let t = String(text || "");
  t = t.replace(/<\/?user_query>/g, "");
  t = t.replace(/<image\b[^>]*>\s*<\/image>/g, "[image]");
  t = t.replace(/\[Image: source: [^\]]*\]/g, "");
  t = t.trim();
  return t.length > MAX_PROMPT_CHARS ? `${t.slice(0, MAX_PROMPT_CHARS)}…` : t;
}

function textOf(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((c) => c && typeof c === "object" && (c.type === "text" || c.type === "input_text"))
    .map((c) => c.text || "")
    .join("\n");
}

function readLines(file) {
  try {
    return fs.readFileSync(file, "utf8").split("\n");
  } catch {
    return [];
  }
}

function firstLine(file) {
  let fd;
  try {
    fd = fs.openSync(file, "r");
    const buf = Buffer.alloc(16384);
    const n = fs.readSync(fd, buf, 0, buf.length, 0);
    const s = buf.toString("utf8", 0, n);
    const i = s.indexOf("\n");
    return i < 0 ? s : s.slice(0, i);
  } catch {
    return "";
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function walk(dir, depth, out) {
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory() && depth > 0) walk(full, depth - 1, out);
    else if (e.isFile() && e.name.endsWith(".jsonl")) out.push(full);
  }
  return out;
}

function samePath(a, b) {
  if (!a || !b) return false;
  const norm = (p) => {
    const r = path.resolve(String(p));
    try {
      return fs.realpathSync(r);
    } catch {
      return r;
    }
  };
  return norm(a) === norm(b);
}

function codexSessions(dir, env) {
  const home = env.CODEX_HOME || path.join(env.HOME || os.homedir(), ".codex");
  const files = walk(path.join(home, "sessions"), 3, []);
  const sessions = [];
  for (const file of files) {
    // The session_meta line carries the whole system prompt and runs past any sane read size,
    // so its few short fields are read from the head of the line rather than parsing it whole.
    const head = firstLine(file);
    if (!head.includes('"type":"session_meta"')) continue;
    const field = (name) => {
      const m = new RegExp(`"${name}":"((?:[^"\\\\]|\\\\.)*)"`).exec(head);
      return m ? JSON.parse(`"${m[1]}"`) : "";
    };
    const meta = { payload: { cwd: field("cwd"), id: field("id"), timestamp: field("timestamp") } };
    if (!samePath(meta.payload.cwd, dir)) continue;
    const prompts = [];
    let reviewer = false;
    for (const line of readLines(file)) {
      if (!line) continue;
      let d;
      try {
        d = JSON.parse(line);
      } catch {
        continue;
      }
      const p = d.payload || {};
      if (d.type !== "response_item" || p.type !== "message" || p.role !== "user") continue;
      const raw = textOf(p.content);
      if (/^The following is the Codex agent history/.test(raw.trim())) {
        reviewer = true;
        break;
      }
      if (isInjected(raw)) continue;
      prompts.push({ at: d.timestamp || "", text: cleanPrompt(raw) });
    }
    if (reviewer || !prompts.length) continue;
    sessions.push({ engine: "codex", sessionId: String(meta.payload?.id || path.basename(file)), startedAt: meta.payload?.timestamp || prompts[0].at, prompts });
  }
  return sessions;
}

/** Claude Code's project folder name for a directory: every `/` and `.` becomes `-`. */
export function claudeProjectSlug(dir) {
  return path.resolve(String(dir)).replace(/[/.]/g, "-");
}

function claudeSessions(dir, env) {
  const base = env.CLAUDE_CONFIG_DIR || path.join(env.HOME || os.homedir(), ".claude");
  const folder = path.join(base, "projects", claudeProjectSlug(dir));
  const sessions = [];
  for (const file of walk(folder, 0, [])) {
    const prompts = [];
    let sessionId = path.basename(file, ".jsonl");
    for (const line of readLines(file)) {
      if (!line) continue;
      let d;
      try {
        d = JSON.parse(line);
      } catch {
        continue;
      }
      if (d.type !== "user" || d.isMeta || d.isSidechain) continue;
      if (d.cwd && !samePath(d.cwd, dir)) continue;
      const content = d.message?.content;
      if (Array.isArray(content) && content.some((c) => c?.type === "tool_result")) continue;
      const raw = textOf(content);
      if (isInjected(raw)) continue;
      if (d.sessionId) sessionId = d.sessionId;
      prompts.push({ at: d.timestamp || "", text: cleanPrompt(raw) });
    }
    if (prompts.length) sessions.push({ engine: "claude", sessionId, startedAt: prompts[0].at, prompts });
  }
  return sessions;
}

function grokSessions(dir, env) {
  const home = env.HOME || os.homedir();
  const folder = path.join(home, ".grok", "sessions", encodeURIComponent(path.resolve(String(dir))));
  let ids = [];
  try {
    ids = fs.readdirSync(folder, { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => e.name);
  } catch {
    return [];
  }
  const sessions = [];
  for (const id of ids) {
    const sdir = path.join(folder, id);
    let startedAt = "";
    try {
      const summary = JSON.parse(fs.readFileSync(path.join(sdir, "summary.json"), "utf8"));
      if (summary?.info?.cwd && !samePath(summary.info.cwd, dir)) continue;
      startedAt = summary?.created_at || "";
    } catch {
      // no summary: the folder name already names the cwd
    }
    const prompts = [];
    for (const line of readLines(path.join(sdir, "chat_history.jsonl"))) {
      if (!line) continue;
      let d;
      try {
        d = JSON.parse(line);
      } catch {
        continue;
      }
      if (d.type !== "user" || d.synthetic_reason) continue;
      const raw = textOf(d.content);
      if (isInjected(raw)) continue;
      prompts.push({ at: d.ts || "", text: cleanPrompt(raw) });
    }
    if (prompts.length) sessions.push({ engine: "grok", sessionId: id, startedAt: startedAt || prompts[0].at, prompts });
  }
  return sessions;
}

/**
 * Every agent session that ran in `dir`, oldest first, each with what the person typed in it.
 * `original` is the very first prompt — the request the board was made from.
 */
export function readWorkspacePrompts({ dir, env = process.env } = {}) {
  if (!dir) return { original: null, sessions: [] };
  const all = [...codexSessions(dir, env), ...claudeSessions(dir, env), ...grokSessions(dir, env)]
    .sort((a, b) => String(a.startedAt).localeCompare(String(b.startedAt)));
  const first = all[0];
  const sessions = all.slice(-MAX_SESSIONS);
  const original = first
    ? { engine: first.engine, sessionId: first.sessionId, at: first.prompts[0].at, text: first.prompts[0].text }
    : null;
  return { original, sessions };
}
