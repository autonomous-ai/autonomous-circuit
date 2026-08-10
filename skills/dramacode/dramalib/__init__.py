"""dramacode helper library — composable short-drama building blocks.

Import the helpers, don't copy numbers from the reference docs. Example:

    from dramalib.bible import Series, Character
    from dramalib.spec import Episode, Scene, Shot
    from dramalib.helpers import beat_sheet, shots_from_beats, validate_beat_law

    beats = beat_sheet(genre="revenge", episode_no=1, length_s=75.0)
    drafts = shots_from_beats(beats=beats, cast=["li_wei", "dorian"])

Each helper follows the convention:

- pure function, keyword-only arguments (no positional surprises)
- returns plain dataclasses / dicts that duck-type against the contract's
  Episode / Scene / Shot shapes (dramapy validates on attributes, never
  isinstance, so a plain dict with the same keys also works)
- raises ``ValueError`` on impossible parameter combinations (so failures
  point at the spec, not at ffmpeg five frames deep)
- has a docstring with units and one usage example

Each module is its own focused topic:

- ``dramalib.bible``   — Series / Character dataclasses (the series bible)
- ``dramalib.spec``    — Episode / Scene / Shot dataclasses (the episode spec)
- ``dramalib.tables``  — THE single owner of numbers AND craft vocab: shot
                         durations, beat law, episode lengths, paywall gates,
                         subtitles, audio levels, trope table, the emotional
                         turns (gut-punches), SFX cues, score moods, cinematic
                         vocabulary
- ``dramalib.tropes``  — genre → trope resolution over the trope table
- ``dramalib.helpers`` — beat_sheet, shots_from_beats, validate_beat_law,
                         recap_flashback, gate_plan, episode_length_for,
                         emotional_arc, sfx_for, shot_rhythm
- ``dramalib.evals``   — the eval team's script-stage BINGE pre-check:
                         binge_scorecard, binge_flags,
                         series_binge_flags (craft is scored separately by
                         screening-room over the rendered pixels)
- ``dramalib.onboarding`` — chat-first onboarding + the front door: INTAKE_QUESTIONS
                         (three jargon-free feeling questions), series_scaffold (a
                         ready default series), and series_bible / format_bible
                         (compose the starter bible + cast — "TV writes the bible")
- ``dramalib.archetypes`` — the archetype casting kit: ARCHETYPES (per-genre stock
                         roles) + archetypes_for (self-insert first) — "TV casts
                         the characters"
- ``dramalib.metrics``  — the binge-metrics contract (the audience-behavior
                         ground-truth): BINGE_METRICS (metric → target band →
                         eval dimension) + diagnose_metrics (real signal → the
                         underperforming dimension → the fix)
- ``dramalib.safety``   — pre-publish safety: likeness_gate (pluggable screener-
                         not-judge; never a false pass) + compliance_scan (text
                         red-lines + banned terms) + SAFETY_CHECKPOINTS
- ``dramalib.taste``    — the taste loop (per-creator): TasteProfile + observe
                         (fold accept/reject/kill/note) + preferred_genre /
                         avoided / bias_scaffold (bias defaults to the creator's eye)
- ``dramalib.golden``   — the binge golden set (flywheel #5 CI): golden_set +
                         golden_rewards + check_golden (no change regresses the
                         binge reward); run by evals/run.py
- ``dramalib.routing``  — model-routing memory (flywheel #4): record_outcome +
                         best_model + route (learn the best model per shot-type
                         from critic scores; cold-start with research defaults)
- ``dramalib.institutional`` — institutional memory (flywheel #7): CraftCard +
                         InstitutionalMemory + promote / retire / coverage (mine
                         runs → promote proven patterns to rubric candidates)
- ``dramalib.titles``   — title generator: title_candidates (fill the mega-hit
                         subject+secret+stakes formula per genre) + TRIGGER_WORDS
- ``dramalib.package`` — sales-package generator: sales_package (compose the
                         one-screen pitch — title/fantasy/audience/hooks/gate) +
                         format_sales_package
- ``dramalib.remix``   — the zero-friction on-ramp (Door 1): remix_bible (recast
                         the lead as you / rename the villain / move the setting,
                         spine preserved) + recast + remix_summary — "make a hit
                         you love your own" (docs/onboarding-ux.md)
- ``dramalib.starters`` — the one-tap starter gallery (Doors 1 & 2): STARTERS +
                         gallery (locale-tuned ordering) + surprise_me + starter_bible
                         (a tap → a full ready-to-watch, hero-recast bible)
- ``dramalib.clone``   — clone-a-drama growth hook: infer_genre + clone_from_title
                         (name a show → an ORIGINAL series in that trope shape,
                         recast with the fan's characters — never a copy of the
                         source's script/footage; the IP line is enforced in-module)

When no helper fits, write the episode structure inline in the episode
``.py`` — that's a signal the library is missing a helper, worth promoting
later. Never hardcode a duration or a gate number that a table owns.
"""
