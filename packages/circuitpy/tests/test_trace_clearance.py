"""The trace clearance repair: does it open the gap, and does it stay honest?

The pass moves routed copper. Everything worth testing is about what it must
*not* do while doing that: never move a port anchor (that disconnects a net),
never move a via, never push copper off its own net, never invent a shortfall
against a shape it modelled wrong, and never report success on a board it left
alone. The one test about the happy path is the cheap half.

The phantom test is the one that caught a real defect: `rotated_pill` read as a
rectangle reported a 0.0192mm gap on weather-badge-32 where KiCad measures
nothing under 0.1000mm, and the pass moved eleven points to close a gap that
was never there.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import trace_clearance  # noqa: E402
from circuitpy.fab import PROFILES  # noqa: E402

PROFILE = PROFILES["jlcpcb"]
REQUIRED = PROFILE.warn_clearance_mm


def _board(*, width: float = 20.0, height: float = 20.0) -> list[dict]:
    return [{"type": "pcb_board", "width": width, "height": height,
             "center": {"x": 0, "y": 0}, "num_layers": 2, "thickness": 1.6}]


def _net(net_id: str, key: str) -> dict:
    return {"type": "source_net", "source_net_id": net_id,
            "subcircuit_connectivity_map_key": key}


def _port(port_id: str, source_port_id: str) -> dict:
    return {"type": "pcb_port", "pcb_port_id": port_id,
            "source_port_id": source_port_id}


def _source_port(source_port_id: str, key: str) -> dict:
    """The connectivity key lives on the *source* port; a `pcb_port` carries
    only the join to it (`diffpair._Board._port_net_map`)."""
    return {"type": "source_port", "source_port_id": source_port_id,
            "subcircuit_connectivity_map_key": key}


def _pad(pad_id: str, x: float, y: float, *, port: str | None = None,
         shape: str = "rect", width: float = 1.0, height: float = 1.0,
         layer: str = "top") -> dict:
    out = {"type": "pcb_smtpad", "pcb_smtpad_id": pad_id, "layer": layer,
           "shape": shape, "x": x, "y": y, "width": width, "height": height}
    if port:
        out["pcb_port_id"] = port
    return out


def _trace(trace_id: str, points: list[dict], *,
           connects: list[str] | None = None) -> dict:
    out = {"type": "pcb_trace", "pcb_trace_id": trace_id, "route": points}
    if connects:
        out["connectsTo"] = connects
    return out


def _wire(x: float, y: float, *, layer: str = "top", width: float = 0.2,
          **extra) -> dict:
    return {"route_type": "wire", "x": x, "y": y, "layer": layer,
            "width": width, **extra}


def _run(elements: list[dict], **kwargs):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "main.circuit.json"
        path.write_text(json.dumps(elements), encoding="utf-8")
        result = trace_clearance.relieve_trace_clearance(path, PROFILE, **kwargs)
        after = json.loads(path.read_text(encoding="utf-8"))
        raw = path.read_text(encoding="utf-8")
    return result, after, raw


def _route_of(elements: list[dict], trace_id: str) -> list[dict]:
    # A `pcb_via` carries `pcb_trace_id` too — that join is the whole point of
    # the via-ownership test below — so match the type as well.
    return next(e for e in elements
                if e.get("type") == "pcb_trace"
                and e.get("pcb_trace_id") == trace_id)["route"]


class TraceClearanceTest(unittest.TestCase):

    def test_opens_a_gap_that_is_too_tight(self):
        """A track running 0.115mm past a foreign pad is pushed to the margin."""
        # Pad spans y in [-0.5, 0.5]; a 0.2mm-wide track at y = 0.715 leaves
        # 0.715 - 0.5 - 0.1 = 0.115mm, the value the shipped autorouter lands.
        elements = _board() + [
            _net("net_a", "KEY_A"), _net("net_b", "KEY_B"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            _port("pp_a", "sp_a"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            _trace("t_b", [
                _wire(-5.0, 0.715), _wire(0.0, 0.715), _wire(5.0, 0.715),
            ]),
        ]
        result, after, _ = _run(elements)
        self.assertTrue(result.ran)
        self.assertTrue(result.changed)
        route = _route_of(after, "t_b")
        moved = route[1]["y"]
        self.assertGreater(moved, 0.715, "the point should move away from the pad")
        self.assertGreaterEqual(
            round(moved - 0.5 - 0.1, 6), REQUIRED,
            "the gap should reach the design margin",
        )

    def test_leaves_a_board_with_room_byte_identical(self):
        """Nothing to repair means nothing written, and nothing claimed."""
        elements = _board() + [
            _net("net_a", "KEY_A"), _net("net_b", "KEY_B"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            _port("pp_a", "sp_a"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            _trace("t_b", [_wire(-5.0, 3.0), _wire(5.0, 3.0)]),
        ]
        before = json.dumps(elements)
        result, after, raw = _run(elements)
        self.assertTrue(result.ran)
        self.assertFalse(result.changed)
        self.assertEqual(result.findings(), [])
        self.assertEqual(json.loads(before), after)
        self.assertEqual(raw, before, "an untouched board must not be rewritten")

    def test_never_moves_a_port_anchor(self):
        """The point where copper meets a pad stays put, whatever it costs."""
        elements = _board() + [
            _net("net_a", "KEY_A"), _net("net_b", "KEY_B"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            {"type": "source_port", "source_port_id": "sp_b",
             "subcircuit_connectivity_map_key": "KEY_B"},
            _port("pp_a", "sp_a"), _port("pp_b", "sp_b"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            _pad("pad_b", 0.0, 0.715, port="pp_b"),
            # Both ends pinned to pads: a pad-to-pad hop with no interior point.
            _trace("t_b", [
                _wire(0.0, 0.715, start_pcb_port_id="pp_b"),
                _wire(4.0, 0.715, end_pcb_port_id="pp_b"),
            ], connects=["pp_b"]),
        ]
        result, after, _ = _run(elements)
        route = _route_of(after, "t_b")
        self.assertEqual(route[0]["y"], 0.715)
        self.assertEqual(route[1]["y"], 0.715)
        self.assertFalse(result.changed)
        # And it says so rather than reporting a clean board.
        kinds = {f["kind"] for f in result.findings()}
        self.assertIn("trace_clearance_unrelieved", kinds)

    def test_never_moves_a_via_point(self):
        """A `route_type: "via"` point is the via. Placement, not routing."""
        elements = _board() + [
            _net("net_a", "KEY_A"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            _port("pp_a", "sp_a"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            _trace("t_b", [
                _wire(-4.0, 0.715),
                {"route_type": "via", "x": 0.0, "y": 0.715,
                 "from_layer": "top", "to_layer": "bottom",
                 "via_diameter": 0.6, "via_hole_diameter": 0.3},
                _wire(4.0, 0.715, layer="bottom"),
            ]),
        ]
        _, after, _ = _run(elements)
        via_point = _route_of(after, "t_b")[1]
        self.assertEqual((via_point["x"], via_point["y"]), (0.0, 0.715))

    def test_does_not_push_copper_off_its_own_via(self):
        """The via a trace placed is that trace's own copper.

        Reading it as foreign is what made the pour pass push the ground plane
        off its own ground for three boards running (2026-09-02). A via carries
        `pcb_trace_id`, and on weather-badge-32 all 112 of them carry a
        connectivity key their traces do not, so the key alone never matches.
        """
        elements = _board() + [
            _net("net_a", "KEY_A"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            _port("pp_a", "sp_a"),
            _pad("pad_a", -4.0, 0.0, port="pp_a"),
            {"type": "pcb_via", "pcb_via_id": "via_1",
             "pcb_trace_id": "t_a", "x": 0.0, "y": 0.0,
             "outer_diameter": 0.6, "hole_diameter": 0.3,
             "layers": ["top", "bottom"],
             "subcircuit_connectivity_map_key": "SOMETHING_ELSE"},
            _trace("t_a", [
                _wire(-4.0, 0.0, start_pcb_port_id="pp_a"),
                _wire(-2.0, 0.0),
                _wire(0.0, 0.0),
            ], connects=["pp_a"]),
        ]
        result, after, _ = _run(elements)
        self.assertFalse(
            result.changed,
            "a trace running into its own via is not a clearance defect",
        )
        self.assertEqual(_route_of(after, "t_a")[1]["y"], 0.0)

    def test_does_not_invent_a_gap_against_a_rounded_pad(self):
        """`rotated_pill` is a stadium, not its bounding rectangle.

        Measured on weather-badge-32: read as a rectangle, `pcb_smtpad_94`
        reported a 0.0192mm gap against a track KiCad measures well clear of.
        """
        # A 2.0 x 0.6 pill: the round end is centred at x = 0.7, radius 0.3.
        # A track at (1.3, 0.55) sits 0.6 from that centre -> 0.3 of pad edge,
        # minus the track's 0.1 half-width = 0.2mm clear. Its bounding
        # rectangle, though, reaches y = 0.3 at x = 1.3 and would read 0.15.
        elements = _board() + [
            _net("net_a", "KEY_A"), _net("net_b", "KEY_B"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            _port("pp_a", "sp_a"),
            _pad("pad_a", 0.0, 0.0, port="pp_a", shape="rotated_pill",
                 width=2.0, height=0.6),
            _trace("t_b", [_wire(1.0, 0.55), _wire(1.3, 0.55), _wire(1.6, 0.55)]),
        ]
        result, _, _ = _run(elements)
        self.assertFalse(
            result.changed,
            "a stadium pad must not be measured as its bounding rectangle",
        )

    def test_same_net_copper_is_left_alone(self):
        """Touching your own net is what a net is."""
        elements = _board() + [
            _net("net_a", "KEY_A"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            {"type": "source_port", "source_port_id": "sp_a2",
             "subcircuit_connectivity_map_key": "KEY_A"},
            _port("pp_a", "sp_a"), _port("pp_a2", "sp_a2"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            _trace("t_a", [
                _wire(-4.0, 0.715), _wire(0.0, 0.715), _wire(4.0, 0.715),
            ], connects=["pp_a2"]),
        ]
        # Both belong to net_a through their ports.
        elements[-1]["connectsTo"] = ["pp_a"]
        result, _, _ = _run(elements)
        self.assertFalse(result.changed)

    def test_reports_what_it_could_not_fix(self):
        """A repair that fixes most of a defect must name the rest."""
        elements = _board() + [
            _net("net_a", "KEY_A"), _net("net_b", "KEY_B"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            {"type": "source_port", "source_port_id": "sp_b",
             "subcircuit_connectivity_map_key": "KEY_B"},
            _port("pp_a", "sp_a"), _port("pp_b", "sp_b"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            _pad("pad_b", 0.0, 0.715, port="pp_b"),
            _trace("t_b", [
                _wire(0.0, 0.715, start_pcb_port_id="pp_b"),
                _wire(4.0, 0.715, end_pcb_port_id="pp_b"),
            ], connects=["pp_b"]),
        ]
        result, _, _ = _run(elements)
        self.assertGreater(result.unresolved, 0)
        detail = next(f["detail"] for f in result.findings()
                      if f["kind"] == "trace_clearance_unrelieved")
        self.assertIn("nowhere to go", detail)

    def test_unreadable_input_never_raises(self):
        """Advisory by construction: a repair that dies costs a repair."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.circuit.json"
            result = trace_clearance.relieve_trace_clearance(path, PROFILE)
        self.assertFalse(result.ran)
        self.assertEqual(result.findings(), [])
        self.assertIsNotNone(result.note)

    def test_elapsed_is_a_duration_not_a_clock_reading(self):
        """`elapsed_s` ships in the sidecar's `build.traceClearance`.

        A local name shadowing the start time made this report 3486 seconds for
        a pass that took one — a number nothing else in the build would have
        contradicted, in an artifact a human reads.
        """
        elements = _board() + [
            _net("net_a", "KEY_A"),
            _source_port("sp_a", "KEY_A"),
            _port("pp_a", "sp_a"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            _trace("t_b", [_wire(-4.0, 0.715), _wire(0.0, 0.715),
                           _wire(4.0, 0.715)]),
        ]
        result, _, _ = _run(elements)
        self.assertGreaterEqual(result.elapsed_s, 0.0)
        self.assertLess(result.elapsed_s, 60.0)

    def test_never_leaves_copper_under_the_fab_floor(self):
        """The guarantee, on the geometry that broke the first version.

        Two traces stepping toward each other inside one sweep is what put a
        0.0959mm gap on weather-badge-27. Whatever the sweep does, nothing may
        end below the floor that did not start there.
        """
        elements = _board() + [
            _net("net_a", "KEY_A"), _net("net_b", "KEY_B"),
            _net("net_c", "KEY_C"),
            _source_port("sp_a", "KEY_A"),
            _port("pp_a", "sp_a"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            # Two foreign tracks in a narrow corridor either side of the pad:
            # each is pushed off the pad, and each push is toward the other.
            _trace("t_b", [_wire(-4.0, 0.715), _wire(0.0, 0.715),
                           _wire(4.0, 0.715)]),
            _trace("t_c", [_wire(-4.0, 1.05), _wire(0.0, 1.05),
                           _wire(4.0, 1.05)]),
        ]
        result, after, _ = _run(elements)
        floor = PROFILE.min_clearance_mm
        b_y = _route_of(after, "t_b")[1]["y"]
        c_y = _route_of(after, "t_c")[1]["y"]
        gap = abs(c_y - b_y) - 0.1 - 0.1  # both tracks are 0.2mm wide
        self.assertGreaterEqual(
            round(gap, 6), floor,
            f"the pass closed two tracks to {gap:.4f}mm, under the "
            f"{floor:g}mm fab floor",
        )
        self.assertTrue(result.ran)

    def test_sees_a_polygon_pad_that_has_no_centre(self):
        """A polygon pad carries an outline and no `x`/`y`.

        Requiring a centre before reading the shape dropped all four of
        weather-badge-27's, and the pass pushed a V5 track into J1's GND pad it
        could not see — 0.0932mm, under the floor, on a board whose narrowest
        was 0.1000mm.
        """
        # A 1 x 1 square pad expressed as an outline, centred on the origin.
        elements = _board() + [
            _net("net_a", "KEY_A"), _net("net_b", "KEY_B"),
            _source_port("sp_a", "KEY_A"),
            _port("pp_a", "sp_a"),
            {"type": "pcb_smtpad", "pcb_smtpad_id": "pad_poly",
             "layer": "top", "shape": "polygon", "pcb_port_id": "pp_a",
             "points": [{"x": -0.5, "y": -0.5}, {"x": 0.5, "y": -0.5},
                        {"x": 0.5, "y": 0.5}, {"x": -0.5, "y": 0.5}]},
            _trace("t_b", [
                _wire(-5.0, 0.715), _wire(0.0, 0.715), _wire(5.0, 0.715),
            ]),
        ]
        result, after, _ = _run(elements)
        self.assertTrue(
            result.changed,
            "a polygon pad is copper and must be measured against",
        )
        moved = _route_of(after, "t_b")[1]["y"]
        self.assertGreaterEqual(round(moved - 0.5 - 0.1, 6), REQUIRED)

    def test_required_margin_comes_from_the_profile(self):
        """Never transcribed; imported. The pass and `clearance_no_margin`
        have to be answering the same question."""
        elements = _board() + [
            _net("net_a", "KEY_A"),
            {"type": "source_port", "source_port_id": "sp_a",
             "subcircuit_connectivity_map_key": "KEY_A"},
            _port("pp_a", "sp_a"),
            _pad("pad_a", 0.0, 0.0, port="pp_a"),
            _trace("t_b", [_wire(-4.0, 0.715), _wire(0.0, 0.715),
                           _wire(4.0, 0.715)]),
        ]
        result, _, _ = _run(elements)
        self.assertEqual(result.required_mm, PROFILE.warn_clearance_mm)


if __name__ == "__main__":
    unittest.main()
