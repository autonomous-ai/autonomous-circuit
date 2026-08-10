# OSS reuse decisions (survey 2026-08-09)

Per Dee's directive: build on proven wheels, the way Panda built on CadQuery. Full survey
with stars/licenses verified via GitHub API lives in the org repo
(`projects/video/` research archive). The decisions:

## Build on (production dependencies)

| Layer | Choice | License | Why |
|---|---|---|---|
| Render runtime (self-host path) | **ComfyUI headless** (workflow-JSON over HTTP) + native Wan 2.2 nodes + **lightx2v 4-step distill LoRAs** | GPL-3.0 (fine out-of-process over HTTP) / Apache-2.0 | The de-facto production standard for open-weights video; 124K★. Fronted by a thin queue wrapper cribbed from SaladTechnologies/comfyui-api (MIT). Lands as dramapy provider `"comfyui"` (after v1; v1 ships `mock` + `fal`). |
| Assembly | **ffmpeg direct** (filtergraphs: compose, concat, subtitle burn, audio mix) | LGPL/GPL binary use | What every Gen-2 drama platform ships. Zero Python deps. |
| Captions (upgrade path) | **pysubs2** for word-timed karaoke ASS (the TikTok caption look) | MIT | v1 uses hand-written SRT/ASS; pysubs2 when captions get styled per-word. |
| TTS (character voices) | **Fun-CosyVoice 3.0** or **Qwen3-TTS** default; **GPT-SoVITS** premium tier; VibeVoice spike for multi-speaker scenes | Apache-2.0 / Apache-2.0 / MIT / MIT | Zero-shot clone → per-character voice bank across episodes. Post-v1 wiring; v1 mock provider needs no TTS. |
| LoRA training (cast lock, open-weights path) | **musubi-tuner** (CLI wrap); DiffSynth-Studio if we want all-Apache train+infer | Apache-2.0 | The 2026 community standard for Wan character LoRAs; 12GB VRAM. |
| Lip-sync post-process (optional stage) | **LatentSync 1.6** (quality) / **MuseTalk** (speed) | Apache-2.0 / MIT | Replaceable stage; field is moving to S2V-native. |
| Player chrome (upgrade path) | **media-chrome** | MIT | v1 is plain `<video>`; media-chrome when player UX grows. |

## Crib patterns from (no code reuse where license forbids)

- **huobao-drama** (13.9K★, **CC BY-NC — patterns only, zero code**): the 4-agent decomposition
  (script_rewriter / extractor / storyboard_breaker / prompt_generator) — mirrored in our
  skills' workflow steps.
- **alibaba/lumenx** (MIT — code liftable): storyboard schema, character turnaround sheets
  (三视图) → R2V consistency pattern, batch 抽卡 re-rolls, ffmpeg export path.
- **VideoClaw** (MIT): stage-gated pipeline where every intermediate artifact is viewable /
  editable / regeneratable; short-drama infinite episode continuation; "chat an idea, get a
  film" interaction model. Worth reading end-to-end.
- **Toonflow** (Apache-2.0): 3-layer agent system (decision / execution / supervision) —
  mirrored by our silent review loop's phase structure.
- **MoneyPrinterTurbo** (102K★, MIT): task model, subtitle module, BGM mixing, FastAPI surface.
- **OpenCut / omniclip** (MIT): timeline state architecture if/when the storyboard strip
  becomes an editor.
- **dramaclaw** (**Elastic License 2.0 — prohibits hosted service; NEVER build on**): study
  its published dramas (归灵司, 天命不可欺) as the quality bar only.

## Hard-avoid list (license tripwires, verified from LICENSE files)

fish-speech (research-only since 2026-03) · index-tts (written authorization required) ·
F5-TTS released checkpoints (CC-NC) · edge-tts (unofficial endpoint, ToS risk) ·
Remotion + react-video-editor + twick (source-available/per-render tiers) ·
huobao-drama code (CC-NC) · dramaclaw code (ELv2) · LingGuo/printfilm/Micro-Drama-Generator
(no license at all) · HunyuanVideo-1.5 (license void EU/UK/KR — geofence, already in contract).

## The proven pipeline shape (identical across huobao / lumenx / VideoClaw / Toonflow)

idea → LLM script rewrite → entity extraction (characters/scenes/props) → per-entity
reference images → storyboard/shot breakdown → per-shot prompt gen → I2V/R2V clip gen with
batch re-rolls → TTS dubbing → per-shot ffmpeg compose (subtitle burn + audio mix) →
episode concat — every stage human-inspectable and regeneratable. Autonomous TV adopts this shape;
differentiation = the agentic chat layer (plan→approve→build→notes) + open-weights cost
structure.
