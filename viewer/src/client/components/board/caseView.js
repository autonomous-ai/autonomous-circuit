// The Case tab's arithmetic, kept out of the WebGL plumbing so node can test it.
//
// Every part arrives in the board contract's frame (boards/<stem>.enclosure.json):
// millimetres, origin at the board outline centre, +x right, +y up seen from the
// top, z = 0 on the top copper, the board spanning -t..0. The case is modelled
// assembled, so drawing the parts where they are IS the assembly; "exploded" only
// pulls the halves apart along z so the board between them shows.

/** What the checker's state means to someone about to print. */
export function describeStatus(state, reason = "") {
  switch (state) {
    case "passed":
      return { tone: "good", title: "Fits the board", detail: "Checked against this board: nothing collides and every screen, button and port has its opening." };
    case "failed":
      return { tone: "bad", title: "Does not fit yet", detail: `${reason || "The check found errors"} — the agent fixes the model and checks again.` };
    case "stale":
      return { tone: "warn", title: "Needs a recheck", detail: `${reason || "Something changed since the last check"}. Do not print until it is checked again.` };
    default:
      return { tone: "idle", title: "No case yet", detail: "The agent models the case after the firmware is built." };
  }
}

/** A part's role, in words, and whether it belongs in the assembled view. */
export function roleLabel(role) {
  return {
    shell: "Shell",
    lid: "Lid",
    plunger: "Button plunger",
    membrane: "Button membrane",
    diffuser: "Light diffuser",
    bracket: "Bracket",
    coupon: "Test-fit coupon",
  }[role] || "Part";
}

/** The coupon is printed alone, first; it is never part of the assembly. */
export function defaultVisibility(parts) {
  const visible = {};
  for (const part of parts || []) visible[part.name] = part.role !== "coupon";
  return visible;
}

/** The order parts are listed in: the coupon first (print it first), then shells, then the rest. */
export function orderParts(parts) {
  const rank = { coupon: 0, shell: 1, lid: 2, plunger: 3, membrane: 3, diffuser: 4, bracket: 5 };
  return [...(parts || [])].sort((a, b) => (rank[a.role] ?? 6) - (rank[b.role] ?? 6) || String(a.name).localeCompare(String(b.name)));
}

/**
 * How far to pull a part out when exploded, along z, from where its centre sits
 * relative to the board: above the board goes up, below goes down. The coupon
 * is set just right of the case (`caseMaxX`, the case's right edge) instead, so
 * it never hides inside the shell.
 */
export function explodeOffset(part, bounds, caseMaxX, gap) {
  if (part?.role === "coupon") return bounds ? [caseMaxX - bounds.min[0] + gap * 0.5, 0, 0] : [0, 0, 0];
  if (!bounds) return [0, 0, 0];
  const centreZ = (bounds.min[2] + bounds.max[2]) / 2;
  return [0, 0, centreZ >= 0 ? gap : -gap];
}

/** "Print it face down" — what a part's print rotation asks of the slicer, in words. */
export function printNote(part) {
  const rot = Array.isArray(part?.printRotateDeg) ? part.printRotateDeg.map((v) => Number(v) || 0) : [0, 0, 0];
  const flipped = rot.some((v, i) => i < 2 && Math.abs(Math.abs(v % 360) - 180) < 1);
  const turned = rot.some((v) => Math.abs(v % 360) > 1 && Math.abs(Math.abs(v % 360) - 180) >= 1);
  const how = flipped ? "flip it face down on the bed" : turned ? `rotate it ${rot.map((v) => `${v}°`).join(" / ")} (x / y / z)` : "print it as modelled";
  return part?.supports === false ? `${how}, no supports` : how;
}

export function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} kB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** The download name for a part: `<board>-<part>.stl`, safe for any file system. */
export function downloadName(board, part, ext) {
  const clean = (s) => String(s || "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "part";
  return `${clean(board)}-${clean(part)}.${ext}`;
}
