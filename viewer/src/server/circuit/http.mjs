// Circuit HTTP layer — `POST /api/<command>` + `GET /api/events` (SSE) + the
// `/projects/<id>/…` asset routes, per contract §2. Mirrors the donor's
// middleware-factory style (httpHandlers.mjs): plain (req, res, next)
// connect-style middlewares, mounted by Vite in dev and by the standalone
// server.mjs in prod.
//
// Errors are `IpcError {code, message, detail?}` JSON bodies on 4xx/5xx.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";

import { createProjectsStore, projectsRootDir, circuitHome } from "./projects.mjs";
import { createSettingsStore, settingsFilePath } from "./settings.mjs";
import { createCatalogService } from "./catalog.mjs";
import {
  PHASE,
  approvedPlanMessage,
  attachmentNote,
  augmentedPathDirs,
  createChatService,
  persistAttachments,
  resolveClaude,
  sessionIdForProject,
} from "./driver.mjs";

const LOG_TAG = "[circuit:http]";
const MAX_BODY_BYTES = 128 * 1024 * 1024; // 6 reference images @ 10 MiB, base64
const SSE_HEARTBEAT_MS = 15_000;

const ASSET_CONTENT_TYPES = new Map([
  [".tsx", "text/plain; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".zip", "application/zip"],
  [".csv", "text/csv; charset=utf-8"],
  [".md", "text/markdown; charset=utf-8"],
  [".glb", "model/gltf-binary"],
]);

function ipcError(code, message, statusCode = 500, detail = undefined) {
  const err = new Error(message);
  err.code = code;
  err.statusCode = statusCode;
  if (detail !== undefined) {
    err.detail = detail;
  }
  return err;
}

function sendJson(res, statusCode, payload) {
  res.statusCode = statusCode;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  res.end(JSON.stringify(payload ?? null));
}

function sendIpcError(res, error) {
  const statusCode = Number(error?.statusCode) || 500;
  sendJson(res, statusCode, {
    code: String(error?.code || "INTERNAL"),
    message: String(error?.message || error || "internal error"),
    ...(error?.detail !== undefined ? { detail: error.detail } : {}),
  });
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(ipcError("PAYLOAD_TOO_LARGE", "request body too large", 413));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      const text = Buffer.concat(chunks).toString("utf8");
      if (!text.trim()) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(text));
      } catch {
        reject(ipcError("INVALID_ARGUMENT", "request body is not valid JSON", 400));
      }
    });
    req.on("error", (error) => reject(error));
  });
}

// ---------------------------------------------------------------------------
// Prereq probes (contract §2 app_prereq_check: claude on PATH · node ≥22.12 ·
// toolchain installed (toolchain/node_modules present) · python ≥3.10 ·
// kicad-cli reported, NOT required)
// ---------------------------------------------------------------------------

function probeVersion(bin, args, parse) {
  return new Promise((resolve) => {
    const child = execFile(bin, args, { timeout: 5000 }, (error, stdout) => {
      if (error) {
        resolve(undefined);
        return;
      }
      resolve(parse(String(stdout || "")));
    });
    child.on("error", () => resolve(undefined));
  });
}

function findOnAugmentedPath(name, env) {
  for (const dir of augmentedPathDirs(env)) {
    const candidate = path.join(dir, name);
    try {
      if (fs.statSync(candidate).isFile()) {
        return candidate;
      }
    } catch {
      // keep looking
    }
  }
  return null;
}

/** The repo's pinned Node toolchain dir: env CIRCUIT_TOOLCHAIN > repo default
 * (`<repo>/toolchain`, four levels above this file). */
export function toolchainDir(env = process.env) {
  if (env.CIRCUIT_TOOLCHAIN) {
    return path.resolve(env.CIRCUIT_TOOLCHAIN);
  }
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname),
    "..",
    "..",
    "..",
    "..",
    "toolchain",
  );
}

/** kicad-cli lives on PATH or inside a macOS app bundle (contract §1).
 * The user-local bundle is listed because the Homebrew cask needs sudo for a
 * shared demos folder, so extracting the app into ~/Applications is the
 * no-sudo install — and a board is only shippable when kicad-cli is found.
 * Keep this list in step with circuitpy/toolchain.py's KICAD_APP_BUNDLES. */
