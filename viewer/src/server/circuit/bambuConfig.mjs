/** One-time, machine-local Bambu LAN monitor configuration. No access code is saved here. */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

import { circuitHome } from "./projects.mjs";
import { readBambuStatus } from "./bambuStatus.mjs";

const KEYCHAIN_SERVICE = "autonomous-circuit-bambu-lan";

export function bambuConfigFile(env = process.env) {
  return path.join(circuitHome(env), "bambu-printers.json");
}

function readConfig(file) {
  if (!fs.existsSync(file)) return null;
  const info = fs.lstatSync(file);
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 16_384) throw new Error("invalid_config");
  const config = JSON.parse(fs.readFileSync(file, "utf8"));
  if (!config || config.schema !== 1 || !Array.isArray(config.printers) || config.printers.length !== 1) throw new Error("invalid_config");
  const printer = config.printers[0];
  if (!printer || Object.keys(printer).sort().join("|") !== "certSha256|host|name|serial" ||
      typeof printer.name !== "string" || !printer.name.trim() || printer.name.length > 80 ||
      typeof printer.host !== "string" || typeof printer.serial !== "string" ||
      typeof printer.certSha256 !== "string") throw new Error("invalid_config");
  return printer;
}

/** The secret is read through Keychain; it never appears in argv, env, logs or the project. */
export function keychainAccessCode(serial, { spawn = spawnSync } = {}) {
  if (process.platform !== "darwin" || !/^[A-Za-z0-9_-]{6,64}$/.test(serial)) return null;
  const result = spawn("/usr/bin/security", ["find-generic-password", "-a", serial, "-s", KEYCHAIN_SERVICE, "-w"], {
    encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 3000, maxBuffer: 4096,
  });
  if (result.error || result.status !== 0) return null;
  const code = String(result.stdout || "").trim();
  return code && code.length <= 64 ? code : null;
}

/** Passive observation only; an absent/broken setup leaves the print team usable. */
export async function readConfiguredBambuStatus({
  env = process.env, lookupSecret = keychainAccessCode, readStatus = readBambuStatus,
} = {}) {
  let printer;
  try { printer = readConfig(bambuConfigFile(env)); }
  catch { return { account: { state: "unavailable", reason: "invalid_config" }, jobs: [] }; }
  if (!printer) return { account: { state: "not_connected", reason: "not_configured" }, jobs: [] };
  let accessCode;
  try { accessCode = lookupSecret(printer.serial); }
  catch { accessCode = null; }
  if (!accessCode) return { account: { state: "unavailable", reason: "keychain_access_code_missing", experimental: true }, jobs: [] };
  try {
    const observed = await readStatus({ ...printer, accessCode, timeoutMs: 5000 });
    const status = observed?.status;
    const active = status && status.state && status.state !== "idle";
    const jobs = active ? [{
      jobId: status.taskId || `${printer.serial}:current`,
      status: status.state,
      jobName: status.jobName || null,
      progressPct: status.progressPercent ?? null,
      remainingMinutes: status.remainingMinutes ?? null,
      currentLayer: status.currentLayer ?? null,
      totalLayers: status.totalLayers ?? null,
      observedAt: observed.observedAt,
      printerName: printer.name,
      matchedParts: [],
      source: "printer_lan",
    }] : [];
    return { account: { state: "connected", reason: observed.reason === "no_status" ? "no_status" : null, experimental: true }, jobs };
  } catch (error) {
    return { account: { state: "unavailable", reason: String(error?.code || "connection_failed"), experimental: true }, jobs: [] };
  }
}

let cachedObservation = null;
/** Share one short-lived LAN connection across simultaneous Case tab readers. */
export async function readConfiguredBambuStatusCached({ env = process.env } = {}) {
  const file = bambuConfigFile(env);
  let version = "missing";
  try { const stat = fs.statSync(file); version = `${stat.mtimeMs}:${stat.size}`; } catch { /* absent */ }
  const key = `${file}:${version}`;
  if (cachedObservation?.key === key) {
    if (cachedObservation.promise) return cachedObservation.promise;
    if (Date.now() < cachedObservation.until) return cachedObservation.value;
  }
  const promise = readConfiguredBambuStatus({ env });
  cachedObservation = { key, promise, until: 0, value: null };
  try {
    const value = await promise;
    if (cachedObservation?.promise === promise) cachedObservation = { key, promise: null, until: Date.now() + 8_000, value };
    return value;
  } catch (error) {
    if (cachedObservation?.promise === promise) cachedObservation = null;
    throw error;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 3 || process.argv[2] !== "--json") {
    process.stderr.write("usage: node bambuConfig.mjs --json\n");
    process.exitCode = 2;
  } else {
    const observation = await readConfiguredBambuStatus();
    process.stdout.write(`${JSON.stringify(observation)}\n`);
  }
}
