import assert from "node:assert/strict";
import test from "node:test";
import {
  IMPACT,
  boardShapeLine,
  boardVerdict,
  groupFindings,
  groupFixRequest,
  impactCounts,
  issueCode,
  issueDetailText,
  joinWords,
  partPlainName,
  partRole,
  partsCostUsd,
  plainIssue,
  plainParts,
  plural,
  refdesPrefix,
} from "./plainLanguage.js";

const row = (over = {}) => ({ part: "", kind: "drc_violation", detail: "", severity: "warning", ...over });

test("issueCode reads KiCad's bracketed code, else the pipeline kind", () => {
  assert.equal(issueCode(row({ detail: "[hole_clearance] Hole clearance violation (…)" })), "hole_clearance");
  assert.equal(issueCode(row({ detail: "PCB trace is 14mm long", kind: "pcb_trace_too_long_warning" })), "pcb_trace_too_long_warning");
  assert.equal(issueCode(row({ detail: "", kind: "" })), "");
});

test("issueDetailText drops the code so the sentence reads on its own", () => {
  assert.equal(issueDetailText(row({ detail: "[clearance] Clearance violation (0.09mm)" })), "Clearance violation (0.09mm)");
});

test("plainIssue names a known code and admits an unknown one", () => {
  const known = plainIssue("hole_clearance");
  assert.equal(known.known, true);
  assert.equal(known.impact, IMPACT.BLOCKS);
  assert.match(known.title, /hole/i);

  const unknown = plainIssue("some_new_check");
  assert.equal(unknown.known, false);
  assert.equal(unknown.title, "some new check");
  assert.equal(unknown.meaning, "", "an unknown code gets no invented meaning");
});

test("groupFindings collapses a wall of rows into issues, blocking first", () => {
  const rows = [
    ...Array.from({ length: 400 }, (_, i) => row({ severity: "info", part: `R${i}`, detail: "[text_height] Text height out of range" })),
    row({ severity: "error", part: "Track [GND]", detail: "[hole_clearance] Hole clearance violation" }),
    row({ severity: "warning", part: "C1", detail: "[footprint_symbol_mismatch] does not match" }),
    row({ severity: "error", part: "Track [V5]", detail: "[hole_clearance] Hole clearance violation" }),
  ];
  const groups = groupFindings(rows);
  assert.equal(groups.length, 3);
  assert.equal(groups[0].code, "hole_clearance");
  assert.equal(groups[0].count, 2);
  assert.equal(groups[0].blocking, true);
  assert.equal(groups[0].severity, "error");
  assert.deepEqual(groups[0].parts, ["Track [GND]", "Track [V5]"]);
  assert.equal(groups[0].sample, "Hole clearance violation");
  // 400 cosmetic rows never outrank two blocking ones, and checker noise
  // (a mismatch between two files) sorts below a cosmetic print problem.
  assert.deepEqual(groups.map((g) => g.code), [
    "hole_clearance",
    "text_height",
    "footprint_symbol_mismatch",
  ]);
});

test("impactCounts separates what stops an order from what is noise", () => {
  const rows = [
    row({ severity: "error", detail: "[clearance] x" }),
    ...Array.from({ length: 10 }, () => row({ severity: "info", detail: "[lib_symbol_issues] x" })),
    ...Array.from({ length: 5 }, () => row({ severity: "info", detail: "[silk_overlap] x" })),
  ];
  const counts = impactCounts(groupFindings(rows));
  assert.deepEqual(counts, { blocks: 1, quality: 0, cosmetic: 5, tooling: 10, total: 16 });
});

test("boardVerdict gates on fab.ready and nothing else", () => {
  const groups = groupFindings([row({ severity: "error", part: "Y1", detail: "[clearance] Clearance violation" })]);

  // Errors outstanding but the packet says ready: still ready. fab.ready is
  // the authority — this asserts we never second-guess it upward OR downward.
  const ready = boardVerdict({ sidecar: { fab: { ready: true } }, groups: [] });
  assert.equal(ready.tone, "ready");
  assert.match(ready.headline, /Ready to order/);
  assert.match(ready.line, /Nothing outstanding/);

  // A ready board that still carries cosmetic notes says so rather than
  // claiming a clean sheet the user can see is not clean.
  const readyWithNotes = boardVerdict({
    sidecar: { fab: { ready: true } },
    groups: groupFindings([row({ severity: "info", detail: "[silk_overlap] x" })]),
  });
  assert.match(readyWithNotes.line, /1 note left in the checks/);

  const blocked = boardVerdict({ sidecar: { fab: { ready: false } }, groups });
  assert.equal(blocked.tone, "blocked");
  assert.equal(blocked.headline, "Not orderable yet — 1 thing to fix");
  assert.match(blocked.line, /copper/i);
  assert.equal(blocked.blockingCount, 1);

  assert.equal(boardVerdict({ sidecar: null }).tone, "unknown");
  assert.equal(boardVerdict({ sidecar: null, building: true }).tone, "building");
});

