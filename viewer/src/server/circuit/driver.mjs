// Circuit chat driver — Node port of the donor's Rust
// `desktop/src-tauri/src/commands/claude_driver.rs` + `chat.rs` semantics,
// coded against docs/video-interfaces.md §2 (the frozen contract).
//
// One chat turn = one spawned `claude -p --output-format stream-json` child.
// The driver translates stream-json lines into the 9-kind ChatEvent union,
// intercepts ExitPlanMode (→ plan_proposed + kill child) and AskUserQuestion
// (→ ```circuit-questions fence + end turn), snapshots the workspace mtimes to
// emit artifact_changed, chains the autopilot build turn after a proposed
// plan, and runs the silent 3-phase post-build review loop.

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import readline from "node:readline";
import { spawn } from "node:child_process";

import {
  claudeConfigDir,
  sessionJsonlPath,
  skipDirNames,
} from "./projects.mjs";

const LOG_TAG = "[circuit:driver]";

function log(...args) {
  console.log(LOG_TAG, ...args);
}

function debugEnabled() {
  return Boolean(process.env.CIRCUIT_DEBUG_CLAUDE);
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

/**
 * UUID v5 namespace for deriving per-project Claude session UUIDs. Circuit's
 * own namespace (freshly minted at fork — new product, no donor sessions to
 * preserve); a given projectId always maps to the same session UUID.
 */
export const CIRCUIT_SESSION_NS = "f466e3eb-799c-4a95-bc9a-72092027e9f7";

/** RFC 4122 UUID v5 (SHA-1) — implemented on node:crypto, no deps. */
export function uuidv5(name, namespace = CIRCUIT_SESSION_NS) {
  const ns = Buffer.from(String(namespace).replace(/-/g, ""), "hex");
  if (ns.length !== 16) {
    throw new Error(`invalid uuid namespace: ${namespace}`);
  }
  const hash = crypto
    .createHash("sha1")
    .update(Buffer.concat([ns, Buffer.from(String(name), "utf8")]))
    .digest();
  const bytes = Buffer.from(hash.subarray(0, 16));
  bytes[6] = (bytes[6] & 0x0f) | 0x50; // version 5
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Deterministic per-project Claude session id (same projectId → same UUID
 * across restarts, which is what `--session-id`/`--resume` need). */
export function sessionIdForProject(projectId) {
  return uuidv5(String(projectId), CIRCUIT_SESSION_NS);
}

/** Has Claude Code already persisted a session JSONL for this UUID? False on
 * any error — the caller then passes `--session-id` (first-turn semantics). */
export function claudeSessionExists(workspace, sessionId, env = process.env) {
  try {
    return fs.existsSync(sessionJsonlPath(workspace, sessionId, env));
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

export const PLAN_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit, the AI short-drama studio. Every user",
  "message is a request to create or refine a vertical short-drama series or",
  "episode. You are in PLANNING mode: design, do not build. You MAY run",
  "read-only analysis (list the project, read series.py, episode sources, and",
  ".episode.json sidecars) to ground the plan in what already exists, but do",
  "NOT write or edit any source file and do NOT run the drama generator or",
  "produce/update episode artifacts yet — the build happens only after the",
  "plan is approved.",
  "",
  "Design with the story-analysis and dramacode skills' knowledge. A full",
  "plan covers: the series bible (title, genre, visual style, language, cast",
  "with ids, looks, and voices), the episode beat sheet (a cold-open hook",
  "inside hook_max_s, escalating beats, a hard cliffhanger), and the concrete",
  "shot list (per shot: kind establish/action/dialogue/insert, duration_s in",
  "[3, 15], cast ids, dialogue lines, prompt). Be specific — names, shot",
  "counts, durations, the exact cliffhanger. A trivial edit needs only the",
  "exact change and its consequence, one to three lines.",
  "",
  "PREFERENCES FIRST. For a NEW series (not a trivial edit), open the turn by",
  "asking 2-4 preference questions (genre, visual style, language, episode",
  "length) by CALLING the AskUserQuestion tool — do NOT write the questions",
  "as plain text or a JSON/code block; only the tool call renders the",
  "multiple-choice UI. Give EVERY question a first option labelled \"Let",
  "Circuit choose\" (description: \"Recommended — we pick the best for you\");",
  "when the user picks it, use your best default and proceed without",
  "re-asking. The tool call ends the turn — do not call ExitPlanMode in the",
  "same turn. A trivial edit needs no questions.",
  "",
  "After the preferences are settled, write the plan and finish by calling",
  "the ExitPlanMode tool with the COMPLETE plan markdown in its `plan` field",
  "— restate the entire plan in that call even if you already wrote it",
  "earlier in the conversation, and even when resuming a prior session. NEVER",
  "call ExitPlanMode with an empty or partial `plan`.",
].join("\n");

export const IMPLEMENT_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit, the AI short-drama studio. The user has",
  "APPROVED a plan. Implement it now using the dramacode skill: write",
  "series.py (the series bible) and the episode source under episodes/",
  "(episodes/epNNN.py defining gen_episode()), then run the generator",
  "(`python ~/.claude/skills/dramacode/scripts/drama episodes/epNNN.py`) to",
  "render the episode .mp4, the .episode.json sidecar, the .srt subtitles,",
  "the per-shot clips, and the _review/ poster and board. Follow the",
  "dramacode protocol. Do not re-plan or ask further questions unless a",
  "blocking ambiguity remains. If the generator reports validation errors,",
  "fix the source and re-run until the episode builds.",
].join("\n");

export const REVIEW_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit, the AI short-drama studio. An automatic",
  "post-build review of the episode you just built is running. Work",
  "SILENTLY: do not greet, explain, summarize, ask questions, or re-plan —",
  "just improve the episode and regenerate with the dramacode skill",
  "(`python ~/.claude/skills/dramacode/scripts/drama <episodes/epNNN.py>`).",
  "The per-round message says what to check. STRUCTURE problems (from the",
  ".episode.json sidecars) are blocking and come first. DRAMATIC-FUNCTION",
  "problems (warnings of kind \"functional\") mean the episode renders but",
  "breaks the beat-sheet rules in the dramacode skill's references — fix the",
  "story/shots until they clear. CRAFT is a final visual pass: re-render,",
  "Read the <stem>_review/_board.png contact sheet and _poster.png, and fix",
  "continuity, composition, and caption legibility. Edit the Python source",
  "(never the rendered artifacts), regenerate, and stop as soon as the",
  "episode is clean.",
].join("\n");

// The screening critic's phase prompt. The marker "SCREENING ROOM" (never
// "post-build") is how the phase is told apart from a plain review round.
export const SCREENING_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit as the SCREENING ROOM: the critic",
  "who WATCHES the rendered episode and judges its quality. Use the",
  "screening-room skill. Work SILENTLY — no greeting, no summary, no",
  "questions. Run the skill's bundle script to sample frames through every",
  "shot and detect technical defects, then `Read` every sampled frame plus",
  "the board and poster (Read returns images — actually look), read the",
  "metadata, audio stats, defects, and the episode source, and score the",
  "film against the rubric. Your ENTIRE output is exactly one fenced",
  "```screening-report JSON block: {overall_1_10, dimension_scores{...},",
  "pass_at_bar, notes:[{department, shot_ids, severity, note, fix}]}. Be a",
  "tough, specific, Oscar-level critic; cite shot ids; and never pass a cut",
  "that carries a technical defect — the orientation/rotation bug fails the",
  "bar on its own.",
].join("\n");

/** Appended to every phase prompt so the model knows the one absolute
 * directory this project lives in (donor: artifacts written elsewhere are
 * invisible to the app's snapshotter and catalog). */
export function workspaceDirective(workspace) {
  return (
    "PROJECT WORKSPACE. This project lives in the single absolute directory " +
    "below. Every file you create — series.py, the episode sources under " +
    "episodes/, and every artifact the drama generator produces — MUST live " +
    "inside it, and you MUST pass paths inside it to the dramacode tools. Do " +
    "not create the project or write artifacts anywhere else: Circuit only " +
    "detects artifacts inside this directory, so anything written outside it " +
    `is invisible to the app.\n${workspace}`
  );
}

const DISABLE_HOOKS_SETTINGS = '{"disableAllHooks":true}';

