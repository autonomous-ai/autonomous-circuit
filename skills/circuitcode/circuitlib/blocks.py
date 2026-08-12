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
import re


@dataclass(frozen=True)
class Part:
    """One pinned, orderable part. ``lcsc`` is the exact C-number."""

    refdes: str
    lcsc: str
    description: str
    basic: bool = False


@dataclass(frozen=True)
class UsbSourceContract:
    """Exact product fields owned by a protected USB source-entry block."""

    raw_net: str
    protected_net: str
    raw_attach_capacitance_max_uf: float
    source_current_max_ma: float
    limiter_ref: str
    limiter_lcsc: str
    input_pin: str
    output_pin: str
    setting_pin: str
    setting_resistor_ref: str
    setting_resistor_lcsc: str
    setting_resistance_ohms: float
    setting_return_net: str
    min_trip_ma: float
    max_trip_ma: float
    # Conservative fixed board-load declaration. The artifact verifier treats
    # this as a hard lower bound; it is not recomputed from optimistic typical
    # current when a selected regulator's quiescent current improves.
    fixed_operational_load_floor_ma: float = 0.0


@dataclass(frozen=True)
class RegulatorContract:
    """Audited regulator profile selected by a golden physical block.

    Product JSON carries only the instance/rail/cap references and ambient.
    Independent verifier code owns the electrical and thermal constants, but
    the planner still records those reviewed limits here so selection and
    budget arithmetic cannot retain an obsolete regulator's semantics.
    """

    profile: str
    ref: str
    input_net: str
    output_net: str
    input_cap_ref: str
    output_cap_ref: str
    max_ambient_c: float
    max_input_volts: float
    output_volts: float
    max_continuous_output_ma: float
    max_ground_current_ma: float
    theta_ja_c_per_w: float
    design_max_junction_c: float
    min_thermal_headroom_c: float


@dataclass(frozen=True)
class AttachmentPort:
    """A proven board-composition attachment in block-local coordinates.

    The selector is the public copper endpoint exposed by a typed block opt-
    out.  Keeping its measured datum beside the registry entry lets starter
    composition derive authored board trees from planner placements instead
    of copying one product's global coordinates.
    """

    role: str
    selector: str
    local_x_mm: float
    local_y_mm: float
    layer: str = "top"


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
    #: Worst case the rail and the regulator must survive. Sizing copper and
    #: heat against the *typical* number is sizing for the easy case: a WS2812
    #: idles at 4mA and pulls 60mA at full white, fifteen times more.
    # ``None`` means "use current_draw_ma as the non-parametric peak". Zero
    # is meaningful for a purely per-unit load: an eight-pixel WS2812 chain is
    # 8 x 60mA, not 4mA + 8 x 60mA. A truthiness fallback cannot express that.
    peak_draw_ma: float | None = None
    #: For a parametric block, the peak each unit adds. ``unit_prop`` names the
    #: prop that sets the count, so the planner can ask for it instead of a
    #: BLOCK.md telling a human to remember to multiply.
    peak_per_unit_ma: float = 0.0
    unit_prop: str = ""
    #: Rail that carries this block's current budget.  Thermal planning must
    #: charge a regulator only for loads on its output rail; summing the whole
    #: board made a 5V pixel ring look as though it flowed through the 3V3 LDO.
    supply_rail: str = ""
    #: Non-empty only for a regulator block.  Its downstream thermal load is
    #: the peak current of blocks whose ``supply_rail`` names this rail.
    regulator_output_rail: str = ""
    #: Non-empty only for a protected USB source-entry block. It is the one
    #: owner for the product schema, current ceiling and exact populated
    #: limiter identity; a generator must not retype those beside the block.
    usb_source_contract: UsbSourceContract | None = None
    #: Audited regulator selected by this physical block, if any.
    regulator_contract: RegulatorContract | None = None
    #: Typed external copper points used by board-owned authored trees.
    attachments: tuple[AttachmentPort, ...] = ()
    #: Nets this block owns that are useless unless they reach a connector or
    #: a test point. An MCU whose SWD pins go nowhere cannot be programmed
    #: after assembly, and every block on that board is individually fine.
    exposes: tuple[str, ...] = ()
    status: str = "compile-verified"    # never claim hardware-verified here
    notes: str = ""

    def peak_ma(self, units: int = 0) -> float:
        """Worst-case draw, including any per-unit scaling."""
        base = self.current_draw_ma if self.peak_draw_ma is None else self.peak_draw_ma
        return base + self.peak_per_unit_ma * max(units, 0)

    def attachment(self, role: str) -> AttachmentPort:
        """Return one typed external copper point or fail at composition."""

        matches = [port for port in self.attachments if port.role == role]
        if len(matches) != 1:
            raise ValueError(
                f"block {self.id!r} has {len(matches)} attachment ports for "
                f"role {role!r}"
            )
        return matches[0]

    @property
    def source_current_max_ma(self) -> float:
        return (
            self.usb_source_contract.source_current_max_ma
            if self.usb_source_contract is not None
            else 0.0
        )

    @property
    def source_operational_limit_ma(self) -> float:
        return (
            self.usb_source_contract.min_trip_ma
            if self.usb_source_contract is not None
            else 0.0
        )

    @property
    def import_path(self) -> str:
        return f"blocks/{self.id}/{self.id}"


