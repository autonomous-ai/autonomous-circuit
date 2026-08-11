"""Heat: the failure that survives every other gate."""

from __future__ import annotations

import fixtures
import pytest

from verifylib import thermal
from verifylib.model import Board


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        f["kind"]
        for f in result.findings
        if severity is None or f["severity"] == severity
    }


def _named_pins(elements: list[dict], index: int, names: list[str]) -> None:
    sid = f"source_component_{index}"
    ports = [
        e
        for e in elements
        if e.get("type") == "source_port" and e.get("source_component_id") == sid
    ]
    for port, name in zip(ports, names):
        port["name"] = name


def _resistor_across_rail(ohms: float, *, size: tuple[float, float]):
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements += fixtures.component(
        "R1", index=1, x=0, y=0, width=size[0], height=size[1], resistance=ohms
    )
    fixtures.connect(elements, "R1", 0, "V5")
    fixtures.connect(elements, "R1", 1, "GND")
    return Board(elements)


# --- package inference ----------------------------------------------------


def test_a_chip_package_is_named_from_its_land_pattern():
    board = _resistor_across_rail(1000, size=(1.56, 0.64))
    assert (
        thermal.infer_package(
            board.by_name["R1"], thermal.CHIP_LAND_MM, thermal.LAND_MATCH_MM
        )
        == "0402"
    )


def test_a_bigger_land_pattern_is_a_bigger_package():
    board = _resistor_across_rail(1000, size=(2.85, 1.3))
    assert (
        thermal.infer_package(
            board.by_name["R1"], thermal.CHIP_LAND_MM, thermal.LAND_MATCH_MM
        )
        == "0805"
    )


def test_an_unrecognisable_land_pattern_returns_none_rather_than_a_guess():
    board = _resistor_across_rail(1000, size=(20.0, 14.0))
    assert (
        thermal.infer_package(
            board.by_name["R1"], thermal.CHIP_LAND_MM, thermal.LAND_MATCH_MM
        )
        is None
    )


def test_an_unrecognised_package_is_reported_as_coverage_not_ignored():
    result = thermal.check(_resistor_across_rail(1000, size=(20.0, 14.0)))
    assert any("R1" in b for b in result.coverage.blind)


# --- resistor dissipation -------------------------------------------------


def test_a_0402_asked_to_dissipate_a_quarter_watt_blocks():
    """100 ohms across 5V is 250mW in a package rated 62.5. Every geometric
    gate passes this board."""
    result = thermal.check(_resistor_across_rail(100, size=(1.56, 0.64)))
    assert "thermal_resistor_power" in kinds(result, "error")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "thermal_resistor_power"
    )
    assert "250mW" in detail and "0402" in detail


def test_the_same_resistance_in_a_2512_is_fine():
    result = thermal.check(_resistor_across_rail(100, size=(7.2, 3.4)))
    assert "thermal_resistor_power" not in kinds(result, "error")


def test_a_resistor_near_its_rating_warns_before_it_fails():
    # 100mW in a 0402 (62.5mW) blocks; aim for ~50mW, which is 80% of rating.
    result = thermal.check(_resistor_across_rail(500, size=(1.56, 0.64)))
    assert "thermal_resistor_power" in kinds(result, "warning")


def test_a_10k_pullup_is_never_mentioned():
    result = thermal.check(_resistor_across_rail(10_000, size=(1.56, 0.64)))
    assert "thermal_resistor_power" not in kinds(result)


# --- regulator dissipation ------------------------------------------------


def _ldo_board(*, led_count: int, package: tuple[float, float]):
    """5V in, AMS1117-3.3 out, feeding ``led_count`` WS2812s."""
    elements = [fixtures.board(40, 30)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements.append(fixtures.net(2, "VLED", is_power=True))
    elements += fixtures.component(
        "U2", index=1, x=0, y=8, width=package[0], height=package[1],
        ftype="simple_chip", lcsc="C6186", manufacturer_part_number="AMS1117-3.3",
        pads=[(-1, 0, 0.6, 0.6), (1, 0, 0.6, 0.6)],
    )
    _named_pins(elements, 1, ["VIN", "VOUT"])
    fixtures.connect(elements, "U2", 0, "V5")
    fixtures.connect(elements, "U2", 1, "VLED")
    for i in range(led_count):
        elements += fixtures.component(
            f"D{i + 1}", index=20 + i, x=-15 + i, y=-5, ftype="simple_chip",
            lcsc="C2761795", manufacturer_part_number="WS2812B-B/T",
        )
        fixtures.connect(elements, f"D{i + 1}", 0, "VLED")
        fixtures.connect(elements, f"D{i + 1}", 1, "GND")
    return Board(elements)


def test_a_lightly_loaded_ldo_reports_its_temperature_and_nothing_more():
    result = thermal.check(_ldo_board(led_count=2, package=(8.4, 5.7)))
    assert "thermal_regulator" in kinds(result, "info")
    assert "thermal_regulator" not in kinds(result, "error")


def test_an_ldo_asked_to_drop_too_much_current_is_caught():
    """The classic hobby-board mistake: an AMS1117 dropping 5V to 3.3V at
    most of an amp. 20 WS2812s at their datasheet peak is 1.2A."""
    result = thermal.check(_ldo_board(led_count=20, package=(8.4, 5.7)))
    assert "thermal_regulator" in kinds(result, "error")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "thermal_regulator"
    )
    assert "SOT-223" in detail and "125" in detail


def test_the_same_load_in_a_sot23_is_far_worse():
    """250 degC/W against 62 — the package choice is the whole answer."""
    small = thermal.check(_ldo_board(led_count=4, package=(2.9, 2.4)))
    large = thermal.check(_ldo_board(led_count=4, package=(8.4, 5.7)))
    assert "thermal_regulator" in kinds(small, "error")
    assert "thermal_regulator" not in kinds(large, "error")


def test_heat_is_graded_at_the_datasheet_peak_not_the_typical():
    """A WS2812 draws 20mA typical and 60mA at full white. Grading a
    regulator at typical grades the easy case."""
    from verifylib import dc

    board = _ldo_board(led_count=8, package=(8.4, 5.7))
    typical = dc.build_network(board, load_mode="typical")
    peak = dc.build_network(board, load_mode="peak")
    assert sum(s.amps for s in peak.sinks) == pytest.approx(
        3 * sum(s.amps for s in typical.sinks), rel=0.01
    )


def test_a_thermal_table_matching_circuitlib_is_used():
    """Duplicated deliberately so this package stands alone; the two must not
    drift apart silently."""
    assert thermal.THETA_JA_C_PER_W["SOT-223"] == 62.0
    assert thermal.THETA_JA_C_PER_W["SOT-23"] == 250.0
    assert thermal.MAX_JUNCTION_C == 125.0


def test_coverage_admits_it_cannot_see_the_copper():
    blind = " ".join(thermal.check(_ldo_board(led_count=2, package=(8.4, 5.7))).coverage.blind)
    assert "copper area" in blind
    assert "airflow" in blind


def test_a_hot_black_box_is_listed_even_without_a_package():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V3_3", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements += fixtures.component(
        "U9", index=1, x=0, y=0, ftype="simple_chip", lcsc="C2913201",
        manufacturer_part_number="ESP32-S3-WROOM-1",
    )
    fixtures.connect(elements, "U9", 0, "V3_3")
    fixtures.connect(elements, "U9", 1, "GND")
    result = thermal.check(Board(elements))
    assert "thermal_hot_part" in kinds(result, "info")
