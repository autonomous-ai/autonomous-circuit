"""The design-review checklist, measured instead of asked.

**The gap this closes.** Published EE review checklists are overwhelmingly
*electrical and architectural*, not geometric: a decoupling capacitor on every
IC supply pin, bulk capacitance per rail, load caps on the crystal, no floating
input, ESD on every off-board signal, a test point on every rail, a programming
header you can still reach after assembly. Our seven-lens review panel asks a
model these questions. Nothing *measures* them — and every one of them is a
plain query over ``circuit.json`` connectivity.

The distinction matters because of what these defects do. A missing decoupling
cap does not fail DRC, does not fail DFM, and does not fail the gerber check.
It produces a board that arrives, gets assembled, and behaves strangely — which
is the most expensive outcome there is, because you spend the two weeks *and*
then you have to work out why.

**What this cannot see.** Whether a specific pin needs a pull-up (that is in a
datasheet, not the netlist), whether a strap's level is the one the firmware
expects, and anything about parts it cannot identify. All reported as coverage.
"""

from __future__ import annotations

import math

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board, Component, Net

#: How far a decoupling capacitor may sit from the pin it decouples. The loop
#: inductance is what does the work, and 3mm of 0.15mm trace is already a few
#: nanohenries. Published guidance clusters at "as close as possible"; 5mm is
#: the number below which nobody argues.
#:
#: Distance is reported at **info**, not warning, and deliberately: unlike a
#: fab rule there is no published floor here, and the three example boards sit
#: at 5.7-10.2mm on parts that work. Absence of a capacitor is a defect;
#: distance is a preference, and a gate set to a preference is noise.
DECOUPLE_RADIUS_MM = 5.0
#: A rail wants at least this much bulk somewhere on it.
BULK_MIN_F = 1e-6

#: Parts that carry a supply pin without being powered by it. A USBLC6's VBUS
#: is a clamp reference, not a rail input, and asking it for a decoupling
#: capacitor is asking the wrong question.
_UNPOWERED_PART_HINTS = ("usblc", "esd", "tvs", "pesd", "srv05", "smf", "cdsod")
#: Pin names that are outputs. An unconnected output is ordinary — the last
#: WS2812 in a chain always has a spare DOUT — while an unconnected input is
#: the defect this check is for.
_OUTPUT_PIN_HINTS = ("dout", "out", "tx", "do", "so", "miso", "q", "int",
                     "clkout", "sdo")

#: Pin names that take supply current on an IC.
_POWER_PIN_HINTS = ("vdd", "vcc", "vbat", "avdd", "iovdd", "dvdd", "vddio",
                    "vin", "vbus", "vsys", "vddpll", "vdda", "usb_vdd")
#: Parts that exist to clamp an off-board signal.
_ESD_HINTS = ("usblc", "esd", "tvs", "pesd", "srv05", "smf", "cdsod")
#: Nets that carry a debug interface.
_DEBUG_NET_HINTS = ("swclk", "swdio", "swd", "tck", "tms", "tdi", "tdo", "jtag")


def _ports_of(board: Board, component: Component) -> list[tuple[str, Net]]:
    out: list[tuple[str, Net]] = []
    for element in board.of_type("source_port"):
        if element.get("source_component_id") != component.source_id:
            continue
        net = board.net_of_port(str(element.get("source_port_id") or ""))
        if net is not None:
            out.append((str(element.get("name") or ""), net))
    return out


def _mpn(board: Board, source_id: str) -> str:
    for element in board.of_type("source_component"):
        if element.get("source_component_id") == source_id:
            return str(element.get("manufacturer_part_number") or "")
    return ""


def _pin_position(board: Board, component: Component, port_name: str) -> tuple[float, float] | None:
    """Where a named pin's pad actually sits, so distance means something."""
    for element in board.of_type("source_port"):
        if element.get("source_component_id") != component.source_id:
            continue
        if str(element.get("name") or "") != port_name:
            continue
        source_port_id = str(element.get("source_port_id") or "")
        for pcb_port in board.of_type("pcb_port"):
            if str(pcb_port.get("source_port_id") or "") != source_port_id:
                continue
            x, y = pcb_port.get("x"), pcb_port.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return (float(x), float(y))
    return component.center


