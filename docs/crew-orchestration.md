# Crew orchestration — running the agent crew like a well-run series

`film-crew.md` names the crew (the departments). This is the **management layer**:
who decides what, the daily loop that makes a crew converge, and the gates that
keep a season consistent. Distilled from how real showrunners, producers, and 1st
ADs run a production — mapped onto Autonomous TV's agents.

## The one shift: copy TV, not film

A film is director-led and one-off. A **series is showrunner-led** — high-volume,
consistency-obsessed, one voice across many episodes. That's our case. The
showrunner is *both* top creative authority *and* top manager, and personally does
the **final pass** on every episode so the whole season speaks in one voice. The
human + a showrunner-agent sit in that seat.

## Split authority two ways, always

- **Creative intent** (story, tone, performance, the look) flows *down* from the
  showrunner/director.
- **Resources** (time, render budget, what actually gets made, delivery spec) are
  owned by the **producer/orchestrator** — who makes *no* creative calls.
- **Craft agents** decide their own domain inside the brief.

**Push every decision to the lowest agent with the context; escalate only
conflicts and irreversibles.**

## The roles (authority, not just skill)

| Agent | Owns | Authority |
|---|---|---|
| **Showrunner (+human)** | the bible; final-pass gate on every script and locked cut | top creative; the only agent that amends canon (human ratify for tone/world/cast) |
| **Producer / orchestrator** (= producer + 1st AD) | schedule, render budget, the shot plan ("call sheet"), delivery spec, the escalation router | halts on a failed gate or budget burn; **makes no creative calls** |
| **Craft** {writer, director, cinematographer, cast, editor, colorist, sound, composer} | its own domain | decides autonomously when the call is reversible, on-bible, and in-budget; else escalates |
| **Continuity** (script supervisor) | the continuity log, every shot | no creative power; can raise a **blocking flag** that stops a shot |
| **Critic** (screening room / braintrust) | notes | **advisory only** — diagnoses, never prescribes, never blocks |

## The decision-rights map

| Decision | Owner | Autonomy |
|---|---|---|
| story / tone / character / arc | showrunner (+human) | escalate |
| per-scene performance & coverage | director | auto (within bible) |
| framing / lens / lighting per shot | cinematographer | auto |
| cut rhythm / pacing / assembly | editor | auto (pre-lock) |
| grade | colorist | auto (post-lock) |
| SFX / ambience / mix | sound | auto (post-lock) |
| score / cue placement | composer | auto (post-spotting) |
| **casting / face / voice** | cast-agent | **LOCKED** — altering it is a bible change |
| render budget & schedule | producer | auto |
| **publish & final spend** | **human** | **always escalate** |

**The autonomy rule, baked into every agent:** *If it's reversible, cheap, and
consistent with the bible — decide and log it. If it changes canon, crosses
another agent, exceeds your shot budget, or is public/irreversible — escalate.*

## The daily loop (the convergence engine)

The rhythm that makes a crew converge, as a state machine — each state has an
owner and an exit gate:

1. **Brief** (human + showrunner) → *gate: bible written & approved*
2. **Write / break** (writer) → *gate: showrunner final-pass on the script*
3. **Shot-plan / call-sheet** (director + producer) → *gate: plan within budget*
4. **Render** (cinematographer + cast) — every shot writes the continuity log
5. **Dailies** (critic notes + continuity check) → *gate: pass, or note → reroll (≤3)*
6. **Turnover** (a typed manifest: passed shots + continuity log → editorial; open flags must be zero)
7. **Assembly** (editor) → *gate: showrunner/human review*
8. **Picture-lock** (**human gate** — the cut is frozen)
9. **Spotting** (composer + sound spot the *locked* cut)
10. **Finish** (color / sound / score to picture, in parallel — locked cut only)
11. **Final-mix / deliver** (producer spec-check) → *gate: **human** publish*

**Dailies** is the cheap-iteration QC while the "set is still up" — our
render-a-batch → critic + continuity → notes → reroll loop.

## The gates (freeze expensive interfaces)

- **Picture-lock dependency gate (hard):** the colorist, composer, sound-mix, and
  any finishing agent are **blocked** until `state = PICTURE-LOCK`. Their work is
  per-frame-expensive; any change to the cut after lock reverts and re-runs the
  affected finishing (and tracks the cost of late changes). Don't grade/score a
  cut that might get recut.
- **Reroll budget:** a craft agent may reroll a shot up to **N=3** times to pass
  dailies. On the 4th failure, **stop and escalate** — the crew is stuck ("we're
  losing the light — call the director").
- **Render-budget gate:** the producer tracks per-episode spend
  (`dramapy.costs`); at **80% burn** it halts non-essential rerolls and
  concentrates the remaining budget on the story-carrying **money shots**; at
  100% it escalates. Connective-tissue shots accept the first pass that clears the
  critic threshold. (*Concentration of force: reroll the money shots, be ruthless
  on the rest.*)

## Consistency = three layers, not one document

1. **Upstream — the bible** (`series.py`: world rules, tone, per-character voice)
   constrains generation *before* it happens; read by every agent.
2. **Inline — the continuity log** (append-only state store: props, wardrobe,
   eyelines/screen-direction, time-of-day, who-knows-what, per shot) so shots cut
   together, and drift is caught the moment it happens.
3. **Downstream — the showrunner's final pass** on the locked cut.

A bible alone does not hold a season together — you need all three. And **casting
is a lock**: the cast book (reference sheets) is frozen at season start and read
by every shot; a recast/new character is a *bible change* needing human ratify,
never a craft-agent decision.

## Critic vs. gate — never fuse them

The screening-room critic is the Pixar braintrust, and it obeys two rules or it
poisons the crew:
1. **Notes, not prescriptions** — it diagnoses ("I stopped caring at the
   midpoint", "the hook is dead in the first 3s") but never writes the fix; the
   author owns the fix.
2. **No authority** — it cannot force a change. Candor is free *because* nothing is
   mandated.

Separate the **critic** (advises, no power) from the **gate** (the showrunner/
human, who actually blocks). Fusing them makes candor expensive and the critic
timid.

## What this maps to (build order)

- The **producer/orchestrator agent** + the **decision-rights map** + the
  **autonomy rule** → the orchestration layer (partly the driver, partly a
  showrunner/producer skill). Ties into roadmap R3 (production locks + spend gate).
- The **daily-loop state machine** + **picture-lock gate** → production locks
  (`script-lock → per-shot-accept → picture-lock`) in `generation.py` / the driver.
- The **render-budget gate** → uses `dramapy.costs` (shipped) + the reroll budget.
- The **continuity log** → the continuity-agent's append-only store (feeds the
  consistency gate + the editor).
