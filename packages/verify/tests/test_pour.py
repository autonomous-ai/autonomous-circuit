"""Is there a ground plane, and does the check say so out loud?

The numbers here are the corpus measured on 2026-08-19, not invented ones:

* **17 of 17** pourable boards pour on ``bottom`` only, at ~98% of the board
  outline, and never on ``top``.
* **weather-badge-5**, **harness-puck** and **terminal-keyboard** carry no
  copper pour at all — and weather-badge-5 carried ``fab.ready = True`` while
  doing it, because nothing in the pipeline asked.
* **hydrate-coaster** has two pours on ``top`` and *neither is on the ground
  net*, which is the case a naive "are there any pours?" test would pass.

A check that cannot separate those four situations is not guarding anything.
"""

from __future__ import annotations

import fixtures

from verifylib import pour
from verifylib.model import Board


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        f["kind"]
        for f in result.findings
        if severity is None or f["severity"] == severity
    }


def copper_pour(
    layer: str,
    width: float,
    height: float,
    *,
    net_index: int = 0,
    index: int = 0,
) -> dict:
    """One rectangular pour of ``width`` x ``height``, centred on the board."""
    half_w, half_h = width / 2, height / 2
    return {
        "type": "pcb_copper_pour",
        "pcb_copper_pour_id": f"pcb_copper_pour_{index}",
        "shape": "brep",
        "layer": layer,
        "source_net_id": f"source_net_{net_index}",
        "brep_shape": {
            "outer_ring": {
                "vertices": [
                    {"x": -half_w, "y": -half_h},
                    {"x": half_w, "y": -half_h},
                    {"x": half_w, "y": half_h},
                    {"x": -half_w, "y": half_h},
                ]
            },
            "inner_rings": [],
        },
    }


def board_with(*pours: dict, width: float = 50.0, height: float = 40.0) -> Board:
    elements = [fixtures.board(width, height)]
    elements.append(fixtures.net(0, "GND", is_ground=True))
    elements.append(fixtures.net(1, "V3_3", is_power=True))
    elements.extend(pours)
    return Board(elements)


# --------------------------------------------------------------------------
# Existence — the hole that let weather-badge-5 ship
# --------------------------------------------------------------------------


def test_a_board_with_no_pour_at_all_is_said_out_loud():
    result = pour.check(board_with())
    assert "ground_pour_missing" in kinds(result, "warning")


def test_pours_that_are_not_on_the_ground_net_do_not_count_as_a_plane():
    """hydrate-coaster: two pours on top, neither on GND. A check that counts
    pours rather than *ground* pours passes this board."""
    result = pour.check(
        board_with(
            copper_pour("top", 20, 10, net_index=1, index=0),
            copper_pour("top", 8, 6, net_index=1, index=1),
        )
    )
    assert "ground_pour_missing" in kinds(result, "warning")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "ground_pour_missing"
    )
    assert "2 copper pour(s) exist" in detail


def test_a_real_ground_plane_produces_no_warning():
    result = pour.check(
        board_with(
            copper_pour("bottom", 49, 39, index=0),
            copper_pour("top", 49, 39, index=1),
        )
    )
    assert kinds(result, "warning") == set()
    assert kinds(result, "error") == set()


# --------------------------------------------------------------------------
# One-sided — the shape every board in the corpus actually has
# --------------------------------------------------------------------------


def test_pouring_only_the_bottom_names_the_side_that_has_none():
    result = pour.check(board_with(copper_pour("bottom", 49, 39)))
    assert "ground_pour_one_sided" in kinds(result, "info")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "ground_pour_one_sided"
    )
    assert "bottom" in detail and "top" in detail


def test_one_sided_never_rises_above_advice():
    """17 of 17 boards are one-sided. If this warned, the whole corpus would
    go amber over a shape nobody has decided is wrong yet."""
    result = pour.check(board_with(copper_pour("bottom", 49, 39)))
    assert kinds(result, "warning") == set()


# --------------------------------------------------------------------------
# Coverage — a patch is not a plane
# --------------------------------------------------------------------------


def test_a_pour_covering_a_tenth_of_the_board_is_not_a_plane():
    result = pour.check(board_with(copper_pour("bottom", 16, 12)))
    assert "ground_pour_partial" in kinds(result, "warning")


def test_the_corpus_coverage_of_98_percent_is_not_reported_as_partial():
    result = pour.check(board_with(copper_pour("bottom", 49.5, 39.5)))
    assert "ground_pour_partial" not in kinds(result)


def test_offcut_slivers_do_not_drag_the_measured_coverage_down():
    """A pour arrives as one real outline plus a scatter of tiny offcuts left
    over from subtracting the traces. Summing them would be wrong; taking the
    largest is what makes 98% read as 98%."""
    result = pour.check(
        board_with(
            copper_pour("bottom", 49.5, 39.5, index=0),
            copper_pour("bottom", 0.2, 0.2, index=1),
            copper_pour("bottom", 0.1, 0.3, index=2),
        )
    )
    assert "ground_pour_partial" not in kinds(result)


# --------------------------------------------------------------------------
# The check has to survive a board it cannot read
# --------------------------------------------------------------------------


def test_a_malformed_pour_outline_is_ignored_rather_than_crashing():
    broken = copper_pour("bottom", 49, 39)
    broken["brep_shape"]["outer_ring"]["vertices"] = [{"x": 0.0}, {"y": 1.0}]
    result = pour.check(board_with(broken))
    assert "ground_pour_missing" in kinds(result, "warning")


def test_coverage_names_what_it_could_not_see():
    result = pour.check(board_with(copper_pour("bottom", 49, 39)))
    blind = " ".join(result.coverage.blind)
    assert "stitched" in blind
