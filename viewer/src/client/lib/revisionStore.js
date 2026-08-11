// revisionStore — the durable half of the revision ring: the last N builds of
// each board, kept in IndexedDB so you can step back through them and actually
// see the old schematic and PCB rather than a thumbnail of one.
//
// Why the browser and not the disk: `<stem>.circuit.json` is rewritten in place
// on every build, and the pipeline owns that write. A server-side ring under
// `.circuit/revisions/` is the better long-term home (it survives a storage
// clear and it is shareable) and is written up in VIBE-NOTES §7 as a pipeline
// change. This is the version that ships today from the client alone.
//
// Size discipline: we store the circuit IR and the schematic SVG, gzipped
// through CompressionStream where the browser has it. A 2.7 MB circuit.json
// (the 134-part keyboard, our worst case) lands around 250 KB, so a full
// eight-deep ring for a board is a few megabytes rather than 25. We do NOT
// store the review PNGs — the PCB pane draws from the IR, so a raster of it
// would be dead weight.
//
// Every export fails soft. Private-mode Safari, a disabled-storage policy, or
// a quota rejection must degrade the pager to "latest only", never break the
// workspace.

const DB_NAME = "circuit-revisions";
const DB_VERSION = 1;
const STORE = "revisions";
const BOARD_INDEX = "board";

/** `${projectId}::${file}` — the ring a revision belongs to. */
export function boardKey(projectId, file) {
  return `${String(projectId || "")}::${String(file || "")}`;
}

/** `${boardKey}::${token}` — the primary key of one stored build. */
export function revisionKey(projectId, file, token) {
  return `${boardKey(projectId, file)}::${String(token || "")}`;
}

function indexedDbApi() {
  const api = globalThis.indexedDB;
  return api && typeof api.open === "function" ? api : null;
}

let dbPromise = null;

