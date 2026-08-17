// HTTP layer tests — the §2 command surface end-to-end on an ephemeral port:
// IpcError shapes, settings round-trip, project CRUD, chat turn → SSE event
// stream (against the fake claude), catalog read + catalog_changed, and the
// /projects asset routes (content types, Range, traversal guard).

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createCircuitServices, quietLimitMs } from "./http.mjs";
import { sessionIdForProject } from "./driver.mjs";
import { sessionJsonlPath } from "./projects.mjs";
import { MockEventSource, waitFor } from "./fixtures/sse.mjs";

const FIXTURE_DIR = path.dirname(fileURLToPath(import.meta.url));
const FAKE_CLAUDE = path.join(FIXTURE_DIR, "fixtures", "fake-claude.mjs");

function tmpdir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

async function bootServer({ scenario } = {}) {
  const home = tmpdir("circuit-home-");
  const cfgDir = tmpdir("circuit-cfg-");
  const scenarioPath = path.join(home, "scenario.json");
  fs.writeFileSync(scenarioPath, JSON.stringify(scenario || {}));
  const env = {
    ...process.env,
    CIRCUIT_HOME: home,
    CLAUDE_CONFIG_DIR: cfgDir,
    CIRCUIT_CLAUDE_BIN: FAKE_CLAUDE,
    CIRCUIT_FAKE_SCENARIO: scenarioPath,
  };
  const services = createCircuitServices({ env });
  const server = http.createServer((req, res) => {
    services.apiMiddleware(req, res, () => {
      services.assetMiddleware(req, res, () => {
        res.statusCode = 404;
        res.end("fallthrough");
      });
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;

  async function post(cmd, body = {}) {
    const response = await fetch(`${base}/api/${cmd}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    return { status: response.status, body: text ? JSON.parse(text) : null };
  }

  function close() {
    services.close();
    server.close();
  }

  return { services, server, base, post, close, home, cfgDir, env };
}

test("unknown command → 404 IpcError; GET on a command → 405; bad JSON → 400", async () => {
  const s = await bootServer();
  try {
    const unknown = await s.post("slice_run", {});
    assert.equal(unknown.status, 404);
    assert.equal(unknown.body.code, "UNKNOWN_COMMAND");
    assert.ok(unknown.body.message.includes("slice_run"));

    const got = await fetch(`${s.base}/api/project_list`);
    assert.equal(got.status, 405);
    const gotBody = await got.json();
    assert.equal(gotBody.code, "METHOD_NOT_ALLOWED");

    const bad = await fetch(`${s.base}/api/project_list`, {
      method: "POST",
      body: "{nope",
    });
    assert.equal(bad.status, 400);
    assert.equal((await bad.json()).code, "INVALID_ARGUMENT");
  } finally {
    s.close();
  }
});

test("app_info, app_prereq_check shape, settings round-trip and app_set_model", async () => {
  const s = await bootServer();
  try {
    const info = await s.post("app_info");
    assert.equal(info.status, 200);
    assert.equal(info.body.rootPath, s.services.projectsRoot);
    assert.equal(info.body.pid, process.pid);

    const prereq = await s.post("app_prereq_check");
    assert.equal(prereq.status, 200);
    // CIRCUIT_CLAUDE_BIN points at the stub → claude reads as found.
    assert.equal(prereq.body.claudeCli.found, true);
    // Contract §2 probes: node ≥22.12, toolchain node_modules, python ≥3.10,
    // kicad-cli reported-not-required.
    assert.ok("found" in prereq.body.node);
    assert.ok("healthy" in prereq.body.node);
    assert.ok("found" in prereq.body.toolchain);
    assert.ok("found" in prereq.body.python);
    assert.ok("healthy" in prereq.body.python);
    assert.ok("found" in prereq.body.kicadCli);
    assert.equal(prereq.body.kicadCli.required, false);
    // Donor probes are gone.
    assert.equal(prereq.body.ffmpeg, undefined);
    assert.equal(prereq.body.slicer, undefined);

    const defaults = await s.post("app_settings_read");
    assert.equal(defaults.body.hasOnboarded, false);
    assert.equal(defaults.body.autoBuild, true);
    assert.equal(defaults.body.model, undefined);

    await s.post("app_settings_write", {
      settings: { hasOnboarded: true, autoBuild: false, model: "opus" },
    });
    const written = await s.post("app_settings_read");
    assert.equal(written.body.hasOnboarded, true);
    assert.equal(written.body.autoBuild, false);
    assert.equal(written.body.model, "opus");
    // The file persists exactly the circuit settings — model and effort
    // included, because both are product decisions the app must pin rather
    // than leave to whatever the CLI defaults to.
    const onDisk = JSON.parse(fs.readFileSync(path.join(s.home, "settings.json"), "utf8"));
    assert.deepEqual(Object.keys(onDisk).sort(), ["autoBuild", "effort", "hasOnboarded", "model"]);

    const setModel = await s.post("app_set_model", { model: "sonnet" });
    assert.equal(setModel.body.model, "sonnet");
  } finally {
    s.close();
  }
});

test("project CRUD over HTTP with IpcError on a missing project", async () => {
  const s = await bootServer();
  try {
    const created = await s.post("project_create", { req: { name: "Ep Test" } });
    assert.equal(created.status, 200);
    const id = created.body.id;

    const listed = await s.post("project_list");
    assert.equal(listed.body.length, 1);

    const opened = await s.post("project_open", { id });
    assert.equal(opened.body.workspaceRoot, path.join(s.services.projectsRoot, id));

    const renamed = await s.post("project_rename", { id, name: "Renamed" });
    assert.equal(renamed.body.name, "Renamed");

    const missing = await s.post("project_open", { id: "nope" });
    assert.equal(missing.status, 404);
    assert.equal(missing.body.code, "PROJECT_NOT_FOUND");

    const deleted = await s.post("project_delete", { id });
    assert.equal(deleted.status, 200);
    assert.equal((await s.post("project_list")).body.length, 0);
  } finally {
    s.close();
  }
});

// project_create, chat_start_turn and its two siblings were ported from Tauri
// commands that name their argument struct, so they read `{req: {...}}` while
// the other twenty commands read the body flat. Everyone who scripted the API
// posted the flat shape, and project_create — whose name is optional — answered
// 200 with a project called "New project" instead of theirs. An unread field
// looked exactly like an absent one.
test("a flat body reaches project_create, which used to silently name it 'New project'", async () => {
  const s = await bootServer();
  try {
    const flat = await s.post("project_create", { name: "Flat Body" });
    assert.equal(flat.status, 200);
    assert.equal(flat.body.name, "Flat Body");

    // The shipped client's shape still works, and wins if somebody sends both.
    const wrapped = await s.post("project_create", { req: { name: "Wrapped" }, name: "Ignored" });
    assert.equal(wrapped.body.name, "Wrapped");

    // No name at all is still the placeholder — that is the "New project"
    // button, not a dropped field, and the self-heal upgrades it later.
    const blank = await s.post("project_create", {});
    assert.equal(blank.body.name, "New project");

    // The chat commands take both shapes too, and a flat body with no project
    // is refused rather than routed at nothing.
    const noProject = await s.post("chat_start_turn", { projectId: "nope", userMessage: "hi" });
    assert.equal(noProject.status, 404);
    assert.equal(noProject.body.code, "PROJECT_NOT_FOUND");
  } finally {
    s.close();
  }
});

test("chat turn end-to-end over SSE: enveloped chat_events in order, plus catalog_changed from the watcher", async () => {
  const s = await bootServer({
    scenario: {
      plan: {
        lines: [
          {
            type: "stream_event",
            event: { type: "content_block_delta", delta: { type: "text_delta", text: "Sketching…" } },
          },
          {
            type: "assistant",
            message: {
              content: [{ type: "tool_use", id: "tp", name: "ExitPlanMode", input: { plan: "# The Pilot" } }],
            },
          },
        ],
        sleepAfterMs: 2000,
      },
    },
  });
  try {
    // Manual mode so the plan turn doesn't chain a build here.
    await s.post("app_settings_write", { settings: { hasOnboarded: true, autoBuild: false } });
    const { body: project } = await s.post("project_create", { req: { name: "SSE" } });
    await s.post("project_open", { id: project.id });

    const received = [];
    const catalogEvents = [];
    const es = new MockEventSource(`${s.base}/api/events`);
    es.addEventListener("chat_event", (e) => received.push(JSON.parse(e.data)));
    es.addEventListener("catalog_changed", (e) => catalogEvents.push(JSON.parse(e.data)));
    await new Promise((resolve) => setTimeout(resolve, 100)); // let SSE attach

    const started = await s.post("chat_start_turn", {
      req: { projectId: project.id, userMessage: "design a board" },
    });
    assert.equal(started.status, 200);
    assert.ok(started.body.turnId);

    await waitFor(() => received.some((e) => e.kind === "turn_end"), { timeoutMs: 15000 });
    const kinds = received.map((e) => e.kind);
    assert.deepEqual(kinds, ["turn_start", "text_delta", "plan_proposed", "turn_end"]);
    assert.ok(received.every((e) => e.projectId === project.id), "projectId envelope on every event");
    assert.equal(received[0].turnId, started.body.turnId);
    assert.equal(received[2].plan, "# The Pilot");

    // The project watcher (activated by project_open) turns a new artifact
    // into a catalog_changed with a bumped revision.
    fs.writeFileSync(
      path.join(s.services.projectsRoot, project.id, "main.tsx"),
      "<board />",
    );
    await waitFor(() => catalogEvents.length >= 1, { timeoutMs: 8000 });
    assert.ok(catalogEvents[0].revision >= 1);

    es.close();
  } finally {
    s.close();
  }
});

test("chat_session_state rehydrates history (with blocks) from the session JSONL", async () => {
  const s = await bootServer();
  try {
    const { body: project } = await s.post("project_create", { req: { name: "H" } });
    const sessionId = sessionIdForProject(project.id);
    const workspace = path.join(s.services.projectsRoot, project.id);
    const jsonl = sessionJsonlPath(workspace, sessionId, s.env);
    fs.mkdirSync(path.dirname(jsonl), { recursive: true });
    fs.writeFileSync(
      jsonl,
      [
        JSON.stringify({
          type: "user",
          message: { role: "user", content: "design a board" },
          timestamp: "2026-08-01T05:00:00.000Z",
        }),
        JSON.stringify({
          type: "assistant",
          message: {
            content: [
              { type: "text", text: "On it." },
              { type: "tool_use", id: "u1", name: "Bash", input: { command: "circuit" } },
            ],
          },
          timestamp: "2026-08-01T05:00:01.000Z",
        }),
        JSON.stringify({
          type: "user",
          message: {
            content: [{ type: "tool_result", tool_use_id: "u1", is_error: false, content: "ok" }],
          },
          timestamp: "2026-08-01T05:00:02.000Z",
        }),
      ].join("\n"),
    );

    const state = await s.post("chat_session_state", { projectId: project.id });
    assert.equal(state.status, 200);
    assert.equal(state.body.sessionId, sessionId);
    assert.equal(state.body.turnInProgress, false);
    assert.equal(state.body.history.length, 2);
    assert.equal(state.body.history[0].role, "user");
    assert.equal(state.body.history[1].role, "assistant");
    assert.equal(state.body.history[1].blocks.length, 2);
    assert.equal(state.body.history[1].blocks[1].tool, "Bash");
    assert.equal(state.body.history[1].blocks[1].status, "ok");
  } finally {
    s.close();
  }
});

test("catalog_read requires an open project; project_catalog_read serves §2 entries", async () => {
  const s = await bootServer();
  try {
    const empty = await s.post("catalog_read");
    assert.deepEqual(empty.body.entries, []);

    const { body: project } = await s.post("project_create", { req: { name: "Cat" } });
    const dir = path.join(s.services.projectsRoot, project.id);
    fs.mkdirSync(path.join(dir, "boards"), { recursive: true });
    fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board />");
    fs.writeFileSync(path.join(dir, "boards", "main.board.json"), "{}");

    const catalog = await s.post("project_catalog_read", { id: project.id });
    assert.equal(catalog.status, 200);
    assert.equal(catalog.body.entries.length, 1);
    const entry = catalog.body.entries[0];
    assert.equal(entry.file, "boards/main.tsx");
    assert.match(entry.url, /\?v=\d+-\d+$/);
    assert.ok(entry.artifact.metadataUrl);

    await s.post("project_open", { id: project.id });
    const active = await s.post("catalog_read");
    assert.equal(active.body.entries.length, 1);
  } finally {
    s.close();
  }
});

test("asset routes: content-type, immutable caching with ?v, byte ranges, traversal guard", async () => {
  const s = await bootServer();
  try {
    const { body: project } = await s.post("project_create", { req: { name: "Assets" } });
    const dir = path.join(s.services.projectsRoot, project.id);
    fs.mkdirSync(path.join(dir, "boards", "main_fab"), { recursive: true });
    fs.writeFileSync(path.join(dir, "boards", "main_fab", "gerbers.zip"), "0123456789");
    fs.writeFileSync(path.join(dir, "boards", "main.tsx"), "<board />");

    const full = await fetch(`${s.base}/projects/${project.id}/boards/main_fab/gerbers.zip?v=1-1`);
    assert.equal(full.status, 200);
    assert.equal(full.headers.get("content-type"), "application/zip");
    assert.equal(full.headers.get("accept-ranges"), "bytes");
    assert.ok(full.headers.get("cache-control").includes("immutable"));
    assert.equal(await full.text(), "0123456789");

    const tsx = await fetch(`${s.base}/projects/${project.id}/boards/main.tsx`);
    assert.equal(tsx.status, 200);
    assert.equal(tsx.headers.get("content-type"), "text/plain; charset=utf-8");

    const range = await fetch(`${s.base}/projects/${project.id}/boards/main_fab/gerbers.zip`, {
      headers: { range: "bytes=2-5" },
    });
    assert.equal(range.status, 206);
    assert.equal(range.headers.get("content-range"), "bytes 2-5/10");
    assert.equal(await range.text(), "2345");

    const suffix = await fetch(`${s.base}/projects/${project.id}/boards/main_fab/gerbers.zip`, {
      headers: { range: "bytes=-3" },
    });
    assert.equal(suffix.status, 206);
    assert.equal(await suffix.text(), "789");

    const badRange = await fetch(`${s.base}/projects/${project.id}/boards/main_fab/gerbers.zip`, {
      headers: { range: "bytes=99-" },
    });
    assert.equal(badRange.status, 416);

    // Traversal: `..%2f` segments survive WHATWG URL normalization (unlike
    // `%2e%2e`, which clients pre-normalize away) and decode to `../` on the
    // server — the resolve-inside-root guard must 403 even though the target
    // extension (.json) is on the allowlist.
    fs.writeFileSync(path.join(s.home, "settings.json"), "{}");
    const traversal = await fetch(
      `${s.base}/projects/${project.id}/..%2f..%2fsettings.json`,
    );
    assert.equal(traversal.status, 403);

    // Non-allowlisted extensions fall through (SPA routes stay servable) —
    // including the donor's mp4, which is no longer on the allowlist.
    const spa = await fetch(`${s.base}/projects/${project.id}/boards/ep001.mp4`);
    assert.equal(spa.status, 404);
    assert.equal(await spa.text(), "fallthrough");
  } finally {
    s.close();
  }
});

test("SSE projectId filter: a scoped connection only sees its project's chat events", async () => {
  const s = await bootServer();
  try {
    const scoped = [];
    const unscoped = [];
    const esScoped = new MockEventSource(`${s.base}/api/events?projectId=proj-A`);
    esScoped.addEventListener("chat_event", (e) => scoped.push(JSON.parse(e.data)));
    const esAll = new MockEventSource(`${s.base}/api/events`);
    esAll.addEventListener("chat_event", (e) => unscoped.push(JSON.parse(e.data)));
    await new Promise((resolve) => setTimeout(resolve, 100));

    s.services.broadcastChatEvent("proj-A", { kind: "turn_start", turnId: "a", phase: "plan" });
    s.services.broadcastChatEvent("proj-B", { kind: "turn_start", turnId: "b", phase: "plan" });

    await waitFor(() => unscoped.length >= 2);
    assert.deepEqual(unscoped.map((e) => e.projectId), ["proj-A", "proj-B"]);
    await waitFor(() => scoped.length >= 1);
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.deepEqual(scoped.map((e) => e.projectId), ["proj-A"]);

    esScoped.close();
    esAll.close();
  } finally {
    s.close();
  }
});

// ---------------------------------------------------------------------------
// Stage quiet limits
//
// A real 35-minute run showed the app announcing "Build stopped responding"
// over a healthy build: the flat 120s limit was shorter than an ordinary
// compile, and the router escalation retry sits in `compile` for ~15 minutes,
// so this would have fired on every escalated build.
// ---------------------------------------------------------------------------

test("a compile may go quiet far longer than the old flat 120s limit", () => {
  // The specific case that broke: 5x router effort on a large board.
  assert.ok(
    quietLimitMs("compile") > 20 * 60_000,
    "a 20-minute route must not read as stalled",
  );
});

test("every known stage gets more than the old 120s", () => {
  for (const stage of ["compile", "scan", "dfm", "substrate", "export", "render"]) {
    assert.ok(
      quietLimitMs(stage) > 120_000,
      `${stage} still has a limit that a real build can cross`,
    );
  }
});

test("file-shuffling stages are held tighter than routing ones", () => {
  // Silence during a render really does mean something died; silence during a
  // route usually means it is routing.
  assert.ok(quietLimitMs("render") < quietLimitMs("compile"));
  assert.ok(quietLimitMs("export") < quietLimitMs("compile"));
});

test("an unknown stage gets the most generous limit, not the tightest", () => {
  // A stage we do not recognise is one whose cost we cannot predict, and a
  // false "stalled" is worse than a late one.
  const known = ["compile", "scan", "dfm", "substrate", "export", "render"]
    .map(quietLimitMs);
  assert.ok(quietLimitMs("some-future-stage") >= Math.max(...known));
  assert.ok(quietLimitMs(undefined) >= Math.max(...known));
  assert.ok(quietLimitMs(null) >= Math.max(...known));
});