@never_raises
def _decoupling(board: Board) -> list[Finding]:
    ground = board.ground
    if ground is None:
        return []
    # Index every local capacitor by the (power net, ground) pair it bridges.
    local_caps: list[tuple[str, tuple[float, float], Component]] = []
    for component in board.components:
        if component.ftype != "simple_capacitor":
            continue
        nets = [net for _, net in _ports_of(board, component)]
        if not any(n.key == ground.key for n in nets):
            continue
        for net in nets:
            if net.key != ground.key:
                local_caps.append((net.key, component.center, component))

    out: list[Finding] = []
    for component in board.components:
        if component.ftype != "simple_chip":
            continue
        haystack = f"{component.name} {_mpn(board, component.source_id)}".lower()
        if any(hint in haystack for hint in _UNPOWERED_PART_HINTS):
            continue
        supply_pins = [
            (name, net)
            for name, net in _ports_of(board, component)
            if net.is_power and any(h in name.lower() for h in _POWER_PIN_HINTS)
        ]
        if not supply_pins:
            continue
        undecoupled: list[str] = []
        distant: list[tuple[str, float]] = []
        for name, net in supply_pins:
            position = _pin_position(board, component, name) or component.center
            candidates = [
                (math.hypot(position[0] - cx, position[1] - cy), cap)
                for key, (cx, cy), cap in local_caps
                if key == net.key
            ]
            if not candidates:
                undecoupled.append(name)
                continue
            nearest, _cap = min(candidates, key=lambda pair: pair[0])
            if nearest > DECOUPLE_RADIUS_MM:
                distant.append((name, nearest))
        if undecoupled:
            out.append(
                finding(
                    component.name,
                    "review_decoupling_missing",
                    f"{component.name} has {len(undecoupled)} supply pin(s) with "
                    f"no local capacitor to ground on that rail "
                    f"({', '.join(sorted(undecoupled)[:6])}). This fails nothing "
                    "geometric — the board arrives, assembles, and misbehaves",
                    "warning",
                )
            )
        if distant:
            worst = max(distant, key=lambda pair: pair[1])
            out.append(
                finding(
                    component.name,
                    "review_decoupling_distant",
                    f"{component.name}'s nearest decoupling capacitor to pin "
                    f"{worst[0]} is {worst[1]:.1f}mm away; the loop inductance "
                    f"is what does the work, so keep it inside "
                    f"{DECOUPLE_RADIUS_MM:g}mm "
                    f"({len(distant)} pin(s) are further)",
                    "info",
                )
            )
    return out


@never_raises
def _bulk(board: Board) -> list[Finding]:
    ground = board.ground
    if ground is None:
        return []
    bulk_by_net: dict[str, float] = {}
    for component in board.components:
        if component.ftype != "simple_capacitor" or component.capacitance is None:
            continue
        if component.capacitance < BULK_MIN_F:
            continue
        nets = [net for _, net in _ports_of(board, component)]
        if not any(n.key == ground.key for n in nets):
            continue
        for net in nets:
            if net.key != ground.key:
                bulk_by_net[net.key] = max(
                    bulk_by_net.get(net.key, 0.0), component.capacitance
                )
    out: list[Finding] = []
    for net in board.power_nets:
        if net.key not in bulk_by_net:
            out.append(
                finding(
                    net.label,
                    "review_bulk_missing",
                    f"{net.label} has no bulk capacitor of {BULK_MIN_F * 1e6:g}uF "
                    "or more; a rail with only 100nF parts sags on any load step",
                    "warning",
                )
            )
    return out


@never_raises
def _crystal_load_caps(board: Board) -> list[Finding]:
    ground = board.ground
    if ground is None:
        return []
    caps_on: dict[str, int] = {}
    for component in board.components:
        if component.ftype != "simple_capacitor":
            continue
        nets = [net for _, net in _ports_of(board, component)]
        if not any(n.key == ground.key for n in nets):
            continue
        for net in nets:
            if net.key != ground.key:
                caps_on[net.key] = caps_on.get(net.key, 0) + 1
    out: list[Finding] = []
    for component in board.components:
        if component.ftype != "simple_crystal":
            continue
        pins = [net for _, net in _ports_of(board, component) if not net.is_ground]
        loaded = sum(1 for net in pins if caps_on.get(net.key))
        if loaded < 2:
            out.append(
                finding(
                    component.name,
                    "review_crystal_load_caps",
                    f"{component.name} has load capacitors on {loaded} of its 2 "
                    "terminals; a crystal without both will start slowly, run "
                    "off frequency, or not start at all",
                    "warning",
                )
            )
    return out


@never_raises
def _floating_pins(board: Board) -> list[Finding]:
    """A pin that is the only thing on its net is connected to nothing.

    KiCad's ERC says this too, but every ERC finding in our pipeline is pinned
    to ``info`` because the schematic converter produces 152 of them on a
    correct board. This one reads the netlist directly, so it has no such
    noise floor.
    """
    out: list[Finding] = []
    lonely: dict[str, list[str]] = {}
    for net in board.nets:
        if len(net.pins) != 1:
            continue
        component_name, pin_name = net.pins[0]
        component = board.by_name.get(component_name)
        if component is None or component.ftype not in ("simple_chip", "simple_connector"):
            continue
        lower = pin_name.lower().rstrip("0123456789_")
        if lower in _OUTPUT_PIN_HINTS:
            continue  # a spare output is ordinary, not a defect
        if any(h in (net.name or "").lower() for h in _DEBUG_NET_HINTS):
            continue  # _debug_header says this better
        lonely.setdefault(component_name, []).append(pin_name)
    for name, pins in sorted(lonely.items()):
        out.append(
            finding(
                name,
                "review_floating_pin",
                f"{len(pins)} pin(s) of {name} are alone on their net and "
                f"connect to nothing ({', '.join(sorted(pins)[:8])}). An input "
                "left floating picks up whatever is nearby; a strap pin left "
                "floating boots at random",
                "warning",
            )
        )
    return out


