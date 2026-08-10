// Catalog scanner tests — §2 kinds/visibility/grouping rules and the
// ?v=<mtimeNs>-<size> cache-bust, plus the watch → debounce → revision bump.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  createCatalogService,
  scanProjectCatalog,
  shotIdFromFilename,
} from "./catalog.mjs";

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function seedProject(dir) {
  fs.mkdirSync(path.join(dir, "episodes", "ep001_shots"), { recursive: true });
  fs.mkdirSync(path.join(dir, "episodes", "ep001_review"), { recursive: true });
  fs.mkdirSync(path.join(dir, "inputs"), { recursive: true });
  fs.mkdirSync(path.join(dir, ".video", "render-cache"), { recursive: true });
  fs.writeFileSync(path.join(dir, "series.py"), "SERIES = 1");
  fs.writeFileSync(path.join(dir, "series.json"), '{"title":"T","cast":[]}');
  fs.writeFileSync(path.join(dir, "episodes", "series.json"), "{}"); // non-root → hidden
  fs.writeFileSync(path.join(dir, "_helper.py"), "hidden");
  fs.writeFileSync(path.join(dir, "notes.json"), "{}");
  fs.writeFileSync(path.join(dir, "random.png"), "png"); // parent not _review → hidden
  fs.writeFileSync(path.join(dir, "inputs", "ref.png"), "png"); // skip-dir
  fs.writeFileSync(path.join(dir, ".video", "render-cache", "c.mp4"), "mp4"); // skip-dir
  fs.writeFileSync(path.join(dir, "episodes", "ep001.py"), "def gen_episode(): ...");
  fs.writeFileSync(path.join(dir, "episodes", "ep001.mp4"), "episode-video");
  fs.writeFileSync(path.join(dir, "episodes", "ep001.episode.json"), "{}");
  fs.writeFileSync(path.join(dir, "episodes", "ep001.srt"), "1\n00:00 --> 00:01\nhi");
  fs.writeFileSync(path.join(dir, "episodes", "ep001_shots", "shot_s1_01.mp4"), "shot1");
  fs.writeFileSync(path.join(dir, "episodes", "ep001_shots", "shot_s1_02.mp4"), "shot2");
  fs.writeFileSync(path.join(dir, "episodes", "ep001_shots", "shot_s1_01.json"), "{}");
  fs.writeFileSync(path.join(dir, "episodes", "ep001_review", "_poster.png"), "poster");
  fs.writeFileSync(path.join(dir, "episodes", "ep001_review", "_board.png"), "board");
}

test("scanProjectCatalog applies the §2 visibility and grouping rules", () => {
  const dir = tmpdir("circuit-cat-");
  seedProject(dir);
  const { entries, rootPath } = scanProjectCatalog({ projectDir: dir, projectId: "p1" });
  assert.equal(rootPath, fs.realpathSync(dir));

  const files = entries.map((e) => e.file);
  assert.deepEqual(files, [
    "episodes/ep001.mp4",
    "episodes/ep001.py",
    "episodes/ep001.srt",
    "episodes/ep001_review/_board.png",
    "episodes/ep001_review/_poster.png",
    "series.json",
    "series.py",
  ]);
  // Hidden: _helper.py (underscore), notes.json + sidecars (.json), random.png
  // (not in a _review dir), shot mp4s (grouped), inputs/ + .video/ (skip-list),
  // episodes/series.json (the series.json exception is root-level only).

  const bible = entries.find((e) => e.file === "series.json");
  assert.equal(bible.kind, "json");
  assert.equal(bible.sourceKind, null);
  assert.match(bible.url, /^\/projects\/p1\/series\.json\?v=\d+-\d+$/);

  const episode = entries.find((e) => e.file === "episodes/ep001.mp4");
  assert.equal(episode.kind, "mp4");
  assert.equal(episode.sourceKind, "python");
  assert.match(episode.url, /^\/projects\/p1\/episodes\/ep001\.mp4\?v=\d+-\d+$/);
  assert.match(episode.artifact.metadataUrl, /ep001\.episode\.json\?v=\d+-\d+$/);
  assert.match(episode.artifact.srtUrl, /ep001\.srt\?v=\d+-\d+$/);
  assert.match(episode.artifact.posterUrl, /ep001_review\/_poster\.png\?v=\d+-\d+$/);
  assert.deepEqual(
    episode.artifact.shots.map((s) => s.id),
    ["s1_01", "s1_02"],
  );
  assert.equal(episode.artifact.shots[0].file, "episodes/ep001_shots/shot_s1_01.mp4");
  assert.match(episode.artifact.shots[0].url, /shot_s1_01\.mp4\?v=\d+-\d+$/);

  const py = entries.find((e) => e.file === "episodes/ep001.py");
  assert.equal(py.kind, "py");
  assert.equal(py.sourceKind, "python");
  const srt = entries.find((e) => e.file === "episodes/ep001.srt");
  assert.equal(srt.kind, "srt");
  const board = entries.find((e) => e.file === "episodes/ep001_review/_board.png");
  assert.equal(board.kind, "png");
});

