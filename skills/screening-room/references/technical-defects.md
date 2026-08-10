# Technical defects — the taxonomy, and why the rotation bug is special

The review bundle detects technical defects **mechanically** (pure
ffmpeg/ffprobe) and hands them to you in `defects[]`. Your job is to route each
one to a department with a concrete fix — and to **never pass a cut that carries
one**. Each defect below is at least a `blocker`.

The bundle emits these `kind`s:

| `kind` | Detected by | Route to | Fix |
|---|---|---|---|
| `orientation_aspect` | shot's display rotation ≠ 0, OR its display aspect ≠ the series aspect | `vfx` (per-shot) / `technical` | re-render the shot in the correct 9:16 orientation; strip the rotation |
| `missing_shot` | a declared shot produced no clip (status `failed` / file absent) | `vfx` | reroll the shot until it renders |
| `silent_dialogue` | a dialogue shot's clip has no audio track, or a voiced episode's final cut has none | `sound` | regenerate the voice track / re-mix |
| `duration_drift` | a shot's measured duration is >15% off spec, or the episode is >10% off | `editor` | re-time the shot / cut to spec |
| `black_frames` | a clip is essentially all-black | `vfx` | reroll the shot |

## The rotation bug — why it needs a critic

**This is the live bug the screening room exists to catch.** A shot can render
"successfully" — correct pixel dimensions, a valid mp4, a passing stitch — and
still be **wrong**, because a display-matrix rotation (or a transposed frame)
makes it land **sideways** in a 9:16 feed.

The generator's own `aspect_mismatch` check reads raw width×height and can miss
this: a 1080×1920 clip flagged with a 90° display-matrix rotation still measures
1080×1920, so the check passes — but every player rotates it on playback and the
viewer sees a landscape image squeezed into a portrait frame. The bundle catches
it two ways:

1. **Display rotation** — it reads the `Display Matrix` side-data `rotation` (and
   the legacy `rotate` tag). Any non-zero quarter-turn → `orientation_aspect`.
2. **Effective aspect** — it applies the rotation, then compares the *display*
   aspect to the series aspect. A transposed clip (16:9 in a 9:16 series) trips
   this even with zero rotation metadata.

When you see an `orientation_aspect` defect: it is a **blocker**, it fails the
bar on its own, and the fix is to re-render that specific shot in portrait and
strip the rotation. Cite the shot id. Do not round it up to "minor" because the
first frame *looked* upright in the board — the sampled mid/late frames and the
probe don't lie.

## Reading the shots table

Each `shots[]` row carries `rotation`, `width`/`height`, `aspect`,
`duration_s_spec` vs `duration_s_measured`, and `has_audio`. Cross-check the
`defects[]` against these rows so your note names the concrete evidence
("s1_03: 270×480 flagged 90°, lands sideways") rather than a vague "looks off".
