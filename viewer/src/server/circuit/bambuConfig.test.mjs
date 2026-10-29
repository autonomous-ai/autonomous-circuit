import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { bambuConfigFile, readConfiguredBambuStatus, readConfiguredBambuStatusCached } from "./bambuConfig.mjs";

const setup = () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "bambu-config-"));
  return { home, env: { CIRCUIT_HOME: home }, cleanup: () => fs.rmSync(home, { recursive: true, force: true }) };
};

test("LAN monitor is inert until configured and never treats a printer job as confirmed parts", async () => {
  const f = setup();
  try {
    assert.deepEqual(await readConfiguredBambuStatus({ env: f.env }), {
      account: { state: "not_connected", reason: "not_configured" }, jobs: [],
    });
    fs.writeFileSync(bambuConfigFile(f.env), JSON.stringify({ schema: 1, printers: [{
      name: "A1", host: "192.168.1.40", serial: "ABC123456", certSha256: "a".repeat(64),
    }] }));
    const result = await readConfiguredBambuStatus({
      env: f.env, lookupSecret: () => "private-code",
      readStatus: async input => {
        assert.equal(input.accessCode, "private-code");
        assert.equal(input.host, "192.168.1.40");
        return { reason: "ok", observedAt: "2026-10-28T00:00:00Z", status: {
          state: "printing", taskId: "task-7", jobName: "plate 2", progressPercent: 42,
        } };
      },
    });
    assert.equal(result.account.state, "connected");
    assert.equal(result.jobs[0].status, "printing");
    assert.deepEqual(result.jobs[0].matchedParts, []);
    assert.equal(result.jobs[0].source, "printer_lan");
    assert.ok(!fs.readFileSync(bambuConfigFile(f.env), "utf8").includes("private-code"));
  } finally { f.cleanup(); }
});

test("missing Keychain code and malformed config fail closed", async () => {
  const f = setup();
  try {
    fs.writeFileSync(bambuConfigFile(f.env), JSON.stringify({ schema: 1, printers: [{
      name: "A1", host: "192.168.1.40", serial: "ABC123456", certSha256: "a".repeat(64),
    }] }));
    assert.equal((await readConfiguredBambuStatus({ env: f.env, lookupSecret: () => null })).account.reason,
      "keychain_access_code_missing");
    fs.writeFileSync(bambuConfigFile(f.env), "{}");
    assert.equal((await readConfiguredBambuStatus({ env: f.env })).account.reason, "invalid_config");
  } finally { f.cleanup(); }
});

test("cached monitor returns the last observation while one refresh runs", async () => {
  const f = setup();
  try {
    let clock = 0, calls = 0, release;
    const read = () => { calls++; return calls === 1 ? Promise.resolve({ n: 1 }) : new Promise(resolve => { release = resolve; }); };
    const opts = { env: f.env, read, now: () => clock };
    assert.deepEqual(await readConfiguredBambuStatusCached(opts), { n: 1 });
    assert.deepEqual(await readConfiguredBambuStatusCached(opts), { n: 1 });
    assert.equal(calls, 1);
    clock = 60_000;
    assert.deepEqual(await readConfiguredBambuStatusCached(opts), { n: 1 });
    assert.deepEqual(await readConfiguredBambuStatusCached(opts), { n: 1 });
    assert.equal(calls, 2);
    release({ n: 2 });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(await readConfiguredBambuStatusCached(opts), { n: 2 });
    assert.equal(calls, 2);
  } finally { f.cleanup(); }
});
