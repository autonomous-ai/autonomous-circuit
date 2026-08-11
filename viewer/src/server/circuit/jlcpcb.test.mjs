// Signed-transport tests for the JLCPCB Open Platform client.
//
// The load-bearing test is `the docs' worked example` below. JLC publishes one
// (keys, method, path, timestamp, nonce, body, expected signature) tuple, and
// it is the only way to know our signing is right without a working appid —
// the live platform answers every request with the same 401 until one exists.
// If that test fails, nothing built on this client can be trusted, so it is
// deliberately the first thing in the file.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  DEFAULT_ENDPOINT,
  JlcError,
  buildAuthHeader,
  buildStringToSign,
  clientFromCredentials,
  createJlcClient,
  credentialStatus,
  generateNonce,
  readCredentials,
  sign,
} from "./jlcpcb.mjs";

/** The published worked example — api.jlcpcb.com/docs/api-request-signature §3. */
const DOCS_EXAMPLE = {
  accessKey: "b6713a535d56412f805afadd7e818455",
  secretKey: "z0BWlikshimuyiwBsH1i2qwnzMb3j3kA",
  appId: "293992070061998081",
  method: "POST",
  path: "/order/v1/createOrder",
  timestamp: "1625208260",
  nonce: "IZHEJYNIHYZIE8S0LLC0VWTPJVRRTO50",
  body: '{"goodsId":100,"quantity":52,"createdTime":"2024-03-21 10:03:20"}',
  signature: "sygwKhKBkLwHVv0c7D+a/A7JTEJjGH/kLugFKh16918=",
};

/** Credentials that satisfy the client's completeness check. Fake, obviously. */
const TEST_CREDS = {
  appId: "111111111111111111",
  accessKey: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  secretKey: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
};

/** A fetch that records the one call it receives and replays a canned reply. */
function fakeFetch({ status = 200, body = { code: 0, success: true, data: {} }, headers = {}, throws = null } = {}) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    if (throws) throw throws;
    const text = typeof body === "string" ? body : JSON.stringify(body);
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: { get: (name) => headers[name] ?? headers[name.toLowerCase()] ?? null },
      text: async () => text,
    };
  };
  impl.calls = calls;
  return impl;
}

function fixedClient(overrides = {}) {
  return createJlcClient({
    ...TEST_CREDS,
    now: () => 1625208260,
    nonceFn: () => DOCS_EXAMPLE.nonce,
    ...overrides,
  });
}

// ---------------------------------------------------------------------------
// Signing
// ---------------------------------------------------------------------------

test("the docs' worked example reproduces byte for byte", () => {
  const stringToSign = buildStringToSign(DOCS_EXAMPLE);
  const signature = sign(stringToSign, DOCS_EXAMPLE.secretKey);
  assert.equal(
    signature,
    DOCS_EXAMPLE.signature,
    "signature does not match the published vector — signing is wrong, do not build on it",
  );
});

test("the string to sign is five lines, every one terminated", () => {
  const s = buildStringToSign(DOCS_EXAMPLE);
  assert.equal(s.split("\n").length - 1, 5, "must be five newlines, not four");
  assert.ok(s.endsWith("\n"), "the last line ends in a newline too");
  assert.equal(
    s,
    "POST\n/order/v1/createOrder\n1625208260\nIZHEJYNIHYZIE8S0LLC0VWTPJVRRTO50\n" +
      '{"goodsId":100,"quantity":52,"createdTime":"2024-03-21 10:03:20"}\n',
  );
});

test("dropping the trailing newline changes the signature", () => {
  // Guards the mistake the header comment calls out: a four-newline string
  // still signs fine locally and fails with a generic 401 in production.
  const wrong = buildStringToSign(DOCS_EXAMPLE).replace(/\n$/, "");
  assert.notEqual(sign(wrong, DOCS_EXAMPLE.secretKey), DOCS_EXAMPLE.signature);
});

test("the method is uppercased and the body defaults to empty", () => {
  assert.equal(
    buildStringToSign({ method: "post", path: "/p", timestamp: "1", nonce: "n" }),
    "POST\n/p\n1\nn\n\n",
  );
});

