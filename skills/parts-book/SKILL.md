---
name: parts-book
description: Synchronize a board project's exact populated component references into parts.json, preserving reviewed LCSC stock and price metadata. Use when pinning or swapping a component, checking orderability/Basic status/stock/cost, migrating a legacy parts lock, or resolving circuitcode findings such as part_not_orderable, part_lock_stale, part_drift, or extended_part.
---

# Parts Book

Own `parts.json` wholly. Read the project's frozen golden-block snapshot and
concrete board composition, run one fresh offline/routing-disabled inventory
compile with the pinned tscircuit toolchain, then write one exact populated
ref per entry. Never write TSX or run the fab board generator.

## Exact on-disk contract

Write a top-level object keyed by exact uppercase component refs:

```json
{
  "C2": {
    "lcsc": "C19702",
    "basic": true,
    "description": "10uF X5R 10V 0603",
    "block": "ldo-3v3",
    "stock": 1800000,
    "unit_price_usd": 0.012,
    "stock_checked": "2026-08-12"
  },
  "U2": {
    "lcsc": "C500795",
    "basic": false,
    "description": "AP7361C-33E-13 SOT-223",
    "block": "ldo-3v3"
  }
}
```

Required per ref: `lcsc`, `basic`, `description`, and `block`. Reviewed
metadata may include `mfr`, `package`, `stock`, `unit_price_usd`,
`stock_checked`, `datasheet_url`, `source`, `preferred`, `override`,
`footprint_risk`, and `swapped_from`.

Do not write a `version`/`summary`/`parts` wrapper, a list, a lowercase key, a
group (`R1/R2`), a range (`C4-C11`), a part family, or a DNP copper feature.
The tool may expand a finite documented group into separate exact keys, but it
must refuse an unresolved parametric or alternate ref rather than emit a
partial lock.

## Identity workflow

1. Read `product.json`, `golden-blocks.lock.json`, `boards/`, and the frozen
   `blocks/` snapshot before changing the lock.
2. Validate every frozen file and the lock's tree SHA-256. Never infer block
   ownership from every block present in a catalog.
3. Compile the exact source graph in a new temporary tree with the pinned CLI,
   `--routing-disabled`, `--disable-parts-engine`, and no copied artifacts.
   Require the newly-created Circuit JSON, reject every serialized error, and
   require the source fingerprint to be unchanged before/after compilation.
4. Extract the exact populated source-component/PCB-component join. Skip only
   literal DNP parts; require one physical land and exactly one JLCPCB C-number.
   This resolves bounded loops, dynamic ref overrides, and board-owned parts
   without trusting a stale artifact or guessing counts.
5. Use `BLOCK.md` for reviewed block-owned description/package/Basic metadata
   and frozen TSX for allowed supplier identities. A compiled/frozen contract
   disagreement is a refusal. Parametric documentation is ownership metadata,
   never the source of population.
6. Board-owned parts must already exist in the compiled inventory. Review all
   of them atomically with `--review-file`, or one at a time with `--add`; these
   flags can never invent population. Existing exact metadata may carry
   forward only when the compiler pins the same ref/LCSC.
7. Carry only catalog-wide stock, unit price, and checked date forward by
   exact LCSC identity. Basic/package/description review belongs to the exact
   compiled ref+LCSC and never transfers from a different ref.
   Legacy wrapper output may supply those migration facts, but never owns the
   new ref identities.
6. Run catalog lookup only when asked for freshness or when a selected block
   lacks a reviewed Basic/Extended classification.

## Command

```bash
# Offline synchronization from the frozen project snapshot.
python ~/.claude/skills/parts-book/scripts/parts /absolute/project

# Refresh stock, price, and Basic/Extended classification.
python ~/.claude/skills/parts-book/scripts/parts /absolute/project --lookup

# Add one board-owned glue component. All identity claims are explicit.
python ~/.claude/skills/parts-book/scripts/parts /absolute/project \
  --add J9 --lcsc C158012 --description "S2B-PH-K-S connector" \
  --mfr S2B-PH-K-S --package JST-PH --extended

# Review several already-compiled board-owned parts atomically.
python ~/.claude/skills/parts-book/scripts/parts /absolute/project \
  --review-file /absolute/project/parts.review.json

# Repoint one populated exact ref. This intentionally produces part_drift
# until the owning block source pins the same LCSC identity.
python ~/.claude/skills/parts-book/scripts/parts /absolute/project \
  --swap C2 --lcsc C15525 --package 0603 --basic
```

Options:

- `--blocks DIR`: alternate frozen-block location; bytes must still match the
  project's `golden-blocks.lock.json`.
- `--inventory-timeout S`: ceiling for the fresh offline placement compile.
- `--review-file FILE`: atomic exact-ref metadata for compiler-proven
  board-owned parts; it cannot add a ref absent from the inventory.
- `--lookup`: refresh catalog facts.
- `--add REF`, `--swap REF`, `--lcsc C123`: manual exact-ref operations.
- `--description`, `--mfr`, `--package`: reviewed manual identity fields.
- `--basic` or `--extended`: explicit reviewed assembly classification.
- `--timeout S`, `--retries N`, `--max-age-days N`, `--no-cache`: lookup
  controls. The cache lives at `~/.autonomous-circuit/parts-cache/`; set
  `CIRCUIT_PARTS_CACHE_DIR` to override it.

The tool prints exactly one JSON line. Success contains a summarized list:

```json
{"ok":true,"parts":[{"ref":"U2","lcsc":"C500795","basic":false,"stock_checked":null}]}
```

Refusal contains `ok:false` and `VALIDATION_FAILED`. Treat it as a hard stop;
do not hand-edit around it.

## Rules

- Prefer JLC Basic when electrically and physically equivalent, but never
  relabel an Extended part as Basic to avoid a loading fee.
- One populated ref has one exact orderable `C` number. Multiple refs may
  legitimately share that number and its catalog metadata.
- A package-changing swap is a **FOOTPRINT CHANGE**. Re-author the owning
  block's land pattern and rebuild every board before ordering.
- Never call `--lookup` in a build loop. Cold requests were measured at
  **47–90s**; refresh once before fab export.
- Never invent a ref, part number, Basic flag, stock, price, ownership, or
  count. Missing proof is a refusal. A pre-existing `*.circuit.json`, process
  exit code, parametric BLOCK row, or AST loop expansion is not population
  evidence.
- This skill never orders parts.

## Final response

Report:

1. What changed in the exact-ref lock.
2. The absolute `parts.json` path and counts of populated Basic/Extended refs.
3. Which LCSC identities have fresh stock/price dates and which remain
   unverified.
4. Any `part_drift` or FOOTPRINT CHANGE consequence and the boards that must
   be rebuilt before ordering.
