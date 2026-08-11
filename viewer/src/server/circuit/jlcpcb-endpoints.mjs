/**
 * Every JLCPCB path the integration uses, in one table, each marked with how
 * we know it.
 *
 * ## These paths were found, not guessed
 *
 * The public docs name the operations but never their URLs, and the one path
 * that appears in the docs prose — `/order/v1/createOrder`, in the signature
 * worked example — **is not a live endpoint**. It is illustrative. Guessing
 * from it produces nothing that exists.
 *
 * The real surface came from `github.com/i2cjak/jlcpcb_api`, a Python client
 * reverse-engineered from JLC's own Java SDK jars, and every path below was
 * then checked against the live platform on 2026-08-11.
 *
 * ## How the platform tells you a path is real
 *
 * Once a valid appid signs the request, the two failures separate cleanly:
 *
 * | Response | Meaning |
 * |---|---|
 * | `401 API not exists` | no such path |
 * | `403 API insufficient permissions, access denied` | the path exists; the app lacks the scope |
 *
 * So a 403 is a *positive* result: it proves the path, the signature and the
 * credentials are all right and only the account's API scope is missing. Every
 * `confirmed` entry below earned that 403.
 *
 * ## Statuses
 *
 * - `confirmed` — the live platform distinguished it from a nonexistent path.
 * - `assumed` — inferred, never verified. An error on one says so.
 *
 * A path can be corrected without a code change; the credentials file may set
 * `JLCPCB_PATH_<KEY>` for any key in this table.
 */

/** @typedef {"confirmed"|"assumed"} PathStatus */

/** When the confirmed paths were checked against open.jlcpcb.com. */
export const CONFIRMED_ON = "2026-08-11";

/**
 * The operation table. `key` is how code refers to an endpoint; nothing else
 * in the codebase writes a path literal.
 *
 * Most calls are POST with a JSON body. `getSteelPriceConfig` is a GET, which
 * contradicts the docs' "only supported via the POST method" — the SDK and the
 * live platform both say GET, so the docs are wrong on that too.
 */
export const ENDPOINTS = {
  // --- PCB ---------------------------------------------------------------
  PCB_UPLOAD_GERBER: {
    path: "/overseas/openapi/pcb/uploadGerber",
    status: "confirmed",
    upload: true,
    summary: "Upload the gerber zip. `data` is the fileKey string used by every later call.",
  },
  PCB_AUDIT_GET: {
    path: "/overseas/openapi/pcb/audit/get",
    status: "confirmed",
    request: "{ key: fileKey, language: int }",
    summary:
      "Pre-review: JLC's own parser over an uploaded gerber — dimensions and a preview image.",
  },
  PCB_CALCULATE: {
    path: "/overseas/openapi/pcb/calculate",
    status: "confirmed",
    request: "{ orderType, pcbParam, fileKey, country, postCode, city, shippingMethod, achieveDate }",
    summary: "Online quote. Read-only; spends nothing.",
  },
  PCB_CREATE: {
    path: "/overseas/openapi/pcb/create",
    status: "confirmed-by-sdk",
    spendsMoney: true,
    request: "{ fileKey, orderType, pcbParam, shippingAddress, billingAddress, shippingMethod, … }",
    summary:
      "Place the order. Spends real money. Deliberately never probed — its existence comes from the SDK, not from a live call.",
  },
  PCB_ORDER_DETAIL: {
    path: "/overseas/openapi/pcb/order/detail",
    status: "confirmed",
    request: "{ batchNum }",
    summary: "Order information, shipping address and costs, by batch number.",
  },
  PCB_WIP_GET: {
    path: "/overseas/openapi/pcb/wip/get",
    status: "confirmed",
    request: "{ orderUUID }",
    summary: "Production progress. Only answers once the order is in production.",
  },
  PCB_IMPEDANCE_TEMPLATES: {
    path: "/overseas/openapi/pcb/getImpedanceTemplateSettingList",
    status: "confirmed",
    request: "{ stencilLayer, stencilPly, cuprumThickness, insideCuprumThickness, plateType, delamination }",
    summary: "Stack-up and impedance templates for a given construction.",
  },
  PCB_STEEL_PRICE_CONFIG: {
    path: "/overseas/openapi/pcb/getSteelPriceConfig",
    status: "confirmed",
    method: "GET",
    summary:
      "SMT stencil price config. No parameters, read-only — the cheapest end-to-end smoke test.",
  },
  PCB_UPLOAD_BLIND_VIA_IMG: {
    path: "/overseas/openapi/pcb/uploadBlindViaHoleImg",
    status: "confirmed",
    upload: true,
    summary: "Upload a blind/buried-via drawing, get an id for the order.",
  },

  // --- Components (LCSC catalogue) ---------------------------------------
  COMPONENT_DETAIL_BY_CODE: {
    path: "/overseas/openapi/component/getComponentDetailByCode",
    status: "confirmed",
    request: "{ componentCodes: [\"C25905\", …] }",
    summary: "Detail for a batch of C-numbers.",
  },
  COMPONENT_LIBRARY_LIST: {
    path: "/overseas/openapi/component/getComponentLibraryList",
    status: "confirmed",
    request: "{ currentPage, pageSize }",
    summary: "Paged public library listing.",
  },
  COMPONENT_PRIVATE_LIBRARY: {
    path: "/overseas/openapi/component/getPrivateComponentLibrary",
    status: "confirmed",
    request: "{ currentPage, pageSize }",
    summary: "The account's own consigned stock.",
  },
  COMPONENT_INFOS: {
    path: "/overseas/openapi/component/getComponentInfos",
    status: "confirmed",
    request: "{ lastKey }",
    summary: "Bulk component sync, cursor-paged by lastKey.",
  },
};

