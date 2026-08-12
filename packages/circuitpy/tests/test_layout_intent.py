from __future__ import annotations

from copy import deepcopy
import unittest

from circuitpy.errors import ProjectShapeError
from circuitpy.layout_intent import validate_layout


VALID = {
    "boardSizeMm": [105, 55],
    "boardSizeToleranceMm": 0.1,
    "minCopperClearanceMm": 0.15,
    "decoupling": {
        "maxDistanceMm": 2,
        "exclude": ["U1", "U_ESD*"],
        "overrides": [
            {
                "match": "U3",
                "maxDistanceMm": 5,
                "source": "https://example.test/vendor-reference.zip",
            }
        ],
    },
    "componentSides": [
        {"match": ["SW[1-5][0-9]", "D*"], "side": "top"},
        {"match": "*", "side": "bottom"},
    ],
    "componentZones": [
        {
            "match": ["D1[0-7]", "C4[0-7]"],
            "containment": "courtyard",
            "shape": {
                "kind": "annulus",
                "center": [0, 0],
                "innerRadiusMm": 23.5,
                "outerRadiusMm": 32.5,
            },
        },
        {
            "match": "U*",
            "containment": "center",
            "shape": {
                "kind": "rect",
                "center": [0, 0],
                "widthMm": 40,
                "heightMm": 30,
            },
        },
    ],
    "edgeConnectors": [
        {
            "ref": "J1",
            "edge": "bottom",
            "alignment": "center",
            "edgeToleranceMm": 1,
            "centerToleranceMm": 0.5,
        }
    ],
    "groundPlanes": {
        "layers": ["top", "bottom"],
        "maxRoutedLengthMm": 20,
        "maxFanoutLengthMm": 2,
        "stitchingPitchMm": 10,
    },
    "netClasses": [
        {
            "name": "POWER",
            "nets": ["V5", "V3_3"],
            "minTrunkWidthMm": 0.6,
            "minNeckdownWidthMm": 0.2,
            "maxNeckdownLengthMm": 2,
            "minViaOuterDiameterMm": 0.8,
            "minViaHoleDiameterMm": 0.5,
        }
    ],
}


