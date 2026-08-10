# Emotional core — the bond, the stakes, and the gut-punch

**Load when:** writing any new episode or series, when a draft "renders
fine but feels flat", when the user asks for something "moving",
"heartbreaking", "with stakes", or when a plot has twists but no ache.
This is tier-1 craft — read it before `beat-structure.md`, not after.

## The claim: the hook earns six seconds, the heartbreak earns the memory

The beat law (`references/beat-structure.md`) keeps an episode *watchable*.
It does not make it *unforgettable*. What people remember — and forward,
and come back for — is one feeling: the moment the thing a character
loved was taken, spent, or turned against them. Plot is how you get
there. The gut-punch is the point.

So the gut-punch has the **same status as the hook**. A hook without a
gut-punch is a magic trick with no payoff; the audience feels used. Every
episode plants a bond and aims at a turn.

## Three moving parts, in order

1. **BOND** — someone or something the protagonist loves and can lose.
   Plant it in the first ~10s, concretely, as an image, not a fact. Not
   "they were close"; a shared lullaby, a saved seat, a kept promise, a
   name half-worn off a sticker. `BOND_TYPES`: person, promise,
   belonging, identity, home, hope.
2. **STAKES** — the specific way the bond can be lost. Name it early and
   make it *inevitable-feeling*, not hypothetical. "Power routes to one of
   you" is stakes; "things could go wrong" is not.
3. **GUT-PUNCH** — the beat where the bond is spent. One of five
   irreversible turns (`EMOTIONAL_TURNS`):
   - **betrayal** — someone trusted chooses against them.
   - **sacrifice** — they give up what they love to save another.
   - **recognition** — the enemy was once loved, or once ours
     (`references/patterns/recognition-beat.md`).
   - **irreversible_loss** — the bond is destroyed and cannot be restored.
   - **reunion** — a bond thought lost returns; relief that aches.

Pick ONE turn per episode and aim the whole arc at it. Ambiguous
heartbreak is no heartbreak.

## Plant early, pay off late — the one discipline

The audience only grieves what you showed them love. A betrayal in shot 12
lands only if the trust was on screen in shot 3. This is *setup and
payoff* applied to feeling instead of plot: every gut-punch is a promise
you made in the bond beat, called due.

The tell of a flat episode: the emotional turn arrives with a character
we were *told* to care about but never *shown* caring. Fix it upstream —
add the bond beat — not at the turn.

## The feeling turns every 15-25 seconds

Retention is not "no dead air" (that's the beat law). It is **the feeling
keeps changing**. Tenderness → unease → dread → devastation → resolve. A
scene that holds one feeling for 40 seconds is flat even if the plot
moves. `FEELING_SHIFT_S` is 15-25s — a new feeling should be arriving as
the last one peaks.

`emotional_arc()` gives you that track, aimed at your chosen gut-punch:

```python
from dramalib.helpers import emotional_arc

arc = emotional_arc(length_s=50.0, gut_punch="recognition")
[b["function"] for b in arc]   # ['bond', 'threat', 'recognition', 'aftermath']
[b["feeling"]  for b in arc]   # ['tenderness', 'unease', 'shock', 'resolve']
```

`beat_sheet()` owns the STRUCTURE (hook / reversal / cliffhanger);
`emotional_arc()` owns the FEELING. Lay them side by side: the structural
reversal and the emotional gut-punch usually want to land on the same
beat, and the cliffhanger cuts on the choice the gut-punch forces.

## It generalizes — the same machine, every genre

| Genre | Bond (planted early) | Gut-punch |
|---|---|---|
| Revenge | the sister who protected her | recognition: the mastermind is the sister |
| Romance | the letters they never sent | irreversible_loss: the letters burn unread |
| Thriller | the partner who has his back | betrayal: the partner is the mole |
| Sci-fi | a drone rebuilt from a dead sibling | recognition: the hunter carries the real sibling |
| Fantasy | the vow to climb past the gods | sacrifice: he burns the power to save one child |
| Slice-of-life | the corner table they always share | reunion: she's at the table, a year later |

Notice none of these lead with a twist. They lead with a **love**. The
twist is just how it gets spent.

## What "cinematic + heartbreaking by default" means in practice

For ANY plain prompt ("make a revenge drama", "a sci-fi love story"),
before writing shots, decide three things and write them into the episode
docstring:

- the **bond** (one concrete image you can plant by 10s),
- the **stakes** (the one specific way it's lost),
- the **gut-punch** (which of the five turns, and the shot it lands on).

Then the hook, the escalation, and the cliffhanger all point at that turn.
The recipe `recipes/recognition_ep1.py` is this doc built end to end.

## Pitfalls

- **Twist without a bond**: a shocking reveal about someone we never saw
  loved is trivia, not heartbreak. Plant first.
- **Telling the bond instead of showing it**: "they were like family"
  in dialogue does nothing. Show the shared thing.
- **Two gut-punches**: a second big turn dilutes the first. One per
  episode; save the rest for later episodes (`series-architecture.md`).
- **Resolving the ache**: don't comfort the audience after the turn — cut
  on the wound (`references/patterns/emotional-hard-cut.md`,
  `cliffhanger-beat.md`).
- **Flat feeling**: if `emotional_arc` says the feeling should have turned
  10s ago and your shots are still in one register, that's the note.
