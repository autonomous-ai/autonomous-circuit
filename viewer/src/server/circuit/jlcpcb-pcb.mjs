/**
 * The PCB operations: pre-review, quote, order, track.
 *
 * ## The one that matters most is not the order button
 *
 * `preReview` (JLC's `pcb/audit/get`) runs **JLC's own gerber parser** over the
 * files we are about to send and hands back the board dimensions and a preview
 * image. Everything else in the verification stack is our opinion of our own
 * output; this is the fab's, for free, before any money moves.
 * `comparePreReview` turns it into a check: if JLC reads a different board size
 * than we drew, the export is wrong and a two-week round trip is about to be
 * wasted on it.
 *
 * ## Paths are confirmed; response shapes are not
 *
 * Every path here answered "exists" on the live platform (see
 * `jlcpcb-endpoints.mjs`), and the request field names come from JLC's own Java
 * SDK. What no source gives us is the *response* schema, because the account
 * still lacks API scope and no call has returned data yet.
 *
 * So the normalizers read a *set* of plausible field names, report which one
 * they matched in `foundKeys`, and always return the untouched `raw`. Nothing
 * downstream should assume a normalized field is present — check it, or read
 * `raw`. When the first real response arrives, `foundKeys` says whether the
 * guesses landed, and the fix is one array per field.
 */

import fs from "node:fs";
import path from "node:path";

import { JlcError, clientFromCredentials, readCredentials } from "./jlcpcb.mjs";
import { endpoint as lookupEndpoint, unverifiedPathNote } from "./jlcpcb-endpoints.mjs";
import {
  JOURNAL_PATH,
  assertOrderConfirmed,
  beginAttempt,
  markFailed,
  markPlaced,
  markUnknown,
} from "./jlcpcb-order-journal.mjs";

/** First present, non-empty value among `names`, with the key it came from. */
function pick(source, names) {
  if (!source || typeof source !== "object") return { value: undefined, key: null };
  for (const name of names) {
    const value = source[name];
    if (value !== undefined && value !== null && value !== "") return { value, key: name };
  }
  return { value: undefined, key: null };
}

function pickNumber(source, names) {
  const { value, key } = pick(source, names);
  const num = Number(value);
  return Number.isFinite(num) ? { value: num, key } : { value: undefined, key: null };
}

/**
 * The file key out of an upload response.
 *
 * The SDK types this call as returning a bare string, so `data` is expected to
 * *be* the key rather than an object wrapping it. Both shapes are handled —
 * the string case is what the SDK says, the object case costs nothing.
 */
export function normalizeUpload(data) {
  if (typeof data === "string" && data) {
    return { fileKey: data, fileKeyKey: "data", raw: data };
  }
  const { value, key } = pick(data, ["fileKey", "key", "fileId", "fileStoreId", "id", "uuid"]);
  return { fileKey: value === undefined ? undefined : String(value), fileKeyKey: key, raw: data };
}

/**
 * Board dimensions and a preview image out of a pre-review response.
 *
 * `width`/`length` lead the candidate lists because those are the names JLC
 * uses in the order payload (`PcbOrderCraftData.width`, `.length`), which is
 * the best evidence available for what it calls them coming back.
 *
 * Units are reported as JLC reports them. Nothing states the unit; JLC's web
 * quote is millimetres throughout, so `unit` defaults to "mm" and sets
 * `unitAssumed` so a caller can tell the difference between knowing and
 * assuming.
 */
export function normalizePreReview(data) {
  const width = pickNumber(data, ["width", "pcbWidth", "boardWidth", "widthMm", "x"]);
  const height = pickNumber(data, ["length", "height", "pcbLength", "boardHeight", "heightMm", "y"]);
  const layers = pickNumber(data, ["layer", "layers", "layerCount", "pcbLayer"]);
  const preview = pick(data, [
    "previewImage",
    "previewImageUrl",
    "previewUrl",
    "imageUrl",
    "topImage",
    "thumbnail",
    "pictureUrl",
  ]);
  const unit = pick(data, ["unit", "sizeUnit", "dimensionUnit"]);
  return {
    width: width.value,
    height: height.value,
    layers: layers.value,
    unit: unit.value ? String(unit.value) : "mm",
    unitAssumed: !unit.value,
    previewImage: preview.value ? String(preview.value) : undefined,
    foundKeys: {
      width: width.key,
      height: height.key,
      layers: layers.key,
      preview: preview.key,
      unit: unit.key,
    },
    raw: data,
  };
}

