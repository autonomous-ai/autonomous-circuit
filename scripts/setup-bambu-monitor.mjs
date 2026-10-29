#!/usr/bin/env node
/** Pair one local Bambu printer for passive status monitoring. Never sends a print command. */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";

import { circuitHome } from "../viewer/src/server/circuit/projects.mjs";
import { discoverBambuFingerprint } from "../viewer/src/server/circuit/bambuStatus.mjs";

if (process.platform !== "darwin" || !stdin.isTTY) {
  process.stderr.write("Run this setup in a macOS terminal.\n");
  process.exit(2);
}

const input = createInterface({ input: stdin, output: stdout });
let host, serial, name, pin, accepted;
try {
  stdout.write("Passive A1 status monitor (experimental Bambu LAN protocol).\n");
  host = (await input.question("Printer LAN IP: ")).trim();
  serial = (await input.question("Printer serial: ")).trim();
  name = (await input.question("Printer name [A1]: ")).trim() || "A1";
  if (!/^[A-Za-z0-9_-]{6,64}$/.test(serial) || name.length > 80) throw new Error("Invalid printer serial or name");
  pin = await discoverBambuFingerprint(host);
  stdout.write(`TLS certificate SHA-256 found at ${host}: ${pin}\n`);
  accepted = (await input.question("Confirm this IP belongs to your printer (trust on first use) [yes/no]: ")).trim().toLowerCase();
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
} finally {
  input.close();
}

if (process.exitCode) process.exit(process.exitCode);
if (accepted !== "yes") {
  process.stderr.write("Pairing cancelled.\n");
  process.exit(1);
}

stdout.write("Enter the LAN access code at the terminal prompt from macOS security.\n");
const saved = spawnSync("/usr/bin/security", [
  "add-generic-password", "-U", "-a", serial, "-s", "autonomous-circuit-bambu-lan", "-w",
], { stdio: "inherit" });
if (saved.error || saved.status !== 0) {
  process.stderr.write("Keychain did not save the LAN access code.\n");
  process.exit(1);
}

const home = circuitHome();
fs.mkdirSync(home, { recursive: true, mode: 0o700 });
const target = path.join(home, "bambu-printers.json");
if (fs.existsSync(target) && !fs.lstatSync(target).isFile()) throw new Error("Printer config path is not a file");
const temp = path.join(home, `.bambu-printers-${process.pid}.tmp`);
const data = `${JSON.stringify({ schema: 1, printers: [{ name, host, serial, certSha256: pin }] }, null, 2)}\n`;
try {
  fs.writeFileSync(temp, data, { flag: "wx", mode: 0o600 });
  fs.renameSync(temp, target);
} finally {
  if (fs.existsSync(temp)) fs.unlinkSync(temp);
}
stdout.write("Monitor paired. New KiCad projects can read printer status without pairing again.\n");
