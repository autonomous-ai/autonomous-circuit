// The Flash button's server half: nothing is written unless the recipe names
// the board, exactly one board of that family is on USB, and the address the
// person confirmed is still the one there. The tool runner is a fake — no
// arduino-cli runs, no port is opened — and the board list is the real JSON
// `arduino-cli board list --format json` printed with a C3 SuperMini plugged in.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { classifyPorts, describeRecipe, detect, flash, normalizeVid, readRecipe, volumePaths } from "./firmwareFlash.mjs";

const BOARD_LIST = {
  detected_ports: [
    {
      matching_boards: [
        { name: "ESP32 Family Device", fqbn: "esp32:esp32:esp32_family", is_hidden: true },
        { name: "Ozobot DRVKit", fqbn: "esp32:esp32:ozobot_drvkit" },
      ],
      port: {
        address: "/dev/cu.usbmodem1201",
        label: "/dev/cu.usbmodem1201",
        protocol: "serial",
        properties: { pid: "0x1001", serialNumber: "E0:72:A1:6C:D3:F8", vid: "0x303A" },
        hardware_id: "E0:72:A1:6C:D3:F8",
      },
    },
    { port: { address: "/dev/cu.debug-console", protocol: "serial", properties: {} } },
    { port: { address: "/dev/cu.Bluetooth-Incoming-Port", protocol: "serial", properties: {} } },
  ],
};

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "circuit-flash-"));
}

function seed(root, recipe, { sketch = true } = {}) {
  fs.mkdirSync(path.join(root, "firmware", "deck"), { recursive: true });
  if (sketch) fs.writeFileSync(path.join(root, "firmware", "deck", "deck.ino"), "void setup() {}");
  fs.writeFileSync(path.join(root, "firmware", "flash.json"), JSON.stringify(recipe));
}

const ESP = { family: "esp32", fqbn: "esp32:esp32:esp32c3:CDCOnBoot=cdc", sketch: "deck", build: "../build/fw", usbVid: ["0x303a"] };

/** A fake arduino-cli: records every call, answers board list with `list`, everything else with `code`. */
function fakeCli({ list = BOARD_LIST, code = 0 } = {}) {
  const calls = [];
  const run = async (cmd, args) => {
    calls.push([path.basename(cmd), ...args]);
    if (args[0] === "board" && args[1] === "list") return { code: 0, output: JSON.stringify(list) };
    if (cmd === "stty") return { code: 1, output: "no tty in tests" };
    return { code: typeof code === "function" ? code(args[0]) : code, output: `${args[0]} output` };
  };
  return { calls, run };
}

const ENV = { PATH: "/usr/bin", ARDUINO_CLI: process.execPath }; // any existing file stands in for the cli

test("normalizeVid accepts the spellings tools use and rejects the rest", () => {
  assert.equal(normalizeVid("0x303A"), "0x303a");
  assert.equal(normalizeVid("303a"), "0x303a");
  assert.equal(normalizeVid("0X2E8A"), "0x2e8a");
  assert.equal(normalizeVid(""), "");
  assert.equal(normalizeVid("usb"), "");
});

test("readRecipe: missing, invalid and escaping recipes are refused with a coded reason", () => {
  const root = tmpdir();
  assert.throws(() => readRecipe(root), { code: "FLASH_RECIPE_MISSING" });
  fs.mkdirSync(path.join(root, "firmware"));
  fs.writeFileSync(path.join(root, "firmware", "flash.json"), "{ nope");
  assert.throws(() => readRecipe(root), { code: "FLASH_RECIPE_INVALID" });
  seed(root, { family: "avr", fqbn: "x", sketch: "deck", build: "b" });
  assert.throws(() => readRecipe(root), { code: "FLASH_RECIPE_INVALID", message: /family/ });
  seed(root, { ...ESP, fqbn: "" });
  assert.throws(() => readRecipe(root), { message: /fqbn/ });
  seed(root, { ...ESP, build: "../../../elsewhere" });
  assert.throws(() => readRecipe(root), { message: /leaves the project/ });
  seed(root, { ...ESP, sketch: "missing" });
  assert.throws(() => readRecipe(root), { message: /sketch folder does not exist/ });
  seed(root, { family: "rp2040" });
  assert.throws(() => readRecipe(root), { message: /uf2/ });
});

