# Night-shift log — autonomous build toward "the Claude Code of video"

Durable plan + progress for the long autonomous run (started 2026-08-09, ~overnight).
Goal (Dee): make Autonomous TV good enough that producers adopt it the way coders
adopted Claude Code — a non-producer can produce Oscar-level short films. Study films,
producers, crew orchestration, ALL AI tools, OSS, competitors, short-drama hits + secret
sauces. Keep iterating; don't stop until told. Cost is not a constraint (Dee will supply
API keys as needed; fal key already covers most frontier models).

**If resuming after compaction: read this file + `docs/film-crew.md` + `docs/video-interfaces.md`
+ `git log --oneline -20`, then continue the current wave.**

## The loop I run each wave
research/insight → build (engine + skills + crew roles) → render a demo → evaluate honestly
→ log learnings → next wave. Commit every milestone. Keep all four test gates green
(dramapy pytest, viewer, skills, evals) and keep `mock` provider working offline/free.

## State of the platform (baseline at start of night)
- Engine `packages/dramapy`: providers mock(animatic)/fal(Wan2.2)/cinematic(keyframe→Kling)/
  minimax/dashscope; character-consistency keyframes; ElevenLabs voices; Lyria/ElevenLabs
  music; concurrent shot render; idempotent regen.
- Skills: dramacode, story-analysis, cast-book, episode-viewer.
- App: web (Vite+React) chat → plan → build → player+storyboard.
- Demo: "Crown of Ash — Skyfire" dragon battle (project 539a502d) rendered at premium.

## In flight (launched, awaiting)
- Video ceiling: lip-sync (sync-lipsync/v2) + Topaz upscale — agent aad96185622759d60
- Audio ceiling: SFX (mmaudio-v2/elevenlabs) + ElevenLabs Music score + ducked mix — agent af8dde8aafb4bb4bc
- Script rewrite (heartbreak): judged workflow wd7q6y08y
- dramacode craft upgrade (platform writing): agent af86342235c6c8c89
- Deep-research fan-out: (this wave) → roadmap

## Decisions / learnings (append as we go)
- fal key is the master key: Veo3.1, Kling2.5, Seedance, Hailuo, Flux, nano-banana,
  Seedream, ElevenLabs voice+music, sync-lipsync, Topaz, mmaudio, Lyria, stable-audio — all reachable.
- Suno: NOT for the platform (song-oriented, separate account, murky commercial API).
  ElevenLabs Music (on fal) is the score model.
- Content filter blocks gore → write spectacle/tension; keep spoken lines out of the video prompt.
- Character consistency = keyframe (identity) → i2v (motion). The core quality lever.
- Next crew roles to build: director, production-design (world/props anchors), colorist
  (grade consistency), continuity (QA), formalize editor/composer/sound.

## Wave log
- W0 (start): film-crew architecture doc written; four ceiling streams + research launched.
- W1 (in progress):
  - DONE: video ceiling (lip-sync sync-lipsync/v2 sync_mode=silence + Topaz 2x upscale, verified). BUG FOUND: post-processed clip came out ROTATED 90° (lipsync or topaz adds a rotate flag the stitch filtergraph ignores). FIX PENDING — diagnose with a cheap 1-shot probe (read rotation side_data), then bake-upright in the provider/stitch before the ceiling render. Video agent files committed-pending (held for green tree).
  - DONE + COMMITTED (d1155fe): dramacode craft upgrade — platform-wide cinematic+heartbreaking defaults, new references/patterns/dramalib helpers. 42 skill tests green.
  - DONE: Skyfire script rewrite (judged, winner D3 merged) → written to demo project 539a502d as "The Name in the Ice", 22 shots/72s. Emotional core: ice dragon Vaeldris = Yun, the dragon Mei Lin hatched, raised undead; two-step recognition (scar + jade bell vs her jade pendant); dagger line "I will set you free"; Pale King + Shadewyrm cliffhanger. 6 cast (adds harrow, shadewyrm). Verbatim descriptor constants = the consistency demo.
  - RUNNING: audio ceiling (af8dde8aafb4bb4bc — ElevenLabs Music score + mmaudio/elevenlabs SFX + ducked mix; test_music mid-edit); research roadmap workflow (w5rlnj1y7, 8 researchers → roadmap).
  - NEXT when audio lands: full suite green → fix rotation (1-shot probe) → re-vendor → ceiling render of "The Name in the Ice" at 2160×3840 master → verify frames+audio+lipsync → commit → show.
  - ADDED (Dee asked): CRITIC/REVIEWER role — skills/screening-room + review_bundle.py + driver screening phase. Watches output, scores on a film rubric, routes department notes, drives targeted re-renders (agent a7033e53a1dbaebbe). This is the eval/dailies role AND the auto-catch for the rotation defect. Maps to film-crew.md continuity/post-sup row → now a real critic.
