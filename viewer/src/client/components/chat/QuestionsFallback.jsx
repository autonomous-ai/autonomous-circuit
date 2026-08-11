import { Button } from "@/components/ui/button";
import { startTurn, useChatStore } from "@/store/chat";
import { DELEGATE_ANSWER } from "./questionFence.js";

/**
 * What the chat shows when a `circuit-questions` fence cannot be read.
 *
 * The old behaviour was to fall through to a code block, so a first-time user
 * got one clipped line of raw JSON under the words "Waiting for your answer" —
 * unreadable, unanswerable, and with nothing on screen suggesting a next move.
 * Dead ends are defects, so this card does the one useful thing available: it
 * offers the same "you decide" escape the question card has, and says plainly
 * that typing works too.
 *
 * While the turn is still running the fence may simply be half-streamed, and
 * announcing a failure that resolves a second later is its own kind of lie —
 * so the same component says "still writing" until the turn ends.
 */
export default function QuestionsFallback() {
  const turnInProgress = useChatStore((s) => s.turnInProgress);

  if (turnInProgress) {
    return (
      <div
        data-slot="chat-questions-arriving"
        className="rounded-2xl border border-border bg-muted px-3 py-2.5 text-[13px] text-muted-foreground"
      >
        Still writing the questions…
      </div>
    );
  }

  return (
    <div
      data-slot="chat-questions-unreadable"
      className="rounded-2xl border border-border bg-muted p-3 text-sm text-foreground"
    >
      <p className="text-[13px] leading-5">
        Circuit tried to ask you a few questions and the message came through broken. Nothing is wrong with your
        board — this is our bug, not yours.
      </p>
      <p className="mt-1.5 text-[13px] leading-5 text-muted-foreground">
        You can let Circuit pick sensible answers, or just say what you want in your own words below.
      </p>
      <Button
        type="button"
        size="sm"
        onClick={() => startTurn(DELEGATE_ANSWER, { echoAs: "You decide — pick the best of each." })}
        data-slot="chat-questions-delegate"
        className="mt-3 h-9 rounded-lg px-4"
      >
        Let Circuit choose
      </Button>
    </div>
  );
}
