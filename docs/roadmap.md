# Autonomous TV — research-driven build roadmap (2026-08-09)

> **Night build 2026-08-10 (renamed to Autonomous TV).** Shipped since the 08-09 update,
> all gates green (203 dramapy / 89 dramacode / viewer 62 / evals 6): **R4 pre-i2v consistency
> gate** (`consistency.py`, off by default — catches the crowded-shot character-drop that needed
> a manual audit); **R3 foundation** — `costs.py` (render-cost estimate) + `plan.py` (the
> approve-before-spend plan); the **crew-orchestration** doctrine (decision-rights, daily-loop
> state machine, picture-lock + spend gates); the **mega-hit teardown** baked into craft
> (dramatic-irony gap + 虐/爽 ratio in the binge engine, dignity-theft cold-open, peeled-reveal
> ladder, craving-peak paywall, `flashmarry` genre, `titles.py` generator); the **master-shot-
> craft** reference (great-film technique for 90s vertical); `docs/pipeline.md`. Also fixed the
> live render bugs (600s→1800s fal budget, Kling 2500-char prompt cap, the missing-shot
> idempotency no-op) → the 22-shot 4K "Crown of Ash" demo. **Still needs live app + funded fal
> (do with Dee around):** wire the model router (R2) + colorist stage (R4b) + production locks
> (R3) into the render path; last-frame chaining (#34); verify the consistency gate's VLM backend.
>
> **Status update (2026-08-09, post OSS/competitor + phenomenon study).** A 13-agent
> research wave cross-checked this roadmap against 9 OSS/competitor products and the
> short-drama market. Findings + decisions: `docs/oss-landscape.md`, `docs/short-drama-
> playbook.md`. **The roadmap held up** — the research independently validated our biggest
> bets (consistency #1 lever; the automated pixel critic — 9/9 competitors lack it; last-
> frame chaining; a non-regressing critic loop).
>
> **Shipped since this roadmap was written** (all gates green): the consistency P0s —
> four-view turnaround sheets + reference-STACK keyframes on nano-banana-pro + world anchors
> (CASTING/CAST P0s ✅); the screening-room critic (CRITIC P0 ✅) + a 9th `shareability`
> (clip-ability) dimension; the screening loop now converges and keeps the best cut; nine-
> genre playbook + emotion→action craft references; the ad-cut-sheet (投流表) pattern
> (distribution money-shape). fal `flux-pro/v1.1-ultra` anchor retired for nano-banana-pro.
>
> **Net-new items the competitor study adds (not yet in the waves below):**
> - **[P1] Pre-publish likeness safety gate** — YuNet+SFace similarity vs a public-figure
>   library; screener-not-judge; gate at sheet-lock / first-frame / pre-ad; verdicts pass/
>   regenerate/escalate/licensed-exception. Real legal exposure at UGC scale (PIPL/GDPR/EU AI
>   Act/§3344). *(from celebrity-face-search; build our own — theirs is unlicensed.)*
> - **[P1] CapCut/JianYing editable-draft export** — 短剧 creators finish in CapCut; both
>   ArcReel and ZJT ship a draft export but shallow. Beat them: carry our subtitle/transition/
>   music timing into the draft. (Distribution-critical; complements the OTIO P2 item.)
> - **[P1] Grid-generate-then-split character sheet** — render a 2×2/3×3 sheet in ONE call so
>   all views share one identity, then split. Cheaper per identity than per-view edits;
>   evaluate against the turnaround-SET. *(from ZJT.)*
> - **[P1] Non-destructive candidate/version model per shot** — every reroll = a new take you
>   compare and pick; our idempotent regen currently overwrites (loses the comparison).
> - **Fold into existing items:** the critic's **non-regressing epsilon + parse-fail guard**
>   (already in the driver loop — mirror in the skill rubric); the **decomposed director
>   pass** (plan→cinematography→acting-direction→motion) into the DIRECTOR P1 stage; a
>   **provider capability catalog** (two independent sources: waoowaoo + ai-fusion) into the
>   model-router P0; a **leased task queue** into the fan-out P1.
>
> **Blocker:** fal balance exhausted — all *live* rendering/verification is blocked until
> topped up. Everything above was built/verified offline on the mock provider.

# Autonomous TV — Build Roadmap: the Claude Code of Video

**Goal:** a non-producer types a sentence and gets an Oscar-level short film they can't stop watching. Two things have to be true at once, and they pull on different levers:

- **It has to look and feel like cinema** — same face every shot, faces that *act*, one coherent color look, sound that lands on the beat, motion that doesn't break. This is the production-quality axis.
- **The story has to be Oscar-worthy *and* bingeable** — a real character arc under subtext, a gut-punch that makes people cry, plus the hook/cliffhanger machine that wins the scroll. This is the craft axis.
- **And a non-producer has to trust it with their money** — plan before spend, the crew checks its own work, every artifact is inspectable. This is the adoption axis (the actual "Claude Code" part).

## The four biggest levers (where the wow comes from)

1. **Consistency is the #1 dropout driver (92% drop on visual discontinuity).** Our pipeline is ~3 generations behind: one 2024-era Flux portrait → a Seedream-v4 keyframe that only ever sees a single reference. The single fastest quality jump we can make is to render the four-view turnaround sheet `cast-book` already writes prompts for, and feed a *stack* of references into a Nano-Banana-Pro edit. Spec'd, half-built, one weekend to the biggest visible gain we have.
2. **The retry tax is ~70% of budget (5–6 gens per usable shot).** A per-shot model router + native long-take/multi-shot generation + regional retakes cuts the gacha and raises the ceiling at the same time.
3. **Acting lives in the whole face, not the mouth.** Our lip-sync repaints mouths on clips the character never performed — dead eyes on exactly the emotional beats that win awards. Audio-driven performance is the fix.
4. **The self-closing quality loop is the adoption unlock.** Plan-and-approve-before-spend + inline dailies QC + a critic that grades the crew's own work is the film equivalent of "run the tests until they pass." It's what lets a non-producer walk away and trust the result.

Priority tags: **[P0]** build next (this is the top-10), **[P1]** fast follow, **[P2]** platform depth. Effort: S (≤1–2 days) / M (≤1–2 weeks) / L (multi-week).

---

## PRODUCER / 1st AD — orchestration, locks, spend gates
*Current: an implicit render pipeline with a soft review loop; no locks, no plan-approve, no batching.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Plan-and-approve-before-spend gate.** Before any paid gen, surface script + shot list + storyboard *stills* + per-shot cost estimate; editable; render only on approval. | Claude Code "plan mode" pattern; wind-comic vision-audited storyboards; storyboard stills are near-free (nano-banana $0.039) vs Veo/Kling seconds. | M | The core trust + money mechanism. Turns a black box into a tool a non-producer spends real money through. |
| **[P0] Three hard locks as pipeline states that gate spend:** `script-lock` → `per-shot-accept` → `picture-lock`. No color/upscale/lip-sync before per-shot-accept; no music/SFX/composer before picture-lock (the "turnover" rule). | Encode as spec states in `generation.py`; the real post turnover sequence. | M | Stops us grading/scoring/upscaling shots that later get recut — pure waste against per-accepted-shot economics. Foundational for every finishing stage below. |
| **[P1] Batching optimizer ("setups").** Cluster shots sharing cast+LoRA/refs+world+lighting and dispatch each cluster together (load refs once, render the cluster). This cluster plan *is* the render plan. | 1st-AD stripboard batching; maps to fal concurrency + reference reuse. | M | Fewer reference reloads, lower per-shot cost, better cache hit-rate. Cost lever with no quality cost. |
| **[P1] "Call sheet" as the unit of render dispatch.** One self-contained record per shot: cast_ids + pinned ref version + world refs + size/angle/lens/move + lighting + voice + emotion + line. It's both the parallel-dispatch unit and the deterministic re-run record. | StudioBinder call-sheet pattern; extend `ShotContext`. | S | Clean parallelism + byte-reproducible single-shot re-renders. |
| **[P1] Cost-permission gradient + budget guard.** Auto-allow free ops (stills/TTS/layout); ask before paid gen; "auto mode" runs to a budget cap; hard stop at the cap. Per-project cost attribution. | wind-comic budget guard + comfyui-mcp "ask before paid credits"; Claude Code permission modes → dollars. | M | Independently arrived at by both reference OSS projects — a validated pattern for paid agents. Prevents runaway spend; a headline adoption feature. |
| **[P2] Decision-rights map in `film-crew.md`.** Director agent owns creative intent; producer owns budget/schedule/delivery and cannot override intent; department agents execute strictly within the locked brief; only true creative forks escalate to the human. | Real producer/director split; mirrors the org thesis. | S | Prevents department agents silently re-interpreting the vision across a 60-episode run. |

---

## WRITER — story-analysis + dramacode (the craft engine)
*Current: bingeable-microdrama craft is strong (hook/escalation/cliffhanger, bond→loss, feeling cadence). Prestige craft and the retention *schema* are the gaps. A platform-wide craft upgrade already landed (commit d1155fe).*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Prestige story mode** on `beat_sheet(mode="prestige")`, bundling five missing primitives: **(a) CHARACTER_ARC** — Ghost/Wound, the Lie, WANT vs NEED, and the beat they choose need over want; **(b) CONTROLLING_IDEA** — one sentence stated obliquely once, proven by the climax; **(c) subtext lint** — flag emotion-words in dialogue ("I love you/I'm scared"), require one line where action contradicts speech; **(d) scene value-polarity** — `validate_scene_turns()` flags any scene whose start/end charge has the same sign; **(e) CRISIS dilemma** — a best-bad-choice before the climax. | Save the Cat 15-beat scaffold scaled to runtime; McKee/Story Grid five commandments; Weiland/Truby arc; Stanislavski subtext. New `references/{character-arc,subtext,scene-turns,prestige-short-structure}.md` + dramalib helpers. | L | **The single biggest prestige differentiator.** Microdrama reverses fortune *at* the hero; Oscar shorts *change* the hero. This is the spine of every awarded arc, and nothing in the skill has it today. |
| **[P0] The kama-muta "being-moved" turn** as a first-class EMOTIONAL_TURN, with placement: the tear-trigger is a *sudden deepening of connection* (recognition of being loved, reunion, witnessed sacrifice/moral beauty), placed right *after* the wound, not the wound itself. | Kama-muta research; new `references/why-we-cry.md`. | S | This is the actual science of movie-crying. Our five turns skew to loss/betrayal ("cut on the wound") which reads bleak, not moving. This is the mechanism that makes audiences *cry*. |
| **[P0] Retention schema as a hard beat-clock** (for the bingeable axis): 0–3s hook, 5–15s stakes, 15–60s ONE escalation + ONE power-shift, 60–85s turn, 85–90s cliffhanger; enforce 120–180 words / ~90s and one-reversal-per-60s. | ReelShort/DramaBox microstructure; lint pass in dramacode before render. | M | Makes every generated episode structurally correct for completion-rate by default — a non-producer can't feel pacing, the schema supplies it. |
| **[P1] Hook generator + cliffhanger library.** Hook must emit one of THE FACE / THE SHOCK LINE / THE REVEAL IN MOTION (ban establishing shots); generate 3–4 candidates, score, pick. Cliffhanger rotates 4 types (Reveal/Threat/Revelation/Decision) across consecutive episodes; genre-specific cuts. | invideo/minionarts hook+cliffhanger taxonomy. | S | The scroll is won or lost in 3 seconds; rotation prevents the sameness that kills a binge. |
| **[P1] Fuse-the-turn + dramatic-irony hook + meaningful-object motif.** Land peripeteia + anagnorisis on the *same* beat (Aristotle). Add a fourth hook engine (audience knows what the character doesn't) for sustained dread. Track one MOTIF object that recurs 3× with shifting meaning and *is* the mirrored Final Image. | Poetics; Hitchcock's bomb; NFI short-film craft. | M | The difference between a twist that surprises and one that devastates; the recurring object carries theme without dialogue. |
| **[P1] Trope-stack + season-architecture + paywall-placement libraries.** Each brief combines 2–3 tropes (billionaire+revenge+rebirth); 60–100 eps / 5 acts with a false victory at eps 30–35 + sequel hook; paywall at the *emotional beat* ("fist raised, not fallen"), not just ep 8–10. | ReelShort catalog; dramwa 卡点 craft; season templates. | M | Turns "make a revenge drama" into a market-proven, monetizable series a non-producer never planned. |
| **[P1] Worked recipe: `recipes/mundane_dread_short.py`.** Clone the *I'm Not a Robot* template (mundane premise → existential recognition, dark-comic, resolved twist, single location) as a complete prestige short. | 2025 Oscar Live Action winner; parallel to `recognition_ep1.py`. | M | Gives the machine a concrete recently-awarded structure to imitate, and proves prestige-mode + arc + fused-turn compose into a real short. |

---

## DIRECTOR — coverage + performance intent + model routing
*Current: folded into dramacode; no explicit director stage; single hard-wired model per shot.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Shot-type model router** — pick the best fal engine per shot: **Kling v3** (multi-shot dialogue), **Seedance 2.5** (long-take/consistency, native 30s), **Veo 3.1** (prompt-precise + 4K + native audio), **Runway Gen-4.5** (photoreal motion/physics hero shots), **Vidu Q3** (long dialogue), **Hailuo H3/2.3** (expressive character VFX). | fal aggregator (one key reaches all); named studios already run multi-model per shot. Env-overridable per stage (the hook already exists in `cinematic.py`). | M | No single model wins every shot type; routing captures each model's best-for. This is the core "AI cinematographer" brain and competitors on a single model can't copy it. **Also delete every Sora 2 path — API sunsets Sept 24 2026.** |
| **[P0] Cheap-draft-then-HD tier.** Blocking on Veo 3.1 Lite ($0.05/s 720p) or a fast model; HD-render only director-approved shots. | Verified Veo pricing (Lite $0.05 / Fast $0.10 / Std $0.40 per second). | S | Retries happen at ~1/8th cost; the draft→approve→finish gate is a real money lever on the biggest cost line. Pairs with the producer's plan-approve gate. |
| **[P1] Director stage as an explicit skill** between writing and camera: shot list, coverage decisions, performance intent per beat, camera-move choice. | `director` skill (planned); the shot list becomes the call-sheet source. | M | Separates creative intent from execution so "more handheld / colder / recast" re-runs one department, not the film. |
| **[P1] Camera-move preset layer** exposed as director shorthand (push-in, whip-pan reveal, bullet-time, earth-zoom). | Higgsfield-style presets / LTX-2 / Veo camera controls. | M | Big-budget camera moves are what read as "cinematic" in a 9:16 hook, and presets remove the prompt-lottery of describing moves in text. |

---

## CASTING / CAST — cast-book (consistency core)
*Current: `cast-book` writes reference PROMPTS only; `cinematic.py` generates ONE Flux-1.1 portrait per character and reuses it. The keyframe edit sees a single image. This is the weakest link in the whole chain.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Render the four-view turnaround sheet cast-book already prompts for** (4–6 images/character: ¼ head CU + front/side/back full body on white + expression), store URLs as `ref_images`. Stop shipping prompts-only. | Nano Banana Pro (`fal-ai/nano-banana-pro/edit`, $0.15) or Seedream to render the sheet from existing `ref_prompts.json`, one-time per character. | M | **The #1 lever in the entire roadmap.** Multiple angles is the biggest identity-stability gain available, and it's already spec'd — we just render the pixels. Directly attacks the 92%-drop discontinuity. |
| **[P0] Feed a reference STACK into every keyframe edit** — cast refs + locked location ref + first-appearance prop refs + a style/LUT frame, all inside the 14-image budget. | Nano Banana Pro (14 refs, 5 faces consistent, 2K/4K, legible in-frame text) as the keyframe default; FLUX.2 [pro] (10 refs) fallback. Flip the existing `VIDEO_CINEMATIC_KEYFRAME_MODEL` default off Seedream v4. | M | Solves multi-character scenes, set/prop/wardrobe/color continuity, and readable signage/phone-screens *in the same call the keyframe already makes* — no new stage. |
| **[P0] Retire the weak anchor.** Replace `fal-ai/flux-pro/v1.1-ultra` (2024 t2i) as the master-portrait + no-cast still generator. | Nano Banana Pro / FLUX.2 [pro] / Seedream 5.0 Pro (current top quality-quadrant). | S | Every downstream edit inherits the seed identity; a weak anchor caps the whole chain. |
| **[P1] Identity-through-motion for tight shots.** Route close/dialogue shots to reference-to-video (fed the turnaround sheet) instead of keyframe→i2v; keep Kling for wide/action. | Vidu Q2/Q3 reference-to-video (up to 7 refs holds identity across motion). | M | Keyframe→i2v still lets the face drift once the clip moves; reference-to-video holds identity through the motion itself, on exactly the shots where drift is most visible. |
| **[P1] Cameo / drop-in cast UX + provenance.** Lock a face+voice as a reusable cast member you drop into any shot; attach C2PA metadata to outputs. | Sora "cameos" / Midjourney omni-ref UX pattern, on top of cast-book. | M | Makes identity a first-class object the non-producer manipulates directly, and provenance is table-stakes for a platform selling output. |
| **[P2] OSS consistency fallbacks** when fal reference conditioning drifts on long sequences. | ComfyUI_VNCCS four-view sheets + fofr/cog-consistent-character to fill slots; StoryDiffusion Consistent Self-Attention (Apache-2.0) as a local node. | L | A non-paid lever on the hardest problem (long-range identity) that doesn't depend on a single vendor. |

---

## CINEMATOGRAPHER / VFX — keyframe → motion, retries, finishing
*Current: Seedream-v4 keyframe → Kling 2.5 i2v → Topaz upscale; a known 90° rotation bug in the post chain; 5–8s clip assumption.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Native long-take + multi-shot routing.** Stop assuming 5–8s clips: route "oners"/establishers to Seedance 2.5 (native 30s, joint audio+video, 30 refs, last-frame control); route multi-cut scenes to Kling v3 `multi_prompt` (up to 6 connected shots, per-shot prompt+duration, native lip-sync). | Seedance 2.5 + Kling v3 (verified). | M | One long gen replaces 4–6 stitched clips and their seam-matching retries; cuts *inside* one generation keep lighting/world/cast identical for free. Attacks the retry tax head-on. |
| **[P0] Regional RETAKE path** — "make her angrier / fix the camera" edits one region instead of re-rolling the whole shot. | LTX-2 retake / Kling Omni edit / Wan videoedit. | M | Attacks the 5–6-gens-per-shot multiplier directly; a surgical edit costs a fraction of a full reroll and preserves everything already approved. Also the core non-regenerative editing UX competitors are racing to ship. |
| **[P1] Last-frame chaining** for takes longer than any single model: extract final frame → start image of next i2v clip, with end-frame guidance to land the pose. | End-frame control on Kling v3 / Seedance 2.5 / Vidu start-end / Wan first-last-frame. Pure orchestration. | M | Arbitrarily long coherent takes with hard continuity, no new model work. |
| **[P1] Multi-engine "race" per hero shot** — render on N engines in parallel, auto-pick the best by the consistency/vision-audit score. | wind-comic race pattern; one fal key reaches all engines. | M | Turns per-engine flakiness into quality with no extra integration; hides model roulette from the producer. Reserve for hero shots (cost). |
| **[P1] Keyframe upscale before i2v.** Upscale the still to crisp 4K before animating (the clip can only be as sharp as its keyframe). | Clarity upscaler ($0.03/MP ≈ $0.06/frame) or Topaz image. | S | Cheap fidelity lift on the input every clip inherits. |
| **[P1] Fix the 90° rotation defect** in the post chain (lipsync/topaz inject a rotate flag stitch ignores). Probe rotation side_data, bake-upright before the ceiling render. | ffmpeg side_data probe; noted in nightshift-log. | S | A live correctness bug — every ceiling-rendered clip is currently sideways. Must clear before any finishing ships. |
| **[P2] 4K/HDR finishing route** for hero shots before the Topaz stage. | LTX-2.3 pro (4K@50fps) / Veo 3.1 4K / Luma Ray 3.2 (native HDR/EXR export into the color pipeline). | M | "Oscar-level" needs a real finish path; native 4K/HDR beats upscaling a 720p draft. |

---

## PRODUCTION DESIGNER — world + prop bible (planned)
*Current: nothing anchors sets or props; production-design is "planned".*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P1] AD breakdown, stage 0.** Tag each scene's elements by category (cast, extras, props, wardrobe, hair/makeup, locations, SFX/VFX, vehicles, set dressing) and emit the explicit list of anchors to lock — feeding cast-book and production-design before any render. | StudioBinder script breakdown; scriptbreak (Apache-2.0) data model. | M | Continuity is *decided* at breakdown — this is the missing front door that populates our consistency-anchor superpower systematically instead of ad hoc. |
| **[P1] World bible as reference IMAGES**, mirroring cast-book: a guarded SETS/PROPS block in `series.py` with `场景名_特征` / `道具名_状态` naming, each with a locked reference render reused across every shot in that location. | Same guarded-block + ref-URL pattern as cast-book; Seedance2-Storyboard asset scheme (C/S/P numbering). | L | Location change + key-prop first-appearance are new-shot triggers and props are consistency anchors like cast — currently unanchored. Locks set/wardrobe/prop continuity across a 60-ep season. |

---

## VOICE / ACTORS — voices.py (ElevenLabs)
*Current: eleven-v3 per-line with emotion tag + stability; voices limited to the default library; lines synthesized in isolation and concatenated.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Audio-driven PERFORMANCE for hero dialogue close-ups.** Generate the shot from the character ref image + the ElevenLabs line so brows/gaze/head-motion act *with* the voice — instead of Kling i2v then mouth-only repaint. Keep Kling+lipsync for wide/non-dialogue. | ByteDance OmniHuman ($0.14/s) or Hedra Character-3. | L | sync lip-sync only repaints the mouth on footage the actor never performed — dead eyes on the emotional beats. Audio-to-performance is where Oscar-level acting lives. Reserve for the ~20% hero close-ups. |
| **[P1] Text-to-Dialogue for two-handers.** Render a scene's back-and-forth as ONE call (multi-speaker, turn-taking, punctuation-driven interruptions/overlaps) instead of concatenating isolated lines. | ElevenLabs Text-to-Dialogue (eleven-v3, 2000 chars/req, seed for repeatability). | M | Concatenated solo lines never breathe together; co-modeling the exchange is the difference between a table-read and a performance. |
| **[P1] Cast bespoke voices with Voice Design v3** from each character's persona text in the bible; pin the voice id per character; fall back to the library if unstable. | ElevenLabs Voice Design v3. | M | The default library lets distinct characters collide; designed voices give each a signature and scale past the preset pool for series consistency. |
| **[P1] Non-verbal performance beats** injected from shot emotion: `[sharp inhale]`/held breath before the reveal, `[sighs]`, `[voice breaking]` on loss/tender beats, paired with the score dropping out. | eleven-v3 audio tags (mid-line allowed). | S | The audible half of the gut-punch the engine leaves silent — "a held breath carries the heartbreak." |
| **[P2] Listener animation** in two-handers so reaction cutaways to the non-speaking character show reactive micro-motion, not a frozen face. | sync React-1 (or a lipsync pass on a silence track). | M | Drama is reaction shots; a dead listener frame telegraphs "AI". |

---

## SOUND / FOLEY — sfx.py
*Current: mmaudio-v2 foley + a discrete-SFX endpoint that is now DEPRECATED on fal.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Retire the dead SFX endpoint.** `fal-ai/elevenlabs/sound-effects` is "no longer supported." Make mmaudio-v2 the default for motion/impact; route discrete stingers to a live source (ElevenLabs SFX via their own API); add a smoke check that fails loudly if the endpoint 404s. | mmaudio-v2 ($0.001/s, motion-matched) + live ElevenLabs SFX. | S | A production path that can silently die; SFX is the strongest *measured* completion lever (+172% on slap + glass-shatter). Correctness + quality in one fix. |
| **[P1] mmaudio everywhere there's an on-screen physical event** (set-down cup, closing door, footsteps in dialogue), layered under the discrete stinger. | mmaudio-v2 at $0.001/s. | S | Near-free; fills the silent connective-tissue shots where flatness currently reads as amateur. |
| **[P1] Spotting session after picture-lock** — walk the locked cut, emit a cue sheet: timestamped in/out for each SFX + intent tags. Foley/SFX never run before it. | Post "turnover"/spotting session; drives the sfx agent. | M | Foley on an unlocked cut is wasted; the cue sheet is the real editor↔sound contract. |

---

## COMPOSER — audio.py (score)
*Current: ElevenLabs Music with a sectioned composition_plan at FIXED fractions (0.15/0.60/0.88); Lyria fallback.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Beat-accurate score.** Drive the composition_plan section boundaries from the *actual* stitched shot offsets — especially the gut-punch shot's real start time — not fixed fractions. | ElevenLabs Music composition_plan; the engine already computes per-shot offsets, so this is wiring existing data. | M | The marquee craft move is dropping the music exactly at the heartbreak; a fractional guess misses by seconds. This lands the emotional peak on the frame. |
| **[P1] Music section-inpainting reroll.** When the climax/hook section is weak, regenerate ONLY that section, not the whole bed. | ElevenLabs Music inpainting / mid-track edit. | M | Cheaper, faster, preserves what works — the reroll loop the platform leans on constantly. |
| **[P1] Spotting session drives the composer** (paired with the sound spotting stage): the cue sheet is the composer's contract; scoring runs only after picture-lock. | Turnover rule. | S | De-risks the whole audio-ceiling work; no bespoke cue wasted on a shot that gets cut. |

---

## EDITOR — stitch + cut rhythm
*Current: one-shot stitching by shot id; hard cuts + Ken Burns; burned subtitles from TTS timestamps.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P1] Staged cuts.** Rough assembly (ordered accepted shots, no polish) → director-agent notes → picture-lock, instead of one-shot stitching. | Real assembly→director's cut→lock; feeds the hard-lock states. | M | Lets director notes land *before* any expensive finishing (color/audio/upscale) is spent. |
| **[P1] Caption + vertical-reframe module.** MediaPipe speaker-tracked 9:16 reframing + libass-burned animated captions in selectable styles. | autoclip (MIT) modules; note the ffmpeg libass/libx264 build gotcha. | M | Ships the exact vertical-short caption/reframe loop; captions in the right style are a big share of perceived polish. |
| **[P2] Adopt OpenTimelineIO as the internal timeline model + export.** | OpenTimelineIO (Academy Software Foundation). | M | Free round-trip to Premiere/Resolve/FCP — the escape hatch that makes real producers trust us because they can always finish in their own NLE. |

