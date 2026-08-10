// Pure helpers for turning catalog entries into the episode rail's model:
// which entries are episodes, how they sort, what label a chip shows, and
// whether an episode reads as ready / rendering / pending. DOM-free and
// transport-free so node:test covers them directly.

// Episodes live directly under episodes/ (contract §1). Shot clips live in
// `<stem>_shots/` and are hidden by the catalog scanner, but stay defensive:
// only a direct child of episodes/ is an episode.
export const EPISODES_DIR = "episodes/";

// An artifact_changed within this window marks the episode as rendering.
export const RENDERING_ACTIVITY_WINDOW_MS = 30_000;

/** True when a catalog entry is an episode video (episodes/epNNN.mp4). */
export function isEpisodeEntry(entry) {
  if (!entry) return false;
  const kind = String(entry.kind || "").toLowerCase();
  if (kind !== "mp4") return false;
  const file = String(entry.file || "");
  if (!file.startsWith(EPISODES_DIR)) return false;
  const rest = file.slice(EPISODES_DIR.length);
  return rest.length > 0 && !rest.includes("/");
}

/** "episodes/ep001.mp4" → "ep001". */
export function episodeStem(file) {
  const base = String(file || "").split("/").pop() || "";
  return base.replace(/\.[^.]+$/, "");
}

/** Episode number parsed from the stem ("ep001" → 1); null when unnumbered. */
export function episodeNumber(stem) {
  const match = /(\d+)\s*$/.exec(String(stem || ""));
  if (!match) return null;
  const n = Number.parseInt(match[1], 10);
  return Number.isFinite(n) ? n : null;
}

/** Rail chip label: "ep001" → "E01" (three digits once past 99). */
export function episodeLabel(stem) {
  const n = episodeNumber(stem);
  if (n == null) return String(stem || "?").toUpperCase().slice(0, 5);
  return `E${String(n).padStart(2, "0")}`;
}

/** Episode entries from a catalog, sorted by number then name. */
export function selectEpisodeEntries(catalog) {
  const entries = Array.isArray(catalog?.entries) ? catalog.entries : [];
  return entries.filter(isEpisodeEntry).sort((a, b) => {
    const na = episodeNumber(episodeStem(a.file));
    const nb = episodeNumber(episodeStem(b.file));
    if (na != null && nb != null && na !== nb) return na - nb;
    if (na != null && nb == null) return -1;
    if (na == null && nb != null) return 1;
    return String(a.file).localeCompare(String(b.file));
  });
}

/** True when this artifact_changed file belongs to the episode's file family. */
export function activityTouchesEpisode(file, stem) {
  const name = String(file || "");
  const s = String(stem || "");
  if (!s) return false;
  // Matches episodes/ep001.mp4, ep001.episode.json, ep001_shots/…, ep001_review/…
  return name.includes(`${s}.`) || name.includes(`${s}_`);
}

/**
 * Episode chip status:
 *   - "rendering": a generation touched this episode's files within the window
 *   - "ready":     sidecar + poster both present (the contract's "done" shape)
 *   - "pending":   the .mp4 exists but its metadata/poster haven't landed
 *
 * @param {{file: string, artifact?: {metadataUrl?: string, posterUrl?: string}}} entry
 * @param {{activity?: Record<string, number>, now?: number, windowMs?: number}} [context]
 * @returns {"ready"|"rendering"|"pending"}
 */
export function episodeStatus(entry, context = {}) {
  const { activity = {}, now = Date.now(), windowMs = RENDERING_ACTIVITY_WINDOW_MS } = context;
  const stem = episodeStem(entry?.file);
  for (const [file, at] of Object.entries(activity)) {
    if (now - at <= windowMs && activityTouchesEpisode(file, stem)) {
      return "rendering";
    }
  }
  const artifact = entry?.artifact || {};
  if (artifact.metadataUrl && artifact.posterUrl) return "ready";
  return "pending";
}

/**
 * The series-bible artifact (series.json), if the catalog surfaces one. The
 * catalog normally hides .json entries, so also accept any entry whose file is
 * exactly "series.json" regardless of kind — Track B may surface it either way.
 */
export function selectSeriesEntry(catalog) {
  const entries = Array.isArray(catalog?.entries) ? catalog.entries : [];
  return (
    entries.find((entry) => String(entry?.file || "") === "series.json") || null
  );
}
