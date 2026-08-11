/**
 * JLCPCB Open Platform client — quote, order and track without leaving the app.
 *
 * Today the fab step is: download a gerber zip, open jlcpcb.com, upload it,
 * fill a form, and hope the board you verified is the board you ordered. This
 * closes that gap. It also buys something the verification work has been
 * approximating from the outside: `pcbPreReview` runs **JLC's own parser** over
 * our gerbers and hands back the dimensions and a preview image. That is the
 * fab's opinion of our output rather than ours, and it is free.
 *
 * ## Auth (docs: api.jlcpcb.com/docs/api-request-signature)
 *
 * HMAC-SHA256 over a five-line string, base64, in an `Authorization: JOP …`
 * header. Two details cause almost every signature failure and both are
 * handled here:
 *
 * 1. **Every line ends with `\n`, including the last one.** A four-newline
 *    string is the common mistake and fails with a generic 401.
 * 2. **The signed body must be byte-identical to the body sent.** So this
 *    module serializes once and signs and sends the same string — never
 *    re-serializes, because key order or spacing could differ and the
 *    signature would be over something the server never saw.
 *
 * The base URL is `https://open.jlcpcb.com`. `api.jlcpcb.com` is the docs site
 * only; pointing a client at it returns HTML and a confusing parse error.
 *
 * ## What "success" means
 *
 * HTTP 200 does **not** mean the call worked. The platform returns business
 * failures inside a 200 body as `{code, message, success:false}`. A client
 * that checks only the status code reports "ordered" on
 * `{"code":1001,"message":"Insufficient prepaid balance"}`, which for an
 * ordering integration is the worst possible bug. `request()` treats the body
 * as authoritative and throws `JlcError` on any non-success.
 *
 * ## No retries, ever
 *
 * Nothing in this module retries. A create-order call that times out after the
 * server accepted it would, on retry, buy the boards twice — and the timeout
 * looks identical either way. Callers that want a retry must decide that per
 * endpoint, with knowledge of whether it spends money; the transport will not
 * decide it for them. `jlcpcb-order-journal.mjs` records the intent before the
 * call so an ambiguous timeout is visible rather than silently repeated.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** Live API. The docs host (api.jlcpcb.com) is NOT an API host. */
export const DEFAULT_ENDPOINT = "https://open.jlcpcb.com";

/** Credentials live outside the repo so they cannot be committed by accident. */
export const CREDENTIALS_PATH = path.join(
  os.homedir(),
  ".config",
  "autonomous-circuit",
  "jlcpcb.env",
);

/** Nonce length the platform specifies: 32 chars of [A-Za-z0-9]. */
const NONCE_LENGTH = 32;
const NONCE_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

/** A business-level failure, or a transport one. Carries the platform's own
 * code so callers can branch on it (1001 = insufficient balance). */
export class JlcError extends Error {
  constructor(message, { code = 0, httpStatus = 0, traceId = "" } = {}) {
    super(message);
    this.name = "JlcError";
    this.code = code;
    this.httpStatus = httpStatus;
    /** The platform's per-request id, from the J-Trace-ID response header.
     * Support cannot find a request without it, so it rides on every error. */
    this.traceId = traceId;
  }
}

export function generateNonce(randomBytes = crypto.randomBytes) {
  const bytes = randomBytes(NONCE_LENGTH);
  let out = "";
  for (let i = 0; i < NONCE_LENGTH; i += 1) {
    out += NONCE_ALPHABET[bytes[i] % NONCE_ALPHABET.length];
  }
  return out;
}

/**
 * The five-line string to sign. Every line terminated, last one included.
 *
 * `path` must carry the query string when there is one — the server signs the
 * path it received, not the one without parameters.
 */
export function buildStringToSign({ method, path: reqPath, timestamp, nonce, body }) {
  return `${String(method).toUpperCase()}\n${reqPath}\n${timestamp}\n${nonce}\n${body ?? ""}\n`;
}

export function sign(stringToSign, secretKey) {
  return crypto
    .createHmac("sha256", secretKey)
    .update(stringToSign, "utf8")
    .digest("base64");
}

/**
 * The Authorization header value. Must be a single line — a wrapped header is
 * rejected, and the docs show it split only for readability.
 */
export function buildAuthHeader({
  appId,
  accessKey,
  nonce,
  timestamp,
  signature,
}) {
  return (
    `JOP appid="${appId}",accesskey="${accessKey}",` +
    `nonce="${nonce}",timestamp="${timestamp}",signature="${signature}"`
  );
}

/** Read `KEY=value` lines. Never logs values. Missing file → empty object. */
export function readCredentials(file = CREDENTIALS_PATH) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch {
    return {};
  }
  const out = {};
  for (const line of raw.split("\n")) {
    const text = line.trim();
    if (!text || text.startsWith("#")) continue;
    const eq = text.indexOf("=");
    if (eq <= 0) continue;
    out[text.slice(0, eq).trim()] = text.slice(eq + 1).trim();
  }
  return out;
}

/**
 * What the app can say about the integration without making a call.
 *
 * Deliberately reports *which* field is missing. "Not configured" sends
 * someone hunting through three keys; "appId is missing" does not. Never
 * returns the secret itself.
 */
export function credentialStatus(creds = readCredentials()) {
  const missing = [];
  if (!creds.JLCPCB_APP_ID) missing.push("appId");
  if (!creds.JLCPCB_ACCESS_KEY) missing.push("accessKey");
  if (!creds.JLCPCB_SECRET_KEY) missing.push("secretKey");
  return {
    configured: missing.length === 0,
    missing,
    endpoint: creds.JLCPCB_ENDPOINT || DEFAULT_ENDPOINT,
  };
}

