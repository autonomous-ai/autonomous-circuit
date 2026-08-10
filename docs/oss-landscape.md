# OSS landscape → what Autonomous TV adopts

Dee asked (2026-08-09): "there's a lot of open source projects out there for creating
videos. study all of them and see what we can learn to make our platform better." He also
pointed us at the GitHub `short-drama` topic (182 repos) — several are direct architectural
twins of what we built.

This doc is the **decision record**, not a dump. It answers two questions: (1) what OSS do
we build on instead of reinventing, and (2) what do the competitor short-drama products
teach us. Reports were produced by a research fan-out (nine agents) reading live repos.

**Context on our stack:** `dramapy` is our own engine (episode pipeline: spec → provider
render → voices/music/SFX → ffmpeg stitch). It is not a fork of any OSS project — the only
OSS underneath is ffmpeg. Today we render via hosted models (Kling/Veo/Seedance via fal.ai).
The self-host path (Vietnam DC + WeGPU 5090s) is what most of these adoptions unlock.

---

## The short list — what we adopt (ranked by leverage)

1. **Self-host base = Wan 2.2 (Apache-2.0) for i2v + Qwen-Image-Edit-2511 (Apache-2.0) for
   keyframes.** One permissively-licensed base-model family covers keyframes, turnarounds,
   i2v, multi-subject composition, and talking heads. This de-risks our two biggest hosted
   dependencies (nano-banana keyframes, Kling i2v). Kling still wins on identity-through-
   motion today, so self-host is a cost/control win first, a quality win later.
2. **Subject-to-video on the Wan family (Phantom-Wan, then VACE)** — inject the fixed cast +
   props straight into the motion model, skipping keyframe→i2v identity loss. This is the
   thesis move (persistent cast + props + world) and structurally beats our two-stage
   pipeline for many shots. Rides the same Wan base, near-zero extra infra.
3. **Remotion as the deterministic compositor** for titles, lower-thirds, **karaoke captions
   (`@remotion/captions` + local Whisper, word-level timestamps)**, transitions, end cards,
   timeline. We're a React app; this deletes our most brittle ffmpeg `drawtext`/overlay
   filtergraphs and makes the "look" versionable in code. Keep ffmpeg for concat/mux/
   loudnorm/encode. License: 4+ people → "Automators" $100/mo min + $0.01/render (budget it).
   Fallback: **Revideo** (MIT) if the license/render-cost bites.
4. **ComfyUI as the self-hosted render/orchestration engine** (workflow-as-JSON + API mode).
   Day-one model support, and `kijai/ComfyUI-WanVideoWrapper` already packages VACE / Phantom
   / ATI / MultiTalk / camera control / GGUF-FP8 quant — all the work we'd otherwise hand-roll.
   GPL-3.0 is fine server-side (no distribution event). Keep Diffusers for LoRA fine-tuning
   (character LoRAs) + bespoke steps.
5. **LTX-Video / LTX-2 (Apache-2.0) as the fast draft/preview tier** — near-real-time cheap
   iterations, LTX-2 has native synced audio. Let creators scrub many takes fast, then
   promote the winner to Wan (or hosted) for the final render. (→ roadmap R2 draft→HD tier.)
6. **A VLM-in-the-loop taste gate** — a vision model watches each rendered shot for character
   consistency + emotion and auto-triggers a `dramacode` re-roll. This is the quality loop no
   OSS competitor has; our screening-room critic is the seed of it. (Pattern from NarratoAI's
   Qwen2-VL highlight-picking.)
7. **LivePortrait (18.9k★) for cheap dialogue lip/expression retarget**; reserve Wan2.2-S2V /
   SkyReels-A1 for hero close-ups where audio-driven whole-face performance is worth compute.

**Study, don't adopt yet:** SkyReels V2 (infinite-length via Diffusion Forcing — the answer
to "keep a scene past 5s"), SkyReels V1 (drama-tuned faces, 33 expressions), Open-Sora 2.0
(the ~$200k training playbook for a future in-house model), DreamO (costume try-on),
OminiControl (prop-only consistency).

