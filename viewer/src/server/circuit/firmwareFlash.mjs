// Flashing the firmware — the pane's Flash button, server side.
//
// Three rules, in the order they were learned:
//
//   1. Only a person flashes. Nothing here runs unless the button was clicked,
//      and the click is two steps: `detect` shows exactly which USB device
//      would be written, `flash` writes it and only it. The agent never flashes
//      (harness/kicad/AGENTS.md) — a board that is plugged in may be running
//      something else entirely, and firmware for THIS board on THAT board is a
//      brick.
//   2. The USB identity has to match the recipe. `firmware/flash.json` names
//      the MCU family and the USB vendor ids it enumerates with; a port whose
//      vid is not on that list is "others", never a candidate, and two
//      candidates is a refusal — the app does not guess between two boards.
//   3. Never a wildcard port. `flash` takes the address `detect` returned and
//      re-detects before writing; if the device changed in between, it refuses.
//
// The recipe is written by the agent next to the firmware (AGENTS.md "Firmware"):
//
//   { "family": "esp32", "tool": "arduino-cli",
//     "fqbn": "esp32:esp32:esp32c3:CDCOnBoot=cdc",
//     "sketch": "deck", "build": "../build/firmware-ssd1306",
//     "usbVid": ["0x303a"], "baud": 115200 }
//
//   { "family": "rp2040", "uf2": "build/firmware.uf2", "volume": "RPI-RP2" }
//
// `sketch`, `build` and `uf2` are relative to `firmware/` and must stay inside
// the project. Everything the tools print is returned as the log the tab shows;
// after a write the serial port is read for a few seconds so the person sees the
// board say something, not just "upload done".

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const FIRMWARE_DIR = "firmware";
export const RECIPE_FILE = "flash.json";

/** Per family: the USB vendor ids the chip enumerates with when no recipe says otherwise. */
export const FAMILIES = Object.freeze({
  // Espressif's own USB (S2/S3/C3/C6 native USB-JTAG/serial) plus the UART bridges
  // dev boards ship with: WCH CH340/CH343, Silicon Labs CP210x, FTDI.
  esp32: Object.freeze({ usbVid: ["0x303a", "0x1a86", "0x10c4", "0x0403"], tool: "arduino-cli" }),
  // RP2040 in BOOTSEL mode is a mass-storage volume, and Raspberry Pi's vid on serial.
  rp2040: Object.freeze({ usbVid: ["0x2e8a"], volume: "RPI-RP2" }),
});

const DEFAULT_BAUD = 115200;
const DEFAULT_MONITOR_SECONDS = 8;
const COMPILE_TIMEOUT_MS = 10 * 60 * 1000;
const UPLOAD_TIMEOUT_MS = 3 * 60 * 1000;
const OUTPUT_TAIL = 6000;

function fail(code, message, statusCode = 400) {
  const err = new Error(message);
  err.code = code;
  err.statusCode = statusCode;
  return err;
}

/** `0x303A`, `303a`, `0X303a` → `0x303a`. Anything else → "". */
export function normalizeVid(value) {
  const hex = String(value ?? "").trim().toLowerCase().replace(/^0x/, "");
  return /^[0-9a-f]{1,4}$/.test(hex) ? `0x${hex.padStart(4, "0")}` : "";
}

function insideProject(projectRoot, target, what) {
  const resolved = path.resolve(projectRoot, FIRMWARE_DIR, target);
  if (!(resolved === projectRoot || resolved.startsWith(projectRoot + path.sep))) {
    throw fail("FLASH_RECIPE_INVALID", `${what} leaves the project: ${target}`);
  }
  return resolved;
}

/**
 * Read and validate `firmware/flash.json`. Throws a coded error the command
 * relays; the tab shows the message as the reason the button cannot run.
 */