export const PHASE = Object.freeze({
  PLAN: "plan",
  IMPLEMENT: "implement",
  REVIEW: "review",
  SCREENING: "screening",
});

function systemPromptForPhase(phase) {
  if (phase === PHASE.PLAN) return PLAN_SYSTEM_PROMPT;
  if (phase === PHASE.REVIEW) return REVIEW_SYSTEM_PROMPT;
  if (phase === PHASE.SCREENING) return SCREENING_SYSTEM_PROMPT;
  return IMPLEMENT_SYSTEM_PROMPT;
}

/** Claude Code's own `--permission-mode` per contract §2: plan turns run in
 * `plan` (writes CLI-blocked); build and review run `bypassPermissions`
 * (unattended headless build — `acceptEdits` would still prompt for the
 * generator's Bash call). */
export function permissionModeForPhase(phase) {
  return phase === PHASE.PLAN ? "plan" : "bypassPermissions";
}

/** Wire tag carried on turn_start. Review rides under `implement` (it never
 * emits its own turn_start). */
export function phaseTag(phase) {
  return phase === PHASE.PLAN ? "plan" : "implement";
}

// ---------------------------------------------------------------------------
// Command construction
// ---------------------------------------------------------------------------

/**
 * PATH for resolving + running `claude`, robust to launch context (the donor's
 * `augmented_path`): prepend the usual user/Homebrew bin dirs to whatever PATH
 * we inherited so both our lookup and the child (claude → node, skill →
 * python) resolve.
 */
export function augmentedPathDirs(env = process.env) {
  const dirs = [];
  const home = env.HOME || process.env.HOME || "";
  if (home) {
    dirs.push(
      path.join(home, ".local", "bin"),
      path.join(home, "bin"),
      path.join(home, ".bun", "bin"),
      path.join(home, ".volta", "bin"),
    );
  }
  dirs.push(
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
  );
  const existing = env.PATH || "";
  if (existing) {
    dirs.push(...existing.split(path.delimiter));
  }
  return dirs;
}

export function augmentedPath(env = process.env) {
  return augmentedPathDirs(env).join(path.delimiter);
}

/**
 * Resolve the absolute path of the `claude` binary. CIRCUIT_CLAUDE_BIN wins
 * (tests point it at a stub script — the driver never spawns real claude in
 * tests); otherwise search the augmented PATH. Returns null when not found.
 */
export function resolveClaude(env = process.env) {
  const override = env.CIRCUIT_CLAUDE_BIN;
  if (override) {
    return fs.existsSync(override) ? override : null;
  }
  for (const dir of augmentedPathDirs(env)) {
    const candidate = path.join(dir, "claude");
    try {
      if (fs.statSync(candidate).isFile()) {
        return candidate;
      }
    } catch {
      // keep looking
    }
  }
  return null;
}

/**
 * argv (flags only — no argv[0]) for one `claude -p` invocation, per §2:
 * `claude -p --output-format stream-json --input-format stream-json --verbose
 * --include-partial-messages --permission-mode <phase> --add-dir <workspace>
 * --add-dir ~/.claude/skills --append-system-prompt <phase prompt>
 * --strict-mcp-config --settings '{"disableAllHooks":true}'
 * (--resume|--session-id) <uuid5(projectId)> --model <settings.model>`.
 */
export function buildCommandArgs({
  workspace,
  phase,
  sessionId,
  model = "",
  env = process.env,
}) {
  const args = [
    "-p",
    "--output-format",
    "stream-json",
    "--input-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
    "--permission-mode",
    permissionModeForPhase(phase),
    "--add-dir",
    String(workspace),
    "--add-dir",
    path.join(claudeConfigDir(env), "skills"),
    "--append-system-prompt",
    `${systemPromptForPhase(phase)}\n\n${workspaceDirective(workspace)}`,
    "--strict-mcp-config",
    "--settings",
    DISABLE_HOOKS_SETTINGS,
  ];
  if (claudeSessionExists(workspace, sessionId, env)) {
    args.push("--resume", String(sessionId));
  } else {
    args.push("--session-id", String(sessionId));
  }
  if (model) {
    args.push("--model", String(model));
  }
  return args;
}

/** Provider vars the render chain understands; keys.env supplies them, the
 * settings renderProvider wins for CIRCUIT_PROVIDER, real env wins overall. */
const PROVIDER_ENV_VARS = new Set([
  "CIRCUIT_PROVIDER",
  "FAL_KEY",
  "DASHSCOPE_API_KEY",
  "MINIMAX_API_KEY",
  "CIRCUIT_FAL_MODEL",
  "CIRCUIT_DASHSCOPE_MODEL",
  "CIRCUIT_DASHSCOPE_BASE_URL",
  "CIRCUIT_MINIMAX_MODEL",
  "CIRCUIT_MINIMAX_BASE_URL",
  "CIRCUIT_MOCK_TTS",
]);

/** Parse ~/.autonomous-circuit/keys.env (KEY=value lines, # comments). The
 * paste-a-key file: drop FAL_KEY / DASHSCOPE_API_KEY / MINIMAX_API_KEY here
 * and every render — app or terminal — can reach the hosted providers. */
export function readKeysEnv(env = process.env) {
  const filePath = path.join(env.HOME || os.homedir(), ".autonomous-circuit", "keys.env");
  const out = {};
  let text = "";
  try {
    text = fs.readFileSync(filePath, "utf8");
  } catch {
    return out;
  }
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const eq = line.indexOf("=");
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
    if (PROVIDER_ENV_VARS.has(key) && value) out[key] = value;
  }
  return out;
}

/** Env for the spawned child: inherited env + augmented PATH + the donor's
 * self-updater/background-task guards + provider keys/selection (keys.env <
 * settings.renderProvider < real environment). */
export function buildChildEnv(env = process.env, { renderProvider } = {}) {
  const child = {
    ...readKeysEnv(env),
    ...(renderProvider ? { CIRCUIT_PROVIDER: renderProvider } : {}),
    ...env,
    PATH: augmentedPath(env),
    DISABLE_AUTOUPDATER: "1",
    CLAUDE_CODE_DISABLE_BACKGROUND_TASKS: "1",
  };
  return child;
}

const IMAGE_MEDIA_TYPES = new Map([
  ["png", "image/png"],
  ["jpg", "image/jpeg"],
  ["jpeg", "image/jpeg"],
  ["webp", "image/webp"],
  ["gif", "image/gif"],
]);

function imageMediaType(filePath) {
  const ext = path.extname(String(filePath)).slice(1).toLowerCase();
  return IMAGE_MEDIA_TYPES.get(ext) || "image/png";
}

/**
 * The stream-json user message piped to claude's stdin (`--input-format
 * stream-json`): one text block (the prompt) followed by one base64 image
 * block per readable reference image. Unreadable images are skipped.
 */
export function streamJsonInput(prompt, imagePaths = []) {
  const content = [{ type: "text", text: String(prompt) }];
  for (const imagePath of imagePaths) {
    try {
      const data = fs.readFileSync(imagePath).toString("base64");
      content.push({
        type: "image",
        source: {
          type: "base64",
          media_type: imageMediaType(imagePath),
          data,
        },
      });
    } catch {
      // skip unreadable image
    }
  }
  return `${JSON.stringify({ type: "user", message: { role: "user", content } })}\n`;
}

// ---------------------------------------------------------------------------
// Artifact snapshotter
// ---------------------------------------------------------------------------

/** Extensions watched per §2: `.mp4 .png .json .srt .py .wav .mp3`. */
export const WATCHED_EXTENSIONS = new Set([
  "mp4",
  "png",
  "json",
  "srt",
  "py",
  "wav",
  "mp3",
]);

/** Snapshot every watched file under `workspace` → Map(relPath → mtimeMs).
 * Recursive; skips inputs/, .video/, .claude/, node_modules, __pycache__. */
export function snapshotWorkspace(workspace) {
  const snapshot = new Map();
  const skip = skipDirNames();
  const stack = [workspace];
  while (stack.length) {
    const dir = stack.pop();
    let dirents;
    try {
      dirents = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of dirents) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!skip.has(entry.name)) {
          stack.push(full);
        }
        continue;
      }
      if (!entry.isFile()) {
        continue;
      }
      const ext = path.extname(entry.name).slice(1).toLowerCase();
      if (!WATCHED_EXTENSIONS.has(ext)) {
        continue;
      }
      let stat;
      try {
        stat = fs.statSync(full);
      } catch {
        continue;
      }
      const rel = path.relative(workspace, full).split(path.sep).join("/");
      snapshot.set(rel, stat.mtimeMs);
    }
  }
  return snapshot;
}

