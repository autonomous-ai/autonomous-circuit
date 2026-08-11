"""The electrical design-review checks.

These are the defects that cost the most: they pass every geometric gate, the
board arrives, gets assembled, and misbehaves. So each one is seeded here and
must trip, and each one has a clean counterpart proving it can stay quiet.
"""

from __future__ import annotations

import fixtures

from verifylib import review
from verifylib.model import Board


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        f["kind"]
        for f in result.findings
        if severity is None or f["severity"] == severity
    }


def _named_pins(elements: list[dict], component_index: int, names: list[str]) -> None:
    sid = f"source_component_{component_index}"
    ports = [
        e
        for e in elements
        if e.get("type") == "source_port" and e.get("source_component_id") == sid
    ]
    for port, name in zip(ports, names):
        port["name"] = name


def _mcu_board(*, decoupling_at: tuple[float, float] | None, cap_farads=1e-7):
    """A chip with one VDD pin, optionally decoupled at a given position."""
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V3_3", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements += fixtures.component(
        "U1", index=1, x=0, y=0, width=4, height=4, ftype="simple_chip",
        manufacturer_part_number="RP2040",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 1, ["VDD", "GND"])
    fixtures.connect(elements, "U1", 0, "V3_3")
    fixtures.connect(elements, "U1", 1, "GND")
    if decoupling_at is not None:
        elements += fixtures.component(
            "C1", index=2, x=decoupling_at[0], y=decoupling_at[1],
            ftype="simple_capacitor", capacitance=cap_farads,
        )
        fixtures.connect(elements, "C1", 0, "V3_3")
        fixtures.connect(elements, "C1", 1, "GND")
    return elements


def test_an_undecoupled_supply_pin_is_caught():
    result = review.check(Board(_mcu_board(decoupling_at=None)))
    assert "review_decoupling_missing" in kinds(result, "warning")


def test_a_decoupled_supply_pin_is_clean():
    result = review.check(Board(_mcu_board(decoupling_at=(2.0, 0.0))))
    assert "review_decoupling_missing" not in kinds(result)
    assert "review_decoupling_distant" not in kinds(result)


def test_a_distant_capacitor_is_advisory_and_carries_the_distance():
    """Distance has no published floor, unlike a fab rule. It is reported with
    its measurement at info; absence is the thing that warns."""
    result = review.check(Board(_mcu_board(decoupling_at=(12.0, 0.0))))
    assert "review_decoupling_distant" in kinds(result, "info")
    assert "review_decoupling_distant" not in kinds(result, "warning")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "review_decoupling_distant"
    )
    assert "mm away" in detail


def test_a_bulk_capacitor_counts_as_local_decoupling_when_it_is_close():
    """An AMS1117's input wants 10uF, and refusing to count it because it is
    over a microfarad reported three false positives on three example boards."""
    result = review.check(Board(_mcu_board(decoupling_at=(2.0, 0.0), cap_farads=1e-5)))
    assert "review_decoupling_missing" not in kinds(result)


def test_an_esd_array_is_not_asked_for_decoupling():
    """A USBLC6's VBUS pin is a clamp reference, not a rail input."""
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements += fixtures.component(
        "U1", index=1, x=0, y=0, ftype="simple_chip",
        manufacturer_part_number="USBLC6-2SC6",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 1, ["VBUS", "GND"])
    fixtures.connect(elements, "U1", 0, "V5")
    fixtures.connect(elements, "U1", 1, "GND")
    result = review.check(Board(elements))
    assert "review_decoupling_missing" not in kinds(result)


def test_a_rail_with_no_bulk_capacitance_is_flagged():
    result = review.check(Board(_mcu_board(decoupling_at=(2.0, 0.0))))
    assert "review_bulk_missing" in kinds(result, "warning")


def test_a_rail_with_bulk_is_not():
    elements = _mcu_board(decoupling_at=(2.0, 0.0))
    elements += fixtures.component(
        "C2", index=3, x=-6, y=4, ftype="simple_capacitor", capacitance=1e-5
    )
    fixtures.connect(elements, "C2", 0, "V3_3")
    fixtures.connect(elements, "C2", 1, "GND")
    result = review.check(Board(elements))
    assert "review_bulk_missing" not in kinds(result)


def test_a_crystal_without_load_caps_is_caught():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "GND", is_ground=True))
    elements.append(fixtures.net(1, "XIN"))
    elements.append(fixtures.net(2, "XOUT"))
    elements += fixtures.component("Y1", index=1, x=0, y=0, ftype="simple_crystal")
    fixtures.connect(elements, "Y1", 0, "XIN")
    fixtures.connect(elements, "Y1", 1, "XOUT")
    result = review.check(Board(elements))
    assert "review_crystal_load_caps" in kinds(result, "warning")


