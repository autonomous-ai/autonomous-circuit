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
    // Codex is deliberately unresolvable here. An override that does not exist
    // makes resolveCodex return null without ever consulting PATH or the
    // Codex.app bundle — otherwise a suite run on a machine with the desktop
    // app installed would spawn the REAL CLI and reach the network, which this
    // repo's tests never do.
    CIRCUIT_CODEX_BIN: path.join(home, "no-codex-here"),
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
    assert.deepEqual(Object.keys(onDisk).sort(), ["autoBuild", "effort", "hasOnboarded", "model", "provider"]);

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
    // Manual mode so the plan turn doesn't chain a build here. The provider is
    // pinned because this test is about Claude's stream-json translation, and
    // the shipped default is codex.
    await s.post("app_settings_write", {
      settings: { hasOnboarded: true, autoBuild: false, provider: "claude" },
    });
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

    const created = await s.post("chat_session_create", { projectId: project.id });
    assert.equal(created.status, 200);
    assert.equal(created.body.title, "New chat");
    assert.notEqual(created.body.sessionId, sessionId);

    const secondJsonl = sessionJsonlPath(workspace, created.body.sessionId, s.env);
    fs.writeFileSync(
      secondJsonl,
      [
        JSON.stringify({
          type: "user",
          message: { role: "user", content: "route the power rail" },
          timestamp: "2026-08-02T05:00:00.000Z",
        }),
        JSON.stringify({ type: "ai-title", aiTitle: "Power routing" }),
      ].join("\n"),
    );
    const sessions = await s.post("chat_session_list", { projectId: project.id });
    assert.equal(sessions.status, 200);
    assert.deepEqual(
      new Set(sessions.body.map((item) => item.sessionId)),
      new Set([sessionId, created.body.sessionId]),
    );
    assert.equal(sessions.body.find((item) => item.sessionId === created.body.sessionId).title, "Power routing");

    const selected = await s.post("chat_session_state", {
      projectId: project.id,
      sessionId: created.body.sessionId,
    });
    assert.equal(selected.body.sessionId, created.body.sessionId);
    assert.equal(selected.body.history[0].content, "route the power rail");
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

test("a second turn on a live project is refused instead of racing the first", async () => {
  const s = await bootServer({ scenario: { plan: { lines: [], sleepAfterMs: 3000 } } });
  try {
    await s.post("app_settings_write", {
      settings: { hasOnboarded: true, autoBuild: false, provider: "claude" },
    });
    const { body: project } = await s.post("project_create", { req: { name: "Race" } });
    await s.post("project_open", { id: project.id });

    const first = await s.post("chat_start_turn", {
      req: { projectId: project.id, userMessage: "design a board" },
    });
    assert.equal(first.status, 200);

    // Both CLIs key their conversation by one id per project and neither
    // tolerates two writers; codex fails the whole turn with "thread <id>
    // already has an active writer".
    for (const [command, req] of [
      ["chat_start_turn", { projectId: project.id, userMessage: "again" }],
      ["chat_approve_plan", { projectId: project.id, planText: "# plan" }],
      ["chat_request_plan_changes", { projectId: project.id, feedback: "smaller" }],
    ]) {
      const second = await s.post(command, { req });
      assert.equal(second.status, 409, command);
      assert.equal(second.body.code, "TURN_IN_PROGRESS", command);
    }

    await s.post("chat_cancel_turn", { turnId: first.body.turnId });
  } finally {
    s.close();
  }
});

// ---------------------------------------------------------------------------
// Viewer-only mode (CIRCUIT_WORKSPACE): the server a host embeds beside its own terminal.
// ---------------------------------------------------------------------------

async function bootWorkspaceServer() {
  const s = await bootServerWith((env) => ({ ...env, CIRCUIT_WORKSPACE: tmpdir("circuit-ws-") }));
  return s;
}

/** `bootServer` with the env edited before the services are built. */
async function bootServerWith(editEnv, serviceOptions = {}) {
  const home = tmpdir("circuit-home-");
  const cfgDir = tmpdir("circuit-cfg-");
  const scenarioPath = path.join(home, "scenario.json");
  fs.writeFileSync(scenarioPath, "{}");
  const env = editEnv({
    ...process.env,
    CIRCUIT_HOME: home,
    CLAUDE_CONFIG_DIR: cfgDir,
    CIRCUIT_CLAUDE_BIN: FAKE_CLAUDE,
    CIRCUIT_CODEX_BIN: path.join(home, "no-codex-here"),
    CIRCUIT_FAKE_SCENARIO: scenarioPath,
  });
  const services = createCircuitServices({ env, ...serviceOptions });
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
  return { services, server, base, post, close() { services.close(); server.close(); }, env };
}