const KICAD_APP_BUNDLE_BINS = [
  "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
  "/Applications/KiCad.app/Contents/MacOS/kicad-cli",
  path.join(os.homedir(), "Applications/KiCad.app/Contents/MacOS/kicad-cli"),
  path.join(os.homedir(), "Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"),
];

async function prereqCheck(env) {
  const claudePath = resolveClaude(env);

  // node ≥22.12 — the tscircuit toolchain's floor.
  const nodePath = findOnAugmentedPath("node", env);
  let nodeVersion;
  let nodeHealthy = false;
  if (nodePath) {
    nodeVersion = await probeVersion(nodePath, ["--version"], (out) => {
      const match = out.match(/v?(\d+)\.(\d+)\.(\d+)/);
      return match ? `${match[1]}.${match[2]}.${match[3]}` : undefined;
    });
    if (nodeVersion) {
      const [major, minor] = nodeVersion.split(".").map((n) => Number(n));
      nodeHealthy = major > 22 || (major === 22 && minor >= 12);
    }
  }

  // toolchain — pinned deps installed by scripts/setup-toolchain.sh.
  const toolchain = toolchainDir(env);
  const toolchainFound = fs.existsSync(path.join(toolchain, "node_modules"));

  // python ≥3.10 — CIRCUIT_PYTHON wins; else python3.12 (the known-good
  // interpreter), else whatever python3 is on the augmented PATH.
  const pythonPath =
    (env.CIRCUIT_PYTHON && fs.existsSync(env.CIRCUIT_PYTHON) ? env.CIRCUIT_PYTHON : null) ||
    findOnAugmentedPath("python3.12", env) ||
    findOnAugmentedPath("python3", env);
  let pythonVersion;
  let pythonHealthy = false;
  if (pythonPath) {
    pythonVersion = await probeVersion(pythonPath, ["--version"], (out) => {
      const match = out.match(/Python\s+(\d+)\.(\d+)(?:\.(\d+))?/);
      return match ? match[0].replace(/^Python\s+/, "") : undefined;
    });
    if (pythonVersion) {
      const [major, minor] = pythonVersion.split(".").map((n) => Number(n));
      pythonHealthy = major > 3 || (major === 3 && minor >= 10);
    }
  }

  // kicad-cli — reported, not required (absent → tscircuit-exported gerbers
  // with a blocking-for-ship unverified_gerbers warning, per contract §1).
  let kicadPath =
    (env.CIRCUIT_KICAD_CLI && fs.existsSync(env.CIRCUIT_KICAD_CLI)
      ? env.CIRCUIT_KICAD_CLI
      : null) || findOnAugmentedPath("kicad-cli", env);
  if (!kicadPath) {
    for (const candidate of KICAD_APP_BUNDLE_BINS) {
      try {
        if (fs.statSync(candidate).isFile()) {
          kicadPath = candidate;
          break;
        }
      } catch {
        // not at this location; try the next
      }
    }
  }
  let kicadVersion;
  if (kicadPath) {
    kicadVersion = await probeVersion(kicadPath, ["version"], (out) => {
      const match = out.match(/(\d+\.\d+\.\d+)/);
      return match ? match[1] : undefined;
    });
  }

  return {
    claudeCli: { found: Boolean(claudePath) },
    node: {
      found: Boolean(nodePath),
      ...(nodeVersion ? { version: nodeVersion } : {}),
      healthy: nodeHealthy,
    },
    toolchain: { found: toolchainFound, path: toolchain },
    python: {
      found: Boolean(pythonPath),
      ...(pythonVersion ? { version: pythonVersion } : {}),
      healthy: pythonHealthy,
    },
    kicadCli: {
      found: Boolean(kicadPath),
      ...(kicadVersion ? { version: kicadVersion } : {}),
      required: false,
    },
  };
}

// ---------------------------------------------------------------------------
// Service wiring
// ---------------------------------------------------------------------------

function readViewerVersion() {
  try {
    const pkgPath = path.resolve(
      path.dirname(new URL(import.meta.url).pathname),
      "..",
      "..",
      "..",
      "package.json",
    );
    return String(JSON.parse(fs.readFileSync(pkgPath, "utf8")).version || "0.0.0");
  } catch {
    return "0.0.0";
  }
}

/**
 * Build the full Circuit backend: settings + projects + catalog + chat driver +
 * SSE hub, exposed as two connect middlewares.
 *
 *   const circuit = createCircuitServices();
 *   server.use(circuit.apiMiddleware);   // POST /api/<cmd>, GET /api/events
 *   server.use(circuit.assetMiddleware); // GET /projects/<id>/<rel>?v=…
 *   … circuit.close();
 */
