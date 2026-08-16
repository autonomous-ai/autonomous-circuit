// boardSource — read and edit the placements a board's TSX actually owns.
//
// The canvas is a view of the board; the board is the code. So a drag on the
// canvas has to end as a changed `pcbX`/`pcbY` literal in `boards/<stem>.tsx`,
// the same way an editor changes the file rather than some parallel model. If
// the canvas and the file could disagree, the file would stop being the board.
//
// Three rules the rest of this module exists to keep:
//
//   1. **Only the number moves.** An edit is a byte range covering one numeric
//      literal and nothing else. Every board in this repo carries comments that
//      record what was measured and what was tried ("tried tucking it beside C1
//      … that put the LDO's own input cap inside C1's courtyard"); a reformat
//      that eats those would cost more than the drag is worth.
//   2. **Only what the board file can move.** A part inside a golden block is
//      placed by the block, not by the board, so it is not draggable here —
//      moving it would mean editing a file other boards share. The draggable
//      unit is a direct child of `<board>`: a block instance, a `<group>`, or a
//      part the board wrote itself.
//   3. **Match, never guess.** A placement is bound to geometry only when its
//      `(pcbX, pcbY)` pair matches exactly one anchor in the compiled circuit.
//      Zero matches or two matches means not draggable, with a reason — a drag
//      that writes to the wrong element is worse than a drag that refuses.
//
// Everything here is pure text and pure data: no DOM, no fetch, no React, so
// `node:test` covers the parser, the matcher and the edit splicer directly.

// Relative, not the `@/` alias: this module is covered by `node:test`, which
// has no bundler to resolve the alias for it.
import { elementId as elementIdOf, pcbElementBox as pcbBoxOf } from "../../lib/boardIndex.js";
import { leadComponent } from "../../lib/boardRegions.js";

/** Inserted above a placement to tell the next agent a human chose this spot. */
export const LOCK_COMMENT = "{/* locked: placed by hand - do not move this without asking */}";
const LOCK_LINE_RE = /^\s*\{\s*\/\*\s*locked:/;

/** Snap steps offered in the UI, mm. */
export const SNAP_STEPS = Object.freeze([1, 0.5, 0.25, 0.1]);
/** Step used while a modifier asks for fine control. */
export const FINE_STEP_MM = 0.01;

/**
 * Rotation steps offered in the UI, degrees. 90 is first because it is
 * Altium's own default: "the amount of rotation, in degrees, applied to objects
 * floating on the cursor when the Spacebar is pressed", default 90.
 * https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences?version=22
 *
 * The other three are ours — Altium's step is a free-form number and publishes
 * no shortlist (**unverified**), so we offer the divisors of 90 that a real
 * footprint needs and leave arbitrary angles to the file.
 */
export const ROTATION_STEPS = Object.freeze([90, 45, 30, 15]);

/**
 * The furthest from the origin a placement may be put, in millimetres.
 *
 * A sanity bound, not a design rule. The board is the constraint an engineer
 * cares about, and this is nowhere near it: JLCPCB's largest panel is under
 * 500mm on a side, so ±1000mm is two board-widths outside anything orderable
 * and still refuses the class of value that actually turns up —
 * `pcbX={1e+30}`, which a fat-fingered API call and a confused model both
 * produce, which writes cleanly into the source, and which every check
 * downstream then calls legal (measured 2026-08-16 through `board_edit_apply`:
 * `saved: true`, `check.status: "legal"`, on a part a light-year off the
 * board).
 *
 * Deliberately not the board's own envelope: a part parked just outside the
 * outline while its neighbours are rearranged is a real move an EE makes, and
 * the DFM check is the thing that should complain about it, in the language of
 * board edges. This bound only catches numbers that are not positions at all.
 */
export const MAX_PLACEMENT_MM = 1000;

/**
 * Why this coordinate cannot be written, or "" when it can.
 *
 * One owner for the rule, imported by the client that types it and the server
 * that writes it, so the two cannot disagree about what a position is.
 */
export function placementRangeReason(x, y) {
  for (const [axis, value] of [["X", x], ["Y", y]]) {
    const number = Number(value);
    if (!Number.isFinite(number)) return `${axis} must be a number`;
    if (Math.abs(number) > MAX_PLACEMENT_MM) {
      return `${axis} is ${number}mm, which is off any board — positions must be within ±${MAX_PLACEMENT_MM}mm`;
    }
  }
  return "";
}

/**
 * The server's own cap on one edit's text (`http.mjs:176` `MAX_EDIT_TEXT`),
 * mirrored here so a wrap that could not be written is refused *before* the
 * user presses the key, with a reason, instead of failing at the transport.
 */
const MAX_EDIT_TEXT = 200;

const NAME_START = /[A-Za-z_$]/;
const NAME_CHAR = /[A-Za-z0-9_$.:-]/;
const NUMBER_LITERAL = /^[+-]?(?:\d+\.?\d*|\.\d+)$/;

/**
 * A mask marking every character that sits inside a comment or a string, so
 * the structural scan below can ignore a `<` in prose and a `{` in a template.
 * Comments win over strings because they are opened first — that is what keeps
 * an apostrophe inside a block comment from opening a fake string.
 *
 * @param {string} text
 * @returns {Uint8Array} 1 where the character is comment/string content
 */
export function maskCommentsAndStrings(text) {
  const mask = new Uint8Array(text.length);
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    const next = text[i + 1];
    if (c === "/" && next === "/") {
      while (i < text.length && text[i] !== "\n") mask[i++] = 1;
      continue;
    }
    if (c === "/" && next === "*") {
      mask[i++] = 1;
      mask[i++] = 1;
      while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) mask[i++] = 1;
      if (i < text.length) {
        mask[i++] = 1;
        mask[i++] = 1;
      }
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      const quote = c;
      mask[i++] = 1;
      while (i < text.length) {
        if (text[i] === "\\") {
          mask[i++] = 1;
          if (i < text.length) mask[i++] = 1;
          continue;
        }
        if (text[i] === quote) {
          mask[i++] = 1;
          break;
        }
        // An unterminated single-quoted string would otherwise swallow the rest
        // of the file; a newline ends it, which is what the language does too.
        if (quote !== "`" && text[i] === "\n") break;
        mask[i++] = 1;
      }
      continue;
    }
    i += 1;
  }
  return mask;
}

