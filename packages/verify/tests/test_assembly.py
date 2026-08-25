"""Assembly (DFA) checks — clean boards stay clean, seeded defects must trip.

The sentinel discipline from ``circuitlib.golden``: a check that only ever
passes has gone blind, and a blind check is worse than none because it implies
coverage. Every rule here has both a negative and a positive case.
"""

from __future__ import annotations

import math

import fixtures
import pytest

from verifylib import assembly
from verifylib.model import Board, Poly


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        f["kind"]
        for f in result.findings
        if severity is None or f["severity"] == severity
    }


def test_clean_board_has_no_blocking_findings():
    result = assembly.check(Board(fixtures.clean_board()))
    assert result.blocking == 0, [f for f in result.findings if f["severity"] == "error"]


def test_coverage_names_what_it_cannot_see():
    result = assembly.check(Board(fixtures.clean_board()))
    assert result.coverage is not None
    assert result.coverage.examined == 4
    blind = " ".join(result.coverage.blind)
    assert "height" in blind, "a check must say it cannot see component height"


# --- edge clearance -------------------------------------------------------


def test_part_inside_the_conveyor_rail_is_an_error():
    elements = [fixtures.board(40, 30)]
    # keep-out 2.3 x 1.4 centred 0.5mm short of the right edge
    elements += fixtures.component("R1", index=1, x=19.0, y=0, courtyard=(2.0, 1.4))
    result = assembly.check(Board(elements))
    assert "dfa_edge_clearance" in kinds(result, "error")
    detail = next(f["detail"] for f in result.findings if f["kind"] == "dfa_edge_clearance")
    assert "0.000mm" in detail or "mm from the board edge" in detail


def test_part_between_the_two_bands_warns_and_carries_the_measurement():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component("R1", index=1, x=17.5, y=0, courtyard=(2.0, 1.4))
    result = assembly.check(Board(elements))
    assert "dfa_edge_clearance" in kinds(result, "warning")
    detail = next(f["detail"] for f in result.findings if f["kind"] == "dfa_edge_clearance")
    assert "1.500mm" in detail and "2.5mm" in detail


def test_a_connector_at_the_edge_is_reported_but_not_blocked():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component(
        "J1", index=1, x=19.0, y=0, ftype="simple_connector", courtyard=(2.0, 1.4)
    )
    result = assembly.check(Board(elements))
    assert result.blocking == 0
    assert "dfa_edge_clearance" in kinds(result, "info")


def test_a_part_off_the_board_blocks():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component("R1", index=1, x=20.5, y=0, courtyard=(2.0, 1.4))
    result = assembly.check(Board(elements))
    assert "dfa_off_board" in kinds(result, "error")


# --- spacing --------------------------------------------------------------


def test_overlapping_courtyards_block():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component("R1", index=1, x=0, y=0, courtyard=(3.0, 2.0))
    elements += fixtures.component("R2", index=2, x=2.0, y=0, courtyard=(3.0, 2.0))
    result = assembly.check(Board(elements))
    assert "dfa_courtyard_overlap" in kinds(result, "error")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "dfa_courtyard_overlap"
    )
    assert "1.000mm" in detail


def test_rotated_courtyards_that_only_overlap_as_bounding_boxes_do_not_trip():
    """The measured regression: on ``harness-puck`` nine WS2812/decoupling
    pairs read as overlapping when compared box-to-box. Their courtyards are
    rotated 22.5 degrees and do not touch. A check that reports those is noise,
    and noise is how a gate gets ignored."""
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component(
        "D1", index=1, x=0, y=0, courtyard=(4.0, 4.0), courtyard_rotation_deg=45
    )
    elements += fixtures.component(
        "C1", index=2, x=2.5, y=2.5, courtyard=(1.4, 1.0), courtyard_rotation_deg=45
    )
    board_model = Board(elements)
    d1, c1 = board_model.by_name["D1"], board_model.by_name["C1"]
    assert d1.keepout.gap_to(c1.keepout) < 0, "the bounding boxes must overlap"
    assert d1.keepout_gap_to(c1) > 0, "the real polygons must not"
    result = assembly.check(board_model)
    assert "dfa_courtyard_overlap" not in kinds(result)