test("readRecipe: a good esp32 recipe resolves its folders inside the project and fills the defaults", () => {
  const root = tmpdir();
  seed(root, { ...ESP, usbVid: undefined, compileArgs: ["--build-property", "x=1"] });
  const r = readRecipe(root);
  assert.equal(r.family, "esp32");
  assert.equal(r.tool, "arduino-cli");
  assert.deepEqual(r.usbVid, ["0x303a", "0x1a86", "0x10c4", "0x0403"]);
  assert.equal(r.sketchDir, path.join(path.resolve(root), "firmware", "deck"));
  assert.equal(r.buildDir, path.join(path.resolve(root), "build", "fw"));
  assert.equal(r.baud, 115200);
  assert.equal(r.monitorSeconds, 8);
  assert.deepEqual(r.compileArgs, ["--build-property", "x=1"]);
  assert.equal(describeRecipe(r), "firmware/deck (esp32:esp32:esp32c3:CDCOnBoot=cdc) via arduino-cli");
});

test("classifyPorts: the C3 is a candidate, ports with no USB identity are ignored, a foreign vid is 'others'", () => {
  const recipe = readRecipeFor(ESP);
  const sorted = classifyPorts(BOARD_LIST, recipe);
  assert.equal(sorted.ignored, 2);
  assert.deepEqual(sorted.others, []);
  assert.equal(sorted.candidates.length, 1);
  const [c3] = sorted.candidates;
  assert.equal(c3.address, "/dev/cu.usbmodem1201");
  assert.equal(c3.vid, "0x303a");
  assert.equal(c3.pid, "0x1001");
  assert.equal(c3.serial, "E0:72:A1:6C:D3:F8");
  assert.deepEqual(c3.boards, ["Ozobot DRVKit"], "hidden family entries are not board names");

  const pico = classifyPorts(BOARD_LIST, readRecipeFor({ family: "rp2040", uf2: "fw.uf2" }));
  assert.equal(pico.candidates.length, 0);
  assert.equal(pico.others[0].address, "/dev/cu.usbmodem1201");
});

function readRecipeFor(recipe) {
  const root = tmpdir();
  seed(root, recipe);
  return readRecipe(root);
}

