import assert from "node:assert/strict";
import test from "node:test";
import {
  ASK,
  connectionList,
  connectionName,
  count,
  headingWords,
  helpCard,
  helpCards,
  helpRequestAll,
  helpVerdict,
  mm,
  readRoutingHelp,
} from "./routingHelp.js";

// A real shape, trimmed: this is what diagnose.py writes for hydrate-coaster.
const MOVE_ASK = {
  kind: "move_part",
  nets: ["LED_NUDGE"],
  headline: "1 net cannot get through the 0.05mm gap between DVDD and U3",
  at: [-20.78, -23.68],
  layer: "top",
  pinch: {
    at: [-20.78, -23.68],
    layer: "top",
    between: [
      { label: "DVDD", kind: "trace", owner: "net:x", part: "" },
      { label: "U3", kind: "pad", owner: "U3", part: "U3" },
    ],
    gapMm: 0.251,
    usableMm: 0.05,
  },
  neededMm: 0.2,
  proven: true,
  move: {
    part: "U3",
    dxMm: 0,
    dyMm: 0.2,
    distanceMm: 0.2,
    heading: "north",
    afterUsableMm: 0.25,
    headroomMm: 0.4,
    caveat: "re-measured after the move; whether the net then routes was not",
  },
  congestion: null,
  evidence: ["DVDD and U3 come within 0.251mm of each other"],
};

const sidecar = (help) => ({ build: { routingHelp: help } });

const ranHelp = (over = {}) => ({
  ran: true,
  routableNets: 32,
  connectedNets: 28,
  unroutedNets: 4,
  resolutionMm: 0.04,
  seconds: 9.2,
  nets: [],
  asks: [MOVE_ASK],
  notes: [],
  ...over,
});

test("mm prints the sidecar's number and never more precision", () => {
  assert.equal(mm(0.2), "0.2mm");
  assert.equal(mm(0.05), "0.05mm");
  assert.equal(mm(-0.093), "-0.09mm");
  assert.equal(mm(0), "0mm");
  assert.equal(mm(undefined), "");
  assert.equal(mm("nonsense"), "");
});

test("count pluralises without a library", () => {
  assert.equal(count(1, "connection"), "1 connection");
  assert.equal(count(4, "connection"), "4 connections");
  assert.equal(count(0, "connection"), "0 connections");
});

test("a generated name that means nothing is not printed as a name", () => {
  assert.equal(connectionName("USB_DP"), "USB_DP");
  assert.equal(connectionName("net22"), "an unnamed connection");
  assert.equal(connectionName(""), "an unnamed connection");
  assert.equal(connectionList(["V3_3", "net9", "USB_DM"]),
    "V3_3, an unnamed connection and USB_DM");
});

test("compass headings become directions on a screen", () => {
  assert.equal(headingWords("north"), "up the board");
  assert.equal(headingWords("south-west"), "down and to the left");
  assert.equal(headingWords(""), "");
});

test("readRoutingHelp ignores everything that is not a diagnosis", () => {
  assert.equal(readRoutingHelp(null), null);
  assert.equal(readRoutingHelp({}), null);
  assert.equal(readRoutingHelp({ build: {} }), null);
});

test("a diagnosis that did not run says so instead of implying a clean board", () => {
  const help = readRoutingHelp(sidecar({ ran: false, reason: "switched off" }));
  assert.equal(help.ran, false);
  assert.equal(help.reason, "switched off");
  assert.deepEqual(help.asks, []);
  assert.equal(helpVerdict(help), null);
});

test("asks are ordered so the one with a measured answer comes first", () => {
  const help = readRoutingHelp(
    sidecar(ranHelp({
      asks: [
        { kind: "unattributed", nets: ["a"], evidence: [] },
        { kind: "router_limit", nets: ["b"], evidence: [] },
        MOVE_ASK,
        { kind: "tight_gap", nets: ["c"], evidence: [], pinch: null },
      ],
    })),
  );
  assert.deepEqual(help.asks.map((a) => a.kind),
    ["move_part", "tight_gap", "router_limit", "unattributed"]);
});

test("a tried move is reported as a measurement, with both numbers", () => {
  const card = helpCard(MOVE_ASK, { board: "main" });
  assert.equal(card.kind, ASK.MOVE);
  assert.equal(card.tone, "decision");
  assert.equal(card.title, "Move U3 0.2mm up the board");
  assert.match(card.body, /0\.05mm of space and 0\.2mm is needed/);
  assert.match(card.body, /measured again/);
  assert.match(card.body, /becomes 0\.25mm/);
  assert.match(card.action.request, /^Move U3 0\.2mm up the board/);
  assert.match(card.action.request, /on main/);
  assert.match(card.action.request, /rebuild/);
});

