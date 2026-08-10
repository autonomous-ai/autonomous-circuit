
https://github.com/user-attachments/assets/534a5ca4-cde4-416e-a2d2-c37226042133

# Autonomous TV: vibe short dramas — create episodes by chatting with AI

With Autonomous TV ("TV"), making a short drama is a conversation.

**1. Describe:**
Tell TV the drama you want — a premise, a genre, a vibe. TV writes the series
bible, casts the characters, and storyboards episode one, beat by beat.

**2. Approve:**
Review the plan — the beat sheet, the shot list, the cast. Give notes or approve.
TV renders every shot, lip-syncs the dialogue, burns the subtitles, lays the music,
and assembles the episode.

**3. Iterate:**
Watch it in the built-in vertical player. Give notes on any shot — TV regenerates
only what changed and re-assembles. You supply taste; TV supplies orchestration,
continuity, and the production labor.

## Status

v1 in active development.

## Repo layout

- `viewer/` — the web app: Vite + React chat surface + vertical episode player,
  storyboard strip, and the Node server driver that runs the `claude` subprocess
- `packages/dramapy/` — Python episode pipeline: spec → shots (provider-rendered) →
  stitched 9:16 MP4 + SRT + poster/board, with deterministic validation warnings
- `skills/` — Claude Code skills bundled with the app
  - `dramacode` — episode generation: beat law, shot grammar, the write→render→review loop
  - `story-analysis` — enrich a vague drama ask into a series brief
  - `cast-book` — create and lock the cast (looks, voices, reference sets)
  - `episode-viewer` — hand finished episodes to the in-app player
- `docs/` — `video-interfaces.md` (the frozen contract), `drama-research-2026-08-09.md`
  (how short dramas are actually constructed), `oss-decisions.md` (what we build on)
- `scripts/` — dev/build helpers

## Render providers

Shots render through a provider abstraction (`VIDEO_PROVIDER`):

- **`cinematic`** — the flagship consistency pipeline (what made the trailer above):
  Nano-Banana-Pro reference-stack keyframes → **Kling 2.5 Turbo Pro** image-to-video →
  lip-sync + Topaz upscale, with ElevenLabs voices + score. Best quality; hosted via fal.
  See [docs/pipeline.md](docs/pipeline.md).
- **`fal`** — a simpler text-to-video path (**Wan 2.2**, `fal-ai/wan/v2.2-a14b`).
- **`mock`** — ffmpeg-synthesized placeholders; the full pipeline runs with zero GPUs, zero keys.
- **`comfyui`** — self-hosted open weights (Wan 2.2 + Qwen-Image-Edit + VACE); planned.

## Prerequisites

- Claude Code installed on PATH: <https://claude.ai/install>
- Node 20+
- ffmpeg + ffprobe on PATH
- Python 3.10+

## v1 LLM stance

Autonomous TV uses the user's existing Claude Code subscription.
