// Driver tests — stream-json translation, spawn semantics against the FAKE
// claude stub (fixtures/fake-claude.mjs; the real CLI is never spawned),
// cancel, autopilot chaining, and the 3-phase review loop.

import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  APPROVE_PLAN_PREAMBLE,
  IMPLEMENT_SYSTEM_PROMPT,
  PLAN_SYSTEM_PROMPT,
  REVIEW_SYSTEM_PROMPT,
  REVIEW_SIDECAR_POLL_MS,
  roundMadeItWorse,
  roundCapAfterUndo,
  shouldRestoreBestBuild,
  settledBuildRunId,
  buildIsStale,
  BEST_BUILD_DIR,
  parseCodexRolloutHistory,
  findCodexRolloutPath,
  ELECTRICAL_KINDS,
  MAX_STRUCTURE_ROUNDS,
  PHASE,
  approvedPlanMessage,
  attachmentNote,
  buildCodexCommandArgs,
  buildCommandArgs,
  buildCraftPrompt,
  buildPanelPrompt,
  emitPhaseNote,
  snapshotWorkspace,
  snapshotForUndo,
  restoreFromUndo,
  MAX_PANEL_ROUNDS,
  buildElectricalPrompt,
  buildStructurePrompt,
  codexSandboxForPhase,
  collectBoardWarnings,
  createChatService,
  diffSnapshots,
  isBlocking,
  isElectrical,
  newStreamState,
  parseCodexLine,
  parseSessionHistory,
  parseStreamLine,
  persistAttachments,
  planFromFencedBlock,
  questionsFenceFromAskUserQuestion,
  recoverPlanFromTranscript,
  resolveCodex,
  reviewImagePaths,
  sessionIdForProject,
  spawnTurn,
  summarizeToolResult,
  turnBudgetMs,
  uuidv5,
  workspaceFabReady,
  workspaceHasBoard,
} from "./driver.mjs";
import { encodeCwd, sessionJsonlPath } from "./projects.mjs";

const FIXTURE_DIR = path.dirname(fileURLToPath(import.meta.url));
const FAKE_CLAUDE = path.join(FIXTURE_DIR, "fixtures", "fake-claude.mjs");

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function writeScenario(dir, scenario) {
  const file = path.join(dir, "scenario.json");
  fs.writeFileSync(file, JSON.stringify(scenario));
  return file;
}

function readLog(logPath) {
  try {
    return fs
      .readFileSync(logPath, "utf8")
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

function makeEnv({ scenarioPath, cfgDir, logPath }) {
  return {
    ...process.env,
    CIRCUIT_CLAUDE_BIN: FAKE_CLAUDE,
    CIRCUIT_FAKE_SCENARIO: scenarioPath,
    CLAUDE_CONFIG_DIR: cfgDir,
    ...(logPath ? { CIRCUIT_FAKE_LOG: logPath } : {}),
  };
}

async function waitFor(predicate, { timeoutMs = 8000, stepMs = 20 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (predicate()) {
      return;
    }
    if (Date.now() > deadline) {
      throw new Error("waitFor timed out");
    }
    await new Promise((resolve) => setTimeout(resolve, stepMs));
  }
}

// stream-json line builders
const delta = (text) => ({
  type: "stream_event",
  event: { type: "content_block_delta", delta: { type: "text_delta", text } },
});
const thinking = (text) => ({
  type: "stream_event",
  event: { type: "content_block_delta", delta: { type: "thinking_delta", thinking: text } },
});
const assistant = (content) => ({ type: "assistant", message: { content } });
const toolUse = (id, name, input = {}) => ({ type: "tool_use", id, name, input });
const toolResult = (id, ok = true, content = "ok") => ({
  type: "user",
  message: {
    content: [{ type: "tool_result", tool_use_id: id, is_error: !ok, content }],
  },
});

// ---------------------------------------------------------------------------
// uuidv5 / sessions
// ---------------------------------------------------------------------------

test("uuidv5 matches the CIRCUIT_SESSION_NS reference vectors", () => {
  // python3: uuid.uuid5(uuid.UUID('f466e3eb-799c-4a95-bc9a-72092027e9f7'), 'proj-A') / 'proj-B'
  assert.equal(sessionIdForProject("proj-A"), "ade6fea8-1df3-58fe-ac16-1493d2e7619c");
  assert.equal(sessionIdForProject("proj-B"), "d5b3d481-c0d5-5496-a892-4c35348dfb96");
  assert.equal(sessionIdForProject("proj-A"), sessionIdForProject("proj-A"));
  assert.notEqual(uuidv5("a"), uuidv5("b"));
});

// ---------------------------------------------------------------------------
// buildCommandArgs — the §2 flag set
// ---------------------------------------------------------------------------

test("buildCommandArgs emits the contract flags, plan permission mode, --session-id for a fresh session", () => {
  const cfgDir = tmpdir("circuit-cfg-");
  const workspace = tmpdir("circuit-ws-");
  const sessionId = sessionIdForProject("p1");
  const env = { ...process.env, CLAUDE_CONFIG_DIR: cfgDir };
  const args = buildCommandArgs({ workspace, phase: PHASE.PLAN, sessionId, env });

  assert.deepEqual(args.slice(0, 7), [
    "-p",
    "--output-format",
    "stream-json",
    "--input-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
  ]);
  const mode = args[args.indexOf("--permission-mode") + 1];
  assert.equal(mode, "plan");
  const addDirs = args
    .map((a, i) => (a === "--add-dir" ? args[i + 1] : null))
    .filter(Boolean);
  assert.deepEqual(addDirs, [workspace, path.join(cfgDir, "skills")]);
  assert.ok(args.includes("--strict-mcp-config"));
  assert.equal(args[args.indexOf("--settings") + 1], '{"disableAllHooks":true}');
  assert.equal(args[args.indexOf("--session-id") + 1], sessionId);
  assert.ok(!args.includes("--resume"));
  assert.ok(!args.includes("--model"), "no --model without a configured model");
  const prompt = args[args.indexOf("--append-system-prompt") + 1];
  assert.ok(prompt.includes("PLANNING"), "plan prompt");
  assert.ok(prompt.includes(workspace), "workspace directive names the dir");
});

test("buildCommandArgs resumes an existing session and passes the configured model; build/review run bypassPermissions", () => {
  const cfgDir = tmpdir("circuit-cfg-");
  const workspace = tmpdir("circuit-ws-");
  const sessionId = sessionIdForProject("p2");
  const env = { ...process.env, CLAUDE_CONFIG_DIR: cfgDir };
  // Persisted JSONL at the encoded-cwd path → --resume.
  const jsonl = sessionJsonlPath(workspace, sessionId, env);
  fs.mkdirSync(path.dirname(jsonl), { recursive: true });
  fs.writeFileSync(jsonl, "");

  const args = buildCommandArgs({
    workspace,
    phase: PHASE.IMPLEMENT,
    sessionId,
    model: "opus",
    env,
  });
  assert.equal(args[args.indexOf("--permission-mode") + 1], "bypassPermissions");
  assert.equal(args[args.indexOf("--resume") + 1], sessionId);
  assert.ok(!args.includes("--session-id"));
  assert.equal(args[args.indexOf("--model") + 1], "opus");

  const review = buildCommandArgs({ workspace, phase: PHASE.REVIEW, sessionId, env });
  assert.equal(review[review.indexOf("--permission-mode") + 1], "bypassPermissions");
  assert.ok(
    review[review.indexOf("--append-system-prompt") + 1].includes("post-build"),
    "review prompt carries the post-build marker",
  );
});

// ---------------------------------------------------------------------------
// parseStreamLine — the 9-kind translation
// ---------------------------------------------------------------------------

test("parseStreamLine translates deltas and suppresses duplicated consolidated text", () => {
  const state = newStreamState();
  let events = parseStreamLine(JSON.stringify(delta("Hel")), "t", state);
  assert.deepEqual(events, [{ kind: "text_delta", turnId: "t", text: "Hel" }]);
  events = parseStreamLine(JSON.stringify(thinking("hmm")), "t", state);
  assert.deepEqual(events, [{ kind: "thinking_delta", turnId: "t", text: "hmm" }]);
  // Consolidated assistant text after deltas → suppressed.
  events = parseStreamLine(
    JSON.stringify(assistant([{ type: "text", text: "Hello" }])),
    "t",
    state,
  );
  assert.deepEqual(events, []);
  // Next message with NO deltas → consolidated text is the only copy.
  events = parseStreamLine(
    JSON.stringify(assistant([{ type: "text", text: "Second" }])),
    "t",
    state,
  );
  assert.deepEqual(events, [{ kind: "text_delta", turnId: "t", text: "Second" }]);
});

// A headless `claude -p` subprocess is never handed ExitPlanMode. Observed
// live on 2026-08-10: the model searched for the tool, failed, wrote its plan
// to a file, and ended the turn — leaving the user with nothing to approve and
// the loop dead. The fence is the transport that does not depend on a tool
// existing, so these tests guard the product's spine.
test("planFromFencedBlock extracts a circuit-plan fence and ignores everything else", () => {
  assert.equal(
    planFromFencedBlock("intro\n```circuit-plan\n# Plan\n- do the thing\n```\nouttro"),
    "# Plan\n- do the thing",
  );
  assert.equal(planFromFencedBlock("```circuit-plan\n\n```"), null, "empty fence");
  assert.equal(planFromFencedBlock("no fence at all"), null);
  assert.equal(planFromFencedBlock("```json\n{}\n```"), null, "wrong fence");
  assert.equal(planFromFencedBlock(undefined), null);
});

test("a plan arriving only as a fence still proposes a plan", () => {
  const state = newStreamState();
  const events = parseStreamLine(
    JSON.stringify(
      assistant([
        { type: "text", text: "Here it is.\n```circuit-plan\n# Board plan\nUSB-C in.\n```" },
      ]),
    ),
    "t",
    state,
  );
  const proposed = events.filter((e) => e.kind === "plan_proposed");
  assert.equal(proposed.length, 1, "the fence must produce exactly one plan");
  assert.equal(proposed[0].plan, "# Board plan\nUSB-C in.");
  assert.equal(state.planProposed, true);
});

test("a fence repeated across messages proposes the plan only once", () => {
  const state = newStreamState();
  const line = JSON.stringify(
    assistant([{ type: "text", text: "```circuit-plan\n# P\n```" }]),
  );
  const first = parseStreamLine(line, "t", state);
  const second = parseStreamLine(line, "t", state);
  assert.equal(first.filter((e) => e.kind === "plan_proposed").length, 1);
  assert.equal(
    second.filter((e) => e.kind === "plan_proposed").length,
    0,
    "a second fence must not re-arm the approve button",
  );
});

test("parseStreamLine pairs tool_use/tool_result by stable toolUseId and drops orphans", () => {
  const state = newStreamState();
  let events = parseStreamLine(
    JSON.stringify(assistant([toolUse("tu1", "Bash", { command: "ls" })])),
    "t",
    state,
  );
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    kind: "tool_use_start",
    turnId: "t",
    tool: "Bash",
    toolUseId: "tu1",
    input: { command: "ls" },
  });
  // Orphan result (unknown id) → dropped (intercepted-builtin discipline).
  events = parseStreamLine(JSON.stringify(toolResult("nope")), "t", state);
  assert.deepEqual(events, []);
  // Matching result → paired end with the start's tool name + line count.
  events = parseStreamLine(
    JSON.stringify(toolResult("tu1", true, [{ type: "text", text: "a\nb\nc" }])),
    "t",
    state,
  );
  assert.deepEqual(events, [
    {
      kind: "tool_use_end",
      turnId: "t",
      tool: "Bash",
      toolUseId: "tu1",
      ok: true,
      resultSummary: "3 lines",
    },
  ]);
  // The id is consumed — a duplicate result is dropped.
  events = parseStreamLine(JSON.stringify(toolResult("tu1")), "t", state);
  assert.deepEqual(events, []);
});

