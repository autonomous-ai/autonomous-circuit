"""How much current each part actually pulls, and where the number came from.

**Why this file exists.** A trace-width check is only as honest as its current
estimate. The temptation is to invent a plausible number per net; the result is
a gate that fires on nothing real and hides the one board that would have
cooked. So every entry below is a datasheet or capability-table figure with its
source named, and a part that is not in the table contributes **unknown**, not
zero — the caller reports unknown loads as coverage rather than pretending the
net was checked.

Two currents matter and they are different:

* ``typical_ma`` — what the part draws in steady operation. Sizing to this is
  how boards brown out on a radio transmit burst.
* ``peak_ma`` — the worst case the rail must survive. This is the number a
  power trace is sized against, because copper heats on peaks.

Matching order: exact LCSC number, then manufacturer part number (substring,
case-insensitive), then ``ftype``. LCSC first because it is the only identifier
that is unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Load:
    typical_ma: float
    peak_ma: float
    source: str


#: Exact LCSC part numbers we stock. Verified 2026-08-11 against the linked
#: datasheets; the LCSC numbers themselves are the ones pinned in
#: docs/circuit-research-2026-08-10.md.
BY_LCSC: dict[str, Load] = {
    # WS2812B-B/T — three 20mA channels, full white is all three at once.
    "C2761795": Load(20.0, 60.0, "WS2812B datasheet: 3 x 20mA channels"),
    "C965555": Load(20.0, 60.0, "WS2812B-2020 datasheet: 3 x 20mA channels"),
    "C5149201": Load(12.0, 36.0, "SK6812MINI-E datasheet: 3 x 12mA channels"),
    # RP2040 — core plus IO at 133MHz, no peripherals.
    "C2040": Load(30.0, 100.0, "RP2040 datasheet 5.2: ~30mA typical, 100mA peak"),
    # W25Q128 QSPI flash during a read burst.
    "C97521": Load(4.0, 25.0, "W25Q128JV datasheet: 4mA read, 25mA program"),
    # AMS1117 quiescent only; its *output* current is accounted on the rail
    # it feeds, not here, or it would be double counted.
    "C6186": Load(5.0, 11.0, "AMS1117 datasheet: 5mA quiescent, 11mA max"),
    # USBLC6 ESD array — leakage only.
    "C2687116": Load(0.0, 0.001, "USBLC6-2SC6 datasheet: 1uA leakage"),
    "C7519": Load(0.0, 0.001, "USBLC6-2SC6 datasheet: 1uA leakage"),
    # Environmental sensors: all sub-milliamp in duty-cycled use.
    "C92489": Load(0.4, 0.7, "BME280 datasheet: 340uA typical measuring"),
    "C2757850": Load(0.3, 0.98, "AHT20 datasheet: 980uA measuring"),
    "C2909890": Load(0.3, 0.5, "SHT40 datasheet: 320uA measuring"),
    "C1850416": Load(0.05, 0.1, "VEML7700 datasheet"),
    "C2874215": Load(2.6, 3.0, "SGP40 datasheet: 2.6mA continuous heater"),
    # ESP32 modules — the transmit burst is the number that matters.
    "C2913206": Load(80.0, 500.0, "ESP32-S3-MINI-1 datasheet: 500mA peak TX"),
    "C2913201": Load(80.0, 500.0, "ESP32-S3-WROOM-1 datasheet: 500mA peak TX"),
    # Motor driver: its load is the motor, which is off-board and unknowable.
    "C50506": Load(0.0, 0.0, "DRV8833 quiescent only; motor load is off-board"),
}

#: Manufacturer part numbers, matched as a lowercase substring. Covers the
#: same parts when the supplier column is empty.
BY_MPN: tuple[tuple[str, Load], ...] = (
    ("ws2812", Load(20.0, 60.0, "WS2812B datasheet: 3 x 20mA channels")),
    ("sk6812", Load(12.0, 36.0, "SK6812 datasheet: 3 x 12mA channels")),
    ("rp2040", Load(30.0, 100.0, "RP2040 datasheet 5.2")),
    ("w25q", Load(4.0, 25.0, "W25Q series datasheet: 25mA program")),
    ("ams1117", Load(5.0, 11.0, "AMS1117 datasheet quiescent")),
    ("usblc6", Load(0.0, 0.001, "USBLC6 datasheet leakage")),
    ("esp32", Load(80.0, 500.0, "ESP32 module datasheet: 500mA peak TX")),
    ("bme280", Load(0.4, 0.7, "BME280 datasheet")),
    ("aht20", Load(0.3, 0.98, "AHT20 datasheet")),
    ("drv8833", Load(0.0, 0.0, "DRV8833 quiescent; motor load off-board")),
)

#: Whole component classes that draw nothing worth counting on a rail. A
#: resistor's current is computed from Ohm's law where it matters (see
#: ``dc.py``), not looked up.
PASSIVE_FTYPES = {
    "simple_capacitor",
    "simple_resistor",
    "simple_inductor",
    "simple_crystal",
    "simple_diode",
    "simple_push_button",
    "simple_switch",
    "simple_connector",
    "simple_test_point",
    "simple_fuse",
}

#: An indicator LED's current is set by its series resistor, so it is computed
#: rather than tabled. This is the fallback when no resistor can be found.
LED_FALLBACK = Load(5.0, 20.0, "indicator LED, resistor-limited, 20mA maximum")


def lookup(*, lcsc: str | None, mpn: str | None, ftype: str | None) -> Load | None:
    """The load for one part, or ``None`` when we genuinely do not know.

    ``None`` is a real answer and the caller must treat it as such. Returning
    zero for an unknown part is how a 500mA radio module ends up sized as if it
    were a resistor.
    """
    if lcsc:
        hit = BY_LCSC.get(lcsc.strip().upper())
        if hit is not None:
            return hit
    if mpn:
        needle = mpn.strip().lower()
        for key, load in BY_MPN:
            if key in needle:
                return load
    if ftype in PASSIVE_FTYPES:
        return Load(0.0, 0.0, f"{ftype} draws no rail current of its own")
    if ftype == "simple_led":
        return LED_FALLBACK
    return None