def test_pads_closer_than_the_line_can_place_warn():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component(
        "R1", index=1, x=0, y=0, pads=[(-0.5, 0, 0.6, 0.6), (0.5, 0, 0.6, 0.6)],
        courtyard=(1.8, 1.0),
    )
    elements += fixtures.component(
        "R2", index=2, x=1.9, y=0, pads=[(-0.5, 0, 0.6, 0.6), (0.5, 0, 0.6, 0.6)],
        courtyard=(1.8, 1.0),
    )
    result = assembly.check(Board(elements))
    assert "dfa_part_spacing" in kinds(result, "warning")


# --- line limits ----------------------------------------------------------


def test_a_bottom_side_part_blocks_on_a_single_sided_tier():
    elements = fixtures.clean_board()
    elements += fixtures.component("R9", index=9, x=0, y=-8, layer="bottom",
                                   courtyard=(2.4, 1.4))
    result = assembly.check(Board(elements))
    assert "dfa_bottom_side" in kinds(result, "error")


def test_the_same_board_passes_on_the_two_sided_tier():
    elements = fixtures.clean_board()
    elements += fixtures.component("R9", index=9, x=0, y=-8, layer="bottom",
                                   courtyard=(2.4, 1.4))
    result = assembly.check(Board(elements), tier="standard")
    assert "dfa_bottom_side" not in kinds(result)


def test_pitch_below_the_line_floor_blocks():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component(
        "U1", index=1, x=0, y=0, width=3, height=3, ftype="simple_chip",
        courtyard=(3.4, 3.4),
        pads=[(-1.2, dy, 0.2, 0.2) for dy in (-0.3, 0.0, 0.3)],
    )
    result = assembly.check(Board(elements))
    assert "dfa_pin_pitch" in kinds(result, "error")


def test_pitch_exactly_on_the_floor_is_legal_and_said_so():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component(
        "U1", index=1, x=0, y=0, width=3, height=3, ftype="simple_chip",
        courtyard=(3.4, 3.4),
        pads=[(-1.2, dy, 0.2, 0.2) for dy in (-0.4, 0.0, 0.4)],
    )
    result = assembly.check(Board(elements))
    assert "dfa_pin_pitch" in kinds(result, "info")
    assert "dfa_pin_pitch" not in kinds(result, "error")


def test_board_too_small_for_the_line_blocks_even_though_the_fab_accepts_it():
    elements = [fixtures.board(8, 8)]
    elements += fixtures.component("R1", index=1, x=0, y=0, courtyard=(2.4, 1.4))
    result = assembly.check(Board(elements))
    assert "dfa_board_size" in kinds(result, "error")
    # the fab floor is 3mm; this is an assembly-only limit and must say so
    assert not assembly.check(Board(elements), assembly=False).blocking


def test_bare_pcb_orders_skip_the_line_rules():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component("R1", index=1, x=19.0, y=0, courtyard=(2.0, 1.4))
    result = assembly.check(Board(elements), assembly=False)
    assert "dfa_edge_clearance" not in kinds(result)


def test_rotation_watchlist_names_the_parts_to_eyeball():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component(
        "J1", index=1, x=0, y=0, ftype="simple_connector", courtyard=(3.0, 2.0)
    )
    result = assembly.check(Board(elements))
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "dfa_rotation_watchlist"
    )
    assert "J1" in detail


def test_a_mounting_hole_inside_a_part_is_caught():
    elements = [fixtures.board(40, 30)]
    elements += fixtures.component("U1", index=1, x=0, y=0, width=4, height=4,
                                   ftype="simple_chip", courtyard=(4.4, 4.4))
    elements.append(
        {
            "type": "pcb_hole",
            "pcb_hole_id": "pcb_hole_0",
            "hole_shape": "circle",
            "hole_diameter": 2.2,
            "x": 1.0,
            "y": 1.0,
        }
    )
    result = assembly.check(Board(elements))
    assert "dfa_hole_in_keepout" in kinds(result, "warning")


# --- geometry primitives --------------------------------------------------


