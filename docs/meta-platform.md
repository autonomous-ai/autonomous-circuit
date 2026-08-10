# Meta-platform — the machinery that makes anyone create bingeable short dramas

Dee (2026-08-09, /loop): "think about the meta platform things — the eval team, studying
the ideal users and their behaviors, etc. What else do we need to add/study/research/build so
the platform gives non-producers the best tools to create the most amazing **bingeable** short
dramas? Binge is the key — these short dramas are so damn bingeable. What is their secret
sauce? Can we package that in our tools so anyone can create bingeable short-drama series just
by chatting with our AI?"

**Scope decision (locked): short-drama SERIES only. No long films.** That's where the growth
is. The unit is a **~50-episode series, 90-120s per episode** (Dee's spec; verified against the
market study — observed mode is 60-100 eps at 60-120s, we target the tighter, higher-completion
50 x 90-120s ≈ 75-100 min total). The roadmap's "prestige short-FILM / Oscar single-film" axis
is deprioritized; the axis that matters is **bingeable series**.

## The thesis this loop serves

Craft quality is table stakes; **binge is the product.** The platform's job is to let a
non-producer — a stay-at-home mom, a student in Brazil — type a premise and get an *bingeable*
50-episode series, because the tool supplies the binge machine they don't know exists. So
the meta work is: (1) name the secret sauce precisely, (2) make it **measurable** (an binge
score), (3) make it **automatic** (the tools apply it by default), (4) build the **eval + reward
loops** that optimize it, and (5) understand the **users** on both sides so the tool speaks their
language and defaults to their taste.

## How it all fits — the create flow (capstone, after iter 12)

The meta-elements aren't a pile of features; they're one pipeline a non-producer's
chat flows through. Each stage names its doctrine (reference) + backbone (code).

1. **Onboard** a vague pitch → `references/ideal-users.md` + `references/onboarding.md`
   + `dramalib.onboarding` (INTAKE_QUESTIONS, series_scaffold) → biased by
   `dramalib.taste` (bias_scaffold) for a returning creator. *Propose a whole
   ~50-ep series; never a blank page.*
2. **Author** the series → `references/binge-engine.md` (the compulsion loop,
   loaded first) + `genre-playbook.md` + `emotion-to-action.md` + the beat law +
   `dramalib.helpers`. *The binge machine runs underneath; the creator steers
   by feel.*
3. **Eval (script stage, pre-spend)** → `references/binge-eval.md` +
   `dramalib.evals` (binge_scorecard proxy + combine_binge with the LLM
   judgment → one verdict) + `dramalib.safety.compliance_scan`. *Score compulsion
   before spending render money.*
4. **Improve** → `dramalib.evals.rank_variants`/`best_variant` (variant-select) +
   `binge_rework` (scorecard → prioritized fixes). *Generate N, keep the most
   bingeable; fix the weak spots mechanically.*
5. **Render + craft-eval** → cast/world consistency (`cast-book` + `dramapy.refstack`)
   → `screening-room` (the pixel critic, keep-the-best convergence) →
   `dramalib.safety.likeness_gate` at the 3 checkpoints. *Finish a consistent,
   defensible cut.*
6. **Package + distribute** → `references/patterns/ad-cut-sheet.md` (投流表 + sales-
   package) + `patterns/paywall-gate-episode.md`. *Ship the episode AND the ad-
   hooks that sell it.*
7. **Learn** → `dramalib.taste` (per-creator) + `dramalib.golden` (CI: no change
   regresses the reward) + `dramalib.metrics.diagnose_metrics` (real audience
   signal → the fix, when the network ships). *The platform compounds.*

The through-line: **chat → propose → score → improve → render → screen → package →
learn.** Binge is default-on at every stage; the creator only supplies taste.

**Testing note:** each skill is a sandboxed unit with its own `dramalib`/conftest —
run the skill suites **separately** (`pytest skills/<skill>/tests`), not in one
pytest process (collection collides). Gates: dramapy 188, dramacode 76,
screening-room 7, cast-book 9, evals 6/6, viewer 36 — all green at iter 12.

## What "meta" means here

