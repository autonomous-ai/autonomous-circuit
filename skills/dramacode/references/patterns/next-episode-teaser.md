# next-episode-teaser

**Trigger:** load when the user says "teaser", "next episode preview",
"end card", "coming up", or when finishing any episode of a
multi-episode series.

## Why this exists

The tail convention: **cliffhanger (dead stop) → teaser card**. The
teaser is NOT part of the episode's drama — it is a marketing frame
after the freeze: 1-2 flash shots or a title card promising the next
episode's peak. It answers a different question than the cliffhanger:
the cliffhanger asks "what happens NOW?", the teaser promises "here is
the pleasure coming NEXT". Keep them distinct — a teaser that resolves
the cliffhanger refunds the tension you just built. The episode's cover
frame (`_review/_poster.png`) is also chosen at the tail stage; the
publish path resolves it by filename.

## Use the helper

```python
from dramalib.helpers import clamp_duration
from dramalib.spec import Scene, Shot

teaser = Scene(id="s9", location="teaser card — next episode",
    shots=[Shot(
        id="s9_01", kind="insert",
        duration_s=clamp_duration(kind="insert"),
        prompt="title card over a single flash image: the wedding "
               "invitation with HER name on it — text: 'Next: The Vows'",
    )])
```

Append the teaser scene AFTER the cliffhanger scene, keep it to one
insert (3s at the contract floor), and pull its flash image from the
NEXT episode's beat sheet (write ep N+1's hook first, then tease it).

## Pitfalls

- **Teaser resolves the cliffhanger**: showing the character surviving
  the crisis you just froze on. Tease a NEW pleasure, not the answer.
- **Teaser before the freeze**: the order is fixed — peak, dead stop,
  THEN teaser. Interleaving reads as a broken edit.
- **Long teasers**: one insert beat. A 10s trailer at the tail of a 60s
  episode is 17% of runtime spent not-watching-the-show.
- **Teasing an episode you haven't planned**: the teaser is a promise;
  if ep N+1's hook changes later, re-render ep N's teaser shot too.
- **No cliffhanger, just a teaser**: the teaser supplements the
  `cliffhanger` field, never replaces it — `no_cliffhanger` still fires.
