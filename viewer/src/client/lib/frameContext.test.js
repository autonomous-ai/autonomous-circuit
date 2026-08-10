// Tests for the frame-grab context note ("Send to AI" on the episode player).

import assert from "node:assert/strict";
import test from "node:test";

import { buildFrameContextNote, formatTimecode } from "./frameContext.js";

test("formatTimecode renders mm:ss under an hour and floors fractions", () => {
  assert.equal(formatTimecode(0), "00:00");
  assert.equal(formatTimecode(14), "00:14");
  assert.equal(formatTimecode(14.9), "00:14");
  assert.equal(formatTimecode(75), "01:15");
  assert.equal(formatTimecode(3600), "1:00:00");
  assert.equal(formatTimecode(3725), "1:02:05");
  // Defensive: negative / non-numeric clamp to zero.
  assert.equal(formatTimecode(-3), "00:00");
  assert.equal(formatTimecode(NaN), "00:00");
});

test("buildFrameContextNote names the episode and timecode", () => {
  assert.equal(
    buildFrameContextNote({ episode: "ep001", timeSeconds: 14 }),
    "[Viewer context: frame at 00:14 of ep001]",
  );
});

test("buildFrameContextNote includes the shot id when known", () => {
  assert.equal(
    buildFrameContextNote({ episode: "ep001", timeSeconds: 14, shotId: "s1_02" }),
    "[Viewer context: frame at 00:14 of ep001, during shot s1_02]",
  );
});

test("buildFrameContextNote returns empty without an episode so callers can guard", () => {
  assert.equal(buildFrameContextNote({}), "");
  assert.equal(buildFrameContextNote(), "");
  assert.equal(buildFrameContextNote({ episode: "  " }), "");
});
