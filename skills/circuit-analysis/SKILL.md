---
name: circuit-analysis
description: Enrich a short or vague hardware idea into a build-ready board brief before any circuit is written. Use when the user asks for a device in a few words — "a plant waterer", "a desk CO2 monitor", "a macropad", "a little box with a screen", "something that logs temperature" — and the request lacks a function spec, power source, connectivity, controls, or enclosure interface. Returns one structured circuit-brief composed from the golden-block catalog with proven defaults so the board comes out buildable and orderable rather than hand-wavy. Not for a full spec, an existing board project, or an edit to written board source.
---

# Circuit Analysis — prompt enrichment for boards

## Purpose

"Make me a desk air monitor" underspecifies the board: it says nothing
about how the thing is powered (which decides half the schematic), which
brain it needs (connectivity decides that), which sensors exist as
validated blocks, what the user touches, how it bolts into a printed
enclosure, or what five assembled units cost. Building it literally
yields a board that compiles and can't be ordered, or worse, one that
invents an IC circuit from a datasheet.

This skill turns a terse request into a **build-ready board brief**: the
product class, the power story, the brain, the blocks, the I/O, the
enclosure interface, and a cost band.

This is a **read-only, no-artifact** skill. It writes no files, compiles
nothing, orders nothing, and never touches the network. Its only output
is one enriched brief, which the user (or the `circuitcode` skill) then
turns into a project.

## When to use

- The user names a device in a few words with no power, connectivity,
  sensor, control, or size detail.
- The prompt is ambiguous about the thing that changes the schematic
  most: is it plugged in or on a battery? does it need Wi-Fi, or just
  USB?
- The user asks "can we build X" and wants the shape of the board before
  committing to a project.

Do **not** use it when the user already gave a complete spec (function +
power + connectivity + size), is editing an existing `boards/*.tsx`, or
asked for a change inside an existing project — that is `circuitcode`
directly.

## The one hard gate: the safety envelope

Run this **before** anything else. It is a refusal, not a warning
(`circuitlib.safety`, contract §1 "Safety envelope"):

- **No mains, ever.** Anything that plugs into a wall socket, switches
  line voltage, or names 110V/230V/AC is refused. Say so plainly, name
  the reason ("mains is outside the envelope this pipeline can verify"),
  and offer the low-voltage version: a USB-C wall adapter feeding a 5V
  board, or a certified off-the-shelf relay module the board drives over
  a low-voltage signal. Emit the brief with
  `"safety": {"verdict": "reject", ...}` and stop.
- Low-voltage DC only, **≤24V** (`circuitlib.tables.MAX_DC_INPUT_V`).
- **Battery only via the sealed validated charge/protect block.** That
  block is gated pending hardware sign-off — if the ask needs a battery,
  say the block is not released yet and default the brief to USB-C.
- **Radio only as certified modules** (ESP32-WROOM/MINI class). Never
  bare-die RF, never a discrete antenna design.

`not_screened` is never a pass. If you can't tell whether the ask
implies mains, ask the one question.

## Workflow

1. **Classify the product class.** Sensor node / input device
   (macropad, controller) / indicator or display / actuator-driver /
   data logger. The class picks the default block set and the default
   size class. State the class in the brief; it is the assumption most
   worth correcting.

2. **Pick the power story.** Exactly one of:
   - `usb-c-5v` — the default for anything that lives on a desk. Block:
     `usb-c-power` (power only) or `usb-c-data` (when the MCU speaks USB
     — they share refdes; **never place both**).
   - `battery-lipo-sealed-block` — only via the sealed block, and only
     once it is released; today this is a *gap*, not a choice.
   - `external-dc-lv` — a low-voltage DC barrel/JST feed for motor or
     LED loads, ≤24V.
   Then the logic rail: `ldo-3v3` (AMS1117-3.3) unless the whole board
   is 5V. Rail names and tolerances are
   `circuitlib.tables.RAILS`; the decoupling rule
   (`DECOUPLE_PER_POWER_PIN`, `BULK_PER_RAIL`) is the table's, not yours.

