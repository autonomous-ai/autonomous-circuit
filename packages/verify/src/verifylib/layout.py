"""Electrical layout quality: is critical copper physically credible?

DRC answers whether two pieces of copper collide. It does not answer whether
USB D+/D- share a reference plane, whether the crystal loop changes layers
four times, or whether the ESD array sits behind a long antenna-like run from
the connector. Those are the conspicuous errors an EE sees in seconds and the
three example boards exposed consistently.

This check stays measurement-based. Inferred electrical-layout budgets are
advisory, but an explicit ``source_trace.min_trace_thickness`` is a compiled
source contract: emitted copper below it is an error, not an autorouter
preference. Silence is not simulation: impedance still needs a real stack-up
and field solver, stated in coverage below.
"""

from __future__ import annotations

import math

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board, Component, Net

CRYSTAL_MAX_LENGTH_MM = 10.0
CRYSTAL_MAX_VIAS = 0
USB_MAX_VIAS_PER_NET = 2
QSPI_CLOCK_MAX_LENGTH_MM = 25.0
QSPI_CLOCK_MAX_VIAS = 1
QSPI_DATA_MAX_LENGTH_MM = 35.0
QSPI_DATA_MAX_VIAS = 2
ESD_MAX_DISTANCE_MM = 8.0

_ESD_HINTS = ("usblc", "esd", "tvs", "pesd", "srv05", "smf", "cdsod")
_USB_CONNECTOR_HINTS = ("type-c", "usb-c", "usb c", "usb2", "usb 2")
_FLASH_HINTS = ("w25q", "gd25", "mx25", "is25", "s25fl")


def _mpn(board: Board, component: Component) -> str:
    for element in board.of_type("source_component"):
        if element.get("source_component_id") == component.source_id:
            return str(element.get("manufacturer_part_number") or "")
    return ""


def _via_count_by_key(board: Board) -> dict[str, int]:
    out: dict[str, int] = {}
    for via in board.of_type("pcb_via"):
        key = via.get("subcircuit_connectivity_map_key")
        if isinstance(key, str) and key:
            out[key] = out.get(key, 0) + 1
    return out


def _net_pin_tokens(net: Net) -> set[str]:
    return {str(pin).upper() for _component, pin in net.pins}


def _is_usb(net: Net) -> bool:
    tokens = _net_pin_tokens(net)
    name = (net.name or "").upper()
    return any(
        token in name or token in tokens
        for token in ("USB_DP", "USB_DM", "D+", "D-")
    )


def _is_crystal(board: Board, net: Net) -> bool:
    for component_name, _pin in net.pins:
        component = board.by_name.get(component_name)
        if component is not None and component.ftype == "simple_crystal":
            return True
    return False


def _qspi_kind(board: Board, net: Net) -> str | None:
    tokens = _net_pin_tokens(net)
    if any("QSPI_SCLK" in token or "QSPI_CLK" in token for token in tokens):
        return "clock"
    if any("QSPI_" in token for token in tokens):
        return "data"
    # Some MCUs call the pin only CLK/IOx. A recognised serial flash on the
    # same net keeps that from being mistaken for an unrelated clock.
    for component_name, pin in net.pins:
        component = board.by_name.get(component_name)
        if component is None:
            continue
        if not any(hint in _mpn(board, component).lower() for hint in _FLASH_HINTS):
            continue
        upper = pin.upper()
        return "clock" if upper in ("CLK", "SCLK") else "data"
    return None


def _critical_label(board: Board, net: Net) -> tuple[str, str] | None:
    if net.is_ground or net.is_power:
        return None
    if _is_crystal(board, net):
        crystal_pins: list[str] = []
        for component_name, pin in net.pins:
            component = board.by_name.get(component_name)
            if (
                component is not None
                and component.ftype == "simple_crystal"
            ) or pin.upper() in ("XIN", "XOUT"):
                crystal_pins.append(f"{component_name}.{pin}")
        readable = "/".join(crystal_pins)
        return ("crystal", readable or net.label)
    if _is_usb(net):
        return ("usb", net.label)
    qspi = _qspi_kind(board, net)
    if qspi:
        readable = next(
            (
                f"{component}.{pin}"
                for component, pin in net.pins
                if "QSPI" in pin.upper()
            ),
            net.label,
        )
        return (f"qspi_{qspi}", readable)
    return None


def _ground_source_ids(board: Board) -> set[str]:
    ground = board.ground
    if ground is None:
        return set()
    return {
        str(element.get("source_net_id"))
        for element in board.of_type("source_net")
        if element.get("subcircuit_connectivity_map_key") == ground.key
        and element.get("source_net_id")
    }


