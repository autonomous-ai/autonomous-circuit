// Group each user prompt with the assistant turns that answer it so related
// messages keep their spacing as a unit in the history.
export function groupTurns(history) {
  const groups = [];
  for (const turn of history) {
    if (turn.role === "user" || groups.length === 0) {
      groups.push([turn]);
    } else {
      groups[groups.length - 1].push(turn);
    }
  }
  return groups;
}

/**
 * The user's own words, with the notes we appended for the model removed.
 *
 * `startTurn` sends the typed text plus up to three model-facing lines — the
 * viewer-context note, the frame-suggestion directive, and the reasoning-effort
 * directive — and keeps the echoed bubble clean by storing `text` separately.
 * A reload does not have that luxury: history is rebuilt from Claude Code's
 * transcript, which only ever saw the combined string. So after any refresh,
 * "a nightlight that comes on when it gets dark" came back as
 *
 *     a nightlight that comes on when it gets dark
 *
 *     [Effort: high — think hard before writing the board. Check every block's
 *     pin assignment against its declared pinout and state the power budget
 *     arithmetic.]
 *
 * attributed to the user, in their own bubble. It reads as the app putting
 * words in their mouth, and none of it means anything to them.
 *
 * Bracketed notes are matched by shape rather than by exact text so a reworded
 * directive cannot start leaking again; the frame directive is matched by its
 * opening clause. Anything that is not one of ours is left alone — a user who
 * genuinely typed a line in square brackets keeps it.
 */
const MODEL_DIRECTIVE_LINE = /^\[(?:Effort|Viewer context):[\s\S]*\]$/;
const FRAME_DIRECTIVE_OPENING = /^The user sent a view from the board workspace but did not say what to change\./;

export function userVisibleText(content) {
  const text = String(content ?? "");
  if (!text.includes("[") && !FRAME_DIRECTIVE_OPENING.test(text)) return text;
  const kept = text
    .split(/\n{2,}/)
    .filter((part) => {
      const trimmed = part.trim();
      if (!trimmed) return false;
      if (MODEL_DIRECTIVE_LINE.test(trimmed)) return false;
      if (FRAME_DIRECTIVE_OPENING.test(trimmed)) return false;
      return true;
    })
    .join("\n\n");
  // All of it was ours: the user really did send an attachment with no words,
  // and an empty bubble is more honest than replaying our own instructions.
  return kept;
}

/**
 * Did this assistant turn end without saying anything?
 *
 * Watched on a real run and it is the worst outcome in the product. Someone
 * asked for a ceiling dimmer; forty minutes later the last thing in the chat
 * was the model's own sentence "Now the cheap structural check before paying
 * for a full build:", followed by three tool rows, and then nothing. The turn
 * had ended. The board pane said "No build to judge yet". There was no board,
 * no error, no reply, and nothing on screen suggesting what to do next — the
 * conversation just stopped mid-promise.
 *
 * The client cannot make the model finish. It can refuse to leave a stopped
 * turn looking like a running one, and it can hand back a next step.
 *
 * **Only while it is still the last word.** The driver resumes a silent turn
 * by itself ("Continue from where you left off"), so on a watched run the card
 * was still saying "it stopped without replying" three turns above the answer
 * it had gone on to give — an alarm about something that had already been
 * handled. A turn with anything after it is history, not a dead end.
 *
 * The test is what the turn's **last word** was. A turn whose final piece of
 * content is a tool call ended mid-flow — it ran something and never came
 * back to say what happened. A turn that closes with text, a plan or an error
 * has said its piece, whatever else it did along the way; a cancelled or
 * still-running turn already reads as one.
 *
 * @param {{role?: string, status?: string, blocks?: Array<{kind?: string, text?: string}>}} turn
 * @param {{isLastTurn?: boolean}} [where] false once anything follows the turn
 */
export function turnStoppedWithoutAnswering(turn, { isLastTurn = true } = {}) {
  if (!isLastTurn) return false;
  if (!turn || turn.role !== "assistant" || turn.status !== "complete") return false;
  const blocks = Array.isArray(turn.blocks) ? turn.blocks : [];
  // Scan back to the last block that carries content. Artifacts are recorded
  // but never rendered, and an empty text block is what a stopped stream
  // leaves behind, so neither counts as the turn's last word.
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    const block = blocks[i];
    const kind = block?.kind;
    if (kind === "artifact") continue;
    if (kind === "text" && !String(block.text || "").trim()) continue;
    return kind === "tool_use";
  }
  return false;
}
