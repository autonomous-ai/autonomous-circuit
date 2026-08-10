# face-slap-cascade

**Trigger:** load when the user says "face-slap", "打脸", "comeuppance",
"she shows them all", "humiliation then payback", or when writing any
赘婿/战神/复仇/revenge-genre episode.

## Why this exists

The 打脸 (face-slap) cascade is the format's core pleasure mechanism:
**humiliation → concealed strength → forced reveal → cascading
comeuppance**. It works because every doubter shown in act one is a
promissory note the cascade repays — in order, smallest to largest. The
male-lead genres (赘婿, 战神) are built entirely on it; the female-lead
复仇 arc is the same machine with awakening in place of concealment.

The cascade is a sequence of SHORT beats: each face-slap is one
dialogue-plus-reaction pair (4-6s), stacked. It reads as rhythm, not as
one long scene.

## Use the helper

```python
from dramalib.tropes import trope_for_genre

pattern = trope_for_genre(genre="zhuixu")["beats"]
# ['humiliation', 'concealed_identity', 'forced_reveal', 'face_slap_cascade']
```

`beat_sheet()` already cycles these names into the escalation beats —
your job is casting each doubter and writing each slap. Give every
doubter ONE line of contempt early (that's the debt) and ONE reaction
shot in the cascade (that's the payment).

## Pitfalls

- **Revealing too early**: the concealment phase is where tension
  accrues. A reveal at 30% runtime leaves nothing to cascade.
- **One big slap instead of a cascade**: the pleasure is serial. Three
  doubters falling in sequence beat one villain falling once.
- **Unpaid debts**: a doubter established but never slapped is the #1
  audience complaint in the genre. Track them in spec.md.
- **The lead gloats in dialogue**: "a slap beats a speech" — the
  comeuppance is shown in the doubter's face (reaction insert), not
  narrated by the lead.
- **Cascade without escalation**: order the slaps by target status —
  clerk, rival, patriarch — never the reverse.

## Peel the reveal (马甲 stack) — the mega-hit form

The biggest hidden-identity/revenge hits don't reveal the secret once — they
**peel it**, one "vest" (马甲) at a time, each face-slap exposing the truth to a
*bigger* audience: secret doctor → secret CEO → secret heir → the one everyone in
the room answers to. Each layer re-prices every earlier humiliation and lands on a
wider crowd, so the cascade escalates in *scope*, not just intensity. Encode the
satisfaction ladder as an ordered STACK of reveals (personal → social →
institutional → total), not a single detonation — and hold the biggest vest for
the finale. (See `docs/hit-teardowns.md` + the dramatic-irony gap in
`references/binge-engine.md`.)
