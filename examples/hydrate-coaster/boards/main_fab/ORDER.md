# Order: hydrate-coaster

80 x 80 mm, 2 layer(s), verified fab packet
(jlcpcb). Files in this folder: `gerbers.zip`, `bom.csv`, `cpl.csv`.

## Walkthrough (JLCPCB economy PCBA)

1. cart.jlcpcb.com/quote -> **Add gerber file** -> drop `gerbers.zip`
   (layers + size auto-detect; verify 2 layers, 80 x 80 mm).
2. Options: Qty **5**, 1.6 mm, HASL, green — leave the rest default.
3. Toggle **PCB Assembly** on: PCBA Type **Economic**, Assembly Side **Top**,
   Qty **2** (or 5) -> **Confirm**.
4. Next -> gerber preview renders -> Next.
5. **Add BOM File** -> `bom.csv`; **Add CPL File** -> `cpl.csv` ->
   **Process BOM & CPL**.
6. Parts-match table: every line should show a matched C-number and stock
   (17/20 lines carry part numbers in this packet). Shortfalls show
   red — either accept "Do Not Place" or swap the part in chat and re-export.
7. Next -> **component placement preview**. **This screen is the safety net:**
   JLCPCB auto-rotates known parts and its rotation conventions differ from the
   CPL's — eyeball pin-1 orientation on every IC/module, connector orientation,
   and polarized parts before continuing.
8. **Save to Cart** -> checkout, pick Global Standard Direct (cheap) or DHL (fast).

## Cost + turnaround

- PCB only, 5x 2-layer <=100x100mm: $2 + shipping (~$4-20 all-in, 24-48h fab); over 100x100 the subsidy is gone — 112x90 quoted $8.90 (2026-08-15).
- Assembled, 5x ESP32-class: ~$75-110 all-in, ~1-2 weeks to door.
- Extended parts add a ~$3/line loading fee; Basic parts avoid it.

Order status: JLCPCB emails + your account page (no API in v1).
