"""Reserving the pair's corridor before the autorouter fills it.

`circuitpy.reserve` writes a keepout corridor into the mirrored board source so
the router never fills the channel D+/D- needs, and takes those exact elements
back out of circuit.json before anything downstream reads them. Two halves,
both testable without a board build: the **geometry** is a function of
placement, and the **release** is a function of the record.

The refusals are tested as hard as the successes. This pass denies space to
every net on the board — the one measured reservation re-solved 88 of 123
traces — so "it refused, and here is which test failed" is the answer far more
often than "it reserved", and a refusal that quietly stops firing is the
failure mode this file exists to prevent.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import diffpair, reserve  # noqa: E402
from circuitpy.fab import PROFILES  # noqa: E402

PROFILE = PROFILES["jlcpcb"]


# ---------------------------------------------------------------------------
# Synthetic placement. No traces, no vias, no pour — a reservation is planned
# from placement and this file proves it needs nothing else.
# ---------------------------------------------------------------------------


def _board(width: float = 24.0, height: float = 20.0, layers: int = 2) -> dict:
    return {
        "type": "pcb_board",
        "pcb_board_id": "pcb_board_0",
        "center": {"x": 0, "y": 0},
        "width": width,
        "height": height,
        "num_layers": layers,
        "thickness": 1.6,
    }


def _net(index: int, name: str) -> dict:
    return {
        "type": "source_net",
        "source_net_id": f"source_net_{index}",
        "name": name,
        "subcircuit_connectivity_map_key": f"net_{name}",
    }


def _pad(index: int, net: str | None, x: float, y: float,
         w: float = 0.4, h: float = 0.4, layer: str = "top") -> list[dict]:
    """One SMD pad, with the source/pcb port chain when it belongs to a net."""
    pad: dict = {
        "type": "pcb_smtpad",
        "pcb_smtpad_id": f"pcb_smtpad_{index}",
        "layer": layer,
        "shape": "rect",
        "width": w,
        "height": h,
        "x": x,
        "y": y,
    }
    if net is None:
        return [pad]
    pad["pcb_port_id"] = f"pcb_port_{index}"
    return [
        {
            "type": "source_port",
            "source_port_id": f"source_port_{index}",
            "name": f"p{index}",
            "source_component_id": "source_component_0",
            "subcircuit_connectivity_map_key": f"net_{net}",
        },
        {
            "type": "pcb_port",
            "pcb_port_id": f"pcb_port_{index}",
            "source_port_id": f"source_port_{index}",
            "layers": [layer],
            "x": x,
            "y": y,
        },
        pad,
    ]


def open_board() -> list[dict]:
    """D+/D- from x=-9 to x=+9, with one obstacle in the middle of the run."""
    elements: list[dict] = [
        _board(),
        {"type": "source_component", "source_component_id": "source_component_0",
         "name": "U1"},
        _net(0, "USB_DP"),
        _net(1, "USB_DM"),
    ]
    elements += _pad(0, "USB_DP", -9.0, 0.3)
    elements += _pad(1, "USB_DP", 9.0, 0.3)
    elements += _pad(2, "USB_DM", -9.0, -0.3)
    elements += _pad(3, "USB_DM", 9.0, -0.3)
    # Something to go round, so the corridor is not a straight line by default.
    elements += _pad(4, None, 0.0, 0.0, w=2.0, h=2.0)
    return elements


def walled_board(gap_half: float, layers: int = 1) -> list[dict]:
    """A wall across the board with one gap, and a net that needs the gap.

    The pair runs top to bottom through the gap. ``victim`` has one pad on each
    side of the wall, so whether it can still reach itself is entirely a
    question about the gap — which is what the strangle test is for.
    """
    elements: list[dict] = [
        _board(width=24.0, height=14.0, layers=layers),
        {"type": "source_component", "source_component_id": "source_component_0",
         "name": "U1"},
        _net(0, "USB_DP"),
        _net(1, "USB_DM"),
        _net(2, "VICTIM"),
    ]
    elements += _pad(0, "USB_DP", -0.3, 5.5)
    elements += _pad(1, "USB_DP", -0.3, -5.5)
    elements += _pad(2, "USB_DM", 0.3, 5.5)
    elements += _pad(3, "USB_DM", 0.3, -5.5)
    elements += _pad(4, "VICTIM", -5.0, 3.5)
    elements += _pad(5, "VICTIM", -5.0, -3.5)
    # The wall: edge of the board to the gap, both sides.
    left = (-11.9, -gap_half)
    right = (gap_half, 11.9)
    for index, (x0, x1) in enumerate((left, right), start=10):
        elements += _pad(index, None, (x0 + x1) / 2, 0.0,
                         w=(x1 - x0), h=1.0)
    return elements


def plan(elements, **kwargs):
    return reserve.plan_reservation(elements, PROFILE, **kwargs)


# ---------------------------------------------------------------------------
# The corridor itself
# ---------------------------------------------------------------------------


class TheCorridor(unittest.TestCase):
    def setUp(self) -> None:
        self.result = plan(open_board())

    def test_a_corridor_is_planned_between_the_two_ends(self) -> None:
        self.assertEqual(self.result.status, "planned", self.result.as_dict())
        booked = self.result.reservation
        assert booked is not None
        self.assertEqual((booked.net_p, booked.net_n), ("USB_DP", "USB_DM"))
        self.assertGreater(len(booked.discs), 10)
        # It reaches from one end to the other, not part of the way. Both ends
        # sit inside the radius `_pair_seed` will search when stage 0c comes
        # looking for a way in, so the corridor spans at least the distance
        # between the anchors less those two fan-outs.
        self.assertLess(booked.end_reach_mm[0], diffpair.SEED_RADIUS_MM)
        self.assertLess(booked.end_reach_mm[1], diffpair.SEED_RADIUS_MM)
        self.assertGreaterEqual(
            booked.path_mm,
            booked.straight_mm - 2 * diffpair.SEED_RADIUS_MM)

    def test_the_corridor_is_exactly_what_stage_0c_will_ask_for(self) -> None:
        """The width comes from the consumer's own ruler plus a measured margin.

        Not "about a millimetre": `diffpair.pair_span` is the single owner of
        the half-span, and a corridor sized at exactly that half-span is one
        stage 0c cannot enter — measured, 0.0093 mm short over its own 0.05 mm
        raster. `RESERVE_MARGIN_MM` is the answer to that and it is part of the
        number, not a garnish.
        """
        booked = self.result.reservation
        assert booked is not None
        span = diffpair.pair_span(PROFILE, booked.width_mm, allow_layers=False)
        self.assertAlmostEqual(booked.half_span_mm, span.half_span_mm, places=12)
        self.assertAlmostEqual(
            booked.reserve_r_mm, span.half_span_mm + reserve.RESERVE_MARGIN_MM,
            places=12)
        self.assertEqual(booked.pitch_mm, span.pitch_mm)
        # Single layer, and it is the layer the pads are on.
        self.assertEqual(booked.layer, "top")
        self.assertEqual({d.layer for d in booked.discs}, {"top"})

    def test_the_discs_overlap_so_the_chain_is_a_corridor(self) -> None:
        """Spaced at the radius, not the diameter: two discs a radius apart
        overlap over the middle of the gap, so the union has no waist."""
        booked = self.result.reservation
        assert booked is not None
        radius = booked.reserve_r_mm
        for a, b in zip(booked.discs, booked.discs[1:]):
            self.assertLessEqual(math.hypot(b.cx - a.cx, b.cy - a.cy),
                                 radius + 1e-9)

    def test_every_disc_is_a_circle_because_a_rect_cannot_turn(self) -> None:
        """`pcb_keepout` rects are axis-aligned with no rotation field, and the
        emitter swaps width/height only at 90/270 degrees — at any other angle
        it emits the wrong shape silently. A corridor that turns is discs."""
        booked = self.result.reservation
        assert booked is not None
        for disc in booked.discs:
            self.assertEqual(disc.as_element()["shape"], "circle")

    def test_nothing_the_corridor_covers_is_copper(self) -> None:
        """The independent check on the raster: exact shapes, zero tolerance."""
        booked = self.result.reservation
        assert booked is not None
        board = diffpair._Board(reserve.placement_elements(open_board()))
        obstacles = [o for o in diffpair.collect_obstacles(board, PROFILE, set())
                     if "top" in o.layers]
        outline = diffpair._board_outline(board)
        self.assertIsNone(reserve._exact_overlap(
            booked.discs, obstacles, outline,
            PROFILE.min_edge_clearance_mm))

    def test_the_corridor_goes_round_the_obstacle_rather_than_through_it(self) -> None:
        booked = self.result.reservation
        assert booked is not None
        # The 2x2mm pad sits on the straight line between the two ends.
        for disc in booked.discs:
            self.assertGreater(max(abs(disc.cx), abs(disc.cy)),
                               1.0 + disc.radius - 1e-9)

    def test_a_measured_radius_is_never_rounded_up(self) -> None:
        """Found on terminal-keyboard, not by reading the code. An end disc
        grows to exactly the largest radius that touches nothing; writing that
        onto the 1e-6 record grid rounded it **up**, and the first disc came
        back overlapping ``pcb_smtpad_346`` by 3.458e-07 mm. Zero tolerance
        means zero, so the whole reservation was refused over 0.35 nanometres.

        The fix direction is the only one available: write down slightly less
        than was measured. Widening the check to swallow it would be changing
        the ruler to fit the board.
        """
        self.assertLess(reserve._round_down(0.7795999996), 0.7795999996)
        self.assertLess(reserve._round_down(1.0), 1.0)
        # Still on the 1e-6 grid, so the record and the TSX stay exact.
        value = reserve._round_down(0.12345678)
        self.assertEqual(float(f"{value:.6f}"), value)
        # And the loss is negligible against any fab rule.
        self.assertLess(1.0 - reserve._round_down(1.0), 1e-4)

    def test_the_area_is_the_swept_capsule_and_is_reported(self) -> None:
        """Pinned against the design's own measured shape: 74 discs of
        r = 0.4957 mm over a 36.35 mm path swept 36.8 mm²."""
        self.assertAlmostEqual(reserve._swept_area(36.35, 0.4957, []), 36.8,
                               places=1)
        booked = self.result.reservation
        assert booked is not None
        reported = self.result.as_dict()["reservation"]
        self.assertEqual(reported["areaMm2"], round(booked.area_mm2, 3))
        self.assertLess(booked.area_mm2, reserve.RESERVE_MAX_AREA_MM2)


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------


class Refusals(unittest.TestCase):
    def test_a_board_with_no_pair_is_skipped_not_refused(self) -> None:
        elements = [_board(), _net(0, "SDA"), _net(1, "SCL")]
        result = plan(elements)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason_code, "no_pair")

    def test_a_multi_drop_pair_is_refused_because_stage_0c_skips_it(self) -> None:
        elements = open_board()
        elements += _pad(20, "USB_DP", 3.0, 6.0)
        result = plan(elements)
        self.assertEqual(result.status, "refused")
        self.assertEqual(result.reason_code, "not_two_terminal")
        self.assertIn("3 pads", result.reason)

    def test_pads_on_two_layers_have_no_single_layer_to_reserve(self) -> None:
        elements = [
            _board(),
            {"type": "source_component",
             "source_component_id": "source_component_0", "name": "U1"},
            _net(0, "USB_DP"), _net(1, "USB_DM"),
        ]
        elements += _pad(0, "USB_DP", -9.0, 0.3)
        elements += _pad(1, "USB_DP", 9.0, 0.3, layer="bottom")
        elements += _pad(2, "USB_DM", -9.0, -0.3)
        elements += _pad(3, "USB_DM", 9.0, -0.3)
        result = plan(elements)
        self.assertEqual(result.reason_code, "pads_on_multiple_layers")

    def test_a_poured_layer_reserves_nothing_so_it_is_refused(self) -> None:
        """Measured: the pour solver has 0 references to `keepout` against 7 to
        `pcb_cutout`, and on the proof rect the bottom pour filled *more* of
        the reserved area than before (13.5025 -> 16.0775 mm²)."""
        elements = open_board()
        elements.append({
            "type": "pcb_copper_pour", "pcb_copper_pour_id": "pour_0",
            "layer": "top", "shape": "rect",
            "center": {"x": 0, "y": 0}, "width": 24, "height": 20,
        })
        result = plan(elements)
        self.assertEqual(result.reason_code, "poured_layer")
        self.assertEqual(result.measurements["pouredLayers"], ["top"])

    def test_never_for_a_power_rail(self) -> None:
        """A keepout is `connectedTo: []` — it denies space to everyone and
        grants it to nobody. Finding 4 is not closed by any reservation."""
        elements = [
            _board(),
            {"type": "source_component",
             "source_component_id": "source_component_0", "name": "U1"},
            _net(0, "3V3_DP"), _net(1, "3V3_DM"),
        ]
        elements += _pad(0, "3V3_DP", -9.0, 0.3)
        elements += _pad(1, "3V3_DP", 9.0, 0.3)
        elements += _pad(2, "3V3_DM", -9.0, -0.3)
        elements += _pad(3, "3V3_DM", 9.0, -0.3)
        result = plan(elements)
        self.assertEqual(result.reason_code, "power_rail")
        for rail in ("GND", "V5", "3V3", "VBUS", "VCC"):
            self.assertTrue(reserve._is_power_name(rail), rail)
        for signal in ("USB_DP", "USB_DM", "SWCLK", "ROW0"):
            self.assertFalse(reserve._is_power_name(signal), signal)

    def test_no_corridor_names_the_width_that_does_exist(self) -> None:
        """The blocker is placement, not routing, and that message is
        actionable. Never narrow the requirement to make a corridor appear."""
        result = plan(walled_board(gap_half=0.25, layers=2))
        self.assertEqual(result.status, "refused")
        self.assertIn(result.reason_code, ("no_corridor", "no_end_pocket"))
        if result.reason_code == "no_corridor":
            need = result.measurements["needMm"]
            widest = result.measurements["widestCorridorMm"]
            self.assertLess(widest, need)
            self.assertIn("placement, not routing", result.reason)

    def test_a_corridor_that_cuts_the_board_in_two_is_refused(self) -> None:
        """Remove the corridor from free space and every net must still reach
        itself. Asked as a difference — with and without — on the same raster,
        so a net the grid cannot connect either way is not blamed on us.

        The three gaps below are the measured boundary of one 0.874 mm-wide
        corridor on a one-layer board, and they are the whole behaviour in one
        line: **0.55 mm no corridor · 0.60 mm strangled · 0.80 mm reserved.**
        A gap that a corridor only just fits through is a gap that has nothing
        left in it, which is exactly why this test exists.
        """
        result = plan(walled_board(gap_half=0.60, layers=1))
        self.assertEqual(result.status, "refused", result.as_dict())
        self.assertEqual(result.reason_code, "strangle")
        # Every cut net, not the first one — "which nets did this break" is
        # what a human acts on. Here that is the victim *and* the pair itself,
        # which is the worst case: the autorouter could not have connected the
        # pair in the reserved build, so stage 0c would get no trace to fix.
        self.assertEqual(sorted(result.measurements["strangledNets"]),
                         ["net_USB_DM", "net_USB_DP", "net_VICTIM"])
        self.assertIn("net_VICTIM", result.reason)
        self.assertIn("including the pair itself", result.reason)

    def test_a_gap_too_narrow_for_the_corridor_refuses_earlier(self) -> None:
        result = plan(walled_board(gap_half=0.55, layers=1))
        self.assertEqual(result.reason_code, "no_corridor")

    def test_the_same_board_with_room_to_go_round_is_not_strangled(self) -> None:
        result = plan(walled_board(gap_half=0.80, layers=1))
        self.assertEqual(result.status, "planned", result.as_dict())

    def test_the_area_cap_refuses_rather_than_shrinking_the_corridor(self) -> None:
        original = reserve.RESERVE_MAX_AREA_MM2
        try:
            reserve.RESERVE_MAX_AREA_MM2 = 1.0
            result = plan(open_board())
        finally:
            reserve.RESERVE_MAX_AREA_MM2 = original
        self.assertEqual(result.reason_code, "area_cap")
        self.assertGreater(result.measurements["areaMm2"], 1.0)

    def test_a_gate_that_cannot_run_is_a_refusal_not_a_pass(self) -> None:
        """Ledger lesson E, applied to this pass's own gates. Without numpy
        nothing here can be measured, and "we could not check" is never
        allowed to read as "we checked and it is fine"."""
        original = reserve._np
        try:
            reserve._np = None
            result = plan(open_board())
        finally:
            reserve._np = original
        self.assertEqual(result.status, "refused")
        self.assertEqual(result.reason_code, "no_numpy")

    def test_a_strangle_test_that_cannot_run_is_a_refusal_too(self) -> None:
        original = reserve._np
        try:
            reserve._np = None
            cut = reserve._strangle_test(
                diffpair._Board(open_board()), [], None, PROFILE, [],
                ("net_USB_DP", "net_USB_DM"))
        finally:
            reserve._np = original
        self.assertTrue(cut)
        self.assertIn("numpy", cut[0])

    def test_a_refusal_is_still_a_report(self) -> None:
        """A reservation that fired and lost still gets reported — an absent
        value makes no claim and nothing checks a claim nobody makes."""
        result = plan(walled_board(gap_half=0.56, layers=1))
        payload = result.as_dict()
        self.assertEqual(payload["status"], "refused")
        self.assertTrue(payload["reasonCode"])
        self.assertTrue(payload["reason"])


# ---------------------------------------------------------------------------
# The consumer test
# ---------------------------------------------------------------------------


class _Outcome:
    def __init__(self, status, reason="", reason_code=""):
        self.net_p, self.net_n = "USB_DP", "USB_DM"
        self.status, self.reason, self.reason_code = status, reason, reason_code


class _Result:
    def __init__(self, *pairs):
        self.pairs = list(pairs)


class WhoAsksForOne(unittest.TestCase):
    def test_only_a_pair_refused_for_want_of_room_gets_a_corridor(self) -> None:
        self.assertEqual(
            reserve.wants_reservation(
                _Result(_Outcome("refused", reason_code="no_corridor"))),
            ("USB_DP", "USB_DM"))
        for outcome in (
            _Outcome("routed"),
            _Outcome("skipped", "5 pads each"),
            _Outcome("refused", reason_code="legs_too_close"),
            _Outcome("refused", reason_code="clearance"),
        ):
            self.assertIsNone(reserve.wants_reservation(_Result(outcome)),
                              outcome.reason_code or outcome.status)
        self.assertIsNone(reserve.wants_reservation(None))
        self.assertIsNone(reserve.wants_reservation(_Result()))

    def test_the_prose_fallback_matches_the_sentence_diffpair_writes(self) -> None:
        """A vendored runtime can lag the tree by a day (ledger #42), so an
        outcome with no `reason_code` still has to be recognised. This pins the
        fallback against the string `diffpair` actually produces rather than
        against one somebody remembered."""
        refusal = _no_corridor_refusal_text()
        self.assertTrue(reserve._NO_CORRIDOR_PROSE.search(refusal), refusal)
        self.assertEqual(
            reserve.wants_reservation(_Result(_Outcome("refused", refusal))),
            ("USB_DP", "USB_DM"))


def _no_corridor_refusal_text() -> str:
    """Make stage 0c refuse for want of room and return what it said.

    One layer, because on two the pair simply dives under the wall — which is
    a good reminder that a corridor is only ever needed where the board has
    genuinely run out of room.
    """
    board = diffpair._Board(reserve.placement_elements(walled_board(0.25, 1)))
    obstacles = diffpair.collect_obstacles(board, PROFILE, set())
    outline = diffpair._board_outline(board)
    ports_p = board.pcb_ports_of_net("net_USB_DP")
    ports_n = board.pcb_ports_of_net("net_USB_DM")
    ends = diffpair._pair_ends(ports_p, ports_n)
    out = diffpair._route_one_pair(board, obstacles, outline, PROFILE, ends,
                                   "top", 0.15, "net_USB_DP", "net_USB_DM")
    # The pass has returned a 4-tuple historically and a richer object since;
    # either way the prose is what a stale runtime would hand `wants_reservation`.
    if isinstance(out, tuple):
        return str(out[3])
    refusal = getattr(out, "refusal", None)
    return str(getattr(refusal, "message", "") or "")


# ---------------------------------------------------------------------------
# Injection — the text edit that puts it in front of the autorouter
# ---------------------------------------------------------------------------


BOARD_TSX = '''import { keepoutRadiusForHole } from "./glue"

export default () => (
  <board
    width="24mm"
    height="20mm"
    autorouterEffortLevel="5x"
    outline={[{ x: 0, y: 0 }, { x: 24, y: 0 }]}
  >
    <resistor name="R1" resistance="1k" pcbX={0} pcbY={0} />
  </board>
)
'''


class Injection(unittest.TestCase):
    def setUp(self) -> None:
        result = plan(open_board())
        assert result.reservation is not None
        self.reservation = result.reservation

    def test_the_keepouts_land_inside_the_board_tag(self) -> None:
        patched = reserve.inject_into_source(BOARD_TSX, self.reservation)
        self.assertFalse(patched.startswith("!"), patched[:200])
        self.assertIn("<keepout", patched)
        self.assertEqual(patched.count("<keepout"), len(self.reservation.discs))
        # After the opening tag, before the first child.
        self.assertLess(patched.index("<keepout"), patched.index("<resistor"))
        self.assertGreater(patched.index("<keepout"), patched.index("<board"))
        # And the props the tag already had are untouched.
        self.assertIn('autorouterEffortLevel="5x"', patched)
        self.assertIn('outline={[{ x: 0, y: 0 }, { x: 24, y: 0 }]}', patched)

    def test_a_prop_containing_a_brace_or_a_quote_does_not_end_the_tag(self) -> None:
        """A regex that stops at the first `>` inserts the reservation into the
        middle of a prop. `<board>`'s props are JSX expressions."""
        source = ('<board width={12 > 10 ? 12 : 10} title="a > b" '
                  'outline={[{ x: 1 }]}>\n  <hole name="H1" />\n</board>')
        end = reserve._board_tag_end(source)
        assert end is not None
        self.assertEqual(source[end - 1], ">")
        self.assertTrue(source[end:].lstrip().startswith("<hole"))

    def test_it_finds_the_tag_on_every_board_we_actually_ship(self) -> None:
        """The three example boards, because a text transform is only as good
        as the text it has met.

        terminal-keyboard is the one that matters: its ``<board>`` tag ends
        with a block comment, so the two characters before the ``>`` are
        ``*/``. A self-closing test that asks "does this end in a slash"
        answers yes, and the injector refuses on one of the three boards this
        whole mechanism exists for — with a message blaming a self-closing tag
        that is not there. Found by running it, not by reading it.
        """
        root = Path(__file__).resolve().parents[3] / "examples"
        if not root.is_dir():
            self.skipTest("examples/ is not in this tree")
        seen = 0
        for name in ("harness-puck", "terminal-keyboard", "hydrate-coaster"):
            source = root / name / "boards" / "main.tsx"
            if not source.is_file():
                continue
            seen += 1
            text = source.read_text(encoding="utf-8")
            end = reserve._board_tag_end(text)
            self.assertIsNotNone(end, name)
            assert end is not None
            self.assertEqual(text[end - 1], ">", name)
            # What follows the tag is a child, not the tail of a prop.
            self.assertTrue(text[end:].lstrip().startswith(("{", "<")), name)
            patched = reserve.inject_into_source(text, self.reservation)
            self.assertFalse(patched.startswith("!"), f"{name}: {patched[:120]}")
            self.assertEqual(patched.count("<keepout"),
                             text.count("<keepout") + len(self.reservation.discs))
        self.assertEqual(seen, 3)

    def test_a_block_comment_before_the_close_is_not_a_self_closing_tag(self) -> None:
        source = '<board width="1mm"\n  /* a note */\n>\n  <hole name="H1" />\n</board>'
        end = reserve._board_tag_end(source)
        assert end is not None
        self.assertTrue(source[end:].lstrip().startswith("<hole"))

    def test_a_self_closing_board_has_no_children_to_insert(self) -> None:
        self.assertIsNone(reserve._board_tag_end('<board width="10mm" />'))
        refused = reserve.inject_into_source('<board width="10mm" />',
                                             self.reservation)
        self.assertTrue(refused.startswith("!"))

    def test_a_source_with_no_board_tag_is_refused(self) -> None:
        refused = reserve.inject_into_source("export default () => null\n",
                                             self.reservation)
        self.assertTrue(refused.startswith("!"))

    def test_it_never_injects_twice(self) -> None:
        once = reserve.inject_into_source(BOARD_TSX, self.reservation)
        twice = reserve.inject_into_source(once, self.reservation)
        self.assertTrue(twice.startswith("!"), twice[:120])

    def test_the_numbers_written_are_the_numbers_recorded(self) -> None:
        """The strip pass demands exactly one match at 1e-6. That is only true
        by construction if the TSX and the record carry the same rounded
        number, so this asserts the digits, not the intent."""
        for disc in self.reservation.discs:
            tsx = disc.as_tsx()
            self.assertIn(f"pcbX={{{disc.cx:.6f}}}", tsx)
            self.assertIn(f"pcbY={{{disc.cy:.6f}}}", tsx)
            self.assertIn(f'radius="{disc.radius:.6f}mm"', tsx)
            self.assertEqual(float(f"{disc.cx:.6f}"), disc.cx)
            self.assertEqual(float(f"{disc.cy:.6f}"), disc.cy)
            self.assertEqual(float(f"{disc.radius:.6f}"), disc.radius)

    def test_it_writes_the_file_it_is_given_and_only_that_file(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mirror = Path(tmp) / "main.tsx"
            mirror.write_text(BOARD_TSX, encoding="utf-8")
            result = reserve.inject_reservation(mirror, self.reservation)
            self.assertTrue(result.ok, result.reason)
            self.assertEqual(result.discs, len(self.reservation.discs))
            self.assertIn("<keepout", mirror.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Release — taking exactly what we wrote back out
# ---------------------------------------------------------------------------


def _as_keepout(index: int, disc: reserve.Disc) -> dict:
    """What the compile turns one injected `<keepout>` into."""
    element = {"type": "pcb_keepout", "pcb_keepout_id": f"pcb_keepout_{index}",
               "subcircuit_id": "subcircuit_source_group_12"}
    element.update(disc.as_element())
    return element


MOUNTING_HOLE_KEEPOUT = {
    "type": "pcb_keepout", "pcb_keepout_id": "pcb_keepout_0",
    "layers": ["top", "bottom"], "shape": "circle", "radius": 1.35,
    "center": {"x": 0, "y": 22}, "subcircuit_id": "subcircuit_source_group_12",
}
USB_BELLY_KEEPOUT = {
    "type": "pcb_keepout", "pcb_keepout_id": "pcb_keepout_1",
    "layers": ["top", "bottom"], "shape": "rect", "width": 7.3, "height": 1.23,
    "center": {"x": 0, "y": -28.1}, "subcircuit_id": "subcircuit_source_group_12",
}


class Release(unittest.TestCase):
    def setUp(self) -> None:
        result = plan(open_board())
        assert result.reservation is not None
        self.reservation = result.reservation
        self.discs = self.reservation.discs
        self.foreign = [MOUNTING_HOLE_KEEPOUT, USB_BELLY_KEEPOUT]
        self.elements = (
            [{"type": "pcb_board", "pcb_board_id": "pcb_board_0"}]
            + self.foreign
            + [_as_keepout(2 + i, d) for i, d in enumerate(self.discs)]
            + [{"type": "pcb_trace", "pcb_trace_id": "t0"}]
        )

    def test_it_removes_exactly_what_it_wrote(self) -> None:
        kept, result = reserve.strip_reserved_keepouts(self.elements, self.discs)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.removed, len(self.discs))
        assert kept is not None
        self.assertEqual([e for e in kept if e.get("type") == "pcb_keepout"],
                         self.foreign)
        self.assertEqual(len(kept), len(self.elements) - len(self.discs))

    def test_ledger_1_and_ledger_7_survive_it(self) -> None:
        """Every keepout on every board today is block-authored — the USB-C
        belly rect (#1) and the mounting holes (#7). Never by id, never by
        type, never "all keepouts"."""
        kept, _ = reserve.strip_reserved_keepouts(self.elements, self.discs)
        assert kept is not None
        self.assertIn(MOUNTING_HOLE_KEEPOUT, kept)
        self.assertIn(USB_BELLY_KEEPOUT, kept)

    def test_a_missing_entry_strips_nothing_at_all(self) -> None:
        elements = [e for e in self.elements
                    if e.get("pcb_keepout_id") != "pcb_keepout_5"]
        kept, result = reserve.strip_reserved_keepouts(elements, self.discs)
        self.assertIsNone(kept)
        self.assertFalse(result.ok)
        self.assertEqual(result.removed, 0)
        self.assertIn("matches 0 keepouts", result.reason)

    def test_two_matching_keepouts_strip_nothing_at_all(self) -> None:
        """Two matches means we cannot tell which one we wrote, and one of the
        others could be a user's."""
        elements = list(self.elements) + [_as_keepout(99, self.discs[0])]
        kept, result = reserve.strip_reserved_keepouts(elements, self.discs)
        self.assertIsNone(kept)
        self.assertFalse(result.ok)
        self.assertIn("matches 2 keepouts", result.reason)

    def test_a_near_miss_is_named_so_a_frame_shift_is_diagnosable(self) -> None:
        """If the compiler ever placed our keepouts relative to the board
        centre instead of the origin, every entry would miss by one constant
        offset. That must show up in the first build, not as silence."""
        shifted = []
        for element in self.elements:
            if element.get("type") == "pcb_keepout" and \
                    element.get("pcb_keepout_id", "").startswith("pcb_keepout_") and \
                    element not in self.foreign:
                element = dict(element)
                element["center"] = {"x": element["center"]["x"] + 5.0,
                                     "y": element["center"]["y"]}
            shifted.append(element)
        kept, result = reserve.strip_reserved_keepouts(shifted, self.discs)
        self.assertIsNone(kept)
        self.assertIn("nearest keepout is", result.reason)

    def test_a_keepout_a_hair_outside_the_tolerance_is_not_ours(self) -> None:
        disc = self.discs[0]
        near = reserve.Disc(disc.layer, disc.cx + 2e-6, disc.cy, disc.radius)
        self.assertFalse(disc.matches(_as_keepout(50, near)))
        inside = reserve.Disc(disc.layer, disc.cx + 5e-7, disc.cy, disc.radius)
        self.assertTrue(disc.matches(_as_keepout(51, inside)))

    def test_a_different_layer_is_a_different_keepout(self) -> None:
        disc = self.discs[0]
        other = dict(_as_keepout(52, disc))
        other["layers"] = ["bottom"]
        self.assertFalse(disc.matches(other))
        both = dict(_as_keepout(53, disc))
        both["layers"] = ["top", "bottom"]
        self.assertFalse(disc.matches(both))

    def test_it_rewrites_the_file_only_when_the_release_succeeds(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.circuit.json"
            path.write_text(json.dumps(self.elements), encoding="utf-8")
            result = reserve.release_reservation(path, self.reservation)
            self.assertTrue(result.ok, result.reason)
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                [e for e in after if e.get("type") == "pcb_keepout"],
                self.foreign)

            # And a second release, with the elements already gone, refuses and
            # leaves the file exactly as it is.
            before = path.read_text(encoding="utf-8")
            again = reserve.release_reservation(path, self.reservation)
            self.assertFalse(again.ok)
            self.assertEqual(path.read_text(encoding="utf-8"), before)


class TheRecordThePipelineCarries(unittest.TestCase):
    def setUp(self) -> None:
        result = plan(open_board())
        assert result.reservation is not None
        self.reservation = result.reservation

    def test_the_record_round_trips_through_json(self) -> None:
        record = json.loads(json.dumps(self.reservation.record()))
        discs = reserve.reservation_from_record(record)
        self.assertEqual(discs, self.reservation.discs)

    def test_a_malformed_record_is_never_half_read(self) -> None:
        for bad in (None, [], {}, {"keepouts": []},
                    {"keepouts": [{"shape": "rect", "layers": ["top"],
                                   "center": {"x": 0, "y": 0},
                                   "width": 1, "height": 1}]},
                    {"keepouts": [{"shape": "circle", "layers": ["top", "bottom"],
                                   "center": {"x": 0, "y": 0}, "radius": 1}]}):
            self.assertIsNone(reserve.reservation_from_record(bad), bad)

    def test_the_record_is_shaped_like_the_element_it_becomes(self) -> None:
        record = self.reservation.record()
        self.assertEqual(record["pair"], ["USB_DP", "USB_DM"])
        for entry in record["keepouts"]:
            self.assertEqual(set(entry), {"shape", "layers", "center", "radius"})

    def test_the_consumer_can_read_it(self) -> None:
        """`diffpair` reads the same record to know which keepouts to stop
        treating as obstacles. If that reader exists in this tree, it has to
        accept what this writer writes — one format, two modules."""
        reader = getattr(diffpair, "Reservation", None)
        if reader is None or not hasattr(reader, "from_obj"):
            self.skipTest("diffpair has no reservation consumer in this tree")
        parsed = reader.from_obj(self.reservation.record())
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed.keepouts), len(self.reservation.discs))

    def test_both_modules_agree_on_the_match_tolerance(self) -> None:
        peer = getattr(diffpair, "RESERVE_MATCH_TOL_MM", None)
        if peer is None:
            self.skipTest("diffpair has no match tolerance in this tree")
        self.assertEqual(peer, reserve.RESERVE_MATCH_TOL_MM)


