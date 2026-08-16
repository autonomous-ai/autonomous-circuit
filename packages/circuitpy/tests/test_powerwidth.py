"""The power-widening pass: does it take the copper that is there to take?

The EE review (2026-08-15, finding 4) asked for the 5V and 3V3 rails to stop
being 0.2mm — the same width as a button signal — and to become 0.5-1.0mm.
`circuitpy.powerwidth` takes the gap the autorouter already left, segment by
segment, and never moves a trace.

The assertions that matter are the ones about restraint. Anything that writes
copper can break a board, so every test below pins a limit as hard as it pins a
gain: a rail with an obstacle beside it stops at the obstacle, a rail with a
neighbour that is also about to grow leaves room for it, and a pass whose
output fails the gate puts the original file back byte for byte.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import powerwidth  # noqa: E402
from circuitpy.fab import PROFILES  # noqa: E402

PROFILE = PROFILES["jlcpcb"]
TARGET = PROFILE.warn_power_trace_mm


# ---------------------------------------------------------------------------
# The smallest board that can express the question: one rail across an open
# field, with whatever obstacle a test wants to put beside it.
# ---------------------------------------------------------------------------


def _board(
    *,
    net: str = "V3_3",
    width: float = 0.2,
    y: float = 0.0,
    extra: list[dict] | None = None,
    poured: bool = False,
) -> list[dict]:
    elements: list[dict] = [
        {"type": "pcb_board", "width": 40, "height": 40, "num_layers": 2,
         "center": {"x": 0, "y": 0}},
        {"type": "source_net", "source_net_id": "net_rail", "name": net,
         "subcircuit_connectivity_map_key": "k_rail"},
        {"type": "source_trace", "source_trace_id": "st_rail",
         "connected_source_net_ids": ["net_rail"]},
        {
            "type": "pcb_trace",
            "pcb_trace_id": "pcb_trace_rail",
            "source_trace_id": "st_rail",
            "subcircuit_connectivity_map_key": "k_rail",
            "route": [
                {"route_type": "wire", "x": -10, "y": y, "width": width,
                 "layer": "top"},
                {"route_type": "wire", "x": 10, "y": y, "width": width,
                 "layer": "top"},
            ],
        },
    ]
    if poured:
        elements.append({
            "type": "pcb_copper_pour", "pcb_copper_pour_id": "pour_0",
            "source_net_id": "net_rail", "layer": "bottom",
        })
    elements.extend(extra or [])
    return elements


def _write(tmp: Path, elements: list[dict]) -> Path:
    path = tmp / "circuit.json"
    path.write_text(json.dumps(elements), encoding="utf-8")
    return path


def _widths(path: Path) -> list[float]:
    elements = json.loads(path.read_text(encoding="utf-8"))
    for element in elements:
        if element.get("type") == "pcb_trace":
            return [p["width"] for p in element["route"]
                    if p.get("route_type") == "wire"]
    return []


def _error(kind: str) -> dict:
    return {"severity": "error", "kind": kind, "detail": kind}


def _breaks_when_widened(elements, _at) -> list[dict]:
    """A gate that only complains about the *new* copper — which is the only
    kind of gate that can tell a regression from a board that was already
    broken."""
    for element in elements:
        if element.get("type") != "pcb_trace":
            continue
        for point in element.get("route") or []:
            if (point.get("width") or 0) > 0.2 + 1e-9:
                return [_error("caused_by_widening")]
    return []


class PowerWidthTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- the gain ---------------------------------------------------------

    def test_an_open_rail_reaches_the_power_floor(self) -> None:
        path = _write(self.tmp, _board())
        result = powerwidth.widen_power_traces(path, PROFILE)
        self.assertTrue(result.kept, result.refusal)
        self.assertEqual(set(_widths(path)), {TARGET})
        self.assertEqual(result.nets[0].name, "V3_3")
        self.assertAlmostEqual(result.nets[0].length_at_target, 1.0)

    def test_it_stops_at_an_obstacle_rather_than_running_over_it(self) -> None:
        """A pad whose near *edge* sits 0.30mm off the centreline leaves
        0.20mm of half-width once the 0.10mm clearance is paid, so the rail
        may be 0.40mm and no more. The edge is what counts, not the centre."""
        pad = {
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_near", "shape": "rect",
            "layer": "top", "x": 0, "y": 0.35, "width": 40, "height": 0.10,
            "pcb_port_id": "other",
        }
        path = _write(self.tmp, _board(extra=[pad]))
        powerwidth.widen_power_traces(path, PROFILE)
        self.assertEqual(max(_widths(path)), 0.40)

    def test_a_rail_with_no_room_at_all_is_left_exactly_as_it_was(self) -> None:
        before = json.dumps(_board(extra=[{
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_tight", "shape": "rect",
            "layer": "top", "x": 0, "y": 0.201, "width": 20, "height": 0.001,
            "pcb_port_id": "other",
        }]))
        path = self.tmp / "circuit.json"
        path.write_text(before, encoding="utf-8")
        result = powerwidth.widen_power_traces(path, PROFILE)
        self.assertFalse(result.kept)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    # -- which nets count -------------------------------------------------

    def test_a_signal_net_is_not_touched(self) -> None:
        path = _write(self.tmp, _board(net="COL3"))
        result = powerwidth.widen_power_traces(path, PROFILE)
        self.assertFalse(result.ran)
        self.assertEqual(_widths(path), [0.2, 0.2])

    def test_ground_counts_as_a_rail(self) -> None:
        path = _write(self.tmp, _board(net="GND"))
        self.assertTrue(powerwidth.widen_power_traces(path, PROFILE).kept)

    def test_a_poured_net_is_exempt_because_the_pour_carries_it(self) -> None:
        """Matches `checks.power_width_warnings`: where ground is poured, the
        plane is the current path and the stubs are not what carries the rail.
        Widening them would only take routing space from everything else."""
        path = _write(self.tmp, _board(net="GND", poured=True))
        result = powerwidth.widen_power_traces(path, PROFILE)
        self.assertFalse(result.ran)
        self.assertEqual(_widths(path), [0.2, 0.2])

    # -- restraint --------------------------------------------------------

    def test_same_net_copper_never_limits_its_own_rail(self) -> None:
        """A wide trace arriving at its own pad is a connection, not a
        violation — the classic way a naive widener refuses to do anything."""
        own_pad = {
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_own", "shape": "rect",
            "layer": "top", "x": 10, "y": 0, "width": 0.3, "height": 0.3,
            "pcb_port_id": "pcb_port_own",
        }
        ports = [
            {"type": "source_port", "source_port_id": "sp_own",
             "subcircuit_connectivity_map_key": "k_rail"},
            {"type": "pcb_port", "pcb_port_id": "pcb_port_own",
             "source_port_id": "sp_own", "x": 10, "y": 0, "layers": ["top"]},
        ]
        path = _write(self.tmp, _board(extra=[own_pad, *ports]))
        powerwidth.widen_power_traces(path, PROFILE)
        self.assertEqual(max(_widths(path)), TARGET)

    def test_two_rails_side_by_side_do_not_both_claim_the_same_gap(self) -> None:
        """Each would otherwise measure the other at the width it has *now*,
        both take the gap, and both be right about a board that stops existing
        the moment the other one grows."""
        second = [
            {"type": "source_net", "source_net_id": "net_b", "name": "V5",
             "subcircuit_connectivity_map_key": "k_b"},
            {"type": "source_trace", "source_trace_id": "st_b",
             "connected_source_net_ids": ["net_b"]},
            {
                "type": "pcb_trace", "pcb_trace_id": "pcb_trace_b",
                "source_trace_id": "st_b",
                "subcircuit_connectivity_map_key": "k_b",
                "route": [
                    {"route_type": "wire", "x": -10, "y": 0.7, "width": 0.2,
                     "layer": "top"},
                    {"route_type": "wire", "x": 10, "y": 0.7, "width": 0.2,
                     "layer": "top"},
                ],
            },
        ]
        path = _write(self.tmp, _board(extra=second))
        powerwidth.widen_power_traces(path, PROFILE)
        elements = json.loads(path.read_text(encoding="utf-8"))
        widths = {
            e["pcb_trace_id"]: e["route"][0]["width"]
            for e in elements if e.get("type") == "pcb_trace"
        }
        # 0.70mm apart, 0.10mm clearance: the two half-widths share 0.60mm.
        gap = 0.70 - widths["pcb_trace_rail"] / 2 - widths["pcb_trace_b"] / 2
        self.assertGreaterEqual(round(gap, 6), PROFILE.min_clearance_mm)

    def test_it_never_narrows_copper_that_is_already_there(self) -> None:
        """The bug this pins, caught on the real boards 2026-08-16: splitting a
        segment let a piece take the *computed* allowance even when that was
        below the width the router had already laid, and GND came back at
        0.05mm — under the 0.1mm fab minimum, i.e. a scrap board. The router's
        copper was legal where it stood; a rule this pass measures lower is a
        rule the existing copper is exempt from, not a reason to cut it back."""
        pinch = {
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_tight", "shape": "rect",
            "layer": "top", "x": 0, "y": 0.12, "width": 2.0, "height": 0.001,
            "pcb_port_id": "other",
        }
        path = _write(self.tmp, _board(width=0.3, extra=[pinch]))
        powerwidth.widen_power_traces(path, PROFILE)
        self.assertGreaterEqual(min(_widths(path)), 0.3)

    def test_it_never_moves_copper_only_widens_it(self) -> None:
        """The whole premise: this pass changes widths, never geometry. The bug
        this pins shipped a rail **-0.275mm** from another net on
        hydrate-coaster — an actual short. Splitting a segment adds points, and
        the step that merges the redundant ones back identified them by object
        identity (`id(p) in {id(q) for q in run}`) — but every emitted point is
        a *copy*, so that test was False for all of them and real corners were
        dropped. Two segments meeting at a corner became one straight segment
        across it, and the trace cut through what the corner was avoiding.

        Comparing the polyline rather than the point list is what makes this
        test right: the pass is *allowed* to add and remove collinear points,
        and is not allowed to change the shape they describe.
        """
        corner = [
            {"route_type": "wire", "x": -10, "y": 0, "width": 0.2, "layer": "top"},
            {"route_type": "wire", "x": 0, "y": 0, "width": 0.2, "layer": "top"},
            {"route_type": "wire", "x": 0, "y": 10, "width": 0.2, "layer": "top"},
        ]
        elements = _board()
        for element in elements:
            if element.get("type") == "pcb_trace":
                element["route"] = corner
        path = _write(self.tmp, elements)
        powerwidth.widen_power_traces(path, PROFILE)

        route = [
            p for e in json.loads(path.read_text(encoding="utf-8"))
            if e.get("type") == "pcb_trace" for p in e["route"]
        ]
        # Every emitted point lies on the original polyline, and both corners
        # survive: a path that skipped (0, 0) would be a different wire.
        self.assertIn((-10.0, 0.0), [(p["x"], p["y"]) for p in route])
        self.assertIn((0.0, 0.0), [(p["x"], p["y"]) for p in route])
        self.assertIn((0.0, 10.0), [(p["x"], p["y"]) for p in route])
        for p in route:
            on_leg_a = p["y"] == 0 and -10 <= p["x"] <= 0
            on_leg_b = p["x"] == 0 and 0 <= p["y"] <= 10
            self.assertTrue(on_leg_a or on_leg_b, f"{p} is off the original path")

    def test_widths_land_on_the_grid(self) -> None:
        pad = {
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_odd", "shape": "rect",
            "layer": "top", "x": 0, "y": 0.2873, "width": 0.4, "height": 0.001,
            "pcb_port_id": "other",
        }
        path = _write(self.tmp, _board(extra=[pad]))
        powerwidth.widen_power_traces(path, PROFILE)
        for width in _widths(path):
            self.assertAlmostEqual(
                width / powerwidth.WIDTH_GRID_MM,
                round(width / powerwidth.WIDTH_GRID_MM), places=6,
            )

    def test_the_bottom_layer_does_not_limit_a_top_layer_rail(self) -> None:
        pad = {
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_under", "shape": "rect",
            "layer": "bottom", "x": 0, "y": 0.21, "width": 20, "height": 0.001,
            "pcb_port_id": "other",
        }
        path = _write(self.tmp, _board(extra=[pad]))
        powerwidth.widen_power_traces(path, PROFILE)
        self.assertEqual(max(_widths(path)), TARGET)

    # -- graded by the pipeline's gate, not by its own model ---------------

    def test_a_rewrite_the_gate_rejects_is_reverted_byte_for_byte(self) -> None:
        """Ledger lesson E: a repair pass graded by its own model is exactly as
        right as its model."""
        before = json.dumps(_board())
        path = self.tmp / "circuit.json"
        path.write_text(before, encoding="utf-8")
        result = powerwidth.widen_power_traces(
            path, PROFILE, grade=_breaks_when_widened,
        )
        self.assertFalse(result.kept)
        self.assertIn("blocking", result.refusal or "")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_gate_that_raises_is_a_refusal_not_a_pass(self) -> None:
        before = json.dumps(_board())
        path = self.tmp / "circuit.json"
        path.write_text(before, encoding="utf-8")

        def explode(elements, at):
            raise RuntimeError("kaboom")

        result = powerwidth.widen_power_traces(path, PROFILE, grade=explode)
        self.assertFalse(result.kept)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_gate_that_was_already_failing_does_not_block_the_widening(self) -> None:
        """The board's own pre-existing errors are not this pass's fault, and
        holding it to zero would mean no board ever improves twice. The bar is
        *no new* blocking finding, not *no* blocking finding."""
        path = _write(self.tmp, _board())
        result = powerwidth.widen_power_traces(
            path, PROFILE, grade=lambda elements, at: [_error("pre_existing")],
        )
        self.assertTrue(result.kept, result.refusal)
        self.assertEqual(result.blocking_before, 1)
        self.assertEqual(result.blocking_after, 1)
        self.assertEqual(max(_widths(path)), TARGET)

    # -- it says what it did ----------------------------------------------

    def test_it_reports_the_fraction_of_the_run_at_target_not_only_the_minimum(self) -> None:
        """`dfm_power_trace_width` reports the narrowest point. A rail that is
        0.5mm for most of its length and necks past one via is a different
        board from one that is 0.2mm throughout, and only this number says so."""
        pinch = {
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_pinch", "shape": "rect",
            "layer": "top", "x": 9.5, "y": 0.21, "width": 1.0, "height": 0.001,
            "pcb_port_id": "other",
        }
        elements = _board(extra=[pinch])
        for element in elements:
            if element.get("type") == "pcb_trace":
                element["route"] = [
                    {"route_type": "wire", "x": -10, "y": 0, "width": 0.2,
                     "layer": "top"},
                    {"route_type": "wire", "x": 8, "y": 0, "width": 0.2,
                     "layer": "top"},
                    {"route_type": "wire", "x": 10, "y": 0, "width": 0.2,
                     "layer": "top"},
                ]
        path = _write(self.tmp, elements)
        result = powerwidth.widen_power_traces(path, PROFILE)
        net = result.nets[0]
        self.assertLess(net.after_mm, TARGET)          # the neck is still a neck
        self.assertGreater(net.length_at_target, 0.5)  # most of the run is not
        self.assertTrue(any(
            "of the run now at" in f["detail"] for f in result.findings()
        ))

    def test_the_reported_limiter_is_the_obstacle_that_caused_the_neck(self) -> None:
        """A route point takes the *min* of the two pieces meeting at it, so
        the piece that ends up narrow is as often the neighbour of the tight
        one as it is the tight one itself. Reporting the wrong side is not
        cosmetic: harness-puck's V3_3_LED and V5 both shipped a sidecar saying
        `limiter: null` for a rail that necks to 0.15mm, i.e. "nothing is
        holding this back", about copper that something plainly was."""
        pinch = {
            "type": "pcb_smtpad", "pcb_smtpad_id": "pad_the_culprit",
            "shape": "rect", "layer": "top",
            "x": 0.0, "y": 0.21, "width": 0.4, "height": 0.001,
            "pcb_port_id": "other",
        }
        path = _write(self.tmp, _board(extra=[pinch]))
        result = powerwidth.widen_power_traces(path, PROFILE)
        net = result.nets[0]
        self.assertLess(net.after_mm, TARGET)
        self.assertEqual(net.limiter, "pad_the_culprit")

    def test_an_unreadable_board_is_a_refusal_and_never_an_exception(self) -> None:
        path = self.tmp / "circuit.json"
        path.write_text("{not json", encoding="utf-8")
        result = powerwidth.widen_power_traces(path, PROFILE)
        self.assertFalse(result.ran)
        self.assertEqual(result.findings(), [])


if __name__ == "__main__":
    unittest.main()
