// Declaring a net's width — the EE review's finding 4, as an edit a human makes.
//
// Every case here is a byte range applied to real board text and re-read, not a
// shape assertion on an object: the failure mode this guards is an edit landing
// one character out and writing `thicknes s=` into a file a fab eventually
// reads.

import assert from "node:assert/strict";
import test from "node:test";

import { applyEdits } from "../boardSource.js";
import { declaredWidth, formatWidth, tracesForNet, widthEdits } from "../netWidth.js";

const BOARD = `export default () => (
  <board width="20mm" height="20mm" minTraceWidth="0.15mm">
    <Ldo3v3 pcbX={0} pcbY={0} />
    <trace name="TR_GPIO2" from=".U3 > .GPIO2" to="net.CAP_DRIVE" />
    <trace name="TR_LED" from=".U3 > .GPIO0" to="net.LED_DATA" thickness="0.3mm" />
    {/* a comment mentioning <trace to="net.DECOY" /> */}
  </board>
)
`;

const apply = (source, result) => {
  assert.equal(result.ok, true, result.reason);
  return applyEdits(source, result.edits);
};

test("a width lands on the net's own trace, and reads back", () => {
  const next = apply(BOARD, widthEdits(BOARD, "CAP_DRIVE", 0.5));
  assert.match(next, /to="net\.CAP_DRIVE" thickness="0\.5mm"/);
  assert.equal(declaredWidth(next, "CAP_DRIVE"), 0.5);
  // Nothing else moved: the other trace keeps the width it had.
  assert.equal(declaredWidth(next, "LED_DATA"), 0.3);
});

test("an existing declaration is replaced, not doubled", () => {
  const next = apply(BOARD, widthEdits(BOARD, "LED_DATA", 0.8));
  assert.equal(declaredWidth(next, "LED_DATA"), 0.8);
  assert.equal((next.match(/thickness=/g) || []).length, 1);
});

test("clearing a width puts the net back on the board's default", () => {
  const next = apply(BOARD, widthEdits(BOARD, "LED_DATA", null));
  assert.equal(declaredWidth(next, "LED_DATA"), null);
  assert.doesNotMatch(next, /thickness=/);
  // And the trace itself survives — clearing a width is not deleting a
  // connection.
  assert.match(next, /<trace name="TR_LED" from="\.U3 > \.GPIO0" to="net\.LED_DATA" \/>/);
});

test("a net wired inside a block gets a board-level trace to hang the width on", () => {
  // V3_3 on terminal-keyboard is wired entirely inside `ldo-3v3`, so the board
  // file has nothing to mark. The new trace duplicates a connection that
  // already exists — it adds no copper, it adds the number the router reads.
  const refused = widthEdits(BOARD, "V3_3", 0.5);
  assert.equal(refused.ok, false);
  assert.match(refused.reason, /wired inside a block/);
  assert.match(refused.reason, /no pin on the net was given/);

  const next = apply(BOARD, widthEdits(BOARD, "V3_3", 0.5, { anchorPort: ".U2 > .VOUT" }));
  assert.match(next, /<trace from="\.U2 > \.VOUT" to="net\.V3_3" thickness="0\.5mm" \/>/);
  assert.match(next, /width: V3_3 routed at 0\.5mm \(set in the app\)/);
  assert.equal(declaredWidth(next, "V3_3"), 0.5);
  // Inside the board, or it is not part of the board.
  assert.ok(next.indexOf('to="net.V3_3"') < next.indexOf("</board>"));
});

test("a trace named only in a comment is not a handle on a net", () => {
  // `maskCommentsAndStrings` is the reason: an edit that landed inside a
  // comment would write a width nothing reads and no one can see.
  assert.deepEqual(tracesForNet(BOARD, "DECOY"), []);
  assert.equal(widthEdits(BOARD, "DECOY", 0.5).ok, false);
});

test("the refusals say which thing was wrong", () => {
  assert.match(widthEdits(BOARD, "", 0.5).reason, /no net named/);
  assert.match(widthEdits(BOARD, "CAP_DRIVE", 40).reason, /not a track width/);
  assert.match(widthEdits(BOARD, "LED_DATA", 0.3).reason, /already 0\.3mm/);
  // Clearing a net that declares nothing is a no-op, and says so in the same
  // words as any other "nothing to do" — not an error about a missing thing.
  assert.match(widthEdits(BOARD, "CAP_DRIVE", null).reason, /already undeclared/);
});

test("widths are spelled the way a board file spells them", () => {
  assert.equal(formatWidth(0.5), "0.5mm");
  assert.equal(formatWidth(1), "1mm");
  assert.equal(formatWidth(0.30000000000000004), "0.3mm");
  assert.equal(formatWidth(0), "");
  assert.equal(formatWidth("nonsense"), "");
});
