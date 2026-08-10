# Chat-first onboarding — from a one-line pitch to an bingeable series

**Load when:** a non-producer starts a project, especially from a vague prompt
("make me a revenge drama", "something like the ones I binge"). This is the
conversation that turns a feeling into a scaffolded series without ever showing a
blank page or a craft term. Pairs with `references/ideal-users.md` (who you're
talking to) and `dramalib.onboarding` (the deterministic backbone).

## The rule: never a blank page, never an interrogation

A non-producer freezes on "what do you want to make?" and can't answer "what's
your paywall episode?" So the flow is **propose, don't interrogate**: ask the
three feeling questions, then *fill in a whole series* they react to. They edit
by feel; the machine holds the craft.

## The four moves

1. **Intake — three feeling questions (`INTAKE_QUESTIONS`), no jargon.**
   - Who do we root for, and what do they secretly wish would happen to them?
   - What got taken from them / done to them? (the wound we open on)
   - What do they want most — and who's in the way?
   That's it. Don't ask about episode count, structure, gates, shots — those are
   ours. If they volunteer a genre or a reference title ("like The CEO's Secret
   Wife"), take it; if not, infer the genre from the answers.

2. **Propose a scaffold — fill the blank page.** Call
   `dramalib.onboarding.series_scaffold(genre=…, market=…)` to get a ready
   default: ~50 episodes at 90-120s, the genre's trope spine, the pay/retention
   gate plan. Present it as *a series taking shape*, not a config:
   > "Here's the shape: a 50-episode revenge series. She's the discarded first
   > wife; episode 1 opens on the divorce papers; by episode 10 she's back and
   > they don't recognize her. Sound right, or should she be someone else?"
   Everything is a default they can change — a default they tweak beats a
   question they can't answer.

3. **First draft fast — show, don't spec.** Generate episode 1 (the binge
   engine + genre pack run underneath) and show it. Seeing a real draft unlocks
   taste a blank prompt never will. Keep spend low here — draft tier, cheap
   stills first (the plan-approve gate).

4. **Steer by feel — the loop.** They react in plain words ("make her angrier
   here", "I want the sister to be the villain", "grovel harder"); the tool maps
   each to a craft move (reroll a shot, swap a beat, sharpen the face-slap) and
   re-renders only what changed. This is the whole product: they supply taste,
   we supply the machine.

## What the tool infers (so they don't have to)

From the three answers + a genre: the trope spine (`genre-playbook.md`), the
ep-1 wound and the 爽点 ladder (`binge-engine.md`), the cast archetypes
(`cast-book`), the ~50-episode arc and gate placement (`gate_plan`), the market
localization. The creator sees a story; the platform ran the whole binge
machine to build it.

## Pitfalls

- **Asking a craft question in disguise.** "How many acts?" / "where's the
  midpoint?" — a non-producer can't answer. Translate to a feeling or default it.
- **A wall of options.** Offer at most 2-3 concrete directions ("revenge framing
  or romance framing?"), never an open menu.
- **Speccing before showing.** Don't perfect the outline in chat — get a draft on
  screen fast; taste comes from reacting.
- **Losing their words.** Echo their pitch back in the scaffold ("the discarded
  first wife" — their phrase), so it feels like *their* story, not ours.