test("parseStreamLine intercepts ExitPlanMode as plan_proposed (inline plan or planFilePath)", () => {
  const state = newStreamState();
  const events = parseStreamLine(
    JSON.stringify(assistant([toolUse("tp", "ExitPlanMode", { plan: "# The plan" })])),
    "t",
    state,
  );
  assert.deepEqual(events, [{ kind: "plan_proposed", turnId: "t", plan: "# The plan" }]);
  assert.equal(state.planProposed, true);

  // planFilePath fallback when the inline plan is empty.
  const dir = tmpdir("circuit-plan-");
  const planFile = path.join(dir, "plan.md");
  fs.writeFileSync(planFile, "# From file");
  const state2 = newStreamState();
  const events2 = parseStreamLine(
    JSON.stringify(
      assistant([toolUse("tp2", "ExitPlanMode", { plan: "", planFilePath: planFile })]),
    ),
    "t",
    state2,
  );
  assert.deepEqual(events2, [{ kind: "plan_proposed", turnId: "t", plan: "# From file" }]);
});

test("parseStreamLine converts AskUserQuestion to a circuit-questions fence and flags the turn", () => {
  const state = newStreamState();
  const questions = [
    { question: "Genre?", options: [{ label: "Let Circuit choose" }, { label: "Revenge" }] },
  ];
  const events = parseStreamLine(
    JSON.stringify(assistant([toolUse("tq", "AskUserQuestion", { questions })])),
    "t",
    state,
  );
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "text_delta");
  assert.ok(events[0].text.includes("```circuit-questions"));
  assert.ok(events[0].text.includes(JSON.stringify({ questions })));
  assert.equal(state.questionsAsked, true);
  // Empty questions → no fence, no flag.
  assert.equal(questionsFenceFromAskUserQuestion({ questions: [] }), null);
});

test("parseStreamLine result-line fallback fires only for a text-less turn; garbage skipped", () => {
  const state = newStreamState();
  assert.deepEqual(parseStreamLine("not json", "t", state), []);
  assert.deepEqual(parseStreamLine("", "t", state), []);
  let events = parseStreamLine(JSON.stringify({ type: "result", result: "fallback" }), "t", state);
  assert.deepEqual(events, [{ kind: "text_delta", turnId: "t", text: "fallback" }]);
  // Once text was emitted, the result line is silent.
  events = parseStreamLine(JSON.stringify({ type: "result", result: "again" }), "t", state);
  assert.deepEqual(events, []);
});

// ---------------------------------------------------------------------------
// Snapshot diff
// ---------------------------------------------------------------------------

test("diffSnapshots reports new files and ≥1s forward mtimes only", () => {
  const before = new Map([
    ["boards/main.tsx", 1_000_000],
    ["boards/main_review/_pcb.png", 1_000_000],
    ["boards/main_fab/bom.csv", 1_000_000],
  ]);
  const after = new Map([
    ["boards/main.tsx", 1_000_500], // +0.5s — same whole second bucket → not modified
    ["boards/main_review/_pcb.png", 2_000_000], // +1000s → modified
    ["boards/main_fab/bom.csv", 1_000_000],
    ["boards/main.circuit.json", 5],
  ]);
  const events = diffSnapshots(before, after, "t");
  assert.deepEqual(events, [
    { kind: "artifact_changed", turnId: "t", file: "boards/main.circuit.json", reason: "new" },
    { kind: "artifact_changed", turnId: "t", file: "boards/main_review/_pcb.png", reason: "modified" },
  ]);
});

// ---------------------------------------------------------------------------
// Review-loop plumbing
// ---------------------------------------------------------------------------

test("collectBoardWarnings walks *.board.json recursively, skipping malformed sidecars", () => {
  const dir = tmpdir("circuit-warn-");
  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.writeFileSync(
    path.join(dir, "boards", "main.board.json"),
    JSON.stringify({
      validation: {
        warnings: [
          { part: "board", kind: "dfm_trace_width", detail: "0.1mm < 0.127mm", severity: "info" },
          { part: "U3.pin7", kind: "source_trace_not_connected_error", detail: "floating", severity: "error" },
          { part: "3V3", kind: "power_budget", detail: "rail at 96% of budget", severity: "warning" },
        ],
      },
    }),
  );
  fs.writeFileSync(path.join(dir, "boards", "broken.board.json"), "{nope");
  fs.writeFileSync(path.join(dir, "notes.json"), JSON.stringify({ validation: { warnings: [{ part: "x" }] } }));

  const warnings = collectBoardWarnings(dir);
  assert.equal(warnings.length, 3, "only *.board.json sidecars count");
  const blocking = warnings.filter(isBlocking);
  assert.deepEqual(blocking.map((w) => w.part), ["U3.pin7"]);
  const electrical = warnings.filter(isElectrical);
  assert.deepEqual(electrical.map((w) => w.part), ["3V3"]);
});

test("phase-2 electrical kind set matches the contract; severity is the only blocking gate", () => {
  assert.deepEqual(
    [...ELECTRICAL_KINDS].sort(),
    ["functional", "netlist_mismatch", "part_drift", "part_not_orderable", "power_budget"],
  );
  for (const kind of ELECTRICAL_KINDS) {
    assert.equal(isElectrical({ part: "p", kind, detail: "", severity: "warning" }), true, kind);
  }
  assert.equal(isElectrical({ part: "p", kind: "drc_violation", severity: "warning" }), false);
  assert.equal(isBlocking({ part: "p", kind: "anything_at_all", severity: "error" }), true);
  assert.equal(isBlocking({ part: "p", kind: "functional", severity: "warning" }), false);
});

test("review prompts gate on their warning sets; craft prompt always builds", () => {
  assert.equal(buildStructurePrompt([]), null);
  assert.equal(buildElectricalPrompt([]), null);
  const w = { part: "U3.pin7", kind: "source_trace_not_connected_error", detail: "floating", severity: "error" };
  const structure = buildStructurePrompt([w]);
  assert.ok(structure.includes("- [U3.pin7] source_trace_not_connected_error: floating"));
  const craft = buildCraftPrompt([]);
  assert.ok(craft.includes("_schematic.png"));
  assert.ok(craft.includes("_pcb.png"));
  assert.ok(craft.includes("decoupling"));
  assert.ok(craft.includes("mounting"));
});

test("the panel prompt makes the last stage the driver's job, not a question", () => {
  // 2026-08-14: a turn finished a fab-ready board and ASKED the user whether
  // to run the panel. Two turns earlier, same wording in the skill, it ran it
  // unprompted. The person waiting has no way to know a stage is owed, so the
  // mandatory part moved out of prose and into the loop.
  const panel = buildPanelPrompt();
  assert.ok(panel.includes("design-review"));
  assert.ok(/do not ask/i.test(panel));
  assert.ok(panel.includes("fab-ready"));
});

test("a turn that already ran the panel is told to say so rather than pay twice", () => {
  // A wasted panel is about half an hour, so the round asks before it spends.
  const panel = buildPanelPrompt();
  assert.ok(panel.includes("NO_CHANGES"));
  assert.ok(/ALREADY ran/i.test(panel));
});

test("the panel gets exactly one driver round; its own loop is bounded inside", () => {
  assert.equal(MAX_PANEL_ROUNDS, 1);
});

test("every review phase says what it is doing, because ninety minutes of silence reads as hung", () => {
  // 2026-08-14, from the user after watching a board for 98 minutes: they were
  // sitting there with no idea when it would finish, and asked for it to at
  // least say what it was doing. The loop logged its phases to the server
  // console and told the user nothing.
  const seen = [];
  const onEvent = (e) => seen.push(e);
  emitPhaseNote("t1", onEvent, {
    phase: "the expert panel", round: 1, rounds: 1,
    detail: "seven lenses score the board",
  });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].kind, "text_delta");   // no new ChatEvent kind: §3 is name-coupled
  assert.equal(seen[0].turnId, "t1");
  assert.ok(seen[0].text.includes("the expert panel"));
  assert.ok(seen[0].text.includes("seven lenses"));
  assert.ok(/takes a few minutes/.test(seen[0].text));
});

test("a phase with several rounds shows which round it is on", () => {
  const seen = [];
  emitPhaseNote("t1", (e) => seen.push(e), {
    phase: "craft", round: 2, rounds: 3, detail: "reading the images",
  });
  assert.ok(seen[0].text.includes("craft 2/3"));
});

test("a broken round is undone, not just stopped", () => {
  // Twice this loop broke an orderable board — wb2 tried three changes that
  // each cost fab.ready, wb5 left it at 41 errors mid-panel — and both times
  // only the model's own diligence put it back. The guard stopped the loop and
  // left the damage where it fell.
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "undo-"));
  fs.mkdirSync(path.join(ws, "boards"), { recursive: true });
  fs.mkdirSync(path.join(ws, "blocks", "rp2040-core"), { recursive: true });
  fs.writeFileSync(path.join(ws, "boards", "main.tsx"), "GOOD");
  fs.writeFileSync(path.join(ws, "boards", "main.board.json"), '{"fab":{"ready":true}}');
  fs.writeFileSync(path.join(ws, "blocks", "rp2040-core", "b.tsx"), "GOOD BLOCK");

  const undo = snapshotForUndo(ws);
  assert.ok(undo);

  // the round edits the board, edits a vendored block, and drops litter
  fs.writeFileSync(path.join(ws, "boards", "main.tsx"), "BROKEN");
  fs.writeFileSync(path.join(ws, "blocks", "rp2040-core", "b.tsx"), "BROKEN BLOCK");
  fs.writeFileSync(path.join(ws, "boards", "scratch.tsx"), "LITTER");

  assert.equal(restoreFromUndo(ws, undo), true);
  assert.equal(fs.readFileSync(path.join(ws, "boards", "main.tsx"), "utf8"), "GOOD");
  // blocks/ is restored too: a round may edit a vendored block, and one did
  assert.equal(
    fs.readFileSync(path.join(ws, "blocks", "rp2040-core", "b.tsx"), "utf8"),
    "GOOD BLOCK",
  );
  // and the undo leaves no litter of its own
  assert.equal(fs.existsSync(path.join(ws, "boards", "scratch.tsx")), false);
});

