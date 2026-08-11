"""What the planner refuses, before a line of TSX exists.

Three defects used to be found on a finished board — or on its gerbers, or not
at all — when the plan already contained every number needed to prevent them:
a rail routed on signal copper, a linear regulator asked to shed more heat than
its package can, and an MCU whose debug pins reach nothing.

Each one is checked here in both directions: it must fire on the composition
that has it, and it must stay quiet on the composition that does not. A gate
that has never been seen refusing is a gate nobody should trust.
"""

from __future__ import annotations

import unittest

from circuitlib.blocks import BLOCKS, total_peak_ma, unexposed_nets
from circuitlib.helpers import board_plan, trace_width_for, validate_board_law

PUCK = ["usb-c-power", "ldo-3v3", "rp2040-core", "ws2812-chain"]


def severities(warnings, kind):
    return [w["severity"] for w in warnings if w["kind"] == kind]


class PeakCurrent(unittest.TestCase):
    def test_a_parametric_block_scales_with_its_count(self):
        """A WS2812 idles at 4mA and pulls 60mA at full white. That factor of
        fifteen used to live in a BLOCK.md sentence asking a human to
        remember to multiply."""
        one = total_peak_ma(["ws2812-chain"], {"ws2812-chain": 1})
        eight = total_peak_ma(["ws2812-chain"], {"ws2812-chain": 8})
        self.assertAlmostEqual(eight - one, 7 * 60.0, places=3)

    def test_the_typical_number_is_not_the_peak(self):
        chain = BLOCKS["ws2812-chain"]
        self.assertLess(chain.current_draw_ma, chain.peak_ma(8) / 10)

    def test_a_missing_count_is_understated_not_invented(self):
        """One unit is assumed, which is low. The planner reports the number it
        used rather than guessing a plausible chain length."""
        self.assertEqual(
            total_peak_ma(["ws2812-chain"]),
            total_peak_ma(["ws2812-chain"], {"ws2812-chain": 1}),
        )


class PowerTraceWidth(unittest.TestCase):
    def test_the_plan_states_the_copper_its_own_current_needs(self):
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
        )
        self.assertGreater(plan.peak_current_ma, 0)
        self.assertAlmostEqual(
            plan.power_trace_width_mm,
            trace_width_for(current_a=plan.peak_current_ma / 1000.0),
            places=6,
        )

    def test_declaring_copper_too_thin_for_the_plan_is_an_error(self):
        out = validate_board_law(
            block_ids=PUCK,
            counts={"ws2812-chain": 8},
            power_trace_width_mm=0.10,
        )
        self.assertIn("error", severities(out, "power_trace_width"))

    def test_declaring_enough_copper_says_nothing(self):
        out = validate_board_law(
            block_ids=PUCK,
            counts={"ws2812-chain": 8},
            power_trace_width_mm=0.6,
        )
        self.assertEqual(severities(out, "power_trace_width"), [])

    def test_declaring_nothing_still_hands_over_the_number(self):
        out = validate_board_law(block_ids=PUCK, counts={"ws2812-chain": 8})
        self.assertIn("info", severities(out, "power_trace_width"))


class RegulatorHeat(unittest.TestCase):
    def test_a_linear_rail_asked_for_too_much_is_flagged_at_plan_time(self):
        out = validate_board_law(block_ids=PUCK, counts={"ws2812-chain": 8})
        self.assertTrue(severities(out, "regulator_thermal"))

    def test_a_light_board_is_not(self):
        out = validate_board_law(
            block_ids=["usb-c-power", "ldo-3v3", "sensor-bme280"], counts={}
        )
        self.assertEqual(severities(out, "regulator_thermal"), [])

    def test_a_plan_that_would_cook_the_regulator_is_not_buildable(self):
        """Twenty WS2812s behind an AMS1117 is 1.3A through a linear drop:
        183 degC junction against a 125 degC limit. That board cannot be
        planned, which is a better answer than finding out it runs hot."""
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 20},
        )
        self.assertTrue(plan.overheats)
        self.assertFalse(plan.buildable)

    def test_the_same_plan_with_a_short_chain_is_buildable(self):
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 2},
        )
        self.assertFalse(plan.overheats)

    def test_heat_is_planned_at_a_warm_room_not_a_laboratory(self):
        """A datasheet quotes 25 degC. A puck in a warm room with an enclosure
        round it does not, and planning at 25 is planning for a bench."""
        from circuitlib.helpers import AMBIENT_HOT_C

        self.assertGreaterEqual(AMBIENT_HOT_C, 40.0)


class DebugAccess(unittest.TestCase):
    def test_an_mcu_owns_pins_that_have_to_come_out(self):
        self.assertEqual(
            set(BLOCKS["rp2040-core"].exposes), {"SWCLK", "SWD"}
        )

    def test_a_board_with_no_route_to_them_is_refused(self):
        out = validate_board_law(block_ids=PUCK, counts={"ws2812-chain": 8})
        self.assertEqual(
            sorted(w["part"] for w in out if w["kind"] == "debug_unreachable"),
            ["board", "board"],
        )
        self.assertTrue(
            all(
                w["severity"] == "error"
                for w in out
                if w["kind"] == "debug_unreachable"
            )
        )

    def test_bringing_them_to_a_test_point_satisfies_it(self):
        out = validate_board_law(
            block_ids=PUCK,
            counts={"ws2812-chain": 8},
            exposed_nets=["SWCLK", "SWD"],
        )
        self.assertEqual(severities(out, "debug_unreachable"), [])

    def test_the_requirement_is_satisfiable_with_what_exists_today(self):
        """A planner obligation nothing can discharge makes every MCU board
        unbuildable. A bare <testpoint> is copper, the BOM gate exempts it, and
        a board carrying one reaches fab.ready — so this one can be met."""
        self.assertEqual(unexposed_nets(["rp2040-core"], ["swclk", "swd"]), [])

    def test_a_board_with_no_mcu_is_never_asked(self):
        out = validate_board_law(block_ids=["usb-c-power", "status-led"])
        self.assertEqual(severities(out, "debug_unreachable"), [])

    def test_the_plan_says_what_must_come_out(self):
        plan = board_plan(capabilities=["mcu"])
        self.assertIn("SWCLK", plan.must_expose)


class NeverRaises(unittest.TestCase):
    def test_a_nonsense_block_id_does_not_break_the_plan(self):
        out = validate_board_law(block_ids=["not-a-block"], counts={"x": 3})
        self.assertIsInstance(out, list)

    def test_a_nonsense_count_does_not_break_the_budget(self):
        self.assertGreaterEqual(total_peak_ma(PUCK, {"ws2812-chain": -5}), 0.0)


if __name__ == "__main__":
    unittest.main()
