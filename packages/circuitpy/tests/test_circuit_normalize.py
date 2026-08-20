"""A closed polygon pad ring makes every via read as inside that pad.

The defect (ledger #11, measured 2026-08-16 on hydrate-coaster): a polygon
smtpad whose last vertex repeats its first has one zero-length edge, and
`@tscircuit/checks` answers "point is on this segment" — and therefore "via is
inside this pad" — for **every point in the plane** against a zero-length
segment. The pad swallows the board.

Every assertion below is pinned from both directions, the way the failure
corpus pins a threshold: the closed ring is repaired *and* the open ring is
left alone; the false via-in-pad is dropped *and* the true one survives. A
repair that cannot tell those apart is a mask, not a fix.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import circuit_normalize  # noqa: E402
from circuitpy.circuit_normalize import normalize_circuit_json  # noqa: E402

REPO = Path(__file__).resolve().parents[3]

#: The pad that actually blocked hydrate-coaster: J1's B4A9 (VBUS), 0.6 x 1.3mm,
#: written with the closing vertex every geometry format writes.
CLOSED_PAD_POINTS = [
    {"x": 2.7001724, "y": -32.4758412},
    {"x": 2.7001724, "y": -31.1758692},
    {"x": 2.400173, "y": -31.1758692},
    {"x": 2.1001482, "y": -31.1758692},
    {"x": 2.1001482, "y": -32.4758412},
    {"x": 2.4001476, "y": -32.4758412},
    {"x": 2.7001724, "y": -32.4758412},
]


def pad(points: list[dict], pad_id: str = "pcb_smtpad_10") -> dict:
    return {
        "type": "pcb_smtpad",
        "pcb_smtpad_id": pad_id,
        "layer": "top",
        "shape": "polygon",
        "points": points,
        "port_hints": ["pin15"],
    }


def via(x: float, y: float, via_id: str = "pcb_via_78") -> dict:
    return {
        "type": "pcb_via",
        "pcb_via_id": via_id,
        "x": x,
        "y": y,
        "hole_diameter": 0.3,
        "outer_diameter": 0.6,
        "layers": ["top", "bottom"],
    }


def via_in_pad_error(vx: float, vy: float, px: float, py: float) -> dict:
    return {
        "type": "pcb_placement_error",
        "pcb_placement_error_id": "pcb_placement_error_0",
        "error_type": "pcb_placement_error",
        "message": (
            f"Via at ({vx:.2f}mm, {vy:.2f}mm) is inside SMD pad J1.B4A9 "
            f"at ({px:.2f}mm, {py:.2f}mm)"
        ),
    }


def write(tmp_path: Path, elements: list) -> Path:
    path = tmp_path / "circuit.json"
    path.write_text(json.dumps(elements, indent=2), encoding="utf-8")
    return path


class ClosedRings(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_tmp_circuit_normalize"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_closed_ring_is_opened_and_the_polygon_is_unchanged(self):
        path = write(self.tmp, [pad(CLOSED_PAD_POINTS)])
        result = normalize_circuit_json(path)
        self.assertEqual(result.rings_opened, 1)
        self.assertEqual(result.vertices_dropped, 1)
        points = json.loads(path.read_text())[0]["points"]
        self.assertEqual(len(points), 6)
        self.assertNotEqual(points[0], points[-1])
        # The shape is the same shape: same extent, same vertices in order.
        self.assertEqual(points, CLOSED_PAD_POINTS[:-1])

    def test_an_open_ring_is_left_byte_for_byte_alone(self):
        """No defect, no rewrite — the byte conservatism the canonicaliser
        works for has to survive this stage on every clean board."""
        path = write(self.tmp, [pad(CLOSED_PAD_POINTS[:-1]), via(2.401, -30.875)])
        before = path.read_bytes()
        result = normalize_circuit_json(path)
        self.assertEqual(result.rings_opened, 0)
        self.assertFalse(result.changed)
        self.assertEqual(path.read_bytes(), before)

    def test_running_twice_changes_nothing_the_second_time(self):
        path = write(self.tmp, [pad(CLOSED_PAD_POINTS)])
        normalize_circuit_json(path)
        after_first = path.read_bytes()
        second = normalize_circuit_json(path)
        self.assertEqual(second.rings_opened, 0)
        self.assertEqual(path.read_bytes(), after_first)

    def test_a_pad_that_would_collapse_below_a_triangle_is_left_alone(self):
        """A pad we cannot repair is a pad we do not touch."""
        degenerate = [
            {"x": 1.0, "y": 1.0},
            {"x": 1.0, "y": 1.0},
            {"x": 1.0, "y": 1.0},
        ]
        path = write(self.tmp, [pad(degenerate)])
        before = path.read_bytes()
        result = normalize_circuit_json(path)
        self.assertEqual(result.rings_opened, 0)
        self.assertEqual(path.read_bytes(), before)


class StaleViaInPadDiagnostics(unittest.TestCase):
    """The repair invalidates diagnostics computed before it. Those, and only
    those, get dropped."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_tmp_circuit_normalize_2"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_false_claim_hydrate_coaster_shipped_is_dropped(self):
        """The exact numbers off the 2026-08-15 build: the via center sits
        0.3009mm above the pad's top edge, on the outside."""
        path = write(self.tmp, [
            pad(CLOSED_PAD_POINTS),
            via(2.401, -30.875),
            via_in_pad_error(2.40, -30.88, 2.40, -31.83),
        ])
        result = normalize_circuit_json(path)
        self.assertEqual(result.rings_opened, 1)
        self.assertEqual(result.stale_via_in_pad_dropped, 1)
        kinds = [e["type"] for e in json.loads(path.read_text())]
        self.assertNotIn("pcb_placement_error", kinds)

    def test_a_via_genuinely_inside_the_pad_keeps_its_error(self):
        """The near miss. Same repair, same message shape — but the via center
        is inside the pad, so the finding is real and must survive."""
        path = write(self.tmp, [
            pad(CLOSED_PAD_POINTS),
            via(2.40, -31.83),
            via_in_pad_error(2.40, -31.83, 2.40, -31.83),
        ])
        result = normalize_circuit_json(path)
        self.assertEqual(result.rings_opened, 1)
        self.assertEqual(result.stale_via_in_pad_dropped, 0)
        kinds = [e["type"] for e in json.loads(path.read_text())]
        self.assertIn("pcb_placement_error", kinds)

    def test_a_placement_error_that_is_not_about_a_via_is_never_touched(self):
        """`pcb_placement_error` also carries "this part hangs off the board".
        Nothing here may reason about a message it does not understand."""
        off_board = {
            "type": "pcb_placement_error",
            "pcb_placement_error_id": "pcb_placement_error_1",
            "error_type": "pcb_placement_error",
            "message": "Component U1 is outside the board outline",
        }
        path = write(self.tmp, [pad(CLOSED_PAD_POINTS), via(2.401, -30.875),
                                off_board])
        result = normalize_circuit_json(path)
        self.assertEqual(result.stale_via_in_pad_dropped, 0)
        kinds = [e["type"] for e in json.loads(path.read_text())]
        self.assertIn("pcb_placement_error", kinds)

    def test_nothing_is_dropped_when_no_ring_was_opened(self):
        """A finding we did not invalidate is a finding we do not touch, even
        when the geometry disagrees with it. That call belongs to the check."""
        path = write(self.tmp, [
            pad(CLOSED_PAD_POINTS[:-1]),
            via(2.401, -30.875),
            via_in_pad_error(2.40, -30.88, 2.40, -31.83),
        ])
        result = normalize_circuit_json(path)
        self.assertEqual(result.stale_via_in_pad_dropped, 0)
        kinds = [e["type"] for e in json.loads(path.read_text())]
        self.assertIn("pcb_placement_error", kinds)