/**
 * A signed client.
 *
 * `fetchImpl` and `now`/`nonceFn` are injectable so the signature can be
 * tested against the documented worked example without a network or a clock.
 */
export function createJlcClient({
  endpoint,
  appId,
  accessKey,
  secretKey,
  fetchImpl = globalThis.fetch,
  now = () => Math.floor(Date.now() / 1000),
  nonceFn = generateNonce,
} = {}) {
  const base = String(endpoint || DEFAULT_ENDPOINT).replace(/\/+$/, "");

  function assertCredentials() {
    if (appId && accessKey && secretKey) return;
    const { missing } = credentialStatus({
      JLCPCB_APP_ID: appId,
      JLCPCB_ACCESS_KEY: accessKey,
      JLCPCB_SECRET_KEY: secretKey,
    });
    throw new JlcError(
      `JLCPCB credentials incomplete: ${missing.join(", ")} not set`,
      { code: 0 },
    );
  }

  /** The Authorization header for one request, over `signedBody`. */
  function authorize(method, reqPath, signedBody) {
    const timestamp = String(now());
    const nonce = nonceFn();
    const signature = sign(
      buildStringToSign({ method, path: reqPath, timestamp, nonce, body: signedBody }),
      secretKey,
    );
    return buildAuthHeader({ appId, accessKey, nonce, timestamp, signature });
  }

  async function request(reqPath, body = {}, { method = "POST" } = {}) {
    assertCredentials();

    // Serialize exactly once. The bytes signed are the bytes sent.
    const payload = method === "GET" ? "" : JSON.stringify(body ?? {});
    const authorization = authorize(method, reqPath, payload);

    let response;
    try {
      response = await fetchImpl(`${base}${reqPath}`, {
        method,
        headers: { "Content-Type": "application/json", Authorization: authorization },
        ...(method === "GET" ? {} : { body: payload }),
      });
    } catch (cause) {
      throw new JlcError(`could not reach JLCPCB: ${cause?.message || cause}`, {
        code: 0,
      });
    }

    return readResponse(response);
  }

  /**
   * A multipart upload — the one place where the bytes signed are *not* the
   * bytes sent.
   *
   * JLC's rule (docs → API Request Signature, step 5): "For file uploads: Use
   * the meta JSON." So the signature covers the JSON description of the
   * upload, while the wire body is `multipart/form-data` carrying the file.
   *
   * The form layout is one `meta` part holding that exact JSON, plus one
   * `file` part — taken from JLC's Java SDK by way of the reverse-engineered
   * client at github.com/i2cjak/jlcpcb_api, not from the prose docs, which do
   * not describe it. `metaMode: "fields"` spreads the meta over one form field
   * per key instead, kept only as an escape hatch; the signature is identical
   * either way, so only the form layout changes.
   *
   * Content-Type is left to the runtime: it must include the multipart
   * boundary, and only `FormData` knows what that is.
   */
  async function requestUpload(
    reqPath,
    meta = {},
    {
      file,
      fileName = "upload.bin",
      fileField = "file",
      metaField = "meta",
      metaMode = "json",
      contentType = "application/octet-stream",
    } = {},
  ) {
    assertCredentials();
    if (!file) throw new JlcError("upload requires a file", { code: 0 });

    const signedBody = JSON.stringify(meta ?? {});
    const authorization = authorize("POST", reqPath, signedBody);

    const form = new FormData();
    const bytes = file instanceof Uint8Array ? file : new Uint8Array(file);
    form.append(fileField, new Blob([bytes], { type: contentType }), fileName);
    if (metaMode === "json") {
      form.append(metaField, signedBody);
    } else {
      for (const [key, value] of Object.entries(meta ?? {})) {
        if (value === undefined || value === null) continue;
        form.append(key, typeof value === "object" ? JSON.stringify(value) : String(value));
      }
    }

    let response;
    try {
      response = await fetchImpl(`${base}${reqPath}`, {
        method: "POST",
        headers: { Authorization: authorization },
        body: form,
      });
    } catch (cause) {
      throw new JlcError(`could not reach JLCPCB: ${cause?.message || cause}`, {
        code: 0,
      });
    }

    return readResponse(response);
  }

  async function readResponse(response) {
    const traceId = response.headers?.get?.("J-Trace-ID") || "";
    const text = await response.text();
    let parsed;
    try {
      parsed = text ? JSON.parse(text) : {};
    } catch {
      // Pointing at the docs host returns HTML; say so rather than surfacing
      // "Unexpected token <".
      throw new JlcError(
        `JLCPCB returned a non-JSON response (HTTP ${response.status}) — ` +
          `check the endpoint is ${DEFAULT_ENDPOINT}`,
        { httpStatus: response.status, traceId },
      );
    }

    // The body is authoritative, not the status. A 200 carrying
    // {"code":1001,"message":"Insufficient prepaid balance"} is a failure.
    const failed =
      parsed?.success === false ||
      (parsed?.code !== undefined &&
        parsed.code !== 0 &&
        parsed.code !== 200 &&
        parsed.code !== null);
    if (!response.ok || failed) {
      throw new JlcError(
        parsed?.message || `JLCPCB request failed (HTTP ${response.status})`,
        { code: Number(parsed?.code ?? 0), httpStatus: response.status, traceId },
      );
    }
    return { data: parsed?.data ?? parsed, raw: parsed, traceId };
  }

  return { request, requestUpload, endpoint: base };
}

/** Build a client from the on-disk credentials. */
export function clientFromCredentials(overrides = {}) {
  const creds = readCredentials();
  return createJlcClient({
    endpoint: creds.JLCPCB_ENDPOINT,
    appId: creds.JLCPCB_APP_ID,
    accessKey: creds.JLCPCB_ACCESS_KEY,
    secretKey: creds.JLCPCB_SECRET_KEY,
    ...overrides,
  });
}
