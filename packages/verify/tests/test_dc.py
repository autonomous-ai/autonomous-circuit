"""The DC operating point and the corner sweep.

The solver is checked against hand arithmetic first — a divider, a series
resistance, an LED — because a solver that is confidently wrong is worse than
no solver at all. Then the classic value mistakes are seeded and must trip.
"""

from __future__ import annotations

import fixtures
import pytest

from verifylib import corners, dc
from verifylib.model import Board


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        f["kind"]
        for f in result.findings
        if severity is None or f["severity"] == severity
    }


def _rail_board(*, series_ohms: float, led: bool = True, color: str = "green"):
    """5V rail -> R1 -> LED1 -> GND, plus a 10k/10k divider off the rail."""
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements.append(fixtures.net(2, "LEDA"))
    elements += fixtures.component("R1", index=1, x=-5, y=0, resistance=series_ohms)
    fixtures.connect(elements, "R1", 0, "V5")
    fixtures.connect(elements, "R1", 1, "LEDA")
    if led:
        elements += fixtures.component(
            "LED1", index=2, x=0, y=0, ftype="simple_led", color=color, lcsc="C2297"
        )
        fixtures.connect(elements, "LED1", 0, "LEDA")   # anode
        fixtures.connect(elements, "LED1", 1, "GND")    # cathode
    return elements


def _divider(top_ohms: float, bottom_ohms: float):
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements.append(fixtures.net(2, "MID"))
    elements += fixtures.component("R1", index=1, x=-5, y=0, resistance=top_ohms)
    fixtures.connect(elements, "R1", 0, "V5")
    fixtures.connect(elements, "R1", 1, "MID")
    elements += fixtures.component("R2", index=2, x=5, y=0, resistance=bottom_ohms)
    fixtures.connect(elements, "R2", 0, "MID")
    fixtures.connect(elements, "R2", 1, "GND")
    return elements


# --- arithmetic -----------------------------------------------------------


def test_a_divider_lands_where_arithmetic_says_it_does():
    board = Board(_divider(10_000, 10_000))
    solution = dc.solve(dc.build_network(board))
    assert solution.converged
    mid = board.net_named("MID")
    assert solution.voltages[mid.key] == pytest.approx(2.5, abs=1e-4)


def test_an_uneven_divider_too():
    board = Board(_divider(9_000, 1_000))
    solution = dc.solve(dc.build_network(board))
    mid = board.net_named("MID")
    assert solution.voltages[mid.key] == pytest.approx(0.5, abs=1e-4)


def test_the_gmin_leak_does_not_move_a_solved_voltage():
    """A gigaohm to ground is what stops the matrix being singular on any
    board with a floating net. It must be invisible in the answer."""
    board = Board(_divider(10_000, 10_000))
    solution = dc.solve(dc.build_network(board))
    mid = board.net_named("MID")
    assert abs(solution.voltages[mid.key] - 2.5) < 1e-7


def test_led_current_matches_ohms_law_through_the_series_resistor():
    """(5 - Vf) / R, with the junction's own exponential softening it a little.
    The golden block quotes ~1.2mA for a 1k from 3.3V; this is the 5V case."""
    board = Board(_rail_board(series_ohms=2_200))
    network = dc.build_network(board)
    solution = dc.solve(network)
    current_ma = solution.currents["LED1"] * 1000.0
    assert 1.2 < current_ma < 1.6


def test_a_solved_board_satisfies_kirchhoff():
    board = Board(_rail_board(series_ohms=1_000))
    network = dc.build_network(board)
    solution = dc.solve(network)
    keys = sorted({n.key for n in network.nets})
    residual = dc._kcl_residual(network, solution.voltages, {k: i for i, k in enumerate(keys)})
    assert residual < dc.KCL_TOLERANCE


# --- the mistakes no other check can see ----------------------------------


def test_the_ten_ohm_resistor_where_ten_k_belongs_is_caught():
    """The failure the whole gap list is named after. This board compiles,
    passes ERC, DRC, DFM and every rendered image."""
    board = Board(_rail_board(series_ohms=10))
    result = dc.check(board)
    assert "dc_led_current" in kinds(result, "error")
    detail = next(f["detail"] for f in result.findings if f["kind"] == "dc_led_current")
    assert "mA" in detail and "20mA" in detail


def test_the_same_board_with_the_right_resistor_is_clean():
    board = Board(_rail_board(series_ohms=1_000))
    result = dc.check(board)
    assert result.blocking == 0, result.findings