export function readRecipe(projectRoot) {
  const root = path.resolve(projectRoot);
  const file = path.join(root, FIRMWARE_DIR, RECIPE_FILE);
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    if (err?.code === "ENOENT") {
      throw fail("FLASH_RECIPE_MISSING", `firmware/${RECIPE_FILE} is not written — the agent writes it beside the firmware`, 404);
    }
    throw fail("FLASH_RECIPE_INVALID", `firmware/${RECIPE_FILE} is not valid JSON: ${err.message}`);
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw fail("FLASH_RECIPE_INVALID", `firmware/${RECIPE_FILE} must be an object`);
  }
  const family = String(raw.family || "").toLowerCase();
  const known = FAMILIES[family];
  if (!known) {
    throw fail("FLASH_RECIPE_INVALID", `family must be one of ${Object.keys(FAMILIES).join(", ")}, not "${raw.family}"`);
  }
  const usbVid = (Array.isArray(raw.usbVid) && raw.usbVid.length ? raw.usbVid : known.usbVid).map(normalizeVid).filter(Boolean);
  if (!usbVid.length) {
    throw fail("FLASH_RECIPE_INVALID", "usbVid must list at least one hex vendor id, like \"0x303a\"");
  }
  const recipe = { family, usbVid, file: path.join(FIRMWARE_DIR, RECIPE_FILE) };
  const strArgs = (key) => {
    const value = raw[key];
    if (value === undefined) return [];
    if (!Array.isArray(value) || !value.every((v) => typeof v === "string")) {
      throw fail("FLASH_RECIPE_INVALID", `${key} must be a list of strings`);
    }
    return value;
  };
  if (family === "esp32") {
    const tool = String(raw.tool || known.tool);
    if (tool !== "arduino-cli") throw fail("FLASH_RECIPE_INVALID", `tool "${tool}" is not supported for esp32 (arduino-cli is)`);
    if (typeof raw.fqbn !== "string" || !raw.fqbn.trim()) throw fail("FLASH_RECIPE_INVALID", "fqbn is required for esp32, e.g. esp32:esp32:esp32c3:CDCOnBoot=cdc");
    if (typeof raw.sketch !== "string" || !raw.sketch.trim()) throw fail("FLASH_RECIPE_INVALID", "sketch (the sketch folder, relative to firmware/) is required");
    if (typeof raw.build !== "string" || !raw.build.trim()) throw fail("FLASH_RECIPE_INVALID", "build (the build folder, relative to firmware/) is required");
    Object.assign(recipe, {
      tool,
      fqbn: raw.fqbn.trim(),
      sketch: raw.sketch,
      sketchDir: insideProject(root, raw.sketch, "sketch"),
      build: raw.build,
      buildDir: insideProject(root, raw.build, "build"),
      compileArgs: strArgs("compileArgs"),
      uploadArgs: strArgs("uploadArgs"),
      baud: Number.isInteger(raw.baud) && raw.baud > 0 ? raw.baud : DEFAULT_BAUD,
      monitorSeconds: Number.isFinite(raw.monitorSeconds) && raw.monitorSeconds >= 0 ? raw.monitorSeconds : DEFAULT_MONITOR_SECONDS,
    });
    if (!fs.existsSync(recipe.sketchDir)) throw fail("FLASH_RECIPE_INVALID", `sketch folder does not exist: firmware/${raw.sketch}`);
  } else {
    if (typeof raw.uf2 !== "string" || !raw.uf2.trim()) throw fail("FLASH_RECIPE_INVALID", "uf2 (the built .uf2, relative to firmware/) is required for rp2040");
    Object.assign(recipe, {
      uf2: raw.uf2,
      uf2Path: insideProject(root, raw.uf2, "uf2"),
      volume: typeof raw.volume === "string" && raw.volume.trim() ? raw.volume.trim() : known.volume,
    });
  }
  return recipe;
}

/** A one-line description of what would be written, for the confirm step. */
export function describeRecipe(recipe) {
  if (recipe.family === "esp32") return `firmware/${recipe.sketch} (${recipe.fqbn}) via arduino-cli`;
  return `firmware/${recipe.uf2} onto the ${recipe.volume} volume`;
}

/**
 * Sort `arduino-cli board list --format json` into the ports that carry one of
 * the recipe's vendor ids (candidates), USB ports that do not (others — the
 * reason a click is refused), and ports with no USB identity at all (Bluetooth,
 * the debug console), which are ignored.
 */
