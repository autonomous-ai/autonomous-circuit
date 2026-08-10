# Zero-friction creation: from bingeing to your own series in three taps

The audience is the creator — a stay-at-home mom, a college student in Lagos, a
grandma in rural Nebraska, a girl in Shenzhen. They already love short dramas.
The only thing between them and making one is fear of the blank box. This doc is
how we remove it.

The bar: **a first-time user watches THEIR OWN drama within 60 seconds of
opening the app, having typed nothing.** Everything below serves that one number.

## The blank box is the churn cliff

A text prompt box is a test. It asks a non-writer to be a writer, on the spot,
with no example. Most people freeze and leave. Every creation tool that leads
with "describe your idea" loses the exact people we want.

So we never show one. Typing is always *optional* and never *first*. The first
screen is not a form — it's a feed of dramas you could make yours with one tap.

## The four principles

1. **Never start from nothing — start from a hit.** The home screen is a
   vertical, autoplaying feed of bingeable series (like the consumption feed,
   because it *is* the consumption feed). Every one has a single button: **Make
   my version.** You are always editing something that already works, never
   authoring from zero.
2. **Tap, don't type.** Every choice is a card you tap, with a strong default
   already selected and a **Surprise me** escape hatch. You can reach a finished
   seed by tapping "Next" three times without a single decision.
3. **Personal stake first.** The single strongest hook in the genre is the
   self-insert — the viewer already fantasizes being the lead. So let them *be*
   the lead: put their name (or their face, or a friend) into the story before
   anything else. Stake is what turns a demo into "oh my god, that's *me*."
4. **Aha before ask.** Show a watchable artifact — a 15-second trailer of *their*
   version — before requesting any real work or spend. Commitment is earned by
   the aha, never demanded before it.

## Three doors, one engine

Different people arrive ready for different first moves. We offer three on-ramps;
all three converge on the same series-bible generator (`dramalib.onboarding.
series_bible`), so the machinery is shared and the choice is only about comfort.

- **Door 1 — Remix a hit (the default).** Tap a series you love → "Make my
  version" → it's yours to twist. ~70% of users should enter here. Backed by
  `dramalib.remix.remix_bible` (recast the lead, move the setting, flip the
  tone) — the "reference an existing series + character swap" flow.
- **Door 2 — Surprise me (one tap).** One button returns a complete,
  watchable, locale-tuned series seed. For the fully overwhelmed. Backed by
  `dramalib.starters.surprise_me`.
- **Door 3 — Tell me a feeling (the 3-card wizard).** For people with a spark
  but no words. Three feeling-cards, no jargon (below). Backed by the existing
  `INTAKE_QUESTIONS` → `series_bible`.

## The killer feature: put yourself in it (character swap)

Recasting is the aha accelerator. On any series, "Make my version" opens a cast
strip where each role is a tap-to-recast slot:

- **Be the lead.** Type a name, pick an avatar, or (with consent) use a selfie.
  The self-insert is now *you*. This is the emotional detonator — the fantasy
  genre already sells "you, but powerful"; we make it literal.
- **Cast the villain.** "Who underestimated you?" Name the boss, the ex, the
  in-law. (Real people only with consent; public figures blocked — see
  `dramalib.safety.likeness_gate`.)
- **Keep the rest.** Untouched roles inherit the original's archetypes, so a
  swap is never more than one tap of real work.

The story spine (trope, beats, gates) is preserved by default, so a recast can't
break the thing that made the original bingeable. You change *who it happens to*,
not *whether it works*.

## The 3-card wizard (Door 3), in full

Three questions, each a screen of big tappable cards, each pre-answered with a
smart default and a **Surprise me** chip. Feeling-words only — no "genre",
"trope", "beat", "arc".

1. **Who do we root for?** → cards: *me* · *a woman who's had enough* · *a quiet
   guy everyone overlooks* · *type a name*. (sets the self-insert)