def test_a_resistor_far_too_large_reports_a_dim_led():
    board = Board(_rail_board(series_ohms=2_000_000))
    result = dc.check(board)
    assert "dc_led_current" in kinds(result, "warning")


def test_an_led_that_cannot_clear_its_forward_drop_is_named():
    """A blue LED on a 3.3V rail after a big series resistance never lights."""
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V3_3", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements.append(fixtures.net(2, "LEDA"))
    elements += fixtures.component("R1", index=1, x=-5, y=0, resistance=100_000)
    fixtures.connect(elements, "R1", 0, "V3_3")
    fixtures.connect(elements, "R1", 1, "LEDA")
    elements += fixtures.component(
        "LED1", index=2, x=0, y=0, ftype="simple_led", color="blue"
    )
    fixtures.connect(elements, "LED1", 0, "LEDA")
    fixtures.connect(elements, "LED1", 1, "GND")
    result = dc.check(Board(elements))
    assert "dc_led_current" in kinds(result, "warning")


def test_a_gpio_driven_led_is_not_reported_as_broken():
    """Its anode is on a signal net nothing drives, so it reads 0mA at rest —
    by design. Reporting that is how a gate earns a reputation for crying
    wolf."""
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "GND", is_ground=True))
    elements.append(fixtures.net(1, "GPIO7"))
    elements += fixtures.component(
        "LED1", index=1, x=0, y=0, ftype="simple_led", color="green"
    )
    fixtures.connect(elements, "LED1", 0, "GPIO7")
    fixtures.connect(elements, "LED1", 1, "GND")
    result = dc.check(Board(elements))
    assert "dc_led_current" not in kinds(result)


def test_an_i2c_pullup_out_of_band_is_flagged():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V3_3", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements.append(fixtures.net(2, "SDA"))
    elements += fixtures.component("R1", index=1, x=0, y=0, resistance=100.0)
    fixtures.connect(elements, "R1", 0, "V3_3")
    fixtures.connect(elements, "R1", 1, "SDA")
    result = dc.check(Board(elements))
    assert "dc_pullup_value" in kinds(result, "warning")


def test_a_sensible_i2c_pullup_is_not_flagged():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V3_3", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements.append(fixtures.net(2, "SDA"))
    elements += fixtures.component("R1", index=1, x=0, y=0, resistance=4_700.0)
    fixtures.connect(elements, "R1", 0, "V3_3")
    fixtures.connect(elements, "R1", 1, "SDA")
    result = dc.check(Board(elements))
    assert "dc_pullup_value" not in kinds(result)


def test_a_rail_with_load_and_no_source_is_named_not_reported_as_minus_a_million_volts():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "GND", is_ground=True))
    elements.append(fixtures.net(1, "VLED", is_power=True))
    for i in range(4):
        elements += fixtures.component(
            f"D{i + 1}", index=10 + i, x=i * 3, y=0, ftype="simple_chip",
            lcsc="C2761795", manufacturer_part_number="WS2812B-B/T",
        )
        fixtures.connect(elements, f"D{i + 1}", 0, "VLED")
        fixtures.connect(elements, f"D{i + 1}", 1, "GND")
    result = dc.check(Board(elements))
    assert "dc_unpowered_rail" in kinds(result, "warning")
    assert "dc_rail_voltage" not in kinds(result)


def test_a_regulator_states_its_output_voltage_in_its_part_number():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V5", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements.append(fixtures.net(2, "VOUT_LOCAL", is_power=True))
    elements += fixtures.component(
        "U2", index=1, x=0, y=0, ftype="simple_chip", lcsc="C6186",
        manufacturer_part_number="AMS1117-3.3",
        pads=[(-1, 0, 0.6, 0.6), (1, 0, 0.6, 0.6)],
    )
    for element in elements:
        if (
            element.get("type") == "source_port"
            and element.get("source_component_id") == "source_component_1"
        ):
            element["name"] = "VIN" if element["pin_number"] == 1 else "VOUT"
    fixtures.connect(elements, "U2", 0, "V5")
    fixtures.connect(elements, "U2", 1, "VOUT_LOCAL")
    board = Board(elements)
    network = dc.build_network(board)
    local = board.net_named("VOUT_LOCAL")
    assert network.sources[local.key] == pytest.approx(3.3)
    assert ("U2", local.key, 3.3) in network.regulators


