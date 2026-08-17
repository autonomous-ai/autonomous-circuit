// board_edit_apply — a drag as a sentence, written to disk, graded, recorded.
//
// Three levels, matching how the code is split. The planner (`planPlacementEdit`)
// and the queue (`createEditQueue`) are pure and driven directly. The command is
// driven over an ephemeral port for everything that involves the disk: the
// atomic write, the mid-build refusal, the compare-and-swap, and the history
// line. The gate (`runFastCheck`) gets its own section, and the part of it that
// needs a real toolchain SKIPS rather than fails when the toolchain is absent —
// the same posture kicad-dependent tests take in circuitpy.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createCircuitServices } from "./http.mjs";
import { createEditQueue, planPlacementEdit, refuseWrittenBoard, sourceDrift } from "./boardEdit.mjs";
import { pythonPathDirs, resolvePython, runFastCheck } from "./fastCheck.mjs";
import { runNetWidths } from "./netWidths.mjs";
import { AUTHOR, REVISION_KIND, readRevisions } from "./revisions.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FAKE_CLAUDE = path.join(HERE, "fixtures", "fake-claude.mjs");
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..");

const BOARD = `export default () => (
  <board width="20mm" height="20mm">
    {/* measured: 0.115mm from the alignment hole at x=3 */}
    <resistor name="R1" resistance="1k" footprint="0402" pcbX={1} pcbY={2} />
    <StatusLed led="LED1" pcbX={-4.5} pcbY={6} />
  </board>
)
`;

// --- the planner -----------------------------------------------------------

test("a delta move rewrites one literal and reports what it actually wrote", () => {
  const planned = planPlacementEdit(BOARD, { kind: "move", placementId: "resistor[1]", dx: 2.8, dy: 0 });
  assert.equal(planned.edits.length, 1);
  assert.equal(planned.edits[0].text, "3.8");
  assert.equal(planned.edits[0].expected, "1");
  assert.deepEqual(planned.delta, { dx: 2.8, dy: 0 });
  assert.equal(planned.summary, "moved resistor R1 from (1, 2) to (3.8, 2)");
});

test("an absolute move and a delta move agree", () => {
  const byDelta = planPlacementEdit(BOARD, { kind: "move", placementId: "StatusLed[1]", dx: 1.5, dy: -2 });
  const byPoint = planPlacementEdit(BOARD, { kind: "move", placementId: "StatusLed[1]", x: -3, y: 4 });
  assert.deepEqual(byDelta.edits, byPoint.edits);
});

test("the reported delta is what the file holds, not what was asked for", () => {
  // formatMm rounds to a micron. A move smaller than that leaves the file
  // unchanged, and a gate told about a move the file does not contain would be
  // grading geometry nobody will ever build.
  const planned = planPlacementEdit(BOARD, { kind: "move", placementId: "resistor[1]", dx: 0.0004, dy: 0.5 });
  assert.deepEqual(planned.delta, { dx: 0, dy: 0.5 });
  assert.equal(planned.edits.length, 1); // only pcbY moved
});

test("a move that changes nothing is refused, not silently accepted", () => {
  assert.throws(
    () => planPlacementEdit(BOARD, { kind: "move", placementId: "resistor[1]", dx: 0, dy: 0 }),
    (error) => error.code === "NO_CHANGE",
  );
});

test("a coordinate that is not a position is refused, however finite it is", () => {
  // `1e30` is finite, and finite was the only thing this check asked for. It
  // wrote `pcbX={1e+30}` into a real board file on the running app and came
  // back from the gate as `legal`, because nothing downstream measures a
  // placement against anything at all.
  for (const edit of [
    { kind: "move", placementId: "resistor[1]", x: 1e30, y: 1 },
    { kind: "move", placementId: "resistor[1]", x: 1, y: -1e15 },
    { kind: "move", placementId: "resistor[1]", dx: 5000, dy: 0 },
  ]) {
    assert.throws(
      () => planPlacementEdit(BOARD, edit),
      (error) => error.code === "INVALID_ARGUMENT" && /±1000mm/.test(error.message),
      JSON.stringify(edit),
    );
  }

  // And the bound is nowhere near a real board: a part parked 200mm off a
  // 20mm board — which is what rearranging a layout looks like — still writes.
  const planned = planPlacementEdit(BOARD, { kind: "move", placementId: "resistor[1]", x: 200, y: -200 });
  assert.equal(planned.next.x, 200);
});

