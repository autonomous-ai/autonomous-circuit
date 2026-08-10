# `<series title>`

A dramacode project (one drama series). Structure:

```
spec.md              series intent (read this first)
series.py            the series bible: SERIES + CAST (cast-book owns the
                     marked block), total episodes, market, format
episodes/            one .py per episode, artifacts land alongside
  ep001.py           defines gen_episode(); validate() + functional_warnings()
cast/<id>/           reference images + ref_prompts.json (cast-book skill)
inputs/              chat reference images (excluded from catalog)
.video/render-cache/ content-addressed shot clips (excluded from catalog)
```

## Run

```bash
python ~/.claude/skills/dramacode/scripts/drama episodes/ep001.py
```

The runner puts the project root (this directory) on `sys.path`, so
episode files do `import series`. Artifacts land next to the episode
unless you pass `--out-dir`.

## Editing rules (AI agents read this)

- **Numbers come from `dramalib.tables`.** Never hardcode a shot duration,
  gate episode, or subtitle limit an existing table owns.
- **One file per episode.** Series-wide facts (cast, style, fps) live in
  `series.py` only — editing it invalidates every rendered episode.
- **Never edit generated artifacts** (`.mp4`, `.srt`, `.episode.json`,
  `_shots/`, `_review/`). Edit the `.py`, re-run.
- **Keep the cast-book markers** in `series.py` intact; the cast-book
  skill regenerates that block.