test("a bare mp4 without a sidecar is a plain entry (no artifact, sourceKind null)", () => {
  const dir = tmpdir("circuit-cat-");
  fs.writeFileSync(path.join(dir, "clip.mp4"), "x");
  const { entries } = scanProjectCatalog({ projectDir: dir, projectId: "p2" });
  assert.equal(entries.length, 1);
  assert.equal(entries[0].kind, "mp4");
  assert.equal(entries[0].sourceKind, null);
  assert.equal(entries[0].artifact, undefined);
});

test("cache-bust token changes when the file changes (mtimeNs-size)", async () => {
  const dir = tmpdir("circuit-cat-");
  fs.writeFileSync(path.join(dir, "clip.mp4"), "x");
  const first = scanProjectCatalog({ projectDir: dir, projectId: "p" }).entries[0].url;
  await new Promise((resolve) => setTimeout(resolve, 5));
  fs.writeFileSync(path.join(dir, "clip.mp4"), "xy-longer");
  const second = scanProjectCatalog({ projectDir: dir, projectId: "p" }).entries[0].url;
  assert.notEqual(first, second, `expected new ?v token: ${first} vs ${second}`);
});

test("shotIdFromFilename strips the shot_ prefix", () => {
  assert.equal(shotIdFromFilename("shot_s1_01.mp4"), "s1_01");
  assert.equal(shotIdFromFilename("weird.mp4"), "weird");
});

test("catalog service: refresh bumps the revision and notifies; read carries the revision", () => {
  const dir = tmpdir("circuit-cat-");
  seedProject(dir);
  const revisions = [];
  const service = createCatalogService({
    projectDir: () => dir,
    onCatalogChanged: (revision) => revisions.push(revision),
  });
  const first = service.read("p1");
  assert.equal(first.revision, 0);
  assert.equal(first.entries.length, 7);
  service.refresh("p1");
  service.refresh("p1");
  assert.deepEqual(revisions, [1, 2]);
  assert.equal(service.read("p1").revision, 2);
  service.close();
});

test("catalog service: fs.watch → 150ms debounce → catalog_changed", async () => {
  const dir = tmpdir("circuit-cat-");
  seedProject(dir);
  const revisions = [];
  const service = createCatalogService({
    projectDir: () => dir,
    onCatalogChanged: (revision) => revisions.push(revision),
  });
  service.read("p1");
  service.activate("p1");
  await new Promise((resolve) => setTimeout(resolve, 50));
  fs.writeFileSync(path.join(dir, "episodes", "ep002.mp4"), "new-episode");

  const deadline = Date.now() + 5000;
  while (!revisions.length && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.ok(revisions.length >= 1, "watcher fired catalog_changed");
  const catalog = service.read("p1");
  assert.ok(catalog.entries.some((e) => e.file === "episodes/ep002.mp4"));
  assert.equal(catalog.revision, revisions.at(-1));
  service.close();
});
