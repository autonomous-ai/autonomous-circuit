// Circuit catalog — Node scanner over a project workspace, per contract §2:
//
//   Kinds: tsx | json | svg | png | zip | csv | md.
//   Visibility: `.json` hidden (the sidecar is surfaced via the board entry's
//   `artifact.metadataUrl`); `_review/` and `_fab/` members hidden, grouped
//   under the board entry's `artifact`; `.tsx` names starting `_` hidden;
//   `blocks/` and `.circuit/` skipped (projects skip-list). Board entries
//   (`boards/<stem>.tsx`) carry `artifact: {schematicUrl, pcbUrl,
//   pcbBottomUrl?, metadataUrl, circuitJsonUrl, gerbersUrl?, bomUrl?,
//   cplUrl?, orderUrl?, glbUrl?, firmware?}` — members present only when the
//   file exists on disk. EVERY media URL carries `?v=<mtime_nanos>-<size>`.
//   `firmware/` — the code the agent writes for the board once it checks out —
//   is grouped the same way: hidden as entries, surfaced as
//   `artifact.firmware = {readmeUrl?, flashUrl?, files: [{file, url, bytes}]}`.
//   `case/` — the printable enclosure the agent models after the firmware — is
//   grouped the same way as `artifact.case` (scanCase below).
//
// The service watches the activated project dirs (fs.watch recursive — no
// chokidar dependency in this package), debounces 150ms, rescans, bumps the
// revision, and reports catalog_changed for the SSE stream.

import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";

import { skipDirNames } from "./projects.mjs";

export const CATALOG_KINDS = new Set(["tsx", "json", "svg", "png", "zip", "csv", "md", "kicad_pcb"]);

// The firmware tree is source the Firmware tab shows as text, so only text
// lands in the catalog: the languages an MCU toolchain reads plus its build
// files. Objects, ELFs, UF2s and whatever a build directory leaves behind are
// not firmware anyone reads, and a `.pio/` or `build/` tree can hold thousands
// of them. Shared with the asset route (http.mjs), which serves exactly these.
export const FIRMWARE_DIR = "firmware";
export const FIRMWARE_TEXT_EXTENSIONS = new Set([
  ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".ino", ".s", ".asm", ".ld",
  ".py", ".rs", ".js", ".ts", ".lua",
  ".md", ".txt", ".ini", ".toml", ".yaml", ".yml", ".json", ".cfg", ".cmake", ".mk", ".sh", ".csv",
]);
export const FIRMWARE_BARE_NAMES = new Set(["Makefile", "makefile", "GNUmakefile", "Kconfig", "Dockerfile"]);
const FIRMWARE_SKIP_DIRS = new Set([
  "node_modules", ".pio", ".pioenvs", ".piolibdeps", "build", "cmake-build-debug", "cmake-build-release",
  "target", "dist", "out", "__pycache__", ".venv", "venv", ".cache", ".git", ".idea", ".vscode",
]);
const FIRMWARE_MAX_FILES = 200;

/** Is this basename one the firmware tree surfaces (text a person reads)? */
export function isFirmwareSource(name) {
  const base = path.basename(String(name || ""));
  if (!base || base.startsWith(".")) return false;
  return FIRMWARE_BARE_NAMES.has(base) || FIRMWARE_TEXT_EXTENSIONS.has(path.extname(base).toLowerCase());
}

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

/**
 * The firmware tree, `<root>/firmware/`, as the Firmware tab reads it: the
 * README (rendered on top) and every source file with a media URL, sorted by
 * path, capped so a vendored SDK cannot swamp the catalog. `null` when there is
 * nothing to show — the tab then explains when firmware appears.
 */
function scanFirmware(rootDir, url) {
  const firmwareDir = path.join(rootDir, FIRMWARE_DIR);
  let stat;
  try {
    stat = fs.statSync(firmwareDir);
  } catch {
    return null;
  }
  if (!stat.isDirectory()) return null;

  const files = [];
  const stack = [firmwareDir];
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
        if (!FIRMWARE_SKIP_DIRS.has(entry.name) && !entry.name.startsWith(".")) stack.push(full);
        continue;
      }
      if (entry.isFile() && isFirmwareSource(entry.name)) files.push(full);
    }
  }
  if (!files.length) return null;

  files.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  const members = [];
  let readmeUrl;
  for (const file of files.slice(0, FIRMWARE_MAX_FILES)) {
    const rel = path.relative(firmwareDir, file).split(path.sep).join("/");
    let bytes = 0;
    try {
      bytes = fs.statSync(file).size;
    } catch {
      // raced with a delete — listed with its stale token, like every other member
    }
    const member = { file: rel, url: url(file), bytes };
    if (!readmeUrl && /^readme\.md$/i.test(rel)) readmeUrl = member.url;
    members.push(member);
  }
  const firmware = { files: members, truncated: files.length > FIRMWARE_MAX_FILES };
  if (readmeUrl) firmware.readmeUrl = readmeUrl;
  // The recipe the Flash button runs (firmwareFlash.mjs). Its presence is what
  // enables the button; the tab reads it for the confirm line.
  const recipe = path.join(firmwareDir, "flash.json");
  if (fs.existsSync(recipe)) firmware.flashUrl = url(recipe);
  return firmware;
}

