"""Net-class checks: current capacity, via bottlenecks, pair skew.

The arithmetic is checked against hand-computed IPC-2221B values first — a
current-capacity check with the wrong constants is a confidently wrong gate,
which is worse than none.
"""

from __future__ import annotations

import fixtures
import pytest

from verifylib import netclass
from verifylib.loads import lookup
from verifylib.model import Board
from verifylib.rules import ipc2221_current_a, ipc2221_width_mm


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        f["kind"]
        for f in result.findings
        if severity is None or f["severity"] == severity
    }


# --- the arithmetic -------------------------------------------------------


def test_ipc2221_width_matches_the_published_worked_example():
    """1A, 10 degC rise, 1oz external is ~0.30mm (12 mil) in every published
    IPC-2221 calculator."""
    assert ipc2221_width_mm(1.0) == pytest.approx(0.30, abs=0.02)


def test_ipc2221_internal_needs_roughly_double_the_copper():
    external = ipc2221_width_mm(1.0, layer="external")
    internal = ipc2221_width_mm(1.0, layer="internal")
    assert 2.0 < internal / external < 3.0


def test_width_and_current_are_inverses():
    for current in (0.1, 0.5, 1.0, 3.0):
        width = ipc2221_width_mm(current)
        assert ipc2221_current_a(width) == pytest.approx(current, rel=1e-6)


def test_the_router_default_carries_about_half_an_amp():
    """The number the whole gap rests on: 0.15mm is legal copper and it is only
    good for ~0.5A."""
    assert ipc2221_current_a(0.15) == pytest.approx(0.60, abs=0.05)


def test_a_via_barrel_carries_less_than_an_amp():
    assert 0.6 < netclass.via_current_a(0.3) < 1.2


# --- load lookup ----------------------------------------------------------


def test_an_unknown_part_returns_unknown_not_zero():
    assert lookup(lcsc="C999999", mpn="MYSTERY-IC", ftype="simple_chip") is None


def test_a_known_part_carries_its_source():
    load = lookup(lcsc="C2761795", mpn=None, ftype="simple_chip")
    assert load is not None and load.peak_ma == 60.0
    assert "WS2812B" in load.source


def test_passives_are_known_to_draw_nothing():
    load = lookup(lcsc=None, mpn=None, ftype="simple_capacitor")
    assert load is not None and load.peak_ma == 0.0


# --- fixtures -------------------------------------------------------------


def _rail_board(*, led_count: int, width: float) -> Board:
    """A 5V rail feeding ``led_count`` WS2812s, routed at ``width``."""
    elements = [fixtures.board(50, 40)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    for i in range(led_count):
        elements += fixtures.component(
            f"D{i + 1}",
            index=10 + i,
            x=-20 + i * 5,
            y=0,
            ftype="simple_chip",
            lcsc="C2761795",
            manufacturer_part_number="WS2812B-B/T",
            courtyard=(5.5, 5.5),
        )
        fixtures.connect(elements, f"D{i + 1}", 0, "V5")
        fixtures.connect(elements, f"D{i + 1}", 1, "GND")
    elements.append(fixtures.trace_on("t_v5", 0, [(-20, 5), (20, 5)], width=width))
    elements.append(fixtures.trace_on("t_gnd", 1, [(-20, -5), (20, -5)], width=width))
    return Board(elements)


def test_an_overloaded_rail_is_caught():
    board = _rail_board(led_count=16, width=0.15)  # 960mA on 0.15mm
    result = netclass.check(board)
    assert "netclass_trace_width" in kinds(result)
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "netclass_trace_width"
    )
    assert "960mA" in detail and "0.15" in detail
    assert "WS2812" in detail or "D1" in detail


def test_a_badly_overloaded_rail_blocks():
    board = _rail_board(led_count=40, width=0.15)  # 2.4A on 0.15mm
    result = netclass.check(board)
    assert "netclass_trace_width" in kinds(result, "error")


def test_the_same_rail_widened_passes():
    board = _rail_board(led_count=16, width=0.60)
    result = netclass.check(board)
    assert "netclass_trace_width" not in kinds(result)


def test_a_light_rail_is_not_flagged():
    board = _rail_board(led_count=2, width=0.15)  # 120mA
    result = netclass.check(board)
    assert "netclass_trace_width" not in kinds(result)


