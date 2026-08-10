// Pure-logic tests for the catalog store: snapshot folding, revision-gated
// refetch decisions, and artifact-activity tracking. The zustand wiring is a
// thin shell over these; node:test covers them without a transport.

import assert from "node:assert/strict";
import test from "node:test";

import {
  applyCatalogSnapshot,
  noteArtifactActivity,
  shouldRefetchForRevision,
  EMPTY_CATALOG,
  ARTIFACT_ACTIVITY_TTL_MS,
} from "../catalog.ts";

test("applyCatalogSnapshot folds a read into the slice and clears refresh/error state", () => {
  const catalog = {
    entries: [{ file: "episodes/ep001.mp4", kind: "mp4", url: "/f?v=1-2" }],
    rootPath: "/proj",
    revision: 7,
  };
  const slice = applyCatalogSnapshot(catalog);
  assert.equal(slice.catalog, catalog);
  assert.equal(slice.revision, 7);
  assert.equal(slice.hydrated, true);
  assert.equal(slice.refreshing, false);
  assert.equal(slice.error, "");
});

test("applyCatalogSnapshot guards a malformed read with the empty catalog", () => {
  const slice = applyCatalogSnapshot(null);
  assert.equal(slice.catalog, EMPTY_CATALOG);
  assert.equal(slice.revision, 0);
  assert.equal(slice.hydrated, true);
});

test("shouldRefetchForRevision refetches only when the revision moved forward", () => {
  assert.equal(shouldRefetchForRevision(5, 6), true);
  assert.equal(shouldRefetchForRevision(5, 5), false, "echo of the read we already hold");
  assert.equal(shouldRefetchForRevision(5, 4), false, "stale event");
  // Unknown revision → refetch rather than risk a stale catalog.
  assert.equal(shouldRefetchForRevision(5, undefined), true);
  assert.equal(shouldRefetchForRevision(5, "not-a-number"), true);
});

test("noteArtifactActivity stamps the file and prunes entries past the TTL", () => {
  const t0 = 1_000_000;
  let activity = noteArtifactActivity({}, "episodes/ep001_shots/shot_s1_01.mp4", t0);
  assert.deepEqual(activity, { "episodes/ep001_shots/shot_s1_01.mp4": t0 });

  // A second file within the TTL keeps both.
  activity = noteArtifactActivity(activity, "episodes/ep001.mp4", t0 + 1000);
  assert.equal(Object.keys(activity).length, 2);

  // A stamp far past the TTL prunes the stale ones.
  const later = t0 + ARTIFACT_ACTIVITY_TTL_MS + 5000;
  activity = noteArtifactActivity(activity, "episodes/ep002.mp4", later);
  assert.deepEqual(activity, { "episodes/ep002.mp4": later });
});

test("noteArtifactActivity ignores a blank file but still prunes", () => {
  const t0 = 1_000_000;
  const seeded = { "old.mp4": t0 - ARTIFACT_ACTIVITY_TTL_MS - 1 };
  const activity = noteArtifactActivity(seeded, "", t0);
  assert.deepEqual(activity, {});
});