/** Index of the matching `}` for the `{` at `open`, or -1. */
function matchBrace(text, mask, open) {
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

/** Read a tag or attribute name starting at `at`; returns the end index. */
function readName(text, at) {
  let i = at;
  if (i >= text.length || !NAME_START.test(text[i])) return at;
  i += 1;
  while (i < text.length && NAME_CHAR.test(text[i])) i += 1;
  return i;
}

/**
 * The end of the opening tag that starts at `lt` (index of `<`).
 *
 * Exported for `editEngine.js`, which has to find the tags this parser
 * deliberately walks past — a part inside a loop, a part inside a helper
 * component — in order to say *why* they are not draggable. Two scanners
 * disagreeing about where a tag ends would put the same tag in both the
 * editable list and the refused list.
 *
 * @returns {{gt: number, nameEnd: number, tag: string, selfClosing: boolean}|null}
 */
export function readOpeningTag(text, mask, lt) {
  const nameEnd = readName(text, lt + 1);
  if (nameEnd === lt + 1) return null;
  const tag = text.slice(lt + 1, nameEnd);
  let i = nameEnd;
  while (i < text.length) {
    if (mask[i]) {
      i += 1;
      continue;
    }
    const c = text[i];
    if (c === "{") {
      const close = matchBrace(text, mask, i);
      if (close < 0) return null;
      i = close + 1;
      continue;
    }
    if (c === ">") {
      // The `/` of a self-closing tag has to be real code. `terminal-keyboard`
      // ends its `<board>` attribute list with a block comment, and a naive
      // look-back reads that comment's `*/` as `/>` — the whole board then
      // parses as an empty self-closing element and every placement vanishes.
      let back = i - 1;
      while (back > nameEnd && (mask[back] || /\s/.test(text[back]))) back -= 1;
      return { gt: i, nameEnd, tag, selfClosing: !mask[back] && text[back] === "/" };
    }
    i += 1;
  }
  return null;
}

/**
 * Everything about one prop on the tag whose attributes span [from, to): where
 * it is written, and in which of the three forms a board file can write it.
 *
 * `readNumericProp` below is the narrow view of this and is what the position
 * readers want. Rotation needs the wide one for two reasons the position
 * readers never had: it has to tell an expression from a quoted string to say
 * *why* it will not turn a part, and an absent `pcbRotation` has to be
 * insertable, which needs the end of a neighbouring prop as an anchor. One
 * scanner serves both — two scanners that disagreed about where a prop ends
 * would put the same tag in the editable list and the refused list at once.
 *
 * `propStart` is the first character of the name; `propEnd` is one past the
 * last character of the value, so `[propStart, propEnd)` is the whole prop.
 *
 * @returns {null
 *   | {form: "number", start: number, end: number, value: number, propStart: number, propEnd: number}
 *   | {form: "expression", propStart: number, propEnd: number}
 *   | {form: "text", propStart: number, propEnd: number}}
 */
export function readPropDetail(text, mask, from, to, prop) {
  for (let i = from; i < to; i += 1) {
    if (mask[i]) continue;
    // Skip whole `{…}` attribute values, so only the tag's OWN props are read.
    // A part written with an inline footprint carries a nested board inside one
    // attribute — `footprint={<footprint><smtpad pcbX="2.92995985mm" …/>…}`
    // (every `blocks/*/ldo-3v3.tsx` in the repo is this shape) — and a flat
    // scan finds the PAD's coordinate first and reports it as the part's
    // position. That is the "writes to the wrong element" failure this module
    // exists to refuse, arriving as a plausible number.
    if (text[i] === "{") {
      const close = matchBrace(text, mask, i);
      if (close < 0 || close >= to) break;
      i = close;
      continue;
    }
    if (text[i] !== prop[0]) continue;
    if (text.slice(i, i + prop.length) !== prop) continue;
    // Whole word only: `pcbX` must not match inside `pcbXOffset`.
    const before = i > 0 ? text[i - 1] : " ";
    if (NAME_CHAR.test(before)) continue;
    let j = i + prop.length;
    if (j < to && NAME_CHAR.test(text[j])) continue;
    while (j < to && /\s/.test(text[j])) j += 1;
    if (text[j] !== "=") continue;
    j += 1;
    while (j < to && /\s/.test(text[j])) j += 1;
    if (text[j] === '"' || text[j] === "'") {
      const quote = text[j];
      const close = text.indexOf(quote, j + 1);
      return { form: "text", propStart: i, propEnd: close < 0 || close >= to ? to : close + 1 };
    }
    // Not a brace and not a quote: a bare `pcbRotation=90deg`. Unwritable for
    // the same reason a quoted value is, so it reports as the same form.
    if (text[j] !== "{") return { form: "text", propStart: i, propEnd: j };
    const close = matchBrace(text, mask, j);
    if (close < 0) return { form: "expression", propStart: i, propEnd: to };
    let start = j + 1;
    let end = close;
    while (start < end && /\s/.test(text[start])) start += 1;
    while (end > start && /\s/.test(text[end - 1])) end -= 1;
    const literal = text.slice(start, end);
    if (!NUMBER_LITERAL.test(literal)) return { form: "expression", propStart: i, propEnd: close + 1 };
    const value = Number(literal);
    if (!Number.isFinite(value)) return { form: "expression", propStart: i, propEnd: close + 1 };
    return { form: "number", start, end, value, propStart: i, propEnd: close + 1 };
  }
  return null;
}

/**
 * The value of prop `prop` on the tag whose attributes span [from, to).
 * Only a bare numeric literal inside `{}` is editable — `pcbX="3mm"` is a
 * string the compiler parses, not a number we may rewrite, and saying so is
 * better than writing `3.5` where `"3mm"` was.
 *
 * @returns {{start: number, end: number, value: number}|null|"non-numeric"}
 */
export function readNumericProp(text, mask, from, to, prop) {
  const detail = readPropDetail(text, mask, from, to, prop);
  if (!detail) return null;
  if (detail.form !== "number") return "non-numeric";
  return { start: detail.start, end: detail.end, value: detail.value };
}

/** Start-of-line index for `at`. */
function lineStartOf(text, at) {
  const nl = text.lastIndexOf("\n", Math.max(0, at - 1));
  return nl < 0 ? 0 : nl + 1;
}

/** The `name="…"` prop of a tag, when it is a plain string literal. */
function readNameProp(text, mask, from, to) {
  for (let i = from; i < to; i += 1) {
    if (mask[i]) continue;
    // Same nested-attribute skip as `readNumericProp`: an inline footprint
    // holds `<smtpad>`s of its own, and the first `name=` inside one of them
    // is not what this tag is called.
    if (text[i] === "{") {
      const close = matchBrace(text, mask, i);
      if (close < 0 || close >= to) break;
      i = close;
      continue;
    }
    if (text.slice(i, i + 4) !== "name") continue;
    if (i > 0 && NAME_CHAR.test(text[i - 1])) continue;
    let j = i + 4;
    while (j < to && /\s/.test(text[j])) j += 1;
    if (text[j] !== "=") continue;
    j += 1;
    while (j < to && /\s/.test(text[j])) j += 1;
    const quote = text[j];
    if (quote !== '"' && quote !== "'") continue;
    const close = text.indexOf(quote, j + 1);
    if (close < 0 || close >= to) continue;
    return text.slice(j + 1, close);
  }
  return "";
}

// --- rotation: which placements can be turned, and how ----------------------
//
// A placement turns in one of three ways, and the difference is a language
// rule rather than a preference. JSX resolves a lowercase tag to an intrinsic
// element and an uppercase tag to a value in scope. Every intrinsic tscircuit
// element that can be a placement accepts `pcbRotation` — it is on
// `CommonLayoutProps`, which is also where the `pcbX`/`pcbY` a placement
// requires come from (`@tscircuit/props` 0.0.2279,
// `toolchain/node_modules/@tscircuit/props/dist/index.d.ts:6986,6999`). Our own
// components are a different matter: all seven behind the 31 component
// placements on the three example boards declare exactly `pcbX`/`pcbY`
// (+ `schX`/`schY`) and forward only those, so a `pcbRotation` written on one
// reaches the function, is never read, and never reaches a `<group>`.
//
// Nothing downstream would catch that. There is no typecheck in this repo's
// build path — `tscircuit-cli build` transpiles — so the write would succeed,
// the change counter would tick, a 95-second rebuild would run, and the part
// would come back at the same angle. **That is the silent discard this module
// exists to refuse**, which is why the gate lives here, before the write.
//
// So: an intrinsic takes the prop. One of ours gets wrapped in a `<group>` that
// carries the prop — an idiom the board author already writes by hand
// (`examples/hydrate-coaster/boards/main.tsx:73`) for exactly this reason. And
// what neither can express is refused out loud.

/** How a placement can be turned. `"no"` always comes with a `rotateBlock`. */
const ROTATE_VIA = Object.freeze({ prop: "prop", wrap: "wrap", no: "no" });

/**
 * Why a placement will not turn, in the words the edit bar shows.
 *
 * A sentence rather than a code because the user reads it, and it names the
 * thing that would have to change — the board file, not this app.
 */
export function rotateReasonFor(block, label) {
  const name = String(label || "this placement");
  switch (block) {
    case "expression":
      return `${name}'s angle is written as an expression in the board file, so this app cannot turn it. Edit pcbRotation there.`;
    case "text":
      return `${name}'s angle is written as text, not a number. Edit pcbRotation in the board file.`;
    case "closing-tag":
      return `${name} is written with a closing tag; this app only wraps self-closing elements.`;
    case "shared-line":
      return `${name} shares its line with other code, so this app cannot wrap it.`;
    case "too-deep":
      return `${name} is nested too deeply for this app to wrap.`;
    case "drill":
      return `${name} is a round drill — turning it changes nothing on the board.`;
    default:
      return block ? `${name} cannot be turned from this app.` : "";
  }
}

/**
 * The opening line a wrap writes: the element's own indent, then a `<group>`
 * carrying the element's coordinates and the angle, then the child's indent.
 *
 * `x` and `y` are copied byte-for-byte from the source and never passed through
 * `formatMm` — rule 1 of this module is that only the number moves, and in a
 * wrap not even that: `pcbX={14}` stays `14` and `-18.0` stays `-18.0`.
 *
 * The group carries no `schX`/`schY`. A pcb-only `<group>` is already shipping
 * board source (`examples/harness-puck/blocks/glue.tsx:64`), the child keeps its
 * own schematic props byte-identical, and turning a part on the PCB must not
 * move anything on the schematic.
 */
function wrapOpenText(indent, childIndent, xText, yText, degrees) {
  return `${indent}<group pcbX={${xText}} pcbY={${yText}} pcbRotation={${degrees}}>\n${childIndent}`;
}

/** The indent of the line `at` sits on, without copying the rest of the file. */
function indentOfLine(text, at) {
  const start = lineStartOf(text, at);
  let end = start;
  while (end < text.length && (text[end] === " " || text[end] === "\t")) end += 1;
  return text.slice(start, end);
}

/**
 * The rotation half of a parsed placement: what the file says the angle is,
 * where an edit would go, and — the part that matters — whether an edit would
 * mean anything once it got there.
 */
function rotationFields(text, { tag, name, rot, x, y, lineStart, tagStart, indent, elementEnd }) {
  const rotation = rot?.form === "number" ? rot.value : 0;
  const rotationSpan = rot?.form === "number" ? { start: rot.start, end: rot.end } : null;

  // A deletion takes the whitespace that separated the prop with it, so undoing
  // an insertion returns the line to the bytes it had rather than leaving a
  // double space or an empty continuation line where the prop used to be.
  let gap = rot ? rot.propStart : 0;
  while (rot && gap > tagStart && /\s/.test(text[gap - 1])) gap -= 1;
  const rotationPropSpan = rot ? { start: gap, end: rot.propEnd } : null;

  // An inserted prop goes immediately after `pcbY`'s closing brace, so the two
  // coordinates and the angle read together. When the props are written one per
  // line it gets its own line at the same indent: folding a multi-line tag onto
  // one line would rewrite bytes this module promises not to touch.
  const rotationInsertAt = y.propEnd;
  let run = rotationInsertAt;
  while (run < text.length && (text[run] === " " || text[run] === "\t")) run += 1;
  const ownLine = text[run] === "\n" || text[run] === "\r";
  const rotationInsertPrefix = ownLine ? `\n${indentOfLine(text, y.propStart)}` : " ";

  let via = ROTATE_VIA.prop;
  let block = "";
  if (rot?.form === "expression") {
    via = ROTATE_VIA.no;
    block = "expression";
  } else if (rot?.form === "text") {
    via = ROTATE_VIA.no;
    block = "text";
  } else if (rot?.form === "number" || /^[a-z]/.test(String(tag))) {
    // A lowercase tag is an intrinsic and takes the prop. So does anything
    // already carrying a numeric angle: a component that threads rotation
    // proves it by having a number written on it, and that is evidence this
    // parser can read. It is also what makes the second turn of a wrapped
    // placement cheap — after a wrap the unit at that anchor is the `<group>`.
    via = ROTATE_VIA.prop;
  } else if (elementEnd === null) {
    via = ROTATE_VIA.no;
    block = "closing-tag";
  } else if (!/^[ \t]*$/.test(text.slice(lineStart, tagStart))) {
    // The wrap replaces `[lineStart, tagStart)` with the group's opening line.
    // If that range holds anything but indent, replacing it would delete code.
    via = ROTATE_VIA.no;
    block = "shared-line";
  } else {
    const childIndent = text.slice(tagStart, elementEnd).includes("\n") ? indent : `${indent}  `;
    // The angle is not known at parse time, so the cap is measured against the
    // longest string `formatDeg` can return. Refusing here rather than at the
    // transport is the difference between a reason and a failed request.
    const widest = wrapOpenText(indent, childIndent, text.slice(x.start, x.end), text.slice(y.start, y.end), "359.999");
    via = widest.length > MAX_EDIT_TEXT ? ROTATE_VIA.no : ROTATE_VIA.wrap;
    if (via === ROTATE_VIA.no) block = "too-deep";
  }

  return {
    rotation,
    rotationSpan,
    rotationPropSpan,
    rotationInsertAt,
    rotationInsertPrefix,
    rotateVia: via,
    rotateBlock: block,
    rotateReason: rotateReasonFor(block, name || tag),
  };
}

/**
 * Parse a board TSX into the placements the board file can move.
 *
 * @param {string} source
 * @returns {{
 *   ok: boolean, reason: string,
 *   placements: Array<{
 *     id: string, tag: string, name: string,
 *     x: number, y: number,
 *     xSpan: {start: number, end: number}, ySpan: {start: number, end: number},
 *     tagStart: number, lineStart: number, indent: string,
 *     locked: boolean, lockSpan: {start: number, end: number}|null,
 *     selfClosing: boolean, elementEnd: number|null,
 *     rotation: number,
 *     rotationSpan: {start: number, end: number}|null,
 *     rotationPropSpan: {start: number, end: number}|null,
 *     rotationInsertAt: number, rotationInsertPrefix: string,
 *     rotateVia: "prop"|"wrap"|"no", rotateBlock: string, rotateReason: string,
 *   }>,
 *   skipped: Array<{tag: string, reason: string}>,
 * }}
 */
export function parseBoardSource(source) {
  const text = String(source || "");
  const empty = { ok: false, reason: "", placements: [], skipped: [] };
  if (!text.trim()) return { ...empty, reason: "the board file is empty" };
  const mask = maskCommentsAndStrings(text);

  // Find `<board` — the one element whose direct children the board file owns.
  let boardLt = -1;
  for (let i = 0; i < text.length; i += 1) {
    if (mask[i] || text[i] !== "<") continue;
    const nameEnd = readName(text, i + 1);
    if (text.slice(i + 1, nameEnd) === "board") {
      boardLt = i;
      break;
    }
  }
  if (boardLt < 0) return { ...empty, reason: "no <board> element in this file" };
  const boardTag = readOpeningTag(text, mask, boardLt);
  if (!boardTag) return { ...empty, reason: "the <board> tag is not closed" };
  if (boardTag.selfClosing) return { ok: true, reason: "", placements: [], skipped: [] };

  const placements = [];
  const skipped = [];
  const counters = new Map();
  let depth = 0;
  let i = boardTag.gt + 1;

  while (i < text.length) {
    if (mask[i]) {
      i += 1;
      continue;
    }
    const c = text[i];
    // An expression container in child position — `{/* a comment */}`, or any
    // interpolation. Skipped whole so nothing inside it reads as structure.
    if (c === "{") {
      const close = matchBrace(text, mask, i);
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
      if (depth === 0) break; // </board>
      depth -= 1;
      i = gt + 1;
      continue;
    }
    const tag = readOpeningTag(text, mask, i);
    if (!tag) {
      i += 1;
      continue;
    }
    if (depth === 0) {
      const x = readPropDetail(text, mask, tag.nameEnd, tag.gt, "pcbX");
      const y = readPropDetail(text, mask, tag.nameEnd, tag.gt, "pcbY");
      if ((x && x.form !== "number") || (y && y.form !== "number")) {
        skipped.push({ tag: tag.tag, reason: "its position is written as text, not a number" });
      } else if (x && y) {
        const ordinal = (counters.get(tag.tag) || 0) + 1;
        counters.set(tag.tag, ordinal);
        const lineStart = lineStartOf(text, i);
        const indent = /^[ \t]*/.exec(text.slice(lineStart, i))?.[0] ?? "";
        const lock = readLock(text, lineStart);
        const rot = readPropDetail(text, mask, tag.nameEnd, tag.gt, "pcbRotation");
        const elementEnd = tag.selfClosing ? tag.gt + 1 : null;
        placements.push({
          id: `${tag.tag}[${ordinal}]`,
          tag: tag.tag,
          name: readNameProp(text, mask, tag.nameEnd, tag.gt),
          x: x.value,
          y: y.value,
          xSpan: { start: x.start, end: x.end },
          ySpan: { start: y.start, end: y.end },
          tagStart: i,
          lineStart,
          indent,
          locked: Boolean(lock),
          lockSpan: lock,
          selfClosing: tag.selfClosing,
          elementEnd,
          ...rotationFields(text, {
            tag: tag.tag,
            name: readNameProp(text, mask, tag.nameEnd, tag.gt),
            rot,
            x,
            y,
            lineStart,
            tagStart: i,
            indent,
            elementEnd,
          }),
        });
      }
    }
    if (!tag.selfClosing) depth += 1;
    i = tag.gt + 1;
  }

  return { ok: true, reason: "", placements, skipped };
}

/** The lock line immediately above `lineStart`, or null. */
function readLock(text, lineStart) {
  if (lineStart <= 0) return null;
  const prevStart = lineStartOf(text, lineStart - 1);
  const line = text.slice(prevStart, lineStart);
  if (!LOCK_LINE_RE.test(line)) return null;
  return { start: prevStart, end: lineStart };
}

/**
 * A millimetre value as a board file would write it: three decimals is a
 * micron, which is two orders below anything a fab can hold, and trailing
 * zeros are noise in a diff.
 */
export function formatMm(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0";
  const rounded = Math.round(n * 1000) / 1000;
  if (Object.is(rounded, -0)) return "0";
  return String(rounded);
}

/**
 * Snap a *delta* rather than an absolute coordinate. Absolute snapping is what
 * Altium does and it is wrong for our boards: a 2.54mm header nudged on a 0.5mm
 * grid lands at 2.5 and quietly loses its pitch. Snapping the movement keeps
 * every relationship the board already had.
 */
export function snapDelta(delta, step) {
  const s = Number(step);
  if (!Number.isFinite(s) || s <= 0) return Number(delta) || 0;
  return Math.round((Number(delta) || 0) / s) * s;
}

/**
 * Splice edits into text. Edits are `{start, end, text}` over the ORIGINAL
 * offsets; they are applied right-to-left so earlier offsets stay valid.
 * Overlapping edits throw rather than producing a plausible-looking wrong file.
 */
export function applyEdits(source, edits) {
  const list = [...(edits || [])].sort((a, b) => a.start - b.start || a.end - b.end);
  for (let i = 1; i < list.length; i += 1) {
    if (list[i].start < list[i - 1].end) throw new Error("overlapping edits");
  }
  let out = String(source || "");
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const edit = list[i];
    out = out.slice(0, edit.start) + String(edit.text) + out.slice(edit.end);
  }
  return out;
}

