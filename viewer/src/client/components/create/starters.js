// Client mirror of skills/dramacode/dramalib/starters.py — the one-tap starter
// gallery that replaces the blank prompt box (docs/onboarding-ux.md). Kept
// JSX/dep-free so Node's --test runner can exercise the prompt builder.
//
// Each starter is a proven hit shape with a ready pitch. Tapping one sends a
// complete creative brief to the chat (which drives dramacode) — the creator
// never faces an empty box. Keep this list in sync with the Python STARTERS.

export const STARTERS = Object.freeze([
  {
    id: "ceo_secret",
    key: "billionaire",
    genre: "Billionaire",
    emoji: "🏢",
    title: "The Temp Who Owns the Tower",
    pitch: "The temp everyone ignores secretly owns the whole company.",
    popular: true, // top overseas hit genre (docs/short-drama-playbook.md)
  },
  {
    id: "revenge_return",
    key: "revenge",
    genre: "Revenge",
    emoji: "🔥",
    title: "Back For Everything",
    pitch: "They framed her and threw her out. She comes back for everything.",
    popular: true,
  },
  {
    id: "war_god_back",
    key: "zhanshen",
    genre: "War God",
    emoji: "⚔️",
    title: "The Son-in-Law They Laughed At",
    pitch: "The son-in-law they mocked is the strongest man alive.",
  },
  {
    id: "second_chance",
    key: "riches",
    genre: "Rags to Riches",
    emoji: "💎",
    title: "Overnight",
    pitch: "Broke, mocked, counted out — then everything changes overnight.",
  },
  {
    id: "fake_marriage",
    key: "contract",
    genre: "Fake Marriage",
    emoji: "💍",
    title: "Married on Paper",
    pitch: "A marriage on paper — until the feelings stop being fake.",
  },
  {
    id: "rejected_mate",
    key: "werewolf",
    genre: "Werewolf",
    emoji: "🐺",
    title: "Rejected, Then Risen",
    pitch: "Rejected by her alpha, she rises as the one he can't live without.",
    popular: true,
  },
  {
    id: "reborn",
    key: "chongsheng",
    genre: "Rebirth",
    emoji: "⏳",
    title: "One More Life",
    pitch: "She dies betrayed — and wakes up the morning it all began.",
  },
  {
    id: "daughter_in_law",
    key: "inlaw",
    genre: "Family Secrets",
    emoji: "🎎",
    title: "They Have No Idea Who She Is",
    pitch: "The family that bullied her has no idea who her real family is.",
  },
]);

// Region -> the genres to surface FIRST (mirrors dramalib.starters.LOCALE_ORDER).
// The feed just already looks like the viewer's taste — they never pick a region.
const REGION_ORDER = Object.freeze({
  cn: ["billionaire", "zhanshen", "contract", "revenge"],
  br: ["contract", "revenge", "billionaire", "riches"],
  africa: ["riches", "revenge", "billionaire", "contract"],
});

// Map a browser locale (navigator.language) to a region key. Best-effort, never
// throws; unknown locales get the default order.
export function regionFromLocale(locale) {
  const l = String(locale || "").toLowerCase();
  if (l.startsWith("zh")) return "cn";
  if (l.startsWith("pt")) return "br";
  if (/(-ng|-ke|-za|-gh|sw|ha|yo|ig|am)/.test(l)) return "africa";
  return "";
}

// Map a browser locale to a human language name so the drama is generated in the
// viewer's language automatically (zero config). English returns "" (no clause —
// it's the engine default). Extend as markets grow.
const LANGUAGES = Object.freeze({
  zh: "Chinese", es: "Spanish", pt: "Portuguese", fr: "French", de: "German",
  ja: "Japanese", ko: "Korean", id: "Indonesian", th: "Thai", vi: "Vietnamese",
  ar: "Arabic", hi: "Hindi", ru: "Russian", tr: "Turkish", it: "Italian",
});

export function languageFromLocale(locale) {
  const base = String(locale || "").toLowerCase().split("-")[0];
  return LANGUAGES[base] || "";
}

// Remember the creator's name so a returning user never retypes it (one less
// step every time). Storage is injectable for tests; all access is guarded so a
// blocked/absent localStorage never throws.
export const NAME_STORAGE_KEY = "autonomousTv.heroName";

function defaultStorage() {
  try {
    return typeof globalThis !== "undefined" ? globalThis.localStorage : null;
  } catch {
    return null; // some browsers throw on localStorage access in private mode
  }
}

export function readStoredName(storage = defaultStorage()) {
  try {
    return String(storage?.getItem(NAME_STORAGE_KEY) || "");
  } catch {
    return "";
  }
}

export function writeStoredName(name, storage = defaultStorage()) {
  try {
    const v = String(name || "").trim();
    if (v) storage?.setItem(NAME_STORAGE_KEY, v);
    else storage?.removeItem(NAME_STORAGE_KEY);
  } catch {
    /* best-effort persistence */
  }
}

