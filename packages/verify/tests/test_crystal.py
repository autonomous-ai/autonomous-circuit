"""Crystal net length against the router's hard ceiling.

The numbers here are not invented. They are the three geometries this repo
actually shipped, recorded in ``packages/golden-blocks/blocks/rp2040-core`` and
in each example board's own source comments:

* **11.78mm** — the v1 block, Y1 at ``pcbX={-11}``. Unroutable: the router
  skipped autorouting for the whole board and blamed Y1.
* **9.88mm** — harness-puck's local patch. Routes, with 0.12mm of margin, and
  its own comment admits another 0.5mm re-broke it.
* **8.44mm** — hydrate-coaster's local patch, and the shape the upstream fix
  settled on. Clean.

A check that cannot separate those three is not guarding anything.
"""

from __future__ import annotations

import fixtures

from verifylib import crystal
from verifylib.model import Board
from verifylib.rules import CRYSTAL_LENGTH_MARGIN_MM, CRYSTAL_MAX_TRACE_LENGTH_MM


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        f["kind"]
        for f in result.findings
        if severity is None or f["severity"] == severity
    }


def oscillator(xin_to_crystal_mm: float, xin_to_cap_mm: float) -> list[dict]:
    """An MCU with a crystal and one load cap on the XIN net.

    ``U3`` sits at the origin with XIN as its first pin; the crystal and the
    load capacitor are placed to its left at the requested pad-to-pad spans, so
    a test states the distance it is testing rather than a coordinate.
    """
    elements = [fixtures.board(40, 30)]
    # XIN is pin 1 of U3, a pad at the component centre.
    elements += fixtures.component(
        "U3", index=1, x=0, y=0, width=4, height=4, ftype="simple_chip",
        pads=[(0.0, 0.0, 0.4, 0.4), (1.6, 0.0, 0.4, 0.4)],
    )
    # Y1 pin1 is the pad at its own centre, so its x IS the span.
    elements += fixtures.component(
        "Y1", index=2, x=-xin_to_crystal_mm, y=0, ftype="simple_crystal",
        pads=[(0.0, 0.0, 0.9, 0.7), (1.2, 0.0, 0.9, 0.7)],
    )
    elements += fixtures.component(
        "C15", index=3, x=-xin_to_cap_mm, y=0, ftype="simple_capacitor",
        capacitance=1.5e-11, pads=[(0.0, 0.0, 0.5, 0.5), (1.0, 0.0, 0.5, 0.5)],
    )
    elements.append(fixtures.net(0, "XIN"))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    for name, pin in (("U3", 0), ("Y1", 0), ("C15", 0)):
        fixtures.connect(elements, name, pin, "XIN")
    for name, pin in (("Y1", 1), ("C15", 1)):
        fixtures.connect(elements, name, pin, "GND")
    elements.append(
        fixtures.source_trace("st0", "XIN", ("Y1", 0), ("U3", 0), elements,
                              name="TR_Y1_xin")
    )
    elements.append(
        fixtures.source_trace("st1", "XIN", ("C15", 0), ("U3", 0), elements,
                              name="TR_C15_xin")
    )
    return elements


def run(xin_to_crystal_mm: float, xin_to_cap_mm: float):
    return crystal.check(Board(oscillator(xin_to_crystal_mm, xin_to_cap_mm)))


# --- the three geometries this repo actually shipped -----------------------


def test_the_v1_block_geometry_is_an_error():
    """Y1 at 11.78mm from XIN — the defect that made every board unroutable."""
    result = run(11.78, 4.0)
    assert "crystal_net_too_long" in kinds(result, "error")


def test_the_error_names_the_part_to_move_and_the_overshoot():
    detail = next(
        f["detail"] for f in run(11.78, 4.0).findings
        if f["kind"] == "crystal_net_too_long"
    )
    assert "11.78mm" in detail          # what it measured
    assert "1.78mm over" in detail      # by how much
    assert "Y1" in detail               # which part to move
    assert "WHOLE board" in detail      # what it costs


