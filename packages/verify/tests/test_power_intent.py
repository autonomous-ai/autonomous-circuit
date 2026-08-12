"""USB source/current contracts are measured from compiled artifacts."""

from __future__ import annotations

from verifylib import model, power_intent

from fixtures import board, component, connect, net


def policy() -> dict:
    return {
        "usb": {
            "rawVbusNet": "VBUS_RAW",
            "protectedVbusNet": "V5",
            "rawAttachCapacitanceMaxUf": 10,
            "sourceCurrentMaxMa": 500,
            "fixedOperationalLoadMa": 100,
            "currentLimiter": {
                "ref": "U7",
                "lcsc": "C55266",
                "inputPin": "pin1",
                "outputPin": "pin2",
                "settingPin": "pin3",
                "settingResistor": {
                    "ref": "R31",
                    "lcsc": "C32297",
                    "resistanceOhms": 59000,
                    "returnNet": "GND",
                },
                "minTripMa": 400.6,
                "maxTripMa": 497,
            },
            "firmwareLimitedLoads": [
                {
                    "match": "D1[0-7]",
                    "perDevicePhysicalPeakMa": 60,
                    "aggregateOperationalMaxMa": 300,
                }
            ],
        }
    }


def fixture() -> list[dict]:
    elements = [
        board(),
        net(0, "GND", is_ground=True),
        net(1, "VBUS_RAW", is_power=True),
        net(2, "V5", is_power=True),
    ]
    elements += component(
        "C1",
        index=1,
        x=-8,
        y=0,
        ftype="simple_capacitor",
        capacitance=1e-6,
    )
    elements += component(
        "C24",
        index=2,
        x=-5,
        y=0,
        ftype="simple_capacitor",
        capacitance=1e-7,
    )
    elements += component(
        "U7",
        index=3,
        x=0,
        y=0,
        width=3,
        height=2,
        ftype="simple_chip",
        lcsc="C55266",
        pads=[
            (-1, -0.5, 0.5, 0.4),
            (1, -0.5, 0.5, 0.4),
            (0, 0.5, 0.5, 0.4),
        ],
    )
    elements += component(
        "R31",
        index=4,
        x=0,
        y=2,
        ftype="simple_resistor",
        lcsc="C32297",
        resistance=59000,
    )
    for offset, ref in enumerate((f"D{i}" for i in range(10, 18)), start=10):
        elements += component(
            ref,
            index=offset,
            x=offset - 10,
            y=5,
            ftype="simple_led",
        )

    for ref in ("C1", "C24"):
        connect(elements, ref, 0, "VBUS_RAW")
        connect(elements, ref, 1, "GND")
    connect(elements, "U7", 0, "VBUS_RAW")
    connect(elements, "U7", 1, "V5")
    connect(elements, "U7", 2, "ILIM")
    connect(elements, "R31", 0, "ILIM")
    connect(elements, "R31", 1, "GND")
    for ref in (f"D{i}" for i in range(10, 18)):
        connect(elements, ref, 0, "V5")
        connect(elements, ref, 1, "GND")
    return elements


def error_kinds(result) -> set[str]:
    return {
        item["kind"]
        for item in result.findings
        if item.get("severity") == "error"
    }


def test_clean_usb_power_boundary_passes() -> None:
    result = power_intent.check(model.Board(fixture()), policy())
    assert result.findings == []
    assert result.coverage is not None
    assert result.coverage.examined == 1


def test_raw_attach_capacitance_is_summed_before_the_limiter() -> None:
    elements = fixture()
    cap = next(
        element
        for element in elements
        if element.get("type") == "source_component" and element.get("name") == "C1"
    )
    cap["capacitance"] = 12e-6
    result = power_intent.check(model.Board(elements), policy())
    assert "power_intent_usb_raw_capacitance" in error_kinds(result)
    assert "12.1uF" in next(
        item["detail"]
        for item in result.findings
        if item["kind"] == "power_intent_usb_raw_capacitance"
    )


