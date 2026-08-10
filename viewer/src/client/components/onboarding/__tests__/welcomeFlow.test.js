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
  // All required tools present → the Start building button unlocks.
  assert.equal(
    evaluatePrereqCheck({
      claudeCli: { found: true, version: "2.1.0" },
      node: { found: true, version: "22.12.0" },
      toolchain: { found: true },
      python: { found: true, version: "3.12.4", healthy: true },
    }).canContinue,
    true,
  );
  // Any required tool missing → blocked, regardless of kicad.
  assert.equal(
    evaluatePrereqCheck({
      claudeCli: { found: false },
      node: { found: true },
      toolchain: { found: true },
      python: { found: true, healthy: true },
      kicadCli: { found: true },
    }).canContinue,
    false,
  );
  assert.equal(
    evaluatePrereqCheck({
      claudeCli: { found: true },
      node: { found: true },
      toolchain: { found: false },
      python: { found: true, healthy: true },
    }).canContinue,
    false,
  );
  // kicad-cli is reported, never required (contract §2).
  assert.equal(
    evaluatePrereqCheck({
      claudeCli: { found: true },
      node: { found: true },
      toolchain: { found: true },
      python: { found: true, healthy: true },
      kicadCli: { found: false },
    }).canContinue,
    true,
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