---

## COLORIST — grade stage (planned)
*Current: none. Gen models drift shot-to-shot in color/exposure — our own doc calls this the single biggest amateur tell after character drift.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] One-look grade stage after picture-lock.** Establish ONE LUT/reference look per episode (primary grade), shot-match every shot to it (secondary correction on outliers). | DI primary→secondary grade; a reference look frame per series. | M | The #2 amateur-tell fix after character drift. Cheap, and it's what turns a pile of clips into one film. |
| **[P1] Generation-time color bible.** Pass a fixed series palette (HEX) + JSON-structured prompt to every keyframe so color is enforced *at generation*, not only graded after. | FLUX.2 JSON prompts + HEX color control (also feeds the reference-stack style frame). | M | A cheap partial colorist that reduces how much the grade stage has to correct. |

---

## CONTINUITY / SCRIPT SUPERVISOR — the editor's data contract (planned) + QA
*Current: aspect/duration/hook/cliffhanger checks exist; no continuity log, no inline QC, no consistency gate.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Inline dailies QC + auto-regen.** Run per-shot QC (cast-match, artifacts/warp, exposure/color drift, focus) IMMEDIATELY after each render while the refs+scene context are warm; reason-coded rejects fire auto-regen inline — never batch QC to the end. | Dailies discipline; the reject reason-codes already spec'd in the Showrunner API. | M | Regen is cheapest while the context is loaded; this is the self-closing loop that lets the producer walk away. |
| **[P0] Automated consistency gate before i2v spend.** After keyframes render, run a face/identity + palette diff against the locked refs; auto-reroll shots past a drift threshold. | Embedding/identity + palette diff; make it the continuity skill's first job. | M | Highest-ROI quality gate we have — catches the 92%-drop discontinuity *before* spending i2v money on a broken keyframe. |
| **[P1] Circle takes.** Per shot, generate N draws, auto-QC scores them, director marks ONE print take + hold takes (logged); only the print take advances, holds kept as ranked fallbacks. | Real take-logging; matches our accept/regen economics. | S | Gives the editor instant alternatives on a recut with no re-render. |
| **[P1] Script-supervisor continuity log** as the editor's data contract: accepted-draw id, exact duration, on-screen action, dialogue on/off-camera, hold vs print. | Script supervisor lined-script/daily-log; wind-comic typed handoff contracts. | M | The editor consumes structured timing/continuity instead of re-deriving from pixels; unblocks a real editor + continuity gate. |

