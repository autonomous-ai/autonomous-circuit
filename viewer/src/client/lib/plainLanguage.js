// plainLanguage — the non-engineer's read of a board.
//
// Everything else in this workspace speaks EDA: `drc_violation`,
// `[hole_clearance]`, `source_component.ftype`. That vocabulary is correct and
// an engineer needs it, but it is not an answer to the only question a
// first-time user actually has: **can I get this made, and if not, what is
// wrong?** This module is the translation layer that answers it.
//
// Three jobs, all pure so node:test covers them directly:
//
//   1. `groupFindings` — collapse a 694-row DRC wall into a handful of issues,
//      each with a plain title, a sentence of meaning, and an honest impact.
//   2. `boardVerdict` — one sentence for the top of the screen: ready to order,
//      or not orderable yet and what is left.
//   3. `plainParts` — "the brain", "the USB-C socket", "the indicator LED"
//      instead of U3, J1, LED1.
//
// The honesty rules this module is built to keep:
//   - `fab.ready` is the ONLY thing that decides whether a board reads as
//     orderable. No count, no heuristic, no severity arithmetic overrides it.
//   - Severity comes from the sidecar and is never rewritten here. `impact` is
//     a *separate* axis about what a finding means for the physical board.
//   - A part whose identity we cannot prove is named by what the source file
//     says it is, never guessed prettier.

/** Impact — what a finding means for the board you would hold in your hand. */
export const IMPACT = Object.freeze({
  BLOCKS: "blocks", // the fab packet will not ship until this is gone
  QUALITY: "quality", // it would build, but it is a real risk
  COSMETIC: "cosmetic", // it affects how the board looks, not whether it works
  TOOLING: "tooling", // it is about the checker's setup, not the board at all
});

/**
 * The issue dictionary. Keys are the bracketed code KiCad puts at the head of
 * a DRC/ERC detail (`[hole_clearance] Hole clearance violation …`) plus our own
 * pipeline's `kind` values for the findings that carry no bracket.
 *
 * `title` is a claim in plain words — what is wrong, not what rule fired.
 * `meaning` is one sentence a non-engineer can act on.
 * `impact` is the physical-board axis described above.
 */
