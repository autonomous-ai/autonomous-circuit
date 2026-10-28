// Catalog scanner tests — §2 kinds/visibility/grouping rules and the
// ?v=<mtimeNs>-<size> cache-bust, plus the watch → debounce → revision bump.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import crypto from "node:crypto";
import os from "node:os";
import path from "node:path";

import { printAll3mf } from "./caseExport.mjs";
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
  // Hidden: .harness/ — the verdict Harness reads is progress, never an artifact.
  fs.mkdirSync(path.join(dir, ".harness"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".harness", "verdict.md"), "hidden");
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

test("team preview stays separate from accepted catalog entries and rejects changed media", () => {
  const dir = tmpdir("circuit-team-preview-");
  const mediaPath = ".circuit/team-previews/current/board/boards/main_review/_pcb.svg";
  const media = path.join(dir, mediaPath);
  fs.mkdirSync(path.dirname(media), { recursive: true });
  fs.writeFileSync(media, "<svg/>");
  fs.writeFileSync(path.join(dir, ".circuit/team-bridge.json"), JSON.stringify({ activeRun: "r001", status: "running", stage: "building", teamReady: false }));
  const preview = { schema: 1, run: "r001", teams: {
    board: { status: "draft", stage: "board", verified: false, files: ["design/main.kicad_pcb"],
      media: [{ file: "boards/main_review/_pcb.svg", path: mediaPath,
        bytes: 6, sha256: crypto.createHash("sha256").update("<svg/>").digest("hex") }], findings: [] },
    firmware: { status: "queued", stage: null, verified: false, files: [], media: [], findings: [] },
    case: { status: "queued", stage: null, verified: false, files: [], media: [], findings: [] },
  } };
  fs.writeFileSync(path.join(dir, ".circuit/team-preview.json"), JSON.stringify(preview));
  const first = scanProjectCatalog({ projectDir: dir, projectId: "p1" });
  assert.deepEqual(first.entries, []);
  assert.equal(first.teamPreview.teams.board.verified, false);
  assert.match(first.teamPreview.teams.board.media[0].url, /team-previews\/current\/board\/boards\/main_review\/_pcb\.svg/);
  fs.writeFileSync(media, "<svg changed/>");
  assert.deepEqual(scanProjectCatalog({ projectDir: dir, projectId: "p1" }).teamPreview.teams.board.media, []);
  const uppercasePath = ".circuit/team-previews/current/board/boards/main_review/_PCB.PNG";
  fs.writeFileSync(path.join(dir, uppercasePath), "png");
  preview.teams.board.media = [{ file: "boards/main_review/_PCB.PNG", path: uppercasePath,
    bytes: 3, sha256: crypto.createHash("sha256").update("png").digest("hex") }];
  fs.writeFileSync(path.join(dir, ".circuit/team-preview.json"), JSON.stringify(preview));
  assert.match(scanProjectCatalog({ projectDir: dir, projectId: "p1" }).teamPreview.teams.board.media[0].url,
    /_PCB\.PNG/);
  fs.writeFileSync(path.join(dir, ".circuit/team-bridge.json"), JSON.stringify({ activeRun: "r001", status: "candidate_ready", teamReady: true }));
  assert.equal(scanProjectCatalog({ projectDir: dir, projectId: "p1" }).teamPreview, undefined);
});

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