/** Attach `expected` (the text being replaced) so the server can refuse a
 *  write against a file that moved under us. */
export function withExpected(source, edits) {
  const text = String(source || "");
  return (edits || []).map((edit) => ({
    ...edit,
    expected: text.slice(edit.start, edit.end),
  }));
}

/** The edits that move one placement to (x, y). Empty when nothing changes. */
export function moveEdits(source, placement, x, y) {
  if (!placement) return [];
  const edits = [];
  const nextX = formatMm(x);
  const nextY = formatMm(y);
  if (nextX !== String(source).slice(placement.xSpan.start, placement.xSpan.end)) {
    edits.push({ start: placement.xSpan.start, end: placement.xSpan.end, text: nextX });
  }
  if (nextY !== String(source).slice(placement.ySpan.start, placement.ySpan.end)) {
    edits.push({ start: placement.ySpan.start, end: placement.ySpan.end, text: nextY });
  }
  return withExpected(source, edits);
}

/**
 * The edits that lock or unlock a placement.
 *
 * A lock has to survive into the board file or it is not a lock: the next agent
 * reads `boards/main.tsx`, not this app's memory. So it is a comment on the
 * line above the element — visible in the diff, visible to the model, and
 * inert to the compiler.
 */
export function lockEdits(source, placement, locked) {
  if (!placement) return [];
  const want = Boolean(locked);
  if (want === Boolean(placement.locked)) return [];
  if (want) {
    return withExpected(source, [
      {
        start: placement.lineStart,
        end: placement.lineStart,
        text: `${placement.indent}${LOCK_COMMENT}\n`,
      },
    ]);
  }
  if (!placement.lockSpan) return [];
  return withExpected(source, [
    { start: placement.lockSpan.start, end: placement.lockSpan.end, text: "" },
  ]);
}

