/**
 * The safety layer around the one call that spends money.
 *
 * `createOrder` is irreversible: it buys boards. Two failure modes matter and
 * neither is solved by careful calling code.
 *
 * **A timeout is ambiguous.** If the connection drops after the server
 * accepted the order, the client sees exactly what it sees when the order was
 * never received. Retrying buys the boards twice. So nothing retries, and
 * every attempt is written to an append-only journal *before* the request goes
 * out. An attempt that never got an answer is recorded as `unknown`, and a
 * later attempt to place the same order refuses until a human has looked at
 * their JLCPCB account and resolved it.
 *
 * **A confirmation has to name what is being bought.** A boolean "yes" is not
 * a confirmation; someone can click it without knowing the number. `assertOrderConfirmed`
 * requires the quantity and the total price to be repeated back and to match
 * the payload, so a UI cannot confirm an order whose price it never showed.
 *
 * The journal lives beside the credentials, outside the repo — it contains
 * order history, not secrets, but it is per-machine state and does not belong
 * in git.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const JOURNAL_PATH = path.join(
  os.homedir(),
  ".config",
  "autonomous-circuit",
  "jlcpcb-orders.jsonl",
);

/** States an attempt can be in. `unknown` is the one that blocks. */
export const ORDER_STATES = ["pending", "placed", "failed", "unknown"];

export class OrderGateError extends Error {
  constructor(message, { reason = "rejected", records = [] } = {}) {
    super(message);
    this.name = "OrderGateError";
    this.reason = reason;
    this.records = records;
  }
}