**Skip:** the whole IP-Adapter / InstantID / PhotoMaker / PuLID adapter camp (superseded by
in-context editors, and the InsightFace/antelopev2 face encoder is non-commercial — a license
landmine), ConsiStory / StoryDiffusion (they make *an invented* consistent character, not
*our exact* cast), MoviePy in any hot path (throughput/quality ceiling), Mochi 1 / Pyramid-
Flow / Allegro (behind the frontier), NVIDIA Cosmos (robotics world-models, wrong domain).

---

## Honest gap vs our current hosted stack

OSS as of Aug 2026 is roughly **mid-tier closed quality (≈ Kling 1.6-era)**, not the frontier.
Real gaps vs Veo 3 / Kling 2.x / Seedance:
- **Native audio + dialogue lip-sync** — Veo 3 does speech+sound in one shot; OSS needs a
  separate stage (LTX-2 a/v, or MultiTalk/FantasyTalking in post). Biggest drama-specific gap.
- **Long-take character consistency** — VACE + character LoRAs get most of the way; a face
  still drifts across many shots more than on closed tools.
- **Prompt adherence + complex multi-subject motion** — closed still follows intricate
  direction and keeps coherent motion better.

**Policy:** self-host Wan for the 80% of shots where i2v gives first-frame control ("good
enough", kills per-render cost); keep the hosted fal path (Kling/Veo/Seedance) for hero
shots, dialogue-heavy beats, and anything the creator/critic flags.

---

## Area detail

### Video models (self-host base)
- **Wan 2.2** (Alibaba, Apache-2.0, ★17k) — best open model. TI2V-5B runs on a 24GB GPU
  (our 5090s); A14B (27B MoE, 14B active) needs 80GB / the RTX 6000 Pro. Full i2v; variants
  for speech-to-video (S2V) and character animation/replacement (Animate-14B). Ecosystem
  (VACE, WanVideoWrapper) targets Wan first — decisive.
- **LTX-Video / LTX-2** (Lightricks, Apache-2.0, ★10.8k) — 13B + distilled 2B, up to 60s,
  multi-keyframe conditioning, LTX-2 adds synced audio. The draft tier.
- **HunyuanVideo** (Tencent, ★12.4k) — top-tier but Tencent Community License (MAU-gated
  commercial) → study, don't standardize. It's the base SkyReels-V1 is built on.
- **SkyReels V1/V2** (Skywork) — the most on-thesis: V1 human/drama-tuned (33 expressions),
  V2 infinite-length via Diffusion Forcing (~60s continuous). Study hard for the techniques
  even if we render on Wan.
- Hardware: RTX 6000 Pro (96GB) runs everything resident; 5090 (32GB) runs the 5B/2B tier
  native, 14B only via GGUF/FP8 + offload.

### Engine
- **ComfyUI** (★125k, GPL-3.0) — adopt as the render graph. Workflow-as-JSON = a versionable,
  A/B-able render recipe per shot-type; `kijai/ComfyUI-WanVideoWrapper` (★6.7k) packages the
  consistency/control stack. API/headless mode lets our backend drive it.
- **Diffusers** (★34.3k, Apache-2.0) — keep for LoRA fine-tuning + programmatic glue.
- **Hybrid call:** ComfyUI = generation + per-shot post (upscale, RIFE interpolate, lip-sync,
  VACE control); Diffusers = character-LoRA training; thin **ffmpeg** = final assembly.

### Character consistency (the core quality lever)
The field moved from embedding-injection adapters → **in-context editing** (edit the reference
pixels directly). That's *why* nano-banana beats the old adapters and *why our keyframe-edit →
i2v architecture is correct.*
- **Adopt:** **Qwen-Image-Edit-2511** (20B MMDiT, Apache-2.0, ★8.2k) — strongest OSS keyframe
  editor, multi-image input, tuned for character consistency; the OSS twin of nano-banana.
  Also use it to generate **turnaround/reference sheets** (one identity anchor → sheet + every
  shot consistent by construction) → maps onto our cast-book skill + R1.
- **Adopt/spike:** **Phantom-Wan** (Apache-2.0) subject-to-video; **VACE** (Apache-2.0)
  reference-to-video + character swap.
- **Skip:** IP-Adapter/InstantID/PhotoMaker/PuLID (superseded + InsightFace non-commercial);
  ConsiStory/StoryDiffusion (invented character, not our cast).
- **Honest:** nano-banana/Seedream still edit identity more cleanly on hard edits; Kling holds
  faces through motion better than Wan i2v today. Self-host is the roadmap, not a quality win.

### Editing / compositing
- **Remotion** (★55.9k) — adopt as compositor (see #3). Don't push generated *clips* through
  headless Chrome (slow/costly per frame) — ffmpeg lays the video bed, Remotion composites
  graphics/captions on top with alpha.
- **ffmpeg fixes we're probably getting wrong:** stream-copy concat (`-c copy` via concat
  demuxer) instead of re-encoding joins; normalize SAR/DAR + force CFR before concat; two-pass
  `loudnorm` to -14 LUFS (TTS+music+SFX at different levels); `-fflags +genpts` at cut
  boundaries; always `-pix_fmt yuv420p` + `-movflags +faststart`.

### Orchestration — steal + avoid (from the agentic generators)
The market proved the *faceless-narration* pattern at scale (MoneyPrinterTurbo ~102k★) and
proved **nobody has cracked character-consistent, quality-looped generative drama** — that's
our wedge, and we already build the two missing pieces (cast references + re-roll loop).
- **Steal:** FilmAgent's role split (director/writer/cinematographer/editor agents with review
  handoffs → our film-crew.md); NarratoAI's VLM-in-the-loop (→ taste gate); ShortGPT's
  editable IR between model and renderer (we already do it *better* — real re-runnable
  `dramacode` .py, not JSON); Whisper word-level captions (via Remotion).