def _has_ground_plane(board: Board) -> bool:
    source_ids = _ground_source_ids(board)
    return any(
        element.get("source_net_id") in source_ids
        for element in board.of_type("pcb_copper_pour")
    )


@never_raises
def _requested_trace_widths(board: Board) -> list[Finding]:
    """Refuse copper narrower than its source trace explicitly requested.

    Match by exact source-trace identity rather than by electrical net. A net
    may intentionally contain a short fine-pitch branch beside a wide trunk;
    comparing every segment on that net to every source request would invent
    violations. Conversely, treating ``min_trace_thickness`` as a nominal
    hint lets the capacity router silently fall back to the board-wide floor.
    """
    pcb_by_source: dict[str, list[dict]] = {}
    for trace in board.of_type("pcb_trace"):
        identities = {
            str(trace.get("source_trace_id") or ""),
            str(trace.get("connection_name") or ""),
        }
        for identity in identities - {""}:
            pcb_by_source.setdefault(identity, []).append(trace)

    out: list[Finding] = []
    for source in board.of_type("source_trace"):
        requested = source.get("min_trace_thickness")
        source_id = str(source.get("source_trace_id") or "")
        if (
            not source_id
            or isinstance(requested, bool)
            or not isinstance(requested, (int, float))
            or not math.isfinite(float(requested))
            or float(requested) <= 0
        ):
            continue

        physical = pcb_by_source.get(source_id, [])
        measured: list[tuple[float, str]] = []
        for trace in physical:
            route = [point for point in trace.get("route") or [] if isinstance(point, dict)]
            for first, second in zip(route, route[1:]):
                if first.get("route_type") != "wire" or second.get("route_type") != "wire":
                    continue
                if first.get("layer") != second.get("layer"):
                    continue
                x0, y0, x1, y1 = (
                    first.get("x"), first.get("y"), second.get("x"), second.get("y")
                )
                if not all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in (x0, y0, x1, y1)
                ):
                    continue
                if math.hypot(float(x1) - float(x0), float(y1) - float(y0)) <= 1e-9:
                    continue
                widths = [
                    float(value)
                    for value in (first.get("width"), second.get("width"))
                    if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                ]
                if widths:
                    measured.append(
                        (min(widths), str(trace.get("pcb_trace_id") or "unknown"))
                    )

        if not measured:
            continue
        actual, pcb_trace_id = min(measured)
        if actual + 1e-9 >= float(requested):
            continue
        label = str(source.get("name") or source_id)
        out.append(
            finding(
                label,
                "layout_trace_below_requested",
                f"{label} emitted {actual:g}mm copper on {pcb_trace_id}, below "
                f"its explicit {float(requested):g}mm min_trace_thickness. "
                "Re-route the geometry at the requested width or author a "
                "separate bounded neck-down trace; never silently substitute "
                "the board-wide minimum",
                "error",
            )
        )
    return out


@never_raises
def _reference_plane(board: Board, critical: list[tuple[Net, str, str]], via_counts: dict[str, int]) -> list[Finding]:
    if not critical or _has_ground_plane(board):
        return []
    ground = board.ground
    gnd_length = sum(trace.length for trace in board.traces_on(ground)) if ground else 0.0
    gnd_vias = via_counts.get(ground.key, 0) if ground else 0
    kinds = ", ".join(sorted({kind.split("_")[0].upper() for _net, kind, _label in critical}))
    return [
        finding(
            "board",
            "layout_reference_plane_missing",
            f"this 2-layer board routes {len(critical)} critical {kinds} net(s) "
            "but has no copper pour tied to GND. The return path is instead a "
            f"{gnd_length:.1f}mm routed GND tree with {gnd_vias} via(s), so "
            "high-frequency current has no continuous reference beneath the "
            "signal. Add a GND plane and re-check its cutouts",
            "warning",
        )
    ]