test("catalog service rescans on read and keeps publishing changes after watch EMFILE", async t => {
  const dir = tmpdir("circuit-watch-fallback-");
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.mkdirSync(path.join(dir, "case/out"), { recursive: true });
  const revisions = [];
  const service = createCatalogService({
    projectDir: () => dir,
    onCatalogChanged: revision => revisions.push(revision),
    watch: () => { throw Object.assign(new Error("watch limit"), { code: "EMFILE" }); },
    fallbackScanMs: 20,
  });
  t.after(() => service.close());
  service.activate("p");
  assert.equal(service.read("p").projectOverviewUrl, undefined);

  const overview = path.join(dir, "case/overview.html");
  fs.writeFileSync(overview, "<main>Draft</main>");
  fs.writeFileSync(path.join(dir, "case/out/parts.json"), JSON.stringify({ overview: "../overview.html", parts: [] }));
  const first = service.read("p");
  assert.match(first.projectOverviewUrl, /\/case\/overview\.html\?v=/);
  assert.equal(first.revision, 1, "a read detects files written after the watcher failed");

  fs.appendFileSync(overview, "\nupdated");
  const deadline = Date.now() + 2000;
  while (revisions.length < 2 && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.deepEqual(revisions, [1, 2], "fallback polling emits catalog_changed for later writes");
  assert.notEqual(service.read("p").projectOverviewUrl, first.projectOverviewUrl);

  service.deactivate("p");
  fs.appendFileSync(overview, "\nmore");
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.deepEqual(revisions, [1, 2], "deactivation stops fallback polling");
});

test("catalog service starts fallback after an asynchronous watcher error", async t => {
  const dir = tmpdir("circuit-watch-error-");
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  let onError;
  let closed = false;
  const service = createCatalogService({
    projectDir: () => dir,
    watch: () => ({
      on: (event, callback) => { if (event === "error") onError = callback; },
      close: () => { closed = true; },
    }),
    fallbackScanMs: 20,
  });
  t.after(() => service.close());
  service.activate("p");
  assert.equal(typeof onError, "function");
  onError(Object.assign(new Error("watch limit"), { code: "EMFILE" }));
  assert.equal(closed, true);
  fs.writeFileSync(path.join(dir, "draft.png"), "image");
  assert.ok(service.read("p").entries.some(entry => entry.file === "draft.png"));
});

test("firmware/ is grouped under every board entry's artifact, as text sources only, and hidden as entries", () => {
  const dir = tmpdir("circuit-cat-");
  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board />");
  fs.mkdirSync(path.join(dir, "firmware", "src"), { recursive: true });
  fs.mkdirSync(path.join(dir, "firmware", ".pio", "build"), { recursive: true });
  fs.mkdirSync(path.join(dir, "firmware", "build"), { recursive: true });
  fs.writeFileSync(path.join(dir, "firmware", "README.md"), "# flash it");
  fs.writeFileSync(path.join(dir, "firmware", "platformio.ini"), "[env:pico]");
  fs.writeFileSync(path.join(dir, "firmware", "Makefile"), "all:");
  fs.writeFileSync(path.join(dir, "firmware", "src", "main.c"), "int main(void) {}");
  fs.writeFileSync(path.join(dir, "firmware", "src", "pins.h"), "#define LED 25");
  // Not firmware anyone reads: build output, binaries, a dotfile, the build trees.
  fs.writeFileSync(path.join(dir, "firmware", "firmware.uf2"), "binary");
  fs.writeFileSync(path.join(dir, "firmware", "firmware.elf"), "binary");
  fs.writeFileSync(path.join(dir, "firmware", ".gitignore"), "build/");
  fs.writeFileSync(path.join(dir, "firmware", ".pio", "build", "main.o"), "obj");
  fs.writeFileSync(path.join(dir, "firmware", "build", "main.c"), "generated");

  const { entries } = scanProjectCatalog({ projectDir: dir, projectId: "fw" });
  // The README is an .md, but it is the firmware's, so it is not a standalone entry.
  assert.deepEqual(entries.map((e) => e.file), ["boards/main.tsx"]);

  const firmware = entries[0].artifact.firmware;
  assert.ok(firmware, "the board entry carries the firmware tree");
  assert.deepEqual(
    firmware.files.map((f) => f.file),
    ["Makefile", "README.md", "platformio.ini", "src/main.c", "src/pins.h"],
  );
  assert.equal(firmware.truncated, false);
  assert.match(firmware.readmeUrl, /^\/projects\/fw\/firmware\/README\.md\?v=\d+-\d+$/);
  const main = firmware.files.find((f) => f.file === "src/main.c");
  assert.match(main.url, /^\/projects\/fw\/firmware\/src\/main\.c\?v=\d+-\d+$/);
  assert.equal(main.bytes, "int main(void) {}".length);

  // An empty firmware/ dir is no firmware: the tab keeps its "not yet" copy.
  const bare = tmpdir("circuit-cat-");
  fs.mkdirSync(path.join(bare, "boards"), { recursive: true });
  fs.mkdirSync(path.join(bare, "firmware"), { recursive: true });
  fs.writeFileSync(path.join(bare, "boards", "main.tsx"), "<board />");
  const none = scanProjectCatalog({ projectDir: bare, projectId: "fw2" }).entries[0];
  assert.equal(none.artifact, undefined);
});

test("a native board entry carries the firmware tree too", () => {
  const dir = tmpdir("circuit-cat-");
  fs.mkdirSync(path.join(dir, "design"), { recursive: true });
  fs.mkdirSync(path.join(dir, "firmware"), { recursive: true });
  fs.writeFileSync(path.join(dir, "design", "main.kicad_pcb"), "(kicad_pcb)");
  fs.writeFileSync(path.join(dir, "firmware", "main.py"), "import board");
  const { entries } = scanProjectCatalog({ projectDir: dir, projectId: "nat" });
  const board = entries.find((e) => e.sourceKind === "kicad-native");
  assert.ok(board);
  assert.deepEqual(board.artifact.firmware.files.map((f) => f.file), ["main.py"]);
  assert.equal(board.artifact.firmware.readmeUrl, undefined);
});

test("firmware/flash.json surfaces as artifact.firmware.flashUrl — what enables the Flash button", () => {
  const dir = tmpdir("circuit-cat-");
  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.mkdirSync(path.join(dir, "firmware"), { recursive: true });
  fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board />");
  fs.writeFileSync(path.join(dir, "firmware", "README.md"), "# flash it");
  fs.writeFileSync(path.join(dir, "firmware", "main.c"), "int main(void) {}");
  let firmware = scanProjectCatalog({ projectDir: dir, projectId: "fw" }).entries[0].artifact.firmware;
  assert.equal(firmware.flashUrl, undefined);
  fs.writeFileSync(path.join(dir, "firmware", "flash.json"), JSON.stringify({ family: "esp32" }));
  firmware = scanProjectCatalog({ projectDir: dir, projectId: "fw" }).entries[0].artifact.firmware;
  assert.match(firmware.flashUrl, /^\/projects\/fw\/firmware\/flash\.json\?v=\d+-\d+$/);
  assert.ok(firmware.files.some((f) => f.file === "flash.json"), "the recipe is also a readable file in the tab");
});

test("case/ is grouped under the native board's artifact: parts from parts.json, the contract, and the check's status", async () => {
  const crypto = await import("node:crypto");
  const sha = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  const dir = tmpdir("circuit-cat-");
  fs.mkdirSync(path.join(dir, "design"), { recursive: true });
  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.mkdirSync(path.join(dir, "case", "out"), { recursive: true });
  fs.writeFileSync(path.join(dir, "design", "main.kicad_pcb"), "(kicad_pcb)");
  const contract = path.join(dir, "boards", "main.enclosure.json");
  fs.writeFileSync(contract, JSON.stringify({ schema: 2 }));
  fs.writeFileSync(path.join(dir, "boards", "main.board.json"), JSON.stringify({ source: { engine: "kicad-native", file: "design/main.kicad_pcb" },
    native: { inputRoot: ".", inputs: { "design/main.kicad_pcb": sha(path.join(dir, "design/main.kicad_pcb")) }, publication: "complete", previewDir: "boards/main_review/test" } }));

  // A contract and no case yet: the tab can say the case comes next.
  let enclosure = scanProjectCatalog({ projectDir: dir, projectId: "cs" }).entries[0].artifact.case;
  assert.equal(enclosure.state, "none");
  assert.deepEqual(enclosure.parts, []);
  assert.match(enclosure.contractUrl, /^\/projects\/cs\/boards\/main\.enclosure\.json\?v=\d+-\d+$/);

  const out = path.join(dir, "case", "out");
  for (const name of ["front.stl", "front.step", "coupon.stl"]) fs.writeFileSync(path.join(out, name), `solid ${name}`);
  fs.writeFileSync(path.join(out, "parts.json"), JSON.stringify({
    parts: [
      { name: "front", role: "shell", stl: "front.stl", step: "front.step", printRotateDeg: [180, 0, 0], supports: false },
      { name: "coupon", role: "coupon", stl: "coupon.stl", step: "coupon.step" },   // no STEP on disk
      { name: "escape", role: "shell", stl: "../../boards/main.enclosure.json" },   // outside case/out: dropped
      { name: "mesh-less", role: "lid", stl: "lid.stl" },                           // missing: dropped
    ],
  }));
  enclosure = scanProjectCatalog({ projectDir: dir, projectId: "cs" }).entries[0].artifact.case;
  assert.equal(enclosure.state, "stale", "built, never checked");
  assert.deepEqual(enclosure.parts.map((p) => [p.name, p.role, Boolean(p.stepUrl)]), [["front", "shell", true], ["coupon", "coupon", false]]);
  assert.match(enclosure.parts[0].stlUrl, /^\/projects\/cs\/case\/out\/front\.stl\?v=\d+-\d+$/);
  assert.deepEqual(enclosure.parts[0].printRotateDeg, [180, 0, 0]);
  assert.equal(enclosure.parts[0].supports, false);

  // A passing report bound to this board and these files.
  fs.mkdirSync(path.join(dir, "case", "reports"), { recursive: true });
  const report = path.join(dir, "case", "reports", "case-check.json");
  const bound = { passed: true, meshValidation: "surface-v1", errors: [], contractSha: sha(contract),
    partShas: { "front.stl": sha(path.join(out, "front.stl")), "parts.json": sha(path.join(out, "parts.json")) } };
  fs.writeFileSync(report, JSON.stringify(bound));
  enclosure = scanProjectCatalog({ projectDir: dir, projectId: "cs" }).entries[0].artifact.case;
  assert.equal(enclosure.state, "passed");
  assert.match(enclosure.reportUrl, /case-check\.json/);
  fs.writeFileSync(report, JSON.stringify({ ...bound, meshValidation: undefined }));
  const { caseStatus: readCaseStatus } = await import("./catalog.mjs");
  assert.equal(readCaseStatus(dir, "main").state, "stale", "old extent-only checks cannot authorize Print all");
  fs.writeFileSync(report, JSON.stringify(bound));

  // A rebuilt part makes it stale; a failed check says how many errors.
  fs.writeFileSync(path.join(out, "front.stl"), "solid moved");
  assert.equal(scanProjectCatalog({ projectDir: dir, projectId: "cs" }).entries[0].artifact.case.state, "stale");

  bound.partShas["front.stl"] = sha(path.join(out, "front.stl"));
  fs.writeFileSync(report, JSON.stringify({ ...bound, passed: false, errors: [{ kind: "case_collides" }, { kind: "case_interface_blocked" }] }));
  enclosure = scanProjectCatalog({ projectDir: dir, projectId: "cs" }).entries[0].artifact.case;
  assert.deepEqual([enclosure.state, enclosure.reason], ["failed", "2 case errors"]);
  // A new board makes any report stale.
  fs.writeFileSync(contract, JSON.stringify({ schema: 2, moved: true }));
  assert.equal(scanProjectCatalog({ projectDir: dir, projectId: "cs" }).entries[0].artifact.case.state, "stale");

  fs.writeFileSync(contract, JSON.stringify({ schema: 2 }));
  fs.writeFileSync(path.join(dir, "design", "main.kicad_pcb"), "(kicad_pcb moved_display)");
  const changed = scanProjectCatalog({ projectDir: dir, projectId: "cs" }).entries.find(e => e.file === "design/main.kicad_pcb");
  assert.equal(changed.nativeStale, true);
  assert.equal(changed.artifact.case.state, "stale", "changed copper invalidates case status before the next publish");
  fs.writeFileSync(path.join(dir, "design", "other.kicad_pcb"), "(kicad_pcb)");
  fs.writeFileSync(path.join(dir, "boards", "other.enclosure.json"), JSON.stringify({ schema: 2, different: true }));
  fs.writeFileSync(path.join(dir, "boards", "other.board.json"), JSON.stringify({ source: { engine: "kicad-native", file: "design/other.kicad_pcb" },
    native: { inputRoot: ".", inputs: {}, publication: "complete" } }));
  const { caseStatus } = await import("./catalog.mjs");
  assert.equal(caseStatus(dir, "other").state, "stale", "a report for main cannot bless other");

  // The meshes are never standalone entries.
  assert.equal(scanProjectCatalog({ projectDir: dir, projectId: "cs" }).entries.filter((e) => e.file.startsWith("case/")).length, 0);
});

test("catalog shows checked native print plates and drops stale plate assignments", t => {
  const dir = tmpdir("circuit-case-plates-");
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  for (const folder of ["design", "boards", "case/out", "case/reports", "case/print"]) {
    fs.mkdirSync(path.join(dir, folder), { recursive: true });
  }
  const sha = value => crypto.createHash("sha256").update(value).digest("hex");
  const pcb = path.join(dir, "design/main.kicad_pcb"), contract = path.join(dir, "boards/main.enclosure.json");
  fs.writeFileSync(pcb, "(kicad_pcb)");
  fs.writeFileSync(contract, "{}");
  fs.writeFileSync(path.join(dir, "boards/main.board.json"), JSON.stringify({
    source: { engine: "kicad-native", file: "design/main.kicad_pcb" },
    native: { inputRoot: ".", inputs: { "design/main.kicad_pcb": sha(fs.readFileSync(pcb)) }, publication: "complete", previewDir: "boards/main_review/test" },
  }));
  const stl = "solid triangle\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid triangle";
  const parts = [
    { name: "base", role: "shell", stl: "base.stl" },
    { name: "lid", role: "lid", stl: "lid.stl" },
    { name: "coupon", role: "coupon", stl: "coupon.stl" },
  ];
  for (const part of parts) fs.writeFileSync(path.join(dir, "case/out", part.stl), stl);
  const manifest = { parts, printProject: { file: "../print/production.3mf", report: "../print/check.json" } };
  const manifestFile = path.join(dir, "case/out/parts.json");
  fs.writeFileSync(manifestFile, JSON.stringify(manifest));
  const projectFile = path.join(dir, "case/print/production.3mf");
  fs.writeFileSync(projectFile, printAll3mf(dir, { parts: parts.slice(0, 2) }));
  const reportFile = path.join(dir, "case/print/check.json");
  const report = { passed: true, file: "case/print/production.3mf", sha256: sha(fs.readFileSync(projectFile)),
    meshes: parts.slice(0, 2).map(part => ({ name: `${part.name}.stl`, canonicalSTLSha256: sha(stl), printRotateDeg: [0, 0, 0] })),
    plates: [
      { index: 1, name: "Body", parts: ["base.stl"] },
      { index: 2, name: "Cover", parts: ["lid.stl"] },
    ] };
  fs.writeFileSync(reportFile, JSON.stringify(report));
  fs.writeFileSync(path.join(dir, "case/reports/case-check.json"), JSON.stringify({
    passed: true, meshValidation: "surface-v1", errors: [], contractSha: sha(fs.readFileSync(contract)),
    partShas: Object.fromEntries(["parts.json", ...parts.map(part => part.stl)].map(name =>
      [name, sha(fs.readFileSync(path.join(dir, "case/out", name)))])),
  }));
  const read = () => scanProjectCatalog({ projectDir: dir, projectId: "plate" }).entries[0];
  const expected = [
    { index: 1, name: "Body", parts: ["base"] },
    { index: 2, name: "Cover", parts: ["lid"] },
  ];
  assert.deepEqual(read().artifact.case.plates, expected);
  assert.deepEqual(read().artifact.case.parts.map(part => part.name), ["base", "lid", "coupon"]);
  report.sha256 = "0".repeat(64);
  fs.writeFileSync(reportFile, JSON.stringify(report));
  assert.deepEqual(read().artifact.case.plates, [], "unverified 3MF cannot claim plate membership");
  report.sha256 = sha(fs.readFileSync(projectFile));
  fs.writeFileSync(reportFile, JSON.stringify(report));
  fs.appendFileSync(path.join(dir, "case/out/base.stl"), "changed");
  assert.deepEqual(read().artifact.case.plates, [], "changed mesh invalidates case check and plates");
  fs.writeFileSync(path.join(dir, "case/out/base.stl"), stl);
  fs.writeFileSync(pcb, "(kicad_pcb changed)");
  const changed = read();
  assert.equal(changed.nativeStale, true);
  assert.deepEqual(changed.artifact.case.plates, [], "changed PCB cannot claim checked plates");
});

test('Case overview discovers the motion demo or an explicit confined manifest file', t => {
  const dir=tmpdir('circuit-case-overview-');t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  fs.mkdirSync(path.join(dir,'design'));fs.mkdirSync(path.join(dir,'boards'));fs.mkdirSync(path.join(dir,'case/out'),{recursive:true});fs.mkdirSync(path.join(dir,'case/motion'));
  fs.writeFileSync(path.join(dir,'design/main.kicad_pcb'),'(kicad_pcb)');fs.writeFileSync(path.join(dir,'boards/main.board.json'),JSON.stringify({source:{engine:'kicad-native',file:'design/main.kicad_pcb'}}));
  const manifest=path.join(dir,'case/out/parts.json');fs.writeFileSync(manifest,JSON.stringify({parts:[]}));
  fs.writeFileSync(path.join(dir,'case/motion/motion-view.html'),'<canvas></canvas>');
  const read=()=>scanProjectCatalog({projectDir:dir,projectId:'p'}).entries.find(e=>e.file==='design/main.kicad_pcb').artifact.case;
  assert.match(read().overviewUrl,/case\/motion\/motion-view\.html\?v=/);
  fs.writeFileSync(path.join(dir,'case/custom.html'),'<p>Custom</p>');fs.writeFileSync(manifest,JSON.stringify({parts:[],overview:'../custom.html'}));assert.match(read().overviewUrl,/case\/custom\.html/);
  fs.writeFileSync(path.join(dir,'outside.html'),'<p>outside</p>');fs.writeFileSync(manifest,JSON.stringify({parts:[],overview:'../../outside.html'}));assert.equal(read().overviewUrl,undefined);
  fs.symlinkSync(path.join(dir,'outside.html'),path.join(dir,'case/link.html'));fs.writeFileSync(manifest,JSON.stringify({parts:[],overview:'../link.html'}));assert.equal(read().overviewUrl,undefined);
});

test('project overview is available before a board only from an explicit confined HTML manifest member', t => {
  const dir = tmpdir('circuit-project-overview-');
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.mkdirSync(path.join(dir, 'case/out'), { recursive: true });
  const overview = path.join(dir, 'case/overview.html');
  fs.writeFileSync(overview, '<main>Reference layout</main>');
  const manifest = path.join(dir, 'case/out/parts.json');
  const read = () => scanProjectCatalog({ projectDir: dir, projectId: 'p' });

  assert.equal(read().projectOverviewUrl, undefined, 'a loose HTML file is not a published overview');
  fs.writeFileSync(manifest, JSON.stringify({ parts: [], overview: '../overview.html' }));
  const published = read();
  assert.deepEqual(published.entries, [], 'an overview must not fabricate a board entry');
  assert.match(published.projectOverviewUrl, /^\/projects\/p\/case\/overview\.html\?v=\d+-\d+$/);
  fs.appendFileSync(overview, '\nchanged');
  assert.notEqual(read().projectOverviewUrl, published.projectOverviewUrl, 'the URL follows changed overview bytes');

  fs.writeFileSync(path.join(dir, 'outside.html'), '<main>outside</main>');
  fs.writeFileSync(manifest, JSON.stringify({ parts: [], overview: '../../outside.html' }));
  assert.equal(read().projectOverviewUrl, undefined, 'traversal outside case/ is refused');
  fs.symlinkSync(path.join(dir, 'outside.html'), path.join(dir, 'case/link.html'));
  fs.writeFileSync(manifest, JSON.stringify({ parts: [], overview: '../link.html' }));
  assert.equal(read().projectOverviewUrl, undefined, 'a symlink outside case/ is refused');
  fs.writeFileSync(manifest, JSON.stringify({ parts: [], overview: '../overview.txt' }));
  assert.equal(read().projectOverviewUrl, undefined, 'non-HTML members are refused');
});

test('firmware wiring guide stays discoverable beyond the source-list cap and changes its cache token after edits',t => {
  const dir=tmpdir('circuit-wiring-');t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  fs.mkdirSync(path.join(dir,'design'));fs.mkdirSync(path.join(dir,'firmware'));
  fs.writeFileSync(path.join(dir,'design/main.kicad_pcb'),'(kicad_pcb)');
  const wiring=path.join(dir,'firmware/WIRING.md');fs.writeFileSync(wiring,'# Physical pins\nSCL → GPIO4');
  for(let i=0;i<400;i++)fs.writeFileSync(path.join(dir,`firmware/A${i}.h`),'// source');
  const read=()=>scanProjectCatalog({projectDir:dir,projectId:'p'}).entries.find(e=>e.sourceKind==='kicad-native').artifact.firmware;
  const before=read();assert.equal(before.truncated,true);assert.match(before.wiringUrl,/firmware\/WIRING\.md\?v=/);
  fs.appendFileSync(wiring,'\nGround → GND');assert.notEqual(read().wiringUrl,before.wiringUrl);
  fs.unlinkSync(wiring);assert.equal(read().wiringUrl,undefined);
});