3. **Pick the brain.** **Connectivity decides it:**
   - needs Wi-Fi / BLE / talks to a phone or cloud → **ESP32-S3**
     (certified module; `esp32-s3-core`).
   - USB-attached only — HID, serial, a device a computer drives →
     **RP2040** (`rp2040-core`; cheaper, deterministic PIO, no radio,
     no certification burden).
   - neither, and the job is "read a sensor and blink" → still pick one;
     an MCU-less board is almost never what the user wants.
   If the block for the chosen brain is not in the registry yet, say so
   in `gaps` and pick the released one that still does the job.

4. **Pick the sensor/actuator blocks — from the registry only.** The
   registry is `circuitlib.blocks` (mirrored by
   `packages/golden-blocks/blocks/<id>/BLOCK.md`, which carries each
   block's pin contract, rail budget, and pinned LCSC parts). Blocks
   released in v1 as of 2026-08-10:

   | Block | What it is |
   |---|---|
   | `usb-c-power` | USB-C 5V sink: CC pulldowns, ESD, VBUS bulk |
   | `usb-c-data` | the same plus USB 2.0 D±, ESD and 27Ω series |
   | `ldo-3v3` | AMS1117-3.3 with in/out bulk — the 3V3 logic rail |
   | `rp2040-core` | RP2040 + QSPI flash + crystal + BOOTSEL/RESET |
   | `i2c-bus` | the one pull-up pair per bus |
   | `sensor-bme280` | temperature / humidity / pressure on I2C |
   | `status-led` | one indicator LED + series resistor |
   | `sw-tact` | one tactile button (instantiate per key) |

   **Never invent a circuit from a datasheet, and never put a block in a
   brief that isn't in the registry.** A need with no block is a `gaps`
   entry naming the part class and what authoring it would take — that
   is an honest, actionable brief; a fictional block is a board that
   cannot be built.

5. **I/O, controls, and the enclosure interface.** The board is half of
   a product; the other half is a **Vibe 3D-printed body**, so the brief
   carries the mechanical contract:
   - `mounting`: hole pattern — count, thread class (M2 / M2.5),
     hole diameter, inset from the edge, and the rectangle they form
     (a 4-hole rectangle inset 3.5mm from the corners is the default).
   - `connector_edge`: which edge carries the USB-C (and whether the
     receptacle sits flush with, or proud of, the board edge) — the
     enclosure needs one opening, on one named edge.
   - `outline_class`: `rect-rounded` (default, 2mm corner radius),
     `rect`, or `custom` — plus the target `envelope_mm` (max
     width × height; the pipeline treats exceeding it as a blocking
     warning).
   - components that must reach the outside world: buttons, the LED,
     the sensor's vent.
   Give the human-facing I/O too: how many buttons, what the LED means,
   which pins are exposed on a header.

6. **Cost band.** From `circuitlib.tables` cost members
   (`ASSEMBLY_SETUP_USD`, `STENCIL_USD`, `SMT_JOINT_USD`,
   `EXTENDED_PART_FEE_USD`, `SHIPPING_SLOW_USD`, `LEAD_TIME_DAYS`) —
   the standing reference points are **5× bare 2-layer PCB ≈ $4–20
   all-in** and **5× assembled ESP32-class ≈ $75–110, 1–2 weeks**
   (contract §1). Two things to call out because they move the number
   more than anything else: **every non-Basic (extended) BOM line adds
   ~$3**, and unit price falls off a cliff below 5 boards. Any figure
   you compute rather than read from a table is an `"estimate"`.

7. **Emit one brief** in the format below, add a 2–3 sentence summary,
   and **stop.** Do not create a project, write any `.tsx`, run the
   generator, or look up parts.

## Output format

Return exactly one fenced ```circuit-brief block containing the enriched
spec as JSON, followed by a 2–3 sentence plain-language summary.

```circuit-brief
{
  "product": {
    "name": "desk-air-monitor",
    "class": "sensor-node",
    "description": "USB-C powered desk air quality monitor with a one-button display toggle"
  },
  "safety": { "verdict": "accept", "reason": "USB-C 5V only; no mains, no battery, certified radio module" },
  "power": {
    "story": "usb-c-5v",
    "rails": ["V5", "V3_3"],
    "blocks": ["usb-c-power", "ldo-3v3"],
    "budget_ma": { "value": 320, "basis": "estimate — ESP32-S3 Wi-Fi TX peak plus sensor idle" }
  },
  "brain": {
    "block": "esp32-s3-core",
    "why": "reports to a phone over Wi-Fi",
    "released": false,
    "fallback": "rp2040-core (USB-attached only) until the ESP32-S3 block lands"
  },
  "blocks": [
    { "id": "usb-c-power", "role": "5V in" },
    { "id": "ldo-3v3",     "role": "logic rail" },
    { "id": "i2c-bus",     "role": "one pull-up pair for the sensor bus" },
    { "id": "sensor-bme280", "role": "temperature / humidity / pressure" },
    { "id": "status-led",  "role": "power + alert indicator" },
    { "id": "sw-tact",     "role": "mode button", "count": 1 }
  ],
  "io": {
    "controls": [{ "name": "mode", "block": "sw-tact", "net": "BTN1" }],
    "indicators": [{ "name": "status", "block": "status-led", "color": "green" }],
    "headers": [{ "name": "expansion", "pins": ["V3_3", "GND", "SDA", "SCL"] }]
  },
  "enclosure": {
    "outline_class": "rect-rounded",
    "corner_radius_mm": 2.0,
    "envelope_mm": [60, 40],
    "mounting": { "holes": 4, "thread": "M2.5", "hole_dia_mm": 2.7,
                  "inset_mm": 3.5, "pattern": "corner rectangle 53 x 33" },
    "connector_edge": "south, USB-C receptacle flush with the board edge",
    "must_reach_outside": ["USB-C", "mode button", "status LED", "sensor vent"]
  },
  "size_class": "credit-card (60 x 40 mm), 2 layers, 1.6mm",
  "cost_band": {
    "qty": 5,
    "usd": [75, 110],
    "basis": "estimate — assembled ESP32-class reference band (contract §1); extended BOM lines add ~$3 each",
    "lead_time_days": [7, 14]
  },
  "gaps": [
    { "need": "ESP32-S3 core", "status": "block not authored yet",
      "unblocks": "author esp32-s3-core (module C2913206, EN RC, strap pins)" }
  ],
  "assumptions": [
    "desk device, always plugged in — no battery",
    "one bus, one sensor; 0x76 address (frozen in the block)"
  ]
}
```

Then: a short summary the user can approve or redirect, e.g. *"A USB-C
desk air monitor on a 60×40mm two-layer board: USB-C 5V into a 3V3 LDO,
BME280 on a single I2C bus, one button and one status LED, four M2.5
corner holes for a printed body. Roughly $75–110 for five assembled,
1–2 weeks — but the ESP32-S3 block isn't authored yet, so this is
RP2040-and-USB until it lands. Say the word and I'll build the
project."*

## Handoff

This skill is standalone: it produces a brief, not a project. When the
user approves, hand the brief to the **`circuitcode`** skill as the
design input:

| brief key | lands as |
|---|---|
| `product.name` / `.description` | `product.json` name + description |
| `power.story` | `product.json` `power` |
| `enclosure.envelope_mm`, `outline_class` | `product.json` `envelopeMm`, the board outline |
| `blocks[]` | the composition in `boards/main.tsx` |
| `io` | connectors, buttons, LEDs, and the header in the source |
| `cost_band` | the number the fab packet's ORDER.md is checked against |

The pinned parts behind those blocks are then locked by the
**`parts-book`** skill into `parts.json` (it owns that file wholly) —
run it once before a fab export, never inside the build loop.

## Guidance

- **Default to the released block, not the ideal one.** A brief that
  names an unauthored block reads as a plan and behaves as a dead end;
  put the ideal in `gaps` and build the brief on what exists.
- **Never invent an electrical number from memory.** Rail voltages,
  trace widths, clearances, decoupling values, DFM limits, and cost
  members are `circuitlib.tables` values. Anything you derive yourself
  is marked `"estimate"` with its basis in one clause.
- **Never invent a part number.** Parts belong to the blocks (their
  `BLOCK.md` carries the pinned LCSC numbers) and to `parts-book`. A
  brief names blocks and part *classes*, never a bare C-number you
  recalled.
- One power story, one brain, one bus per sensor family. Keep briefs
  tight — the handful of decisions that most change whether the board
  is buildable.
- State every assumption in `assumptions` so the user can correct the
  whole brief in one message.
