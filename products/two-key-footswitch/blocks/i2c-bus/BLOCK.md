# i2c-bus — the one pull-up pair per I2C bus

**Function:** bus termination. Two 4.7kΩ pull-ups from SDA and SCL to the logic
rail. Place **exactly one** i2c-bus block per bus no matter how many sensors
share it — a second block halves the pull-up and is the classic reason a bus
stops acking.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.SDA` (default; `sdaNet` prop overrides) | I2C data, pulled up |
| `net.SCL` (default; `sclNet` prop overrides) | I2C clock, pulled up |
| `net.V3_3` (default; `rail` prop overrides) | the rail the pull-ups sit on |

No ground connection — the block only touches the rail and the two bus lines.
The devices on the bus (e.g. `sensor-bme280`) drive the same net names.

## Rail budget

Static draw is zero; each line sinks `V_rail / 4.7k` while a device holds it
low — **≈0.7mA per line at 3V3** (derived from the rail and the resistor,
worst case both lines low ≈1.4mA). 4.7k is the standing value for 100/400kHz
at 3V3 with a handful of devices and is owned by `circuitlib.tables`; a long
bus or many devices wants a lower value, which is a block change, not a board
change.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| R8 | 0402WGF4701TCE, 4.7kΩ ±1% | C25900 | 0402 | yes | $0.0005, 9.38M stock — SDA pull-up |
| R9 | 0402WGF4701TCE, 4.7kΩ ±1% | C25900 | 0402 | yes | $0.0005, 9.38M stock — SCL pull-up |

## Design-rule notes

- Exactly one instance per bus. Two blocks on one bus = 2.35k effective, which
  exceeds most sensors' sink rating at 3V3.
- `rail` must be the same rail the bus devices run on — a 3V3 pull-up on a bus
  shared with a 5V device backfeeds through the device's ESD diodes.
- Both resistors are on the rail side (`pin1`), bus side on `pin2`; the
  testbench asserts SDA and SCL stay isolated from each other.
- Default refdes R8/R9 are the global v1 allocation; override `rSda`/`rScl`
  when a board carries a second bus.

## Provenance

- Value and bus rules from `circuitlib.tables` (the electrical-law owner); no
  registry package was used — the registry survey (r5, 2026-08-10) found only
  auto-generated single-part wrappers.
- Land pattern: footprinter builtin `0402` (no imported footprint needed).
