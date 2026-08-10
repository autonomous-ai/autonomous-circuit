import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STARTERS,
  FEELINGS,
  starterById,
  surpriseStarter,
  buildStarterPrompt,
  buildClonePrompt,
  regionFromLocale,
  orderedStarters,
  ctaLabel,
  languageFromLocale,
  readStoredName,
  writeStoredName,
  NAME_STORAGE_KEY,
  inferGenreLabel,
} from "./starters.js";

function fakeStorage(init = {}) {
  const map = new Map(Object.entries(init));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    _map: map,
  };
}

test("starters are non-empty and uniquely identified", () => {
  assert.ok(STARTERS.length >= 4);
  const ids = new Set(STARTERS.map((s) => s.id));
  assert.equal(ids.size, STARTERS.length);
  for (const s of STARTERS) {
    assert.ok(s.title && s.pitch && s.genre, `starter ${s.id} fully populated`);
  }
});

test("starterById resolves and misses cleanly", () => {
  assert.equal(starterById("ceo_secret").title, "The Temp Who Owns the Tower");
  assert.equal(starterById("nope"), null);
});

test("surpriseStarter is deterministic in the seed and always valid", () => {
  assert.equal(surpriseStarter(0).id, STARTERS[0].id);
  assert.equal(surpriseStarter(STARTERS.length).id, STARTERS[0].id); // wraps
  assert.equal(surpriseStarter(-1).id, STARTERS[STARTERS.length - 1].id); // negative safe
});

test("buildStarterPrompt yields a complete brief and puts the user in it", () => {
  const p = buildStarterPrompt(STARTERS[0]);
  assert.match(p, /short-drama series/);
  assert.match(p, /episode 1/i);
  assert.match(p, /50 episodes/);
  assert.match(p, /Put me in it as the lead/);
});

test("buildStarterPrompt recasts the hero when a name is given", () => {
  const p = buildStarterPrompt(STARTERS[1], "Amara");
  assert.match(p, /my name in the story is Amara/);
});

test("buildStarterPrompt is empty for a missing starter", () => {
  assert.equal(buildStarterPrompt(null), "");
});

test("languageFromLocale maps to a language name, English is silent", () => {
  assert.equal(languageFromLocale("zh-CN"), "Chinese");
  assert.equal(languageFromLocale("pt-BR"), "Portuguese");
  assert.equal(languageFromLocale("es"), "Spanish");
  assert.equal(languageFromLocale("en-US"), "");   // engine default, no clause
  assert.equal(languageFromLocale(""), "");
});

test("briefs generate in the viewer's language when non-English", () => {
  assert.match(buildStarterPrompt(STARTERS[0], "", "Chinese"), /Write it in Chinese\./);
  assert.match(buildClonePrompt("something", "Mei", "", "Portuguese"), /Write it in Portuguese\./);
  // English (empty) adds no language clause
  assert.ok(!/Write it in/.test(buildStarterPrompt(STARTERS[0], "", "")));
});

test("inferGenreLabel previews the genre from keywords, silent when unsure", () => {
  assert.equal(inferGenreLabel("The son-in-law war god"), "War God");
  assert.equal(inferGenreLabel("rejected by my alpha"), "Werewolf");
  assert.equal(inferGenreLabel("the CEO's secret wife"), "Billionaire");
  assert.equal(inferGenreLabel("comes back for revenge"), "Revenge");
  assert.equal(inferGenreLabel("qwerty zxcv"), "");   // no confident match → no guess
  assert.equal(inferGenreLabel(""), "");
});

test("buildClonePrompt frames an original-in-the-style, never a copy", () => {
  const p = buildClonePrompt("The Alpha's Rejected Mate", "Wei", "my old boss");
  assert.match(p, /inspired by the style of "The Alpha's Rejected Mate"/);
  assert.match(p, /NOT a copy/);
  assert.match(p, /my name is Wei/);
  assert.match(p, /wronged me is my old boss/);
  assert.equal(buildClonePrompt(""), ""); // needs a name
});

test("hero name persists and rehydrates for a returning creator", () => {
  const s = fakeStorage();
  writeStoredName("Amara", s);
  assert.equal(s._map.get(NAME_STORAGE_KEY), "Amara");
  assert.equal(readStoredName(s), "Amara");
  // clearing removes it; blank reads safely
  writeStoredName("", s);
  assert.equal(readStoredName(s), "");
});

test("stored-name helpers never throw on a hostile/absent storage", () => {
  const boom = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  assert.equal(readStoredName(boom), "");
  assert.doesNotThrow(() => writeStoredName("x", boom));
  assert.equal(readStoredName(null), "");
});

test("ctaLabel reflects the hero name (direct-manipulation feedback)", () => {
  assert.equal(ctaLabel(""), "Make my version");
  assert.equal(ctaLabel("  "), "Make my version");
  assert.equal(ctaLabel("Amara"), "You're Amara — make it");
});

test("regionFromLocale maps languages, defaults safely", () => {
  assert.equal(regionFromLocale("zh-CN"), "cn");
  assert.equal(regionFromLocale("pt-BR"), "br");
  assert.equal(regionFromLocale("en-NG"), "africa");
  assert.equal(regionFromLocale("en-US"), "");   // default order
  assert.equal(regionFromLocale(undefined), "");
});

test("orderedStarters leads with the region's taste and keeps the full feed", () => {
  const cn = orderedStarters("zh-CN");
  assert.equal(cn[0].key, "billionaire");
  assert.equal(cn.length, STARTERS.length);       // nothing dropped
  const def = orderedStarters("en-US");
  assert.deepEqual(def.map((s) => s.id), STARTERS.map((s) => s.id)); // unchanged
});

test("every feeling maps to a real starter or the surprise door", () => {
  for (const f of FEELINGS) {
    if (f.starterId !== null) assert.ok(starterById(f.starterId), `${f.label} maps`);
  }
});