@never_raises
def _esd_on_connectors(board: Board) -> list[Finding]:
    """Every signal that leaves the board wants a clamp on it."""
    protected: set[str] = set()
    for component in board.components:
        haystack = f"{component.name} {_mpn(board, component.source_id)}".lower()
        if not any(hint in haystack for hint in _ESD_HINTS):
            continue
        for _, net in _ports_of(board, component):
            protected.add(net.key)

    out: list[Finding] = []
    for component in board.components:
        if component.ftype != "simple_connector":
            continue
        exposed = [
            (name, net)
            for name, net in _ports_of(board, component)
            if not net.is_power and not net.is_ground
        ]
        # Name the pin when the net is unnamed. "net:ctivity_net2" tells a
        # reader nothing; "J1.CC1" tells them where to look.
        unclamped = sorted(
            {
                net.name or f"{component.name}.{name}"
                for name, net in exposed
                if net.key not in protected
            }
        )
        if not unclamped:
            continue
        out.append(
            finding(
                component.name,
                "review_esd_unprotected",
                f"{len(unclamped)} signal(s) leave the board through "
                f"{component.name} with no ESD clamp on them "
                f"({', '.join(unclamped[:6])}). A finger on a connector is "
                "several kilovolts",
                "info",
            )
        )
    return out


@never_raises
def _test_points(board: Board) -> list[Finding]:
    """A rail you cannot put a probe on is a rail you cannot debug — and the
    user cannot scope this board, so the conversation has to replace the
    oscilloscope."""
    probeable: set[str] = set()
    for component in board.components:
        is_probe = component.prefix in ("TP", "J", "P", "CN") or component.ftype in (
            "simple_test_point",
            "simple_connector",
        )
        if not is_probe:
            continue
        for _, net in _ports_of(board, component):
            probeable.add(net.key)
    missing = [
        net.label
        for net in board.power_nets + ([board.ground] if board.ground else [])
        if net.key not in probeable
    ]
    if not missing:
        return []
    return [
        finding(
            "board",
            "review_no_test_point",
            f"{len(missing)} rail(s) have nowhere to put a probe "
            f"({', '.join(sorted(missing)[:6])}). Add a test point — nobody can "
            "diagnose a dead board over chat without one",
            "info",
        )
    ]


@never_raises
def _debug_header(board: Board) -> list[Finding]:
    """A programming interface that reaches no connector or pad cannot be
    used once the board is assembled."""
    reachable: set[str] = set()
    for component in board.components:
        if component.ftype not in ("simple_connector", "simple_test_point") and (
            component.prefix not in ("TP", "J", "P", "CN")
        ):
            continue
        for _, net in _ports_of(board, component):
            reachable.add(net.key)
    stranded = sorted(
        {
            net.label
            for net in board.nets
            if net.name
            and any(h in net.name.lower() for h in _DEBUG_NET_HINTS)
            and net.key not in reachable
        }
    )
    if not stranded:
        return []
    return [
        finding(
            "board",
            "review_debug_unreachable",
            f"the debug interface ({', '.join(stranded)}) reaches no connector "
            "or test point, so the board cannot be programmed or halted once it "
            "is assembled",
            "warning",
        )
    ]


def check(board: Board) -> CheckResult:
    chips = [c for c in board.components if c.ftype == "simple_chip"]
    coverage = Coverage(unit="ICs", total=len(chips))
    coverage.examined = sum(
        1
        for c in chips
        if any(
            net.is_power and any(h in name.lower() for h in _POWER_PIN_HINTS)
            for name, net in _ports_of(board, c)
        )
    )
    if coverage.examined < coverage.total:
        coverage.skip(
            f"{coverage.total - coverage.examined} IC(s) expose no pin this "
            "check recognises as a supply, so their decoupling was not judged"
        )
    coverage.skip(
        "whether a specific pin needs a pull-up or a particular strap level — "
        "that is in a datasheet, not in the netlist"
    )
    coverage.skip("power-up sequencing between rails")

    findings: list[Finding] = []
    findings += _decoupling(board)
    findings += _bulk(board)
    findings += _crystal_load_caps(board)
    findings += _floating_pins(board)
    findings += _esd_on_connectors(board)
    findings += _test_points(board)
    findings += _debug_header(board)
    return CheckResult(
        name="review",
        findings=findings,
        coverage=coverage,
        notes=[
            "the electrical half of a published EE design review, computed "
            "from connectivity rather than asked of a model"
        ],
    )
