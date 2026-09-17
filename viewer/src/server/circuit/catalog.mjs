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
import crypto from "node:crypto";
import path from "node:path";

import { skipDirNames } from "./projects.mjs";

export const CATALOG_KINDS = new Set(["tsx", "json", "svg", "png", "zip", "csv", "md", "kicad_pcb"]);

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
  return absPath.split(path.sep).some(part => part.endsWith("_review") || part.endsWith("_fab"));
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

  // Native board source with a versioned, checked SVG bundle. Never offer
  // v1 Circuit JSON or a TSX edit path for a native board.
  for (const pcb of byExt.get("kicad_pcb") || []) {
    const rel = relPath(rootDir, pcb);
    if (!/^design\/[^/_][^/]*\.kicad_pcb$/.test(rel)) continue;
    const stem = path.basename(pcb, ".kicad_pcb");
    const entry = { file: rel, kind: "kicad_pcb", sourceKind: "kicad-native", url: url(pcb), artifact: {} };
    const meta = path.join(rootDir, "boards", `${stem}.board.json`);
    try {
      const data = JSON.parse(fs.readFileSync(meta, "utf8"));
      entry.artifact.metadataUrl = url(meta);
      const inputs = Object.entries(data.native?.inputs || {});
      const sourceRoot = path.resolve(rootDir, data.native?.inputRoot || "design");
      if (sourceRoot !== rootDir && sourceRoot !== path.join(rootDir, "design")) throw new Error("invalid native source root");
      const suffixes = new Set([".kicad_pro", ".kicad_pcb", ".kicad_sch", ".kicad_dru", ".kicad_sym", ".kicad_mod", ".step", ".stp", ".wrl"]);
      const names = new Set(["fp-lib-table", "sym-lib-table", "product.json", "parts.json", "manufacturing.json"]);
      // Same rule as kicadpy.project.manifest: inside a workspace only the
      // design directory, engineering/ and the root JSONs are inputs.
      const designDir = path.dirname(String(data.source?.file || ''));
      const isInput = (f) => {
        const rel = path.relative(sourceRoot, f).split(path.sep);
        if (designDir !== '.' && sourceRoot === rootDir && !(rel[0] === designDir || rel[0] === 'engineering' || (rel.length === 1 && names.has(rel[0])))) return false;
        return suffixes.has(path.extname(f)) || names.has(path.basename(f)) || rel[0] === 'engineering';
      };
      const actual = walkFiles(sourceRoot).filter(isInput).map(f => relPath(rootDir, f)).sort();
      const addedParentSpec = sourceRoot !== rootDir && ["product.json", "parts.json", "manufacturing.json"].some(n => fs.existsSync(path.join(rootDir, n)));
      const valid = !addedParentSpec && inputs.length > 0 && JSON.stringify(actual) === JSON.stringify(inputs.map(([name]) => name).sort()) && inputs.every(([name, sha]) => {
        const input = path.resolve(rootDir, name);
        return input.startsWith(rootDir + path.sep) && crypto.createHash("sha256").update(fs.readFileSync(input)).digest("hex") === sha;
      });
      entry.nativeStale = !valid;
      if (valid && data.native?.publication === "complete") {
        const bundle = path.resolve(rootDir, data.native.previewDir);
        if (!bundle.startsWith(path.join(rootDir, "boards", `${stem}_review`) + path.sep)) throw new Error("invalid preview bundle");
        for (const [key, file] of [["pcbUrl", "_pcb.svg"], ["pcbBottomUrl", "_pcb_bottom.svg"], ["schematicUrl", "_schematic.svg"], ["glbUrl", "board.glb"]]) {
          const asset = path.join(bundle, file);
          if (fs.existsSync(asset)) entry.artifact[key] = url(asset);
        }
        const packet = data.native?.manufacturing;
        const packetDir = path.join(bundle,'manufacturing');
        const members = Object.entries(packet?.files || {});
        const intact = members.length > 0 && members.every(([name,sha]) => {
          const file = path.resolve(packetDir,name);
          return file.startsWith(packetDir + path.sep) && fs.existsSync(file) && crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') === sha;
        });
        let reportsPassed = false;
        try {
          const report=JSON.parse(fs.readFileSync(path.join(packetDir,'manufacturing-report.json'),'utf8'));
          const nativeCheck=JSON.parse(fs.readFileSync(path.join(packetDir,'native-check.json'),'utf8'));
          reportsPassed=Object.hasOwn(packet?.files || {},'manufacturing-report.json') && Object.hasOwn(packet?.files || {},'native-check.json') && report.prototypeReady===true && report.checkedRevision===data.native.checkedRevision && Array.isArray(report.findings) && !report.findings.some(f=>f.severity==='error') && nativeCheck.passed===true && nativeCheck.revision===data.native.checkedRevision && nativeCheck.findings?.length===0;
        } catch { /* unreadable reports: not verified */ }
        // The reviewer's journal is shown, not required (owner's rule 2026-09-17):
        // an intact packet with zero error findings for the current source is ready.
        try {
          const journal=JSON.parse(fs.readFileSync(path.join(rootDir,'.circuit/native-review.json'),'utf8'));
          entry.nativeReviewState = String(journal.state || '');
        } catch { entry.nativeReviewState = ''; }
        entry.nativeManufacturingVerified = reportsPassed && intact && packet.prototypeReady === true && packet.checkedRevision === data.native.checkedRevision && data.native.checksPassed === true;
        if (intact) for (const [key,name] of [['gerbersUrl','gerbers.zip'],['bomUrl','bom.csv'],['cplUrl','cpl.csv'],['orderUrl','ORDER.md'],['kicadProjectUrl','kicad-project.zip'],['manufacturingReportUrl','manufacturing-report.json']]) {
          const file = path.join(packetDir,name);
          if (Object.hasOwn(packet.files,name) && fs.existsSync(file)) entry.artifact[key] = url(file);
        }
      }
    } catch { entry.nativeStale = true; }
    entries.push(entry);
  }

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
      // The enclosure brief — outline, holes, connector edges — is what Vibe
      // reads to model the printed body, so it belongs beside the packet.
      ["enclosureUrl", path.join(`${stem}_fab`, "enclosure.json")],
      // The KiCad project is the only packet member a person can open in a
      // real EDA tool, so it is offered even on a board that is not fab-ready.
      ["kicadProjectUrl", path.join(`${stem}_fab`, "kicad-project.zip")],
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
  for (const kind of ["svg", "png", "zip", "csv", "md", "kicad_pcb"]) {
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
