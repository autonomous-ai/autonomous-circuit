// Circuit projects store — ~/.autonomous-circuit/projects/<uuid4>/ CRUD.
//
// Ports the donor's `desktop/src-tauri/src/commands/project.rs` semantics to
// Node per docs/circuit-interfaces.md §2:
//   - `project.json` is snake_case and pretty-printed: {id, name, created_at,
//     updated_at}.
//   - Placeholder-name self-heal: while a project still carries the
//     "New project" placeholder (or an empty name), re-resolve it from the
//     Claude Code session JSONL's `ai-title` lines on every read.
//   - `encodeCwd` replaces EVERY non-alphanumeric char with `-` (the donor
//     footgun: Claude Code's `cwd.replace(/[^a-zA-Z0-9]/g, '-')` — replacing
//     only `/` mismatches paths containing spaces or dots, the driver then
//     passes `--session-id` for an existing session and claude dies with
//     "Session ID already in use").

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

/** Name given to a project created without one; upgraded by the self-heal
 * once the session JSONL contains an `ai-title` line. */
export const PLACEHOLDER_PROJECT_NAME = "New project";

const PROJECT_META_FILE = "project.json";

/** Dirs never scanned for artifacts (mirrors the catalog skip-list, contract
 * §2: inputs/, .circuit/, .claude/, node_modules, blocks/). */
const SKIP_DIR_NAMES = new Set([
  "inputs",
  ".circuit",
  ".claude",
  "node_modules",
  "blocks",
  "__pycache__",
  ".git",
]);

export function skipDirNames() {
  return SKIP_DIR_NAMES;
}

/** Root of all Circuit state (`~/.autonomous-circuit` unless CIRCUIT_HOME overrides). */
export function circuitHome(env = process.env) {
  return env.CIRCUIT_HOME || path.join(os.homedir(), ".autonomous-circuit");
}

export function projectsRootDir(env = process.env) {
  return path.join(circuitHome(env), "projects");
}

/** Claude Code's config dir. The `claude` CLI honors CLAUDE_CONFIG_DIR, so we
 * read the same env for session JSONL lookups (and tests point it at a temp
 * dir instead of the real `~/.claude`). */
export function claudeConfigDir(env = process.env) {
  return env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude");
}

/**
 * Encode an absolute path the way Claude Code persists session dirs: replace
 * every character that is not ASCII alphanumeric with `-`, 1:1, no collapsing
 * (matches `cwd.replace(/[^a-zA-Z0-9]/g, '-')`).
 */
export function encodeCwd(absolutePath) {
  return String(absolutePath).replace(/[^a-zA-Z0-9]/g, "-");
}

/**
 * Path Claude Code persists a session JSONL at:
 * `<claudeConfigDir>/projects/<encodeCwd(workspace)>/<sessionId>.jsonl`.
 * Resolves the workspace to its real path first (falling back to the raw
 * absolute path) so the encoding matches Claude Code's own `cwd`.
 */
export function sessionJsonlPath(workspace, sessionId, env = process.env) {
  let abs = path.resolve(workspace);
  try {
    abs = fs.realpathSync(abs);
  } catch {
    // keep the resolved-but-not-real path (workspace may not exist yet)
  }
  return path.join(
    claudeConfigDir(env),
    "projects",
    encodeCwd(abs),
    `${sessionId}.jsonl`,
  );
}

/**
 * Scan session-JSONL text for the last `ai-title` line's non-empty `aiTitle`.
 * Claude Code appends `{"type":"ai-title","aiTitle":"…"}` lines to the session
 * transcript. Returns null when absent. Pure.
 */
export function parseLatestAiTitle(contents) {
  let latest = null;
  for (const line of String(contents || "").split("\n")) {
    if (!line.includes('"ai-title"')) {
      continue;
    }
    try {
      const obj = JSON.parse(line);
      if (obj && obj.type === "ai-title") {
        const title = String(obj.aiTitle || "").trim();
        if (title) {
          latest = title;
        }
      }
    } catch {
      // partial trailing write — skip
    }
  }
  return latest;
}

function nowMs() {
  return Date.now();
}

function metaPath(projectDir) {
  return path.join(projectDir, PROJECT_META_FILE);
}

/** Write `project.json` snake_case + pretty (2-space indent, trailing \n). */
function writeMeta(projectDir, meta) {
  const payload = {
    id: meta.id,
    name: meta.name,
    created_at: meta.created_at,
    updated_at: meta.updated_at,
  };
  fs.mkdirSync(projectDir, { recursive: true });
  fs.writeFileSync(metaPath(projectDir), `${JSON.stringify(payload, null, 2)}\n`);
}

function readMeta(projectDir) {
  try {
    const raw = fs.readFileSync(metaPath(projectDir), "utf8");
    const obj = JSON.parse(raw);
    if (!obj || typeof obj !== "object") {
      return null;
    }
    return {
      id: String(obj.id || path.basename(projectDir)),
      name: String(obj.name ?? ""),
      created_at: Number(obj.created_at) || 0,
      updated_at: Number(obj.updated_at) || 0,
    };
  } catch {
    return null;
  }
}