def test_polygon_gap_matches_hand_arithmetic():
    a = Poly([(0, 0), (2, 0), (2, 1), (0, 1)])
    b = Poly([(3, 0), (5, 0), (5, 1), (3, 1)])
    assert a.gap_to(b) == pytest.approx(1.0)
    assert b.gap_to(a) == pytest.approx(1.0)


def test_polygon_overlap_is_reported_negative():
    a = Poly([(0, 0), (2, 0), (2, 1), (0, 1)])
    b = Poly([(1.5, 0), (3, 0), (3, 1), (1.5, 1)])
    assert a.gap_to(b) == pytest.approx(-0.5)


def test_rotated_square_gap_is_not_its_bounding_box_gap():
    diamond = Poly(
        [
            (math.cos(math.radians(a)) * 2, math.sin(math.radians(a)) * 2)
            for a in (0, 90, 180, 270)
        ]
    )
    corner = Poly([(1.8, 1.8), (2.4, 1.8), (2.4, 2.4), (1.8, 2.4)])
    assert diamond.bounds.gap_to(corner.bounds) < 0
    assert diamond.gap_to(corner) > 0


# --- mounting support -----------------------------------------------------


def _mount(x: float, y: float, diameter: float = 2.7) -> dict:
    return {
        "type": "pcb_hole",
        "pcb_hole_id": f"pcb_hole_{x}_{y}",
        "x": x,
        "y": y,
        "hole_diameter": diameter,
    }


def _corners(w: float, h: float) -> list[dict]:
    dx, dy = w / 2 - 4, h / 2 - 4
    return [_mount(x, y) for x in (-dx, dx) for y in (-dy, dy)]


def test_four_corner_holes_are_what_27_of_32_boards_do():
    elements = [fixtures.board(75, 50), *_corners(75, 50)]
    assert not assembly._mounting_support(Board(elements))


def test_two_holes_on_a_board_over_50mm_is_reported():
    """wb-28's shape: 60x45 with a diagonal pair. A warning, never an error —
    the agent's only moves would be to grow the board or drop parts, and that
    is a product decision."""
    elements = [fixtures.board(60, 45), _mount(-26.5, -19), _mount(26.5, 10)]
    found = assembly._mounting_support(Board(elements))
    assert {f["kind"] for f in found} == {"dfa_mounting_points"}
    assert {f["severity"] for f in found} == {"warning"}


def test_both_holes_on_one_edge_is_reported_separately():
    """wb-29, and the only board in the corpus that does it: both at x=+26, so
    the rest of the board is cantilevered off that line."""
    elements = [fixtures.board(60, 40), _mount(26, -16), _mount(26, 16)]
    kinds_found = {f["kind"] for f in assembly._mounting_support(Board(elements))}
    assert kinds_found == {"dfa_mounting_points", "dfa_mounting_collinear"}


def test_a_diagonal_pair_is_not_collinear():
    """The distinction the second rule exists to draw: four boards before wb-29
    shipped two holes and every one of them put the pair on a diagonal."""
    elements = [fixtures.board(60, 45), _mount(-26.5, -19), _mount(26.5, 10)]
    kinds_found = {f["kind"] for f in assembly._mounting_support(Board(elements))}
    assert "dfa_mounting_collinear" not in kinds_found


def test_a_small_board_is_not_scored():
    """weather-badge-8 is 50x35 with two holes and is not flagged: below the
    threshold an enclosure's own clips are a normal answer."""
    elements = [fixtures.board(50, 35), _mount(-20, -12), _mount(20, 12)]
    assert not assembly._mounting_support(Board(elements))


def test_a_via_sized_hole_is_not_a_mounting_point():
    """Only holes at or above 1.0mm count; the profile's largest via drill is
    0.3mm, so a densely-vias board must not read as well mounted."""
    elements = [fixtures.board(75, 50)] + [
        _mount(x, y, diameter=0.3) for x in (-30, 30) for y in (-20, 20)
    ]
    kinds_found = {f["kind"] for f in assembly._mounting_support(Board(elements))}
    assert "dfa_mounting_points" in kinds_found


def test_mounting_findings_reach_the_check_result():
    elements = [fixtures.board(60, 40), _mount(26, -16), _mount(26, 16)]
    result = assembly.check(Board(elements))
    assert "dfa_mounting_collinear" in kinds(result, "warning")