/** Diff two snapshots → one artifact_changed per new file or per file whose
 * mtime moved forward by ≥ 1 second (whole-second comparison, donor rule). */
export function diffSnapshots(before, after, turnId) {
  const events = [];
  const paths = [...after.keys()].sort();
  for (const file of paths) {
    const afterSecs = Math.floor(after.get(file) / 1000);
    let reason = null;
    if (!before.has(file)) {
      reason = "new";
    } else if (afterSecs > Math.floor(before.get(file) / 1000)) {
      reason = "modified";
    }
    if (reason) {
      events.push({ kind: "artifact_changed", turnId, file, reason });
    }
  }
  return events;
}

// ---------------------------------------------------------------------------
// Stream-json → ChatEvent translation
// ---------------------------------------------------------------------------

export function newStreamState() {
  return {
    pendingTools: new Map(), // toolUseId -> tool name
    textDeltaStreamed: false, // per-message (reset by each assistant message)
    anyTextEmitted: false, // per-turn
    planProposed: false,
    questionsAsked: false,
  };
}

/** Extract the plan markdown from an ExitPlanMode tool input: prefer the
 * inline `plan`; else read the `planFilePath` file the model just wrote. */
export function planFromExitPlanMode(input) {
  const inline = String(input?.plan || "");
  if (inline.trim()) {
    return inline;
  }
  const planFilePath = String(input?.planFilePath || "");
  if (planFilePath) {
    try {
      return fs.readFileSync(planFilePath, "utf8");
    } catch {
      return "";
    }
  }
  return "";
}

/** Build the synthetic ```circuit-questions fenced block from an
 * AskUserQuestion tool input (donor mechanism, renamed fence). Null when the
 * tool carried no questions. */
export function questionsFenceFromAskUserQuestion(input) {
  const questions = input?.questions;
  if (!Array.isArray(questions) || questions.length === 0) {
    return null;
  }
  const json = JSON.stringify({ questions });
  return `\n\n\`\`\`circuit-questions\n${json}\n\`\`\`\n`;
}

function toolResultText(content) {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .filter((item) => item && item.type === "text" && typeof item.text === "string")
      .map((item) => item.text)
      .join("\n");
  }
  return "";
}

/** Short human summary of a tool result — a line count ("3 lines"); undefined
 * when empty/non-text. Shared with the session rehydrator so live and
 * reloaded traces read the same. */
export function summarizeToolResult(content) {
  const trimmed = toolResultText(content).trimEnd();
  if (!trimmed) {
    return undefined;
  }
  const lines = Math.max(trimmed.split("\n").length, 1);
  return `${lines} line${lines === 1 ? "" : "s"}`;
}

function fromStreamEvent(obj, turnId, state) {
  const ev = obj.event;
  if (!ev || ev.type !== "content_block_delta" || !ev.delta) {
    return [];
  }
  const delta = ev.delta;
  if (delta.type === "text_delta" && typeof delta.text === "string" && delta.text) {
    state.textDeltaStreamed = true;
    state.anyTextEmitted = true;
    return [{ kind: "text_delta", turnId, text: delta.text }];
  }
  if (delta.type === "thinking_delta" && typeof delta.thinking === "string" && delta.thinking) {
    return [{ kind: "thinking_delta", turnId, text: delta.thinking }];
  }
  return [];
}

function fromAssistant(obj, turnId, state) {
  const out = [];
  // The consolidated assistant message arrives after its own text_delta
  // stream events; snapshot + reset the per-message flag up front.
  const textAlreadyStreamed = state.textDeltaStreamed;
  state.textDeltaStreamed = false;

  const content = obj?.message?.content;
  if (!Array.isArray(content)) {
    return out;
  }
  for (const block of content) {
    const type = block?.type;
    if (type === "text") {
      // Emit the final text only when --include-partial-messages did NOT
      // already stream it as deltas (would duplicate); when deltas are
      // unavailable this is the only place the response text exists.
      if (!textAlreadyStreamed && typeof block.text === "string" && block.text) {
        state.anyTextEmitted = true;
        out.push({ kind: "text_delta", turnId, text: block.text });
      }
      continue;
    }
    if (type !== "tool_use") {
      continue;
    }
    const name = String(block.name || "");
    if (name === "ExitPlanMode") {
      state.anyTextEmitted = true;
      state.planProposed = true;
      out.push({ kind: "plan_proposed", turnId, plan: planFromExitPlanMode(block.input) });
      continue;
    }
    if (name === "AskUserQuestion") {
      const fence = questionsFenceFromAskUserQuestion(block.input);
      if (fence) {
        state.anyTextEmitted = true;
        state.questionsAsked = true;
        out.push({ kind: "text_delta", turnId, text: fence });
      }
      continue;
    }
    const toolUseId = String(block.id || "");
    state.pendingTools.set(toolUseId, name);
    out.push({
      kind: "tool_use_start",
      turnId,
      tool: name,
      toolUseId,
      input: block.input ?? {},
    });
  }
  return out;
}

function fromUser(obj, turnId, state) {
  const out = [];
  const content = obj?.message?.content;
  if (!Array.isArray(content)) {
    return out;
  }
  for (const block of content) {
    if (block?.type !== "tool_result") {
      continue;
    }
    const toolUseId = String(block.tool_use_id || "");
    // Pair the result to its start by id. A miss means the start was
    // deliberately suppressed (intercepted ExitPlanMode / AskUserQuestion) —
    // drop it so no phantom tool row appears.
    if (!state.pendingTools.has(toolUseId)) {
      continue;
    }
    const tool = state.pendingTools.get(toolUseId);
    state.pendingTools.delete(toolUseId);
    const isError = block.is_error === true;
    const resultSummary = summarizeToolResult(block.content);
    out.push({
      kind: "tool_use_end",
      turnId,
      tool,
      toolUseId,
      ok: !isError,
      ...(resultSummary ? { resultSummary } : {}),
    });
  }
  return out;
}

function fromResult(obj, turnId, state) {
  // Last-resort fallback: a whole turn with no text surfaces the result
  // line's top-level `result` string so the bubble isn't empty.
  if (state.anyTextEmitted) {
    return [];
  }
  const text = typeof obj.result === "string" ? obj.result : "";
  if (!text) {
    return [];
  }
  state.anyTextEmitted = true;
  return [{ kind: "text_delta", turnId, text }];
}

/**
 * Parse one line of `claude -p --output-format stream-json` output into
 * zero-or-more ChatEvents (without projectId — the envelope is stamped at
 * emission). Non-JSON and decorative lines are skipped.
 */
export function parseStreamLine(line, turnId, state) {
  const trimmed = String(line || "").trim();
  if (!trimmed) {
    return [];
  }
  let obj;
  try {
    obj = JSON.parse(trimmed);
  } catch {
    return [];
  }
  switch (obj?.type) {
    case "stream_event":
      return fromStreamEvent(obj, turnId, state);
    case "assistant":
      return fromAssistant(obj, turnId, state);
    case "user":
      return fromUser(obj, turnId, state);
    case "result":
      return fromResult(obj, turnId, state);
    default:
      return [];
  }
}

// ---------------------------------------------------------------------------
// Plan recovery from the persisted transcript
// ---------------------------------------------------------------------------

const MIN_PLAN_CHARS = 200;

/** Recover the plan from the transcript when ExitPlanMode arrived empty: the
 * most recent substantial assistant text block, else thinking block. */
export function recoverPlanFromTranscript(contents) {
  let bestText = "";
  let bestThinking = "";
  for (const line of String(contents || "").split("\n")) {
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    if (obj?.type !== "assistant") {
      continue;
    }
    const content = obj?.message?.content;
    if (!Array.isArray(content)) {
      continue;
    }
    for (const block of content) {
      if (block?.type === "text" && typeof block.text === "string") {
        if (block.text.trim().length >= MIN_PLAN_CHARS) {
          bestText = block.text;
        }
      } else if (block?.type === "thinking" && typeof block.thinking === "string") {
        if (block.thinking.trim().length >= MIN_PLAN_CHARS) {
          bestThinking = block.thinking;
        }
      }
    }
  }
  return bestText || bestThinking;
}