test("an unknown placement is refused by name", () => {
  assert.throws(
    () => planPlacementEdit(BOARD, { kind: "move", placementId: "capacitor[7]", dx: 1, dy: 0 }),
    (error) => error.code === "PLACEMENT_NOT_FOUND" && /capacitor\[7\]/.test(error.message),
  );
});

test("locking inserts the comment above the tag and counts as no movement", () => {
  const planned = planPlacementEdit(BOARD, { kind: "lock", placementId: "StatusLed[1]", locked: true });
  assert.equal(planned.edits.length, 1);
  assert.match(planned.edits[0].text, /locked: placed by hand/);
  assert.deepEqual(planned.delta, { dx: 0, dy: 0 });
});

test("locking an already-locked placement is refused", () => {
  const locked = BOARD.replace(
    "    <StatusLed",
    "    {/* locked: placed by hand - do not move this without asking */}\n    <StatusLed",
  );
  assert.throws(
    () => planPlacementEdit(locked, { kind: "lock", placementId: "StatusLed[1]", locked: true }),
    (error) => error.code === "NO_CHANGE",
  );
});

test("an unparseable board is a refusal, not a crash", () => {
  assert.throws(
    () => planPlacementEdit("const x = 1\n", { kind: "move", placementId: "resistor[1]", dx: 1, dy: 0 }),
    (error) => error.code === "INVALID_ARGUMENT",
  );
});

// --- the queue -------------------------------------------------------------

test("two edits to one project run one after the other, not interleaved", async () => {
  const serialize = createEditQueue();
  const order = [];
  const slow = serialize("p", async () => {
    order.push("a:start");
    await new Promise((resolve) => setTimeout(resolve, 20));
    order.push("a:end");
  });
  const fast = serialize("p", async () => {
    order.push("b:start");
    order.push("b:end");
  });
  await Promise.all([slow, fast]);
  assert.deepEqual(order, ["a:start", "a:end", "b:start", "b:end"]);
});

test("a failed edit does not wedge the queue behind it", async () => {
  const serialize = createEditQueue();
  await assert.rejects(serialize("p", async () => {
    throw new Error("boom");
  }));
  assert.equal(await serialize("p", async () => "still working"), "still working");
});

test("different projects do not wait on each other", async () => {
  const serialize = createEditQueue();
  let released;
  const blocked = new Promise((resolve) => {
    released = resolve;
  });
  const first = serialize("a", () => blocked);
  assert.equal(await serialize("b", async () => "b ran"), "b ran");
  released("a ran");
  assert.equal(await first, "a ran");
});

// --- the command -----------------------------------------------------------

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

