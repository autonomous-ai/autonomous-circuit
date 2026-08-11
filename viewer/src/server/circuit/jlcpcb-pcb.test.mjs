// Tests for the PCB operations, the endpoint table and the order gate.
//
// Two things are worth stating about what these can and cannot prove. The
// *paths* are checked against the live platform (see docs/jlcpcb-integration.md
// for the 403-vs-401 method) and are frozen here so a careless edit is caught.
// The *response* shapes are still unverified — no call has returned data yet —
// so the normalizer tests pin the fallback behaviour, not the real schema.
//
// The order-gate tests are the ones that matter. Placing an order spends real
// money, so every refusal it makes is tested explicitly, including the one
// that only happens after a timeout.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { JlcError } from "./jlcpcb.mjs";
import { ENDPOINTS, endpoint, endpointReport, resolveEndpoints } from "./jlcpcb-endpoints.mjs";
import {
  OrderGateError,
  assertOrderConfirmed,
  beginAttempt,
  canonicalJson,
  markFailed,
  markPlaced,
  markUnknown,
  orderFingerprint,
  readJournal,
  resolveUnknown,
} from "./jlcpcb-order-journal.mjs";
import {
  comparePreReview,
  createJlcPcb,
  normalizeOrder,
  normalizePreReview,
  normalizeQuote,
  normalizeUpload,
} from "./jlcpcb-pcb.mjs";

function tmpJournal() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "jlc-journal-")), "orders.jsonl");
}

/** A client stub that records calls and replays scripted answers. */
function stubClient(script = {}) {
  const calls = [];
  const answer = (kind, reqPath) => {
    const entry = script[reqPath];
    if (entry instanceof Error) throw entry;
    if (typeof entry === "function") return entry();
    return entry ?? { data: {}, raw: { code: 200, data: {} }, traceId: "t" };
  };
  return {
    calls,
    async request(reqPath, body, options) {
      calls.push({ kind: "request", path: reqPath, body, options });
      return answer("request", reqPath);
    },
    async requestUpload(reqPath, meta, options) {
      calls.push({ kind: "upload", path: reqPath, meta, options });
      return answer("upload", reqPath);
    },
  };
}

const P = {
  upload: "/overseas/openapi/pcb/uploadGerber",
  audit: "/overseas/openapi/pcb/audit/get",
  calculate: "/overseas/openapi/pcb/calculate",
  create: "/overseas/openapi/pcb/create",
  detail: "/overseas/openapi/pcb/order/detail",
  wip: "/overseas/openapi/pcb/wip/get",
  steel: "/overseas/openapi/pcb/getSteelPriceConfig",
};

// ---------------------------------------------------------------------------
// The endpoint table
// ---------------------------------------------------------------------------

test("the confirmed paths are the ones verified against the live platform", () => {
  // Frozen deliberately: these were found in JLC's SDK and each one answered
  // 403 (exists, no scope) rather than 401 (no such path) on 2026-08-11.
  // Changing one should require changing this line and re-verifying.
  assert.equal(ENDPOINTS.PCB_UPLOAD_GERBER.path, P.upload);
  assert.equal(ENDPOINTS.PCB_AUDIT_GET.path, P.audit);
  assert.equal(ENDPOINTS.PCB_CALCULATE.path, P.calculate);
  assert.equal(ENDPOINTS.PCB_CREATE.path, P.create);
  assert.equal(ENDPOINTS.PCB_ORDER_DETAIL.path, P.detail);
  assert.equal(ENDPOINTS.PCB_WIP_GET.path, P.wip);
  assert.equal(ENDPOINTS.PCB_STEEL_PRICE_CONFIG.path, P.steel);
});

test("no path in the table is one of the docs' illustrative ones", () => {
  // /order/v1/createOrder appears in JLC's own signature example and does not
  // exist. Nothing derived from that shape belongs here.
  for (const [key, entry] of Object.entries(ENDPOINTS)) {
    assert.ok(
      entry.path.startsWith("/overseas/openapi/"),
      `${key} does not look like a real JLC path: ${entry.path}`,
    );
  }
});

