# terminal-keyboard — the thumb keyboard for the Autonomous Terminal

The Terminal is a BlackBerry-shaped handheld that is a full mobile Linux
computer: you run Claude Code, Codex and any other agent CLI on it, from a
physical keyboard, with the screen above and nothing else competing for your
attention. This board is the part under your thumbs.

It is a 5-row × 10-column diode-isolated key matrix on an RP2040. The RP2040
speaks USB natively, so the board enumerates as a plain HID keyboard over
USB-C — no bridge chip, no driver, and the same firmware path (BOOTSEL → drag
a UF2) every RP2040 board uses.

> **Migration status (2026-08-11): not fab-ready.** `boards/main.tsx` and
> `product.json` now describe the 108 × 58 mm, two-sided layout fixture; the
> committed `main.circuit.json`, sidecar, review images, and fab directory below
> are historical until the full pipeline republishes them from that source.
> The declared power-trunk policy is still an intentional blocking check.
> SW2/SW3 use the reusable golden-core compact option: validated TPT-2C1,
> LCSC C2828561, 3 × 2 mm, with the two-pin topology compiled rather than
> pretending it has the standard switch's redundant four terminals.

```
boards/main.tsx          the board program — the only hand-written file
boards/main.circuit.json the compiled IR of record
boards/main.board.json   the sidecar (warnings, BOM summary, fab state)
boards/main_review/      _schematic.png, _pcb.png
boards/main_fab/         gerbers.zip, bom.csv, cpl.csv, board.glb
DESIGN-REVIEW.md         the seven-lens panel, round by round
```

## The key map, and how it becomes a matrix

The legend comes off the real prototype, five rows, left to right:

| row | keys |
|---|---|
| 1 | ESC/~ · FN/+ · CTRL/@ · F1/# · F2/$ · F3/% · F4/( · CTRL · FN/- · BKSP/DEL |
| 2 | Q/= · W/1 · E/2 · R/3 · T/? · Y/{ · U/} · I/[ · O/] · P/\| |
| 3 | A/- · S/4 · D/5 · F/6 · G/& · H/! · J/; · K · L/" · **ENTER** (tall) |
| 4 | TAB/* · Z/7 · X/8 · C/9 · V/, · B/. · N/< · M/> · PgUp/▲ |
| 5 | SHIFT · ALT/! · MIC/0 · **SPACE** (wide) · Home/◀ · PgDn/▼ · End/▶ |

That is 46 legends on a 5 × 10 grid of 50 positions. The board populates all
50 — every position gets its own switch and its own diode, on the same pitch —
and the four extra positions are absorbed by the oversized keycaps:

| position | what sits there |
|---|---|
| R3C9 **and** R4C9 | two switches under the one tall ENTER cap |
| R5C3, R5C4, R5C5 | three switches under the one wide SPACE cap |
| R5C9 | wired spare — in the matrix, no cap in the current body, free for the FN layer |

Putting two or three switches under one big keycap is deliberate, not padding.
A wide cap on a single central switch rocks and needs a stabiliser bar; a wide
cap resting on three switches actuates evenly from any corner with no extra
mechanical parts, and firmware simply maps all three to the same code. Row 4
has nine legends because its tenth position is ENTER's lower half — the map
closes exactly.

Silkscreen carries the legend above each key (`ESC`, `FN+`, `CTL@`, … `SPR`),
so the bare board is populatable and testable without the map in hand.

**Refdes:** switches are `SW10`–`SW59` and diodes `D1`–`D50`, both numbered
row-major, so `D1`/`SW10` is top-left, `D10`/`SW19` is top-right, `D50`/`SW59`
is bottom-right. `SW1`–`SW3` are reserved (the `sw-tact` default and the
RP2040 core's BOOTSEL/RESET), so nothing collides.

## Diode direction: COL2ROW

One 1N4148W (SOD-123, LCSC **C81598**, pinned in the board source the way every
golden block pins its parts — `supplierPartNumbers={{ jlcpcb: [...] }}`) sits in
series with every switch, all 50 facing the same way:

```
net.COL<c> ──▶|── net.K<r><c> ── SW ── net.ROW<r>
            D<n>
        anode   cathode
```

Anode on the column, cathode toward the row: **current can only flow column →
row**, which is what QMK calls `COL2ROW`. Firmware drives one ROW low at a time
and reads the ten COL inputs on their internal pull-ups, so a scan is five
steps. Without the diodes, three keys sharing a row and a column create a
fourth phantom press; with them, the return path through an unpressed key is
blocked and n-key rollover is real.

**Pin allocation** — 15 pins, not 50:

| RP2040 | use |
|---|---|
| GPIO0–GPIO4 | ROW0–ROW4, driven outputs |
| GPIO5–GPIO14 | COL0–COL9, inputs with internal pull-ups |
| GPIO15–GPIO29 | free |

No external pull-ups: the RP2040's internal ones are the pull-up story, exactly
as `sw-tact`'s BLOCK.md specifies.

## Pitch, size, and why

**Pitch: 10.0 mm, both axes.** That is the floor, not a preference. The switch
is a TS-1187A on the footprinter land pattern
`dfn4_p3.6998mm_w7mm_pw0.75mm` — 7.9 mm across the pads, 5.4 mm of courtyard
tall. At 10 mm the pad-to-pad channel between columns is 2.1 mm, which is what
the column trace runs down; below about 9.5 mm the channel closes and the
switch courtyards touch. Real thumb keyboards live at 9–12 mm, so 10 mm is
inside the band, at the tight end.

The diode sits 3.4 mm left and 4.4 mm below its switch. The 4.4 mm is measured,
not guessed: at 3.6 mm the SOD-123 courtyard (4.7 × 2.3 mm) overlapped the
switch courtyard (7.5 × 5.4 mm) by 0.25 mm and the build returned 100
`pcb_courtyard_overlap_error`s.

**Board: 108 × 58 mm, 2 layers, 1.6 mm.** The 100 × 50 mm nominal key field gets
an explicit 4 mm mechanical band on every edge. Switches and diodes are the
only top-side parts; the RP2040 core, USB protection, regulator, LED, and their
passives live on the bottom behind the key field instead of requiring a stale
electronics strip below it.

**Mechanical:** six M2.5 clearance holes (2.7 mm) at x = ±52, y = +27 / 0 /
−27. USB-C is centred at **x = 0** on the bottom edge, so cable alignment is a
machine-checked layout contract rather than a historical placement anecdote.

## Parts and cost

| | |
|---|---|
| Components | **134** (50 switches, 50 diodes, RP2040 core ×22, USB-C+ESD ×7, LDO ×3, LED ×2) |
| Unique part numbers | 17 (5 extended: RP2040, the crystal, the USB-C receptacle, the 27 Ω pair, the USBLC6) |
| Solder joints | 455 |
| Nets | 75 |

Cost at qty 5, assembled, from `circuitlib.helpers.estimate_cost()`:
**$30.37 excluding parts** ($2.00 PCB + $13.37 assembly + $15.00 in extended-part
feeder fees). Parts are roughly **$5.70 a board** at LCSC list
(RP2040 $0.99, flash $2.45, 50 switches $0.90, 50 diodes ≈ $0.50, crystal
$0.33, the rest under a dollar), so about **$59 for five boards, ≈ $12 each**.

Two honest corrections to that number. The `$2.00` PCB line is JLCPCB's flat
prototype price, which applies to boards up to 100 × 100 mm — this one is
108 × 58 mm and falls outside it, so budget **$15–25** for the bare boards and
about **$75–85 all-in for five**, which is the profile's own
`cost_band_assembled_5x_usd` of $75–110. And every number here is *modelled*,
not quoted: nobody has put this board through a real JLC cart.

## Building it yourself

```bash
CIRCUIT_PARTS_ENGINE=off python3.12 skills/circuitcode/scripts/circuit \
  /abs/path/examples/terminal-keyboard/boards/main.tsx --wall-clock-s 2400
```

There is no trustworthy wall-clock estimate yet. The migrated source no longer
sets `autorouterEffortLevel="5x"`: the pinned core accepts that prop but does
not map it to a different router implementation. The old 17-minute-versus-
4:45 comparison was also contaminated by stale compiled/cache inputs and is
withdrawn. Treat a build as current only when the exact TSX source fingerprint
matches the published artifacts and every routing phase completes.

## Bring-up

Plug USB-C in: LED1 lights, the board enumerates as `RP2040` mass storage when
BOOTSEL (SW2) is held during reset (SW3), and after a UF2 the host sees a
50-key HID keyboard — press ESC (SW10) first, then any key in the bottom row.

Finding one dead key: every matrix net is exposed on big hand-probe-able
copper. Shorting pads 3 and 4 of a switch to its own signal pads with tweezers
fires that key without the switch; if that works the switch is dead, and if it
does not, walk the column at the diode anode. There is no SWD header — see
below.

## Where it stands now

**Not fab-ready.** The historical claim of “46 → 1 blocking error” came from a
superseded 112 × 90 mm layout and is not evidence for the current 108 × 58 mm
source. The latest clean exact-source routing evidence passes the six
crystal/clock connections and five remaining QSPI connections, then stops in
the dedicated GND fanout phase after routing only 20 of 31 drops. No full
routed artifact or current KiCad/fab packet has been published from this
source.

## Honest limits

The checked-in circuit JSON, review images, sidecar, and fab directory are a
historical packet. Their counts must not be mixed with the migrated source:

| | |
|---|---|
| current source routing | **blocked** — GND fanout routed 20/31 drops |
| parsed routed artifact | **not available** — the partial artifact contains a `pcb_autorouting_error` |
| current KiCad/DFM gate | **not run** — routing must complete first |
| `fab.ready` | **not proven / must remain false** |
| historical packet | retained for provenance only; excluded from current review results |

### Withdrawn router experiment record

Earlier versions of this README compared default effort, `5x`, four layers,
and several placement changes using blocking-error counts from generated
artifacts. Those numbers are withdrawn: the runs did not reliably prove that
the source fingerprint, sibling `main.circuit.json`, and router cache all
described the same build, and the pinned core does not implement a distinct
`5x` routing strategy. The useful lessons that survive are procedural:

1. Gate every phase and parsed `*_error`; a zero CLI exit code is insufficient.
2. Never compare router knobs unless both runs start from the exact same source
   fingerprint and a compatible, isolated cache.
3. Treat a small placement or obstacle change as a fresh routing problem. A
   remote obstacle can change global topology even when it appears in none of
   the final DRC pairs.
4. Four-layer export support and narrower fabrication rules remain capability
   questions, not demonstrated fixes for this migrated board.

### Other things you should know

- **The old KiCad packet used the wrong clearance.** Its converted board carries
  a 0.09 mm netclass and is historical evidence only. The migrated product and
  TSX board both require 0.15 mm trace-to-pad and via-to-pad clearance, and the
  pipeline now carries that same intent into KiCad. A current KiCad/DFM pass is
  still required after routing; the declaration alone is not fabrication proof.
- **SWD bring-up furniture is now explicit.** The reusable RP block emits
  SWCLK, SWD, and GND test points at a board-owned collision-free coordinate;
  the BOM policy excludes test points. The old `<via connectsTo="net.X">`
  lesson still stands: a net-tagged via alone is metadata, not a routed
  dogbone, and must not be presented as probeable connected copper.
- **Current route quality is unknown.** Historical PCB images do not represent
  the migrated source. Do not review trace aesthetics, length, or via count
  until a complete exact-source artifact passes parsed DRC.
- **Ground planes are source intent, not yet proven output.** The migrated
  board declares top and bottom GND pours, explicit stitching, and a dedicated
  fanout phase. That phase is currently blocked, so the final poured geometry
  and island connectivity remain unverified.
- **`bom.csv` ships with empty `Comment` and `Footprint` columns.** Only
  `Designator` and `LCSC Part #` are populated for all 134 rows. JLC will take
  it; a human checking a 134-line BOM cannot tell a 15 pF from a 10 µF. That is
  the circuit-json exporter, not the board source.
- **The `sw-tact` pad pairing is still hardware-unverified.** Its BLOCK.md says
  so plainly: if the TS-1187A's real internal pairing is 1+3 / 2+4 rather than
  1+2 / 3+4, the block's tie traces are a permanent short and all 50 keys are
  scrap. On a board that is 50 copies of one switch, that is the single largest
  risk in the design. Verify on the first article before anyone orders five.
- **The RP critical-cluster fixes are reusable now.** Crystal/flash placement,
  routing phases, compact BOOTSEL/RESET buttons, and GND fanouts come from the
  golden block snapshot; this example must stay byte-for-byte in parity rather
  than accumulating project-local shims.
- **A generated sibling can masquerade as a build input.** A broad CLI build
  may discover `boards/main.circuit.json` and copy that historical artifact
  without executing the edited `boards/main.tsx`. Always pass the exact TSX
  entry point through the pinned loader/pipeline, and require the source
  fingerprint freshness guard before publishing review results. Router-cache
  comparisons are valid only when the source and toolchain fingerprints match.
- **Never wrap a generated element in a bare `<group>`.** The first version put
  each key cell in `<group key={…}>` with no `pcbX/pcbY`. That hands the board
  to the auto-pack solver, which threw away all 50 computed positions and
  returned `PackSolver2 ran out of iterations` plus 100 pad overlaps. The key
  grid is emitted as flat siblings for exactly this reason.

### Not checked by anything here

Thermal behaviour, EMI, USB signal integrity (the D± pair is not
length-matched and not impedance-controlled — it is a full-speed link, which
is forgiving, but it is not verified), keycap mechanics, actual switch feel,
and whether 10 mm pitch is comfortable for real thumbs. Those need the first
article.
