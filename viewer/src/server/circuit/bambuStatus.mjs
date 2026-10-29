/**
 * Experimental, unsupported Bambu LAN status reader. Bambu documents MQTT status
 * visibility, but does not support the wire protocol as a public integration API.
 * Credentials and the printer certificate pin must be supplied by the caller.
 * This module never reads Bambu Studio files and never sends printer commands;
 * its only PUBLISH is the read-only `pushall` status request.
 */
import crypto from "node:crypto";
import net from "node:net";
import tls from "node:tls";
import {pathToFileURL} from "node:url";

const SOURCE = "bambu-lan-mqtt-experimental";
const MAX_FRAME_BYTES = 128 * 1024;
const MAX_SESSION_BYTES = 512 * 1024;
const MAX_STATUS_MESSAGES = 100;

export class BambuStatusError extends Error {
  constructor(code) {
    super({
      invalid_config: "Invalid Bambu LAN monitor configuration",
      tls_untrusted: "Bambu printer certificate fingerprint did not match",
      connection_failed: "Could not connect to Bambu printer",
      mqtt_rejected: "Bambu printer rejected the monitor connection",
      protocol_error: "Invalid Bambu printer status stream",
      timeout: "Bambu printer status timed out",
    }[code] || "Bambu printer status unavailable");
    this.name = "BambuStatusError";
    this.code = code;
  }
}

function privateIPv4(host) {
  if (net.isIP(host) !== 4) return false;
  const [a, b] = host.split(".").map(Number);
  return a === 10 || (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) || (a === 169 && b === 254);
}

function normalisePin(value) {
  const pin = typeof value === "string" ? value.replaceAll(":", "").toLowerCase() : "";
  if (!/^[0-9a-f]{64}$/.test(pin)) throw new BambuStatusError("invalid_config");
  return pin;
}

function validateHost(host) {
  if (typeof host !== "string" || !privateIPv4(host)) {
    throw new BambuStatusError("invalid_config");
  }
}

function validateConfig({host, serial, accessCode, certSha256, timeoutMs = 7000} = {}) {
  validateHost(host);
  if (typeof serial !== "string" || !/^[A-Za-z0-9_-]{6,64}$/.test(serial) ||
      typeof accessCode !== "string" || accessCode.length < 6 || accessCode.length > 64 ||
      /[\x00-\x1f\x7f]/.test(accessCode) ||
      !Number.isInteger(timeoutMs) || timeoutMs < 500 || timeoutMs > 30000) {
    throw new BambuStatusError("invalid_config");
  }
  return {host, serial, accessCode, certSha256: normalisePin(certSha256), timeoutMs};
}

function utf8(value) {
  const data = Buffer.from(value, "utf8");
  if (data.length > 65535) throw new BambuStatusError("invalid_config");
  const header = Buffer.allocUnsafe(2);
  header.writeUInt16BE(data.length);
  return Buffer.concat([header, data]);
}

function remainingLength(value) {
  const bytes = [];
  do {
    let octet = value % 128;
    value = Math.floor(value / 128);
    if (value > 0) octet |= 0x80;
    bytes.push(octet);
  } while (value > 0);
  return Buffer.from(bytes);
}

function mqttPacket(type, body) {
  return Buffer.concat([Buffer.from([type]), remainingLength(body.length), body]);
}

function connectPacket(accessCode) {
  const flags = Buffer.from([4, 0xc2, 0, 20]); // MQTT 3.1.1, username/password, clean session.
  const clientId = `circuit-status-${crypto.randomBytes(6).toString("hex")}`;
  return mqttPacket(0x10, Buffer.concat([
    utf8("MQTT"), flags, utf8(clientId), utf8("bblp"), utf8(accessCode),
  ]));
}

function subscribePacket(serial) {
  const packetId = Buffer.from([0, 1]);
  return mqttPacket(0x82, Buffer.concat([packetId, utf8(`device/${serial}/report`), Buffer.from([0])]));
}

/** A1/P1 printers push only deltas; `pushall` asks for one full status report. */
function pushallPacket(serial) {
  const payload = Buffer.from(JSON.stringify({pushing: {sequence_id: "0", command: "pushall"}}));
  return mqttPacket(0x30, Buffer.concat([utf8(`device/${serial}/request`), payload]));
}

function extractFrame(buffer) {
  if (buffer.length < 2) return null;
  let length = 0;
  let multiplier = 1;
  let offset = 1;
  for (let i = 0; i < 4; i++) {
    if (offset >= buffer.length) return null;
    const byte = buffer[offset++];
    length += (byte & 0x7f) * multiplier;
    if (length > MAX_FRAME_BYTES) throw new BambuStatusError("protocol_error");
    if (!(byte & 0x80)) {
      if (buffer.length < offset + length) return null;
      return {type: buffer[0], body: buffer.subarray(offset, offset + length), end: offset + length};
    }
    multiplier *= 128;
  }
  throw new BambuStatusError("protocol_error");
}

