import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  IMPACT,
  boardShapeLine,
  boardVerdict,
  buildRequest,
  buildWaitNote,
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
  shortPartName,
  plainParts,
  plural,
  refdesPrefix,
} from "./plainLanguage.js";

const REPO = path.resolve(fileURLToPath(new URL("../../../..", import.meta.url)));

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

test("a repair that declined does not read as a check that could not run", () => {
  // weather-badge-12 and -13, 2026-08-18: both fab.ready with zero errors,
  // and both showed "a check could not finish" marked blocking. The pipeline
  // never blocked on it — `check_failed` is in neither policy set — so the app
  // was contradicting the verdict it was rendering.
  const declined = plainIssue("repair_declined");
  assert.equal(declined.known, true);
  assert.notEqual(declined.impact, IMPACT.BLOCKS);
  assert.doesNotMatch(declined.meaning, /not examined|absence/i);

  // The row above keeps its teeth: a step that truly did not run is still a
  // reason not to ship, and splitting these must not soften it.
  assert.equal(plainIssue("check_failed").impact, IMPACT.BLOCKS);
});

test("a declined repair never marks its group blocking", () => {
  const [group] = groupFindings([
    row({ kind: "repair_declined", severity: "info", detail: "the dead-end(s) on net(s) 2 were left as the router laid them" }),
  ]);
  assert.equal(group.code, "repair_declined");
  assert.equal(group.blocking, false);

  const [failed] = groupFindings([
    row({ kind: "check_failed", severity: "warning", detail: "could not read board.kicad_pcb" }),
  ]);
  assert.equal(failed.blocking, true);
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

// --- every dead end has an exit --------------------------------------------
//
// Watched on a real run: a project holding boards/main.tsx with no build
// behind it. The strip said "No build to judge yet", every tab was empty, no
// turn was running, and nothing on the screen said what would make a build
// happen. True, and a dead end.

test("a board with no build offers the build, in words ready for the chat", () => {
  const verdict = boardVerdict({ sidecar: null, boardName: "main" });
  assert.equal(verdict.tone, "unknown");
  assert.equal(verdict.action.label, "Build it");
  assert.match(verdict.action.request, /^Build boards\/main\.tsx and run the checks\./);
  // A turn that already stopped once has to be asked for the reason, or it
  // stops silently again.
  assert.match(verdict.action.request, /say what stopped it/);
});

test("a failed or stopped build offers the retry rather than describing it", () => {
  for (const tone of ["failed", "stale"]) {
    const verdict = boardVerdict({ sidecar: null, buildLine: { tone }, boardName: "main" });
    assert.equal(verdict.action.label, "Build it again");
    // The instruction moved from the prose into the button.
    assert.doesNotMatch(verdict.line, /Ask the chat/);
  }
});

test("nothing offers to start a build while a turn is running", () => {
  // The pipeline writes its first status record a minute into a build turn,
  // so `building` is false for the whole opening stretch — long enough that
  // "Build it" appeared under a board that was being built as you read it.
  const verdict = boardVerdict({ sidecar: null, turnActive: true, boardName: "main" });
  assert.equal(verdict.action, null);
  assert.match(verdict.line, /The chat is working/);
});

test("a finished build that left nothing here offers to build it here", () => {
  const verdict = boardVerdict({ sidecar: null, buildLine: { tone: "done" }, boardName: "main" });
  assert.equal(verdict.action.label, "Build it here");
});

test("a built board offers no build button — ready and blocked have their own", () => {
  assert.equal(boardVerdict({ sidecar: { fab: { ready: true } } }).action, null);
  assert.equal(boardVerdict({ sidecar: { fab: { ready: false } } }).action, null);
  assert.equal(boardVerdict({ building: true }).action, null);
});

// Nine minutes into a real build the board pane read "Compiling the board ·
// 1/7" — the same words it showed at second one. `compile` routes the board
// and its quiet limit is 45 minutes, so nothing else was ever going to change.
test("only the stage measured going long earns a reassuring sentence", () => {
  assert.match(buildWaitNote({ stage: "compile", elapsedS: 600 }), /fifteen minutes/);
  // Under two minutes the clock alone is enough.
  assert.equal(buildWaitNote({ stage: "compile", elapsedS: 30 }), "");
  // Every other stage stays silent rather than reassuring without evidence.
  for (const stage of ["scan", "checks", "substrate", "dfm", "export", "render", ""]) {
    assert.equal(buildWaitNote({ stage, elapsedS: 900 }), "", `${stage} says nothing`);
  }
  assert.equal(buildWaitNote(), "");
});

test("a build in flight no longer promises a minute and a half", () => {
  const verdict = boardVerdict({ building: true });
  assert.doesNotMatch(verdict.line, /minute and a half/);
});

test("the build request names the board, and falls back to main", () => {
  assert.match(buildRequest("blinky"), /boards\/blinky\.tsx/);
  assert.match(buildRequest(""), /boards\/main\.tsx/);
  assert.match(buildRequest(null), /boards\/main\.tsx/);
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

// KiCad names its own objects for KiCad. On a real board three of the four
// chips under one issue read `Track [DVDD] on F.Cu, length 0.2973 mm`, which
// turns the "where" line into noise — and F.Cu is not a thing a first-timer
// can be expected to know.
test("shortPartName keeps designators and shortens KiCad's own sentences", () => {
  assert.equal(shortPartName("U3"), "U3");
  assert.equal(shortPartName("C6"), "C6");
  assert.equal(shortPartName("J1.B4A9"), "J1.B4A9");
  assert.equal(shortPartName("Track [DVDD] on F.Cu, length 0.2973 mm"), "track DVDD");
  assert.equal(shortPartName("Via [GND] on F.Cu - B.Cu"), "hole GND");
  assert.equal(shortPartName("Pad 2 [VBUS] of J1"), "J1.2");
  assert.equal(shortPartName("Zone [GND] on B.Cu"), "copper fill GND");
  assert.equal(shortPartName(""), "");
  assert.equal(shortPartName(null), "");
});

test("grouped findings carry the short names", () => {
  const groups = groupFindings([
    { severity: "error", kind: "clearance", part: "Track [DVDD] on F.Cu, length 0.2973 mm", detail: "[clearance] too close" },
    { severity: "error", kind: "clearance", part: "U3", detail: "[clearance] too close" },
  ]);
  assert.deepEqual(groups[0].parts, ["track DVDD", "U3"]);
});

// The dictionary is measured against the boards, not against memory.
//
// A round-4 discoverability judge counted the app's own demo board: 16 of the
// 36 finding codes hydrate-coaster reports had no words and rendered as raw
// identifiers — `supplier footprint mismatch warning`, 27 times over. A
// finding an engineer cannot read is one they learn to scroll past, and every
// unmapped code there was **ours**: nobody else was going to name them.
//
// So this walks every sidecar in the repo and fails on a kind the pipeline
// actually emits with no plain-language entry behind it. It cannot go stale
// the way a hand-written list does, and a new check kind cannot ship mute.
test("every finding kind the fleet emits has words behind it", () => {
  const roots = [path.join(REPO, "examples"), path.join(REPO, "products")];
  const seen = new Map(); // code → count
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const name of fs.readdirSync(root)) {
      const sidecar = path.join(root, name, "boards", "main.board.json");
      if (!fs.existsSync(sidecar)) continue;
      let data;
      try {
        data = JSON.parse(fs.readFileSync(sidecar, "utf8"));
      } catch {
        continue;
      }
      for (const row of (data.validation || {}).warnings || []) {
        const code = issueCode(row);
        if (code) seen.set(code, (seen.get(code) || 0) + 1);
      }
    }
  }
  assert.ok(seen.size > 10, `only ${seen.size} kinds found across the fleet — the walk is wrong`);

  const mute = [...seen.entries()]
    .filter(([code]) => !plainIssue(code).known)
    .sort((a, b) => b[1] - a[1]);
  assert.deepEqual(
    mute.map(([code, count]) => `${code} (${count}×)`),
    [],
    "these finding kinds render as raw identifiers in the app; give them a title and a meaning in ISSUES",
  );
});