test("viewer-only: one workspace project, open from the first request, chat refused, assets served from the folder", async () => {
  const s = await bootWorkspaceServer();
  const ws = s.env.CIRCUIT_WORKSPACE;
  try {
    assert.equal(s.services.viewerOnly, true);
    assert.equal(s.services.projectsRoot, path.resolve(ws));

    // The settings reply is where the client learns the mode; the normal server never sends it.
    const settings = await s.post("app_settings_read");
    assert.equal(settings.body.viewerOnly, true);
    assert.equal((await s.post("app_info")).body.rootPath, path.resolve(ws));

    // Exactly one project, and it is already the active one: no project_open needed before
    // the catalog answers, which is the state the host's page boots into.
    const listed = await s.post("project_list");
    assert.equal(listed.status, 200);
    assert.deepEqual(listed.body.map((p) => p.id), ["workspace"]);
    fs.mkdirSync(path.join(ws, "boards"), { recursive: true });
    fs.writeFileSync(path.join(ws, "boards", "main.tsx"), "<board />");
    s.services.catalog.refresh("workspace");
    const catalog = await s.post("catalog_read");
    assert.equal(catalog.status, 200);
    assert.deepEqual(catalog.body.entries.map((e) => e.file), ["boards/main.tsx"]);
    assert.equal(catalog.body.rootPath, fs.realpathSync(ws));

    // The folder is the user's: nothing here creates, renames or deletes a project in it.
    for (const [cmd, body] of [
      ["project_create", { name: "x" }],
      ["project_rename", { id: "workspace", name: "x" }],
      ["project_delete", { id: "workspace" }],
    ]) {
      const refused = await s.post(cmd, body);
      assert.equal(refused.status, 409, cmd);
      assert.equal(refused.body.code, "VIEWER_ONLY", cmd);
    }
    assert.equal(fs.existsSync(path.join(ws, "project.json")), false);

    // The conversation lives in the host's terminal; a turn started here would put a second
    // `claude` on the same workspace.
    for (const [cmd, body] of [
      ["chat_start_turn", { projectId: "workspace", userMessage: "hi" }],
      ["chat_approve_plan", { projectId: "workspace", planText: "p" }],
      ["chat_session_create", { projectId: "workspace" }],
    ]) {
      const refused = await s.post(cmd, body);
      assert.equal(refused.status, 409, cmd);
      assert.equal(refused.body.code, "VIEWER_ONLY", cmd);
    }

    // Assets resolve inside the workspace, and the traversal guard still holds.
    fs.mkdirSync(path.join(ws, "boards", "main_review"), { recursive: true });
    fs.writeFileSync(path.join(ws, "boards", "main_review", "_pcb.png"), "png-bytes");
    const png = await fetch(`${s.base}/projects/workspace/boards/main_review/_pcb.png?v=1-1`);
    assert.equal(png.status, 200);
    assert.equal(png.headers.get("content-type"), "image/png");
    assert.equal(await png.text(), "png-bytes");
    const outside = await fetch(`${s.base}/projects/workspace/..%2F..%2Fetc%2Fpasswd`);
    assert.notEqual(outside.status, 200);
    const other = await fetch(`${s.base}/projects/other/boards/main.tsx`);
    assert.equal(other.status, 404);
  } finally {
    s.close();
  }
});

test("viewer-only: `.harness/` is invisible to the catalog, and a write there still bumps nothing an artifact would", async () => {
  const s = await bootWorkspaceServer();
  const ws = s.env.CIRCUIT_WORKSPACE;
  try {
    fs.mkdirSync(path.join(ws, ".harness"), { recursive: true });
    fs.writeFileSync(path.join(ws, ".harness", "verdict.json"), JSON.stringify({ spec: 1, ready: false }));
    fs.writeFileSync(path.join(ws, "NOTES.md"), "# notes");
    s.services.catalog.refresh("workspace");
    const catalog = await s.post("catalog_read");
    assert.deepEqual(catalog.body.entries.map((e) => e.file), ["NOTES.md"]);
  } finally {
    s.close();
  }
});