/** STARTERS reordered so the viewer's region leads; stable for the rest. */
export function orderedStarters(locale) {
  const order = REGION_ORDER[regionFromLocale(locale)];
  if (!order) return [...STARTERS];
  const rank = new Map(order.map((k, i) => [k, i]));
  return [...STARTERS].sort((a, b) => {
    const ra = rank.has(a.key) ? rank.get(a.key) : order.length;
    const rb = rank.has(b.key) ? rank.get(b.key) : order.length;
    return ra - rb || STARTERS.indexOf(a) - STARTERS.indexOf(b);
  });
}

// The feeling-cards (Door 3) — a jargon-free wizard entry. Each maps to a starter
// genre so a tap still produces a complete brief. ("Surprise me" is promoted to
// its own hero button — the zero-decision path — so it's not lost among these.)
export const FEELINGS = Object.freeze([
  { emoji: "😤", label: "I want revenge", starterId: "revenge_return" },
  { emoji: "👑", label: "I want to rise", starterId: "war_god_back" },
  { emoji: "💗", label: "I want to be adored", starterId: "fake_marriage" },
]);

export function starterById(id) {
  return STARTERS.find((s) => s.id === id) || null;
}

/**
 * The card CTA label — reflects the entered hero name so the self-insert
 * personalization is visible before the tap (direct manipulation). Falls back to
 * the generic label when no name is set.
 */
export function ctaLabel(heroName) {
  const hero = String(heroName || "").trim();
  return hero ? `You're ${hero} — make it` : "Make my version";
}

// Lightweight client-side genre preview for the clone box — mirrors the keyword
// inference in dramalib.clone so the user sees what we understood as they type
// ("We'll make a Revenge drama"). Returns "" when nothing matches confidently
// (we show nothing rather than guess — the server makes the authoritative call).
const CLONE_SIGNALS = [
  [["son-in-law", "war god", "god of war", "supreme", "dragon king", "strongest"], "War God"],
  [["mother-in-law", "daughter-in-law", "in-law", "in law"], "Family Secrets"],
  [["alpha", "werewolf", "wolf", "luna", "fated mate", "mate", "moon"], "Werewolf"],
  [["reborn", "rebirth", "reincarnat", "second life", "wakes up"], "Rebirth"],
  [["contract marriage", "fake marriage", "married to", "flash marr", "contract", "fake"], "Fake Marriage"],
  [["mafia", "cartel", "underworld", "gang"], "Mafia"],
  [["revenge", "vengeance", "betrayed", "framed", "discarded", "back for", "comes back"], "Revenge"],
  [["rags", "riches", "poor", "overnight", "cinderella", "broke"], "Rags to Riches"],
  [["ceo", "boss", "billionaire", "tycoon", "president", "heiress", "rich"], "Billionaire"],
];

export function inferGenreLabel(text) {
  const hay = String(text || "").toLowerCase();
  if (!hay.trim()) return "";
  for (const [keywords, label] of CLONE_SIGNALS) {
    if (keywords.some((k) => hay.includes(k))) return label;
  }
  return "";
}

/**
 * Build the brief for "clone a drama you love" (the growth hook). We generate an
 * ORIGINAL in the named show's style with the fan's characters — never a copy of
 * its script or footage (that framing is written into the prompt so the engine
 * stays on the right side of it). See skills/dramacode/dramalib/clone.py.
 */
export function buildClonePrompt(title, heroName = "", villainName = "", language = "") {
  const t = String(title || "").trim();
  if (!t) return "";
  const hero = String(heroName || "").trim();
  const villain = String(villainName || "").trim();
  const lang = String(language || "").trim();
  const parts = [
    `Make a bingeable short-drama series inspired by the style of "${t}" —`,
    "an original story in the same genre, NOT a copy of its script, dialogue, or footage.",
    hero ? `Make me the lead — my name is ${hero}.` : "Put me in it as the lead.",
    villain ? `The one who wronged me is ${villain}.` : "",
    "About 50 episodes, 90–120 seconds each.",
    lang ? `Write it in ${lang}.` : "",
    "Write episode 1 and render a quick draft I can watch.",
  ];
  return parts.filter(Boolean).join(" ");
}

// Deterministic in `seed` so "surprise me" is testable; the UI passes a rotating
// value (e.g. Date.now()).
export function surpriseStarter(seed = 0) {
  const n = STARTERS.length;
  const i = ((Math.floor(seed) % n) + n) % n;
  return STARTERS[i];
}

/**
 * Build the creative brief a starter sends to the chat. Natural language a
 * non-producer would recognize — genre + logline + the series shape + "write
 * episode 1" — optionally recasting the lead as the user (the character swap).
 */
export function buildStarterPrompt(starter, heroName = "", language = "") {
  if (!starter) return "";
  const hero = String(heroName || "").trim();
  const lead = hero
    ? `Make me the lead — my name in the story is ${hero}.`
    : "Put me in it as the lead.";
  const lang = String(language || "").trim();
  return [
    `Make a bingeable short-drama series: ${starter.title}.`,
    starter.pitch,
    lead,
    "About 50 episodes, 90–120 seconds each.",
    lang ? `Write it in ${lang}.` : "",
    "Write episode 1 and render a quick draft I can watch.",
  ].filter(Boolean).join(" ");
}