/** An angle wrapped into [0, 360). `-0` comes back as `0`. */
export function normalizeDeg(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  const wrapped = ((n % 360) + 360) % 360;
  return wrapped === 0 ? 0 : wrapped;
}

/**
 * An angle as a board file would write it. Three decimals for the same reason
 * `formatMm` uses three: it is two orders finer than anything downstream can
 * hold, and trailing zeros are noise in a diff. Rounding that lands on 360
 * comes back as 0, because a file should not carry a full turn.
 */
export function formatDeg(value) {
  const rounded = Math.round(normalizeDeg(value) * 1000) / 1000;
  const wrapped = rounded >= 360 ? 0 : rounded;
  if (Object.is(wrapped, -0)) return "0";
  return String(wrapped);
}

/**
 * The edits that wrap a placement in a `<group>` carrying the angle.
 *
 * Four edits over the ORIGINAL offsets: the element's indent becomes the
 * group's opening line, the element's own coordinates become `0` because the
 * group took them, and a closing tag is appended. Children rotate about the
 * group's anchor — measured on `examples/hydrate-coaster`, where the
 * hand-written wrapper at `boards/main.tsx:73` anchors `pcb_group_2` at exactly
 * its un-rotated `(-20, -22)` and puts the block's `U4`, written
 * `pcbX={13} pcbY={0}`, at `(-33, -22)` = `(-20, -22) + 13·(cos 180°, sin 180°)`.
 */