_FIXED_REFDES = re.compile(r"^[A-Z][A-Z0-9]*$")


def parts_lock_for_blocks(block_ids: tuple[str, ...] | list[str]) -> dict[str, dict]:
    """Resolve exact fixed-ref parts for a generated composition.

    Slash-separated registry refs (``R1/R2``) are a compact declaration of
    identical fixed instances. Parametric ``D10+``-style families cannot be
    inferred without a count and therefore fail closed here instead of
    emitting a partial parts lock.
    """

    lock: dict[str, dict] = {}
    for block_id in block_ids:
        try:
            block = BLOCKS[block_id]
        except KeyError as exc:
            raise ValueError(f"unknown block in parts lock: {block_id!r}") from exc
        for part in block.parts:
            refs = part.refdes.split("/")
            if not refs or any(not _FIXED_REFDES.fullmatch(ref) for ref in refs):
                raise ValueError(
                    f"block {block_id!r} part {part.refdes!r} needs a "
                    "count-aware parts-lock resolver"
                )
            for ref in refs:
                if ref in lock:
                    raise ValueError(
                        f"duplicate generated part ref {ref!r} from block {block_id!r}"
                    )
                lock[ref] = {
                    "lcsc": part.lcsc,
                    "basic": part.basic,
                    "description": part.description,
                    "block": block_id,
                }
    return dict(sorted(lock.items()))


