/** Read-only view of the case print project and lead-owned physical print journal. */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { casePrintStatus } from "./catalog.mjs";
import { preparedPrintProject } from "./caseExport.mjs";

const MAX_LEDGER_BYTES = 8 * 1024 * 1024;
const MAX_EVENT_BYTES = 32 * 1024;
const MAX_EVENTS = 10000;
const MAX_MANIFEST_BYTES = 2 * 1024 * 1024;
const HEX = /^[a-f0-9]{64}$/;
const EVENT_FIELDS = ["schema", "sequence", "previousSha256", "sha256", "eventId", "at", "kind", "status", "contractSha256", "releaseNoteSha256", "case", "plateIndex", "parts", "printerJobId", "evidence", "summary"];
const STATUSES = {
  proposal: new Set(["proposed", "unavailable"]),
  printer_observation: new Set(["queued", "printing", "completed", "failed", "unknown"]),
  user_confirmation: new Set(["printed", "fit", "failed", "discarded"]),
};

const sha = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const isSha = value => typeof value === "string" && HEX.test(value);
const boundedText = (value, max = 16000) => typeof value === "string" && value.length <= max && !value.includes("\0");

// Match parallel_contract.encoded(): sorted keys, Python's ensure_ascii=True, newline.
function canonical(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value).replace(/[\u0080-\uffff]/g, char => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`);
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map(key => `${canonical(key)}:${canonical(value[key])}`).join(",")}}`;
}

function regularFile(root, relative, maximum) {
  const base = fs.realpathSync(root);
  let current = base;
  for (const component of relative.split("/")) {
    if (!component || component === "." || component === "..") throw new Error("Invalid print file path");
    current = path.join(current, component);
    const info = fs.lstatSync(current);
    if (info.isSymbolicLink()) throw new Error("Print file path is linked");
  }
  const info = fs.statSync(current);
  if (!info.isFile() || info.nlink !== 1 || info.size > maximum) throw new Error("Invalid or oversized print file");
  if (!current.startsWith(base + path.sep)) throw new Error("Print file escaped project");
  return current;
}

function validEvent(event, sequence, previous) {
  if (!event || typeof event !== "object" || Array.isArray(event) ||
      Object.keys(event).sort().join("|") !== EVENT_FIELDS.slice().sort().join("|") ||
      event.schema !== 1 || event.sequence !== sequence || !isSha(event.sha256) ||
      !isSha(event.previousSha256) || !isSha(event.contractSha256) ||
      !isSha(event.releaseNoteSha256) || !isSha(event.eventId) ||
      event.previousSha256 !== (previous || event.contractSha256) ||
      !boundedText(event.at, 80) || !Number.isFinite(Date.parse(event.at)) ||
      !STATUSES[event.kind]?.has(event.status) || !boundedText(event.summary) ||
      !Number.isInteger(event.plateIndex) && event.plateIndex !== null ||
      event.plateIndex !== null && event.plateIndex < 1 ||
      !Array.isArray(event.parts) || event.parts.length > 128 ||
      event.printerJobId !== null && !boundedText(event.printerJobId, 256)) return false;
  if (event.case !== null && (!event.case || typeof event.case !== "object" ||
      Object.keys(event.case).sort().join("|") !== "manifestSha256|printProjectSha256|revision" ||
      !boundedText(event.case.revision, 256) || !isSha(event.case.manifestSha256) ||
      !isSha(event.case.printProjectSha256))) return false;
  if (!event.evidence || typeof event.evidence !== "object" ||
      Object.keys(event.evidence).sort().join("|") !== "kind|sha256|summary" ||
      !boundedText(event.evidence.kind, 80) || !isSha(event.evidence.sha256) ||
      !boundedText(event.evidence.summary, 16000)) return false;
  for (const part of event.parts) {
    if (!part || typeof part !== "object" || Array.isArray(part) ||
        Object.keys(part).sort().join("|") !== "instanceIds|name|quantity|revision|sha256STL" ||
        !boundedText(part.name, 256) || !boundedText(part.revision, 256) ||
        !isSha(part.sha256STL) || !Number.isInteger(part.quantity) || part.quantity < 1 ||
        part.quantity > 1000 || !Array.isArray(part.instanceIds) ||
        part.instanceIds.length !== (event.kind === "user_confirmation" ? part.quantity : 0) ||
        new Set(part.instanceIds).size !== part.instanceIds.length ||
        part.instanceIds.some(id => !boundedText(id, 256) || !id)) return false;
  }
  const { sha256, ...unsigned } = event;
  return sha(`${canonical(unsigned)}\n`) === sha256;
}

