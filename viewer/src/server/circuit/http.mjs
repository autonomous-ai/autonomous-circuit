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
import { readRevisions, recordEdit, revisionTrend } from "./revisions.mjs";
import { createEditQueue, planPlacementEdit, writeAtomic } from "./boardEdit.mjs";
import { runFastCheck } from "./fastCheck.mjs";
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

/**
 * How long a build stage may go quiet before we call it stalled.
 *
 * The pipeline writes `build-status.json` on stage transitions only — no
 * heartbeat — so "time since the last write" is really "how long this stage
 * has been running". A single flat limit therefore has to be longer than the
 * slowest legitimate stage, and the old one was 120 seconds.
 *
 * That was shorter than an ordinary compile. Worse, the router escalation
 * retry (stage 0b, `autorouterEffortLevel="5x"`) spends ~15 minutes inside
 * `compile` on a large board, so the app announced **"Build stopped
 * responding" over a perfectly healthy build** — and would have done it on
 * every escalated build. Caught by watching a real 35-minute run, which is the
 * only way this kind of thing gets caught.
 *
 * So the limit is per stage, sized to what that stage actually does. Generous
 * on the two that route and convert; tight on the ones that only move files,
 * where silence really does mean something died.
 */
const STAGE_QUIET_LIMIT_MS = {
  compile: 45 * 60_000,   // routes the board; 5x effort on 400+ traces is ~20 min
  substrate: 15 * 60_000, // KiCad conversion + ERC/DRC on a dense board
  export: 10 * 60_000,    // gerbers, drill, BOM, CPL, 3D
  scan: 5 * 60_000,
  dfm: 5 * 60_000,
  render: 5 * 60_000,
};

/** Unknown stages get the most generous limit — a stage we do not recognise is
 * one we cannot predict the cost of, and a false "stalled" is worse than a
 * late one. */
const DEFAULT_QUIET_LIMIT_MS = 45 * 60_000;

export function quietLimitMs(stage) {
  return STAGE_QUIET_LIMIT_MS[String(stage ?? "")] ?? DEFAULT_QUIET_LIMIT_MS;
}

// ---------------------------------------------------------------------------
// board_source_write — the one command that lets the UI change a board file
// ---------------------------------------------------------------------------

/** Most edits a single drag or lock can produce; a drag makes two. */
const MAX_SOURCE_EDITS = 8;
/** Longest replacement text we will splice in. The lock comment is the big one. */
const MAX_EDIT_TEXT = 200;

/**
 * Check one write request against the file on disk.
 *
 * This is a compare-and-swap, and it has to be, because the agent writing this
 * same file is the whole point of the app. The client sends the byte ranges it
 * means to replace **and the text it expects to find there**, plus the length
 * of the file it read them from. If either has moved, the write is refused and
 * the UI re-reads — a stale offset would otherwise land a coordinate in the
 * middle of a comment.
 *
 * Exported so `node:test` can drive every refusal without an HTTP server.
 *
 * @returns {{ok: true, text: string} | {ok: false, code: string, message: string}}
 */