test("viewer-only: harness_new_board asks the daemon for a sibling harness; the command does not exist elsewhere", async () => {
  // A fake daemon: answers machine_select with `connected`, agent_create with a created agent.
  const frames = [];
  const fakeConnect = () => {
    const listeners = { open: [], message: [], error: [], close: [] };
    const socket = {
      addEventListener: (t, fn) => listeners[t].push(fn),
      send(text) {
        const frame = JSON.parse(text);
        frames.push(frame);
        const reply = frame.type === "machine_select"
          ? { type: "connected", payload: { machineId: frame.payload.machineId } }
          : { type: "agent_create_result", payload: { requestId: frame.payload.requestId, state: "created", agent: { id: "agent-9", dshName: "KiCad" } } };
        setTimeout(() => listeners.message.forEach((fn) => fn({ data: JSON.stringify(reply) })), 0);
      },
      close() {},
    };
    setTimeout(() => listeners.open.forEach((fn) => fn({})), 0);
    return socket;
  };
  const home = tmpdir("circuit-nb-home-");
  fs.mkdirSync(path.join(home, ".harness", "cli", "data"), { recursive: true });
  fs.writeFileSync(path.join(home, ".harness", "cli", "data", "machines.json"), JSON.stringify({ machines: [{ machineId: "m-local", local: true }] }));
  const dshDir = tmpdir("circuit-nb-dsh-");
  fs.writeFileSync(path.join(dshDir, "harness.json"), JSON.stringify({ engine: "codex" }));
  const parent = tmpdir("circuit-nb-");
  const ws = path.join(parent, "pet");
  fs.mkdirSync(ws);
  const s = await bootServerWith(
    (env) => ({ ...env, CIRCUIT_WORKSPACE: ws, HOME: home, HARNESS_DSH: "autonomous/kicad", HARNESS_DSH_DIR: dshDir }),
    { harnessConnect: fakeConnect },
  );
  try {
    const made = await s.post("harness_new_board");
    assert.equal(made.status, 200, JSON.stringify(made.body));
    assert.equal(made.body.name, "pet-2");
    assert.equal(made.body.cwd, path.join(parent, "pet-2"));
    assert.equal(made.body.engine, "codex");
    assert.equal(made.body.agentId, "agent-9");
    assert.match(made.body.hint, /⌘O/);
    assert.equal(fs.existsSync(path.join(parent, "pet-2")), true);
    assert.deepEqual(frames.map((f) => f.type), ["machine_select", "agent_create"]);
    assert.equal(frames[1].payload.dsh, "autonomous/kicad");
    assert.equal(frames[1].payload.cwd, path.join(parent, "pet-2"));
  } finally {
    s.close();
  }

  // A daemon that refuses is a 502 with its reason, and the folder is not left behind.
  const refusing = () => {
    const listeners = { open: [], message: [], error: [], close: [] };
    return {
      addEventListener: (t, fn) => listeners[t].push(fn),
      send(text) {
        const frame = JSON.parse(text);
        const reply = frame.type === "machine_select" ? { type: "connected", payload: {} } : { type: "agent_create_result", payload: { requestId: frame.payload.requestId, state: "unconfirmed" } };
        setTimeout(() => listeners.message.forEach((fn) => fn({ data: JSON.stringify(reply) })), 0);
      },
      close() {},
      ...(setTimeout(() => listeners.open.forEach((fn) => fn({})), 0), {}),
    };
  };
  const s2 = await bootServerWith(
    (env) => ({ ...env, CIRCUIT_WORKSPACE: ws, HOME: home, HARNESS_DSH: "autonomous/kicad", HARNESS_DSH_DIR: dshDir }),
    { harnessConnect: refusing },
  );
  try {
    const refused = await s2.post("harness_new_board");
    assert.equal(refused.status, 502);
    assert.equal(refused.body.code, "HARNESS_NEW_BOARD_FAILED");
    assert.match(refused.body.message, /unconfirmed/);
    assert.equal(fs.existsSync(path.join(parent, "pet-3")), false);
  } finally {
    s2.close();
  }

  // The app proper has project_create; this command is viewer-only.
  const s3 = await bootServer();
  try {
    const absent = await s3.post("harness_new_board");
    assert.equal(absent.status, 404);
    assert.equal(absent.body.code, "UNKNOWN_COMMAND");
  } finally {
    s3.close();
  }
});
