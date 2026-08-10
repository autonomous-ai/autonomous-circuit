# Short-drama construction research (2026-08-09)

Source document for the Autonomous TV skills (story-analysis, dramacode references, dramalib
tables). Compiled from Chinese practitioner guides (红果 official screenwriting tutorials,
53AI, 腾讯云, SegmentFault, cnblogs, 新浪 worked cases) and overseas sources (ReelShort,
Filmustage, DataEye). Every number sourced in the research archive; uncertainties labeled.

## Series structure

- Universal format: vertical 9:16, 1080×1920, H.264 MP4. FPS: 25 (漫剧 norm) or 30
  (Douyin norm); never upsample. Bitrate 8–12 Mbps @1080p.
- Episode counts: China paid mini-program 80–100 eps; 漫剧 60 eps × 2–3 min;
  ReelShort-class 50–100 eps × 1–3 min. AI短剧 episodes: 45–90s sweet spot; Douyin caps
  live-action at 3:00 (AI/漫剧 exempt), recommends 1:30.
- Paywall gates (卡点): China first gate ~ep 10 (hooks loaded into eps 9–10), then 20, 30.
  Overseas: first gate ep 5–12 (varies by app — make configurable), major gate eps 26–30.
  红果/free model: no gates — every episode must tolerate ~15s ad breaks instead.
- Series pacing (红果 official): eps 1–10 golden window (protagonist + core conflict +
  reason to watch); 11–30 main development; 31–80 escalating climaxes + payoffs.
  Minor reversal every 5–10 eps; major every 20–30; 2–3 core reversals per series.

## Episode beat law (the formula every hit follows)

- **0–3s: the hook** (3 types: direct confrontation 正面冲突, mystery 悬念, extreme
  contrast 极致反差). 80% of viewers decide within 6s.
- **0–10s:** core conflict + character relations + protagonist's goal all established.
  Exposition never exceeds 10s.
- **0–30s:** first emotional reversal/爽点 (suppression→eruption).
- **Every 20–30s:** an emotional beat (3–4 beats/min target).
- **Final 5–10s:** cliffhanger (suspense / crisis / reversal hook) — cut at the emotional
  peak, dead stop.
- 2-min episode template: 0:00–0:10 recap-as-flashback (optional on binge platforms) →
  0:10–0:30 new scene → 0:30–1:20 core event → 1:20–1:50 reversal/climax → 1:50–2:00 hook.
- Dialogue register: short, fragmented, sharp (短碎锋利); single line ≤15 Chinese chars;
  200–300 chars dialogue per 1.5–2 min episode; two-handers preferred, no monologues.
  "Kneeling beats crying, a slap beats a speech."

## Shot grammar

- Shots per episode: 8–15 for a 60–90s episode (3–8s each); 漫剧 runs 20–30 shots/min at
  2–4s. Hard cap ≤10s per generated shot. Scenes per episode: 1–3 (few locations — asset
  reuse doctrine).
- Duration by shot type: establishing/wide 3–4s (≤5s) · action 2–3s · emotional
  expression 2–3s · dialogue 2–3s per sentence · prop/detail close-up 1–2s ·
  emotional-peak freeze 3s.
- Shot scales restricted to five: 大特写/特写/近景/中景/全景 (ECU/CU/MCU/MS/WS).
- New-shot triggers: location change, time jump, cast change, key prop first appearance,
  emotional turn, action start/end, speaker switch, needed scale change. Do NOT split:
  continuous action, sustained emotion, ongoing dialogue.
- 分镜表 (shot list) is the production source of truth. Canonical field set (14-field
  industrial version): 镜号, 时间轴, 时长, 场景, 人物, 道具, 剧本原文, 画面提示词(AI prompt
  as a first-class column), 景别, 镜头运动, 人物动作, 对白, BGM/音效, 备注.
- I2V prompt formula: [subject] + [action/emotion] + [camera] + [light/atmosphere] +
  [style]; video prompts need explicit action chains ("then… immediately… next…").
- Candidates: generate 2–3 per shot, pick best; hero shots expect 5+ retries.
  Cheapest QC gate: verify storyboard STILLS against script BEFORE any video spend.

