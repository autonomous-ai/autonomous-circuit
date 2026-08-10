// Pure helpers for the onboarding wizard. Kept JSX/dep-free so Node's
// --test runner can exercise the state machine and re-poll logic.
//
// v1 onboarding is a prereq check (contract §2: claude on PATH · node ≥22.12 ·
// toolchain installed · python ≥3.10 · kicad-cli reported-but-not-required)
// followed by done. There is no in-app installer, no guided sign-in, and no
// account — missing tools get friendly manual instructions and the screen
// re-polls until they appear.

export const ONBOARDING_STEPS = Object.freeze([
  "prereq",
  "done",
]);

const STEP_ORDER = ONBOARDING_STEPS;

export function nextOnboardingStep(current) {
  const index = STEP_ORDER.indexOf(current);
  if (index < 0) {
    return STEP_ORDER[0];
  }
  return STEP_ORDER[Math.min(index + 1, STEP_ORDER.length - 1)];
}

export function previousOnboardingStep(current) {
  const index = STEP_ORDER.indexOf(current);
  if (index <= 0) {
    return STEP_ORDER[0];
  }
  return STEP_ORDER[index - 1];
}

export function isOnboardingComplete(step) {
  return step === "done";
}

/**
 * Compute the next-step decision after a prereq check. Returns
 * `{ proceed: true }` once the Claude CLI is found; otherwise reports back
 * the missing reason so the UI can render the install instructions.
 */
export function evaluateClaudeCheck(check) {
  const found = Boolean(check?.claudeCli?.found);
  if (found) {
    return { proceed: true, version: String(check?.claudeCli?.version || "") };
  }
  return { proceed: false, reason: "claude_cli_missing" };
}

/**
 * Row model for one reported tool. A server that doesn't report the field
 * can't be gated on it, so an absent object reads as ok-but-unknown rather
 * than blocking onboarding forever (the donor's ffmpeg posture, kept for
 * every non-claude tool). `healthy: false` (version too old) blocks even
 * when the binary is found.
 */
function toolRow(reported) {
  const known = reported != null;
  return {
    ok: known ? Boolean(reported.found) && reported.healthy !== false : true,
    known,
    version: known ? String(reported.version || "") : "",
  };
}

/**
 * Collapse a full `app_prereq_check` result into the welcome screen's row
 * model. Gating (contract §2): Claude Code, Node ≥22.12, the pinned toolchain
 * (`toolchain/node_modules` present), and Python ≥3.10 must all pass;
 * kicad-cli is reported but never blocks (fab packets need it; everything
 * else works without it).
 */
export function evaluatePrereqCheck(check) {
  const claude = {
    ok: Boolean(check?.claudeCli?.found),
    version: String(check?.claudeCli?.version || ""),
  };
  const node = toolRow(check?.node);
  const toolchain = toolRow(check?.toolchain);
  const python = toolRow(check?.python);
  const kicad = toolRow(check?.kicadCli);
  return {
    claude,
    node,
    toolchain,
    python,
    kicad,
    canContinue: claude.ok && node.ok && toolchain.ok && python.ok,
  };
}

/**
 * Drive the Claude-check polling loop. Returns the timer id so callers can
 * cancel. Pulled out so it can be unit-tested with a fake timer.
 */
export function schedulePoll(callback, intervalMs, scheduler = setTimeout) {
  return scheduler(callback, intervalMs);
}

export const CLAUDE_INSTALL_URL = "https://claude.ai/install";

/** Manual installs (macOS; the dev platform for v1). */
export const NODE_INSTALL_COMMAND = "brew install node";
export const PYTHON_INSTALL_COMMAND = "brew install python@3.12";
export const KICAD_INSTALL_COMMAND = "brew install --cask kicad";
/** The pinned toolchain installs from the repo, not brew. */
export const TOOLCHAIN_INSTALL_COMMAND = "scripts/setup-toolchain.sh";

/** How the user signs Claude Code in — there is no in-app login flow in v1. */
export const CLAUDE_LOGIN_HINT =
  "Sign in: run `claude` once in a terminal and follow the prompt.";

export const CLAUDE_CHECK_POLL_INTERVAL_MS = 5000;

/**
 * Drive the Claude-check loop without React: caller passes a transport-like
 * `runCheck` and an `onAdvance` callback. Returns a `{ recheck, cancel }`
 * pair the test can drive. The loop polls at `intervalMs` until either
 * cancel() is called or `runCheck` resolves with `proceed: true`.
 */
export function buildClaudeCheckLoop({
  runCheck,
  onAdvance,
  intervalMs = CLAUDE_CHECK_POLL_INTERVAL_MS,
  scheduler = setTimeout,
  clear = clearTimeout,
}) {
  let cancelled = false;
  let timer = null;
  let advanced = false;
  let pollCount = 0;

  async function tick() {
    if (cancelled) return;
    pollCount += 1;
    const check = await runCheck();
    const result = evaluateClaudeCheck(check);
    if (cancelled) return;
    if (result.proceed && !advanced) {
      advanced = true;
      onAdvance?.();
      return;
    }
    if (!advanced && !cancelled) {
      timer = scheduler(tick, intervalMs);
    }
  }

  const recheck = () => {
    if (timer) {
      clear(timer);
      timer = null;
    }
    void tick();
  };

  const cancel = () => {
    cancelled = true;
    if (timer) {
      clear(timer);
      timer = null;
    }
  };

  // Kick off the initial probe.
  void tick();

  return {
    recheck,
    cancel,
    get pollCount() {
      return pollCount;
    },
  };
}

// ---------------------------------------------------------------------------
// Welcome screen (single-screen onboarding) helpers
// ---------------------------------------------------------------------------

/**
 * App-entry gate: does this profile still need the Welcome wizard? Gates solely
 * on `hasOnboarded`. "The prereqs actually pass" is enforced inside the wizard
 * (the Continue button), not here — an onboarded machine is left alone.
 */
export function shouldOnboard(settings) {
  return !Boolean(settings?.hasOnboarded);
}

/**
 * Build the settings object that completes onboarding, preserving the rest of
 * `existing` (re-read just before writing so we never clobber anything another
 * screen persisted) and forcing `hasOnboarded: true`.
 */
export function buildOnboardedSettings(existing) {
  const base = existing || {};
  return {
    defaultFilament: base.defaultFilament ?? "PLA",
    slicerBinaryPath: base.slicerBinaryPath ?? "",
    claudeOauthToken: base.claudeOauthToken,
    hasOnboarded: true,
    autoUpdate: base.autoUpdate ?? false,
  };
}