2. **What did they do to them?** → cards: *dumped them* · *fired them* · *laughed
   at them* · *stole what's theirs*. (sets the ep-1 wound)
3. **What do they secretly want?** → cards: *to be adored* · *to win it all back*
   · *to make them regret it* · *to be seen*. (sets the drive / the payoff)

Three taps → the AI writes the bible + episode 1 live on screen (the wait is
shown as the story being written, which is delight, not a spinner). Then the
trailer autoplays. That is the aha.

## The aha ladder (progressive commitment)

Never ask for the big thing first. Each rung is earned by the previous payoff,
and real spend is gated until the user has already fallen in love.

1. **Tap a hit** (0 friction) — no account, no prompt.
2. **See it becoming yours** — title morphs to include the user's name/hero; the
   poster recasts. Instant, free, no render.
3. **Watch the 15-second trailer** — the aha. A stitched keyframe/animatic
   montage or one hero shot; cheap or free, fast.
4. **Make episode 1** (the first real render) — offered only now, with the
   plan-and-approve spend gate (`dramapy.plan`).
5. **Make the season** — the ~50-episode commitment, offered after ep 1 lands.

Account creation is deferred to rung 4 (save/share), never demanded at the door.

## The consumption → creation bridge

The people who create are the people who binge. So the *watch* surface (the TV
feed) carries a persistent, one-tap **Make my version** on every episode. No
mode switch, no "go to the studio" — the feed you scroll for fun is the same
feed you create from. This is the TikTok insight: the content IS the prompt.

## Growth loops (creation that spreads itself)

- **Remix chains.** Every published series is itself remixable; each shows
  "1,204 people made their own version." The self-insert genre is *built* for
  this — everyone wants to be the lead, so everyone remixes.
- **Share the trailer, not the app.** Output is a vertical trailer with a "make
  yours" end-card deep-link. Every share is both an ad and a one-tap on-ramp.
- **Cast someone you know.** "Put your ex as the villain" / "make your best
  friend the CEO" — the highest-emotion, highest-share creation act (consent-
  gated).
- **Today's seed.** A fresh, locale-tuned remixable starter drops daily — a
  reason to return and a streak to keep.
- **The 60-second challenge.** "Make a drama before your coffee's done." Framing
  the whole act as tiny and winnable beats framing it as "author a series".

## Localization: the gallery already knows you

Zero config. On first open we tune the starter gallery by locale + language so
the very first cards feel native:

- **Nebraska grandma** → small-town secrets, second-chance-at-love, family-land
  feuds.
- **Shenzhen girl** → 霸总 (domineering CEO), 战神 (returning war-god), 甜宠
  (sweet doting), 逆袭 (underdog rise).
- **Lagos student** → rags-to-riches, campus rivalry, family-honor comeback.
- **São Paulo mom** → telenovela betrayals, long-lost-heir, forbidden love.

Same engine, reordered defaults. The user never picks a "region" — the feed just
already looks like their taste. Backed by `dramalib.starters` locale maps.

## What exists vs. what we build

Already shipped (the engine): `INTAKE_QUESTIONS` + `series_bible` (feeling →
bible), `archetypes_for` (auto-cast), `title_candidates` (name it), `plan` (the
spend gate), `safety.likeness_gate` (recast consent), `metrics` (the binge
ground-truth).