function wrapEdits(text, placement, degrees) {
  const { lineStart, tagStart, indent, xSpan, ySpan, elementEnd } = placement;
  const multiline = text.slice(tagStart, elementEnd).includes("\n");
  // A multi-line element keeps its own indent and the group brackets it at the
  // same level. Re-indenting its interior lines — nine of them on
  // `examples/harness-puck/boards/main.tsx:194-203` — would touch bytes this
  // module promises not to touch, and ragged by one level is still valid TSX.
  const childIndent = multiline ? indent : `${indent}  `;
  return withExpected(text, [
    {
      start: lineStart,
      end: tagStart,
      text: wrapOpenText(indent, childIndent, text.slice(xSpan.start, xSpan.end), text.slice(ySpan.start, ySpan.end), degrees),
    },
    { start: xSpan.start, end: xSpan.end, text: "0" },
    { start: ySpan.start, end: ySpan.end, text: "0" },
    { start: elementEnd, end: elementEnd, text: `\n${indent}</group>` },
  ]);
}

/**
 * The edits that turn one placement to `degrees`. Empty when nothing changes,
 * and empty when the source cannot say it — `placement.rotateReason` is the
 * sentence to show in that case, and a caller that writes nothing and says
 * nothing has discarded the user's action, which is the one outcome worse than
 * a missing feature.
 *
 * **`pcbRotation={0}` is never written.** `null` and an angle that normalizes
 * to zero both **remove** the prop, and the absence of a prop is what zero
 * means to the compiler — the two forms are the same board. One rule, because
 * the alternative is a file that accumulates dead props: four taps of Space is
 * a full turn, and writing the literal `0` on the fourth left
 * `pcbRotation={0}` behind, counted a change, and offered a ~96-second rebuild
 * for a board that had not moved. Measured on hydrate-coaster's `TP1`.
 *
 * The cost, stated: a `pcbRotation={0}` somebody wrote by hand does not come
 * back byte-for-byte after a turn and an undo — it comes back absent. Nothing
 * downstream can tell the difference, and the dead-prop case is the one that
 * happens.
 */
