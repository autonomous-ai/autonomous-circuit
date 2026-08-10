// Video catalog — Node scanner over a project workspace, per contract §2:
//
//   Kinds: mp4 | png | srt | py | json.
//   Visibility: `.json` hidden (surfaced via the episode entry's
//   `artifact.metadataUrl`); `.png` hidden unless its parent dir ends
//   `_review`; shot `.mp4` hidden if its parent dir ends `_shots` (grouped
//   under the episode entry's `artifact.shots[]`); `.py` hidden if its name
//   starts `_`. Episode entries carry
//   `artifact: {srtUrl?, metadataUrl?, posterUrl?, shots?: [{id, file, url}]}`.
//   EVERY media URL carries `?v=<mtime_nanos>-<size>` (`<video>` caches
//   harder than STL).
//
// The service watches the activated project dirs (fs.watch recursive — no
// chokidar dependency in this package), debounces 150ms, rescans, bumps the
// revision, and reports catalog_changed for the SSE stream.

import fs from "node:fs";
import path from "node:path";

import { skipDirNames } from "./projects.mjs";

export const CATALOG_KINDS = new Set(["mp4", "png", "srt", "py", "json"]);

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

/** Shot id from a `shot_<id>.mp4` filename (falls back to the bare stem). */
export function shotIdFromFilename(name) {
  const stem = String(name).replace(/\.mp4$/i, "");
  return stem.startsWith("shot_") ? stem.slice("shot_".length) : stem;
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
  const fileSet = new Set();
  for (const file of files) {
    const ext = path.extname(file).slice(1).toLowerCase();
    if (!CATALOG_KINDS.has(ext)) {
      continue;
    }
    if (!byExt.has(ext)) {
      byExt.set(ext, []);
    }
    byExt.get(ext).push(file);
    fileSet.add(file);
  }

  const entries = [];
  const url = (abs) => mediaUrl(projectId, relPath(rootDir, abs), abs);

  // mp4 — episode entries (with grouped artifact) unless inside a *_shots dir.
  for (const mp4 of byExt.get("mp4") || []) {
    const parent = path.dirname(mp4);
    if (path.basename(parent).endsWith("_shots")) {
      continue; // grouped under its episode's artifact.shots[]
    }
    const stemBase = path.basename(mp4, ".mp4");
    const stem = path.join(parent, stemBase);
    const sidecar = `${stem}.episode.json`;
    const srt = `${stem}.srt`;
    const poster = path.join(`${stem}_review`, "_poster.png");
    const shotsDir = `${stem}_shots`;

    const entry = {
      file: relPath(rootDir, mp4),
      kind: "mp4",
      sourceKind: fileSet.has(`${stem}.py`) || fs.existsSync(sidecar) ? "python" : null,
      url: url(mp4),
    };

    const artifact = {};
    if (fs.existsSync(sidecar)) {
      artifact.metadataUrl = url(sidecar);
    }
    if (fs.existsSync(srt)) {
      artifact.srtUrl = url(srt);
    }
    if (fs.existsSync(poster)) {
      artifact.posterUrl = url(poster);
    }
    let shotFiles = [];
    try {
      shotFiles = fs
        .readdirSync(shotsDir, { withFileTypes: true })
        .filter((d) => d.isFile() && d.name.toLowerCase().endsWith(".mp4"))
        .map((d) => path.join(shotsDir, d.name))
        .sort();
    } catch {
      shotFiles = [];
    }
    if (shotFiles.length) {
      artifact.shots = shotFiles.map((shot) => ({
        id: shotIdFromFilename(path.basename(shot)),
        file: relPath(rootDir, shot),
        url: url(shot),
      }));
    }
    if (Object.keys(artifact).length) {
      entry.artifact = artifact;
    }
    entries.push(entry);
  }

  // png — visible only inside *_review dirs.
  for (const png of byExt.get("png") || []) {
    if (!path.basename(path.dirname(png)).endsWith("_review")) {
      continue;
    }
    entries.push({
      file: relPath(rootDir, png),
      kind: "png",
      sourceKind: null,
      url: url(png),
    });
  }

  // srt — visible.
  for (const srt of byExt.get("srt") || []) {
    entries.push({
      file: relPath(rootDir, srt),
      kind: "srt",
      sourceKind: null,
      url: url(srt),
    });
  }

  // py — hidden when the name starts with `_`.
  for (const py of byExt.get("py") || []) {
    if (path.basename(py).startsWith("_")) {
      continue;
    }
    entries.push({
      file: relPath(rootDir, py),
      kind: "py",
      sourceKind: "python",
      url: url(py),
    });
  }

  // json — hidden (surfaced via artifact.metadataUrl), with one exception
  // (contract CHANGES 2026-08-09): a root-level series.json — the series
  // bible the cast panel renders — is surfaced as an entry.
  for (const json of byExt.get("json") || []) {
    if (relPath(rootDir, json) !== "series.json") {
      continue;
    }
    entries.push({
      file: "series.json",
      kind: "json",
      sourceKind: null,
      url: url(json),
    });
  }

  // Plain code-unit ordering (not locale-aware) so the order is stable across
  // machines and "ep001.mp4" sorts before "ep001_review/…".
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
