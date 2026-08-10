# recap-flashback

**Trigger:** load when writing episode 2 or later, when the user says
"previously on", "recap", "catch people up", or when a recap beat runs
long (the template's "recap beat exceeds 8s" warning).

## Why this exists

Short-drama recaps are not TV recaps: they are a **flashback re-showing
the previous cliffhanger** — 2 shots, under 10 seconds
(`RECAP_MAX_S`), then straight into today's conflict. On binge platforms
the recap is optional (the next episode auto-plays seconds later), so
the recap scene must be **droppable**: nothing new may happen in it.
Structure: a desaturated flash-frames insert of the previous peak, then
one reaction beat snapping back to the present.

## Use the helper

```python
from dramalib.helpers import recap_flashback

recap = recap_flashback(
    prev_cliffhanger="freeze on the burning letter",  # ep N-1's cliffhanger field
    cast=["li_wei"],
    duration_s=8.0,          # 3-10s; ValueError past the cap
)
ep = Episode(number=2, title="…", scenes=[recap, *main_scenes], ...)
```

The helper reads the PREVIOUS episode's `cliffhanger` string — which is
why that field is mandatory. It returns a `Scene(id="s0", ...)` so the
main scenes keep their s1/s2 numbering.

## Pitfalls

- **New information in the recap**: if the recap is skipped (binge
  platforms), viewers must lose nothing. New info goes in s1.
- **Recap over 10s**: exposition budget blown before the episode
  starts — `hook` law effectively broken. The helper caps at 10s.
- **Recapping the whole episode instead of the peak**: flash the
  cliffhanger image, not the plot.
- **Same footage verbatim**: re-show the peak desaturated / re-cropped
  ("memory texture") so it reads as memory, not as a player glitch.
- **Recap on episode 1**: never. There is nothing to recall; ep 1 opens
  on the hook.