test("a query string is part of the signed path", () => {
  // Java sample: path = encodedPath + "?" + encodedQuery. Sign what was sent.
  const s = buildStringToSign({ method: "GET", path: "/a/b?x=1&y=2", timestamp: "1", nonce: "n", body: "" });
  assert.ok(s.includes("/a/b?x=1&y=2\n"));
});

// ---------------------------------------------------------------------------
// Nonce and header
// ---------------------------------------------------------------------------

test("the nonce is 32 chars of [A-Za-z0-9]", () => {
  for (let i = 0; i < 200; i += 1) {
    const nonce = generateNonce();
    assert.equal(nonce.length, 32);
    assert.match(nonce, /^[A-Za-z0-9]{32}$/);
  }
});

test("the nonce covers the whole alphabet and does not repeat", () => {
  const seen = new Set();
  let chars = "";
  for (let i = 0; i < 500; i += 1) {
    const nonce = generateNonce();
    assert.ok(!seen.has(nonce), "nonces must not collide");
    seen.add(nonce);
    chars += nonce;
  }
  // A modulo bug that truncates the alphabet shows up as missing characters.
  assert.ok(new Set(chars).size >= 60, "expected nearly all 62 alphabet chars");
});

test("the Authorization header is one line in the documented order", () => {
  const header = buildAuthHeader({
    appId: DOCS_EXAMPLE.appId,
    accessKey: DOCS_EXAMPLE.accessKey,
    nonce: DOCS_EXAMPLE.nonce,
    timestamp: DOCS_EXAMPLE.timestamp,
    signature: DOCS_EXAMPLE.signature,
  });
  assert.equal(
    header,
    'JOP appid="293992070061998081",accesskey="b6713a535d56412f805afadd7e818455",' +
      'nonce="IZHEJYNIHYZIE8S0LLC0VWTPJVRRTO50",timestamp="1625208260",' +
      'signature="sygwKhKBkLwHVv0c7D+a/A7JTEJjGH/kLugFKh16918="',
  );
  assert.ok(!/[\r\n]/.test(header), "a wrapped header value is rejected by the platform");
});

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------

test("readCredentials parses KEY=value, skips comments, tolerates '=' in values", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "jlc-creds-"));
  const file = path.join(dir, "jlcpcb.env");
  fs.writeFileSync(
    file,
    "# a comment\n\nJLCPCB_ENDPOINT=https://open.jlcpcb.com\n  JLCPCB_ACCESS_KEY = abc \nODD=a=b=c\nnokey\n=novalue\n",
  );
  const creds = readCredentials(file);
  assert.equal(creds.JLCPCB_ENDPOINT, "https://open.jlcpcb.com");
  assert.equal(creds.JLCPCB_ACCESS_KEY, "abc");
  assert.equal(creds.ODD, "a=b=c");
  assert.ok(!("nokey" in creds));
  assert.ok(!("" in creds));
});

test("a missing credentials file reads as empty rather than throwing", () => {
  assert.deepEqual(readCredentials("/nonexistent/jlcpcb.env"), {});
});

test("credentialStatus names the missing field and never echoes a secret", () => {
  const status = credentialStatus({
    JLCPCB_ACCESS_KEY: "a",
    JLCPCB_SECRET_KEY: "s3cret",
  });
  assert.equal(status.configured, false);
  assert.deepEqual(status.missing, ["appId"]);
  assert.equal(status.endpoint, DEFAULT_ENDPOINT);
  assert.ok(!JSON.stringify(status).includes("s3cret"));
});

test("credentialStatus is configured only when all three are present", () => {
  const status = credentialStatus({
    JLCPCB_APP_ID: "1",
    JLCPCB_ACCESS_KEY: "a",
    JLCPCB_SECRET_KEY: "s",
    JLCPCB_ENDPOINT: "https://example.test/",
  });
  assert.equal(status.configured, true);
  assert.deepEqual(status.missing, []);
  assert.equal(status.endpoint, "https://example.test/");
});

