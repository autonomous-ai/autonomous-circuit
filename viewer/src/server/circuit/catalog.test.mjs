// Catalog scanner tests — §2 kinds/visibility/grouping rules and the
// ?v=<mtimeNs>-<size> cache-bust, plus the watch → debounce → revision bump.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { createCatalogService, scanProjectCatalog } from "./catalog.mjs";

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function seedProject(dir) {
  fs.mkdirSync(path.join(dir, "boards", "main_review"), { recursive: true });
  fs.mkdirSync(path.join(dir, "boards", "main_fab"), { recursive: true });
  fs.mkdirSync(path.join(dir, "blocks"), { recursive: true });
  fs.mkdirSync(path.join(dir, "inputs"), { recursive: true });
  fs.mkdirSync(path.join(dir, ".circuit", "cache"), { recursive: true });
  // Visible entries.
  fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board />");
  fs.writeFileSync(path.join(dir, "NOTES.md"), "# notes");
  fs.writeFileSync(path.join(dir, "render.png"), "png"); // not in _review → visible
  // Hidden: json everywhere (surfaced via the board artifact / asset route).
  fs.writeFileSync(path.join(dir, "product.json"), "{}");
  fs.writeFileSync(path.join(dir, "parts.json"), "{}");
  fs.writeFileSync(path.join(dir, "boards", "main.board.json"), "{}");
  fs.writeFileSync(path.join(dir, "boards", "main.circuit.json"), "{}");
  // Hidden: underscore tsx, blocks/, .circuit/, inputs/.
  fs.writeFileSync(path.join(dir, "boards", "_helper.tsx"), "hidden");
  fs.writeFileSync(path.join(dir, "blocks", "regulator.tsx"), "golden");
  fs.writeFileSync(path.join(dir, ".circuit", "cache", "tmp.svg"), "svg");
  fs.writeFileSync(path.join(dir, "inputs", "ref.png"), "png");
  // Hidden: _review/ + _fab/ members — grouped under the board artifact.
  fs.writeFileSync(path.join(dir, "boards", "main_review", "_schematic.png"), "png");
  fs.writeFileSync(path.join(dir, "boards", "main_review", "_schematic.svg"), "svg");
  fs.writeFileSync(path.join(dir, "boards", "main_review", "_pcb.png"), "png");
  fs.writeFileSync(path.join(dir, "boards", "main_review", "_pcb_bottom.png"), "png");
  fs.writeFileSync(path.join(dir, "boards", "main_fab", "gerbers.zip"), "zip");
  fs.writeFileSync(path.join(dir, "boards", "main_fab", "bom.csv"), "csv");
  fs.writeFileSync(path.join(dir, "boards", "main_fab", "cpl.csv"), "csv");
  fs.writeFileSync(path.join(dir, "boards", "main_fab", "ORDER.md"), "# order");
  fs.writeFileSync(path.join(dir, "boards", "main_fab", "board.glb"), "glb");
}

test("scanProjectCatalog applies the §2 visibility and grouping rules", () => {
  const dir = tmpdir("circuit-cat-");
  seedProject(dir);
  const { entries, rootPath } = scanProjectCatalog({ projectDir: dir, projectId: "p1" });
  assert.equal(rootPath, fs.realpathSync(dir));

  const files = entries.map((e) => e.file);
  assert.deepEqual(files, ["NOTES.md", "boards/main.tsx", "render.png"]);
  // Hidden: _helper.tsx (underscore), every .json (sidecar/IR/product/parts),
  // _review/ + _fab/ members (grouped), blocks/ + .circuit/ + inputs/
  // (skip-list).

  const board = entries.find((e) => e.file === "boards/main.tsx");
  assert.equal(board.kind, "tsx");
  assert.equal(board.sourceKind, "tsx");
  assert.match(board.url, /^\/projects\/p1\/boards\/main\.tsx\?v=\d+-\d+$/);
  const artifact = board.artifact;
  assert.deepEqual(Object.keys(artifact).sort(), [
    "bomUrl",
    "circuitJsonUrl",
    "cplUrl",
    "gerbersUrl",
    "glbUrl",
    "metadataUrl",
    "orderUrl",
    "pcbBottomUrl",
    "pcbUrl",
    "schematicUrl",
  ]);
  assert.match(artifact.metadataUrl, /boards\/main\.board\.json\?v=\d+-\d+$/);
  assert.match(artifact.circuitJsonUrl, /boards\/main\.circuit\.json\?v=\d+-\d+$/);
  assert.match(artifact.schematicUrl, /boards\/main_review\/_schematic\.png\?v=\d+-\d+$/);
  assert.match(artifact.pcbUrl, /boards\/main_review\/_pcb\.png\?v=\d+-\d+$/);
  assert.match(artifact.pcbBottomUrl, /boards\/main_review\/_pcb_bottom\.png\?v=\d+-\d+$/);
  assert.match(artifact.gerbersUrl, /boards\/main_fab\/gerbers\.zip\?v=\d+-\d+$/);
  assert.match(artifact.bomUrl, /boards\/main_fab\/bom\.csv\?v=\d+-\d+$/);
  assert.match(artifact.cplUrl, /boards\/main_fab\/cpl\.csv\?v=\d+-\d+$/);
  assert.match(artifact.orderUrl, /boards\/main_fab\/ORDER\.md\?v=\d+-\d+$/);
  assert.match(artifact.glbUrl, /boards\/main_fab\/board\.glb\?v=\d+-\d+$/);

  const notes = entries.find((e) => e.file === "NOTES.md");
  assert.equal(notes.kind, "md");
  assert.equal(notes.sourceKind, null);
  const png = entries.find((e) => e.file === "render.png");
  assert.equal(png.kind, "png");
});

test("a bare tsx without built artifacts is a plain entry (no artifact object)", () => {
  const dir = tmpdir("circuit-cat-");
  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board />");
  const { entries } = scanProjectCatalog({ projectDir: dir, projectId: "p2" });
  assert.equal(entries.length, 1);
  assert.equal(entries[0].kind, "tsx");
  assert.equal(entries[0].sourceKind, "tsx");
  assert.equal(entries[0].artifact, undefined);
});

test("a partially built board carries only the artifact members on disk", () => {
  const dir = tmpdir("circuit-cat-");
  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board />");
  fs.writeFileSync(path.join(dir, "boards", "main.board.json"), "{}");
  const { entries } = scanProjectCatalog({ projectDir: dir, projectId: "p3" });
  assert.equal(entries.length, 1);
  assert.deepEqual(Object.keys(entries[0].artifact), ["metadataUrl"]);
});

test("cache-bust token changes when the file changes (mtimeNs-size)", async () => {
  const dir = tmpdir("circuit-cat-");
  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board />");
  const first = scanProjectCatalog({ projectDir: dir, projectId: "p" }).entries[0].url;
  await new Promise((resolve) => setTimeout(resolve, 5));
  fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board name='longer' />");
  const second = scanProjectCatalog({ projectDir: dir, projectId: "p" }).entries[0].url;
  assert.notEqual(first, second, `expected new ?v token: ${first} vs ${second}`);
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
  assert.equal(first.entries.length, 3);
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
  fs.writeFileSync(path.join(dir, "boards", "second.tsx"), "<board />");

  const deadline = Date.now() + 5000;
  while (!revisions.length && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.ok(revisions.length >= 1, "watcher fired catalog_changed");
  const catalog = service.read("p1");
  assert.ok(catalog.entries.some((e) => e.file === "boards/second.tsx"));
  assert.equal(catalog.revision, revisions.at(-1));
  service.close();
});
