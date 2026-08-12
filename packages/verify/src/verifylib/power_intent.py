"""Verify a product's declared USB power budget against compiled hardware.

The ordinary DC and thermal checks infer what they can from part models.  A
USB source contract is different: attach capacitance, the exact current-limit
boundary, and a firmware-enforced operating cap are product decisions.  This
module consumes ``product.json.powerBudget`` as an independent second opinion
and fails closed when the routed artifact does not implement that contract.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board, Component, Net


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
    findings = _usb(board, policy)
    declared = 1 if isinstance(policy.get("usb"), dict) else 0
    coverage = Coverage(unit="declared power budgets", total=declared, examined=declared)
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
        ],
    )