test("clientFromCredentials accepts overrides and defaults the endpoint", () => {
  const client = clientFromCredentials({
    endpoint: undefined,
    appId: "1",
    accessKey: "a",
    secretKey: "s",
    fetchImpl: fakeFetch(),
  });
  assert.ok(client.endpoint.startsWith("https://"));
});

// ---------------------------------------------------------------------------
// request(): what goes out
// ---------------------------------------------------------------------------

test("the bytes signed are the bytes sent", async () => {
  const impl = fakeFetch();
  const client = fixedClient({ fetchImpl: impl });
  const body = { goodsId: 100, quantity: 52, createdTime: "2024-03-21 10:03:20" };
  await client.request("/order/v1/createOrder", body);

  const { url, init } = impl.calls[0];
  assert.equal(url, `${DEFAULT_ENDPOINT}/order/v1/createOrder`);
  assert.equal(init.headers["Content-Type"], "application/json");

  // Re-derive the signature from the body actually transmitted. If request()
  // ever serializes twice, key order or spacing can drift and this fails.
  const sent = init.body;
  const expected = sign(
    buildStringToSign({
      method: "POST",
      path: "/order/v1/createOrder",
      timestamp: "1625208260",
      nonce: DOCS_EXAMPLE.nonce,
      body: sent,
    }),
    TEST_CREDS.secretKey,
  );
  assert.ok(init.headers.Authorization.includes(`signature="${expected}"`));
});

test("a GET signs and sends an empty body", async () => {
  const impl = fakeFetch();
  await fixedClient({ fetchImpl: impl }).request("/pcb/v1/ping", {}, { method: "GET" });
  const { init } = impl.calls[0];
  assert.equal(init.method, "GET");
  assert.equal(init.body, undefined, "a GET must not carry a body");
  const expected = sign(
    buildStringToSign({ method: "GET", path: "/pcb/v1/ping", timestamp: "1625208260", nonce: DOCS_EXAMPLE.nonce, body: "" }),
    TEST_CREDS.secretKey,
  );
  assert.ok(init.headers.Authorization.includes(`signature="${expected}"`));
});

test("a trailing slash on the endpoint does not double up in the URL", async () => {
  const impl = fakeFetch();
  const client = fixedClient({ endpoint: "https://open.jlcpcb.com///", fetchImpl: impl });
  await client.request("/pcb/v1/x", {});
  assert.equal(impl.calls[0].url, "https://open.jlcpcb.com/pcb/v1/x");
});

test("each request gets a fresh nonce and a current timestamp", async () => {
  const impl = fakeFetch();
  const client = createJlcClient({ ...TEST_CREDS, fetchImpl: impl });
  await client.request("/a", {});
  await client.request("/a", {});
  const nonceOf = (i) => /nonce="([^"]+)"/.exec(impl.calls[i].init.headers.Authorization)[1];
  assert.notEqual(nonceOf(0), nonceOf(1));
  const ts = Number(/timestamp="(\d+)"/.exec(impl.calls[0].init.headers.Authorization)[1]);
  assert.ok(Math.abs(ts - Math.floor(Date.now() / 1000)) < 5, "timestamp must be Unix seconds, now");
});

test("incomplete credentials fail before any network call", async () => {
  const impl = fakeFetch();
  const client = createJlcClient({ accessKey: "a", secretKey: "s", fetchImpl: impl });
  await assert.rejects(() => client.request("/x", {}), (err) => {
    assert.ok(err instanceof JlcError);
    assert.match(err.message, /appId/);
    return true;
  });
  assert.equal(impl.calls.length, 0, "must not call out with incomplete credentials");
});

// ---------------------------------------------------------------------------
// request(): what comes back
// ---------------------------------------------------------------------------

