// netWidth — declaring how wide a net is routed, as one edit to the board file.
//
// The EE review's finding 4, in the engineer's own words: *"the main power
// nets, 5V and 3V3, are currently the same width as a signal, 0.2mm ... should
// be 0.5–1.0mm"*. The board file can say so and the router obeys it —
// `<trace thickness="0.5mm">` reaches the autorouter as a per-net
// `nominalTraceWidth` it holds while it searches — so this is a real edit a
// human can make, not a request an agent has to translate.
//
// No DOM, no React, no `@/` alias, for the reason `boardSource.js` has none:
// the part that can be wrong invisibly is the text arithmetic, and `node:test`
// covers it directly.
//
// Two facts from the shipped bundle decide the whole design:
//
//   1. **One declaration anywhere on a net sets the whole net.**
//      `getSimpleRouteJsonFromCircuitJson` takes
//      `nominalTraceWidth = max(min_trace_thickness)` over every trace joined
//      to the net. So marking one trace is identical to marking all of them,
//      and this writes exactly one.
//   2. **A board-level trace is enough**, even when the net is wired inside a
//      block. Measured 2026-08-16 on terminal-keyboard: adding
//      `<trace from=".U2 > .VOUT" to="net.V3_3" thickness="0.5mm" />` to the
//      board file moved V3_3's copper (and cost the board two clearance
//      findings inside the RP2040 fanout, which is the ceiling doing its job).
//
// What this module will not do is decide the number. The ceiling comes from
// `circuitpy.netwidth`, measured against the placement, and the caller shows
// it: 0.4000mm on V3_3 through a QFN-56's 0.400mm pitch, 1.1mm on V5. A tool
// that let someone type 0.5 into the first of those without saying so would be
// handing them a board that cannot exist.

import { maskCommentsAndStrings } from "./boardSource.js";

/** How a width is spelled in the file. Millimetres, the board's own unit. */
export function formatWidth(mm) {
  const value = Number(mm);
  if (!Number.isFinite(value) || value <= 0) return "";
  // Trailing zeros stripped: `0.5mm`, not `0.500mm`. Four decimals is the
  // micrometre, which is finer than any fab tolerance we quote.
  return `${String(Number(value.toFixed(4)))}mm`;
}

/**
 * Every `<trace …/>` in the file, with its span and its text.
 *
 * `maskCommentsAndStrings` returns a **mask**, one byte per character, 1 where
 * that character is inside a comment or a string — not a masked copy of the
 * text. Both the opening `<trace` and the `>` that ends the tag are only real
 * where the mask is 0, which is what keeps `from=".U3 > .GPIO0"` from ending
 * the tag at its own greater-than and a `<trace …/>` written inside a comment
 * from being edited at all.
 */
function traceTags(source) {
  const text = String(source || "");
  const mask = maskCommentsAndStrings(text);
  const tags = [];
  const re = /<trace\b/g;
  let match;
  while ((match = re.exec(text)) !== null) {
    const start = match.index;
    if (mask[start]) continue;
    let end = -1;
    for (let i = start; i < text.length; i += 1) {
      if (text[i] === ">" && !mask[i]) {
        end = i;
        break;
      }
    }
    if (end === -1) break;
    tags.push({ start, end: end + 1, text: text.slice(start, end + 1) });
    re.lastIndex = end + 1;
  }
  return tags;
}

/** The net a `to=`/`from=` value names, or "" — `net.V3_3` → `V3_3`. */
function netOf(value) {
  const match = /^net\.([A-Za-z0-9_]+)$/.exec(String(value || "").trim());
  return match ? match[1] : "";
}

/** `attr="value"` out of one tag's text. */
function attr(text, name) {
  const match = new RegExp(`\\b${name}=(?:"([^"]*)"|\\{\`([^\`]*)\`\\})`).exec(text);
  return match ? match[1] ?? match[2] ?? "" : "";
}

/**
 * The traces in this file that land on `net`, nearest thing to a handle the
 * board has on that net's width.
 */
export function tracesForNet(source, net) {
  const wanted = String(net || "");
  if (!wanted) return [];
  return traceTags(String(source || "")).filter(
    (tag) => netOf(attr(tag.text, "to")) === wanted || netOf(attr(tag.text, "from")) === wanted,
  );
}

/** What the file currently declares for `net`, in mm, or null. */
export function declaredWidth(source, net) {
  let best = null;
  for (const tag of tracesForNet(source, net)) {
    const raw = attr(tag.text, "thickness");
    const value = Number(String(raw).replace(/mm$/i, ""));
    if (Number.isFinite(value) && value > 0) best = best === null ? value : Math.max(best, value);
  }
  return best;
}

/**
 * The edits that make `net` route at `mm`, or a refusal saying why not.
 *
 * `mm` of 0 or null clears the declaration, which is how a rail goes back to
 * the board's own `minTraceWidth` — an engineer who widens a net and does not
 * like the result must be able to put it back without hand-editing TSX.
 *
 * @returns {{ok: true, edits: Array, summary: string} | {ok: false, reason: string}}
 */