test("the undo copy lives where the artifact watcher cannot see it", () => {
  // Otherwise taking a backup would itself look like the board changed.
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "undo-hidden-"));
  fs.mkdirSync(path.join(ws, "boards"), { recursive: true });
  fs.writeFileSync(path.join(ws, "boards", "main.tsx"), "x");
  const undo = snapshotForUndo(ws);
  assert.ok(undo.includes(".circuit"));
  assert.ok(!snapshotWorkspace(ws).has(path.join(".circuit", "review-undo", "boards", "main.tsx")));
});

test("restoring without a snapshot reports failure instead of pretending", () => {
  assert.equal(restoreFromUndo("/nonexistent", null), false);
});

test("a single-round phase does not print a pointless 1/1", () => {
  const seen = [];
  emitPhaseNote("t1", (e) => seen.push(e), { phase: "the expert panel", round: 1, rounds: 1 });
  assert.ok(!seen[0].text.includes("1/1"));
});

// ---------------------------------------------------------------------------
// Session history rehydration
// ---------------------------------------------------------------------------

test("parseSessionHistory groups an assistant trace with tool timings and drops meta/synthetic lines", () => {
  const jsonl = [
    JSON.stringify({
      type: "user",
      isMeta: true,
      message: { role: "user", content: "<system-reminder>noise</system-reminder>" },
      timestamp: "2026-08-01T05:00:00.000Z",
    }),
    JSON.stringify({
      type: "user",
      message: { role: "user", content: `${approvedPlanMessage("Build the board.")}` },
      timestamp: "2026-08-01T05:00:01.000Z",
    }),
    JSON.stringify({
      type: "user",
      message: { role: "user", content: "design a desk air monitor" },
      timestamp: "2026-08-01T05:00:02.000Z",
    }),
    JSON.stringify({
      type: "assistant",
      message: {
        content: [
          { type: "thinking", thinking: "blocks..." },
          { type: "text", text: "Writing." },
          { type: "tool_use", id: "u1", name: "Read", input: { file_path: "boards/main.tsx" } },
          { type: "tool_use", id: "p1", name: "ExitPlanMode", input: { plan: "x" } },
        ],
      },
      timestamp: "2026-08-01T05:00:03.000Z",
    }),
    JSON.stringify({
      type: "user",
      message: {
        content: [
          { type: "tool_result", tool_use_id: "u1", is_error: false, content: [{ type: "text", text: "a\nb" }] },
        ],
      },
      timestamp: "2026-08-01T05:00:05.000Z",
    }),
    JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "text", text: "Done." }] },
      timestamp: "2026-08-01T05:00:06.000Z",
    }),
  ].join("\n");

  const history = parseSessionHistory(jsonl);
  assert.equal(history.length, 2, `got ${JSON.stringify(history)}`);
  assert.equal(history[0].role, "user");
  assert.equal(history[0].content, "design a desk air monitor");
  assert.ok(history[0].at > 0);

  const asst = history[1];
  assert.equal(asst.role, "assistant");
  assert.equal(asst.content, "Writing.\n\nDone.");
  const kinds = asst.blocks.map((b) => b.kind);
  // ExitPlanMode never becomes a tool block.
  assert.deepEqual(kinds, ["thinking", "text", "tool_use", "text"]);
  const tool = asst.blocks[2];
  assert.equal(tool.tool, "Read");
  assert.equal(tool.status, "ok");
  assert.equal(tool.resultSummary, "2 lines");
  assert.ok(tool.endedAt > tool.at, "tool end resolves from the tool_result timestamp");
});

test("parseSessionHistory strips the attachment note and resolves tool errors", () => {
  const content = `make it darker${attachmentNote(["inputs/a.png"])}`;
  const jsonl = [
    JSON.stringify({
      type: "user",
      message: { role: "user", content },
      timestamp: "2026-08-01T05:00:00.000Z",
    }),
    JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "tool_use", id: "w1", name: "Write", input: {} }] },
      timestamp: "2026-08-01T05:00:01.000Z",
    }),
    JSON.stringify({
      type: "user",
      message: { content: [{ type: "tool_result", tool_use_id: "w1", is_error: true, content: "boom" }] },
      timestamp: "2026-08-01T05:00:02.000Z",
    }),
  ].join("\n");
  const history = parseSessionHistory(jsonl);
  assert.equal(history[0].content, "make it darker");
  assert.equal(history[1].blocks[0].status, "error");
  assert.equal(parseSessionHistory("garbage\n{bad}\n").length, 0);
});

test("chat service lists and reads multiple independent sessions in one project", () => {
  const dir = tmpdir("circuit-many-chats-");
  const workspace = path.join(dir, "workspace");
  const cfg = path.join(dir, "cfg");
  fs.mkdirSync(workspace, { recursive: true });
  const env = { ...process.env, CLAUDE_CONFIG_DIR: cfg };
  const ids = [crypto.randomUUID(), crypto.randomUUID()];
  for (const [index, sessionId] of ids.entries()) {
    const file = sessionJsonlPath(workspace, sessionId, env);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(
      file,
      [
        JSON.stringify({
          type: "user",
          message: { role: "user", content: index ? "Route the power rail" : "Choose the switches" },
          timestamp: `2026-08-0${index + 1}T00:00:00.000Z`,
        }),
        JSON.stringify({ type: "ai-title", aiTitle: index ? "Power routing" : "Switch selection" }),
      ].join("\n"),
    );
    fs.utimesSync(file, new Date(index + 1), new Date(index + 1));
  }
  const chat = createChatService({
    projectDir: () => workspace,
    settings: { read: () => ({ autoBuild: false }) },
    emit: () => {},
    env,
  });
  try {
    const list = chat.sessionList("macropad");
    assert.deepEqual(list.map((item) => item.sessionId), [ids[1], ids[0]]);
    assert.deepEqual(list.map((item) => item.title), ["Power routing", "Switch selection"]);
    assert.equal(chat.sessionState("macropad", ids[0]).history[0].content, "Choose the switches");
    const created = chat.createSession("macropad");
    assert.equal(created.title, "New chat");
    assert.notEqual(created.sessionId, ids[0]);
  } finally {
    chat.close();
  }
});

test("recoverPlanFromTranscript prefers the last substantial text block over thinking", () => {
  const long = (label) => `${label} ${"x".repeat(220)}`;
  const jsonl = [
    JSON.stringify({ type: "assistant", message: { content: [{ type: "thinking", thinking: long("THINK") }] } }),
    JSON.stringify({ type: "assistant", message: { content: [{ type: "text", text: long("PLAN") }] } }),
    JSON.stringify({ type: "assistant", message: { content: [{ type: "text", text: "short ack" }] } }),
  ].join("\n");
  assert.ok(recoverPlanFromTranscript(jsonl).startsWith("PLAN"));
  const thinkingOnly = JSON.stringify({
    type: "assistant",
    message: { content: [{ type: "thinking", thinking: long("THINK") }] },
  });
  assert.ok(recoverPlanFromTranscript(thinkingOnly).startsWith("THINK"));
  assert.equal(recoverPlanFromTranscript(""), "");
});

test("approvedPlanMessage keeps the strippable preamble even for an empty plan", () => {
  assert.ok(approvedPlanMessage("Build it.").startsWith(APPROVE_PLAN_PREAMBLE));
  const empty = approvedPlanMessage("   ");
  assert.ok(empty.startsWith(APPROVE_PLAN_PREAMBLE));
  assert.ok(empty.includes("Implement the plan you just designed"));
  assert.equal(summarizeToolResult(""), undefined);
});

// ---------------------------------------------------------------------------
// persistAttachments
// ---------------------------------------------------------------------------

test("persistAttachments writes uuid-named files under inputs/ and rejects bad input", () => {
  const workspace = tmpdir("circuit-att-");
  const rels = persistAttachments(workspace, [
    { name: "../../evil.png", mediaType: "image/png", dataBase64: Buffer.from("hello").toString("base64") },
  ]);
  assert.equal(rels.length, 1);
  assert.ok(rels[0].startsWith("inputs/"));
  assert.ok(!rels[0].includes("evil"), "user-supplied name never reaches the path");
  assert.ok(fs.existsSync(path.join(workspace, rels[0])));

  assert.throws(
    () => persistAttachments(workspace, [{ mediaType: "text/plain", dataBase64: "aGk=" }]),
    /unsupported image type/,
  );
  const one = { mediaType: "image/png", dataBase64: "aGk=" };
  assert.throws(() => persistAttachments(workspace, Array(7).fill(one)), /too many images/);
});

// ---------------------------------------------------------------------------
// spawnTurn against the fake claude
// ---------------------------------------------------------------------------

test("plan turn: text delta → plan_proposed → turn ends (child killed, later lines dropped)", async () => {
  const dir = tmpdir("circuit-run-");
  const workspace = path.join(dir, "ws");
  const scenarioPath = writeScenario(dir, {
    plan: {
      lines: [
        delta("Speccing the board."),
        assistant([toolUse("tp1", "ExitPlanMode", { plan: "# Plan\nBoard: desk air monitor" })]),
        delta("SHOULD NOT APPEAR"),
      ],
      sleepAfterMs: 3000,
    },
  });
  const events = [];
  const result = await spawnTurn({
    workspace,
    sessionId: sessionIdForProject("p-plan"),
    message: "design a board",
    turnId: "t1",
    phase: PHASE.PLAN,
    onEvent: (e) => events.push(e),
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg") }),
  });

  assert.deepEqual(events[0], { kind: "turn_start", turnId: "t1", phase: "plan" });
  assert.deepEqual(events[1], { kind: "text_delta", turnId: "t1", text: "Speccing the board." });
  assert.equal(events[2].kind, "plan_proposed");
  assert.equal(events[2].plan, "# Plan\nBoard: desk air monitor");
  assert.deepEqual(events.at(-1), { kind: "turn_end", turnId: "t1" });
  assert.ok(!events.some((e) => e.kind === "text_delta" && e.text.includes("SHOULD NOT APPEAR")));
  assert.equal(result.proposedPlan, "# Plan\nBoard: desk air monitor");
  assert.equal(result.cancelled, false);
});

test("plan turn: AskUserQuestion ends the turn with a circuit-questions fence and NO proposed plan", async () => {
  const dir = tmpdir("circuit-run-");
  const workspace = path.join(dir, "ws");
  const questions = [{ question: "Power source?", options: [{ label: "Let Circuit choose" }] }];
  const scenarioPath = writeScenario(dir, {
    plan: {
      lines: [assistant([toolUse("tq1", "AskUserQuestion", { questions })])],
      sleepAfterMs: 3000,
    },
  });
  const events = [];
  const result = await spawnTurn({
    workspace,
    sessionId: sessionIdForProject("p-q"),
    message: "design a board",
    turnId: "t2",
    phase: PHASE.PLAN,
    onEvent: (e) => events.push(e),
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg") }),
  });
  const fence = events.find((e) => e.kind === "text_delta");
  assert.ok(fence.text.includes("```circuit-questions"));
  assert.equal(events.at(-1).kind, "turn_end");
  assert.equal(result.proposedPlan, null, "questions ≠ plan; autopilot must not chain");
});

