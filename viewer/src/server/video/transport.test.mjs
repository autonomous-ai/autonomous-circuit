// Transport runtime tests — the rewritten viewer/src/client/lib/transport.ts
// against a tiny HTTP test server: fetch POST /api/<cmd>, IpcError throwing,
// the shared-EventSource event bus, and the kept test seams (Proxy override +
// injected bridge).

import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

import { MockEventSource, waitFor } from "./fixtures/sse.mjs";
import {
  _resetTransportForTests,
  adaptTauriListen,
  isTauriRuntime,
  listenEvent,
  resetTransport,
  setApiBase,
  setTauriBridge,
  setTransport,
  transport,
} from "../../client/lib/transport.ts";

/** Tiny Video-shaped test server: records POST bodies, answers per-route. */
async function bootTinyServer() {
  const calls = [];
  const sseClients = new Set();
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/api/events") {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write("retry: 1000\n\n");
      sseClients.add(res);
      req.on("close", () => sseClients.delete(res));
      return;
    }
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      const cmd = url.pathname.replace("/api/", "");
      calls.push({ cmd, method: req.method, body: body ? JSON.parse(body) : null });
      if (cmd === "project_open") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ workspaceRoot: "/video/projects/x" }));
        return;
      }
      if (cmd === "project_delete") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end("null");
        return;
      }
      if (cmd === "chat_start_turn") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ turnId: "turn-9" }));
        return;
      }
      if (cmd === "project_rename") {
        res.writeHead(404, { "content-type": "application/json" });
        res.end(JSON.stringify({ code: "PROJECT_NOT_FOUND", message: "project not found: x" }));
        return;
      }
      res.writeHead(500, { "content-type": "text/plain" });
      res.end("boom");
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  function emit(eventName, payload) {
    for (const res of sseClients) {
      res.write(`event: ${eventName}\ndata: ${JSON.stringify(payload)}\n\n`);
    }
  }
  return { server, base, calls, emit, sseClients };
}

test("commands POST /api/<cmd> with the camelCase args body and return the parsed reply", async (t) => {
  const tiny = await bootTinyServer();
  t.after(() => {
    resetTransport();
    tiny.server.close();
  });
  setApiBase(tiny.base);

  const opened = await transport.project_open("proj-1");
  assert.deepEqual(opened, { workspaceRoot: "/video/projects/x" });
  assert.deepEqual(tiny.calls.at(-1), {
    cmd: "project_open",
    method: "POST",
    body: { id: "proj-1" },
  });

  const started = await transport.chat_start_turn({ projectId: "p", userMessage: "hi" });
  assert.deepEqual(started, { turnId: "turn-9" });
  assert.deepEqual(tiny.calls.at(-1).body, { req: { projectId: "p", userMessage: "hi" } });

  // Void command → null reply, no throw.
  assert.equal(await transport.project_delete("p"), null);
});

test("non-2xx replies throw the server's IpcError body (or a synthesized HTTP_<status>)", async (t) => {
  const tiny = await bootTinyServer();
  t.after(() => {
    resetTransport();
    tiny.server.close();
  });
  setApiBase(tiny.base);

  await assert.rejects(
    () => transport.project_rename("x", "y"),
    (err) => err.code === "PROJECT_NOT_FOUND" && /project not found/.test(err.message),
  );
  await assert.rejects(
    () => transport.app_info(),
    (err) => err.code === "HTTP_500" && err.message === "boom",
  );
});

test("events ride ONE shared EventSource on /api/events; unsubscribe detaches", async (t) => {
  const tiny = await bootTinyServer();
  const previousEventSource = globalThis.EventSource;
  globalThis.EventSource = MockEventSource;
  t.after(() => {
    globalThis.EventSource = previousEventSource;
    resetTransport();
    tiny.server.close();
  });
  setApiBase(tiny.base);

  const chatEvents = [];
  const catalogEvents = [];
  const unsubscribe = transport.events.subscribe("chat_event", (e) => chatEvents.push(e));
  await transport.onCatalogChanged((e) => catalogEvents.push(e));
  await waitFor(() => tiny.sseClients.size >= 1);
  // Both subscriptions share the single connection (§2: one EventSource).
  assert.equal(tiny.sseClients.size, 1);

  tiny.emit("chat_event", { kind: "turn_start", turnId: "t", phase: "plan", projectId: "p" });
  tiny.emit("catalog_changed", { revision: 3 });
  await waitFor(() => chatEvents.length === 1 && catalogEvents.length === 1);
  assert.equal(chatEvents[0].kind, "turn_start");
  assert.equal(chatEvents[0].projectId, "p");
  assert.deepEqual(catalogEvents[0], { revision: 3 });

  unsubscribe();
  tiny.emit("chat_event", { kind: "turn_end", turnId: "t", projectId: "p" });
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(chatEvents.length, 1, "unsubscribed handler stays deaf");
});

test("setTransport override wins through the Proxy; resetTransport restores the fetch runtime", async () => {
  const seen = [];
  setTransport({
    project_list: async () => {
      seen.push("mock");
      return [{ id: "m", name: "Mock", createdAt: 0, updatedAt: 0, hasModel: false }];
    },
  });
  const projects = await transport.project_list();
  assert.equal(projects[0].id, "m");
  assert.deepEqual(seen, ["mock"]);
  resetTransport();
});

test("an injected bridge takes precedence over fetch/SSE (test seam kept from the donor)", async (t) => {
  t.after(() => resetTransport());
  const invocations = [];
  const handlers = new Map();
  const rawListen = (event, cb) => {
    handlers.set(event, cb);
    return Promise.resolve(() => handlers.delete(event));
  };
  setTauriBridge({
    invoke: async (cmd, args) => {
      invocations.push({ cmd, args });
      return { ok: true };
    },
    listen: adaptTauriListen(rawListen),
  });
  assert.equal(isTauriRuntime(), true);

  const reply = await transport.app_settings_read();
  assert.deepEqual(reply, { ok: true });
  assert.deepEqual(invocations, [{ cmd: "app_settings_read", args: undefined }]);

  const payloads = [];
  await listenEvent("chat_event", (payload) => payloads.push(payload));
  handlers.get("chat_event")({ payload: { kind: "turn_end", turnId: "t" } });
  assert.deepEqual(payloads, [{ kind: "turn_end", turnId: "t" }]);

  _resetTransportForTests();
  assert.equal(isTauriRuntime(), false);
});
