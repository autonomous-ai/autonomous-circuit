// Circuit catalog — Node scanner over a project workspace, per contract §2:
//
//   Kinds: tsx | json | svg | png | zip | csv | md.
//   Visibility: `.json` hidden (the sidecar is surfaced via the board entry's
//   `artifact.metadataUrl`); `_review/` and `_fab/` members hidden, grouped
//   under the board entry's `artifact`; `.tsx` names starting `_` hidden;
//   `blocks/` and `.circuit/` skipped (projects skip-list). Board entries
//   (`boards/<stem>.tsx`) carry `artifact: {schematicUrl, pcbUrl,
//   pcbBottomUrl?, metadataUrl, circuitJsonUrl, gerbersUrl?, bomUrl?,
//   cplUrl?, orderUrl?, glbUrl?}` — members present only when the file
//   exists on disk. EVERY media URL carries `?v=<mtime_nanos>-<size>`.
//
// The service watches the activated project dirs (fs.watch recursive — no
// chokidar dependency in this package), debounces 150ms, rescans, bumps the
// revision, and reports catalog_changed for the SSE stream.

import fs from "node:fs";
import path from "node:path";

import { skipDirNames } from "./projects.mjs";

export const CATALOG_KINDS = new Set(["tsx", "json", "svg", "png", "zip", "csv", "md"]);

const DEBOUNCE_MS = 150;

/** `?v=` cache-bust token for a file: `<mtime_nanos>-<size>`. */
export function versionToken(filePath) {
  const stat = fs.statSync(filePath, { bigint: true });
  return `${stat.mtimeNs}-${stat.size}`;
}

function mediaUrl(projectId, rel, absPath) {
  let token = "0-0";
  try {
    token = versionToken(absPath);
  } catch {
    // race with a delete — serve a stable-but-stale token
  }
  const encodedRel = rel.split("/").map(encodeURIComponent).join("/");
  return `/projects/${encodeURIComponent(projectId)}/${encodedRel}?v=${token}`;
}

function walkFiles(rootDir) {
  const skip = skipDirNames();
  const files = [];
  const stack = [rootDir];
  while (stack.length) {
    const dir = stack.pop();
    let dirents;
    try {
      dirents = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of dirents) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!skip.has(entry.name)) {
          stack.push(full);
        }
        continue;
      }
      if (entry.isFile()) {
        files.push(full);
      }
    }
  }
  return files;
}

function relPath(rootDir, filePath) {
  return path.relative(rootDir, filePath).split(path.sep).join("/");
}

/** Is this file a member of a generated `<stem>_review/` or `<stem>_fab/`
 * dir? Members are hidden as entries — grouped under the board's artifact. */
function inGeneratedDir(absPath) {
  const parent = path.basename(path.dirname(absPath));
  return parent.endsWith("_review") || parent.endsWith("_fab");
}

/**
 * Scan one project workspace into contract-§2 catalog entries. Pure with
 * respect to inputs (reads the filesystem, mutates nothing).
 */