const ISSUES = Object.freeze({
  // --- copper and drilling: the ones that make a board fail -----------------
  hole_clearance: {
    title: "A drilled hole sits too close to copper",
    meaning:
      "The drill could cut into a nearby track. The factory will not guarantee a board like this.",
    impact: IMPACT.BLOCKS,
  },
  clearance: {
    title: "Two pieces of copper are too close together",
    meaning:
      "The gap is smaller than the factory can etch reliably, so the two could end up touching.",
    impact: IMPACT.BLOCKS,
  },
  unconnected_items: {
    title: "Something on the board is not wired up",
    meaning:
      "The schematic says these two points connect; the copper layout does not connect them.",
    impact: IMPACT.BLOCKS,
  },
  copper_edge_clearance: {
    title: "Copper runs too close to the board edge",
    meaning: "The cutting tool could nick the copper when the board is routed out.",
    impact: IMPACT.BLOCKS,
  },
  shorting_items: {
    title: "Two nets are shorted together",
    meaning: "Copper that should be separate is touching, which will short the circuit.",
    impact: IMPACT.BLOCKS,
  },
  holes_co_located: {
    title: "Two holes are drilled in the same spot",
    meaning: "One drill hit does the work of two, so one of these holes is redundant or misplaced.",
    impact: IMPACT.QUALITY,
  },
  hole_near_hole: {
    title: "Two holes are drilled too close together",
    meaning: "The material between them can break out during drilling.",
    impact: IMPACT.QUALITY,
  },
  track_dangling: {
    title: "A track ends in mid-air",
    meaning: "A copper track stops without reaching a pad, so it does nothing.",
    impact: IMPACT.QUALITY,
  },
  net_conflict: {
    title: "A pad is assigned to the wrong signal",
    meaning:
      "The copper and the schematic disagree about what this pad connects to. Usually the two need re-syncing, not re-designing.",
    impact: IMPACT.QUALITY,
  },
  track_width: {
    title: "A track is thinner than the rule allows",
    meaning: "A thin track carries less current and is easier for the factory to break.",
    impact: IMPACT.QUALITY,
  },
  annular_width: {
    title: "A pad ring is too thin around its hole",
    meaning: "Not enough copper is left around the drill, so the pad can lift off.",
    impact: IMPACT.QUALITY,
  },
  pcb_trace_too_long_warning: {
    title: "A track is longer than this design's own limit",
    meaning: "It will still build. Long tracks pick up more noise, which matters for fast signals.",
    impact: IMPACT.QUALITY,
  },

  // --- tscircuit's own errors ----------------------------------------------
  //
  // Watched on a real first build: six issues were listed as what was left to
  // fix, and four of them read `pcb trace missing error ×37 — we do not have
  // plain words for this check yet`. Honest, and a dead end: the person has
  // nothing to act on. These are the codes the generator emits most often, and
  // the order they cascade in is the repo's own (`skills/circuitcode/
  // references/patterns/fix-a-drc.md`) — placement first, routing second,
  // because the routing errors are usually consequences of the placement ones.
  pcb_footprint_overlap_error: {
    title: "Two parts are sitting on top of each other",
    meaning: "They overlap on the board, so they cannot both be soldered where they are. One has to move.",
    impact: IMPACT.BLOCKS,
  },
  pcb_courtyard_overlap_error: {
    title: "Two parts are packed too tightly to assemble",
    meaning:
      "Each part needs a margin of clear space around it for the machine that places it. These two are inside each other's margin.",
    impact: IMPACT.BLOCKS,
  },
  pcb_pad_pad_clearance_error: {
    title: "Two solder pads are too close together",
    meaning: "Solder would bridge across the gap and short them. Moving one of the parts fixes it.",
    impact: IMPACT.BLOCKS,
  },
  pcb_component_outside_board_error: {
    title: "A part hangs off the edge of the board",
    meaning: "Some of it sits outside the outline, so it would be cut off. The board has to grow or the part has to move in.",
    impact: IMPACT.BLOCKS,
  },
  pcb_placement_error: {
    title: "A part could not be placed",
    meaning: "The layout step could not find a spot for this part on the board as drawn.",
    impact: IMPACT.BLOCKS,
  },
  pcb_autorouting_error: {
    title: "The wiring could not be drawn",
    meaning:
      "The step that draws the copper gave up. It usually gives up because two parts overlap — fix those first and this normally goes with them.",
    impact: IMPACT.BLOCKS,
  },
  pcb_trace_missing_error: {
    title: "A connection was never drawn on the board",
    meaning:
      "The wiring diagram asks for this link and no copper was laid down for it. Usually the same cause as the wiring that could not be drawn.",
    impact: IMPACT.BLOCKS,
  },
  pcb_trace_not_connected_error: {
    title: "A copper track stops short of what it should reach",
    meaning: "The track was drawn but does not arrive at the pad at its far end.",
    impact: IMPACT.BLOCKS,
  },
  pcb_port_not_connected_error: {
    title: "A part's pin has nothing attached to it",
    meaning:
      "This pin is supposed to be wired to something and no copper reaches it. Usually a knock-on from wiring that could not be drawn.",
    impact: IMPACT.BLOCKS,
  },
  pcb_port_not_matched_error: {
    title: "A pin in the diagram has no matching pad on the board",
    meaning: "The drawing and the part's physical pads disagree about which pins exist.",
    impact: IMPACT.BLOCKS,
  },
  pcb_missing_footprint_error: {
    title: "A part has no pads to solder to",
    meaning: "This part is in the design but nothing says what its metal contacts look like, so the board has nowhere to put it.",
    impact: IMPACT.BLOCKS,
  },
  source_trace_not_connected_error: {
    title: "A wire points at a pin that does not exist",
    meaning: "Almost always a mis-typed pin name in the design. The part's own list of pin names is the reference.",
    impact: IMPACT.BLOCKS,
  },
  pcb_trace_clearance_error: {
    title: "Two copper tracks run too close together",
    meaning: "The gap is smaller than the factory can etch reliably, so the two could end up touching.",
    impact: IMPACT.BLOCKS,
  },
  pcb_pad_trace_clearance_error: {
    title: "A track runs too close to a solder pad",
    meaning: "Solder could bridge from the pad onto the track. Route the track further away.",
    impact: IMPACT.BLOCKS,
  },
  pcb_via_clearance_error: {
    title: "A through-hole sits too close to other copper",
    meaning: "The hole that carries a signal between layers does not have enough clear space around it.",
    impact: IMPACT.BLOCKS,
  },
  pcb_via_trace_clearance_error: {
    title: "A through-hole sits too close to a track",
    meaning: "The hole that carries a signal between layers is nearly touching a nearby track.",
    impact: IMPACT.BLOCKS,
  },

  // --- the factory's own limits (dfm_*) -------------------------------------
  // Everything here is measured against JLCPCB's published minimums
  // (`circuitpy/fab.py`), so each one is "smaller/closer than they will build",
  // never an opinion.
  dfm_trace_width: {
    title: "A copper track is thinner than the factory will make",
    meaning: "Below their minimum width the track may come out broken or missing.",
    impact: IMPACT.BLOCKS,
  },
  dfm_trace_clearance: {
    title: "Two pieces of copper are closer than the factory will etch",
    meaning: "Below their minimum gap the two can end up joined.",
    impact: IMPACT.BLOCKS,
  },
  dfm_edge_clearance: {
    title: "Copper runs too close to the board edge",
    meaning: "The tool that cuts the board out could nick the copper.",
    impact: IMPACT.BLOCKS,
  },
  dfm_hole_clearance: {
    title: "A hole sits too close to copper",
    meaning: "The drill could break into a nearby track.",
    impact: IMPACT.BLOCKS,
  },
  dfm_drill_size: {
    title: "A hole is smaller than the factory can drill",
    meaning: "Their smallest drill is bigger than this hole, so it cannot be made as drawn.",
    impact: IMPACT.BLOCKS,
  },
  dfm_via_diameter: {
    title: "A layer-to-layer hole is smaller than the factory can make",
    meaning: "The hole that carries a signal between layers is below their minimum size.",
    impact: IMPACT.BLOCKS,
  },
  dfm_annular_ring: {
    title: "A pad ring is too thin around its hole",
    meaning: "Not enough copper is left around the drill, so the pad can tear off.",
    impact: IMPACT.BLOCKS,
  },
  dfm_board_size: {
    title: "The board is outside the sizes this factory makes",
    meaning: "It is either smaller or larger than their range for this service.",
    impact: IMPACT.BLOCKS,
  },
  dfm_thickness: {
    title: "The board thickness is not one the factory offers",
    meaning: "Pick one of their standard thicknesses instead.",
    impact: IMPACT.BLOCKS,
  },

  // --- parts on the board ---------------------------------------------------
  missing_footprint: {
    title: "A part in the schematic has nothing to solder to",
    meaning: "The drawing has this part but the board has no pads for it.",
    impact: IMPACT.QUALITY,
  },
  extra_footprint: {
    title: "The board has pads for a part that is not in the schematic",
    meaning: "Something is on the board that the drawing does not know about.",
    impact: IMPACT.QUALITY,
  },
  duplicate_footprints: {
    title: "Two parts share the same name",
    meaning: "Two footprints carry one reference, so the assembly house cannot tell them apart.",
    impact: IMPACT.QUALITY,
  },
  footprint_symbol_mismatch: {
    title: "A part's pads and its symbol disagree",
    meaning:
      "The board and the schematic describe the same part differently. It is a bookkeeping mismatch between two files, not a wiring fault.",
    impact: IMPACT.TOOLING,
  },
  footprint_symbol_field_mismatch: {
    title: "A part's text fields differ between the drawing and the board",
    meaning: "A description or value is spelled differently in the two files. It changes nothing physical.",
    impact: IMPACT.TOOLING,
  },
  footprint_type_mismatch: {
    title: "A part is marked as a different mounting type in each file",
    meaning: "Through-hole versus surface-mount is recorded inconsistently. Cosmetic to the fab.",
    impact: IMPACT.TOOLING,
  },
  part_not_orderable: {
    title: "A part cannot be ordered",
    meaning: "No supplier number is pinned for this part, so nobody can buy it to build the board.",
    impact: IMPACT.BLOCKS,
  },
  extended_part: {
    title: "A part costs extra to place",
    meaning:
      "JLCPCB charges a one-off fee per non-stock part. It builds fine; it just costs more.",
    impact: IMPACT.QUALITY,
  },
  part_drift: {
    title: "A part changed since it was last checked",
    meaning: "The stock or price behind this part has moved. Worth re-checking before ordering.",
    impact: IMPACT.QUALITY,
  },

  // --- printing on the board (silkscreen) -----------------------------------
  silk_overlap: {
    title: "Printed labels overlap each other",
    meaning: "The white text on the board will be hard to read here. The board works the same.",
    impact: IMPACT.COSMETIC,
  },
  silk_over_copper: {
    title: "Printed text sits on bare metal",
    meaning: "Ink over an exposed pad rubs off. Looks only.",
    impact: IMPACT.COSMETIC,
  },
  silk_edge_clearance: {
    title: "Printed text runs off the board edge",
    meaning: "Part of a label will be cut away when the board is routed out. Looks only.",
    impact: IMPACT.COSMETIC,
  },
  text_height: {
    title: "Some printed text is smaller than the factory prints",
    meaning: "Those labels may come out blurred or missing. Nothing electrical changes.",
    impact: IMPACT.COSMETIC,
  },
  text_thickness: {
    title: "Some printed text is thinner than the factory prints",
    meaning: "Same as above — the ink may not take. Nothing electrical changes.",
    impact: IMPACT.COSMETIC,
  },

  // --- the schematic drawing ------------------------------------------------
  endpoint_off_grid: {
    title: "A wire in the drawing ends slightly off the grid",
    meaning: "Tidiness in the schematic sheet. It does not reach the board.",
    impact: IMPACT.TOOLING,
  },
  unconnected_wire_endpoint: {
    title: "A wire in the drawing stops short",
    meaning: "A drawn line ends without touching anything. Check it is not a connection you meant.",
    impact: IMPACT.COSMETIC,
  },
  wire_dangling: {
    title: "A wire in the drawing connects to nothing",
    meaning: "Leftover line work on the schematic sheet.",
    impact: IMPACT.COSMETIC,
  },
  label_dangling: {
    title: "A label in the drawing is attached to nothing",
    meaning: "A net name floats free on the sheet.",
    impact: IMPACT.COSMETIC,
  },
  isolated_pin_label: {
    title: "A label touches only one pin",
    meaning: "A named signal that goes nowhere else. Often deliberate, sometimes a missed connection.",
    impact: IMPACT.COSMETIC,
  },
  pin_not_connected: {
    title: "A chip pin is left unconnected",
    meaning: "Most chips have pins that are meant to be left alone. Worth a glance, rarely a fault.",
    impact: IMPACT.COSMETIC,
  },
  power_pin_not_driven: {
    title: "A power pin has no supply feeding it",
    meaning: "The checker cannot find where this pin gets its voltage from.",
    impact: IMPACT.QUALITY,
  },
  pin_to_pin: {
    title: "Two pins are connected that should not be",
    meaning: "For example two outputs driving each other. Worth reading closely.",
    impact: IMPACT.QUALITY,
  },
  duplicate_reference: {
    title: "Two parts in the drawing share one name",
    meaning: "Reference names have to be unique for the assembly house to place parts.",
    impact: IMPACT.QUALITY,
  },

  // --- the checker itself, not the board ------------------------------------
  lib_symbol_issues: {
    title: "The checker is missing a symbol library",
    meaning: "KiCad on this machine does not have the library, so it reports every part that uses it. Nothing is wrong with the board.",
    impact: IMPACT.TOOLING,
  },
  lib_footprint_issues: {
    title: "The checker is missing a footprint library",
    meaning: "Same as above — a missing library on this machine, not a fault in the design.",
    impact: IMPACT.TOOLING,
  },
  library_symbol_mismatch: {
    title: "A symbol differs from the library copy",
    meaning: "The drawing uses an edited version of a stock symbol. Normal for generated boards.",
    impact: IMPACT.TOOLING,
  },
  unverified_gerbers: {
    title: "The factory files have not been checked",
    meaning:
      "The gerbers were produced without KiCad verifying them, so nobody has confirmed the factory would read them correctly.",
    impact: IMPACT.BLOCKS,
  },
  check_failed: {
    title: "A check could not finish",
    meaning: "One step of the build stopped before it could give an answer, so its result is unknown.",
    impact: IMPACT.BLOCKS,
  },
});