def test_ap7361_standard_pin_voltage_code_is_exact_and_reverse_pin_is_not() -> None:
    assert dc._regulator_output_volts("AP7361C-33E-13") == pytest.approx(3.3)
    assert dc._regulator_output_volts("AP7361C-33E") == pytest.approx(3.3)
    assert dc._regulator_output_volts("AP7361C-33ER-13") is None
    assert dc._regulator_output_volts("AP7361C-lookalike") is None


def test_a_board_with_no_named_rail_says_so_rather_than_reporting_zeroes():
    elements = [fixtures.board(30, 20)]
    elements += fixtures.component("R1", index=1, x=0, y=0, resistance=1000)
    result = dc.check(Board(elements))
    assert result.findings == []
    assert "no named power rail" in " ".join(result.notes)


def test_coverage_names_the_parts_it_could_not_model():
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V3_3", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements += fixtures.component(
        "U9", index=1, x=0, y=0, ftype="simple_chip", lcsc="C000000",
        manufacturer_part_number="MYSTERY",
    )
    fixtures.connect(elements, "U9", 0, "V3_3")
    result = dc.check(Board(elements))
    assert any("U9" in b for b in result.coverage.blind)


def test_a_button_scenario_finds_a_switch_that_shorts_a_rail():
    """A tact switch wired rail-to-ground is dead quiet until someone presses
    it. The resting solve says nothing; the pressed one has to."""
    elements = [fixtures.board(30, 20)]
    elements.append(fixtures.net(0, "V3_3", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    elements += fixtures.component(
        "SW1", index=1, x=0, y=0, ftype="simple_push_button",
    )
    fixtures.connect(elements, "SW1", 0, "V3_3")
    fixtures.connect(elements, "SW1", 1, "GND")
    board = Board(elements)
    assert dc.check(board, scenario="resting").blocking == 0
    pressed = dc.check(board, scenario="pressed")
    assert "dc_rail_overload" in kinds(pressed, "error")


# --- corners --------------------------------------------------------------


def test_a_design_that_only_passes_at_nominal_is_caught():
    """Sized so the LED sits just inside 20mA at nominal and steps outside it
    once the rail is high and the forward voltage low. Nothing that checks
    only the centre of the distribution can see this."""
    board = Board(_rail_board(series_ohms=160))
    nominal = dc.check(board)
    assert "dc_led_current" not in kinds(nominal, "error"), (
        "the nominal check must be clean, or this proves nothing"
    )
    swept = corners.check(board, trials=120)
    assert "corner_led_current" in kinds(swept, "warning")
    detail = next(
        f["detail"] for f in swept.findings if f["kind"] == "corner_led_current"
    )
    assert "nominal" in detail and "corner" in detail


def test_a_design_with_margin_reports_its_margin_rather_than_a_warning():
    board = Board(_rail_board(series_ohms=2_200))
    swept = corners.check(board, trials=80)
    assert "corner_led_current" in kinds(swept, "info")
    assert "corner_led_current" not in kinds(swept, "warning")


def test_the_sweep_is_reproducible():
    board = Board(_rail_board(series_ohms=1_000))
    first = corners.sweep(board, trials=40)
    second = corners.sweep(board, trials=40)
    assert first.led_current_ma["LED1"].high == second.led_current_ma["LED1"].high
    assert first.led_current_ma["LED1"].low == second.led_current_ma["LED1"].low


def test_the_deterministic_corners_are_always_included():
    """With zero random trials the sweep still runs every combination of
    tolerance kinds at both extremes."""
    from verifylib.rules import TOLERANCE

    board = Board(_rail_board(series_ohms=1_000))
    result = corners.check(board, trials=0)
    assert result.coverage.examined == 2 ** len(TOLERANCE)
    assert "LED1" in {f["part"] for f in result.findings}


def test_the_mixed_corner_is_reached_without_any_random_draws():
    """Rail high with forward voltage low is the corner that pushes an
    indicator hardest, and it is in neither the all-low nor the all-high
    sweep. It has to be found deterministically or not at all."""
    board = Board(_rail_board(series_ohms=160))
    swept = corners.check(board, trials=0)
    assert "corner_led_current" in kinds(swept, "warning")


def test_corner_coverage_admits_what_it_does_not_vary():
    board = Board(_rail_board(series_ohms=1_000))
    blind = " ".join(corners.check(board, trials=10).coverage.blind)
    assert "temperature" in blind
    assert "DC-bias" in blind
