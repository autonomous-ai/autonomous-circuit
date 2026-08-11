import assert from "node:assert/strict";
import test from "node:test";

import { groupTurns, turnStoppedWithoutAnswering, userVisibleText } from "../chatHistoryModel.js";

test("groupTurns groups assistant turns under the preceding user prompt", () => {
  const history = [
    { id: "u1", role: "user" },
    { id: "a1", role: "assistant" },
    { id: "a2", role: "assistant" },
    { id: "u2", role: "user" },
    { id: "a3", role: "assistant" },
  ];
  const groups = groupTurns(history);
  assert.equal(groups.length, 2);
  assert.deepEqual(
    groups.map((group) => group.map((turn) => turn.id)),
    [["u1", "a1", "a2"], ["u2", "a3"]],
  );
});

test("groupTurns starts a group when history begins with assistant output", () => {
  const history = [{ id: "a1", role: "assistant" }];
  const groups = groupTurns(history);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].map((turn) => turn.id), ["a1"]);
});

// A refresh rebuilds history from Claude Code's transcript, which only ever
// saw the combined string — so the model-facing directives came back inside
// the user's own bubble. Watched on the first plain-language request typed
// into this app: "a nightlight that comes on when it gets dark" reloaded with
// a paragraph of instructions about pin assignments glued underneath it.
test("userVisibleText strips the notes we appended for the model", () => {
  const sent = [
    "a nightlight that comes on when it gets dark",
    "[Viewer context: pcb view of board main, component R4 selected]",
    "[Effort: high — think hard before writing the board. Check every block's pin assignment against its declared pinout and state the power budget arithmetic.]",
  ].join("\n\n");
  assert.equal(userVisibleText(sent), "a nightlight that comes on when it gets dark");
});

test("userVisibleText strips the frame-suggestion directive", () => {
  const sent =
    "The user sent a view from the board workspace but did not say what to change. " +
    "Before editing anything, look at any attached image, then propose 3–5 specific options.\n\n" +
    "[Viewer context: 3d view of board main]";
  assert.equal(userVisibleText(sent), "");
});

test("userVisibleText leaves the user's own words alone, brackets and all", () => {
  assert.equal(userVisibleText("make it smaller"), "make it smaller");
  assert.equal(userVisibleText("call it [prototype] please"), "call it [prototype] please");
  assert.equal(
    userVisibleText("two lines\n\nsecond paragraph"),
    "two lines\n\nsecond paragraph",
  );
  assert.equal(userVisibleText(""), "");
  assert.equal(userVisibleText(null), "");
});

// The worst outcome watched in a real session: forty minutes in, the last
// thing in the chat was the model's own sentence "Now the cheap structural
// check before paying for a full build:", three tool rows, and then nothing.
// The turn had ended; the board pane said "No build to judge yet"; there was
// no board, no error, no reply, and nothing suggesting what to do.
test("a turn whose last word is a tool call stopped mid-flow", () => {
  assert.equal(
    turnStoppedWithoutAnswering({
      role: "assistant",
      status: "complete",
      blocks: [
        { kind: "text", text: "Now the cheap structural check before paying for a full build:" },
        { kind: "tool_use", tool: "Bash" },
        { kind: "tool_use", tool: "Bash" },
      ],
    }),
    true,
  );
});

test("a turn that closed with words, a plan or an error has said its piece", () => {
  const base = { role: "assistant", status: "complete" };
  assert.equal(
    turnStoppedWithoutAnswering({ ...base, blocks: [{ kind: "tool_use" }, { kind: "text", text: "Done." }] }),
    false,
  );
  assert.equal(turnStoppedWithoutAnswering({ ...base, blocks: [{ kind: "tool_use" }, { kind: "plan" }] }), false);
  assert.equal(turnStoppedWithoutAnswering({ ...base, blocks: [{ kind: "tool_use" }, { kind: "error" }] }), false);
  assert.equal(
    turnStoppedWithoutAnswering({ role: "assistant", status: "cancelled", blocks: [{ kind: "tool_use" }] }),
    false,
  );
  assert.equal(
    turnStoppedWithoutAnswering({ role: "assistant", status: "running", blocks: [{ kind: "tool_use" }] }),
    false,
  );
});

test("an empty trailing text block is what a stopped stream leaves — not an answer", () => {
  const base = { role: "assistant", status: "complete" };
  assert.equal(
    turnStoppedWithoutAnswering({ ...base, blocks: [{ kind: "tool_use" }, { kind: "text", text: "  " }] }),
    true,
  );
  // Artifacts are recorded but never rendered, so they are not the last word.
  assert.equal(
    turnStoppedWithoutAnswering({ ...base, blocks: [{ kind: "tool_use" }, { kind: "artifact", file: "main.tsx" }] }),
    true,
  );
  assert.equal(
    turnStoppedWithoutAnswering({ ...base, blocks: [{ kind: "text", text: "ok" }, { kind: "artifact" }] }),
    false,
  );
});

test("nothing to carry on from is not a stop", () => {
  const base = { role: "assistant", status: "complete" };
  assert.equal(turnStoppedWithoutAnswering({ ...base, blocks: [] }), false);
  assert.equal(turnStoppedWithoutAnswering({ ...base, blocks: [{ kind: "thinking", text: "hm" }] }), false);
  assert.equal(turnStoppedWithoutAnswering({ role: "user", status: "complete", blocks: [{ kind: "tool_use" }] }), false);
  assert.equal(turnStoppedWithoutAnswering(null), false);
});