export function rotateEdits(source, placement, degrees) {
  if (!placement) return [];
  const text = String(source || "");
  const toZero = degrees === null || degrees === undefined || normalizeDeg(degrees) === 0;
  if (toZero) {
    if (!placement.rotationSpan || !placement.rotationPropSpan) return [];
    if (placement.rotateVia === ROTATE_VIA.no) return [];
    const { start, end } = placement.rotationPropSpan;
    return withExpected(text, [{ start, end, text: "" }]);
  }
  if (placement.rotateVia === ROTATE_VIA.no) return [];
  const next = formatDeg(degrees);
  if (placement.rotationSpan) {
    const { start, end } = placement.rotationSpan;
    if (next === text.slice(start, end)) return [];
    return withExpected(text, [{ start, end, text: next }]);
  }
  // A zero turn never reaches here — `toZero` above took it — so a wrap is
  // only ever written for an angle that actually turns something.
  if (placement.rotateVia === ROTATE_VIA.wrap) return wrapEdits(text, placement, next);
  if (!Number.isFinite(Number(placement.rotationInsertAt))) return [];
  const at = Number(placement.rotationInsertAt);
  return withExpected(text, [
    { start: at, end: at, text: `${placement.rotationInsertPrefix ?? " "}pcbRotation={${next}}` },
  ]);
}

/**
 * The lines a wrap is about to write, for a confirmation to show.
 *
 * The diff itself, not a description of it: a four-line structural edit to a
 * file that carries the board's engineering record in its comments has a wider
 * blast radius than replacing `180` with `270`, and Altium's own pattern is
 * that the wider the blast radius the more it interposes a confirmation
 * (`Confirm Global Edit`,
 * https://www.altium.com/documentation/altium-designer/pcb-editor-general-preferences).
 * Altium's wording for it is not something to copy — a diff is not a wording
 * question.
 *
 * Empty string for anything that is not a wrap.
 */
export function wrapPreview(source, placement, degrees) {
  if (placement?.rotateVia !== ROTATE_VIA.wrap) return "";
  const edits = rotateEdits(source, placement, degrees);
  if (!edits.length) return "";
  const text = String(source || "");
  const end = edits.reduce(
    (at, edit) => (edit.start >= placement.elementEnd ? at + edit.text.length : at + edit.text.length - (edit.end - edit.start)),
    placement.elementEnd,
  );
  return applyEdits(text, edits).slice(placement.lineStart, end);
}

/**
 * The edits that put `applyEdits(source, edits)` back to `source`.
 *
 * Offsets shift by the cumulative length change of every earlier edit, which is
 * exactly what `applyEdits` did to produce them. Every input edit must carry
 * `expected` (i.e. must have come through `withExpected`).
 *
 * This is how a structural edit gets an undo. A move inverts by knowing the old
 * coordinates, but a wrap changes which tag a placement id names — after it,
 * `Ldo3v3[1]` is not in the file at all and there is nothing to recompute from.
 * Recording the inverse bytes sidesteps that, and it is general: every
 * structural edit that lands after this one gets undo by recording it.
 */
export function invertEdits(edits) {
  const list = [...(edits || [])].sort((a, b) => a.start - b.start || a.end - b.end);
  let shift = 0;
  return list.map((edit) => {
    const text = String(edit.text);
    const start = edit.start + shift;
    shift += text.length - (edit.end - edit.start);
    return { start, end: start + text.length, text: edit.expected, expected: text };
  });
}

// --- binding a placement to the geometry it owns ----------------------------

const KEY = (x, y) => `${Math.round(Number(x) * 10000)},${Math.round(Number(y) * 10000)}`;

/**
 * PCB element kinds a board line can place directly, with no component behind
 * them. A `<MountingHole>` compiles to a `pcb_hole` with `pcb_component_id:
 * null`; a `<silkscreentext>` to a `pcb_silkscreen_text`. Copper is
 * deliberately absent — a trace or a via is something the router made, never
 * something a line of the board file put at that point.
 */
const LOOSE_TYPES = new Set([
  "pcb_hole",
  "pcb_cutout",
  "pcb_silkscreen_text",
  "pcb_silkscreen_path",
  "pcb_silkscreen_rect",
  "pcb_silkscreen_circle",
]);

/** Where a loose element says it is, or null when it has no single anchor. */
function looseAnchor(element) {
  const point =
    element?.type === "pcb_silkscreen_text"
      ? element.anchor_position
      : element?.center || (Number.isFinite(Number(element?.x)) ? { x: element.x, y: element.y } : null);
  if (!point || !Number.isFinite(Number(point.x)) || !Number.isFinite(Number(point.y))) return null;
  return { x: Number(point.x), y: Number(point.y) };
}

/** Every component in a group, including the ones its child groups hold. */
export function groupComponentKeys(index, groupId) {
  const out = [];
  const seen = new Set();
  const stack = [String(groupId || "")];
  while (stack.length) {
    const id = stack.pop();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const group = index?.groupById?.get(id);
    if (!group) continue;
    for (const key of group.componentKeys) out.push(key);
    for (const child of group.childIds) stack.push(child);
  }
  return out;
}

/**
 * Bind parsed placements to the compiled board.
 *
 * The bridge is arithmetic, not naming: a block instance written
 * `pcbX={14} pcbY={-18}` compiles to a `pcb_group` whose `anchor_position` is
 * exactly (14, -18), and a part the board wrote itself compiles to a
 * `pcb_component` whose `center` is exactly its `pcbX`/`pcbY`. Nothing else in
 * the compiled file remembers which line of source it came from — group names
 * are `unnamed_group7` — so the anchor is the only honest link.
 *
 * A placement that does not land on exactly one anchor is returned as
 * unmatched — two elements that genuinely sit on the same point, or a line
 * whose coordinates no longer describe anything on the built board.
 *
 * @returns {{
 *   byId: Map<string, object>,
 *   byComponentKey: Map<string, string>,
 *   byElementId: Map<string, string>,
 *   unmatched: Array<{placement: object, reason: string}>,
 * }}
 */