- **Avoid (their mistakes = our differentiation):** stock footage as content (can't do
  recurring characters); one-image-per-segment slideshows (no cross-shot identity — story-
  flicks); one-shot fire-and-forget pipelines with no re-roll/review; Streamlit-glue + MoviePy
  coupling with no persistent canon (our series.py bible is the counter).

### Audio + lip-sync
The whole audio stack can plausibly go self-hosted; the real filter is **license, not quality**
— the best-sounding open models ship *non-commercial* weights (F5-TTS, Fish-Speech, MusicGen,
Stable Audio Open, MMAudio, IndexTTS). Two of our four hosted layers are already OSS-derived:
**fal's "mmaudio" SFX is literally the open MMAudio model** (CC-BY-NC weights — fal likely has
a commercial arrangement we don't), and **sync.so is Sync Labs, the commercial arm of the
Wav2Lip authors.** The commercial-safe self-host winners:
- **Voice — highest per-render cost, do first, hybrid.** Adopt **Chatterbox Multilingual
  (MIT)** as the cloned-voice + emotion workhorse; **Kokoro-82M (Apache)** for cheap/bulk/CPU
  narration. Keep **ElevenLabs v3** for hero *English* dramatic lines — the one thing OSS still
  can't match. For **Chinese short drama, pilot IndexTTS2** (SOTA CN, and duration-control
  built for dub sync) — but get Bilibili's commercial license in writing first.
- **Lip-sync.** Adopt **LatentSync 1.6 (Apache, 512px)** as the sync.so v2 replacement (budget
  engineering for temporal stability + HD upscale); **MuseTalk 1.5 (MIT)** as the fast/cheap
  real-time tier. Before committing, spend one pass on the DiT frontier (MultiTalk/InfiniteTalk,
  Hallo3, OmniHuman) — may leapfrog re-lipping for full-performance shots.
- **Music.** Adopt **ACE-Step v1.5 (Apache)** for instrumental score — genuinely good, 4-min,
  8GB, runs on a 4090; the single best OSS-vs-hosted swap in the stack. Keep ElevenLabs
  Music/Lyria for finished signature cues.
- **SFX.** Adopt **FoleyCrafter (Apache)** as commercial-safe foley; keep MMAudio internal
  until its CC-BY-NC weights are resolved for paid renders.
- **Skip:** Wav2Lip, SadTalker, V-Express, Bark, XTTS/Coqui, MusicGen, Stable Audio Open (for
  music) — non-commercial, abandoned, or superseded.

