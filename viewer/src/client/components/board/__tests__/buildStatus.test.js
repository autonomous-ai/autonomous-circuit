import assert from "node:assert/strict";
import test from "node:test";
import {
  BUILD_STAGES,
  ROUTER_RETRY_STAGE,
  blockingFromDetail,
  buildHistoryLine,
  buildProgress,
  buildStageChecklist,
  buildStatusLine,
  formatElapsed,
  isRunning,
  isTerminal,
  stageLabelFor,
  normalizeEpochMs,
} from "../buildStatus.js";

test("the stage list is our design prelude plus the pipeline's seven, in order", () => {
  assert.deepEqual(
    BUILD_STAGES.map((stage) => stage.key),
    ["design", "compile", "scan", "checks", "substrate", "dfm", "export", "render"],
  );
  assert.equal(BUILD_STAGES[0].prelude, true);
  for (const stage of BUILD_STAGES) {
    assert.ok(stage.plain.length > 10, `${stage.key} needs a plain-language line`);
  }
});

test("buildStageChecklist marks done / active / pending from the stage key", () => {
  const list = buildStageChecklist({ state: "running", stage: "substrate", stageIndex: 4, stageCount: 7 });
  assert.deepEqual(
    list.map((stage) => stage.state),
    ["done", "done", "done", "done", "active", "pending", "pending", "pending"],
  );
});

test("an unknown stage still advances the list using stageIndex", () => {
  const list = buildStageChecklist({ state: "running", stage: "something_new", stageIndex: 3, stageCount: 8 });
  assert.equal(list[3].state, "active");
  assert.equal(list[2].state, "done");
});

test("a finished build shows every stage done", () => {
  const list = buildStageChecklist({ state: "done", stage: "render", stageIndex: 7, stageCount: 7 });
  assert.ok(list.every((stage) => stage.state === "done"));
});

test("before the pipeline reports anything, the design prelude is the live one", () => {
  // This is the case that matters: for the first minute the model is writing
  // the board and no build-status record exists at all. An all-grey list here
  // reads as a hang.
  const list = buildStageChecklist(null);
  assert.equal(list[0].state, "active");
  assert.ok(list.slice(1).every((stage) => stage.state === "pending"));
});

test("progress, elapsed and state helpers behave", () => {
  assert.equal(buildProgress({ state: "running", stageIndex: 3, stageCount: 7 }).toFixed(2), "0.43");
  assert.equal(buildProgress({ state: "done" }), 1);
  assert.equal(buildProgress(null), 0);
  assert.equal(formatElapsed(48), "48s");
  assert.equal(formatElapsed(185), "3m 05s");
  assert.equal(isRunning({ state: "running" }), true);
  assert.equal(isTerminal({ state: "stale" }), true);
  assert.equal(buildStatusLine({ state: "failed", detail: "boom" }).text, "Build failed");
});

// --- the router retry -------------------------------------------------------
//
// Stage 0b re-runs the compile at 5x effort and can take twenty minutes. It
// reports itself only by replaying `compile`, so without a row of its own the
// checklist walks backwards and then sits still — the "reads as hung" failure
// the checklist exists to prevent.

test("a router retry gets its own row instead of rewinding the checklist", () => {
  const plain = buildStageChecklist({ state: "running", stage: "dfm", stageIndex: 4, stageCount: 7 });
  const retry = buildStageChecklist({
    state: "running",
    stage: "compile",
    stageIndex: 0,
    stageCount: 7,
    routerRetry: true,
  });
  assert.equal(retry.length, plain.length + 1, "the retry adds a row rather than replacing one");
  const active = retry.find((stage) => stage.state === "active");
  assert.equal(active.key, ROUTER_RETRY_STAGE.key);
  // Everything before it stays done — the earlier stages really did finish.
  assert.ok(retry.slice(0, 3).every((stage) => stage.state === "done"));
});