const BRACKET_CODE_RE = /^\s*\[([a-z0-9_]+)\]/i;

/**
 * The issue code for a finding: the bracketed code KiCad writes at the head of
 * its message, else the pipeline's own `kind`. Lower-cased; "" when neither.
 */
export function issueCode(finding) {
  const detail = String(finding?.detail || "");
  const bracket = BRACKET_CODE_RE.exec(detail);
  if (bracket) return bracket[1].toLowerCase();
  const kind = String(finding?.kind || "").trim().toLowerCase();
  // `drc_violation` / `erc_violation` are containers, not codes — a finding
  // that reaches here with one of those and no bracket has nothing better.
  return kind;
}

/** Strip the leading `[code] ` so the detail reads as a sentence. */
export function issueDetailText(finding) {
  return String(finding?.detail || "").replace(BRACKET_CODE_RE, "").trim();
}

/**
 * The plain-language entry for a code. Unknown codes get an honest fallback:
 * the code itself, spaced out, and a `meaning` that says we do not have words
 * for it rather than inventing some.
 */
export function plainIssue(code) {
  const key = String(code || "").toLowerCase();
  const known = ISSUES[key];
  if (known) return { code: key, known: true, ...known };
  return {
    code: key,
    known: false,
    title: key ? key.replace(/_/g, " ") : "Unrecognised finding",
    meaning: "",
    impact: IMPACT.QUALITY,
  };
}

