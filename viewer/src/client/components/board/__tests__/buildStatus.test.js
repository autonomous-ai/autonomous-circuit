import assert from "node:assert/strict";
import test from "node:test";
import {
  BUILD_STAGES,
  buildProgress,
  buildStageChecklist,
  buildStatusLine,
  formatElapsed,
  isRunning,
  isTerminal,
} from "../buildStatus.js";

test("the stage list is our design prelude plus the pipeline's seven, in order", () => {
  assert.deepEqual(
    BUILD_STAGES.map((stage) => stage.key),
    ["design", "compile", "scan", "checks", "substrate", "dfm", "export", "render"],
  );
  assert.equal(BUILD_STAGES[0].prelude, true);
  for (const stage of BUILD_STAGES) {
    assert.ok(stage.plain.length > 10, `${stage.key} needs a plain-language line`);
  }
});

test("buildStageChecklist marks done / active / pending from the stage key", () => {
  const list = buildStageChecklist({ state: "running", stage: "substrate", stageIndex: 4, stageCount: 7 });
  assert.deepEqual(
    list.map((stage) => stage.state),
    ["done", "done", "done", "done", "active", "pending", "pending", "pending"],
  );
});

test("an unknown stage still advances the list using stageIndex", () => {
  const list = buildStageChecklist({ state: "running", stage: "something_new", stageIndex: 3, stageCount: 8 });
  assert.equal(list[3].state, "active");
  assert.equal(list[2].state, "done");
});

test("a finished build shows every stage done", () => {
  const list = buildStageChecklist({ state: "done", stage: "render", stageIndex: 7, stageCount: 7 });
  assert.ok(list.every((stage) => stage.state === "done"));
});

test("before the pipeline reports anything, the design prelude is the live one", () => {
  // This is the case that matters: for the first minute the model is writing
  // the board and no build-status record exists at all. An all-grey list here
  // reads as a hang.
  const list = buildStageChecklist(null);
  assert.equal(list[0].state, "active");
  assert.ok(list.slice(1).every((stage) => stage.state === "pending"));
});

test("progress, elapsed and state helpers behave", () => {
  assert.equal(buildProgress({ state: "running", stageIndex: 3, stageCount: 7 }).toFixed(2), "0.43");
  assert.equal(buildProgress({ state: "done" }), 1);
  assert.equal(buildProgress(null), 0);
  assert.equal(formatElapsed(48), "48s");
  assert.equal(formatElapsed(185), "3m 05s");
  assert.equal(isRunning({ state: "running" }), true);
  assert.equal(isTerminal({ state: "stale" }), true);
  assert.equal(buildStatusLine({ state: "failed", detail: "boom" }).text, "Build failed");
});