- W2 (in progress):
  - COMMITTED bdc968d: ceiling engine — lip-sync + Topaz upscale (video), ElevenLabs Music composition_plan + layered SFX + sidechain-ducked mix (audio), rotation guard (normalize_orientation, no-op on upright). 166 dramapy tests + 5/5 evals green. Re-vendored.
  - ROTATION BUG resolved: a fresh full-path probe render came back 2160×3840 upright, no rotate flag — the earlier sideways frame was a one-off. Added a conditional bake-upright guard as insurance anyway; critic's defect-detector is the second net.
  - AUDIO SCHEMA LEARNINGS: elevenlabs/music can't combine force_instrumental + composition_plan (use empty lines + negative vocals); fal-ai/elevenlabs/sound-effects is BROKEN upstream (pins deprecated model) → use mmaudio-v2/text-to-audio; mmaudio-v2 returns video (extract audio); sync-lipsync default cut_off truncates → use sync_mode=silence.
  - Designed docs/flywheel.md (8 self-improving loops) + docs/film-crew.md; roadmap.md + docs/research/ archived; night build queue = tasks 25-30.
  - RUNNING: ceiling render of "The Name in the Ice" at 4K-vertical (2160×3840) via CLI (pid in /tmp/ceiling_pid.txt, log /tmp/ceiling_render.log) — full crew: keyframe→Kling→lipsync→upscale + ElevenLabs voices + ElevenLabs Music + SFX. ~$25-30, ~40min. Critic agent a7033e53a1dbaebbe still finishing (review_bundle/driver/screening-room).
  - NEXT: when critic lands → full suite green (incl. test_review_bundle) → commit critic → when render lands → verify frames (consistency across shots)+audio+lipsync, board, poster → show Dee. THEN start roadmap R1 (turnaround sheets + reference-stack Nano-Banana-Pro keyframes — biggest quality jump) and continue the waves.
- W3 (research + platform-gaps wave, 2026-08-09 day):
  - **BLOCKER (Dee must clear): fal balance exhausted** (`403 User is locked. Reason:
    Exhausted balance`). Killed the 4K ceiling render at 5/22 shots; R1's live verify and ALL
    live rendering are blocked until topped up. Offline work (mock provider) is unaffected.
  - RESEARCH (13 agents, all landed; docs committed): `docs/oss-landscape.md` (9 OSS/competitor
    teardowns) + `docs/short-drama-playbook.md` (audience/psychology, monetization/economics,
    format/genre craft, studios-vs-UGC strategy). Headlines: our architecture is validated not
    novel (ArcReel 3.9k = our twin); **9/9 competitors have NO automated pixel critic → screening-
    room is the wedge**; last-frame→first-frame chaining + inkos non-regressing critic loop both
    independently confirm work we'd already done. HONEST strategic verdict: AI collapses
    production (10% of cost); the moat is distribution+UA+payment (90%). "Millions vs studios"
    is a distribution claim — the real wedge is niche/community serialized ("AI-Wattpad-for-
    video"), and Create must be money-shaped (episode + 50 ad hooks + paywall structure).
  - BUILT + COMMITTED (all gates green: dramapy 188 / dramacode 42 / cast-book 9 / screening 7 /
    viewer 36): (1) screening loop converges + keeps the best cut (was fixed 2 rounds); removed
    cadcode donor residue. (2) dramacode nine-genre playbook + emotion-to-action references;
    extended TROPE_TABLE (mafia/riches/inlaw/contract + aliases). (3) R1 landed: turnaround
    sheets + reference-stack keyframes (refstack.py) + world anchors wired into generation.py +
    location added to render-cache key. (4) screening critic gains a 9th dimension, shareability
    (clip-ability / ad-hook density), routing an editor note when low+story-strong.
  - TOP REMAINING LEVER (needs Dee steer on scope — edges toward the Watch/distribution layer):
    the **ad-hook / 投流表 exporter + money-shape output** (episodic, cliffhanger-timed, first-8-
    free split, coin-unlock metadata, sales-package). This is the strategically-highest create
    feature. Also queued: last-frame→first-frame I2V chaining (#34, offline-buildable), and the
    steal-list in oss-landscape.md (CapCut draft export, leased task queue, cost ledger, likeness
    safety gate, decomposed director stack, grid-generate-then-split sheets).
