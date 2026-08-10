"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, ExternalLink, Loader2, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { transport } from "@/lib/transport.ts";
import { copyTextToClipboard } from "@/ui/clipboard";
import { cn } from "@/ui/utils";
import {
  buildOnboardedSettings,
  CLAUDE_CHECK_POLL_INTERVAL_MS,
  CLAUDE_INSTALL_URL,
  CLAUDE_LOGIN_HINT,
  evaluatePrereqCheck,
  KICAD_INSTALL_COMMAND,
  NODE_INSTALL_COMMAND,
  PYTHON_INSTALL_COMMAND,
  TOOLCHAIN_INSTALL_COMMAND,
} from "./onboardingHelpers.js";

/**
 * Single-screen onboarding, v1: a prereq check and nothing else.
 *
 * `app_prereq_check` reports the tools a board build needs (contract §2) —
 * the `claude` CLI, Node ≥22.12, the pinned toolchain, and Python ≥3.10 —
 * plus kicad-cli (shown, but non-blocking: fab packets need it; everything
 * else works). Anything missing gets friendly manual instructions. The screen
 * re-polls every few seconds so finishing an install out-of-band lights the
 * row green on its own. Continue persists `hasOnboarded: true` and hands
 * control to the app. No auth flows, no installers, no accounts.
 */