export function classifyPorts(boardList, recipe) {
  const ports = Array.isArray(boardList?.detected_ports) ? boardList.detected_ports : [];
  const wanted = new Set(recipe.usbVid);
  const candidates = [];
  const others = [];
  let ignored = 0;
  for (const entry of ports) {
    const port = entry?.port || {};
    const props = port.properties || {};
    const vid = normalizeVid(props.vid);
    if (!vid) {
      ignored += 1;
      continue;
    }
    const row = {
      address: String(port.address || port.label || ""),
      vid,
      pid: normalizeVid(props.pid),
      serial: String(props.serialNumber || port.hardware_id || ""),
      boards: (Array.isArray(entry.matching_boards) ? entry.matching_boards : [])
        .filter((b) => !b.is_hidden)
        .map((b) => String(b.name || "")),
    };
    if (!row.address) continue;
    (wanted.has(vid) ? candidates : others).push(row);
  }
  return { candidates, others, ignored };
}

/** Where a BOOTSEL volume mounts on this OS, for every mount root we know. */
export function volumePaths(volume, platform = process.platform, home = os.homedir()) {
  if (platform === "darwin") return [path.join("/Volumes", volume)];
  const roots = ["/media", "/run/media", "/mnt"];
  const found = [];
  for (const root of roots) {
    let users = [];
    try {
      users = fs.readdirSync(root);
    } catch {
      continue;
    }
    for (const user of users) found.push(path.join(root, user, volume));
    found.push(path.join(root, volume));
  }
  found.push(path.join(home, volume));
  return found;
}

function findArduinoCli(env = process.env) {
  // An explicit ARDUINO_CLI is the answer, present or not: a person who set it
  // to a path that is not there wants to hear that, not have a Homebrew copy
  // silently used instead.
  if (env.ARDUINO_CLI) return fs.existsSync(env.ARDUINO_CLI) ? env.ARDUINO_CLI : "";
  const candidates = [
    env.CIRCUIT_TOOLCHAIN ? path.join(env.CIRCUIT_TOOLCHAIN, "arduino-cli", "bin", "arduino-cli") : "",
    env.CIRCUIT_TOOLCHAIN ? path.join(env.CIRCUIT_TOOLCHAIN, "arduino-cli", "arduino-cli") : "",
  ].filter(Boolean);
  for (const c of candidates) if (fs.existsSync(c)) return c;
  for (const dir of String(env.PATH || "").split(path.delimiter)) {
    const c = path.join(dir, "arduino-cli");
    if (dir && fs.existsSync(c)) return c;
  }
  for (const c of ["/opt/homebrew/bin/arduino-cli", "/usr/local/bin/arduino-cli"]) if (fs.existsSync(c)) return c;
  return "";
}

/** Run a tool and capture everything it says; resolves `{code, output}`, never rejects. */
export function runTool(cmd, args, { cwd, timeoutMs = UPLOAD_TIMEOUT_MS, env = process.env } = {}) {
  return new Promise((resolve) => {
    let output = "";
    let done = false;
    let child;
    try {
      child = spawn(cmd, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    } catch (err) {
      resolve({ code: 127, output: String(err?.message || err) });
      return;
    }
    const timer = setTimeout(() => {
      if (done) return;
      output += `\n[timed out after ${Math.round(timeoutMs / 1000)}s]`;
      try {
        child.kill("SIGKILL");
      } catch {
        // already gone
      }
    }, timeoutMs);
    const take = (chunk) => {
      output += chunk.toString();
      if (output.length > OUTPUT_TAIL * 4) output = output.slice(-OUTPUT_TAIL * 2);
    };
    child.stdout.on("data", take);
    child.stderr.on("data", take);
    child.on("error", (err) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ code: 127, output: `${output}\n${err.message}` });
    });
    child.on("close", (code) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ code: code ?? 1, output });
    });
  });
}

/**
 * Read what the board says on its serial port for a few seconds after the
 * write. Best effort and never fatal: a board with no console, a port that
 * re-enumerates under a new name, a `stty` that objects — all of those are a
 * note in the log, not a failed flash.
 */
