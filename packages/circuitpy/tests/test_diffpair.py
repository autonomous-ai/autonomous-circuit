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
