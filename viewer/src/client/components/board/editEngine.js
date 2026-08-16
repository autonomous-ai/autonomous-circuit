// editEngine — "the user moved U3 to (12.5, -4)" becomes one changed number in
// `boards/<stem>.tsx`, or a refusal that says why not.
//
// This is the headless half of the IDE: no React, no DOM, no fetch. It reads a
// board's source, says which positions the file can express, plans an edit as a
// set of byte ranges, and hands back an exact inverse so undo restores the file
// byte-for-byte rather than re-deriving a coordinate and hoping.
//
// `boardSource.js` is the parser, the anchor matcher and the splicer. This
// module is the part above it that a caller can hold: one read, one plan, one
// apply, one inverse. Three things it adds that the parser deliberately does
// not:
//
//   1. **A reason for every position the file cannot move.** The parser walks
//      past a part inside a `for` loop and a part inside a helper component
//      without a word — correct for the accept path, useless for a UI that has
//      to tell someone why the key they clicked will not drag. `scanRefusals`
//      finds every JSX tag in the file that names a `pcbX`, and every one of
//      them lands in exactly one of two lists: editable, or refused with a
//      sentence. Nothing is silently dropped.
//   2. **A plan that re-checks the file before it commits.** A placement holds
//      byte offsets from the parse that produced it. If the text under those
//      offsets is not what the parse saw, the plan refuses instead of writing a
//      coordinate into the middle of a comment.
//   3. **A byte-exact inverse.** `invertEdits` maps the edits back onto the
//      text they produced, so undo is a splice that restores the original
//      bytes. Re-deriving "move it back to (x, y)" cannot restore an insertion
//      (the lock comment) and cannot promise the surrounding bytes are
//      untouched; this can, and a test asserts the round trip is identity.
//
// What it refuses is the contract's list, and each refusal is structural — in
// every case there is no number in the board file to write:
// `docs/architecture/ide-edit-contract.md` § "What this refuses".

import {
  applyEdits,
  bindPlacements,
  describeMove,
  formatMm,
  geometrySnapshot,
  lockEdits,
  maskCommentsAndStrings,
  moveEdits,
  parseBoardSource,
  readNumericProp,
  readOpeningTag,
  rebindPlacements,
  snapDelta,
} from "./boardSource.js";

/** Why a position in the board file cannot be dragged. One of these codes rides
 *  with every refusal so a caller can group them without matching on prose. */
export const REFUSAL = Object.freeze({
  /** A `for`, `while` or `.map()` emits this tag — one line makes N parts. */
  LOOP_PLACED: "loop_placed",
  /** The tag is inside another JSX element, so its parent owns the position. */
  INSIDE_ELEMENT: "inside_element",
  /** The tag is inside a helper component this file defines, not inside `<board>`. */
  HELPER_COMPONENT: "helper_component",
  /** The tag is inside a `{…}` expression in `<board>` — code, not a plain tag. */
  INSIDE_EXPRESSION: "inside_expression",
  /** `pcbX={x - 1.4}` — an expression, so there is no literal to replace. */
  COMPUTED_POSITION: "computed_position",
  /** `pcbX="3mm"` — a string the compiler parses, not a number we may rewrite. */
  TEXT_POSITION: "text_position",
});

/** Why a *plan* was refused. Distinct from REFUSAL: these are about the request,
 *  not about the shape of the board file. */
export const PLAN_ERROR = Object.freeze({
  /** No placement with that id in the read. */
  UNKNOWN_PLACEMENT: "unknown_placement",
  /** The bytes under the placement's offsets are not what the parse saw. */
  STALE_SOURCE: "stale_source",
  /** A coordinate that is not a finite number. */
  INVALID_POSITION: "invalid_position",
  /** An undo whose edits no longer match the file. */
  STALE_UNDO: "stale_undo",
});

// ---------------------------------------------------------------------------
// reading: what this file can move, and what it cannot
// ---------------------------------------------------------------------------

/** The last `n` characters of real code before `at`, comments and string bodies
 *  removed. Used to ask what kind of construct opened a brace — a question a
 *  comment sitting between the two would otherwise answer wrongly. */
function codeTail(text, mask, at, n = 32) {
  const out = [];
  for (let i = at - 1; i >= 0 && out.length < n; i -= 1) {
    if (mask[i]) continue;
    out.push(text[i]);
  }
  return out.reverse().join("");
}

