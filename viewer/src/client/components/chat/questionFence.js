// Reading a ```circuit-questions fence without trusting it to be well-formed.
//
// The defect this exists for, watched on the first plain-language request
// anyone has typed into this app ("a nightlight that comes on when it gets
// dark"): the model answered with four good questions — power, light type,
// extra behaviour, assembled or bare — and the fence arrived one closing brace
// short. `JSON.parse` threw, the renderer fell through to its code-block
// fallback, and the user was shown a single clipped line of raw JSON reading
// `{"questions":[{"question":"How should it be powere` under the words
// "Waiting for your answer".
//
// That is a dead end with no exit: the questions are unreadable, the options
// are unreachable, and nothing on screen suggests what to type instead. It is
// also not a bug we can fix upstream — the fence is produced by a model, so
// malformed JSON is a normal event and the client has to survive it.
//
// Two rules shape the repair:
//
//   1. **Repair the container, never the content.** Closing brackets the
//      scanner knows are open is arithmetic. Guessing at a truncated option
//      label would be inventing the model's words, so anything incomplete is
//      dropped instead.
//   2. **Only complete questions survive.** A question with no text, or with
//      no option that has a label, cannot be answered — showing it as an empty
//      row would be worse than admitting it was lost.

/**
 * The one-click escape from every question surface: the question card's
 * delegate button and the unreadable-fence recovery card send the same
 * sentence, so "let it decide" means one thing to the model.
 */
export const DELEGATE_ANSWER =
  "Build the best version — you decide every preference above. Pick the best " +
  "option for each and proceed without asking again.";

/** A question is usable only if it can actually be answered. */
function usableQuestion(entry) {
  if (!entry || typeof entry !== "object") return null;
  const question = String(entry.question || "").trim();
  if (!question) return null;
  const options = (Array.isArray(entry.options) ? entry.options : [])
    .filter((opt) => opt && typeof opt === "object" && String(opt.label || "").trim())
    .map((opt) => ({
      label: String(opt.label).trim(),
      description: String(opt.description || "").trim(),
    }));
  if (!options.length) return null;
  return {
    question,
    header: String(entry.header || "").trim(),
    multiSelect: Boolean(entry.multiSelect),
    options,
  };
}

/**
 * Close whatever the scanner can prove is still open.
 *
 * Walks the text once tracking string state and the bracket stack, cuts any
 * partial trailing token (an unterminated string, a dangling comma, a key with
 * no value), then appends the closers in reverse order. No guessing: the stack
 * says exactly which characters are missing.
 *
 * @param {string} text
 * @returns {string} a string that is *more likely* to parse — not a promise
 */
export function closeOpenContainers(text) {
  const src = String(text || "");
  const stack = [];
  let inString = false;
  let escaped = false;
  // The last index at which the document was structurally "settled" — not
  // mid-string, and not immediately after a comma or a key's colon. Truncated
  // output gets cut back to here so a half-written value never reaches the
  // parser.
  let settled = 0;

  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') {
        inString = false;
        settled = i + 1;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{" || ch === "[") {
      stack.push(ch === "{" ? "}" : "]");
      continue;
    }
    if (ch === "}" || ch === "]") {
      stack.pop();
      settled = i + 1;
      continue;
    }
    if (ch === "," || ch === ":") continue;
    // A bare literal (number, true, false, null) settles where it ends; the
    // cheap approximation is to treat any non-space as settling.
    if (!/\s/.test(ch)) settled = i + 1;
  }

  let head = src.slice(0, settled).replace(/[,:]\s*$/, "");
  // A key with no value ("...,\"label\"") cannot be closed into anything
  // valid, so drop the orphan back to the previous separator.
  if (/[{[]\s*"[^"]*"$/.test(head) || /,\s*"[^"]*"$/.test(head)) {
    head = head.replace(/,?\s*"[^"]*"$/, "");
  }
  head = head.replace(/[,\s]+$/, "");
  return head + stack.reverse().join("");
}

/**
 * Parse a `circuit-questions` fence body into answerable questions.
 *
 * @param {string} raw
 * @returns {{questions: Array<object>, repaired: boolean, dropped: number}|null}
 *   null when nothing answerable could be recovered at all.
 */
export function parseQuestionFence(raw) {
  const text = String(raw || "").trim();
  if (!text) return null;

  let parsed = null;
  let repaired = false;
  try {
    parsed = JSON.parse(text);
  } catch {
    try {
      parsed = JSON.parse(closeOpenContainers(text));
      repaired = true;
    } catch {
      return null;
    }
  }

  const list = Array.isArray(parsed?.questions) ? parsed.questions : null;
  if (!list) return null;

  const questions = [];
  for (const entry of list) {
    const usable = usableQuestion(entry);
    if (usable) questions.push(usable);
  }
  if (!questions.length) return null;
  return { questions, repaired, dropped: list.length - questions.length };
}
