// Circuit chat driver — Node port of the donor's Rust
// `desktop/src-tauri/src/commands/claude_driver.rs` + `chat.rs` semantics,
// coded against docs/circuit-interfaces.md §2 (the frozen contract).
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

import { countWarnings, recordRevision } from "./revisions.mjs";
import {
  circuitHome,
  claudeConfigDir,
  parseLatestAiTitle,
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

const SESSION_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function resolvedSessionId(projectId, sessionId) {
  const value = String(sessionId || sessionIdForProject(projectId));
  if (!SESSION_ID_RE.test(value)) {
    const error = new Error("invalid chat session id");
    error.code = "INVALID_ARGUMENT";
    error.statusCode = 400;
    throw error;
  }
  return value;
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
  "You are running inside Autonomous Circuit, the AI PCB studio. Every user",
  "message is a request to design or refine a printed circuit board. You are",
  "in PLANNING mode: design, do not build. You MAY run read-only analysis",
  "(list the project, read product.json, parts.json, the board sources under",
  "boards/, and the .board.json sidecars) to ground the plan in what already",
  "exists, but do NOT write or edit any source file and do NOT run the",
  "circuit generator or produce/update board artifacts yet — the build",
  "happens only after the plan is approved.",
  "",
  "READ THE CATALOG BEFORE YOU WRITE A WORD. For a NEW board, invoke the",
  "circuit-analysis skill (~/.claude/skills/circuit-analysis) first — it",
  "carries the list of golden blocks that actually exist, and it is the only",
  "list that exists. Nothing may be offered, promised or planned that is not",
  "built from it. Recalling a part from training is how this app offers a",
  "person a radio, a battery or a light sensor it has never had a block for;",
  "the ask arrives, the refusal is written, and the very next screen reopens",
  "the same door. Also read circuitcode's protocol",
  "(~/.claude/skills/circuitcode). A full",
  "plan is an engineering spec: the golden blocks chosen (composition is",
  "blocks + glue only — never a novel IC circuit invented from a datasheet),",
  "the power budget math (source, per-rail current sums, headroom), the pin",
  "allocation table (every block pin → MCU pin/net), the board size against",
  "product.json's envelope, and the estimated parts-cost band. Mark any",
  "number a circuitlib table does not own as an estimate. The safety",
  "envelope is non-negotiable and refused at spec time, in the plan: no",
  "mains ever (low-voltage DC ≤24V only), battery power only via the sealed",
  "charge/protect block, radio only as certified modules. A trivial edit",
  "needs only the exact change and its consequence, one to three lines.",
  "",
  "PREFERENCES FIRST. For a NEW board (not a trivial edit), open the turn by",
  "asking 2-4 preference questions (power source, size class, PCBA vs bare",
  "PCB, must-have I/O). Emit them as ONE fenced block, exactly:",
  "```circuit-questions",
  '{"questions":[{"question":"…","header":"Power","multiSelect":false,',
  '"options":[{"label":"Let Circuit choose","description":"Recommended — we',
  ' pick the best for you"},{"label":"USB-C","description":"…"}]}]}',
  "```",
  "EVERY OPTION MUST BE BUILDABLE FROM THE CATALOG. Offering a choice we",
  "cannot deliver is the same error as designing it — the person picks it and",
  "we fail them one screen later. So never offer wireless, a battery, a",
  "screen, a knob or encoder, a motor, or light/motion/sound sensing: no",
  "block exists for any of them. Never offer a mains option, in any question,",
  "even to let the user rule it out. If the ask needs one of those, say so in",
  "prose above the questions, name the nearest thing we can build, and ask",
  "only about choices that are real.",
  "Give EVERY question a first option labelled \"Let Circuit choose\"",
  "(description: \"Recommended — we pick the best for you\"); when the user",
  "picks it, use your best default and proceed without re-asking. Emitting",
  "the block ends the turn — do not also propose a plan in the same turn.",
  "A trivial edit needs no questions.",
  "",
  "After the preferences are settled, finish the turn by emitting the",
  "COMPLETE plan inside ONE fenced block:",
  "```circuit-plan",
  "…the whole plan in markdown…",
  "```",
  "Restate the entire plan inside that fence every time, even if you already",
  "wrote it earlier in the conversation and even when resuming a prior",
  "session — the fence is what the app turns into the approve button, so a",
  "plan that is only prose leaves the user with nothing to approve. Never",
  "emit an empty or partial fence, and never write the plan to a file",
  "instead. (If an ExitPlanMode tool happens to be available, calling it with",
  "the same complete plan works too — but do not go looking for it.)",
].join("\n");

export const IMPLEMENT_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit, the AI PCB studio. The user has",
  "APPROVED a plan. Implement it now using the circuitcode skill: write the",
  "board source boards/<stem>.tsx (normal case boards/main.tsx; golden",
  "blocks + glue only), lock parts with the parts-book skill when the BOM",
  "changes, then run the generator",
  "(`python ~/.claude/skills/circuitcode/scripts/circuit boards/<stem>.tsx`)",
  "to build the .circuit.json IR, the .board.json sidecar, the _review/",
  "schematic and PCB images, and the _fab/ packet. The generator's verdict",
  "is its one stdout JSON line plus the sidecar's validation.warnings —",
  "read it, make the smallest responsible fix in the source, and re-run",
  "until the board builds clean. `Read` _review/_schematic.png and",
  "_review/_pcb.png before you call it done. Follow the circuitcode",
  "protocol. Do not re-plan or ask further questions unless a blocking",
  "ambiguity remains.",
].join("\n");

