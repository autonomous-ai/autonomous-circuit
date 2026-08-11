"""The golden-block registry — the one doctrine that makes this safe.

**Compose validated blocks. Never invent a circuit from a datasheet.**

No deterministic check catches a wrong resistor value, a mirrored pinout, or
swapped SDA/SCL — every representation agrees because they all inherit the
same wrong source (``docs/circuit-research-2026-08-10.md``, "What no
deterministic check catches"). Blocks eliminate that class: values, polarities,
pinouts and land patterns are frozen inside a block that a human verified once.
The AI composes blocks and routes between them; the gauntlet deterministically
verifies everything composition can break.

Entries here mirror ``packages/golden-blocks/blocks/<id>/`` — the .tsx source
is ground truth, this registry is the index the planner reasons over. Each
block also ships ``BLOCK.md`` (pin contract, rail budget, pinned parts with
verification date, provenance) and a graded testbench.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Part:
    """One pinned, orderable part. ``lcsc`` is the exact C-number."""

    refdes: str
    lcsc: str
    description: str
    basic: bool = False


@dataclass(frozen=True)
class Block:
    """A validated subcircuit. ``symbol`` is the default export to import."""

    id: str
    symbol: str
    function: str
    provides: tuple[str, ...] = ()      # nets/capabilities this block offers
    requires: tuple[str, ...] = ()      # nets it must be fed
    props: tuple[str, ...] = ()         # the props a composer may set
    parts: tuple[Part, ...] = ()
    current_draw_ma: float = 0.0        # typical, for the power budget
    status: str = "compile-verified"    # never claim hardware-verified here
    notes: str = ""

    @property
    def import_path(self) -> str:
        return f"blocks/{self.id}/{self.id}"


BLOCKS: dict[str, Block] = {
    "usb-c-power": Block(
        id="usb-c-power",
        symbol="UsbCPower",
        function="USB-C 5V power entry (sink, 5.1k CC, ESD, bulk cap)",
        provides=("V5", "GND"),
        props=("j", "vbusNet", "pcbX", "pcbY", "schX", "schY"),
        parts=(
            Part("J1", "C165948", "TYPE-C-31-M-12 receptacle"),
            Part("R1/R2", "C25905", "5.1k 0402 CC pulldown", basic=True),
            Part("U1", "C2687116", "USBLC6-2SC6 ESD"),
            Part("C1", "C15850", "10uF 0805 VBUS bulk", basic=True),
        ),
        notes="Power only. Never place alongside usb-c-data — that block is a superset.",
    ),
    "usb-c-data": Block(
        id="usb-c-data",
        symbol="UsbCData",
        function="USB-C power + D+/D- data pair with 27.4R series resistors",
        provides=("V5", "GND", "USB_DP", "USB_DM"),
        props=("j", "vbusNet", "dpNet", "dmNet", "pcbX", "pcbY", "schX", "schY"),
        parts=(
            Part("J1", "C165948", "TYPE-C-31-M-12 receptacle"),
            Part("R1/R2", "C25905", "5.1k 0402 CC pulldown", basic=True),
            Part("RDP/RDM", "C25100", "27.4R 0402 USB series", basic=True),
            Part("U1", "C2687116", "USBLC6-2SC6 ESD"),
            Part("C1", "C15850", "10uF 0805 VBUS bulk", basic=True),
        ),
        notes="Superset of usb-c-power; use when the MCU speaks USB.",
    ),
    "ldo-3v3": Block(
        id="ldo-3v3",
        symbol="Ldo3v3",
        function="5V -> 3.3V linear rail (AMS1117-3.3 + in/out bulk)",
        provides=("V3_3",),
        requires=("V5", "GND"),
        props=("u", "cin", "cout", "vinNet", "voutNet", "pcbX", "pcbY", "schX", "schY"),
        parts=(
            Part("U2", "C6186", "AMS1117-3.3 SOT-223", basic=True),
            Part("C2/C3", "C15850", "10uF 0805", basic=True),
        ),
        notes="Linear: dropout heat = (Vin-3.3) * I. Above ~500mA prefer a buck (not yet a block).",
    ),
    "i2c-bus": Block(
        id="i2c-bus",
        symbol="I2cBus",
        function="I2C pull-ups (4.7k to the logic rail) — place once per bus",
        provides=("I2C_SDA", "I2C_SCL"),
        requires=("V3_3", "GND"),
        props=("rSda", "rScl", "sdaNet", "sclNet", "rail", "pcbX", "pcbY", "schX", "schY"),
        parts=(Part("R3/R4", "C25900", "4.7k 0402", basic=True),),
        notes="Exactly one instance per bus. Two instances halve the pull-up resistance.",
    ),
    "status-led": Block(
        id="status-led",
        symbol="StatusLed",
        function="Indicator LED + series resistor to a rail",
        requires=("GND",),
        props=("led", "r", "rail", "pcbX", "pcbY", "schX", "schY"),
        parts=(
            Part("D1", "C965793", "0402 LED"),
            Part("R5", "C25104", "1k 0402", basic=True),
        ),
        current_draw_ma=2.0,
    ),
    "sw-tact": Block(
        id="sw-tact",
        symbol="SwTact",
        function="Tactile push button to a signal net (active low)",
        requires=("GND",),
        props=("name", "signal", "to", "pcbX", "pcbY", "schX", "schY"),
        parts=(Part("SW1", "C318884", "TS-1187A-B-A-B tactile", basic=True),),
        notes="Parametric: instantiate one per button. Pull-up is the MCU's internal one.",
    ),
    "rp2040-core": Block(
        id="rp2040-core",
        symbol="Rp2040Core",
        function="RP2040 minimal core: QSPI flash, crystal, decoupling, BOOTSEL",
        provides=("MCU",),
        requires=("V3_3", "GND", "USB_DP", "USB_DM"),
        props=("u", "flash", "xtal", "pcbX", "pcbY", "schX", "schY"),
        parts=(
            Part("U3", "C2040", "RP2040 QFN-56"),
            Part("U4", "C97521", "W25Q128JVSIQ QSPI flash", basic=True),
            Part("Y1", "C20625731", "ABM8-272-T3 12MHz crystal"),
        ),
        current_draw_ma=40.0,
        notes="Ported from seveibar/rp2040-module, itself following the RPi hardware design guide.",
    ),
    "sensor-bme280": Block(
        id="sensor-bme280",
        symbol="SensorBme280",
        function="BME280 temperature/humidity/pressure sensor on I2C",
        requires=("V3_3", "GND", "I2C_SDA", "I2C_SCL"),
        props=("u", "cVdd", "cVddio", "sdaNet", "sclNet", "rail", "pcbX", "pcbY", "schX", "schY"),
        parts=(Part("U5", "C92489", "BME280 LGA-8"),),
        current_draw_ma=0.7,
        notes="Needs an i2c-bus block for pull-ups. Address strap on SDO.",
    ),
    "ws2812-chain": Block(
        id="ws2812-chain",
        symbol="Ws2812Chain",
        function="Chain of WS2812B addressable RGB pixels on one GPIO",
        provides=("LED_DATA",),
        requires=("V3_3", "GND"),
        props=("count", "dinNet", "rail", "startIndex", "pitch", "r",
               "pcbX", "pcbY", "schX", "schY"),
        parts=(
            Part("D10+", "C2761795", "WS2812B-B/T 5050 pixel"),
            Part("C40+", "C1525", "100nF 0402, one per pixel", basic=True),
        ),
        # Idle. Worst case is ~60mA/pixel at full white — budget
        # count x 60mA with helpers.power_budget(), see BLOCK.md.
        current_draw_ma=4.0,
        notes=(
            "Parametric in `count` (default 4). Worst-case draw is "
            "count x 60mA at full white, which is what the power budget must "
            "carry — the figure here is idle. The last pixel's DOUT is "
            "deliberately unconnected so the chain can be extended."
        ),
    ),
}

#: Blocks that may not be composed without an explicit human hardware sign-off.
#: Battery charging is a fire risk; the envelope permits it only as a sealed,
#: validated block (see safety.py). None exist yet — that is deliberate.
GATED_BLOCKS: dict[str, str] = {
    "lipo-tp4056": "battery charge/protect — requires hardware sign-off before first use",
}

#: Capability -> block ids that satisfy it, best first. The planner's index.
CAPABILITY_INDEX: dict[str, tuple[str, ...]] = {
    "power-usb": ("usb-c-power", "usb-c-data"),
    "rail-3v3": ("ldo-3v3",),
    "mcu": ("rp2040-core",),
    "usb-data": ("usb-c-data",),
    "i2c": ("i2c-bus",),
    "sensor-environment": ("sensor-bme280",),
    "button": ("sw-tact",),
    "indicator": ("status-led",),
    "rgb-pixels": ("ws2812-chain",),
}


def block_for(capability: str) -> Block | None:
    """Best block for a capability, or None when we have not built one yet.

    Returning None is a real answer: it means the ask is out of catalog and the
    honest move is to say so, not to invent the circuit.
    """
    ids = CAPABILITY_INDEX.get(capability, ())
    return BLOCKS[ids[0]] if ids else None


def missing_requirements(block_ids: list[str]) -> list[str]:
    """Nets required by the chosen blocks that nothing in the set provides."""
    provided: set[str] = {"GND"}
    required: set[str] = set()
    for bid in block_ids:
        block = BLOCKS.get(bid)
        if block is None:
            continue
        provided.update(block.provides)
        required.update(block.requires)
    return sorted(required - provided)


def total_current_ma(block_ids: list[str]) -> float:
    return sum(BLOCKS[b].current_draw_ma for b in block_ids if b in BLOCKS)
