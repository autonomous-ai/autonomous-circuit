# The Autonomous TV pipeline — how "Crown of Ash" was made

The full stack that produced **"Crown of Ash — The Name in the Ice"** (22 shots,
112.7s, 4K vertical 2160×3840), model by model. Everything hosted goes through
**fal.ai** as a single aggregator key; the render backend is the `cinematic`
provider (`VIDEO_PROVIDER=cinematic`). Project data lives in
`~/.autonomous-video/projects/<uuid>/`.

## The layers

1. **Chat + orchestration** — the web app (Vite + React) + a Node driver that
   spawns the `claude` CLI (stream-json). You describe the drama; the **dramacode**
   Claude Code skill turns it into a series bible + episode as *diffable Python*
   (`series.py` + `episodes/ep001.py`), runs the render, and drives the notes loop.
2. **Authoring** — dramacode + `dramalib` supply the craft: the beat law, genre
   packs, the binge engine, the emotion→action table. Output is a spec, not a
   prompt blob.
3. **Per-shot render** — the `dramapy` engine renders each shot through the
   consistency pipeline below.
4. **Audio** — voices + score + SFX, generated then mixed (clip audio is discarded).
5. **Assembly** — ffmpeg stitches the shots + burns subtitles + lays the mix.
6. **Eval** — the screening-room critic watches the rendered frames; the binge
   eval scores the script before spend.

## Model-by-part

| Stage | Model / tool | What it does |
|---|---|---|
| Chat + build | **Claude Code** (your subscription) via the Node driver + `dramacode` skill | prompt → series.py + episodes/*.py → render → review loop |
| Story / spec | `dramacode` + `dramalib` (beat law, genre packs, binge engine) | write the episode as re-runnable Python |
| Character turnaround sheets | **nano-banana-pro** (base t2i) + **nano-banana-pro/edit** (4 views: front / profile / ¾ / expression) | one identity reference *set* per character — the consistency anchor (cached, reused every shot) |
| World anchors | **nano-banana-pro** | one locked reference image per location + recurring prop |
| **Keyframe** (per shot) | **nano-banana-pro/edit** — a reference *stack* (≤14 refs: cast views + location + props) at 9:16 / 4K; fallback **seedream/v4/edit** | the shot's first frame with every character/world identity locked in — the core consistency step |
| **Image-to-video** | **Kling 2.5 Turbo Pro** (`kling-video/v2.5-turbo/pro/image-to-video`) | animate the keyframe into a ~5s clip (motion inherits the frame's identity) |
| Lip-sync (dialogue) | **sync-lipsync/v2** (`sync_mode=silence`) | mouth the voice line on dialogue shots |
| Upscale (finish) | **Topaz** (`topaz/upscale/video`) | sharpen the clip |
| Voices | **ElevenLabs eleven-v3** (`elevenlabs/tts`), pinned per character; macOS `say` fallback | the spoken dialogue |
| **Score** | **ElevenLabs Music** (`elevenlabs/music`) with an arc-tracing `composition_plan`; **Lyria 2** fallback | the cinematic bed — ominous → rising → heartbreaking piano → orchestra+taiko climax → cliffhanger sting |
| SFX / foley | **mmaudio-v2** | layered sound effects at each peak |
| Mix | **ffmpeg** (sidechain compressor) | 3-layer mix: silent base + voice + score + SFX; the score ducks ~8-10 dB under dialogue |
| Assembly | **ffmpeg** (concat + libass burned subtitles) | 22 clips → one 2160×3840 h264 + AAC episode, `+faststart` |
| Eval — pixels | **screening-room** critic (samples rendered frames, scores a film rubric, routes reroll notes) | catches drift/defects on the finished cut |
| Eval — script | **binge eval** (`dramalib.evals`) | scores compulsion *before* render spend |

## The consistency mechanism (the whole selling point)

**Turnaround sheet → reference-stack keyframe → i2v.** Identity is fixed in the
*image* (the keyframe carries the exact face/costume/dragon from the reference
set), then Kling only has to add *motion*. This is why the same fire dragon, ice
dragon, Mei Lin, and Corvus hold across all 22 shots. Crowded shots (2 dragons +
2 riders) are the one place the keyframe model can still drop a character — the
planned fix is a pre-i2v consistency gate that verifies every cast member is
present in the keyframe and re-rolls it *before* spending on i2v.

## Cost (estimate)

`dramapy.costs.estimate_episode_cost` puts one full 4K render of this episode at
**~$46** on `cinematic` (keyframes + turnarounds + i2v + upscale + audio). That's
a directional estimate to reconcile against live invoices — it's what the
plan-and-approve gate shows before spending.

## Self-hosting path (production)

Today it renders on hosted frontier models via fal (best quality, per-render
cost). The self-host path (see `docs/oss-landscape.md`) swaps in open weights on
our own GPUs: **Wan 2.2** (i2v) + **Qwen-Image-Edit** (keyframes) + **VACE /
Phantom** (subject-to-video) via **ComfyUI**, **LTX-Video** as the fast draft
tier, **ACE-Step** (music) + **Chatterbox** (voice) + **LatentSync** (lip-sync).
