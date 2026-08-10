import assert from "node:assert/strict";
import test from "node:test";
import {
  evaluateClaudeCheck,
  evaluatePrereqCheck,
  isOnboardingComplete,
  nextOnboardingStep,
  ONBOARDING_STEPS,
  previousOnboardingStep,
} from "../onboardingHelpers.js";

test("ONBOARDING_STEPS exposes the two-step machine: prereq check, done", () => {
  assert.deepEqual(ONBOARDING_STEPS, [
    "prereq",
    "done",
  ]);
});

test("nextOnboardingStep advances and clamps at done", () => {
  assert.equal(nextOnboardingStep("prereq"), "done");
  assert.equal(nextOnboardingStep("done"), "done");
});

test("nextOnboardingStep maps unknown labels back to the first step", () => {
  assert.equal(nextOnboardingStep("garbage"), "prereq");
});

test("previousOnboardingStep clamps at the first step", () => {
  assert.equal(previousOnboardingStep("done"), "prereq");
  assert.equal(previousOnboardingStep("prereq"), "prereq");
});

test("isOnboardingComplete only reports done", () => {
  assert.equal(isOnboardingComplete("prereq"), false);
  assert.equal(isOnboardingComplete("done"), true);
});

test("evaluateClaudeCheck proceeds when claudeCli.found is true", () => {
  const result = evaluateClaudeCheck({ claudeCli: { found: true, version: "1.2.3" } });
  assert.deepEqual(result, { proceed: true, version: "1.2.3" });
});

test("evaluateClaudeCheck reports missing when found is false", () => {
  const result = evaluateClaudeCheck({ claudeCli: { found: false } });
  assert.deepEqual(result, { proceed: false, reason: "claude_cli_missing" });
});

test("evaluateClaudeCheck guards against missing payloads", () => {
  assert.deepEqual(
    evaluateClaudeCheck(undefined),
    { proceed: false, reason: "claude_cli_missing" },
  );
});

test("evaluatePrereqCheck gates on Claude Code AND ffmpeg", () => {
  const result = evaluatePrereqCheck({
    claudeCli: { found: true, version: "2.1.0" },
    ffmpeg: { found: true, version: "7.1" },
    python: { found: false, healthy: false },
  });
  assert.equal(result.claude.ok, true);
  assert.equal(result.claude.version, "2.1.0");
  assert.equal(result.ffmpeg.ok, true);
  assert.equal(result.ffmpeg.known, true);
  assert.equal(result.canContinue, true, "python is non-blocking");
  assert.equal(result.python.ok, false);
});

test("evaluatePrereqCheck blocks when ffmpeg is reported missing", () => {
  const result = evaluatePrereqCheck({
    claudeCli: { found: true },
    ffmpeg: { found: false },
    python: { found: true, version: "3.12" },
  });
  assert.equal(result.ffmpeg.ok, false);
  assert.equal(result.canContinue, false);
});

test("evaluatePrereqCheck blocks when the Claude CLI is missing", () => {
  const result = evaluatePrereqCheck({
    claudeCli: { found: false },
    ffmpeg: { found: true },
  });
  assert.equal(result.claude.ok, false);
  assert.equal(result.canContinue, false);
});

test("evaluatePrereqCheck treats an absent ffmpeg field as ok-but-unknown", () => {
  // A server that predates the ffmpeg field can't be gated on it — onboarding
  // must not block forever on a field that will never arrive.
  const result = evaluatePrereqCheck({ claudeCli: { found: true } });
  assert.equal(result.ffmpeg.ok, true);
  assert.equal(result.ffmpeg.known, false);
  assert.equal(result.canContinue, true);
});

test("evaluatePrereqCheck guards against a missing payload", () => {
  const result = evaluatePrereqCheck(undefined);
  assert.equal(result.claude.ok, false);
  assert.equal(result.canContinue, false);
});