test("implement turn: tool pairing, incremental artifact_changed, result-line suppressed after text", async () => {
  const dir = tmpdir("circuit-run-");
  const workspace = path.join(dir, "ws");
  const scenarioPath = writeScenario(dir, {
    implement: {
      writeFiles: [{ path: "boards/main.circuit.json", content: "{}" }],
      lines: [
        assistant([{ type: "text", text: "Building." }, toolUse("tb1", "Bash", { command: "circuit" })]),
        toolResult("tb1", true, [{ type: "text", text: "x\ny\nz" }]),
        { type: "result", result: "done" },
      ],
    },
    review: {}, // craft pass runs once, changes nothing, breaks
  });
  const events = [];
  await spawnTurn({
    workspace,
    sessionId: sessionIdForProject("p-impl"),
    message: approvedPlanMessage("plan"),
    turnId: "t3",
    phase: PHASE.IMPLEMENT,
    onEvent: (e) => events.push(e),
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg") }),
  });

  assert.deepEqual(events[0], { kind: "turn_start", turnId: "t3", phase: "implement" });
  const kinds = events.map((e) => e.kind);
  const startIdx = kinds.indexOf("tool_use_start");
  const endIdx = kinds.indexOf("tool_use_end");
  assert.ok(startIdx > 0 && endIdx > startIdx, `pairing order: ${kinds}`);
  assert.equal(events[endIdx].toolUseId, events[startIdx].toolUseId);
  assert.equal(events[endIdx].resultSummary, "3 lines");
  const artifact = events.find((e) => e.kind === "artifact_changed");
  assert.deepEqual(artifact, {
    kind: "artifact_changed",
    turnId: "t3",
    file: "boards/main.circuit.json",
    reason: "new",
  });
  assert.ok(!events.some((e) => e.kind === "text_delta" && e.text === "done"), "result fallback suppressed");
  assert.equal(events.at(-1).kind, "turn_end");
});

test("cancel: kills the child and emits error{cancelled} then turn_end", async () => {
  const dir = tmpdir("circuit-run-");
  const workspace = path.join(dir, "ws");
  const scenarioPath = writeScenario(dir, {
    plan: { lines: [delta("thinking...")], sleepAfterMs: 30000 },
  });
  const controller = new AbortController();
  const events = [];
  const startedAt = Date.now();
  const turn = spawnTurn({
    workspace,
    sessionId: sessionIdForProject("p-cancel"),
    message: "design a board",
    turnId: "t4",
    phase: PHASE.PLAN,
    onEvent: (e) => {
      events.push(e);
      if (e.kind === "text_delta") {
        controller.abort();
      }
    },
    signal: controller.signal,
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg") }),
  });
  const result = await turn;
  assert.equal(result.cancelled, true);
  assert.deepEqual(events.at(-2), { kind: "error", turnId: "t4", message: "cancelled" });
  assert.deepEqual(events.at(-1), { kind: "turn_end", turnId: "t4" });
  assert.ok(Date.now() - startedAt < 10000, "cancel does not wait out the child's sleep");
});

test("silent failure: no stream output surfaces stderr as error BEFORE turn_end", async () => {
  const dir = tmpdir("circuit-run-");
  const workspace = path.join(dir, "ws");
  const scenarioPath = writeScenario(dir, {
    plan: { lines: [], stderr: "Session ID already in use", exitCode: 1 },
  });
  const events = [];
  await spawnTurn({
    workspace,
    sessionId: sessionIdForProject("p-fail"),
    message: "hi",
    turnId: "t5",
    phase: PHASE.PLAN,
    onEvent: (e) => events.push(e),
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg") }),
  });
  const errIdx = events.findIndex((e) => e.kind === "error");
  const endIdx = events.findIndex((e) => e.kind === "turn_end");
  assert.ok(errIdx !== -1 && errIdx < endIdx, "error precedes turn_end");
  assert.ok(events[errIdx].message.includes("claude produced no response"));
  assert.ok(events[errIdx].message.includes("Session ID already in use"));
});

test("missing claude binary → CLAUDE not-found error then turn_end", async () => {
  const dir = tmpdir("circuit-run-");
  const events = [];
  await spawnTurn({
    workspace: path.join(dir, "ws"),
    sessionId: sessionIdForProject("p-missing"),
    message: "hi",
    turnId: "t6",
    phase: PHASE.PLAN,
    onEvent: (e) => events.push(e),
    env: { ...process.env, CIRCUIT_CLAUDE_BIN: path.join(dir, "does-not-exist") },
  });
  assert.equal(events[1].kind, "error");
  assert.ok(events[1].message.includes("`claude` CLI not found"));
  assert.equal(events[2].kind, "turn_end");
});

// ---------------------------------------------------------------------------
// Review loop through a build turn
// ---------------------------------------------------------------------------

const CLEAN_SIDECAR = JSON.stringify({
  generator: "circuitpy",
  entryKind: "board",
  validation: {},
});
const BLOCKED_SIDECAR = JSON.stringify({
  validation: {
    warnings: [
      { part: "U3.pin7", kind: "source_trace_not_connected_error", detail: "floating", severity: "error" },
    ],
  },
});

test("review loop: structure round fixes the blocking warning, then craft always runs once — all silently", async () => {
  const dir = tmpdir("circuit-review-");
  const workspace = path.join(dir, "ws");
  fs.mkdirSync(path.join(workspace, "boards"), { recursive: true });
  fs.writeFileSync(path.join(workspace, "boards", "main.board.json"), BLOCKED_SIDECAR);
  const logPath = path.join(dir, "log.jsonl");
  const scenarioPath = writeScenario(dir, {
    implement: {
      writeFiles: [{ path: "boards/main.circuit.json", content: "{}" }],
      lines: [assistant([{ type: "text", text: "Built." }])],
    },
    review: {
      writeFiles: [{ path: "boards/main.board.json", content: CLEAN_SIDECAR }],
      lines: [delta("review chatter")],
    },
  });
  const events = [];
  await spawnTurn({
    workspace,
    sessionId: sessionIdForProject("p-review"),
    message: approvedPlanMessage("plan"),
    turnId: "t7",
    phase: PHASE.IMPLEMENT,
    onEvent: (e) => events.push(e),
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg"), logPath }),
  });

  const log = readLog(logPath);
  assert.equal(log[0].phase, "implement");
  const reviews = log.filter((entry) => entry.phase === "review");
  assert.ok(reviews.length >= 2, `structure round + craft round(s), got ${reviews.length}`);
  assert.ok(
    reviews[0].stdin.includes("- [U3.pin7] source_trace_not_connected_error: floating"),
    "structure prompt lists the blocking warning",
  );
  assert.ok(reviews[0].stdin.includes("structural check"));
  assert.ok(
    reviews.some((r) => r.stdin.includes("craft verification")),
    "craft pass always runs once after structure clears",
  );
  assert.ok(
    !reviews.some((r) => r.stdin.includes("ELECTRICAL-FUNCTION")),
    "no electrical warnings → no electrical round",
  );
  // Review is silent: its chat never reaches the event stream.
  assert.ok(!events.some((e) => e.kind === "text_delta" && e.text.includes("review chatter")));
  assert.equal(events.at(-1).kind, "turn_end");
});

test("review loop: non-converging structure stops at the 2-round cap with one unresolved note; craft skipped", async () => {
  const dir = tmpdir("circuit-review-");
  const workspace = path.join(dir, "ws");
  fs.mkdirSync(path.join(workspace, "boards"), { recursive: true });
  fs.writeFileSync(path.join(workspace, "boards", "main.board.json"), BLOCKED_SIDECAR);
  const logPath = path.join(dir, "log.jsonl");
  const scenarioPath = writeScenario(dir, {
    implement: {
      writeFiles: [{ path: "boards/main.circuit.json", content: "{}" }],
      lines: [assistant([{ type: "text", text: "Built." }])],
    },
    review: { lines: [] }, // never fixes anything
  });
  const events = [];
  await spawnTurn({
    workspace,
    sessionId: sessionIdForProject("p-review2"),
    message: approvedPlanMessage("plan"),
    turnId: "t8",
    phase: PHASE.IMPLEMENT,
    onEvent: (e) => events.push(e),
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg"), logPath }),
  });

  const reviews = readLog(logPath).filter((entry) => entry.phase === "review");
  assert.equal(reviews.length, MAX_STRUCTURE_ROUNDS, "exactly the structure cap, then bail");
  assert.ok(reviews.every((r) => r.stdin.includes("structural check")));
  const note = events.find((e) => e.kind === "text_delta" && e.text.includes("unresolved"));
  assert.ok(note, "one unresolved-issues note surfaces");
  assert.ok(note.text.includes("structure"));
  assert.ok(note.text.includes("U3.pin7"));
  assert.ok(events.indexOf(note) < events.findIndex((e) => e.kind === "turn_end"));
});

test("electrical phase: contract kinds get the electrical-function prompt after structure is clean", async () => {
  const dir = tmpdir("circuit-review-");
  const workspace = path.join(dir, "ws");
  fs.mkdirSync(path.join(workspace, "boards"), { recursive: true });
  fs.writeFileSync(
    path.join(workspace, "boards", "main.board.json"),
    JSON.stringify({
      validation: {
        warnings: [
          { part: "3V3", kind: "power_budget", detail: "rail at 96% of budget", severity: "warning" },
          { part: "U2", kind: "part_not_orderable", detail: "no LCSC number", severity: "warning" },
        ],
      },
    }),
  );
  const logPath = path.join(dir, "log.jsonl");
  const scenarioPath = writeScenario(dir, {
    implement: {
      writeFiles: [{ path: "boards/main.circuit.json", content: "{}" }],
      lines: [assistant([{ type: "text", text: "Built." }])],
    },
    review: {
      writeFiles: [{ path: "boards/main.board.json", content: CLEAN_SIDECAR }],
    },
  });
  await spawnTurn({
    workspace,
    sessionId: sessionIdForProject("p-review3"),
    message: approvedPlanMessage("plan"),
    turnId: "t9",
    phase: PHASE.IMPLEMENT,
    onEvent: () => {},
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg"), logPath }),
  });
  const reviews = readLog(logPath).filter((entry) => entry.phase === "review");
  assert.ok(reviews[0].stdin.includes("ELECTRICAL-FUNCTION"), "electrical prompt first (no blocking errors)");
  assert.ok(reviews[0].stdin.includes("- [3V3] power_budget: rail at 96% of budget"));
  assert.ok(reviews[0].stdin.includes("- [U2] part_not_orderable: no LCSC number"));
});

// ---------------------------------------------------------------------------
// Chat service — autopilot chaining
// ---------------------------------------------------------------------------