export function recoverPlanFromSession(workspace, sessionId, env = process.env) {
  try {
    const contents = fs.readFileSync(sessionJsonlPath(workspace, sessionId, env), "utf8");
    return recoverPlanFromTranscript(contents);
  } catch {
    return "";
  }
}

// ---------------------------------------------------------------------------
// Review loop (silent, best-effort, caps mirrored from the donor: 2/3/2)
// ---------------------------------------------------------------------------

export const MAX_STRUCTURE_ROUNDS = 2;
export const MAX_FUNCTIONAL_ROUNDS = 3;
export const MAX_CRAFT_ROUNDS = 2;

/** Read every `*.episode.json` sidecar under `dir` (skip-list honored) and
 * collect `validation.warnings`. Best-effort; malformed sidecars skipped. */
export function collectEpisodeWarnings(dir) {
  const out = [];
  const skip = skipDirNames();
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    let dirents;
    try {
      dirents = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of dirents) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (!skip.has(entry.name)) {
          stack.push(full);
        }
        continue;
      }
      if (!entry.isFile() || !entry.name.endsWith(".episode.json")) {
        continue;
      }
      let json;
      try {
        json = JSON.parse(fs.readFileSync(full, "utf8"));
      } catch {
        continue;
      }
      const warnings = json?.validation?.warnings;
      if (!Array.isArray(warnings)) {
        continue;
      }
      for (const w of warnings) {
        out.push({
          part: String(w?.part ?? ""),
          kind: String(w?.kind ?? ""),
          detail: String(w?.detail ?? ""),
          severity: String(w?.severity ?? "warning"),
        });
      }
    }
  }
  return out;
}

/** Severity routing is the driver's ONLY gate (contract §1): `error` blocks. */
export function isBlocking(warning) {
  return warning.severity === "error";
}

export function isFunctional(warning) {
  return warning.kind === "functional";
}

function warningLines(warnings) {
  return warnings.map((w) => `- [${w.part}] ${w.kind}: ${w.detail}`).join("\n");
}

export function buildStructurePrompt(warnings) {
  if (!warnings.length) {
    return null;
  }
  return (
    "An automatic structural check found these blocking problems in the " +
    "episode(s) you just built. Fix every one, then regenerate with the " +
    "dramacode generator until the check is clean:\n\n" +
    `${warningLines(warnings)}\n`
  );
}

export function buildFunctionalPrompt(warnings) {
  if (!warnings.length) {
    return null;
  }
  return (
    "An automatic DRAMATIC-FUNCTION check found these problems in the " +
    "episode(s) you just built — the episode renders but does not work as " +
    "drama. Re-read the beat-sheet rules in the dramacode skill's " +
    "references, fix the story/shots in the episode source, and regenerate " +
    "until every functional check is clean:\n\n" +
    `${warningLines(warnings)}\n`
  );
}

export function buildCraftPrompt(hints = []) {
  let body =
    "Structure and dramatic function are clean. Do ONE craft verification " +
    "pass now. Re-render the episode so the generator refreshes " +
    "<stem>_review/_board.png and <stem>_review/_poster.png, `Read` the " +
    "board contact sheet and the poster, and check continuity across shots, " +
    "composition, and caption legibility. Fix anything wrong in the episode " +
    "source, regenerate, and re-check. If everything already reads right, " +
    "change nothing. Then stop.\n";
  if (hints.length) {
    body +=
      "\nThe deterministic checks flagged these advisories (verify before " +
      "acting — some may be intentional):\n" +
      `${warningLines(hints)}\n`;
  }
  return body;
}

// ---------------------------------------------------------------------------
// Subprocess driver
// ---------------------------------------------------------------------------

function spawnClaude(claudePath, args, { workspace, env, renderProvider }) {
  // Test stubs are node scripts; spawn them through the current node binary
  // so they need no chmod/shebang gymnastics on any platform.
  const viaNode = /\.(mjs|cjs|js)$/.test(claudePath);
  const bin = viaNode ? process.execPath : claudePath;
  const argv = viaNode ? [claudePath, ...args] : args;
  return spawn(bin, argv, {
    cwd: workspace,
    env: buildChildEnv(env, { renderProvider }),
    stdio: ["pipe", "pipe", "pipe"],
  });
}

function killChild(child) {
  try {
    child.kill("SIGKILL");
  } catch {
    // already gone
  }
}

function waitForExit(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode) {
      resolve();
      return;
    }
    child.once("close", () => resolve());
    child.once("error", () => resolve());
  });
}

function shortId(id) {
  return String(id).slice(0, 8);
}

/**
 * Spawn one `claude -p` turn, stream + translate its output, snapshot/diff
 * the workspace, and forward ChatEvents to `onEvent`. Emits turn_start first
 * and turn_end last; failures emit `error` **then** `turn_end`. On abort,
 * kills the child and emits `error{message:"cancelled"}` + `turn_end`.
 *
 * Returns `{ proposedPlan, cancelled, sawOutput }` — `proposedPlan` is a
 * string when ExitPlanMode fired (possibly empty after recovery) and null
 * otherwise; the autopilot gate is plan-PRESENT, not plan-non-empty.
 */
