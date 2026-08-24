---
name: parts-book
description: Lock the BOM identities of an Autonomous Circuit board into parts.json — one exact orderable LCSC number per part, with package, Basic/extended status, stock, unit price, and the date it was checked. Use when the user pins, swaps, or questions a component — "use a USB-C connector", "swap to the cheaper regulator", "is that part in stock?", "pin the exact LED", "that part is out of stock", "what does this BOM cost" — or when circuitcode reports part_not_orderable, extended_part, or part_drift. Refreshes live stock and price from the JLCPCB/LCSC catalog with --lookup; never run it inside a build loop.
---

# Parts Book — the board's locked BOM identities

## Purpose

A board is only real if every line can be ordered. Part identity must
live in ONE place — not scattered through the source, not re-typed into
a spreadsheet. That place is `parts.json` at the project root: one
record per **unique orderable part**, carrying the exact LCSC C-number,
manufacturer part number, package, Basic-vs-extended status, stock, unit
price, the date those numbers were checked, and the datasheet page.

The client's parts panel reads this file. The pipeline's DFM/BOM gate
reads it too — a BOM row that disagrees with it raises `part_drift`.

## Whole-file ownership — the one rule that matters

**parts-book owns `parts.json` wholly.** Every run rewrites the entire
file. There are no guarded-block markers (the simplification over the
donor's cast-book, which had to share `series.py` with hand-written
code): nothing else writes `parts.json`, and this skill writes nothing
else. **circuitcode never writes parts.json; parts-book never writes
TSX.**

Consequences:

- Never hand-edit `parts.json` — the next sync overwrites it. Change a
  part with `--swap`/`--add`, or change the block.
- Stock/price/checked-date **carry forward by LCSC number**, so an
  offline re-sync never erases a lookup you already paid 90 seconds for.
- Editing the lock invalidates every board (the build fingerprint folds
  `parts.json` in) — batch part changes BEFORE an export pass, not
  between boards.

## Where the parts come from

**A board can pin a part too, and those count.** The blocks are where a
reusable lock belongs, but a board entry (`boards/<stem>.tsx`) may introduce a
part of its own — and it does so two ways. One is a block's way, a literal
`supplierPartNumbers` on the element. The other passes the number in as a prop,
`supplierPartNumbers={{ jlcpcb: [props.ledLcsc] }}`, with the literal at the
call site instead: `<ComfortLed led="LED2" ledLcsc="C2297" rLcsc="C25091" />`.
Both are read. The convention is that **`<x>Lcsc` pins the part named by
`<x>`**, which is where the refdes comes from; write a call site that way or
the part is invisible here and ships on `bom.csv` with nothing behind it.
Such a record carries `boards` and `source: board-source`, and the run says so
— **it has no stock or price until you `--lookup`.** A part meant to be reused
belongs in a block.

The **golden blocks are the part lock.** `supplierPartNumbers` in
`blocks/<id>/<id>.tsx` is ground truth for *which* orderable numbers a
board can contain; `blocks/<id>/BLOCK.md`'s parts table supplies the
refdes, package, Basic status, and the human-readable description. This
skill reads both out of the project's own `blocks/` directory (frozen
with the project at creation), falling back to the repo's
`packages/golden-blocks/blocks` when the project has none.

So the offline path is not a guess: it writes **candidate slots** with
real pinned numbers and no stock claim (`stock_checked: null`).
`--lookup` turns candidates into checked records.

## Available tool

```bash
# Sync: candidate slots from the blocks, previous lookups carried forward.
python ~/.claude/skills/parts-book/scripts/parts <project_dir>

# Sync AND refresh stock / price / Basic from jlcsearch (slow — see Rules).
python ~/.claude/skills/parts-book/scripts/parts <project_dir> --lookup

# Add a glue part no block owns (a header, a JST inlet).
python ~/.claude/skills/parts-book/scripts/parts <project_dir> \
       --add jst-ph-2 --lcsc C158012 --mfr S2B-PH-K-S --package JST-PH --refdes J9

# Point an existing part at a different orderable number.
python ~/.claude/skills/parts-book/scripts/parts <project_dir> \
       --swap c-10uf-0805 --lcsc C15525 --package 0603
```

