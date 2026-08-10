# video-interfaces.md — changes since freeze

Append-only. Template per entry: Change / Why / Backward compatible / Mechanism / Tracks affected.

## 2026-08-09 — voices move to a provider-independent assembly stage; mock → `animatic-2`

- **Change:** spoken dialogue is now generated **once at assembly**, not per-provider. A new
  `dramapy.voices` module speaks each dialogue line with macOS `say`
  (`voice_for_tag` — the deterministic `Character.voice` → voice+rate table — moved here from
  `providers/mock.py`), places every line onto one episode-length stereo wav at its
  stitched-timeline offset (trim + 0.15 s fade-out when speech outruns its shot, silence-pad
  when shorter), and hands that track to `stitch_episode(..., voice_track=…)`, which mixes it
  at **0 dB (primary)** under the BGM bed at **−14 dB** over a silent base. Clip audio is no
  longer used in the mix (every provider — fal/Wan/MiniMax and the mock — renders silent
  video), so all providers' episodes now get voices from one code path. The **mock provider's
  model id bumps `animatic-1` → `animatic-2`**: its shots are now silent video (all visuals —
  zoompan motion, grain, vignette, per-scene palette, burned shot header + dialogue text row —
  are unchanged); its `cache_salt` drops the `tts=/voices=` parts and becomes `"v2"`. The
  `silent_dialogue` warning is **redefined**: it no longer inspects the shot clip's audio
  track, but the **final episode** — it fires for a dialogue line only when no spoken audio
  landed in the episode (voice layer off, `say` unavailable, or that one line failed). A new
  `VIDEO_VOICES=off` kill switch (and any absence of `say`) degrades the whole layer to
  `None` — the episode keeps its burned subtitles; nothing raises.
- **Why:** episodes had burned subtitles and a music bed but **no spoken dialogue** — silent
  provider footage plus a mock that baked `say` at the wrong (per-shot) layer, and even that
  path had gone silent. Fixing it once at assembly gives every provider voices and makes the
  mock a faithful (silent) stand-in for real silent-video providers.
- **Backward compatible:** yes for the schema — additive `stitch_episode` keyword and a
  redefined-but-same-name/severity `silent_dialogue` warning. The mock model-id bump plus the
  `cache_salt "v2"` change intentionally invalidate pre-voice cached clips and flip the
  idempotent short-circuit, so existing episodes re-render once on their next run (and pick up
  real voices). `ClipSegment.has_audio` is removed (clip audio is unused).
- **Mechanism:** new `voices.py` (`voice_for_tag`, `build_voice_track`); `stitch.py`
  (video-only concat, silent base + voice + bed mix); `providers/mock.py` (silent shots,
  `animatic-2`, `cache_salt "v2"`); `generation.py` (builds voice entries from the subtitle
  timeline + cast voice tags, calls `build_voice_track`, passes the track to stitch and the
  voiced-shot set to checks); `checks.py` (`silent_dialogue` reads the voiced set). Voices are
  deterministic (`say` is deterministic for a fixed voice/rate/text; ffmpeg placement is
  pure). Skill runtime re-vendor required (`scripts/build/build-skill-runtimes.sh`).
- **Tracks affected:** B (dramapy).

## 2026-08-09 — mock becomes an animatic: model `lavfi-mock` → `animatic-1`

- **Change:** the §1 mock provider now renders a watchable animatic instead of a static
  gradient; its model id (sidecar `provider.model`) becomes `animatic-1`. Per shot:
  seeded camera motion via `zoompan` (establish = slow push 100→108%, direction seeded;
  action = 100→115%; dialogue = gentle drift at 105%; insert = quick punch-in to 112%),
  film grain (`noise=alls=6:allf=t`, seeded) + subtle vignette, per-SCENE palette
  variation (the scene id hashes into a bounded hue/saturation/value shift of the style
  palette), and a larger, wrapped dialogue text row. Dialogue shots speak their line via
  macOS `say` → 48 kHz stereo AAC baked into the clip: `Character.voice` tags map
  deterministically to voice + rate (`f_`/female-ish → Samantha, `m_` → Daniel, others
  rotate Karen/Moira/Tessa by tag hash; 170–200 wpm by tag hash; same tag → same voice
  forever), silence-padded when shorter than the shot, cut with a 0.15 s fade when
  longer. `VIDEO_MOCK_TTS=beep` — or a machine without `say` — falls back to the old
  seeded sine bed, now at speech level (gain 0.08 → 2.0 over the 1/8-FS lavfi sine).
  Episode mix: BGM bed rises −18 dB → −14 dB (`stitch.BGM_MIX_DB`), constant level, no
  ducking.