export async function spawnTurn({
  workspace,
  sessionId,
  message,
  imagePaths = [],
  turnId,
  phase,
  model = "",
  renderProvider = "",
  onEvent,
  signal,
  env = process.env,
}) {
  onEvent({ kind: "turn_start", turnId, phase: phaseTag(phase) });

  const fail = (msg) => {
    onEvent({ kind: "error", turnId, message: msg });
    onEvent({ kind: "turn_end", turnId });
  };

  const claudePath = resolveClaude(env);
  if (!claudePath) {
    fail("`claude` CLI not found. Install Claude Code (https://claude.ai/install).");
    return { proposedPlan: null, cancelled: false, sawOutput: false };
  }

  try {
    fs.mkdirSync(workspace, { recursive: true });
  } catch (error) {
    fail(`failed to create workspace dir: ${error?.message || error}`);
    return { proposedPlan: null, cancelled: false, sawOutput: false };
  }

  const preSnapshot = snapshotWorkspace(workspace);
  const args = buildCommandArgs({ workspace, phase, sessionId, model, env });
  const resume = args.includes("--resume");
  log(
    `turn ${phase} start session=${shortId(sessionId)} (${resume ? "resume" : "new"})` +
      `${model ? ` model=${model}` : ""}`,
  );

  let child;
  try {
    child = spawnClaude(claudePath, args, { workspace, env, renderProvider });
  } catch (error) {
    fail(`failed to spawn claude: ${error?.message || error}`);
    return { proposedPlan: null, cancelled: false, sawOutput: false };
  }

  // Feed the stream-json user message (prompt + image blocks) and close stdin
  // so claude's `-p` reader sees EOF and starts the turn.
  child.stdin.on("error", () => {});
  child.stdin.end(streamJsonInput(message, imagePaths));

  // Drain stderr concurrently: an undrained pipe deadlocks the child, and a
  // fast failure (bad session id, auth, missing node) prints its reason here
  // with nothing on stdout.
  let stderrBuf = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    if (debugEnabled()) {
      process.stderr.write(`[circuit:claude:err] ${chunk}`);
    }
    if (stderrBuf.length < 8192) {
      stderrBuf += chunk;
    }
  });

  const state = newStreamState();
  let cancelled = false;
  let sawOutput = false;
  let proposedPlan = null;
  let artifactsChanged = false;
  let runningSnapshot = preSnapshot;

  const onAbort = () => {
    cancelled = true;
    killChild(child);
  };
  if (signal) {
    if (signal.aborted) {
      onAbort();
    } else {
      signal.addEventListener("abort", onAbort, { once: true });
    }
  }

  const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  try {
    for await (const line of rl) {
      if (cancelled) {
        break;
      }
      if (debugEnabled()) {
        process.stderr.write(`[circuit:claude:out] ${line}\n`);
      }
      const events = parseStreamLine(line, turnId, state);
      const stopTurn = state.planProposed || state.questionsAsked;
      let toolJustEnded = false;
      for (let event of events) {
        sawOutput = true;
        if (event.kind === "tool_use_end") {
          toolJustEnded = true;
        }
        if (event.kind === "plan_proposed") {
          // Empty plan (model exited plan mode without restating it, typical
          // on resume, no planFilePath either): recover from the transcript.
          if (!event.plan.trim()) {
            event = {
              ...event,
              plan: recoverPlanFromSession(workspace, sessionId, env),
            };
          }
          proposedPlan = event.plan;
        }
        onEvent(event);
      }
      // Incremental artifact emission: when a tool just finished, diff against
      // the running snapshot so artifacts materialize mid-build; advancing the
      // snapshot keeps the final diff from re-emitting the same files.
      if (toolJustEnded) {
        const nowSnapshot = snapshotWorkspace(workspace);
        const incremental = diffSnapshots(runningSnapshot, nowSnapshot, turnId);
        if (incremental.length) {
          artifactsChanged = true;
          for (const event of incremental) {
            onEvent(event);
          }
        }
        runningSnapshot = nowSnapshot;
      }
      // ExitPlanMode (plan ready) or AskUserQuestion (preference fork) ends
      // the turn deterministically — kill rather than waiting for `-p` EOF.
      if (stopTurn) {
        killChild(child);
        break;
      }
    }
  } catch {
    // stream torn down (kill/cancel) — fall through to the wait
  } finally {
    rl.close();
  }

  await waitForExit(child);
  if (signal) {
    signal.removeEventListener("abort", onAbort);
  }

  // Silent failure: claude exited without emitting any stream-json.
  if (!cancelled && !sawOutput) {
    const detail = stderrBuf.trim() || `claude exited without output (code ${child.exitCode})`;
    onEvent({ kind: "error", turnId, message: `claude produced no response: ${detail}` });
  }

  // Post-turn workspace diff — even when cancelled (the user still wants to
  // see artifacts produced before the cancel).
  const postSnapshot = snapshotWorkspace(workspace);
  const diffEvents = diffSnapshots(runningSnapshot, postSnapshot, turnId);
  if (diffEvents.length) {
    artifactsChanged = true;
  }
  for (const event of diffEvents) {
    onEvent(event);
  }

  // Automatic post-build review, silent, inside this build turn.
  if (phase === PHASE.IMPLEMENT && !cancelled && sawOutput && artifactsChanged) {
    await runReviewFixLoop({
      claudePath,
      workspace,
      sessionId,
      turnId,
      model,
      renderProvider,
      onEvent,
      signal,
      env,
    });
    // Then the screening room WATCHES the cut and closes the quality loop:
    // critic verdict → targeted re-render → re-screen (behind CIRCUIT_SCREENING).
    // Skip it while the cut is still structurally broken — a critic can't
    // judge an episode that didn't render clean.
    const stillBlocked =
      collectEpisodeWarnings(workspace).filter(isBlocking).length > 0;
    if (!cancelled && !stillBlocked && screeningEnabled(env)) {
      await runScreeningLoop({
        claudePath,
        workspace,
        sessionId,
        turnId,
        model,
        renderProvider,
        onEvent,
        signal,
        env,
      });
    }
  }

  if (cancelled) {
    onEvent({ kind: "error", turnId, message: "cancelled" });
  }
  onEvent({ kind: "turn_end", turnId });
  log(`turn ${phase} end session=${shortId(sessionId)}${cancelled ? " (cancelled)" : ""}`);
  return { proposedPlan, cancelled, sawOutput };
}

/** One silent review round: spawn a Review-phase child, drain its stdout to
 * EOF WITHOUT parsing (review chatter never reaches the user), then diff the
 * workspace and surface changed artifacts. Returns whether files changed. */
async function runReviewRound({
  claudePath,
  workspace,
  sessionId,
  turnId,
  model,
  prompt,
  renderProvider = "",
  onEvent,
  signal,
  env,
}) {
  const pre = snapshotWorkspace(workspace);
  const args = buildCommandArgs({ workspace, phase: PHASE.REVIEW, sessionId, model, env });
  let child;
  try {
    child = spawnClaude(claudePath, args, { workspace, env, renderProvider });
  } catch {
    return false; // best-effort: a build that can't be reviewed just ends
  }
  child.stdin.on("error", () => {});
  child.stdin.end(streamJsonInput(prompt));
  child.stderr.resume();

  const onAbort = () => killChild(child);
  if (signal) {
    if (signal.aborted) {
      onAbort();
    } else {
      signal.addEventListener("abort", onAbort, { once: true });
    }
  }
  if (debugEnabled()) {
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => process.stderr.write(`[circuit:claude:review] ${chunk}`));
  } else {
    child.stdout.resume(); // drain so a full pipe can't deadlock the child
  }
  await waitForExit(child);
  if (signal) {
    signal.removeEventListener("abort", onAbort);
  }

  const post = snapshotWorkspace(workspace);
  const diff = diffSnapshots(pre, post, turnId);
  for (const event of diff) {
    onEvent(event); // only artifact diffs surface from a review round
  }
  return diff.length > 0;
}

function emitUnresolvedNote(turnId, label, remaining, onEvent) {
  const parts = [...new Set(remaining.map((w) => w.part).filter(Boolean))].sort();
  onEvent({
    kind: "text_delta",
    turnId,
    text:
      `\n\n_Note: automatic ${label} review left ${remaining.length} issue(s) ` +
      `unresolved (parts: ${parts.length ? parts.join(", ") : "episode"}). ` +
      "You may want to inspect those parts._",
  });
}

/**
 * The silent 3-phase post-build review loop (contract §2, donor caps 2/3/2):
 *
 * 1. **Structure** (≤2): while any `severity:"error"` warning remains in the
 *    `*.episode.json` sidecars, resume the session with the warning list.
 * 2. **Dramatic function** (≤3): while any `kind:"functional"` warning
 *    remains, fix against the beat-sheet rules.
 * 3. **Craft** (≤2, ALWAYS runs once): re-render board + poster, Read them,
 *    fix continuity/composition/caption legibility; break when a round
 *    changes no files. Non-blocking leftovers seed the prompt as hints.
 *
 * Best-effort throughout — never fails the build turn.
 */
export async function runReviewFixLoop({
  claudePath,
  workspace,
  sessionId,
  turnId,
  model = "",
  renderProvider = "",
  onEvent,
  signal,
  env = process.env,
}) {
  let changed = false;
  const aborted = () => Boolean(signal?.aborted);
  const round = (prompt) =>
    runReviewRound({
      claudePath,
      workspace,
      sessionId,
      turnId,
      model,
      prompt,
      renderProvider,
      onEvent,
      signal,
      env,
    });

  // Phase 1 — structure (blocking = severity "error").
  for (let i = 0; i < MAX_STRUCTURE_ROUNDS; i += 1) {
    if (aborted()) return changed;
    const blocking = collectEpisodeWarnings(workspace).filter(isBlocking);
    const prompt = buildStructurePrompt(blocking);
    if (!prompt) break;
    log(`review structure round ${i + 1}: ${blocking.length} blocking warning(s)`);
    changed = (await round(prompt)) || changed;
  }
  if (aborted()) return changed;
  const structureRemaining = collectEpisodeWarnings(workspace).filter(isBlocking);
  if (structureRemaining.length) {
    emitUnresolvedNote(turnId, "structure", structureRemaining, onEvent);
    return changed;
  }

  // Phase 2 — dramatic function (kind "functional").
  for (let i = 0; i < MAX_FUNCTIONAL_ROUNDS; i += 1) {
    if (aborted()) return changed;
    const functional = collectEpisodeWarnings(workspace).filter(isFunctional);
    const prompt = buildFunctionalPrompt(functional);
    if (!prompt) break;
    log(`review functional round ${i + 1}: ${functional.length} functional warning(s)`);
    changed = (await round(prompt)) || changed;
  }
  if (aborted()) return changed;
  const functionalRemaining = collectEpisodeWarnings(workspace).filter(isFunctional);
  if (functionalRemaining.length) {
    emitUnresolvedNote(turnId, "dramatic-function", functionalRemaining, onEvent);
    return changed;
  }

  // Phase 3 — craft. ALWAYS at least one round; break when a round changes
  // nothing. Remaining advisory warnings seed the prompt as hints.
  let hints = collectEpisodeWarnings(workspace).filter(
    (w) => !isBlocking(w) && !isFunctional(w),
  );
  for (let i = 0; i < MAX_CRAFT_ROUNDS; i += 1) {
    if (aborted()) return changed;
    log(`review craft round ${i + 1}`);
    const roundChanged = await round(buildCraftPrompt(hints));
    changed = roundChanged || changed;
    if (!roundChanged) {
      break;
    }
    if (i + 1 < MAX_CRAFT_ROUNDS) {
      hints = collectEpisodeWarnings(workspace).filter(
        (w) => !isBlocking(w) && !isFunctional(w),
      );
    }
  }
  return changed;
}

