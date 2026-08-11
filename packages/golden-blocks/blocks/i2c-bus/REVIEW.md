# Block sign-off — `i2c-bus`

**This block goes into every board a user generates that needs it**, unchanged —
the AI composes blocks, it never edits them. So an error here is not one bad
board, it is a bad board every time. It is also the specific class of error our
automated checks provably cannot catch, which is why this sheet matters more
than any individual board in the packet. Anything you find gets fixed once and
is then right forever.

Source: [`i2c-bus.tsx`](./i2c-bus.tsx) · Datasheet: [`BLOCK.md`](./BLOCK.md)

## Check these against the part datasheets, not against our documentation

Our `BLOCK.md` and our source can be wrong in the same way at the same time —
they were written together. Please check against the manufacturer's datasheet
and the LCSC listing.

| # | Question | Verdict |
|---|---|---|
| 1 | Is **every component value** correct for this circuit — not merely plausible? | pass / **fail** |
| 2 | Is **every polarity** right? Diodes, electrolytics, ICs. | pass / **fail** |
| 3 | Does **every pin number** match the datasheet's pinout, in the datasheet's own numbering? | pass / **fail** |
| 4 | Is the **land pattern** right for the package actually ordered (IPC density, thermal pad, paste)? | pass / **fail** |
| 5 | Is each **LCSC part** the right part — and a sane choice for cost, stock and lifecycle? | pass / **fail** |
| 6 | Is the **decoupling** adequate in value, count and placement? | pass / **fail** |
| 7 | Does the block behave at its **stated limits** — the rail budget and current draw in `BLOCK.md`? | pass / **fail** |
| 8 | What does this block do that is **wrong at the edges** — brown-out, inrush, hot-plug, ESD, thermal? | notes |

## Anything you would have done differently

Not a defect, but worth recording — if it is a real preference we should encode
it as a default, because a user will never know to ask for it.

```
```

## Verdict

- [ ] **Approved** — safe to compose into user boards as-is
- [ ] **Approved with changes** — listed above, must land before release
- [ ] **Rejected** — do not release with this block in the catalog

Reviewer: ______________________  Date: ____________

---

## The block's own datasheet, for reference

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

