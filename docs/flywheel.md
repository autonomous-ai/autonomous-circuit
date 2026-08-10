# The Flywheel — how Autonomous TV self-loops, self-evaluates, self-learns

`film-crew.md` is the crew that *makes* a film (a line: prompt → film). This is what
makes it a **platform that gets better every time it's used** (a loop: film → feedback →
learning → better film). A good loop needs a real signal, a real evaluator, a real reward,
and an action that changes the next output. Nest several of these at different timescales
and quality compounds — a flywheel, not a pipeline.

Every loop has the same shape: **produce → evaluate (signal) → score (reward) → act →
produce again.** The roles below are the evaluators/learners; the crew are the actors.

## The nested loops (inner → outer, fast → slow)

### 1. Production loop — per shot / per film (built + building)
Generate → **critic (screening-room)** watches, scores the rubric, routes department notes
→ crew acts (reroll shot, re-time, fix orientation, rewrite beat) → re-render → re-screen,
until it clears the bar. Signal: the critic's rubric + mechanical defects. This is the
director-watching-dailies loop. *(screening-room in flight.)*

### 2. Variant-and-select loop — per high-leverage element (BACKBONE BUILT)
For the elements that decide a film's fate — the **first-3s hook**, the title, the poster/
thumbnail, a hero shot — generate **N variants** and let the critic (now) and the audience
(later) pick the winner; the winner ships, the losers become training signal. Short-drama
companies live on this (they A/B hooks/thumbnails/ad-creative). Role: **the test-screening
optimizer.** Signal: comparative critic/audience preference.
**Shipped:** `dramalib.evals.rank_variants` / `best_variant` (produce→score→pick) driven by
`binge_reward` (the script-stage reward signal). Next: wire the audience reward (loop #8)
in as the scorer when the network ships, and add a render-stage variant race on the critic.

### 3. Taste loop — per creator (BACKBONE BUILT)
Capture every human accept/reject/note/reroll and learn **this creator's taste** — their
palette, pacing, voice, tropes, what they kill — into a persistent **taste profile** that
biases the crew's defaults for that creator. This is why Claude Code feels personal (it
adapts to your codebase); ours adapts to your eye. Role: **the creative partner that learns
you.** Signal: the creator's own choices. Adoption driver #1.
**Shipped:** `dramalib.taste` — `TasteProfile` + `observe` (fold accept/reject/kill/note) +
`bias_scaffold` (bias the onboarding defaults to the creator's eye; bias, never override).
Next: persist one profile per creator and capture the UI's accept/reject/reroll events into it.

### 4. Model-routing / R&D loop — per shot-type (BACKBONE BUILT)
Record, from critic scores, **which model+params win for which shot-type** (establish vs
dialogue vs action vs VFX) and route accordingly. When a new model drops, auto-benchmark it
against a **golden set** and adopt it if it beats the incumbent. This is how we "always use
the best of the best" without manual chasing. Role: **the technical director / R&D.**
Signal: critic scores per model on the golden set. Compounds as the model market moves.
**Shipped:** `dramalib.routing` — `record_outcome` / `best_model` / `route` (learn the winning
model per shot-type from critic scores; min-trials guard against noise; cold-start with the
research defaults per type). Next: feed the screening-room critic's per-shot scores into it and
have the provider read `route(shot_type)` at dispatch (roadmap R2).

### 5. Quality-eval / CI loop — per platform change (BINGE CI BUILT)
A standing **golden set** of representative prompts (across genres) rendered on every
material change, scored by the critic, with score trends tracked. No change ships that
regresses quality; every improvement is *measured*, not asserted. This is the platform's
CI-for-quality and the reward source for loops 3–4. Role: **the QA lead.** Upgrade
`evals/run.py` from structural checks to critic-scored quality.
**Reward source now exists:** `dramalib.evals.binge_scorecard` / `binge_reward` is the
script-stage reward (the binge axis), complementing the screening-room craft critic.
**Shipped:** `dramalib.golden` (a fixed strong/weak golden set + `check_golden`, invariant-based)
runs as the `binge-golden` case in `evals/run.py` — no change ships that stops the binge
reward separating strong from weak. Next: track the reward *trend* over time, and add the
critic-scored render-stage golden set on top of the script-stage one.

### 6. Craft-learning loop — cross-film (build after outcomes exist)
The slow, compounding one. Mine outcomes (critic scores + human taste + audience data) for
**what actually works** — which hooks, trope stacks, pacing, shot grammar, score arcs win —
and fold it back into the **craft library** (dramacode's tables/patterns, the trope
templates, the prompt recipes) so the platform's *defaults* get better for everyone. Role:
**the showrunner / creative exec** running the writers' room across the whole slate. Signal:
aggregated rewards. This is the institutional-knowledge flywheel.

### 7. Institutional-memory loop — cross-film (BACKBONE BUILT)
Winning character bibles, keyframes/seeds, shot recipes, prompt snippets → a **reusable
studio library**. Reuse compounds quality *and* cuts cost (don't re-derive what worked).
Role: **the studio archive.** Feeds loops 3, 4, 6.
**Shipped:** `dramalib.institutional` — `CraftCard` + `InstitutionalMemory` + `promote` (a
pattern becomes a rubric candidate only with support + a consistent positive outcome — no
promotion on one anecdote) / `retire` (consistently-harmful patterns) / `coverage` (where we
lack evidence). Needs real runs to mine — the store + promotion discipline are ready for them.

### 8. Audience-reward loop — the ultimate signal (design now, wire with the network)
Once the Shows network is live, real **retention / completion / watch-time / re-watch /
shares** are the true reward — the box office. It's the strongest signal and it feeds loops
2, 3, 6 (learn what real viewers can't stop watching). Role: **the audience / the market.**
Design the hooks now (the critic's rubric is a *proxy* for this until it exists); wire the
real signal when the network ships. This is what ultimately makes the platform learn taste
better than any human studio.

## Why this is the moat
A line can be copied. A **flywheel that has run 100,000 films** — with a critic tuned on
outcomes, model routing proven on a golden set, per-creator taste profiles, a craft library
mined from what audiences actually watched, and a reusable studio archive — cannot. Every
film makes the next one better and cheaper. That compounding is the platform, and it's the
reason a producer adopts us and stays: the tool gets better at *their* films the more they
use it, the way Claude Code gets better at your codebase.

## Build order (loops)
1. **Quality-eval / CI loop (5)** — on the critic; the reward source. Golden set + critic-scored evals + trend tracking.
2. **Model-routing / R&D loop (4)** — record per-shot-type winners; a bench harness for new models. "Always the best."
3. **Taste loop (3)** — capture accept/reject/notes → taste profile → biases defaults. Adoption.
4. **Variant-and-select (2)** + **institutional memory (7)** — cheap, compounding.
5. **Craft-learning (6)** — once enough outcome data exists.
6. **Audience-reward (8)** — hooks now, wire with the Shows network.

## Rule
Never ship a "line" where a loop belongs. Any place the platform produces something judged
good-or-bad, there must be an evaluator, a reward, and an action that changes the next
output — and the reward should, wherever possible, trace back to the real audience.