function openDb() {
  if (dbPromise) return dbPromise;
  const api = indexedDbApi();
  if (!api) return Promise.resolve(null);
  dbPromise = new Promise((resolve) => {
    let request;
    try {
      request = api.open(DB_NAME, DB_VERSION);
    } catch {
      resolve(null);
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "id" });
        store.createIndex(BOARD_INDEX, "board", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
  return dbPromise;
}

function runTransaction(mode, work) {
  return openDb().then(
    (db) =>
      new Promise((resolve) => {
        if (!db) {
          resolve(null);
          return;
        }
        let tx;
        try {
          tx = db.transaction(STORE, mode);
        } catch {
          resolve(null);
          return;
        }
        const store = tx.objectStore(STORE);
        let value = null;
        try {
          value = work(store);
        } catch {
          resolve(null);
          return;
        }
        // Resolve on the *transaction*, not the request: a put that succeeds
        // inside a transaction that later aborts on quota did not happen.
        tx.oncomplete = () => resolve(value && typeof value.then === "function" ? value : value);
        tx.onerror = () => resolve(null);
        tx.onabort = () => resolve(null);
      }),
  );
}

function requestValue(request) {
  return new Promise((resolve) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
  });
}

// --- compression -------------------------------------------------------------

function hasCompression() {
  return typeof globalThis.CompressionStream === "function" && typeof globalThis.Response === "function";
}

/** Text → a Blob, gzipped when the browser can. Shape is `{gzip, blob}`. */
export async function packText(text) {
  const body = String(text ?? "");
  if (!hasCompression()) return { gzip: false, blob: new Blob([body], { type: "application/json" }) };
  try {
    const stream = new Response(body).body.pipeThrough(new globalThis.CompressionStream("gzip"));
    return { gzip: true, blob: await new Response(stream).blob() };
  } catch {
    return { gzip: false, blob: new Blob([body], { type: "application/json" }) };
  }
}

/** The inverse of {@link packText}; "" when the payload cannot be read. */
export async function unpackText(payload) {
  if (!payload?.blob) return "";
  if (!payload.gzip) return payload.blob.text();
  try {
    const stream = payload.blob.stream().pipeThrough(new globalThis.DecompressionStream("gzip"));
    return await new Response(stream).text();
  } catch {
    return "";
  }
}

// --- public API ---------------------------------------------------------------

/**
 * The summaries of every stored build for one board, oldest first. Bodies are
 * left on disk — the pager renders from summaries alone and only pays for a
 * body when someone actually steps onto a revision.
 *
 * @returns {Promise<Array<{token: string, capturedAt: number, summary: object}>>}
 */
export async function listRevisions(projectId, file) {
  const key = boardKey(projectId, file);
  const rows = await runTransaction("readonly", (store) =>
    requestValue(store.index(BOARD_INDEX).getAll(key)),
  );
  const list = await rows;
  if (!Array.isArray(list)) return [];
  return list
    .map((row) => ({ token: row.token, capturedAt: Number(row.capturedAt) || 0, summary: row.summary || {} }))
    .sort((a, b) => a.capturedAt - b.capturedAt);
}

/**
 * Record one build. Idempotent on `token`: a board re-read writes nothing.
 * Prunes the ring to `limit` newest afterwards.
 *
 * @param {{projectId: string, file: string, token: string, capturedAt: number,
 *          summary: object, circuitText: string, sidecarText: string,
 *          schematicSvg: string}} input
 * @returns {Promise<boolean>} true when a new revision was written
 */
export async function saveRevision(input, limit = 8) {
  const { projectId, file, token } = input || {};
  if (!indexedDbApi() || !token || !file) return false;
  const id = revisionKey(projectId, file, token);

  const existing = await runTransaction("readonly", (store) => requestValue(store.get(id)));
  if (await existing) return false;

  const [circuit, sidecar, schematic] = await Promise.all([
    packText(input.circuitText),
    packText(input.sidecarText),
    packText(input.schematicSvg),
  ]);

  const written = await runTransaction("readwrite", (store) => {
    store.put({
      id,
      board: boardKey(projectId, file),
      token,
      capturedAt: Number(input.capturedAt) || Date.now(),
      summary: input.summary || {},
      circuit,
      sidecar,
      schematic,
    });
    return true;
  });
  if (!written) return false;
  await pruneBoard(projectId, file, limit);
  return true;
}

/** Drop everything past the `limit` newest builds of one board. */
export async function pruneBoard(projectId, file, limit = 8) {
  const key = boardKey(projectId, file);
  const rows = await runTransaction("readonly", (store) => requestValue(store.index(BOARD_INDEX).getAll(key)));
  const list = await rows;
  if (!Array.isArray(list) || list.length <= limit) return;
  const doomed = [...list]
    .sort((a, b) => (Number(a.capturedAt) || 0) - (Number(b.capturedAt) || 0))
    .slice(0, list.length - limit);
  await runTransaction("readwrite", (store) => {
    for (const row of doomed) store.delete(row.id);
    return true;
  });
}

/**
 * The full body of one stored build, decompressed and parsed.
 * @returns {Promise<{circuit: unknown, sidecar: object|null, schematicSvg: string}|null>}
 */
export async function loadRevision(projectId, file, token) {
  const row = await runTransaction("readonly", (store) =>
    requestValue(store.get(revisionKey(projectId, file, token))),
  );
  const record = await row;
  if (!record) return null;
  const [circuitText, sidecarText, schematicSvg] = await Promise.all([
    unpackText(record.circuit),
    unpackText(record.sidecar),
    unpackText(record.schematic),
  ]);
  const parse = (text) => {
    try {
      return text ? JSON.parse(text) : null;
    } catch {
      return null;
    }
  };
  return { circuit: parse(circuitText), sidecar: parse(sidecarText), schematicSvg };
}

/** Forget a board's history entirely (used when its project is deleted). */
export async function clearBoard(projectId, file) {
  const key = boardKey(projectId, file);
  const rows = await runTransaction("readonly", (store) => requestValue(store.index(BOARD_INDEX).getAll(key)));
  const list = await rows;
  if (!Array.isArray(list) || !list.length) return;
  await runTransaction("readwrite", (store) => {
    for (const row of list) store.delete(row.id);
    return true;
  });
}
