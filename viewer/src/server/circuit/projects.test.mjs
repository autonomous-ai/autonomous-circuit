// Projects store tests — CRUD, snake_case pretty project.json, encode_cwd
// footgun, and the placeholder-name self-heal from the session JSONL.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  PLACEHOLDER_PROJECT_NAME,
  createProjectsStore,
  encodeCwd,
  parseLatestAiTitle,
  sessionJsonlPath,
} from "./projects.mjs";
import { sessionIdForProject } from "./driver.mjs";

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

test("encodeCwd replaces EVERY non-alphanumeric char with '-' (spaces and dots included)", () => {
  assert.equal(
    encodeCwd("/Users/ab/Library/Application Support/app.circuit.web/projects/u1"),
    "-Users-ab-Library-Application-Support-app-circuit-web-projects-u1",
  );
  // Existing hyphens map to themselves; a `/`-only encoding would differ.
  assert.equal(encodeCwd("/a-b/c_d.e f"), "-a-b-c-d-e-f");
  assert.equal(encodeCwd("abc123"), "abc123");
});

test("sessionJsonlPath is rooted at CLAUDE_CONFIG_DIR with the encoded cwd", () => {
  const cfg = tmpdir("circuit-cfg-");
  const workspace = tmpdir("circuit-ws-");
  const real = fs.realpathSync(workspace);
  const p = sessionJsonlPath(workspace, "abc-123", { CLAUDE_CONFIG_DIR: cfg });
  assert.equal(p, path.join(cfg, "projects", encodeCwd(real), "abc-123.jsonl"));
});

test("parseLatestAiTitle takes the last non-empty ai-title line", () => {
  const jsonl = [
    '{"type":"ai-title","aiTitle":"First Draft","sessionId":"s"}',
    '{"type":"assistant","message":"noise"}',
    '{"type":"ai-title","aiTitle":"Contract Bride","sessionId":"s"}',
    '{"type":"ai-title","aiTitle":"","sessionId":"s"}',
  ].join("\n");
  assert.equal(parseLatestAiTitle(jsonl), "Contract Bride");
  assert.equal(parseLatestAiTitle(""), null);
  assert.equal(parseLatestAiTitle('{"type":"assistant"}'), null);
});

test("create writes snake_case pretty project.json and returns a camelCase summary", () => {
  const root = tmpdir("circuit-projects-");
  const store = createProjectsStore({ rootDir: root });
  const summary = store.create("My Board");
  assert.match(summary.id, /^[0-9a-f-]{36}$/);
  assert.equal(summary.name, "My Board");
  assert.ok(summary.createdAt > 0);
  assert.equal(summary.hasModel, false);

  const raw = fs.readFileSync(path.join(root, summary.id, "project.json"), "utf8");
  assert.ok(raw.endsWith("\n"), "pretty file ends with a newline");
  assert.ok(raw.includes('  "created_at"'), "snake_case + 2-space indent");
  assert.ok(raw.includes('  "updated_at"'));
  const parsed = JSON.parse(raw);
  assert.deepEqual(Object.keys(parsed).sort(), ["created_at", "id", "name", "updated_at"]);
});

test("CRUD: list, open, rename, delete; empty name create falls back to the placeholder", () => {
  const root = tmpdir("circuit-projects-");
  const store = createProjectsStore({ rootDir: root });
  const a = store.create("");
  assert.equal(a.name, PLACEHOLDER_PROJECT_NAME);
  const b = store.create("Second");

  const listed = store.list();
  assert.equal(listed.length, 2);

  const opened = store.open(b.id);
  assert.equal(opened.workspaceRoot, path.join(root, b.id));

  const renamed = store.rename(b.id, "  Renamed  ");
  assert.equal(renamed.name, "Renamed");
  assert.throws(() => store.rename(b.id, "   "), /INVALID_ARGUMENT|empty/);

  store.remove(a.id);
  assert.ok(!fs.existsSync(path.join(root, a.id)));
  assert.equal(store.list().length, 1);
  assert.throws(() => store.open(a.id), /project not found/);
  assert.throws(() => store.open("../escape"), /project not found/);
});

test("hasModel: a built *.circuit.json counts; plain json and skip-dirs do not", () => {
  const root = tmpdir("circuit-projects-");
  const store = createProjectsStore({ rootDir: root });
  const p = store.create("X");
  const dir = path.join(root, p.id);

  fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
  fs.writeFileSync(path.join(dir, "boards", "main.board.json"), "{}");
  fs.writeFileSync(path.join(dir, "product.json"), "{}");
  fs.mkdirSync(path.join(dir, ".circuit", "cache"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".circuit", "cache", "x.circuit.json"), "{}");
  fs.mkdirSync(path.join(dir, "blocks"), { recursive: true });
  fs.writeFileSync(path.join(dir, "blocks", "reg.circuit.json"), "{}");
  assert.equal(store.get(p.id).hasModel, false, "plain json, caches, and blocks don't count");

  fs.writeFileSync(path.join(dir, "boards", "main.circuit.json"), "{}");
  assert.equal(store.get(p.id).hasModel, true);
});

test("placeholder-name self-heal adopts the session JSONL's ai-title and persists it", () => {
  const root = tmpdir("circuit-projects-");
  const cfg = tmpdir("circuit-cfg-");
  const env = { CLAUDE_CONFIG_DIR: cfg };
  const store = createProjectsStore({ rootDir: root, env, sessionIdForProject });
  const p = store.create(""); // placeholder

  const workspace = path.join(root, p.id);
  const jsonl = sessionJsonlPath(workspace, sessionIdForProject(p.id), env);
  fs.mkdirSync(path.dirname(jsonl), { recursive: true });
  fs.writeFileSync(jsonl, '{"type":"ai-title","aiTitle":"Runaway Heiress"}\n');

  const listed = store.list().find((s) => s.id === p.id);
  assert.equal(listed.name, "Runaway Heiress");
  // Persisted back into project.json (self-heal writes through).
  const meta = JSON.parse(fs.readFileSync(path.join(workspace, "project.json"), "utf8"));
  assert.equal(meta.name, "Runaway Heiress");

  // A user-chosen name is never overwritten.
  store.rename(p.id, "Chosen Name");
  fs.writeFileSync(jsonl, '{"type":"ai-title","aiTitle":"Something Else"}\n');
  assert.equal(store.get(p.id).name, "Chosen Name");
});