### Long-form + cross-shot continuity + upscaling
**The key reframe:** a 60-120s short drama is *many camera setups of the same people/world*,
not one 2-minute continuous take. "Long-video" repos (FramePack, Self-Forcing, StreamingT2V)
keep *one take* coherent — they do **not** give cross-shot identity for free. Every credible
multi-shot result today comes from **reference-conditioning + planning**, not a bigger
autoregressive model. Bet engineering there. Ranked:
1. **Reference-image identity locking via VACE (Wan-native, Apache).** Feed our `cast/<id>/`
   reference sheets into VACE reference-to-video so every shot renders the same face/wardrobe.
   Biggest single continuity win; drops into the Wan 2.2 stack. SkyReels-A2 is the fallback.
2. **Last-frame→first-frame I2V chaining + keyframe conditioning — DO THIS NOW.** Feed the last
   frame of shot N as the conditioning image for shot N+1 for continuous action; use LTX
   multi-keyframe conditioning (or Wan first-last-frame) to hit a specific composed frame.
   Cheapest, most controllable continuity mechanism, **needs no new base model — we can add it
   to dramapy against the current hosted providers today.** (Candidate near-term roadmap item.)
3. **World+cast bible in the planning layer — EXTEND what we have.** series.py + cast-book +
   story-analysis already *is* the FilmAgent pattern (validated). Push more state in: per-scene
   lighting/location/wardrobe tokens injected into every shot prompt so the *world* stays fixed,
   not just faces.
4. **Study only (borrow concepts):** Self-Forcing's anti-drift training + FramePack context-
   packing, for longer *single* takes (a continuous 8-10s emotional beat past clip length).
   SkyReels-V2 diffusion-forcing for long takes; SkyReels-V1/A1 for the drama look + talking-
   head dialogue. Ignore StreamingT2V/AnimateDiff/StoryDiffusion as engines (stale / SDXL-era).
- **Upscaling:** adopt **Real-ESRGAN + Practical-RIFE** as the permissive, cheap self-host
  baseline (runs on the 5090s) now; keep Topaz-via-fal as the managed fallback; **pilot SeedVR2**
  (Apache, the only OSS upscaler that plausibly beats Topaz) when H100-class GPU time is
  available. Ignore Upscale-A-Video (non-commercial).

---

## Short-drama competitor products (from the GitHub `short-drama` topic)

### 短剧 pipelines + likeness-safety (landed)
Studied: `yfge/ai-video-studio` (52★, MIT — craft + architecture gold), `drasstry/shortdrama-
pipeline` (112★ — best engineering discipline), `Anil-matcha/Open-AI-Micro-Drama-Generator`
(452★ — clean minimal, Western not 短剧), `yubowen123/celebrity-face-search` (4★ — likeness-
safety blueprint). Two ship with no license file → reference only, don't copy code.