function makeChatHarness({ dir, scenario, autoBuild = true }) {
  const logPath = path.join(dir, "log.jsonl");
  const scenarioPath = writeScenario(dir, scenario);
  const events = [];
  const chat = createChatService({
    projectDir: (id) => path.join(dir, "projects", id),
    settings: { read: () => ({ autoBuild, model: "" }) },
    emit: (projectId, event) => events.push({ projectId, ...event }),
    env: makeEnv({ scenarioPath, cfgDir: path.join(dir, "cfg"), logPath }),
  });
  return { chat, events, logPath };
}

test("autopilot: a proposed plan chains a build turn (plan-present gate, even when plan text is empty)", async () => {
  const dir = tmpdir("circuit-auto-");
  const { chat, events, logPath } = makeChatHarness({
    dir,
    scenario: {
      // ExitPlanMode with NO plan text and no file — plan-present must still build.
      plan: { lines: [assistant([toolUse("tp", "ExitPlanMode", {})])], sleepAfterMs: 2000 },
      implement: {
        writeFiles: [{ path: "boards/main.tsx", content: "<board />" }],
        lines: [assistant([{ type: "text", text: "Built it." }])],
      },
      review: {},
    },
  });
  const turnId = chat.startTurn({ projectId: "proj-1", message: "go", phase: PHASE.PLAN });
  assert.ok(turnId);
  await waitFor(() => events.filter((e) => e.kind === "turn_end").length >= 2, { timeoutMs: 15000 });
  const starts = events.filter((e) => e.kind === "turn_start");
  assert.equal(starts.length, 2);
  assert.equal(starts[0].phase, "plan");
  assert.equal(starts[1].phase, "implement");
  assert.notEqual(starts[0].turnId, starts[1].turnId, "the chained build is its own turn");
  assert.ok(events.every((e) => e.projectId === "proj-1"), "every event is enveloped with projectId");

  const log = readLog(logPath);
  const implement = log.find((entry) => entry.phase === "implement");
  assert.ok(implement.stdin.includes(APPROVE_PLAN_PREAMBLE));
  assert.ok(implement.stdin.includes("Implement the plan you just designed"), "empty plan body fallback");
  chat.close();
});

test("autopilot off (autoBuild=false): the plan turn does NOT chain", async () => {
  const dir = tmpdir("circuit-auto-");
  const { chat, events, logPath } = makeChatHarness({
    dir,
    autoBuild: false,
    scenario: {
      plan: { lines: [assistant([toolUse("tp", "ExitPlanMode", { plan: "P" })])], sleepAfterMs: 2000 },
    },
  });
  chat.startTurn({ projectId: "proj-2", message: "go", phase: PHASE.PLAN });
  await waitFor(() => events.some((e) => e.kind === "turn_end"));
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(events.filter((e) => e.kind === "turn_start").length, 1);
  assert.equal(readLog(logPath).length, 1, "exactly one claude spawn");
  chat.close();
});

test("a questions turn does not chain a build even with autopilot on", async () => {
  const dir = tmpdir("circuit-auto-");
  const { chat, events } = makeChatHarness({
    dir,
    scenario: {
      plan: {
        lines: [
          assistant([
            toolUse("tq", "AskUserQuestion", { questions: [{ question: "Genre?", options: [] }] }),
          ]),
        ],
        sleepAfterMs: 2000,
      },
    },
  });
  chat.startTurn({ projectId: "proj-3", message: "go", phase: PHASE.PLAN });
  await waitFor(() => events.some((e) => e.kind === "turn_end"));
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(events.filter((e) => e.kind === "turn_start").length, 1);
  chat.close();
});

test("cancelTurn aborts an in-flight turn via the registry; unknown ids are a safe no-op", async () => {
  const dir = tmpdir("circuit-auto-");
  const { chat, events } = makeChatHarness({
    dir,
    scenario: { plan: { lines: [delta("working...")], sleepAfterMs: 30000 } },
  });
  assert.equal(chat.cancelTurn("does-not-exist"), false);
  const turnId = chat.startTurn({ projectId: "proj-4", message: "go", phase: PHASE.PLAN });
  await waitFor(() => events.some((e) => e.kind === "text_delta"));
  assert.equal(chat.turnInProgress("proj-4"), true);
  assert.equal(chat.cancelTurn(turnId), true);
  await waitFor(() => events.some((e) => e.kind === "turn_end"));
  const errIdx = events.findIndex((e) => e.kind === "error" && e.message === "cancelled");
  const endIdx = events.findIndex((e) => e.kind === "turn_end");
  assert.ok(errIdx !== -1 && errIdx < endIdx);
  assert.equal(chat.turnInProgress("proj-4"), false);
  chat.close();
});

// A board must be orderable on the first build, so the model and the
// reasoning effort behind it are pinned product decisions — not whatever the
// CLI happens to default to. Before this, the app passed no --model at all.
test("buildCommandArgs passes model and effort through to the CLI", () => {
  const args = buildCommandArgs({
    workspace: "/tmp/ws",
    phase: PHASE.IMPLEMENT,
    sessionId: "11111111-2222-3333-4444-555555555555",
    model: "claude-opus-5",
    effort: "high",
    env: { HOME: "/tmp" },
  });
  assert.equal(args[args.indexOf("--model") + 1], "claude-opus-5");
  assert.equal(args[args.indexOf("--effort") + 1], "high");
});

test("buildCommandArgs omits both flags when unset rather than sending empties", () => {
  const args = buildCommandArgs({
    workspace: "/tmp/ws",
    phase: PHASE.PLAN,
    sessionId: "11111111-2222-3333-4444-555555555555",
    env: { HOME: "/tmp" },
  });
  assert.equal(args.includes("--model"), false);
  assert.equal(args.includes("--effort"), false);
});

// ---------------------------------------------------------------------------
// workspaceFabReady — the ratchet's input
//
// The agent eval caught macropad-6 fab-ready on its first build and NOT
// fab-ready five repair rounds later. The loop had no rule against walking
// downhill, so it did.
// ---------------------------------------------------------------------------

function sidecarDir(boards) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "circuit-fabready-"));
  boards.forEach((fab, i) => {
    const body = fab === undefined ? {} : { fab: { ready: fab } };
    fs.writeFileSync(path.join(dir, `b${i}.board.json`), JSON.stringify(body));
  });
  return dir;
}

test("workspaceFabReady is null when nothing reports a fab verdict", () => {
  // "We do not know" must not collapse into false, or the very first round of
  // every build would look like a regression.
  assert.equal(workspaceFabReady(sidecarDir([])), null);
  assert.equal(workspaceFabReady(sidecarDir([undefined])), null);
});

test("workspaceFabReady is true only when every board is orderable", () => {
  assert.equal(workspaceFabReady(sidecarDir([true])), true);
  assert.equal(workspaceFabReady(sidecarDir([true, true])), true);
  assert.equal(workspaceFabReady(sidecarDir([true, false])), false);
  assert.equal(workspaceFabReady(sidecarDir([false])), false);
});

test("workspaceFabReady ignores a sidecar it cannot parse", () => {
  // A half-written file during a build must not read as "not ready" and trip
  // the ratchet on a healthy board.
  const dir = sidecarDir([true]);
  fs.writeFileSync(path.join(dir, "broken.board.json"), '{"fab": {"rea');
  assert.equal(workspaceFabReady(dir), true);
});

test("workspaceFabReady skips the directories the walker is told to skip", () => {
  const dir = sidecarDir([true]);
  const inputs = path.join(dir, "inputs");
  fs.mkdirSync(inputs);
  fs.writeFileSync(
    path.join(inputs, "old.board.json"),
    JSON.stringify({ fab: { ready: false } }),
  );
  assert.equal(workspaceFabReady(dir), true, "a stale copy must not veto");
});

// --- what counts as a question worth the user's attention ------------------

test("the build prompt defines a blocking ambiguity instead of leaving it open", () => {
  // It said "unless a blocking ambiguity remains" and never said what one was.
  // weather-badge-31, 2026-08-27: the agent finished a board that did not
  // build, then asked whether to revert to the two-sided pour's predecessor —
  // a configuration that had built clean two days earlier, reachable by
  // editing one line. That is not an ambiguity; it is the work.
  const p = IMPLEMENT_SYSTEM_PROMPT;
  assert.match(p, /BLOCKING AMBIGUITY IS A DECISION YOU DO NOT HAVE THE STANDING/);
  assert.match(p, /already on\s*record/i);
  assert.match(p, /try it and measuring|try it/i);
});