## Assembly conventions

- Transitions: **80% hard cuts, 20% effects**; static shots get Ken Burns 100%→115% over
  3–4s.
- Subtitles: burned in, always. Position lower 1/5 of frame (never absolute bottom —
  platform UI). Source Han Sans, white fill + 2px dark stroke, ≤14 chars/line. Generated
  from TTS timestamps → zero drift.
- Audio: 4 layers — dialogue / ambient / emotional SFX (heartbeat, slaps, glass) / BGM.
  Dialogue +10dB over bed; BGM −5dB, ducks under dialogue. TTS speed: conflict 1.1–1.15×,
  tender 0.85–0.9×. Slap + glass-shatter SFX lift completion (+172%, single source).
- Lip-sync ordering: **audio FIRST for realistic dialogue shots** (TTS line → fed into
  generation for native lip-sync); 漫剧/action tolerates video-first + dub-after.
  The tool needs both paths.
- Head/tail: amplify the 3s hook at the head; "next episode" teaser card at tail; cover
  frame required.
- Final QC: lip alignment, ducking, SFX on peaks, duration, codec — then review on an
  actual phone.

## Asset conventions

- Naming: characters `人名_特征` (林野_6岁), scenes `场景名_特征` (舅舅家客厅_白天), props
  `道具名_状态` (酒杯_破碎). Clips by 镜号; audio batch-named by shot_id.
- Character library: 4–6 ref images per character (four-view sheet: 1/4 head close-up +
  front/side/back full body, white bg) + 1–2 expression refs + key prop. Main cast 2–3
  leads.
- Consistency is the #1 dropout driver (92% of viewers drop on visual discontinuity —
  single source, directional).

## Genre formulas (trope tables for dramalib)

- 男频 male-lead: 赘婿 (despised son-in-law hidden power), 战神 (war god returns),
  神豪/龙王/神医. Beat: humiliation → concealed identity → forced reveal → 打脸 cascade.
- 女频 female-lead: 霸总+灰姑娘 (CEO+Cinderella 双洁HE), 重生 (rebirth w/ memory),
  复仇 (bullied→awaken→face-slap), 真假千金, 穿越.
- Overseas mapping: werewolf/vampire romance (NA), billionaire/secret-heiress (SEA),
  revenge universal. ReelShort adapts validated web-novel IP, rarely originals.
- The three paywall-hook drivers: climax, reversal, borderline-risqué (compliance-gated).

## Pipeline order (consensus, QC gates marked)

题材立项 → 剧本 (500–800 chars/ep) → 分镜表 → 角色/场景资产 [QC: consistency audit] →
分镜静帧 t2i [QC: stills vs script — cheapest gate] → i2v 生成 (batch, 2–3 candidates,
抽卡 here) → 配音/对口型 (audio-first for dialogue) → 剪辑组装 (by 镜号) → 字幕 →
音效+BGM → 片头尾/封面 → 画质增强+导出 [QC: on-device review] → 发行.

Benchmarks: skilled team ~3h/episode (script 20m / storyboard 30m / assets 1h / video 1h /
edit 40m); 4-person team 5–10 eps/day; cost ~¥1,000/finished-min (Dec 2025), floor
¥60–120/min on tuned pipelines.

## Worked storyboard example (translated, 仙侠漫剧 opening)

| Shot | Dur | Scale/move | Picture | Dialogue | Audio/style |
|---|---|---|---|---|---|
| 1 | 3s | Extreme wide, slow push-in | Starfield; the Immortal-Execution Platform floats above a cloud sea, ringed by celestial soldiers | — | Low strings; cold palette |
| 2 | 2s | Full shot, tilt down | White-robed youth on the altar, gods encircling | Gods (V.O.): "Insolent mortal — you dared slaughter sixteen sanctuaries!" | Conflict statement via V.O. |
| 3 | 2s | Close-up, slight turn | Sharp profile, cold smirk | Inner monologue: "You sanctimonious frauds…" | Side backlight |

The 3s爆点 → 10s立角色 rule executed literally: 3s hook → 2s conflict → 2s attitude.