const IMPACT_RANK = { blocks: 0, quality: 1, cosmetic: 2, tooling: 3 };
const SEVERITY_RANK = { error: 0, warning: 1, info: 2 };

/**
 * Collapse message rows into one group per issue code.
 *
 * This is the single biggest thing standing between a non-engineer and this
 * tool: 694 rows of `[text_height] Text height out of range (…)` is not
 * information, it is weather. Nine groups with counts is.
 *
 * Each group keeps its rows, so the detailed view is one expand away and
 * nothing is hidden.
 *
 * @param {Array<object>} rows buildMessages() output (or normalized warnings)
 * @returns {Array<{code,title,meaning,impact,severity,count,blocking,rows,parts,sample}>}
 */
export function groupFindings(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const groups = new Map();
  for (const row of list) {
    const code = issueCode(row);
    const key = code || "unknown";
    let group = groups.get(key);
    if (!group) {
      const plain = plainIssue(code);
      group = {
        code: key,
        title: plain.title,
        meaning: plain.meaning,
        impact: plain.impact,
        known: plain.known,
        severity: "info",
        count: 0,
        blocking: false,
        rows: [],
        parts: [],
        sample: "",
      };
      groups.set(key, group);
    }
    group.count += 1;
    group.rows.push(row);
    const severity = String(row?.severity || "warning").toLowerCase();
    // A group takes the worst severity any of its rows carries — the sidecar
    // stays the authority, we only aggregate it.
    if ((SEVERITY_RANK[severity] ?? 1) < (SEVERITY_RANK[group.severity] ?? 1)) {
      group.severity = severity;
    }
    if (severity === "error" || key === "unverified_gerbers" || key === "check_failed") {
      group.blocking = true;
    }
    if (!group.sample) group.sample = issueDetailText(row);
    const part = String(row?.part || "").trim();
    if (part && part !== "board" && group.parts.length < 24 && !group.parts.includes(part)) {
      group.parts.push(part);
    }
  }

  return [...groups.values()].sort(
    (a, b) =>
      Number(b.blocking) - Number(a.blocking) ||
      (IMPACT_RANK[a.impact] ?? 1) - (IMPACT_RANK[b.impact] ?? 1) ||
      (SEVERITY_RANK[a.severity] ?? 1) - (SEVERITY_RANK[b.severity] ?? 1) ||
      b.count - a.count,
  );
}