export function createCircuitServices({ env = process.env } = {}) {
  const home = circuitHome(env);
  const projectsRoot = projectsRootDir(env);
  fs.mkdirSync(projectsRoot, { recursive: true });

  const settings = createSettingsStore({ filePath: settingsFilePath(env) });

  // --- SSE hub -------------------------------------------------------------
  const clients = new Set(); // { res, projectId|null }

  function writeSse(client, eventName, payload) {
    try {
      client.res.write(`event: ${eventName}\ndata: ${JSON.stringify(payload)}\n\n`);
    } catch {
      clients.delete(client);
    }
  }

  /** Every chat event goes out enveloped: `{ …ChatEvent, projectId }`. */
  function broadcastChatEvent(projectId, event) {
    const enveloped = { ...event, projectId };
    for (const client of [...clients]) {
      if (client.projectId && client.projectId !== projectId) {
        continue; // optional per-connection filter
      }
      writeSse(client, "chat_event", enveloped);
    }
  }

  function broadcastCatalogChanged(revision) {
    for (const client of [...clients]) {
      writeSse(client, "catalog_changed", { revision });
    }
  }

  // --- stores + services ---------------------------------------------------
  const projects = createProjectsStore({
    rootDir: projectsRoot,
    env,
    sessionIdForProject,
  });

  const catalog = createCatalogService({
    projectDir: (id) => projects.projectDir(id),
    onCatalogChanged: broadcastCatalogChanged,
  });

  const chat = createChatService({
    projectDir: (id) => projects.projectDir(id),
    settings,
    emit: broadcastChatEvent,
    env,
  });

  let activeProjectId = null;

  function requireProject(id) {
    if (!id || !projects.exists(id)) {
      throw ipcError("PROJECT_NOT_FOUND", `project not found: ${id}`, 404);
    }
    return String(id);
  }

  // --- command handlers (contract §2 command list, verbatim) ---------------
  const commands = {
    // app
    app_info: async () => ({
      rootPath: projectsRoot,
      appVersion: readViewerVersion(),
      pid: process.pid,
    }),
    app_prereq_check: async () => prereqCheck(env),

    // Live build progress. The pipeline writes its current stage to
    // `<project>/.circuit/build-status.json`, and `.circuit/` is skipped by
    // both the catalog scanner and the artifact snapshotter — deliberately,
    // so progress never masquerades as a new artifact — which means no SSE
    // event carries it. The client polls this while a turn is running so a
    // 90-second build reads as "Cross-checking with KiCad" rather than a
    // spinner that might be a hang.
    build_status: async ({ id }) => {
      const projectId = requireProject(id);
      const file = path.join(
        projects.projectDir(projectId),
        ".circuit",
        "build-status.json",
      );
      try {
        const raw = fs.readFileSync(file, "utf8");
        const status = JSON.parse(raw);
        // A "running" record left behind by a killed build would spin
        // forever; anything unheard from for two minutes is stale, and
        // saying so is better than lying about progress.
        const updatedAt = Number(status?.updatedAt) * 1000;
        if (
          status?.state === "running" &&
          Number.isFinite(updatedAt) &&
          Date.now() - updatedAt > 120_000
        ) {
          return { ...status, state: "stale" };
        }
        return status;
      } catch {
        return null; // no build has run, or the file is mid-write
      }
    },
    app_settings_read: async () => settings.readWire(),
    app_settings_write: async ({ settings: next }) => {
      settings.write(next && typeof next === "object" ? next : {});
      return null;
    },
    app_set_model: async ({ model }) => {
      settings.write({ model: typeof model === "string" ? model : "" });
      return settings.readWire();
    },

    // projects
    project_list: async () => projects.list(),
    project_create: async ({ req }) => projects.create(req?.name),
    project_open: async ({ id }) => {
      requireProject(id);
      activeProjectId = String(id);
      catalog.activate(activeProjectId);
      return projects.open(id);
    },
    project_rename: async ({ id, name }) => {
      requireProject(id);
      return projects.rename(id, name);
    },
    project_delete: async ({ id }) => {
      requireProject(id);
      catalog.deactivate(String(id));
      projects.remove(id);
      if (activeProjectId === String(id)) {
        activeProjectId = null;
      }
      return null;
    },

    // catalog
    catalog_read: async () => {
      if (!activeProjectId || !projects.exists(activeProjectId)) {
        return { entries: [], rootPath: "", revision: catalog.revision };
      }
      return catalog.read(activeProjectId);
    },
    project_catalog_read: async ({ id }) => {
      requireProject(id);
      catalog.activate(String(id));
      return catalog.read(String(id));
    },

    // chat
    chat_start_turn: async ({ req }) => {
      const projectId = requireProject(req?.projectId);
      let message = String(req?.userMessage ?? "");
      const images = Array.isArray(req?.images) ? req.images : [];
      let imagePaths = [];
      if (images.length) {
        const workspace = projects.projectDir(projectId);
        const rels = persistAttachments(workspace, images);
        imagePaths = rels.map((rel) => path.join(workspace, rel));
        message += attachmentNote(rels);
      }
      projects.touch(projectId);
      const turnId = chat.startTurn({
        projectId,
        message,
        imagePaths,
        phase: PHASE.PLAN,
      });
      return { turnId };
    },
    chat_approve_plan: async ({ req }) => {
      const projectId = requireProject(req?.projectId);
      const turnId = chat.startTurn({
        projectId,
        message: approvedPlanMessage(String(req?.planText ?? "")),
        phase: PHASE.IMPLEMENT,
      });
      return { turnId };
    },
    chat_request_plan_changes: async ({ req }) => {
      const projectId = requireProject(req?.projectId);
      const turnId = chat.startTurn({
        projectId,
        message: String(req?.feedback ?? ""),
        phase: PHASE.PLAN,
      });
      return { turnId };
    },
    chat_cancel_turn: async ({ turnId }) => {
      chat.cancelTurn(String(turnId || ""));
      return null;
    },
    chat_session_state: async ({ projectId }) => {
      requireProject(projectId);
      return chat.sessionState(String(projectId));
    },
  };

  // --- middlewares ----------------------------------------------------------

  async function handleCommand(commandName, req, res) {
    const handler = commands[commandName];
    if (!handler) {
      sendIpcError(res, ipcError("UNKNOWN_COMMAND", `unknown command: ${commandName}`, 404));
      return;
    }
    if (String(req.method || "").toUpperCase() !== "POST") {
      res.setHeader("allow", "POST");
      sendIpcError(res, ipcError("METHOD_NOT_ALLOWED", "use POST for /api commands", 405));
      return;
    }
    let body;
    try {
      body = await readJsonBody(req);
    } catch (error) {
      sendIpcError(res, error);
      return;
    }
    try {
      const result = await handler(body && typeof body === "object" ? body : {});
      sendJson(res, 200, result ?? null);
    } catch (error) {
      if (!error?.code) {
        console.error(LOG_TAG, `${commandName} failed:`, error);
      }
      sendIpcError(res, error);
    }
  }

  function handleEvents(req, res, requestUrl) {
    const projectId = requestUrl.searchParams.get("projectId") || null;
    res.statusCode = 200;
    res.setHeader("content-type", "text/event-stream; charset=utf-8");
    res.setHeader("cache-control", "no-store");
    res.setHeader("connection", "keep-alive");
    res.setHeader("x-accel-buffering", "no");
    res.flushHeaders?.();
    res.write(`retry: 3000\n\n`);

    const client = { res, projectId };
    clients.add(client);
    const heartbeat = setInterval(() => {
      try {
        res.write(":hb\n\n");
      } catch {
        // cleaned up below
      }
    }, SSE_HEARTBEAT_MS);
    heartbeat.unref?.();
    const cleanup = () => {
      clearInterval(heartbeat);
      clients.delete(client);
    };
    res.on("close", cleanup);
    req.on("close", cleanup);
  }

  function apiMiddleware(req, res, next) {
    let requestUrl;
    try {
      requestUrl = new URL(req.url || "/", "http://localhost");
    } catch {
      next();
      return;
    }
    const { pathname } = requestUrl;
    if (!pathname.startsWith("/api/")) {
      next();
      return;
    }
    if (pathname === "/api/events") {
      handleEvents(req, res, requestUrl);
      return;
    }
    const commandName = decodeURIComponent(pathname.slice("/api/".length));
    if (!/^[a-z][a-z0-9_]*$/.test(commandName)) {
      sendIpcError(res, ipcError("UNKNOWN_COMMAND", `unknown command: ${commandName}`, 404));
      return;
    }
    handleCommand(commandName, req, res).catch((error) => {
      if (!res.writableEnded) {
        sendIpcError(res, error);
      }
    });
  }

  // --- asset serving (donor localAsset pattern, rooted at project dirs) ----

  function assetPathForRequest(pathname) {
    // /projects/<id>/<rel…>
    const match = /^\/projects\/([^/]+)\/(.+)$/.exec(pathname);
    if (!match) {
      return null;
    }
    const projectId = decodeURIComponent(match[1]);
    const rel = decodeURIComponent(match[2]);
    let projectRoot;
    try {
      projectRoot = projects.projectDir(projectId);
    } catch {
      return null;
    }
    const candidate = path.resolve(projectRoot, rel);
    if (!(candidate === projectRoot || candidate.startsWith(`${projectRoot}${path.sep}`))) {
      const error = new Error("Forbidden");
      error.statusCode = 403;
      throw error;
    }
    if (!ASSET_CONTENT_TYPES.has(path.extname(candidate).toLowerCase())) {
      return null;
    }
    return candidate;
  }

  function assetMiddleware(req, res, next) {
    let requestUrl;
    try {
      requestUrl = new URL(req.url || "/", "http://localhost");
    } catch {
      next();
      return;
    }
    if (!requestUrl.pathname.startsWith("/projects/")) {
      next();
      return;
    }
    let assetPath;
    try {
      assetPath = assetPathForRequest(requestUrl.pathname);
    } catch (error) {
      res.statusCode = Number(error?.statusCode) || 403;
      res.end("Forbidden");
      return;
    }
    if (!assetPath) {
      next();
      return;
    }
    fs.stat(assetPath, (error, stats) => {
      if (res.destroyed) {
        return;
      }
      if (error || !stats.isFile()) {
        res.statusCode = 404;
        res.setHeader("content-type", "text/plain; charset=utf-8");
        res.end("Not found");
        return;
      }
      const contentType = ASSET_CONTENT_TYPES.get(path.extname(assetPath).toLowerCase());
      res.setHeader("content-type", contentType);
      res.setHeader("accept-ranges", "bytes");
      // URLs are cache-busted with ?v=<mtimeNs>-<size>, so long-cache is safe.
      res.setHeader("cache-control", requestUrl.searchParams.has("v")
        ? "public, max-age=31536000, immutable"
        : "no-store");

      // Byte ranges: kept from the donor — cheap, and resumable downloads of
      // large fab zips/GLBs get 206s for free.
      const range = /^bytes=(\d*)-(\d*)$/.exec(String(req.headers.range || ""));
      let start = 0;
      let end = stats.size - 1;
      if (range && (range[1] !== "" || range[2] !== "")) {
        if (range[1] === "") {
          const suffix = Number(range[2]);
          start = Math.max(stats.size - suffix, 0);
        } else {
          start = Number(range[1]);
          if (range[2] !== "") {
            end = Math.min(Number(range[2]), stats.size - 1);
          }
        }
        if (Number.isNaN(start) || Number.isNaN(end) || start > end || start >= stats.size) {
          res.statusCode = 416;
          res.setHeader("content-range", `bytes */${stats.size}`);
          res.end();
          return;
        }
        res.statusCode = 206;
        res.setHeader("content-range", `bytes ${start}-${end}/${stats.size}`);
      } else {
        res.statusCode = 200;
      }
      res.setHeader("content-length", String(end - start + 1));
      if (String(req.method || "").toUpperCase() === "HEAD") {
        res.end();
        return;
      }
      const stream = fs.createReadStream(assetPath, { start, end });
      res.on("close", () => {
        if (!res.writableEnded) {
          stream.destroy();
        }
      });
      stream.on("error", () => {
        if (!res.headersSent) {
          res.statusCode = 500;
          res.end();
        } else {
          res.destroy();
        }
      });
      stream.pipe(res);
    });
  }

  function close() {
    chat.close();
    catalog.close();
    for (const client of [...clients]) {
      try {
        client.res.end();
      } catch {
        // already gone
      }
    }
    clients.clear();
  }

  return {
    home,
    projectsRoot,
    settings,
    projects,
    catalog,
    chat,
    apiMiddleware,
    assetMiddleware,
    broadcastChatEvent,
    broadcastCatalogChanged,
    get activeProjectId() {
      return activeProjectId;
    },
    close,
  };
}