test("boardVerdict never says orderable when the packet is not ready and nothing is named", () => {
  const verdict = boardVerdict({ sidecar: { fab: { ready: false } }, groups: [] });
  assert.equal(verdict.tone, "blocked");
  assert.equal(verdict.headline, "Not orderable yet");
  assert.doesNotMatch(verdict.line, /ready/i);
});

test("groupFixRequest carries the plain words AND the exact code", () => {
  const [group] = groupFindings([
    row({ severity: "error", part: "Track [GND]", detail: "[hole_clearance] Hole clearance violation (0.115mm)" }),
  ]);
  const text = groupFixRequest(group, { board: "main" });
  assert.match(text, /hole_clearance/);
  assert.match(text, /on main/);
  assert.match(text, /Track \[GND\]/);
  assert.match(text, /rebuild/i);
});

test("plural and joinWords keep the copy readable", () => {
  assert.equal(plural(1, "thing"), "1 thing");
  assert.equal(plural(3, "thing"), "3 things");
  assert.equal(joinWords(["a"]), "a");
  assert.equal(joinWords(["a", "b"]), "a and b");
  assert.equal(joinWords(["a", "b", "c"]), "a, b and c");
});

test("partRole reads the part number first, then ftype, then the refdes", () => {
  assert.equal(partRole({ mpn: "RP2040", ftype: "simple_chip", refdes: "U3" }).role, "brain");
  assert.equal(partRole({ mpn: "AMS1117-3.3", ftype: "simple_chip", refdes: "U2" }).role, "power_reg");
  assert.equal(partRole({ mpn: "W25Q128JVSIQ", ftype: "simple_chip", refdes: "U4" }).role, "memory");
  assert.equal(partRole({ mpn: "USBLC6-2SC6", ftype: "simple_chip", refdes: "U1" }).role, "protection");
  assert.equal(partRole({ mpn: "WS2812B-B/T", ftype: "simple_chip", refdes: "D10" }).role, "indicator");
  assert.equal(partRole({ mpn: "TYPE-C-31-M-12", ftype: "simple_connector", refdes: "J1" }).role, "power_in");
  assert.equal(partRole({ mpn: "", ftype: "simple_push_button", refdes: "SW1" }).role, "control");
  assert.equal(partRole({ mpn: "", ftype: "simple_crystal", refdes: "Y1" }).role, "clock");
  assert.equal(partRole({ mpn: "", ftype: "simple_capacitor", refdes: "C4" }).role, "passive");
  // An unknown chip stays honestly "other" rather than being guessed into a role.
  assert.equal(partRole({ mpn: "XYZ999", ftype: "simple_chip", refdes: "U9" }).role, "other");
});

test("partPlainName says what a part is without dropping the real number", () => {
  assert.equal(partPlainName({ mpn: "RP2040", ftype: "simple_chip" }), "Microcontroller");
  assert.equal(partPlainName({ mpn: "", ftype: "simple_resistor", value: "10kΩ" }), "Resistor 10kΩ");
  assert.equal(partPlainName({ mpn: "1N4148W", ftype: "simple_diode" }), "1N4148W");
});

test("refdesPrefix reads the designator letter", () => {
  assert.equal(refdesPrefix("LED12"), "LED");
  assert.equal(refdesPrefix("R1"), "R");
  assert.equal(refdesPrefix("weird name"), "");
});

test("plainParts groups a board brain-first and passives last", () => {
  const index = {
    components: [
      { key: "c1", refdes: "C1", ftype: "simple_capacitor", mpn: "", value: "100nF" },
      { key: "u3", refdes: "U3", ftype: "simple_chip", mpn: "RP2040" },
      { key: "j1", refdes: "J1", ftype: "simple_connector", mpn: "TYPE-C-31-M-12" },
      { key: "c2", refdes: "C2", ftype: "simple_capacitor", mpn: "", value: "1uF" },
    ],
  };
  const groups = plainParts(index);
  assert.deepEqual(groups.map((g) => g.role), ["brain", "power_in", "passive"]);
  assert.equal(groups[2].count, 2);
  assert.deepEqual(groups[2].items.map((i) => i.refdes), ["C1", "C2"]);
});

test("boardShapeLine only states numbers the files carry", () => {
  const line = boardShapeLine({
    sidecar: { board: { widthMm: 70, heightMm: 70, layers: 2 } },
    index: { components: [{}, {}, {}] },
  });
  assert.equal(line, "70 × 70 mm · 2 copper layers · 3 parts");
  assert.equal(boardShapeLine({}), "");
});