def test_a_crystal_with_both_load_caps_is_clean():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "GND", is_ground=True))
    elements.append(fixtures.net(1, "XIN"))
    elements.append(fixtures.net(2, "XOUT"))
    elements += fixtures.component("Y1", index=1, x=0, y=0, ftype="simple_crystal")
    fixtures.connect(elements, "Y1", 0, "XIN")
    fixtures.connect(elements, "Y1", 1, "XOUT")
    for i, net in enumerate(("XIN", "XOUT")):
        elements += fixtures.component(
            f"C{i + 1}", index=10 + i, x=i * 3, y=-3,
            ftype="simple_capacitor", capacitance=1.5e-11,
        )
        fixtures.connect(elements, f"C{i + 1}", 0, net)
        fixtures.connect(elements, f"C{i + 1}", 1, "GND")
    result = review.check(Board(elements))
    assert "review_crystal_load_caps" not in kinds(result)


def test_a_floating_input_is_caught():
    elements = _mcu_board(decoupling_at=(2.0, 0.0))
    elements += fixtures.component(
        "U2", index=5, x=8, y=0, ftype="simple_chip",
        manufacturer_part_number="MCP23017",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 5, ["RESET", "GND"])
    fixtures.connect(elements, "U2", 1, "GND")
    result = review.check(Board(elements))
    assert "review_floating_pin" in kinds(result, "warning")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "review_floating_pin"
    )
    assert "RESET" in detail


def test_a_spare_output_is_not_reported_as_floating():
    """The last WS2812 in a chain always has an unconnected DOUT. Calling that
    a defect is how a gate earns a reputation for crying wolf."""
    elements = _mcu_board(decoupling_at=(2.0, 0.0))
    elements += fixtures.component(
        "D9", index=5, x=8, y=0, ftype="simple_chip",
        manufacturer_part_number="WS2812B-B/T",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 5, ["DOUT", "GND"])
    fixtures.connect(elements, "D9", 1, "GND")
    result = review.check(Board(elements))
    assert "review_floating_pin" not in kinds(result)


def test_an_unprotected_off_board_signal_is_named_by_its_pin():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "GND", is_ground=True))
    elements += fixtures.component(
        "J1", index=1, x=0, y=0, ftype="simple_connector",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 1, ["CC1", "GND"])
    fixtures.connect(elements, "J1", 1, "GND")
    result = review.check(Board(elements))
    assert "review_esd_unprotected" in kinds(result, "info")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "review_esd_unprotected"
    )
    assert "J1.CC1" in detail, "an unnamed net must be reported by its pin"


def test_a_clamped_signal_is_not_reported():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "GND", is_ground=True))
    elements.append(fixtures.net(1, "DP"))
    elements += fixtures.component(
        "J1", index=1, x=0, y=0, ftype="simple_connector",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 1, ["DP", "GND"])
    fixtures.connect(elements, "J1", 0, "DP")
    fixtures.connect(elements, "J1", 1, "GND")
    elements += fixtures.component(
        "U1", index=2, x=6, y=0, ftype="simple_chip",
        manufacturer_part_number="USBLC6-2SC6",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 2, ["IO1", "GND"])
    fixtures.connect(elements, "U1", 0, "DP")
    fixtures.connect(elements, "U1", 1, "GND")
    result = review.check(Board(elements))
    assert "review_esd_unprotected" not in kinds(result)


def test_a_rail_with_no_probe_point_is_reported():
    result = review.check(Board(_mcu_board(decoupling_at=(2.0, 0.0))))
    assert "review_no_test_point" in kinds(result, "info")


def test_a_debug_interface_that_reaches_nothing_warns():
    elements = _mcu_board(decoupling_at=(2.0, 0.0))
    elements.append(fixtures.net(9, "SWCLK"))
    elements += fixtures.component(
        "U2", index=5, x=8, y=0, ftype="simple_chip",
        manufacturer_part_number="RP2040",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 5, ["SWCLK", "GND"])
    fixtures.connect(elements, "U2", 0, "SWCLK")
    fixtures.connect(elements, "U2", 1, "GND")
    result = review.check(Board(elements))
    assert "review_debug_unreachable" in kinds(result, "warning")
    # and it is not double-reported as a floating pin
    floating = [f for f in result.findings if f["kind"] == "review_floating_pin"]
    assert not any("SWCLK" in f["detail"] for f in floating)


def test_a_debug_interface_brought_to_a_header_is_clean():
    elements = _mcu_board(decoupling_at=(2.0, 0.0))
    elements.append(fixtures.net(9, "SWCLK"))
    elements += fixtures.component(
        "U2", index=5, x=8, y=0, ftype="simple_chip",
        manufacturer_part_number="RP2040",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 5, ["SWCLK", "GND"])
    fixtures.connect(elements, "U2", 0, "SWCLK")
    fixtures.connect(elements, "U2", 1, "GND")
    elements += fixtures.component(
        "J2", index=6, x=-8, y=6, ftype="simple_connector",
        pads=[(-1, 0, 0.5, 0.5), (1, 0, 0.5, 0.5)],
    )
    _named_pins(elements, 6, ["SWCLK", "GND"])
    fixtures.connect(elements, "J2", 0, "SWCLK")
    fixtures.connect(elements, "J2", 1, "GND")
    result = review.check(Board(elements))
    assert "review_debug_unreachable" not in kinds(result)


def test_coverage_admits_what_a_netlist_cannot_answer():
    blind = " ".join(review.check(Board(_mcu_board(decoupling_at=None))).coverage.blind)
    assert "datasheet" in blind
    assert "sequencing" in blind