export async function readSerial(port, baud, seconds, { run = runTool, platform = process.platform } = {}) {
  if (!seconds) return { text: "", note: "serial monitor off" };
  const sttyFlag = platform === "darwin" ? "-f" : "-F";
  // The port needs a moment to come back after the chip resets.
  await new Promise((r) => setTimeout(r, 1500));
  const stty = await run("stty", [sttyFlag, port, String(baud), "raw", "-echo", "-hupcl"], { timeoutMs: 5000 });
  if (stty.code !== 0) return { text: "", note: `could not open ${port} for reading: ${stty.output.trim()}` };
  return new Promise((resolve) => {
    let text = "";
    let fd = null;
    const finish = (note) => {
      if (fd !== null) {
        try {
          fs.closeSync(fd);
        } catch {
          // closed by the read error
        }
        fd = null;
      }
      resolve({ text: text.slice(-OUTPUT_TAIL), note });
    };
    try {
      fd = fs.openSync(port, fs.constants.O_RDONLY | fs.constants.O_NONBLOCK);
    } catch (err) {
      resolve({ text: "", note: `could not open ${port}: ${err.message}` });
      return;
    }
    const buffer = Buffer.alloc(4096);
    const deadline = Date.now() + seconds * 1000;
    const tick = () => {
      if (fd === null) return;
      try {
        const n = fs.readSync(fd, buffer, 0, buffer.length, null);
        if (n > 0) text += buffer.toString("utf8", 0, n);
      } catch (err) {
        if (err.code !== "EAGAIN") {
          finish(`read stopped: ${err.message}`);
          return;
        }
      }
      if (Date.now() >= deadline) {
        finish(text ? `read ${seconds}s` : `nothing on ${port} in ${seconds}s`);
        return;
      }
      setTimeout(tick, 50);
    };
    tick();
  });
}

/**
 * Step one of the button: what is on USB, and would the recipe write to it?
 * `decision` is `one` (exactly one candidate — the flash step may run), `none`
 * or `many`; `others` are the USB serial devices that are NOT this board's
 * family, listed so the person sees why nothing was written.
 */
export async function detect({ projectRoot, run = runTool, env = process.env, platform = process.platform, volumes = volumePaths }) {
  const root = path.resolve(projectRoot);
  const recipe = readRecipe(root);
  const result = { family: recipe.family, recipe: describeRecipe(recipe), candidates: [], others: [], ignored: 0, decision: "none", hint: "" };

  if (recipe.family === "rp2040") {
    const mounted = volumes(recipe.volume, platform).filter((p) => fs.existsSync(p));
    result.candidates = mounted.map((p) => ({ address: p, kind: "volume", vid: recipe.usbVid[0], pid: "", serial: "", boards: [`${recipe.volume} volume`] }));
    result.decision = mounted.length === 1 ? "one" : mounted.length ? "many" : "none";
    result.hint = mounted.length
      ? ""
      : `No ${recipe.volume} volume is mounted. Hold BOOTSEL while plugging the board in (or while tapping RESET); it appears as a USB drive named ${recipe.volume}.`;
    return result;
  }

  const cli = findArduinoCli(env);
  if (!cli) {
    throw fail("FLASH_TOOLCHAIN_MISSING", env.ARDUINO_CLI
      ? `ARDUINO_CLI points at ${env.ARDUINO_CLI}, which does not exist`
      : "arduino-cli is not on this machine (brew install arduino-cli, or set ARDUINO_CLI)", 409);
  }
  const listed = await run(cli, ["board", "list", "--format", "json"], { timeoutMs: 20000, env });
  let boardList;
  try {
    boardList = JSON.parse(listed.output.slice(listed.output.indexOf("{")));
  } catch {
    throw fail("FLASH_DETECT_FAILED", `arduino-cli board list did not answer with JSON: ${listed.output.trim().slice(-400)}`, 500);
  }
  const sorted = classifyPorts(boardList, recipe);
  Object.assign(result, sorted);
  result.decision = sorted.candidates.length === 1 ? "one" : sorted.candidates.length ? "many" : "none";
  if (result.decision === "none") {
    result.hint = sorted.others.length
      ? `The USB serial device${sorted.others.length === 1 ? "" : "s"} on this machine (${sorted.others.map((o) => `${o.address} vid ${o.vid}`).join(", ")}) ${sorted.others.length === 1 ? "is" : "are"} not this board's family (${recipe.usbVid.join(", ")}). Not flashing it.`
      : "No USB serial device found. A charge-only cable is the usual cause; a board that needs its bootloader: hold BOOT, tap RESET, release BOOT, then look again.";
  } else if (result.decision === "many") {
    result.hint = `${sorted.candidates.length} boards of this family are plugged in (${sorted.candidates.map((c) => c.address).join(", ")}). Unplug all but the one to flash — the app does not guess.`;
  }
  return result;
}

