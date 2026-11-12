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
    case "unverified":
      return { tone: "warn", title: "Not fully measured", detail: `${reason || "Part of the case could not be measured"}. Do not count it as a pass.` };
    case "stale":
      return { tone: "warn", title: "Needs a recheck", detail: `${reason || "Something changed since the last check"}. Do not print until it is checked again.` };
    case "draft":
      return { tone: "warn", title: "Đang làm · chưa duyệt", detail: `${reason || "The case team is still modelling"}. A draft: not checked, not printable.` };
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

/** Place each model part under its verified print plate; keep test/reference parts separate. */
export function groupPartsByPlate(parts, plates) {
  const byName = new Map((parts || []).map((part) => [part.name, part]));
  const assigned = new Set();
  const groups = [];
  for (const plate of plates || []) {
    if (!Number.isInteger(plate?.index) || plate.index < 1 || !Array.isArray(plate.parts)) continue;
    const members = [];
    for (const name of plate.parts) {
      const part = byName.get(name);
      if (!part || assigned.has(name) || part.role === "coupon" || part.printable === false) continue;
      members.push(part);
      assigned.add(name);
    }
    if (members.length) groups.push({ index: plate.index, name: String(plate.name || `Plate ${plate.index}`), parts: members });
  }
  groups.sort((a, b) => a.index - b.index);
  return { groups, other: (parts || []).filter((part) => !assigned.has(part.name)) };
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

// ---------------------------------------------------------------------------
// Colour per part. Every printed part gets its own colour so the 3D view reads
// as parts, not one grey lump; the list shows the same colour as a swatch.
// Mid-tone, saturated enough for the dark scene (#0e0f13) and dark enough for
// the light one (#eaf0f2); none is the board's green (0x468a69), and no two
// are near enough to be mistaken side by side (two blues read as one part).
export const PART_PALETTE = [
  0x4f9de8, 0xf0a23a, 0x9b72d8, 0x2fb5a6, 0xe0609f,
  0x8bbd3e, 0xe6d34a, 0xd9534f, 0xa0663a, 0x36c9e0,
];
/** Test coupons: printed alone, first — a quiet sand, not a part colour. */
export const COUPON_COLOR = 0xc9b48a;
/** Reference models (bought parts, DO_NOT_PRINT): grey, they are not printed. */
export const REFERENCE_COLOR = 0x7d8a90;

function isReference(part) {
  return part?.printable === false || part?.role === "reference";
}

function hashName(name) {
  let h = 2166136261;
  for (const ch of String(name)) h = Math.imul(h ^ ch.codePointAt(0), 16777619) >>> 0;
  return h;
}

/**
 * `{ [part name]: 0xRRGGBB }`. A name hashes to a palette slot; a slot already
 * taken moves to the next free one, taking names in sorted order, so a given set
 * of parts always gets the same colours and no two share one until the palette
 * runs out (then colours repeat).
 */
export function partColors(parts) {
  const colors = {};
  const taken = new Set();
  const names = [];
  for (const part of parts || []) {
    if (part?.role === "coupon") colors[part.name] = COUPON_COLOR;
    else if (isReference(part)) colors[part.name] = REFERENCE_COLOR;
    else names.push(String(part.name));
  }
  for (const name of [...new Set(names)].sort()) {
    let slot = hashName(name) % PART_PALETTE.length;
    if (taken.size < PART_PALETTE.length) while (taken.has(slot)) slot = (slot + 1) % PART_PALETTE.length;
    taken.add(slot);
    colors[name] = PART_PALETTE[slot];
  }
  return colors;
}

export function cssColor(color) {
  return `#${(Number(color) >>> 0).toString(16).padStart(6, "0").slice(-6)}`;
}

/** A part that is printed as part of the product: not a test coupon, not a reference model. */
export function isPrintedPart(part) {
  return part?.role !== "coupon" && !isReference(part);
}

// ---------------------------------------------------------------------------
// The summary chips over the 3D view. Only what the data says: a chip whose
// number is not known is left out, never guessed.
const STATUS_CHIP = {
  passed: { label: "Pass", tone: "good" },
  unverified: { label: "Unverified", tone: "warn" },
  stale: { label: "Review", tone: "warn" },
  failed: { label: "Fail", tone: "bad" },
  draft: { label: "Draft", tone: "warn" },
};

/**
 * @param {{ parts?: object[], plates?: object[], state?: string, sizeMm?: number[]|null }} input
 * @returns {Array<{ id: string, label: string, title: string, tone?: string }>}
 */
export function summaryChips({ parts = [], plates = [], state = "", sizeMm = null } = {}) {
  const chips = [];
  const printed = (parts || []).filter(isPrintedPart);
  if (printed.length) {
    chips.push({ id: "parts", label: `${printed.length} ${printed.length === 1 ? "part" : "parts"}`, title: "Parts to print. Test coupons and reference models are not counted." });
  }
  if (Array.isArray(sizeMm) && sizeMm.length === 3 && sizeMm.every((v) => Number.isFinite(v) && v > 0)) {
    chips.push({ id: "size", label: `${sizeMm.map((v) => Math.round(v)).join(" × ")} mm`, title: "Overall size of the assembled printed parts (width × depth × height)." });
  }
  const known = printed.filter((part) => typeof part.supports === "boolean");
  if (known.length) {
    const need = known.filter((part) => part.supports).length;
    const unknown = printed.length - known.length;
    chips.push({
      id: "supports",
      label: need ? `${need} need support` : "No supports",
      title: `Parts that need supports in the slicer${unknown ? ` (${unknown} part${unknown === 1 ? "" : "s"} not stated)` : ""}.`,
    });
  }
  const plateCount = (plates || []).length;
  if (plateCount) chips.push({ id: "plates", label: `${plateCount} ${plateCount === 1 ? "plate" : "plates"}`, title: "Print plates in the Print all project." });
  const status = STATUS_CHIP[state];
  if (status) chips.push({ id: "status", label: status.label, tone: status.tone, title: describeStatus(state).title });
  return chips;
}

// ---------------------------------------------------------------------------
// Plate titles. Agents write names like
//   "MỚI/SỬA — CẦN IN — Khung xương MỚI A1V4-r5 (khung dưới, khung trên) — CHỌN IN PLATE NÀY — PETG — A1V4-r5"
// which is a status, an instruction, a material and a revision around one
// description. The rule, simple on purpose:
//   1. split on " — " (spaced em dash); a name without one is shown as is;
//   2. drop segments written in capitals (status words and instructions:
//      "MỚI/SỬA", "CẦN IN", "CHỌN IN PLATE NÀY"), segments that are only a
//      filament ("PETG", "PLA") and segments that are only a revision ("A1V4-r5", "r5", "v2");
//   3. show the longest segment left; if none is left, show the whole name.
// The first capitals segment (when short) is kept as a small tag, so "new" and
// "already printed" plates still read apart; the full name is the tooltip.
const MATERIAL = /^(PLA\+?|PETG|ABS|ASA|TPU|PA(\d+)?(-CF)?|PC|PVA|HIPS|NYLON|CF)( ?(\+|-)? ?[A-Z]*)?$/i;
const REVISION = /^([A-Za-z0-9]+-)?[rv]\d+[a-z]?$|^rev\.? ?\d+$/i;

function isCapitals(segment) {
  return /\p{L}/u.test(segment) && segment === segment.toLocaleUpperCase("vi");
}

export function plateTitle(name) {
  const full = String(name || "").trim();
  const segments = full.split(" — ").map((s) => s.trim()).filter(Boolean);
  if (segments.length < 2) return { title: full, tag: "", full };
  const kept = segments.filter((s) => !isCapitals(s) && !MATERIAL.test(s) && !REVISION.test(s));
  const title = kept.length ? kept.reduce((a, b) => (b.length > a.length ? b : a)) : full;
  const first = segments[0];
  const tag = kept.length && isCapitals(first) && first.length <= 16 ? first : "";
  return { title, tag, full };
}

// ---------------------------------------------------------------------------
// Camera presets for the case view, as directions from the target to the camera
// in the contract frame (+z out of the board's top face, which is the camera's
// up). "Top" leans a hair toward -y: looking straight down +z is parallel to
// `up`, and lookAt then has no defined roll.
export const CAMERA_VIEWS = {
  iso: [0.55, -0.9, 0.75],
  top: [0, -0.001, 1],
  front: [0, -1, 0],
};

export function viewDirection(view) {
  const v = CAMERA_VIEWS[view] || CAMERA_VIEWS.iso;
  const len = Math.hypot(...v);
  return v.map((x) => x / len);
}

/** What the Case tabs say while a team run is on and no draft part is exported yet. */
export function drawingMessage(run) {
  return `The case team is still drawing${run ? ` (run ${run})` : ""}. Its parts appear here, marked as a draft, as soon as they are exported.`;
}

/** The label on a disabled Print all while the case is a draft. */
export const DRAFT_PRINT_LABEL = "Đang vẽ… chưa in được";

/**
 * Which case the Case tabs show. An accepted case with parts always wins; with
 * none, the case team's draft parts from the running team preview are shown as
 * an unverified draft (state "draft", never printable or exportable).
 *
 * @returns {{ enclosure: object|null, draft: { run: string, badge: string }|null, drawing: boolean, run: string }}
 *   `drawing`: a team run is active but nothing is there to show yet.
 */
export function resolveCase(accepted, teamPreview = null, teamRuns = null) {
  const run = String(teamPreview?.run || (teamRuns?.running ? teamRuns.activeRun || "" : ""));
  const running = Boolean(teamPreview || teamRuns?.running);
  if (Array.isArray(accepted?.parts) && accepted.parts.length) return { enclosure: accepted, draft: null, drawing: false, run };
  const team = teamPreview?.teams?.case;
  const models = Array.isArray(team?.models) ? team.models.filter((m) => m && typeof m.stlUrl === "string") : [];
  if (models.length) {
    const badge = `Đang làm · chưa duyệt${run ? ` (run ${run})` : ""}`;
    return {
      enclosure: {
        state: "draft",
        reason: team.stage ? `The case team is still on ${team.stage}` : "",
        contractUrl: accepted?.contractUrl || "",
        nativePCBCheckerApplies: accepted?.nativePCBCheckerApplies !== false,
        printStatus: { state: "draft", reason: "a draft is not printable" },
        parts: models.map((m) => ({ name: String(m.name), role: String(m.role || "other"), stlUrl: m.stlUrl,
          bytes: Number(m.bytes) || 0, printable: m.printable !== false })),
        plates: [],
        drawings: [],
      },
      draft: { run, badge },
      drawing: false,
      run,
    };
  }
  return { enclosure: accepted || null, draft: null, drawing: running, run };
}
