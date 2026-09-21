// harnessHost — the viewer-only pane asking the Harness daemon for a new board.
// The daemon is a fake socket here: the tests check the protocol we speak, the folder we pick,
// and that nothing hangs when the daemon is dead, refuses, or answers something else.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  DAEMON_WS_URL,
  LOCAL_PROTOCOL_VERSION,
  createHarnessAgent,
  newBoard,
  nextWorkspaceDir,
  readMachineId,
  readTileEngine,
} from "./harnessHost.mjs";

/** A WebSocket stand-in: `script(frame)` answers each frame we send with zero or more frames. */
function fakeSocket(script, { openDelay = 0, failOpen = false } = {}) {
  const listeners = { open: [], message: [], error: [], close: [] };
  const sent = [];
  const socket = {
    sent,
    closed: false,
    addEventListener(type, fn) {
      listeners[type].push(fn);
    },
    emit(type, data) {
      for (const fn of listeners[type]) fn(data === undefined ? {} : { data });
    },
    send(text) {
      const frame = JSON.parse(text);
      sent.push(frame);
      for (const reply of script(frame) || []) {
        setTimeout(() => socket.emit("message", JSON.stringify(reply)), 0);
      }
    },
    close() {
      socket.closed = true;
    },
  };
  setTimeout(() => (failOpen ? socket.emit("error") : socket.emit("open")), openDelay);
  return socket;
}

const happyDaemon = (frame) => {
  if (frame.type === "machine_select") return [{ type: "connected", payload: { machineId: frame.payload.machineId } }];
  if (frame.type === "agent_create") {
    return [{ type: "agent_create_result", payload: { requestId: frame.payload.requestId, state: "created", agent: { id: "a1b2c3", dsh: frame.payload.dsh, dshName: "KiCad" } } }];
  }
  return [];
};

test("nextWorkspaceDir: the next free sibling, numbered from 2, and a numbered folder does not nest its number", () => {
  const taken = new Set(["/w/pet-2", "/w/pet-3"]);
  const exists = (p) => taken.has(p);
  assert.equal(nextWorkspaceDir("/w/pet", { exists }), "/w/pet-4");
  assert.equal(nextWorkspaceDir("/w/pet-2", { exists }), "/w/pet-4");
  assert.equal(nextWorkspaceDir("/w/harness-6", { exists: () => false }), "/w/harness-2");
});

test("readMachineId: the local machine from the daemon's list, not the computer-id file", () => {
  const readFile = () => JSON.stringify({ machines: [{ machineId: "remote1", local: false }, { machineId: "local1", local: true }] });
  assert.equal(readMachineId({ HOME: "/h" }, { readFile }), "local1");
  assert.throws(() => readMachineId({ HOME: "/h" }, { readFile: () => { throw new Error("ENOENT"); } }), /machine list/);
  assert.throws(() => readMachineId({ HOME: "/h" }, { readFile: () => '{"machines":[]}' }), /no machine/);
});

test("readTileEngine: the installed manifest's engine, claude when there is none", () => {
  assert.equal(readTileEngine({ HARNESS_DSH_DIR: "/d" }, { readFile: () => '{"engine":"codex"}' }), "codex");
  assert.equal(readTileEngine({ HARNESS_DSH_DIR: "/d" }, { readFile: () => { throw new Error("ENOENT"); } }), "claude");
  assert.equal(readTileEngine({}), "claude");
});

test("createHarnessAgent speaks machine_select then agent_create and resolves the agent id", async () => {
  let socket;
  const connect = (url) => {
    assert.equal(url, DAEMON_WS_URL);
    socket = fakeSocket(happyDaemon);
    return socket;
  };
  const result = await createHarnessAgent({ machineId: "m1", engine: "codex", cwd: "/w/pet-2", dsh: "autonomous/kicad", connect });
  assert.equal(result.agentId, "a1b2c3");
  assert.equal(socket.sent[0].type, "machine_select");
  assert.deepEqual(socket.sent[0].payload, { machineId: "m1", localProtocolVersion: LOCAL_PROTOCOL_VERSION });
  assert.equal(socket.sent[1].type, "agent_create");
  const create = socket.sent[1].payload;
  assert.equal(create.engine, "codex");
  assert.equal(create.cwd, "/w/pet-2");
  assert.equal(create.dsh, "autonomous/kicad");
  assert.match(create.requestId, /^[0-9a-f]{16}$/);
  assert.match(create.creationId, /^[0-9a-f]{24}$/);
  assert.equal(create.bypassPermission, true);
  assert.equal(create.permissionMode, "full");
  assert.equal(socket.closed, true);
});

