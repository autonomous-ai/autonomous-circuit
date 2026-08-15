import test from "node:test";
import assert from "node:assert/strict";

import {
  boxCenterAndRadius,
  fitDistance,
  thinnestAxis,
  flipOffset,
  rotateOffset90,
} from "./threeFit.js";

// ------------------------------------------------------------------ fitting

test("boxCenterAndRadius centres the box and takes the half-diagonal", () => {
  const { center, radius } = boxCenterAndRadius([-10, -20, -1], [30, 20, 1]);
  assert.deepEqual(center, [10, 0, 0]);
  // extents 40 x 40 x 2 -> diagonal sqrt(1600+1600+4), radius is half
  assert.ok(Math.abs(radius - Math.sqrt(3204) / 2) < 1e-9);
});

test("fitDistance fits the sphere with sin, not tan", () => {
  // Square pane, 60° fov: distance = r*margin / sin(30°) = 2*r*margin.
  const d = fitDistance(50, 60, 1, 1.0);
  assert.ok(Math.abs(d - 100) < 1e-9, `got ${d}`);
});

test("a portrait pane is limited by its horizontal fov", () => {
  // aspect 0.5 halves the horizontal tan, so the distance must grow.
  const landscape = fitDistance(50, 60, 2, 1.0);
  const square = fitDistance(50, 60, 1, 1.0);
  const portrait = fitDistance(50, 60, 0.5, 1.0);
  assert.equal(landscape, square, "vertical fov limits any aspect >= 1");
  assert.ok(portrait > square, "narrow pane needs more distance");
});

test("a degenerate box still fits at a finite distance", () => {
  const { radius } = boxCenterAndRadius([0, 0, 0], [0, 0, 0]);
  assert.equal(radius, 0);
  const d = fitDistance(radius, 60, 1);
  assert.ok(Number.isFinite(d) && d > 0, "empty GLB must not produce NaN");
});

// -------------------------------------------------------- board orientation

test("thinnestAxis finds the board normal whatever the exporter chose", () => {
  assert.equal(thinnestAxis([0, 0, 0], [80, 80, 1.6]), 2, "z-thin board");
  assert.equal(thinnestAxis([0, 0, 0], [80, 1.6, 80]), 1, "y-up exporter");
  assert.equal(thinnestAxis([0, 0, 0], [1.6, 80, 80]), 0, "x-thin, why not");
});

test("flip reflects only the thin-axis component", () => {
  assert.deepEqual(flipOffset([3, 4, 5], 1), [3, -4, 5]);
  // flipping twice is the identity
  assert.deepEqual(flipOffset(flipOffset([3, 4, 5], 1), 1), [3, 4, 5]);
});

test("rotate90 turns in the board plane and keeps the height", () => {
  // board normal = y: (x, z) rotate, y stays
  assert.deepEqual(rotateOffset90([1, 7, 0], 1), [-0, 7, 1]);
  // four turns come home
  let v = [3, 7, 5];
  for (let i = 0; i < 4; i += 1) v = rotateOffset90(v, 1);
  assert.deepEqual(v.map(Math.round), [3, 7, 5]);
});

test("rotate90 preserves distance from the rotation axis", () => {
  const v = rotateOffset90([3, 9, 4], 1);
  const before = Math.hypot(3, 4);
  const after = Math.hypot(v[0], v[2]);
  assert.ok(Math.abs(before - after) < 1e-12);
});
