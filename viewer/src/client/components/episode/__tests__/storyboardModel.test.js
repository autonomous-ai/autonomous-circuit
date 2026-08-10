// Tests for the storyboard strip's pure model: shot normalization from the
// .episode.json sidecar (with the artifact.shots fallback) and the cumulative
// offset math that drives click-to-seek.

import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeShots,
  normalizeShotStatus,
  shotOffsets,
  shotIndexAtTime,
} from "../storyboardModel.js";

test("normalizeShots prefers the sidecar and accepts durationS or duration_s", () => {
  const sidecar = {
    shots: [
      { id: "s1_01", path: "ep001_shots/shot_s1_01.mp4", durationS: 5 },
      { id: "s1_02", path: "ep001_shots/shot_s1_02.mp4", duration_s: 8, status: "cached" },
      { id: "s1_03", status: "failed" },
    ],
  };
  const shots = normalizeShots(sidecar, [{ id: "ignored", file: "x.mp4" }]);
  assert.equal(shots.length, 3);
  assert.deepEqual(
    shots.map((s) => s.durationS),
    [5, 8, null],
  );
  assert.deepEqual(
    shots.map((s) => s.status),
    ["rendered", "cached", "failed"],
  );
});

test("normalizeShots falls back to artifact.shots before the sidecar lands", () => {
  const shots = normalizeShots(null, [
    { id: "s1_01", file: "ep001_shots/shot_s1_01.mp4", url: "/u?v=1" },
    { id: "s1_02", file: "ep001_shots/shot_s1_02.mp4", url: "/u?v=2" },
  ]);
  assert.equal(shots.length, 2);
  assert.equal(shots[0].id, "s1_01");
  assert.equal(shots[0].durationS, null, "artifact shots carry no duration");
  assert.equal(shots[0].status, "rendered");
});

test("normalizeShotStatus clamps to the contract's closed set", () => {
  assert.equal(normalizeShotStatus("rendered"), "rendered");
  assert.equal(normalizeShotStatus("cached"), "cached");
  assert.equal(normalizeShotStatus("FAILED"), "failed");
  assert.equal(normalizeShotStatus("bogus"), "rendered");
  assert.equal(normalizeShotStatus(undefined), "rendered");
});

test("shotOffsets accumulates explicit durations", () => {
  const offsets = shotOffsets([
    { durationS: 5 },
    { durationS: 8 },
    { durationS: 5 },
  ]);
  assert.deepEqual(
    offsets.map((o) => o.startS),
    [0, 5, 13],
  );
});

test("shotOffsets splits the remaining episode time across unknown durations", () => {
  // 5s known + two unknowns over a 15s episode → each unknown gets 5s.
  const offsets = shotOffsets(
    [{ durationS: 5 }, { durationS: null }, { durationS: null }],
    15,
  );
  assert.deepEqual(
    offsets.map((o) => o.startS),
    [0, 5, 10],
  );
  assert.equal(offsets[1].durationS, 5);
});

test("shotOffsets with no episode duration gives unknown shots zero width", () => {
  const offsets = shotOffsets([{ durationS: 4 }, { durationS: null }, { durationS: 6 }]);
  assert.deepEqual(
    offsets.map((o) => o.startS),
    [0, 4, 4],
  );
});

test("shotOffsets handles empty and missing input", () => {
  assert.deepEqual(shotOffsets([]), []);
  assert.deepEqual(shotOffsets(undefined), []);
});

test("shotIndexAtTime maps playback time to the owning shot", () => {
  const offsets = shotOffsets([{ durationS: 5 }, { durationS: 8 }, { durationS: 5 }]);
  assert.equal(shotIndexAtTime(offsets, 0), 0);
  assert.equal(shotIndexAtTime(offsets, 4.9), 0);
  assert.equal(shotIndexAtTime(offsets, 5), 1);
  assert.equal(shotIndexAtTime(offsets, 12.9), 1);
  assert.equal(shotIndexAtTime(offsets, 999), 2, "clamps to the last shot");
  assert.equal(shotIndexAtTime([], 3), -1);
});
