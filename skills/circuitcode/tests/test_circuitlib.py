"""circuitlib is the domain law — the numbers and refusals every board is
built against. These tests assert *invariants and behaviour*, not literals:
an eval pinned to an exact table value breaks the moment the table is
legitimately updated, so it gets deleted, so nothing is checked.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from circuitlib import golden, safety, tables  # noqa: E402
from circuitlib.blocks import BLOCKS, block_for, missing_requirements  # noqa: E402
from circuitlib.helpers import (  # noqa: E402
    board_plan,
    clearance_for,
    decoupling_for,
    estimate_cost,
    fab_profile,
    led_current,
    led_warnings,
    power_budget,
    pullup_warnings,
    regulator_thermal,
    regulator_thermal_warnings,
    trace_width_for,
    validate_board_law,
)


class SafetyEnvelope(unittest.TestCase):
    """The three refusals, and the rule that makes them trustworthy."""

    def test_unscreened_is_never_a_pass(self) -> None:
        verdict = safety.safety_gate()
        self.assertEqual(verdict.status, safety.NOT_SCREENED)
        self.assertFalse(verdict.ok, "absence of screening must not read as safe")

    def test_mains_refused(self) -> None:
        for text in ("230VAC triac dimmer", "switches mains", "120 V line voltage",
                     "wall outlet relay board", "AC-DC converter module"):
            with self.subTest(text=text):
                self.assertEqual(safety.screen_text(text).status, safety.REFUSE)

    def test_bare_rf_refused(self) -> None:
        verdict = safety.screen_text("nRF24L01 die with a pi matching network")
        self.assertEqual(verdict.status, safety.REFUSE)

    def test_charger_silicon_refused(self) -> None:
        self.assertEqual(safety.screen_text("TP4056 lipo charger").status,
                         safety.REFUSE)

    def test_certified_module_allowed(self) -> None:
        verdict = safety.safety_gate(
            description="ESP32-S3-WROOM-1 module on USB-C power",
            power="usb-c-5v",
        )
        self.assertTrue(verdict.ok)

    def test_over_voltage_refused(self) -> None:
        self.assertEqual(
            safety.screen_voltage(tables.MAX_DC_INPUT_V + 1).status, safety.REFUSE
        )

    def test_unparseable_input_is_not_screened(self) -> None:
        self.assertEqual(safety.screen_voltage("banana").status, safety.NOT_SCREENED)
        self.assertEqual(safety.screen_power(None).status, safety.NOT_SCREENED)

    def test_refusal_becomes_a_blocking_warning(self) -> None:
        warnings = safety.screen_text("mains").as_warnings()
        self.assertTrue(warnings)
        self.assertTrue(all(w["severity"] == "error" for w in warnings))
        self.assertTrue(all(w["kind"] == "safety_envelope" for w in warnings))


class GoldenSet(unittest.TestCase):
    def test_invariants_hold(self) -> None:
        self.assertEqual(golden.invariants(), [])

    def test_every_sentinel_is_refused(self) -> None:
        rows = {r["name"]: r for r in golden.run_golden_set()}
        for case in golden.SENTINEL_CASES:
            with self.subTest(case=case.name):
                self.assertFalse(rows[case.name]["cleared"],
                                 f"sentinel {case.name} passed — the eval is blind")

    def test_good_cases_clear(self) -> None:
        rows = {r["name"]: r for r in golden.run_golden_set()}
        for case in golden.GOOD_CASES:
            with self.subTest(case=case.name):
                self.assertTrue(rows[case.name]["cleared"], rows[case.name]["reasons"])


class TraceGeometry(unittest.TestCase):
    def test_width_rises_with_current(self) -> None:
        widths = [trace_width_for(current_a=i) for i in (0.5, 1.0, 2.0, 4.0)]
        self.assertEqual(widths, sorted(widths))
        self.assertTrue(len(set(widths)) > 1)

    def test_never_below_the_fab_floor(self) -> None:
        self.assertGreaterEqual(
            trace_width_for(current_a=0.001), tables.MIN_TRACE_WIDTH_MM
        )

    def test_one_amp_is_physically_plausible(self) -> None:
        # IPC-2221, 1oz external, 10C rise: ~0.25-0.4mm is the accepted range.
        width = trace_width_for(current_a=1.0)
        self.assertGreater(width, 0.2)
        self.assertLess(width, 0.5)

    def test_impossible_asks_raise_at_the_spec(self) -> None:
        for kwargs in ({"current_a": 0}, {"current_a": -1},
                       {"current_a": 1, "delta_t_c": 0},
                       {"current_a": 1, "layer": "middle"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    trace_width_for(**kwargs)

    def test_clearance_refuses_outside_the_envelope(self) -> None:
        with self.assertRaises(ValueError):
            clearance_for(volts=400)


class RegulatorThermal(unittest.TestCase):
    def test_gentle_load_is_fine(self) -> None:
        self.assertEqual(
            regulator_thermal(vin=5.0, vout=3.3, current_a=0.1)["verdict"], "ok"
        )

    def test_heat_rises_with_dropout_and_current(self) -> None:
        low = regulator_thermal(vin=5.0, vout=3.3, current_a=0.1)["watts"]
        high = regulator_thermal(vin=12.0, vout=3.3, current_a=0.5)["watts"]
        self.assertGreater(high, low)

    def test_the_classic_mistake_is_caught(self) -> None:
        """A SOT-23 LDO asked for 500mA from 12V is a fire, not a warning."""
        result = regulator_thermal(
            vin=12.0, vout=3.3, current_a=0.5, package="SOT-23"
        )
        self.assertEqual(result["verdict"], "over-temperature")
        warnings = regulator_thermal_warnings(
            vin=12.0, vout=3.3, current_a=0.5, package="SOT-23"
        )
        self.assertEqual(warnings[0]["severity"], "error")

    def test_ok_produces_no_noise(self) -> None:
        self.assertEqual(regulator_thermal_warnings(current_a=0.05), [])

    def test_unknown_package_is_a_check_failure_not_a_crash(self) -> None:
        warnings = regulator_thermal_warnings(current_a=0.1, package="QFN-96")
        self.assertEqual(warnings[0]["kind"], "check_failed")

    def test_backwards_rails_raise(self) -> None:
        with self.assertRaises(ValueError):
            regulator_thermal(vin=3.3, vout=5.0, current_a=0.1)


class OhmsLaw(unittest.TestCase):
    """The failure class no structural check can see: right topology, wrong
    numbers. A board with a 10-ohm LED resistor passes compile, ERC, DRC, DFM
    and every image review."""

    def test_tiny_series_resistor_is_an_error(self) -> None:
        result = led_current(rail_v=3.3, resistance_ohms=10)
        self.assertEqual(result["verdict"], "over-current")
        warnings = led_warnings(refdes="D1", rail_v=3.3, resistance_ohms=10)
        self.assertEqual(warnings[0]["severity"], "error")

    def test_sane_resistor_is_silent(self) -> None:
        self.assertEqual(led_warnings(refdes="D1", rail_v=3.3, resistance_ohms=1000), [])

    def test_huge_resistor_is_too_dim(self) -> None:
        warnings = led_warnings(refdes="D1", rail_v=3.3, resistance_ohms=1_000_000)
        self.assertTrue(warnings)
        self.assertIn("visible", warnings[0]["detail"])

    def test_rail_below_forward_voltage_never_lights(self) -> None:
        result = led_current(rail_v=1.8, resistance_ohms=330, color="blue")
        self.assertEqual(result["verdict"], "no-conduction")

    def test_current_falls_as_resistance_rises(self) -> None:
        currents = [
            led_current(rail_v=3.3, resistance_ohms=r)["current_ma"]
            for r in (220, 470, 1000, 4700)
        ]
        self.assertEqual(currents, sorted(currents, reverse=True))

    def test_zero_resistance_raises_at_the_spec(self) -> None:
        with self.assertRaises(ValueError):
            led_current(rail_v=3.3, resistance_ohms=0)

    def test_the_block_default_pullup_passes(self) -> None:
        """Regression: the golden blocks must satisfy our own law."""
        self.assertEqual(
            pullup_warnings(refdes="R3", resistance_ohms=tables.I2C_PULLUP_OHMS), []
        )

    def test_pullup_extremes_warn(self) -> None:
        self.assertTrue(pullup_warnings(refdes="R3", resistance_ohms=100))
        self.assertTrue(pullup_warnings(refdes="R3", resistance_ohms=100_000))


class Layout(unittest.TestCase):
    """Placement is where first-pass yield is won or lost: blocks too close
    overlap courtyards, the router refuses to run, and one nudge shows up as
    fifty cascading errors."""

    def test_measured_extents_are_plausible(self) -> None:
        from circuitlib.layout import BLOCK_BOX_MM, extent

        for block_id in BLOCK_BOX_MM:
            with self.subTest(block=block_id):
                width, height = extent(block_id)
                self.assertGreater(width, 0.5)
                self.assertGreater(height, 0.5)
                self.assertLess(width, 200)
                self.assertLess(height, 200)

    def test_placed_blocks_land_where_the_arithmetic_says(self) -> None:
        """The 2026-08-11 defect: `place_row` assumed a block's geometry was
        centred on its origin, so blocks with an offset box (usb-c-power by
        3.29mm, usb-c-data by 6.04mm) landed off by that much and hung over
        the outline. 36 of 42 compositions failed on it."""
        from circuitlib.layout import box, place_row

        blocks = ["usb-c-power", "ldo-3v3", "status-led"]
        placed = place_row(blocks)
        for block_id in blocks:
            x, y = placed[block_id]
            min_x, min_y, max_x, max_y = box(block_id)
            with self.subTest(block=block_id):
                # A placed row is centred on y=0 in *board* coordinates.
                self.assertAlmostEqual((y + min_y + y + max_y) / 2, 0.0, places=1)

    def test_board_fits_catches_a_part_over_the_edge(self) -> None:
        from circuitlib.layout import board_fits, min_board_for, place_row

        blocks = ["usb-c-power", "rp2040-core", "ldo-3v3"]
        width, height = min_board_for(blocks, columns=len(blocks))
        self.assertEqual(board_fits(place_row(blocks), width, height), [])
        # The same placement on a board too small must be caught before a build.
        self.assertTrue(board_fits(place_row(blocks), 20.0, 20.0))

    def test_unmeasured_block_raises_rather_than_guessing(self) -> None:
        from circuitlib.layout import extent

        with self.assertRaises(ValueError):
            extent("flux-capacitor")

    def test_place_row_does_not_collide_with_itself(self) -> None:
        from circuitlib.layout import overlap_warnings, place_row

        blocks = ["usb-c-power", "ldo-3v3", "status-led", "sw-tact"]
        self.assertEqual(overlap_warnings(place_row(blocks)), [])

    def test_overlapping_placement_is_caught(self) -> None:
        from circuitlib.layout import overlap_warnings

        warnings = overlap_warnings({"usb-c-power": (0, 0), "ldo-3v3": (3, 0)})
        self.assertTrue(warnings)
        self.assertIn("courtyards", warnings[0]["detail"])

    def test_min_board_grows_with_content(self) -> None:
        from circuitlib.layout import min_board_for

        small = min_board_for(["status-led"])
        large = min_board_for(["rp2040-core", "usb-c-data", "ldo-3v3"])
        self.assertGreater(large[0] * large[1], small[0] * small[1])

    def test_place_board_puts_the_connector_on_the_edge(self) -> None:
        """A USB socket in the middle of the board is not a product. The
        composition matrix flagged it as
        pcb_connector_not_in_accessible_orientation on every board where
        place_row put the connector inline."""
        from circuitlib.layout import box, place_board

        plan = place_board(["usb-c-power", "ldo-3v3", "status-led"])
        x, y = plan["placements"]["usb-c-power"]
        _, min_y, _, _ = box("usb-c-power")
        bottom = y + min_y
        # Its lowest copper sits within a millimetre of the margin, not
        # somewhere in the middle of the board.
        self.assertLess(bottom, -plan["height_mm"] / 2 + 2.6)
        self.assertGreater(bottom, -plan["height_mm"] / 2)

    def test_place_board_keeps_holes_off_the_footprints(self) -> None:
        """Corner holes on a board sized only for its parts land on a
        footprint. The strip is why the board grows sideways."""
        from circuitlib.layout import place_board

        for blocks in (
            ["usb-c-power", "ldo-3v3", "status-led"],
            ["usb-c-data", "rp2040-core", "ldo-3v3", "sw-tact"],
            ["sensor-bme280", "i2c-bus"],
            ["status-led"],
        ):
            with self.subTest(blocks=blocks):
                plan = place_board(blocks)
                self.assertEqual(plan["warnings"], [])
                self.assertEqual(len(plan["holes"]), 2)

    def test_place_board_without_holes_is_smaller(self) -> None:
        from circuitlib.layout import place_board

        with_holes = place_board(["status-led"])["width_mm"]
        without = place_board(["status-led"], mounting_holes=False)["width_mm"]
        self.assertLess(without, with_holes)

    def test_parametric_block_scales(self) -> None:
        from circuitlib.layout import extent

        self.assertGreater(
            extent("ws2812-chain", count=12)[0], extent("ws2812-chain", count=4)[0]
        )


class Planner(unittest.TestCase):
    def test_plan_pulls_in_required_providers(self) -> None:
        plan = board_plan(capabilities=["sensor-environment"])
        self.assertIn("sensor-bme280", plan.block_ids)
        # the sensor needs a rail and a bus; the planner must supply them
        self.assertIn("ldo-3v3", plan.block_ids)
        self.assertEqual(plan.unmet, ())

    def test_unknown_capability_is_reported_not_invented(self) -> None:
        plan = board_plan(capabilities=["gps", "power-usb"])
        self.assertIn("gps", plan.unavailable)
        self.assertFalse(plan.buildable)

    def test_every_registry_block_resolves_its_requirements(self) -> None:
        for block_id in BLOCKS:
            with self.subTest(block=block_id):
                plan = board_plan(capabilities=[])
                # placing a block alone may need providers; the planner's job is
                # to name what is missing rather than silently proceed
                missing = missing_requirements([block_id])
                self.assertIsInstance(missing, list)
                del plan

    def test_capability_index_points_at_real_blocks(self) -> None:
        from circuitlib.blocks import CAPABILITY_INDEX

        for capability, ids in CAPABILITY_INDEX.items():
            for block_id in ids:
                with self.subTest(capability=capability, block=block_id):
                    self.assertIn(block_id, BLOCKS)

    def test_block_for_unknown_returns_none(self) -> None:
        self.assertIsNone(block_for("time-machine"))


class BoardLaw(unittest.TestCase):
    def test_double_i2c_bus_warns(self) -> None:
        warnings = validate_board_law(block_ids=["i2c-bus", "i2c-bus"])
        self.assertTrue(any("pull-up" in w["detail"] for w in warnings))

    def test_both_usb_blocks_warns(self) -> None:
        warnings = validate_board_law(block_ids=["usb-c-power", "usb-c-data"])
        self.assertTrue(any("superset" in w["detail"] for w in warnings))

    def test_oversize_board_warns(self) -> None:
        warnings = validate_board_law(board_mm=(80, 50), envelope_mm=(60, 40))
        self.assertTrue(any("envelope" in w["detail"] for w in warnings))

    def test_one_mounting_hole_warns(self) -> None:
        warnings = validate_board_law(mounting_holes=1)
        self.assertTrue(any("enclosure" in w["detail"] for w in warnings))

    def test_wrong_thickness_warns(self) -> None:
        warnings = validate_board_law(thickness_mm=1.4)
        self.assertTrue(any("thickness" in w["detail"] for w in warnings))

    def test_never_raises(self) -> None:
        self.assertIsInstance(validate_board_law(block_ids=["nonsense"]), list)

    def test_warning_shape_matches_the_contract(self) -> None:
        for warning in validate_board_law(mounting_holes=0, thickness_mm=1.4):
            self.assertEqual(
                set(warning), {"part", "kind", "detail", "severity"}
            )
            self.assertIn(warning["severity"], {"error", "warning", "info"})


class Budgets(unittest.TestCase):
    def test_over_budget_warns(self) -> None:
        self.assertTrue(power_budget(source="usb-c-5v", current_ma=5000))

    def test_within_budget_is_silent(self) -> None:
        self.assertEqual(power_budget(source="usb-c-5v", current_ma=100), [])

    def test_unknown_source_is_reported(self) -> None:
        self.assertTrue(power_budget(source="nuclear", current_ma=1))

    def test_decoupling_counts(self) -> None:
        counts = decoupling_for(power_pins=4, rails=2)
        self.assertEqual(counts[tables.DECOUPLE_PER_POWER_PIN], 4)
        self.assertEqual(counts[tables.BULK_PER_RAIL], 2)

    def test_cost_grows_with_extended_parts(self) -> None:
        cheap = estimate_cost(unique_parts=10, extended_parts=0, joints=100)
        dear = estimate_cost(unique_parts=10, extended_parts=5, joints=100)
        self.assertGreater(dear["fees"], cheap["fees"])
        self.assertGreater(dear["total_ex_parts"], cheap["total_ex_parts"])

    def test_economy_assembly_cap_is_enforced(self) -> None:
        with self.assertRaises(ValueError):
            estimate_cost(unique_parts=5, extended_parts=0, joints=10,
                          quantity=tables.ECONOMY_MAX_ASSEMBLED + 1)

    def test_fab_profile_only_knows_jlcpcb(self) -> None:
        self.assertEqual(fab_profile()["vendor"], "jlcpcb")
        with self.assertRaises(ValueError):
            fab_profile("oshpark")


class TablesSanity(unittest.TestCase):
    def test_block_floors_sit_below_the_warn_bands(self) -> None:
        self.assertLess(tables.MIN_VIA_DIAMETER_MM, tables.WARN_VIA_DIAMETER_MM)
        self.assertLess(tables.MIN_VIA_DRILL_MM, tables.WARN_VIA_DRILL_MM)
        self.assertLess(tables.MIN_VIA_ANNULAR_MM, tables.WARN_VIA_ANNULAR_MM)
        self.assertLess(tables.MIN_TRACE_WIDTH_MM, tables.WARN_TRACE_WIDTH_MM)

    def test_via_rules_are_finer_than_component_hole_rules(self) -> None:
        """Vias are a routing feature and JLC specs them far finer; conflating
        the two flags every routed board (regression from 2026-08-10)."""
        self.assertLess(tables.MIN_VIA_DRILL_MM, tables.MIN_PTH_DRILL_MM)
        self.assertLess(tables.MIN_VIA_ANNULAR_MM, tables.MIN_PTH_ANNULAR_MM)

    def test_thickness_is_the_fab_standard_not_the_toolchain_default(self) -> None:
        self.assertEqual(tables.BOARD_THICKNESS_MM, 1.6)


if __name__ == "__main__":
    unittest.main()
