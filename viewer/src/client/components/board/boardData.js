// Pure models for the board workspace's data surfaces: the fab BOM csv, the
// parts.json lock, and the sidecar's validation warnings. DOM-free and
// transport-free so node:test covers them directly.

/**
 * Minimal CSV parser (quoted fields, embedded commas/quotes, CRLF). The fab
 * BOM is machine-written by circuitpy, so this stays deliberately small — no
 * multi-line-cell support.
 * @param {string} text
 * @returns {string[][]} rows of cells
 */
export function parseCsv(text) {
  const rows = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    if (!line.trim()) continue;
    const cells = [];
    let cell = "";
    let quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (quoted) {
        if (ch === '"' && line[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else if (ch === '"') {
          quoted = false;
        } else {
          cell += ch;
        }
      } else if (ch === '"') {
        quoted = true;
      } else if (ch === ",") {
        cells.push(cell);
        cell = "";
      } else {
        cell += ch;
      }
    }
    cells.push(cell);
    rows.push(cells);
  }
  return rows;
}

/** "C165948" → "165948"; tolerant of lowercase/whitespace; "" when absent. */
export function lcscNumber(value) {
  const match = /^\s*[Cc]\s*(\d+)\s*$/.exec(String(value || ""));
  return match ? match[1] : "";
}

/** JLCPCB part-detail URL for an LCSC number; "" when the number is absent. */
export function lcscUrl(value) {
  const nr = lcscNumber(value);
  return nr ? `https://jlcpcb.com/partdetail/C${nr}` : "";
}

/**
 * Parse the fab BOM csv (JLCPCB columns per contract §1:
 * `Comment,Designator,Footprint,LCSC Part #`) into row objects. Header
 * matching is case/space-insensitive; unknown columns are ignored. A
 * `Designator` cell may carry several refdes ("R1,R2") — kept verbatim.
 *
 * @param {string} text raw csv
 * @returns {Array<{comment: string, designator: string, footprint: string, lcsc: string}>}
 */
export function parseBomCsv(text) {
  const rows = parseCsv(text);
  if (!rows.length) return [];
  const header = rows[0].map((cell) => cell.trim().toLowerCase());
  const col = (name) => header.findIndex((h) => h.startsWith(name));
  const comment = col("comment");
  const designator = col("designator");
  const footprint = col("footprint");
  const lcsc = col("lcsc");
  return rows.slice(1).map((cells) => ({
    comment: String(cells[comment] ?? "").trim(),
    designator: String(cells[designator] ?? "").trim(),
    footprint: String(cells[footprint] ?? "").trim(),
    lcsc: String(cells[lcsc] ?? "").trim(),
  }));
}

/**
 * Normalize whatever shape parts.json carries into part cards. parts-book owns
 * the file wholly (contract §3): per part id — LCSC C-number, mfr, package,
 * basic/extended, stock + unit price + checked date, datasheet URL. Accepts
 * `{parts: [...]}` (the expected shape), a bare array, or an object map keyed
 * by part id; snake_case and camelCase field spellings both read. Exported for
 * tests.
 */
export function normalizeParts(data) {
  let list = [];
  if (Array.isArray(data)) {
    list = data;
  } else if (Array.isArray(data?.parts)) {
    list = data.parts;
  } else if (data && typeof data === "object") {
    // Object map {id: {…}} — including `{parts: {id: {…}}}`.
    const source =
      data.parts && typeof data.parts === "object" ? data.parts : data;
    list = Object.entries(source)
      .filter(([, value]) => value && typeof value === "object")
      .map(([id, value]) => ({ id, ...value }));
  }
  return list
    .map((part, index) => {
      const price = Number(
        part?.unitPriceUsd ?? part?.unit_price_usd ?? part?.price,
      );
      return {
        id: String(part?.id ?? `part_${index}`),
        lcsc: String(part?.lcsc ?? part?.lcscPart ?? part?.lcsc_part ?? "").trim(),
        mfr: String(part?.mfr ?? part?.manufacturer ?? "").trim(),
        pkg: String(part?.package ?? part?.pkg ?? part?.footprint ?? "").trim(),
        basic: part?.basic === true,
        stock: Number.isFinite(Number(part?.stock)) ? Number(part.stock) : null,
        unitPriceUsd: Number.isFinite(price) && price > 0 ? price : null,
        checked: String(part?.stockChecked ?? part?.stock_checked ?? part?.checked ?? "").trim(),
        datasheet: String(part?.datasheet ?? part?.datasheetUrl ?? part?.datasheet_url ?? "").trim(),
      };
    })
    .filter((part) => part.id);
}

/** Index normalized parts by their bare LCSC digits for BOM row joins. */
export function partsByLcsc(parts) {
  const map = new Map();
  for (const part of Array.isArray(parts) ? parts : []) {
    const nr = lcscNumber(part?.lcsc);
    if (nr && !map.has(nr)) map.set(nr, part);
  }
  return map;
}

// Severity is the contract's closed set; anything unrecognized reads as
// "warning" so a new producer can never make a finding invisible.
const SEVERITIES = new Set(["error", "warning", "info"]);

/**
 * Normalize the sidecar's `validation.warnings` (contract §1:
 * `{part, kind, detail, severity}`; kind open, severity closed) for the strip.
 * @param {object|null|undefined} sidecar parsed .board.json
 * @returns {Array<{part: string, kind: string, detail: string, severity: "error"|"warning"|"info"}>}
 */
export function normalizeWarnings(sidecar) {
  const list = Array.isArray(sidecar?.validation?.warnings)
    ? sidecar.validation.warnings
    : [];
  return list.map((warning) => {
    const severity = String(warning?.severity || "").toLowerCase();
    return {
      part: String(warning?.part ?? "").trim(),
      kind: String(warning?.kind ?? "").trim(),
      detail: String(warning?.detail ?? "").trim(),
      severity: SEVERITIES.has(severity) ? severity : "warning",
    };
  });
}

/**
 * The warnings that block a fab packet, for the not-fab-ready state: every
 * `error`-severity finding, plus `unverified_gerbers` (severity `warning` but
 * blocking-for-ship per contract §1 stage 5).
 */
export function blockingWarnings(sidecar) {
  return normalizeWarnings(sidecar).filter(
    (w) => w.severity === "error" || w.kind === "unverified_gerbers",
  );
}

/**
 * The chat note for a warning chip click — pre-fills the composer with
 * "U3.pin7 (source_trace_not_connected_error): " so the user types the fix
 * request against exactly that finding (contract §2).
 */
export function warningNoteText(warning) {
  const part = String(warning?.part || "").trim() || "board";
  const kind = String(warning?.kind || "").trim() || "warning";
  return `${part} (${kind}): `;
}

/**
 * The model-facing note for "Send to AI" — names the current tab and board so
 * the model maps the request onto the right artifact (rides the chat store's
 * `pendingViewContext`, never shown in the echoed bubble).
 * @param {{board?: string, tab?: string}} input
 * @returns {string} "" when there's no board to name
 */
export function buildViewContextNote({ board, tab } = {}) {
  const stem = String(board || "").trim();
  if (!stem) return "";
  const view = String(tab || "").trim().toLowerCase() || "board";
  return `[Viewer context: ${view} view of board ${stem}]`;
}
