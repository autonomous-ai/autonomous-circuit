import assert from "node:assert/strict";
import crypto from "node:crypto";
import {EventEmitter} from "node:events";
import {Readable, Writable} from "node:stream";
import test from "node:test";
import {
  BambuStatusError, discoverBambuFingerprint, parseBambuReport,
  readBambuStatus, readBambuStatusFromStdin,
} from "./bambuStatus.mjs";

const cert = Buffer.from("test printer certificate");
const pin = crypto.createHash("sha256").update(cert).digest("hex");
const config = {
  host: "192.168.1.42", serial: "03900D610214855", accessCode: "secret12",
  certSha256: pin, timeoutMs: 700,
};

function frame(type, body) {
  const length = [];
  let remaining = body.length;
  do {
    let byte = remaining % 128;
    remaining = Math.floor(remaining / 128);
    if (remaining) byte |= 0x80;
    length.push(byte);
  } while (remaining);
  return Buffer.concat([Buffer.from([type, ...length]), body]);
}

function publish(serial, report) {
  const topic = Buffer.from(`device/${serial}/report`);
  const header = Buffer.alloc(2);
  header.writeUInt16BE(topic.length);
  return frame(0x30, Buffer.concat([header, topic, Buffer.from(JSON.stringify(report))]));
}

class FakeSocket extends EventEmitter {
  constructor(onWrite = () => {}, peerCert = cert) {
    super();
    this.onWrite = onWrite;
    this.peerCert = peerCert;
    this.writes = [];
    this.destroyed = false;
  }
  getPeerCertificate() { return {raw: this.peerCert}; }
  write(data) {
    this.writes.push(data);
    this.onWrite(data, this);
    return true;
  }
  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    queueMicrotask(() => this.emit("close"));
  }
}

function fakeConnect(socket) {
  return options => {
    assert.deepEqual({host: options.host, port: options.port}, {host: config.host, port: 8883});
    queueMicrotask(() => socket.emit("secureConnect"));
    return socket;
  };
}

test("parser keeps only bounded status fields and ignores non-status messages", () => {
  const update = parseBambuReport(JSON.stringify({print: {
    command: "push_status", gcode_state: "RUNNING", subtask_name: "Robot\nplate 2",
    mc_percent: 53, mc_remaining_time: "128", layer_num: 111,
    total_layer_num: 305, task_id: "job-7", access_code: "leak me",
  }}));
  assert.deepEqual(update, {
    state: "printing", jobName: "Robot plate 2", progressPercent: 53,
    remainingMinutes: 128, currentLayer: 111, totalLayers: 305, taskId: "job-7",
  });
  assert.equal(parseBambuReport('{"print":{"command":"pause","mc_percent":50}}'), null);
  assert.equal(parseBambuReport('{"print":{"command":"push_status","gcode_state":"constructor"}}'), null);
  assert.equal(parseBambuReport('{"print":{"command":"push_status","mc_percent":101}}'), null);
});

test("rejects non-local host and missing certificate pin before network access", () => {
  let called = false;
  const connectTls = () => { called = true; throw new Error("unexpected"); };
  assert.throws(() => readBambuStatus({...config, host: "8.8.8.8"}, {connectTls}),
    error => error instanceof BambuStatusError && error.code === "invalid_config");
  assert.throws(() => readBambuStatus({...config, certSha256: undefined}, {connectTls}),
    error => error instanceof BambuStatusError && error.code === "invalid_config");
  assert.equal(called, false);
});

test("rejects a mismatched certificate before sending the access code", async () => {
  const socket = new FakeSocket();
  await assert.rejects(readBambuStatus({...config, certSha256: "0".repeat(64)},
    {connectTls: fakeConnect(socket)}),
  error => error instanceof BambuStatusError && error.code === "tls_untrusted");
  assert.deepEqual(socket.writes, []);
});