/** Does the project dir contain any built board yet (contract §2: any
 * `*.circuit.json`)? Recursive, honors the skip-list. */
function hasBoard(projectDir) {
  const stack = [projectDir];
  while (stack.length) {
    const dir = stack.pop();
    let dirents;
    try {
      dirents = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of dirents) {
      if (entry.isDirectory()) {
        if (SKIP_DIR_NAMES.has(entry.name)) {
          continue;
        }
        stack.push(path.join(dir, entry.name));
        continue;
      }
      if (entry.isFile() && entry.name.toLowerCase().endsWith(".circuit.json")) {
        return true;
      }
    }
  }
  return false;
}

function toSummary(projectDir, meta) {
  return {
    id: meta.id,
    name: meta.name,
    createdAt: meta.created_at,
    updatedAt: meta.updated_at,
    hasModel: hasBoard(projectDir),
  };
}

export class ProjectNotFoundError extends Error {
  constructor(id) {
    super(`project not found: ${id}`);
    this.code = "PROJECT_NOT_FOUND";
    this.statusCode = 404;
  }
}

/**
 * Create the projects store. `sessionIdForProject` is injected (it lives in
 * driver.mjs with the rest of the session machinery) so the self-heal can
 * locate the session JSONL without a circular import.
 */
export function createProjectsStore({
  rootDir = projectsRootDir(),
  env = process.env,
  sessionIdForProject = null,
} = {}) {
  fs.mkdirSync(rootDir, { recursive: true });

  function projectDir(id) {
    const dir = path.resolve(rootDir, String(id || ""));
    if (dir !== rootDir && !dir.startsWith(`${rootDir}${path.sep}`)) {
      throw new ProjectNotFoundError(id);
    }
    return dir;
  }

  function requireMeta(id) {
    const dir = projectDir(id);
    const meta = readMeta(dir);
    if (!meta) {
      throw new ProjectNotFoundError(id);
    }
    return { dir, meta };
  }

  /** Self-heal: while the project still carries the placeholder (or empty)
   * name, adopt the latest `ai-title` from the session JSONL. Best-effort. */
  function healName(dir, meta) {
    const name = String(meta.name || "").trim();
    if (name && name !== PLACEHOLDER_PROJECT_NAME) {
      return meta;
    }
    if (typeof sessionIdForProject !== "function") {
      return { ...meta, name: name || PLACEHOLDER_PROJECT_NAME };
    }
    let title = null;
    try {
      const jsonl = sessionJsonlPath(dir, sessionIdForProject(meta.id), env);
      title = parseLatestAiTitle(fs.readFileSync(jsonl, "utf8"));
    } catch {
      title = null;
    }
    if (!title) {
      return { ...meta, name: name || PLACEHOLDER_PROJECT_NAME };
    }
    const healed = { ...meta, name: title, updated_at: nowMs() };
    try {
      writeMeta(dir, healed);
    } catch {
      // read-only fs etc. — serve the healed name without persisting
    }
    return healed;
  }

  function list() {
    let dirents = [];
    try {
      dirents = fs.readdirSync(rootDir, { withFileTypes: true });
    } catch {
      return [];
    }
    const summaries = [];
    for (const entry of dirents) {
      if (!entry.isDirectory()) {
        continue;
      }
      const dir = path.join(rootDir, entry.name);
      const meta = readMeta(dir);
      if (!meta) {
        continue;
      }
      summaries.push(toSummary(dir, healName(dir, meta)));
    }
    summaries.sort((a, b) => b.updatedAt - a.updatedAt || a.id.localeCompare(b.id));
    return summaries;
  }

  function create(name) {
    const id = crypto.randomUUID();
    const dir = projectDir(id);
    const at = nowMs();
    const meta = {
      id,
      name: String(name || "").trim() || PLACEHOLDER_PROJECT_NAME,
      created_at: at,
      updated_at: at,
    };
    writeMeta(dir, meta);
    return toSummary(dir, meta);
  }

  function open(id) {
    const { dir } = requireMeta(id);
    return { workspaceRoot: dir };
  }

  function rename(id, name) {
    const { dir, meta } = requireMeta(id);
    const trimmed = String(name || "").trim();
    if (!trimmed) {
      const err = new Error("project name must not be empty");
      err.code = "INVALID_ARGUMENT";
      err.statusCode = 400;
      throw err;
    }
    const next = { ...meta, name: trimmed, updated_at: nowMs() };
    writeMeta(dir, next);
    return toSummary(dir, next);
  }

  function remove(id) {
    const { dir } = requireMeta(id);
    fs.rmSync(dir, { recursive: true, force: true });
  }

  function touch(id) {
    try {
      const { dir, meta } = requireMeta(id);
      writeMeta(dir, { ...meta, updated_at: nowMs() });
    } catch {
      // best-effort
    }
  }

  function exists(id) {
    try {
      requireMeta(id);
      return true;
    } catch {
      return false;
    }
  }

  function get(id) {
    const { dir, meta } = requireMeta(id);
    return toSummary(dir, healName(dir, meta));
  }

  return {
    rootDir,
    projectDir,
    list,
    create,
    open,
    rename,
    remove,
    touch,
    exists,
    get,
  };
}
