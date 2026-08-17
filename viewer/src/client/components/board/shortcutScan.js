// shortcutScan — read the keyboard bindings back out of the handlers that
// actually run them.
//
// The shortcut sheet exists because Altium carries ~100 bindings without a
// learning curve, and it does that by having somewhere to look them up
// (ALTIUM-NOTES §7). A sheet is only worth having if it is true, and a
// hand-maintained list of keys is true exactly until the next person moves a
// binding. So the list is not maintained: it is read out of
// `BoardWorkspace.jsx`, `PcbCanvas.jsx` and friends, and a test fails when the
// two disagree.
//
// This file is a small static reader, not a JS parser. It knows the four
// shapes our handlers are written in and nothing else:
//
//   if (event.key === "Escape") { … }          a guarded comparison
//   if (key.toLowerCase() === "c") setX(null); a guarded comparison, no braces
//   switch (key) { case "1": … break; }        a switch on the key
//   if (event.shiftKey) { … }                  an enclosing modifier guard
//
// It is deliberately loud rather than clever. `scanKeyBindings` throws when the
// masking pass cannot account for every brace, because a reader that silently
// returns nothing produces an empty sheet, and an empty sheet is the exact
// failure this whole module was built to prevent. See `assertBalanced`.
//
// The durable version of this is a declared dispatch table inside each handler
// (see docs/architecture/ide-shortcut-sheet.md § "What would make this
// unnecessary"). Until a handler owner does that, reading their source is the
// only derivation available that does not require editing their file.

/** Modifier property names, mapped to the token the sheet prints. */
const MODIFIER_PROPS = Object.freeze({
  shiftKey: "Shift",
  ctrlKey: "Mod",
  metaKey: "Mod",
  altKey: "Alt",
});

/** Print order, so `Shift+Mod+X` and `Mod+Shift+X` can never be two bindings. */
const MODIFIER_ORDER = Object.freeze(["Mod", "Ctrl", "Cmd", "Alt", "Shift"]);

/** Keys whose `event.key` value is not what a person calls the key. */
const KEY_DISPLAY = Object.freeze({
  " ": "Space",
  Escape: "Esc",
  ArrowUp: "↑",
  ArrowDown: "↓",
  ArrowLeft: "←",
  ArrowRight: "→",
  PageUp: "PgUp",
  PageDown: "PgDn",
  Delete: "Del",
});

// --- masking -----------------------------------------------------------------

/**
 * True when a `/` at this point can only begin a regex literal, not a divide.
 *
 * `<` is deliberately NOT in the set: the character before the slash of a JSX
 * closing tag is `<`, and reading `</span>` as a regex swallows the rest of the
 * line — which is how the first version of this file lost a brace in
 * PropertiesPanel and reported the file as unreadable.
 */
function regexCanStartAfter(prev) {
  return prev === "" || "(,=:[!&|?;".includes(prev);
}

/**
 * Blank out everything the structural pass must not see.
 *
 * Returns two same-length views of the file so offsets are interchangeable:
 *   `code` — comments blanked, string and regex bodies kept (literals readable)
 *   `mask` — comments AND string/regex bodies blanked (braces countable)
 *
 * Newlines survive both, so an offset still maps to the right line number.
 */
