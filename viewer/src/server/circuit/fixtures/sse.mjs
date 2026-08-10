// Minimal SSE client for tests (Node 22.0 ships no global EventSource).
// Parses `event:`/`data:` frames; enough for the Circuit /api/events stream.

import http from "node:http";

export class MockEventSource {
  constructor(url) {
    this.listeners = new Map();
    this.buffer = "";
    this.request = http.get(url, (res) => {
      this.response = res;
      res.setEncoding("utf8");
      res.on("data", (chunk) => this._feed(chunk));
    });
    this.request.on("error", () => {});
  }

  _feed(chunk) {
    this.buffer += chunk;
    let idx;
    while ((idx = this.buffer.indexOf("\n\n")) !== -1) {
      const frame = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      let eventName = "message";
      const dataLines = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) {
          eventName = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice("data:".length).trimStart());
        }
      }
      if (!dataLines.length) {
        continue; // comment/heartbeat/retry frame
      }
      const payload = { data: dataLines.join("\n") };
      for (const listener of this.listeners.get(eventName) || []) {
        listener(payload);
      }
    }
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set());
    }
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    try {
      this.request.destroy();
      this.response?.destroy();
    } catch {
      // already closed
    }
  }
}

export async function waitFor(predicate, { timeoutMs = 8000, stepMs = 20 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (predicate()) {
      return;
    }
    if (Date.now() > deadline) {
      throw new Error("waitFor timed out");
    }
    await new Promise((resolve) => setTimeout(resolve, stepMs));
  }
}
