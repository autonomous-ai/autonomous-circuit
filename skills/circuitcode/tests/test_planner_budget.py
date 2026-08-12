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

from circuitlib.blocks import (
    BLOCKS,
    missing_requirements,
    peak_ma_for_rail,
    total_peak_ma,
    unexposed_nets,
)
from circuitlib.helpers import (
    board_plan,
    trace_width_for,
    usb_power_budget_for_plan,
    validate_board_law,
)

RAW_PUCK = [
    "usb-c-power",
    "usb-power-entry",
    "ldo-3v3",
    "rp2040-core",
    "ws2812-chain",
]
PUCK = [*RAW_PUCK, "ws2812-level-shifter"]


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
        self.assertEqual(chain.peak_ma(8), 480.0)

    def test_a_missing_count_is_understated_not_invented(self):
        """One unit is assumed, which is low. The planner reports the number it
        used rather than guessing a plausible chain length."""
        self.assertEqual(
            total_peak_ma(["ws2812-chain"]),
            total_peak_ma(["ws2812-chain"], {"ws2812-chain": 1}),
        )

    def test_a_raw_5v_chain_is_unmet_without_the_level_shifter(self):
        self.assertIn("LED_DATA_5V", missing_requirements(RAW_PUCK))
        self.assertNotIn("LED_DATA_5V", missing_requirements(PUCK))

    def test_peak_current_is_accounted_on_the_rail_that_carries_it(self):
        counts = {"ws2812-chain": 8}
        self.assertGreater(peak_ma_for_rail(PUCK, "V5", counts), 480.0)
        self.assertLess(peak_ma_for_rail(PUCK, "V3_3", counts), 150.0)

    def test_usb_connector_is_raw_and_the_planner_inserts_the_limiter(self):
        self.assertIn("VBUS_RAW", BLOCKS["usb-c-data"].provides)
        self.assertNotIn("V5", BLOCKS["usb-c-data"].provides)
        plan = board_plan(capabilities=["power-usb"])
        self.assertIn("usb-c-power", plan.block_ids)
        self.assertIn("usb-power-entry", plan.block_ids)
        self.assertIn("ldo-3v3", plan.block_ids)  # supplies the FAULT pull-up
        self.assertEqual(plan.unmet, ())

    def test_usb_mcu_uses_one_data_connector_and_the_protected_entry(self):
        plan = board_plan(capabilities=["mcu", "power-usb"])

        self.assertIn("usb-c-data", plan.block_ids)
        self.assertNotIn("usb-c-power", plan.block_ids)
        self.assertIn("usb-power-entry", plan.block_ids)
        self.assertEqual(plan.block_ids.count("usb-c-data"), 1)
        self.assertEqual(plan.unmet, ())

    def test_the_usb_source_contract_matches_its_own_block_and_part(self):
        entry = BLOCKS["usb-power-entry"]
        contract = entry.usb_source_contract

        self.assertIsNotNone(contract)
        self.assertIn(contract.raw_net, entry.requires)
        self.assertIn(contract.protected_net, entry.provides)
        self.assertIn(contract.setting_return_net, entry.requires)
        self.assertGreater(contract.setting_resistance_ohms, 0)
        self.assertIn(
            (contract.limiter_ref, contract.limiter_lcsc),
            {(part.refdes, part.lcsc) for part in entry.parts},
        )
        self.assertIn(
            (contract.setting_resistor_ref, contract.setting_resistor_lcsc),
            {(part.refdes, part.lcsc) for part in entry.parts},
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
        self.assertGreaterEqual(plan.power_trunk_width_mm, plan.power_trace_width_mm)
        self.assertGreaterEqual(plan.power_trunk_width_mm, 0.6)
        self.assertLess(plan.power_neckdown_width_mm, plan.power_trunk_width_mm)

    def test_the_plan_prefers_quarter_mm_ordinary_signals_without_raising_the_floor(self):
        from circuitlib import tables

        plan = board_plan(capabilities=["mcu"])
        self.assertEqual(plan.signal_trace_width_mm, 0.25)
        self.assertGreater(plan.signal_trace_width_mm, tables.MIN_TRACE_WIDTH_MM)
        self.assertEqual(tables.MIN_TRACE_WIDTH_MM, 0.127)

    def test_the_plan_defaults_to_ground_planes_on_both_faces(self):
        plan = board_plan(capabilities=["mcu"])

        self.assertEqual(plan.ground_plane_layers, ("top", "bottom"))

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
        out = validate_board_law(
            block_ids=PUCK,
            counts={"ws2812-chain": 8},
            supply_rail_overrides={"ws2812-chain": "V3_3"},
        )
        self.assertTrue(severities(out, "regulator_thermal"))

    def test_a_light_board_is_not(self):
        out = validate_board_law(
            block_ids=["usb-c-power", "ldo-3v3", "sensor-bme280"], counts={}
        )
        self.assertEqual(severities(out, "regulator_thermal"), [])

    def test_a_direct_linear_plan_above_ap7361_limit_is_not_buildable(self):
        """Twenty WS2812s behind the 3V3 LDO exceed both the AP7361's
        product-level 150mA ceiling and its thermal envelope. The architecture
        must be rejected while it is still a plan."""
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 20},
            supply_rail_overrides={"ws2812-chain": "V3_3"},
        )
        self.assertTrue(plan.overheats)
        self.assertFalse(plan.buildable)

    def test_an_eight_pixel_direct_linear_plan_is_not_generated(self):
        """The Harness-scale chain exceeds a conservative SOT-223 board
        model. The planner must choose a cooler rail architecture while it
        still can; a package name is not permission to assume enhanced copper."""
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
            supply_rail_overrides={"ws2812-chain": "V3_3"},
        )
        self.assertEqual(plan.regulator["severity"], "error")
        self.assertTrue(plan.overheats)
        self.assertFalse(plan.buildable)

    def test_even_a_short_direct_pixel_chain_exceeds_the_150ma_profile(self):
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 1},
            supply_rail_overrides={"ws2812-chain": "V3_3"},
        )
        self.assertEqual(plan.regulator["max_continuous_output_ma"], 150.0)
        self.assertEqual(plan.regulator["verdict"], "over-current")
        self.assertTrue(plan.overheats)
        self.assertFalse(plan.buildable)

    def test_the_v5_ahct_eight_pixel_plan_requires_an_operational_cap(self):
        uncapped = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
        )
        self.assertFalse(uncapped.buildable)
        self.assertIsNotNone(uncapped.source_budget)
        self.assertEqual(uncapped.source_budget["severity"], "error")
        self.assertGreater(
            uncapped.source_budget["physical_peak_ma"],
            uncapped.source_budget["operational_limit_ma"],
        )

        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
            firmware_load_caps_ma={"ws2812-chain": 280},
            exposed_nets=["SWCLK", "SWD"],
        )
        self.assertIn("ws2812-level-shifter", plan.block_ids)
        self.assertIn("ws2812-chain", plan.block_ids)
        self.assertEqual(plan.unmet, ())
        self.assertIsNotNone(plan.regulator)
        self.assertEqual(plan.regulator["output_rail"], "V3_3")
        self.assertLess(float(plan.regulator["load_ma"]), 150.0)
        self.assertFalse(plan.overheats)
        self.assertTrue(plan.buildable)
        self.assertEqual(plan.source_budget["physical_peak_ma"], 582.1)
        self.assertEqual(plan.source_budget["operational_load_ma"], 382.1)
        self.assertEqual(plan.source_budget["fixed_operational_load_ma"], 102.1)
        self.assertEqual(
            plan.source_budget["firmware_load_caps_ma"],
            {"ws2812-chain": 280.0},
        )
        self.assertEqual(
            plan.source_budget["firmware_limited_loads"]["ws2812-chain"],
            {
                "count": 8,
                "per_device_physical_peak_ma": 60.0,
                "physical_peak_ma": 480.0,
                "operational_max_ma": 280.0,
            },
        )

    def test_firmware_cap_cannot_exceed_or_hide_the_physical_block_peak(self):
        with self.assertRaisesRegex(ValueError, "physical peak"):
            board_plan(
                capabilities=["mcu", "rgb-pixels"],
                counts={"ws2812-chain": 8},
                firmware_load_caps_ma={"ws2812-chain": 500},
            )

    def test_the_plan_compiles_the_exact_usb_product_contract(self):
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
            firmware_load_caps_ma={"ws2812-chain": 280},
            exposed_nets=["SWCLK", "SWD"],
        )

        self.assertEqual(
            usb_power_budget_for_plan(
                plan,
                firmware_load_matches={"ws2812-chain": "D1[0-7]"},
            ),
            {
                "usb": {
                    "rawVbusNet": "VBUS_RAW",
                    "protectedVbusNet": "V5",
                    "rawAttachCapacitanceMaxUf": 10.0,
                    "sourceCurrentMaxMa": 500.0,
                    "fixedOperationalLoadMa": 102.1,
                    "currentLimiter": {
                        "ref": "U7",
                        "lcsc": "C55266",
                        "inputPin": "IN",
                        "outputPin": "OUT",
                        "settingPin": "ILIM",
                        "settingResistor": {
                            "ref": "R31",
                            "lcsc": "C32297",
                            "resistanceOhms": 59000.0,
                            "returnNet": "GND",
                        },
                        "minTripMa": 400.6,
                        "maxTripMa": 500.0,
                    },
                    "firmwareLimitedLoads": [
                        {
                            "match": ["D1[0-7]"],
                            "perDevicePhysicalPeakMa": 60.0,
                            "aggregateOperationalMaxMa": 280.0,
                        }
                    ],
                },
                "regulators": [
                    {
                        "profile": "ap7361c-33e-c500795-v1",
                        "ref": "U2",
                        "inputNet": "V5",
                        "outputNet": "V3_3",
                        "inputCapRef": "C2",
                        "outputCapRef": "C3",
                        "maxAmbientC": 60.0,
                    }
                ],
            },
        )

    def test_usb_product_contract_refuses_missing_or_extra_load_patterns(self):
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
            firmware_load_caps_ma={"ws2812-chain": 280},
            exposed_nets=["SWCLK", "SWD"],
        )
        with self.assertRaisesRegex(ValueError, "need product refdes"):
            usb_power_budget_for_plan(plan)
        with self.assertRaisesRegex(ValueError, "not capped"):
            usb_power_budget_for_plan(
                plan,
                firmware_load_matches={
                    "ws2812-chain": "D1[0-7]",
                    "sensor-bme280": "U5",
                },
            )

    def test_usb_product_contract_refuses_an_over_limit_plan(self):
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
        )
        with self.assertRaisesRegex(ValueError, "exceeds"):
            usb_power_budget_for_plan(plan)

    def test_usb_product_contract_refuses_any_other_unbuildable_plan(self):
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
            supply_rail_overrides={"ws2812-chain": "V3_3"},
            firmware_load_caps_ma={"ws2812-chain": 280},
        )
        self.assertTrue(plan.overheats)
        with self.assertRaisesRegex(ValueError, "not buildable"):
            usb_power_budget_for_plan(
                plan,
                firmware_load_matches={"ws2812-chain": "D1[0-7]"},
            )

    def test_usb_product_contract_cannot_waive_missing_debug_furniture(self):
        plan = board_plan(
            capabilities=["mcu", "rgb-pixels"],
            counts={"ws2812-chain": 8},
            firmware_load_caps_ma={"ws2812-chain": 280},
        )
        self.assertEqual(plan.must_expose, ("SWCLK", "SWD"))
        self.assertFalse(plan.overheats)
        self.assertEqual(plan.source_budget["severity"], "info")
        with self.assertRaisesRegex(ValueError, "debug/test-point exposure"):
            usb_power_budget_for_plan(
                plan,
                firmware_load_matches={"ws2812-chain": "D1[0-7]"},
            )

    def test_a_light_usb_plan_compiles_without_firmware_loads(self):
        plan = board_plan(capabilities=["power-usb"])
        budget = usb_power_budget_for_plan(plan)

        self.assertEqual(budget["usb"]["firmwareLimitedLoads"], [])
        self.assertLessEqual(
            budget["usb"]["fixedOperationalLoadMa"],
            budget["usb"]["currentLimiter"]["minTripMa"],
        )

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
        self.assertFalse(plan.buildable)

        debugged = board_plan(
            capabilities=["mcu"],
            exposed_nets=["swclk", "swd"],
        )
        self.assertEqual(debugged.must_expose, ())
        self.assertTrue(debugged.buildable)


class NeverRaises(unittest.TestCase):
    def test_a_nonsense_block_id_does_not_break_the_plan(self):
        out = validate_board_law(block_ids=["not-a-block"], counts={"x": 3})
        self.assertIsInstance(out, list)

    def test_a_nonsense_count_does_not_break_the_budget(self):
        self.assertGreaterEqual(total_peak_ma(PUCK, {"ws2812-chain": -5}), 0.0)


if __name__ == "__main__":
    unittest.main()