test("a 200 carrying a business error is a failure, not a success", async () => {
  // The worst possible bug for an ordering integration: reporting "ordered"
  // on {"code":1001,"message":"Insufficient prepaid balance"}.
  const impl = fakeFetch({
    status: 200,
    body: { code: 1001, message: "Insufficient prepaid balance" },
    headers: { "J-Trace-ID": "trace-1001" },
  });
  await assert.rejects(() => fixedClient({ fetchImpl: impl }).request("/order/v1/createOrder", {}), (err) => {
    assert.equal(err.code, 1001);
    assert.equal(err.httpStatus, 200);
    assert.equal(err.traceId, "trace-1001");
    assert.equal(err.message, "Insufficient prepaid balance");
    return true;
  });
});

test("success:false is a failure even when the code looks fine", async () => {
  const impl = fakeFetch({ body: { code: 0, success: false, message: "nope" } });
  await assert.rejects(() => fixedClient({ fetchImpl: impl }).request("/x", {}), /nope/);
});

test("code 0, code 200 and an absent code all count as success", async () => {
  for (const body of [
    { code: 0, data: { a: 1 } },
    { code: 200, data: { a: 1 } },
    { data: { a: 1 } },
    { code: null, data: { a: 1 } },
    { success: true, data: { a: 1 } },
  ]) {
    const res = await fixedClient({ fetchImpl: fakeFetch({ body }) }).request("/x", {});
    assert.deepEqual(res.data, { a: 1 });
  }
});

test("a payload without a data envelope returns the whole body", async () => {
  const res = await fixedClient({ fetchImpl: fakeFetch({ body: { code: 0, fileId: "abc" } }) }).request("/x", {});
  assert.equal(res.data.fileId, "abc");
  assert.equal(res.raw.code, 0);
});

test("the live 401 shape — application not exists — surfaces code and trace id", async () => {
  // Exactly what open.jlcpcb.com returns today for every path, verified live
  // on 2026-08-11 while the appid is still missing.
  const impl = fakeFetch({
    status: 401,
    body: { code: 401, message: "application not exists" },
    headers: { "J-Trace-ID": "48b60c4e60ce4a34aa703afa4979023f" },
  });
  await assert.rejects(() => fixedClient({ fetchImpl: impl }).request("/order/v1/createOrder", {}), (err) => {
    assert.equal(err.code, 401);
    assert.equal(err.httpStatus, 401);
    assert.equal(err.traceId, "48b60c4e60ce4a34aa703afa4979023f");
    return true;
  });
});

test("HTML from the docs host is reported as a wrong-endpoint mistake", async () => {
  const impl = fakeFetch({ body: "<!doctype html><html>…</html>" });
  await assert.rejects(() => fixedClient({ fetchImpl: impl }).request("/x", {}), (err) => {
    assert.match(err.message, /non-JSON/);
    assert.match(err.message, /open\.jlcpcb\.com/);
    return true;
  });
});

test("an empty 200 body parses as an empty success", async () => {
  const res = await fixedClient({ fetchImpl: fakeFetch({ body: "" }) }).request("/x", {});
  assert.deepEqual(res.data, {});
});

test("a transport failure becomes a JlcError, not a raw TypeError", async () => {
  const impl = fakeFetch({ throws: new TypeError("fetch failed") });
  await assert.rejects(() => fixedClient({ fetchImpl: impl }).request("/x", {}), (err) => {
    assert.ok(err instanceof JlcError);
    assert.match(err.message, /could not reach JLCPCB/);
    return true;
  });
});

test("a response with no J-Trace-ID header still resolves", async () => {
  const res = await fixedClient({ fetchImpl: fakeFetch() }).request("/x", {});
  assert.equal(res.traceId, "");
});

test("the client never retries — one request in, one request out", async () => {
  // A create-order call that is retried after a timeout orders twice. The
  // transport must have no retry anywhere in it, on any failure shape.
  for (const opts of [
    { throws: new Error("ETIMEDOUT") },
    { status: 500, body: { code: 500, message: "server error" } },
    { status: 200, body: { code: 1001, message: "Insufficient prepaid balance" } },
  ]) {
    const impl = fakeFetch(opts);
    await assert.rejects(() => fixedClient({ fetchImpl: impl }).request("/order/v1/createOrder", {}));
    assert.equal(impl.calls.length, 1, `retried on ${JSON.stringify(opts.status ?? "throw")}`);
  }
});
