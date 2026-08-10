# The short-drama playbook → what Autonomous TV builds

Dee (2026-08-09): "deep research into the short-drama phenomenon — who's the audience, why
they love it, why they pay — and adjust the platform to create the best possible skills, tools,
loops, and frameworks to enable users to create dramas that get hundreds of millions of viewers.
We take on a few big studios and give the power to millions of users in every corner of the
world to create their own short dramas, to their community's taste, to their niche."

This doc = the synthesis + the concrete build plan. Sources: a research fan-out (audience/
psychology, monetization/market, format/genre craft, studios-vs-UGC strategy) + the competitor-
repo teardowns in `oss-landscape.md`.

## The one-line thesis
**The product's job is to let an ordinary person hit `3-second hook → reversal every ~20s →
cliffhanger every episode → 虐→爽 (torment→payoff) → paywall at peak craving` WITHOUT knowing
any of those rules exist.** That engineered emotional cadence — not video quality — is what the
audience is hooked to. So the platform's core value is **structure made automatic**: the
creator brings premise + taste; the tool brings the machine.

---

## Part 1 — Audience & psychology (landed)

**Market context:** China 微短剧 ≈ ¥50B in 2024 (iiMedia) — **bigger than China's total film
box office** (~¥42.5B) for the first time; ~576M users (CNNIC). Overseas led by **ReelShort**
(Crazy Maple/COL) and **DramaBox** (Storm/点众), + ShortMax/GoodShort/FlexTV/DreameShort;
overseas gross low-single-digit $B in 2024, **US is the largest overseas market (~⅓)**. It's a
**volume + retention + micro-payment** business, not a prestige business.

**Who watches:** skews **female, ~30-55+, middle/lower income, suburban/rural** in the West —
the soap-opera/Hallmark/Harlequin audience moved onto a phone, a group Big Tech under-serves.
Genre-dependent (men concentrate in revenge / war-god 战神 / son-in-law 赘婿 / sudden-wealth).
**Payers ≠ viewers** — whale-shaped: older women convert and pay unusually well (high ARPPU,
low payer %). China broader/older; SEA/LATAM/India younger, mobile-first, price-sensitive.

**Why they love it — the mechanisms (ranked):**
1. **黄金3秒 — the 3-second hook.** Cold open mid-conflict (a slap, "sign the divorce papers,"
   a betrayal). Create a question the brain must answer. Defeats the scroll.
2. **反转 — reversal density, a twist every 15-30s.** Compression is the innovation over soap
   opera: a staircase of small shocks; boredom never forms.
3. **爽 (shuǎng) — the payoff engine, THE drug.** Visceral cathartic gratification via 爽点:
   the **face-slap (打脸)**, the underdog counterattack (逆袭), the hidden-identity reveal
   (扮猪吃老虎). Prestige TV rations catharsis over a season; short drama serves it every 60-90s.
4. **虐→爽 (torment→payoff) micro-arcs.** Inflict injustice/humiliation *just before* the
   payoff — catharsis scales with the grievance setup. The pleasure is the contrast.
5. **Cliffhanger + paywall = one compulsion loop.** Every episode ends on a cliff; the paywall
   sits at a cliffhanger after ~8-15 free eps. The narrative loop and the money loop are the
   same mechanism.
6. **Variable-reward scrolling** (slot-machine timing of the next payoff).
7. **Wish-fulfillment for the disrespected** — the invisible/wronged self-insert gains dignity,
   wealth, love, vengeance. Emotional restitution for people who feel unseen. Netflix is too
   slow/distant; TikTok has no story to inhabit; short drama is the hybrid.
8. **Low cognitive load** — emotions named aloud, clean morality, watchable tired/half-attentive.
9. **Vertical full-screen intimacy** — big faces, reaction close-ups, near-camera address →
   parasocial closeness fast.
10. **Serialized parasocial attachment** across 60-100 tiny episodes (soap loyalty at TikTok
    speed). *Depends on consistent character faces across 60+ episodes — our cast-consistency
    system is load-bearing for the core emotional mechanic, not a nicety.*

**Genre → audience map:** CEO/billionaire romance 霸道总裁 (women, the spine) · revenge/face-slap/
counterattack 逆袭·打脸·复仇 (both) · werewolf/Luna/fated-mates (US younger women, Wattpad DNA) ·
mafia/possessive-alpha (women) · time-travel/rebirth 重生·穿越 do-over (women, huge in China) ·
hidden-identity/secret-royalty 扮猪吃老虎 (both) · rags-to-riches/sudden-wealth 神豪 (male-skew) ·
in-law/family 婆媳·家庭伦理 (older women; strong SEA/LATAM) · male power fantasy 战神/赘婿 (men).
**Localization is rewrite, not translation** (US wants revenge follow-through, not the Chinese
silent-suffer/win-back 追妻火葬场).

---

## Part 2 — The build plan (what we ship)

Mapped from the audience "implications" + competitor steals. **P0 = the bingeability core (do
first); P1 = consistency + cost; P2 = distribution/monetization layer.** Building the stable,
fully-specified pieces first; genre-catalog-heavy pieces wait for the format-research agent.

### P0 — the bingeability engine (structure made automatic)
- **A short-drama story engine reference** encoding the cadence *in seconds*: 黄金3秒 hook,
  inciting conflict by 20-30s, ≥1 reversal/episode (2 if >90s), cliffhanger in the final 6-10s,
  爽点 density target, 虐→爽 pairing, series arc (payoffs at ep 6/8/10), 60-80 ep runtime, the
  paywall-at-cliffhanger placement. Ship as a dramacode reference + a deterministic **cadence
  lint** the critic runs (warn if >~20-30s with no beat, if a 爽 lacks its 虐 setup, if an
  episode ends on resolution, if the open spends 3s on establishment). *(STATUS: building now.)*
- **emotion→physical-action lookup + "turn off the sound" test** — map 紧张/悲痛/愤怒/… to
  concrete micro-actions; a viewer should read the character's state on mute. Makes shots
  *filmable* instead of "she looks nervous." Fold into dramacode shot-prompt gen. *(building now.)*
- **HookScore critic rubric** in screening-room — five 0-5 dims (conflict intensity, character
  recognizability, cultural fit/localization, **clip-ability** = cuttable hooks per 60s, logic
  coherence); Pass ≥4.0 & no dim <3.5, Review 3.5-3.9, **Rewrite <3.5** → emit rewrite_guidance
  + suggested_ad_hooks. Plus inkos's **non-regressing** discipline (accept a reroll only if it
  beats the prior by ≥epsilon; roll back to the best; never auto-act on an unparseable critic).
  *(the epsilon/keep-best half already shipped in the screening loop today; add the rubric.)*
- **Genre-pack templates** (overridable `craft_default`, NOT a hard gate — the drama-skills vs
  OnlyShot fork): proven localized spines — face-slap revenge, contract-marriage CEO, werewolf
  fated-mate, rebirth do-over, hidden-identity, in-law drama, war-god/son-in-law. Each: core
  fantasy, stock archetypes, mandatory beats, signature tropes, per-market no-go flags.
  *(STATUS: waiting on the format/genre research agent for the richest English-market catalog.)*
- **Archetype casting kit** — pre-built roles mapping to the psychology (invisible/wronged
  self-insert, arrogant CEO, scheming other-woman, the villain who exists to be face-slapped).
  Fold into cast-book. *(after genre packs.)*

### P1 — consistency + cost (the quality + margin levers)
- **Decomposed director stack** (waoowaoo) — split shot authoring into passes: plan →
  cinematography (composition/lighting/palette/atmosphere) → acting-direction (observable) →
  motion. Highest-leverage quality upgrade.
- **cast-book → real consistency engine** — occurrence→decision continuity ledger (reuse/
  new_variant/new_asset/unresolved), identity-anchor vs. transient-state split, **versioned
  appearances** (changeReason + own reference keyframe), **stable asset index repeated at every
  character mention** in crowd shots, per-character bound voice. (R1 agent is already on
  turnaround sheets + reference-stack — this extends it.)
- **Storyboard-still gate before video (18× cost lever)** — approve a cheap keyframe still as
  the first frame before spending on the clip. (roadmap R3/R4.)
- **Last-frame→first-frame I2V chaining** — cross-shot continuity, no new base model (task #34;
  confirmed by 3 sources incl. waoowaoo's linkedToNextPanel).
- **Spatial "slot" staging** — named placement anchors per location; pin cast to slots per shot.
- **Audio-ledger-drives-duration** — dialogue length sets shot duration; unallocated seconds get
  filled with unapproved motion, so segment times must sum exactly.
- **Continuity audit over the *visual* timeline** — run inkos-style continuity dimensions as
  critic checks over the rendered board (outfit/prop/location/time-of-day match across shots).
  Nobody does this; unique to us.

### P2 — the distribution / monetization layer (the part that actually makes money)
- **Ad-clip / hook exporter (投流表)** — auto-cut the strongest 15-30s hook clips for TikTok/Meta
  acquisition with hook_type, key_line, first-frame visual hook, CTA. The funnel *starts* with
  the ad; paid traffic is 80%+ of 短剧 cost. A distinct sellable feature. + a `sales-package.md`
  selling-points output per episode.
- **Series scaffolding + paywall placement** — generate a 60-100 ep arc from one premise, mark
  the recommended pay gate at a cliffhanger after the first act.
- **Market/localization presets** — one story, many localized re-skins (werewolf/billionaire US,
  family/in-law SEA, telenovela-revenge LATAM, rebirth/战神 China).
- **Retention feedback loop** — surface per-episode drop-off + 3-second retention back to the
  creator so the tool teaches the cadence over time. (The flywheel; needs distribution/data.)
- **Pre-publish likeness safety gate** — YuNet+SFace similarity vs a public-figure library,
  screener-not-judge, at 3 checkpoints. Real legal protection at UGC scale.

### The learning flywheel (long-horizon moat)
Mine our own real production runs → craft cards → genre×mechanism coverage matrix → blind
fresh-agent forward-eval → promote to rubric/reference → ledger. (drama-skills' knowhow skill;
maps to flywheel.md.) How the platform's taste compounds instead of staying static.

---

## Part 3 — Monetization & unit economics (landed)
**These are not video apps; they are gacha-style payment funnels wrapped in melodrama.** The
content exists to manufacture a cliffhanger every ~2 min so the app can charge coins for the
next one. The whole business is a race between cost-to-acquire-a-payer and coins-that-payer-
spends — and **marketing, not production, is 80-94% of the cost.**
- **Why they pay:** coins → per-episode unlock → cliffhanger → buy more coins. First ~8-12 eps
  free, then every ep costs coins; unlocking a full ~80-ep series ≈ **$30-50**. Coins don't map
  cleanly to dollars/episodes (gacha obfuscation → over-purchase). VIP subs ($19.99/wk or
  $199/yr ReelShort; ~$5.99/wk DramaBox) are the whale tier. Ads-to-unlock strings non-payers
  along (China's Hongguo flipped fully to free+ad → 120M MAU in year one). Auto-unlock removes
  the last friction between cliffhanger and payment.
- **Unit economics:** blended ARPU ~$24/yr (ReelShort) vs ~$5.50 (DramaBox); payer conversion
  ~2-5%; ARPPU ~$40-90/yr; **whale-shaped** (top 5-10% of payers ≈ most IAP). Sessions ~25-36
  min/day. Retention D1 ~20-30%, D7 ~8-12%. Hits-driven portfolio — most series lose money, a
  few carry the slate.
- **The CAC fact (defining):** UA ("投流") eats 80-94% of revenue. The China hit "Wushuang":
  <500K RMB to produce, ≥80M RMB to market (~160×); producers net **as little as 3%** even on a
  hit. Overseas CPI $0.40-0.80/install, but cost-per-*paying*-user reaches $20-30. **~90% of
  budgets go to UA, ~10% to content.** Operators cut hundreds-to-thousands of ad hooks (the
  juiciest 30-90s) and let Meta/TikTok algorithms find payers — **the ad creative is the real
  storefront; the series is inventory to feed it.**
- **Production economics:** live-action $150-250K / 60-90 eps / 7-10 days; bought out by the
  platform. AI collapses this toward ~$0 and hours — but **AI attacks the 10% (production), not
  the 90% (marketing).** AI-animated dramas projected ~$650M in 2026 (~6× growth).
- **Market size:** China 微短剧 ~¥50.4B in 2024 (>China's box office), 576M users; ~¥100B by 2027.
  Overseas ~$3.2-3.6B gross 2025, 1.2B+ downloads, **US ≈ half**; download growth led by SEA
  (32%)/LATAM (23%)/India (22%). ReelShort ~$432M store-IAP / ~$600M-1.2B gross; DramaBox ~$370M.
  (Store IAP undercounts ~2× — apps push web checkout to dodge the 30% cut; use gross for TAM.)
- **Who makes money:** platforms win (own distribution + payment funnel). Studios scrape ~3-15%.
  **Writers are the only craft role that earns real money** (rev-share on hits). **Individual/
  UGC creators get essentially nothing today — no YouTube-style payout exists.** That absence is
  the opening *and* the trap: a tool that only lets people *make* dramas produces content with
  no monetization path — a hobby, not a business.

## Part 4 — Studios vs. UGC: can millions really take on the studios? (landed — HONEST verdict)
**The blunt finding (both the strategy and monetization agents converge, red-teamed):**
1. Cheap AI commoditizes **production — the thing that was never the moat** — and leaves the real
   moats (**distribution, UA capital, the payment relationship, conversion data**) fully intact.
   A create tool alone *exports* value to whoever owns the audience.
2. **"Millions take on the studios" is a distribution claim, not a creation claim.** People can
   already *make* dramas; they can't *reach* or *get paid by* an audience. AI doesn't lower CPMs
   or build a feed.
3. **The TikTok/YouTube analogy is the anchoring trap.** Those run on free+ad+organic economics;
   short drama's native economics are paywall+paid-UA — the opposite. If our Watch feed goes
   ad+organic, our competitor stops being ReelShort and becomes TikTok/YouTube themselves — a
   worse fight. The honest anchor is **fanfiction/webnovels (Wattpad/AO3): niche, community-
   tuned, serialized, creators+audiences who show up and return without being bought.** The bet
   is **"AI Wattpad-for-video," not "TikTok for short drama."**

**Niche/community taste — real wedge or trap?** Both. A trap in the incumbent model (a title
must clear paid-UA cost → niche = too few payers → unservable, which is *why* studios only make
mass tropes). A **real, defensible wedge** only if BOTH hold: (1) creation cost→0 (✓) AND (2) an
**organic, zero-marginal-cost, per-community matching engine** (we don't have it). Under those,
niche becomes the thing the UA-funded studios *structurally cannot serve.*

**The three options, honestly:** (a) create-tool-for-studios = a contested feature that
strengthens the incumbents we can't out-distribute (revenue yes, moat no); (b) UGC platform +
Watch/distribution = the only path that builds a real moat, but 80% of the problem is the
demand side we don't have; (c) our own AI studio = red ocean at ROI ~1.05 where 80-90% lose
money (a bootstrap *tactic* inside (b), never standalone). **None stands alone.** The org's
already-ratified structure is correct: **Watch = the business, Create = the wedge/supply engine,
API = a low-touch cash rail; north star = qualified weekly returning viewers; kill-gate on D7
retention.** The scariest competitor isn't ReelShort — it's **Hongguo/ByteDance** (356M MAU, an
organic feed, folding AI dramas in): they race us from the hard side (demand); we'd race them
from the easy side (supply).

**What this means for what I'm building (the Create layer):** a better create tool is needed in
*every* scenario, so building it is not wasted — but two things must change in emphasis so we
don't ship "a better shovel for a gold rush whose gold the platforms own":
- **The killer feature isn't the episode — it's the episode + 50 paywall-ready ad hooks.** Ad-
  creative is what the business runs on. "Make the drama" and "make the ad cuts" ship as one
  motion. → P2 ad-clip exporter moves UP.
- **Bake the money-shape into the output:** episodic, cliffhanger-timed, first-~8-free/paid
  split, coin-unlock metadata, a `sales-package.md`. A generic "make a video" tool misses the
  shape of the money.
- **Don't pretend Create is the moat.** It's the supply engine. The moat is Watch — a separate,
  already-ratified bet whose make-or-break is cold-start retention, not any create feature.

**Escalation for Dee:** your instinct (empower millions, niche/community taste) is the *right
wedge* — but it only "takes on the studios" if paired with the organic distribution + Continue/
remix layer (the ratified Watch bet), not from the create tool alone. I'm building Create to be
the best possible supply engine *and* money-shaped (ad hooks + paywall structure), which serves
every path. The true fork — how hard/when to build the Watch/distribution layer and whether to
seed our own slate — is yours; the research says it's the only thing that makes "millions vs.
studios" real.
