# The fleet

Boards built **with the app**, by the AI electrical engineers who are testing
it. `examples/` holds the three the test suite pins; this holds everything
after that, and it exists for one reason:

> Three boards prove the pipeline can build a board. Tens of boards prove the
> tool is ready for someone else's hands.

Every product here is a real device someone might order — not a demo, not a
skeleton — and every one carries the same verdict the first three do.

## The bar

A product is **done** when its sidecar says `"fab": {"ready": true}` with zero
blocking findings, and the fab packet under `boards/main_fab/` is complete
(gerbers, BOM, CPL, ORDER.md). Anything short of that stays in the table below
with its real state, because a fleet that only lists its successes measures
nothing.

`fab.ready: true` is a **floor**. A later build may never report `false` after
a `true`; the change that did it gets reverted and the finding recorded.

## How one gets built

```bash
SKILL=~/.claude/skills/circuitcode
mkdir -p products/<slug>
cp -R "$SKILL/templates/project_skeleton/." products/<slug>/
cp -R "$SKILL/blocks" products/<slug>/blocks
# edit products/<slug>/product.json first — name, power, envelopeMm, layers
# then write products/<slug>/boards/main.tsx from the golden blocks
/Users/d/miniconda/bin/python3.12 "$SKILL/scripts/circuit" \
    /Users/d/code/autonomous-circuit/products/<slug>/boards/main.tsx
```

Read the verdict off the last line of stdout (one JSON object) and then off
`boards/main.board.json` → `validation.warnings[]` by severity. **Look at both
review PNGs** before calling anything finished.

The golden blocks are the vocabulary: `usb-c-power`, `usb-c-data`, `ldo-3v3`,
`rp2040-core`, `status-led`, `sw-tact`, `ws2812-chain`, `sensor-bme280`,
`i2c-bus`, plus `glue.tsx` (mounting holes, pours). A product that needs
something outside that list is a **finding about the library**, not a reason to
invent a circuit inline.

## What the builders owe us besides the board

Every engineer who builds one writes down where the tool got in their way, in
`work/ee-feedback/<slug>.md`: what they reached for that was not there, what
they had to do by hand, what the app told them that turned out to be wrong.
That file is the input to the next round of IDE work — the boards are the
product, the friction is the point.

## The fleet

| Product | What it is | `fab.ready` | Blocking | Notes |
|---|---|---|---|---|
| `desk-air-monitor` | USB-C desk device that reads temperature, humidity and pressure and sh | **yes** | 0 | 61 × 64.8mm |
| `dual-rail-psu` | USB-C bench supply brick: 5V and 3V3 broken out on pad headers with a power LED per rail, so a laptop charger can feed a breadboard without a bench PSU. No MCU. | **yes** | 0 | 41.1 × 51mm |
| `dual-sensor-node` | USB-C RP2040 node with two BME280s on one I2C bus (on-board + short pi | **yes** | 0 | 95.3 × 68mm |
| `env-logger-usb` | USB-C environment logger streaming temperature, humidity and pressure over USB serial, with power/logging/error LEDs, a start-stop button and a bench I2C+SWD breakout | **yes** | 0 | 99.6 × 64mm |
| `i2c-sensor-hub` | USB-C board that reads a BME280 and breaks its I2C bus out to a pad he | **yes** | 0 | 95 × 67mm |
| `macropad-6` | USB-C six-key macropad: 3x2 grid of tactile switches on 19 | no | 14 | 66 × 84mm · 7× drc_violation, 3× pcb_via_trace_clearance_error |
| `pixel-badge` | a wearable conference badge: 8 addressable RGB pixels across the top,  | no | 10 | 82 × 58mm · 7× drc_violation, 1× pcb_trace_error |
| `rgb-lamp-controller` | USB-C desk lamp controller: RP2040 brain driving 8 on-board WS2812 pix | **yes** | 0 | 84.9 × 68.6mm |
| `sensor-node-mini` | Small always-on environment node: temperature, humidity and pressure o | **yes** | 0 | 80.9 × 45.9mm |
| `two-key-footswitch` | USB-C RP2040 footswitch with two heavy-press keys for push-to-talk and | no | 3 | 47.5 × 68.9mm · 2× drc_violation, 1× dfm_hole_clearance |
| `usb-c-breakout` | USB-C receptacle broken out to labelled bench pads (VBUS, GND, CC1, CC | **yes** | 0 | 36 × 40.5mm |

**8 of 11 built boards are fab-ready** (11 products in the fleet).
