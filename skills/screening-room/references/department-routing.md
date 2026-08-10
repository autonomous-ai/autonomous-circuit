# Department routing — symptom → who fixes it → the fix

Every `screening-report` note carries a `department` from this closed set. A
note routed to the wrong department (or to no one) wastes a fix round. Match the
symptom, name the shots, state the concrete action.

The set (matches the film-crew departments in `docs/film-crew.md`):

`writer | director | cinematographer | cast | vfx | editor | colorist | sound |
composer | continuity | technical`

| Symptom you saw | Department | Concrete `fix` to write |
|---|---|---|
| Weak/late hook, no gut-punch, flat stakes, bad line | **writer** | rewrite the beat/line; move the reveal earlier; plant the bond by 10s |
| Wrong coverage, performance intent missing, blocking unclear | **director** | re-block the shot; change the coverage; direct the emotion |
| Flat composition, no shot-size variety, weak framing | **cinematographer** | vary the scale across the cut; reframe; restage the camera move |
| A character's face/body/hair/wardrobe changes between shots | **cast** | reroll the named shots pinned to the cast reference; lock the look |
| A shot reads nothing like its prompt; artifact; missing/black clip; wrong subject | **vfx** | reroll the shot; fix the generation; strip the bad frame |
| Drags, sags, shots overstay, cut rhythm off, duration drift | **editor** | trim/re-time the named shots; tighten to the cliffhanger |
| Color drifts shot-to-shot; grade inconsistent; muddy tone | **colorist** | regrade for consistent filmic color across the flagged shots |
| Dialogue buried/silent, mix off, SFX missing on a peak | **sound** | regenerate the voice; re-mix; add the SFX cue |
| Score doesn't fit, doesn't build, or doesn't cut for the turn | **composer** | change the cue/mood; build to the climax; drop the score at the gut-punch |
| Prop/position/eyeline/time-of-day jumps between shots | **continuity** | fix the continuity error in the flagged shots |
| Orientation/aspect (rotation bug), duration/format, container issues | **technical** | re-render in the correct 9:16 orientation; fix the format/timing defect |

## Choosing between neighbors

- **cast vs vfx** — identity drift (same character, different face) → `cast`
  (the reference is the fix). A shot that renders garbage or ignores its prompt →
  `vfx`.
- **technical vs vfx** — an orientation/aspect/format problem across the pipeline
  → `technical`; a single shot that needs regenerating in portrait → `vfx` (or
  `technical`; either is fine as long as the `fix` names the re-render). Every
  technical defect is at minimum a `blocker`.
- **editor vs director** — pacing/rhythm inside the existing coverage → `editor`.
  Wrong coverage (the shot shouldn't exist / needs different framing) →
  `director`.
- **sound vs composer** — dialogue/SFX/mix → `sound`. The musical score → `composer`.

## Severity discipline

- `blocker` — ships broken without the fix: every technical defect, a dead hook,
  a character that reads as a different person, a missing/silent shot.
- `major` — clearly hurts the film: a real sag, a weak turn, buried dialogue,
  visible color drift.
- `minor` — polish: a slightly late hook, one same-scale run, a small continuity
  slip.

Only `blocker` and `major` notes trigger a fix round; write `minor` notes for the
record, but don't inflate polish into a blocker or the loop churns.