async function bootServer() {
  const home = tmpdir("circuit-home-");
  const cfgDir = tmpdir("circuit-cfg-");
  fs.writeFileSync(path.join(home, "scenario.json"), JSON.stringify({}));
  const env = {
    ...process.env,
    CIRCUIT_HOME: home,
    CLAUDE_CONFIG_DIR: cfgDir,
    CIRCUIT_CLAUDE_BIN: FAKE_CLAUDE,
    CIRCUIT_FAKE_SCENARIO: path.join(home, "scenario.json"),
  };
  const services = createCircuitServices({ env });
  const server = http.createServer((req, res) => {
    services.apiMiddleware(req, res, () => {
      res.statusCode = 404;
      res.end("fallthrough");
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  async function post(cmd, body = {}) {
    const response = await fetch(`${base}/api/${cmd}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    return { status: response.status, body: text ? JSON.parse(text) : null };
  }
  const project = await post("project_create", { req: { name: "drag-me" } });
  const id = project.body.id;
  const dir = services.projects.projectDir(id);
  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.writeFileSync(path.join(dir, "boards", "main.tsx"), BOARD, "utf8");
  return {
    services,
    post,
    id,
    dir,
    board: path.join(dir, "boards", "main.tsx"),
    close: () => {
      services.close();
      server.close();
    },
  };
}

test("a move lands on disk, keeps the comments, and leaves no temp file", async () => {
  const s = await bootServer();
  try {
    const response = await s.post("board_edit_apply", {
      id: s.id,
      file: "boards/main.tsx",
      edit: { kind: "move", placementId: "resistor[1]", dx: 2.8, dy: -0.5 },
      sourceLength: BOARD.length,
      verify: false,
    });
    assert.equal(response.status, 200);
    assert.equal(response.body.saved, true);
    assert.equal(response.body.check, null);
    const onDisk = fs.readFileSync(s.board, "utf8");
    assert.equal(onDisk.includes("pcbX={3.8} pcbY={1.5}"), true);
    assert.equal(onDisk.includes("measured: 0.115mm"), true);
    assert.equal(response.body.text, onDisk);
    assert.deepEqual(response.body.placement, {
      id: "resistor[1]",
      tag: "resistor",
      name: "R1",
      x: 3.8,
      y: 1.5,
      locked: false,
    });
    assert.deepEqual(fs.readdirSync(path.join(s.dir, "boards")).sort(), ["main.tsx"]);
  } finally {
    s.close();
  }
});

test("the edit is recorded in the same history a build lands in, marked human", async () => {
  const s = await bootServer();
  try {
    await s.post("board_edit_apply", {
      id: s.id,
      file: "boards/main.tsx",
      edit: { kind: "move", placementId: "StatusLed[1]", dx: 1, dy: 0 },
      verify: false,
    });
    const rows = readRevisions(s.dir);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].author, AUTHOR.HUMAN);
    assert.equal(rows[0].kind, REVISION_KIND.EDIT);
    assert.equal(rows[0].phase, "placement");
    assert.equal(rows[0].file, "boards/main.tsx");
    assert.equal(rows[0].summary, "moved StatusLed from (-4.5, 6) to (-3.5, 6)");
    // No compile ran, so the fab verdict is unknown — never false.
    assert.equal(rows[0].fabReady, null);

    const history = await s.post("build_revisions", { id: s.id });
    assert.equal(history.body.revisions.length, 1);
    assert.equal(history.body.revisions[0].kind, REVISION_KIND.EDIT);
  } finally {
    s.close();
  }
});

test("a build in progress blocks the edit and the file is untouched", async () => {
  const s = await bootServer();
  try {
    fs.mkdirSync(path.join(s.dir, ".circuit"), { recursive: true });
    fs.writeFileSync(
      path.join(s.dir, ".circuit", "build-status.json"),
      JSON.stringify({ state: "running", stage: "compile", updatedAt: Date.now() / 1000 }),
    );
    const response = await s.post("board_edit_apply", {
      id: s.id,
      file: "boards/main.tsx",
      edit: { kind: "move", placementId: "resistor[1]", dx: 2.8, dy: 0 },
      verify: false,
    });
    assert.equal(response.status, 409);
    assert.equal(response.body.code, "BUILD_RUNNING");
    assert.equal(fs.readFileSync(s.board, "utf8"), BOARD);
    assert.deepEqual(readRevisions(s.dir), []);
  } finally {
    s.close();
  }
});

test("an edit against a file the agent has since rewritten is refused with 409", async () => {
  const s = await bootServer();
  try {
    fs.writeFileSync(s.board, `${BOARD}// the agent added a line\n`, "utf8");
    const before = fs.readFileSync(s.board, "utf8");
    const response = await s.post("board_edit_apply", {
      id: s.id,
      file: "boards/main.tsx",
      edit: { kind: "move", placementId: "resistor[1]", dx: 2.8, dy: 0 },
      sourceLength: BOARD.length,
      verify: false,
    });
    assert.equal(response.status, 409);
    assert.equal(response.body.code, "SOURCE_CHANGED");
    assert.equal(fs.readFileSync(s.board, "utf8"), before);
  } finally {
    s.close();
  }
});

test("two edits fired at once both land — neither reads the pre-edit file", async () => {
  // Without the per-project queue both handlers read the same bytes and the
  // second write erases the first. This is the test that fails if serialisation
  // is removed.
  const s = await bootServer();
  try {
    const [a, b] = await Promise.all([
      s.post("board_edit_apply", {
        id: s.id,
        file: "boards/main.tsx",
        edit: { kind: "move", placementId: "resistor[1]", dx: 2.8, dy: 0 },
        verify: false,
      }),
      s.post("board_edit_apply", {
        id: s.id,
        file: "boards/main.tsx",
        edit: { kind: "move", placementId: "StatusLed[1]", dx: 0, dy: -1.5 },
        verify: false,
      }),
    ]);
    assert.deepEqual([a.status, b.status], [200, 200]);
    const onDisk = fs.readFileSync(s.board, "utf8");
    assert.equal(onDisk.includes("pcbX={3.8}"), true, "the resistor move survived");
    assert.equal(onDisk.includes("pcbY={4.5}"), true, "the LED move survived");
    assert.equal(readRevisions(s.dir).length, 2);
  } finally {
    s.close();
  }
});

test("locking writes a comment the compiler ignores and the next agent reads", async () => {
  const s = await bootServer();
  try {
    const response = await s.post("board_edit_apply", {
      id: s.id,
      file: "boards/main.tsx",
      edit: { kind: "lock", placementId: "StatusLed[1]", locked: true },
      verify: false,
    });
    assert.equal(response.status, 200);
    assert.equal(response.body.placement.locked, true);
    const onDisk = fs.readFileSync(s.board, "utf8");
    assert.match(onDisk, /\{\/\* locked: placed by hand[^\n]*\*\/\}\n\s*<StatusLed/);
    assert.equal(readRevisions(s.dir)[0].summary, "unlocked StatusLed".replace("un", ""));
  } finally {
    s.close();
  }
});

test("a path outside boards/ never reaches the edit engine", async () => {
  const s = await bootServer();
  try {
    const response = await s.post("board_edit_apply", {
      id: s.id,
      file: "../../../etc/hosts",
      edit: { kind: "move", placementId: "resistor[1]", dx: 1, dy: 0 },
      verify: false,
    });
    assert.equal(response.status, 400);
    assert.equal(response.body.code, "INVALID_ARGUMENT");
  } finally {
    s.close();
  }
});

test("an unknown project is a 404, not a write", async () => {
  const s = await bootServer();
  try {
    const response = await s.post("board_edit_apply", {
      id: "not-a-project",
      file: "boards/main.tsx",
      edit: { kind: "move", placementId: "resistor[1]", dx: 1, dy: 0 },
      verify: false,
    });
    assert.equal(response.status, 404);
  } finally {
    s.close();
  }
});

// --- the gate --------------------------------------------------------------

test("a board that has never been built reports unavailable, never clean", async () => {
  // Silence is not a pass. An absent check has to be visible in the verdict or
  // the UI will render "no problems found" over a board nothing looked at.
  const s = await bootServer();
  try {
    const verdict = await runFastCheck(path.join(s.dir, "boards", "main.circuit.json"), {
      projectRoot: s.dir,
    });
    assert.equal(verdict.ok, false);
    assert.equal(verdict.status, "unavailable");
    assert.match(verdict.reason, /has not been built yet/);
    assert.deepEqual(verdict.counts, { error: 0, warning: 0, info: 0 });
  } finally {
    s.close();
  }
});

test("a missing interpreter reports unavailable rather than throwing", async () => {
  const verdict = await runFastCheck("/nonexistent/boards/main.circuit.json", {
    projectRoot: "/nonexistent",
    env: { ...process.env, CIRCUIT_PYTHON: "", PATH: "/nonexistent-bin" },
  });
  assert.equal(verdict.status, "unavailable");
  assert.equal(verdict.counts.error, 0);
});

test("board_fast_check refuses a path that is not a board source", async () => {
  const s = await bootServer();
  try {
    const response = await s.post("board_fast_check", { id: s.id, file: "blocks/glue.tsx" });
    assert.equal(response.status, 400);
    assert.equal(response.body.code, "INVALID_ARGUMENT");
  } finally {
    s.close();
  }
});

/**
 * The real thing, on the largest board we ship.
 *
 * Skipped rather than failed when the pinned toolchain or a 3.10+ interpreter
 * is absent, matching how circuitpy treats kicad-dependent tests: a missing
 * tool is not a broken gate. When it does run it asserts the two claims this
 * whole feature rests on — that a 2.8mm drag is caught without a compile, and
 * that the verdict admits what it cannot see.
 */
const EXAMPLE = path.join(REPO_ROOT, "examples", "terminal-keyboard");
const TOOLCHAIN_READY =
  fs.existsSync(path.join(EXAMPLE, "boards", "main.circuit.json")) &&
  fs.existsSync(path.join(REPO_ROOT, "toolchain", "node_modules")) &&
  Boolean(resolvePython()) &&
  pythonPathDirs().length > 0;

test("a byte-range write cannot put a part somewhere a board cannot be", async () => {
  // The bound used to live in `planPlacementEdit`, which is `board_edit_apply`
  // — an endpoint no client code calls. Every real gesture (drag, Properties,
  // undo, redo) writes byte ranges through `board_source_write`, and
  // `pcbX={1e30}` went straight to disk through it, twice, on the running
  // server. A command that takes byte ranges has no coordinate to check, so
  // the *result* is what gets checked.
  const app = await bootServer();
  try {
    const before = fs.readFileSync(app.board, "utf8");
    const at = before.indexOf("pcbX={1}");
    assert.ok(at > 0, "the fixture board must have R1's literal in it");
    const start = at + "pcbX={".length;

    const refused = await app.post("board_source_write", {
      id: app.id,
      file: "boards/main.tsx",
      edits: [{ start, end: start + 1, text: "1e30", expected: "1" }],
      sourceLength: before.length,
    });
    assert.equal(refused.status, 400);
    // Caught by the coordinate scan, which reads every numeric `pcbX`/`pcbY`
    // literal in the file — nested ones included — rather than only the
    // top-level placements the canvas can select. It names the line and the
    // number, which is what somebody needs to go and look at it.
    assert.match(refused.body.message, /line \d+: X is 1e\+30mm/);
    assert.equal(fs.readFileSync(app.board, "utf8"), before, "the refused write reached the file");

    // A coordinate the parser CAN read, and that is still nowhere a board
    // could be, is refused by the bound itself.
    const far = await app.post("board_source_write", {
      id: app.id,
      file: "boards/main.tsx",
      edits: [{ start, end: start + 1, text: "4000", expected: "1" }],
      sourceLength: before.length,
    });
    assert.equal(far.status, 400);
    assert.match(far.body.message, /±1000mm/);
    assert.equal(fs.readFileSync(app.board, "utf8"), before);

    // And an ordinary move through the same path still writes.
    const ok = await app.post("board_source_write", {
      id: app.id,
      file: "boards/main.tsx",
      edits: [{ start, end: start + 1, text: "4", expected: "1" }],
      sourceLength: before.length,
    });
    assert.equal(ok.status, 200);
    assert.match(fs.readFileSync(app.board, "utf8"), /pcbX=\{4\}/);
  } finally {
    app.close();
  }
});

test(
  "the gate catches a drag on terminal-keyboard without compiling anything",
  { skip: TOOLCHAIN_READY ? false : "example board or pinned toolchain not present" },
  async () => {
    const circuitJson = path.join(EXAMPLE, "boards", "main.circuit.json");

    const before = await runFastCheck(circuitJson, { projectRoot: EXAMPLE });
    assert.equal(before.ok, true, before.reason);
    assert.equal(before.geometry, "as_built");

    // <StatusLed> sits at (-45.8, -32) and owns LED1 + R20. The board's own
    // comment at main.tsx:268-271 says this leaves 0.44mm of courtyard; 2.8mm
    // east spends it and lands LED1 on the LDO's input cap.
    const after = await runFastCheck(circuitJson, {
      projectRoot: EXAMPLE,
      moves: [{ anchor: { x: -45.8, y: -32 }, dx: 2.8, dy: 0 }],
    });
    assert.equal(after.ok, true, after.reason);
    assert.equal(after.geometry, "predicted");
    assert.equal(after.status, "blocked");
    assert.equal(after.moves[0].ok, true, after.moves[0].reason);
    assert.deepEqual(after.moves[0].refdes, ["LED1", "R20"]);
    assert.ok(
      after.counts.error > before.counts.error,
      `the drag must add blockers (${before.counts.error} -> ${after.counts.error})`,
    );

    const kinds = new Set(after.warnings.filter((w) => w.severity === "error").map((w) => w.kind));
    for (const expected of [
      "pcb_footprint_overlap_error",
      "pcb_pad_pad_clearance_error",
      "pcb_courtyard_overlap_error",
      "trace_left_its_pad",
    ]) {
      assert.ok(kinds.has(expected), `expected a ${expected} finding, got ${[...kinds].join(", ")}`);
    }

    // The verdict has to carry its own blind spots. The pour is the one that
    // matters: no gate in this pipeline, including KiCad DRC, can see a pour
    // defect, so it must never be reported as clear.
    assert.ok(after.notChecked.length >= 3);
    assert.match(JSON.stringify(after.notChecked), /copper pour/);
    assert.ok(
      after.checked.some((line) => /minus checkTracesAreContiguous/.test(line)),
      "the verdict must name the check it dropped",
    );
  },
);

test("a human's drag lands in the board's own history", async () => {
  // `recordEdit` existed, `REVISION_KIND.EDIT` existed, and the only caller was
  // `board_edit_apply` — the endpoint no client uses. So an engineer could move
  // twenty parts and the history between two builds was empty.
  const app = await bootServer();
  try {
    const before = fs.readFileSync(app.board, "utf8");
    const at = before.indexOf("pcbX={1}") + "pcbX={".length;
    const wrote = await app.post("board_source_write", {
      id: app.id,
      file: "boards/main.tsx",
      edits: [{ start: at, end: at + 1, text: "4", expected: "1" }],
      sourceLength: before.length,
      summary: "R1 moved 3, 0 mm",
    });
    assert.equal(wrote.status, 200);

    const history = readRevisions(app.dir);
    const edits = history.filter((one) => one.kind === REVISION_KIND.EDIT);
    assert.equal(edits.length, 1, JSON.stringify(history));
    assert.equal(edits[0].summary, "R1 moved 3, 0 mm");
    assert.equal(edits[0].author, AUTHOR.HUMAN);
    assert.equal(edits[0].file, "boards/main.tsx");

    // A write with nothing to say still leaves a row. It used to leave none,
    // which meant an API caller — or any client that forgot the field — could
    // edit the board and leave the history looking like nothing happened
    // between two builds (round 3, integrity judge). "Something changed and
    // nobody said what" is a worse row than a good one and a far better row
    // than silence.
    await app.post("board_source_write", {
      id: app.id,
      file: "boards/main.tsx",
      edits: [{ start: at, end: at + 1, text: "5", expected: "4" }],
      sourceLength: fs.readFileSync(app.board, "utf8").length,
    });
    const rows = readRevisions(app.dir).filter((one) => one.kind === REVISION_KIND.EDIT);
    assert.equal(rows.length, 2);
    assert.match(rows[rows.length - 1].summary, /edited boards\/main\.tsx/);
  } finally {
    app.close();
  }
});

test("what the server answers is exactly what is on disk, and nothing is left behind", async () => {
  // Autosave's whole promise: the reply the client re-parses and the bytes the
  // next build compiles are the same text. The write goes through a sibling
  // temp file and a rename, so this also checks the temp file does not survive
  // — a `.main.tsx.<pid>.tmp` left in `boards/` is a file the catalog scanner
  // would show an engineer.
  const app = await bootServer();
  try {
    const before = fs.readFileSync(app.board, "utf8");
    const at = before.indexOf("pcbX={1}") + "pcbX={".length;

    const wrote = await app.post("board_source_write", {
      id: app.id,
      file: "boards/main.tsx",
      edits: [{ start: at, end: at + 1, text: "7", expected: "1" }],
      sourceLength: before.length,
      summary: "R1 moved 6, 0 mm",
    });
    assert.equal(wrote.status, 200);

    const onDisk = fs.readFileSync(app.board, "utf8");
    assert.equal(wrote.body.text, onDisk, "the reply and the file disagree");
    assert.equal(wrote.body.sourceLength, onDisk.length);
    assert.match(onDisk, /pcbX=\{7\}/);

    const strays = fs.readdirSync(path.dirname(app.board)).filter((name) => name.includes(".tmp"));
    assert.deepEqual(strays, [], "the atomic write left its temp file behind");

    // And the compare-and-swap is against the file as it now is: replaying the
    // same request writes nothing and says why.
    const replay = await app.post("board_source_write", {
      id: app.id,
      file: "boards/main.tsx",
      edits: [{ start: at, end: at + 1, text: "7", expected: "1" }],
      sourceLength: before.length,
    });
    assert.equal(replay.status, 409);
    assert.equal(fs.readFileSync(app.board, "utf8"), onDisk, "a refused replay changed the file");
  } finally {
    app.close();
  }
});

test(
  "board_net_widths measures what a rail could be, not just what it is",
  { skip: TOOLCHAIN_READY ? false : "example board or pinned toolchain not present" },
  async () => {
    // The two numbers the EE review's finding 4 turns on, on the board it was
    // written about: V5 can take 1.1mm and V3_3 cannot exceed 0.4mm, because a
    // track leaving a QFN-56 pad at 0.400mm pitch is 2 x (0.4 - 0.1 - 0.1)
    // wide and no wider. One is free; the other is impossible at any effort.
    const measured = await runNetWidths(path.join(EXAMPLE, "boards", "main.circuit.json"), {
      projectRoot: EXAMPLE,
      nets: ["V3_3", "V5"],
    });
    assert.equal(measured.ok, true, measured.reason);
    const byNet = Object.fromEntries(measured.nets.map((row) => [row.net, row]));

    assert.ok(Math.abs(byNet.V3_3.ceiling_mm - 0.4) < 0.001, JSON.stringify(byNet.V3_3));
    assert.match(byNet.V3_3.ceiling_at, /^U3\./, "the ceiling must name the pin that holds it");
    assert.ok(Math.abs(byNet.V5.ceiling_mm - 1.1) < 0.001, JSON.stringify(byNet.V5));

    // What it is today, which is the other half of the sentence: 0.2mm against
    // a 0.5mm power floor.
    assert.ok(Math.abs(byNet.V3_3.narrowest_mm - 0.2) < 0.001);
    assert.equal(byNet.V3_3.declared_mm, null);
    assert.equal(measured.powerFloorMm, 0.5);

    // A net nobody named is not measured — this costs seconds per net and the
    // caller is a person looking at one of them.
    const nothing = await runNetWidths(path.join(EXAMPLE, "boards", "main.circuit.json"), {
      projectRoot: EXAMPLE,
    });
    assert.equal(nothing.ok, false);
    assert.match(nothing.reason, /no net named/);
  },
);

test(
  "a verdict asked for with no moves says when the file has moved on",
  { skip: TOOLCHAIN_READY ? false : "example board or pinned toolchain not present" },
  async () => {
    // The trap the first fleet build walked into: move a part with
    // `board_source_write`, ask `board_fast_check` for a verdict, and be told
    // `legal` — about the old geometry, because the gate grades `circuit.json`
    // and nobody told it what the file now says.
    //
    // The server cannot compute the delta for them: `bindPlacements` matches by
    // coordinate, so a moved literal stops binding and there is nothing left to
    // measure against. What it can do is notice, and this is that.
    const source = fs.readFileSync(path.join(EXAMPLE, "boards", "main.tsx"), "utf8");
    const circuitJson = JSON.parse(
      fs.readFileSync(path.join(EXAMPLE, "boards", "main.circuit.json"), "utf8"),
    );

    // As shipped, the file and the artifact agree.
    assert.deepEqual(sourceDrift(source, circuitJson), { drifted: 0, total: 12 });

    // Move <StatusLed> 2.8mm east in the source only.
    const moved = source.replace("pcbX={-45.8}", "pcbX={-43}");
    assert.notEqual(moved, source, "the fixture literal moved under this test");
    assert.equal(sourceDrift(moved, circuitJson).drifted, 1);

    // Neither an unparseable file nor an unbuilt board is drift — the gate has
    // its own words for both, and inventing a number here would be worse.
    assert.equal(sourceDrift("not tsx at all {{{", circuitJson).drifted, 0);
    assert.equal(sourceDrift(source, []).drifted, 0);
  },
);

test("a write that breaks the file, or hides a coordinate in a group, is refused", () => {
  // Round 3, integrity judge: the result bound walked `parseBoardSource`'s
  // placements, which are top-level tags only, so a coordinate inside a
  // `<group>` was unguarded; and a write that produced unparseable TSX was
  // waved through on the reasoning that a broken file is the compiler's
  // problem. It is not — the file parsed a moment ago, and this write is what
  // broke it.
  const good = `export default () => (
  <board width="20mm" height="20mm">
    <group pcbX={0} pcbY={0}>
      <resistor name="R1" pcbX={1} pcbY={2} />
    </group>
  </board>
)
`;
  assert.equal(refuseWrittenBoard(good, good), "");

  const nested = good.replace("pcbX={1}", "pcbX={4000}");
  assert.match(refuseWrittenBoard(nested, good), /±1000mm/);
  assert.match(refuseWrittenBoard(nested, good), /^line \d+:/);

  const broken = good.replace("</board>", "");
  // esbuild's own words, with the line — `parseBoardSource` cannot answer
  // this, since it is a structural scanner and returns `ok` for a file
  // missing its own `</board>`.
  assert.match(refuseWrittenBoard(broken, good), /unable to compile/);
  assert.match(refuseWrittenBoard(broken, good), /closing "board" tag/);

  // A file that was ALREADY unparseable is left alone: that is somebody else's
  // mess, and refusing every edit to it would trap them in it.
  assert.equal(refuseWrittenBoard(broken, broken), "");

  // And a comment that mentions a huge number is not a coordinate.
  const commented = good.replace(
    "<group",
    "{/* tried pcbX={1e30} once, do not */}\n    <group",
  );
  assert.equal(refuseWrittenBoard(commented, good), "");
});
