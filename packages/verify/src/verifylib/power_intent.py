"""Verify a product's declared USB power budget against compiled hardware.

The ordinary DC and thermal checks infer what they can from part models.  A
USB source contract is different: attach capacitance, the exact current-limit
boundary, and a firmware-enforced operating cap are product decisions.  This
module consumes ``product.json.powerBudget`` as an independent second opinion
and fails closed when the routed artifact does not implement that contract.
"""

from __future__ import annotations

import fnmatch
import math
from typing import Any

from verifylib import dc
from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.loads import lookup
from verifylib.model import Board, Component, Net, Pad


_REGULATOR_KEYS = {
    "profile",
    "ref",
    "inputNet",
    "outputNet",
    "inputCapRef",
    "outputCapRef",
    "maxAmbientC",
}

# Independently reviewed regulator profiles.  Do not accept thermal or
# capacitor requirements from product.json: a generated product choosing its
# own theta-JA or junction limit would be circular evidence.  This table is
# intentionally independent of circuitpy's schema registry.
AUDITED_REGULATOR_PROFILES: dict[str, dict[str, Any]] = {
    "ap7361c-33e-c500795-v1": {
        "lcsc": "C500795",
        "mpn": "AP7361C-33E-13",
        "pins": {
            "input": "VIN",
            "grounds": ("GND1", "GND2"),
            "output": "VOUT",
        },
        # Diodes DS37274 Rev. 5-2 page 21, rotated into any board orientation.
        # Theta-JA=110C/W is conditional on at least this manufacturer land.
        "thermalLand": {
            "leadPadMm": (1.2, 1.6),
            "tabPadMm": (1.6, 3.3),
            "leadPitchMm": 2.3,
            "rowCenterMm": 6.4,
            "toleranceMm": 0.01,
        },
        # C19702 is Samsung CL10A106KP8NNNC: 10uF +/-10%, X5R, 10V,
        # 0603.  It exceeds the AP7361C's >=1uF input and >=2.2uF ceramic
        # output requirements while retaining DC-bias margin.
        "capacitorLcsc": "C19702",
        "capacitorFarads": 10e-6,
        "maxCapPadGapMm": 2.0,
        "outputVolts": 3.3,
        "maxInputVolts": 5.25,
        # The 1A headline rating is not a board-level thermal entitlement.
        # At the audited worst-case input and a 60C product ambient, 150mA
        # retains just over 30C to the 125C design ceiling on the literal
        # manufacturer land pattern.  Products with lower loads may declare a
        # hotter ambient and are checked from their compiled load inventory.
        "maxContinuousOutputMa": 150.0,
        "maxGroundCurrentMa": 0.08,
        # Datasheet theta-JA for SOT-223 on FR-4 with the manufacturer's
        # minimum recommended pad layout.  The block footprint and thermal
        # copper are therefore part of this profile, not optional decoration.
        "thetaJaCPerW": 110.0,
        "designMaxJunctionC": 125.0,
        "minThermalHeadroomC": 30.0,
    }
}


def _patterns(value: object) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _component_pins(board: Board) -> dict[str, dict[str, Net]]:
    out: dict[str, dict[str, Net]] = {}
    for net in board.nets:
        for component, pin in net.pins:
            out.setdefault(component, {})[pin.casefold()] = net
    return out


def _source_component_names(board: Board) -> dict[str, str]:
    return {
        str(element.get("source_component_id") or ""): str(
            element.get("name") or ""
        )
        for element in board.of_type("source_component")
        if element.get("source_component_id")
    }


def _component_port_records(
    board: Board,
) -> dict[str, dict[str, tuple[str, Net | None]]]:
    names = _source_component_names(board)
    out: dict[str, dict[str, tuple[str, Net | None]]] = {}
    for element in board.of_type("source_port"):
        port_id = str(element.get("source_port_id") or "")
        component = names.get(str(element.get("source_component_id") or ""), "")
        name = str(element.get("name") or element.get("pin_number") or "")
        if component and name and port_id:
            out.setdefault(component, {})[name.casefold()] = (
                port_id,
                board.net_of_port(port_id),
            )
    return out


def _component_mpn(board: Board, component: Component) -> str | None:
    for element in board.of_type("source_component"):
        if element.get("source_component_id") != component.source_id:
            continue
        value = element.get("manufacturer_part_number")
        return str(value) if isinstance(value, str) and value else None
    return None


def _authored_edge(board: Board, left: str, right: str) -> bool:
    wanted = {left, right}
    for trace in board.of_type("source_trace"):
        ports = {
            str(value)
            for value in (trace.get("connected_source_port_ids") or [])
            if value
        }
        nets = [
            value for value in (trace.get("connected_source_net_ids") or []) if value
        ]
        if ports == wanted and not nets:
            return True
    return False


def _source_pad_rects(board: Board) -> dict[str, Any]:
    pcb_port_by_source = {
        str(element.get("source_port_id") or ""): str(
            element.get("pcb_port_id") or ""
        )
        for element in board.of_type("pcb_port")
        if element.get("source_port_id") and element.get("pcb_port_id")
    }
    pads_by_port = {
        pad.port_id: pad.rect
        for component in board.components
        for pad in component.pads
        if pad.port_id
    }
    return {
        source_port_id: pads_by_port[pcb_port_id]
        for source_port_id, pcb_port_id in pcb_port_by_source.items()
        if pcb_port_id in pads_by_port
    }