export default function WelcomeScreen({ onComplete }) {
  const [checking, setChecking] = useState(true);
  const [prereqs, setPrereqs] = useState(null);
  const [checkError, setCheckError] = useState("");
  const [finishing, setFinishing] = useState(false);
  const pollTimerRef = useRef(0);

  // Read-only and idempotent, so it doubles as the poll tick: a user who
  // installs a tool out-of-band sees the row flip without a manual refresh.
  const runDetect = useCallback(async () => {
    try {
      const check = await transport.app_prereq_check();
      setPrereqs(evaluatePrereqCheck(check));
      setCheckError("");
    } catch (err) {
      const message =
        err && typeof err === "object" && "message" in err
          ? String(err.message || "Failed to check your setup")
          : String(err || "Failed to check your setup");
      setCheckError(message);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void runDetect();
    const tick = () => {
      if (cancelled) return;
      void runDetect();
      pollTimerRef.current = setTimeout(tick, CLAUDE_CHECK_POLL_INTERVAL_MS);
    };
    pollTimerRef.current = setTimeout(tick, CLAUDE_CHECK_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, [runDetect]);

  // Flip hasOnboarded, then hand control to the app. Re-read first so we
  // never clobber settings persisted elsewhere.
  const finish = useCallback(async () => {
    setFinishing(true);
    try {
      const existing = await transport.app_settings_read();
      const next = buildOnboardedSettings(existing);
      await transport.app_settings_write(next);
      onComplete?.(next);
    } catch (err) {
      console.warn("Failed to persist onboarding completion", err);
      // Let the user into the app anyway — they can re-run setup later.
      onComplete?.(null);
    } finally {
      setFinishing(false);
    }
  }, [onComplete]);

  const canContinue = Boolean(prereqs?.canContinue);

  return (
    <div
      role="dialog"
      aria-label="Welcome to Autonomous Circuit"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 p-4"
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-background p-8 shadow-xl">
        <header className="flex flex-col gap-1 text-center">
          <Sparkles className="mx-auto size-7 text-emerald-600" />
          <h1 className="mt-3 text-2xl font-semibold">
            Design real circuit boards by chatting with AI.
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            A few tools power the workshop. Install what’s missing and continue.
          </p>
        </header>

        <div className="mt-6 flex flex-col gap-2" data-testid="prereq-list">
          <PrereqRow
            label="Claude Code"
            ok={Boolean(prereqs?.claude.ok)}
            version={prereqs?.claude.version}
            checking={checking}
            testId="prereq-claude"
          >
            <p>
              Install it from{" "}
              <a
                href={CLAUDE_INSTALL_URL}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
                data-testid="claude-install-link"
              >
                <ExternalLink className="size-3.5" /> claude.ai/install
              </a>
              . {CLAUDE_LOGIN_HINT}
            </p>
          </PrereqRow>

          <PrereqRow
            label="Node 22+"
            ok={Boolean(prereqs?.node.ok)}
            version={prereqs?.node.version}
            checking={checking}
            testId="prereq-node"
          >
            <p className="flex flex-wrap items-center gap-1.5">
              Runs the board compiler. Install it with{" "}
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                {NODE_INSTALL_COMMAND}
              </code>
              <CopyCommand text={NODE_INSTALL_COMMAND} />
            </p>
          </PrereqRow>

          <PrereqRow
            label="Circuit toolchain"
            ok={Boolean(prereqs?.toolchain.ok)}
            checking={checking}
            testId="prereq-toolchain"
          >
            <p className="flex flex-wrap items-center gap-1.5">
              The pinned board compiler. From the repo, run{" "}
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                {TOOLCHAIN_INSTALL_COMMAND}
              </code>
              <CopyCommand text={TOOLCHAIN_INSTALL_COMMAND} />
            </p>
          </PrereqRow>

          <PrereqRow
            label="Python 3.10+"
            ok={Boolean(prereqs?.python.ok)}
            version={prereqs?.python.version}
            checking={checking}
            testId="prereq-python"
          >
            <p className="flex flex-wrap items-center gap-1.5">
              Runs the build pipeline. Install it with{" "}
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                {PYTHON_INSTALL_COMMAND}
              </code>
              <CopyCommand text={PYTHON_INSTALL_COMMAND} />
            </p>
          </PrereqRow>

          <PrereqRow
            label="KiCad"
            ok={Boolean(prereqs?.kicad.ok)}
            version={prereqs?.kicad.version}
            checking={checking}
            optional
            testId="prereq-kicad"
          >
            <p className="flex flex-wrap items-center gap-1.5">
              Verifies boards and exports the fab gerbers. You can continue
              without it — install later with{" "}
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                {KICAD_INSTALL_COMMAND}
              </code>
              <CopyCommand text={KICAD_INSTALL_COMMAND} />
            </p>
          </PrereqRow>
        </div>

        {checkError ? (
          <p className="mt-3 text-sm text-destructive" role="alert">
            {checkError}
          </p>
        ) : null}

        <div className="mt-6 flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => void runDetect()}
            disabled={finishing}
            data-testid="welcome-recheck"
          >
            Re-check
          </Button>
          <Button
            variant="default"
            size="lg"
            className="flex-1"
            onClick={() => void finish()}
            disabled={!canContinue || finishing || checking}
            data-testid="welcome-continue"
          >
            {finishing ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" /> Finishing…
              </>
            ) : (
              "Start building"
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** A tiny copy button so a non-technical user pastes the command exactly
 * instead of mistyping it. Shows a brief "Copied" state. */
function CopyCommand({ text }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    const ok = await copyTextToClipboard(text);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [text]);
  return (
    <button
      type="button"
      onClick={() => void onCopy()}
      aria-label={`Copy the command: ${text}`}
      className="inline-flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:border-primary/60 hover:text-foreground"
    >
      {copied ? <Check className="size-3 text-emerald-500" aria-hidden /> : <Copy className="size-3" aria-hidden />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

/** One prereq row: status glyph + name + version, instructions when missing. */
function PrereqRow({ label, ok, version, checking, optional = false, testId, children }) {
  return (
    <div
      className="rounded-md border border-border bg-card/60 p-3"
      data-testid={testId}
      data-ok={ok ? "true" : "false"}
    >
      <div className="flex items-center gap-2">
        {checking && !ok ? (
          <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />
        ) : ok ? (
          <Check className="size-4 shrink-0 text-emerald-500" aria-hidden />
        ) : (
          <X
            className={cn("size-4 shrink-0", optional ? "text-muted-foreground" : "text-destructive")}
            aria-hidden
          />
        )}
        <span className="text-sm font-medium">{label}</span>
        {optional ? (
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            optional
          </span>
        ) : null}
        {ok && version ? (
          <span className="ml-auto font-mono text-xs text-muted-foreground">{version}</span>
        ) : null}
      </div>
      {!ok && !checking ? (
        <div className="mt-2 pl-6 text-sm text-muted-foreground">{children}</div>
      ) : null}
    </div>
  );
}