test("the retry row keeps advancing once the pipeline moves past it", () => {
  const list = buildStageChecklist({
    state: "running",
    stage: "dfm",
    stageIndex: 4,
    stageCount: 7,
    routerRetry: true,
  });
  const active = list.find((stage) => stage.state === "active");
  assert.equal(active.key, "dfm");
  assert.equal(list.find((stage) => stage.key === ROUTER_RETRY_STAGE.key).state, "done");
});

test("the status line names the retry, because a silent twenty minutes is a bug", () => {
  const line = buildStatusLine({
    state: "running",
    stage: "compile",
    stageLabel: "Compiling the board",
    stageIndex: 0,
    stageCount: 7,
    routerRetry: true,
  });
  assert.equal(line.text, ROUTER_RETRY_STAGE.label);
  assert.match(line.detail, /5×/);
});

// --- build history ----------------------------------------------------------

const revision = (blocking, extra = {}) => ({
  at: "2026-08-11T00:00:00Z",
  turnId: "t1",
  phase: "structure",
  round: 1,
  counts: { total: blocking + 3, blocking, electrical: 0, other: 3 },
  fabReady: null,
  ...extra,
});

test("one recorded round is not a trend, and is not written up as one", () => {
  assert.equal(buildHistoryLine(null), null);
  assert.equal(buildHistoryLine({ revisions: [], trend: null }), null);
  assert.equal(buildHistoryLine({ revisions: [revision(4)], trend: null }), null);
});

test("clean on the first build reads as the win it is", () => {
  const line = buildHistoryLine({
    revisions: [revision(0, { fabReady: true, counts: { total: 0, blocking: 0, electrical: 0, other: 0 } })],
    trend: null,
  });
  assert.equal(line.tone, "first-try");
  assert.match(line.text, /Clean on the first build/);
});

test("a mid-loop round that never re-ran the fab gate is not read as ready", () => {
  // fabReady: null is "we did not check", and collapsing it to a claim either
  // way would invent history.
  const line = buildHistoryLine({
    revisions: [revision(0, { fabReady: null, counts: { total: 0, blocking: 0, electrical: 0, other: 0 } })],
    trend: null,
  });
  assert.equal(line, null);
});

test("converging is stated with both numbers", () => {
  const line = buildHistoryLine({
    revisions: [revision(6), revision(3), revision(1)],
    trend: { builds: 3, from: 6, to: 1, fixed: 5, worse: false, fabReady: false },
  });
  assert.equal(line.tone, "better");
  assert.match(line.text, /6 findings stopped the order 3 builds ago; 1 still does/);
});

test("getting worse is said out loud", () => {
  const line = buildHistoryLine({
    revisions: [revision(2), revision(5)],
    trend: { builds: 2, from: 2, to: 5, fixed: 0, worse: true, fabReady: false },
  });
  assert.equal(line.tone, "worse");
  assert.match(line.text, /made it worse/);
});

test("no movement is reported as no movement", () => {
  const line = buildHistoryLine({
    revisions: [revision(2), revision(2)],
    trend: { builds: 2, from: 2, to: 2, fixed: 0, worse: false, fabReady: false },
  });
  assert.equal(line.tone, "flat");
  assert.match(line.text, /unchanged across 2 builds/);
});

// --- quiet vs dead ----------------------------------------------------------
//
// The server calls a record stale after two minutes without a stage
// transition, and the pipeline writes one only between stages. Watching a real
// build, an ordinary RP2040 compile crossed that line and the app announced
// "Build stopped responding" over a build that was working perfectly. The 5x
// router retry runs fifteen minutes in one stage, so this is not an edge case.

const STALE = {
  state: "stale",
  stage: "compile",
  stageLabel: "Compiling the board",
  stageIndex: 1,
  stageCount: 7,
  updatedAt: 1_700_000_000, // epoch SECONDS, the way the pipeline writes it
};

test("a stale record with a turn still running is quiet, not dead", () => {
  const line = buildStatusLine(STALE, { now: 1_700_000_300_000, turnActive: true });
  assert.equal(line.tone, "quiet");
  assert.equal(line.text, "Compiling the board");
  assert.match(line.detail, /quiet for 5m/);
});

