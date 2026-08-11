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
        // How many places this part is used. parts-book writes the refdes list
        // per part; without it a BOM cost is a sum of unit prices, which is a
        // different (and wrong) number.
        refdes: Array.isArray(part?.refdes) ? part.refdes.map((value) => String(value)).filter(Boolean) : [],
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
 *
 * When something is selected in the workspace the note names it too, in the
 * model's own vocabulary ("component R20", "net GND"): the user pointing at a
 * part on screen and then typing "make this bigger" should not have to say
 * which part. `selection` and `index` are optional — the base note is unchanged
 * without them.
 *
 * @param {{board?: string, tab?: string, selection?: object|null, index?: object|null}} input
 * @returns {string} "" when there's no board to name
 */
export function buildViewContextNote({ board, tab, selection, index } = {}) {
  const stem = String(board || "").trim();
  if (!stem) return "";
  const view = String(tab || "").trim().toLowerCase() || "board";
  const focus = describeSelection(selection, index);
  return `[Viewer context: ${view} view of board ${stem}${focus ? `, ${focus} selected` : ""}]`;
}

/** "component R20" / "net GND" / "" — the human name for the current selection. */
export function describeSelection(selection, index) {
  if (!selection?.kind || !selection.key) return "";
  if (selection.kind === "component") {
    const refdes = index?.componentBySourceId?.get(selection.key)?.refdes;
    return refdes ? `component ${refdes}` : "";
  }
  if (selection.kind === "net") {
    const name = index?.netByKey?.get(selection.key)?.name;
    return name ? `net ${name}` : "";
  }
  return "";
}

// ---------------------------------------------------------------------------
// product.json
// ---------------------------------------------------------------------------

/**
 * The project skeleton's unfilled fields, verbatim from
 * `skills/circuitcode/templates/project_skeleton/product.json`. They are
 * instructions addressed to the model, and a new project carries them until
 * the model overwrites them.
 */
const SKELETON_NAME = "new-board";
const SKELETON_DESCRIPTION = "one sentence: what this device does for its owner";

/**
 * Instruction-shaped text, for the same field after the skeleton's wording
 * changes. Deliberately narrow: a real one-line product description does not
 * open with "one sentence", carry a TODO, or sit inside angle brackets.
 */
const PLACEHOLDER_SHAPE = /^(todo\b|tbd\b|one sentence\b|describe |<.*>$|\.{3}$|…$)/i;

/** True when this value is the template talking, not the board. */
export function isPlaceholderText(value, skeleton = "") {
  const text = String(value ?? "").trim();
  if (!text) return true;
  if (skeleton && text.toLowerCase() === String(skeleton).toLowerCase()) return true;
  return PLACEHOLDER_SHAPE.test(text);
}

/**
 * product.json with the skeleton's unfilled fields removed.
 *
 * Watching a first build: the Overview's "What this is" panel read
 * **"new-board — one sentence: what this device does for its owner"** for the
 * whole run. That is the template's note to the model, rendered to a person as
 * if it were the answer to what their board does — the exact shape of
 * manufactured confidence, and unreadable as anything but a bug. Dropping the
 * field lets the panel fall back to the board's real name and say nothing it
 * cannot support.
 *
 * @param {object|null} product
 * @returns {object|null} null when nothing usable survives
 */
export function sanitizeProduct(product) {
  if (!product || typeof product !== "object") return null;
  const next = { ...product };
  if (isPlaceholderText(next.name, SKELETON_NAME)) delete next.name;
  if (isPlaceholderText(next.description, SKELETON_DESCRIPTION)) delete next.description;
  return next;
}
