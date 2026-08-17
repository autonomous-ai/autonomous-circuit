# sensor-bme280 — temperature / humidity / pressure on I2C

**Function:** sensing. A Bosch BME280 wired for I2C: CSB tied to VDDIO (selects
I2C over SPI), SDO tied to GND (address **0x76**), 100nF on each of VDD and
VDDIO. Both frozen choices live inside the block — mode and address are exactly
the kind of thing a deterministic check cannot catch.

**Status:** v1. Compile-verified against tscircuit@0.0.2279; not yet
hardware-verified (first article pending).

## Pin contract (what the block exposes)

| Net | Meaning |
|---|---|
| `net.V3_3` (default; `rail` prop overrides) | sensor supply (VDD and VDDIO) |
| `net.GND` | ground (both GND pads, plus SDO for the 0x76 address) |
| `net.SDA` (default; `sdaNet` prop overrides) | I2C data |
| `net.SCL` (default; `sclNet` prop overrides) | I2C clock |

**Requires exactly one `i2c-bus` block on the same SDA/SCL nets** — this block
ships no pull-ups.

## Rail budget

Sub-milliamp. The BME280's measurement current is in the µA range at typical
1Hz sampling (datasheet class figure — **not re-verified today**); the 100nF
decoupling pair is what actually matters, not the supply budget. Treat the
sensor as negligible against the MCU's draw when sizing `ldo-3v3`.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| U5 | BME280 | C92489 | LGA-8 (2.5×2.5) | no | $2.86, 8.5k stock — extended, ~$3 loading fee |
| C18 | CL05B104KO5NNNC, 100nF X7R | C1525 | 0402 | yes | $0.001 — VDD decoupling |
| C19 | CL05B104KO5NNNC, 100nF X7R | C1525 | 0402 | yes | $0.001 — VDDIO decoupling |

The BME280 is the block's cost driver and the only extended line: **$2.86 plus
the ~$3 one-off loading fee** dominates a small board's BOM. Cheaper siblings
(AHT20 C2757850, SHT40 C2909890) are separate blocks, not props — a different
sensor is a different pin contract.

## Design-rule notes

- **Address is 0x76**, fixed by SDO→GND. Two BME280s on one bus need the second
  one's SDO at VDDIO (0x77), which is a block variant, not a board-level trace.
- CSB must stay at VDDIO. A floating CSB drops the part into SPI mode and the
  bus goes quiet — this is the single most common BME280 bring-up failure.
- Both decoupling caps sit adjacent to their pin in layout; the netlist cannot
  express "close to", so the craft pass on `_pcb.png` is where that is checked.
- Humidity and pressure both need the sensor to see outside air: the enclosure
  brief must carry a vent over U5, and the part should not sit next to the LDO
  or the MCU (self-heating skews the temperature reading by whole degrees).
- Default refdes U5/C18/C19 are the global v1 allocation.

## Provenance

- Land pattern: footprinter
  `lga8_grid4x0_pillpads_p0.65mm_w3.2841mm_pw0.364mm_pl0.767mm` — matched at
  100% copper IoU against the EasyEDA pattern (`tscircuit-cli import C92489
  --jlcpcb`, 2026-08-10), so the builtin was kept.
- Pin map and the CSB/SDO mode+address strapping from the Bosch BME280
  datasheet; `@tsci/nubzzz.BME280` (a bare C92489 wrapper, 0 stars) was read as
  a reference only — no registry package is imported (r5 §5).
