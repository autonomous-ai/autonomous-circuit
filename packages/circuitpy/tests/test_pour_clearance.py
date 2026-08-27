"""The pour clearance repair: does it push the plane back, and only that far?

A pour is drawn as polygons and a polygon approximating a circle always errs
inward. `<copperpour>`'s margin props do not reach a via at all, so the cutout
around one comes out at `via_radius + 0.1` however much clearance is asked for,
and the 32-gon takes that to 0.098mm — under the 0.15mm the exported zone
declares about itself, and under JLCPCB's 0.1mm floor.

The tests that matter are the ones about restraint and about the chord. A pass
that moves copper can short a board, and a pass that measures a polygon as if
it were the circle it approximates will report a gap the fab does not have.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import pour_clearance  # noqa: E402
from circuitpy.fab import PROFILES  # noqa: E402

PROFILE = PROFILES["jlcpcb"]
REQUIRED = max(PROFILE.min_clearance_mm, PROFILE.kicad_zone_clearance_mm)


def _ring(cx: float, cy: float, radius: float, sides: int = 32) -> dict:
    return {"vertices": [
        {"x": cx + radius * math.cos(2 * math.pi * i / sides),
         "y": cy + radius * math.sin(2 * math.pi * i / sides)}
        for i in range(sides)
    ]}


def _square(half: float) -> dict:
    return {"vertices": [
        {"x": -half, "y": -half}, {"x": half, "y": -half},
        {"x": half, "y": half}, {"x": -half, "y": half},
    ]}


def _board(*, inner: list[dict] | None = None, via_net: str = "k_sig",
           via_xy: tuple[float, float] = (0.0, 0.0),
           via_outer: float = 0.6) -> list[dict]:
    return [
        {"type": "pcb_board", "width": 40, "height": 40, "num_layers": 2,
         "center": {"x": 0, "y": 0}},
        {"type": "source_net", "source_net_id": "net_gnd", "name": "GND",
         "is_ground": True, "subcircuit_connectivity_map_key": "k_gnd"},
        {"type": "source_net", "source_net_id": "net_sig", "name": "SIG",
         "subcircuit_connectivity_map_key": "k_sig"},
        {
            "type": "pcb_copper_pour", "pcb_copper_pour_id": "pour_0",
            "layer": "bottom", "source_net_id": "net_gnd", "shape": "brep",
            "brep_shape": {"outer_ring": _square(15.0),
                           "inner_rings": inner if inner is not None else []},
        },
        {
            "type": "pcb_via", "pcb_via_id": "pcb_via_0",
            "pcb_trace_id": "t0", "x": via_xy[0], "y": via_xy[1],
            "hole_diameter": 0.3, "outer_diameter": via_outer,
            "layers": ["top", "bottom"],
            "subcircuit_connectivity_map_key": via_net,
        },
    ]


def _write(tmp: Path, elements: list[dict]) -> Path:
    path = tmp / "circuit.json"
    path.write_text(json.dumps(elements), encoding="utf-8")
    return path


def _edge_distance(a: tuple, b: tuple, p: tuple) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, (
        (p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _worst_gap(path: Path) -> float:
    """Boundary-to-copper, measured the way a fab sees it: the polygon's own
    edges, not the circle it is standing in for."""
    elements = json.loads(path.read_text(encoding="utf-8"))
    pour = next(e for e in elements if e["type"] == "pcb_copper_pour")
    via = next(e for e in elements if e["type"] == "pcb_via")
    radius = via["outer_diameter"] / 2
    worst = math.inf
    brep = pour["brep_shape"]
    for ring in [brep["outer_ring"], *brep["inner_rings"]]:
        pts = [(v["x"], v["y"]) for v in ring["vertices"]]
        for i, (ax, ay) in enumerate(pts):
            bx, by = pts[(i + 1) % len(pts)]
            # distance from the via centre to this edge
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            t = 0.0 if length == 0 else max(0.0, min(1.0, (
                (via["x"] - ax) * dx + (via["y"] - ay) * dy) / length))
            worst = min(worst, math.hypot(
                via["x"] - (ax + t * dx), via["y"] - (ay + t * dy)) - radius)
    return worst


class PourClearanceTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- the defect -------------------------------------------------------

    def test_the_real_shortfall_is_repaired(self) -> None:
        """The measured case: a via cutout punched at `via_radius + 0.1` and
        drawn as a 32-gon leaves 0.098mm, not 0.1mm."""
        path = _write(self.tmp, _board(inner=[_ring(0, 0, 0.3 + 0.1)]))
        self.assertLess(_worst_gap(path), 0.1)
        result = pour_clearance.repair_pour_clearance(path, PROFILE)
        self.assertTrue(result.changed)
        self.assertGreaterEqual(round(_worst_gap(path), 6), REQUIRED - 1e-6)

    def test_the_chord_is_paid_for_not_assumed_away(self) -> None:
        """A 32-gon through radius R measures only R * cos(pi/32) between its
        vertices. Placing them at exactly `radius + required` would leave the
        *boundary* short — small, and the entire defect. The repair overshoots
        and then re-measures the edge, so the boundary clears rather than the
        vertices."""
        path = _write(self.tmp, _board(inner=[_ring(0, 0, 0.3 + 0.1)]))
        pour_clearance.repair_pour_clearance(path, PROFILE)
        pour = next(e for e in json.loads(path.read_text(encoding="utf-8"))
                    if e["type"] == "pcb_copper_pour")
        vertex = pour["brep_shape"]["inner_rings"][0]["vertices"][0]
        self.assertGreater(math.hypot(vertex["x"], vertex["y"]), 0.3 + REQUIRED)
        self.assertGreaterEqual(round(_worst_gap(path), 6), REQUIRED - 1e-6)

    def test_the_outer_ring_is_measured_too_not_only_the_holes(self) -> None:
        """Reading `outer_ring` and stopping is how the true worst gap on
        terminal-keyboard was measured as 0.297mm when it was 0.098mm — so the
        reverse blind spot is worth pinning as well."""
        path = _write(self.tmp, _board(inner=[], via_xy=(14.9, 0.0)))
        result = pour_clearance.repair_pour_clearance(path, PROFILE)
        self.assertTrue(result.changed)
        self.assertGreaterEqual(round(_worst_gap(path), 6), REQUIRED - 1e-6)

    def test_it_converges_when_one_push_creates_the_next(self) -> None:
        """Pushing a vertex clear of one via can carry it toward another. A
        single pass left terminal-keyboard at -0.1019mm and called itself
        done."""
        elements = _board(inner=[_ring(0, 0, 0.4)])
        elements.append({
            "type": "pcb_via", "pcb_via_id": "pcb_via_1", "pcb_trace_id": "t1",
            "x": 1.6, "y": 0.0, "hole_diameter": 0.3, "outer_diameter": 0.6,
            "layers": ["top", "bottom"],
            "subcircuit_connectivity_map_key": "k_sig2",
        })
        path = _write(self.tmp, elements)
        pour_clearance.repair_pour_clearance(path, PROFILE)
        after = json.loads(path.read_text(encoding="utf-8"))
        pour = next(e for e in after if e["type"] == "pcb_copper_pour")
        vias = [e for e in after if e["type"] == "pcb_via"]
        for ring in [pour["brep_shape"]["outer_ring"],
                     *pour["brep_shape"]["inner_rings"]]:
            pts = [(v["x"], v["y"]) for v in ring["vertices"]]
            for via in vias:
                worst = min(
                    _edge_distance(pts[i], pts[(i + 1) % len(pts)],
                                   (via["x"], via["y"]))
                    for i in range(len(pts))
                ) - via["outer_diameter"] / 2
                self.assertGreaterEqual(round(worst, 6), REQUIRED - 1e-6)

    def test_an_impossible_gap_stops_and_reports_rather_than_looping(self) -> None:
        """Two vias 0.9mm apart each need 0.45mm of room, so a boundary between
        them cannot be placed legally at all. The pass must terminate and let
        the measured number say so — a repair that cannot converge and pretends
        otherwise is worse than one that refuses."""
        elements = _board(inner=[_ring(0, 0, 0.4)])
        elements.append({
            "type": "pcb_via", "pcb_via_id": "pcb_via_1", "pcb_trace_id": "t1",
            "x": 0.9, "y": 0.0, "hole_diameter": 0.3, "outer_diameter": 0.6,
            "layers": ["top", "bottom"],
            "subcircuit_connectivity_map_key": "k_sig2",
        })
        path = _write(self.tmp, elements)
        result = pour_clearance.repair_pour_clearance(path, PROFILE)
        self.assertTrue(result.ran)
        worst = min(f.worst_after_mm for f in result.fixes)
        self.assertLess(worst, REQUIRED)     # honest about not reaching it

    # -- restraint --------------------------------------------------------

    def test_the_pours_own_net_is_never_pushed_away_from(self) -> None:
        """A ground via inside a ground pour is a connection. Pushing the plane
        off its own stitching vias would cut the plane into islands and undo
        the reason it exists."""
        path = _write(self.tmp, _board(inner=[], via_net="k_gnd"))
        before = path.read_text(encoding="utf-8")
        result = pour_clearance.repair_pour_clearance(path, PROFILE)
        self.assertFalse(result.changed)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_pour_that_already_holds_is_left_byte_for_byte(self) -> None:
        path = _write(self.tmp, _board(inner=[_ring(0, 0, 2.0)]))
        before = path.read_text(encoding="utf-8")
        result = pour_clearance.repair_pour_clearance(path, PROFILE)
        self.assertFalse(result.changed)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_board_with_no_pour_is_not_touched(self) -> None:
        elements = [e for e in _board() if e["type"] != "pcb_copper_pour"]
        path = _write(self.tmp, elements)
        before = path.read_text(encoding="utf-8")
        result = pour_clearance.repair_pour_clearance(path, PROFILE)
        self.assertFalse(result.ran)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_copper_is_only_ever_removed_never_added(self) -> None:
        """Every vertex moves *away* from copper, so a repaired pour is a
        subset of the pour it replaced around each obstacle. A pass that could
        grow a plane could short one."""
        path = _write(self.tmp, _board(inner=[_ring(0, 0, 0.4)]))
        before = json.loads(path.read_text(encoding="utf-8"))
        pour_clearance.repair_pour_clearance(path, PROFILE)
        after = json.loads(path.read_text(encoding="utf-8"))
        b = next(e for e in before if e["type"] == "pcb_copper_pour")
        a = next(e for e in after if e["type"] == "pcb_copper_pour")
        for rb, ra in zip(b["brep_shape"]["inner_rings"],
                          a["brep_shape"]["inner_rings"]):
            for vb, va in zip(rb["vertices"], ra["vertices"]):
                # A hole in the plane may only get bigger.
                self.assertGreaterEqual(
                    math.hypot(va["x"], va["y"]) + 1e-9,
                    math.hypot(vb["x"], vb["y"]),
                )

    def test_an_unreadable_board_is_a_refusal_and_never_an_exception(self) -> None:
        path = self.tmp / "circuit.json"
        path.write_text("{not json", encoding="utf-8")
        result = pour_clearance.repair_pour_clearance(path, PROFILE)
        self.assertFalse(result.ran)
        self.assertEqual(result.findings(), [])

    def test_it_says_what_it_moved(self) -> None:
        path = _write(self.tmp, _board(inner=[_ring(0, 0, 0.4)]))
        result = pour_clearance.repair_pour_clearance(path, PROFILE)
        detail = result.findings()[0]["detail"]
        self.assertIn("pcb_via_0", detail)
        self.assertIn("boundary vertices pushed back", detail)


if __name__ == "__main__":
    unittest.main()


def _board_with_trace(*, net: str = "k_sig", width: float = 0.2,
                      seg: tuple = (-14.0, 0.0, 14.0, 0.0)) -> list[dict]:
    """A pour with a track crossing it, far from any via."""
    ax, ay, bx, by = seg
    # A cutout that already exists and is already too tight — the real shape.
    # weather-badge-17 did not lack a cutout around its tracks; the converter
    # carved one and it came out at 0.0465mm against a 0.15mm rule. This pass
    # pushes an existing boundary; it does not invent one.
    half = width / 2 + 0.05
    inner = [[
        {"x": ax - 0.05, "y": ay - half}, {"x": bx + 0.05, "y": by - half},
        {"x": bx + 0.05, "y": by + half}, {"x": ax - 0.05, "y": ay + half},
    ]]
    els = [e for e in _board(inner=inner) if e.get("type") != "pcb_via"]
    els.append({
        "type": "pcb_trace", "pcb_trace_id": "t_cross",
        "subcircuit_connectivity_map_key": net,
        "route": [
            {"route_type": "wire", "x": ax, "y": ay, "width": width,
             "layer": "bottom"},
            {"route_type": "wire", "x": bx, "y": by, "width": width,
             "layer": "bottom"},
        ],
    })
    return els


class PourVersusTracks(unittest.TestCase):
    """Tracks were the class this pass could not see.

    weather-badge-17, 2026-08-19: a top pour landed **41 clearance violations
    at 0.0465mm against the 0.15mm the zone declares**, every one of them the
    pour against a *track*, and that is what kept the top pour off every board.
    An outside hardware reviewer on 2026-08-27 put the cost plainly —
    *"thường phủ đồng phủ luôn 2 mặt"*, both sides is simply what a board looks
    like — and wb-17 recorded the reward: with the top poured,
    `netclass_pair_reference` clears, because the USB pair gets a plane.
    """

    def test_a_track_crossing_the_pour_is_seen_at_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), _board_with_trace())
            result = pour_clearance.repair_pour_clearance(path, PROFILE, required_mm=0.3)
            self.assertTrue(result.changed, "a track through the pour must move it")

    def test_the_pour_is_pushed_off_the_track_but_not_yet_to_the_margin(self) -> None:
        """**A partial fix, measured and stated rather than claimed.**

        The cutout starts 0.05mm off the track and comes out at ~0.137mm — a
        real improvement, and short of the 0.3mm asked for. The reason is
        geometric and specific: where a ring edge runs **parallel** to the
        track, every point on it is equidistant, so `_split_edges_near` has no
        single closest point to insert a vertex at and the corners alone cannot
        carry the middle of the edge outward.

        Discs never showed this because a via is never parallel to anything.
        Closing it means splitting a parallel edge at both ends of the overlap
        instead of at one point — filed, not done here.

        The assertion is deliberately the honest one: it must move, it must
        move the right way, and it must not silently claim the margin.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), _board_with_trace())
            before = pour_clearance._Capsule("t", (-14.0, 0.0, 14.0, 0.0), 0.1)
            pour_clearance.repair_pour_clearance(path, PROFILE, required_mm=0.3)
            els = json.loads(path.read_text())
            pour = next(e for e in els if e.get("type") == "pcb_copper_pour")
            worst = min(pour_clearance._ring_gap(r, before)
                        for r in pour_clearance._rings(pour))
            self.assertGreater(worst, 0.05,
                               "the pour must move off the track at all")
            self.assertLess(worst, 0.3,
                            "if this now reaches the margin, the parallel-edge "
                            "gap is closed and this test should assert it")

    def test_the_pours_own_net_is_left_alone(self) -> None:
        """Touching the plane is what a ground track is for."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), _board_with_trace(net="k_gnd"))
            result = pour_clearance.repair_pour_clearance(path, PROFILE, required_mm=0.3)
            self.assertFalse(result.changed)

    def test_a_track_on_the_other_layer_is_not_an_obstacle(self) -> None:
        els = _board_with_trace()
        for e in els:
            if e.get("type") == "pcb_trace":
                for pt in e["route"]:
                    pt["layer"] = "top"
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), els)
            result = pour_clearance.repair_pour_clearance(path, PROFILE, required_mm=0.3)
            self.assertFalse(result.changed, "a bottom pour ignores top copper")

    def test_a_capsule_is_one_obstacle_not_a_sampled_chain(self) -> None:
        """Sampling a 28mm track into discs would cost hundreds of obstacles
        per track and the loop is O(obstacles x vertices) per pass."""
        els = _board_with_trace()
        board = pour_clearance.diffpair._Board(els)
        caps = pour_clearance._trace_obstacles(board, "k_gnd", "bottom")
        self.assertEqual(len(caps), 1)
        self.assertAlmostEqual(caps[0].half_width, 0.1)