test("createHarnessAgent rejects when the daemon refuses, is dead, or never answers", async () => {
  await assert.rejects(
    createHarnessAgent({ machineId: "m1", engine: "claude", cwd: "/w/x", dsh: "d", connect: () => fakeSocket((f) => (f.type === "machine_select" ? [{ type: "machine_select_error", payload: { error: "NO_SUCH_MACHINE" } }] : [])) }),
    /refused machine m1: NO_SUCH_MACHINE/,
  );
  await assert.rejects(
    createHarnessAgent({ machineId: "m1", engine: "claude", cwd: "/w/x", dsh: "d", connect: () => fakeSocket(() => [], { failOpen: true }) }),
    /cannot reach the Harness daemon/,
  );
  await assert.rejects(
    createHarnessAgent({ machineId: "m1", engine: "claude", cwd: "/w/x", dsh: "d", timeoutMs: 40, connect: () => fakeSocket((f) => (f.type === "machine_select" ? [{ type: "connected", payload: {} }] : [])) }),
    /did not answer within/,
  );
  await assert.rejects(
    createHarnessAgent({ machineId: "m1", engine: "claude", cwd: "/w/x", dsh: "d", connect: () => fakeSocket((f) => {
      if (f.type === "machine_select") return [{ type: "connected", payload: {} }];
      return [{ type: "agent_create_result", payload: { requestId: f.payload.requestId, state: "unconfirmed" } }];
    }) }),
    /did not succeed: unconfirmed/,
  );
  // A result for somebody else's request is ignored, then ours lands.
  const result = await createHarnessAgent({ machineId: "m1", engine: "claude", cwd: "/w/x", dsh: "d", connect: () => fakeSocket((f) => {
    if (f.type === "machine_select") return [{ type: "connected", payload: {} }];
    return [
      { type: "agent_create_result", payload: { requestId: "someone-else", state: "created", agent: { id: "nope" } } },
      { type: "agent_create_result", payload: { requestId: f.payload.requestId, state: "created", agent: { id: "mine" } } },
    ];
  }) });
  assert.equal(result.agentId, "mine");
});

test("newBoard makes the sibling folder, asks for the tile's engine, and removes the folder when the daemon refuses", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "circuit-nb-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "circuit-nb-home-"));
  fs.mkdirSync(path.join(home, ".harness", "cli", "data"), { recursive: true });
  fs.writeFileSync(path.join(home, ".harness", "cli", "data", "machines.json"), JSON.stringify({ machines: [{ machineId: "m-local", local: true }] }));
  const dshDir = fs.mkdtempSync(path.join(os.tmpdir(), "circuit-nb-dsh-"));
  fs.writeFileSync(path.join(dshDir, "harness.json"), JSON.stringify({ engine: "codex" }));
  const ws = path.join(root, "pet");
  fs.mkdirSync(ws);
  const env = { HOME: home, HARNESS_DSH: "autonomous/kicad", HARNESS_DSH_DIR: dshDir };

  let created;
  const result = await newBoard({ workspaceDir: ws, env, connect: () => fakeSocket((f) => { if (f.type === "agent_create") created = f.payload; return happyDaemon(f); }) });
  assert.equal(result.cwd, path.join(root, "pet-2"));
  assert.equal(result.name, "pet-2");
  assert.equal(result.engine, "codex");
  assert.equal(result.agentId, "a1b2c3");
  assert.equal(result.dshName, "KiCad");
  assert.match(result.hint, /⌘O/);
  assert.equal(created.cwd, path.join(root, "pet-2"));
  assert.equal(created.engine, "codex");
  assert.equal(fs.existsSync(path.join(root, "pet-2")), true);

  // The daemon refuses: the folder it never materialized goes away, the name is reusable.
  await assert.rejects(
    newBoard({ workspaceDir: ws, env, connect: () => fakeSocket((f) => (f.type === "machine_select" ? [{ type: "connected", payload: {} }] : [{ type: "agent_create_result", payload: { requestId: f.payload.requestId, state: "unconfirmed" } }])) }),
    /did not succeed/,
  );
  assert.equal(fs.existsSync(path.join(root, "pet-3")), false);

  // Outside Harness there is nobody to ask.
  await assert.rejects(newBoard({ workspaceDir: ws, env: { HOME: home } }), /HARNESS_DSH/);
});
