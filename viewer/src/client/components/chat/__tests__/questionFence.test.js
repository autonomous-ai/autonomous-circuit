import assert from "node:assert/strict";
import test from "node:test";
import { DELEGATE_ANSWER, closeOpenContainers, parseQuestionFence } from "../questionFence.js";

test("a well-formed fence parses untouched", () => {
  const raw = JSON.stringify({
    questions: [
      {
        question: "How should it be powered?",
        header: "Power",
        multiSelect: false,
        options: [
          { label: "Let Circuit choose", description: "Recommended" },
          { label: "USB-C wall adapter", description: "Always-on 5V" },
        ],
      },
    ],
  });
  const out = parseQuestionFence(raw);
  assert.equal(out.repaired, false);
  assert.equal(out.dropped, 0);
  assert.equal(out.questions.length, 1);
  assert.equal(out.questions[0].options.length, 2);
});

// The real payload, verbatim in shape and truncation, from the first
// plain-language request anyone typed into this app: "a nightlight that comes
// on when it gets dark". The model's text block ended one closing brace short,
// JSON.parse threw, and the user was shown a clipped line of raw JSON under the
// words "Waiting for your answer".
test("a fence missing its final brace still yields every question", () => {
  const complete = JSON.stringify({
    questions: [
      {
        question: "How should it be powered?",
        header: "Power",
        multiSelect: false,
        options: [
          { label: "Let Circuit choose", description: "Recommended — we pick the best for you" },
          { label: "USB-C wall adapter", description: "Always-on 5V, no batteries to change" },
        ],
      },
      {
        question: "Do you want boards assembled, or bare PCBs?",
        header: "Build",
        multiSelect: false,
        options: [
          { label: "Fully assembled (PCBA)", description: "JLCPCB solders everything" },
          { label: "Bare PCB, I solder it", description: "Cheapest per board" },
        ],
      },
    ],
  });
  assert.throws(() => JSON.parse(complete.slice(0, -1)), "the raw text really is invalid");

  const out = parseQuestionFence(complete.slice(0, -1));
  assert.equal(out.repaired, true);
  assert.equal(out.dropped, 0);
  assert.deepEqual(
    out.questions.map((q) => q.header),
    ["Power", "Build"],
  );
});

test("a fence cut mid-option keeps the complete questions and counts the loss", () => {
  const raw =
    '{"questions":[{"question":"How should it be powered?","header":"Power","options":' +
    '[{"label":"USB-C","description":"5V"},{"label":"Let Circuit choose"}]},' +
    '{"question":"What kind of light?","header":"Light","options":[{"label":"Warm whi';
  const out = parseQuestionFence(raw);
  assert.equal(out.repaired, true);
  assert.equal(out.questions.length, 1, "the finished question survives");
  assert.equal(out.questions[0].options.length, 2);
  // The second question lost every option, so it cannot be answered and is
  // reported as dropped rather than rendered as an empty row.
  assert.equal(out.dropped, 1);
});

test("a question with no answerable options is dropped, not rendered empty", () => {
  const out = parseQuestionFence(
    '{"questions":[{"question":"Pick one","options":[]},{"question":"Power?","options":[{"label":"USB-C"}]}]}',
  );
  assert.equal(out.questions.length, 1);
  assert.equal(out.questions[0].question, "Power?");
  assert.equal(out.dropped, 1);
});

test("nothing recoverable returns null so the caller can offer a way out", () => {
  assert.equal(parseQuestionFence(""), null);
  assert.equal(parseQuestionFence("not json at all"), null);
  assert.equal(parseQuestionFence('{"questions":[]}'), null);
  assert.equal(parseQuestionFence('{"other":1}'), null);
  // A half-streamed opening is not answerable yet either.
  assert.equal(parseQuestionFence('{"questi'), null);
});

test("repair never invents content — braces inside strings are left alone", () => {
  const raw = '{"questions":[{"question":"Use {braces} or [brackets]?","options":[{"label":"{yes}"}]}]}';
  const out = parseQuestionFence(raw);
  assert.equal(out.repaired, false);
  assert.equal(out.questions[0].question, "Use {braces} or [brackets]?");
  assert.equal(out.questions[0].options[0].label, "{yes}");
});

test("closeOpenContainers closes in reverse order and drops partial tokens", () => {
  assert.equal(closeOpenContainers('{"a":[1,2'), '{"a":[1,2]}');
  assert.equal(closeOpenContainers('{"a":[{"b":1},'), '{"a":[{"b":1}]}');
  // An unterminated string is cut, not guessed at.
  assert.equal(closeOpenContainers('{"a":"unfinis'), "{}");
  // A key with no value is an orphan with no valid closure.
  assert.equal(closeOpenContainers('{"a":1,"b"'), '{"a":1}');
  assert.equal(closeOpenContainers(""), "");
});

test("the delegate sentence is one string both surfaces send", () => {
  assert.match(DELEGATE_ANSWER, /you decide/i);
  assert.match(DELEGATE_ANSWER, /without asking again/i);
});