/**
 * Does the fab read the same board we drew?
 *
 * Dimensions are compared unordered — nobody agrees on which side is "width",
 * and a swapped pair is the same board. `toleranceMm` defaults to 0.5mm, which
 * absorbs how a parser treats the board-outline stroke width without hiding a
 * real mistake: a wrong outline layer, the wrong units, or a stray object
 * pushing the extents out all miss by far more than that.
 *
 * `agrees` is deliberately three-valued. `null` means the comparison could not
 * be made, and it must not read as agreement.
 */
export function comparePreReview(preReview, expected = {}, { toleranceMm = 0.5 } = {}) {
  const notes = [];
  const expectedPair = [Number(expected.widthMm), Number(expected.heightMm)]
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const actualPair = [preReview?.width, preReview?.height]
    .map(Number)
    .filter(Number.isFinite)
    .sort((a, b) => a - b);

  if (preReview?.unit && preReview.unit.toLowerCase() !== "mm") {
    notes.push(`JLC reported dimensions in ${preReview.unit}, not mm — comparison skipped`);
    return { agrees: null, notes, expectedPair, actualPair };
  }
  if (actualPair.length !== 2) {
    notes.push("JLC's response did not carry two dimensions — check `raw` and `foundKeys`");
    return { agrees: null, notes, expectedPair, actualPair };
  }
  if (expectedPair.length !== 2) {
    notes.push("no expected dimensions supplied — nothing to compare against");
    return { agrees: null, notes, expectedPair, actualPair };
  }

  const deltas = [
    Math.abs(actualPair[0] - expectedPair[0]),
    Math.abs(actualPair[1] - expectedPair[1]),
  ];
  const agrees = deltas.every((d) => d <= toleranceMm);
  if (!agrees) {
    notes.push(
      `JLC parses this board as ${actualPair[0]}×${actualPair[1]}mm; we drew ` +
        `${expectedPair[0]}×${expectedPair[1]}mm (off by ${deltas[0].toFixed(2)}mm and ` +
        `${deltas[1].toFixed(2)}mm). The gerbers describe a different board than the design does.`,
    );
  }
  if (
    Number.isFinite(expected.layers) &&
    Number.isFinite(preReview?.layers) &&
    expected.layers !== preReview.layers
  ) {
    notes.push(`JLC reads ${preReview.layers} copper layers; the design has ${expected.layers}`);
  }
  return { agrees, notes, expectedPair, actualPair, deltas, toleranceMm };
}

/** Total, currency and lead time out of a quote response. */
export function normalizeQuote(data) {
  const total = pickNumber(data, [
    "totalPrice",
    "totalAmount",
    "amount",
    "orderPrice",
    "price",
    "total",
  ]);
  const currency = pick(data, ["currency", "currencyCode", "moneyType"]);
  const lead = pick(data, ["buildTime", "leadTime", "productionTime", "deliveryTime", "achieveDate"]);
  return {
    totalPrice: total.value,
    currency: currency.value ? String(currency.value) : undefined,
    leadTime: lead.value === undefined ? undefined : String(lead.value),
    foundKeys: { totalPrice: total.key, currency: currency.key, leadTime: lead.key },
    raw: data,
  };
}

/**
 * Order identifiers out of a create-order response.
 *
 * `orderUUID` leads because that is the field `pcb/wip/get` asks for, so it is
 * the one that has to survive: without it there is no way to track what was
 * just bought.
 */
export function normalizeOrder(data) {
  const orderUuid = pick(data, ["orderUUID", "orderUuid", "orderId", "id"]);
  const batch = pick(data, ["batchNum", "batchNumber", "batchNo", "batchId"]);
  const type = pick(data, ["orderType", "type"]);
  return {
    orderUuid: orderUuid.value === undefined ? undefined : String(orderUuid.value),
    batchNum: batch.value === undefined ? undefined : String(batch.value),
    orderType: type.value === undefined ? undefined : String(type.value),
    foundKeys: { orderUuid: orderUuid.key, batchNum: batch.key, orderType: type.key },
    raw: data,
  };
}