BLOCKS: dict[str, Block] = {
    "usb-c-power": Block(
        id="usb-c-power",
        symbol="UsbCPower",
        function="USB-C raw VBUS entry (sink, 5.1k CC, ESD, 1uF attach cap)",
        provides=("VBUS_RAW", "GND"),
        props=(
            "j", "r1", "r2", "u", "c", "vbusNet",
            "vbusBoundaryRefs", "vbusRailNodeRef",
            "localRoutingPhaseIndex", "signalTraceWidthMm", "layer",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(
            Part("J1", "C165948", "TYPE-C-31-M-12 receptacle"),
            Part("R1/R2", "C25905", "5.1k 0402 CC pulldown", basic=True),
            Part("U1", "C2687116", "USBLC6-2SC6 ESD"),
            Part("C1", "C52923", "1uF 0402 raw-attach bypass", basic=True),
        ),
        supply_rail="VBUS_RAW",
        notes=(
            "Raw connector side only; compose usb-power-entry before V5. "
            "Never place alongside usb-c-data — that block is a superset."
        ),
    ),
    "usb-c-data": Block(
        id="usb-c-data",
        symbol="UsbCData",
        function="USB-C raw VBUS + coupled D+/D- pair with 27R series resistors",
        provides=("VBUS_RAW", "GND", "USB_DP", "USB_DM"),
        props=(
            "j", "r1", "r2", "rDp", "rDm", "u", "c",
            "vbusNet", "vbusBoundaryRefs", "vbusRailNodeRef",
            "vbusClampNodeRef", "dpNet", "dmNet", "pairRules",
            "localRoutingPhaseIndex", "dpConnectorRoutingPhaseIndex",
            "dmConnectorRoutingPhaseIndex", "pairRoutingPhaseIndex",
            "connectorPairRoutingPhaseIndex", "seriesPairRoutingPhaseIndex",
            "cc1RoutingPhaseIndex", "cc2RoutingPhaseIndex",
            "powerRoutingPhaseIndex", "criticalSignalWidthMm",
            "signalTraceWidthMm", "emitMcuNetLeaves", "layer",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(
            Part("J1", "C165948", "TYPE-C-31-M-12 receptacle"),
            Part("R1/R2", "C25905", "5.1k 0402 CC pulldown", basic=True),
            Part("R3/R4", "C25100", "27R 0402 USB series", basic=True),
            Part("U1", "C2687116", "USBLC6-2SC6 ESD"),
            Part("C1", "C52923", "1uF 0402 raw-attach bypass", basic=True),
        ),
        supply_rail="VBUS_RAW",
        attachments=(
            AttachmentPort(
                "raw_vbus_boundary", ".N15 > .pin1", -2.8, 7.75
            ),
        ),
        notes=(
            "Superset of usb-c-power; use when the MCU speaks USB. Compose "
            "usb-power-entry before every downstream V5 load."
        ),
    ),
    "usb-power-entry": Block(
        id="usb-power-entry",
        symbol="UsbPowerEntry",
        function="Current-limited controlled-rise VBUS_RAW -> V5 entry",
        provides=("V5", "USB_POWER_FAULT"),
        requires=("VBUS_RAW", "V3_3", "GND"),
        props=(
            "u", "cIn", "rIlim", "rFault", "faultTestpoint",
            "rawNet", "outputNet", "faultNet", "externalPowerTrunkPort",
            "externalRawPowerTrunkPort", "externalFaultPullupPort",
            "signalTraceWidthMm", "finePitchEscapeWidthMm",
            "maxFinePitchEscapeLengthMm", "layer",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(
            Part("U7", "C55266", "TPS2553DBVR USB power-distribution switch"),
            Part("C24", "C1525", "100nF 0402 raw-input bypass", basic=True),
            Part("R31", "C32297", "59k 1% 0402 current-limit resistor"),
            Part("R32", "C25741", "100k 0402 FAULT pull-up", basic=True),
        ),
        supply_rail="VBUS_RAW",
        usb_source_contract=UsbSourceContract(
            raw_net="VBUS_RAW",
            protected_net="V5",
            raw_attach_capacitance_max_uf=10.0,
            source_current_max_ma=500.0,
            limiter_ref="U7",
            limiter_lcsc="C55266",
            input_pin="IN",
            output_pin="OUT",
            setting_pin="ILIM",
            setting_resistor_ref="R31",
            setting_resistor_lcsc="C32297",
            setting_resistance_ohms=59_000.0,
            setting_return_net="GND",
            min_trip_ma=400.6,
            max_trip_ma=500.0,
            fixed_operational_load_floor_ma=13.0,
        ),
        attachments=(
            AttachmentPort("raw_input", ".C24 > .pin1", 2.59, -0.5),
            AttachmentPort("protected_output", ".U7 > .OUT", -1.35001, -0.94996),
            AttachmentPort("fault_pullup", ".R32 > .pin2", -3.61, 0.95),
        ),
        notes=(
            "TPS2553 fixed 59k ILIM: 400.6mA minimum / 500mA maximum trip. "
            "Raw attach capacitance belongs before U7; all bulk belongs after it."
        ),
    ),
    "ldo-3v3": Block(
        id="ldo-3v3",
        symbol="Ldo3v3",
        function="Protected 5V -> 3.3V AP7361C rail with audited ceramic caps",
        provides=("V3_3",),
        requires=("V5", "GND"),
        props=(
            "u", "cin", "cout", "vinNet", "voutNet",
            "externalInputPowerTrunkPort", "externalPowerTrunkPort",
            "railWidthMm", "pinNeckdownWidthMm",
            "maxPinNeckdownLengthMm", "layer",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(
            Part("U2", "C500795", "AP7361C-33E-13 SOT-223"),
            Part("C2/C3", "C19702", "10uF X5R 10V 0603", basic=True),
        ),
        current_draw_ma=0.06,
        peak_draw_ma=0.08,  # AP7361C ground current; load is counted on V3_3
        supply_rail="V5",
        regulator_output_rail="V3_3",
        regulator_contract=RegulatorContract(
            profile="ap7361c-33e-c500795-v1",
            ref="U2",
            input_net="V5",
            output_net="V3_3",
            input_cap_ref="C2",
            output_cap_ref="C3",
            max_ambient_c=60.0,
            max_input_volts=5.25,
            output_volts=3.3,
            max_continuous_output_ma=150.0,
            max_ground_current_ma=0.08,
            theta_ja_c_per_w=110.0,
            design_max_junction_c=125.0,
            min_thermal_headroom_c=30.0,
        ),
        attachments=(
            AttachmentPort("input_cap", ".C2 > .pin1", 4.925, -2.3),
            AttachmentPort("regulated_output", ".U2 > .VOUT", 3.2, 2.3),
        ),
        notes=(
            "AP7361C-33E exact E pinout: VIN/GND1/VOUT/GND2; the broad tab is "
            "GND, never output. Manufacturer land and C19702 input/output caps "
            "are part of the 150mA, 60C ambient, >=30C-headroom profile."
        ),
    ),
    "i2c-bus": Block(
        id="i2c-bus",
        symbol="I2cBus",
        function="I2C pull-ups (4.7k to the logic rail) — place once per bus",
        provides=("I2C_SDA", "I2C_SCL"),
        requires=("V3_3", "GND"),
        props=("rSda", "rScl", "sdaNet", "sclNet", "rail", "pcbX", "pcbY", "schX", "schY"),
        parts=(Part("R3/R4", "C25900", "4.7k 0402", basic=True),),
        supply_rail="V3_3",
        notes="Exactly one instance per bus. Two instances halve the pull-up resistance.",
    ),
    "status-led": Block(
        id="status-led",
        symbol="StatusLed",
        function="Indicator LED + series resistor to a rail",
        requires=("GND",),
        props=(
            "led", "r", "rail", "driveKind", "externalRailAttachmentPort",
            "railTraceWidthMm", "signalTraceWidthMm",
            "maxRailNeckdownLengthMm", "maxSeriesTraceLengthMm", "layer",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(
            Part("LED1", "C2297", "KT-0805G green 0805 LED", basic=True),
            Part("R20", "C11702", "1k 0402 series resistor", basic=True),
        ),
        current_draw_ma=2.0,
        supply_rail="V3_3",
        attachments=(
            AttachmentPort("rail_input", ".R20 > .pin1", 0.51, 2.2, "bottom"),
        ),
    ),
    "sw-tact": Block(
        id="sw-tact",
        symbol="SwTact",
        function="Tactile push button to a signal net (active low)",
        requires=("GND",),
        props=(
            "name", "signal", "to", "signalTraceWidthMm", "variant", "layer",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(Part("SW1", "C318884", "TS-1187A-B-A-B tactile", basic=True),),
        notes="Parametric: instantiate one per button. Pull-up is the MCU's internal one.",
    ),
    "rp2040-core": Block(
        id="rp2040-core",
        symbol="Rp2040Core",
        function="RP2040 minimal core: QSPI flash, crystal, decoupling, BOOTSEL",
        provides=("MCU",),
        requires=("V3_3", "GND", "USB_DP", "USB_DM"),
        props=(
            "u", "flash", "xtal", "layer",
            "debugPortPcbX", "debugPortPcbY", "debugPortSchX", "debugPortSchY",
            "debugSwclkBoundaryRef", "debugSwdBoundaryRef", "powerRailNodeRefs",
            "debugSignalTraceWidthMm", "criticalSignalWidthMm",
            "criticalRoutingPhaseIndices", "localPowerRoutingPhaseIndex",
            "powerRoutingPhaseIndices", "controlRoutingPhaseIndex",
            "emitUsbNetLeaves", "buttonVariant",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(
            Part("U3", "C2040", "RP2040 QFN-56"),
            Part("U4", "C97521", "W25Q128JVSIQ QSPI flash", basic=True),
            Part("Y1", "C20625731", "ABM8-272-T3 12MHz crystal"),
        ),
        current_draw_ma=40.0,
        peak_draw_ma=100.0,   # RP2040 datasheet 5.2, core + IO at 133MHz
        supply_rail="V3_3",
        exposes=("SWCLK", "SWD"),
        notes="Ported from seveibar/rp2040-module, itself following the RPi hardware design guide.",
    ),
    "sensor-bme280": Block(
        id="sensor-bme280",
        symbol="SensorBme280",
        function="BME280 temperature/humidity/pressure sensor on I2C",
        requires=("V3_3", "GND", "I2C_SDA", "I2C_SCL"),
        props=(
            "u", "cVdd", "cVddio", "sdaNet", "sclNet", "rail",
            "signalTraceWidthMm", "localPowerWidthMm", "railTrunkWidthMm",
            "maxDecouplingLengthMm", "layer",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(Part("U5", "C92489", "BME280 LGA-8"),),
        current_draw_ma=0.7,
        peak_draw_ma=0.7,
        supply_rail="V3_3",
        notes=(
            "Needs an i2c-bus block for pull-ups. Address strap on SDO; CSB "
            "joins the VDDIO local tree. VDD and VDDIO each own a <=2mm "
            "pin-to-cap branch before a wide rail boundary."
        ),
    ),
    "ws2812-level-shifter": Block(
        id="ws2812-level-shifter",
        symbol="Ws2812LevelShifter",
        function="3.3V GPIO to 5V WS2812 data translation (AHCT buffer)",
        provides=("LED_DATA_5V",),
        requires=("V5", "GND"),
        props=(
            "u", "c", "inputNet", "outputNet", "signalTraceWidthMm",
            "localPowerWidthMm", "railTrunkWidthMm",
            "maxDecouplingLengthMm", "layer",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(
            Part("U6", "C7484", "SN74AHCT1G125DBVR DBV/SOT-23-5"),
            Part("C20", "C1525", "100nF 0402 local bypass", basic=True),
        ),
        current_draw_ma=0.01,
        peak_draw_ma=2.0,
        supply_rail="V5",
        notes=(
            "Always enabled (/OE hard-low). Input defaults to LED_DATA_3V3; "
            "output defaults to LED_DATA_5V and must feed the chain's 330R. "
            "C7484 uses the exact imported supplier footprint; VCC owns a "
            "<=2mm local bypass branch before its wide V5 boundary."
        ),
    ),
    "ws2812-chain": Block(
        id="ws2812-chain",
        symbol="Ws2812Chain",
        function="Chain of WS2812B addressable RGB pixels on one GPIO",
        requires=("V5", "GND", "LED_DATA_5V"),
        props=(
            "count", "dinNet", "rail", "startIndex", "pitch",
            "signalTraceWidthMm", "localPowerWidthMm", "railTrunkWidthMm",
            "maxDecouplingLengthMm", "maxRailNeckLengthMm",
            "railNodeRefs", "railRoutingPhaseIndex",
            "dataRoutingPhaseIndices", "layer", "r",
            "pcbX", "pcbY", "schX", "schY",
        ),
        parts=(
            Part("D10+", "C2761795", "WS2812B-B/T 5050 pixel"),
            Part("C40+", "C1525", "100nF 0402, one per pixel", basic=True),
        ),
        # Idle. The worst case is 60mA per pixel at full white, and it used to
        # live in a BLOCK.md sentence telling a human to remember to multiply
        # — which is advice, not a mechanism. `peak_per_unit_ma` makes the
        # planner do it: see helpers.board_plan.
        current_draw_ma=4.0,
        peak_draw_ma=0.0,
        peak_per_unit_ma=60.0,   # WS2812B datasheet: 3 x 20mA channels
        unit_prop="count",
        supply_rail="V5",
        notes=(
            "Parametric in `count` (default 4). Worst-case draw is "
            "count x 60mA at full white, which is what the power budget must "
            "carry — the figure here is idle. The last pixel's DOUT is "
            "deliberately unconnected so the chain can be extended. Every "
            "pixel owns a <=2mm local bypass branch before one count-aware "
            "V5 tree; direct data hops may receive distinct board phases."
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
    # Choose the protected boundary first. Its VBUS_RAW requirement pulls in
    # usb-c-power; if an MCU also needs USB data, normal superset collapse
    # replaces that connector with usb-c-data without duplicating J1.
    "power-usb": ("usb-power-entry",),
    "rail-3v3": ("ldo-3v3",),
    "mcu": ("rp2040-core",),
    "usb-data": ("usb-c-data",),
    "i2c": ("i2c-bus",),
    "sensor-environment": ("sensor-bme280",),
    "button": ("sw-tact",),
    "indicator": ("status-led",),
    "rgb-pixels": ("ws2812-chain",),
    "logic-level-3v3-to-5v": ("ws2812-level-shifter",),
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


def total_peak_ma(block_ids: list[str], counts: dict[str, int] | None = None) -> float:
    """Worst-case rail current for a block set.

    ``counts`` gives the unit count for parametric blocks (``{"ws2812-chain":
    8}``). A block with a ``unit_prop`` and no count is assumed to be one unit,
    which understates it — the planner reports that rather than hiding it.
    """
    counts = counts or {}
    total = 0.0
    for block_id in block_ids:
        block = BLOCKS.get(block_id)
        if block is None:
            continue
        units = counts.get(block_id, 1 if block.unit_prop else 0)
        total += block.peak_ma(units)
    return total


def peak_ma_for_rail(
    block_ids: list[str],
    rail: str,
    counts: dict[str, int] | None = None,
    supply_rail_overrides: dict[str, str] | None = None,
) -> float:
    """Worst-case load carried by one named rail.

    ``supply_rail_overrides`` models an explicit board composition that wires
    a configurable block to a non-default rail.  It exists so validation can
    reject the legacy WS2812-on-AMS1117 topology without pretending the V5
    architecture's pixel current also heats the 3V3 regulator.
    """
    counts = counts or {}
    overrides = supply_rail_overrides or {}
    total = 0.0
    for block_id in block_ids:
        block = BLOCKS.get(block_id)
        if block is None:
            continue
        supply_rail = overrides.get(block_id, block.supply_rail)
        if supply_rail != rail:
            continue
        units = counts.get(block_id, 1 if block.unit_prop else 0)
        total += block.peak_ma(units)
    return total


def unexposed_nets(block_ids: list[str], exposed: list[str] | None = None) -> list[str]:
    """Nets a chosen block owns that nothing brings out to a probe.

    This is the planner-level form of "the board cannot be programmed once it
    is assembled": every block is individually correct and the board is still
    useless, which no per-block check can see.
    """
    brought_out = {n.upper() for n in (exposed or [])}
    missing: list[str] = []
    for block_id in block_ids:
        block = BLOCKS.get(block_id)
        if block is None:
            continue
        for net in block.exposes:
            if net.upper() not in brought_out and net not in missing:
                missing.append(net)
    return missing
