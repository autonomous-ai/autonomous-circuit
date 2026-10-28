import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { readPrintEvents, readPrintStatus } from "./printStatus.mjs";

const sha = value => crypto.createHash("sha256").update(value).digest("hex");
function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value).replace(/[\u0080-\uffff]/g,
    char => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map(key => `${canonical(key)}:${canonical(value[key])}`).join(",")}}`;
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "print-status-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "case/out"), { recursive: true });
  fs.mkdirSync(path.join(root, "case/reports"));
  fs.mkdirSync(path.join(root, "case/print"));
  fs.mkdirSync(path.join(root, "print"));
  const write = (relative, value) => fs.writeFileSync(path.join(root, relative),
    typeof value === "string" || Buffer.isBuffer(value) ? value : JSON.stringify(value));
  const fileSha = relative => sha(fs.readFileSync(path.join(root, relative)));
  write("case/out/frame.stl", "solid frame\nendsolid frame\n");
  write("case/out/frame.step", "step frame");
  const project = Buffer.from([0x50, 0x4b, 0x03, 0x04, 1, 2, 3]);
  write("case/print/production.3mf", project);
  const manifest = { revision: "A1-V0", verification: { nativePCBCheckerApplies: false,
    report: "../reports/mechanical.json" },
    parts: [{ name: "frame", revision: "r1", role: "bracket", stl: "frame.stl",
      step: "frame.step", printable: true, quantity: 1 }],
    printProject: { file: "../print/production.3mf", report: "../print/production-check.json" } };
  const projectReport = { passed: true, errors: [], file: "case/print/production.3mf",
    sha256: sha(project), meshes: [{ name: "frame.stl", canonicalSTLSha256: fileSha("case/out/frame.stl"),
      printRotateDeg: [0, 0, 0] }], plates: [{ index: 2, name: "MỚI — CẦN IN", parts: ["frame.stl"] }] };
  write("case/print/production-check.json", projectReport);
  const refresh = () => {
    write("case/out/parts.json", manifest);
    const mechanical = { passed: true, scope: "handwired assembly", collisions: [],
      partShas: { "frame.stl": fileSha("case/out/frame.stl"), "frame.step": fileSha("case/out/frame.step") } };
    write("case/reports/mechanical.json", mechanical);
    write("case/reports/print-check.json", { passed: true, errors: [], stem: "main",
      meshValidation: "surface-v1", validationScope: "handwired-print", intentSha: null,
      verificationReport: "case/reports/mechanical.json",
      verificationSha: fileSha("case/reports/mechanical.json"),
      partShas: Object.fromEntries(["parts.json", "frame.stl", "frame.step"].map(name =>
        [name, fileSha(`case/out/${name}`)])) });
  };
  refresh();
  const contractSha256 = sha("contract");
  let previous = contractSha256, sequence = 0;
  const add = (kind, status, overrides = {}) => {
    const eventId = sha(`event-${++sequence}`);
    const unsigned = { schema: 1, sequence, previousSha256: previous, eventId,
      at: `2026-10-28T00:00:${String(sequence).padStart(2, "0")}Z`, kind, status,
      contractSha256, releaseNoteSha256: eventId,
      case: { revision: manifest.revision, manifestSha256: fileSha("case/out/parts.json"),
        printProjectSha256: sha(project) }, plateIndex: 2,
      parts: [{ name: "frame", revision: "r1", sha256STL: fileSha("case/out/frame.stl"),
        quantity: 1, instanceIds: kind === "user_confirmation" ? ["physical-frame-1"] : [] }], printerJobId: null,
      evidence: { kind: "user_message", sha256: sha("message"), summary: "Mày xác nhận" },
      summary: "Mày xác nhận", ...overrides };
    const event = { ...unsigned, sha256: sha(`${canonical(unsigned)}\n`) };
    fs.appendFileSync(path.join(root, "print/events.jsonl"), `${canonical(event)}\n`);
    previous = event.sha256;
    return event;
  };
  return { root, manifest, refresh, add, fileSha, project };
}

test("verified prepared plates are available without treating a slicer export as a print", t => {
  const f = fixture(t);
  const result = readPrintStatus(f.root);
  assert.equal(result.ready, true);
  assert.equal(result.source.verified, true);
  assert.equal(result.parts[0].confirmedPrintedQuantity, 0);
  assert.equal(result.plates[0].status, "needs_print");
  assert.equal(result.account.state, "not_connected");
});

test("printer completion never counts; exact user confirmation changes physical inventory", t => {
  const f = fixture(t);
  f.add("proposal", "proposed");
  f.add("printer_observation", "completed", { printerJobId: "job-1" });
  assert.equal(readPrintStatus(f.root).parts[0].confirmedPrintedQuantity, 0);
  assert.deepEqual(readPrintStatus(f.root).jobs[0].matchedParts, ["frame"]);
  f.add("user_confirmation", "printed");
  assert.equal(readPrintStatus(f.root).parts[0].confirmedPrintedQuantity, 1);
  f.add("user_confirmation", "fit");
  assert.equal(readPrintStatus(f.root).parts[0].fitStatus, "fits");
  f.add("user_confirmation", "discarded");
  assert.equal(readPrintStatus(f.root).parts[0].confirmedPrintedQuantity, 0);
  assert.equal(readPrintStatus(f.root).plates[0].status, "needs_print");
});

test("old revision print survives identical STL, but fit and job association do not", t => {
  const f = fixture(t);
  f.add("user_confirmation", "printed");
  f.add("user_confirmation", "fit");
  f.add("printer_observation", "completed", { printerJobId: "old-job" });
  f.manifest.revision = "A1-V1";
  f.refresh();
  const result = readPrintStatus(f.root);
  assert.equal(result.ready, true);
  assert.equal(result.parts[0].confirmedPrintedQuantity, 1);
  assert.equal(result.parts[0].fitStatus, "unknown");
  assert.deepEqual(result.jobs[0].matchedParts, []);
  assert.equal(readPrintEvents(f.root).length, 3, "history remains available for case repair");
});

test("stale case and corrupt or linked journal never claim trusted inventory", t => {
  const f = fixture(t);
  f.add("user_confirmation", "printed");
  fs.appendFileSync(path.join(f.root, "case/out/frame.stl"), "changed");
  assert.equal(readPrintStatus(f.root).ready, false);
  assert.deepEqual(readPrintStatus(f.root).parts, []);
  fs.writeFileSync(path.join(f.root, "case/out/frame.stl"), "solid frame\nendsolid frame\n");
  fs.appendFileSync(path.join(f.root, "print/events.jsonl"), "{}\n");
  const corrupt = readPrintStatus(f.root);
  assert.equal(corrupt.inventoryTrusted, false);
  assert.equal(corrupt.ready, false);
  assert.equal(corrupt.parts[0].confirmedPrintedQuantity, 0);
  fs.unlinkSync(path.join(f.root, "print/events.jsonl"));
  fs.symlinkSync(path.join(f.root, "case/out/parts.json"), path.join(f.root, "print/events.jsonl"));
  assert.equal(readPrintStatus(f.root).inventoryTrusted, false);
});