/** Stable JSON: same payload, same string, regardless of key order. */
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(",")}}`;
}

/**
 * Identity of an order attempt: the payload, not the wall clock. Two calls
 * with the same payload are the same purchase and must not both go out by
 * accident.
 */
export function orderFingerprint(payload) {
  return crypto.createHash("sha256").update(canonicalJson(payload)).digest("hex").slice(0, 32);
}

function readLines(file) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  const out = [];
  for (const line of raw.split("\n")) {
    const text = line.trim();
    if (!text) continue;
    try {
      out.push(JSON.parse(text));
    } catch {
      // A half-written line from a killed process must not hide the rest of
      // the history — skip it and keep reading.
    }
  }
  return out;
}

function append(file, record) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // If a previous process died mid-write the file ends without a newline, and
  // appending straight onto it would fuse the two into one unparseable line —
  // losing the *new* record as well as the broken one. In this journal a lost
  // record is a lost order, so close the line first.
  let needsNewline = false;
  try {
    const { size } = fs.statSync(file);
    if (size > 0) {
      const handle = fs.openSync(file, "r");
      try {
        const tail = Buffer.alloc(1);
        fs.readSync(handle, tail, 0, 1, size - 1);
        needsNewline = tail[0] !== 0x0a;
      } finally {
        fs.closeSync(handle);
      }
    }
  } catch {
    // No file yet; nothing to close.
  }
  fs.appendFileSync(file, `${needsNewline ? "\n" : ""}${JSON.stringify(record)}\n`, { mode: 0o600 });
  return record;
}

/** Every attempt, collapsed to its latest state. */
export function readJournal(file = JOURNAL_PATH) {
  const byId = new Map();
  for (const event of readLines(file)) {
    if (!event?.attemptId) continue;
    byId.set(event.attemptId, { ...(byId.get(event.attemptId) || {}), ...event });
  }
  return [...byId.values()];
}

/**
 * Attempts that stop a new order for the same payload: one still in flight,
 * one whose outcome was never learned, or one that already succeeded.
 */
export function blockingAttempts(fingerprint, file = JOURNAL_PATH) {
  return readJournal(file).filter(
    (r) => r.fingerprint === fingerprint && ["pending", "unknown", "placed"].includes(r.state),
  );
}

/**
 * Check the confirmation before anything is written or sent.
 *
 * `confirmation` must name the quantity and the total price, and both must
 * match what is about to be bought. Passing `{confirmed:true}` alone is
 * rejected: the point is that the number was seen.
 */
export function assertOrderConfirmed({ payload = {}, quantity, totalPrice, currency, confirmation } = {}) {
  if (!confirmation || confirmation.confirmed !== true) {
    throw new OrderGateError(
      "an order needs an explicit confirmation naming the quantity and the total price",
      { reason: "not_confirmed" },
    );
  }
  const qty = Number(quantity);
  const price = Number(totalPrice);
  if (!Number.isInteger(qty) || qty <= 0) {
    throw new OrderGateError(`order quantity must be a positive whole number, got ${quantity}`, {
      reason: "bad_quantity",
    });
  }
  if (!Number.isFinite(price) || price <= 0) {
    throw new OrderGateError(`order total must be a positive number, got ${totalPrice}`, {
      reason: "bad_price",
    });
  }
  if (!currency) {
    throw new OrderGateError("order total must name a currency", { reason: "bad_currency" });
  }
  if (Number(confirmation.quantity) !== qty) {
    throw new OrderGateError(
      `confirmation is for ${confirmation.quantity} boards but the order is for ${qty}`,
      { reason: "quantity_mismatch" },
    );
  }
  // Money compares to the cent; floats do not compare exactly.
  if (Math.abs(Number(confirmation.totalPrice) - price) > 0.005) {
    throw new OrderGateError(
      `confirmation is for ${confirmation.totalPrice} but the order total is ${price}`,
      { reason: "price_mismatch" },
    );
  }
  if (confirmation.currency && String(confirmation.currency) !== String(currency)) {
    throw new OrderGateError(
      `confirmation is in ${confirmation.currency} but the order is in ${currency}`,
      { reason: "currency_mismatch" },
    );
  }

  // The payload is what the platform actually builds, so where it carries the
  // same numbers they have to agree with the ones confirmed. JLC's create
  // payload puts the board count at `pcbParam.qty`; the flatter names are
  // accepted too so a caller using a different shape is still checked.
  const payloadQty = payload?.pcbParam?.qty ?? payload.qty ?? payload.quantity;
  if (payloadQty !== undefined && Number(payloadQty) !== qty) {
    throw new OrderGateError(
      `the order payload asks for ${payloadQty} boards, not the ${qty} confirmed`,
      { reason: "payload_quantity_mismatch" },
    );
  }
  // JLC's create payload carries no price — the platform prices it server-side
  // — so this only fires for callers that do send one.
  const payloadTotal = payload.amount ?? payload.totalAmount ?? payload.totalPrice;
  if (payloadTotal !== undefined && Math.abs(Number(payloadTotal) - price) > 0.005) {
    throw new OrderGateError(
      `the order payload totals ${payloadTotal}, not the ${price} confirmed`,
      { reason: "payload_price_mismatch" },
    );
  }
  return { quantity: qty, totalPrice: price, currency: String(currency) };
}

/**
 * Record the intent to order, and refuse if this exact order is already in
 * flight, already placed, or last seen in an unknown state.
 */
export function beginAttempt(
  { payload, quantity, totalPrice, currency, note = "", allowDuplicate = false },
  file = JOURNAL_PATH,
) {
  const fingerprint = orderFingerprint(payload);
  const blocking = blockingAttempts(fingerprint, file);
  if (blocking.length && !allowDuplicate) {
    const unknown = blocking.find((r) => r.state === "unknown");
    if (unknown) {
      throw new OrderGateError(
        `an identical order was attempted at ${unknown.startedAt} and never returned an answer ` +
          `(trace ${unknown.traceId || "none"}). Check your JLCPCB order list before trying again — ` +
          "it may already exist.",
        { reason: "unresolved_attempt", records: blocking },
      );
    }
    const placed = blocking.find((r) => r.state === "placed");
    if (placed) {
      throw new OrderGateError(
        `an identical order was already placed at ${placed.finishedAt} ` +
          `(batch ${placed.batchNumber || "?"}). Pass allowDuplicate to order it again.`,
        { reason: "already_placed", records: blocking },
      );
    }
    throw new OrderGateError("an identical order is already in flight", {
      reason: "in_flight",
      records: blocking,
    });
  }

  const attemptId = crypto.randomUUID();
  append(file, {
    attemptId,
    fingerprint,
    state: "pending",
    startedAt: new Date().toISOString(),
    quantity,
    totalPrice,
    currency,
    note,
  });
  return { attemptId, fingerprint };
}

export function markPlaced(attemptId, { orderId, batchNumber, traceId, raw } = {}, file = JOURNAL_PATH) {
  return append(file, {
    attemptId,
    state: "placed",
    finishedAt: new Date().toISOString(),
    orderId,
    batchNumber,
    traceId,
    raw,
  });
}

/** The platform answered, and the answer was no. Safe to try again. */
export function markFailed(attemptId, { code, message, traceId } = {}, file = JOURNAL_PATH) {
  return append(file, {
    attemptId,
    state: "failed",
    finishedAt: new Date().toISOString(),
    code,
    message,
    traceId,
  });
}

/**
 * No answer arrived. The order may or may not exist. This is the state that
 * blocks a repeat, and it is cleared by a human, not by code.
 */
export function markUnknown(attemptId, { message, traceId } = {}, file = JOURNAL_PATH) {
  return append(file, {
    attemptId,
    state: "unknown",
    finishedAt: new Date().toISOString(),
    message,
    traceId,
  });
}

/**
 * A person checked their JLCPCB account and knows what happened. Resolving is
 * deliberately an explicit act with a reason attached.
 */
export function resolveUnknown(attemptId, { state, reason = "", batchNumber } = {}, file = JOURNAL_PATH) {
  if (!["placed", "failed"].includes(state)) {
    throw new OrderGateError(`an unknown attempt resolves to placed or failed, not ${state}`, {
      reason: "bad_resolution",
    });
  }
  return append(file, {
    attemptId,
    state,
    resolvedAt: new Date().toISOString(),
    resolutionReason: reason,
    ...(batchNumber ? { batchNumber } : {}),
  });
}