@never_raises
def _critical_routes(
    board: Board,
    critical: list[tuple[Net, str, str]],
    via_counts: dict[str, int],
) -> list[Finding]:
    out: list[Finding] = []
    for net, kind, label in critical:
        length = sum(trace.length for trace in board.traces_on(net))
        vias = via_counts.get(net.key, 0)
        if length <= 0:
            continue
        if kind == "crystal" and (length > CRYSTAL_MAX_LENGTH_MM or vias > CRYSTAL_MAX_VIAS):
            out.append(
                finding(
                    label,
                    "layout_crystal_route",
                    f"{label} routes {length:.2f}mm through {vias} via(s); the "
                    f"crystal loop budget is {CRYSTAL_MAX_LENGTH_MM:g}mm and "
                    "0 vias. Put the crystal and load capacitors beside the "
                    "oscillator pins and keep the loop on one layer",
                    "warning",
                )
            )
        elif kind == "usb" and vias > USB_MAX_VIAS_PER_NET:
            out.append(
                finding(
                    label,
                    "layout_usb_layer_changes",
                    f"{label} routes {length:.2f}mm through {vias} via(s); a USB "
                    f"data leg should need no more than {USB_MAX_VIAS_PER_NET} "
                    "when the connector, protection and MCU are placed as one "
                    "corridor. Route D+/D- together over the same reference",
                    "warning",
                )
            )
        elif kind == "qspi_clock" and (
            length > QSPI_CLOCK_MAX_LENGTH_MM or vias > QSPI_CLOCK_MAX_VIAS
        ):
            out.append(
                finding(
                    label,
                    "layout_qspi_clock_route",
                    f"{label} routes {length:.2f}mm through {vias} via(s); the "
                    f"flash-clock budget is {QSPI_CLOCK_MAX_LENGTH_MM:g}mm and "
                    f"at most {QSPI_CLOCK_MAX_VIAS} via. Place flash beside the "
                    "MCU and fan the bus out as a short group",
                    "warning",
                )
            )
        elif kind == "qspi_data" and (
            length > QSPI_DATA_MAX_LENGTH_MM or vias > QSPI_DATA_MAX_VIAS
        ):
            out.append(
                finding(
                    label,
                    "layout_qspi_data_route",
                    f"{label} routes {length:.2f}mm through {vias} via(s); the "
                    f"data budget is {QSPI_DATA_MAX_LENGTH_MM:g}mm and "
                    f"{QSPI_DATA_MAX_VIAS} vias. Keep the flash bus clustered "
                    "and comparable in topology",
                    "warning",
                )
            )
    return out


@never_raises
def _esd_placement(board: Board) -> list[Finding]:
    protectors = [
        component
        for component in board.components
        if any(
            hint in f"{component.name} {_mpn(board, component)}".lower()
            for hint in _ESD_HINTS
        )
    ]
    connectors = []
    for component in board.components:
        if component.ftype != "simple_connector":
            continue
        # The MPN identifies receptacles reliably; pin labels catch fixtures
        # and generic connector parts whose MPN was not preserved.
        pin_names = {
            pin.upper()
            for net in board.nets
            for owner, pin in net.pins
            if owner == component.name
        }
        haystack = f"{component.name} {_mpn(board, component)}".lower()
        if any(hint in haystack for hint in _USB_CONNECTOR_HINTS) or {"DP1", "DM1"} & pin_names:
            connectors.append(component)
    if not protectors or not connectors:
        return []

    out: list[Finding] = []
    for connector in connectors:
        protector, measured = min(
            (
                component,
                math.hypot(
                    component.center[0] - connector.center[0],
                    component.center[1] - connector.center[1],
                ),
            )
            for component in protectors
        )
        if measured <= ESD_MAX_DISTANCE_MM:
            continue
        out.append(
            finding(
                protector.name,
                "layout_esd_distant",
                f"{protector.name} is {measured:.1f}mm from {connector.name}; "
                f"the protection corridor budget is {ESD_MAX_DISTANCE_MM:g}mm. "
                "Put the clamp immediately behind the connector so the surge "
                "does not travel across the board before it is diverted",
                "warning",
            )
        )
    return out


def check(board: Board) -> CheckResult:
    via_counts = _via_count_by_key(board)
    critical = [
        (net, kind, label)
        for net in board.nets
        if (classified := _critical_label(board, net)) is not None
        for kind, label in [classified]
    ]
    findings: list[Finding] = []
    findings += _requested_trace_widths(board)
    findings += _reference_plane(board, critical, via_counts)
    findings += _critical_routes(board, critical, via_counts)
    findings += _esd_placement(board)
    coverage = Coverage(unit="critical nets", total=len(critical), examined=len(critical))
    coverage.skip("controlled impedance — circuit.json carries no verified PCB stack-up")
    coverage.skip("EMI/EMC and eye quality — these require simulation or lab measurement")
    return CheckResult(
        name="layout",
        findings=findings,
        coverage=coverage,
        notes=[
            "route length and via count are measured from compiled copper; "
            "budgets are advisory electrical-layout rules, not fab limits"
        ],
    )
