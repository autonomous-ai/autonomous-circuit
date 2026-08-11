import assert from "node:assert/strict";
import test from "node:test";

import { buildBoardIndex } from "./boardIndex.js";
import { boardRegions } from "./boardRegions.js";
import {
  FUNCTION_STATUS,
  brainSignals,
  findBrain,
  functionRows,
  functionSummary,
  isRailNet,
  looseEnds,
  railRows,
  splitSignals,
} from "./boardFunction.js";
import { fixtureBoard } from "./boardFixture.test-helper.js";

const build = () => {
  const index = buildBoardIndex(fixtureBoard());
  return { index, regions: boardRegions(index), brain: findBrain(index) };
};
const rowFor = (rows, predicate) => rows.find(predicate);

test("the microcontroller is found by what it is, not by its reference name", () => {
  const { brain } = build();
  assert.equal(brain.refdes, "U3");
  assert.equal(brain.mpn, "RP2040");
  assert.equal(findBrain(null), null);
  assert.equal(findBrain(buildBoardIndex([])), null);
});

test("rails are rails and signals are signals", () => {
  const { index } = build();
  assert.equal(isRailNet(index.netByName.get("V3_3")), true);
  assert.equal(isRailNet(index.netByName.get("GND")), true);
  assert.equal(isRailNet(index.netByName.get("SIG_LED")), false);
  assert.equal(isRailNet(null), false);
});

test("every signal the brain owns names the pin it uses and what is on the other end", () => {
  const { index, brain } = build();
  const signals = brainSignals(index, brain);
  const led = signals.find((signal) => signal.net === "SIG_LED");
  assert.deepEqual(led.pins, ["GPIO0"]);
  assert.deepEqual(led.others.map((other) => other.refdes).sort(), ["LED2", "R21"]);
  // Rails never appear here: "the chip has power" is a different question from
  // "the chip can drive this".
  assert.equal(signals.some((signal) => signal.net === "V3_3"), false);
});

test("a signal that never leaves the chip's own block is separated from one that does", () => {
  const { index, brain } = build();
  const split = splitSignals(brainSignals(index, brain));
  assert.deepEqual(split.external.map((signal) => signal.net).sort(), [
    "BTN_ROW",
    "SIG_CAP",
    "SIG_LED",
    "USB_DP",
  ]);
  // U4 is the chip's own flash, in the same group — housekeeping, not a feature.
  assert.deepEqual(split.internal.map((signal) => signal.net), ["QSPI_SCLK"]);
});

test("a pin named with nothing attached is reported, never quietly counted as wired", () => {
  const { index, brain } = build();
  const split = splitSignals(brainSignals(index, brain));
  assert.deepEqual(split.empty.map((signal) => signal.net), ["SWCLK"]);
  assert.equal(split.empty[0].goesNowhere, true);
});

test("an area wired to a pin says so, with the net and the pin", () => {
  const { index, regions } = build();
  const rows = functionRows(index, regions);
  const led = rowFor(rows, (row) => row.refdes.includes("LED2"));
  assert.equal(led.status, FUNCTION_STATUS.SIGNAL);
  assert.equal(led.confirmed, true);
  assert.match(led.sentence, /Wired to the brain \(U3\) on SIG_LED → GPIO0\./);
});

test("a light on a rail is not described as something the program controls", () => {
  const { index, regions } = build();
  const rows = functionRows(index, regions);
  const led = rowFor(rows, (row) => row.refdes.includes("LED1"));
  assert.equal(led.status, FUNCTION_STATUS.POWER);
  assert.equal(led.confirmed, false);
  assert.match(led.sentence, /Shares V3_3 with the brain/);
  assert.match(led.sentence, /the program cannot change it/);
});

test("a regulator with no signal is normal, and is not written up as a fault", () => {
  const { index, regions } = build();
  const rows = functionRows(index, regions);
  const ldo = rowFor(rows, (row) => row.refdes.includes("U2"));
  assert.equal(ldo.status, FUNCTION_STATUS.POWER);
  assert.match(ldo.sentence, /Power and ground is all this part needs/);
  assert.doesNotMatch(ldo.sentence, /cannot/);
});

