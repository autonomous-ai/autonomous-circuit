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
 * The value of prop `prop` on the tag whose attributes span [from, to).
 * Only a bare numeric literal inside `{}` is editable — `pcbX="3mm"` is a
 * string the compiler parses, not a number we may rewrite, and saying so is
 * better than writing `3.5` where `"3mm"` was.
 *
 * @returns {{start: number, end: number, value: number}|null|"non-numeric"}
 */
export function readNumericProp(text, mask, from, to, prop) {
  for (let i = from; i < to; i += 1) {
    if (mask[i]) continue;
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
    if (text[j] !== "{") return "non-numeric";
    const close = matchBrace(text, mask, j);
    if (close < 0) return "non-numeric";
    let start = j + 1;
    let end = close;
    while (start < end && /\s/.test(text[start])) start += 1;
    while (end > start && /\s/.test(text[end - 1])) end -= 1;
    const literal = text.slice(start, end);
    if (!NUMBER_LITERAL.test(literal)) return "non-numeric";
    const value = Number(literal);
    if (!Number.isFinite(value)) return "non-numeric";
    return { start, end, value };
  }
  return null;
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
      const x = readNumericProp(text, mask, tag.nameEnd, tag.gt, "pcbX");
      const y = readNumericProp(text, mask, tag.nameEnd, tag.gt, "pcbY");
      if (x === "non-numeric" || y === "non-numeric") {
        skipped.push({ tag: tag.tag, reason: "its position is written as text, not a number" });
      } else if (x && y) {
        const ordinal = (counters.get(tag.tag) || 0) + 1;
        counters.set(tag.tag, ordinal);
        const lineStart = lineStartOf(text, i);
        const indent = /^[ \t]*/.exec(text.slice(lineStart, i))?.[0] ?? "";
        const lock = readLock(text, lineStart);
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
    if (!pcbIds.size) {
      for (const element of loose.get(key) || []) {
        const id = elementIdOf(element);
        if (!id) continue;
        pcbIds.add(id);
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
]);

function attachLabel(placement, geometry) {
  const anchor = geometry.anchor || { x: placement.x, y: placement.y };
  return {
    ...geometry,
    anchor,
    // How far ahead of the drawing this placement is. Zero until a drag.
    offset: { dx: placement.x - anchor.x, dy: placement.y - anchor.y },
    label: placementLabel(placement, geometry),
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
