// Pure helpers for turning catalog entries into the board rail's model: which
// entries are boards, how they sort, what label a chip shows, and whether a
// board reads as ready / building / pending. DOM-free and transport-free so
// node:test covers them directly. Ported from the donor episodeModel.js —
// the activity-window and stem-prefix logic carry over verbatim.

// Board sources live directly under boards/ (contract §1). Review/fab members
// live in `<stem>_review/` / `<stem>_fab/` and are hidden by the catalog
// scanner, but stay defensive: only a direct child of boards/ is a board.
export const BOARDS_DIR = "boards/";

// An artifact_changed within this window marks the board as building.
export const BUILDING_ACTIVITY_WINDOW_MS = 30_000;

/**
 * True when a catalog entry is a board: `boards/<stem>.tsx` (the source) or
 * `boards/<stem>.circuit.json` (the IR of record, if Track B surfaces it).
 * Names starting with `_` are hidden helpers, not boards.
 */
export function isBoardEntry(entry) {
  if (!entry) return false;
  const file = String(entry.file || "");
  if (!file.startsWith(BOARDS_DIR)) return false;
  const rest = file.slice(BOARDS_DIR.length);
  if (!rest || rest.includes("/") || rest.startsWith("_")) return false;
  return rest.endsWith(".tsx") || rest.endsWith(".circuit.json");
}

/** "boards/main.tsx" → "main"; "boards/main.circuit.json" → "main". */
export function boardStem(file) {
  const base = String(file || "").split("/").pop() || "";
  return base.replace(/\.circuit\.json$/, "").replace(/\.[^.]+$/, "");
}

/** Rail chip label: the stem, shortened so it fits a chip. */
export function boardLabel(stem) {
  const s = String(stem || "?");
  return s.length > 10 ? `${s.slice(0, 9)}…` : s;
}

/**
 * Board entries from a catalog, sorted by stem, one entry per stem. When both
 * the `.tsx` source and the `.circuit.json` IR are surfaced for the same
 * board, prefer the one carrying the grouped `artifact` (that's the entry the
 * workspace can actually render from).
 */
export function selectBoardEntries(catalog) {
  const entries = Array.isArray(catalog?.entries) ? catalog.entries : [];
  const byStem = new Map();
  for (const entry of entries) {
    if (!isBoardEntry(entry)) continue;
    const stem = boardStem(entry.file);
    const existing = byStem.get(stem);
    if (!existing) {
      byStem.set(stem, entry);
      continue;
    }
    const existingHasArtifact = Boolean(existing.artifact?.metadataUrl);
    const entryHasArtifact = Boolean(entry.artifact?.metadataUrl);
    if (entryHasArtifact && !existingHasArtifact) byStem.set(stem, entry);
  }
  return [...byStem.values()].sort((a, b) =>
    boardStem(a.file).localeCompare(boardStem(b.file)),
  );
}

/** True when this artifact_changed file belongs to the board's file family. */
export function activityTouchesBoard(file, stem) {
  const name = String(file || "");
  const s = String(stem || "");
  if (!s) return false;
  // Matches boards/main.tsx, main.circuit.json, main.board.json,
  // main_review/…, main_fab/… (stem-prefix logic ported verbatim).
  return name.includes(`${s}.`) || name.includes(`${s}_`);
}

/**
 * Board chip status:
 *   - "building": a generation touched this board's files within the window
 *   - "ready":    sidecar + schematic render both present (the "done" shape)
 *   - "pending":  the source exists but its artifacts haven't landed
 *
 * @param {{file: string, artifact?: {metadataUrl?: string, schematicUrl?: string}}} entry
 * @param {{activity?: Record<string, number>, now?: number, windowMs?: number}} [context]
 * @returns {"ready"|"building"|"pending"}
 */
export function boardStatus(entry, context = {}) {
  const { activity = {}, now = Date.now(), windowMs = BUILDING_ACTIVITY_WINDOW_MS } = context;
  const stem = boardStem(entry?.file);
  for (const [file, at] of Object.entries(activity)) {
    if (now - at <= windowMs && activityTouchesBoard(file, stem)) {
      return "building";
    }
  }
  const artifact = entry?.artifact || {};
  if (artifact.metadataUrl && artifact.schematicUrl) return "ready";
  return "pending";
}

/**
 * The locked-BOM artifact (parts.json), if the catalog surfaces one. The
 * catalog normally hides .json entries, so accept any entry whose file is
 * exactly "parts.json" regardless of kind — Track B may surface it either way
 * (mirrors the donor's series.json handling).
 */
export function selectPartsEntry(catalog) {
  const entries = Array.isArray(catalog?.entries) ? catalog.entries : [];
  return (
    entries.find((entry) => String(entry?.file || "") === "parts.json") || null
  );
}