export function planSourceWrite(current, edits, sourceLength) {
  const text = String(current ?? "");
  if (!Array.isArray(edits) || !edits.length) {
    return { ok: false, code: "INVALID_ARGUMENT", message: "no edits in the request" };
  }
  if (edits.length > MAX_SOURCE_EDITS) {
    return { ok: false, code: "INVALID_ARGUMENT", message: `at most ${MAX_SOURCE_EDITS} edits per write` };
  }
  if (!Number.isInteger(sourceLength) || sourceLength !== text.length) {
    return {
      ok: false,
      code: "SOURCE_CHANGED",
      message: "the board file changed since it was read — reopen the board and try again",
    };
  }
  const sorted = [...edits].sort((a, b) => Number(a.start) - Number(b.start) || Number(a.end) - Number(b.end));
  let previousEnd = -1;
  for (const edit of sorted) {
    const start = Number(edit?.start);
    const end = Number(edit?.end);
    const replacement = String(edit?.text ?? "");
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > text.length) {
      return { ok: false, code: "INVALID_ARGUMENT", message: "an edit points outside the file" };
    }
    if (replacement.length > MAX_EDIT_TEXT) {
      return { ok: false, code: "INVALID_ARGUMENT", message: "an edit is longer than a placement change can be" };
    }
    // Tab, newline and carriage return are the only invisible bytes allowed.
    if (/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(replacement)) {
      return { ok: false, code: "INVALID_ARGUMENT", message: "an edit carries control characters" };
    }
    // `start === end` is an insertion (the lock comment); two insertions at the
    // same point would be order-dependent, so they are refused too.
    if (start < previousEnd || (start === previousEnd && start === end)) {
      return { ok: false, code: "INVALID_ARGUMENT", message: "edits overlap" };
    }
    if (text.slice(start, end) !== String(edit?.expected ?? "")) {
      return {
        ok: false,
        code: "SOURCE_CHANGED",
        message: "the board file changed since it was read — reopen the board and try again",
      };
    }
    previousEnd = end;
  }
  let out = text;
  for (let i = sorted.length - 1; i >= 0; i -= 1) {
    out = out.slice(0, sorted[i].start) + String(sorted[i].text ?? "") + out.slice(sorted[i].end);
  }
  return { ok: true, text: out };
}

/**
 * Where a board source may live: `boards/<stem>.tsx`, directly under the
 * project root and nowhere else. Not a general file-write command — the app
 * has exactly one reason to change a file on disk, and widening this to "any
 * path inside the project" would make it a general one by accident.
 *
 * @returns {string|null} the relative path, normalised, or null when refused
 */