test("only the create path is flagged as spending money", () => {
  const spending = endpointReport().filter((e) => e.spendsMoney).map((e) => e.key);
  assert.deepEqual(spending, ["PCB_CREATE"]);
});

test("a credentials-file override replaces a path and drops it to assumed", () => {
  const entry = endpoint("PCB_AUDIT_GET", { JLCPCB_PATH_PCB_AUDIT_GET: "/new/path" });
  assert.equal(entry.path, "/new/path");
  assert.equal(entry.status, "assumed");
  assert.equal(entry.overridden, true);
  // An override must not leak into the untouched entries.
  assert.equal(resolveEndpoints({}).PCB_AUDIT_GET.path, P.audit);
});

test("an unknown endpoint key throws instead of building an undefined URL", () => {
  assert.throws(() => endpoint("NOPE"), /unknown JLCPCB endpoint/);
});

// ---------------------------------------------------------------------------
// Operations: what goes out
// ---------------------------------------------------------------------------

test("preReview posts the fileKey as `key` to the audit path", async () => {
  const client = stubClient({ [P.audit]: { data: { width: 30, length: 20 }, raw: {}, traceId: "t" } });
  const pcb = createJlcPcb({ client, pathOverrides: {} });
  const res = await pcb.preReview({ fileKey: "fk-1" });
  assert.deepEqual(client.calls[0].body, { key: "fk-1", language: 1 });
  assert.equal(client.calls[0].path, P.audit);
  assert.equal(res.width, 30);
});

test("preReview refuses without a fileKey rather than calling out", async () => {
  const client = stubClient();
  await assert.rejects(() => createJlcPcb({ client, pathOverrides: {} }).preReview({}), /fileKey/);
  assert.equal(client.calls.length, 0);
});

test("uploadGerber sends multipart with a zip content type", async () => {
  const client = stubClient({ [P.upload]: { data: "file-key-abc", raw: {}, traceId: "t" } });
  const pcb = createJlcPcb({ client, pathOverrides: {} });
  const res = await pcb.uploadGerber({ data: new Uint8Array([1, 2, 3]), fileName: "g.zip" });
  assert.equal(client.calls[0].kind, "upload");
  assert.equal(client.calls[0].options.contentType, "application/zip");
  assert.equal(res.fileKey, "file-key-abc");
});

test("reviewGerberZip uploads, pre-reviews and compares in one pass", async () => {
  const zipDir = fs.mkdtempSync(path.join(os.tmpdir(), "jlc-zip-"));
  const zip = path.join(zipDir, "gerbers.zip");
  fs.writeFileSync(zip, "not really a zip");
  const client = stubClient({
    [P.upload]: { data: "fk-9", raw: {}, traceId: "t" },
    [P.audit]: { data: { width: 30.1, length: 20.0, layer: 2 }, raw: {}, traceId: "t" },
  });
  const out = await createJlcPcb({ client, pathOverrides: {} }).reviewGerberZip(zip, {
    expected: { widthMm: 30, heightMm: 20, layers: 2 },
  });
  assert.equal(out.fileKey, "fk-9");
  assert.equal(out.comparison.agrees, true);
  assert.equal(client.calls[1].body.key, "fk-9");
});

test("reviewGerberZip stops when the upload returns no file key", async () => {
  const zipDir = fs.mkdtempSync(path.join(os.tmpdir(), "jlc-zip-"));
  const zip = path.join(zipDir, "gerbers.zip");
  fs.writeFileSync(zip, "x");
  const client = stubClient({ [P.upload]: { data: {}, raw: {}, traceId: "t" } });
  await assert.rejects(
    () => createJlcPcb({ client, pathOverrides: {} }).reviewGerberZip(zip),
    /no file key/,
  );
  assert.equal(client.calls.length, 1, "must not pre-review a key it does not have");
});

test("the smoke test uses the parameterless GET", async () => {
  const client = stubClient({ [P.steel]: { data: [], raw: {}, traceId: "t" } });
  await createJlcPcb({ client, pathOverrides: {} }).smokeTest();
  assert.equal(client.calls[0].path, P.steel);
  assert.equal(client.calls[0].options.method, "GET");
});