Other flags: `--blocks DIR` (explicit block library), `--timeout S`
(default 90), `--retries N` (default 2), `--max-age-days N` (cache
freshness, default 7), `--no-cache`. Always pass absolute paths. The
lookup cache lives at `~/.autonomous-circuit/parts-cache/`
(`CIRCUIT_PARTS_CACHE_DIR` overrides).

Prints exactly one JSON line:

```json
{"ok": true, "parts": [{"id": "ams1117-3.3", "lcsc": "C6186", "stock_checked": "2026-08-10", "basic": true}]}
```

Optional keys: `lookup_note` (what could not be refreshed and why) and
`notes` (footprint warnings, block/lock divergence, docs drift). On
refusal: `{"ok": false, "error": {"code": "VALIDATION_FAILED",
"message": "…"}}` — missing `product.json`, a duplicate part id, a
part family instead of an exact number, or an unknown id to swap.

## The record written per part

| Field | Meaning |
|---|---|
| `id` | readable slug — `ams1117-3.3`, `r-4.7k-0402`, `type-c-31-m-12` |
| `lcsc` | the one exact orderable number, `C6186` |
| `mfr` | manufacturer part number |
| `package` | package/footprint class as documented by the block |
| `basic` | true = JLC Basic (no loading fee); false = extended |
| `stock`, `unit_price_usd` | last checked catalog numbers, `null` until checked |
| `stock_checked` | ISO date of that check, `null` for a candidate slot |
| `datasheet_url` | the LCSC catalog page for the number (it carries the datasheet; jlcsearch returns no direct PDF) |
| `refdes`, `blocks` | which designators and which golden blocks use it |
| `boards` | which board entries pin it — present only for a part no block owns |
| `source` | `block-default` · `board-source` · `jlcsearch` · `jlcsearch-cached` · `manual` |
| `preferred`, `override`, `footprint_risk`, `swapped_from`, `lookup_mismatch` | present only when true/relevant |

## Workflow

1. **Read the project first** — `product.json`, and `parts.json` if it
   exists. Know what is locked before changing it.
2. Run the tool (bare sync normally; `--lookup` when the user asks about
   stock, price, or cost, or before ordering).
3. Read the JSON line. On `ok:false`, fix exactly what the message names
   and re-run. On a `lookup_note`, say plainly which parts are
   unverified — never present a carried-forward number as fresh.
4. Report per part: id, LCSC, Basic-or-extended, and the stock/price
   with its check date.
5. Hand back to `circuitcode`. A `--swap` lives in `parts.json` only:
   until the block's TSX pins the new number, the build raises
   `part_drift`, and the real fix is a block edit.

## Rules

- **Prefer JLC Basic parts.** Every extended line adds a ~$3 loading fee
  to the order, which on a 20-line board can double the assembly cost.
  When a swap is being considered, "is there a Basic equivalent" is the
  first question.
- **One part = one exact orderable number.** Never a family ("TP4056"),
  never a value alone ("10uF"). The tool refuses anything that is not
  `C` + digits.
- **A swap that changes the footprint invalidates the LAYOUT, not just
  the BOM.** The tool shouts it (`footprint_risk`, a `FOOTPRINT CHANGE`
  note); repeat it to the user in those words. The land pattern lives in
  the block, so the block must be re-authored and every board rebuilt
  before ordering.
- **Never call this inside a build loop.** Cold jlcsearch queries were
  measured at **47–90s** on 2026-08-10; that is why the generator never
  touches the catalog and why the cache exists. One refresh before a fab
  export, not one per iteration.
- **Never invent a part number, stock figure, or price.** If it was not
  read from a block, the catalog, or a previous check, it does not go in
  the file — a candidate slot with `null` stock is honest; a made-up
  stock number is how a board gets ordered and never ships.
- This skill never writes TSX, never runs the generator, and never
  orders anything.

## Required final response

1. One sentence: what changed in the lock.
2. The `parts.json` path and the line count, split Basic vs extended.
3. Stock/price status: how many parts are checked and as of when; name
   anything still unverified.
4. Any footprint-change or `part_drift` consequence, in plain words —
   which boards must be rebuilt before ordering.