test("a part nobody wired is reported as unconfirmed, never as working", () => {
  const { index, regions } = build();
  const rows = functionRows(index, regions);
  const sensor = rowFor(rows, (row) => row.refdes.includes("U5"));
  assert.equal(sensor.status, FUNCTION_STATUS.ISOLATED);
  assert.equal(sensor.confirmed, false);
  assert.match(sensor.sentence, /We cannot confirm this does anything/);
});

test("the summary leads with the gap when there is one, and never grades the board", () => {
  const { index, regions, brain } = build();
  const summary = functionSummary(functionRows(index, regions), { brain });
  assert.equal(summary.tone, "gap");
  assert.equal(summary.isolated, 1);
  assert.match(summary.headline, /could not tie back to the brain/);
});

test("with nothing isolated the summary says every area is joined, not a score", () => {
  // Drop the unwired sensor and the same board reads as fully traced.
  const elements = fixtureBoard().filter(
    (element) => !JSON.stringify(element).includes("sc_u5") && element.source_group_id !== "g_sensor",
  );
  const index = buildBoardIndex(elements);
  const summary = functionSummary(functionRows(index, boardRegions(index)), { brain: findBrain(index) });
  assert.equal(summary.tone, "traced");
  assert.equal(summary.isolated, 0);
  assert.match(summary.headline, /Every area is joined/);
});

test("a board with no microcontroller says so instead of inventing a reference point", () => {
  const elements = fixtureBoard().filter((element) => !JSON.stringify(element).includes("sc_u3"));
  const index = buildBoardIndex(elements);
  const summary = functionSummary(functionRows(index, boardRegions(index)), { brain: findBrain(index) });
  assert.equal(summary.tone, "unknown");
  assert.match(summary.headline, /No microcontroller/);
});

test("an unbuilt board produces the honest empty state, not an empty pass", () => {
  const index = buildBoardIndex([]);
  const summary = functionSummary(functionRows(index, boardRegions(index)), { brain: null });
  assert.equal(summary.tone, "unknown");
  assert.equal(summary.total, 0);
  assert.match(summary.headline, /Nothing to trace yet/);
});

test("rails report who is on them, including whether the brain is", () => {
  const { index, brain } = build();
  const rails = railRows(index, brain);
  const v33 = rails.find((rail) => rail.net === "V3_3");
  assert.equal(v33.feedsBrain, true);
  assert.ok(v33.parts >= 4);
  const v5 = rails.find((rail) => rail.net === "V5");
  assert.equal(v5.feedsBrain, false);
});

test("loose ends find the part on no net and the net with one end", () => {
  const { index } = build();
  const ends = looseEnds(index);
  assert.deepEqual(ends.unconnected.map((part) => part.refdes), ["U5"]);
  assert.deepEqual(ends.dangling.map((net) => net.net), ["SWCLK"]);
});

test("joined to something that is not the brain is not the same as joined to nothing", () => {
  // A sensor wired to a connector but never to a pin. Calling that "isolated"
  // would be false — it is wired — and calling it confirmed would be worse.
  const elements = fixtureBoard();
  elements.push({
    type: "source_net",
    source_net_id: "n_side",
    name: "SIDE",
    subcircuit_connectivity_map_key: "sub_connectivity_SIDE",
  });
  for (const element of elements) {
    if (element.type === "source_port" && element.source_port_id === "sp_u5_sda") {
      element.subcircuit_connectivity_map_key = "sub_connectivity_SIDE";
    }
    if (element.type === "source_port" && element.source_port_id === "sp_r3_1") {
      // R3 also lands on SIDE, so the sensor has a neighbour that is not the MCU.
      element.subcircuit_connectivity_map_key = "sub_connectivity_SIDE";
    }
  }
  const index = buildBoardIndex(elements);
  const rows = functionRows(index, boardRegions(index));
  const sensor = rows.find((row) => row.refdes.includes("U5"));
  assert.equal(sensor.status, FUNCTION_STATUS.LINKED);
  assert.equal(sensor.confirmed, false);
  assert.match(sensor.sentence, /Joined to R3/);
  assert.match(sensor.sentence, /nothing carries a signal from here to the brain/);
});