export const CASE_DIR = "case";
const CASE_OUT = path.join(CASE_DIR, "out");
const CASE_REPORT = path.join(CASE_DIR, "reports", "case-check.json");
const CASE_MAX_PARTS = 128;

function sha256File(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

/** A path from parts.json, kept inside case/out/ — the manifest is the agent's, not trusted. */
function caseMember(outDir, name) {
  if (typeof name !== "string" || !name) return null;
  const full = path.resolve(outDir, name);
  if (!full.startsWith(outDir + path.sep) || !fs.existsSync(full)) return null;
  return full;
}

/**
 * Has the case been checked against the board it is built for? The same rule
 * as kicadpy.case.status, read without CadQuery: none (no parts.json), stale
 * (never checked, or the board, a part or the manifest changed since), failed,
 * or passed.
 */
export function caseStatus(rootDir, stem) {
  const outDir = path.join(rootDir, CASE_OUT);
  if (!fs.existsSync(path.join(outDir, "parts.json"))) return { state: "none", reason: "no case yet" };
  const report = readJson(path.join(rootDir, CASE_REPORT));
  if (!report) return { state: "stale", reason: "the case has not been checked yet" };
  if (report.meshValidation !== "surface-v1") return { state: "stale", reason: "recheck the case with printable-surface validation" };
  let contract = null;
  try {
    contract = stem ? `${stem}.enclosure.json` : fs.readdirSync(path.join(rootDir, "boards")).filter((n) => n.endsWith(".enclosure.json")).sort()[0] || null;
  } catch {
    contract = null;
  }
  try {
    if (!contract || report.contractSha !== sha256File(path.join(rootDir, "boards", contract))) {
      return { state: "stale", reason: "the board changed since the case was checked" };
    }
    for (const [name, digest] of Object.entries(report.partShas || {})) {
      const file = caseMember(outDir, name);
      if (!file || sha256File(file) !== digest) return { state: "stale", reason: `case/out/${name} changed since the case was checked` };
    }
  } catch {
    return { state: "stale", reason: "the case files could not be read" };
  }
  if (report.passed !== true) {
    const n = Array.isArray(report.errors) ? report.errors.length : 0;
    return { state: "failed", reason: n === 1 ? "1 case error" : `${n} case errors` };
  }
  return { state: "passed", reason: "the case fits the board and opens for every interface" };
}

/** Print readiness for a handwired assembly is independent of the native PCB verdict. */
export function casePrintStatus(rootDir, stem = "main") {
  const out = path.join(rootDir, CASE_OUT), manifest = readJson(path.join(out, "parts.json"));
  if (manifest?.verification?.nativePCBCheckerApplies !== false) return { ...caseStatus(rootDir, stem), scope: "pcb" };
  const stale = reason => ({ state: "stale", reason, scope: "handwired" });
  try {
    const root = fs.realpathSync(rootDir), caseDir = fs.realpathSync(path.join(root, "case"));
    if (!caseDir.startsWith(root + path.sep)) return stale("case files are outside the project");
    if (!fs.realpathSync(out).startsWith(caseDir + path.sep)) return stale("case output is outside case/");
    const receiptFile = fs.realpathSync(path.join(caseDir, "reports/print-check.json"));
    if (!receiptFile.startsWith(caseDir + path.sep)) return stale("print receipt is outside case/");
    const report = readJson(receiptFile);
    if (report?.meshValidation !== "surface-v1" || report.validationScope !== "handwired-print" || report.stem !== stem) return stale("check the handwired case before Print all");
    const mechanicalPath = fs.realpathSync(path.resolve(out, manifest.verification.report));
    if (!mechanicalPath.startsWith(caseDir + path.sep) || path.relative(root, mechanicalPath) !== report.verificationReport || sha256File(mechanicalPath) !== report.verificationSha) return stale("the handwired mechanical report changed");
    const mechanical = readJson(mechanicalPath);
    if (mechanical?.passed !== true || !mechanical.scope || Object.entries(mechanical).some(([key, value]) => (key === "errors" || /collisions$/i.test(key)) && (Array.isArray(value) ? value.length : value))) return { state: "failed", reason: "the handwired mechanical check has errors", scope: "handwired" };
    const hashes = report.partShas || {}, intent = path.join(caseDir, "interfaces.json");
    if ((fs.existsSync(intent) ? sha256File(intent) : null) !== report.intentSha) return stale("the print settings changed");
    const members = new Set(["parts.json"]);
    if (!Array.isArray(manifest.parts) || !manifest.parts.length || manifest.parts.length > CASE_MAX_PARTS) return stale("invalid case parts list");
    for (const part of manifest.parts) for (const key of ["step", "stl"]) {
      const file = caseMember(out, part[key]);
      if (!file || !fs.realpathSync(file).startsWith(fs.realpathSync(out) + path.sep) || mechanical.partShas?.[part[key]] !== sha256File(file)) return stale("the handwired report omits a part or a part changed");
      members.add(part[key]);
    }
    for (const member of members) {
      const file = caseMember(out, member);
      if (!file || hashes[member] !== sha256File(file)) return stale(`case/out/${member} changed since print validation`);
    }
    if (report.passed !== true || report.errors?.length) return { state: "failed", reason: "the printable meshes need fixing", scope: "handwired" };
    return { state: "passed", reason: "Handwired mechanical report and printable surfaces checked. Print the test-fit coupon first; fit has not been tested on hardware.", scope: "handwired" };
  } catch { return stale("the handwired print evidence could not be read"); }
}

/**
 * The case, `<root>/case/`, as the Case tab reads it: every part parts.json
 * lists with its STL and STEP, the board's contract (the frame the parts are
 * modelled in, drawn under them), the checker's report and its status.
 * `null` when there is neither a manifest nor a contract — nothing to show.
 */
function scanCase(rootDir, url, stem) {
  const outDir = path.join(rootDir, CASE_OUT);
  const contract = path.join(rootDir, "boards", `${stem}.enclosure.json`);
  const manifest = readJson(path.join(outDir, "parts.json"));
  const hasContract = fs.existsSync(contract);
  if (!manifest && !hasContract) return null;
  const result = { parts: [], ...caseStatus(rootDir, stem), printStatus: casePrintStatus(rootDir, stem) };
  result.nativePCBCheckerApplies = manifest?.verification?.nativePCBCheckerApplies !== false;
  if (!result.nativePCBCheckerApplies) result.printReportUrl = url(path.join(rootDir, "case/reports/print-check.json"));
  if (hasContract) result.contractUrl = url(contract);
  // Agent-authored overviews are HTML assets, rendered in an isolated frame.
  const overviewMembers = manifest?.overview ? [manifest.overview] : ["../overview.html", "../motion/motion-view.html"];
  for (const member of overviewMembers) {
    if (typeof member !== "string") continue;
    try {
      const root = fs.realpathSync(rootDir), file = fs.realpathSync(path.resolve(outDir, member));
      if (file.startsWith(path.join(root, CASE_DIR) + path.sep) && path.extname(file).toLowerCase() === ".html" && fs.statSync(file).isFile()) {
        result.overviewUrl = url(file); break;
      }
    } catch { /* no overview or unsafe member */ }
  }

  const intent = path.join(rootDir, CASE_DIR, "interfaces.json");
  if (fs.existsSync(intent)) result.intentUrl = url(intent);
  const report = path.join(rootDir, CASE_REPORT);
  if (fs.existsSync(report)) result.reportUrl = url(report);
  for (const part of (Array.isArray(manifest?.parts) ? manifest.parts : []).slice(0, CASE_MAX_PARTS)) {
    const stl = caseMember(outDir, part?.stl);
    if (!stl || path.extname(stl).toLowerCase() !== ".stl") continue;
    const step = caseMember(outDir, part?.step);
    const member = {
      name: String(part.name || path.basename(stl, ".stl")),
      role: String(part.role || "other"),
      stlUrl: url(stl),
      bytes: fs.statSync(stl).size,
      printable: part.printable !== false && !/DO_NOT_PRINT/i.test(String(part.name || "")),
    };
    if (step && /\.(step|stp)$/i.test(step)) member.stepUrl = url(step);
    if (Array.isArray(part.printRotateDeg)) member.printRotateDeg = part.printRotateDeg.slice(0, 3).map(Number);
    if (typeof part.supports === "boolean") member.supports = part.supports;
    result.parts.push(member);
  }
  return result;
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
  // One firmware tree per workspace; every board entry carries it, because
  // the tab that shows it hangs off whichever board is selected.
  const firmware = scanFirmware(rootDir, url);

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
    if (firmware) entry.artifact.firmware = firmware;
    const enclosure = scanCase(rootDir, url, stem);
    if (enclosure && entry.nativeStale && enclosure.state !== "none") {
      enclosure.state = "stale";
      enclosure.reason = "the board sources changed; publish the board and recheck the case";
      if (enclosure.printStatus.scope === "pcb") enclosure.printStatus = { state: "stale", reason: enclosure.reason, scope: "pcb" };
    }
    if (enclosure) entry.artifact.case = enclosure;
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
    if (firmware) artifact.firmware = firmware;
    if (Object.keys(artifact).length) {
      entry.artifact = artifact;
    }
    entries.push(entry);
  }

  // svg / png / zip / csv / md — visible standalone files, hidden inside
  // `_review/` and `_fab/` dirs and under `firmware/` (those surface via the
  // board artifact).
  for (const kind of ["svg", "png", "zip", "csv", "md", "kicad_pcb"]) {
    for (const file of byExt.get(kind) || []) {
      if (inGeneratedDir(file) || relPath(rootDir, file).startsWith(`${FIRMWARE_DIR}/`)) {
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