/** Counts by impact across groups: {blocks, quality, cosmetic, tooling, total}. */
export function impactCounts(groups) {
  const out = { blocks: 0, quality: 0, cosmetic: 0, tooling: 0, total: 0 };
  for (const group of Array.isArray(groups) ? groups : []) {
    const bucket = group.blocking ? IMPACT.BLOCKS : group.impact;
    out[bucket] = (out[bucket] || 0) + group.count;
    out.total += group.count;
  }
  return out;
}

/** English list: ["a"] → "a"; ["a","b"] → "a and b"; more → "a, b and c". */
export function joinWords(items, conjunction = "and") {
  const list = (Array.isArray(items) ? items : []).filter(Boolean);
  if (!list.length) return "";
  if (list.length === 1) return String(list[0]);
  return `${list.slice(0, -1).join(", ")} ${conjunction} ${list[list.length - 1]}`;
}

/** "1 thing" / "3 things" — plurals without a library. */
export function plural(count, one, many = `${one}s`) {
  const n = Number(count) || 0;
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * The verdict: the one line at the top of the screen that answers "can I get
 * this made?".
 *
 * The gate is `fab.ready` and nothing else. When the sidecar has not been read
 * yet we say so; we never fill the gap with an optimistic guess.
 *
 * A build that *failed* is its own answer and outranks everything below it.
 * Without this case the strip fell back to "No build to judge yet" while the
 * tree three inches away said the build had stopped — seen in a real run, and
 * worse still on a project whose earlier build succeeded, where a stale sidecar
 * would have kept saying "Ready to order" after a crash.
 *
 * @param {{sidecar?: object|null, groups?: Array<object>, building?: boolean,
 *          buildLine?: {tone: string, text: string, detail: string}|null}} input
 * @returns {{
 *   tone: "ready"|"blocked"|"building"|"failed"|"unknown",
 *   headline: string,
 *   line: string,
 *   blockingGroups: Array<object>,
 *   blockingCount: number,
 * }}
 */
export function boardVerdict({ sidecar = null, groups = [], building = false, buildLine = null } = {}) {
  const blockingGroups = (Array.isArray(groups) ? groups : []).filter((g) => g.blocking);
  const blockingCount = blockingGroups.reduce((sum, g) => sum + g.count, 0);

  if (building) {
    return {
      tone: "building",
      headline: "Working on it",
      line: "The board is being designed and checked. This takes about a minute and a half.",
      blockingGroups,
      blockingCount,
    };
  }

  const buildTone = String(buildLine?.tone || "");
  if (buildTone === "failed" || buildTone === "stale") {
    const why = String(buildLine?.detail || "").trim();
    return {
      tone: "failed",
      headline: buildTone === "failed" ? "The last build failed" : "The last build stopped",
      line: sidecar
        ? `Nothing new was produced, so this is the build before it.${why ? ` ${why}.` : ""} Ask the chat to build it again.`
        : `No board came out of it.${why ? ` ${why}.` : ""} Ask the chat to build it again.`,
      blockingGroups,
      blockingCount,
    };
  }

  if (!sidecar) {
    // A build can finish without leaving a board here: the agent's own check
    // runs in a scratch copy, and until it writes one into the project there
    // is nothing to judge. Saying "no build yet" beside a line that reads
    // "Built · 6m 49s" is the kind of small contradiction that costs trust, so
    // when a build has just finished the sentence says which fact is which.
    // `done-blocked` is the same fact with a worse outcome — a build that
    // finished carrying problems. Both mean "a build happened".
    if (buildTone === "done" || buildTone === "done-blocked") {
      return {
        tone: "unknown",
        headline: "Nothing to judge yet",
        line: "A build finished, but no board file has landed in this project. Until one does there is nothing to check or order.",
        blockingGroups,
        blockingCount,
      };
    }
    return {
      tone: "unknown",
      headline: "No build to judge yet",
      line: "Once the board builds, this line says whether it can be ordered.",
      blockingGroups,
      blockingCount,
    };
  }

  if (sidecar?.fab?.ready === true) {
    // A ready board can still carry cosmetic and checker-setup notes. Saying
    // "every check passed" when 60 findings are on screen would read as a lie
    // the moment anyone scrolled, so the count goes in the sentence.
    const rest = (Array.isArray(groups) ? groups : []).reduce((sum, g) => sum + g.count, 0);
    const tail = rest
      ? ` ${plural(rest, "note")} left in the checks, none of which stop the order.`
      : " Nothing outstanding.";
    return {
      tone: "ready",
      headline: "Ready to order",
      line: `The factory files are made and verified — send this to JLCPCB as it is.${tail}`,
      blockingGroups,
      blockingCount,
    };
  }

  // Not ready. Say what is left, in the order a person would fix it.
  const headline = blockingGroups.length
    ? `Not orderable yet — ${plural(blockingGroups.length, "thing")} to fix`
    : "Not orderable yet";
  const first = blockingGroups[0];
  const line = first
    ? `${first.title}${first.count > 1 ? `, in ${first.count} places` : ""}. ${first.meaning}`
    : "The build did not produce a verified factory packet. Rebuild the board to try again.";

  return { tone: "blocked", headline, line, blockingGroups, blockingCount };
}

/**
 * The chat request a group's Fix button writes. Names the issue in plain
 * words, then hands over the exact code, count and parts so the model repairs
 * the real thing rather than the paraphrase.
 */
export function groupFixRequest(group, { board = "" } = {}) {
  if (!group) return "";
  const where = group.parts.length
    ? ` Affected: ${group.parts.slice(0, 12).join(", ")}${group.parts.length > 12 ? ", …" : ""}.`
    : "";
  const boardName = String(board || "").trim();
  return [
    `Fix ${group.count === 1 ? "this" : `all ${group.count}`} "${group.title.toLowerCase()}"`,
    boardName ? ` on ${boardName}` : "",
    ` (${group.code}).`,
    group.sample ? ` Example: ${group.sample}` : "",
    where,
    " Then rebuild and re-run the checks.",
  ].join("");
}

// --- what am I looking at -------------------------------------------------

/**
 * Part roles, in the order they should be read. `id` is stable for tests,
 * `label` is the plain name, `blurb` says what the thing does in one clause.
 */
const ROLE_ORDER = [
  "brain",
  "power_in",
  "power_reg",
  "memory",
  "sensor",
  "radio",
  "driver",
  "indicator",
  "control",
  "clock",
  "protection",
  "passive",
  "mechanical",
  "other",
];

const ROLES = Object.freeze({
  brain: { label: "The brain", blurb: "runs the program and drives everything else" },
  power_in: { label: "Power in", blurb: "where the cable plugs in" },
  power_reg: { label: "Power supply", blurb: "turns the input voltage into what the chips need" },
  memory: { label: "Memory", blurb: "where the program is stored" },
  sensor: { label: "Sensors", blurb: "how the board reads the world" },
  radio: { label: "Radio", blurb: "wireless link" },
  driver: { label: "Drivers", blurb: "push real power out to motors, speakers or lights" },
  indicator: { label: "Lights", blurb: "what the board shows you" },
  control: { label: "Controls", blurb: "what you press" },
  clock: { label: "Clock", blurb: "keeps the chip in time" },
  protection: { label: "Protection", blurb: "guards the board against static and bad cables" },
  passive: { label: "Supporting parts", blurb: "resistors, capacitors and diodes that make the rest behave" },
  mechanical: { label: "Holes and test points", blurb: "for screws and for probing" },
  other: { label: "Other parts", blurb: "" },
});

// Manufacturer-part patterns worth naming. Deliberately conservative: these
// are families anyone in the field would recognise on sight, and a miss simply
// falls through to the ftype, which came out of the source file and is always
// true.
const MPN_ROLES = [
  [/^(RP2040|RP2350)/i, "brain"],
  [/ESP32|ESP8266/i, "brain"],
  [/^STM32|^GD32|^CH32|^APM32/i, "brain"],
  [/^ATMEGA|^ATTINY|^ATSAM|^SAMD/i, "brain"],
  [/^NRF5|^NRF52|^NRF91/i, "brain"],
  [/^RA4M|^MSP430|^PIC(16|18|32)/i, "brain"],
  [/AMS1117|LM1117|LD1117|XC6206|RT9013|ME6211|HT73|TPS6|MP15|MP23|SGM2212|AP2112|NCP170/i, "power_reg"],
  [/TP4056|BQ2405|MCP7383|IP5306|CN3065/i, "power_reg"],
  [/^W25Q|^GD25|^MX25|^AT24C|^24LC|^IS25|^EN25/i, "memory"],
  [/USBLC|^ESDA|^SP0503|^PESD|^SRV05|^ULC/i, "protection"],
  [/WS2812|SK6812|APA102|SK9822/i, "indicator"],
  [/BME280|BMP280|BME680|SHT[234]|SHTC3|AHT[12]|DHT[12]|SCD4|SGP[34]|CCS811/i, "sensor"],
  [/MPU6|ICM2|LSM6|LIS3|BMI[12]|QMC5883|HMC5883/i, "sensor"],
  [/VL53|TOF|APDS9|VEML|BH1750|TSL25/i, "sensor"],
  [/TMP1[01]|DS18B20|LM75|MAX31855|MAX6675|INA2[12]|ACS712/i, "sensor"],
  [/DRV8|TB6612|L293|A4988|TMC2|BTS7960|MOSFET DRIVER/i, "driver"],
  [/MAX98|PAM8|TPA2|NS4168/i, "driver"],
  [/^NRF24|^RFM9|^SX12|^LORA|^SIM800|^SIM7|^EC200/i, "radio"],
  [/TYPE-C|USB-?C|USB4110|^HRO-TYPE|MICRO-?USB|^USB[- ]?A/i, "power_in"],
];

const FTYPE_ROLES = {
  simple_resistor: "passive",
  simple_capacitor: "passive",
  simple_inductor: "passive",
  simple_diode: "passive",
  simple_fuse: "protection",
  simple_led: "indicator",
  simple_push_button: "control",
  simple_switch: "control",
  simple_rotary_encoder: "control",
  simple_potentiometer: "control",
  simple_crystal: "clock",
  simple_resonator: "clock",
  simple_oscillator: "clock",
  simple_connector: "power_in",
  simple_pin_header: "power_in",
  simple_battery: "power_in",
  simple_test_point: "mechanical",
  simple_mosfet: "driver",
  simple_transistor: "driver",
  simple_chip: "other",
};

// Refdes prefixes, the last resort before "other". IEEE-315 letters.
const REFDES_ROLES = {
  R: "passive",
  C: "passive",
  L: "passive",
  D: "passive",
  LED: "indicator",
  U: "other",
  IC: "other",
  Q: "driver",
  J: "power_in",
  P: "power_in",
  SW: "control",
  BTN: "control",
  Y: "clock",
  X: "clock",
  F: "protection",
  TP: "mechanical",
  H: "mechanical",
  MH: "mechanical",
  B: "power_in",
  BT: "power_in",
};

/** "LED12" → "LED"; "R1" → "R"; "" when the name has no letter prefix. */
export function refdesPrefix(refdes) {
  const match = /^([A-Za-z]+)\d*$/.exec(String(refdes || "").trim());
  return match ? match[1].toUpperCase() : "";
}

/**
 * The role of one component. MPN first (it identifies the actual part), then
 * the source file's own `ftype`, then the reference-designator letter.
 *
 * @returns {{role: string, label: string, blurb: string, why: "mpn"|"ftype"|"refdes"|"none"}}
 */
export function partRole(component) {
  const mpn = String(component?.mpn || "").trim();
  if (mpn) {
    for (const [pattern, role] of MPN_ROLES) {
      if (pattern.test(mpn)) return { role, ...ROLES[role], why: "mpn" };
    }
  }
  const ftype = String(component?.ftype || "").trim().toLowerCase();
  if (ftype && FTYPE_ROLES[ftype] && FTYPE_ROLES[ftype] !== "other") {
    const role = FTYPE_ROLES[ftype];
    return { role, ...ROLES[role], why: "ftype" };
  }
  const prefix = refdesPrefix(component?.refdes);
  if (prefix && REFDES_ROLES[prefix] && REFDES_ROLES[prefix] !== "other") {
    const role = REFDES_ROLES[prefix];
    return { role, ...ROLES[role], why: "refdes" };
  }
  return { role: "other", ...ROLES.other, why: "none" };
}

/**
 * The one-line description of a single part: what it is, in the words a
 * non-engineer would use, with the real part number kept beside it so an
 * engineer can check the claim.
 */
export function partPlainName(component) {
  const mpn = String(component?.mpn || "").trim();
  const value = String(component?.value || "").trim();
  const ftype = String(component?.ftype || "").replace(/^simple_/, "").replace(/_/g, " ");
  const role = partRole(component);
  if (role.why === "mpn") {
    const singular = {
      brain: "Microcontroller",
      power_reg: "Voltage regulator",
      memory: "Flash memory",
      protection: "Protection chip",
      indicator: "Addressable RGB LED",
      sensor: "Sensor",
      driver: "Driver chip",
      radio: "Radio module",
      power_in: "USB-C socket",
    }[role.role];
    if (singular) return singular;
  }
  if (value) return `${capitalize(ftype || "Part")} ${value}`;
  if (mpn) return mpn;
  return capitalize(ftype || "Part");
}

function capitalize(text) {
  const value = String(text || "");
  return value ? value[0].toUpperCase() + value.slice(1) : "";
}

/**
 * The whole board as a plain-English part list, grouped by role and ordered so
 * the interesting things come first: the brain, then power, then everything it
 * talks to, and the resistors and capacitors last where they belong.
 *
 * @param {object|null} index buildBoardIndex output
 * @returns {Array<{role,label,blurb,count,items:Array<{key,refdes,name,mpn,value}>}>}
 */
export function plainParts(index) {
  const components = Array.isArray(index?.components) ? index.components : [];
  const buckets = new Map();
  for (const component of components) {
    const role = partRole(component);
    let bucket = buckets.get(role.role);
    if (!bucket) {
      bucket = { role: role.role, label: role.label, blurb: role.blurb, count: 0, items: [] };
      buckets.set(role.role, bucket);
    }
    bucket.count += 1;
    bucket.items.push({
      key: component.key,
      refdes: component.refdes,
      name: partPlainName(component),
      mpn: String(component.mpn || ""),
      value: String(component.value || ""),
      lcsc: String(component.lcsc || ""),
    });
  }
  for (const bucket of buckets.values()) {
    bucket.items.sort((a, b) =>
      String(a.refdes).localeCompare(String(b.refdes), undefined, { numeric: true, sensitivity: "base" }),
    );
  }
  return ROLE_ORDER.map((role) => buckets.get(role)).filter(Boolean);
}

/**
 * "A 70 × 70 mm two-layer board with 58 parts on it." — the size sentence,
 * built only from numbers the sidecar and the index actually carry.
 */
export function boardShapeLine({ sidecar = null, index = null } = {}) {
  const width = Number(sidecar?.board?.widthMm);
  const height = Number(sidecar?.board?.heightMm);
  const layers = Number(sidecar?.board?.layers);
  const parts = Array.isArray(index?.components) ? index.components.length : 0;
  const bits = [];
  if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
    bits.push(`${trimNumber(width)} × ${trimNumber(height)} mm`);
  }
  if (Number.isFinite(layers) && layers > 0) bits.push(layers === 2 ? "2 copper layers" : `${layers} copper layers`);
  if (parts) bits.push(plural(parts, "part"));
  return bits.join(" · ");
}

function trimNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/**
 * Parts cost per board from the parts-book lock: unit price × how many of that
 * part the board uses. Returns null when parts.json has no prices — an
 * estimate we cannot source is worse than no number at all.
 *
 * @param {Array<object>} parts normalizeParts() output, with `refdes` preserved
 * @param {object|null} index buildBoardIndex output, for the placement count
 */
export function partsCostUsd(parts, index) {
  const list = Array.isArray(parts) ? parts : [];
  if (!list.length) return null;
  let total = 0;
  let priced = 0;
  let unpriced = 0;
  for (const part of list) {
    const count = Array.isArray(part?.refdes) && part.refdes.length ? part.refdes.length : 1;
    const price = Number(part?.unitPriceUsd);
    if (Number.isFinite(price) && price > 0) {
      total += price * count;
      priced += 1;
    } else {
      unpriced += 1;
    }
  }
  if (!priced) return null;
  return { usd: total, priced, unpriced, complete: unpriced === 0, index: index ? true : false };
}