export function boardSourceRelPath(file) {
  const rel = String(file ?? "").replaceAll("\\", "/");
  if (!/^boards\/[A-Za-z0-9._-]+\.tsx$/.test(rel)) return null;
  if (rel.includes("..") || rel.includes("//")) return null;
  const base = rel.slice("boards/".length);
  if (base.startsWith(".") || base.startsWith("_")) return null;
  return rel;
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

  /** The pipeline's own view of whether it is mid-build, read the same way
   *  `build_status` reads it (including the per-stage staleness limit, so a
   *  build that died three hours ago does not lock the file forever). */
  function pipelineRunning(projectId) {
    try {
      const status = JSON.parse(
        fs.readFileSync(path.join(projects.projectDir(projectId), ".circuit", "build-status.json"), "utf8"),
      );
      if (status?.state !== "running") return false;
      const updatedAt = Number(status?.updatedAt) * 1000;
      if (!Number.isFinite(updatedAt)) return true;
      return Date.now() - updatedAt <= quietLimitMs(status?.stage);
    } catch {
      return false;
    }
  }

  /**
   * Resolve `boards/<stem>.tsx` inside a project, or throw the refusal.
   *
   * Shared by both write commands so there is exactly one answer to "may this
   * path be written". The `lstat` matters: the path shape can be perfectly
   * legal while the file itself is a symlink pointing out of the project.
   */
  function resolveBoardSource(projectId, file) {
    const rel = boardSourceRelPath(file);
    if (!rel) {
      throw ipcError("INVALID_ARGUMENT", "only a board source under boards/ can be edited", 400);
    }
    const root = fs.realpathSync(projects.projectDir(projectId));
    const abs = path.resolve(root, rel);
    if (!abs.startsWith(`${root}${path.sep}`)) {
      throw ipcError("FORBIDDEN", "that path is outside the project", 403);
    }
    let stat;
    try {
      stat = fs.lstatSync(abs);
    } catch {
      throw ipcError("NOT_FOUND", `no board source at ${rel}`, 404);
    }
    if (!stat.isFile()) {
      throw ipcError("FORBIDDEN", "that board source is not a regular file", 403);
    }
    return { rel, root, abs };
  }

  /** Refuse any write while something else owns the board file. */
  function refuseIfBuilding(projectId) {
    if (chat.turnInProgress(projectId) || pipelineRunning(projectId)) {
      throw ipcError(
        "BUILD_RUNNING",
        "the board is building — wait for it to finish before moving parts",
        409,
      );
    }
  }

  // One promise chain per project: a semantic edit is a read-modify-write, and
  // two of them arriving together would otherwise both read the pre-edit file.
  const serializeEdit = createEditQueue();

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
        const updatedAt = Number(status?.updatedAt) * 1000;
        if (
          status?.state === "running" &&
          Number.isFinite(updatedAt) &&
          Date.now() - updatedAt > quietLimitMs(status?.stage)
        ) {
          return { ...status, state: "stale" };
        }
        return status;
      } catch {
        return null; // no build has run, or the file is mid-write
      }
    },
    // Build history — what the board looked like on previous rounds.
    // `build_status` says where this build is now; this says where the board
    // came from, which is the more convincing sentence: "6 blockers three
    // builds ago, 1 now" is visible convergence rather than a bare complaint.
    // Written by the review loop to `<project>/.circuit/revisions.jsonl`,
    // inside the same `.circuit/` the snapshotter skips, so history never
    // masquerades as a changed artifact.
    build_revisions: async ({ id, limit }) => {
      const projectId = requireProject(id);
      const dir = projects.projectDir(projectId);
      const revisions = readRevisions(dir, {
        limit: Number.isFinite(limit) ? Number(limit) : undefined,
      });
      return { revisions, trend: revisionTrend(revisions) };
    },

    /**
     * Write a placement change back into `boards/<stem>.tsx`.
     *
     * The only command in this app that changes a file the user owns, so it is
     * deliberately the narrowest one: a fixed path shape, a byte-range splice
     * whose expected text has to match, and a refusal whenever the agent is
     * mid-turn. A drag on the canvas is not allowed to be a general
     * file-write API by accident.
     *
     * Returns the file as it now stands so the client re-parses the truth
     * rather than its own prediction of it.
     */
    board_source_write: async ({ id, file, edits, sourceLength }) => {
      const projectId = requireProject(id);
      refuseIfBuilding(projectId);
      const { rel, abs } = resolveBoardSource(projectId, file);
      const current = fs.readFileSync(abs, "utf8");
      const planned = planSourceWrite(current, edits, sourceLength);
      if (!planned.ok) {
        throw ipcError(planned.code, planned.message, planned.code === "SOURCE_CHANGED" ? 409 : 400);
      }
      // Written through a sibling temp file and renamed: a half-written board
      // source is one the next build cannot compile, and the build may start
      // the instant the catalog watcher notices the change.
      writeAtomic(abs, planned.text);
      projects.touch(projectId);
      return { file: rel, text: planned.text, sourceLength: planned.text.length };
    },

    /**
     * Apply one semantic placement edit, then say what it costs.
     *
     * The whole round trip: serialise against other edits to this project →
     * refuse if the agent or the pipeline holds the file → read → parse with
     * the same engine the canvas uses → compare-and-swap → atomic write →
     * grade the predicted geometry in ~1.2s → record the round in the same
     * history a build lands in.
     *
     * The three words stay separate in the response. `saved` is true the
     * moment the literal is on disk. `check.status` may reach `legal`. Nothing
     * here may say `orderable` — see `check.notChecked`, which the Python side
     * fills in and which always includes the copper pour, because no gate in
     * this pipeline can see a pour defect.
     */
    board_edit_apply: async ({ id, file, edit, sourceLength, builtAnchor, verify = true }) => {
      const projectId = requireProject(id);
      return serializeEdit(projectId, async () => {
        // Re-checked inside the queue, not before it: a build can start while
        // this request waits its turn behind another edit.
        refuseIfBuilding(projectId);
        const { rel, root, abs } = resolveBoardSource(projectId, file);
        const current = fs.readFileSync(abs, "utf8");
        // The client's length is a compare-and-swap token against a write by
        // the agent between its read and this call. Omitted, the server's own
        // read is the baseline — safe here only because the parse and the
        // write happen inside one turn of this queue.
        if (sourceLength !== undefined && sourceLength !== current.length) {
          throw ipcError(
            "SOURCE_CHANGED",
            "the board file changed since it was read — reopen the board and try again",
            409,
          );
        }
        let planned;
        try {
          planned = planPlacementEdit(current, edit);
        } catch (error) {
          const code = String(error?.code || "INVALID_ARGUMENT");
          throw ipcError(code, String(error?.message || error), code === "NO_CHANGE" ? 409 : 400);
        }
        const write = planSourceWrite(current, planned.edits, current.length);
        if (!write.ok) {
          throw ipcError(write.code, write.message, write.code === "SOURCE_CHANGED" ? 409 : 400);
        }
        writeAtomic(abs, write.text);
        projects.touch(projectId);

        // The gate grades the BUILT board with this move applied to it. The
        // anchor is therefore the position the last build was made from, which
        // the client carries per placement across edits — not the value that
        // was in the file a moment ago, which is already one drag stale if the
        // user has moved this part twice without rebuilding.
        const anchor = Number.isFinite(Number(builtAnchor?.x)) && Number.isFinite(Number(builtAnchor?.y))
          ? { x: Number(builtAnchor.x), y: Number(builtAnchor.y) }
          : { x: planned.placement.x, y: planned.placement.y };
        const stem = rel.slice("boards/".length, -".tsx".length);
        let check = null;
        if (verify) {
          check = await runFastCheck(path.join(root, "boards", `${stem}.circuit.json`), {
            projectRoot: root,
            moves: [{ anchor, dx: planned.delta.dx, dy: planned.delta.dy }],
            env,
          });
        }

        // History gets the round whether or not the gate ran, because "a human
        // moved this part" is the fact worth keeping; the counts are the part
        // that may be missing.
        recordEdit(root, {
          summary: planned.summary,
          file: rel,
          counts: {
            total: check?.warnings?.length ?? 0,
            blocking: check?.counts?.error ?? 0,
            electrical: 0,
            other: 0,
          },
        });

        return {
          saved: true,
          file: rel,
          text: write.text,
          sourceLength: write.text.length,
          summary: planned.summary,
          delta: planned.delta,
          placement: {
            id: planned.placement.id,
            tag: planned.placement.tag,
            name: planned.placement.name,
            ...planned.next,
          },
          check,
        };
      });
    },

    /**
     * The same verdict without an edit — "how does the board stand right now".
     * Same gate, same blind spots, no write, so it is safe to call while the
     * user is reading rather than editing.
     */
    board_fast_check: async ({ id, file, moves = [] }) => {
      const projectId = requireProject(id);
      const rel = boardSourceRelPath(file);
      if (!rel) {
        throw ipcError("INVALID_ARGUMENT", "only a board under boards/ can be checked", 400);
      }
      const root = fs.realpathSync(projects.projectDir(projectId));
      const stem = rel.slice("boards/".length, -".tsx".length);
      return runFastCheck(path.join(root, "boards", `${stem}.circuit.json`), {
        projectRoot: root,
        moves: Array.isArray(moves) ? moves : [],
        env,
      });
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

    // Reasoning effort, the sibling of app_set_model. The UI grew an effort
    // selector before there was anywhere to put the value, so it was a
    // prompt directive pretending to be a setting; this persists it and the
    // driver sends it as --effort. An unknown level falls back to the pinned
    // default rather than being passed through to the CLI.
    app_set_effort: async ({ effort }) => {
      settings.write({ effort: typeof effort === "string" ? effort : "" });
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
