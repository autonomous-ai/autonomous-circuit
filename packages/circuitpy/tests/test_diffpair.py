"""The differential-pair route pass: does it pair the pair, and refuse the rest?

The EE review (2026-08-15, finding 3) asked for D+/D- to run parallel at a
constant gap over a reference. `circuitpy.diffpair` replaces the autorouter's
copper for a pair with one coupled pair, after the fact, in the space the
autorouter's own detour frees.

The interesting assertions are the ones about *not* acting. A repair pass that
can only be observed when it fires is a pass nobody can trust: every test below
pins a refusal as hard as it pins a success, and the file is checked byte for
byte on the boards where nothing should happen.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import diffpair  # noqa: E402
from circuitpy.fab import PROFILES  # noqa: E402

PROFILE = PROFILES["jlcpcb"]


# ---------------------------------------------------------------------------
# A minimal board with a routable pair: four pads, an open field between them
# and one obstacle in the middle that both legs have to go round together.
# ---------------------------------------------------------------------------


def _source_net(index: int, name: str) -> dict:
    return {
        "type": "source_net",
        "source_net_id": f"source_net_{index}",
        "name": name,
        "subcircuit_connectivity_map_key": f"net_{name}",
    }


def _port(index: int, net: str, x: float, y: float) -> list[dict]:
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
            "layers": ["top"],
            "x": x,
            "y": y,
        },
        {
            "type": "pcb_smtpad",
            "pcb_smtpad_id": f"pcb_smtpad_{index}",
            "pcb_port_id": f"pcb_port_{index}",
            "layer": "top",
            "shape": "rect",
            "width": 0.4,
            "height": 0.4,
            "x": x,
            "y": y,
        },
    ]


def _trace(trace_id: str, points: list[tuple[float, float]], start: str,
           end: str) -> dict:
    route = []
    for i, (x, y) in enumerate(points):
        point = {"route_type": "wire", "x": x, "y": y, "width": 0.15,
                 "layer": "top"}
        if i == 0:
            point["start_pcb_port_id"] = start
        if i == len(points) - 1:
            point["end_pcb_port_id"] = end
        route.append(point)
    return {
        "type": "pcb_trace",
        "pcb_trace_id": trace_id,
        "connectsTo": [start, end],
        "route": route,
    }


def sample_board() -> list[dict]:
    """D+ and D- from x=-6 to x=6, routed by a router that split them apart."""
    elements: list[dict] = [
        {
            "type": "pcb_board",
            "pcb_board_id": "pcb_board_0",
            "center": {"x": 0, "y": 0},
            "width": 20,
            "height": 20,
            "num_layers": 2,
            "thickness": 1.6,
        },
        {
            "type": "source_component",
            "source_component_id": "source_component_0",
            "name": "U1",
        },
        _source_net(0, "USB_DP"),
        _source_net(1, "USB_DM"),
    ]
    elements += _port(0, "USB_DP", -6.0, 0.3)
    elements += _port(1, "USB_DP", 6.0, 0.3)
    elements += _port(2, "USB_DM", -6.0, -0.3)
    elements += _port(3, "USB_DM", 6.0, -0.3)
    # The router's answer: one leg over the top of the board, one under it.
    elements.append(_trace("trace_dp", [(-6.0, 0.3), (-6.0, 6.0), (6.0, 6.0),
                                        (6.0, 0.3)], "pcb_port_0", "pcb_port_1"))
    elements.append(_trace("trace_dm", [(-6.0, -0.3), (-6.0, -7.0), (6.0, -7.0),
                                        (6.0, -0.3)], "pcb_port_2", "pcb_port_3"))
    return elements


def write(tmp: Path, elements: list[dict]) -> Path:
    path = tmp / "main.circuit.json"
    path.write_text(json.dumps(elements, indent=2), encoding="utf-8")
    return path


class PairDetection(unittest.TestCase):
    def test_the_connector_side_pair_is_found_too(self):
        """``USB_DP_CONN``/``USB_DM_CONN`` is a pair. The end-anchored match
        this replaced missed it on all three example boards — the half of the
        pair closest to the cable was never measured."""
        pairs = diffpair.find_pairs(
            ["USB_DP", "USB_DM", "USB_DP_CONN", "USB_DM_CONN", "GND", "V5"]
        )
        self.assertEqual(
            sorted(pairs),
            [("USB_DP", "USB_DM"), ("USB_DP_CONN", "USB_DM_CONN")],
        )

    def test_a_net_is_claimed_by_only_one_pair(self):
        pairs = diffpair.find_pairs(["DP", "DM"])
        self.assertEqual(pairs, [("DP", "DM")])

    def test_a_lone_half_is_not_a_pair(self):
        self.assertEqual(diffpair.find_pairs(["USB_DP", "GND"]), [])


class Offsetting(unittest.TestCase):
    def test_the_two_tracks_hold_a_constant_gap(self):
        """The tracks are the same polyline offset both ways, so the gap is
        constant by construction — that is the whole design of the pass."""
        line = [(0.0, 0.0), (5.0, 0.0), (8.0, 3.0), (12.0, 3.0)]
        left = diffpair._offset_polyline(line, 0.15)
        right = diffpair._offset_polyline(line, -0.15)
        self.assertEqual(len(left), len(right))
        for i in range(len(left) - 1):
            for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                x = left[i][0] + (left[i + 1][0] - left[i][0]) * t
                y = left[i][1] + (left[i + 1][1] - left[i][1]) * t
                gap = min(
                    diffpair._seg_point_distance(
                        right[j][0], right[j][1], right[j + 1][0], right[j + 1][1],
                        x, y)
                    for j in range(len(right) - 1)
                )
                # Never tighter than the nominal pitch, and the mitre at a
                # 45-degree corner opens it by 1/cos(22.5) at most.
                self.assertGreaterEqual(gap, 0.30 - 1e-9)
                self.assertLessEqual(gap, 0.30 / math.cos(math.radians(22.5)))


class RoutingASamplePair(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())

    def test_the_pair_is_coupled_and_the_skew_collapses(self):
        path = write(self.tmp, sample_board())
        result = diffpair.route_diff_pairs(path, PROFILE)
        self.assertTrue(result.changed, result.as_dict())
        pair = result.pairs[0]
        self.assertEqual(pair.status, "routed", pair.reason)
        self.assertLess(pair.before.coupled_fraction, 0.1)
        self.assertGreater(pair.after.coupled_fraction, 0.8)
        self.assertLess(pair.after.skew_mm, pair.before.skew_mm)
        # Both legs get shorter: a pair that travels together stops detouring.
        self.assertLess(pair.after.length_p_mm, pair.before.length_p_mm)
        self.assertLess(pair.after.length_n_mm, pair.before.length_n_mm)

    def test_the_new_copper_clears_everything_it_has_to(self):
        path = write(self.tmp, sample_board())
        result = diffpair.route_diff_pairs(path, PROFILE)
        pair = result.pairs[0]
        self.assertGreaterEqual(pair.after.worst_clearance_mm,
                                PROFILE.min_clearance_mm - 1e-9)

    def test_it_is_deterministic(self):
        (self.tmp / "a").mkdir(exist_ok=True)
        (self.tmp / "b").mkdir(exist_ok=True)
        first = write(self.tmp / "a", sample_board())
        second = write(self.tmp / "b", sample_board())
        diffpair.route_diff_pairs(first, PROFILE)
        diffpair.route_diff_pairs(second, PROFILE)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_running_twice_changes_nothing_the_second_time(self):
        """Byte-identical is the assertion, not the status.

        A second run either refuses (the pair it already laid is no more
        coupled than itself) or re-derives exactly the same geometry, and on
        the real hydrate-coaster board it does the latter. Both are fine; what
        must never happen is the artifact drifting under a repeated pass.
        """
        path = write(self.tmp, sample_board())
        diffpair.route_diff_pairs(path, PROFILE)
        once = path.read_bytes()
        diffpair.route_diff_pairs(path, PROFILE)
        self.assertEqual(path.read_bytes(), once)

    def test_a_board_with_no_pair_is_left_byte_for_byte_alone(self):
        elements = [e for e in sample_board()
                    if not (e.get("type") == "source_net")]
        path = write(self.tmp, elements)
        before = path.read_bytes()
        result = diffpair.route_diff_pairs(path, PROFILE)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result.pairs, [])

    def test_a_multi_drop_pair_is_skipped_with_the_reason_said_out_loud(self):
        elements = sample_board()
        elements += _port(9, "USB_DP", 0.0, 8.0)  # a third pad on D+
        path = write(self.tmp, elements)
        before = path.read_bytes()
        result = diffpair.route_diff_pairs(path, PROFILE)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result.pairs[0].status, "skipped")
        self.assertIn("two-terminal", result.pairs[0].reason)

    def test_a_pair_with_nowhere_to_go_keeps_the_router_s_copper(self):
        """A wall of other-net copper across the middle: there is no corridor,
        so the pass refuses and says so rather than routing through it."""
        elements = sample_board()
        elements.append(_source_net(2, "GND"))
        wall = {
            "type": "pcb_trace",
            "pcb_trace_id": "trace_wall",
            "connectsTo": [],
            "subcircuit_connectivity_map_key": "net_GND",
            "route": [
                {"route_type": "wire", "x": 0.0, "y": -9.0, "width": 4.0,
                 "layer": "top"},
                {"route_type": "wire", "x": 0.0, "y": 9.0, "width": 4.0,
                 "layer": "top"},
            ],
        }
        bottom = dict(wall)
        bottom["pcb_trace_id"] = "trace_wall_bottom"
        bottom["route"] = [dict(p, layer="bottom") for p in wall["route"]]
        elements += [wall, bottom]
        path = write(self.tmp, elements)
        before = path.read_bytes()
        result = diffpair.route_diff_pairs(path, PROFILE)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertTrue(result.pairs[0].reason)


class GradedByTheRealGate(unittest.TestCase):
    """The pass keeps its rewrite only if the pipeline's own gate agrees.

    This is the assertion the first version of this pass did not have, and it
    is the one that would have caught it: it modelled every clearance rule it
    knew about, passed its own tests, and shipped a board with eight
    `dfm_hole_clearance` errors — one via's pad against another via's drill,
    a cross term nobody had written down.
    """

    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())

    def test_a_gate_that_finds_a_new_error_reverts_the_rewrite(self):
        path = write(self.tmp, sample_board())
        before = path.read_bytes()

        original = json.loads(before.decode())

        def grumpy(elements, at):
            # Clean on the router's copper, one error on anything else.
            if elements == original:
                return []
            return [{"kind": "invented_defect", "severity": "error",
                     "part": "board", "detail": "no"}]

        result = diffpair.route_diff_pairs(path, PROFILE, grade=grumpy)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertIn("invented_defect", result.pairs[0].reason)

    def test_an_error_the_board_already_had_does_not_block_the_rewrite(self):
        path = write(self.tmp, sample_board())

        def always_angry(elements, at):
            return [{"kind": "pre_existing", "severity": "error",
                     "part": "board", "detail": "was already here"}]

        result = diffpair.route_diff_pairs(path, PROFILE, grade=always_angry)
        self.assertEqual(result.pairs[0].status, "routed")

    def test_a_gate_that_cannot_run_is_not_permission_to_ship(self):
        path = write(self.tmp, sample_board())
        before = path.read_bytes()

        def broken(elements, at):
            raise RuntimeError("node is missing")

        result = diffpair.route_diff_pairs(path, PROFILE, grade=broken)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result.pairs[0].status, "refused")


class UsingAReservedCorridor(unittest.TestCase):
    """EE finding 3, the half this pass could not reach on its own.

    The board below is the shape of the real problem in miniature: a wall of
    other-net copper the pair cannot cross, with **one** gap in it. In the real
    pipeline that gap exists because a keepout was written before the
    autorouter ran and the router went round it. Here the gap is built in and
    the keepout sits in it, which is the same board circuit.json shows the pass:
    a channel that is empty of copper and covered by an obstacle this pass
    treats as hard at clearance 0.0.

    So the assertions are the two halves of "use the reservation":
    **without** the record the keepout blocks the pair and the pass refuses;
    **with** it the pair routes through the channel — and every keepout the
    record does not name keeps blocking, whatever it costs.
    """

    #: The wall's gap, and the keepout that reserves it. Both layers, because
    #: the wall is on both.
    GAP = {
        "type": "pcb_keepout",
        "pcb_keepout_id": "pcb_keepout_reserved",
        "shape": "rect",
        "layers": ["top", "bottom"],
        "width": 4.4,
        "height": 3.0,
        "center": {"x": 0.0, "y": 0.0},
    }

    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())

    def walled_board(self, extra: list[dict] | None = None) -> list[dict]:
        """`sample_board` plus a wall with a 3mm gap at the origin."""
        elements = sample_board()
        elements.append(_source_net(2, "GND"))
        # 4.0mm of copper has a 2.0mm half-width, and a capsule's round cap
        # reaches that far past its endpoint too — so the wall's free band is
        # |y| < 2.0, not |y| < 4.0. Getting that wrong is how a "control" test
        # measures nothing.
        for name, y0, y1 in (("upper", 4.0, 9.0), ("lower", -9.0, -4.0)):
            for layer in ("top", "bottom"):
                elements.append({
                    "type": "pcb_trace",
                    "pcb_trace_id": f"trace_wall_{name}_{layer}",
                    "connectsTo": [],
                    "subcircuit_connectivity_map_key": "net_GND",
                    "route": [
                        {"route_type": "wire", "x": 0.0, "y": y0, "width": 4.0,
                         "layer": layer},
                        {"route_type": "wire", "x": 0.0, "y": y1, "width": 4.0,
                         "layer": layer},
                    ],
                })
        elements.extend(extra or [])
        return elements

    def record(self, keepout: dict) -> list[dict]:
        """What the pipeline would have recorded when it wrote this keepout —
        the numbers only, never the id."""
        return [{k: v for k, v in keepout.items()
                 if k in ("shape", "layers", "width", "height", "radius",
                          "center")}]

    def test_the_gap_is_routable_before_anything_is_reserved_in_it(self):
        """The control. Without the keepout the pair goes through the gap, so
        every refusal below is the keepout's doing and not the wall's."""
        path = write(self.tmp, self.walled_board())
        result = diffpair.route_diff_pairs(path, PROFILE)
        self.assertEqual(result.pairs[0].status, "routed",
                         result.pairs[0].reason)

    def test_an_unclaimed_keepout_in_the_only_gap_refuses(self):
        path = write(self.tmp, self.walled_board([self.GAP]))
        before = path.read_bytes()
        result = diffpair.route_diff_pairs(path, PROFILE)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertEqual(result.pairs[0].reason_code, "no_corridor")
        # The machine-readable half stage 0R keys on: how much clear copper the
        # narrowest attempt asked for.
        self.assertIsNotNone(result.pairs[0].need_mm)
        self.assertGreater(result.pairs[0].need_mm, 0.5)

    def test_the_same_keepout_handed_back_as_a_reservation_routes_the_pair(self):
        path = write(self.tmp, self.walled_board([self.GAP]))
        result = diffpair.route_diff_pairs(
            path, PROFILE, reservation=self.record(self.GAP))
        pair = result.pairs[0]
        self.assertEqual(pair.status, "routed", pair.reason)
        self.assertEqual(pair.reserved, {"entries": 1, "matched": 1,
                                         "released": 0, "usable": True,
                                         "sumOfShapeAreasMm2": 13.2,
                                         "usedCorridor": True})
        # And it is a real pair, held to the same bar as any other route.
        self.assertGreater(pair.after.coupled_fraction, 0.8)
        self.assertLess(pair.after.skew_mm, pair.before.skew_mm)
        self.assertGreaterEqual(pair.after.worst_clearance_mm,
                                PROFILE.min_clearance_mm - 1e-9)

    def test_a_keepout_the_record_does_not_name_still_blocks(self):
        """The one that matters. A reservation is permission to enter *its own*
        channel, and nothing else: a second keepout in the same gap — a user's
        rule, a mounting hole, ledger #1's USB-C belly rect — is still a wall."""
        foreign = {
            "type": "pcb_keepout",
            "pcb_keepout_id": "pcb_keepout_someone_elses",
            "shape": "circle",
            "layers": ["top", "bottom"],
            "radius": 1.6,
            "center": {"x": 0.0, "y": 0.0},
        }
        path = write(self.tmp, self.walled_board([self.GAP, foreign]))
        before = path.read_bytes()
        result = diffpair.route_diff_pairs(
            path, PROFILE, reservation=self.record(self.GAP))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertEqual(result.pairs[0].reason_code, "no_corridor")

    def test_a_record_that_matches_nothing_unblocks_nothing(self):
        """Zero matches is safe and expected — a release pass may have stripped
        the element already — but it is never a licence to enter a keepout that
        *is* still there. Same numbers, wrong size: the gap stays shut."""
        wrong = dict(self.GAP, width=4.5)
        path = write(self.tmp, self.walled_board([self.GAP]))
        result = diffpair.route_diff_pairs(
            path, PROFILE, reservation=self.record(wrong))
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertEqual(result.pairs[0].reserved["matched"], 0)
        self.assertEqual(result.pairs[0].reserved["released"], 1)

    def test_a_record_matching_two_keepouts_drops_the_whole_reservation(self):
        """We cannot tell which one we wrote, and the other one could be a
        user's. Strip nothing, exempt nothing, say so."""
        twin = dict(self.GAP, pcb_keepout_id="pcb_keepout_twin")
        path = write(self.tmp, self.walled_board([self.GAP, twin]))
        result = diffpair.route_diff_pairs(
            path, PROFILE, reservation=self.record(self.GAP))
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertFalse(result.pairs[0].reserved["usable"])
        self.assertTrue(any("may not be ours" in n for n in result.notes),
                        result.notes)

    def test_the_pair_goes_where_the_corridor_goes(self):
        """Exempting the keepouts is not enough — the pair has to *use* the
        channel that was planned for it. On an open board the shortest route is
        a straight line; confined to a corridor that detours north, the pair
        takes the detour, which is how we know the mask is doing the work.
        """
        (self.tmp / "a").mkdir(exist_ok=True)
        straight = write(self.tmp / "a", sample_board())
        result = diffpair.route_diff_pairs(straight, PROFILE)
        flat = result.pairs[0].after.length_p_mm

        # A chain of overlapping discs, the shape the corridor planner emits
        # (a rect is not rotation-safe in circuit-json). Centres are deduped:
        # two keepouts on one point would match one record twice and the whole
        # reservation would be dropped, exactly as designed.
        detour: list[dict] = []
        seen: set[tuple[float, float]] = set()
        points = [(-6.0, 0.0), (-6.0, 4.0), (6.0, 4.0), (6.0, 0.0)]
        step = 0.4
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            span = math.hypot(x1 - x0, y1 - y0)
            for i in range(int(span / step) + 1):
                t = min(1.0, i * step / span)
                centre = (round(x0 + (x1 - x0) * t, 6),
                          round(y0 + (y1 - y0) * t, 6))
                if centre in seen:
                    continue
                seen.add(centre)
                detour.append({
                    "type": "pcb_keepout",
                    "pcb_keepout_id": f"pcb_keepout_corridor_{len(detour)}",
                    "shape": "circle",
                    "layers": ["top"],
                    "radius": 0.9,
                    "center": {"x": centre[0], "y": centre[1]},
                })
        (self.tmp / "b").mkdir(exist_ok=True)
        path = write(self.tmp / "b", sample_board() + detour)
        result = diffpair.route_diff_pairs(
            path, PROFILE, reservation=self.record_all(detour))
        pair = result.pairs[0]
        self.assertEqual(pair.status, "routed", pair.reason)
        self.assertIn("reserved corridor", pair.layer_mode)
        # The corridor is 8mm of detour longer than the straight line, and the
        # route pays for it. A pass that merely ignored the keepouts would take
        # the straight line and come out shorter.
        self.assertGreater(pair.after.length_p_mm, flat + 4.0)

    def record_all(self, keepouts: list[dict]) -> list[dict]:
        return [self.record(k)[0] for k in keepouts]

    def test_the_reserved_route_is_deterministic(self):
        for name in ("a", "b"):
            (self.tmp / name).mkdir(exist_ok=True)
        paths = [write(self.tmp / name, self.walled_board([self.GAP]))
                 for name in ("a", "b")]
        for path in paths:
            diffpair.route_diff_pairs(
                path, PROFILE, reservation=self.record(self.GAP))
        self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())

    def test_the_gate_still_decides(self):
        """A reservation buys room to search. It buys no leniency: the
        pipeline's own gate is unchanged, and a new blocking finding still
        reverts the whole rewrite (ledger lesson E)."""
        path = write(self.tmp, self.walled_board([self.GAP]))
        before = path.read_bytes()
        original = json.loads(before.decode())

        def grumpy(elements, at):
            if elements == original:
                return []
            return [{"kind": "invented_defect", "severity": "error",
                     "part": "board", "detail": "no"}]

        result = diffpair.route_diff_pairs(
            path, PROFILE, reservation=self.record(self.GAP), grade=grumpy)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertEqual(result.pairs[0].reason_code, "gate")

    def test_a_reservation_for_another_pair_is_not_this_pair_s_to_enter(self):
        path = write(self.tmp, self.walled_board([self.GAP]))
        result = diffpair.route_diff_pairs(
            path, PROFILE,
            reservation={"pair": ["OTHER_DP", "OTHER_DM"],
                         "keepouts": self.record(self.GAP)})
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertIsNone(result.pairs[0].reserved)

    def test_an_unreadable_reservation_is_ignored_not_guessed(self):
        path = write(self.tmp, self.walled_board([self.GAP]))
        result = diffpair.route_diff_pairs(
            path, PROFILE, reservation=[{"shape": "hexagon"}])
        self.assertEqual(result.pairs[0].status, "refused")
        self.assertTrue(any("could not be read" in n for n in result.notes),
                        result.notes)

    def test_collect_obstacles_exempts_only_what_it_is_told(self):
        board = diffpair._Board(self.walled_board([self.GAP]))
        every = {o.label for o in
                 diffpair.collect_obstacles(board, PROFILE, set())}
        self.assertIn("pcb_keepout_reserved", every)
        fewer = {o.label for o in diffpair.collect_obstacles(
            board, PROFILE, set(),
            exempt_keepout_ids={"pcb_keepout_reserved"})}
        self.assertNotIn("pcb_keepout_reserved", fewer)
        self.assertEqual(every - fewer, {"pcb_keepout_reserved"})