test("no card invents a number the sidecar does not carry", () => {
  const card = helpCard({ kind: "tight_gap", nets: ["V3_3"], evidence: [] });
  // usable and needed are absent, so they render empty rather than as zero.
  assert.doesNotMatch(card.body, /0mm/);
  assert.equal(card.nets.length, 1);
});

test("a gap nothing could open asks for a decision, not a fix", () => {
  const card = helpCard({
    kind: "tight_gap",
    nets: ["USB_DP"],
    neededMm: 0.2,
    pinch: { between: [{ label: "J1" }, { label: "a no-copper area" }], usableMm: -0.09 },
    evidence: ["that no-copper area is J1's own"],
  });
  assert.equal(card.title, "1 connection cannot fit between J1 and a no-copper area");
  assert.match(card.body, /-0\.09mm of space/);
  assert.match(card.action.request, /What are the options/);
  assert.match(card.action.request, /do not guess/);
});

test("copper in the way is a re-route request and moves no part", () => {
  const card = helpCard({
    kind: "reroute",
    nets: ["V3_3"],
    neededMm: 0.5,
    pinch: { between: [{ label: "DVDD" }, { label: "GND" }], usableMm: 0.088 },
    evidence: [],
  });
  assert.match(card.title, /already drawn are in the way/);
  assert.match(card.body, /No part has to move/);
  assert.match(card.action.request, /Re-route DVDD or GND/);
});

test("room but no copper is the router's problem and says so", () => {
  const card = helpCard({
    kind: "router_limit",
    nets: ["SCL", "SDA"],
    neededMm: 0.1,
    evidence: ["nothing on the board has to move for these"],
  });
  assert.match(card.title, /2 connections had room but were not drawn/);
  assert.match(card.body, /Nothing has to move/);
  assert.match(card.action.request, /working harder/);
});

test("an unexplained failure keeps its evidence and still offers a next step", () => {
  const card = helpCard({
    kind: "unattributed",
    nets: ["net21"],
    neededMm: 0.1,
    evidence: ["the widest channel found is 0.04mm against the net's 0.2mm"],
  });
  assert.equal(card.tone, "note");
  assert.match(card.title, /cannot say why/);
  assert.match(card.body, /not going to guess/);
  assert.ok(card.action, "a dead end still needs an exit");
  assert.match(card.action.request, /the widest channel found is 0\.04mm/);
  assert.match(card.action.request, /say so rather than guessing/);
});

test("the verdict line leads with the decision and counts the rest", () => {
  const help = readRoutingHelp(
    sidecar(ranHelp({ asks: [MOVE_ASK, { kind: "reroute", nets: ["V3_3"], evidence: [] }] })),
  );
  const verdict = helpVerdict(help, { board: "main" });
  assert.match(verdict.headline, /^4 connections missing/);
  assert.match(verdict.headline, /one decision would help/);
  assert.match(verdict.line, /^Move U3 0\.2mm up the board\./);
  assert.match(verdict.line, /\(1 more like this\.\)/);
  assert.equal(verdict.action.label, "Move U3");
});

test("every card's request survives into the ask-about-all-of-it message", () => {
  const help = readRoutingHelp(
    sidecar(ranHelp({ asks: [MOVE_ASK, { kind: "unattributed", nets: ["net9"], evidence: [] }] })),
  );
  const text = helpRequestAll(help, { board: "main" });
  assert.equal(text.split("\n\n").length >= 2, true);
  assert.match(text, /Move U3/);
  assert.match(text, /could not find the cause/);
});

test("no card uses a word that needs an electronics background", () => {
  const help = readRoutingHelp(
    sidecar(ranHelp({
      asks: [
        MOVE_ASK,
        { kind: "reroute", nets: ["V3_3"], neededMm: 0.5,
          pinch: { between: [{ label: "A" }, { label: "B" }], usableMm: 0.1 }, evidence: [] },
        { kind: "router_limit", nets: ["X"], neededMm: 0.1, evidence: [] },
        { kind: "unattributed", nets: ["Y"], neededMm: 0.1, evidence: [] },
      ],
    })),
  );
  // The evidence lines come from the analysis and keep its vocabulary; the
  // titles and bodies are the part a first-time user reads, and they may not.
  const banned = /\b(net|nets|netlist|DRC|gerber|footprint|via|vias|pad|pads|trace|traces)\b/i;
  for (const card of helpCards(help, { board: "main" })) {
    assert.doesNotMatch(card.title, banned, `title: ${card.title}`);
    assert.doesNotMatch(card.body, banned, `body: ${card.body}`);
  }
});