test("detect: one C3 on USB is 'one'; a foreign device is 'none' with the reason; two boards is 'many'", async () => {
  const root = tmpdir();
  seed(root, ESP);
  const one = await detect({ projectRoot: root, run: fakeCli().run, env: ENV });
  assert.equal(one.decision, "one");
  assert.equal(one.candidates[0].address, "/dev/cu.usbmodem1201");
  assert.equal(one.hint, "");

  const foreign = structuredClone(BOARD_LIST);
  foreign.detected_ports[0].port.properties.vid = "0x2e8a";
  const none = await detect({ projectRoot: root, run: fakeCli({ list: foreign }).run, env: ENV });
  assert.equal(none.decision, "none");
  assert.match(none.hint, /not this board's family/);
  assert.match(none.hint, /0x2e8a/);

  const empty = await detect({ projectRoot: root, run: fakeCli({ list: { detected_ports: [] } }).run, env: ENV });
  assert.equal(empty.decision, "none");
  assert.match(empty.hint, /charge-only cable/);

  const two = structuredClone(BOARD_LIST);
  two.detected_ports.push({ ...structuredClone(two.detected_ports[0]), port: { ...two.detected_ports[0].port, address: "/dev/cu.usbmodem2" } });
  const many = await detect({ projectRoot: root, run: fakeCli({ list: two }).run, env: ENV });
  assert.equal(many.decision, "many");
  assert.match(many.hint, /does not guess/);
});

test("detect without arduino-cli on the machine says so instead of pretending", async () => {
  const root = tmpdir();
  seed(root, ESP);
  await assert.rejects(detect({ projectRoot: root, run: fakeCli().run, env: { PATH: "/nonexistent", ARDUINO_CLI: "/nonexistent/arduino-cli" } }), { code: "FLASH_TOOLCHAIN_MISSING", message: /ARDUINO_CLI points at/ });
});

test("flash: compiles then uploads to exactly the confirmed port, and reads the serial console after", async () => {
  const root = tmpdir();
  seed(root, ESP);
  const cli = fakeCli();
  const serial = async (port, baud, seconds) => ({ text: `hello from ${port} @${baud} for ${seconds}s`, note: "read" });
  const result = await flash({ projectRoot: root, port: "/dev/cu.usbmodem1201", run: cli.run, env: ENV, serial });
  assert.equal(result.ok, true);
  assert.deepEqual(result.steps.map((s) => [s.name, s.code]), [["compile", 0], ["upload", 0]]);
  const [, compile, upload] = cli.calls;
  assert.equal(compile[1], "compile");
  assert.deepEqual(compile.slice(2, 4), ["--fqbn", ESP.fqbn]);
  assert.equal(compile.at(-1), path.join(path.resolve(root), "firmware", "deck"));
  assert.equal(upload[1], "upload");
  assert.deepEqual(upload.slice(upload.indexOf("--port"), upload.indexOf("--port") + 2), ["--port", "/dev/cu.usbmodem1201"]);
  assert.ok(!upload.some((a) => a.includes("*")), "never a wildcard port");
  assert.match(result.serial.text, /hello from \/dev\/cu\.usbmodem1201 @115200 for 8s/);
});

test("flash refuses when the port is not the one board seen now, and stops at a failed compile", async () => {
  const root = tmpdir();
  seed(root, ESP);
  await assert.rejects(flash({ projectRoot: root, port: "/dev/cu.usbmodem9", run: fakeCli().run, env: ENV }), { code: "FLASH_PORT_MISMATCH" });
  await assert.rejects(flash({ projectRoot: root, port: "", run: fakeCli().run, env: ENV }), { code: "FLASH_PORT_MISMATCH" });
  await assert.rejects(flash({ projectRoot: root, port: "/dev/cu.usbmodem1201", run: fakeCli({ list: { detected_ports: [] } }).run, env: ENV }), { code: "FLASH_REFUSED" });

  const broken = fakeCli({ code: (verb) => (verb === "compile" ? 2 : 0) });
  const result = await flash({ projectRoot: root, port: "/dev/cu.usbmodem1201", run: broken.run, env: ENV });
  assert.equal(result.ok, false);
  assert.deepEqual(result.steps.map((s) => [s.name, s.code]), [["compile", 2]]);
  assert.ok(!broken.calls.some((c) => c[1] === "upload"), "nothing is uploaded after a failed compile");
});

test("rp2040: the BOOTSEL volume is the target; the .uf2 is copied and the unmount is the reboot", async () => {
  const root = tmpdir();
  fs.mkdirSync(path.join(root, "firmware"), { recursive: true });
  fs.writeFileSync(path.join(root, "firmware", "fw.uf2"), "UF2!");
  fs.writeFileSync(path.join(root, "firmware", "flash.json"), JSON.stringify({ family: "rp2040", uf2: "fw.uf2" }));
  const volume = path.join(tmpdir(), "RPI-RP2");
  const volumes = () => [volume];

  const none = await detect({ projectRoot: root, volumes });
  assert.equal(none.decision, "none");
  assert.match(none.hint, /BOOTSEL/);

  fs.mkdirSync(volume);
  const one = await detect({ projectRoot: root, volumes });
  assert.equal(one.decision, "one");
  assert.equal(one.candidates[0].address, volume);
  assert.equal(one.candidates[0].kind, "volume");

  // The fake board "reboots" by unmounting once the file lands.
  const watcher = setInterval(() => {
    if (fs.existsSync(path.join(volume, "fw.uf2"))) {
      fs.rmSync(volume, { recursive: true, force: true });
      clearInterval(watcher);
    }
  }, 30);
  const result = await flash({ projectRoot: root, port: volume, volumes });
  assert.equal(result.ok, true);
  assert.deepEqual(result.steps.map((s) => [s.name, s.code]), [["copy", 0], ["reboot", 0]]);
  assert.equal(result.serial, null);
});

test("volumePaths knows where macOS and Linux mount a BOOTSEL drive", () => {
  assert.deepEqual(volumePaths("RPI-RP2", "darwin"), ["/Volumes/RPI-RP2"]);
  const linux = volumePaths("RPI-RP2", "linux", "/home/me");
  assert.ok(linux.includes("/home/me/RPI-RP2"));
  assert.ok(linux.every((p) => p.endsWith("RPI-RP2")));
});
