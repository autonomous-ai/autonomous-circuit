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

test("evaluatePrereqCheck gates on claude + node + toolchain + python", () => {
  const result = evaluatePrereqCheck({
    claudeCli: { found: true, version: "2.1.0" },
    node: { found: true, version: "22.12.0" },
    toolchain: { found: true },
    python: { found: true, version: "3.12.4", healthy: true },
    kicadCli: { found: false },
  });
  assert.equal(result.claude.ok, true);
  assert.equal(result.claude.version, "2.1.0");
  assert.equal(result.node.ok, true);
  assert.equal(result.toolchain.ok, true);
  assert.equal(result.python.ok, true);
  assert.equal(result.kicad.ok, false);
  assert.equal(result.canContinue, true, "kicad-cli is non-blocking");
});

test("evaluatePrereqCheck blocks when a required tool is reported missing", () => {
  const base = {
    claudeCli: { found: true },
    node: { found: true },
    toolchain: { found: true },
    python: { found: true, healthy: true },
  };
  assert.equal(
    evaluatePrereqCheck({ ...base, node: { found: false } }).canContinue,
    false,
  );
  assert.equal(
    evaluatePrereqCheck({ ...base, toolchain: { found: false } }).canContinue,
    false,
  );
  assert.equal(
    evaluatePrereqCheck({ ...base, python: { found: false } }).canContinue,
    false,
  );
});

test("evaluatePrereqCheck blocks a found-but-too-old tool (healthy: false)", () => {
  const result = evaluatePrereqCheck({
    claudeCli: { found: true },
    node: { found: true, version: "18.0.0", healthy: false },
    toolchain: { found: true },
    python: { found: true, healthy: true },
  });
  assert.equal(result.node.ok, false);
  assert.equal(result.canContinue, false);
});

test("evaluatePrereqCheck blocks when the Claude CLI is missing", () => {
  const result = evaluatePrereqCheck({
    claudeCli: { found: false },
    node: { found: true },
    toolchain: { found: true },
    python: { found: true, healthy: true },
  });
  assert.equal(result.claude.ok, false);
  assert.equal(result.canContinue, false);
});

test("evaluatePrereqCheck treats an absent tool field as ok-but-unknown", () => {
  // A server that doesn't report a field can't be gated on it — onboarding
  // must not block forever on a field that will never arrive.
  const result = evaluatePrereqCheck({ claudeCli: { found: true } });
  assert.equal(result.node.ok, true);
  assert.equal(result.node.known, false);
  assert.equal(result.toolchain.ok, true);
  assert.equal(result.python.ok, true);
  assert.equal(result.kicad.known, false);
  assert.equal(result.canContinue, true);
});

test("evaluatePrereqCheck guards against a missing payload", () => {
  const result = evaluatePrereqCheck(undefined);
  assert.equal(result.claude.ok, false);
  assert.equal(result.canContinue, false);
});