/** Fail closed on any gap, corruption, symlink or duplicate event ID. */
export function readPrintEvents(root) {
  const file = path.join(root, "print/events.jsonl");
  const directory = path.join(root, "print");
  try {
    const info = fs.lstatSync(directory);
    if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("Invalid print journal directory");
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
  try { fs.lstatSync(file); }
  catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
  const bytes = fs.readFileSync(regularFile(root, "print/events.jsonl", MAX_LEDGER_BYTES));
  if (bytes.length && bytes[bytes.length - 1] !== 10) throw new Error("Incomplete print journal event");
  const lines = bytes.length ? bytes.toString("utf8").slice(0, -1).split("\n") : [];
  if (lines.some(line => !line)) throw new Error("Print journal contains an empty event");
  if (lines.length > MAX_EVENTS) throw new Error("Print journal exceeds event limit");
  const events = [], seen = new Set();
  let previous = null, contract = null;
  for (const line of lines) {
    if (Buffer.byteLength(line) > MAX_EVENT_BYTES) throw new Error("Print journal event is too large");
    const event = JSON.parse(line);
    if (!validEvent(event, events.length + 1, previous) ||
        contract !== null && event.contractSha256 !== contract || seen.has(event.eventId) ||
        line !== canonical(event)) throw new Error("Print journal integrity check failed");
    previous = event.sha256;
    contract = event.contractSha256;
    seen.add(event.eventId);
    events.push(event);
  }
  return events;
}

function checkedCase(root, stem) {
  const readiness = casePrintStatus(root, stem);
  if (readiness.state !== "passed") return { reason: readiness.reason || "Case print check has not passed" };
  let manifest, report, bytes;
  try {
    const manifestFile = regularFile(root, "case/out/parts.json", MAX_MANIFEST_BYTES);
    const manifestBytes = fs.readFileSync(manifestFile);
    manifest = JSON.parse(manifestBytes);
    if (!manifest.printProject) return { reason: "No verified native print plates yet" };
    bytes = preparedPrintProject(root, manifest);
    const reportFile = path.relative(root, path.resolve(root, "case/out", manifest.printProject.report));
    report = JSON.parse(fs.readFileSync(regularFile(root, reportFile.split(path.sep).join("/"), MAX_MANIFEST_BYTES)));
    const parts = manifest.parts.filter(part => part.printable !== false &&
      !/DO_NOT_PRINT/i.test(String(part.name || "")) && part.role !== "coupon");
    const plateByName = new Map(report.plates.flatMap(plate => plate.parts.map(name => [name, plate])));
    const current = parts.map(part => {
      const plate = plateByName.get(`${part.name}.stl`);
      const stl = regularFile(root, `case/out/${part.stl}`, 64 * 1024 * 1024);
      return { name: part.name, role: part.role || "other", revision: String(part.revision || manifest.revision || "unknown"),
        stlSha256: sha(fs.readFileSync(stl)), plateIndex: plate.index,
        plateName: typeof plate.name === "string" ? plate.name : null,
        requiredQuantity: Number.isInteger(part.quantity) && part.quantity > 0 ? part.quantity : 1 };
    });
    return { source: { caseRevision: String(manifest.revision || "unknown"),
      manifestSha256: sha(manifestBytes), projectSha256: sha(bytes), file: report.file,
      verified: true }, parts: current, plates: report.plates.map(plate => ({
        index: plate.index, name: typeof plate.name === "string" ? plate.name : null,
        parts: plate.parts.map(name => name.replace(/\.stl$/, "")) })) };
  } catch (error) {
    return { reason: `Print project is not verified: ${error.message}` };
  }
}

/** No account login or printer command is reachable through this endpoint. */
export function readPrintStatus(root, stem = "main", { boardReady = true } = {}) {
  if (!/^[A-Za-z0-9_-][A-Za-z0-9._-]*$/.test(stem)) throw new Error("Invalid board stem");
  const checked = checkedCase(root, stem);
  let events = [], ledgerError = null;
  try { events = readPrintEvents(root); }
  catch (error) { ledgerError = error.message; }
  const ready = Boolean(checked.source && boardReady && !ledgerError);
  const source = checked.source || null;
  const parts = (checked.parts || []).map(part => ({ ...part,
    confirmedPrintedQuantity: 0, fitStatus: "unknown" }));
  const partByName = new Map(parts.map(part => [part.name, part]));
  const printInstances = new Map();
  const jobsById = new Map();
  if (!ledgerError) for (const event of events) {
    if (event.kind === "printer_observation") {
      const associated = source && event.case && event.plateIndex &&
        event.case.printProjectSha256 === source.projectSha256 &&
        event.case.manifestSha256 === source.manifestSha256 &&
        (checked.plates || []).some(plate => plate.index === event.plateIndex &&
          event.parts.length === plate.parts.length &&
          event.parts.every(part => plate.parts.includes(part.name) &&
            partByName.get(part.name)?.stlSha256 === part.sha256STL));
      jobsById.set(event.printerJobId || event.eventId, { jobId: event.printerJobId,
        status: event.status, progressPct: null,
        observedAt: event.at, printerName: null, matchedParts: event.parts.map(part => part.name),
        source: "printer_observation", summary: event.summary,
        ...(associated ? {} : { matchedParts: [] }) });
      continue;
    }
    if (event.kind !== "user_confirmation") continue;
    for (const entry of event.parts) {
      const current = partByName.get(entry.name);
      if (!current || !event.case || current.stlSha256 !== entry.sha256STL) continue;
      const sameAssembly = source && event.case.revision === source.caseRevision &&
        event.case.manifestSha256 === source.manifestSha256 &&
        event.case.printProjectSha256 === source.projectSha256 &&
        entry.revision === current.revision;
      let applied = false;
      for (const id of entry.instanceIds) {
        const present = printInstances.get(id);
        if (event.status === "printed" && !present) {
          printInstances.set(id, { name: current.name, state: "printed" });
          applied = true;
        }
        else if (event.status === "fit" && sameAssembly && present?.name === current.name) {
          printInstances.set(id, { name: current.name, state: "fit" });
          applied = true;
        } else if ((event.status === "failed" || event.status === "discarded") &&
                   present?.name === current.name) {
          printInstances.delete(id);
          applied = true;
        }
      }
      if (applied && event.status === "printed") current.fitStatus = "unknown";
      if (applied && event.status === "fit" && sameAssembly) current.fitStatus = "fits";
      else if (applied && sameAssembly && (event.status === "failed" || event.status === "discarded")) current.fitStatus = event.status;
    }
  }
  for (const part of parts) {
    part.confirmedPrintedQuantity = [...printInstances.values()]
      .filter(instance => instance.name === part.name).length;
  }
  const plates = (checked.plates || []).map(plate => {
    const members = plate.parts.map(name => partByName.get(name)).filter(Boolean);
    // Every part needs its own quantity; extras of one part never cover another.
    const done = members.every(part => part.confirmedPrintedQuantity >= part.requiredQuantity);
    const started = members.some(part => part.confirmedPrintedQuantity > 0);
    return { ...plate, status: !members.length ? "unknown" : done ? "printed" :
      started ? "partly_printed" : "needs_print" };
  });
  return { ready, reason: ready ? null : ledgerError || checked.reason || "Native board is stale",
    source, parts, plates, jobs: [...jobsById.values()].slice(-50),
    account: { state: "not_connected", reason: "not_configured" },
    inventoryTrusted: ledgerError === null, ledgerError };
}