export const REVIEW_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit, the AI PCB studio. An automatic",
  "post-build review of the board you just built is running. Work SILENTLY:",
  "do not greet, explain, summarize, ask questions, or re-plan — just",
  "improve the board and regenerate with the circuitcode skill",
  "(`python ~/.claude/skills/circuitcode/scripts/circuit boards/<stem>.tsx`).",
  "The per-round message says what to check; fix by severity. STRUCTURE",
  "problems (severity \"error\" in the .board.json sidecars — compile",
  "errors, ERC/DRC violations, safety envelope, DFM blockers) are blocking",
  "and come first. ELECTRICAL-FUNCTION problems (power budget, unorderable",
  "or drifted parts, netlist mismatches) mean the board builds but would",
  "not work or could not be ordered — fix them next. CRAFT is a final",
  "visual pass: rebuild, `Read` <stem>_review/_schematic.png and",
  "<stem>_review/_pcb.png, and fix what reads wrong. Edit the TSX source",
  "(never the generated artifacts), regenerate, and stop as soon as the",
  "board is clean.",
].join("\n");

/** Appended to every phase prompt so the model knows the one absolute
 * directory this project lives in (donor: artifacts written elsewhere are
 * invisible to the app's snapshotter and catalog). */
export function workspaceDirective(workspace) {
  return (
    "PROJECT WORKSPACE. This project lives in the single absolute directory " +
    "below. Every file you create — product.json, parts.json, the board " +
    "sources under boards/, and every artifact the circuit generator " +
    "produces — MUST live inside it, and you MUST pass paths inside it to " +
    "the circuitcode tools. Do not create the project or write artifacts " +
    "anywhere else: Circuit only detects artifacts inside this directory, " +
    `so anything written outside it is invisible to the app.\n${workspace}`
  );
}

const DISABLE_HOOKS_SETTINGS = '{"disableAllHooks":true}';

export const PHASE = Object.freeze({
  PLAN: "plan",
  IMPLEMENT: "implement",
  REVIEW: "review",
});

function systemPromptForPhase(phase) {
  if (phase === PHASE.PLAN) return PLAN_SYSTEM_PROMPT;
  if (phase === PHASE.REVIEW) return REVIEW_SYSTEM_PROMPT;
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
  effort = "",
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
  // Reasoning effort is a product decision here, not a preference. Designing a
  // board means composing it, reading two rendered images and judging what is
  // wrong with them — under-thinking that produces a board that needs a repair
  // round, which costs far more than the tokens saved.
  if (effort) {
    args.push("--effort", String(effort));
  }
  return args;
}

/** Pipeline vars the circuit generator understands (contract §1); keys.env
 * supplies them, the real environment wins overall. */