To build for zero-friction (this doc's deliverables):

- **`dramalib.remix`** — reference a series + swap cast/setting/tone → a new
  personalized bible without breaking the spine. *(Door 1, the killer feature.)*
- **`dramalib.starters`** — a curated one-tap starter gallery seeded from the
  hit teardowns, `surprise_me()`, and locale ordering. *(Doors 1 & 2 + localization.)*
- **The web wizard** — the 3-card flow, the cast strip, the aha-ladder gating.
  *(Front-end, on the shared engine.)*

## The metrics that prove it worked

- **TTFW — time to first watch:** open → watching your trailer. Target < 60s.
- **Taps to aha:** target ≤ 3.
- **Blank-box rate:** share of creations that ever touched the text box. Lower is
  better; typing is a power feature, not the path.
- **Remix rate:** share of new series that are remixes of an existing one.
- **Trailer share rate** and **remix-of-a-remix depth** (the viral coefficient).

The whole design reduces to one loop: *watch something you love → one tap → it's
about you → watch that → share it → someone else taps it.* Creation disappears
into consumption. That's zero friction.

## Status — shipped, and what's next (2026-08-10)

**Shipped into the web app** (`viewer/src/client/components/create/*`, wired into
the chat sidebar's empty state; verified live at `:4178`, both desktop and phone
widths; viewer suite green):

- The blank prompt box is gone — the create surface opens on a tappable **starter
  feed** (poster cards, genre badges, loglines, one-tap "Make my version").
- **Surprise me** promoted to a one-tap hero (the zero-decision path).
- **Clone a drama you love** — name a show + your characters → an *original* in
  that style (never a copy); Enter-to-submit.
- **Self-insert** — optional name field; card CTAs update live ("You're X — make it").
- **Localized feed** by `navigator.language` (Shenzhen / Brazil / West Africa lead).
- **Popular** badges (honest curation, not fake counts) to cut first-tap paralysis.
- **Mobile**: the panel goes full-screen under 640px (was clipped off-screen);
  touch-friendly targets; viewport width cap.
- The composer's placeholder now guides with an example instead of a bare label.
- Post-tap activity already reads human ("Writing the episode → Casting →
  Rendering shots") via `activityLabels.js` — left as is.

**Next — needs live turns or a product/hosting decision, not more front-door polish:**

1. **The install wall (biggest real barrier).** A first-run non-producer must have
   the `claude` CLI + ffmpeg installed in a terminal (`WelcomeScreen` prereq gate).
   For a true mom/grandma audience this needs a hosted path (no local install) —
   a product/hosting call, above the UI layer.
2. **Post-tap reassurance on the FIRST turn** — a warm, non-technical "we're
   writing your episode…" state that doesn't duplicate the turn header. Needs a
   live turn to design against real timing/output.
3. **The aha ladder in-app** — after ep 1 renders, offer "make the whole season";
   gate real spend behind the draft→premiere tiers (engine ready in `dramapy.tiers`).
4. **See-before-commit trailer** — the draft-tier 15s preview as the aha, before
   any season spend. Needs the live render path (fal).

**Open product question:** should tapping a starter always start a *fresh*
project? Today `startFromBrief` reuses the open project when one is active, so a
starter tapped while a content-bearing (but chat-empty) project is open would
append that drama's turn to it. Fine for a brand-new session; potentially
confusing when an existing show is open. Leaning "one starter tap = one new
drama" — Dee's call before changing the behavior.

**Known correctness bug (needs a live turn to fix safely):** the above isn't
just cosmetic — tapping a starter while viewing a project that already has a
rendered show injects the new drama's turn into that show. The safe fix is to
start a fresh project when the open one has catalog content, but detecting that
reliably depends on catalog load timing, and the create-turn/project routing
can't be verified offline (no live `claude` turn in CI). Fix alongside the live
post-tap work, not blind.

## Create-UX loop — shipped log (2026-08-10)

One-tap starter feed · Surprise-me hero · clone-a-drama (original-in-style, live
genre preview, Enter-to-submit) · self-insert name (persisted + rehydrated across
flows) · localized feed order · auto-generate in the viewer's language + a
transparency line · Popular badges · live CTA feedback · mobile full-screen +
touch targets + width cap · guiding composer placeholder · screen-reader labels ·
friendly failure notice · app-wide prefers-reduced-motion · ffmpeg install
copy-button. All green (viewer 318, dramapy 217, dramacode 117); front door is
feature-complete for offline work — further gains need live turns or the
decisions above.
