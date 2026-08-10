// Pure helpers for the onboarding wizard. Kept JSX/dep-free so Node's
// --test runner can exercise the state machine and re-poll logic.
//
// v1 is Create-only: onboarding is a prereq check (Claude Code + ffmpeg, with
// python shown but non-blocking) followed by done. There is no in-app
// installer, no guided sign-in, and no social account — missing tools get
// friendly manual instructions and the screen re-polls until they appear.

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
 * Collapse a full `app_prereq_check` result into the welcome screen's row
 * model. Gating: Claude Code AND ffmpeg must be present; python is surfaced
 * but never blocks (the render pipeline reports its own errors with more
 * context). A server that predates the ffmpeg field can't be gated on it, so
 * an absent `ffmpeg` object reads as ok-but-unknown rather than blocking
 * onboarding forever.
 */
export function evaluatePrereqCheck(check) {
  const claude = {
    ok: Boolean(check?.claudeCli?.found),
    version: String(check?.claudeCli?.version || ""),
  };
  const ffmpegReported = check?.ffmpeg != null;
  const ffmpeg = {
    ok: ffmpegReported ? Boolean(check.ffmpeg.found) : true,
    known: ffmpegReported,
    version: ffmpegReported ? String(check.ffmpeg.version || "") : "",
  };
  const python = {
    ok: Boolean(check?.python?.found),
    version: String(check?.python?.version || ""),
  };
  return {
    claude,
    ffmpeg,
    python,
    canContinue: claude.ok && ffmpeg.ok,
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

/** Manual ffmpeg install (macOS; the dev platform for v1). */
export const FFMPEG_INSTALL_COMMAND = "brew install ffmpeg";

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
