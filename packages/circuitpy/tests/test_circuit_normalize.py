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


if __name__ == "__main__":
    unittest.main()
