# The eval team — scoring binge at the script stage

**Load when:** finishing an episode/series draft, before spending render money,
when the user asks "is this actually bingeable / will people binge it", or when
gating/optimizing a series. This defines the **eval team** — the standing
evaluator that scores the *binge* axis, distinct from the screening-room
critic that scores the *craft* axis.

## Two axes, two stages, two evaluators

| axis | question | stage | evaluator | needs a render? |
|---|---|---|---|---|
| **Craft** | is it well made? | RENDER | screening-room (pixels) | yes (+ fal) |
| **Binge** | is it compulsive? | SCRIPT | the eval team (this) | no — reads the `.py` |

Binge is scored **before** rendering, because a gorgeous episode nobody
binges is wasted spend. A series can be beautifully made and un-bingeable — so
the two axes are independent and both gate. The binge rubric is
`references/binge-engine.md` (the seven forces + the seven-dimension score).

## Deterministic pre-check + judgment score

The eval has two layers, and it's honest about which is which:

- **Deterministic pre-check** (`dramalib.evals`, no LLM, no spend): coarse proxy
  scores for the four dimensions structure can estimate, plus flags for the
  structural binge-killers `validate_beat_law` doesn't cover.
  ```python
  from dramalib.evals import binge_scorecard, series_binge_flags
  card = binge_scorecard(episode=ep)
  # card["scores"]: hook_strength/payoff_cadence/cliffhanger_pull/pacing_fit = 0-10
  #                 wish_fulfillment/bingeability/clip_ability = None (judgment)
  # card["flags"]:  episode_too_short/too_long, flat_emotion, ...
  series_binge_flags(episode_count=50, gates=gate_plan(...)["gates"])
  ```
  Coarse on purpose — bands, not false precision. It catches "obviously weak"
  cheaply; it never fakes a number it can't compute (judgment dims are `None`).
- **Judgment score** (the LLM eval role): read the story and score the three
  `judgment` dimensions — **wish_fulfillment** (is there a clear self-insert
  wound, paid off progressively?), **bingeability** (would you *actually* start
  the next episode?), **clip_ability** (are there liftable ad-hooks? see
  `patterns/ad-cut-sheet.md`) — and sanity-check the proxy scores against the
  actual writing. The proxy can't tell whether a reversal *reverses*; you can.

## The judgment layer — rubric + contract

The proxy can't read the story; you can. Score the three `judgment` dimensions
0-10 against these anchors, then hand them to `combine_binge`:

- **wish_fulfillment** — *is there a clear self-insert wound in ep 1, paid off
  progressively?* 9-10: an invisible/wronged lead the audience becomes, restituted
  (dignity/wealth/love/vengeance) up the satisfaction ladder. 4: spectacle with no
  one to *be*. 1: no wound, no restitution.
- **bingeability** — *would you actually start the next episode without deciding
  to?* 9-10: variable reward + a deepening parasocial bond + a held cast. 4: you'd
  stop after one. 1: a chore.
- **clip_ability** — *are there liftable ad-hooks?* (`patterns/ad-cut-sheet.md`)
  9-10: several standalone scroll-stoppers. 4: works only in sequence. 1:
  unmarketable.

Emit exactly one fenced block:

````
```binge-judgment
{"wish_fulfillment": 8, "bingeability": 7, "clip_ability": 6,
 "rationale": {"wish_fulfillment": "the discarded wife's wound is planted in ep1 and...",
               "bingeability": "cliffhanger + the sister-secret pulls the next tap",
               "clip_ability": "the slap and the will-reading each lift as a 20s hook"}}
```
````

Then `dramalib.evals.combine_binge(episode=…, judgment=…)` merges proxy +
judgment into one 7-dimension verdict (`scores`, `overall`, `pass`, `rework`).

## The verdict + the gate

Combine into one binge verdict. **Overall is a judgment, not an average** —
a dead hook (`hook_strength ≤ 2`), an episode that resolves itself
(`cliffhanger_pull ≤ 1`), or a `flat_emotion` flag caps it low no matter the
rest. Gate: don't advance a series to render spend on a low binge verdict —
a great-but-quiet draft gets the note **"make it compulsive"** (add the loop),
not "make it better" (polish). This is the script-stage twin of the screening
room's `pass_at_bar`.

## The auto-rework loop (where this feeds)

A low dimension has a specific, mechanical fix — this is how the eval closes into
improvement instead of just judging. `dramalib.evals.binge_rework(episode=…)`
emits this as a prioritized, act-on-able list (structural flags first, then the
weakest proxy dimensions, each with its fix), so the authoring agent applies fixes
instead of re-reading a scorecard:

| low dimension / flag | the fix |
|---|---|
| `hook_strength` | regenerate the cold open mid-conflict (`patterns/cold-open-hook.md`) |
| `payoff_cadence` | insert a face-slap / reversal (`patterns/face-slap-cascade.md`) |
| `cliffhanger_pull` | cut on the peak, delete the resolution (`patterns/cliffhanger-beat.md`) |
| `flat_emotion` | add the 虐 setup before the 爽 payoff (`binge-engine.md`) |
| `wish_fulfillment` | plant the self-insert wound in ep 1, pay it off on the ladder |
| `clip_ability` | mark/strengthen liftable hooks (`patterns/ad-cut-sheet.md`) |
| `series_too_short/long` | resize toward the ~50-ep target (`gate_plan()`) |

## Pitfalls

- **Trusting the proxy as the score.** The deterministic layer is a floor, not
  the verdict — a formally-correct episode (hook on time, cliffhanger set) can
  still be un-bingeable. The judgment layer is where the real call is made.
- **Scoring craft and binge on one number.** They're independent axes; keep
  them separate or you'll ship a pretty, un-bingeable series (or a compulsive,
  ugly one) thinking one score covered both.
- **Gating on binge after spending render money.** Run it at script-lock —
  the whole point is to catch it before the bill.