// ---------------------------------------------------------------------------
// Screening room — the quality gate (the critic that closes the quality loop)
// ---------------------------------------------------------------------------
//
// After the mechanical review loop clears STRUCTURE/FUNCTION/CRAFT, a critic
// actually WATCHES the rendered cut (the screening-room skill: sample frames,
// Read them, score the film) and returns a ```screening-report verdict. When
// the cut misses the bar and carries blocker/major notes, the driver resumes
// the session with those notes as fix instructions — reroll the named shots,
// re-time the beat, fix the orientation — re-renders, and re-screens. Up to
// MAX_SCREENING_ROUNDS cycles. Silent + best-effort like the review phases;
// never fails the build. Behind CIRCUIT_SCREENING (default on).

// Backstop only — the loop keeps going until the critic PASSES the bar, or it
// can no longer improve. This cap just bounds cost/oscillation when a film
// stubbornly won't converge (stochastic rerolls). Raise it with
// CIRCUIT_SCREENING_MAX_ROUNDS when you want to grind harder for quality.
export const MAX_SCREENING_ROUNDS = 6;

/** The convergence backstop: CIRCUIT_SCREENING_MAX_ROUNDS or the default. */
export function screeningMaxRounds(env = process.env) {
  const raw = Number.parseInt(String(env.CIRCUIT_SCREENING_MAX_ROUNDS ?? "").trim(), 10);
  return Number.isInteger(raw) && raw > 0 ? Math.min(raw, 30) : MAX_SCREENING_ROUNDS;
}

/** CIRCUIT_SCREENING gate: on by default; "0"/"false"/"off"/"no" disables. */
export function screeningEnabled(env = process.env) {
  const raw = String(env.CIRCUIT_SCREENING ?? "").trim().toLowerCase();
  if (raw === "") return true;
  return !["0", "false", "off", "no"].includes(raw);
}

/** Numeric overall score from a report, or null. */
function screeningScore(report) {
  const v = Number(report?.overall_1_10);
  return Number.isFinite(v) ? v : null;
}

/** The episode deliverables in a workspace (mp4 + sidecar + srt + review dir)
 * — what "the cut" is. Used to snapshot/restore the best-scoring version so
 * extra screening rounds can never ship a worse result than a prior round. */
function episodeDeliverables(workspace) {
  const epDir = path.join(workspace, "episodes");
  let names = [];
  try {
    names = fs.readdirSync(epDir).filter((n) => n.endsWith(".mp4"));
  } catch {
    return [];
  }
  return names.map((mp4) => {
    const stem = mp4.slice(0, -4);
    return {
      files: [`${stem}.mp4`, `${stem}.episode.json`, `${stem}.srt`].map((f) =>
        path.join(epDir, f),
      ),
      reviewDir: path.join(epDir, `${stem}_review`),
    };
  });
}

function snapshotBestCut(workspace, tag) {
  const dest = path.join(workspace, ".video", "keepbest", String(tag));
  try {
    fs.rmSync(dest, { recursive: true, force: true });
    fs.mkdirSync(dest, { recursive: true });
    for (const d of episodeDeliverables(workspace)) {
      for (const f of d.files) {
        if (fs.existsSync(f)) fs.copyFileSync(f, path.join(dest, path.basename(f)));
      }
      if (fs.existsSync(d.reviewDir)) {
        fs.cpSync(d.reviewDir, path.join(dest, path.basename(d.reviewDir)), {
          recursive: true,
        });
      }
    }
    return dest;
  } catch {
    return null;
  }
}

function restoreBestCut(workspace, snapshotDir) {
  if (!snapshotDir) return false;
  try {
    const epDir = path.join(workspace, "episodes");
    for (const entry of fs.readdirSync(snapshotDir)) {
      const src = path.join(snapshotDir, entry);
      const dst = path.join(epDir, entry);
      if (fs.statSync(src).isDirectory()) {
        fs.rmSync(dst, { recursive: true, force: true });
        fs.cpSync(src, dst, { recursive: true });
      } else {
        fs.copyFileSync(src, dst);
      }
    }
    return true;
  } catch {
    return false;
  }
}

const SCREENING_REPORT_FENCE = /```screening-report\s*\n([\s\S]*?)```/g;
const SCREENING_REPORT_KEYS = ["overall_1_10", "dimension_scores", "pass_at_bar", "notes"];

/**
 * Extract + parse the LAST ` ```screening-report ` fenced JSON block from a
 * critic transcript. Returns the report object, or null when absent / invalid
 * JSON / missing a required key. Mirrors the skill's Python parser (last block
 * wins, same discipline as "one JSON line, last line wins").
 */
export function parseScreeningReport(text) {
  const source = String(text || "");
  const re = new RegExp(SCREENING_REPORT_FENCE.source, "g");
  let match;
  let last = null;
  while ((match = re.exec(source)) !== null) {
    last = match[1];
  }
  if (last === null) {
    return null;
  }
  let report;
  try {
    report = JSON.parse(last.trim());
  } catch {
    return null;
  }
  if (!report || typeof report !== "object" || Array.isArray(report)) {
    return null;
  }
  if (SCREENING_REPORT_KEYS.some((key) => !(key in report))) {
    return null;
  }
  if (!Array.isArray(report.notes)) {
    return null;
  }
  return report;
}

/** Blocker/major notes — the ones that trigger a fix round. */
export function actionableScreeningNotes(report) {
  const notes = Array.isArray(report?.notes) ? report.notes : [];
  return notes.filter(
    (n) => n && (n.severity === "blocker" || n.severity === "major"),
  );
}

/** Reconstruct the critic's assistant text from one stream-json line — text
 * deltas, consolidated assistant text blocks, and the final result string —
 * so the ```screening-report can be recovered however claude surfaced it. */
function accumulateAssistantText(line, acc) {
  let obj;
  try {
    obj = JSON.parse(String(line || "").trim());
  } catch {
    return;
  }
  if (obj?.type === "stream_event") {
    const ev = obj.event;
    if (ev?.type === "content_block_delta" && ev.delta?.type === "text_delta") {
      if (typeof ev.delta.text === "string") acc.push(ev.delta.text);
    }
  } else if (obj?.type === "assistant") {
    const content = obj?.message?.content;
    if (Array.isArray(content)) {
      for (const block of content) {
        if (block?.type === "text" && typeof block.text === "string") {
          acc.push(block.text);
        }
      }
    }
  } else if (obj?.type === "result" && typeof obj.result === "string") {
    acc.push(obj.result);
  }
}

/** The per-round message that runs the critic. */
export function buildScreeningCriticPrompt() {
  return (
    "Screen the episode(s) you just built. Run the screening-room bundle " +
    "(`python ~/.claude/skills/screening-room/scripts/bundle <the episode " +
    ".mp4>`), `Read` every sampled frame plus the board and poster, judge " +
    "them against the rubric, and emit exactly one ```screening-report JSON " +
    "block with the overall score, dimension scores, pass_at_bar, and " +
    "department-routed, shot-specific notes. Output nothing else."
  );
}

/** The fix-round message: the critic's actionable notes as instructions. */
export function buildScreeningFixPrompt(notes) {
  const lines = notes
    .map((n) => {
      const shots =
        Array.isArray(n.shot_ids) && n.shot_ids.length
          ? n.shot_ids.join(", ")
          : "episode";
      const dept = n.department || "crew";
      const fix = n.fix ? ` → FIX: ${n.fix}` : "";
      return `- [${dept}] (${n.severity}) ${shots}: ${n.note || ""}${fix}`;
    })
    .join("\n");
  return (
    "The screening room watched the rendered episode and it did NOT pass the " +
    "quality bar. Fix these flagged issues in the episode source, then " +
    "regenerate with the dramacode generator so the episode re-renders. " +
    "Reroll only the named shots for consistency or orientation, re-time or " +
    "rewrite the beat as noted, fix any orientation/aspect defect, and " +
    "re-render. Work silently:\n\n" +
    `${lines}\n`
  );
}

