import assert from "node:assert/strict";
import test from "node:test";
import {
  buildOnboardedSettings,
  evaluatePrereqCheck,
  shouldOnboard,
} from "../onboardingHelpers.js";

test("shouldOnboard gates only on hasOnboarded", () => {
  // Fresh / never-onboarded → show the wizard.
  assert.equal(shouldOnboard(null), true);
  assert.equal(shouldOnboard({}), true);
  assert.equal(shouldOnboard({ hasOnboarded: false }), true);
  // Onboarded → into the app.
  assert.equal(shouldOnboard({ hasOnboarded: true }), false);
  assert.equal(
    shouldOnboard({ hasOnboarded: true, claudeOauthToken: "oauth-x" }),
    false,
  );
});

test("welcome Continue gate mirrors evaluatePrereqCheck.canContinue", () => {
  // Both tools present → the Start creating button unlocks.
  assert.equal(
    evaluatePrereqCheck({
      claudeCli: { found: true, version: "2.1.0" },
      ffmpeg: { found: true },
    }).canContinue,
    true,
  );
  // Either required tool missing → blocked, regardless of python.
  assert.equal(
    evaluatePrereqCheck({
      claudeCli: { found: false },
      ffmpeg: { found: true },
      python: { found: true, healthy: true },
    }).canContinue,
    false,
  );
  assert.equal(
    evaluatePrereqCheck({
      claudeCli: { found: true },
      ffmpeg: { found: false },
    }).canContinue,
    false,
  );
});

test("buildOnboardedSettings forces hasOnboarded and preserves existing settings", () => {
  const next = buildOnboardedSettings({
    defaultFilament: "PETG",
    slicerBinaryPath: "/orca",
    autoUpdate: true,
  });
  assert.equal(next.hasOnboarded, true);
  // Preserves the rest of the existing settings.
  assert.equal(next.defaultFilament, "PETG");
  assert.equal(next.slicerBinaryPath, "/orca");
  assert.equal(next.autoUpdate, true);
});

test("buildOnboardedSettings preserves the local Claude OAuth token", () => {
  const next = buildOnboardedSettings({ claudeOauthToken: "oauth-abc" });
  assert.equal(next.hasOnboarded, true);
  assert.equal(next.claudeOauthToken, "oauth-abc");
});

test("buildOnboardedSettings defaults a fresh profile", () => {
  const next = buildOnboardedSettings(null);
  assert.equal(next.hasOnboarded, true);
  assert.equal(next.defaultFilament, "PLA");
  assert.equal(next.slicerBinaryPath, "");
  assert.equal(next.autoUpdate, false);
});
