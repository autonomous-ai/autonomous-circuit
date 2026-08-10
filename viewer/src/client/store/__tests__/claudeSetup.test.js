// Tests for the Claude Code readiness gate: no inference attempt may reach the
// backend without a working `claude` CLI. v1 is Create-only — there is no
// in-app installer. When the CLI is missing, the gate opens the setup dialog
// with manual instructions and parks the send; Re-check resumes it once the
// CLI appears. Uses fake transports throughout — no network.

import assert from "node:assert/strict";
import test from "node:test";

import {
  ensureClaudeReady,
  dismissClaudeSetup,
  getClaudeSetupState,
  isClaudeMissingError,
  openClaudeSetup,
  recheckClaude,
  resetClaudeSetupStore,
} from "../claudeSetup.js";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// ---------------------------------------------------------------------------
// isClaudeMissingError — detection of the driver's CLAUDE_NOT_INSTALLED errors
// ---------------------------------------------------------------------------

test("isClaudeMissingError matches the chat driver's not-found message", () => {
  assert.equal(
    isClaudeMissingError(
      "`claude` CLI not found. Install Claude Code (https://claude.ai/install).",
    ),
    true,
  );
});

test("isClaudeMissingError matches the DriverError display form", () => {
  assert.equal(isClaudeMissingError("claude CLI not found on PATH"), true);
});

test("isClaudeMissingError ignores unrelated errors", () => {
  assert.equal(isClaudeMissingError("cancelled"), false);
  assert.equal(isClaudeMissingError("A paid subscription is required"), false);
  assert.equal(isClaudeMissingError(""), false);
  assert.equal(isClaudeMissingError(null), false);
});

// ---------------------------------------------------------------------------
// ensureClaudeReady — the pre-inference gate
// ---------------------------------------------------------------------------

test("gate passes when the CLI is detected, and caches the positive result", async (t) => {
  t.after(resetClaudeSetupStore);
  resetClaudeSetupStore();
  let checks = 0;
  const transport = {
    async app_prereq_check() {
      checks += 1;
      return { claudeCli: { found: true, version: "2.1.0" } };
    },
  };
  assert.equal(await ensureClaudeReady(transport), true);
  assert.equal(await ensureClaudeReady(transport), true);
  assert.equal(checks, 1, "positive detection is cached — no re-probe per send");
  assert.equal(getClaudeSetupState().open, false);
});

test("gate fails open when the prereq check is unavailable or throws", async (t) => {
  t.after(resetClaudeSetupStore);
  resetClaudeSetupStore();
  assert.equal(await ensureClaudeReady({}), true, "no prereq method → let the driver report");
  resetClaudeSetupStore();
  const throwing = {
    async app_prereq_check() {
      throw new Error("dev route missing");
    },
  };
  assert.equal(await ensureClaudeReady(throwing), true, "check failure → fail open");
  assert.equal(getClaudeSetupState().open, false);
});

test("missing CLI opens the instructions dialog and parks the send", async (t) => {
  t.after(resetClaudeSetupStore);
  resetClaudeSetupStore();
  const transport = {
    async app_prereq_check() {
      return { claudeCli: { found: false } };
    },
  };

  const gate = ensureClaudeReady(transport);
  await tick();

  const state = getClaudeSetupState();
  assert.equal(state.open, true, "dialog opens");
  assert.equal(state.phase, "instructions", "manual instructions — no installer");
  assert.equal(state.hasPendingSend, true);

  dismissClaudeSetup();
  assert.equal(await gate, false, "dismissing resolves the parked send as blocked");
  assert.equal(getClaudeSetupState().open, false);
});

test("re-check picks up a manual install and resumes the parked send", async (t) => {
  t.after(resetClaudeSetupStore);
  resetClaudeSetupStore();
  let found = false;
  const transport = {
    async app_prereq_check() {
      return { claudeCli: { found } };
    },
  };

  const gate = ensureClaudeReady(transport);
  await tick();
  assert.equal(getClaudeSetupState().open, true);

  // Not installed yet → stays open with guidance.
  assert.equal(await recheckClaude(transport), false);
  let state = getClaudeSetupState();
  assert.equal(state.open, true);
  assert.equal(state.phase, "error");
  assert.match(state.errorMessage, /wasn.t detected/i);

  // The user ran the terminal install themselves → re-check passes.
  found = true;
  assert.equal(await recheckClaude(transport), true);
  assert.equal(await gate, true, "parked send resumes after a passing re-check");
  state = getClaudeSetupState();
  assert.equal(state.open, false);
  assert.equal(state.cliReady, true);

  // Later sends skip straight through without re-checking.
  assert.equal(await ensureClaudeReady({}), true);
});

test("a newer send supersedes an older parked one", async (t) => {
  t.after(resetClaudeSetupStore);
  resetClaudeSetupStore();
  let found = false;
  const transport = {
    async app_prereq_check() {
      return { claudeCli: { found } };
    },
  };

  const first = ensureClaudeReady(transport);
  await tick();
  const second = ensureClaudeReady(transport);
  await tick();

  assert.equal(await first, false, "older send is dropped, not double-fired");
  found = true;
  await recheckClaude(transport);
  assert.equal(await second, true, "only the latest send fires after the re-check");
});

test("openClaudeSetup from a chat error shows instructions without a parked send", async (t) => {
  t.after(resetClaudeSetupStore);
  resetClaudeSetupStore();

  openClaudeSetup();
  const state = getClaudeSetupState();
  assert.equal(state.open, true);
  assert.equal(state.phase, "instructions");
  assert.equal(state.hasPendingSend, false);

  dismissClaudeSetup();
  assert.equal(getClaudeSetupState().open, false);
});

test("openClaudeSetup is a no-op once the CLI is known-ready", async (t) => {
  t.after(resetClaudeSetupStore);
  resetClaudeSetupStore();
  const transport = {
    async app_prereq_check() {
      return { claudeCli: { found: true } };
    },
  };
  assert.equal(await ensureClaudeReady(transport), true);
  openClaudeSetup();
  assert.equal(getClaudeSetupState().open, false, "ready CLI never reopens the dialog");
});