- **Why:** founder review of a mock episode (Dee, 2026-08-09): "i don't see any dramas
  just blank blue screen no visual no sound." The mock drives the whole loop — dev, CI,
  and the review phases — so the placeholder must be watchable and audible while staying
  deterministic, offline, zero-deps, and fast (15-shot 1080×1920 episode ≈ 33 s wall).
- **Backward compatible:** yes — additive behavior, no schema change. The model-id bump
  plus render-cache key changes (`CACHE_VERSION` 1→2; the key gains `sceneId` and
  `providerSalt` — the mock salts with TTS mode + voice-map version) intentionally
  invalidate pre-animatic cached clips and flip the idempotent short-circuit, so existing
  episodes re-render once on their next run.
- **Mechanism:** `providers/mock.py` (zoompan/noise/vignette lavfi graph; `say` → AIFF →
  AAC; voice table), `providers/base.py` (`ShotContext.scene_id`, `Provider.cache_salt`),
  `render_cache.py` (key fields), `generation.py` (scene map + salt into keys/context),
  `stitch.py` (`BGM_MIX_DB`). Text stays pre-rasterized PNG + `overlay` (the PATH ffmpeg
  has no drawtext); non-dialogue shots still carry no audio track, so `silent_dialogue`
  keeps meaning something. Skill runtime re-vendor required
  (`scripts/build/build-skill-runtimes.sh`).
- **Tracks affected:** B (dramapy).

## 2026-08-09 — provider set: add `dashscope` and `minimax` hosted providers

- **Change:** §1 provider abstraction gains two providers alongside `mock` and `fal`:
  `dashscope` (Alibaba Model Studio — Qwen/Wan video models, env `DASHSCOPE_API_KEY`)
  and `minimax` (Hailuo video models, env `MINIMAX_API_KEY`). `VIDEO_PROVIDER` accepts
  `mock | fal | dashscope | minimax`.
- **Why:** founder decision (Dee, 2026-08-09): development renders use hosted model APIs
  (Qwen, MiniMax, etc.); self-hosted open weights (`comfyui` provider) arrives at
  production. The abstraction exists precisely so this is a config change, not a design
  change.
- **Backward compatible:** yes — additive; `mock` remains the default and the only
  provider exercised by tests/CI.
- **Mechanism:** new `providers/dashscope.py` and `providers/minimax.py` implementing
  `Provider.render_shot()`; async-task submit + poll per each vendor's current API docs;
  network code isolated in the provider module, never imported by tests. Marked
  network-untested until first live smoke test with real keys.
- **Tracks affected:** B (dramapy) only.

## 2026-08-09 — sidecar `shots[]` gains `durationS` + `status`; `series.json` surfaced

- **Change (1):** §1 `.episode.json` sidecar `shots[]` entries gain two fields:
  `durationS` (float, measured) and `status` (`"rendered" | "cached" | "failed"`), i.e.
  `{ "id", "path", "jsonPath", "durationS", "status" }`.
- **Change (2):** dramapy also writes `<project>/series.json` (derived from `series.py`:
  title, genre, style, aspect, fps, cast[] with id/name/look/voice/ref image paths) on
  every generation; §2 catalog visibility gains one exception: a root-level `series.json`
  is surfaced as an entry (kind `json`) instead of hidden.
