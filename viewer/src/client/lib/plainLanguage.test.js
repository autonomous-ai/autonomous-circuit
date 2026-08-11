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