def test_unknown_raw_cap_value_fails_closed() -> None:
    elements = fixture()
    cap = next(
        element
        for element in elements
        if element.get("type") == "source_component" and element.get("name") == "C24"
    )
    del cap["capacitance"]
    result = power_intent.check(model.Board(elements), policy())
    assert "power_intent_usb_raw_capacitance_unknown" in error_kinds(result)


def test_limiter_identity_and_pin_boundary_are_measured() -> None:
    elements = fixture()
    limiter = next(
        element
        for element in elements
        if element.get("type") == "source_component" and element.get("name") == "U7"
    )
    limiter["supplier_part_numbers"] = {"jlcpcb": ["C00000"]}
    connect(elements, "U7", 1, "VBUS_RAW")
    result = power_intent.check(model.Board(elements), policy())
    assert {
        "power_intent_usb_limiter_identity",
        "power_intent_usb_limiter_topology",
    } <= error_kinds(result)


def test_limiter_setting_resistor_identity_value_and_topology_are_measured() -> None:
    elements = fixture()
    setting = next(
        element
        for element in elements
        if element.get("type") == "source_component" and element.get("name") == "R31"
    )
    setting["supplier_part_numbers"] = {"jlcpcb": ["C00000"]}
    setting["resistance"] = 47000
    connect(elements, "R31", 0, "VBUS_RAW")

    result = power_intent.check(model.Board(elements), policy())
    assert {
        "power_intent_usb_limiter_setting_identity",
        "power_intent_usb_limiter_setting_value",
        "power_intent_usb_limiter_setting_topology",
    } <= error_kinds(result)


def test_limiter_setting_resistor_must_be_populated() -> None:
    elements = fixture()
    setting_pcb = next(
        element
        for element in elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") == "source_component_4"
    )
    setting_pcb["do_not_place"] = True

    result = power_intent.check(model.Board(elements), policy())
    assert "power_intent_usb_limiter_setting_missing" in error_kinds(result)


def test_firmware_load_rule_must_match_populated_protected_hardware() -> None:
    bad = policy()
    bad["usb"]["firmwareLimitedLoads"][0]["match"] = "LED*"
    missing = power_intent.check(model.Board(fixture()), bad)
    assert "power_intent_usb_load_missing" in error_kinds(missing)

    elements = fixture()
    connect(elements, "D10", 0, "VBUS_RAW")
    off_rail = power_intent.check(model.Board(elements), policy())
    assert "power_intent_usb_load_topology" in error_kinds(off_rail)


def test_firmware_limit_does_not_erase_physical_peak_or_exceed_trip() -> None:
    too_high = policy()
    too_high["usb"]["firmwareLimitedLoads"][0]["aggregateOperationalMaxMa"] = 480
    result = power_intent.check(model.Board(fixture()), too_high)
    finding = next(
        item
        for item in result.findings
        if item["kind"] == "power_intent_usb_load_budget"
    )
    assert finding["severity"] == "error"
    assert "matched physical peak 480" in finding["detail"]


def test_omitted_contract_reports_coverage_without_inventing_requirements() -> None:
    result = power_intent.check(model.Board(fixture()), None)
    assert result.findings == []
    assert result.coverage is not None
    assert result.coverage.examined == 0
    assert result.coverage.blind


def test_standalone_verifier_rejects_a_malformed_unvalidated_contract() -> None:
    malformed = policy()
    malformed["usb"]["sourceCurrentMaxMa"] = -1
    result = power_intent.check(model.Board(fixture()), malformed)
    assert "power_intent_usb_contract" in error_kinds(result)

    missing_setting = policy()
    missing_setting["usb"]["currentLimiter"].pop("settingResistor")
    result = power_intent.check(model.Board(fixture()), missing_setting)
    assert "power_intent_usb_contract" in error_kinds(result)
