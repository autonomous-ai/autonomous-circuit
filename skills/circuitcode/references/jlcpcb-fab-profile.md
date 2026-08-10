# The JLCPCB fab profile — limits, fees, and what "orderable" means

**Load:** when the user asks about cost, ordering, or a DFM warning fires.

All numbers from `docs/circuit-research-2026-08-10.md` (vendor docs read
2026-08-10). `helpers.fab_profile()` returns the machine-readable version.

## The manufacturable window (2-layer, 1oz)

| Rule | We block below | JLC's own floor |
|---|---|---|
| Trace width / spacing | 0.127mm | 0.10mm |
| Via drill / diameter | 0.30 / 0.50mm | 0.15 / 0.25mm |
| PTH annular ring | 0.20mm | 0.20mm |
| Copper to board edge | 0.30mm | 0.30mm |
| Board edge | 3.0mm | 3×3mm |
| Silkscreen text | warn under 1.0mm | 1.0mm |

We pre-filter tighter than JLC's floor because the cheap process is where a
marginal board turns into a remake. **JLC's upload-time check is still the
authority** — ours is a filter, not a guarantee.

Board thickness: set `thickness={1.6}` explicitly. The toolchain default is
1.4mm, which is not JLC's standard stackup.

## What a run costs

Economy PCBA, top side, quantity 5:

- Bare PCB, 5× 2-layer ≤100×100mm: **$2** + shipping ($1.50–3 slow, $16–25 fast)
- Assembly setup + stencil: **$9.50**
- SMT joints: $0.0017 each
- **Extended parts: $3.00 per unique BOM line.** This is the number that moves a
  small board's price. Prefer JLC **Basic** parts; `parts.json` records
  basic/extended per part and the pipeline emits an `extended_part` info warning
  where a Basic alternative exists.
- Components themselves: from the parts lock, not from a table.

Typical all-in for an ESP32-class assembled sensor board, qty 5:
**$75–110, 1–2 weeks to the door.** Bare boards only: **$4–20.**

Economy assembly caps at 30 assembled pieces per design.

## The packet

`<stem>_fab/` contains exactly what the upload flow wants:

- `gerbers.zip` — copper, mask, silk, edge cuts, drill
- `bom.csv` — `Comment, Designator, Footprint, LCSC Part #`
- `cpl.csv` — `Designator, Mid X, Mid Y, Layer, Rotation` (mm, component centres)
- `ORDER.md` — the click-by-click walkthrough

## "Orderable" has a precise meaning

`fab.ready` is true only when **both**: zero `error`-severity warnings, **and**
the gerbers came from kicad-cli (`fab.gerberSource == "kicad-cli"`).

If kicad-cli is not installed, the pipeline still writes gerbers from the
tscircuit exporter and attaches an `unverified_gerbers` warning — and does not
write `ORDER.md`. That packet is for looking at, not for ordering. Say so
plainly; do not let a user spend $85 on a packet we did not verify.

## The one thing to warn the user about

JLCPCB's zero-rotation convention differs from most EDA output for several
package families (SOT-23, some SOICs, many connectors). JLC auto-corrects for
known LCSC parts, but the **component placement preview** screen during checkout
is the real safety net. `ORDER.md` says this; repeat it when you hand over a
packet. A rotated connector is a dead board that passed every check we own.
