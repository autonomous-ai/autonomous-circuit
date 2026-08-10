# Cinematic shots — writing a shot a director would recognize

**Load when:** writing or fixing any shot's `prompt`, when the `_board.png`
looks flat / staged / like a slideshow, or when the user says "make it
cinematic", "it looks cheap", "everything's a close-up". Pairs with
`references/shot-grammar.md` (durations, scales, when to cut) — that doc
is the *when*, this is the *how it looks*.

## The claim: a prompt is a director's shot order, not a caption

A flat episode is usually a caption problem. "She is sad in a room" is a
caption. A director never says that — a director says a *size*, a *move*,
a *light*, and one *action*. Write the prompt the way you would brief a
DP, and the engine has something to shoot.

## The five ingredients, every shot

```
[shot size] + [camera move] + [lighting] + [blocking / one action] + [atmosphere]
```

- **Shot size** (`SHOT_SCALES`): ECU / CU / MCU / MS / WS. Name it —
  providers follow a named scale far better than "a shot of". The size
  *is* the emotion: ECU = interiority, WS = isolation or scale.
- **Camera move** (`CAMERA_MOVES`): static locked-off, slow push-in, pull-
  out, handheld drift, whip pan, tracking follow, crane up, tilt down,
  rack focus, dolly zoom. A move is a verb the audience feels; a push-in
  says "lean in", a pull-out says "they're alone".
- **Lighting** (`LIGHTING_KEYS`): hard key + deep shadow, soft window
  light, single practical, rim/silhouette backlight, neon wash, firelight
  flicker, candle warm. Light is 80% of "cinematic".
- **Blocking / one action**: one physical thing, as an *action chain* —
  "she reaches for the letter, then freezes, then looks up". Sequence
  words drive motion (`shot-grammar.md`); a state produces a still.
- **Atmosphere**: rain on glass, drifting embers, frost, dust in a
  sunbeam. One texture that makes the frame breathe.

The vocab lives in `dramalib.tables` so you reach past the first word:

```python
from dramalib.tables import SHOT_SCALES, CAMERA_MOVES, LIGHTING_KEYS
```

## The rewrite ladder (this is the whole skill)

- **Flat**: `she is sad in a room`
- **+ size + light**: `CU, single practical lamp: her face half in shadow`
- **+ move + action**: `CU slow push-in, single practical: she reads the
  letter, then sets it down without finishing`
- **Cinematic**: `CU slow push-in, single lamp carving one lit cheek from
  the dark: she reads two lines, stops, lets the letter fall — rain
  streaking the window behind her, out of focus`

Same beat. The last one has a size, a move, a motivated light, an action
chain, and atmosphere. That is the default register — write the fourth
line, not the first.

## Vary the size — the one rule that does most of the work

Do **not** shoot every beat the same size. A wall of medium close-ups
reads as flat no matter how good each one is; meaning comes from the
*change* between shots. The classic move: **WS → MS → CU → ECU** tightening
into a reveal, then a hard cut to WS to drop the floor out. `shot_rhythm()`
flags a run of more than three same-kind shots:

```python
from dramalib.helpers import shot_rhythm
shot_rhythm(shots=all_shots)   # {"longest_run": 2, "monotone": False}
```

`monotone: True` is your note to break the run with a different scale.

## Let the engine do the camera language it owns — you write the scene

The engine (`dramapy.cinematography`) already derives a **default** shot
size and camera move from `shot.kind`, and lens/lighting/grade from
`series.style` (photoreal-drama → anamorphic + shallow DoF + filmic grade;
manhwa/anime swap their own). So:

- You do **not** have to restate the style every shot — set it once in
  `series.py` and it rides every prompt.
- You **do** write the specifics the engine can't guess: the exact size
  when it should defy the default (an ECU on a "dialogue" kind for
  interiority), the motivated light, the blocking, the atmosphere.
- The line's *words* never go in the prompt — folding dialogue text into
  an image prompt trips content filters and the text lives on the
  audio/subtitle track anyway (`references/assembly-conventions.md`).

Think of it as: the engine sets the lens, you direct the actor and light
the set.

## Hero shots earn more specificity

The hook and the cliffhanger are hero shots (`shot-grammar.md`: 5+ draws).
Spend the most prompt detail there — the exact freeze, the exact light,
the one atmospheric element. A generic hero shot wastes the whole episode.

## Pitfalls

- **The caption prompt**: no size, no light, no move — the flatness
  source. Run every prompt up the ladder.
- **Same-size everything**: `shot_rhythm` monotone. Change the scale to
  make the cut mean something.
- **Camera moving for no reason**: a whip-pan on a tender beat fights the
  feeling. Match the move to the emotion (still for grief, kinetic for
  panic).
- **Style soup in every prompt**: restating "anamorphic filmic grade" 14
  times bloats the prompt and fights `series.style`. Set style once.
- **Describing a state, not an action**: "he looks angry" is a still.
  "his jaw tightens, then he turns away" moves.
