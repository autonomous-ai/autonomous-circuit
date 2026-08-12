"""Validation for product-level power budgets.

Electrical geometry cannot prove that a USB-powered product respects the
source it plugs into.  ``product.json.powerBudget`` makes the source contract
explicit: how much capacitance is presented at attach, which exact part
current-limits raw VBUS, and how firmware-bounded loads fit below the worst
case hardware trip point.  The independent artifact verifier measures the
compiled board against the validated dictionary returned here.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, NoReturn

from circuitpy.errors import ProjectShapeError

_TOP_LEVEL_KEYS = {"usb", "regulators"}
_USB_KEYS = {
    "rawVbusNet",
    "protectedVbusNet",
    "rawAttachCapacitanceMaxUf",
    "sourceCurrentMaxMa",
    "fixedOperationalLoadMa",
    "currentLimiter",
    "firmwareLimitedLoads",
}
_LIMITER_KEYS = {
    "ref",
    "lcsc",
    "inputPin",
    "outputPin",
    "settingPin",
    "settingResistor",
    "minTripMa",
    "maxTripMa",
}
_SETTING_RESISTOR_KEYS = {
    "ref",
    "lcsc",
    "resistanceOhms",
    "returnNet",
}
_LOAD_KEYS = {
    "match",
    "perDevicePhysicalPeakMa",
    "aggregateOperationalMaxMa",
}
_REGULATOR_KEYS = {
    "profile",
    "ref",
    "inputNet",
    "outputNet",
    "inputCapRef",
    "outputCapRef",
    "maxAmbientC",
}

# A regulator profile is reviewed engineering evidence, not a bag of thermal
# numbers a generated product may choose for itself.  The independent
# verifylib copy owns the artifact-side constants (part identity, pinout,
# capacitor identities and thermal model); this registry merely limits the
# product schema to profiles the pipeline knows how to verify.
AUDITED_REGULATOR_PROFILES = frozenset({"ap7361c-33e-c500795-v1"})


def _fail(path: str, detail: str) -> NoReturn:
    raise ProjectShapeError(f"product.json '{path}' {detail}")


def _number(
    value: object,
    path: str,
    *,
    allow_zero: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            path,
            f"must be a {'non-negative' if allow_zero else 'positive'} "
            f"finite number (got {value!r})",
        )
    number = float(value)
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        _fail(
            path,
            f"must be a {'non-negative' if allow_zero else 'positive'} "
            f"finite number (got {value!r})",
        )
    return number


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, f"must be a non-empty string (got {value!r})")
    return value.strip()


def _patterns(value: object, path: str) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item.strip() for item in value)
    ):
        return [item.strip() for item in value]
    _fail(path, f"must be a non-empty string or list of strings (got {value!r})")


def validate_power_budget(raw: object) -> dict[str, Any]:
    """Return a defensive copy of a valid ``powerBudget`` contract.

    Unknown members fail closed.  A misspelled current or capacitance limit is
    more dangerous than a malformed product file stopping before routing.
    """

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _fail("powerBudget", f"must be an object (got {type(raw).__name__})")
    unknown = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if unknown:
        _fail("powerBudget", f"contains unknown member(s): {', '.join(unknown)}")

    regulators = raw.get("regulators", [])
    if not isinstance(regulators, list):
        _fail("powerBudget.regulators", "must be a list")
    seen_refs: set[str] = set()
    seen_outputs: set[str] = set()
    seen_caps: set[str] = set()
    for index, regulator in enumerate(regulators):
        path = f"powerBudget.regulators[{index}]"
        if not isinstance(regulator, dict):
            _fail(path, f"must be an object (got {type(regulator).__name__})")
        unknown = sorted(set(regulator) - _REGULATOR_KEYS)
        missing = sorted(_REGULATOR_KEYS - set(regulator))
        if unknown:
            _fail(path, f"contains unknown member(s): {', '.join(unknown)}")
        if missing:
            _fail(path, f"is missing member(s): {', '.join(missing)}")
        profile = _string(regulator.get("profile"), f"{path}.profile")
        if profile not in AUDITED_REGULATOR_PROFILES:
            _fail(
                f"{path}.profile",
                "must select an audited regulator profile "
                f"({', '.join(sorted(AUDITED_REGULATOR_PROFILES))})",
            )
        ref = _string(regulator.get("ref"), f"{path}.ref")
        input_net = _string(regulator.get("inputNet"), f"{path}.inputNet")
        output_net = _string(regulator.get("outputNet"), f"{path}.outputNet")
        input_cap = _string(regulator.get("inputCapRef"), f"{path}.inputCapRef")
        output_cap = _string(regulator.get("outputCapRef"), f"{path}.outputCapRef")
        max_ambient = _number(
            regulator.get("maxAmbientC"), f"{path}.maxAmbientC"
        )
        # Ambient is a product requirement, not a part constant.  Requiring at
        # least the indoor-consumer design floor prevents a generated product
        # from making the thermal proof pass by claiming an unrealistically
        # cold room; the part's audited operating ceiling remains 85degC.
        if max_ambient < 50.0 or max_ambient > 85.0:
            _fail(f"{path}.maxAmbientC", "must be between 50 and 85 degC")
        if input_net == output_net:
            _fail(f"{path}.outputNet", "must differ from inputNet")
        if len({ref, input_cap, output_cap}) != 3:
            _fail(path, "requires distinct regulator/input-cap/output-cap references")
        if ref in seen_refs:
            _fail(f"{path}.ref", f"duplicates regulator reference {ref!r}")
        if output_net in seen_outputs:
            _fail(
                f"{path}.outputNet",
                f"duplicates regulated output {output_net!r}; two sources may not fight",
            )
        duplicate_caps = sorted({input_cap, output_cap} & seen_caps)
        if duplicate_caps:
            _fail(
                path,
                "reuses capacitor reference(s) already owned by another regulator: "
                + ", ".join(duplicate_caps),
            )
        seen_refs.add(ref)
        seen_outputs.add(output_net)
        seen_caps.update((input_cap, output_cap))

    usb = raw.get("usb")
    if usb is None:
        return deepcopy(raw)
    if not isinstance(usb, dict):
        _fail("powerBudget.usb", f"must be an object (got {type(usb).__name__})")
    unknown = sorted(set(usb) - _USB_KEYS)
    if unknown:
        _fail("powerBudget.usb", f"contains unknown member(s): {', '.join(unknown)}")

    raw_net = _string(usb.get("rawVbusNet"), "powerBudget.usb.rawVbusNet")
    protected_net = _string(
        usb.get("protectedVbusNet"), "powerBudget.usb.protectedVbusNet"
    )
    if raw_net == protected_net:
        _fail(
            "powerBudget.usb.protectedVbusNet",
            "must differ from rawVbusNet so the limiter has a real boundary",
        )
    _number(
        usb.get("rawAttachCapacitanceMaxUf"),
        "powerBudget.usb.rawAttachCapacitanceMaxUf",
    )
    source_max = _number(
        usb.get("sourceCurrentMaxMa"), "powerBudget.usb.sourceCurrentMaxMa"
    )
    fixed_load = _number(
        usb.get("fixedOperationalLoadMa", 0),
        "powerBudget.usb.fixedOperationalLoadMa",
        allow_zero=True,
    )

    limiter = usb.get("currentLimiter")
    if not isinstance(limiter, dict):
        _fail("powerBudget.usb.currentLimiter", "must be an object")
    unknown = sorted(set(limiter) - _LIMITER_KEYS)
    if unknown:
        _fail(
            "powerBudget.usb.currentLimiter",
            f"contains unknown member(s): {', '.join(unknown)}",
        )
    for key in ("ref", "lcsc", "inputPin", "outputPin", "settingPin"):
        _string(limiter.get(key), f"powerBudget.usb.currentLimiter.{key}")
    setting_resistor = limiter.get("settingResistor")
    if not isinstance(setting_resistor, dict):
        _fail("powerBudget.usb.currentLimiter.settingResistor", "must be an object")
    unknown = sorted(set(setting_resistor) - _SETTING_RESISTOR_KEYS)
    if unknown:
        _fail(
            "powerBudget.usb.currentLimiter.settingResistor",
            f"contains unknown member(s): {', '.join(unknown)}",
        )
    for key in ("ref", "lcsc", "returnNet"):
        _string(
            setting_resistor.get(key),
            f"powerBudget.usb.currentLimiter.settingResistor.{key}",
        )
    _number(
        setting_resistor.get("resistanceOhms"),
        "powerBudget.usb.currentLimiter.settingResistor.resistanceOhms",
    )
    min_trip = _number(
        limiter.get("minTripMa"), "powerBudget.usb.currentLimiter.minTripMa"
    )
    max_trip = _number(
        limiter.get("maxTripMa"), "powerBudget.usb.currentLimiter.maxTripMa"
    )
    if min_trip > max_trip:
        _fail(
            "powerBudget.usb.currentLimiter.minTripMa",
            "must not exceed maxTripMa",
        )
    if max_trip > source_max + 1e-9:
        _fail(
            "powerBudget.usb.currentLimiter.maxTripMa",
            f"must not exceed sourceCurrentMaxMa ({source_max:g}mA)",
        )

    loads = usb.get("firmwareLimitedLoads", [])
    if not isinstance(loads, list):
        _fail("powerBudget.usb.firmwareLimitedLoads", "must be a list")
    operational_total = fixed_load
    for index, load in enumerate(loads):
        path = f"powerBudget.usb.firmwareLimitedLoads[{index}]"
        if not isinstance(load, dict):
            _fail(path, f"must be an object (got {type(load).__name__})")
        unknown = sorted(set(load) - _LOAD_KEYS)
        if unknown:
            _fail(path, f"contains unknown member(s): {', '.join(unknown)}")
        _patterns(load.get("match"), f"{path}.match")
        _number(load.get("perDevicePhysicalPeakMa"), f"{path}.perDevicePhysicalPeakMa")
        operational_total += _number(
            load.get("aggregateOperationalMaxMa"),
            f"{path}.aggregateOperationalMaxMa",
            allow_zero=True,
        )
    if operational_total > min_trip + 1e-9:
        _fail(
            "powerBudget.usb",
            f"declares {operational_total:g}mA maximum operational load, above "
            f"the limiter's {min_trip:g}mA worst-case trip point",
        )

    return deepcopy(raw)