test("subscribes to status only, merges partial reports, and returns sanitized snapshot", async () => {
  const socket = new FakeSocket((data, self) => {
    if (data[0] === 0x10) {
      queueMicrotask(() => self.emit("data", frame(0x20, Buffer.from([0, 0]))));
    } else if (data[0] === 0x82) {
      const packets = Buffer.concat([
        frame(0x90, Buffer.from([0, 1, 0])),
        publish(config.serial, {print: {command: "push_status", gcode_state: "RUNNING", subtask_name: "plate_2"}}),
        publish(config.serial, {print: {command: "push_status", mc_percent: 24, mc_remaining_time: 195, layer_num: 42}}),
      ]);
      queueMicrotask(() => {
        self.emit("data", packets.subarray(0, 7));
        self.emit("data", packets.subarray(7));
      });
    }
  });
  const result = await readBambuStatus(config, {
    connectTls: fakeConnect(socket), now: () => new Date("2026-10-28T05:00:00.000Z"),
  });
  assert.equal(result.source, "bambu-lan-mqtt-experimental");
  assert.equal(result.supported, false);
  assert.equal(result.reason, "ok");
  assert.equal(result.observedAt, "2026-10-28T05:00:00.000Z");
  assert.deepEqual(result.status, {
    state: "printing", jobName: "plate_2", progressPercent: 24,
    remainingMinutes: 195, currentLayer: 42,
  });
  assert.deepEqual(socket.writes.map(packet => packet[0]), [0x10, 0x82]);
  assert.ok(!JSON.stringify(result).includes(config.accessCode));
});

test("oversized MQTT frame fails closed", async () => {
  const socket = new FakeSocket((data, self) => {
    if (data[0] === 0x10) queueMicrotask(() => self.emit("data", Buffer.from([0x30, 0x80, 0x80, 0x10])));
  });
  await assert.rejects(readBambuStatus(config, {connectTls: fakeConnect(socket)}),
    error => error instanceof BambuStatusError && error.code === "protocol_error");
});

test("connection without a subscription acknowledgement is unavailable", async () => {
  const socket = new FakeSocket((data, self) => {
    if (data[0] === 0x10) queueMicrotask(() => self.emit("data", frame(0x20, Buffer.from([0, 0]))));
    if (data[0] === 0x82) queueMicrotask(() => self.destroy());
  });
  await assert.rejects(readBambuStatus(config, {connectTls: fakeConnect(socket)}),
    error => error instanceof BambuStatusError && error.code === "mqtt_rejected");
});

test("fingerprint discovery sends no application bytes", async () => {
  const socket = new FakeSocket();
  const actual = await discoverBambuFingerprint(config.host, {connectTls: fakeConnect(socket)});
  assert.equal(actual, pin);
  assert.deepEqual(socket.writes, []);
});

test("CLI input rejects malformed JSON without including its contents", async () => {
  const stdin = Readable.from(["{accessCode:secret12}"]);
  const output = [];
  const stdout = new Writable({write(chunk, _enc, next) { output.push(chunk.toString()); next(); }});
  await assert.rejects(readBambuStatusFromStdin(stdin, stdout),
    error => error instanceof BambuStatusError && error.code === "invalid_config" &&
      !error.message.includes("secret12"));
  assert.deepEqual(output, []);
});

test("CLI emits only sanitized JSON to the coordinator", async () => {
  const socket = new FakeSocket((data, self) => {
    if (data[0] === 0x10) {
      queueMicrotask(() => self.emit("data", frame(0x20, Buffer.from([0, 0]))));
    } else if (data[0] === 0x82) {
      queueMicrotask(() => self.emit("data", Buffer.concat([
        frame(0x90, Buffer.from([0, 1, 0])),
        publish(config.serial, {print: {
          command: "push_status", gcode_state: "FINISH", subtask_name: "plate_2",
          mc_percent: 100, access_code: "never expose",
        }}),
      ])));
    }
  });
  const stdin = Readable.from([JSON.stringify(config)]);
  const output = [];
  const stdout = new Writable({write(chunk, _enc, next) { output.push(chunk.toString()); next(); }});
  const result = await readBambuStatusFromStdin(stdin, stdout, {connectTls: fakeConnect(socket)});
  assert.deepEqual(JSON.parse(output.join("")), result);
  assert.equal(result.status.state, "finished");
  assert.ok(!output.join("").includes(config.accessCode));
  assert.ok(!output.join("").includes("never expose"));
});
