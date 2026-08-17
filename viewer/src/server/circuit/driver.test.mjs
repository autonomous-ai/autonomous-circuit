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
  ELECTRICAL_KINDS,
  MAX_STRUCTURE_ROUNDS,
  PHASE,
  approvedPlanMessage,
  attachmentNote,
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
  collectBoardWarnings,
  createChatService,
  diffSnapshots,
  isBlocking,
  isElectrical,
  newStreamState,
  parseSessionHistory,
  parseStreamLine,
  persistAttachments,
  planFromFencedBlock,
  questionsFenceFromAskUserQuestion,
  recoverPlanFromTranscript,
  sessionIdForProject,
  spawnTurn,
  summarizeToolResult,
  uuidv5,
  workspaceFabReady,
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