test("tracking calls send the field names JLC's SDK uses", async () => {
  const client = stubClient();
  const pcb = createJlcPcb({ client, pathOverrides: {} });
  await pcb.getOrderDetail("BATCH-1");
  await pcb.getProductionProgress("UUID-1");
  assert.deepEqual(client.calls[0].body, { batchNum: "BATCH-1" });
  assert.deepEqual(client.calls[1].body, { orderUUID: "UUID-1" });
});

test("a 403 is explained as missing scope, not as bad credentials", async () => {
  const denied = new JlcError("API insufficient permissions, access denied", {
    code: 403,
    httpStatus: 403,
  });
  const client = stubClient({ [P.audit]: denied });
  await assert.rejects(
    () => createJlcPcb({ client, pathOverrides: {} }).preReview({ fileKey: "k" }),
    (err) => {
      assert.match(err.message, /missing this API's scope/);
      assert.match(err.message, /console/);
      return true;
    },
  );
});

test("a failure on an overridden path says the path is a guess", async () => {
  const client = stubClient({ "/guess": new JlcError("boom", { code: 500, httpStatus: 500 }) });
  await assert.rejects(
    () =>
      createJlcPcb({ client, pathOverrides: { JLCPCB_PATH_PCB_AUDIT_GET: "/guess" } }).preReview({
        fileKey: "k",
      }),
    /assumed, not confirmed/,
  );
});

// ---------------------------------------------------------------------------
// Normalizers
// ---------------------------------------------------------------------------

test("normalizeUpload handles the bare-string file key the SDK describes", () => {
  assert.equal(normalizeUpload("abc123").fileKey, "abc123");
  assert.equal(normalizeUpload({ fileKey: "abc" }).fileKey, "abc");
  assert.equal(normalizeUpload({ fileStoreId: "sid" }).fileKey, "sid");
  assert.equal(normalizeUpload({}).fileKey, undefined);
});

test("normalizePreReview reports which key each value came from", () => {
  const out = normalizePreReview({ width: 30, length: 20, layer: 4, previewUrl: "u" });
  assert.deepEqual(
    { w: out.width, h: out.height, l: out.layers, p: out.previewImage },
    { w: 30, h: 20, l: 4, p: "u" },
  );
  assert.equal(out.foundKeys.height, "length");
  assert.equal(out.foundKeys.preview, "previewUrl");
});

test("normalizePreReview marks the millimetre default as an assumption", () => {
  assert.equal(normalizePreReview({ width: 1, length: 2 }).unitAssumed, true);
  const stated = normalizePreReview({ width: 1, length: 2, unit: "inch" });
  assert.equal(stated.unit, "inch");
  assert.equal(stated.unitAssumed, false);
});

test("the normalizers keep the raw body and never invent a value", () => {
  const raw = { unexpected: "shape" };
  assert.equal(normalizeQuote(raw).totalPrice, undefined);
  assert.deepEqual(normalizeQuote(raw).raw, raw);
  assert.equal(normalizeOrder(raw).orderUuid, undefined);
  assert.equal(normalizePreReview(null).width, undefined);
});

test("normalizeOrder prefers the identifier the tracking call needs", () => {
  const out = normalizeOrder({ orderUUID: "u-1", orderId: "ignored", batchNum: "b-1" });
  assert.equal(out.orderUuid, "u-1");
  assert.equal(out.batchNum, "b-1");
});

// ---------------------------------------------------------------------------
// comparePreReview — the fab's opinion against ours
// ---------------------------------------------------------------------------

test("a board JLC reads the same way agrees, whichever side it calls width", () => {
  const ok = comparePreReview({ width: 20, height: 30, unit: "mm" }, { widthMm: 30, heightMm: 20 });
  assert.equal(ok.agrees, true);
  assert.deepEqual(ok.notes, []);
});

test("a real size mismatch disagrees and says by how much", () => {
  const bad = comparePreReview({ width: 30, height: 25, unit: "mm" }, { widthMm: 30, heightMm: 20 });
  assert.equal(bad.agrees, false);
  assert.match(bad.notes[0], /5\.00mm/);
  assert.match(bad.notes[0], /different board/);
});

test("half a millimetre is tolerated; a hair over is not", () => {
  const at = comparePreReview({ width: 30.5, height: 20 }, { widthMm: 30, heightMm: 20 });
  assert.equal(at.agrees, true);
  const over = comparePreReview({ width: 30.51, height: 20 }, { widthMm: 30, heightMm: 20 });
  assert.equal(over.agrees, false);
});

test("a layer-count disagreement is reported alongside the size", () => {
  const out = comparePreReview({ width: 30, height: 20, layers: 4 }, { widthMm: 30, heightMm: 20, layers: 2 });
  assert.equal(out.agrees, true);
  assert.match(out.notes[0], /4 copper layers.*has 2/);
});

test("a comparison that cannot be made is null, never true", () => {
  // The dangerous bug would be reading "we could not check" as "it is fine".
  for (const out of [
    comparePreReview({ width: 30 }, { widthMm: 30, heightMm: 20 }),
    comparePreReview({ width: 30, height: 20 }, {}),
    comparePreReview({ width: 30, height: 20, unit: "inch" }, { widthMm: 30, heightMm: 20 }),
    comparePreReview(null, { widthMm: 30, heightMm: 20 }),
  ]) {
    assert.equal(out.agrees, null);
    assert.ok(out.notes.length > 0, "a skipped comparison must say why");
  }
});

// ---------------------------------------------------------------------------
// The order gate
// ---------------------------------------------------------------------------

const GOOD_ORDER = {
  payload: { fileKey: "fk", orderType: 1, pcbParam: { layer: 2, qty: 5 } },
  quantity: 5,
  totalPrice: 12.34,
  currency: "USD",
  confirmation: { confirmed: true, quantity: 5, totalPrice: 12.34, currency: "USD" },
};

test("a confirmation must name the quantity and the price", () => {
  assert.throws(() => assertOrderConfirmed({ ...GOOD_ORDER, confirmation: { confirmed: true } }), OrderGateError);
  assert.throws(() => assertOrderConfirmed({ ...GOOD_ORDER, confirmation: undefined }), /explicit confirmation/);
  // "truthy" is not "true" — a stray string must not confirm a purchase.
  assert.throws(
    () => assertOrderConfirmed({ ...GOOD_ORDER, confirmation: { ...GOOD_ORDER.confirmation, confirmed: "yes" } }),
    /explicit confirmation/,
  );
});

test("a confirmation naming the wrong number is refused", () => {
  const wrongQty = { ...GOOD_ORDER, confirmation: { ...GOOD_ORDER.confirmation, quantity: 10 } };
  assert.throws(() => assertOrderConfirmed(wrongQty), /confirmation is for 10 boards/);
  const wrongPrice = { ...GOOD_ORDER, confirmation: { ...GOOD_ORDER.confirmation, totalPrice: 1.0 } };
  assert.throws(() => assertOrderConfirmed(wrongPrice), /order total is 12.34/);
  const wrongCcy = { ...GOOD_ORDER, confirmation: { ...GOOD_ORDER.confirmation, currency: "EUR" } };
  assert.throws(() => assertOrderConfirmed(wrongCcy), /EUR/);
});

test("a confirmation that disagrees with the payload's own quantity is refused", () => {
  const drift = { ...GOOD_ORDER, payload: { pcbParam: { qty: 50 } } };
  assert.throws(() => assertOrderConfirmed(drift), /payload asks for 50 boards/);
});

test("nonsense quantities and prices are refused before anything else", () => {
  assert.throws(() => assertOrderConfirmed({ ...GOOD_ORDER, quantity: 0, confirmation: { confirmed: true, quantity: 0, totalPrice: 12.34 } }), /positive whole number/);
  assert.throws(() => assertOrderConfirmed({ ...GOOD_ORDER, quantity: 2.5, confirmation: { confirmed: true, quantity: 2.5, totalPrice: 12.34 } }), /positive whole number/);
  assert.throws(() => assertOrderConfirmed({ ...GOOD_ORDER, totalPrice: 0, confirmation: { confirmed: true, quantity: 5, totalPrice: 0 } }), /positive number/);
  assert.throws(() => assertOrderConfirmed({ ...GOOD_ORDER, currency: undefined }), /currency/);
});

test("prices compare to the cent, so float noise does not block an order", () => {
  const noisy = {
    ...GOOD_ORDER,
    totalPrice: 0.1 + 0.2,
    confirmation: { ...GOOD_ORDER.confirmation, totalPrice: 0.3 },
  };
  assert.deepEqual(assertOrderConfirmed(noisy).quantity, 5);
});

test("the fingerprint ignores key order but not the numbers", () => {
  assert.equal(canonicalJson({ b: 1, a: 2 }), '{"a":2,"b":1}');
  assert.equal(orderFingerprint({ a: 1, b: 2 }), orderFingerprint({ b: 2, a: 1 }));
  assert.notEqual(orderFingerprint({ qty: 5 }), orderFingerprint({ qty: 6 }));
});

test("placeOrder writes pending before the call and placed after it", async () => {
  const journalPath = tmpJournal();
  const client = stubClient({
    [P.create]: { data: { orderUUID: "u-1", batchNum: "b-1" }, raw: {}, traceId: "tr" },
  });
  const out = await createJlcPcb({ client, pathOverrides: {}, journalPath }).placeOrder(GOOD_ORDER);
  assert.equal(out.orderUuid, "u-1");
  const journal = readJournal(journalPath);
  assert.equal(journal.length, 1);
  assert.equal(journal[0].state, "placed");
  assert.equal(journal[0].batchNumber, "b-1");
  assert.equal(journal[0].totalPrice, 12.34);
});

test("placeOrder refuses an unconfirmed order without writing or calling", async () => {
  const journalPath = tmpJournal();
  const client = stubClient();
  await assert.rejects(
    () =>
      createJlcPcb({ client, pathOverrides: {}, journalPath }).placeOrder({
        ...GOOD_ORDER,
        confirmation: { confirmed: true },
      }),
    OrderGateError,
  );
  assert.equal(client.calls.length, 0);
  assert.deepEqual(readJournal(journalPath), []);
});

test("placeOrder refuses when the quote and the confirmed total disagree", async () => {
  const journalPath = tmpJournal();
  const client = stubClient();
  await assert.rejects(
    () =>
      createJlcPcb({ client, pathOverrides: {}, journalPath }).placeOrder({
        ...GOOD_ORDER,
        quote: { totalPrice: 99.0 },
      }),
    /quote says 99/,
  );
  assert.equal(client.calls.length, 0);
});

test("a rejected order is a clean failure and can be tried again", async () => {
  const journalPath = tmpJournal();
  const denied = new JlcError("Insufficient prepaid balance", { code: 1001, httpStatus: 200 });
  const client = stubClient({ [P.create]: denied });
  const pcb = createJlcPcb({ client, pathOverrides: {}, journalPath });
  await assert.rejects(() => pcb.placeOrder(GOOD_ORDER), /Insufficient prepaid balance/);
  assert.equal(readJournal(journalPath)[0].state, "failed");

  // The platform answered "no", so the order does not exist and a second
  // attempt is safe — the gate must not block it.
  const ok = stubClient({ [P.create]: { data: { orderUUID: "u-2" }, raw: {}, traceId: "t" } });
  const retried = await createJlcPcb({ client: ok, pathOverrides: {}, journalPath }).placeOrder(GOOD_ORDER);
  assert.equal(retried.orderUuid, "u-2");
});

test("a timeout is recorded as unknown and blocks the next identical order", async () => {
  // The case the whole journal exists for: the server may have taken the
  // order. Trying again would buy the boards twice.
  const journalPath = tmpJournal();
  const timeout = new JlcError("could not reach JLCPCB: ETIMEDOUT", { code: 0, httpStatus: 0 });
  const client = stubClient({ [P.create]: timeout });
  await assert.rejects(
    () => createJlcPcb({ client, pathOverrides: {}, journalPath }).placeOrder(GOOD_ORDER),
    (err) => {
      assert.match(err.message, /may or may not have been placed/);
      assert.ok(err.unresolvedAttemptId);
      return true;
    },
  );
  assert.equal(readJournal(journalPath)[0].state, "unknown");

  const second = stubClient({ [P.create]: { data: { orderUUID: "u-3" }, raw: {}, traceId: "t" } });
  await assert.rejects(
    () => createJlcPcb({ client: second, pathOverrides: {}, journalPath }).placeOrder(GOOD_ORDER),
    (err) => {
      assert.equal(err.reason, "unresolved_attempt");
      assert.match(err.message, /heck your JLCPCB order list/);
      return true;
    },
  );
  assert.equal(second.calls.length, 0, "a blocked order must never reach the network");
});

test("an already-placed order is blocked unless the duplicate is intended", async () => {
  const journalPath = tmpJournal();
  const client = stubClient({ [P.create]: { data: { orderUUID: "u-1", batchNum: "b-1" }, raw: {}, traceId: "t" } });
  const pcb = createJlcPcb({ client, pathOverrides: {}, journalPath });
  await pcb.placeOrder(GOOD_ORDER);
  await assert.rejects(() => pcb.placeOrder(GOOD_ORDER), (err) => {
    assert.equal(err.reason, "already_placed");
    assert.match(err.message, /batch b-1/);
    return true;
  });
  const again = await pcb.placeOrder({ ...GOOD_ORDER, allowDuplicate: true });
  assert.equal(again.orderUuid, "u-1");
});

test("a different order is never blocked by an unresolved one", async () => {
  const journalPath = tmpJournal();
  beginAttempt({ payload: { pcbParam: { qty: 5 } }, quantity: 5, totalPrice: 1, currency: "USD" }, journalPath);
  const other = beginAttempt(
    { payload: { pcbParam: { qty: 10 } }, quantity: 10, totalPrice: 2, currency: "USD" },
    journalPath,
  );
  assert.ok(other.attemptId);
});

test("an unknown attempt is cleared by a person, with a reason", () => {
  const journalPath = tmpJournal();
  const { attemptId } = beginAttempt(
    { payload: { pcbParam: { qty: 5 } }, quantity: 5, totalPrice: 1, currency: "USD" },
    journalPath,
  );
  markUnknown(attemptId, { message: "timeout" }, journalPath);
  assert.throws(() => resolveUnknown(attemptId, { state: "maybe" }, journalPath), /placed or failed/);
  resolveUnknown(attemptId, { state: "failed", reason: "not in my JLC order list" }, journalPath);
  const [record] = readJournal(journalPath);
  assert.equal(record.state, "failed");
  assert.equal(record.resolutionReason, "not in my JLC order list");
});

test("the journal survives a truncated line from a killed process", () => {
  const journalPath = tmpJournal();
  const { attemptId } = beginAttempt(
    { payload: { pcbParam: { qty: 5 } }, quantity: 5, totalPrice: 1, currency: "USD" },
    journalPath,
  );
  fs.appendFileSync(journalPath, '{"attemptId":"half-writ');
  markPlaced(attemptId, { batchNumber: "b-1" }, journalPath);
  const journal = readJournal(journalPath);
  assert.equal(journal.length, 1);
  assert.equal(journal[0].state, "placed");
});

test("a missing journal reads as empty rather than throwing", () => {
  assert.deepEqual(readJournal("/nonexistent/dir/orders.jsonl"), []);
});

test("the journal is written owner-only", () => {
  const journalPath = tmpJournal();
  beginAttempt({ payload: { a: 1 }, quantity: 1, totalPrice: 1, currency: "USD" }, journalPath);
  assert.equal(fs.statSync(journalPath).mode & 0o777, 0o600);
});

test("markFailed and markPlaced keep the earlier fields of the attempt", () => {
  const journalPath = tmpJournal();
  const { attemptId, fingerprint } = beginAttempt(
    { payload: { a: 1 }, quantity: 7, totalPrice: 3.5, currency: "USD", note: "hydrate v2" },
    journalPath,
  );
  markFailed(attemptId, { code: 1001, message: "no balance" }, journalPath);
  const [record] = readJournal(journalPath);
  assert.equal(record.fingerprint, fingerprint);
  assert.equal(record.quantity, 7);
  assert.equal(record.note, "hydrate v2");
  assert.equal(record.state, "failed");
});
