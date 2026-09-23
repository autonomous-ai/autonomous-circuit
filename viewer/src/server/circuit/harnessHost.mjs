// The viewer-only pane's one way back to its host: ask the Harness daemon for a NEW board.
//
// Inside Harness the viewer is a pane beside the agent's terminal, serving ONE workspace the host
// fixed (`CIRCUIT_WORKSPACE`). It has no project picker and `project_create` is refused — a new
// board is a new harness, and only the host makes those. Harness has no hook for that, but its
// daemon listens on a loopback WebSocket for the desktop (`store/tools/dsh-e2e.mjs` in openharness
// drives it the same way): `machine_select`, then `agent_create { engine, cwd, dsh }`. This module
// speaks that much of the protocol and nothing more.
//
// What the daemon does NOT do is place the new pane in a tab — the desktop owns tabs. So the
// reply carries a hint the pane shows: open it with ⌘O. The button saves the tile / machine /
// folder / engine steps of New Harness, not the last one.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { randomBytes } from "node:crypto";

/** The daemon's loopback socket, the same one the desktop uses. */
export const DAEMON_WS_URL = "ws://127.0.0.1:18473/api/local-ws";
/** Local protocol version the daemon accepts on `machine_select` (cli: `FM`). */
export const LOCAL_PROTOCOL_VERSION = 1;
const DEFAULT_TIMEOUT_MS = 30_000;
/** Engines the daemon has a `permissionMode: "full"` mapping for (cli 0.2.89: `$H`). */
export const FULL_MODE_ENGINES = new Set(["claude", "codex"]);

/**
 * The next free sibling of `current` for a new board: `<parent>/<base>-N`, N from 2, where
 * `base` is the folder's name with any trailing `-N` removed — so `pet`, `pet-2`, `pet-3` sit
 * together and a click from `pet-2` still yields `pet-3`, not `pet-2-2`.
 */
export function nextWorkspaceDir(current, { exists = fs.existsSync } = {}) {
  const dir = path.resolve(String(current));
  const parent = path.dirname(dir);
  const base = path.basename(dir).replace(/-\d+$/, "") || "board";
  for (let n = 2; n < 10_000; n++) {
    const candidate = path.join(parent, `${base}-${n}`);
    if (!exists(candidate)) return candidate;
  }
  throw new Error(`no free sibling name next to ${dir}`);
}

/**
 * This machine's id on the daemon — `~/.harness/cli/data/machines.json`, the entry marked
 * `local`. Not the `computer-id` file: the daemon refuses that one (seen 2026-09-18).
 */
export function readMachineId(env = process.env, { readFile = fs.readFileSync } = {}) {
  const home = env.HOME || os.homedir();
  const file = path.join(home, ".harness", "cli", "data", "machines.json");
  let parsed;
  try {
    parsed = JSON.parse(readFile(file, "utf8"));
  } catch (err) {
    throw new Error(`cannot read the daemon's machine list at ${file}: ${err.message}`);
  }
  const machines = Array.isArray(parsed?.machines) ? parsed.machines : [];
  const local = machines.find((m) => m && m.local) || machines[0];
  if (!local?.machineId) throw new Error(`no machine in ${file}`);
  return String(local.machineId);
}

/**
 * The engine the tile runs on: the installed manifest at `$HARNESS_DSH_DIR/harness.json`. A
 * store wrapper and a `--link` install both put `harness.json` at that root. Falls back to
 * `claude`, which is what every Circuit tile ran on until Astra came back.
 */
export function readTileEngine(env = process.env, { readFile = fs.readFileSync } = {}) {
  const dir = env.HARNESS_DSH_DIR;
  if (!dir) return "claude";
  try {
    const manifest = JSON.parse(readFile(path.join(dir, "harness.json"), "utf8"));
    return typeof manifest?.engine === "string" && manifest.engine ? manifest.engine : "claude";
  } catch {
    return "claude";
  }
}

function defaultConnect(url) {
  if (typeof globalThis.WebSocket !== "function") {
    throw new Error("this Node has no WebSocket; Node 22+ is required to reach the Harness daemon");
  }
  return new globalThis.WebSocket(url);
}

/**
 * One `agent_create` on the daemon. `connect(url)` returns a WebSocket-like object (`send`,
 * `close`, `addEventListener('open'|'message'|'error'|'close')`); the default is Node's global
 * WebSocket. Resolves `{ agentId, agent }` or rejects with the daemon's reason. Bounded by
 * `timeoutMs` so a dead daemon is an error, never a hang.
 */