/** One critic pass: spawn a SCREENING-phase child, capture its stdout WITHOUT
 * surfacing it (the critique is internal), parse the ```screening-report, and
 * return it (or null). Best-effort — a critic that can't run just returns
 * null and the loop ends. */
async function runScreeningRound({
  claudePath,
  workspace,
  sessionId,
  turnId,
  model,
  renderProvider = "",
  signal,
  env,
}) {
  const args = buildCommandArgs({ workspace, phase: PHASE.SCREENING, sessionId, model, env });
  let child;
  try {
    child = spawnClaude(claudePath, args, { workspace, env, renderProvider });
  } catch {
    return null;
  }
  child.stdin.on("error", () => {});
  child.stdin.end(streamJsonInput(buildScreeningCriticPrompt()));
  child.stderr.resume();

  const onAbort = () => killChild(child);
  if (signal) {
    if (signal.aborted) {
      onAbort();
    } else {
      signal.addEventListener("abort", onAbort, { once: true });
    }
  }

  const acc = [];
  const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  try {
    for await (const line of rl) {
      if (signal?.aborted) break;
      if (debugEnabled()) {
        process.stderr.write(`[circuit:claude:screening] ${line}\n`);
      }
      accumulateAssistantText(line, acc);
    }
  } catch {
    // stream torn down — parse whatever we captured
  } finally {
    rl.close();
  }
  await waitForExit(child);
  if (signal) {
    signal.removeEventListener("abort", onAbort);
  }
  return parseScreeningReport(acc.join(""));
}

/**
 * The screening loop: screen → (if below bar with blocker/major notes) fix +
 * re-render → re-screen, up to MAX_SCREENING_ROUNDS. The fix round rides the
 * REVIEW phase (silent, drains, surfaces changed artifacts) with the critic's
 * notes as its prompt. Best-effort; never throws.
 */
export async function runScreeningLoop({
  claudePath,
  workspace,
  sessionId,
  turnId,
  model = "",
  renderProvider = "",
  onEvent,
  signal,
  env = process.env,
}) {
  const aborted = () => Boolean(signal?.aborted);
  const maxRounds = screeningMaxRounds(env);
  let changed = false;
  // Keep the best-scored cut so extra rounds are always safe: a fix that makes
  // things worse can't ship because we restore the best at the end.
  let bestScore = -Infinity;
  let bestSnapshot = null;
  for (let i = 0; i < maxRounds; i += 1) {
    if (aborted()) break;
    const report = await runScreeningRound({
      claudePath,
      workspace,
      sessionId,
      turnId,
      model,
      renderProvider,
      signal,
      env,
    });
    if (!report) {
      log(`screening round ${i + 1}: no verdict (skipped)`);
      break;
    }
    const overall = report.overall_1_10;
    const pass = report.pass_at_bar === true;
    const score = screeningScore(report);
    log(
      `screening round ${i + 1}/${maxRounds}: verdict=${pass ? "pass" : "fail"} ` +
        `overall=${overall} (best=${bestScore === -Infinity ? "-" : bestScore})`,
    );
    // Snapshot this cut if it's the best we've seen (best-effort).
    if (score !== null && score > bestScore) {
      bestScore = score;
      bestSnapshot = snapshotBestCut(workspace, `r${i + 1}-s${score}`) || bestSnapshot;
    }
    if (pass) {
      log(`screening: PASSED the bar at round ${i + 1} (overall=${overall})`);
      return changed; // current on-disk cut is the passing one
    }
    const notes = actionableScreeningNotes(report);
    if (notes.length === 0) {
      log(`screening round ${i + 1}: below bar but no actionable notes — stopping`);
      break;
    }
    if (i === maxRounds - 1) {
      log(`screening: hit round cap (${maxRounds}) without passing — keeping best cut`);
      break; // don't fix on the last round; nothing would re-screen it
    }
    if (aborted()) break;
    log(`screening round ${i + 1}: ${notes.length} note(s) → targeted fix round`);
    const fixChanged = await runReviewRound({
      claudePath,
      workspace,
      sessionId,
      turnId,
      model,
      prompt: buildScreeningFixPrompt(notes),
      renderProvider,
      onEvent,
      signal,
      env,
    });
    changed = fixChanged || changed;
    if (!fixChanged) {
      log(`screening round ${i + 1}: fix changed nothing — cannot improve, stopping`);
      break; // no progress possible; re-screening the same cut is waste
    }
  }
  // Ship the best cut seen, not necessarily the last (a late fix may regress).
  if (bestSnapshot && restoreBestCut(workspace, bestSnapshot)) {
    log(`screening: restored best cut (overall=${bestScore})`);
  }
  return changed;
}

// ---------------------------------------------------------------------------
// Session JSONL → chat history rehydration (donor: chat.rs)
// ---------------------------------------------------------------------------

/** Prefix of the synthetic approve-plan prompt; rehydration drops user lines
 * starting with it. Keep in sync with `approvedPlanMessage`. */
export const APPROVE_PLAN_PREAMBLE = "The plan below is approved. Implement it now";

/** Marker beginning the machine-readable attachment note; stripped from
 * rehydrated user bubbles. */
export const ATTACHMENT_NOTE_MARKER = "\n\n[Attached reference image";

/** The synthetic message that kicks off the build phase from an approved (or
 * auto-approved) plan. */
export function approvedPlanMessage(planText) {
  const body = String(planText || "").trim()
    ? String(planText)
    : "(Implement the plan you just designed in this session.)";
  return (
    "The plan below is approved. Implement it now: write the episode " +
    "source and generate the episode and all artifacts as described.\n\n" +
    body
  );
}

function extractVisibleText(content) {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .filter((b) => b && b.type === "text" && typeof b.text === "string")
      .map((b) => b.text)
      .join("\n\n");
  }
  return "";
}

/**
 * Parse a Claude Code session JSONL transcript into ChatSessionState history:
 * one entry per user prompt, one grouped assistant entry per response whose
 * `blocks` rebuild the live trace (thinking/text/tool_use with resolved
 * status, summary, and timings). `isMeta` injections, the synthetic
 * approve-plan prompt, attachment notes, and intercepted
 * ExitPlanMode/AskUserQuestion tool calls are dropped.
 */
export function parseSessionHistory(contents) {
  const history = [];
  let blocks = [];
  let textParts = [];
  let turnAt = 0;
  let pending = new Map(); // toolUseId -> index into blocks

  const flush = () => {
    if (blocks.length) {
      history.push({
        role: "assistant",
        content: textParts.join("\n\n"),
        at: turnAt,
        blocks,
      });
    }
    blocks = [];
    textParts = [];
    turnAt = 0;
    pending = new Map();
  };

  for (const rawLine of String(contents || "").split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    if (!obj || typeof obj !== "object" || obj.isMeta === true) {
      continue;
    }
    const type = obj.type;
    if (type !== "user" && type !== "assistant") {
      continue;
    }
    const at = Date.parse(obj.timestamp || "") || 0;
    const content = obj?.message?.content;

    if (type === "assistant") {
      if (turnAt === 0) {
        turnAt = at;
      }
      if (!Array.isArray(content)) {
        continue;
      }
      for (const block of content) {
        const bt = block?.type;
        if (bt === "thinking") {
          if (typeof block.thinking === "string" && block.thinking) {
            blocks.push({ kind: "thinking", text: block.thinking, at });
          }
        } else if (bt === "text") {
          if (typeof block.text === "string" && block.text) {
            textParts.push(block.text);
            blocks.push({ kind: "text", text: block.text });
          }
        } else if (bt === "tool_use") {
          const name = String(block.name || "");
          // Not tool chips live — they become a plan / question card.
          if (name === "ExitPlanMode" || name === "AskUserQuestion") {
            continue;
          }
          const id = String(block.id || "");
          if (id) {
            pending.set(id, blocks.length);
          }
          blocks.push({
            kind: "tool_use",
            tool: name,
            toolUseId: id,
            input: block.input ?? {},
            status: "ok",
            at,
            endedAt: at,
          });
        }
      }
      continue;
    }

    // A user turn carrying tool_result blocks isn't a prompt — it resolves
    // the current assistant turn's pending tools and continues it.
    const isToolResult =
      Array.isArray(content) && content.some((b) => b?.type === "tool_result");
    if (isToolResult) {
      for (const block of content) {
        if (block?.type !== "tool_result") {
          continue;
        }
        const id = String(block.tool_use_id || "");
        if (!pending.has(id)) {
          continue;
        }
        const idx = pending.get(id);
        const target = blocks[idx];
        if (target && target.kind === "tool_use") {
          target.status = block.is_error === true ? "error" : "ok";
          target.endedAt = at;
          const summary = summarizeToolResult(block.content);
          if (summary) {
            target.resultSummary = summary;
          }
        }
      }
      continue;
    }

    // A real user prompt closes the previous assistant turn, then lands.
    flush();
    let text = extractVisibleText(content);
    const markerIdx = text.indexOf(ATTACHMENT_NOTE_MARKER);
    if (markerIdx !== -1) {
      text = text.slice(0, markerIdx);
    }
    const trimmed = text.trim();
    if (!trimmed || trimmed.startsWith(APPROVE_PLAN_PREAMBLE)) {
      continue;
    }
    history.push({ role: "user", content: trimmed, at, blocks: [] });
  }
  flush();
  return history;
}

