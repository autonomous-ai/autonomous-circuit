# escalating-spectacle

**Trigger:** load when the user wants "epic", "bigger", "wow", "make it
breathtaking", a battle/chase/power-reveal/disaster sequence, or when the
middle of an episode sags. The spectacle side of retention — pairs with
`references/emotional-core.md` (spectacle serves the bond, never replaces it).

## Why this exists

"Breathtaking" is not one huge shot — it is a **staircase**: each beat
tops the last in scale, and the audience feels the ceiling lift. A single
big explosion is a firework; three escalating ones with a person we love in
the blast radius is a sequence. The rule is monotonic: never show your
biggest image second. And the spectacle must be *stakes made visible* —
the bigger it gets, the closer it comes to the bond.

The mechanics:
- **Escalate the scale**: WS of the threat → MS as it nears → ECU on the
  cost. Cut *tighter* as it gets *bigger* (`references/cinematic-shots.md`).
- **Escalate the sound**: layer the score up (`SCORE_ARC` build → climax),
  stack SFX textures under the impacts.
- **Anchor to a face**: every spectacle beat needs a reaction shot of
  someone who can lose something — spectacle without stakes is a
  screensaver.
- **Peak, then drop**: at the top, cut the score for the human beat
  (`references/sound-design.md` — silence is the loudest score).

## Use the helper

```python
from dramalib.helpers import beat_sheet, clamp_duration, sfx_for
from dramalib.spec import Shot

beats = beat_sheet(genre="zhanshen", episode_no=1, length_s=75.0)
# the escalation beats are where the staircase goes

impact = Shot(id="s1_10", kind="action",
              duration_s=clamp_duration(kind="action"),
              sfx=sfx_for(moment="tearing_metal"),
              prompt="WS: the platform's outer edge shears into the cloud sea")
cost   = Shot(id="s1_11", kind="insert",
              duration_s=clamp_duration(kind="insert"),
              sfx=sfx_for(moment="heartbeat"),
              prompt="ECU: a god's knuckles whitening on the jade decree")
```

`recipes/manju_ep1.py` runs the staircase literally (nine lightning
tribulations, each topping the last, anchored to one face).

## Pitfalls

- **Biggest shot too early**: nothing left to escalate — the sequence
  deflates. Order by scale, always up.
- **Spectacle without a face**: no reaction shot = no stakes = boredom
  dressed as action.
- **Same scale every beat**: `shot_rhythm` monotone. Tighten as you enlarge.
- **Wall-to-wall loud**: no dynamics. Build, peak, then a silent human
  beat — the drop is what makes the peak read as a peak.
- **Spectacle replacing the gut-punch**: the explosion is the setup for the
  loss, not a substitute for it. Aim it at the bond.