def _source_pads(board: Board) -> dict[str, Pad]:
    pcb_port_by_source = {
        str(element.get("source_port_id") or ""): str(
            element.get("pcb_port_id") or ""
        )
        for element in board.of_type("pcb_port")
        if element.get("source_port_id") and element.get("pcb_port_id")
    }
    pads_by_port = {
        pad.port_id: pad
        for component in board.components
        for pad in component.pads
        if pad.port_id
    }
    return {
        source_port_id: pads_by_port[pcb_port_id]
        for source_port_id, pcb_port_id in pcb_port_by_source.items()
        if pcb_port_id in pads_by_port
    }


def _pad_size(pad: Pad) -> tuple[float, float]:
    return tuple(sorted((pad.width, pad.height)))


def _thermal_land_failures(
    profile: dict[str, Any],
    pin_names: dict[str, Any],
    component_ports: dict[str, tuple[str, Net | None]],
    pads: dict[str, Pad],
) -> list[str]:
    land = profile.get("thermalLand")
    grounds = tuple(pin_names.get("grounds", ()))
    if not isinstance(land, dict) or len(grounds) != 2:
        return ["audited thermal-land profile is incomplete"]

    names = {
        "input": str(pin_names["input"]),
        "groundLead": str(grounds[0]),
        "output": str(pin_names["output"]),
        "groundTab": str(grounds[1]),
    }
    resolved: dict[str, Pad] = {}
    failures: list[str] = []
    for role, name in names.items():
        record = component_ports.get(name.casefold())
        pad = pads.get(record[0]) if record else None
        if pad is None or pad.plated_hole:
            failures.append(f"{name} has no measurable SMT copper pad")
        else:
            resolved[role] = pad
    if failures:
        return failures

    tolerance = float(land["toleranceMm"])

    def close(left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=0, abs_tol=tolerance)

    expected_lead = tuple(sorted(float(value) for value in land["leadPadMm"]))
    expected_tab = tuple(sorted(float(value) for value in land["tabPadMm"]))
    for role in ("input", "groundLead", "output"):
        actual = _pad_size(resolved[role])
        if not all(close(a, b) for a, b in zip(actual, expected_lead)):
            failures.append(
                f"{names[role]} pad is {actual[0]:.3f}x{actual[1]:.3f}mm, "
                f"not the audited {expected_lead[0]:g}x{expected_lead[1]:g}mm land"
            )
    actual_tab = _pad_size(resolved["groundTab"])
    if not all(close(a, b) for a, b in zip(actual_tab, expected_tab)):
        failures.append(
            f"{names['groundTab']} pad is {actual_tab[0]:.3f}x{actual_tab[1]:.3f}mm, "
            f"not the audited {expected_tab[0]:g}x{expected_tab[1]:g}mm land"
        )

    distance = lambda a, b: math.hypot(a.x - b.x, a.y - b.y)
    lead_pitch = float(land["leadPitchMm"])
    for role in ("input", "output"):
        actual = distance(resolved[role], resolved["groundLead"])
        if not close(actual, lead_pitch):
            failures.append(
                f"{names[role]} to {names['groundLead']} pitch is {actual:.3f}mm, "
                f"not {lead_pitch:g}mm"
            )
    row_center = float(land["rowCenterMm"])
    actual_row = distance(resolved["groundLead"], resolved["groundTab"])
    if not close(actual_row, row_center):
        failures.append(
            f"lead-to-tab row spacing is {actual_row:.3f}mm, not {row_center:g}mm"
        )
    return failures