/** Index of the `}` matching the `{` at `open`, or -1. */
function matchBraceAt(text, mask, open) {
  let depth = 0;
  for (let i = open; i < text.length; i += 1) {
    if (mask[i]) continue;
    if (text[i] === "{") depth += 1;
    else if (text[i] === "}") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/** Index of the `(` matching the `)` at `close`, or -1. */
function matchParenBack(text, mask, close) {
  let depth = 0;
  for (let i = close; i >= 0; i -= 1) {
    if (mask[i]) continue;
    if (text[i] === ")") depth += 1;
    else if (text[i] === "(") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

// `.map(`, `.flatMap(`, `.forEach(`, `Array.from(` — a callback that runs once
// per element. `slots.flatMap((i) => …)` in harness-puck's ring is this shape.
const LOOP_CALL_RE = /(?:\.(?:map|flatMap|forEach|reduce)|Array\.from)$/;
const LOOP_KEYWORD_RE = /(?:^|[^A-Za-z0-9_$])(?:for|while)$/;

/** Does this bracket belong to a loop? */
function isLoopOpener(text, mask, opener) {
  if (opener.c === "(") {
    return LOOP_CALL_RE.test(codeTail(text, mask, opener.at, 40).trimEnd());
  }
  if (opener.c !== "{") return false;
  // `for (…) {` / `while (…) {` — the brace's own look-back is a `)`, and what
  // opened that paren is the keyword we are after.
  let j = opener.at - 1;
  while (j >= 0 && (mask[j] || /\s/.test(text[j]))) j -= 1;
  if (j < 0 || text[j] !== ")") return false;
  const open = matchParenBack(text, mask, j);
  if (open < 0) return false;
  return LOOP_KEYWORD_RE.test(codeTail(text, mask, open, 12).trimEnd());
}

/** For each index in `indices`, the stack of brackets open at that point. */
function openersAt(text, mask, indices) {
  const wanted = [...indices].sort((a, b) => a - b);
  const out = new Map();
  const stack = [];
  let w = 0;
  for (let i = 0; i < text.length && w < wanted.length; i += 1) {
    while (w < wanted.length && wanted[w] === i) {
      out.set(wanted[w], stack.slice());
      w += 1;
    }
    if (mask[i]) continue;
    const c = text[i];
    if (c === "(" || c === "{" || c === "[") stack.push({ c, at: i });
    else if (c === ")" || c === "}" || c === "]") stack.pop();
  }
  while (w < wanted.length) {
    out.set(wanted[w], stack.slice());
    w += 1;
  }
  return out;
}

/**
 * How a prop's value is written, for the *reason* only. The accept decision
 * stays with `readNumericProp`, which is the one function allowed to say a
 * value is a literal we may rewrite; this exists so a refusal can say "written
 * as text" rather than the same sentence for every non-number.
 *
 * @returns {{form: "expression"|"string", raw: string}|null}
 */
function readPropForm(text, mask, from, to, prop) {
  for (let i = from; i < to; i += 1) {
    if (mask[i]) continue;
    // Skip whole `{…}` values, exactly as `readNumericProp` does. Divergence
    // here would put the same tag in both lists: the scanner would see a pad's
    // `pcbX` inside an inline `footprint={…}` and call the tag positioned,
    // while the parser correctly reads the tag's own props and does not.
    if (text[i] === "{") {
      const close = matchBraceAt(text, mask, i);
      if (close < 0 || close >= to) break;
      i = close;
      continue;
    }
    if (text.slice(i, i + prop.length) !== prop) continue;
    if (i > 0 && /[A-Za-z0-9_$.]/.test(text[i - 1])) continue;
    let j = i + prop.length;
    if (j < to && /[A-Za-z0-9_$]/.test(text[j])) continue;
    while (j < to && /\s/.test(text[j])) j += 1;
    if (text[j] !== "=") continue;
    j += 1;
    while (j < to && /\s/.test(text[j])) j += 1;
    const c = text[j];
    if (c === '"' || c === "'") {
      const close = text.indexOf(c, j + 1);
      return { form: "string", raw: text.slice(j, close < 0 ? to : close + 1) };
    }
    if (c === "{") {
      const close = matchBraceAt(text, mask, j);
      const end = close < 0 ? text.length : close + 1;
      return { form: "expression", raw: text.slice(j, Math.min(end, j + 60)).replace(/\s+/g, " ") };
    }
    return { form: "expression", raw: "" };
  }
  return null;
}

/** Line number (1-based) of an offset — a refusal a person has to act on names
 *  the line, because that is how they will find it in their editor. */
function lineOf(text, at) {
  let line = 1;
  for (let i = 0; i < at && i < text.length; i += 1) if (text[i] === "\n") line += 1;
  return line;
}

// Top-level only — no leading whitespace before the keyword. An indented
// declaration is a local inside the helper we are trying to name, and naming
// the tag's owner `sy` (the last `const` before it) tells a user nothing.
const DECL_RE = /(?:^|\n)(?:export\s+)?(?:const|function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)/g;

/** The nearest top-level declaration above `at` — the helper that emits this
 *  tag. `keyCells` for terminal-keyboard's 50 keys, `PuckRing` for the ring. */
function enclosingDeclaration(text, at) {
  DECL_RE.lastIndex = 0;
  let name = "";
  let match = DECL_RE.exec(text);
  while (match && match.index < at) {
    name = match[1];
    match = DECL_RE.exec(text);
  }
  return name;
}

/**
 * Walk `<board>`'s children exactly the way the parser does, recording where
 * each positioned tag sits: at depth 0 (a placement the board file owns), or
 * inside some other element.
 *
 * @returns {{start: number, end: number, tags: Map<number, {tag: string, depth: number, parent: string}>}|null}
 */
function scanBoardBody(text, mask) {
  let boardLt = -1;
  for (let i = 0; i < text.length; i += 1) {
    if (mask[i] || text[i] !== "<") continue;
    const tag = readOpeningTag(text, mask, i);
    if (tag?.tag === "board") {
      boardLt = i;
      break;
    }
  }
  if (boardLt < 0) return null;
  const boardTag = readOpeningTag(text, mask, boardLt);
  if (!boardTag || boardTag.selfClosing) return null;

  const tags = new Map();
  const stack = [];
  let i = boardTag.gt + 1;
  let end = text.length;
  while (i < text.length) {
    if (mask[i]) {
      i += 1;
      continue;
    }
    const c = text[i];
    if (c === "{") {
      // An expression container in child position. The parser skips it whole;
      // so do we, and anything inside it is reported by the global scan as
      // built by code rather than written as a tag.
      const close = matchBraceAt(text, mask, i);
      if (close < 0) break;
      i = close + 1;
      continue;
    }
    if (c !== "<") {
      i += 1;
      continue;
    }
    if (text[i + 1] === "/") {
      const gt = text.indexOf(">", i);
      if (gt < 0) break;
      if (!stack.length) {
        end = gt + 1; // </board>
        break;
      }
      stack.pop();
      i = gt + 1;
      continue;
    }
    const tag = readOpeningTag(text, mask, i);
    if (!tag) {
      i += 1;
      continue;
    }
    tags.set(i, { tag: tag.tag, depth: stack.length, parent: stack[stack.length - 1] || "board" });
    if (!tag.selfClosing) stack.push(tag.tag);
    i = tag.gt + 1;
  }
  return { start: boardLt, end, tags };
}

/**
 * Every JSX tag in the file that names a `pcbX`, classified.
 *
 * The point of scanning the *whole* file rather than only `<board>`: the parts
 * a user most wants an explanation for are the ones the parser never sees.
 * terminal-keyboard's 50 keys are emitted by `keyCells()` a hundred lines above
 * `<board>`; harness-puck's 8 ring pixels come out of a `.flatMap` inside
 * `PuckRing`. Both are invisible to a scan of the board element, and both are
 * the first thing someone will try to drag.
 *
 * @returns {{editable: Array<{at: number, tag: string}>, refused: Array<{at, line, tag, code, reason, detail}>}}
 */
export function scanRefusals(source) {
  const text = String(source || "");
  const mask = maskCommentsAndStrings(text);
  const body = scanBoardBody(text, mask);

  // Every positioned tag in the file, wherever it lives.
  const found = [];
  for (let i = 0; i < text.length; i += 1) {
    if (mask[i] || text[i] !== "<") continue;
    const tag = readOpeningTag(text, mask, i);
    if (!tag) continue;
    const form = readPropForm(text, mask, tag.nameEnd, tag.gt, "pcbX");
    if (!form) continue;
    found.push({ at: i, gt: tag.gt, nameEnd: tag.nameEnd, tag: tag.tag, form });
  }

  const openers = openersAt(text, mask, found.map((entry) => entry.at));
  const editable = [];
  const refused = [];

  for (const entry of found) {
    const place = body?.tags.get(entry.at) || null;
    const inBoard = Boolean(body) && entry.at > body.start && entry.at < body.end;
    const loop = (openers.get(entry.at) || []).some((opener) => isLoopOpener(text, mask, opener));
    const line = lineOf(text, entry.at);
    const push = (code, reason, detail = "") =>
      refused.push({ at: entry.at, line, tag: entry.tag, code, reason, detail });

    if (loop) {
      const owner = enclosingDeclaration(text, entry.at);
      push(
        REFUSAL.LOOP_PLACED,
        `${owner ? `${owner}()` : "a loop"} emits these in a loop — the board file has no line for just this one`,
        entry.form.raw,
      );
      continue;
    }
    if (!inBoard) {
      const owner = enclosingDeclaration(text, entry.at);
      push(
        REFUSAL.HELPER_COMPONENT,
        `${owner ? `<${owner}>` : "a component in this file"} places this, not the board file — move the whole block instead`,
        entry.form.raw,
      );
      continue;
    }
    if (!place) {
      push(
        REFUSAL.INSIDE_EXPRESSION,
        "the board file builds this with code rather than writing it as a tag",
        entry.form.raw,
      );
      continue;
    }
    if (place.depth > 0) {
      push(
        REFUSAL.INSIDE_ELEMENT,
        `it sits inside <${place.parent}>, and only a direct child of <board> has a line to change`,
        entry.form.raw,
      );
      continue;
    }
    const x = readNumericProp(text, mask, entry.nameEnd, entry.gt, "pcbX");
    const y = readNumericProp(text, mask, entry.nameEnd, entry.gt, "pcbY");
    if (x === "non-numeric" || y === "non-numeric" || !x || !y) {
      const bad = x === "non-numeric" || !x ? "pcbX" : "pcbY";
      const form = readPropForm(text, mask, entry.nameEnd, entry.gt, bad);
      if (!form) {
        push(REFUSAL.COMPUTED_POSITION, `it names a ${bad === "pcbX" ? "pcbY" : "pcbX"} but no ${bad}`, "");
      } else if (form.form === "string") {
        push(REFUSAL.TEXT_POSITION, `its position is written as text (${bad}=${form.raw}), not a number`, form.raw);
      } else {
        push(
          REFUSAL.COMPUTED_POSITION,
          `its position is computed (${bad}=${form.raw}), so there is no number in the file to change`,
          form.raw,
        );
      }
      continue;
    }
    editable.push({ at: entry.at, tag: entry.tag });
  }

  return { editable, refused };
}

const EMPTY_BINDING = Object.freeze({
  byId: new Map(),
  byComponentKey: new Map(),
  byElementId: new Map(),
  unmatched: [],
});

/**
 * Read a board file into everything a caller needs to offer an edit.
 *
 * `index` is the compiled board (`buildBoardIndex(circuit.json)`) and is
 * optional: without it the placements are still returned and still writable —
 * the file is the model — they just have no geometry to draw a handle on.
 *
 * `snapshot` is geometry captured at the last build (`geometrySnapshot`). Pass
 * it after any edit: re-matching by coordinate would unbind the part that was
 * just moved, because the file says one thing and the built board still says
 * the other, and that disagreement is the honest state until a rebuild.
 *
 * @param {string} source
 * @param {{index?: object|null, snapshot?: object|null}} [options]
 */
export function readBoard(source, { index = null, snapshot = null } = {}) {
  const text = String(source || "");
  const parsed = parseBoardSource(text);
  if (!parsed.ok) {
    return {
      ok: false,
      reason: parsed.reason,
      source: text,
      placements: [],
      byId: new Map(),
      byComponentKey: new Map(),
      byElementId: new Map(),
      refusals: [],
      unbound: [],
      snapshot: null,
    };
  }

  const binding = snapshot
    ? rebindPlacements(parsed.placements, snapshot)
    : index
      ? bindPlacements(parsed.placements, index)
      : EMPTY_BINDING;

  // A placement the binder could not tie to geometry is still a line in the
  // file, so it stays in the list with `bound: false` and the binder's own
  // words for why. The engine can write it; only the canvas cannot point at it.
  const reasonById = new Map();
  for (const entry of binding.unmatched) reasonById.set(entry.placement.id, entry.reason);

  const placements = parsed.placements.map((placement) => {
    const bound = binding.byId.get(placement.id);
    if (bound) return { ...bound, bound: true, bindReason: "" };
    return {
      ...placement,
      bound: false,
      bindReason: reasonById.get(placement.id) || (index || snapshot ? "" : "the board has not been built yet"),
      label: placement.name || placement.tag,
      componentKeys: [],
      refdes: [],
      pcbIds: new Set(),
    };
  });

  const byId = new Map(placements.map((placement) => [placement.id, placement]));
  const { refused } = scanRefusals(text);

  return {
    ok: true,
    reason: "",
    source: text,
    placements,
    byId,
    byComponentKey: binding.byComponentKey,
    byElementId: binding.byElementId,
    /** Positions in this file that no edit can express, each with a sentence. */
    refusals: refused,
    /** Placements with no geometry behind them yet. */
    unbound: placements.filter((placement) => !placement.bound),
    /** Geometry to carry into the next read. Taken from the binding we already
     *  did, never re-derived: `bindBoardIndex` walks every element on the board
     *  and the caller will pass this back on every keystroke after an edit. */
    snapshot: snapshot || (index ? geometrySnapshot(binding) : null),
  };
}

// ---------------------------------------------------------------------------
// planning: an edit, checked against the bytes it is about to replace
// ---------------------------------------------------------------------------

const refuse = (code, reason) => ({ ok: false, code, reason, edits: [] });

/** Are the placement's offsets still describing the text they were parsed from?
 *  A plan that skips this check writes a coordinate into whatever moved into
 *  those bytes — a comment, usually, because comments are what surround them. */
function offsetsAreCurrent(source, placement) {
  const text = String(source || "");
  const at = (span) => text.slice(span.start, span.end);
  if (!placement?.xSpan || !placement?.ySpan) return false;
  if (placement.xSpan.end > text.length || placement.ySpan.end > text.length) return false;
  return Number(at(placement.xSpan)) === Number(placement.x) && Number(at(placement.ySpan)) === Number(placement.y);
}

/**
 * Move a placement to an absolute board position.
 *
 * Returns a *plan*, never a written file: `{ok, kind, edits, summary}`. A plan
 * with no edits (`ok: true`, `edits: []`) is a move to where the part already
 * is — the caller should do nothing rather than write a file with no change in
 * it, which would invalidate the build cache for free.
 */
export function planMove(source, placement, x, y) {
  if (!placement) return refuse(PLAN_ERROR.UNKNOWN_PLACEMENT, "that placement is not in this board file");
  if (!Number.isFinite(Number(x)) || !Number.isFinite(Number(y))) {
    return refuse(PLAN_ERROR.INVALID_POSITION, "a position has to be a number of millimetres");
  }
  if (!offsetsAreCurrent(source, placement)) {
    return refuse(PLAN_ERROR.STALE_SOURCE, "the board file changed since it was read — reopen the board and try again");
  }
  const edits = moveEdits(source, placement, x, y);
  const to = { x: Number(formatMm(x)), y: Number(formatMm(y)) };
  return {
    ok: true,
    code: "",
    reason: "",
    kind: "move",
    placementId: placement.id,
    edits,
    from: { x: placement.x, y: placement.y },
    to,
    summary: edits.length ? describeMove(placement.label || placement.id, placement, to) : "",
  };
}

/**
 * Move a placement by a delta, snapped to `step`.
 *
 * Snapping the *movement* rather than the destination is the whole difference:
 * a 2.54mm testpoint nudged onto a 0.5mm grid would land at 2.5 and quietly
 * lose its pitch, while snapping the delta keeps every relationship the board
 * already had.
 */
export function planNudge(source, placement, dx, dy, { step = 0 } = {}) {
  if (!placement) return refuse(PLAN_ERROR.UNKNOWN_PLACEMENT, "that placement is not in this board file");
  const sx = step ? snapDelta(dx, step) : Number(dx) || 0;
  const sy = step ? snapDelta(dy, step) : Number(dy) || 0;
  return planMove(source, placement, placement.x + sx, placement.y + sy);
}

/**
 * Lock or unlock a placement: a comment above the tag, in the file and in the
 * diff, so the next agent reads it off `boards/main.tsx` rather than out of
 * this app's memory. Inert to the compiler, and — say it plainly — a
 * convention, not a write barrier.
 */
export function planLock(source, placement, locked) {
  if (!placement) return refuse(PLAN_ERROR.UNKNOWN_PLACEMENT, "that placement is not in this board file");
  if (!offsetsAreCurrent(source, placement)) {
    return refuse(PLAN_ERROR.STALE_SOURCE, "the board file changed since it was read — reopen the board and try again");
  }
  const edits = lockEdits(source, placement, locked);
  return {
    ok: true,
    code: "",
    reason: "",
    kind: "lock",
    placementId: placement.id,
    edits,
    locked: Boolean(locked),
    summary: edits.length ? `${placement.label || placement.id} ${locked ? "locked in place" : "unlocked"}` : "",
  };
}

// ---------------------------------------------------------------------------
// applying, and going back
// ---------------------------------------------------------------------------

/**
 * The edits that undo `edits`, expressed over the text they produce.
 *
 * Undo by re-deriving a coordinate cannot restore an insertion, and cannot
 * promise that the bytes around the number came back unchanged. This can: each
 * edit already carries the text it replaced, so the inverse is that text spliced
 * back over the range the replacement now occupies. Positions shift by the
 * running length change of every earlier edit, which is why the list is sorted
 * before the walk.
 *
 * The inverse carries `expected` too, so an undo sent to the server is the same
 * compare-and-swap as the edit was: if an agent rewrote the line in between, the
 * undo is refused rather than clobbering the agent's work.
 */
export function invertEdits(edits) {
  const list = [...(edits || [])].sort((a, b) => a.start - b.start || a.end - b.end);
  const out = [];
  let shift = 0;
  for (const edit of list) {
    if (typeof edit.expected !== "string") {
      throw new Error("an edit with no `expected` cannot be inverted");
    }
    const start = edit.start + shift;
    const end = start + String(edit.text).length;
    out.push({ start, end, text: edit.expected, expected: String(edit.text) });
    shift += String(edit.text).length - (edit.end - edit.start);
  }
  return out;
}

/**
 * Apply a plan. Returns the new text and the plan that undoes it.
 *
 * The caller writes `text` (through `board_source_write`, which re-checks every
 * `expected` against the file on disk) and keeps `inverse` for the undo stack.
 */
export function applyPlan(source, plan) {
  if (!plan?.ok) throw new Error(`cannot apply a refused plan: ${plan?.reason || "no reason given"}`);
  if (!plan.edits.length) return { text: String(source || ""), inverse: null, changed: false };
  const text = applyEdits(source, plan.edits);
  const inverse = {
    ok: true,
    code: "",
    reason: "",
    kind: plan.kind,
    placementId: plan.placementId,
    edits: invertEdits(plan.edits),
    summary: `undid: ${plan.summary}`,
    ...(plan.kind === "move" ? { from: plan.to, to: plan.from } : { locked: !plan.locked }),
  };
  return { text, inverse, changed: true };
}

/**
 * The undo stack: plans, not coordinates.
 *
 * Pure and synchronous — the caller owns the write. `undo(source)` refuses when
 * the file no longer holds the bytes the inverse expects, because an undo that
 * lands on text somebody else wrote is a clobber wearing an undo's clothes.
 *
 * @param {{limit?: number}} [options] 50 deep, matching the contract.
 */
export function createHistory({ limit = 50 } = {}) {
  let stack = [];
  return {
    get depth() {
      return stack.length;
    },
    get canUndo() {
      return stack.length > 0;
    },
    /** The description of what undo would reverse, for a button's tooltip. */
    get top() {
      return stack[stack.length - 1] || null;
    },
    push(inverse) {
      if (!inverse) return;
      stack = [...stack, inverse].slice(-limit);
    },
    /** Drop everything — the file being edited changed identity. */
    clear() {
      stack = [];
    },
    /**
     * The plan that undoes the most recent edit, checked against `source`.
     * Pops only on success, so a refused undo leaves the stack intact and the
     * user can retry after reloading the file.
     */
    undo(source) {
      const plan = stack[stack.length - 1];
      if (!plan) return refuse(PLAN_ERROR.STALE_UNDO, "nothing to undo");
      const text = String(source || "");
      for (const edit of plan.edits) {
        if (text.slice(edit.start, edit.end) !== edit.expected) {
          return refuse(
            PLAN_ERROR.STALE_UNDO,
            "the board file changed since that edit — reopen the board rather than undoing onto somebody else's work",
          );
        }
      }
      stack = stack.slice(0, -1);
      return plan;
    },
  };
}