/**
 * The operations, bound to a signed client.
 *
 * `client` is injectable so every operation is testable without a network, and
 * `journalPath` so order-gate tests never touch the real journal.
 */
export function createJlcPcb({
  client = clientFromCredentials(),
  pathOverrides = readCredentials(),
  journalPath = JOURNAL_PATH,
} = {}) {
  const entryFor = (key) => lookupEndpoint(key, pathOverrides);

  /** Call an endpoint by table key, decorating failures with what they mean. */
  async function call(key, body, options) {
    const entry = entryFor(key);
    try {
      return await client.request(entry.path, body, { method: entry.method || "POST", ...options });
    } catch (err) {
      if (err instanceof JlcError) err.message += unverifiedPathNote(entry, err);
      throw err;
    }
  }

  async function upload(key, meta, options) {
    const entry = entryFor(key);
    try {
      return await client.requestUpload(entry.path, meta, options);
    } catch (err) {
      if (err instanceof JlcError) err.message += unverifiedPathNote(entry, err);
      throw err;
    }
  }

  return {
    entryFor,

    /**
     * The cheapest end-to-end check: a GET with no parameters that spends
     * nothing and uploads nothing. Run this first when credentials or scope
     * change — it proves signing, routing and permission in one call.
     */
    async smokeTest() {
      const res = await call("PCB_STEEL_PRICE_CONFIG", {});
      return { ok: true, data: res.data, traceId: res.traceId };
    },

    /** Upload gerber bytes, get the fileKey every later call needs. */
    async uploadGerber({ data, fileName = "gerbers.zip", meta = {} } = {}) {
      if (!data) throw new JlcError("uploadGerber needs the zip bytes", { code: 0 });
      const res = await upload("PCB_UPLOAD_GERBER", meta, {
        file: data,
        fileName,
        contentType: "application/zip",
      });
      return { ...normalizeUpload(res.data), traceId: res.traceId };
    },

    /** Same, from a path on disk. */
    async uploadGerberFile(zipPath, options = {}) {
      const bytes = fs.readFileSync(zipPath);
      return this.uploadGerber({ data: bytes, fileName: path.basename(zipPath), ...options });
    },

    /**
     * JLC's parse of an uploaded gerber: dimensions and a preview image.
     *
     * `key` is the fileKey from the upload. `language` is an integer in the
     * SDK with no documented values; 1 is a guess and the server may ignore
     * it — it should not affect the dimensions either way.
     */
    async preReview({ fileKey, language = 1 } = {}) {
      if (!fileKey) throw new JlcError("preReview needs the gerber fileKey", { code: 0 });
      const res = await call("PCB_AUDIT_GET", { key: fileKey, language });
      return { ...normalizePreReview(res.data), traceId: res.traceId };
    },

    /**
     * Upload a gerber zip and immediately ask JLC what it sees, optionally
     * checking the answer against the dimensions we believe we drew.
     *
     * This is the whole point of the integration for design work: it costs
     * nothing, spends nothing, and catches an export that describes the wrong
     * board before a two-week fab cycle does.
     */
    async reviewGerberZip(zipPath, { expected, toleranceMm, language } = {}) {
      const uploaded = await this.uploadGerberFile(zipPath);
      if (!uploaded.fileKey) {
        throw new JlcError(
          "the upload succeeded but no file key was found in the response — see `raw`",
          { code: 0 },
        );
      }
      const preReview = await this.preReview({ fileKey: uploaded.fileKey, language });
      const comparison = expected ? comparePreReview(preReview, expected, { toleranceMm }) : null;
      return { fileKey: uploaded.fileKey, preReview, comparison };
    },

    /** Stack-up and impedance templates for a given construction. */
    async getImpedanceTemplates(spec = {}) {
      const res = await call("PCB_IMPEDANCE_TEMPLATES", spec);
      return { templates: res.data, raw: res.raw, traceId: res.traceId };
    },

    /** Price a build. Read-only: a quote never spends anything. */
    async quote(spec = {}) {
      const res = await call("PCB_CALCULATE", spec);
      return { ...normalizeQuote(res.data), traceId: res.traceId };
    },

    /**
     * Place the order. **This spends real money and cannot be undone.**
     *
     * Four things must hold before a request goes out, and all four are
     * enforced here rather than trusted to the caller:
     *
     * 1. A confirmation that names the quantity and the total price, matching
     *    the payload. A bare "yes" is refused.
     * 2. No identical order already in flight, already placed, or last seen
     *    with an unknown outcome.
     * 3. The attempt is journalled *before* the call, so a lost answer is
     *    recorded rather than forgotten.
     * 4. No retry, ever. A timeout resolves to `unknown` and blocks the next
     *    attempt until a human has checked their JLCPCB account.
     *
     * The create payload carries no price — JLC prices it server-side from the
     * same options the quote used — so pass the `quote` this order came from
     * and the total is checked against it rather than against nothing.
     */
    async placeOrder({
      payload,
      quantity,
      totalPrice,
      currency,
      confirmation,
      quote = null,
      note = "",
      allowDuplicate = false,
    } = {}) {
      if (!payload || typeof payload !== "object") {
        throw new JlcError("placeOrder needs the order payload", { code: 0 });
      }
      if (quote && Number.isFinite(Number(quote.totalPrice))) {
        if (Math.abs(Number(quote.totalPrice) - Number(totalPrice)) > 0.005) {
          throw new JlcError(
            `the quote says ${quote.totalPrice} but the order is being confirmed at ${totalPrice}`,
            { code: 0 },
          );
        }
      }
      const confirmed = assertOrderConfirmed({
        payload,
        quantity,
        totalPrice,
        currency,
        confirmation,
      });

      const { attemptId, fingerprint } = beginAttempt(
        { payload, ...confirmed, note, allowDuplicate },
        journalPath,
      );

      let res;
      try {
        res = await call("PCB_CREATE", payload);
      } catch (err) {
        // Did the platform answer? If it did, the order definitively did not
        // happen and the attempt is a clean failure. If it did not — a
        // timeout, a dropped socket — the order may exist and only a person
        // can say which. That distinction is the whole reason for the journal.
        const answered = err instanceof JlcError && err.httpStatus > 0;
        if (answered) {
          markFailed(
            attemptId,
            { code: err.code, message: err.message, traceId: err.traceId },
            journalPath,
          );
        } else {
          markUnknown(attemptId, { message: err?.message, traceId: err?.traceId }, journalPath);
          err.message =
            `${err.message} — the order may or may not have been placed. It is recorded as ` +
            `unresolved (attempt ${attemptId}); check your JLCPCB order list before retrying.`;
          err.unresolvedAttemptId = attemptId;
        }
        throw err;
      }

      const order = normalizeOrder(res.data);
      markPlaced(
        attemptId,
        {
          orderUuid: order.orderUuid,
          batchNumber: order.batchNum,
          traceId: res.traceId,
          raw: res.raw,
        },
        journalPath,
      );
      return { ...order, attemptId, fingerprint, traceId: res.traceId };
    },

    /** Order information, shipping address and costs, by batch number. */
    async getOrderDetail(batchNum) {
      if (!batchNum) throw new JlcError("getOrderDetail needs a batch number", { code: 0 });
      const res = await call("PCB_ORDER_DETAIL", { batchNum });
      return { order: res.data, raw: res.raw, traceId: res.traceId };
    },

    /** Production progress. The docs note it only answers once in production. */
    async getProductionProgress(orderUUID) {
      if (!orderUUID) throw new JlcError("getProductionProgress needs an order UUID", { code: 0 });
      const res = await call("PCB_WIP_GET", { orderUUID });
      return { progress: res.data, raw: res.raw, traceId: res.traceId };
    },

    /** Detail for a batch of LCSC C-numbers. */
    async getComponentDetails(componentCodes = []) {
      const res = await call("COMPONENT_DETAIL_BY_CODE", { componentCodes });
      return { components: res.data, raw: res.raw, traceId: res.traceId };
    },
  };
}