/**
 * Step two: write the firmware to `port`, which must be the one and only
 * candidate `detect` returns right now. Returns the steps run with their
 * output, and what the board said afterwards.
 */
export async function flash({ projectRoot, port, run = runTool, env = process.env, platform = process.platform, volumes = volumePaths, serial = readSerial }) {
  const root = path.resolve(projectRoot);
  const recipe = readRecipe(root);
  const seen = await detect({ projectRoot: root, run, env, platform, volumes });
  if (seen.decision !== "one") {
    throw fail("FLASH_REFUSED", seen.hint || "nothing to flash", 409);
  }
  const target = seen.candidates[0];
  if (!port || String(port) !== target.address) {
    throw fail("FLASH_PORT_MISMATCH", `The board on USB is now ${target.address}, not ${port || "(none)"}; look again before flashing.`, 409);
  }

  const steps = [];
  const record = (name, cmd, args, result) => {
    steps.push({ name, command: [cmd, ...args].join(" "), code: result.code, output: result.output.slice(-OUTPUT_TAIL) });
    return result.code === 0;
  };

  if (recipe.family === "rp2040") {
    if (!fs.existsSync(recipe.uf2Path)) throw fail("FLASH_RECIPE_INVALID", `the .uf2 does not exist: firmware/${recipe.uf2} — build it first`);
    const dest = path.join(target.address, path.basename(recipe.uf2Path));
    try {
      fs.copyFileSync(recipe.uf2Path, dest);
      steps.push({ name: "copy", command: `cp ${recipe.uf2Path} ${dest}`, code: 0, output: `${fs.statSync(recipe.uf2Path).size} bytes` });
    } catch (err) {
      steps.push({ name: "copy", command: `cp ${recipe.uf2Path} ${dest}`, code: 1, output: err.message });
      return { ok: false, port: target.address, target, steps, serial: null };
    }
    // The volume vanishing is the board rebooting into the new firmware.
    const gone = await new Promise((resolve) => {
      const deadline = Date.now() + 10000;
      const poll = () => (!fs.existsSync(target.address) ? resolve(true) : Date.now() > deadline ? resolve(false) : setTimeout(poll, 250));
      poll();
    });
    steps.push({ name: "reboot", command: `wait for ${target.address} to unmount`, code: gone ? 0 : 1, output: gone ? "the board rebooted into the new firmware" : "the volume is still mounted — the board did not take the file" });
    return { ok: gone, port: target.address, target, steps, serial: null };
  }

  const cli = findArduinoCli(env);
  const compile = await run(cli, ["compile", "--fqbn", recipe.fqbn, "--build-path", recipe.buildDir, ...recipe.compileArgs, recipe.sketchDir], { cwd: path.join(root, FIRMWARE_DIR), timeoutMs: COMPILE_TIMEOUT_MS, env });
  if (!record("compile", cli, ["compile", "--fqbn", recipe.fqbn, "--build-path", recipe.buildDir, ...recipe.compileArgs, recipe.sketchDir], compile)) {
    return { ok: false, port: target.address, target, steps, serial: null };
  }
  const uploadArgs = ["upload", "--fqbn", recipe.fqbn, "--input-dir", recipe.buildDir, "--port", target.address, ...recipe.uploadArgs, recipe.sketchDir];
  const upload = await run(cli, uploadArgs, { cwd: path.join(root, FIRMWARE_DIR), timeoutMs: UPLOAD_TIMEOUT_MS, env });
  if (!record("upload", cli, uploadArgs, upload)) {
    return { ok: false, port: target.address, target, steps, serial: null };
  }
  const monitor = await serial(target.address, recipe.baud, recipe.monitorSeconds, { run, platform });
  return { ok: true, port: target.address, target, steps, serial: monitor };
}