export function maskSource(text) {
  const code = Array.from(text);
  const mask = Array.from(text);
  const blank = (i, into) => {
    if (text[i] !== "\n") into[i] = " ";
  };
  // A template literal's `${…}` holds real code with real braces, so the two
  // halves cannot be masked the same way: the literal text is blanked, the
  // interpolation is not. The stack is what keeps `${a ? {x:1} : `${b}`}` from
  // ending the outer template at the first inner backtick.
  const stack = [{ mode: "code", depth: 0 }];
  let i = 0;
  let prev = "";
  while (i < text.length) {
    const top = stack[stack.length - 1];
    const c = text[i];
    const next = text[i + 1];

    if (top.mode === "template") {
      if (c === "\\") {
        blank(i, mask);
        blank(i + 1, mask);
        i += 2;
        continue;
      }
      if (c === "`") {
        stack.pop();
        prev = "`";
        i += 1;
        continue;
      }
      if (c === "$" && next === "{") {
        blank(i, mask); // the `$` is not structure; the `{` that follows is
        stack.push({ mode: "code", depth: 0, interpolation: true });
        prev = "{";
        i += 2;
        continue;
      }
      blank(i, mask);
      i += 1;
      continue;
    }

    if (c === "/" && next === "/") {
      while (i < text.length && text[i] !== "\n") {
        blank(i, code);
        blank(i, mask);
        i += 1;
      }
      continue;
    }
    if (c === "/" && next === "*") {
      while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) {
        blank(i, code);
        blank(i, mask);
        i += 1;
      }
      blank(i, code);
      blank(i, mask);
      blank(i + 1, code);
      blank(i + 1, mask);
      i += 2;
      prev = "/";
      continue;
    }
    if (c === "`") {
      stack.push({ mode: "template" });
      prev = "`";
      i += 1;
      continue;
    }
    // An apostrophe inside JSX prose ("this net's own pads") is not a string
    // quote, and treating it as one swallows every brace after it. The scanner
    // then reports the file as unbalanced — loudly, which is the right failure
    // and is how this was caught, but it made ordinary English a landmine in
    // any file the sheet scans. A quote wedged between two word characters is
    // an apostrophe in every JS grammar there is: `don't` cannot be a string
    // start, because a string cannot begin immediately after an identifier.
    if (c === "'" && /[A-Za-z]/.test(prev) && /[A-Za-z]/.test(next || "")) {
      blank(i, mask);
      prev = "'";
      i += 1;
      continue;
    }
    if (c === '"' || c === "'") {
      i += 1;
      while (i < text.length && text[i] !== c) {
        if (text[i] === "\\") {
          blank(i, mask);
          blank(i + 1, mask);
          i += 2;
          continue;
        }
        blank(i, mask);
        i += 1;
      }
      i += 1;
      prev = c;
      continue;
    }
    // `/>` closes a JSX tag and `a / b` divides; neither may be eaten as a
    // regex. Requiring the terminator on the same line keeps a stray slash
    // from swallowing the rest of the file.
    if (c === "/" && next !== ">" && regexCanStartAfter(prev)) {
      const lineEnd = text.indexOf("\n", i + 1);
      const stop = lineEnd === -1 ? text.length : lineEnd;
      let j = i + 1;
      let closed = -1;
      while (j < stop) {
        if (text[j] === "\\") {
          j += 2;
          continue;
        }
        if (text[j] === "/") {
          closed = j;
          break;
        }
        j += 1;
      }
      if (closed !== -1) {
        for (let k = i + 1; k < closed; k += 1) blank(k, mask);
        i = closed + 1;
        prev = "/";
        continue;
      }
    }
    if (c === "{") top.depth += 1;
    else if (c === "}") {
      if (top.interpolation && top.depth === 0) {
        stack.pop(); // back into the template this `${` opened
        prev = "}";
        i += 1;
        continue;
      }
      top.depth -= 1;
    }
    if (!/\s/u.test(c)) prev = c;
    i += 1;
  }
  return { code: code.join(""), mask: mask.join("") };
}

/**
 * Refuse to report bindings from a file we did not read correctly.
 *
 * A masking bug shows up as unbalanced braces long before it shows up as a
 * wrong answer, and a wrong answer here is a sheet that lies.
 */
function assertBalanced(mask, file) {
  let depth = 0;
  for (const c of mask) {
    if (c === "{") depth += 1;
    else if (c === "}") depth -= 1;
    if (depth < 0) throw new Error(`shortcutScan: ${file} closes a brace it never opened`);
  }
  if (depth !== 0) throw new Error(`shortcutScan: ${file} ends inside ${depth} unclosed brace(s)`);
}

// --- structure ---------------------------------------------------------------

/** Every `{ … }` in the file, with the `if (…)` that guards it when there is one. */
function parseBlocks(mask, code) {
  const blocks = [];
  const stack = [];
  for (let i = 0; i < mask.length; i += 1) {
    if (mask[i] === "{") {
      let j = i - 1;
      while (j >= 0 && /\s/u.test(mask[j])) j -= 1;
      let guard = null;
      if (mask[j] === ")") {
        const open = matchBackward(mask, j);
        if (open !== -1) {
          const head = /(if|while|for|switch|catch)\s*$/u.exec(mask.slice(0, open));
          if (head) guard = { kind: head[1], start: open + 1, end: j, text: code.slice(open + 1, j) };
        }
      }
      const block = { guard, bodyStart: i + 1, bodyEnd: mask.length, parent: stack[stack.length - 1] || null };
      blocks.push(block);
      stack.push(block);
    } else if (mask[i] === "}") {
      const block = stack.pop();
      if (block) block.bodyEnd = i;
    }
  }
  return blocks;
}

