---
name: cast-book
description: Manage the cast of an Autonomous TV drama series — create or update CAST entries in series.py and the per-character reference slots under cast/<id>/. Use when the user adds or recasts a character — "add a rival named Vera", "make the lead silver-haired", "set up character reference sheets", "the cast keeps changing faces" — or when dramacode reports an unknown cast id or visible cast drift on the _board.png. v1 writes reference PROMPTS (four-view sheet + expression), not images; image generation lands with real providers.
---

# Cast Book — the series' character reference system

## Purpose

Cast consistency is the #1 dropout driver in short drama — identity must
live in ONE place, not be re-described per shot. That place is the
series bible: `series.py`'s `CAST` list (id, name, look, voice,
ref_images) plus the reference assets under `cast/<id>/`. This skill
owns both: it syncs reference-prompt sheets per character and performs
the ONLY sanctioned edit of the CAST block.

By default this writes reference PROMPTS — a `cast/<id>/ref_prompts.json`
with the 4-6 slots of the industry four-view sheet (1/4 head close-up,
front/side/back full body on white, plus an expression ref; asset naming
follows 人名_特征). Drop reference images into `cast/<id>/` as
`ref_*.png` / `ref_*.jpg` and re-run and the tool wires them into
`ref_images` automatically.

**`--render` generates the PIXELS.** With `--render` (needs `dramapy` on
the path + `FAL_KEY`) the tool renders each character's turnaround SET
through the cinematic consistency model — a base front view (text-to-image)
then edited-from-base views (expression / three-quarter / profile) so the
whole set is provably the SAME person — writes them to
`cast/<id>/ref_<slot>.jpg` + a `refset.json` manifest, and wires them into
`ref_images`. Without `FAL_KEY`/dramapy it degrades to prompts-only and
says so in `render_note`. **You do not have to pre-render:** the cinematic
provider generates and caches the same turnaround set on first use (and
reuses `refset.json` / pinned `ref_images` if present), so identity holds
across shots either way.

## The guarded block — the one rule that matters

`series.py` carries two markers:

```python
# CAST-BOOK BEGIN
CAST = [
    Character(id="li_wei", name="Li Wei", look="…", voice="f_low_calm",
              ref_images=[]),
]
# CAST-BOOK END
```

This skill rewrites ONLY the text between the markers (it parses the
file with `ast`, never executes it). Everything outside — `SERIES`,
pacing constants, comments — is never touched. Consequences:

- Never hand-edit inside the markers; the next sync overwrites it.
  Change a character via `--add` or by asking for a resync after editing
  the look through this skill.
- Never delete the markers. Without them the tool refuses to write
  (`ok:false`) rather than guess.
- Editing `series.py` invalidates every rendered episode (the
  fingerprint folds the bible in) — batch cast changes BEFORE a render
  pass, not between episodes.

## Available tool

```bash
# Sync: prompts out, existing ref images wired in, block regenerated.
python ~/.claude/skills/cast-book/scripts/cast <project_dir>

# Sync AND render the turnaround-sheet pixels (needs FAL_KEY + dramapy).
python ~/.claude/skills/cast-book/scripts/cast <project_dir> --render

# Add a character (then sync runs automatically).
python ~/.claude/skills/cast-book/scripts/cast <project_dir> \
       --add vera --name "Vera Lin" \
       --look "woman, 30, silver crop, red blazer, never blinks first" \
       --voice f_bright_sharp
```

Always pass absolute paths. Prints exactly one JSON line:

```json
{"ok": true, "cast": [{"id": "li_wei", "ref_prompts": 5}]}
```

On refusal (missing markers, malformed CAST, duplicate id):
`{"ok": false, "error": {"code": "VALIDATION_FAILED", "message": "…"}}`.

## Workflow

1. **Read `series.py`** first — know the current cast before changing it.
2. Run the tool (`--add` for new characters; bare sync otherwise).
3. Read the JSON line; on `ok:false`, fix what the message names (add
   markers, resolve the duplicate id) and re-run.
4. Report per character: the `ref_prompts.json` path and how many
   `ref_images` are wired (0 until images land — say so plainly).
5. Hand back to `dramacode` — episodes reference characters by cast id
   only; shot prompts must never re-describe appearance.

## Rules

- **2-3 leads** (`LEADS_PER_SERIES`). Refuse politely past 3: every
  additional consistent face multiplies drift risk; background
  characters don't get CAST entries.
- A `look` is one line: age, costume, hair, ONE signature detail. It is
  the consistency anchor — specific beats lyrical.
- The `look` given to this skill is final for the four-view sheet; a
  later look change regenerates prompts AND invalidates rendered
  episodes. Warn the user when they recast mid-series.
- This skill never writes episode files, never renders, and never edits
  outside the markers.

## Required final response

1. One sentence: what changed in the cast.
2. The `cast/<id>/ref_prompts.json` path(s) and prompt count per
   character.
3. `ref_images` wired count per character (with "images land later via
   the provider pipeline" while it is 0).
4. One line on render impact: which episodes the bible change
   invalidates, if any exist.
