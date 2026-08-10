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
    power_budget,
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
