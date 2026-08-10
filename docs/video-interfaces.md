# Video interface contracts

Document version: **v0.1-draft** (2026-08-09). This document mirrors `docs/panda-interfaces.md`
(the donor contract) for **Autonomous TV** — chat → screenplay → shots → stitched vertical episodes.
Once the build tracks start, this document is **frozen**: changes go through
`docs/video-interfaces-CHANGES.md` (append-only: Change / Why / Backward compatible /
Mechanism / Tracks affected).

Who consumes what (the donor repo's sharpest lesson, restated on day one):

- The **stdout JSON line** from a skill script is consumed by the `claude` CLI as the
  tool-call result — it is what the **model** sees. The server never parses it.
  Fields here are snake_case and may be renamed cheaply.
- The **`.episode.json` sidecar** is the **server driver's** machine contract — the review
  loop walks `*.episode.json` files and gates on `validation.warnings[].severity`.
  Fields here are camelCase and renaming them breaks the review loop.

Three sections, three build tracks: §1 dramapy (Python pipeline), §2 server driver + client
transport (Node/React), §3 skills.

---

## §1 `gen_episode()` contract (packages/dramapy)

### Project = one drama series; episode = one generation target

```
<project>/                        ← the workspace (one drama series)
├── series.py                     required once — the series bible (module-level constants)
├── episodes/
│   ├── ep001.py                  one generation target per episode; defines gen_episode()
│   ├── ep001.mp4                 ┐
│   ├── ep001.episode.json        │ artifacts land next to the source
│   ├── ep001.srt                 │
│   ├── ep001_shots/              │   shot_001.mp4, shot_001.json, …
│   └── ep001_review/             ┘   _poster.png, _board.png
├── inputs/                       chat reference images (excluded from catalog)
└── .video/render-cache/          content-addressed shot clips (excluded from catalog)
```

The generator (`skills/dramacode/scripts/drama`) accepts, like the donor's polymorphic input:
a **directory** containing `main.py`, a **single `.py` file** (the normal case:
`episodes/ep001.py`), and `--out-dir` / `--stem` overrides. The project root (the dir
containing `series.py`) is put on `sys.path`, so episode files do `import series`.

### `series.py` — the series bible

Module-level constants, plain Python (dataclasses provided by dramalib):

```python
from dramalib.bible import Series, Character

SERIES = Series(
    title="Contract Bride of the Chaebol Heir",
    genre="revenge-romance",            # keys dramalib.tropes tables
    style="photoreal-drama",            # or "manhwa", "anime" — provider style preset
    aspect="9:16", resolution=(1080, 1920), fps=24,
    language="en",
)
CAST = [
    Character(id="li_wei",  name="Li Wei",  look="woman, 28, sharp bob, gray suit …",
              voice="f_low_calm", ref_images=[]),   # ref_images filled by cast-book skill
    Character(id="dorian",  name="Dorian Cross", look="man, 34, black coat …",
              voice="m_deep_cold", ref_images=[]),
]
```

### `gen_episode()` — accepted return value

`episodes/epNNN.py` defines `gen_episode()` at module scope returning an **envelope dict**
(the only accepted shape — no legacy forms; unknown keys raise `TypeError`):

```python
def gen_episode():
    return {
        "episode": Episode(...),        # dramalib.spec.Episode — see below
        "warnings": [                   # optional, project-declared
            {"part": "scene_2", "kind": "functional",
             "detail": "recap beat exceeds 8s", "severity": "warning"},
        ],
    }
```

`Episode` (dramalib.spec — duck-typed on attributes, not isinstance, mirroring the donor's
`.wrapped` rule so a plain dict with the same keys also works):

```python
Episode(
  number=1, title="The Slap",
  hook_max_s=5.0,                      # cold-open rule; verifier reads this
  scenes=[
    Scene(id="s1", location="penthouse lobby, night, rain",
      shots=[
        Shot(id="s1_01", kind="establish", duration_s=5,
             prompt="rain-slick lobby doors, neon reflections, she enters"),
        Shot(id="s1_02", kind="dialogue", duration_s=8, cast=["li_wei"],
             line="You never told me he was alive.", emotion="dread",
             prompt="close-up, trembling, letter in hand"),
        Shot(id="s1_03", kind="action", duration_s=5, cast=["dorian"],
             prompt="he turns from the window, slow, backlit"),
      ]),
  ],
  cliffhanger="freeze on the burning letter",   # last shot must exist; verifier checks
  bgm="tense-strings",                          # dramalib.audio mood key or None
  burn_subtitles=True,
)
```

Shot rules (enforced by validators, not convention): `duration_s ∈ [3, 15]`;
`kind ∈ {establish, action, dialogue, insert}`; `dialogue` requires `cast` and `line`;
every `cast` id must exist in `series.CAST`.

### Public entry point

```python
def generate_episode(
    source_path: Path | str,            # episodes/epNNN.py or a dir with main.py
    output_path: Path | str,            # …/epNNN.mp4  (suffix must be .mp4)
    *,
    provider: str | None = None,        # default: env VIDEO_PROVIDER, default "mock"
    max_render_s: float | None = None,  # per-shot render budget
) -> dict[str, object]
```

### Artifacts written per call (`<stem>` = output basename)

| File | Always? | Purpose |
|---|---|---|
| `<stem>.mp4` | yes | stitched episode — H.264/AAC, `+faststart`, exact series resolution/fps |
| `<stem>.episode.json` | yes | sidecar: source hash, generator metadata, validation summary |
| `<stem>.srt` | when any dialogue | subtitles; also burned in when `burn_subtitles` |
| `<stem>_shots/shot_<id>.mp4` | yes | one clip per shot, post lip-sync, pre-stitch |
| `<stem>_shots/shot_<id>.json` | yes | per-shot sidecar: prompt, cast, provider, renderMs, draws |
| `<stem>_review/_poster.png` | yes | cover frame — publish path resolves it **by this filename** |
| `<stem>_review/_board.png` | yes | contact sheet (first frame of every shot + timings) — the review loop reads this |

Ordering rule (mirrors the donor's mtime discipline): the sidecar is written **before**
the final `.mp4`, so `artifact_changed` for the episode fires after its metadata is readable.

### `.episode.json` sidecar schema (camelCase, canonical JSON: `sort_keys`, `(",",":")`)

```jsonc
{
  "generator": "dramapy",
  "entryKind": "episode",
  "source":     { "kind": "python", "path": "episodes/ep001.py", "hash": "…", "fingerprint": "…" },
  "episode":    { "path": "ep001.mp4", "number": 1, "title": "The Slap",
                  "durationS": 96.4, "fps": 24, "resolution": [1080, 1920] },
  "provider":   { "name": "mock" | "fal", "model": "wan-2.2" },
  "validation": { "durationS": 96.4, "shotCount": 14,
                  "warnings": [ { "part": "s1_02", "kind": "duration_drift",
                                  "detail": "...", "severity": "warning" } ] },  // omitted when empty
  "shots":      [ { "id": "s1_01", "path": "ep001_shots/shot_s1_01.mp4",
                    "jsonPath": "ep001_shots/shot_s1_01.json" } ]
}
```

`source.fingerprint` folds `episodes/epNNN.py` + `series.py` + local imports
(donor `source_hash` algorithm verbatim) — editing the bible invalidates every episode.

### `validation.warnings` — open `kind` set, closed `severity` set

Severity routing is the driver's ONLY gate: `"error"` blocks, `"warning"` reviews,
`"info"` advises. The driver never switches on `kind`. Initial kinds:

| kind | severity | emitted when |
|---|---|---|
| `missing_shot` | error | a declared shot produced no clip |
| `render_failed` | error | provider returned failure after retries |
| `aspect_mismatch` | error | a clip's aspect ≠ series aspect |
| `stitch_failed` | error | ffmpeg concat/mux failed |
| `duration_drift` | warning | clip duration off spec by >15% or episode off by >10% |
| `silent_dialogue` | warning | dialogue shot has no audio track post lip-sync |
| `hook_too_long` | warning | first non-establish beat lands after `hook_max_s` |
| `no_cliffhanger` | warning | `cliffhanger` unset or final shot missing |
| `subtitle_overrun` | info | a subtitle line exceeds 42 chars × 2 lines |
| `check_failed` | warning | a verifier itself raised (never fatal) |
| `functional` | warning | project-declared via envelope `warnings` |

### Error contract

All errors from `generate_episode()` subclass `dramapy.generation.GenerationError`:

```
GenerationError
├── ProjectShapeError        # missing gen_episode / bad envelope / bad output suffix
│                            #   (also: AssertionError from validate() re-raised with message)
├── GeneratorRuntimeError    # gen_episode() raised, or module failed to load
├── SpecValidationError      # Episode/Scene/Shot structure invalid
├── ProviderError            # render backend failed (network, quota, content policy)
└── ExportError              # stitch/mux/subtitle/poster writing failed
```

`SyntaxError` and `ImportError` propagate untouched (the runner maps them by type).

### Provider abstraction (dramapy.providers)

`Provider.render_shot(shot_ctx) -> Path` — implementations: **mock** (ffmpeg-synthesized
placeholder: style-colored gradient bg, burned shot id + prompt excerpt, correct
duration/resolution/fps, sine-beep audio bed on dialogue) and **fal** (Wan 2.2 t2v/i2v via
`FAL_KEY`, polling, per-shot timeout). Selection: arg > env `VIDEO_PROVIDER` > `"mock"`.
Render cache: shots are content-addressed (`sha256` of shot spec + style + provider + cast
fingerprints) into `.video/render-cache/`; a re-stitch after an edit re-renders only
changed shots. Regression tests use the mock provider only — CI never hits a network.

---

## §2 Server driver + client transport

The donor's Rust/Tauri layer is replaced by one Node server (`viewer/src/server/video/`)
mounted as Vite middleware in dev and standalone `server.mjs` in prod. The client keeps the
donor's live types verbatim (transport.ts:156-165 union — the CODE, not the stale doc).

### Commands — `POST /api/<command>` (JSON body, JSON reply, `IpcError {code,message,detail?}` on 4xx/5xx)

Chat: `chat_start_turn {req: StartTurnRequest} → {turnId}` · `chat_approve_plan` ·
`chat_request_plan_changes` · `chat_cancel_turn {turnId}` · `chat_session_state {projectId}
→ ChatSessionState` (with `blocks[]` — reconstructed from the Claude Code session JSONL).
Projects: `project_list | project_create | project_open | project_rename | project_delete`
(shapes verbatim from transport.ts). Catalog: `catalog_read`, `project_catalog_read`.
App: `app_info`, `app_prereq_check` (checks: claude on PATH, ffmpeg, python3.10+),
`app_settings_read`, `app_settings_write`, `app_set_model`.
Deleted families: slicer, printer, cloud, step, social, update, snapshots (post-v1).

### Events — `GET /api/events?projectId=…` (SSE)

One `EventSource` per client; every event is `{ …ChatEvent, projectId }` — the 9-kind union
verbatim, plus `catalog_changed {revision}`. The client change is confined to
`listenEvent()`/`events.subscribe()` in transport.ts (~30 lines).

### Driver behavior (ports `claude_driver.rs` semantics to Node)

- Spawn: `claude -p --output-format stream-json --input-format stream-json --verbose
  --include-partial-messages --permission-mode <phase> --add-dir <workspace>
  --add-dir ~/.claude/skills --append-system-prompt <phase prompt> --strict-mcp-config
  --settings '{"disableAllHooks":true}' (--resume|--session-id) <uuid5(projectId)>
  --model <settings.model>`.
- Two-phase turn: **plan** (`--permission-mode plan`; intercept `ExitPlanMode` →
  `plan_proposed`, kill child) → **build** (`chat_approve_plan`, `--permission-mode
  bypassPermissions`, same session). Autopilot: `autoBuild !== false` chains build
  server-side; guard is plan-present, not plan-non-empty.
- Intercept `AskUserQuestion` → emit `text_delta` with a ```video-questions fenced JSON
  block (donor mechanism, renamed fence) and end the turn.
- `toolUseId` stable + paired on tool events; `error` then `turn_end` on failure;
  `error{message:"cancelled"}` on cancel.
- Artifact snapshotter: recursive mtime diff over `.mp4 .png .json .srt .py .wav .mp3`
  (≥1s forward move or new file) → `artifact_changed`. `inputs/`, `.video/`, `.claude/`,
  `node_modules`, `__pycache__` excluded — same skip-list discipline as the donor catalog.
- **Review loop** after every build turn, silent, best-effort, caps mirrored from donor:
  Phase 1 *structure* (≤2 rounds): walk `*.episode.json`, blocking = severity `error`;
  prompt lists `- [part] kind: detail` lines, resume session, re-check.
  Phase 2 *dramatic function* (≤3 rounds): warnings with `kind == "functional"` —
  prompt cites the beat-sheet rules from the dramacode skill references.
  Phase 3 *craft* (≤2 rounds, ALWAYS runs once): re-render `_board.png` + `_poster.png`,
  Read them, check continuity/composition/caption legibility; break when a round changes
  no files.
- Sessions: deterministic `uuidv5(projectId, VIDEO_NS)`; history from
  `~/.claude/projects/<encode_cwd(workspace)>/<session>.jsonl`; `encode_cwd` replaces
  **every** non-alphanumeric char with `-` (donor footgun, preserved).
- Projects root: `~/.autonomous-video/projects/<uuid4>/` with `project.json`
  `{id, name, created_at, updated_at}` (snake_case, pretty) + placeholder-name self-heal
  from the session JSONL `ai-title` line.

### Catalog rules (Node scanner, donor conventions)

Kinds: `mp4 | png | srt | py | json`. Visibility: `.json` hidden (surfaced via the episode
entry's `artifact.metadataUrl`); `.png` hidden unless parent dir ends `_review`; shot
`.mp4` hidden if parent dir ends `_shots` (grouped under the episode entry's
`artifact.shots[]`); `.py` hidden if name starts `_`. Episode entries carry
`artifact: {srtUrl?, metadataUrl?, posterUrl?, shots?: [{id, file, url}]}`.
**Every media URL carries `?v=<mtime_nanos>-<size>`** — `<video>` caches harder than STL.

### Client surgery (tracked, minimal)

Keep: `store/chat.js` (minus `pendingTokens`, `pendingViewContext` → repurposed as
timecode context, `selectedMeshFile`, `lastSlice`), `components/chat/*`, `components/ui/*`,
projects/claudeSetup stores, globals.css/prose.css, chatLayout.js.
Replace: `CadWorkspace.js` → `EpisodeWorkspace.jsx` (same 6 props from main.jsx);
`CadRenderPane.js` body → vertical `<video>` player + episode rail + storyboard strip
(shot thumbs from `_shots/` sidecars with status) + cast panel.
Reword: `activityLabels.js` (`dramacode → "Writing episode"`, `scripts/drama → "Rendering
shots"`), the ~8 CAD copy strings, fence `panda-questions` → `video-questions`.
"Send to AI": current-video-frame grab + timecode note (donor's highlightContext pattern).

---

## §3 Skill stdout + artifact contract

Skills (product): `story-analysis`, `dramacode`, `cast-book`, `episode-viewer`.
Anatomy per skill mirrors the donor exactly: 2-field frontmatter (`name`, `description`
— trigger-dense), self-contained runtime, vendored dramapy under
`skills/dramacode/scripts/packages/dramapy/` (rsync'd by
`scripts/build/build-skill-runtimes.sh`, README + .gitignore protected), references/ with
named load triggers, `agents/claude-code.md` host overrides, tests/ with a dramapy stub
injected via `DRAMACODE_TEST_DRAMAPY_PATH`.

### `dramacode` generator invocation

```
python skills/dramacode/scripts/drama <episodes/epNNN.py | project_dir>
       [--out-dir DIR] [--stem NAME] [--provider mock|fal] [--wall-clock-s S]
```

Sandbox: RLIMIT_AS 1 GiB, RLIMIT_CPU scaled, RLIMIT_NOFILE 64; import allow-list
(dramapy + stdlib-safe + `series`/project modules; network denied **except** the provider
module's own client when `--provider fal` — providers run outside the user-code sandbox
in the parent process, so user code never gets the network; mock runs everywhere).

### Stdout: exactly one JSON line (snake_case; parent parses `stdout.splitlines()[-1]`)

```typescript
interface DramacodeResult {
  ok: boolean;
  episode_path?: string; srt_path?: string; metadata_path?: string;  // workspace-relative
  duration_s?: number; shot_count?: number;
  shots?: { id: string; path: string; duration_s: number;
            status: "rendered" | "cached" | "failed" }[];
  warnings?: { part: string; kind: string; detail: string; severity: string }[];
  error?: { code: "VALIDATION_FAILED" | "RENDER_TIMEOUT" | "PROVIDER_ERROR"
                 | "EXPORT_ERROR" | "SYNTAX_ERROR" | "RUNTIME_ERROR";
            message: string; traceback?: string };
}
```

### `story-analysis` output contract

Read-only, no artifacts: exactly one fenced ```drama-brief JSON block (series premise,
genre + trope set, cast list with looks/voices, season arc, episode-1 beat sheet) +
2-3 sentence summary. Hands off to `dramacode`.

### `cast-book` contract

Creates/updates `series.py` CAST entries and (fal provider) reference images under
`cast/<id>/ref_*.png`; prints one JSON line `{ok, cast: [{id, ref_images: n}]}`.

### `episode-viewer` contract

`npm --prefix scripts/viewer run serve:ensure -- --root-dir <project> --file episodes/ep001.mp4`
→ stdout is the URL + newline (donor mechanism; port scan 4178-4198; registry file;
reuse-if-same-root).
