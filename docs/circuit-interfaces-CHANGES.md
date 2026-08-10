# circuit-interfaces.md — change log (append-only, newest first)

`docs/circuit-interfaces.md` is frozen as of v0.1 (2026-08-10). Every change lands here
first, in this template, before the doc itself is edited:

```
## YYYY-MM-DD — <one-line title>
- **Change:** what changed, naming exact identifiers/env vars/paths.
- **Why:** the reason, citing the decision/finding (with date) that forced it.
- **Backward compatible:** yes/no — and for no, what breaks (cache invalidation
  consequences stated explicitly).
- **Mechanism:** how the change is applied (code sites, migration, re-vendor).
  Flag "skill runtime re-vendor required" whenever packages/circuitpy changes.
- **Tracks affected:** pipeline / server / client / skills / docs.
```

## 2026-08-10 — Fab packet members written on non-ready builds too (ORDER.md stays ready-only)
- **Change:** `build_board()` writes `gerbers.zip` / `bom.csv` / `cpl.csv` into
  `<stem>_fab/` whenever the exports succeed — including builds with
  `error`-severity warnings and kicad-absent builds — not only "when fab-ready"
  as the §1 artifact table's Always? column reads literally. `ORDER.md` remains
  strictly fab-ready-only, and export failures on a board that already has
  error warnings degrade to a `check_failed` warning instead of `ExportError`
  (an otherwise-clean board still raises `ExportError`).
- **Why:** the table's literal reading contradicts §1 stage 5's own gate
  ("kicad absent → tscircuit-exported gerbers plus `unverified_gerbers`" — a
  by-definition not-ready packet that still writes gerbers) and §2's BOM tab,
  which needs `bom.csv` for boards mid-repair. Found while building the
  pipeline track (2026-08-10).
- **Backward compatible:** yes — consumers gate on `fab.ready` +
  `validation.warnings` severity, never on file presence; extra files carry no
  new semantics.
- **Mechanism:** `packages/circuitpy/src/circuitpy/generation.py` stage 5
  block. Skill runtime re-vendor required (packages/circuitpy changed).
- **Tracks affected:** pipeline / docs (table footnote when the freeze lifts).

## 2026-08-10 — Catalog must surface root `parts.json` as an entry
- **Change:** the client (PartsPanel + BomTable enrichment) reads the parts
  lock through a catalog entry whose `file` is exactly `parts.json` (any
  `kind`), fetching `entry.url` verbatim (`?v=` cache-bust). §2's visibility
  rule "`.json` hidden" gets one more exception alongside the sidecar:
  root `parts.json` is surfaced.
- **Why:** §2 requires "PartsPanel replaces CastPanel (reads parts.json)" but
  names no transport for it; the donor precedent (series.json surfaced despite
  the .json-hidden rule) is the cheapest path and keeps the ?v= refetch
  semantics. Decided while building Track E (2026-08-10).
- **Backward compatible:** yes — if the scanner doesn't surface it, the panel
  and the BOM badges degrade to their empty states; nothing breaks.
- **Tracks affected:** server (catalog visibility rule), client (already
  built to this: `lib/boardModel.js selectPartsEntry`).

(No further entries yet.)
