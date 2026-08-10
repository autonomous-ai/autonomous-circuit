# dialogue-shot-writing

**Trigger:** load when writing or fixing dialogue shots, when the user
says "sounds stiff", "too talky", "subtitles overflow", or when the
`silent_dialogue` / `subtitle_overrun` warnings fire.

## Why this exists

Dialogue is the most expensive shot kind: it needs a cast id, a line,
TTS, lip-sync, and subtitles — and for photoreal styles the audio is
generated FIRST and fed into the render, so **the line is final before
the render**; every line edit re-renders its shot. The register that
works is 短碎锋利 — short, fragmented, sharp: one breath per line,
≤15 Chinese characters, 200-300 dialogue characters per episode total.
Two-handers, no monologues. "Kneeling beats crying, a slap beats a
speech" — cut any line an action can replace.

## Use the helper

```python
from dramalib.helpers import clamp_duration
from dramalib.spec import Shot
from dramalib.tables import SUBTITLE, AUDIO, DIALOGUE_LINE_MAX_ZH

line = "Then read the codicil. Out loud, sister."      # 2 sentences
shot = Shot(
    id="s1_05", kind="dialogue",
    duration_s=clamp_duration(kind="dialogue_per_sentence",
                              duration_s=2.5 * 2),      # per-sentence rule
    cast=["mira"], line=line, emotion="eruption",
    prompt="close-up, she rises, envelope high, the room turning",
)
```

`SUBTITLE` owns the burn rules (≤14 zh / 42 en chars per line, lower
fifth, 2px stroke); `AUDIO` owns levels (dialogue +10dB over bed) and
TTS speeds (conflict 1.1-1.15x, tender 0.85-0.9x) — pick speed by the
`emotion` field.

## Pitfalls

- **Dialogue without `cast` + `line`**: a hard validator error, not a
  warning. Every dialogue shot names its speaker.
- **The monologue**: break it into an exchange or intercut reaction
  inserts. Three consecutive dialogue shots from one speaker is a bug.
- **Lines that exceed the subtitle budget**: `subtitle_overrun` fires at
  42 latin chars x 2 lines — shorten the line, never shrink the font.
- **Exposition in dialogue**: relations and stakes go in the picture in
  the first 10s; dialogue carries want and threat, not backstory.
- **Editing a line and expecting a free re-render**: audio-first
  lip-sync means the shot re-renders. Batch line edits before running.
- **`emotion` unset**: TTS speed and delivery key off it; "dread",
  "contempt", "eruption" render differently.
