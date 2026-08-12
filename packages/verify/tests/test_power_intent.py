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


def regulator_policy() -> dict:
    return {
        "regulators": [
            {
                "profile": "ap7361c-33e-c500795-v1",
                "ref": "U2",
                "inputNet": "V5",
                "outputNet": "V3_3",
                "inputCapRef": "C2",
                "outputCapRef": "C3",
                "maxAmbientC": 60,
            }
        ]
    }


def _rename_pins(elements: list[dict], index: int, names: list[str]) -> None:
    ports = [
        element
        for element in elements
        if element.get("type") == "source_port"
        and element.get("source_component_id") == f"source_component_{index}"
    ]
    assert len(ports) == len(names)
    for port, name in zip(ports, names):
        port["name"] = name


def _port_id(elements: list[dict], ref: str, index: int) -> str:
    source_id = next(
        element["source_component_id"]
        for element in elements
        if element.get("type") == "source_component" and element.get("name") == ref
    )
    ports = [
        element
        for element in elements
        if element.get("type") == "source_port"
        and element.get("source_component_id") == source_id
    ]
    return str(ports[index]["source_port_id"])


def regulator_fixture(*, extra_flash: int = 0) -> list[dict]:
    elements = [
        board(),
        net(0, "GND", is_ground=True),
        net(1, "V5", is_power=True),
        net(2, "V3_3", is_power=True),
    ]
    elements += component(
        "U2",
        index=1,
        x=0,
        y=0,
        width=7,
        height=3.5,
        ftype="simple_chip",
        lcsc="C500795",
        manufacturer_part_number="AP7361C-33E-13",
        pads=[
            (-2.4, -1.8, 1.0, 1.5),
            (0, -1.8, 1.0, 1.5),
            (2.4, -1.8, 1.0, 1.5),
        ],
    )
    _rename_pins(elements, 1, ["IN", "GND", "OUT"])
    elements += component(
        "C2",
        index=2,
        x=-4.2,
        y=-1.8,
        width=1.6,
        height=0.8,
        ftype="simple_capacitor",
        lcsc="C19702",
        capacitance=10e-6,
        pads=[(0.5, 0, 0.5, 0.6), (-0.5, 0, 0.5, 0.6)],
    )
    elements += component(
        "C3",
        index=3,
        x=4.2,
        y=-1.8,
        width=1.6,
        height=0.8,
        ftype="simple_capacitor",
        lcsc="C19702",
        capacitance=10e-6,
        pads=[(-0.5, 0, 0.5, 0.6), (0.5, 0, 0.5, 0.6)],
    )
    elements += component(
        "U3",
        index=4,
        x=-2,
        y=5,
        ftype="simple_chip",
        lcsc="C2040",
        manufacturer_part_number="RP2040",
    )
    elements += component(
        "U4",
        index=5,
        x=2,
        y=5,
        ftype="simple_chip",
        lcsc="C97521",
        manufacturer_part_number="W25Q128JV",
    )

    for ref, pin, rail in (
        ("U2", 0, "V5"),
        ("U2", 1, "GND"),
        ("U2", 2, "V3_3"),
        ("C2", 0, "V5"),
        ("C2", 1, "GND"),
        ("C3", 0, "V3_3"),
        ("C3", 1, "GND"),
        ("U3", 0, "V3_3"),
        ("U3", 1, "GND"),
        ("U4", 0, "V3_3"),
        ("U4", 1, "GND"),
    ):
        connect(elements, ref, pin, rail)
    elements += [
        {
            "type": "source_trace",
            "source_trace_id": "source_trace_regulator_input_cap",
            "connected_source_port_ids": [
                _port_id(elements, "U2", 0),
                _port_id(elements, "C2", 0),
            ],
            "connected_source_net_ids": [],
        },
        {
            "type": "source_trace",
            "source_trace_id": "source_trace_regulator_output_cap",
            "connected_source_port_ids": [
                _port_id(elements, "U2", 2),
                _port_id(elements, "C3", 0),
            ],
            "connected_source_net_ids": [],
        },
    ]
    for offset in range(extra_flash):
        ref = f"U{10 + offset}"
        index = 10 + offset
        elements += component(
            ref,
            index=index,
            x=-4 + offset,
            y=8,
            ftype="simple_chip",
            lcsc="C97521",
            manufacturer_part_number="W25Q128JV",
        )
        connect(elements, ref, 0, "V3_3")
        connect(elements, ref, 1, "GND")
    return elements


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


