// Tests for the episode rail's pure model: entry filtering, labels, and the
// ready / rendering / pending status logic.

import assert from "node:assert/strict";
import test from "node:test";

import {
  activityTouchesEpisode,
  episodeLabel,
  episodeNumber,
  episodeStatus,
  episodeStem,
  isEpisodeEntry,
  selectEpisodeEntries,
  selectSeriesEntry,
  RENDERING_ACTIVITY_WINDOW_MS,
} from "./episodeModel.js";

const entry = (file, kind = "mp4", artifact) => ({ file, kind, url: `/${file}?v=1-2`, artifact });

test("isEpisodeEntry accepts only mp4s directly under episodes/", () => {
  assert.equal(isEpisodeEntry(entry("episodes/ep001.mp4")), true);
  assert.equal(isEpisodeEntry(entry("episodes/ep001_shots/shot_s1_01.mp4")), false);
  assert.equal(isEpisodeEntry(entry("ep001.mp4")), false);
  assert.equal(isEpisodeEntry(entry("episodes/ep001.srt", "srt")), false);
  assert.equal(isEpisodeEntry(null), false);
});

test("episodeStem/Number/Label parse the epNNN convention", () => {
  assert.equal(episodeStem("episodes/ep001.mp4"), "ep001");
  assert.equal(episodeNumber("ep001"), 1);
  assert.equal(episodeNumber("ep060"), 60);
  assert.equal(episodeNumber("finale"), null);
  assert.equal(episodeLabel("ep001"), "E01");
  assert.equal(episodeLabel("ep060"), "E60");
  assert.equal(episodeLabel("ep100"), "E100");
});

test("selectEpisodeEntries filters and sorts by episode number", () => {
  const catalog = {
    entries: [
      entry("episodes/ep002.mp4"),
      entry("series.json", "json"),
      entry("episodes/ep001.mp4"),
      entry("episodes/ep010.mp4"),
      entry("episodes/ep001_review/_poster.png", "png"),
    ],
  };
  assert.deepEqual(
    selectEpisodeEntries(catalog).map((e) => e.file),
    ["episodes/ep001.mp4", "episodes/ep002.mp4", "episodes/ep010.mp4"],
  );
});

test("activityTouchesEpisode matches the episode's file family only", () => {
  assert.equal(activityTouchesEpisode("episodes/ep001.mp4", "ep001"), true);
  assert.equal(activityTouchesEpisode("episodes/ep001.episode.json", "ep001"), true);
  assert.equal(activityTouchesEpisode("episodes/ep001_shots/shot_s1_01.mp4", "ep001"), true);
  assert.equal(activityTouchesEpisode("episodes/ep001_review/_poster.png", "ep001"), true);
  assert.equal(activityTouchesEpisode("episodes/ep010.mp4", "ep001"), false);
  assert.equal(activityTouchesEpisode("series.py", "ep001"), false);
});

test("episodeStatus: sidecar + poster = ready; recent activity = rendering; else pending", () => {
  const now = 1_000_000;
  const ready = entry("episodes/ep001.mp4", "mp4", {
    metadataUrl: "/episodes/ep001.episode.json?v=1-2",
    posterUrl: "/episodes/ep001_review/_poster.png?v=1-2",
  });
  assert.equal(episodeStatus(ready, { activity: {}, now }), "ready");

  // Fresh artifact churn on this episode's files wins over ready.
  assert.equal(
    episodeStatus(ready, {
      activity: { "episodes/ep001_shots/shot_s1_02.mp4": now - 1000 },
      now,
    }),
    "rendering",
  );

  // Stale activity does not.
  assert.equal(
    episodeStatus(ready, {
      activity: { "episodes/ep001_shots/shot_s1_02.mp4": now - RENDERING_ACTIVITY_WINDOW_MS - 1 },
      now,
    }),
    "ready",
  );

  // Another episode's churn does not mark this one.
  assert.equal(
    episodeStatus(ready, {
      activity: { "episodes/ep002.mp4": now - 1000 },
      now,
    }),
    "ready",
  );

  // Missing sidecar/poster without activity → pending.
  assert.equal(episodeStatus(entry("episodes/ep002.mp4"), { activity: {}, now }), "pending");
});

test("selectSeriesEntry finds the series bible regardless of kind", () => {
  const catalog = {
    entries: [entry("episodes/ep001.mp4"), entry("series.json", "json")],
  };
  assert.equal(selectSeriesEntry(catalog)?.file, "series.json");
  assert.equal(selectSeriesEntry({ entries: [] }), null);
});