test("the build prompt names the things only a person can settle", () => {
  // The owner's rule: what it cannot decide, it asks; what it can, it does.
  // These three are the "cannot" side and must stay named, because a prompt
  // that only says "use judgement" gets read as "ask when unsure".
  const p = IMPLEMENT_SYSTEM_PROMPT;
  assert.match(p, /hardware\s+engineer signing off/i);
  assert.match(p, /spending the user's money/i);
  assert.match(p, /what the product IS/);
});

test("handing back a broken board is named as a cost, not as caution", () => {
  assert.match(
    IMPLEMENT_SYSTEM_PROMPT,
    /is not caution|their attention/i,
    "the prompt has to say why over-asking is expensive, or it reads as safe",
  );
});

// ---------------------------------------------------------------------------
// Codex provider — argv, binary resolution, and the JSONL event vocabulary.
//
// The real `codex` CLI is never spawned here. The flag names and event shapes
// asserted below were read off a live `codex-cli 0.153.4` run on 2026-09-07
// (`codex exec --json --skip-git-repo-check --cd ... --sandbox read-only
// --ephemeral -`), so these tests pin what was actually observed rather than
// what the docs imply.
// ---------------------------------------------------------------------------

test("buildCodexCommandArgs emits the exec flag set and reads the prompt from stdin", () => {
  const workspace = tmpdir("circuit-ws-");
  const args = buildCodexCommandArgs({ workspace, phase: PHASE.IMPLEMENT });

  assert.equal(args[0], "exec");
  assert.ok(args.includes("--json"));
  // Project workspaces under ~/.autonomous-circuit/projects are not git repos;
  // without this flag codex exec refuses to start at all.
  assert.ok(args.includes("--skip-git-repo-check"));
  assert.equal(args[args.indexOf("--cd") + 1], workspace);
  assert.equal(args.at(-1), "-", "prompt arrives on stdin, never as an argv");
  assert.ok(!args.includes("resume"), "a fresh thread does not resume");
});

test("buildCodexCommandArgs keeps the plan turn read-only and runs build and review unsandboxed", () => {
  const workspace = tmpdir("circuit-ws-");
  const sandboxOf = (phase) => {
    const args = buildCodexCommandArgs({ workspace, phase });
    return args[args.indexOf("--sandbox") + 1];
  };
  assert.equal(sandboxOf(PHASE.PLAN), "read-only");
  assert.equal(sandboxOf(PHASE.IMPLEMENT), "danger-full-access");
  assert.equal(sandboxOf(PHASE.REVIEW), "danger-full-access");
  // The helper is the single owner of that decision.
  assert.equal(codexSandboxForPhase(PHASE.PLAN), "read-only");
  assert.equal(codexSandboxForPhase(PHASE.IMPLEMENT), "danger-full-access");
});

test("buildCodexCommandArgs omits --model when unset so the CLI keeps its own configured default", () => {
  const workspace = tmpdir("circuit-ws-");
  assert.ok(!buildCodexCommandArgs({ workspace }).includes("--model"));

  const named = buildCodexCommandArgs({ workspace, model: "gpt-6-astra" });
  assert.equal(named[named.indexOf("--model") + 1], "gpt-6-astra");
  assert.equal(named.at(-1), "-", "--model is spliced in before the stdin marker");
});

test("buildCodexCommandArgs spends the pinned reasoning effort as a config override", () => {
  const workspace = tmpdir("circuit-ws-");
  // Codex has no --effort flag; without this the two providers would run at
  // different reasoning levels and no comparison between them would mean much.
  const configsOf = (opts) => {
    const args = buildCodexCommandArgs({ workspace, ...opts });
    return args.map((a, i) => (a === "-c" ? args[i + 1] : null)).filter(Boolean);
  };
  assert.ok(configsOf({ effort: "high" }).includes("model_reasoning_effort=high"));
  assert.equal(buildCodexCommandArgs({ workspace, effort: "high" }).at(-1), "-");
  // Unset spends nothing — the CLI keeps whatever config.toml says. The other
  // -c overrides (the sandbox's network) are unaffected.
  assert.ok(!configsOf({ effort: "" }).some((c) => c.startsWith("model_reasoning_effort")));
});

test("buildCodexCommandArgs resumes with the resume subcommand's own flag set", () => {
  const workspace = tmpdir("circuit-ws-");
  const args = buildCodexCommandArgs({
    workspace,
    phase: PHASE.IMPLEMENT,
    sessionId: "01a07a02-afe5-73c1-a112-83815bacbb10",
    imagePaths: ["/tmp/a.png", "/tmp/b.png"],
  });

  assert.deepEqual(args.slice(0, 2), ["exec", "resume"]);
  // `codex exec resume` rejects both of these outright — it exited with
  // "unexpected argument '--cd' found" before the model was ever reached.
  assert.ok(!args.includes("--cd"), "resume takes no --cd; cwd comes from the spawn");
  assert.ok(!args.includes("--sandbox"), "resume takes no --sandbox");
  assert.ok(args.includes("--skip-git-repo-check"));
  // The phase's sandbox mode still has to reach codex, via the config key
  // --sandbox is sugar for: a resumed turn must get the same mode a fresh one
  // gets, or the two would run on different footing.
  const configs = args.map((a, i) => (a === "-c" ? args[i + 1] : null)).filter(Boolean);
  assert.ok(configs.includes("sandbox_mode=danger-full-access"), configs.join(","));

  const images = args.map((a, i) => (a === "--image" ? args[i + 1] : null)).filter(Boolean);
  assert.deepEqual(images, ["/tmp/a.png", "/tmp/b.png"]);
  // Usage is `resume [OPTIONS] [SESSION_ID] [PROMPT]`: the id is positional and
  // comes after every flag, immediately before the stdin marker.
  assert.equal(args.at(-2), "01a07a02-afe5-73c1-a112-83815bacbb10");
  assert.equal(args.at(-1), "-");
});

test("a resumed plan turn is still read-only, through the config key instead of the flag", () => {
  const workspace = tmpdir("circuit-ws-");
  const args = buildCodexCommandArgs({ workspace, phase: PHASE.PLAN, sessionId: "sid" });
  const configs = args.map((a, i) => (a === "-c" ? args[i + 1] : null)).filter(Boolean);
  assert.ok(configs.includes("sandbox_mode=read-only"), configs.join(","));
  assert.ok(!args.includes("--sandbox"));
});

test("resolveCodex honours the CIRCUIT_CODEX_BIN stub and refuses one that is not there", () => {
  const dir = tmpdir("circuit-codex-");
  const stub = path.join(dir, "codex");
  fs.writeFileSync(stub, "#!/bin/sh\nexit 0\n");
  assert.equal(resolveCodex({ CIRCUIT_CODEX_BIN: stub }), stub);
  assert.equal(resolveCodex({ CIRCUIT_CODEX_BIN: path.join(dir, "nope") }), null);
});

test("parseCodexLine records the thread id so the next turn resumes the same conversation", () => {
  const state = newStreamState();
  assert.deepEqual(
    parseCodexLine('{"type":"thread.started","thread_id":"t-1"}', "turn-1", state),
    [],
    "a thread banner is bookkeeping, not a user-visible event",
  );
  assert.equal(state.codexSessionId, "t-1");
});

test("parseCodexLine surfaces assistant text and lifts a circuit-plan fence exactly once", () => {
  const state = newStreamState();
  const line = JSON.stringify({
    type: "item.completed",
    item: { id: "item_0", type: "agent_message", text: "Here it is\n```circuit-plan\nBuck + ESP32\n```" },
  });
  const events = parseCodexLine(line, "turn-1", state);
  assert.deepEqual(events.map((e) => e.kind), ["text_delta", "plan_proposed"]);
  assert.equal(events[1].plan, "Buck + ESP32");
  assert.ok(state.anyTextEmitted);
  assert.ok(state.planProposed);

  // A restated plan on a later message does not re-open the approve button.
  const again = parseCodexLine(line, "turn-1", state);
  assert.deepEqual(again.map((e) => e.kind), ["text_delta"]);
});

test("parseCodexLine pairs a shell command's start and end and ignores an unknown end", () => {
  const state = newStreamState();
  const start = parseCodexLine(
    JSON.stringify({
      type: "item.started",
      item: { id: "cmd-1", type: "command_execution", command: "python circuit boards/main.tsx" },
    }),
    "turn-1",
    state,
  );
  assert.equal(start.length, 1);
  assert.equal(start[0].kind, "tool_use_start");
  assert.equal(start[0].tool, "shell");
  assert.equal(start[0].input.command, "python circuit boards/main.tsx");

  const end = parseCodexLine(
    JSON.stringify({
      type: "item.completed",
      item: { id: "cmd-1", type: "command_execution", status: "completed" },
    }),
    "turn-1",
    state,
  );
  assert.deepEqual(
    end.map((e) => [e.kind, e.ok]),
    [["tool_use_end", true]],
  );
  assert.equal(state.pendingTools.size, 0);

  // A completion for a command we never saw start emits nothing.
  assert.deepEqual(
    parseCodexLine(
      JSON.stringify({
        type: "item.completed",
        item: { id: "cmd-ghost", type: "command_execution", status: "failed" },
      }),
      "turn-1",
      state,
    ),
    [],
  );
});

test("parseCodexLine reports a failed command as a non-ok tool end", () => {
  const state = newStreamState();
  parseCodexLine(
    JSON.stringify({ type: "item.started", item: { id: "c", type: "command_execution", command: "false" } }),
    "turn-1",
    state,
  );
  const end = parseCodexLine(
    JSON.stringify({ type: "item.completed", item: { id: "c", type: "command_execution", status: "failed" } }),
    "turn-1",
    state,
  );
  assert.equal(end[0].ok, false);
});

test("parseCodexLine turns a top-level error into a visible error and tolerates junk lines", () => {
  const state = newStreamState();
  const events = parseCodexLine('{"type":"error","message":"model not supported"}', "turn-1", state);
  assert.deepEqual(events, [{ kind: "error", turnId: "turn-1", message: "model not supported" }]);

  assert.deepEqual(parseCodexLine("", "turn-1", state), []);
  assert.deepEqual(parseCodexLine("not json at all", "turn-1", state), []);
  assert.deepEqual(parseCodexLine('{"type":"turn.started"}', "turn-1", state), []);
});

test("build and review run unsandboxed like the Claude arm; the plan turn stays read-only", () => {
  const workspace = tmpdir("circuit-ws-");
  const NET = "sandbox_workspace_write.network_access=true";
  const argsOf = (opts) => buildCodexCommandArgs({ workspace, ...opts });
  const configsOf = (opts) => {
    const args = argsOf(opts);
    return args.map((a, i) => (a === "-c" ? args[i + 1] : null)).filter(Boolean);
  };
  const sandboxOf = (opts) => {
    const args = argsOf(opts);
    const i = args.indexOf("--sandbox");
    return i >= 0 ? args[i + 1] : "";
  };
  // Under workspace-write, kicad-cli aborted at startup on every Codex build
  // (exit -6, measured 2026-09-09 on desk-cube-ship) and the KiCad DRC gate
  // never ran, while the same file passed in 2.3s on the unsandboxed Claude
  // arm. Both arms now run the build on the same footing.
  assert.equal(sandboxOf({ phase: PHASE.IMPLEMENT }), "danger-full-access");
  assert.equal(sandboxOf({ phase: PHASE.REVIEW }), "danger-full-access");
  assert.ok(configsOf({ phase: PHASE.IMPLEMENT, sessionId: "sid" }).includes("sandbox_mode=danger-full-access"), "resume too");
  assert.ok(configsOf({ phase: PHASE.REVIEW, sessionId: "sid" }).includes("sandbox_mode=danger-full-access"), "resumed review too");
  // With no sandbox there is nothing to punch a network hole through; the old
  // workspace-write opt-in must not linger as a stray config key.
  for (const opts of [{ phase: PHASE.IMPLEMENT }, { phase: PHASE.REVIEW }, { phase: PHASE.IMPLEMENT, sessionId: "sid" }]) {
    assert.ok(!configsOf(opts).includes(NET), JSON.stringify(opts));
  }
  // A plan turn proposes a spec and may not write board source; it stays
  // read-only, fresh or resumed, and does not get to reach the network either.
  assert.equal(sandboxOf({ phase: PHASE.PLAN }), "read-only");
  assert.ok(configsOf({ phase: PHASE.PLAN, sessionId: "sid" }).includes("sandbox_mode=read-only"));
  assert.ok(!configsOf({ phase: PHASE.PLAN }).includes(NET));
  assert.ok(!configsOf({ phase: PHASE.PLAN, sessionId: "sid" }).includes(NET));
});

test("workspaceHasBoard is false until a sidecar exists, so a stopped build is not 'reviewed'", () => {
  const workspace = tmpdir("circuit-ws-");
  assert.equal(workspaceHasBoard(workspace), false, "an empty project has no board");

  // A turn that stops before writing board source still touches the workspace —
  // a blocked SOURCE step leaves records behind. That is not a board.
  fs.mkdirSync(path.join(workspace, "sourcing", "sen0097"), { recursive: true });
  fs.writeFileSync(path.join(workspace, "sourcing", "sen0097", "BLOCK.md"), "# pending\n");
  assert.equal(workspaceHasBoard(workspace), false, "sourcing records are not a board");

  fs.mkdirSync(path.join(workspace, "boards"), { recursive: true });
  fs.writeFileSync(path.join(workspace, "boards", "main.tsx"), "export default () => null;\n");
  assert.equal(workspaceHasBoard(workspace), false, "source without a sidecar is not a built board");

  fs.writeFileSync(path.join(workspace, "boards", "main.board.json"), "{}");
  assert.equal(workspaceHasBoard(workspace), true);
});

test("reviewImagePaths finds the renders a craft round has to look at, and nothing else", () => {
  const workspace = tmpdir("circuit-ws-");
  const review = path.join(workspace, "boards", "main_review");
  fs.mkdirSync(review, { recursive: true });
  assert.deepEqual(reviewImagePaths(workspace), [], "no renders yet");

  fs.writeFileSync(path.join(review, "_pcb.png"), "x");
  fs.writeFileSync(path.join(review, "_schematic.png"), "x");
  // The SVGs are the same picture at a size no model needs, and a stray PNG
  // elsewhere in the project is not a board render.
  fs.writeFileSync(path.join(review, "_pcb.svg"), "x");
  fs.mkdirSync(path.join(workspace, "imports"), { recursive: true });
  fs.writeFileSync(path.join(workspace, "imports", "_pcb.png"), "x");

  assert.deepEqual(reviewImagePaths(workspace), [
    path.join(review, "_pcb.png"),
    path.join(review, "_schematic.png"),
  ]);
});

// ---------------------------------------------------------------------------
// A part you cannot get is not a reason to hand back nothing.
//
// desk-cube-55 hit this and shipped: no golden block for the OLED or the
// ambient-light sensor, so both went off-board on labelled I2C pad rows, and
// the board came out 55x55 and fab-ready. A second agent hit the same wall
// three runs running and stopped every time, having read block-source's "a
// block that does not grade ok does not go on a board" as an instruction to
// build nothing. The escape hatch existed only as a precedent — the
// servo-header block — and was never written down.
// ---------------------------------------------------------------------------

test("the build prompt says an unsourceable part goes off-board rather than stopping the board", () => {
  const p = IMPLEMENT_SYSTEM_PROMPT.toLowerCase();
  assert.ok(p.includes("does not stop the board"), "the rule is stated, not implied");
  assert.ok(p.includes("off-board"), "and names where the part goes instead");
  assert.ok(p.includes("pad row"), "on a labelled pad row");
  assert.ok(p.includes("servo-header"), "citing the precedent already in the catalog");
  // The clause it replaced ended "say which field is missing and why it
  // stopped you", which read as permission for stopping to be the outcome.
  assert.ok(!p.includes("why it stopped you"));
});

test("the build prompt overrides an approved plan that told it to stop, and keeps the safety refusal", () => {
  const p = IMPLEMENT_SYSTEM_PROMPT.toLowerCase();
  // A plan whose conclusion is "stop" gets auto-approved under autopilot, so
  // the build turn has to be the thing that refuses to honour it.
  assert.ok(p.includes("that line is wrong"), "an approved plan does not license a stop");
  assert.ok(p.includes("safety refusal"), "stopping still has exactly one legitimate cause");
  for (const kept of ["mains", "unsealed battery", "uncertified radio"]) {
    assert.ok(p.includes(kept), `${kept} is still refused outright`);
  }
});

test("the plan prompt never proposes stopping as the outcome", () => {
  const p = PLAN_SYSTEM_PROMPT.toLowerCase();
  assert.ok(p.includes("never write a plan whose conclusion is that the build"));
  assert.ok(p.includes("the nearest thing we can build is never nothing"));
  assert.ok(p.includes("off-board"));
});

test("every turn carries a wall clock, because a build turn once ran 20 hours", () => {
  const MIN = 60 * 1000;
  // The review loop always had round caps; the turn running it had none, and
  // on 2026-09-08 a build turn rebuilt an unchanged source for 20 hours while
  // its error count climbed from 33 to 76.
  // Generous on purpose: a healthy plan turn was cut at 15 minutes while still
  // working, so these sit where only a stuck turn reaches them.
  assert.equal(turnBudgetMs(PHASE.PLAN, {}), 40 * MIN);
  assert.equal(turnBudgetMs(PHASE.REVIEW, {}), 60 * MIN);
  assert.equal(turnBudgetMs(PHASE.IMPLEMENT, {}), 300 * MIN);
  // Overridable, including 0 to switch it off for a deliberately long session.
  assert.equal(turnBudgetMs(PHASE.IMPLEMENT, { CIRCUIT_TURN_MAX_S: "120" }), 120 * 1000);
  assert.equal(turnBudgetMs(PHASE.IMPLEMENT, { CIRCUIT_TURN_MAX_S: "0" }), 0);
  // Junk falls back to the phase default rather than to no limit at all.
  assert.equal(turnBudgetMs(PHASE.IMPLEMENT, { CIRCUIT_TURN_MAX_S: "soon" }), 300 * MIN);
  assert.equal(turnBudgetMs(PHASE.IMPLEMENT, { CIRCUIT_TURN_MAX_S: "-5" }), 300 * MIN);
});

test("the build prompt tells both providers how long a build actually takes", () => {
  const p = IMPLEMENT_SYSTEM_PROMPT.toLowerCase();
  // The first version of this rule said two to six minutes and forbade
  // backgrounding. Then one 55x55 build spent 2133s in compile and the rule
  // said "20-40 minutes" — and a Claude arm reading that sat in `sleep 590`
  // loops while 25 builds measured on 2026-09-09 took 6 to 9 minutes each.
  // Both numbers were real once; the rule now carries the typical AND the
  // outlier and says to wait for the process, not for a number.
  assert.ok(p.includes("6-10 minutes"), "the measured typical range");
  assert.ok(p.includes("2133 seconds"), "and the measured outlier, not forgotten");
  assert.ok(p.includes("wait"), "wait for the process");
  assert.ok(!p.includes("two to six minutes"));
  assert.ok(!p.includes("do not send it to the background"));
  assert.ok(p.includes("make each round"), "and why it matters: rounds are expensive");
});

test("the build prompt forbids hand copper and router reordering, and keeps asked-for parts on the board", () => {
  const p = IMPLEMENT_SYSTEM_PROMPT.toLowerCase();
  // Desk Cube USB Controller, 2026-09-09: `pcbPath` copper took the board from
  // 9 to 73 findings because the router's obstacle set has no traces in it;
  // `routingPhaseIndex` aborted the router. Both were the model's own idea.
  assert.ok(p.includes("never hand-draw copper"));
  assert.ok(p.includes("pcbpath"));
  assert.ok(p.includes("routingphaseindex"));
  assert.ok(p.includes("not from"), "the mechanism is stated, so the ban reads as a fact");
  // Same board: an 8-pixel ring the user asked for became a header labelled
  // LED8. The other arm soldered the ring. Neither prompt had said which.
  assert.ok(p.includes("stays on the board"));
  assert.ok(p.includes("only when sourcing has failed"));
});

test("the prompts say what the autorouter can actually route", () => {
  const build = IMPLEMENT_SYSTEM_PROMPT.toLowerCase();
  const plan = PLAN_SYSTEM_PROMPT.toLowerCase();
  // A 2-layer board with parts on both sides exhausts the router's iteration
  // budget: 14 missing traces and 14 unconnected pads at both 54x54 and
  // 55x55, while a single-sided board of the same size routed clean. The
  // failure names no cause, so the model cannot deduce it from the verdict.
  assert.ok(build.includes("ran out of iterations"), "the error it will see");
  assert.ok(build.includes("one side"));
  assert.ok(plan.includes("single-sided"), "and the plan turn is where it is decided");
  // Needing both sides is a decision to hand back, and four layers is not the
  // way around it: one run raised `layers` to 4 on its own, hit the exporter's
  // inner-copper bugs, and spent the turn patching the toolchain instead.
  assert.ok(build.includes("say so in plain words and stop"));
  assert.ok(build.includes("layers` to 4"), "the specific move that was taken");
  assert.ok(plan.includes("not yours to choose"));
  // And the toolchain is not the model's to rebuild mid-board.
  assert.ok(build.includes("not a copy of it"));
  assert.ok(build.includes("bun"), "the launcher that hung for 35 minutes");
});

test("a toolchain bug is a report to write, not a patch to apply mid-board", () => {
  const build = IMPLEMENT_SYSTEM_PROMPT.toLowerCase();
  assert.ok(build.includes("write down"), "the reproduction is worth having");
  assert.ok(build.includes("not yours to apply"));
});

test("the build prompt carries the rules that used to live only in CLAUDE.md", () => {
  const p = IMPLEMENT_SYSTEM_PROMPT.toLowerCase();
  // Neither provider ever reads the repo's CLAUDE.md: it sits at the repo
  // root, and a turn's workspace is ~/.autonomous-circuit/projects/<uuid>.
  assert.ok(p.includes("import the domain numbers"));
  // Named to the file, not to a module path: told only "circuitlib.tables",
  // one provider went looking for `*tables*.ts` and found nothing, because the
  // board source is TSX and nothing said the tables are Python.
  assert.ok(p.includes("circuitlib/tables.py"));
  assert.ok(p.includes("no typescript copy"));
  assert.ok(p.includes("do not"), "transcribing is forbidden, not discouraged");
  // Web research is open to both arms, with a bar on what may be claimed.
  assert.ok(p.includes("search the web"));
  assert.ok(p.includes("never state a stock level"));
});

// ---------------------------------------------------------------------------
// 2026-09-09, pomodoro-puck: a review structure round ran 2h06 with no clock,
// went 2→14→3→2→78→6→2→32→3→2 blocking findings fixing one thing per build,
// and nothing put the board back when a build came out worse. Three rules.
// ---------------------------------------------------------------------------

test("the review prompt tells the model to batch its fixes and that a worse round is undone", () => {
  const p = REVIEW_SYSTEM_PROMPT.toLowerCase();
  assert.ok(p.includes("fix everything"), "batching was only in the build prompt before");
  assert.ok(p.includes("rebuild once"), "and says what batching means");
  assert.ok(p.includes("worse than it found it"), "the ratchet is stated to the model, not only enforced");
  // The two detours that cost 40 minutes on the Desk Cube build.
  assert.ok(p.includes("pcbpath"));
  assert.ok(p.includes("routingphaseindex"));
});

test("the plan prompt makes a size change a question, never a plan detail", () => {
  const p = PLAN_SYSTEM_PROMPT.toLowerCase();
  // Autopilot approves a plan without anyone reading it, so a size written
  // into the plan is a size nobody agreed to: 45mm asked, 75mm built.
  assert.ok(p.includes("the board size is the user's decision"));
  assert.ok(p.includes("do not propose a plan at a bigger"));
  assert.ok(p.includes("circuit-questions block instead"));
});

test("roundMadeItWorse is strictly more blocking findings, and never fires on an unreadable board", () => {
  assert.equal(roundMadeItWorse(2, 78), true);
  assert.equal(roundMadeItWorse(2, 2), false, "equal is kept — it may be a trade on the way somewhere");
  assert.equal(roundMadeItWorse(14, 3), false);
  assert.equal(roundMadeItWorse(0, 1), true, "a clean board made unclean is worse");
  assert.equal(roundMadeItWorse(null, 5), false, "no sidecar before: nothing to compare against");
  assert.equal(roundMadeItWorse(5, undefined), false);
  assert.equal(roundMadeItWorse(NaN, 1), false);
});

test("a review round watches the sidecar often enough to show a rebuild, not so often it is noise", () => {
  // A build is 6–9 minutes; the sidecar changes once per build.
  assert.ok(REVIEW_SIDECAR_POLL_MS >= 5_000 && REVIEW_SIDECAR_POLL_MS <= 60_000, String(REVIEW_SIDECAR_POLL_MS));
});

test("parseCodexRolloutHistory returns the user's own words and the assistant's, and nothing of the harness", () => {
  const workspace = "/Users/x/.autonomous-circuit/projects/abc";
  const line = (ts, role, text) =>
    JSON.stringify({ timestamp: ts, type: "response_item", payload: { type: "message", role, content: [{ type: "input_text", text }] } });
  const rollout = [
    JSON.stringify({ timestamp: "2026-09-09T04:39:49.000Z", type: "session_meta", payload: { id: "sid" } }),
    line("2026-09-09T04:39:50.000Z", "user", "<environment_context>\n  <cwd>/x</cwd>\n</environment_context>"),
    line("2026-09-09T04:39:51.000Z", "user", "<recommended_plugins>\n- Airtable\n</recommended_plugins>"),
    line(
      "2026-09-09T04:39:52.000Z",
      "user",
      "You are running inside Autonomous Circuit, the AI PCB studio. Every user\nmessage is a request to design or refine a printed circuit board. You are\nin PLANNING mode...\n\n" +
        "PROJECT WORKSPACE. This project lives in the single absolute directory below. Every file...\n" +
        `${workspace}\n\n` +
        "Tao muốn một cái \"desk cube\" điều khiển bằng USB-C.\n\n- RP2040\n- BME280\n\n" +
        "[Effort: high — think hard before writing the board. Check every block's pin assignment.]",
    ),
    JSON.stringify({ timestamp: "2026-09-09T04:40:00.000Z", type: "response_item", payload: { type: "reasoning", summary: [] } }),
    line("2026-09-09T04:41:00.000Z", "assistant", "Đây là kế hoạch.\n\n```circuit-plan\n# Plan\n```"),
    line(
      "2026-09-09T07:25:56.000Z",
      "user",
      "You are running inside Autonomous Circuit, the AI PCB studio. An automatic\npost-build review of the board you just built is running. Work SILENTLY...\n\n" +
        `PROJECT WORKSPACE...\n${workspace}\n\nStructure and electrical function are clean. Do ONE craft verification pass now.`,
    ),
    line("2026-09-09T07:44:49.000Z", "assistant", "NO_CHANGES"),
    "not json at all",
  ].join("\n");

  const history = parseCodexRolloutHistory(rollout, { workspace });
  assert.deepEqual(
    history.map((h) => [h.role, h.content.split("\n")[0]]),
    [
      ["user", 'Tao muốn một cái "desk cube" điều khiển bằng USB-C.'],
      ["assistant", "Đây là kế hoạch."],
      ["assistant", "NO_CHANGES"],
    ],
  );
  const [first] = history;
  assert.ok(!first.content.includes("You are running inside"), "the phase preamble is not the user's message");
  assert.ok(!first.content.includes("[Effort:"), "the effort suffix is a setting, not something typed");
  assert.ok(first.content.endsWith("- BME280"), first.content);
  assert.equal(first.at, Date.parse("2026-09-09T04:39:52.000Z"));
  assert.deepEqual(history[1].blocks, [{ kind: "text", text: history[1].content }]);
  // The review-round prompt is the silent loop talking to itself, not chat.
  assert.ok(history.every((h) => !h.content.includes("craft verification")));
});

test("findCodexRolloutPath walks CODEX_HOME/sessions newest day first, by session id suffix", () => {
  const home = tmpdir("circuit-codex-home-");
  const env = { CODEX_HOME: home };
  assert.equal(findCodexRolloutPath("01a0-sid", env), "", "nothing there yet");
  assert.equal(findCodexRolloutPath("", env), "", "no id, no lookup");
  const old = path.join(home, "sessions", "2026", "09", "08");
  const today = path.join(home, "sessions", "2026", "09", "09");
  fs.mkdirSync(old, { recursive: true });
  fs.mkdirSync(today, { recursive: true });
  fs.writeFileSync(path.join(old, "rollout-2026-09-08T10-00-00-other-sid.jsonl"), "");
  fs.writeFileSync(path.join(today, "rollout-2026-09-09T11-39-49-01a0-sid.jsonl"), "");
  assert.equal(findCodexRolloutPath("01a0-sid", env), path.join(today, "rollout-2026-09-09T11-39-49-01a0-sid.jsonl"));
  assert.equal(findCodexRolloutPath("other-sid", env), path.join(old, "rollout-2026-09-08T10-00-00-other-sid.jsonl"));
  assert.equal(findCodexRolloutPath("nobody", env), "");
});

// ---------------------------------------------------------------------------
// An undone round gives its phase one spare slot; a stopped implement turn
// hands back its best rebuild (2026-09-10, pomodoro-puck, Astra run #3)
// ---------------------------------------------------------------------------

test("an undone round does not use up a slot — once per phase", () => {
  // Run #3: round 1 chopped at 60 min and undone (5→8), round 2 went 5→3 in
  // 8 minutes, and the 2-round cap ended the run there. The undone round's
  // slot bought nothing.
  assert.equal(roundCapAfterUndo(MAX_STRUCTURE_ROUNDS, 0), MAX_STRUCTURE_ROUNDS);
  assert.equal(roundCapAfterUndo(MAX_STRUCTURE_ROUNDS, 1), MAX_STRUCTURE_ROUNDS + 1);
  // but a phase that keeps making things worse must still end
  assert.equal(roundCapAfterUndo(MAX_STRUCTURE_ROUNDS, 5), MAX_STRUCTURE_ROUNDS + 1);
  assert.equal(roundCapAfterUndo(3, -1), 3);
  assert.equal(roundCapAfterUndo(3, undefined), 3);
});

test("only a STOPPED implement turn hands back its best rebuild", () => {
  // stopped by the clock or the user while worse than the best copy → restore
  assert.equal(shouldRestoreBestBuild({ stoppedEarly: true, bestBlocking: 2, finalBlocking: 8 }), true);
  // stopped, but no worse → keep what is there
  assert.equal(shouldRestoreBestBuild({ stoppedEarly: true, bestBlocking: 2, finalBlocking: 2 }), false);
  assert.equal(shouldRestoreBestBuild({ stoppedEarly: true, bestBlocking: 8, finalBlocking: 2 }), false);
  // ended on its own: may have just implemented "make the board smaller",
  // which reads as a regression to a count and is not one
  assert.equal(shouldRestoreBestBuild({ stoppedEarly: false, bestBlocking: 2, finalBlocking: 8 }), false);
  // an unreadable board is never called worse
  assert.equal(shouldRestoreBestBuild({ stoppedEarly: true, bestBlocking: 2, finalBlocking: null }), false);
  assert.equal(shouldRestoreBestBuild({ stoppedEarly: true, bestBlocking: null, finalBlocking: 8 }), false);
});

test("a build is settled only when build-status.json says done", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "settled-"));
  const statusPath = path.join(ws, ".circuit", "build-status.json");
  fs.mkdirSync(path.dirname(statusPath), { recursive: true });

  assert.equal(settledBuildRunId(ws), null, "no status file → nothing settled");
  fs.writeFileSync(statusPath, JSON.stringify({ state: "running", runId: "r1", stage: "compile" }));
  assert.equal(settledBuildRunId(ws), null, "mid-flight artifacts must not be copied");
  fs.writeFileSync(statusPath, JSON.stringify({ state: "done", runId: "r1" }));
  assert.equal(settledBuildRunId(ws), "r1");
  fs.writeFileSync(statusPath, JSON.stringify({ state: "failed", runId: "r2" }));
  assert.equal(settledBuildRunId(ws), null);
  fs.writeFileSync(statusPath, "{not json");
  assert.equal(settledBuildRunId(ws), null);
});