- **Why:** the storyboard strip shows per-shot durations/status (seek offsets, failed
  chips) and the cast panel renders the bible — Track D built defensively against both
  gaps and flagged them; without (1) seeks are proportional guesses and every shot reads
  "rendered", without (2) the cast panel is permanently empty.
- **Backward compatible:** yes — both additive; the client already parses defensively.
- **Mechanism:** dramapy writes both; the Node catalog adds the one-file exception.
- **Tracks affected:** B (dramapy), A (catalog rule).

## 2026-08-09 — shot duration floor 3.0s → 1.5s

- **Change:** §1 shot rule `duration_s ∈ [3, 15]` becomes `duration_s ∈ [1.5, 15]`.
- **Why:** the construction research is unambiguous (prop/detail inserts run 1–2s;
  漫剧 cuts 2–4s) and the first live episode showed insert shots pinned at a
  too-slow 3.0s by the old floor. The 10s skill-level hard cap is unchanged.
- **Backward compatible:** yes — widens the accepted range; existing specs stay valid.
- **Mechanism:** dramapy `SHOT_MIN_DURATION_S`, dramalib `SHOT_CONTRACT_RANGE_S`,
  recipes/templates rebalanced.
- **Tracks affected:** B, C.

## 2026-08-09 — idempotent regeneration (additive)

- **Change:** `generate_episode()` short-circuits when the source fingerprint,
  provider, and every artifact on disk are unchanged: returns the prior result
  (reconstructed from the sidecar, all shots `"cached"`, plus an additive
  `unchanged: true` stdout field) with **zero writes**. `VIDEO_FORCE_REGEN=1`
  overrides.
- **Why:** the driver's review rounds re-invoke the generator after every build;
  without this, each round rewrote byte-identical artifacts, moved mtimes, and
  re-fired `artifact_changed` for the entire episode (observed live: two full
  no-op rewrite sweeps per build turn).
- **Backward compatible:** yes — stdout field is additive; sidecar unchanged;
  behavior identical whenever anything actually changed.
- **Tracks affected:** B.

## 2026-08-09 — internal rename: `steve-*` → `video-*` (product renamed Autonomous TV)

- **Change:** every internal `steve-*` identifier is renamed to `video-*`. The document
  itself moves `docs/steve-interfaces.md` → `docs/video-interfaces.md` (this file
  `docs/steve-interfaces-CHANGES.md` → `docs/video-interfaces-CHANGES.md`). Server module
  `viewer/src/server/steve/` → `viewer/src/server/video/` (`createSteveServices` →
  `createVideoServices`, `steveApiPlugin` → `videoApiPlugin`, `STEVE_SESSION_NS` →
  `VIDEO_SESSION_NS` — namespace UUID value unchanged, so session ids are stable).
  Question fence ```` ```steve-questions ```` → ```` ```video-questions ````. DOM event
  `steve:prefill-chat-input` → `video:prefill-chat-input`. Env vars `STEVE_*` → `VIDEO_*`
  (`VIDEO_PROVIDER`, `VIDEO_FORCE_REGEN`, `VIDEO_PYTHON`, `VIDEO_CLAUDE_BIN`,
  `VIDEO_DEBUG_CLAUDE`, `VIDEO_HOME`, `VIDEO_DASHSCOPE_*`, `VIDEO_MINIMAX_*`,
  `VIDEO_FAL_MODEL`, `VIDEO_PYVERSION_REEXEC`). Data dir `~/.steve` → `~/.autonomous-video`;
  project-local `.steve/` (render cache, tmp, catalog/snapshotter skip lists) → `.video/`.
  Log prefixes `[steve:*]` → `[video:*]`.
- **Why:** the product was renamed **Autonomous TV** ("Video"); internal identifiers
  follow the user-visible rename.
- **Backward compatible:** breaking for identifiers, env vars, and data directories — but
  pre-release with zero external users, so nothing external can break.
- **Mechanism:** this repo-wide mechanical sweep plus a one-time local data-dir migration
  (`mv ~/.steve ~/.autonomous-video`; per-project `.steve/` → `.video/`).
- **Tracks affected:** all.