class GeometryHelpers(unittest.TestCase):
    def test_a_stadium_covers_its_rectangle(self):
        poly = diffpair._stadium_poly(0.0, 0.0, 2.0, 1.0)
        for x, y in ((0.0, 0.49), (0.5, 0.45), (-0.5, -0.45), (0.95, 0.0)):
            self.assertTrue(diffpair._point_in_poly(poly, x, y), (x, y))
        for x, y in ((1.1, 0.0), (0.0, 0.6), (0.95, 0.4)):
            self.assertFalse(diffpair._point_in_poly(poly, x, y), (x, y))

    def test_same_net_copper_is_exempt_from_its_own_clearance(self):
        obstacle = diffpair.Obstacle("disc", 0.1, ("top",), "pad", cx=0.0,
                                     cy=0.0, radius=0.2, net="A")
        segments = [(0.0, 0.0, 1.0, 0.0, "top", 0.15, "A")]
        worst, violation = diffpair._worst_clearance(segments, [obstacle], None,
                                                     0.2)
        self.assertIsNone(violation)
        # And the same copper on a different net is a violation.
        segments = [(0.0, 0.0, 1.0, 0.0, "top", 0.15, "B")]
        _, violation = diffpair._worst_clearance(segments, [obstacle], None, 0.2)
        self.assertIsNotNone(violation)

    def test_a_crossover_is_not_read_as_a_short(self):
        """The two legs cross on purpose at a swap — on different layers. A
        layer-blind measurement reads that as copper on copper."""
        p = [(0.0, 0.0, 1.0, 1.0, "top", 0.15, "A")]
        n = [(0.0, 1.0, 1.0, 0.0, "bottom", 0.15, "B")]
        self.assertGreater(diffpair._pair_self_clearance(p, n), 0.0)
        same_layer = [(0.0, 1.0, 1.0, 0.0, "top", 0.15, "B")]
        self.assertLess(diffpair._pair_self_clearance(p, same_layer), 0.0)


if __name__ == "__main__":
    unittest.main()
