# The film rubric — 8 dimensions, scored 1-10

Load this when scoring a screening. Each dimension is scored **1-10**. The
anchors below keep the numbers honest: **most first cuts sit at 5-7.** Reserve
9-10 for work that is genuinely, specifically excellent. A wall of 8s means you
stopped watching.

The overall score is your judgment, not an average — a single blocker
(a rotated shot, a dead hook, a face that changes) caps the film low no matter
how the other rows read.

## The bar (what `pass_at_bar` means)

`pass_at_bar = true` **only** when ALL hold:

- `overall_1_10 >= 8`, AND
- **no technical defect** — the `technical` dimension is clean and the bundle's
  `defects[]` carries nothing, AND
- `consistency >= 7` — the subject reads as the same subject across the cut.

Miss any one and `pass_at_bar` is `false`. The bar is deliberately high: this is
the gate that keeps mediocre episodes from shipping.

---

## a. Hook (`hook`) — first 3 seconds

Does the open seize attention before a scrolling viewer flicks past? 80% decide
in ~6s.

- **9-10** — the first frame is a question or a shock you must resolve; conflict
  or extreme contrast is already on screen at t=0.
- **7** — a strong image, but the hook lands at 2-3s rather than 0.
- **4** — a slow establishing pan, a logo, exposition before anything happens.
- **1** — nothing is at stake by 3s.

## b. Story / emotional impact (`story`)

Does it land the gut-punch? Plot is watchable; heartbreak is memorable. Judge
against the script's intended bond and turn (`source.episode_source`).

- **9-10** — the planted bond is spent on one irreversible turn and you *feel*
  it; the last beat re-prices everything before it.
- **7** — the turn happens but is under-set-up or under-felt.
- **4** — events occur; nothing is at stake emotionally.
- **1** — no bond, no turn, no reason to care.

## c. Pacing / retention (`pacing`)

Would you keep watching? Where does it drag?

- **9-10** — every shot earns its place; a new feeling every 15-25s; no dead
  air; the cut accelerates into the cliffhanger.
- **7** — mostly tight, one or two shots overstay.
- **4** — a visible sag (name the shots); the middle loses you.
- **1** — you'd have scrolled away.

## d. Character & world consistency (`consistency`)

The platform's hardest problem and the #1 amateur tell. Same **faces, bodies,
wardrobe, hair** for each character; same **recurring subjects** (dragons,
creatures, vehicles), **hero props**, and **locations/sets** across shots.

- **9-10** — every recurring subject is unmistakably the same across every
  shot.
- **7** — one minor drift (a wardrobe/lighting shift), no identity confusion.
- **4** — a character reads as a *different person* in at least one shot — **name
  the shot ids**.
- **1** — the subject changes constantly; you can't track who is who.

Always cite the drifting shot ids in the note. This usually routes to `cast`
(reroll pinned to the reference) or `vfx`.

## e. Cinematography (`cinematography`)

Composition, shot-size variety, lighting, framing.

- **9-10** — deliberate composition; the five scales (ECU/CU/MCU/MS/WS) vary
  across the cut; lighting keys the mood.
- **7** — competent but safe; some same-scale runs.
- **4** — flat framing, a wall of the same shot size, muddy or mismatched light.
- **1** — accidental framing; subject cut off or lost.

## f. Audio (`audio`)

Dialogue clarity, score fit, SFX on the peaks. Use `audio_stats` +
`Read`-what-you-can, and judge intent from the source (does a slap have its
crack; does the score cut for the gut-punch).

- **9-10** — dialogue clear over a ducked score; SFX on every peak; silence used
  deliberately at the turn.
- **7** — balanced but generic; a peak or two unscored.
- **4** — dialogue buried, score fights the cut, or peaks are silent.
- **1** — no audio where there should be, or it actively distracts.

If `voice_expected` but `has_audio` is false, that is a **technical** blocker,
not just a low audio score — route it to `sound`.

## g. Continuity (`continuity`)

Props, positions, eyelines, time-of-day, weather shot-to-shot.

- **9-10** — the world holds; nothing pops between shots.
- **7** — one small slip a viewer likely misses.
- **4** — a visible jump (a prop appears/vanishes, day→night mid-scene) — name
  the shots.
- **1** — continuity is incoherent.

## h. Technical (`technical`)

Orientation/aspect, artifacts, black/frozen frames, duration drift, missing
shots. **Start from the bundle's `defects[]`** — it detects these mechanically.

- **9-10** — clean master: correct 9:16 orientation, no artifacts, durations on
  spec.
- **1-3** — any orientation/aspect defect (the rotation bug), a missing shot, a
  black frame, or silent dialogue. **Any of these fails the bar.**

Every technical defect is at least a `blocker` note, routed to `technical` (or
`vfx`/`sound` when a re-render of a specific shot is the fix).

## i. Shareability / clip-ability (`shareability`)

How many **cuttable, share-worthy moments** does the cut contain — the beats you
could pull as a standalone 15-30s ad hook or a screenshot-worthy 金句. This is
the metric the whole short-drama business runs on: paid acquisition is 80-90% of
cost, and it's fed by ad-hooks cut from the episode. A cut with no liftable
moment is inventory that can't be marketed, however well-made.

- **9-10** — several self-contained hooks (a slap, a reveal, a devastating line);
  each would stop a scroll on its own; an obvious screenshot line.
- **7** — one strong liftable moment, the rest is connective tissue.
- **4** — watchable in sequence but nothing survives being cut out of context.
- **1** — no moment lands without the full setup; unmarketable.

**Not a hard bar gate** — a quiet, aching episode can be great and score low here.
But flag it: a low `shareability` with a strong `story` routes a `minor`/`major`
note to `editor`/`writer` — "add a liftable hook" — because on this platform an
un-clippable episode won't find its audience. See the ad-cut export in
`references/department-routing.md` when it exists; for now the note is the signal.