function textField(value, limit = 160) {
  if (typeof value !== "string") return undefined;
  const clean = value.replace(/[\x00-\x1f\x7f]/g, " ").trim();
  return clean ? clean.slice(0, limit) : undefined;
}

function intField(value, min, max) {
  if (typeof value === "string" && /^\d{1,10}$/.test(value)) value = Number(value);
  return Number.isInteger(value) && value >= min && value <= max ? value : undefined;
}

const STATES = {
  IDLE: "idle", PREPARE: "preparing", PREPARING: "preparing",
  RUNNING: "printing", PRINTING: "printing", PAUSE: "paused", PAUSED: "paused",
  FINISH: "finished", FINISHED: "finished", FAILED: "failed",
};

/** Return only whitelisted, bounded fields from one MQTT status payload. */
export function parseBambuReport(payload) {
  if (!Buffer.isBuffer(payload) && typeof payload !== "string") return null;
  if (Buffer.byteLength(payload) > MAX_FRAME_BYTES) return null;
  let doc;
  try { doc = JSON.parse(payload.toString()); } catch { return null; }
  const print = doc?.print;
  if (!print || typeof print !== "object" || Array.isArray(print) || print.command !== "push_status") return null;
  const result = {};
  if (typeof print.gcode_state === "string") {
    const key = print.gcode_state.toUpperCase();
    const state = Object.hasOwn(STATES, key) ? STATES[key] : undefined;
    if (state) result.state = state;
  }
  const fields = [
    ["subtask_name", "jobName", () => textField(print.subtask_name)],
    ["mc_percent", "progressPercent", () => intField(print.mc_percent, 0, 100)],
    ["mc_remaining_time", "remainingMinutes", () => intField(print.mc_remaining_time, 0, 60 * 24 * 30)],
    ["layer_num", "currentLayer", () => intField(print.layer_num, 0, 1_000_000)],
    ["total_layer_num", "totalLayers", () => intField(print.total_layer_num, 0, 1_000_000)],
    ["task_id", "taskId", () => textField(print.task_id, 64)],
    ["print_error", "errorCode", () => intField(print.print_error, 0, 0xffffffff)],
  ];
  for (const [source, target, read] of fields) {
    if (Object.hasOwn(print, source)) {
      const value = read();
      if (value !== undefined) result[target] = value;
    }
  }
  return Object.keys(result).length ? result : null;
}

function peerFingerprint(socket) {
  const cert = socket.getPeerCertificate?.();
  if (!cert?.raw || !Buffer.isBuffer(cert.raw)) throw new BambuStatusError("tls_untrusted");
  return crypto.createHash("sha256").update(cert.raw).digest("hex");
}

function constantTimePinMatch(actual, expected) {
  return crypto.timingSafeEqual(Buffer.from(actual, "hex"), Buffer.from(expected, "hex"));
}

/**
 * One-shot passive status observation. A verified certificate is required before
 * the LAN access code is transmitted. The only PUBLISH is a QoS 0 `pushall`.
 */
