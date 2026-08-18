import assert from "node:assert/strict";
import test from "node:test";

import {
  USAGE_KEYS,
  addUsage,
  formatTurnLog,
  newUsage,
  readResultLine,
  weigh,
} from "./usage.mjs";
import { currentUserId } from "./projects.mjs";

/** A `result` line shaped like the real ones, with weather-badge-13's own
 * whole-session counters on it (measured 2026-08-18). */
const RESULT_LINE = {
  type: "result",
  subtype: "success",
  result: "done",
  total_cost_usd: 12.3456,
  duration_ms: 3_360_000,
  is_error: false,
  stop_reason: "end_turn",
  model: "claude-opus-5",
  usage: {
    input_tokens: 420,
    output_tokens: 241_443,
    cache_creation_input_tokens: 2_248_535,
    cache_read_input_tokens: 30_555_177,
    service_tier: "standard",
  },
};

test("it reads the four counters a bill is actually made of", () => {
  const record = readResultLine(RESULT_LINE);
  assert.equal(record.usage.input_tokens, 420);
  assert.equal(record.usage.output_tokens, 241_443);
  assert.equal(record.usage.cache_creation_input_tokens, 2_248_535);
  assert.equal(record.usage.cache_read_input_tokens, 30_555_177);
  assert.equal(record.costUsd, 12.3456);
  assert.equal(record.durationMs, 3_360_000);
  assert.equal(record.model, "claude-opus-5");
});

test("the obvious two counters would have missed 99% of the volume", () => {
  // The argument for keeping four columns instead of a total, in one
  // assertion. On this real board raw input is 420 tokens and cache reads are
  // 30.5M: logging input+output records 0.7% of what moved.
  const u = readResultLine(RESULT_LINE).usage;
  const obvious = u.input_tokens + u.output_tokens;
  const all = USAGE_KEYS.reduce((sum, key) => sum + u[key], 0);
  assert.ok(obvious / all < 0.01, `${obvious} of ${all}`);
});

test("a line that is not a result line is not a bill", () => {
  for (const obj of [null, undefined, 42, "result", {}, { type: "assistant" }]) {
    assert.equal(readResultLine(obj), null);
  }
});

test("a missing cost reads as unknown, never as free", () => {
  // Summing a null as zero is how an underestimate looks like a measurement.
  const record = readResultLine({ type: "result", usage: {} });
  assert.equal(record.costUsd, null);
  for (const key of USAGE_KEYS) {
    assert.equal(record.usage[key], 0);
  }
});

test("garbage counters do not become negative or fractional tokens", () => {
  const record = readResultLine({
    type: "result",
    usage: { input_tokens: -5, output_tokens: 1.7, cache_read_input_tokens: "many" },
  });
  assert.equal(record.usage.input_tokens, 0);
  assert.equal(record.usage.output_tokens, 1);
  assert.equal(record.usage.cache_read_input_tokens, 0);
});

test("an API error is carried, not flattened into a clean turn", () => {
  const record = readResultLine({
    type: "result",
    is_error: true,
    api_error_status: 529,
    stop_reason: "error",
    usage: {},
  });
  assert.equal(record.isError, true);
  assert.equal(record.apiErrorStatus, "529");
  assert.equal(record.stopReason, "error");
});

test("addUsage folds the main turn and every review child into one bill", () => {
  // The measured reason this exists: review rounds are 23-39% of a board's
  // weighted spend, and they used to be drained to /dev/null.
  const acc = newUsage();
  addUsage(acc, readResultLine(RESULT_LINE));
  addUsage(acc, readResultLine(RESULT_LINE));
  assert.equal(acc.output_tokens, 482_886);
  assert.equal(acc.turns, 2);
  assert.ok(Math.abs(acc.costUsd - 24.6912) < 1e-9);
});

test("folding nothing changes nothing", () => {
  const acc = newUsage();
  addUsage(acc, null);
  addUsage(acc, undefined);
  assert.equal(acc.turns, 0);
  assert.equal(acc.costUsd, 0);
});

test("weighting says output dominates volume even when cache reads dwarf it", () => {
  // 241k output at 5x outweighs 30.5M cache reads at 0.1x only if you do the
  // multiply — which is the whole point of not storing a total.
  const u = readResultLine(RESULT_LINE).usage;
  assert.ok(weigh(u) > u.cache_read_input_tokens * 0.1);
  assert.equal(weigh(null), 0);
});

test("the turn line carries the fields pre-deploy asks for, on one line", () => {
  const line = formatTurnLog({
    turnId: "t1",
    projectId: "p1",
    userId: "u1",
    phase: "build",
    model: "claude-opus-5",
    effort: "5x",
    elapsedMs: 3_360_123,
    usage: { ...readResultLine(RESULT_LINE).usage, turns: 8 },
    costUsd: 12.3456,
    exit: "ok",
  });
  assert.doesNotMatch(line, /\n/);
  for (const field of ["turn=t1", "project=p1", "user=u1", "phase=build", "effort=5x", "exit=ok"]) {
    assert.ok(line.includes(field), `${field} missing from: ${line}`);
  }
  assert.ok(line.includes("cost_usd=12.3456"));
  assert.ok(line.includes("claude_turns=8"));
  assert.ok(line.includes("out=241k"), line);
  assert.ok(line.includes("cache_r=30.6M"), line);
});

test("absent fields are omitted rather than printed as null", () => {
  // A short line has to mean "little happened", not "something broke".
  const line = formatTurnLog({ turnId: "t1", exit: "ok" });
  assert.equal(line, "turn=t1 exit=ok");
});

test("an error never breaks the one-line contract", () => {
  const line = formatTurnLog({
    turnId: "t1",
    exit: "error",
    error: "Error: boom\n    at foo (bar.mjs:1:1)\n    at baz",
  });
  assert.doesNotMatch(line, /\n/);
  assert.ok(line.includes("boom"));
});

test("a runaway error message is capped", () => {
  const line = formatTurnLog({ exit: "error", error: "x".repeat(5000) });
  assert.ok(line.length < 400, `line was ${line.length} chars`);
});

// ---------------------------------------------------------------------------
// The user column, before there is a login
// ---------------------------------------------------------------------------

test("there is a user id to attribute a turn to", () => {
  assert.equal(currentUserId({}), "local");
  assert.equal(currentUserId({ CIRCUIT_USER_ID: "  " }), "local");
  assert.equal(currentUserId({ CIRCUIT_USER_ID: "tri" }), "tri");
});

test("the user id is sanitised while it is still a constant", () => {
  // Nothing can set this to anything odd today. The day it becomes
  // request-derived is the day a `..` in it reads someone else's boards, and
  // it costs one line now rather than an incident later.
  assert.equal(currentUserId({ CIRCUIT_USER_ID: "a/b" }), "a-b");
  assert.equal(currentUserId({ CIRCUIT_USER_ID: "tri.luong" }), "tri.luong");
  for (const hostile of ["../../etc", "..", "...", "./.", "/", "..%2f..", "  ../x  "]) {
    const id = currentUserId({ CIRCUIT_USER_ID: hostile });
    assert.doesNotMatch(id, /\.\./, `${hostile} -> ${id}`);
    assert.doesNotMatch(id, /[/\\]/, `${hostile} -> ${id}`);
    assert.ok(id.length > 0, hostile);
  }
});

test("the turn line carries the user it was for", () => {
  const line = formatTurnLog({ turnId: "t1", userId: currentUserId({}), exit: "ok" });
  assert.ok(line.includes("user=local"), line);
});
