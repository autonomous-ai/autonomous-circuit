import { isClaudeMissingError, openClaudeSetup } from "@/store/claudeSetup.js";

/**
 * Renders a chat error message. A missing-`claude` error gets a "Show me how"
 * action that opens the manual-setup instructions dialog (v1 has no in-app
 * installer or checkout — Create-only). Everything else renders verbatim,
 * including billing/subscription errors: the user manages their Claude
 * subscription with Anthropic, not here.
 */
export default function ChatErrorContent({ message }) {
  if (isClaudeMissingError(message)) {
    return (
      <>
        Claude Code isn’t set up on this computer yet.{" "}
        <button
          type="button"
          data-slot="chat-error-claude-setup"
          className="font-semibold text-orange-500 underline underline-offset-2 hover:text-orange-600"
          onClick={() => openClaudeSetup()}
        >
          Show me how
        </button>
      </>
    );
  }
  return <>{message}</>;
}