export function widthEdits(source, net, mm, { anchorPort = "" } = {}) {
  const text = String(source || "");
  const name = String(net || "").trim();
  if (!name) return { ok: false, reason: "no net named" };
  const want = mm === null || mm === undefined || Number(mm) === 0 ? null : Number(mm);
  if (want !== null && (!Number.isFinite(want) || want <= 0 || want > 10)) {
    return { ok: false, reason: `${want}mm is not a track width` };
  }

  const tags = tracesForNet(text, name);
  const current = declaredWidth(text, name);
  if (current === want || (current === null && want === null)) {
    return { ok: false, reason: `${name} is already ${want === null ? "undeclared" : formatWidth(want)}` };
  }

  // The trace that already carries the declaration, else the first one on the
  // net — one is enough, and marking a second would leave two numbers to
  // disagree later.
  const marked = tags.find((tag) => attr(tag.text, "thickness")) || tags[0] || null;

  if (marked) {
    const existing = /\s*thickness=(?:"[^"]*"|\{`[^`]*`\})/.exec(marked.text);
    if (want === null) {
      if (!existing) return { ok: false, reason: `${name} has no declared width to clear` };
      const start = marked.start + existing.index;
      return {
        ok: true,
        summary: `${name} back to the board's default width`,
        edits: [{ start, end: start + existing[0].length, text: "", expected: existing[0] }],
      };
    }
    if (existing) {
      const start = marked.start + existing.index;
      return {
        ok: true,
        summary: `${name} routed at ${formatWidth(want)}`,
        edits: [
          {
            start,
            end: start + existing[0].length,
            text: ` thickness="${formatWidth(want)}"`,
            expected: existing[0],
          },
        ],
      };
    }
    // No `thickness` yet: insert one after the last attribute rather than
    // immediately before the close, or a tag written `… />` comes back
    // `…  thickness="0.5mm"/>` — two spaces and none where the eye expects
    // one. The file is read by people.
    const closeLen = marked.text.endsWith("/>") ? 2 : 1;
    const body = marked.text.slice(0, marked.text.length - closeLen);
    const closeAt = marked.start + body.replace(/\s+$/, "").length;
    return {
      ok: true,
      summary: `${name} routed at ${formatWidth(want)}`,
      edits: [{ start: closeAt, end: closeAt, text: ` thickness="${formatWidth(want)}"`, expected: "" }],
    };
  }

  // No trace in the board file touches this net — it is wired inside a block.
  // A new board-level trace between a port on the net and the net is a
  // connection that already exists, so it adds no copper; what it adds is the
  // `min_trace_thickness` the router reads. Measured working on
  // terminal-keyboard's V3_3, which is wired entirely inside `ldo-3v3`.
  if (want === null) return { ok: false, reason: `${name} has no declared width to clear` };
  if (!anchorPort) {
    return {
      ok: false,
      reason:
        `${name} is wired inside a block, so the board file has no trace to widen — ` +
        "and no pin on the net was given to hang one on",
    };
  }
  const closing = text.lastIndexOf("</board>");
  if (closing === -1) return { ok: false, reason: "this board file has no <board> to add a trace to" };
  const indent = "    ";
  const insert =
    `${indent}{/* width: ${name} routed at ${formatWidth(want)} (set in the app) */}\n` +
    `${indent}<trace from="${anchorPort}" to="net.${name}" thickness="${formatWidth(want)}" />\n`;
  return {
    ok: true,
    summary: `${name} routed at ${formatWidth(want)}`,
    edits: [{ start: closing, end: closing, text: insert, expected: "" }],
  };
}

/**
 * A pin on this net that the board file can name, as a tscircuit selector.
 *
 * Only needed when the net is wired entirely inside a block: the board file
 * then has no trace to mark, and a board-level trace between one of the net's
 * own pins and the net gives the router the width without adding copper,
 * because that connection already exists. `.U2 > .VOUT` is the spelling
 * tscircuit uses and the one every trace in our board files already carries.
 *
 * Read off the built board rather than the source, because the source is
 * exactly what does not mention this net.
 */
export function anchorPortForNet(index, netKey) {
  const elements = index?.elements;
  if (!index || !netKey || !Array.isArray(elements)) return "";
  const sourcePorts = new Map();
  const refdesById = new Map();
  for (const element of elements) {
    if (!element || typeof element !== "object") continue;
    if (element.type === "source_port") sourcePorts.set(String(element.source_port_id), element);
    else if (element.type === "source_component") {
      refdesById.set(String(element.source_component_id), String(element.name || ""));
    }
  }
  const net = index.netByKey?.get(netKey);
  for (const id of net?.pcbElementIds || []) {
    const element = index.byId?.get(id);
    if (!element || (element.type !== "pcb_smtpad" && element.type !== "pcb_plated_hole")) continue;
    const pcbPort = index.byId?.get(String(element.pcb_port_id || ""));
    const source = sourcePorts.get(String(pcbPort?.source_port_id || ""));
    if (!source) continue;
    const refdes = refdesById.get(String(source.source_component_id || ""));
    const pin = String(source.name || "");
    // A pin with no name is a pin the file cannot address; keep looking.
    if (refdes && pin) return `.${refdes} > .${pin}`;
  }
  return "";
}