export function bindPlacements(placements, index) {
  const byId = new Map();
  const byComponentKey = new Map();
  const byElementId = new Map();
  const unmatched = [];
  const list = Array.isArray(placements) ? placements : [];
  if (!index) {
    for (const placement of list) unmatched.push({ placement, reason: "the board has not been built yet" });
    return { byId, byComponentKey, byElementId, unmatched };
  }

  const rootId = (index.groups || []).find((group) => group.isRoot)?.id || "";
  const candidates = new Map(); // anchor key → candidate[]
  const add = (map, x, y, value) => {
    const key = KEY(x, y);
    const bucket = map.get(key);
    if (bucket) bucket.push(value);
    else map.set(key, [value]);
  };
  for (const group of index.groups || []) {
    if (!group.anchor) continue;
    if (rootId && group.parentId !== rootId) continue;
    if (!rootId && group.isRoot) continue;
    add(candidates, group.anchor.x, group.anchor.y, { kind: "group", group });
  }
  for (const component of index.components || []) {
    if (!component.pcb) continue;
    if (rootId && component.groupId !== rootId) continue;
    add(candidates, component.pcb.center?.x, component.pcb.center?.y, { kind: "component", component });
  }

  // Geometry a board line placed with no component behind it: a mounting
  // hole's drill, a silkscreen label. These never decide *which* placement a
  // point belongs to — they are only how a placement finds its drawing.
  const loose = new Map();
  for (const element of index.pcbDrawables || []) {
    if (!LOOSE_TYPES.has(element.type)) continue;
    const id = elementIdOf(element);
    if (!id || index.componentKeyByElementId?.get(id)) continue;
    const anchor = looseAnchor(element);
    if (!anchor) continue;
    add(loose, anchor.x, anchor.y, element);
  }

  // Two placements on the same point cannot be told apart, so neither is bound.
  const placementsAt = new Map();
  for (const placement of list) {
    const key = KEY(placement.x, placement.y);
    placementsAt.set(key, (placementsAt.get(key) || 0) + 1);
  }

  for (const placement of list) {
    const key = KEY(placement.x, placement.y);
    if (placementsAt.get(key) > 1) {
      unmatched.push({ placement, reason: "two things in the board file sit on this exact spot" });
      continue;
    }
    const bucket = candidates.get(key) || [];
    if (bucket.length > 1) {
      unmatched.push({ placement, reason: "this spot matches more than one thing on the board" });
      continue;
    }
    const hit = bucket[0] || null;
    const componentKeys = !hit
      ? []
      : hit.kind === "group"
        ? groupComponentKeys(index, hit.group.id)
        : [hit.component.key];
    const pcbIds = new Set();
    const refdes = [];
    let box = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
    const grow = (b) => {
      if (!b || !Number.isFinite(b.minX)) return;
      box = {
        minX: Math.min(box.minX, b.minX),
        minY: Math.min(box.minY, b.minY),
        maxX: Math.max(box.maxX, b.maxX),
        maxY: Math.max(box.maxY, b.maxY),
      };
    };
    for (const componentKey of componentKeys) {
      const component = index.componentBySourceId?.get(componentKey);
      if (!component) continue;
      if (component.refdes) refdes.push(component.refdes);
      if (component.pcbId) pcbIds.add(component.pcbId);
      for (const id of component.pcbElementIds) pcbIds.add(id);
      grow(component.pcbBox);
    }
    // A `<MountingHole>` is a real group that owns no parts, and a
    // `<silkscreentext>` is not a group at all. Both still draw something at
    // exactly the point the board file names, and both are worth dragging.
    const looseTypes = new Set();
    if (!pcbIds.size) {
      for (const element of loose.get(key) || []) {
        const id = elementIdOf(element);
        if (!id) continue;
        pcbIds.add(id);
        looseTypes.add(String(element.type || ""));
        grow(pcbBoxOf(element));
      }
    }
    if (!pcbIds.size) {
      unmatched.push({
        placement,
        reason: hit ? "nothing is drawn for this part yet" : "nothing on the built board sits where this line says",
      });
      continue;
    }
    refdes.sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }));
    const lead = leadComponent(componentKeys.map((key) => index.componentBySourceId?.get(key)).filter(Boolean));
    byId.set(placement.id, {
      ...placement,
      ...attachLabel(placement, {
        kind: hit ? hit.kind : "loose",
        componentKeys,
        refdes,
        lead: lead?.refdes || "",
        pcbIds,
        box,
        anchor: { x: placement.x, y: placement.y },
        // A drill is round: turning it about its own centre changes nothing
        // anybody can measure or inspect. That is 13 of the 57 placements on
        // our three boards (`MountingHole` H1-H6, H1-H4, H1-H3), and refusing a
        // no-op is cheaper than offering one.
        roundDrill:
          componentKeys.length === 0 &&
          looseTypes.size > 0 &&
          [...looseTypes].every((type) => type === "pcb_hole" || type === "pcb_cutout"),
      }),
    });
    for (const componentKey of componentKeys) byComponentKey.set(componentKey, placement.id);
    for (const id of pcbIds) byElementId.set(id, placement.id);
  }

  return { byId, byComponentKey, byElementId, unmatched };
}

/** The half of a binding that belongs to the BUILT board rather than the file:
 *  what is drawn for this placement, where, and what it should be called.
 *  `anchor` is the source position the build was made from — the difference
 *  between it and the file's current position is how far this placement has
 *  moved since anything was drawn. */
export const GEOMETRY_FIELDS = Object.freeze([
  "kind",
  "componentKeys",
  "refdes",
  "lead",
  "pcbIds",
  "box",
  "anchor",
  "roundDrill",
]);

function attachLabel(placement, geometry) {
  const anchor = geometry.anchor || { x: placement.x, y: placement.y };
  const label = placementLabel(placement, geometry);
  // The parser decides how a turn would be WRITTEN; the built board decides
  // whether it would MEAN anything. Only the second half knows a mounting hole
  // came out as a drill, so the last word on `rotateVia` is here, and the
  // reason is re-rendered now that there is a label to name.
  const block = geometry.roundDrill && placement.rotateVia !== "no" ? "drill" : placement.rotateBlock || "";
  return {
    ...geometry,
    anchor,
    // How far ahead of the drawing this placement is. Zero until a drag.
    offset: { dx: placement.x - anchor.x, dy: placement.y - anchor.y },
    label,
    rotateVia: block ? "no" : placement.rotateVia,
    rotateBlock: block,
    rotateReason: rotateReasonFor(block, label),
  };
}

/**
 * Re-attach geometry from an earlier binding to a freshly parsed file.
 *
 * This is the difference between an editor and a viewer. `bindPlacements`
 * matches source coordinates to compiled anchors, so the moment a drag writes
 * a new `pcbX` the two stop agreeing — and they *should*, because the board on
 * screen is still the last build. Rebinding by coordinate after every edit
 * would unbind the part you just moved, which makes the second drag and the
 * undo impossible. So geometry is captured once per build and carried forward
 * by placement id, which survives a coordinate change because it is the tag
 * plus its position in the file.
 *
 * @param {Array<object>} placements fresh parse
 * @param {{byId: Map<string, object>, reasonById: Map<string, string>}} snapshot
 */