class TheUpstreamCheckItself(unittest.TestCase):
    """The mechanism, not our model of it. Runs @tscircuit/checks for real."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_tmp_circuit_normalize_3"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_checks(self, elements: list) -> list[dict]:
        node = shutil.which("node")
        modules = REPO / "toolchain" / "node_modules"
        helper = (Path(circuit_normalize.__file__).parent / "_js"
                  / "run_all_checks.cjs")
        if node is None or not (modules / "@tscircuit" / "checks").is_dir():
            self.skipTest("toolchain not installed")
        path = write(self.tmp, elements)
        out = subprocess.run(
            [node, str(helper), str(path)],
            capture_output=True, text=True, timeout=120,
            env={"NODE_PATH": str(modules), "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_the_closed_ring_is_what_makes_the_check_fire(self):
        board = {"type": "pcb_board", "pcb_board_id": "pcb_board_0",
                 "width": 20, "height": 20, "center": {"x": 2.4, "y": -31.5},
                 "thickness": 1.6}
        closed = [board, pad(CLOSED_PAD_POINTS), via(2.401, -30.875)]
        opened = [board, pad(CLOSED_PAD_POINTS[:-1]), via(2.401, -30.875)]

        fired = [f for f in self._run_checks(closed)
                 if f.get("type") == "pcb_placement_error"]
        self.assertEqual(len(fired), 1, fired)
        self.assertIn("is inside SMD pad", fired[0]["message"])

        clean = [f for f in self._run_checks(opened)
                 if f.get("type") == "pcb_placement_error"]
        self.assertEqual(clean, [])


# ---------------------------------------------------------------------------
# One drilled hole, written once per spanning-tree branch
# ---------------------------------------------------------------------------

#: The junction weather-badge-19 wrote four times: same point, same diameters,
#: same net, four `pcb_trace_id`s — `mst10`, `mst12`, `mst14`, `mst36`. A
#: spanning-tree branch drops its own via where it changes layer, so a junction
#: several branches share is emitted several times. KiCad reads that as
#: `holes_co_located`; a fab reads it as drilling the same hole twice.
GND = "unnamedsubcircuitsubcircuit_source_group_16_connectivity_net1"
SCL = "unnamedsubcircuitsubcircuit_source_group_16_connectivity_net8"


def routed_via(x, y, via_id, trace_id, net=GND, layers=("bottom", "top")):
    return {
        "type": "pcb_via",
        "pcb_via_id": via_id,
        "x": x,
        "y": y,
        "outer_diameter": 0.6,
        "hole_diameter": 0.3,
        "layers": list(layers),
        "from_layer": layers[0],
        "to_layer": layers[1],
        "pcb_trace_id": trace_id,
        "subcircuit_connectivity_map_key": net,
    }


class DuplicateVias(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_tmp_dup_vias"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, elements):
        path = write(self.tmp, elements)
        result = normalize_circuit_json(path)
        return result, json.loads(path.read_text(encoding="utf-8"))

    def test_four_branches_landing_on_one_junction_leave_one_via(self):
        X, Y = -18.366666007623476, 4.840000999999999
        result, kept = self._run([
            routed_via(X, Y, "pcb_via_78", "source_net_1_mst10_0"),
            routed_via(X, Y, "pcb_via_80", "source_net_1_mst12_0"),
            routed_via(X, Y, "pcb_via_83", "source_net_1_mst14_0",
                       layers=("top", "bottom")),
            routed_via(X, Y, "pcb_via_99", "source_net_1_mst36_0",
                       layers=("top", "bottom")),
        ])

        vias = [e for e in kept if e["type"] == "pcb_via"]
        self.assertEqual(len(vias), 1, vias)
        self.assertEqual(result.vias_deduped, 3)
        self.assertEqual(result.vias_before, 4)

    def test_the_layer_pair_is_compared_as_a_set(self):
        """Two of the four duplicates measured carry `["bottom","top"]` and two
        carry `["top","bottom"]`. Compared in order, half of them survive."""
        result, kept = self._run([
            routed_via(1.0, 2.0, "a", "n_mst0_0", layers=("bottom", "top")),
            routed_via(1.0, 2.0, "b", "n_mst1_0", layers=("top", "bottom")),
        ])

        self.assertEqual(len([e for e in kept if e["type"] == "pcb_via"]), 1)
        self.assertEqual(result.vias_deduped, 1)

    def test_two_nets_meeting_at_one_point_are_never_merged(self):
        """The failure that would ship copper. `connected_source_net_id` is
        null on every via the router writes, so a key built on it would read
        two nets as one and short them."""
        result, kept = self._run([
            routed_via(5.0, 5.0, "a", "source_net_1_mst0_0", net=GND),
            routed_via(5.0, 5.0, "b", "source_net_8_mst0_0", net=SCL),
        ])

        self.assertEqual(len([e for e in kept if e["type"] == "pcb_via"]), 2)
        self.assertEqual(result.vias_deduped, 0)

    def test_a_via_with_no_net_is_left_alone(self):
        """Cannot tell, so do not merge."""
        one = routed_via(5.0, 5.0, "a", "t0")
        two = routed_via(5.0, 5.0, "b", "t1")
        del one["subcircuit_connectivity_map_key"]
        del two["subcircuit_connectivity_map_key"]

        result, kept = self._run([one, two])

        self.assertEqual(len([e for e in kept if e["type"] == "pcb_via"]), 2)
        self.assertEqual(result.vias_deduped, 0)

    def test_vias_a_hole_apart_are_two_holes(self):
        result, kept = self._run([
            routed_via(5.0, 5.0, "a", "n_mst0_0"),
            routed_via(5.6, 5.0, "b", "n_mst1_0"),
        ])

        self.assertEqual(len([e for e in kept if e["type"] == "pcb_via"]), 2)
        self.assertEqual(result.vias_deduped, 0)

    def test_a_board_with_no_duplicates_is_not_rewritten(self):
        elements = [routed_via(1.0, 1.0, "a", "n_mst0_0")]
        path = write(self.tmp, elements)
        before = path.read_text(encoding="utf-8")

        result = normalize_circuit_json(path)

        self.assertFalse(result.changed)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_deduping_survives_a_board_with_no_closed_rings(self):
        """The guard this pass had to move. `normalize_circuit_json` used to
        return the moment no polygon ring was opened, so a board with duplicate
        vias and no closed ring was never rewritten at all."""
        result, kept = self._run([
            {"type": "pcb_board", "pcb_board_id": "b0", "width": 20,
             "height": 20, "center": {"x": 0, "y": 0}, "thickness": 1.6},
            routed_via(1.0, 1.0, "a", "n_mst0_0"),
            routed_via(1.0, 1.0, "b", "n_mst1_0"),
        ])

        self.assertEqual(result.rings_opened, 0)
        self.assertEqual(result.vias_deduped, 1)
        self.assertEqual(len([e for e in kept if e["type"] == "pcb_via"]), 1)

    def test_it_is_idempotent(self):
        path = write(self.tmp, [
            routed_via(1.0, 1.0, "a", "n_mst0_0"),
            routed_via(1.0, 1.0, "b", "n_mst1_0"),
        ])
        normalize_circuit_json(path)
        once = path.read_text(encoding="utf-8")

        again = normalize_circuit_json(path)

        self.assertEqual(again.vias_deduped, 0)
        self.assertEqual(path.read_text(encoding="utf-8"), once)

    def test_the_first_record_wins_so_the_pass_is_deterministic(self):
        _, kept = self._run([
            routed_via(1.0, 1.0, "pcb_via_78", "source_net_1_mst10_0"),
            routed_via(1.0, 1.0, "pcb_via_80", "source_net_1_mst12_0"),
        ])

        (survivor,) = [e for e in kept if e["type"] == "pcb_via"]
        self.assertEqual(survivor["pcb_via_id"], "pcb_via_78")

    def test_nothing_but_vias_is_touched(self):
        board = {"type": "pcb_board", "pcb_board_id": "b0", "width": 20,
                 "height": 20, "center": {"x": 0, "y": 0}, "thickness": 1.6}
        trace = {"type": "pcb_trace", "pcb_trace_id": "n_mst0_0", "route": []}
        _, kept = self._run([
            board, trace,
            routed_via(1.0, 1.0, "a", "n_mst0_0"),
            routed_via(1.0, 1.0, "b", "n_mst1_0"),
        ])

        self.assertEqual([e["type"] for e in kept],
                         ["pcb_board", "pcb_trace", "pcb_via"])


if __name__ == "__main__":
    unittest.main()
