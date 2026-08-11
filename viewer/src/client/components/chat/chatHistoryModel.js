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