def _component_nets(
    pins: dict[str, dict[str, Net]], component: Component
) -> set[str]:
    return {net.key for net in pins.get(component.name, {}).values()}


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _contract_failures(usb: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for key in ("rawVbusNet", "protectedVbusNet"):
        if not isinstance(usb.get(key), str) or not str(usb[key]).strip():
            failures.append(f"{key} is missing")
    for key in (
        "rawAttachCapacitanceMaxUf",
        "sourceCurrentMaxMa",
    ):
        value = _finite_number(usb.get(key))
        if value is None or value <= 0:
            failures.append(f"{key} is not positive and finite")
    fixed = _finite_number(usb.get("fixedOperationalLoadMa", 0))
    if fixed is None or fixed < 0:
        failures.append("fixedOperationalLoadMa is not non-negative and finite")
    limiter = usb.get("currentLimiter")
    if not isinstance(limiter, dict):
        failures.append("currentLimiter is missing")
    else:
        for key in ("ref", "lcsc", "inputPin", "outputPin", "settingPin"):
            if not isinstance(limiter.get(key), str) or not str(limiter[key]).strip():
                failures.append(f"currentLimiter.{key} is missing")
        setting_resistor = limiter.get("settingResistor")
        if not isinstance(setting_resistor, dict):
            failures.append("currentLimiter.settingResistor is missing")
        else:
            for key in ("ref", "lcsc", "returnNet"):
                if not isinstance(setting_resistor.get(key), str) or not str(
                    setting_resistor[key]
                ).strip():
                    failures.append(
                        f"currentLimiter.settingResistor.{key} is missing"
                    )
            resistance = _finite_number(setting_resistor.get("resistanceOhms"))
            if resistance is None or resistance <= 0:
                failures.append(
                    "currentLimiter.settingResistor.resistanceOhms is not positive and finite"
                )
        for key in ("minTripMa", "maxTripMa"):
            value = _finite_number(limiter.get(key))
            if value is None or value <= 0:
                failures.append(f"currentLimiter.{key} is not positive and finite")
    loads = usb.get("firmwareLimitedLoads", [])
    if not isinstance(loads, list):
        failures.append("firmwareLimitedLoads is not a list")
    else:
        for index, load in enumerate(loads):
            if not isinstance(load, dict):
                failures.append(f"firmwareLimitedLoads[{index}] is not an object")
                continue
            if not _patterns(load.get("match")):
                failures.append(f"firmwareLimitedLoads[{index}].match is empty")
            physical = _finite_number(load.get("perDevicePhysicalPeakMa"))
            operational = _finite_number(load.get("aggregateOperationalMaxMa"))
            if physical is None or physical <= 0:
                failures.append(
                    f"firmwareLimitedLoads[{index}].perDevicePhysicalPeakMa is not positive"
                )
            if operational is None or operational < 0:
                failures.append(
                    f"firmwareLimitedLoads[{index}].aggregateOperationalMaxMa is not non-negative"
                )
    return failures


def _regulator_contract_failures(value: object) -> list[str]:
    if not isinstance(value, list):
        return ["regulators is not a list"]
    failures: list[str] = []
    seen_refs: set[str] = set()
    seen_outputs: set[str] = set()
    seen_caps: set[str] = set()
    for index, regulator in enumerate(value):
        label = f"regulators[{index}]"
        if not isinstance(regulator, dict):
            failures.append(f"{label} is not an object")
            continue
        unknown = sorted(set(regulator) - _REGULATOR_KEYS)
        missing = sorted(_REGULATOR_KEYS - set(regulator))
        if unknown:
            failures.append(f"{label} has unknown members: {', '.join(unknown)}")
        if missing:
            failures.append(f"{label} is missing: {', '.join(missing)}")
        profile = regulator.get("profile")
        if profile not in AUDITED_REGULATOR_PROFILES:
            failures.append(f"{label}.profile is not audited")
        strings: dict[str, str] = {}
        for key in _REGULATOR_KEYS - {"profile", "maxAmbientC"}:
            raw = regulator.get(key)
            if not isinstance(raw, str) or not raw.strip():
                failures.append(f"{label}.{key} is missing")
            else:
                strings[key] = raw.strip()
        max_ambient = _finite_number(regulator.get("maxAmbientC"))
        if max_ambient is None or max_ambient < 50.0 or max_ambient > 85.0:
            failures.append(f"{label}.maxAmbientC is not between 50 and 85 degC")
        ref = strings.get("ref")
        input_net = strings.get("inputNet")
        output_net = strings.get("outputNet")
        input_cap = strings.get("inputCapRef")
        output_cap = strings.get("outputCapRef")
        if input_net and output_net and input_net == output_net:
            failures.append(f"{label}.inputNet and outputNet are identical")
        if ref and input_cap and output_cap and len({ref, input_cap, output_cap}) != 3:
            failures.append(f"{label} reuses its regulator/capacitor reference")
        if ref:
            if ref in seen_refs:
                failures.append(f"{label}.ref duplicates {ref}")
            seen_refs.add(ref)
        if output_net:
            if output_net in seen_outputs:
                failures.append(f"{label}.outputNet duplicates {output_net}")
            seen_outputs.add(output_net)
        for cap in (input_cap, output_cap):
            if not cap:
                continue
            if cap in seen_caps:
                failures.append(f"{label} reuses capacitor {cap}")
            seen_caps.add(cap)
    return failures


def _peak_current_on_net(
    board: Board, output: Net, excluded_refs: set[str]
) -> tuple[float | None, list[str]]:
    """Measure a built rail at datasheet peak, never a declared estimate."""

    network = dc.build_network(board, load_mode="peak")
    solution = dc.solve(network)
    if not solution.voltages:
        return None, ["DC operating point did not solve"]

    unknown: list[str] = []
    pins = _component_pins(board)
    for component in board.placed():
        if component.name in excluded_refs:
            continue
        if output.key not in _component_nets(pins, component):
            continue
        if lookup(
            lcsc=component.lcsc,
            mpn=_component_mpn(board, component),
            ftype=component.ftype,
        ) is None:
            unknown.append(component.name)

    amps = sum(
        sink.amps
        for sink in network.sinks
        if sink.net == output.key and sink.refdes not in excluded_refs
    )
    # Resistor-limited LEDs and similar loads are solved as elements rather
    # than black-box sinks. Count current leaving the regulated node in either
    # element orientation, exactly once at the boundary.
    for element in network.elements:
        if element.refdes in excluded_refs:
            continue
        flow = solution.currents.get(element.refdes, 0.0)
        if element.a == output.key and flow > 0:
            amps += flow
        elif element.b == output.key and flow < 0:
            amps -= flow
    return amps * 1000.0, sorted(set(unknown))


def _regulator_peak_current(
    board: Board,
    output: Net,
    regulator_ref: str,
    cap_refs: set[str],
    *,
    excluded_refs: set[str] | None = None,
) -> tuple[float | None, list[str]]:
    """Measure one regulator output, excluding its boundary and capacitors."""

    return _peak_current_on_net(
        board,
        output,
        {regulator_ref, *cap_refs, *(excluded_refs or set())},
    )


def _compiled_fixed_usb_peak(
    board: Board,
    policy: dict[str, Any],
    protected: Net,
    firmware_refs: set[str],
) -> tuple[float | None, list[str]]:
    """Measure uncapped protected loads, including declared downstream rails.

    The USB limiter and regulator packages are boundaries, not loads on every
    rail they touch. Their audited quiescent currents remain covered by the
    deliberately conservative declared fixed allowance; all compiled consumer
    loads are a hard lower bound on that allowance.
    """

    usb = policy.get("usb")
    limiter_ref = ""
    if isinstance(usb, dict) and isinstance(usb.get("currentLimiter"), dict):
        limiter_ref = str(usb["currentLimiter"].get("ref") or "")

    raw_regulators = policy.get("regulators")
    declarations = raw_regulators if isinstance(raw_regulators, list) else []
    boundary_refs = {limiter_ref} if limiter_ref else set()
    for declaration in declarations:
        if isinstance(declaration, dict):
            boundary_refs.add(str(declaration.get("ref") or ""))
    boundary_refs.discard("")

    total, unknown = _peak_current_on_net(
        board, protected, firmware_refs | boundary_refs
    )
    if total is None:
        return None, unknown

    for declaration in declarations:
        if not isinstance(declaration, dict):
            continue
        if str(declaration.get("inputNet") or "") != protected.label:
            continue
        output = board.net_named(str(declaration.get("outputNet") or ""))
        if output is None:
            continue
        downstream, downstream_unknown = _regulator_peak_current(
            board,
            output,
            str(declaration.get("ref") or ""),
            {
                str(declaration.get("inputCapRef") or ""),
                str(declaration.get("outputCapRef") or ""),
            },
            excluded_refs=firmware_refs,
        )
        unknown.extend(downstream_unknown)
        if downstream is None:
            return None, sorted(set(unknown))
        total += downstream
    return total, sorted(set(unknown))


@never_raises
def _regulators(board: Board, policy: dict[str, Any]) -> list[Finding]:
    raw = policy.get("regulators", [])
    failures = _regulator_contract_failures(raw)
    if failures:
        return [
            finding(
                "product.json",
                "power_intent_regulator_contract",
                "regulator power budget is malformed: " + "; ".join(failures),
                "error",
            )
        ]
    if not isinstance(raw, list):
        return []

    out: list[Finding] = []
    placed_names = {component.name for component in board.placed()}
    declared_refs = {
        str(declaration.get("ref") or "")
        for declaration in raw
        if isinstance(declaration, dict)
    }
    for component in board.placed():
        actual_mpn = _component_mpn(board, component)
        matching_profiles = [
            name
            for name, profile in AUDITED_REGULATOR_PROFILES.items()
            if component.lcsc == profile["lcsc"] and actual_mpn == profile["mpn"]
        ]
        if matching_profiles and component.name not in declared_refs:
            out.append(
                finding(
                    component.name,
                    "power_intent_regulator_contract",
                    f"populated audited regulator {component.name} compiles as "
                    f"{component.lcsc}/{actual_mpn} but product.json does not "
                    f"declare its {matching_profiles[0]} load, ambient, capacitor, "
                    "and thermal-land contract",
                    "error",
                )
            )
    ports = _component_port_records(board)
    pads = _source_pad_rects(board)
    source_pads = _source_pads(board)
    ground = board.ground

    for declaration in raw:
        if not isinstance(declaration, dict):
            continue
        profile_name = str(declaration["profile"])
        profile = AUDITED_REGULATOR_PROFILES[profile_name]
        ref = str(declaration["ref"])
        input_name = str(declaration["inputNet"])
        output_name = str(declaration["outputNet"])
        input_cap_ref = str(declaration["inputCapRef"])
        output_cap_ref = str(declaration["outputCapRef"])
        max_ambient = float(declaration["maxAmbientC"])
        input_net = board.net_named(input_name)
        output_net = board.net_named(output_name)
        component = board.by_name.get(ref)

        if component is None or ref not in placed_names:
            out.append(
                finding(
                    ref,
                    "power_intent_regulator_missing",
                    f"{profile_name} requires populated regulator {ref}, but it is absent or DNP",
                    "error",
                )
            )
            continue
        actual_mpn = _component_mpn(board, component)
        if component.lcsc != profile["lcsc"] or actual_mpn != profile["mpn"]:
            out.append(
                finding(
                    ref,
                    "power_intent_regulator_identity",
                    f"{ref} compiles as LCSC {component.lcsc!r} / MPN {actual_mpn!r}; "
                    f"{profile_name} requires {profile['lcsc']} / {profile['mpn']}",
                    "error",
                )
            )

        pin_names = profile["pins"]
        component_ports = ports.get(ref, {})
        required_names = {
            str(value).casefold()
            for role, value in pin_names.items()
            if role != "grounds"
        }
        required_names.update(
            str(value).casefold() for value in pin_names.get("grounds", ())
        )
        topology_failures: list[str] = []
        if set(component_ports) != required_names:
            topology_failures.append(
                "semantic pins are "
                + ", ".join(sorted(component_ports) or ["unreadable"])
                + "; expected "
                + ", ".join(sorted(required_names))
            )

        def port(role: str) -> tuple[str, Net | None] | None:
            name = pin_names.get(role)
            return component_ports.get(str(name).casefold()) if name else None

        input_port = port("input")
        output_port = port("output")
        ground_port = port("ground")
        ground_ports = [
            component_ports.get(str(name).casefold())
            for name in pin_names.get("grounds", ())
        ]
        enable_port = port("enable")
        unused_port = port("unused")
        expected_connections = [
            (str(pin_names["input"]), input_port, input_net),
            (str(pin_names["output"]), output_port, output_net),
        ]
        if "ground" in pin_names:
            expected_connections.append(
                (str(pin_names["ground"]), ground_port, ground)
            )
        expected_connections.extend(
            (str(name), record, ground)
            for name, record in zip(pin_names.get("grounds", ()), ground_ports)
        )
        if "enable" in pin_names:
            expected_connections.append(
                (str(pin_names["enable"]), enable_port, input_net)
            )
        for label, record, expected in expected_connections:
            actual = record[1] if record else None
            if expected is None or actual is None or actual.key != expected.key:
                topology_failures.append(
                    f"{label} is on {actual.label if actual else 'no readable net'}, "
                    f"not {expected.label if expected else 'the required net'}"
                )
        if "unused" in pin_names and (unused_port is None or unused_port[1] is None):
            topology_failures.append(
                f"{pin_names['unused']} has no readable isolated source port"
            )
        elif "unused" in pin_names and unused_port is not None:
            nc_id, nc_net = unused_port
            if len(nc_net.pins) != 1 or nc_net.pins[0] != (ref, pin_names["unused"]):
                topology_failures.append("NC is electrically connected; it must be unused")
            if any(
                nc_id in (trace.get("connected_source_port_ids") or [])
                for trace in board.of_type("source_trace")
            ):
                topology_failures.append("NC participates in a source trace")
        if topology_failures:
            out.append(
                finding(
                    ref,
                    "power_intent_regulator_topology",
                    f"{ref} does not implement {profile_name}: "
                    + "; ".join(topology_failures),
                    "error",
                )
            )

        thermal_land_failures = _thermal_land_failures(
            profile, pin_names, component_ports, source_pads
        )
        if thermal_land_failures:
            out.append(
                finding(
                    ref,
                    "power_intent_regulator_thermal_land",
                    f"{ref} does not implement the land required by {profile_name}'s "
                    f"{profile['thetaJaCPerW']:g}C/W model: "
                    + "; ".join(thermal_land_failures),
                    "error",
                )
            )

        for role, cap_ref, rail, regulator_port in (
            ("input", input_cap_ref, input_net, input_port),
            ("output", output_cap_ref, output_net, output_port),
        ):
            cap = board.by_name.get(cap_ref)
            if cap is None or cap_ref not in placed_names:
                out.append(
                    finding(
                        cap_ref,
                        "power_intent_regulator_capacitor_missing",
                        f"{ref} requires populated {role} capacitor {cap_ref}",
                        "error",
                    )
                )
                continue
            if cap.ftype != "simple_capacitor" or cap.lcsc != profile["capacitorLcsc"]:
                out.append(
                    finding(
                        cap_ref,
                        "power_intent_regulator_capacitor_identity",
                        f"{cap_ref} compiles as {cap.ftype!r} / LCSC {cap.lcsc!r}; "
                        f"{profile_name} requires audited X5R capacitor {profile['capacitorLcsc']}",
                        "error",
                    )
                )
            expected_farads = float(profile["capacitorFarads"])
            if cap.capacitance is None or not math.isclose(
                cap.capacitance, expected_farads, rel_tol=1e-9, abs_tol=1e-15
            ):
                out.append(
                    finding(
                        cap_ref,
                        "power_intent_regulator_capacitor_value",
                        f"{cap_ref} compiles as {cap.capacitance!r}F; "
                        f"{profile_name} requires {expected_farads * 1e6:g}uF",
                        "error",
                    )
                )
            cap_records = ports.get(cap_ref, {})
            rail_records = [
                record
                for record in cap_records.values()
                if rail is not None and record[1] is not None and record[1].key == rail.key
            ]
            ground_records = [
                record
                for record in cap_records.values()
                if ground is not None
                and record[1] is not None
                and record[1].key == ground.key
            ]
            geometry_failure = ""
            if len(rail_records) != 1 or len(ground_records) != 1 or len(cap_records) != 2:
                geometry_failure = f"{cap_ref} does not bridge {rail.label if rail else role + ' rail'} to GND"
            elif regulator_port is None or not _authored_edge(
                board, regulator_port[0], rail_records[0][0]
            ):
                geometry_failure = f"{ref}.{pin_names[role]} has no authored two-port branch to {cap_ref}"
            elif component.layer != cap.layer:
                geometry_failure = f"{cap_ref} is on {cap.layer}, not {component.layer} with {ref}"
            else:
                regulator_rect = pads.get(regulator_port[0])
                cap_rect = pads.get(rail_records[0][0])
                if regulator_rect is None or cap_rect is None:
                    geometry_failure = "supply/capacitor pad copper is not measurable"
                else:
                    gap = max(0.0, regulator_rect.gap_to(cap_rect))
                    if gap > float(profile["maxCapPadGapMm"]) + 1e-9:
                        geometry_failure = (
                            f"{cap_ref} is {gap:.3f}mm pad-edge from {ref}.{pin_names[role]}; "
                            f"the audited maximum is {profile['maxCapPadGapMm']:g}mm"
                        )
            if geometry_failure:
                out.append(
                    finding(
                        cap_ref,
                        "power_intent_regulator_capacitor_topology",
                        geometry_failure,
                        "error",
                    )
                )

        if input_net is None or output_net is None:
            continue
        peak_ma, unknown = _regulator_peak_current(
            board, output_net, ref, {input_cap_ref, output_cap_ref}
        )
        if unknown:
            out.append(
                finding(
                    ref,
                    "power_intent_regulator_load_unknown",
                    f"{ref}'s {output_name} peak cannot be proven: " + ", ".join(unknown),
                    "error",
                )
            )
        if peak_ma is None:
            continue
        max_load = float(profile["maxContinuousOutputMa"])
        if peak_ma > max_load + 1e-9:
            out.append(
                finding(
                    ref,
                    "power_intent_regulator_load_budget",
                    f"{ref}'s compiled datasheet-peak load is {peak_ma:.3f}mA; "
                    f"{profile_name} is approved for at most {max_load:g}mA continuous",
                    "error",
                )
            )
        output_volts = float(profile["outputVolts"])
        max_input = float(profile["maxInputVolts"])
        ground_ma = float(profile["maxGroundCurrentMa"])
        # Output current is dissipated across the regulator's voltage drop;
        # quiescent/ground current is drawn from the input and therefore
        # dissipates Vin * Iq.  Keeping these terms separate avoids silently
        # understating the audited worst case.
        watts = (
            (max_input - output_volts) * peak_ma / 1000.0
            + max_input * ground_ma / 1000.0
        )
        junction = max_ambient + watts * float(
            profile["thetaJaCPerW"]
        )
        headroom = float(profile["designMaxJunctionC"]) - junction
        if headroom + 1e-9 < float(profile["minThermalHeadroomC"]):
            out.append(
                finding(
                    ref,
                    "power_intent_regulator_thermal",
                    f"{ref} reaches {junction:.2f}C at {max_input:g}V input, "
                    f"{peak_ma:.3f}mA peak and {max_ambient:g}C ambient "
                    f"using {profile['thetaJaCPerW']:g}C/W; only {headroom:.2f}C "
                    f"remains to the {profile['designMaxJunctionC']:g}C design limit, "
                    f"below the required {profile['minThermalHeadroomC']:g}C",
                    "error",
                )
            )
    return out


@never_raises
def _usb(board: Board, policy: dict[str, Any]) -> list[Finding]:
    usb = policy.get("usb")
    if not isinstance(usb, dict):
        return []

    out: list[Finding] = []
    contract_failures = _contract_failures(usb)
    if contract_failures:
        out.append(
            finding(
                "product.json",
                "power_intent_usb_contract",
                "USB power budget is malformed: " + "; ".join(contract_failures),
                "error",
            )
        )
    raw_name = str(usb.get("rawVbusNet") or "")
    protected_name = str(usb.get("protectedVbusNet") or "")
    raw = board.net_named(raw_name) if raw_name else None
    protected = board.net_named(protected_name) if protected_name else None
    ground = board.ground
    if raw is None:
        out.append(
            finding(
                raw_name or "raw VBUS",
                "power_intent_usb_raw_net",
                f"product power budget requires raw USB net {raw_name!r}, but the compiled board has no such net",
                "error",
            )
        )
    if protected is None:
        out.append(
            finding(
                protected_name or "protected VBUS",
                "power_intent_usb_protected_net",
                f"product power budget requires protected USB net {protected_name!r}, but the compiled board has no such net",
                "error",
            )
        )
    if ground is None:
        out.append(
            finding(
                "GND",
                "power_intent_usb_raw_capacitance",
                "USB attach-capacitance budget cannot be measured because the compiled board has no ground net",
                "error",
            )
        )

    pins = _component_pins(board)
    if raw is not None and ground is not None:
        raw_caps: list[Component] = []
        unknown_caps: list[str] = []
        for component in board.components:
            if component.ftype != "simple_capacitor":
                continue
            nets = _component_nets(pins, component)
            if raw.key not in nets or ground.key not in nets:
                continue
            raw_caps.append(component)
            if component.capacitance is None:
                unknown_caps.append(component.name)
        if unknown_caps:
            out.append(
                finding(
                    raw_name,
                    "power_intent_usb_raw_capacitance_unknown",
                    f"raw USB capacitor value is missing for {', '.join(sorted(unknown_caps))}; an unknown value cannot prove the attach limit",
                    "error",
                )
            )
        actual_uf = sum(
            component.capacitance or 0.0 for component in raw_caps
        ) * 1e6
        limit_uf = _finite_number(usb.get("rawAttachCapacitanceMaxUf"))
        if limit_uf is None or actual_uf > limit_uf + 1e-9:
            refs = ", ".join(
                f"{component.name}={(component.capacitance or 0) * 1e6:g}uF"
                for component in sorted(raw_caps, key=lambda item: item.name)
            ) or "no measurable raw capacitors"
            out.append(
                finding(
                    raw_name,
                    "power_intent_usb_raw_capacitance",
                    f"raw USB attach capacitance is {actual_uf:g}uF ({refs}); the product allows at most {limit_uf!r}uF before the current-limited boundary",
                    "error",
                )
            )

    limiter = usb.get("currentLimiter")
    limiter = limiter if isinstance(limiter, dict) else {}
    limiter_ref = str(limiter.get("ref") or "")
    component = board.by_name.get(limiter_ref)
    placed_names = {item.name for item in board.placed()}
    if component is None or component.name not in placed_names:
        out.append(
            finding(
                limiter_ref or "USB current limiter",
                "power_intent_usb_limiter_missing",
                f"product power budget requires populated current limiter {limiter_ref!r}, but it is absent or DNP",
                "error",
            )
        )
    else:
        expected_lcsc = str(limiter.get("lcsc") or "")
        if not expected_lcsc or component.lcsc != expected_lcsc:
            out.append(
                finding(
                    limiter_ref,
                    "power_intent_usb_limiter_identity",
                    f"{limiter_ref} compiles as LCSC {component.lcsc!r}; the approved current-limit part is {expected_lcsc!r}",
                    "error",
                )
            )
        component_pins = pins.get(component.name, {})
        input_pin = str(limiter.get("inputPin") or "")
        output_pin = str(limiter.get("outputPin") or "")
        actual_input = component_pins.get(input_pin.casefold())
        actual_output = component_pins.get(output_pin.casefold())
        topology_failures: list[str] = []
        if raw is None or actual_input is None or actual_input.key != raw.key:
            topology_failures.append(
                f"{input_pin or 'input'} is on {actual_input.label if actual_input else 'no readable net'}, not {raw_name}"
            )
        if protected is None or actual_output is None or actual_output.key != protected.key:
            topology_failures.append(
                f"{output_pin or 'output'} is on {actual_output.label if actual_output else 'no readable net'}, not {protected_name}"
            )
        if topology_failures:
            out.append(
                finding(
                    limiter_ref,
                    "power_intent_usb_limiter_topology",
                    f"{limiter_ref} does not form the declared raw-to-protected USB boundary: "
                    + "; ".join(topology_failures),
                    "error",
                )
            )

    # The TPS2553 identity alone does not establish its trip range: the ILIM
    # resistor programs that range. Tie the declared 400.6..500mA contract to
    # the exact populated value and to the compiled ILIM-to-GND topology.
    setting = limiter.get("settingResistor")
    setting = setting if isinstance(setting, dict) else {}
    setting_ref = str(setting.get("ref") or "")
    setting_component = board.by_name.get(setting_ref)
    if setting_component is None or setting_component.name not in placed_names:
        out.append(
            finding(
                setting_ref or "USB current-limit setting resistor",
                "power_intent_usb_limiter_setting_missing",
                f"product power budget requires populated current-limit setting resistor {setting_ref!r}, but it is absent or DNP",
                "error",
            )
        )
    else:
        expected_setting_lcsc = str(setting.get("lcsc") or "")
        if (
            setting_component.ftype != "simple_resistor"
            or not expected_setting_lcsc
            or setting_component.lcsc != expected_setting_lcsc
        ):
            out.append(
                finding(
                    setting_ref,
                    "power_intent_usb_limiter_setting_identity",
                    f"{setting_ref} compiles as {setting_component.ftype or 'unknown part'} / LCSC {setting_component.lcsc!r}; the approved current-limit setting resistor is {expected_setting_lcsc!r}",
                    "error",
                )
            )

        expected_resistance = _finite_number(setting.get("resistanceOhms"))
        actual_resistance = setting_component.resistance
        resistance_tolerance = max(
            1e-6,
            abs(expected_resistance or 0.0) * 1e-9,
        )
        if (
            expected_resistance is None
            or actual_resistance is None
            or abs(actual_resistance - expected_resistance) > resistance_tolerance
        ):
            out.append(
                finding(
                    setting_ref,
                    "power_intent_usb_limiter_setting_value",
                    f"{setting_ref} compiles as {actual_resistance!r} ohms; the declared current-limit range requires {expected_resistance!r} ohms",
                    "error",
                )
            )

        setting_pin = str(limiter.get("settingPin") or "")
        limiter_setting_net = (
            pins.get(limiter_ref, {}).get(setting_pin.casefold())
            if limiter_ref and setting_pin
            else None
        )
        return_name = str(setting.get("returnNet") or "")
        return_net = board.net_named(return_name) if return_name else None
        resistor_nets = _component_nets(pins, setting_component)
        if (
            limiter_setting_net is None
            or return_net is None
            or limiter_setting_net.key == return_net.key
            or resistor_nets != {limiter_setting_net.key, return_net.key}
        ):
            labels = sorted(
                net.label
                for net in pins.get(setting_component.name, {}).values()
            )
            out.append(
                finding(
                    setting_ref,
                    "power_intent_usb_limiter_setting_topology",
                    f"{setting_ref} is on {labels or ['no readable nets']}; it must be the sole resistor from {limiter_ref}.{setting_pin} to {return_name}",
                    "error",
                )
            )

    fixed_load = _finite_number(usb.get("fixedOperationalLoadMa")) or 0.0
    operational_total = fixed_load
    physical_total = 0.0
    firmware_refs: set[str] = set()
    loads = usb.get("firmwareLimitedLoads")
    loads = loads if isinstance(loads, list) else []
    for index, load in enumerate(loads):
        if not isinstance(load, dict):
            continue
        patterns = _patterns(load.get("match"))
        matches = [
            component
            for component in board.placed()
            if any(fnmatch.fnmatchcase(component.name, pattern) for pattern in patterns)
        ]
        firmware_refs.update(component.name for component in matches)
        label = ",".join(patterns) or f"load[{index}]"
        if not matches:
            out.append(
                finding(
                    label,
                    "power_intent_usb_load_missing",
                    f"firmware load rule {label!r} matches no populated component; the declared current cap is not tied to hardware",
                    "error",
                )
            )
            continue
        off_rail = [
            item.name
            for item in matches
            if protected is None or protected.key not in _component_nets(pins, item)
        ]
        if off_rail:
            out.append(
                finding(
                    label,
                    "power_intent_usb_load_topology",
                    f"firmware-limited load component(s) {', '.join(sorted(off_rail))} are not attached to protected net {protected_name}; the current budget does not cover their real supply path",
                    "error",
                )
            )
        per_device = _finite_number(load.get("perDevicePhysicalPeakMa")) or 0.0
        operational = _finite_number(load.get("aggregateOperationalMaxMa")) or 0.0
        physical = len(matches) * per_device
        physical_total += physical
        operational_total += operational
        if operational > physical + 1e-9:
            out.append(
                finding(
                    label,
                    "power_intent_usb_load_budget",
                    f"{len(matches)} matched device(s) have {physical:g}mA declared physical peak, but their aggregate operational cap is {operational:g}mA",
                    "error",
                )
            )

    if protected is not None:
        compiled_fixed, fixed_unknown = _compiled_fixed_usb_peak(
            board, policy, protected, firmware_refs
        )
        if fixed_unknown:
            out.append(
                finding(
                    protected_name,
                    "power_intent_usb_load_unknown",
                    "the protected fixed-load peak cannot be proven because "
                    + ", ".join(fixed_unknown)
                    + " have no audited load model",
                    "error",
                )
            )
        elif compiled_fixed is not None and compiled_fixed > fixed_load + 1e-9:
            out.append(
                finding(
                    protected_name,
                    "power_intent_usb_load_budget",
                    f"compiled uncapped fixed loads draw {compiled_fixed:.3f}mA peak, "
                    f"but fixedOperationalLoadMa declares only {fixed_load:g}mA; "
                    "the declaration must conservatively cover the built hardware",
                    "error",
                )
            )

    min_trip = _finite_number(limiter.get("minTripMa"))
    source_max = _finite_number(usb.get("sourceCurrentMaxMa"))
    max_trip = _finite_number(limiter.get("maxTripMa"))
    if (
        min_trip is None
        or max_trip is None
        or source_max is None
        or min_trip > max_trip + 1e-9
        or max_trip > source_max + 1e-9
        or operational_total > min_trip + 1e-9
    ):
        out.append(
            finding(
                protected_name or "USB load",
                "power_intent_usb_load_budget",
                f"declared USB operating load is {operational_total:g}mA "
                f"({fixed_load:g}mA fixed plus firmware-limited loads; matched physical peak {physical_total:g}mA), "
                f"limiter range is {min_trip!r}..{max_trip!r}mA, and source maximum is {source_max!r}mA; "
                "the operating load must stay at or below the worst-case trip and the best-case trip must stay within the source contract",
                "error",
            )
        )
    return out


def check(board: Board, intent: dict[str, Any] | None = None) -> CheckResult:
    policy = intent if isinstance(intent, dict) else {}
    findings = _usb(board, policy) + _regulators(board, policy)
    usb_declared = 1 if isinstance(policy.get("usb"), dict) else 0
    raw_regulators = policy.get("regulators")
    regulator_count = len(raw_regulators) if isinstance(raw_regulators, list) else 0
    declared = usb_declared + regulator_count
    coverage = Coverage(
        unit="declared power boundaries",
        total=declared,
        examined=declared,
    )
    if not policy:
        coverage.skip(
            "product.json has no powerBudget policy; USB attach/current intent is unknown"
        )
    elif isinstance(policy.get("usb"), dict) and policy["usb"].get(
        "firmwareLimitedLoads"
    ):
        coverage.skip(
            "firmware implementation is outside circuit.json; this verifies its declared electrical ceiling, not that application code enforces it"
        )
    return CheckResult(
        name="power_intent",
        findings=findings,
        coverage=coverage,
        notes=[
            "raw attach capacitance is summed only before the declared limiter boundary",
            "the current-limit range is tied to the populated setting resistor, value, and return topology",
            "firmware limits never erase the matched load family's physical peak",
            "regulator thermal constants and capacitor technology come from an audited profile, never product-authored numbers",
            "regulator load is measured from compiled datasheet-peak consumers at worst-case input and ambient",
        ],
    )