export function createHarnessAgent({
  machineId,
  engine,
  cwd,
  dsh,
  url = DAEMON_WS_URL,
  connect = defaultConnect,
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  if (!machineId) return Promise.reject(new Error("machineId is required"));
  if (!engine) return Promise.reject(new Error("engine is required"));
  if (!cwd) return Promise.reject(new Error("cwd is required"));
  return new Promise((resolve, reject) => {
    let socket;
    let settled = false;
    const requestId = randomBytes(8).toString("hex");
    const creationId = randomBytes(12).toString("hex");
    const finish = (err, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        socket?.close();
      } catch {
        // already closed
      }
      err ? reject(err) : resolve(value);
    };
    const timer = setTimeout(() => finish(new Error(`the Harness daemon did not answer within ${timeoutMs / 1000}s`)), timeoutMs);
    try {
      socket = connect(url);
    } catch (err) {
      finish(err);
      return;
    }
    const send = (type, payload) => socket.send(JSON.stringify({ type, payload }));
    socket.addEventListener("error", () => finish(new Error(`cannot reach the Harness daemon at ${url}`)));
    socket.addEventListener("close", () => finish(new Error("the Harness daemon closed the socket before answering")));
    socket.addEventListener("open", () => {
      send("machine_select", { machineId, localProtocolVersion: LOCAL_PROTOCOL_VERSION });
    });
    socket.addEventListener("message", (event) => {
      let frame;
      try {
        frame = JSON.parse(typeof event.data === "string" ? event.data : String(event.data));
      } catch {
        return;
      }
      const { type, payload } = frame || {};
      if (type === "connected") {
        // The Circuit tiles run their pipeline (KiCad, the supplier's catalog, the router) from
        // the agent's shell, so the engine must run without a sandbox. On the daemon (0.2.58)
        // `bypassPermission` only selects the "auto" mode — for codex `--approve-for-me`, a
        // reviewer model over a workspace-write sandbox, under which a kicad-cli DRC sat in an
        // uninterruptible exit for two hours (2026-09-21). `permissionMode: "full"` is the mode
        // that maps to `--dangerously-bypass-approvals-and-sandbox` / `--dangerously-skip-
        // permissions`; both fields are sent so an older daemon still gets the bypass. The daemon
        // (0.2.89) has that table for claude and codex only and refuses the field for any other
        // engine (`INVALID_PERMISSION_MODE`), so a grok tile gets `bypassPermission` alone and
        // carries its own always-approve in the manifest's args.
        send("agent_create", { requestId, engine, cwd, dsh, creationId, bypassPermission: true, ...(FULL_MODE_ENGINES.has(engine) ? { permissionMode: "full" } : {}) });
        return;
      }
      if (type === "machine_select_error") {
        finish(new Error(`the daemon refused machine ${machineId}: ${payload?.error || "machine_select_error"}`));
        return;
      }
      if (type === "agent_create_result" && payload?.requestId === requestId) {
        if (payload.state === "created" && payload.agent?.id) {
          finish(null, { agentId: String(payload.agent.id), agent: payload.agent });
        } else {
          finish(new Error(`agent_create did not succeed: ${payload.error || payload.failure || payload.state || "unknown"}`));
        }
      }
    });
  });
}

/**
 * The whole button: pick the next sibling folder, make it, ask the daemon for an agent of the
 * same tile on the same engine in it. Returns what the pane shows. The folder is created before
 * the call because the daemon materializes the tile's template into an EXISTING directory; if the
 * daemon then refuses, the empty folder is removed so a retry gets the same name.
 */
export async function newBoard({
  workspaceDir,
  env = process.env,
  connect,
  url,
  timeoutMs,
  mkdir = (p) => fs.mkdirSync(p, { recursive: true }),
  rmdir = (p) => fs.rmSync(p, { recursive: true, force: true }),
} = {}) {
  if (!workspaceDir) throw new Error("newBoard needs the current workspace");
  const dsh = env.HARNESS_DSH;
  if (!dsh) throw new Error("not inside a Harness pane (HARNESS_DSH is not set)");
  const machineId = readMachineId(env);
  const engine = readTileEngine(env);
  const cwd = nextWorkspaceDir(workspaceDir);
  mkdir(cwd);
  let created;
  try {
    created = await createHarnessAgent({ machineId, engine, cwd, dsh, url, connect, timeoutMs });
  } catch (err) {
    rmdir(cwd);
    throw err;
  }
  const name = path.basename(cwd);
  return {
    agentId: created.agentId,
    cwd,
    name,
    dsh,
    engine,
    dshName: created.agent?.dshName || null,
    hint: `Created "${name}" on ${engine}. Harness does not open it here: press ⌘O, pick "${created.agent?.dshName || dsh} … ${name}", then ⏎.`,
  };
}