**The valuable finds (ranked STEAL):**
1. **Hook/twist/cliff cadence in SECONDS** (from ai-video-studio's microgenre framework):
   episode 60-120s; cold-open hook by **5-8s**; inciting conflict by **20-30s**; **≥1 reversal
   per episode** (2 if >90s); **cliffhanger in the final 6-10s**. Twist density by genre:
   revenge/mafia 1.5-2.0/ep, romance/contract 1.0-1.2, supernatural 1.2-1.6. Series arc: eps
   1-3 core relationship + one betrayal/reveal, 4-6 first payoff + escalation, 7-10 second
   payoff + irreversible reveal; shows run **60-80 episodes**; real rhythm = **3 payoffs in the
   first 10 eps (ep 6/8/10)** — scheduled build→release, not literally every few seconds.
   → encode into story-analysis + make the critic score against it.
2. **HookScore rubric** (adopt as the critic's script gate): five 0-5 dims — **conflict
   intensity, character recognizability, cultural fit/localization, clip-ability (cuttable
   hooks per 60s), logic coherence**. Pass ≥4.0 & no dim <3.5; Review 3.5-3.9; **Rewrite <3.5**
   → emit `rewrite_guidance` + `suggested_ad_hooks`. "Clip-ability" as a first-class score is
   the non-obvious, high-value one.
3. **Pre-publish likeness safety gate** (celebrity-face-search blueprint): YuNet face-detect →
   SFace embedding → cosine similarity vs a public-figure library → Top-K candidates + evidence
   + source/license. Gate at 3 points (before locking the character sheet, after first-frame
   gen, before any poster/ad). Verdicts: pass / regenerate / escalate-to-legal / licensed-
   exception (logged). Philosophy: **screener, not judge** — evidence for a human, never an
   automatic legal verdict. Cites PIPL/GDPR Art.9/EU AI Act Art.50/CA Civil Code §3344. Real,
   cheap legal protection we currently lack. (Build our own — their repo is unlicensed.)
4. **Reaction-shot + 打脸 ("face-slap") rule:** the close-up *after* the emotional hit + the
   crowd reaction is the money shot — lock camera + timing on it. Absent from every generator.
5. **Market×micro-genre matrix with localization wrappers:** same emotional engine, swappable
   skin, per-market no-go flags (child harm, non-consent, underage, defamation). NA=mafia/
   werewolf/billionaire-secret-identity, LATAM=revenge+family-honor, SEA=CEO-contract-marriage,
   MENA=elite-family-intrigue, KR/JP=idol-scandal, Global=secret-baby. **Localization is
   rewrite not translation** — NA wants revenge *follow-through*, not the Chinese silent-suffer/
   win-back (追妻火葬场); even props flip (US green=up vs China red=up).
6. **Two hard approval gates before spending video credits** (lock script → lock character
   sheets → render). Protects per-episode unit economics. → aligns with roadmap R3.
7. **Shot discipline checks:** 4-10s single-beat shots, ≤1 line dialogue/shot, overload
   detection (>5 action markers / >3 chars / >3 cuts per shot). Cheap deterministic guardrails.
8. **Provider capability-matrix + graceful field-drop with audit trail** — the right way to
   abstract our hosted roster (Kling/Seedance/Jimeng/Hailuo) so unsupported params never
   silently no-op (records `audit.dropped_fields`).
9. **Ad-cut sheet ("投流表") as a first-class output** — auto-emit 15/30/60s cuttable clips with
   hook_type, key_line, first-frame visual hook, CTA. Paid traffic is 80%+ of 短剧 cost; this is
   how the business actually monetizes — a distinct sellable feature.
10. **Static-vs-dynamic character features + per-character bound voice** → fold into cast-book
    (separate permanent identity from per-scene wardrobe; bind a voice_id per cast member).

**WEDGE confirmed:** nobody does character consistency well (best is one approved portrait
threaded by text) — our cast-book four-view sheets + reroll are already ahead; none do real
voices/lip-sync (only ai-video-studio builds a TTS audio *timeline* — steal its beat model for
our subtitle/timing layer — but no lip-sync anywhere); the two popular repos have no critic/
rewrite loop; assembly is naive ffmpeg/moviepy concat; **none close the loop to distribution**
(the ad-cut → paid-traffic → data → rewrite loop is where the money is, and it's unoccupied).

Engineering notes to remember: Seedance needs **publicly reachable** reference-image URLs (local
paths fail); script-LLM prompts force strict JSON with an auto-repair retry; fake-provider mode
runs the whole state machine at zero API spend (we already do this with mock).

### Claude-skill twins (landed) — our exact architecture
Studied: **worldwonderer/drama-skills** (586★, MIT, active — the teacher), **OnlyShot** (MIT —
prescriptive, renders), **ai-mandrama-skills** (MIT — full DIY ffmpeg assembly), **video-prompt-
engineer** (MIT — Seedance storyboards). `drama-skills` was distilled from a real comic-drama
studio that ran 1000+ AI projects + ~80K lines of tooling, then dropped the GUI.

**drama-skills' 8-skill taxonomy** (router + 6 stages + independent reviewer): short-drama
(router/lifecycle/dashboard) · develop (novel→episode-map, genre+hook playbook) · write
(episode card, causal beats) · assets (continuity ledger) · image-prompts · storyboard
(coverage, frozen keyframes) · video-prompts (start→change→end motion spec) · review
(independent) + a maintainer `knowhow` craft-learning flywheel. Each skill = SKILL.md +
hash-pinned `suite-ref.json` + on-demand references + JSONL asset templates + deterministic
Python checkers. Our analogs: story-analysis≈develop, dramacode≈write+storyboard+video-prompts,
cast-book≈assets+image-prompts, screening-room≈review. **Gaps we lack: a router/lifecycle
layer and the knowhow flywheel.**

**NEW distinct STEAL (beyond the pipelines report):**
1. **Genre + hook craft library** — combine drama-skills' *comparative* playbook (pressure
   mechanics per genre + the "this-ep result → next-ep pressure → invalid substitute" hook
   table) with OnlyShot's *prescriptive* **7-beat sheet** (招1 confrontational open 0-3s → 招2
   30s burst → 招3 60s first payoff → 招4 90s reversal → 招5 120s big payoff → 招6 150s ultimate
   hook → 招7 180s red countdown "未完待续"; target ~4 climax-points/min; a screenshot-shareable
   金句 subtitle per ep). **Ship as an overridable `craft_default`, NOT a hard gate** — the
   deliberate philosophical fork: drama-skills refuses a fixed beat sheet (causal pressure over
   formula); OnlyShot *is* the beat sheet. Adopt beats as a strong default, keep the escape
   hatch so we don't ship 60 identical episodes.
2. **emotion→physical-action lookup table + "turn off the sound" test** (video-prompt-engineer's
   `情绪外化速查.md`): map 紧张/悲痛/愤怒/… to concrete micro-actions; the viewer should
   understand the character's state with the sound muted. Cheapest craft-per-line win — makes
   shots *filmable* instead of "she looks nervous." → fold into dramacode shot-prompt gen.
3. **Two-step occurrence→decision continuity ledger** (drama-skills assets): extract every
   appearance, classify reuse/new_variant/new_asset/unresolved; split identity-anchor vs.
   transient-state; track cross-episode outgoing→incoming deltas; **repeat a stable asset index
   at every character mention in multi-character shots** (fixes face-swapping in crowds). →
   upgrade cast-book from prompt-writer to a real consistency engine.
4. **Storyboard-still gate before video — 18× cost lever** (OnlyShot Phase 1.5): render/approve
   a cheap keyframe still (3 credits) as the video's first frame before spending on the clip
   (55 credits). "Confirm the frame, then animate." Biggest hosted-bill lever. → roadmap R3/R4.
5. **Audio-ledger-drives-duration** (video-prompt-engineer): dialogue/VO length is the *hard
   constraint* that sets shot duration; log every voiced line with estimated duration; if a unit
   >15s, split. Plus drama-skills' rule that **unallocated seconds get filled by the render
   engine with unapproved motion** — segment times must sum exactly to shot length. Prevents cut
   lines / padded-garbage shots.
6. **Independent fresh-context critic + explicit schema** (drama-skills review): reviewer must be
   a *fresh context that didn't write the thing*; verdicts APPROVE/APPROVE_WITH_NOTES/REVISE/
   PROVISIONAL; findings fatal/error/warning/note bound to exact shot-ID hashes; degrades
   transparently if isolation unavailable. + OnlyShot's attitude: "don't praise, be sharp, give
   the concrete rewrite." → harden screening-room.
7. **Deterministic checkers for the boring math** (storyboard_check/container_check/motion_timing):
   arithmetic + set audits (duration sums, coverage, no shot in two/zero containers) with no LLM
   in the loop. → add to dramapy.
8. **Aspirational — the knowhow learning flywheel:** mine our own real production runs → cards →
   genre×mechanism coverage matrix → blind fresh-agent forward-eval → promote to rubric/
   reference → ledger. How craft compounds into a long-horizon moat. Maps to flywheel.md.

**WEDGE strongly confirmed** (four competitor reports agree): (a) **we're the only one whose
loop closes on actual rendered pixels** — drama-skills/video-prompt-engineer render nothing;
OnlyShot/ai-mandrama render but their critic only reads the script/plan, a human eyeballs the
video. Screening-room watching the mp4 and routing shot-specific rerolls is the whole ballgame.
(b) **True end-to-end** — the others dump you into CapCut / a 176-line ffmpeg script + manual
TTS/BGM/subtitles; dramapy does keyframes→video→voice→music/SFX→lip-sync→stitch in one pass.
(c) **Multi-model via fal** vs. their single-vendor Jimeng/Seedance lock. (d) **We can own the
English/Western (ReelShort/DramaBox) craft library** none of them wrote — theirs is 红果/抖音/
番茄-shaped. Raise our rigor toward drama-skills without inheriting its ceremony.

### Top platforms (landed) — waoowaoo + inkos
The two "AI film" leaders split cleanly and **neither does our job**: **waoowaoo** (CC BY-NC-SA,
non-commercial; Next.js+MySQL+Remotion+fal, solo-dev beta) renders cinematic video but has
**zero automated quality loop** (pure human candidate-picking). **inkos** (AGPL, npm
`@actalk/inkos`, Kimi-sponsored, mature) has a **superb automated critic loop** but renders
**no video** (it's a novel/interactive-fiction engine — "film" is a label overreach).
**Autonomous TV = the union (rendered vertical drama + automated screening critic), and that
union is open whitespace.**

**Two validations of work already in flight:**
- **inkos's non-regressing critic loop == the keep-the-best screening loop we shipped today.**
  inkos: auditor emits 0-100 + typed issues; a revision is accepted **only if it beats the
  prior by ≥ epsilon (3 pts)**; keeps all snapshots and **rolls back to the highest-scoring
  version**; `parseFailed` guard = never auto-revise on an unparseable critic. Independent
  convergence on our design. **To add: the epsilon (only accept a materially better reroll) +
  the parse-fail guard.**
- **waoowaoo's `linkedToNextPanel` first/last-frame chaining == roadmap task #34** (last frame
  of shot N seeds shot N+1). Now confirmed by three independent sources (long-form OSS,
  waoowaoo, our own roadmap). Buildable on fal (Kling/Luma) today.

**NEW distinct STEAL:**
1. **Decomposed director stack** (waoowaoo) — split shot authoring into separate structured LLM
   passes: `storyboard_plan` → `cinematographer` (composition/lighting/color_palette/atmosphere/
   technical as JSON) → `acting_direction` (per-character **observable** notes — expression/body/
   gaze, "no abstract words like 'sad'") → `storyboard_detail` (motion-ready video prompts).
   Highest-leverage quality upgrade; maps onto our episodes/*.py shot spec + dramacode. (Note the
   convergence with video-prompt-engineer's "turn off the sound" test — both demand *observable*
   action, not adjectives.)
2. **Versioned character appearances** (waoowaoo) — appearance as a first-class versioned list
   (`appearanceIndex`, `changeReason` e.g. "wearing armor", own reference keyframe, undo);
   panels reference character *by name + appearance*; a cross-project Asset Hub
   (GlobalCharacter/Location/Voice). The consistency data model cast-book is missing.
3. **Spatial "slot" staging** (waoowaoo) — locations carry named placement anchors
   (`available_slots`); pin each cast member to a slot per shot. Cheap fix for "where is everyone
   standing" drift + composition control.
4. **Scored non-regressing critic details** (inkos, above) — add epsilon + parse-fail guard to
   screening-room; emit typed issues with `repairScope: local|structural`.
5. **Governed-context + JSON-delta state** (inkos) — compile context deterministically before
   generating; model emits a **Zod-validated JSON delta that CODE applies via an immutable
   reducer** (corrupt data rejected, not propagated); auto-snapshot per episode; per-shot
   trace.json. The reliability spine for drama-as-code. (We already have the code-not-prose
   spine via .py; borrow the validated-delta + snapshot discipline.)
6. **Data-driven capability + pricing catalog with CI guards** (waoowaoo `standards/
   capabilities/*.json`, ~12 guard scripts: no-hardcoded-model-capabilities, no-provider-
   guessing) — adding a model becomes a data edit. Fits our Grid/provider-protocol instincts.
7. **Durable resumable render engine** (waoowaoo: Run→Step→Attempt→Checkpoint→Artifact, lease+
   heartbeat, **dedup by input hash**, cancel, per-attempt cost) — steal the schema when we go
   multi-user. (Our content-addressed render cache already does the dedup-by-hash half.)
8. **De-slop pass** (inkos) — per-genre fatigue-word / AI-tell lists + a statistical style
   fingerprint, applied to script + voice lines; plus a `sales-package.md` selling-points output
   per episode (pairs with the 投流表 ad-cut sheet).

**WEDGE (all six competitor reports agree):** the union is empty (video + automated critic);
**continuity audit over the *visual* timeline is done by nobody** (waoowaoo has consistency
*inputs* but never checks shot 12 matches shot 3's outfit; inkos has a 37-dimension continuity
auditor but only over text — run those dimensions as **critic dimensions over the rendered
board**, unique to us); drama-as-code (skills authoring series.py/episodes/*.py — diffable,
rerollable, NL-editable) beats their JSON-in-a-DB prompt chains; our local-first, multi-model,
composable stack beats waoowaoo's non-commercial solo-dev SaaS. Skip inkos's interactive-fiction
branching graph — category overreach, not our lane.

### End-to-end workspaces (landed)
Studied: **ArcReel** (3.9★, AGPL — Python FastAPI + React), **Jellyfish** (5.9★ — pre-production
only, no audio/subs/assembly despite the README), **ai-fusion-video** (1.3★, Java — deepest
backend), **ZJT/智剧通** (187★ — production "drama factory", open-core lead-gen).

**The headline:** **ArcReel independently built our exact bet** — Claude Agent SDK + skills +
orchestrator-over-subagents + in-process `mcp__arcreel__*` tools + a leased DB queue. Our
architecture is **validated, not novel** (parity vs ArcReel; edge over the other three). And
across all nine competitors, **not one has an automated media-quality critic** — every review
loop is human gates or text-only QC. **Screening-room watching the pixels is the wedge**, now
confirmed 9/9.

**NEW distinct STEAL:**
1. **CapCut/JianYing editable-draft export** — 短剧 creators finish in CapCut; we emit only flat
   MP4+SRT. Both ArcReel and ZJT ship a draft export but **both are shallow** (empty texts/
   transitions/keyframes). Steal the concept and beat it: carry our subtitle + transition +
   music timing *into* the draft. (Distribution-critical for real creators.)
2. **Decoupled leased DB task queue** (per-provider concurrency caps, dependency edges, dedup,
   crash-resume) — we render largely synchronously; a 60-ep series against rate-limited hosted
   APIs needs this. ArcReel's is cleanest. → pairs with roadmap R2 / multi-user.
3. **Grid-generate-then-split character sheets** (ZJT) — render a 2×2/3×3 sheet in ONE call so
   all views share one identity, then split. Cheaper than our per-view edits and a strong
   identity lock; "solves face collapse." Evaluate against R1's turnaround-SET.
4. **Non-destructive candidate/version model per shot** — every reroll = a new take you compare
   and pick (ZJT `selected_*_id`, ArcReel VersionTimeMachine). Fits keep-the-best; our
   idempotent regen currently *overwrites* — we lose the comparison.
5. **Provider capability registry + slot planning** — query each model's caps (last-frame? max
   ref images? durations? 9:16?) and assemble frame slots, don't hardcode. (Same lesson as
   waoowaoo's capability catalog — two independent sources now.) → R2.
6. **Cost estimate-before-generate + per-shot cost ledger** — each shot spends real money; show
   the estimate, track spend per project/episode/shot. → pairs with the spend-gate (R3).
7. **Tool-only-mutation guardrail** — project JSON editable *only* via validated tools; block
   stray Write/Edit/Bash on the spec (our Node driver spawns the claude CLI over project files,
   so a stray write can corrupt the spec). Real hardening for our driver.
8. **Cheap pre-flight gates** — asset-readiness (every character has refs+voice before video) +
   content-compliance banned-word loop (matters for Douyin/Kuaishou distribution).
9. **Assembly correctness** — normalize every audio segment to constant SR/channels/bitrate
   before concat (else "滋滋" buzzing); bundle a CJK font for libass. Small real fixes for
   stitch.py/subtitles.py.

**WEDGE reconfirmed:** the automated critic loop (9/9 don't have it); finishing in the core
(lip-sync + upscale + audio mix + burned captions — Jellyfish/ai-fusion have no finishing,
ArcReel can't burn subs, ZJT burns subs but nothing else); consistency rigor (our turnaround-SET
+ reference-STACK vs their library lookup — but steal their single-call grid *economy*); zero-
friction mock path (all four are dead without API keys); idempotent content-hash regen. **Gaps
to close (their lead):** editable CapCut delivery, provider breadth + failover, queue/throughput,
cost visibility.