const PIPELINE_ENV_VARS = new Set([
  "CIRCUIT_PARTS_ENGINE",
  "CIRCUIT_FAB",
  "CIRCUIT_TOOLCHAIN",
  "CIRCUIT_WALL_CLOCK_S",
]);

/** Parse ~/.autonomous-circuit/keys.env (KEY=value lines, # comments). The
 * paste-a-setting file: drop CIRCUIT_PARTS_ENGINE / CIRCUIT_FAB /
 * CIRCUIT_TOOLCHAIN / CIRCUIT_WALL_CLOCK_S here and every build — app or
 * terminal — picks them up. */
export function readKeysEnv(env = process.env) {
  const filePath = path.join(circuitHome(env), "keys.env");
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
    if (PIPELINE_ENV_VARS.has(key) && value) out[key] = value;
  }
  return out;
}

/** Env for the spawned child: inherited env + augmented PATH + the donor's
 * self-updater/background-task guards + pipeline settings (keys.env < real
 * environment). */
export function buildChildEnv(env = process.env) {
  const child = {
    ...readKeysEnv(env),
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

/** Extensions watched per §2: `.tsx .json .svg .png .zip .csv .md`. */
export const WATCHED_EXTENSIONS = new Set([
  "tsx",
  "json",
  "svg",
  "png",
  "zip",
  "csv",
  "md",
]);

/** Snapshot every watched file under `workspace` → Map(relPath → mtimeMs).
 * Recursive; skips inputs/, .circuit/, .claude/, blocks/, node_modules. */
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

/** A ```circuit-plan fenced block, or null.
 *
 * The tool-free path to a proposed plan. `claude -p` subprocesses are not
 * handed ExitPlanMode, so the fence is what actually carries plans in
 * production; ExitPlanMode stays supported for hosts that do provide it.
 * Mirrors the ```circuit-questions fence the client already renders.
 */
export function planFromFencedBlock(text) {
  if (typeof text !== "string" || !text) return null;
  const match = text.match(/```circuit-plan[ \t]*\r?\n([\s\S]*?)```/);
  if (!match) return null;
  const plan = match[1].trim();
  return plan || null;
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
      // A headless `claude -p` subprocess is not given ExitPlanMode, so a plan
      // that only ever arrives as a tool call never arrives at all — the turn
      // ends with a good plan in prose, no approve button, and a dead loop.
      // Verified 2026-08-10: the model searched for the tool, failed to find
      // it, wrote the plan to a file and said "say go". The fenced block is
      // the transport that does not depend on tool availability.
      if (typeof block.text === "string" && block.text) {
        const fenced = planFromFencedBlock(block.text);
        if (fenced && !state.planProposed) {
          state.planProposed = true;
          state.anyTextEmitted = true;
          out.push({ kind: "plan_proposed", turnId, plan: fenced });
        }
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

/**
 * Is every board in the workspace orderable right now?
 *
 * Returns null when no sidecar carries a `fab` block — "we do not know" is a
 * third answer and must not collapse into false, or the very first round would
 * look like a regression.
 */
export function workspaceFabReady(dir) {
  const skip = skipDirNames();
  const stack = [dir];
  let seen = 0;
  let ready = 0;
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
        if (!skip.has(entry.name)) stack.push(full);
        continue;
      }
      if (!entry.isFile() || !entry.name.endsWith(".board.json")) continue;
      try {
        const json = JSON.parse(fs.readFileSync(full, "utf8"));
        if (json?.fab && typeof json.fab.ready === "boolean") {
          seen += 1;
          if (json.fab.ready) ready += 1;
        }
      } catch {
        // malformed sidecar — tells us nothing either way
      }
    }
  }
  if (seen === 0) return null;
  return ready === seen;
}

export const MAX_STRUCTURE_ROUNDS = 2;
export const MAX_ELECTRICAL_ROUNDS = 3;
export const MAX_CRAFT_ROUNDS = 2;

/** Read every `*.board.json` sidecar under `dir` (skip-list honored) and
 * collect `validation.warnings`. Best-effort; malformed sidecars skipped. */
export function collectBoardWarnings(dir) {
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
      if (!entry.isFile() || !entry.name.endsWith(".board.json")) {
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

/** Phase-2 kinds (contract §2): the board builds but would not work or could
 * not be ordered. The severity gate stays the only *blocking* gate; this set
 * only routes non-blocking warnings into the electrical-function rounds. */
export const ELECTRICAL_KINDS = new Set([
  "functional",
  "power_budget",
  "part_not_orderable",
  "part_drift",
  "netlist_mismatch",
]);

export function isElectrical(warning) {
  return ELECTRICAL_KINDS.has(warning.kind);
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
    "board(s) you just built. Fix every one, then regenerate with the " +
    "circuitcode generator until the check is clean:\n\n" +
    `${warningLines(warnings)}\n`
  );
}

export function buildElectricalPrompt(warnings) {
  if (!warnings.length) {
    return null;
  }
  return (
    "An automatic ELECTRICAL-FUNCTION check found these problems in the " +
    "board(s) you just built — the board builds but would not work or could " +
    "not be ordered as-is (power budget, part orderability, part drift, " +
    "netlist parity). Fix the board source (re-lock parts with the " +
    "parts-book skill when a part must change), regenerate, and repeat " +
    "until every one is clear:\n\n" +
    `${warningLines(warnings)}\n`
  );
}

export function buildCraftPrompt(hints = []) {
  let body =
    "Structure and electrical function are clean. Do ONE craft verification " +
    "pass now. Rebuild the board so the generator refreshes " +
    "<stem>_review/_schematic.png and <stem>_review/_pcb.png, `Read` both " +
    "images, and check: net labels present and legible? decoupling " +
    "capacitors adjacent to their ICs? connectors oriented and placed at " +
    "the board edge? silkscreen legible (no refdes under parts)? mounting " +
    "holes where the enclosure needs them? Fix anything wrong in the TSX " +
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

function spawnClaude(claudePath, args, { workspace, env }) {
  // Test stubs are node scripts; spawn them through the current node binary
  // so they need no chmod/shebang gymnastics on any platform.
  const viaNode = /\.(mjs|cjs|js)$/.test(claudePath);
  const bin = viaNode ? process.execPath : claudePath;
  const argv = viaNode ? [claudePath, ...args] : args;
  return spawn(bin, argv, {
    cwd: workspace,
    env: buildChildEnv(env),
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
  effort = "",
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
  const args = buildCommandArgs({ workspace, phase, sessionId, model, effort, env });
  const resume = args.includes("--resume");
  log(
    `turn ${phase} start session=${shortId(sessionId)} (${resume ? "resume" : "new"})` +
      `${model ? ` model=${model}` : ""}`,
  );

  let child;
  try {
    child = spawnClaude(claudePath, args, { workspace, env });
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
      onEvent,
      signal,
      env,
    });
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
  effort = "",
  prompt,
  onEvent,
  signal,
  env,
}) {
  const pre = snapshotWorkspace(workspace);
  const args = buildCommandArgs({ workspace, phase: PHASE.REVIEW, sessionId, model, effort, env });
  let child;
  try {
    child = spawnClaude(claudePath, args, { workspace, env });
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
      `unresolved (parts: ${parts.length ? parts.join(", ") : "board"}). ` +
      "You may want to inspect those parts._",
  });
}

/**
 * The silent 3-phase post-build review loop (contract §2, donor caps 2/3/2):
 *
 * 1. **Structure** (≤2): while any `severity:"error"` warning remains in the
 *    `*.board.json` sidecars, resume the session with the warning list.
 * 2. **Electrical function** (≤3): while any warning with
 *    `kind ∈ ELECTRICAL_KINDS` remains, fix power budget / orderability /
 *    part drift / netlist parity.
 * 3. **Craft** (≤2, ALWAYS runs once): rebuild, Read `_schematic.png` +
 *    `_pcb.png`, fix what reads wrong; break when a round changes no files.
 *    Non-blocking leftovers seed the prompt as hints.
 *
 * Best-effort throughout — never fails the build turn.
 */
export async function runReviewFixLoop({
  claudePath,
  workspace,
  sessionId,
  turnId,
  model = "",
  onEvent,
  signal,
  env = process.env,
}) {
  let changed = false;
  const aborted = () => Boolean(signal?.aborted);

  // Keep the warning count each round already computes. Without this the loop
  // converges silently and the app can only ever describe the board's current
  // state, never the fact that it started at six blockers and is now at one.
  // `fabReady` stays null: the fab gate has not re-run mid-loop, and writing
  // false here would draw failures that never happened.
  const snapshot = (phase, roundNo, warnings) =>
    recordRevision(workspace, {
      turnId,
      phase,
      round: roundNo,
      counts: countWarnings(warnings, { isBlocking, isElectrical }),
      fabReady: null,
    });

  // The ratchet. The agent eval caught a board that was fab-ready on its first
  // build and was NOT fab-ready five repair rounds later: the loop took a
  // finished board and broke it, chasing cosmetic findings. The router already
  // refuses a retry that is not strictly better (stage 0b); the board-level
  // loop had no such rule, so nothing stopped it walking downhill.
  //
  // A round may leave the board no worse than it found it. The moment one
  // turns orderable into not-orderable, the loop stops — one bad round instead
  // of five — and says so out loud rather than quietly handing back a board
  // that used to be shippable.
  let regressed = false;
  const round = async (prompt) => {
    const readyBefore = workspaceFabReady(workspace);
    const didChange = await runReviewRound({
      claudePath,
      workspace,
      sessionId,
      turnId,
      model,
      prompt,
      onEvent,
      signal,
      env,
    });
    if (readyBefore === true && workspaceFabReady(workspace) === false) {
      regressed = true;
      log("review: a round made an orderable board un-orderable — stopping");
      onEvent?.({
        kind: "assistant_message",
        turnId,
        text:
          "_I stopped the automatic review: the last change made a board that " +
          "could be ordered no longer orderable. The board is back in your " +
          "hands rather than being polished further._",
      });
    }
    return didChange;
  };

  // Phase 1 — structure (blocking = severity "error").
  for (let i = 0; i < MAX_STRUCTURE_ROUNDS; i += 1) {
    if (aborted() || regressed) return changed;
    const all = collectBoardWarnings(workspace);
    const blocking = all.filter(isBlocking);
    const prompt = buildStructurePrompt(blocking);
    if (!prompt) break;
    snapshot("structure", i + 1, all);
    log(`review structure round ${i + 1}: ${blocking.length} blocking warning(s)`);
    changed = (await round(prompt)) || changed;
  }
  if (aborted() || regressed) return changed;
  const afterStructure = collectBoardWarnings(workspace);
  const structureRemaining = afterStructure.filter(isBlocking);
  if (structureRemaining.length) {
    snapshot("structure-unresolved", 0, afterStructure);
    emitUnresolvedNote(turnId, "structure", structureRemaining, onEvent);
    return changed;
  }

  // Phase 2 — electrical function (contract kind set).
  for (let i = 0; i < MAX_ELECTRICAL_ROUNDS; i += 1) {
    if (aborted() || regressed) return changed;
    const all = collectBoardWarnings(workspace);
    const electrical = all.filter(isElectrical);
    const prompt = buildElectricalPrompt(electrical);
    if (!prompt) break;
    snapshot("electrical", i + 1, all);
    log(`review electrical round ${i + 1}: ${electrical.length} electrical warning(s)`);
    changed = (await round(prompt)) || changed;
  }
  if (aborted() || regressed) return changed;
  const afterElectrical = collectBoardWarnings(workspace);
  const electricalRemaining = afterElectrical.filter(isElectrical);
  if (electricalRemaining.length) {
    snapshot("electrical-unresolved", 0, afterElectrical);
    emitUnresolvedNote(turnId, "electrical-function", electricalRemaining, onEvent);
    return changed;
  }

  // Phase 3 — craft. ALWAYS at least one round; break when a round changes
  // nothing. Remaining advisory warnings seed the prompt as hints.
  let hints = collectBoardWarnings(workspace).filter(
    (w) => !isBlocking(w) && !isElectrical(w),
  );
  for (let i = 0; i < MAX_CRAFT_ROUNDS; i += 1) {
    if (aborted() || regressed) return changed;
    snapshot("craft", i + 1, collectBoardWarnings(workspace));
    log(`review craft round ${i + 1}`);
    const roundChanged = await round(buildCraftPrompt(hints));
    changed = roundChanged || changed;
    if (!roundChanged) {
      break;
    }
    if (i + 1 < MAX_CRAFT_ROUNDS) {
      hints = collectBoardWarnings(workspace).filter(
        (w) => !isBlocking(w) && !isElectrical(w),
      );
    }
  }
  snapshot("final", 0, collectBoardWarnings(workspace));
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
    "The plan below is approved. Implement it now: write the board " +
    "source and generate the board and all fab artifacts as described.\n\n" +
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

  function activeEffort() {
    try {
      return settings.read().effort || "";
    } catch {
      return "";
    }
  }

  function runTurn({ projectId, sessionId, message, imagePaths, phase, turnId }) {
    const controller = new AbortController();
    turns.set(turnId, { projectId, sessionId: resolvedSessionId(projectId, sessionId), controller });
    const workspace = projectDir(projectId);
    const activeSessionId = resolvedSessionId(projectId, sessionId);
    const onEvent = (event) => emit(projectId, { ...event, sessionId: activeSessionId });

    const run = spawnTurn({
      workspace,
      sessionId: activeSessionId,
      message,
      imagePaths,
      turnId,
      phase,
      model: activeModel(),
      effort: activeEffort(),
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

  function startTurn({ projectId, sessionId, message, imagePaths = [], phase }) {
    const turnId = crypto.randomUUID();
    const activeSessionId = resolvedSessionId(projectId, sessionId);
    const { run } = runTurn({ projectId, sessionId: activeSessionId, message, imagePaths, phase, turnId });

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
          sessionId: activeSessionId,
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

  function turnInProgress(projectId, sessionId = "") {
    for (const entry of turns.values()) {
      if (entry.projectId === projectId && (!sessionId || entry.sessionId === sessionId)) {
        return true;
      }
    }
    return false;
  }

  function sessionState(projectId, requestedSessionId = "") {
    const sessionId = resolvedSessionId(projectId, requestedSessionId);
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
    return { sessionId, turnInProgress: turnInProgress(projectId, sessionId), history };
  }

  function sessionList(projectId) {
    const workspace = projectDir(projectId);
    const sessionsDir = path.dirname(sessionJsonlPath(workspace, sessionIdForProject(projectId), env));
    let entries = [];
    try {
      entries = fs.readdirSync(sessionsDir, { withFileTypes: true });
    } catch {
      return [];
    }
    const summaries = [];
    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.endsWith(".jsonl")) continue;
      const sessionId = entry.name.slice(0, -".jsonl".length);
      if (!SESSION_ID_RE.test(sessionId)) continue;
      const file = path.join(sessionsDir, entry.name);
      try {
        const contents = fs.readFileSync(file, "utf8");
        const history = parseSessionHistory(contents);
        if (!history.length) continue;
        const firstUser = history.find((item) => item.role === "user");
        const fallback = String(firstUser?.content || "New chat").replace(/\s+/g, " ").trim();
        const title = parseLatestAiTitle(contents) || fallback.slice(0, 54) || "New chat";
        const updatedAt = Math.max(
          fs.statSync(file).mtimeMs,
          ...history.map((item) => Number(item.at) || 0),
        );
        summaries.push({ sessionId, title, updatedAt, messageCount: history.length });
      } catch {
        // A transcript can be mid-write; omit it until the next refresh.
      }
    }
    summaries.sort((a, b) => b.updatedAt - a.updatedAt || a.sessionId.localeCompare(b.sessionId));
    return summaries;
  }

  function createSession(projectId) {
    return {
      sessionId: resolvedSessionId(projectId, crypto.randomUUID()),
      title: "New chat",
      updatedAt: Date.now(),
      messageCount: 0,
    };
  }

  function close() {
    for (const { controller } of turns.values()) {
      controller.abort();
    }
    turns.clear();
  }

  return { startTurn, cancelTurn, turnInProgress, sessionState, sessionList, createSession, close };
}