// ---------------------------------------------------------------------------
// Attachments (reference images → <workspace>/inputs/)
// ---------------------------------------------------------------------------

const MAX_ATTACHMENTS = 6;
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

const IMAGE_EXTENSIONS = new Map([
  ["image/png", "png"],
  ["image/jpeg", "jpg"],
  ["image/jpg", "jpg"],
  ["image/webp", "webp"],
  ["image/gif", "gif"],
]);

function invalidArgument(message) {
  const err = new Error(message);
  err.code = "INVALID_ARGUMENT";
  err.statusCode = 400;
  return err;
}

/**
 * Decode and persist reference images into `<workspace>/inputs/` (uuid-named,
 * never the user-supplied name), returning workspace-relative paths. Written
 * before the turn spawns so they predate the mtime baseline; `inputs/` is on
 * the skip-list so they never surface as artifacts or catalog entries.
 */
export function persistAttachments(workspace, images = []) {
  if (images.length > MAX_ATTACHMENTS) {
    throw invalidArgument(`too many images: ${images.length} (max ${MAX_ATTACHMENTS})`);
  }
  const dir = path.join(workspace, "inputs");
  fs.mkdirSync(dir, { recursive: true });
  const rels = [];
  for (const image of images) {
    const ext = IMAGE_EXTENSIONS.get(String(image?.mediaType || "").trim().toLowerCase());
    if (!ext) {
      throw invalidArgument(`unsupported image type: ${image?.mediaType}`);
    }
    let bytes;
    try {
      bytes = Buffer.from(String(image?.dataBase64 || ""), "base64");
      // Node's base64 decoder is lenient; round-trip to reject garbage.
      if (!bytes.length && String(image?.dataBase64 || "").trim()) {
        throw new Error("empty decode");
      }
    } catch {
      throw invalidArgument("invalid base64 image data");
    }
    if (!bytes.length || bytes.length > MAX_ATTACHMENT_BYTES) {
      throw invalidArgument(
        `image must be 1..=${MAX_ATTACHMENT_BYTES} bytes, got ${bytes.length}`,
      );
    }
    const name = `${crypto.randomUUID()}.${ext}`;
    fs.writeFileSync(path.join(dir, name), bytes);
    rels.push(`inputs/${name}`);
  }
  return rels;
}

/** The note appended to a user message so the model opens the attached
 * images with Read. Begins with ATTACHMENT_NOTE_MARKER (stripped on
 * rehydration). */
export function attachmentNote(rels) {
  if (!rels.length) {
    return "";
  }
  return `${ATTACHMENT_NOTE_MARKER}(s): ${rels.join(", ")}. View each with the Read tool before responding.]`;
}

// ---------------------------------------------------------------------------
// Chat service — turn registry + autopilot chaining (donor: chat.rs)
// ---------------------------------------------------------------------------

/**
 * Create the chat orchestration service.
 *
 * - `projectDir(projectId)` → absolute workspace dir.
 * - `settings.read()` → `{ autoBuild, model }`.
 * - `emit(projectId, event)` → deliver one enveloped ChatEvent.
 *
 * `startTurn` returns the turnId synchronously (the run continues in the
 * background); **autopilot**: a PLAN turn that proposed a plan (ExitPlanMode
 * fired — plan-present, not plan-non-empty) chains straight into a build turn
 * when `autoBuild !== false`.
 */
export function createChatService({ projectDir, settings, emit, env = process.env }) {
  const turns = new Map(); // turnId -> { projectId, controller }

  function activeModel() {
    try {
      return settings.read().model || "";
    } catch {
      return "";
    }
  }

  function activeRenderProvider() {
    try {
      return settings.read().renderProvider || "";
    } catch {
      return "";
    }
  }

  function runTurn({ projectId, message, imagePaths, phase, turnId }) {
    const controller = new AbortController();
    turns.set(turnId, { projectId, controller });
    const workspace = projectDir(projectId);
    const sessionId = sessionIdForProject(projectId);
    const onEvent = (event) => emit(projectId, event);

    const run = spawnTurn({
      workspace,
      sessionId,
      message,
      imagePaths,
      turnId,
      phase,
      model: activeModel(),
      renderProvider: activeRenderProvider(),
      onEvent,
      signal: controller.signal,
      env,
    })
      .catch((error) => {
        // Defensive: spawnTurn reports its own failures; this catch only
        // guards a driver bug so the turn still closes error → turn_end.
        onEvent({ kind: "error", turnId, message: `driver failure: ${error?.message || error}` });
        onEvent({ kind: "turn_end", turnId });
        return { proposedPlan: null, cancelled: false, sawOutput: false };
      })
      .then((result) => {
        turns.delete(turnId);
        return result;
      });

    return { run };
  }

  function startTurn({ projectId, message, imagePaths = [], phase }) {
    const turnId = crypto.randomUUID();
    const { run } = runTurn({ projectId, message, imagePaths, phase, turnId });

    if (phase === PHASE.PLAN) {
      // Autopilot: after a plan turn that PROPOSED a plan, build it — gated
      // on the ExitPlanMode event, NOT on the plan text being non-empty (an
      // empty proposed plan must still build; the resumed session carries the
      // reasoning). A turn that stopped for questions proposes no plan.
      run.then(({ proposedPlan, cancelled }) => {
        if (proposedPlan === null || cancelled) {
          return;
        }
        let autoBuild = true;
        try {
          autoBuild = settings.read().autoBuild !== false;
        } catch {
          autoBuild = true;
        }
        if (!autoBuild) {
          return;
        }
        log(`autopilot: chaining build turn for project ${projectId}`);
        const buildTurnId = crypto.randomUUID();
        runTurn({
          projectId,
          message: approvedPlanMessage(proposedPlan),
          imagePaths: [],
          phase: PHASE.IMPLEMENT,
          turnId: buildTurnId,
        });
      });
    }

    return turnId;
  }

  function cancelTurn(turnId) {
    const entry = turns.get(turnId);
    if (!entry) {
      return false;
    }
    turns.delete(turnId);
    entry.controller.abort();
    return true;
  }

  function turnInProgress(projectId) {
    for (const entry of turns.values()) {
      if (entry.projectId === projectId) {
        return true;
      }
    }
    return false;
  }

  function sessionState(projectId) {
    const sessionId = sessionIdForProject(projectId);
    let history = [];
    try {
      const contents = fs.readFileSync(
        sessionJsonlPath(projectDir(projectId), sessionId, env),
        "utf8",
      );
      history = parseSessionHistory(contents);
    } catch {
      history = [];
    }
    return { sessionId, turnInProgress: turnInProgress(projectId), history };
  }

  function close() {
    for (const { controller } of turns.values()) {
      controller.abort();
    }
    turns.clear();
  }

  return { startTurn, cancelTurn, turnInProgress, sessionState, close };
}
