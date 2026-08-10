# Emotion → action — write the body, not the feeling

**Load when:** writing or fixing any shot whose prompt names a feeling
("she's nervous", "he's furious", "heartbroken", "emotion: grief"), when the
board renders faces that read blank or generic, or when the critic flags a
performance note. This is the bridge between the emotional plan
(`references/emotional-core.md`) and a shot a video model can actually render.

## The claim: a video model can't render an adjective

"She looks sad" gives the model nothing to animate — you get a neutral face or
a random guess. **A feeling is invisible; only its physical tells are on
screen.** So never ship a feeling word in a shot prompt. Convert it to
**observable action**: what the body does, where the eyes go, what the hands
find. The model renders bodies, not emotions. Write the body.

This is also how real direction works — waoowaoo's acting-direction pass and
every 短剧 storyboard guide say the same thing: "no abstract words like 'sad' —
say what the face and body do."

## The "turn off the sound" test

The one check that catches every flat shot: **mute the episode. Can you still
read each character's state from the picture alone?** A huge share of the
audience watches muted (in bed, at work, on a commute), so the reveal must land
as *image*, not audio. If a shot only works with the dialogue on, it fails —
rewrite it around a visible action. Emotion the viewer can't see isn't in the
film.

## The lookup — feeling → concrete tells

Pick 1-2 tells per shot; don't stack all of them (that reads as overacting).
Prefer the smallest tell that reads at a glance in a vertical close-up.

| Feeling | Physical tells (pick 1-2) |
|---|---|
| **Nervous / anxious** | fingers worrying a sleeve/ring; a hard swallow; eyes flicking to the exit; a bouncing knee; wiping a palm on a thigh |
| **Fear / dread** | a half-step back; breath held then shallow; whitening knuckles on an edge; frozen mid-motion; pupils wide, chin tucked |
| **Anger (held)** | a clenched jaw, a ticking muscle; slow controlled exhale; fist tightening at the side; a level, too-steady stare |
| **Rage (breaking)** | a sudden sweep of the table; stepping into someone's space; a grab of the collar; voice-cracking shout (mouth wide, tendons in the neck) |
| **Grief / heartbreak** | a slow blink, gaze dropping; a hand pressed flat to the chest or mouth; going still as it lands; a single unwiped tear; folding down onto something |
| **Shock / recognition** | a sharp inhale; a half-formed word dying on the lips; taking a step and stopping; the object slipping from a loosened hand |
| **Contempt / smugness** | a slow one-corner smile; a glance up and down; a small head-shake; turning a shoulder; a slow clap |
| **Shame / humiliation** | eyes to the floor; a reddening face turned away; shoulders curling in; a hand shielding the face from a crowd |
| **Longing / tenderness** | a gaze that lingers a beat too long; reaching, then not; a softened jaw; a thumb brushing an object they associate with the person |
| **Determination / resolve** | a jaw set; wiping blood/tears and rising; squaring the shoulders; a slow deliberate step forward; picking the weapon/pen back up |
| **Triumph (the 爽 beat)** | chin lifting; a slow turn to face the humiliator; an unhurried walk through a parting crowd; laying the reveal (card/badge/document) on the table without a word |
| **Villain's collapse (the grovel)** | legs buckling to a kneel; hands grabbing at a hem; face crumpling from a sneer; backing into a wall, sliding down |

## Relational tells — put the feeling *between* two people

Two-shots carry more than a single face. Show the relationship physically:
- **Power:** who stands, who sits; who steps forward, who gives ground; who
  looks down at whom; whose hand is on the door.
- **Intimacy vs distance:** the gap between them closing or held; a turn toward
  or a shoulder away; a touch offered and taken, or offered and refused.
- **A shift mid-shot:** the strongest beats *change* the physical relationship
  inside one shot — she looks up *from* the floor, he steps *back* — the body
  reversal is the story.

## How to write it in a shot

Bad (adjective, unrenderable): `emotion: she is heartbroken and shocked`.

Good (observable, in the shot prompt / action line):
> Close on Mei Lin. A sharp inhale; the jade bell slips from her loosening
> fingers and rings once on the stone. Her hand rises halfway to her mouth and
> stops. She does not blink.

Keep the `emotion:` tag if the schema wants it (it seeds tone), but the
**action line must carry the feeling as a body doing a thing** — that's what
renders. Pair the tell with a shot size that shows it: hand tells need the hand
in frame; a swallow or an eye-flick needs a tight close-up (`references/
cinematic-shots.md`).

## Pitfalls

- **Adjective smuggled into the prompt** ("looking devastated") — the model
  can't render it; name the tell instead.
- **Over-stacking tells** — three tells at once reads as a cartoon. One clear
  action beats five.
- **Tell the size can't show** — "a single tear" in a wide master is invisible;
  match the tell to the shot scale.
- **Feeling only in the dialogue** — fails the mute test. If the line carries
  the whole beat, add the body doing it.