Not craft doctrine (that's dramacode's references) and not one series. Meta = the *machinery
around* the create tool: how it evaluates itself, how it learns, who it's for, how it onboards,
how it measures binge, how it improves without us. This doc is the **loop's brain** — the
backlog + the record of what each iteration built.

---

## The meta-element backlog (the loop works this list)

Status: ☐ todo · ◐ in progress · ☑ shipped. Each element: what it is · why it matters · where it lands.

1. **☑ The binge engine** — the secret sauce named as ONE system (the compulsion loop:
   3s hook → 爽点 cadence → 虐→爽 → per-episode cliffhanger → paywall-at-craving → variable
   reward → parasocial bond), *why* it's bingeable (the psychology), and a **measurable
   binge score** the tools optimize. Lands: `dramacode/references/binge-engine.md`
   (doctrine) + feeds the eval (score). This is the "secret sauce in a bottle."

2. **☑ The eval team** — a standing evaluator for the BINGE axis, distinct from the
   screening-room craft critic and run at the SCRIPT stage (before render spend). Shipped:
   `dramalib/evals.py` (deterministic pre-check — coarse proxy scores for hook_strength/
   payoff_cadence/cliffhanger_pull/pacing_fit + honest `judgment` placeholders for
   wish_fulfillment/bingeability/clip_ability; flags for episode-duration band, emotional
   flatness, series length) + `references/binge-eval.md` (the two-axis/two-stage model, the
   judgment layer, the gate, the auto-rework map). Wired into SKILL.md; 5 tests.
   **Still open (future iterations):** the *judgment* score as an explicit LLM eval role/skill;
   a **retention predictor** trained/checked against real viewer data; an **eval CI** that runs
   the scorecard on every generated series and gates in the driver loop.

