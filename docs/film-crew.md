# The Film Crew — Autonomous TV's production model

A real film's credits run to hundreds of names across dozens of departments. A
"non-producer" typing one sentence can't hire any of them. The platform's job is
to **staff the whole crew with role-specialized agents** and run them like a
production: each owns one craft, does it to a professional bar, and hands off to
the next. This is the org thesis (specialist agents, one human) applied to film.

Every role below maps to a **skill** (craft the model applies) or an **engine
stage** (an executable step in `packages/dramapy`), coordinated by the
**producer/director orchestration** (the chat driver + dramacode loop). "Built"
= shipping now; "planned" = designed, next.

## The crew, by department

### Development & Writing
| Real role | Platform | Owns | Status |
|---|---|---|---|
| Screenwriter | `story-analysis` + `dramacode` | premise → series bible → beat sheet → shot-level script; the emotional core | built (craft upgrade in flight) |
| Story editor / script doctor | dramacode review loop | dramatic-function pass: hook, escalation, cliffhanger, the recognition beat | built |
| Script supervisor (continuity) | `continuity` skill + checks | character/prop/wardrobe/timeline continuity across shots & episodes | **planned** |

### Direction
| Real role | Platform | Owns | Status |
|---|---|---|---|
| Director | `director` skill | the vision: tone, performance intent, blocking, shot list, which beats get which coverage | **planned** (today folded into dramacode) |
| 1st AD | producer orchestration | shot list → schedule → render plan (concurrency, budget) | built (implicit) |
| Casting director | `cast-book` | character design, look bibles, voice casting (pinned per character) | built |

### Camera & Lighting
| Real role | Platform | Owns | Status |
|---|---|---|---|
| Cinematographer (DP) | `cinematography.py` + cinematic provider | shot size, lens, camera move, composition per shot | built |
| Gaffer / lighting | cinematography (lighting vocab) | lighting design keyed to mood/genre | built (deepen) |
| VFX supervisor | cinematic provider (keyframe→i2v) + `postprocess` | the generated imagery, character consistency, upscale | built (lip-sync/upscale in flight) |

### Design
| Real role | Platform | Owns | Status |
|---|---|---|---|
| Production designer / art director | `production-design` skill | the world bible: palette, architecture, sets, era, texture — consistent across the series | **planned** |
| Set decorator / props | production-design | recurring props/locations as consistency anchors (like cast) | **planned** |
| Costume / hair / makeup | `cast-book` (look bibles) | wardrobe + look, locked into character refs | built (deepen) |

### Performance & Sound
| Real role | Platform | Owns | Status |
|---|---|---|---|
| Actors / voice | `voices.py` (ElevenLabs) | per-character performed voice, emotion, ADR | built |
| Lip-sync | `postprocess` (sync-lipsync) | mouths match the performance | in flight |
| Sound designer / foley | `sfx.py` | diegetic SFX, ambience, impacts | in flight |
| Re-recording mixer | `stitch` mix | dialogue clarity, ducking, SFX punch, the final balance | in flight |

### Music
| Real role | Platform | Owns | Status |
|---|---|---|---|
| Composer | `audio.py` (Lyria) | the score: theme, mood, build to climax, heartbreak motif | in flight |
| Music supervisor | audio (cue planning) | which cue plays where, dynamics against the cut | in flight |

### Post
| Real role | Platform | Owns | Status |
|---|---|---|---|
| Editor | `editor` craft (in dramacode + stitch) | cut rhythm, shot order, transitions, pacing for retention | built (formalize as `editor`) |
| Colorist | `color` grade stage | the grade: consistent filmic color across shots (fixes shot-to-shot color drift) | **planned** |
| VFX / finishing / upscale | `postprocess` (topaz) | resolution, sharpness, final polish | in flight |
| Subtitles / localization | `subtitles.py` + InfiniteTalk dub | captions; dub to new languages with voice continuity | built (dub planned) |

### Production management
| Real role | Platform | Owns | Status |
|---|---|---|---|
| Producer / line producer | chat driver + generation pipeline | orchestrates the crew, budget (provider tier), schedule (render concurrency), delivery | built |
| Post supervisor | review loop | QA gates between stages, re-do until it clears | built |

## How the crew runs (production pipeline)

```
PRODUCER (orchestration)
  → SCREENWRITER (story-analysis, dramacode)      : bible + beat sheet + emotional core
  → DIRECTOR (director)                            : shot list, coverage, performance intent
  → CASTING (cast-book) + PRODUCTION DESIGN (production-design)
                                                    : locked character & world reference bibles
  → per shot, in parallel:
      CINEMATOGRAPHER (cinematography) → keyframe (consistent cast + world)
      VFX (cinematic provider) → image-to-video
      LIP-SYNC (postprocess) on dialogue
      COLORIST (color) → consistent grade
      UPSCALE (postprocess) → finish
  → ACTORS (voices) : performed dialogue     COMPOSER (audio) : score     SOUND (sfx) : effects
  → EDITOR (stitch) : cut, rhythm, transitions
  → RE-RECORDING MIX (stitch) : voice + ducked score + SFX
  → CONTINUITY + POST SUP (checks + review loop) : QA, re-do failures
  → DELIVER : 9:16 master + captions
```

## Design rules for adding a role

1. **A role is a skill when it's a decision** (director's coverage, designer's
   palette, editor's rhythm, colorist's grade intent) and an **engine stage when
   it's an execution** (render, voice, mix, upscale). Most departments are both:
   a skill that decides + a stage that executes.
2. **Consistency anchors are the platform's superpower** — cast (built) and, next,
   world/props (production-design) and color (colorist). Everything a real crew
   keeps continuous, we lock into a reference the way we lock a character.
3. **Every role has a QA gate.** Continuity/script-supervisor + the review loop
   catch what any department got wrong and send it back — the post supervisor.
4. **Non-producer default:** the user supplies taste and intent; the crew is
   staffed automatically at a professional bar. The user can direct any
   department ("make it colder," "more handheld," "recast the lead") and only
   that department re-runs.

## Build order (next)

1. `director` — shot list + coverage + performance intent as an explicit stage between writing and camera.
2. `production-design` — the world bible (palette, sets, props) as consistency anchors, mirroring cast-book.
3. `color` — a grading stage for shot-to-shot color consistency (the single biggest "looks amateur" tell after character drift).
4. `continuity` — the script supervisor: cross-shot/episode consistency QA.
5. Formalize `editor` (cut/rhythm/transitions) and `sound`/`composer` as named departments over the built stages.