def test_audited_regulator_identity_caps_topology_and_thermal_budget_pass() -> None:
    result = power_intent.check(model.Board(regulator_fixture()), regulator_policy())
    assert result.findings == []
    assert result.coverage is not None
    assert result.coverage.examined == 1


def test_regulator_identity_and_exact_three_pin_topology_are_measured() -> None:
    elements = regulator_fixture()
    regulator = next(
        element
        for element in elements
        if element.get("type") == "source_component" and element.get("name") == "U2"
    )
    regulator["supplier_part_numbers"] = {"jlcpcb": ["C00000"]}
    regulator["manufacturer_part_number"] = "AP7361C-lookalike"
    connect(elements, "U2", 1, "V3_3")
    result = power_intent.check(model.Board(elements), regulator_policy())
    assert {
        "power_intent_regulator_identity",
        "power_intent_regulator_topology",
    } <= error_kinds(result)


def test_regulator_cap_identity_value_authored_branch_face_and_distance_are_measured() -> None:
    elements = regulator_fixture()
    capacitor = next(
        element
        for element in elements
        if element.get("type") == "source_component" and element.get("name") == "C3"
    )
    capacitor["supplier_part_numbers"] = {"jlcpcb": ["C52923"]}
    capacitor["capacitance"] = 1e-6
    trace = next(
        element
        for element in elements
        if element.get("source_trace_id") == "source_trace_regulator_output_cap"
    )
    elements.remove(trace)
    pcb = next(
        element
        for element in elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") == "source_component_3"
    )
    pcb["layer"] = "bottom"
    result = power_intent.check(model.Board(elements), regulator_policy())
    assert {
        "power_intent_regulator_capacitor_identity",
        "power_intent_regulator_capacitor_value",
        "power_intent_regulator_capacitor_topology",
    } <= error_kinds(result)


def test_unknown_regulator_load_fails_closed() -> None:
    elements = regulator_fixture()
    elements += component(
        "U9",
        index=9,
        x=0,
        y=9,
        ftype="simple_chip",
        lcsc="C00000",
        manufacturer_part_number="UNKNOWN-LOAD",
    )
    connect(elements, "U9", 0, "V3_3")
    connect(elements, "U9", 1, "GND")
    result = power_intent.check(model.Board(elements), regulator_policy())
    assert "power_intent_regulator_load_unknown" in error_kinds(result)


def test_150ma_regulator_peak_has_required_margin_but_175ma_is_rejected() -> None:
    at_limit = power_intent.check(
        model.Board(regulator_fixture(extra_flash=1)), regulator_policy()
    )
    assert "power_intent_regulator_load_budget" not in error_kinds(at_limit)
    assert "power_intent_regulator_thermal" not in error_kinds(at_limit)

    too_hot = power_intent.check(
        model.Board(regulator_fixture(extra_flash=2)), regulator_policy()
    )
    assert "power_intent_regulator_load_budget" in error_kinds(too_hot)


def test_hot_product_ambient_is_measured_and_cannot_be_gamed_below_50c() -> None:
    hot = regulator_policy()
    hot["regulators"][0]["maxAmbientC"] = 70
    result = power_intent.check(
        model.Board(regulator_fixture(extra_flash=1)), hot
    )
    assert "power_intent_regulator_thermal" in error_kinds(result)

    detail = next(
        item["detail"]
        for item in result.findings
        if item["kind"] == "power_intent_regulator_thermal"
    )
    assert "102.22C" in detail
    assert "22.78C" in detail

    implausibly_cold = regulator_policy()
    implausibly_cold["regulators"][0]["maxAmbientC"] = 25
    result = power_intent.check(model.Board(regulator_fixture()), implausibly_cold)
    assert "power_intent_regulator_contract" in error_kinds(result)


def test_standalone_regulator_contract_cannot_invent_thermal_numbers() -> None:
    malformed = regulator_policy()
    malformed["regulators"][0]["thetaJaCPerW"] = 1
    result = power_intent.check(model.Board(regulator_fixture()), malformed)
    assert "power_intent_regulator_contract" in error_kinds(result)