class PlacementOnly(unittest.TestCase):
    def test_the_routers_own_output_is_not_evidence_about_placement(self) -> None:
        """A rebuild re-solves every trace, via and pour — 122 vias became 129
        on the measured reserved build — so a corridor planned around them is
        planned around a fiction."""
        elements = open_board() + [
            {"type": "pcb_trace", "pcb_trace_id": "t0", "route": []},
            {"type": "pcb_via", "pcb_via_id": "v0", "x": 0, "y": 0},
            {"type": "pcb_copper_pour", "pcb_copper_pour_id": "p0",
             "layer": "bottom"},
        ]
        kept = reserve.placement_elements(elements)
        self.assertEqual([e["type"] for e in kept
                          if e["type"] in reserve.ROUTER_OWNED], [])
        self.assertEqual(len(kept), len(elements) - 3)

    def test_a_corridor_is_the_same_with_or_without_the_routers_copper(self) -> None:
        """The whole reason no probe build is needed: placement is what the
        plan reads, so a routed artifact and a placement-only one plan the
        same corridor."""
        bare = plan(open_board())
        with_copper = plan(open_board() + [
            {"type": "pcb_trace", "pcb_trace_id": "t0", "route": [
                {"route_type": "wire", "x": -9.0, "y": 0.3, "width": 0.15,
                 "layer": "top"},
                {"route_type": "wire", "x": 9.0, "y": 0.3, "width": 0.15,
                 "layer": "top"}]},
            {"type": "pcb_via", "pcb_via_id": "v0", "x": 4.0, "y": 4.0,
             "outer_diameter": 0.6, "hole_diameter": 0.3},
        ])
        assert bare.reservation is not None and with_copper.reservation is not None
        self.assertEqual(bare.reservation.discs, with_copper.reservation.discs)

    def test_the_pairs_own_copper_still_sets_the_width(self) -> None:
        """Width is the one thing read from the route: the copper the pair is
        drawn in today is the copper stage 0c will lay it in tomorrow."""
        elements = open_board() + [
            {"type": "pcb_trace", "pcb_trace_id": "t_dp",
             "connectsTo": ["pcb_port_0", "pcb_port_1"], "route": [
                 {"route_type": "wire", "x": -9.0, "y": 0.3, "width": 0.25,
                  "layer": "top", "start_pcb_port_id": "pcb_port_0"},
                 {"route_type": "wire", "x": 9.0, "y": 0.3, "width": 0.25,
                  "layer": "top", "end_pcb_port_id": "pcb_port_1"}]},
        ]
        result = plan(elements)
        assert result.reservation is not None
        self.assertAlmostEqual(result.reservation.width_mm, 0.25, places=9)
        wider = diffpair.pair_span(PROFILE, 0.25, allow_layers=False)
        self.assertAlmostEqual(
            result.reservation.reserve_r_mm,
            wider.half_span_mm + reserve.RESERVE_MARGIN_MM, places=12)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