export function readBambuStatus(input, {connectTls = tls.connect, now = () => new Date()} = {}) {
  const {host, serial, accessCode, certSha256, timeoutMs} = validateConfig(input);
  return new Promise((resolve, reject) => {
    let socket;
    let done = false;
    let buffer = Buffer.alloc(0);
    let bytes = 0;
    let messages = 0;
    let connected = false;
    let subscribed = false;
    let pinVerified = false;
    let status = null;
    let observedAt = null;
    const finish = (error, value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      socket?.destroy();
      if (error) reject(error); else resolve(value);
    };
    const result = reason => ({source: SOURCE, supported: false, reason, observedAt, status});
    const timer = setTimeout(() => {
      if (!connected) finish(new BambuStatusError("timeout"));
      else if (!subscribed) finish(new BambuStatusError("mqtt_rejected"));
      else finish(null, result(status ? "ok" : "no_status"));
    }, timeoutMs);
    try {
      socket = connectTls({host, port: 8883, rejectUnauthorized: false, minVersion: "TLSv1.2"});
      socket.on("secureConnect", () => {
        if (done) return;
        try {
          if (!constantTimePinMatch(peerFingerprint(socket), certSha256)) {
            throw new BambuStatusError("tls_untrusted");
          }
          pinVerified = true;
          socket.write(connectPacket(accessCode));
        } catch (error) { finish(error instanceof BambuStatusError ? error : new BambuStatusError("tls_untrusted")); }
      });
      socket.on("data", chunk => {
        if (done) return;
        try {
          if (!pinVerified) throw new BambuStatusError("tls_untrusted");
          bytes += chunk.length;
          if (bytes > MAX_SESSION_BYTES) throw new BambuStatusError("protocol_error");
          buffer = Buffer.concat([buffer, chunk]);
          while (true) {
            const frame = extractFrame(buffer);
            if (!frame) break;
            buffer = buffer.subarray(frame.end);
            const packetType = frame.type >> 4;
            if (packetType === 2) { // CONNACK
              if (connected || frame.body.length !== 2 || frame.body[1] !== 0) {
                throw new BambuStatusError("mqtt_rejected");
              }
              connected = true;
              socket.write(subscribePacket(serial));
            } else if (packetType === 9) { // SUBACK
              if (!connected || subscribed || frame.body.length !== 3 ||
                  frame.body.readUInt16BE(0) !== 1 || frame.body[2] !== 0) {
                throw new BambuStatusError("mqtt_rejected");
              }
              subscribed = true;
              socket.write(pushallPacket(serial));
            } else if (packetType === 3 && subscribed) { // PUBLISH, QoS 0 only
              if (((frame.type >> 1) & 3) !== 0 || frame.body.length < 2) {
                throw new BambuStatusError("protocol_error");
              }
              const topicLength = frame.body.readUInt16BE(0);
              if (frame.body.length < 2 + topicLength) throw new BambuStatusError("protocol_error");
              const topic = frame.body.subarray(2, 2 + topicLength).toString("utf8");
              if (topic !== `device/${serial}/report`) continue;
              if (++messages > MAX_STATUS_MESSAGES) throw new BambuStatusError("protocol_error");
              const update = parseBambuReport(frame.body.subarray(2 + topicLength));
              if (update) {
                status = {...status, ...update};
                observedAt = now().toISOString();
                if (status.state && (status.state === "idle" || status.state === "finished" ||
                    status.state === "failed" || (status.jobName && status.progressPercent !== undefined))) {
                  finish(null, result("ok"));
                  return;
                }
              }
            }
          }
        } catch (error) { finish(error instanceof BambuStatusError ? error : new BambuStatusError("protocol_error")); }
      });
      socket.on("error", () => finish(new BambuStatusError("connection_failed")));
      socket.on("close", () => {
        if (!done) finish(!connected ? new BambuStatusError("connection_failed") :
          !subscribed ? new BambuStatusError("mqtt_rejected") : null,
        result(status ? "ok" : "no_status"));
      });
    } catch { finish(new BambuStatusError("connection_failed")); }
  });
}

/** Read only the peer certificate fingerprint for an explicit pairing UI. No secret is sent. */
export function discoverBambuFingerprint(host, {connectTls = tls.connect, timeoutMs = 5000} = {}) {
  validateHost(host);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 500 || timeoutMs > 30000) {
    throw new BambuStatusError("invalid_config");
  }
  return new Promise((resolve, reject) => {
    let socket;
    let done = false;
    const finish = (error, fingerprint) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      socket?.destroy();
      if (error) reject(error); else resolve(fingerprint);
    };
    const timer = setTimeout(() => finish(new BambuStatusError("timeout")), timeoutMs);
    try {
      socket = connectTls({host, port: 8883, rejectUnauthorized: false, minVersion: "TLSv1.2"});
      socket.on("secureConnect", () => {
        if (done) return;
        try { finish(null, peerFingerprint(socket)); }
        catch { finish(new BambuStatusError("tls_untrusted")); }
      });
      socket.on("error", () => finish(new BambuStatusError("connection_failed")));
      socket.on("close", () => finish(new BambuStatusError("connection_failed")));
    } catch { finish(new BambuStatusError("connection_failed")); }
  });
}

/** CLI transport for a trusted local coordinator: JSON input on stdin, status JSON on stdout. */
export async function readBambuStatusFromStdin(
  stdin = process.stdin, stdout = process.stdout, options = {},
) {
  let bytes = 0;
  const chunks = [];
  for await (const chunk of stdin) {
    const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += data.length;
    if (bytes > 4096) throw new BambuStatusError("invalid_config");
    chunks.push(data);
  }
  let input;
  try { input = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw new BambuStatusError("invalid_config"); }
  const result = await readBambuStatus(input, options);
  stdout.write(`${JSON.stringify(result)}\n`);
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 3 || process.argv[2] !== "--json") {
    process.stderr.write("usage: node bambuStatus.mjs --json < private-config.json\n");
    process.exitCode = 2;
  } else {
    try { await readBambuStatusFromStdin(); }
    catch (error) {
      const safe = error instanceof BambuStatusError ? error : new BambuStatusError("connection_failed");
      process.stdout.write(`${JSON.stringify({error: {code: safe.code, message: safe.message}})}\n`);
      process.exitCode = 1;
    }
  }
}
