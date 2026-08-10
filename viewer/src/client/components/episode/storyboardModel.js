// Pure model for the storyboard strip: normalize the episode sidecar's shot
// list against the catalog entry's artifact.shots[], fill in missing durations,
// and compute each shot's cumulative start offset so clicking a chip can seek
// the player. DOM-free; unit-tested with node:test.

/**
 * One normalized shot: `{ id, durationS: number|null, status, path }`.
 * Sources, in precedence order:
 *   1. sidecar.shots[] (.episode.json) — ids + paths always; duration/status
 *      when the pipeline includes them (accepts durationS | duration_s, and
 *      status ∈ rendered|cached|failed).
 *   2. artifact.shots[] from the catalog entry — ids/paths fallback so the
 *      strip renders even before the sidecar fetch lands.
 *
 * @param {{shots?: any[]} | null | undefined} sidecar
 * @param {Array<{id?: string, file?: string, url?: string}> | undefined} artifactShots
 * @returns {Array<{id: string, durationS: number|null, status: string, path: string}>}
 */
export function normalizeShots(sidecar, artifactShots) {
  const fromSidecar = Array.isArray(sidecar?.shots) ? sidecar.shots : [];
  const source = fromSidecar.length
    ? fromSidecar
    : Array.isArray(artifactShots)
      ? artifactShots
      : [];
  return source
    .map((shot, index) => {
      const id = String(shot?.id ?? `shot_${index + 1}`);
      const rawDuration = shot?.durationS ?? shot?.duration_s;
      const durationS =
        Number.isFinite(Number(rawDuration)) && Number(rawDuration) > 0
          ? Number(rawDuration)
          : null;
      const status = normalizeShotStatus(shot?.status);
      const path = String(shot?.path ?? shot?.file ?? "");
      return { id, durationS, status, path };
    })
    .filter((shot) => shot.id);
}

const SHOT_STATUSES = new Set(["rendered", "cached", "failed"]);

/** Clamp a shot status to the contract's closed set; unknown → "rendered". */
export function normalizeShotStatus(status) {
  const value = String(status || "").toLowerCase();
  return SHOT_STATUSES.has(value) ? value : "rendered";
}

/**
 * Cumulative start offsets. Shots with explicit durations consume their own
 * time; shots without one split the episode's remaining duration evenly (so a
 * sidecar that omits per-shot durations still seeks proportionally). With no
 * episode duration either, unknown shots contribute 0 and land at the last
 * known offset.
 *
 * @param {Array<{durationS: number|null}>} shots normalized shots
 * @param {number} [episodeDurationS] total episode duration when known
 * @returns {Array<{startS: number, durationS: number}>} same order as `shots`
 */
export function shotOffsets(shots, episodeDurationS) {
  const list = Array.isArray(shots) ? shots : [];
  const known = list.reduce(
    (sum, shot) => sum + (shot?.durationS ?? 0),
    0,
  );
  const unknownCount = list.filter((shot) => shot?.durationS == null).length;
  const total = Number(episodeDurationS);
  const remainder =
    unknownCount > 0 && Number.isFinite(total) && total > known
      ? (total - known) / unknownCount
      : 0;

  const out = [];
  let cursor = 0;
  for (const shot of list) {
    const duration = shot?.durationS ?? remainder;
    out.push({ startS: cursor, durationS: duration });
    cursor += duration;
  }
  return out;
}

/** The shot (by index) that owns a given playback time; -1 when none do. */
export function shotIndexAtTime(offsets, timeS) {
  const list = Array.isArray(offsets) ? offsets : [];
  const t = Number(timeS) || 0;
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (t >= list[i].startS) return i;
  }
  return list.length ? 0 : -1;
}