def test_unknown_loads_are_reported_as_coverage_not_counted_as_zero():
    elements = [fixtures.board(50, 40)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements += fixtures.component(
        "U9", index=9, x=0, y=0, ftype="simple_chip", lcsc="C000000",
        manufacturer_part_number="MYSTERY-500", courtyard=(4, 4),
    )
    fixtures.connect(elements, "U9", 0, "V5")
    elements.append(fixtures.trace_on("t", 0, [(-10, 0), (10, 0)], width=0.15))
    result = netclass.check(Board(elements))
    blind = " ".join(result.coverage.blind)
    assert "U9" in blind, "an unmodelled part must be named, not silently zeroed"


def test_exact_ahct_level_shifter_load_covers_ttl_high_and_switching_terms():
    load = lookup(
        lcsc="C7484",
        mpn="SN74AHCT1G125DBVR",
        ftype="simple_chip",
    )
    assert load is not None
    assert load.typical_ma == pytest.approx(1.5)
    assert load.peak_ma == pytest.approx(2.0)
    assert "delta-Icc" in load.source and "14pF" in load.source


def test_regulator_input_carries_its_output_current():
    """An LDO passes everything downstream through its input pin. Without the
    propagation V5 would look idle while 3.3V is loaded to a full amp."""
    elements = [fixtures.board(50, 40)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "V3_3", is_power=True))
    elements += fixtures.component(
        "U2", index=2, x=0, y=10, ftype="simple_chip", lcsc="C6186",
        manufacturer_part_number="AMS1117-3.3", courtyard=(4, 4),
        pads=[(-1, 0, 0.6, 0.6), (1, 0, 0.6, 0.6)],
    )
    # pin1 = VIN, pin2 = VOUT
    for element in elements:
        if element.get("type") == "source_port" and element.get(
            "source_component_id"
        ) == "source_component_2":
            element["name"] = "VIN" if element["pin_number"] == 1 else "VOUT"
    fixtures.connect(elements, "U2", 0, "V5")
    fixtures.connect(elements, "U2", 1, "V3_3")
    for i in range(16):
        elements += fixtures.component(
            f"D{i + 1}", index=20 + i, x=-20 + i * 2, y=-10, ftype="simple_chip",
            lcsc="C2761795", manufacturer_part_number="WS2812B-B/T",
            courtyard=(1.4, 1.0),
        )
        fixtures.connect(elements, f"D{i + 1}", 0, "V3_3")
    elements.append(fixtures.trace_on("t_v5", 0, [(-10, 12), (10, 12)], width=0.15))
    elements.append(fixtures.trace_on("t_33", 1, [(-20, -8), (20, -8)], width=0.8))
    result = netclass.check(Board(elements))
    v5 = [
        f for f in result.findings
        if f["kind"] == "netclass_trace_width" and f["part"] == "V5"
    ]
    assert v5, "V5 must inherit the 3.3V rail's load through the regulator"
    assert "U2" in v5[0]["detail"]


def test_ap7361_load_is_propagated_without_false_unknown_regulator_load():
    elements = [fixtures.board(50, 40)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "V3_3", is_power=True))
    elements += fixtures.component(
        "U2",
        index=2,
        x=0,
        y=10,
        ftype="simple_chip",
        lcsc="C500795",
        manufacturer_part_number="AP7361C-33E-13",
        courtyard=(8, 5.7),
        pads=[(-2, 0, 1.2, 1.6), (2, 0, 1.2, 1.6)],
    )
    for element in elements:
        if element.get("type") == "source_port" and element.get(
            "source_component_id"
        ) == "source_component_2":
            element["name"] = "IN" if element["pin_number"] == 1 else "OUT"
    fixtures.connect(elements, "U2", 0, "V5")
    fixtures.connect(elements, "U2", 1, "V3_3")
    for offset in range(6):
        ref = f"U{3 + offset}"
        elements += fixtures.component(
            ref,
            index=3 + offset,
            x=-10 + offset * 4,
            y=0,
            ftype="simple_chip",
            lcsc="C2040",
            manufacturer_part_number="RP2040",
        )
        fixtures.connect(elements, ref, 0, "V3_3")
    elements.append(
        fixtures.trace_on("t_v5", 0, [(-10, 12), (10, 12)], width=0.15)
    )

    built = Board(elements)
    loads = netclass._Loads(built)
    v5 = built.net_named("V5")
    assert loads.per_net[v5.key] == pytest.approx(600)
    assert loads.contributors[v5.key] == [
        "600mA drawn through U2 from V3_3"
    ]
    assert "U2" not in " ".join(sum(loads.unknown.values(), []))


# --- pair skew ------------------------------------------------------------


def _pair_board(length_p: float, length_n: float) -> Board:
    elements = [fixtures.board(60, 40)]
    elements.append(fixtures.net(0, "USB_DP"))
    elements.append(fixtures.net(1, "USB_DM"))
    elements.append(fixtures.trace_on("tp", 0, [(0, 2), (length_p, 2)]))
    elements.append(fixtures.trace_on("tn", 1, [(0, -2), (length_n, -2)]))
    return Board(elements)


def test_a_matched_pair_passes():
    result = netclass.check(_pair_board(30.0, 31.0))
    assert "netclass_pair_skew" not in kinds(result)


def test_a_skewed_pair_is_caught_with_both_lengths():
    result = netclass.check(_pair_board(30.0, 48.0))
    assert "netclass_pair_skew" in kinds(result)
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "netclass_pair_skew"
    )
    assert "30.00mm" in detail and "48.00mm" in detail and "18.00mm" in detail


# --- the plain observation ------------------------------------------------


def test_one_width_for_the_whole_board_is_said_out_loud():
    board = _rail_board(led_count=2, width=0.15)
    result = netclass.check(board)
    assert "netclass_uniform_width" in kinds(result, "info")


def test_a_board_with_real_net_classes_does_not_get_the_note():
    elements = [fixtures.board(50, 40)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "SIG"))
    elements.append(fixtures.trace_on("t_v5", 0, [(-10, 5), (10, 5)], width=0.5))
    elements.append(fixtures.trace_on("t_sig", 1, [(-10, -5), (10, -5)], width=0.15))
    result = netclass.check(Board(elements))
    assert "netclass_uniform_width" not in kinds(result)


def test_a_check_that_raises_becomes_a_finding_not_a_crash():
    board = Board([{"type": "pcb_board", "width": "not a number"}])
    result = netclass.check(board)
    assert isinstance(result.findings, list)