export function rebindPlacements(placements, snapshot) {
  const byId = new Map();
  const byComponentKey = new Map();
  const byElementId = new Map();
  const unmatched = [];
  for (const placement of Array.isArray(placements) ? placements : []) {
    const geometry = snapshot?.byId?.get(placement.id);
    if (!geometry) {
      unmatched.push({
        placement,
        reason: snapshot?.reasonById?.get(placement.id) || "nothing on the built board sits where this line says",
      });
      continue;
    }
    byId.set(placement.id, { ...placement, ...attachLabel(placement, geometry) });
    for (const key of geometry.componentKeys) byComponentKey.set(key, placement.id);
    for (const id of geometry.pcbIds) byElementId.set(id, placement.id);
  }
  return { byId, byComponentKey, byElementId, unmatched };
}

/**
 * Carry a geometry snapshot across an edit that renamed placements.
 *
 * A placement id is its tag plus its ordinal in the file, which survives a
 * coordinate change — that is the whole point of `rebindPlacements`. It does
 * **not** survive a structural change: wrapping `<Ldo3v3 …/>` in a `<group>`
 * removes an `Ldo3v3[1]` and inserts a `group[1]`, which renumbers every later
 * `group[…]` in the file. On hydrate-coaster that is not hypothetical — the
 * board already has a hand-written `<group>` below the LDO, so wrapping the LDO
 * makes the new wrapper `group[1]` and the RP2040's wrapper `group[2]`, and a
 * snapshot keyed by the old ids would hand the LDO the RP2040's geometry. The
 * part would then highlight, box and cross-probe as 22 other components.
 *
 * The fix is positional, and it is sound for exactly the reason it is needed: a
 * wrap keeps the placement list the same length and in the same file order, and
 * changes one entry's name. When the lengths disagree the edit did something
 * this cannot reason about, so the snapshot is dropped and `rebindPlacements`
 * reports the honest "nothing on the built board sits where this line says"
 * rather than a confident wrong answer.
 */
export function remapSnapshot(snapshot, before, after) {
  const byId = new Map(snapshot?.byId || []);
  const reasonById = new Map(snapshot?.reasonById || []);
  const from = (Array.isArray(before) ? before : []).map((placement) => placement.id);
  const to = (Array.isArray(after) ? after : []).map((placement) => placement.id);
  if (from.length !== to.length) return { byId: new Map(), reasonById: new Map() };
  const nextById = new Map();
  const nextReason = new Map();
  for (let i = 0; i < from.length; i += 1) {
    const geometry = byId.get(from[i]);
    if (geometry) nextById.set(to[i], geometry);
    const reason = reasonById.get(from[i]);
    if (reason) nextReason.set(to[i], reason);
  }
  return { byId: nextById, reasonById: nextReason };
}

/**
 * Every move the file is holding that the built board has not seen yet.
 *
 * This is what turns a check on a stale artifact into a check on what the user
 * is actually looking at. `circuit.json` was compiled from the positions the
 * board had at build time; each bound placement carries that position as
 * `anchor` and its current one as `x`/`y`, so the difference is exactly the
 * translation the gate has to apply before it grades anything
 * (`fastcheck.apply_moves`, and `board_fast_check`'s `moves` argument).
 *
 * All of them, not the last one: a user who drags three parts and then asks
 * "is this legal" is asking about the board with all three moved. One move per
 * request would grade two of them back where they started.
 *
 * A turn is not here and cannot be — the gate translates elements, it does not
 * rotate them — which is why the caller counts pending rotations separately and
 * says so rather than letting a verdict imply it covered them.
 */
export function pendingMoves(binding) {
  const moves = [];
  for (const bound of binding?.byId?.values() ?? []) {
    const dx = Number(bound?.offset?.dx) || 0;
    const dy = Number(bound?.offset?.dy) || 0;
    if (!dx && !dy) continue;
    const anchor = bound.anchor || {};
    if (!Number.isFinite(Number(anchor.x)) || !Number.isFinite(Number(anchor.y))) continue;
    moves.push({ anchor: { x: Number(anchor.x), y: Number(anchor.y) }, dx, dy });
  }
  return moves;
}

/** Freeze a binding's geometry so a later parse of the same file can reuse it. */
export function geometrySnapshot(binding) {
  const byId = new Map();
  for (const [id, bound] of binding?.byId || []) {
    const geometry = {};
    for (const field of GEOMETRY_FIELDS) geometry[field] = bound[field];
    byId.set(id, geometry);
  }
  const reasonById = new Map();
  for (const entry of binding?.unmatched || []) reasonById.set(entry.placement.id, entry.reason);
  return { byId, reasonById };
}

/**
 * What to call a placement on screen: the name the board file gave it, else
 * the part that names the block. "U3 +21" reads as the RP2040 and its
 * supporting cast; "C4 +21" — the alphabetically first refdes — reads as a
 * capacitor that somehow brought a microcontroller with it.
 */
export function placementLabel(placement, geometry) {
  const list = Array.isArray(geometry?.refdes) ? geometry.refdes.filter(Boolean) : [];
  if (placement?.name) return placement.name;
  const head = String(geometry?.lead || "") || list[0] || "";
  if (!head) return placement?.tag || "part";
  return list.length > 1 ? `${head} +${list.length - 1}` : head;
}

/** One-line summary of a move, for the edit bar and the undo stack. */
export function describeMove(label, from, to) {
  const dx = formatMm(Number(to.x) - Number(from.x));
  const dy = formatMm(Number(to.y) - Number(from.y));
  return `${label} moved ${dx}, ${dy} mm`;
}

/**
 * One-line summary of a turn, in the same register as `describeMove`.
 *
 * Reported as the shorter way round, because that is the way the user asked
 * for it: `pcbRotation` is counterclockwise-positive, so a step from 90 to 0
 * is 90° CW rather than 270° CCW. The sign was measured, not assumed — the
 * first ring LED on `examples/harness-puck` compiles at `rotation: 337.5` with
 * pads at ±32.88°/±147.12° from its centre after subtracting 337.5; read as
 * clockwise the same pads come out at −12.12°/102.12°/−77.88°/167.88°, which is
 * not a rectangle.
 */
export function describeRotate(label, from, to) {
  const ccw = normalizeDeg(Number(to) - Number(from));
  const shorter = ccw <= 180 ? ccw : 360 - ccw;
  return `${label} turned ${formatDeg(shorter)}° ${ccw <= 180 ? "CCW" : "CW"} (now ${formatDeg(to)}°)`;
}