test("a build is stale once the source moves past the sidecar — outputs never make it stale", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "stale-"));
  fs.mkdirSync(path.join(ws, "boards", "main_fab"), { recursive: true });
  fs.mkdirSync(path.join(ws, "blocks", "rp2040-core"), { recursive: true });
  const t0 = new Date(Date.now() - 60_000);
  const t1 = new Date(Date.now() - 30_000);
  const t2 = new Date();
  const put = (rel, when) => {
    const full = path.join(ws, rel);
    fs.writeFileSync(full, "x");
    fs.utimesSync(full, when, when);
  };

  assert.equal(buildIsStale(ws), true, "no sidecar → nothing to trust");

  put("boards/main.tsx", t0);
  put("blocks/rp2040-core/b.tsx", t0);
  put("product.json", t0);
  put("parts.json", t0);
  put("boards/main.board.json", t1);
  // outputs land AFTER the sidecar by design and are not source
  put("boards/main.circuit.json", t2);
  put("boards/main_fab/bom.csv", t2);
  put("boards/main_fab/enclosure.json", t2);
  assert.equal(buildIsStale(ws), false);

  // the agent starts its next edit: the sidecar no longer describes the source
  put("blocks/rp2040-core/b.tsx", t2);
  assert.equal(buildIsStale(ws), true);
  put("blocks/rp2040-core/b.tsx", t0);
  put("parts.json", t2);
  assert.equal(buildIsStale(ws), true);
});