test("a stale record with no turn running still means what it says", () => {
  const line = buildStatusLine(STALE, { now: 1_700_000_300_000, turnActive: false });
  assert.equal(line.tone, "stale");
  assert.match(line.text, /stopped responding/);
});

test("epoch seconds and epoch milliseconds both normalise", () => {
  assert.equal(normalizeEpochMs(1_700_000_000), 1_700_000_000_000);
  assert.equal(normalizeEpochMs(1_700_000_000_000), 1_700_000_000_000);
  assert.equal(normalizeEpochMs(0), 0);
  assert.equal(normalizeEpochMs("nope"), 0);
});

test("a quiet stage that is the router retry says which slow thing it is", () => {
  const line = buildStatusLine(
    { ...STALE, routerRetry: true },
    { now: 1_700_000_300_000, turnActive: true },
  );
  assert.equal(line.tone, "quiet");
  assert.equal(line.text, ROUTER_RETRY_STAGE.label);
  assert.match(line.detail, /5× router effort · quiet for 5m/);
});

// Watched on a real run: the tree read a green `Built  9m 16s · 20 blocking`
// while the pane beside it said the board could not be ordered. Green plus a
// count nobody can read is worse than either alone.
test("a build that finished carrying blocking findings does not read as success", () => {
  const line = buildStatusLine({
    state: "done",
    elapsedS: 556,
    detail: "20 blocking",
    updatedAt: Date.now() / 1000,
  });
  assert.equal(line.tone, "done-blocked");
  assert.equal(line.text, "Built, not orderable");
  assert.match(line.detail, /20 to fix/);
  assert.doesNotMatch(line.detail, /blocking/);
});

test("a clean build stays green and drops the pipeline's own zero", () => {
  const line = buildStatusLine({
    state: "done",
    elapsedS: 92,
    detail: "0 blocking",
    updatedAt: Date.now() / 1000,
  });
  assert.equal(line.tone, "done");
  assert.equal(line.text, "Built");
  assert.equal(line.detail, "1m 32s");
});

test("an unrecognised detail is passed through rather than dropped", () => {
  const line = buildStatusLine({
    state: "done",
    elapsedS: 10,
    detail: "router retry kept",
    updatedAt: Date.now() / 1000,
  });
  assert.equal(line.tone, "done");
  assert.match(line.detail, /router retry kept/);
});

test("blockingFromDetail reads the pipeline's string and nothing else", () => {
  assert.equal(blockingFromDetail("20 blocking"), 20);
  assert.equal(blockingFromDetail("0 blocking"), 0);
  assert.equal(blockingFromDetail("blocking"), null);
  assert.equal(blockingFromDetail(""), null);
  assert.equal(blockingFromDetail(undefined), null);
});

// One stage, one name. The tree line used the pipeline's `stageLabel` while
// the checklist beside it used ours, so the same step could be called two
// different things on one screen.
test("the tree line and the checklist call a stage the same thing", () => {
  const status = { state: "running", stage: "export", stageLabel: "Writing the fab packet", stageIndex: 6, stageCount: 7 };
  assert.equal(stageLabelFor(status), "Writing the files for the factory");
  assert.equal(buildStatusLine(status).text, "Writing the files for the factory");
  const checklist = buildStageChecklist(status);
  assert.equal(checklist.find((s) => s.key === "export").label, "Writing the files for the factory");
});

test("an unknown stage keeps whatever the pipeline called it", () => {
  assert.equal(stageLabelFor({ stage: "new-thing", stageLabel: "Doing a new thing" }), "Doing a new thing");
  assert.equal(stageLabelFor({ stage: "new-thing" }), "new-thing");
  assert.equal(stageLabelFor(null), "");
});

test("no build stage label needs a vocabulary the reader does not have", () => {
  const jargon = /gerber|netlist|fab packet|DRC|ERC|BOM|CPL|refdes/i;
  for (const stage of BUILD_STAGES) {
    assert.doesNotMatch(stage.label, jargon, stage.key);
    assert.doesNotMatch(stage.plain, jargon, `${stage.key} (plain)`);
  }
});