---

## CRITIC / SCREENING ROOM — the reviewer (in flight)
*Current: a screening-room critic role is being built (agent a7033e53a1dbaebbe) — finish it.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Adversarial continuity critic.** A fresh-context reviewer grades the finished cut against the brief on a film rubric; flags only correctness gaps (wrong face, broken eyeline, dead audio, drift, rotation) — not taste; routes department notes and drives targeted re-renders. | Claude Code "fresh-context reviewer" pattern; the in-flight screening-room role. | M | The film "code review" that closes a long unattended run — catches what the maker missed (and would have caught the rotation bug). Also our eval harness. |
| **[P1] Episode-level test-screening retention gate** before delivery: score the first-3s hook and the end cliffhanger against retention heuristics; can trigger a re-cut or A/B alternate opening. | Producer test-screening authority; retention heuristics. | M | Retention is the whole game in short drama — bake the alt-ending authority into a hard gate rather than hoping the writer nailed it. |

---

## PRODUCT & ADOPTION LAYER — the "Claude Code" of it
*This is what turns a good renderer into a tool non-producers adopt and trust. Several items double as producer-crew mechanisms above; listed here as product surfaces.*

| Build | Use | Effort | Impact |
|---|---|---|---|
| **[P0] Evidence-not-status board.** A live board of every intermediate artifact, which model made each shot, and cost per shot — never an opaque "making your film" spinner. | Claude Code "show evidence" principle; wind-comic per-shot cost attribution. | M | Transparency is a core trust driver; producers adopt tools whose work they can inspect. Pairs with plan-approve. |
| **[P0] series.py / the bible = our CLAUDE.md.** Short, human-editable, git-tracked, always loaded on every render; only cast/style/voice/canon that would cause mistakes if removed. | Claude Code memory pattern; series-level persistent memory. | S | Series-level (not clip-level) consistency memory is our defensible moat — no competitor holds a locked cast+world across 60 episodes. |
| **[P1] Autonomous TV as an MCP server + CLI.** Tools: `write_episode`, `render_shot`, `reroll`, `breakdown`, `get_board`, with per-engine "expert skills" (curated params) and an ask-before-paid-spend guard. | comfyui-mcp pattern; Runway/Higgsfield/Pika all ship MCP now. | M | Lets producers drive the crew from Claude Code/Cursor/their own agents — a distribution channel and the extensibility driver, on-brand for the agent fleet. |
| **[P1] User-assignable / customizable crew agents** with per-role prompts (cinematographer, sound designer, colorist) the non-producer can add, rename, tune; + one-click "Featured Apps" verbs (Relight=colorist, Add Dialogue=voice, Remove=VFX). | Invideo Agent Two crew-agents; Runway Featured Apps. | M | Directly matches the closest competitor and validates the crew thesis; the verbs are low-friction entry points and upsell surfaces. |
| **[P1] Distribution as a first-class output:** auto-generate the ad cut + title + thumbnail from the same timeline (cut the highest-conflict scene walled before resolution; title = trope+twist noun phrase; thumbnail = the Face/reveal frame). | Short-drama ad-cut craft (+126% installs, +4.3× ROAS vs generic). | M | Distribution is the other half of the business, and producing the marketing asset from the same timeline is near-free for us. |
| **[P1] Template / remix market.** Save a finished project as a template; rate/favorite; one-click remix INCLUDING its per-character voices. | wind-comic template market; the Panda remix loop. | M | The network/remix loop and the cold-start killer for non-producers who want a proven format. |
| **[P1] Fan-out / non-interactive season rendering.** Batch-render N episodes headless, parallelize shots, run the multi-engine race concurrently, resumable named projects. | Claude Code `-p` loops / parallel sessions. | M | The scale story a studio actually buys; resumable projects mirror sessions-as-branches. |
| **[P2] Retention/paywall analytics closed loop.** Instrument episodes (per-second view-through, 6s-completion, paywall conversion, day-2 retention), feed back to auto-reroll weak hooks/cliffhangers. | Reelytics-style analytics; our orchestration closes the loop. | L | Measure→reroll-the-weak-beat is the defensible advantage only our orchestration enables. |
| **[P2] Open-source a flagship short with all prompts + cast configs + crew settings.** | Higgsfield's open "Hell Grind"/"Zephyr" playbook. | M | Credibility + reusable templates + onboarding scaffold; on-brand for Autonomous. |
| **[P2] Local/offline tier + BYO keys extensibility.** IndexTTS-1.5 MLX local voice, NarratoAI narration chain, BYO LLM+engine keys, post-render hooks, plugin surface. | IndexTTS/NarratoAI (MIT); Claude Code bendability. | L | Converts a tool into a studio's platform; the private/no-API-cost path some users require. |

---

## Sequencing logic (why this order)

- **Wave 1 (P0) is the "wow" wave.** It fixes the two biggest amateur tells (character drift, color drift), gives the director a real model brain, adds the story spine and the tear-trigger, makes faces act, and closes the quality loop so a non-producer can trust the result and spend money through it. Most of it is *finishing half-built work* (cast sheets are spec'd, the model hook exists, the critic is in flight) — highest impact per unit effort.
- **Wave 2 (P1)** is depth: production design anchors, the full audio finishing chain, staged editing, distribution, MCP, and the crew-customization surface competitors are shipping.
- **Wave 3 (P2)** is the platform moat: OTIO interop, analytics closed loop, open-source flagship, local tier, extensibility.

Two hygiene items that gate the finishing stages and should ride along in Wave 1 regardless of priority ranking: **fix the 90° rotation defect** (every ceiling clip is currently sideways) and **verify + pin exact fal $/s** for every routed model at wire-up (catalog pages are challenge-walled; pricing unconfirmed this session) plus **cache/dedupe fixed assets** (a documented ~60% saving before any model choice).