3. **☑ The ideal-user model** — shipped `references/ideal-users.md`: creator personas (the
   binge-watcher mom, the fanfic writer, the student in Brazil, the hustler) — what they can/
   can't articulate and how the tool must talk to them (no jargon, 2-3 feeling questions,
   default aggressively, steer by feel); viewer personas the tool designs *for* on the creator's
   behalf; the bridge (feeling → viewer → engine → loop). Wired into SKILL.md as tier-0.
   *Open (future): fold the creator-intake questions into a helper for the onboarding flow (#4).*

4. **☑ Chat-first onboarding / the "just chat" flow** — shipped `dramalib/onboarding.py`
   (`INTAKE_QUESTIONS` — three jargon-free feeling questions; `series_scaffold` — a ready
   default ~50-ep series composing the trope spine + gate plan + episode-length canon, so the
   creator never faces a blank page) + `references/onboarding.md` (the four moves: intake →
   propose scaffold → first draft fast → steer by feel; never interrogate). Wired into SKILL.md
   as tier-0. 5 tests.

5. **◐ The reward / feedback loops (the flywheel)** — reward signal + variant-select SHIPPED:
   `dramalib.evals.binge_reward` (scalar reward = proxy scores − flag penalties) +
   `rank_variants`/`best_variant` (produce→score→pick — flywheel loop #2, made real). flywheel.md
   loops #2 and #5 updated to point at the shipped code. *Still open (future): the taste loop
   (#3 — per-creator profile from accept/reject/notes), the eval-CI golden set (#5 wiring), and
   the audience reward (#8) as the scorer once the network ships.*

6. **☑ Retention/binge instrumentation** — shipped `dramalib/metrics.py` (BINGE_METRICS:
   the canonical metric names + directional target bands + the eval dimension each grades;
   `diagnose_metrics`: real signal → underperforming dimension → the fix) + `references/
   retention-metrics.md` (the ground-truth that defines "bingeable"; next-episode-start is the
   one to watch; why spec it before we have data — reward becomes swappable). Wired into SKILL.md.
   Pre-wires flywheel #8. 4 tests. *Open: emit these events from the network when it ships.*

7. **☑ Safety at UGC scale** — shipped `dramalib/safety.py` (`likeness_gate` — pluggable
   screener-not-judge, returns `not_screened` never a false `pass`, verdicts pass/regenerate/
   escalate/licensed_exception, 3 checkpoints; `compliance_scan` — deterministic text red-lines
   + banned terms) + `references/safety-gate.md` (the posture, the 3 checkpoints, provenance/
   C2PA, legal anchors PIPL/GDPR/EU-AI-Act/§3344). Wired into SKILL.md. 5 tests. *Open: wire the
   real face-embedding screener backend + a public-figure library; C2PA at export.*

8. **☑ The binge-score → auto-rework loop** — shipped `dramalib.evals.binge_rework`
   (scorecard → prioritized, act-on-able fix list: structural flags first, then weak proxy dims,
   each with its pattern/lever) + `REWORK_FIXES`/`FLAG_FIXES` as the single source of truth
   (metrics.py now imports it — DRY). Closes eval → reward → fix in code. *Open: wire it as a
   script-stage gate in the driver loop (the analog of the render-stage screening loop).*

---

## How this loop runs (each iteration)

Pick the next element → research/think it through from first principles + the market study →
**build the artifact** (doc + reference/skill/helper where concrete) → verify (tests green) →
commit → check it off here → schedule the next iteration. Compounding, one element at a time —
not a single mega-build. Keep it honest: name what's a spec vs. what's shipped, and don't
goal-seek "bingeable" to a wished-for number — measure it.

## Iteration log
- **Iter 1 (2026-08-09):** locked scope to short-drama series (50 x 90-120s, dropped long films;
  canon updated in tables.py). Wrote this charter. Built element #1 — the binge engine
  reference (`dramacode/references/binge-engine.md`), the secret sauce as one measurable
  system, wired into SKILL.md.
- **Iter 2 (2026-08-09):** built element #2, the eval team — `dramalib/evals.py` (script-stage
  binge pre-check: proxy scores + honest judgment placeholders + structural killer flags) +
  `references/binge-eval.md` (two-axis/two-stage model, judgment layer, gate, auto-rework
  map), wired into SKILL.md. 47 dramacode tests green (+5).
- **Iter 3 (2026-08-09):** built element #3, the ideal-user model — `references/ideal-users.md`
  (creator personas + how to talk to them with no jargon; viewer personas the tool designs for;
  the feeling→viewer→engine→loop bridge), wired into SKILL.md as tier-0.
- **Iter 4 (2026-08-09):** built element #4, chat-first onboarding — `dramalib/onboarding.py`
  (INTAKE_QUESTIONS + series_scaffold: a ready ~50-ep default so no blank page) +
  `references/onboarding.md` (intake → propose → first draft fast → steer by feel), wired into
  SKILL.md as tier-0. 52 dramacode tests green (+5).
- **Iter 5 (2026-08-09):** built element #5 backbone, the reward/variant-select flywheel —
  `dramalib.evals.binge_reward` + `rank_variants`/`best_variant` (produce→score→pick on the
  binge reward); flywheel.md loops #2/#5 updated to the shipped code. 55 dramacode tests
  green (+3).
- **Iter 6 (2026-08-09):** built element #6, retention/binge instrumentation —
  `dramalib/metrics.py` (BINGE_METRICS contract + diagnose_metrics: real signal → dimension
  → fix) + `references/retention-metrics.md` (the ground-truth defining "bingeable"; next-
  episode-start is the key metric). Pre-wires flywheel #8; reward becomes swappable. Wired into
  SKILL.md. 59 dramacode tests green (+4).
- **Iter 7 (2026-08-09):** built element #7, safety at UGC scale — `dramalib/safety.py`
  (likeness_gate: pluggable screener-not-judge, never a false pass; compliance_scan: text red-
  lines + banned terms; 3 checkpoints) + `references/safety-gate.md`. Wired into SKILL.md. 64
  dramacode tests green (+5).
- **Iter 8 (2026-08-09):** built element #8, the binge→auto-rework loop —
  `dramalib.evals.binge_rework` (scorecard → prioritized fix list) + `REWORK_FIXES`/
  `FLAG_FIXES` as the single source of truth (metrics.py DRY-imports it). 66 dramacode tests
  green (+2). **All 8 backlog elements now shipped.**

## Retrospective (after iter 8) — the backlog is done; what's next

The original 8 meta-elements are shipped. The platform now has, packaged as skills +
`dramalib` + docs: the binge engine (secret sauce, measurable) → the eval team (score it) →
the ideal-user model + chat-first onboarding (who + how) → the reward/variant-select flywheel →
the retention-metrics ground-truth → the safety gate → the auto-rework loop. The through-line is
whole: *a non-producer chats → the tool proposes an bingeable series → scores it → reworks the
weak spots → screens the render → and, when the network ships, learns from real audience data.*

**Open sub-items to work next iterations** (deepen the loops rather than start over):
- ✅ The **taste loop** (flywheel #3, iter 9) — `dramalib/taste.py` (TasteProfile + observe +
  bias_scaffold) + `references/taste-loop.md`. Open: persist per-creator + capture UI events.
- ✅ The **judgment-eval role** (iter 11) — the rubric + fenced `binge-judgment` contract for
  the 3 judgment dims in `references/binge-eval.md` + `dramalib.evals.combine_binge`
  (merge proxy + judgment → one 7-dim verdict; a killer flag or any dim ≤2 hard-fails it).
- ✅ The **eval-CI golden set** (flywheel #5, iter 10) — `dramalib/golden.py` + the
  `binge-golden` case in `evals/run.py`. Open: track the reward trend + a render-stage golden set.
- **Wire the gates into the driver** — binge gate at script-lock, safety gate at sheet-lock/
  publish — so the running app enforces what the doctrine describes.
- ✅ **Institutional memory** (flywheel #7, iter 14) — `dramalib/institutional.py` (CraftCard +
  promote/retire/coverage; support + consistent-outcome discipline). Needs real runs to mine.

### Deepening iterations
- **Iter 9 (2026-08-10):** the taste loop (flywheel #3) — `dramalib/taste.py` (TasteProfile +
  observe: fold accept/reject/kill/pace/tone/note; preferred_genre / avoided / bias_scaffold —
  bias defaults, never override) + `references/taste-loop.md`; flywheel.md #3 marked backbone-
  built. 71 dramacode tests green (+5).
- **Iter 10 (2026-08-10):** the eval-CI golden set (flywheel #5) — `dramalib/golden.py`
  (fixed strong/weak golden set + check_golden, invariant-based: strong must out-reward weak +
  strong clean / weak has rework) wired as the `binge-golden` case in `evals/run.py` (now
  6 eval cases). 74 dramacode tests green (+3) + eval gate green.
- **Iter 11 (2026-08-10):** the judgment-eval role — the rubric + fenced `binge-judgment`
  output contract for the 3 judgment dims (wish_fulfillment/bingeability/clip_ability) in
  `references/binge-eval.md` + `dramalib.evals.combine_binge` (proxy + judgment → one
  7-dim verdict; killer flag or any dim ≤2 hard-fails). The binge score is now WHOLE
  (deterministic backbone + LLM judgment). 76 dramacode tests green (+2).
- **Iter 12 (2026-08-10):** consolidation — verified all gates green (dramapy 188, dramacode 76,
  screening-room 7, cast-book 9, evals 6/6, viewer 36) and wrote the "How it all fits" capstone
  map (the 7-stage create flow → each stage's reference + dramalib module). Recorded the
  per-skill test-isolation note.
- **Iter 13 (2026-08-10):** model-routing memory (flywheel #4) — `dramalib/routing.py`
  (record_outcome / best_model / route: learn the best model per shot-type from critic scores,
  min-trials noise guard, cold-start research defaults per type). flywheel.md #4 marked backbone-
  built. 80 dramacode tests green (+4). Flywheel loops #2/#3/#4/#5 all backbone-built → roadmap R6.
- **Iter 14 (2026-08-10):** institutional memory (flywheel #7) — `dramalib/institutional.py`
  (CraftCard + InstitutionalMemory + promote/retire/coverage; a pattern promotes to a rubric
  candidate only with support + a consistent outcome, never one anecdote). 84 dramacode tests
  green (+4). **ALL 8 flywheel loops now backbone-built** (#1 production/critic, #2 variant-
  select, #3 taste, #4 model-routing, #5 eval-CI, #6 craft-learning via institutional, #7
  institutional memory, #8 audience-reward contract in metrics). The offline-buildable meta-
  platform is COMPLETE. Everything remaining needs the live app + a funded fal, or real
  run/audience data — a genuine pause point for Dee's review / redirect.