export function scanProjectCatalog({ projectDir, projectId }) {
  let rootDir = path.resolve(projectDir);
  try {
    rootDir = fs.realpathSync(rootDir);
  } catch {
    // scan of a missing dir yields an empty catalog below
  }
  const files = walkFiles(rootDir);
  const byExt = new Map();
  for (const file of files) {
    const ext = path.extname(file).slice(1).toLowerCase();
    if (!CATALOG_KINDS.has(ext)) {
      continue;
    }
    if (!byExt.has(ext)) {
      byExt.set(ext, []);
    }
    byExt.get(ext).push(file);
  }

  const entries = [];
  const url = (abs) => mediaUrl(projectId, relPath(rootDir, abs), abs);

  // tsx — board entries (with the grouped artifact object). Names starting
  // `_` are hidden (helper sources); blocks/ never reaches here (skip-list).
  for (const tsx of byExt.get("tsx") || []) {
    if (path.basename(tsx).startsWith("_")) {
      continue;
    }
    const parent = path.dirname(tsx);
    const stem = path.join(parent, path.basename(tsx, ".tsx"));

    const entry = {
      file: relPath(rootDir, tsx),
      kind: "tsx",
      sourceKind: "tsx",
      url: url(tsx),
    };

    // Contract §2 board-entry artifact members, present only when on disk.
    const members = [
      ["metadataUrl", `${stem}.board.json`],
      ["circuitJsonUrl", `${stem}.circuit.json`],
      ["schematicUrl", path.join(`${stem}_review`, "_schematic.png")],
      ["pcbUrl", path.join(`${stem}_review`, "_pcb.png")],
      ["pcbBottomUrl", path.join(`${stem}_review`, "_pcb_bottom.png")],
      ["gerbersUrl", path.join(`${stem}_fab`, "gerbers.zip")],
      ["bomUrl", path.join(`${stem}_fab`, "bom.csv")],
      ["cplUrl", path.join(`${stem}_fab`, "cpl.csv")],
      ["orderUrl", path.join(`${stem}_fab`, "ORDER.md")],
      ["glbUrl", path.join(`${stem}_fab`, "board.glb")],
    ];
    const artifact = {};
    for (const [key, absPath] of members) {
      if (fs.existsSync(absPath)) {
        artifact[key] = url(absPath);
      }
    }
    if (Object.keys(artifact).length) {
      entry.artifact = artifact;
    }
    entries.push(entry);
  }

  // svg / png / zip / csv / md — visible standalone files, hidden inside
  // `_review/` and `_fab/` dirs (those surface via the board artifact).
  for (const kind of ["svg", "png", "zip", "csv", "md"]) {
    for (const file of byExt.get(kind) || []) {
      if (inGeneratedDir(file)) {
        continue;
      }
      entries.push({
        file: relPath(rootDir, file),
        kind,
        sourceKind: null,
        url: url(file),
      });
    }
  }

  // json — hidden entirely: the sidecar surfaces via artifact.metadataUrl,
  // the IR via artifact.circuitJsonUrl; product.json/parts.json are served
  // by the asset route directly (no catalog entry).

  // Plain code-unit ordering (not locale-aware) so the order is stable across
  // machines and "boards/main.tsx" sorts before "boards/main_review/…".
  entries.sort((a, b) => (a.file < b.file ? -1 : a.file > b.file ? 1 : 0));
  return { entries, rootPath: rootDir };
}

/**
 * Catalog service: cached scans + recursive watchers over activated project
 * dirs. `onCatalogChanged(revision)` fires (debounced 150ms) whenever a
 * watched project's files change; `revision` increments monotonically.
 */
export function createCatalogService({ projectDir, onCatalogChanged = () => {} } = {}) {
  let revision = 0;
  const cache = new Map(); // projectId -> catalog
  const watchers = new Map(); // projectId -> fs.FSWatcher
  const timers = new Map(); // projectId -> debounce timer
  let closed = false;

  function scan(projectId) {
    const catalog = scanProjectCatalog({ projectDir: projectDir(projectId), projectId });
    cache.set(projectId, catalog);
    return catalog;
  }

  function read(projectId) {
    const catalog = cache.get(projectId) || scan(projectId);
    return { ...catalog, revision };
  }

  function refresh(projectId) {
    if (closed) {
      return;
    }
    try {
      scan(projectId);
    } catch {
      cache.delete(projectId);
    }
    revision += 1;
    onCatalogChanged(revision);
  }

  function scheduleRefresh(projectId) {
    if (timers.has(projectId)) {
      clearTimeout(timers.get(projectId));
    }
    const timer = setTimeout(() => {
      timers.delete(projectId);
      refresh(projectId);
    }, DEBOUNCE_MS);
    timer.unref?.();
    timers.set(projectId, timer);
  }

  /** Start watching a project dir (idempotent). */
  function activate(projectId) {
    if (closed || watchers.has(projectId)) {
      return;
    }
    let watcher;
    try {
      watcher = fs.watch(projectDir(projectId), { recursive: true }, () => {
        scheduleRefresh(projectId);
      });
    } catch {
      return; // dir vanished (deleted project) — nothing to watch
    }
    watcher.on("error", () => {
      watchers.delete(projectId);
      try {
        watcher.close();
      } catch {
        // already closed
      }
    });
    watchers.set(projectId, watcher);
  }

  function deactivate(projectId) {
    const watcher = watchers.get(projectId);
    if (watcher) {
      watchers.delete(projectId);
      try {
        watcher.close();
      } catch {
        // already closed
      }
    }
    if (timers.has(projectId)) {
      clearTimeout(timers.get(projectId));
      timers.delete(projectId);
    }
    cache.delete(projectId);
  }

  function close() {
    closed = true;
    for (const projectId of [...watchers.keys()]) {
      deactivate(projectId);
    }
    for (const timer of timers.values()) {
      clearTimeout(timer);
    }
    timers.clear();
  }

  return {
    read,
    activate,
    deactivate,
    refresh,
    close,
    get revision() {
      return revision;
    },
  };
}
