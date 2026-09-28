# modules — header-mounted modules, knowledge only

A module on a header (a display board, a touch board) is not a golden block: it carries its own
IC circuit, we do not author it, and in v1 terms the header is glue. What a board needs from it
is the **pin contract**, the current it draws, and the trap that bit an earlier run. That is
what `modules/<id>/BLOCK.md` holds — no TSX, no testbench, no snapshot.

The KiCad harness reads them beside the golden blocks (`$KICAD_HARNESS_BLOCKS/../modules/`).
`tests/test_blocks.py` enumerates `blocks/` only; a module directory needs its BLOCK.md and
nothing else.

| Module | Header | Born from |
|---|---|---|
| `st7789-1.54-module` | 1×8, 2.54 mm | harness-12/14 (2026-09-23/24): an NPN low-side on BLK could never light the backlight |
| `ttp223-module` | 1×3, 2.54 mm | harness-14: the DFR0030 pin order is OUT, VCC, GND — the cable must be mapped |