/**
 * JLC's API list names a balance endpoint ("JLC Balance → Get Available
 * Balance") that is absent from the SDK, so its path is unknown and it is not
 * in the table. `getSteelPriceConfig` serves as the smoke test instead.
 */
export const KNOWN_MISSING = ["JLC Balance / Get Available Balance — path unknown"];

/** Table key → credentials-file override key. */
export function overrideKeyFor(key) {
  return `JLCPCB_PATH_${key}`;
}

/** Resolve the table against credential-file overrides. */
export function resolveEndpoints(overrides = {}) {
  const out = {};
  for (const [key, entry] of Object.entries(ENDPOINTS)) {
    const override = overrides[overrideKeyFor(key)];
    out[key] = override
      ? { ...entry, path: override, status: "assumed", overridden: true }
      : { ...entry };
  }
  return out;
}

/** Look one up, or throw rather than send a request to `undefined`. */
export function endpoint(key, overrides = {}) {
  const entry = resolveEndpoints(overrides)[key];
  if (!entry) throw new Error(`unknown JLCPCB endpoint: ${key}`);
  return { key, ...entry };
}

/**
 * What to append to an error so the reader knows whether to suspect the path.
 *
 * A confirmed path adds nothing — it is not the problem. A 403 on any path
 * adds the one thing that actually fixes it, because "insufficient
 * permissions" reads like a credential fault and is not one.
 */
export function unverifiedPathNote(entry, err) {
  if (err?.code === 403) {
    return (
      " — the app is missing this API's scope. Enable the business type for" +
      " the application in the JLCPCB API Platform console; the credentials" +
      " and the signature are fine."
    );
  }
  if (!entry || entry.status?.startsWith("confirmed")) return "";
  return (
    ` (path ${entry.path} is assumed, not confirmed — see docs/jlcpcb-integration.md;` +
    ` override it with ${overrideKeyFor(entry.key ?? "…")}=…)`
  );
}

/** A table for humans: what we know, in one glance. */
export function endpointReport(overrides = {}) {
  return Object.entries(resolveEndpoints(overrides)).map(([key, entry]) => ({
    key,
    path: entry.path,
    method: entry.method || "POST",
    status: entry.status,
    overridden: Boolean(entry.overridden),
    spendsMoney: Boolean(entry.spendsMoney),
    summary: entry.summary,
  }));
}
