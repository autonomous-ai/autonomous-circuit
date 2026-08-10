# the-bond-and-the-loss

**Trigger:** load at the START of any episode or series — before the beat
sheet — and whenever a draft "has twists but I don't care", the user asks
for "stakes", "make me feel something", "raise the stakes", or a villain
scheme with no one to root for. The plant-and-payoff spine from
`references/emotional-core.md`.

## Why this exists

Audiences don't grieve plots; they grieve *loves*. The one structural
habit behind every drama that lands: plant a **bond** early, then build the
episode toward **spending** it. Skip the plant and the best twist in the
world is trivia. This pattern is the setup-and-payoff rule applied to
feeling — the bond beat is a promise, the gut-punch is that promise called
due.

The bond must be **concrete and shown**, not stated: a shared lullaby, a
saved seat, a name half-worn off a sticker, a vow whispered once. `BOND_TYPES`:
person, promise, belonging, identity, home, hope. The stakes are the one
specific way that bond is lost — named early, felt as inevitable.

## Use the helper

```python
from dramalib.helpers import emotional_arc, sfx_for

# Aim the whole episode at one of the five turns.
arc = emotional_arc(length_s=60.0, gut_punch="sacrifice")
for b in arc:
    print(b["function"], "->", b["feeling"], "|", b["purpose"])
# bond       -> tenderness | show the thing the protagonist loves and can lose
# threat     -> unease     | put the bond in danger — name the stakes
# escalation -> dread      | tighten the vise; each turn costs more
# sacrifice  -> anguish    | the protagonist gives up what they love ...
# aftermath  -> resolve    | the choice the gut-punch forces — cut on it
```

Lay `emotional_arc()` (feeling) beside `beat_sheet()` (structure): the bond
rides the *world* beat, the gut-punch rides the *reversal/cliffhanger*.
Write the bond image into the episode docstring so every later shot can
reference it. `EMOTIONAL_TURNS` names the five gut-punches.

## Pitfalls

- **Telling the bond**: "they were like family" in a line does nothing.
  Give the bond a physical object or ritual and put it on screen.
- **Planting late**: a bond introduced in the same scene it's lost has no
  weight. First 10s.
- **Vague stakes**: "danger" isn't stakes. "Power routes to one of you" is.
- **No payoff**: a bond planted and never threatened is a dropped thread —
  the audience feels the missing beat even if they can't name it.
- **Two bonds competing**: one central bond per episode reads clearly;
  spread the rest across the series (`references/series-architecture.md`).