def test_harness_pucks_patch_routes_but_is_reported_as_tight():
    """9.88mm passes the ceiling with 0.12mm to spare. Not a pass."""
    result = run(8.88, 9.88)
    assert kinds(result, "error") == set()
    assert "crystal_net_tight" in kinds(result, "warning")


def test_the_tight_warning_prints_the_actual_margin():
    detail = next(
        f["detail"] for f in run(8.88, 9.88).findings
        if f["kind"] == "crystal_net_tight"
    )
    assert "9.88mm" in detail
    assert "0.12mm inside" in detail


def test_the_upstream_fix_geometry_is_clean():
    """8.44mm, hydrate-coaster's patch and the shape upstream settled on."""
    assert run(8.44, 6.74).findings == []


# --- the boundaries -------------------------------------------------------


def test_the_ceiling_itself_is_not_over():
    result = run(CRYSTAL_MAX_TRACE_LENGTH_MM, 4.0)
    assert kinds(result, "error") == set()


def test_a_hair_over_the_ceiling_is_over():
    result = run(CRYSTAL_MAX_TRACE_LENGTH_MM + 0.01, 4.0)
    assert "crystal_net_too_long" in kinds(result, "error")


def test_exactly_the_margin_of_slack_is_not_tight():
    span = CRYSTAL_MAX_TRACE_LENGTH_MM - CRYSTAL_LENGTH_MARGIN_MM
    assert run(span, 4.0).findings == []


def test_error_and_tight_are_never_both_reported_for_one_connection():
    for span in (7.0, 9.5, 10.5, 12.0):
        per_connection = [
            f for f in run(span, 4.0).findings
            if f["kind"] in ("crystal_net_too_long", "crystal_net_tight")
        ]
        assert len(per_connection) <= 1


# --- what it does and does not look at ------------------------------------


def test_a_load_cap_can_be_the_binding_constraint_not_the_crystal():
    """The failure that cost three debugging sessions: Y1 is inside the
    ceiling and a capacitor on the same net is not."""
    result = run(8.0, 11.0)
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "crystal_net_too_long"
    )
    assert "C15" in detail


def test_ground_connections_are_not_measured():
    """Every crystal ties two pads to a ground pour that can be anywhere."""
    elements = oscillator(8.0, 4.0)
    elements.append(
        fixtures.source_trace("st_gnd", "GND", ("Y1", 1), ("C15", 1), elements,
                              name="TR_Y1_gnd")
    )
    # Put the far end 30mm away: measured, it would blow the ceiling open.
    for e in elements:
        if e.get("type") in ("pcb_port", "pcb_smtpad") and "3_1" in str(
            e.get("pcb_port_id") or e.get("pcb_smtpad_id")
        ):
            e["x"] = 30.0
    assert crystal.check(Board(elements)).findings == []


def test_a_board_with_no_crystal_passes_and_says_so():
    result = crystal.check(Board(fixtures.clean_board()))
    assert result.findings == []
    assert result.coverage.total == 0
    assert any("no crystal" in n for n in result.notes)


def test_an_unplaced_pin_is_reported_as_coverage_not_silently_skipped():
    elements = oscillator(8.0, 4.0)
    elements = [
        e for e in elements
        if not (e.get("type") == "pcb_smtpad" and e.get("pcb_smtpad_id") == "pcb_smtpad_2_0")
    ]
    result = crystal.check(Board(elements))
    assert any("no placed pad" in b for b in result.coverage.blind)


def test_coverage_admits_the_routed_path_is_longer_than_the_measurement():
    result = run(8.0, 4.0)
    assert any("lower bound" in b for b in result.coverage.blind)


def test_the_check_never_raises_on_junk():
    assert crystal.check(Board([{"type": "pcb_board"}, {"type": "source_trace"}]))
    assert crystal.check(Board([])).findings == []
