# status-led — one indicator LED

**Function:** indication. A green 0805 LED with a 1kΩ series resistor from a
rail (default `net.V3_3`) to ground — the "it's alive" light. Point `rail` at a
GPIO net instead and the same block becomes a firmware-driven indicator.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.V3_3` (default; `rail` prop overrides) | source — a rail, or a GPIO net |
| `net.GND` | ground (LED cathode) |

Order is fixed: `rail → R20.pin1`, `R20.pin2 → LED1.anode`, `LED1.cathode →
GND`. The resistor is on the high side so the LED never sees the rail directly.
Set `driveKind="signal"` whenever `rail` names a GPIO/control net. The block
then compiles both signal edges at the ordinary 0.25mm board width. Rail drive
uses an explicit, at-most-3mm 0.2mm neck into R20; the local R20→LED1 edge is
0.25mm and at most 3mm. `railTraceWidthMm`, `signalTraceWidthMm`, and the two
maximum-length props exist for a board with an explicitly measured exception;
they are not a reason to lower the board-wide signal class.

`layer="bottom"` preserves polarity and is an exact block-local X mirror.
LED1, R20, their pads, and both routed endpoints remain on the selected face;
the rail neck and resistor-to-anode edge retain their 0.2/0.25mm widths and
3mm bounds.

When a board-wide authored V3_3 tree already owns the only named-net boundary,
set `externalRailAttachmentPort="R"` and attach that tree at
`.R20 > .pin1` (or the overridden `r` ref). This removes only the ordinary
`TR_R20_rail` leaf; the resistor-to-LED polarity, 0.25mm local series path,
and GND fanout remain frozen. Do not set the prop without adding that explicit
board connection.

## Rail budget

**≈1.2mA at 3V3** through 1kΩ, assuming a green Vf ≈ 2.1V ((3.3 − 2.1)/1k).
The Vf is the block's own working figure and is **not re-verified against the
KT-0805G datasheet** — treat the current as an estimate good enough for
budgeting, not for a photometric spec. Visible and frugal; when driven from a
GPIO the same 1.2mA is far inside any MCU's per-pin limit.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| LED1 | KT-0805G, green | C2297 | 0805 | yes | $0.010, 2.80M stock |
| R20 | 0402WGF1001TCE, 1kΩ ±1% | C11702 | 0402 | yes | $0.0005, 12.6M stock — series |

## Design-rule notes

- Polarity lives in the block: `anode` to the resistor, `cathode` to GND. Never
  re-wire an LED at board level — a reversed indicator is the defect deterministic
  checks are worst at catching.
- Instantiate twice by overriding `led` and `r` (e.g. `LED2` / `R21`); the
  default LED1/R20 refdes are the global v1 allocation.
- Driving from a GPIO: pass `rail="GPIO_NET_NAME" driveKind="signal"`. The LED is then active-high;
  an active-low arrangement needs the block flipped, which is a block change.
- 1kΩ is deliberately conservative. A brighter indicator is a resistor value
  change inside the block, not a board-level override.

## Provenance

- Both parts chosen from the JLC Basic library (r5 recon rule: Basic parts
  avoid the ~$3/line extended loading fee).
- Land patterns: footprinter builtins `0805` (LED) and `0402` (resistor); no
  imported footprint needed.
