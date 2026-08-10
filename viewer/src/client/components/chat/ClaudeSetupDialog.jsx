import { ExternalLink, Loader2, TriangleAlert } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  CLAUDE_INSTALL_URL,
  CLAUDE_LOGIN_HINT,
} from "@/components/onboarding/onboardingHelpers.js";
import {
  dismissClaudeSetup,
  recheckClaude,
  useClaudeSetupStore,
} from "@/store/claudeSetup.js";

/**
 * Modal shown when a chat send needs the `claude` CLI but it isn't installed.
 * v1 is Create-only, so there is no in-app installer — this renders the manual
 * instructions (install link + "run `claude` once to sign in") and a Re-check
 * that resumes the parked send once the CLI appears. Mounted once in
 * ChatSidebar.
 */
export default function ClaudeSetupDialog() {
  const { open, phase, errorMessage, hasPendingSend } = useClaudeSetupStore();
  const checking = phase === "checking";

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? null : dismissClaudeSetup())}>
      <DialogContent data-slot="claude-setup-dialog" className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Set up Claude Code</DialogTitle>
          <DialogDescription>
            Autonomous TV runs on Claude Code, and it isn’t on this computer
            yet. Two quick steps in a terminal and you’re in.
            {hasPendingSend
              ? " Your message will send automatically once it’s detected."
              : ""}
          </DialogDescription>
        </DialogHeader>

        <ol
          className="flex flex-col gap-2 rounded-md border border-border bg-muted/30 p-3 text-sm"
          data-slot="claude-setup-instructions"
        >
          <li className="flex items-start gap-2">
            <span className="mt-px font-semibold text-muted-foreground">1.</span>
            <span>
              Install Claude Code:{" "}
              <a
                href={CLAUDE_INSTALL_URL}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
                data-slot="claude-setup-manual-link"
              >
                <ExternalLink className="size-3.5" /> claude.ai/install
              </a>
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-px font-semibold text-muted-foreground">2.</span>
            <span>{CLAUDE_LOGIN_HINT}</span>
          </li>
        </ol>

        {phase === "error" ? (
          <div
            className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm"
            role="alert"
            data-slot="claude-setup-error"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
            <span className="text-destructive">{errorMessage}</span>
          </div>
        ) : null}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => dismissClaudeSetup()}
            data-slot="claude-setup-dismiss"
          >
            Not now
          </Button>
          <Button
            variant="default"
            onClick={() => void recheckClaude()}
            disabled={checking}
            data-slot="claude-setup-recheck"
          >
            {checking ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" /> Checking…
              </>
            ) : (
              "I’ve installed it — re-check"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