/** Walk left from a `)` to its `(`. Returns -1 when there is no match. */
function matchBackward(mask, closeIndex) {
  let depth = 0;
  for (let i = closeIndex; i >= 0; i -= 1) {
    if (mask[i] === ")") depth += 1;
    else if (mask[i] === "(") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/** Walk right from a `(` to its `)`. Returns -1 when there is no match. */
function matchForward(mask, openIndex) {
  let depth = 0;
  for (let i = openIndex; i < mask.length; i += 1) {
    if (mask[i] === "(") depth += 1;
    else if (mask[i] === ")") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/** The innermost `{ … }` containing this offset. */
function blockAt(blocks, offset) {
  let found = null;
  for (const block of blocks) {
    if (offset >= block.bodyStart && offset < block.bodyEnd) {
      if (!found || block.bodyStart > found.bodyStart) found = block;
    }
  }
  return found;
}

/**
 * The `if (…)` / `while (…)` whose condition contains this offset.
 *
 * Walks outward through the call parens a comparison sits inside, so
 * `if (foo(event.key) === "x")` and `if ((a || b) && k === "f")` both resolve
 * to the same enclosing condition.
 */
function enclosingCondition(mask, code, offset) {
  let cursor = offset;
  for (let guard = 0; guard < 16; guard += 1) {
    const open = unbalancedOpenBefore(mask, cursor);
    if (open === -1) return null;
    const head = /(if|while)\s*$/u.exec(mask.slice(0, open));
    if (head) {
      const close = matchForward(mask, open);
      if (close === -1) return null;
      return { kind: head[1], start: open + 1, end: close, text: code.slice(open + 1, close) };
    }
    cursor = open;
  }
  return null;
}

/** The nearest `(` to the left of `offset` that is not closed before it. */
function unbalancedOpenBefore(mask, offset) {
  let depth = 0;
  for (let i = offset - 1; i >= 0; i -= 1) {
    if (mask[i] === ")") depth += 1;
    else if (mask[i] === "(") {
      if (depth === 0) return i;
      depth -= 1;
    }
  }
  return -1;
}

// --- modifiers ---------------------------------------------------------------

/**
 * The modifiers a condition demands.
 *
 * `!event.shiftKey` is a demand that the modifier be *absent*, so it must not
 * be read as a binding on Shift — that mistake would print a shortcut nobody
 * can press.
 */
function modifiersIn(conditionText) {
  const found = new Set();
  const required = new Set();
  const forbidden = new Set();
  const re = /(!\s*)?(?:\w+\.)?(shiftKey|ctrlKey|metaKey|altKey)/gu;
  let match = re.exec(conditionText);
  while (match) {
    if (match[1]) forbidden.add(match[2]);
    else {
      required.add(match[2]);
      found.add(MODIFIER_PROPS[match[2]]);
    }
    match = re.exec(conditionText);
  }
  // `Mod` means "⌘ on a Mac, Ctrl elsewhere" and the sheet prints it that way,
  // so collapsing both into it is right for the handlers that accept either.
  // A handler that requires one and explicitly REFUSES the other has said
  // something different, and printing `Mod` there would teach a Mac user a
  // keystroke that does not work — or, in the Move-by-exact box, one that
  // quits the app. Say which key it really is.
  if (required.has("ctrlKey") && forbidden.has("metaKey")) {
    found.delete("Mod");
    found.add("Ctrl");
  } else if (required.has("metaKey") && forbidden.has("ctrlKey")) {
    found.delete("Mod");
    found.add("Cmd");
  }
  return found;
}

/** Modifiers from every enclosing block guard, innermost outward. */
function inheritedModifiers(block) {
  const found = new Set();
  let node = block;
  while (node) {
    if (node.guard && (node.guard.kind === "if" || node.guard.kind === "while")) {
      for (const mod of modifiersIn(node.guard.text)) found.add(mod);
    }
    node = node.parent;
  }
  return found;
}

// --- key comparisons ---------------------------------------------------------

/**
 * Local names that hold `event.key`.
 *
 * `const k = event.key.toLowerCase()` is the shape Board3DView uses, and a
 * scanner that only understood `event.key === "x"` would report that file as
 * having no bindings at all.
 */
function keyAliases(code) {
  const aliases = new Map([
    ["event.key", false],
    ["e.key", false],
    ["evt.key", false],
  ]);
  const re = /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[\w.]*\bkey\b(\.toLowerCase\(\))?/gu;
  let match = re.exec(code);
  while (match) {
    aliases.set(match[1], Boolean(match[2]));
    match = re.exec(code);
  }
  return aliases;
}

/** `<alias> === "x"` and `<alias>.toLowerCase() === "x"`, either way round. */
function findComparisons(code, aliases) {
  const hits = [];
  const names = [...aliases.keys()].map((name) => name.replace(/\./gu, "\\.")).join("|");
  if (!names) return hits;
  const forward = new RegExp(`\\b(${names})\\b(\\.toLowerCase\\(\\))?\\s*===\\s*"((?:[^"\\\\]|\\\\.)*)"`, "gu");
  let match = forward.exec(code);
  while (match) {
    hits.push({
      index: match.index,
      alias: match[1],
      lowered: Boolean(match[2]) || aliases.get(match[1]) === true,
      key: JSON.parse(`"${match[3]}"`),
    });
    match = forward.exec(code);
  }
  return hits;
}

/** `switch (<alias>) { case "1": … }` — one binding per case group. */
function findSwitchCases(mask, code, blocks, aliases) {
  const groups = [];
  for (const block of blocks) {
    if (block.guard?.kind !== "switch") continue;
    const discriminant = block.guard.text.trim().replace(/\.toLowerCase\(\)$/u, "");
    if (!aliases.has(discriminant)) continue;
    const lowered = /\.toLowerCase\(\)\s*$/u.test(block.guard.text) || aliases.get(discriminant) === true;
    const body = mask.slice(block.bodyStart, block.bodyEnd);
    const labels = [];
    const re = /\bcase\s+"((?:[^"\\]|\\.)*)"\s*:|\bdefault\s*:/gu;
    // Case labels are matched on `code` (literals intact) but located by the
    // same offsets, since masking preserves length.
    const source = code.slice(block.bodyStart, block.bodyEnd);
    let match = re.exec(source);
    while (match) {
      const depth = braceDepth(body, match.index);
      if (depth === 0) {
        labels.push({
          key: match[1] === undefined ? null : JSON.parse(`"${match[1]}"`),
          start: block.bodyStart + match.index,
          end: block.bodyStart + match.index + match[0].length,
        });
      }
      match = re.exec(source);
    }
    for (let i = 0; i < labels.length; i += 1) {
      if (labels[i].key === null) continue;
      const keys = [labels[i].key];
      let last = i;
      // `case "e": case "E":` is one binding written twice, not two.
      while (
        last + 1 < labels.length &&
        labels[last + 1].key !== null &&
        code.slice(labels[last].end, labels[last + 1].start).trim() === ""
      ) {
        last += 1;
        keys.push(labels[last].key);
      }
      const bodyEnd = last + 1 < labels.length ? labels[last + 1].start : block.bodyEnd;
      groups.push({
        keys,
        lowered,
        index: labels[i].start,
        block,
        effect: code.slice(labels[last].end, bodyEnd),
      });
      i = last;
    }
  }
  return groups;
}

/** Brace depth at an offset within a slice. */
function braceDepth(slice, offset) {
  let depth = 0;
  for (let i = 0; i < offset; i += 1) {
    if (slice[i] === "{") depth += 1;
    else if (slice[i] === "}") depth -= 1;
  }
  return depth;
}

/** The statement a condition runs, braced or not. */
function statementAfter(mask, code, conditionEnd) {
  let i = conditionEnd + 1;
  while (i < mask.length && /\s/u.test(mask[i])) i += 1;
  if (mask[i] === "{") {
    let depth = 0;
    for (let j = i; j < mask.length; j += 1) {
      if (mask[j] === "{") depth += 1;
      else if (mask[j] === "}") {
        depth -= 1;
        if (depth === 0) return code.slice(i + 1, j);
      }
    }
    return code.slice(i + 1);
  }
  let depth = 0;
  for (let j = i; j < mask.length; j += 1) {
    const c = mask[j];
    if (c === "(" || c === "{" || c === "[") depth += 1;
    else if (c === ")" || c === "}" || c === "]") depth -= 1;
    else if (c === ";" && depth === 0) return code.slice(i, j);
  }
  return code.slice(i);
}

// --- naming ------------------------------------------------------------------

/** One printable token per key, e.g. `Space`, `Esc`, `PgDn`, `Q`, `[`. */
export function displayKey(rawKey, lowered) {
  if (KEY_DISPLAY[rawKey]) return KEY_DISPLAY[rawKey];
  if (rawKey.length === 1 && /[a-z]/iu.test(rawKey)) return rawKey.toUpperCase();
  if (lowered && rawKey.length > 1) return rawKey.charAt(0).toUpperCase() + rawKey.slice(1);
  return rawKey;
}

/** `Mod+Shift+X`, always in the same order, so a combo is an identity. */
export function comboOf(mods, rawKey, lowered) {
  const ordered = MODIFIER_ORDER.filter((mod) => mods.has(mod));
  return [...ordered, displayKey(rawKey, lowered)].join("+");
}

/**
 * A combo back into one token per keycap.
 *
 * Lives here rather than in the renderer so that `+` — a modifier separator
 * and also a key somebody could bind — is joined and split by the same file.
 */
export function comboParts(combo) {
  if (combo === "+") return ["+"];
  if (combo.endsWith("++")) return [...combo.slice(0, -2).split("+"), "+"];
  return combo.split("+");
}

/** Whitespace-flattened effect text — the fingerprint a copy entry is pinned to. */
export function normalizeEffect(text) {
  return String(text).replace(/\s+/gu, " ").trim();
}

/** FNV-1a, 32-bit. Short, stable, and not a security claim. */
export function effectHash(text) {
  let hash = 0x811c9dc5;
  const normalized = normalizeEffect(text);
  for (let i = 0; i < normalized.length; i += 1) {
    hash ^= normalized.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

// --- the scan ----------------------------------------------------------------

/**
 * Every keyboard binding one source file implements.
 *
 * @param {string} file  file name, used only in the failure messages
 * @param {string} text  the file's source
 * @returns {Array<{file,combo,keys,mods,effect,effectHash,line}>} sorted by combo
 */
export function scanKeyBindings(file, text) {
  const { code, mask } = maskSource(text);
  assertBalanced(mask, file);
  const blocks = parseBlocks(mask, code);
  const aliases = keyAliases(code);
  const raw = [];

  for (const hit of findComparisons(code, aliases)) {
    const condition = enclosingCondition(mask, code, hit.index);
    const mods = new Set();
    let effect = "";
    if (condition) {
      for (const mod of modifiersIn(condition.text)) mods.add(mod);
      for (const mod of inheritedModifiers(blockAt(blocks, condition.start))) mods.add(mod);
      effect = statementAfter(mask, code, condition.end);
    } else {
      for (const mod of inheritedModifiers(blockAt(blocks, hit.index))) mods.add(mod);
    }
    raw.push({ keys: [hit.key], lowered: hit.lowered, mods, effect, index: hit.index });
  }

  for (const group of findSwitchCases(mask, code, blocks, aliases)) {
    const mods = inheritedModifiers(group.block);
    raw.push({ keys: group.keys, lowered: group.lowered, mods, effect: group.effect, index: group.index });
  }

  // One combo, one binding: `case "e"` / `case "E"` and a lowercased compare
  // are the same keystroke to the person pressing it.
  const byCombo = new Map();
  for (const entry of raw.sort((a, b) => a.index - b.index)) {
    const combo = comboOf(entry.mods, entry.keys[0], entry.lowered);
    const existing = byCombo.get(combo);
    if (existing) {
      for (const key of entry.keys) if (!existing.keys.includes(key)) existing.keys.push(key);
      existing.effect = `${existing.effect} ${entry.effect}`;
      continue;
    }
    byCombo.set(combo, {
      file,
      combo,
      keys: [...entry.keys],
      mods: MODIFIER_ORDER.filter((mod) => entry.mods.has(mod)),
      effect: entry.effect,
      index: entry.index,
    });
  }

  return [...byCombo.values()]
    .map((entry) => ({
      file: entry.file,
      combo: entry.combo,
      keys: entry.keys,
      mods: entry.mods,
      line: text.slice(0, entry.index).split("\n").length,
      effect: normalizeEffect(entry.effect),
      effectHash: effectHash(entry.effect),
    }))
    .sort((a, b) => a.combo.localeCompare(b.combo));
}

/**
 * Scan a set of files at once.
 *
 * @param {Array<{surface: string, file: string, text: string}>} sources
 * @returns {Array} every binding, tagged with the surface it belongs to
 */
export function scanAll(sources) {
  const bindings = [];
  for (const source of sources) {
    const found = scanKeyBindings(source.file, source.text);
    // A source that contributes nothing is a reader that stopped working, not
    // a file that lost its keyboard. Fail here rather than ship a short sheet.
    if (!found.length) throw new Error(`shortcutScan: found no bindings in ${source.file}`);
    bindings.push(...found.map((binding) => ({ ...binding, surface: source.surface })));
  }
  return bindings.sort((a, b) => `${a.surface}:${a.combo}`.localeCompare(`${b.surface}:${b.combo}`));
}