class LayoutIntentValidation(unittest.TestCase):
    def test_none_is_an_empty_backward_compatible_contract(self) -> None:
        self.assertEqual(validate_layout(None), {})

    def test_complete_contract_is_accepted_and_copied(self) -> None:
        resolved = validate_layout(VALID)
        self.assertEqual(resolved, VALID)
        self.assertIsNot(resolved, VALID)

    def test_unknown_key_fails_closed(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "unknown member"):
            validate_layout({"groundPlane": ["bottom"]})

    def test_bad_component_side_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "top or bottom"):
            validate_layout({"componentSides": [{"match": "*", "side": "back"}]})

    def test_component_zone_shape_dimensions_are_finite_and_ordered(self) -> None:
        base = {
            "match": "D*",
            "containment": "courtyard",
            "shape": {
                "kind": "annulus",
                "center": [0, 0],
                "innerRadiusMm": 10,
                "outerRadiusMm": 9,
            },
        }
        with self.assertRaisesRegex(ProjectShapeError, "less than outerRadiusMm"):
            validate_layout({"componentZones": [base]})
        bad = deepcopy(base)
        bad["shape"]["innerRadiusMm"] = 1
        bad["shape"]["outerRadiusMm"] = float("inf")
        with self.assertRaisesRegex(ProjectShapeError, "positive"):
            validate_layout({"componentZones": [bad]})

    def test_component_zone_rejects_bad_coordinates_containment_and_members(self) -> None:
        circle = {
            "match": "U*",
            "containment": "center",
            "shape": {"kind": "circle", "center": [0, 0], "radiusMm": 10},
        }
        bad_point = deepcopy(circle)
        bad_point["shape"]["center"] = [0, float("nan")]
        with self.assertRaisesRegex(ProjectShapeError, "finite"):
            validate_layout({"componentZones": [bad_point]})
        bad_containment = deepcopy(circle)
        bad_containment["containment"] = "body"
        with self.assertRaisesRegex(ProjectShapeError, "center or courtyard"):
            validate_layout({"componentZones": [bad_containment]})
        unhashable_containment = deepcopy(circle)
        unhashable_containment["containment"] = ["center"]
        with self.assertRaisesRegex(ProjectShapeError, "center or courtyard"):
            validate_layout({"componentZones": [unhashable_containment]})
        unhashable_kind = deepcopy(circle)
        unhashable_kind["shape"]["kind"] = ["circle"]
        with self.assertRaisesRegex(ProjectShapeError, "must be one of"):
            validate_layout({"componentZones": [unhashable_kind]})
        unknown = deepcopy(circle)
        unknown["shape"]["angle"] = 45
        with self.assertRaisesRegex(ProjectShapeError, "unknown member"):
            validate_layout({"componentZones": [unknown]})

    def test_clearance_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "positive"):
            validate_layout({"minCopperClearanceMm": 0})

    def test_decoupling_distance_is_required_and_positive(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "maxDistanceMm"):
            validate_layout({"decoupling": {}})
        with self.assertRaisesRegex(ProjectShapeError, "positive"):
            validate_layout({"decoupling": {"maxDistanceMm": 0}})

    def test_decoupling_exclusions_are_explicit_non_empty_patterns(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "non-empty"):
            validate_layout(
                {"decoupling": {"maxDistanceMm": 2, "exclude": []}}
            )

    def test_decoupling_overrides_are_explicit_typed_rules(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "non-empty list"):
            validate_layout(
                {"decoupling": {"maxDistanceMm": 2, "overrides": []}}
            )
        with self.assertRaisesRegex(ProjectShapeError, "non-empty string"):
            validate_layout(
                {
                    "decoupling": {
                        "maxDistanceMm": 2,
                        "overrides": [
                            {
                                "match": [],
                                "maxDistanceMm": 5,
                                "source": "vendor-reference",
                            }
                        ],
                    }
                }
            )
        with self.assertRaisesRegex(ProjectShapeError, "positive"):
            validate_layout(
                {
                    "decoupling": {
                        "maxDistanceMm": 2,
                        "overrides": [
                            {
                                "match": "U3",
                                "maxDistanceMm": 0,
                                "source": "vendor-reference",
                            }
                        ],
                    }
                }
            )
        with self.assertRaisesRegex(ProjectShapeError, "unknown member"):
            validate_layout(
                {
                    "decoupling": {
                        "maxDistanceMm": 2,
                        "overrides": [
                            {
                                "match": "U3",
                                "maxDistanceMm": 5,
                                "source": "vendor-reference",
                                "reason": "vendor",
                            }
                        ],
                    }
                }
            )

        with self.assertRaisesRegex(ProjectShapeError, "manufacturer reference"):
            validate_layout(
                {
                    "decoupling": {
                        "maxDistanceMm": 2,
                        "overrides": [{"match": "U3", "maxDistanceMm": 5}],
                    }
                }
            )

    def test_decoupling_unknown_member_fails_closed(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "unknown member"):
            validate_layout(
                {"decoupling": {"maxDistanceMm": 2, "radius": 3}}
            )

    def test_neckdown_cannot_be_wider_than_the_trunk(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "must not exceed"):
            validate_layout(
                {
                    "netClasses": [
                        {
                            "nets": ["V5"],
                            "minTrunkWidthMm": 0.6,
                            "minNeckdownWidthMm": 0.8,
                        }
                    ]
                }
            )

    def test_netclass_via_outer_must_exceed_its_hole(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "minViaOuterDiameterMm"):
            validate_layout(
                {
                    "netClasses": [
                        {
                            "nets": ["V5"],
                            "minTrunkWidthMm": 0.8,
                            "minViaOuterDiameterMm": 0.5,
                            "minViaHoleDiameterMm": 0.5,
                        }
                    ]
                }
            )

    def test_connector_edge_is_closed(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "must be one of"):
            validate_layout(
                {"edgeConnectors": [{"ref": "J1", "edge": "middle"}]}
            )

    def test_ground_fanout_length_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ProjectShapeError, "positive"):
            validate_layout(
                {
                    "groundPlanes": {
                        "layers": ["bottom"],
                        "maxFanoutLengthMm": 0,
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