test("partsCostUsd multiplies by placements and reports what it could not price", () => {
  const cost = partsCostUsd(
    [
      { unitPriceUsd: 0.2, refdes: ["U2", "U5"] },
      { unitPriceUsd: 1.5, refdes: ["U3"] },
      { unitPriceUsd: null, refdes: ["Y1"] },
    ],
    null,
  );
  assert.equal(cost.usd.toFixed(2), "1.90");
  assert.equal(cost.priced, 2);
  assert.equal(cost.unpriced, 1);
  assert.equal(cost.complete, false);
  assert.equal(partsCostUsd([], null), null, "no parts.json means no invented number");
});

// --- a build that failed outranks the sidecar -------------------------------
//
// Seen in a real run: the tree said "Build stopped responding" while the
// verdict strip three inches away said "No build to judge yet". Worse on a
// project whose earlier build succeeded — a stale sidecar would have kept
// saying "Ready to order" after a crash.

test("a failed build is the verdict, even when an older sidecar says ready", () => {
  const verdict = boardVerdict({
    sidecar: { fab: { ready: true } },
    groups: [],
    buildLine: { tone: "failed", text: "Build failed", detail: "compile error" },
  });
  assert.equal(verdict.tone, "failed");
  assert.match(verdict.headline, /last build failed/);
  assert.match(verdict.line, /this is the build before it/);
});

test("a stopped build with no sidecar says nothing came out of it", () => {
  const verdict = boardVerdict({ sidecar: null, buildLine: { tone: "stale", text: "", detail: "" } });
  assert.equal(verdict.tone, "failed");
  assert.match(verdict.line, /No board came out of it/);
});

test("a running build still outranks a failure line from the build before", () => {
  const verdict = boardVerdict({ building: true, buildLine: { tone: "failed" } });
  assert.equal(verdict.tone, "building");
});

test("with no build line the verdict is unchanged", () => {
  assert.equal(boardVerdict({ sidecar: { fab: { ready: true } } }).tone, "ready");
  assert.equal(boardVerdict({ sidecar: null }).tone, "unknown");
});

test("a finished build with no board file says which fact is which", () => {
  // Seen in a real run: "Built · 6m 49s · 1 blocking" in the tree next to "No
  // build to judge yet" in the strip. Both were true — the agent's check runs
  // in a scratch copy — and together they read as a contradiction.
  const verdict = boardVerdict({ sidecar: null, buildLine: { tone: "done", text: "Built" } });
  assert.equal(verdict.tone, "unknown");
  assert.match(verdict.line, /no board file has landed in this project/);
});

// Watched on a real first build: six issues were listed as what was left to
// fix and four of them read "pcb trace missing error ×37 — we do not have
// plain words for this check yet". These are the codes the generator emits
// most often; every one of them has to arrive as a sentence.
test("the generator's most common errors all have plain words", () => {
  const seen = [
    "pcb_footprint_overlap_error",
    "pcb_courtyard_overlap_error",
    "pcb_pad_pad_clearance_error",
    "pcb_component_outside_board_error",
    "pcb_placement_error",
    "pcb_autorouting_error",
    "pcb_trace_missing_error",
    "pcb_trace_not_connected_error",
    "pcb_port_not_connected_error",
    "pcb_port_not_matched_error",
    "pcb_missing_footprint_error",
    "source_trace_not_connected_error",
    "pcb_trace_clearance_error",
    "pcb_pad_trace_clearance_error",
    "pcb_via_clearance_error",
    "pcb_via_trace_clearance_error",
  ];
  for (const code of seen) {
    const issue = plainIssue(code);
    assert.equal(issue.known, true, `${code} has no plain entry`);
    assert.ok(issue.meaning.length > 20, `${code} has no explanation`);
    assert.equal(issue.impact, IMPACT.BLOCKS, `${code} should block the order`);
  }
});

// The fab limits are the other half of what stops a board being orderable.
test("every factory-limit check has plain words", () => {
  const dfm = [
    "dfm_trace_width",
    "dfm_trace_clearance",
    "dfm_edge_clearance",
    "dfm_hole_clearance",
    "dfm_drill_size",
    "dfm_via_diameter",
    "dfm_annular_ring",
    "dfm_board_size",
    "dfm_thickness",
  ];
  for (const code of dfm) {
    const issue = plainIssue(code);
    assert.equal(issue.known, true, `${code} has no plain entry`);
    assert.ok(issue.title.length > 10, `${code} has no title`);
  }
});

test("plain words stay plain — no code, no term of art in a title", () => {
  const jargon = /gerber|netlist|refdes|DRC|ERC|footprint\b|courtyard|annular|via\b|_/;
  for (const code of ["pcb_autorouting_error", "pcb_trace_missing_error", "dfm_trace_width", "dfm_via_diameter"]) {
    assert.doesNotMatch(plainIssue(code).title, jargon, code);
  }
});

test("an unknown code still fails safe — named, not invented", () => {
  const issue = plainIssue("pcb_something_new_error");
  assert.equal(issue.known, false);
  assert.equal(issue.meaning, "", "an unknown code must not be given a made-up meaning");
  assert.equal(issue.title, "pcb something new error");
});
