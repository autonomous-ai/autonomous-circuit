"""Regression for placement boxes including assembly courtyards."""

from __future__ import annotations

import pytest

from evals.measure_block_boxes import _element_box


def test_measurement_includes_outline_and_rotated_rect_courtyards() -> None:
    assert _element_box({
        "type": "pcb_courtyard_outline",
        "outline": [
            {"x": -4.25, "y": 3.65},
            {"x": 4.25, "y": 3.65},
            {"x": 4.25, "y": -3.65},
            {"x": -4.25, "y": -3.65},
        ],
    }) == (-4.25, -3.65, 4.25, 3.65)

    assert _element_box({
        "type": "pcb_courtyard_rect",
        "center": {"x": 5.75, "y": 2.3},
        "width": 1.46,
        "height": 2.96,
        "ccw_rotation": 90,
    }) == pytest.approx((4.27, 1.57, 7.23, 3.03))