test("the best-build copy lives beside the review undo copy, and restores like it", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "best-"));
  fs.mkdirSync(path.join(ws, "boards"), { recursive: true });
  fs.writeFileSync(path.join(ws, "boards", "main.tsx"), "BEST");
  fs.writeFileSync(path.join(ws, "boards", "main.board.json"), '{"validation":{"warnings":[]}}');

  const best = snapshotForUndo(ws, BEST_BUILD_DIR);
  assert.ok(best.endsWith(path.join(".circuit", "best-build")));
  assert.notEqual(best, snapshotForUndo(ws), "never the review undo directory — the loop after would clobber it");
  assert.ok(!snapshotWorkspace(ws).has(path.join(".circuit", "best-build", "boards", "main.tsx")),
    "taking the copy must not look like the board changed");

  fs.writeFileSync(path.join(ws, "boards", "main.tsx"), "WRECK");
  assert.equal(restoreFromUndo(ws, best), true);
  assert.equal(fs.readFileSync(path.join(ws, "boards", "main.tsx"), "utf8"), "BEST");
});

test("the build prompt tells the model its best rebuild is what a stopped turn hands back", () => {
  assert.ok(IMPLEMENT_SYSTEM_PROMPT.includes("best\nrebuild so far is kept"));
  assert.ok(IMPLEMENT_SYSTEM_PROMPT.includes("stopped by the clock or\nthe user"));
  // the review prompt has its own ratchet (undo per round); this one is the build turn's
  assert.ok(!REVIEW_SYSTEM_PROMPT.includes("rebuild so far is kept"));
});
