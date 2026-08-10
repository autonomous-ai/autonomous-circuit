# Assembly conventions — transitions, subtitles, audio, head/tail

**Load when:** a warning or the `_board.png` points at captions, audio,
transitions, or pacing feel; or when setting `bgm` / `burn_subtitles` /
episode head-tail structure.

## Transitions

- **80% hard cuts, 20% effects.** Short drama is cut-driven; every
  dissolve must earn its place (time jump, memory, dream).
- Static shots get **Ken Burns**: 100%→115% scale over 3-4s. A truly
  static frame reads as buffering.

## Subtitles (burned in, always)

Owned by `dramalib.tables.SUBTITLE`:

- Burn subtitles into the video (`burn_subtitles=True`). Platform UI
  covers external captions; the render pipeline generates timing from
  TTS timestamps, so drift is zero.
- Position: **lower fifth** of frame — never the absolute bottom, where
  progress bars and captions collide with platform chrome.
- Type: Source Han Sans, white fill, 2px dark stroke.
- Line limits: **≤14 Chinese chars / ≤42 Latin chars per line, 2 lines
  max**. The `subtitle_overrun` info warning fires past this — shorten
  the line, don't shrink the font.

## Audio — four layers

Owned by `dramalib.tables.AUDIO`:

1. **Dialogue** — +10dB over the bed. Non-negotiable intelligibility.
2. **Ambient** — the room tone that makes cuts feel continuous.
3. **Emotional SFX** — heartbeat, the slap, glass shatter. Peaks get an
   SFX; a slap without its crack is half a slap (slap + glass-shatter
   SFX lifted completion +172% in one — single-source, directional —
   measurement).
4. **BGM** — −5dB, ducks under dialogue. Set `bgm` to a mood key
   ("tense-strings") or None; one bed per episode, switch on the
   reversal if at all.

TTS pacing: conflict lines 1.1-1.15x speed; tender lines 0.85-0.9x.

For sound as *storytelling* — cueing the per-shot `sfx` list on every peak
(`sfx_for()`), picking a score mood, and shaping its arc so it **cuts out
for the gut-punch** — see `references/sound-design.md`.

## Lip-sync ordering

- **Audio FIRST for realistic (photoreal) dialogue shots**: generate the
  TTS line, feed it into generation so lip-sync is native.
- **Manju / action tolerates video-first + dub-after.** Anime mouths
  forgive; live-action faces don't.
- Consequence for writing: in photoreal episodes the `line` is final
  before render — a line edit re-renders the shot. In manju you can
  re-dub without a re-render.

## Head and tail

- **Head**: amplify the 3s hook — loudest SFX, tightest cut of the
  episode. Never open on logo cards.
- **Tail**: cliffhanger (dead stop) → optional "next episode" teaser card
  (`references/patterns/next-episode-teaser.md`). The cover frame
  (`_review/_poster.png`) is required — the publish path resolves it by
  filename.

## Final QC (before declaring done)

Lip alignment on dialogue shots · ducking under dialogue · SFX present
on peaks · duration inside the format range · and the review-loop rule:
**Read the `_board.png`**. The research's last gate is "review on an
actual phone" — the board at 9:16 tiles is the loop's proxy for it.
