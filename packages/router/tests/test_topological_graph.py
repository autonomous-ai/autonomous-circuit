"""Unit tests for ``algorithms/topological-graph.py``.

The file's name is not a Python identifier, so it is loaded by path. Nothing in
here touches the harness: the tests use ``routerfix``'s synthetic boards, the
committed instances, and the same scorer the tournament runs on.

What is worth testing in a topological router is not "did it route" — that is
the benchmark's job — but the four claims the approach rests on:

1. the triangulation is a real triangulation, on the degenerate input that
   breaks the textbook algorithm (a lattice);
2. a gate's width is *measured*, and a drill's 0.28mm keep-away is part of the
   measurement;
3. every emitted segment is legal, and an unroutable net is reported unrouted
   rather than papered over;
4. the same input gives byte-identical output.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

import routerfix
from routerlib import connectivity as conn
from routerlib import drc as drc_mod
from routerlib.bench import INSTANCE_DIR, load_instance
from routerlib.model import (
    BOTTOM,
    TOP,
    Budget,
    DesignRules,
    Drill,
    Keepout,
    Net,
    Pad,
    Point,
    RoutingProblem,
    Board,
)
from routerlib.scoring import determinism_check, score
from routerlib.workspace import Workspace

_PATH = Path(__file__).resolve().parents[1] / "algorithms" / "topological-graph.py"


def _load():
    spec = importlib.util.spec_from_file_location("topological_graph", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["topological_graph"] = module
    spec.loader.exec_module(module)
    return module


tg = _load()


# ---------------------------------------------------------------------------
# 1. The triangulation
# ---------------------------------------------------------------------------


def _triangle_area(points, tri) -> float:
    (ax, ay), (bx, by), (cx, cy) = (points[i] for i in tri)
    return abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) / 2.0


def test_delaunay_covers_a_square_exactly_once():
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    tris = tg.delaunay(points)
    assert len(tris) == 2
    assert sum(_triangle_area(points, t) for t in tris) == pytest.approx(100.0)


def test_delaunay_survives_a_perfect_lattice():
    """The degenerate case, which is also the common one.

    A key matrix puts every pad on an exact grid, so four points are exactly
    cocircular everywhere and three are exactly collinear along every row. That
    is precisely the input that leaves Bowyer-Watson's cavity ill-defined. The
    triangles must still tile the hull exactly once.
    """
    points = [(float(x), float(y)) for x in range(8) for y in range(8)]
    tris = tg.delaunay(points)
    assert sum(_triangle_area(points, t) for t in tris) == pytest.approx(49.0, abs=1e-9)
    assert len({v for tri in tris for v in tri}) == len(points)
    # Every interior edge is shared by exactly two triangles; hull edges by one.
    counts: dict[tuple[int, int], int] = {}
    for a, b, c in tris:
        for u, v in ((a, b), (b, c), (c, a)):
            counts[(min(u, v), max(u, v))] = counts.get((min(u, v), max(u, v)), 0) + 1
    assert set(counts.values()) <= {1, 2}


def test_delaunay_is_deterministic_and_orientation_is_ccw():
    points = [(math.cos(i) * 10, math.sin(i * 1.7) * 10) for i in range(60)]
    first = tg.delaunay(points)
    assert first == tg.delaunay(list(points))
    for a, b, c in first:
        assert tg._orient(*points[a], *points[b], *points[c]) > 0


def test_delaunay_of_two_points_is_empty():
    assert tg.delaunay([(0.0, 0.0), (1.0, 1.0)]) == []


# ---------------------------------------------------------------------------
# 2. Gates are measured, and they know what a drill costs
# ---------------------------------------------------------------------------


def test_a_gate_between_two_holes_is_narrower_than_between_two_pads():
    """The rule the shipped router has no representation for.

    Two obstacles the same size and the same distance apart leave *different*
    room depending on what they are: a component plated hole keeps copper
    0.28mm away, a pad 0.147mm. The gap between the two numbers is 0.266mm of
    channel, which is more than a whole 0.2mm track.
    """
    rules = DesignRules()  # 0.147 target clearance, 0.28 to a component hole
    pads = routerfix.two_pad_board(rules=rules)
    topo = tg.Topology(pads)
    sites = topo.sites
    pad_site = next(s for s in sites if s.kind == "pad")
    assert pad_site.keepaway == pytest.approx(rules.target_clearance_mm)

    with_hole = routerfix.two_pad_board(
        rules=rules,
        drills=(
            Drill(id="h1", center=Point(0.0, 2.0), width_mm=0.8, height_mm=0.8,
                  plated=True, pad_id="somepad"),
        ),
    )
    hole_site = next(
        s for s in tg.Topology(with_hole).sites if s.kind == "drill"
    )
    assert hole_site.keepaway == pytest.approx(rules.min_pth_to_copper_mm)
    assert hole_site.keepaway > pad_site.keepaway


def test_a_pad_of_the_routed_net_gives_its_own_wire_more_room():
    """Copper does not need clearance from the pad it is landing on."""
    problem = routerfix.two_pad_board()
    topo = tg.Topology(problem)
    net_id = problem.nets[0].id
    gates = [
        g for g in topo.gates
        if topo.sites[g.site_a].net == net_id or topo.sites[g.site_b].net == net_id
    ]
    assert gates, "the two-pad board must have gates touching its own pads"
    for gate in gates:
        assert topo.room(gate, 0, net_id) >= topo.room(gate, 0, "some-other-net")
    assert any(
        topo.room(g, 0, net_id) > topo.room(g, 0, "other") + 1e-9 for g in gates
    )


def test_a_top_pad_is_not_an_obstacle_on_the_bottom_layer():
    """Half the reason the bottom layer is a highway on a board of SMD parts."""
    problem = routerfix.two_pad_board(
        extra_pads=(
            routerfix.pad("x1", None, 0.0, 3.0, w=0.6, h=0.6, component="X"),
            routerfix.pad("x2", None, 0.0, 4.2, w=0.6, h=0.6, component="X"),
        )
    )
    topo = tg.Topology(problem)
    pad_gates = [
        g for g in topo.gates
        if topo.sites[g.site_a].kind == "pad" and topo.sites[g.site_b].kind == "pad"
    ]
    assert pad_gates, "two pads 1.2mm apart must share a gate"
    assert all(g.usable[1] > g.usable[0] for g in pad_gates)


# ---------------------------------------------------------------------------
# 3. Every segment is legal, and failure is reported as failure
# ---------------------------------------------------------------------------


def _route(problem, **kwargs):
    router = tg.TopologicalRouter(**kwargs)
    return router, router.route(problem, Budget(seed=0))


def test_a_two_pad_board_routes_and_scores_clean():
    problem = routerfix.two_pad_board()
    _router, solution = _route(problem)
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.errors == 0
    assert result.claim_honest


def test_it_goes_around_a_keepout_rather_than_through_it():
    problem = routerfix.two_pad_board(
        keepouts=(
            Keepout(id="k1", center=Point(0.0, 0.0), width_mm=3.0, height_mm=3.0),
        )
    )
    _router, solution = _route(problem)
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.violations_by_kind.get("keepout", 0) == 0
    # A straight line would have crossed the keepout, so the route must bend.
    assert any(len(t.points) > 2 for t in solution.traces)


#: Four keepout bars forming a closed ring around the pad at (-5, 0). Copper
#: cannot cross a keepout and a via does not help, so ``N1`` is unroutable on
#: purpose — the point of the test is what the router *says* about that.
_SEALED_IN = (
    Keepout(id="k_top", center=Point(-5.0, 1.5), width_mm=5.0, height_mm=1.0),
    Keepout(id="k_bottom", center=Point(-5.0, -1.5), width_mm=5.0, height_mm=1.0),
    Keepout(id="k_left", center=Point(-7.0, 0.0), width_mm=1.0, height_mm=4.0),
    Keepout(id="k_right", center=Point(-3.0, 0.0), width_mm=1.0, height_mm=4.0),
)


def test_an_impossible_net_is_reported_unrouted_not_faked():
    """A pad walled in on every side. The honest answer is "I did not finish"."""
    problem = routerfix.two_pad_board(keepouts=_SEALED_IN)
    _router, solution = _route(problem)
    result = score(problem, solution)
    assert result.completeness < 1.0
    assert solution.complete is False
    assert solution.unrouted_nets == ("N1",)
    assert result.errors == 0, "an unfinished board is fine; an illegal one is not"


def test_no_copper_is_left_behind_by_a_net_that_failed():
    """Half a net earns nothing and blocks everything after it, so it is rolled
    back rather than left on the board."""
    problem = routerfix.two_pad_board(keepouts=_SEALED_IN)
    _router, solution = _route(problem)
    assert solution.traces == ()
    assert solution.vias == ()


def test_every_emitted_segment_passes_the_workspace_it_was_built_with():
    """The claim the whole design rests on: nothing is emitted unchecked.

    Re-checking is not circular — it replays each committed segment against a
    Workspace holding *all the other* copper, which is stricter than the
    incremental check the router made while placing them in order.
    """
    problem = load_instance(INSTANCE_DIR / "matrix-ldo-3v3__usb-c-power.json")
    _router, solution = _route(problem)
    for index, trace in enumerate(solution.traces):
        others = [t for i, t in enumerate(solution.traces) if i != index]
        ws = Workspace(problem)
        for via in solution.vias:
            ws.commit_via(via)
        for other in others:
            ws.commit_trace(other)
        verdict = ws.path_ok(trace.layer, trace.points, trace.width_mm, trace.net)
        assert verdict is True, f"{trace.id}: {verdict}"


def test_a_via_never_lands_inside_an_smd_pad():
    for name in ("matrix-rp2040-core__usb-c-data", "harness-puck"):
        problem = load_instance(INSTANCE_DIR / f"{name}.json")
        _router, solution = _route(problem)
        found = drc_mod.via_in_pad_findings(problem, solution)
        assert found == [], f"{name}: {found}"


def test_it_beats_the_baseline_without_breaking_a_rule():
    """The only comparison that matters, on the instance the baseline is worst
    at. Legality is not traded for completeness: both are asserted."""
    from routerlib.baseline import PatternRouter

    problem = load_instance(INSTANCE_DIR / "matrix-rp2040-core__usb-c-data.json")
    floor = score(problem, PatternRouter().route(problem, Budget(seed=0)))
    _router, solution = _route(problem)
    mine = score(problem, solution)
    assert mine.errors == 0
    assert mine.completeness > floor.completeness


def test_a_ground_plane_is_a_destination_not_an_obstacle():
    """The case that diagnosed the shipped router: it produced byte-identical
    copper with and without a pour. These two must differ, and the plane
    variant must not need a trace between ground pads to connect them."""
    plain = load_instance(INSTANCE_DIR / "hydrate-coaster.json")
    poured = load_instance(INSTANCE_DIR / "hydrate-coaster-plane.json")
    _r1, without = _route(plain)
    _r2, with_plane = _route(poured)
    assert without.fingerprint() != with_plane.fingerprint()

    # With a pour, ground is connected, and it is connected by stitching into
    # the copper that is already there rather than by a spanning tree: the
    # copper it uses is a fraction of its own minimum spanning tree, which is
    # the hard lower bound for connecting those pads with traces alone.
    ground = next(n for n in poured.nets if n.net_class == "ground")
    linked = conn.analyse(poured, with_plane)
    assert ground.id in linked.connected_nets

    pads = poured.pads_of(ground.id)
    mst = sum(
        math.hypot(pads[i].center.x - pads[j].center.x,
                   pads[i].center.y - pads[j].center.y)
        for i, j in tg._prim(
            [(p.center.x, p.center.y, p.id) for p in pads]
        )
    )
    stitched = sum(t.length_mm for t in with_plane.traces if t.net == ground.id)
    assert stitched < mst / 2.0
    assert any(v.net == ground.id for v in with_plane.vias)


# ---------------------------------------------------------------------------
# 4. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["matrix-status-led__ws2812-chain", "matrix-ldo-3v3__usb-c-power",
     "hydrate-coaster-plane"],
)
def test_same_input_same_output(name):
    problem = load_instance(INSTANCE_DIR / f"{name}.json")
    result = determinism_check(
        tg.TopologicalRouter(), problem, Budget(seed=0), runs=2
    )
    assert result.deterministic, result.detail


def test_a_fresh_router_reproduces_another_router_s_copper():
    """Determinism has to survive a new object and a warm topology cache."""
    problem = load_instance(INSTANCE_DIR / "matrix-ldo-3v3__usb-c-power.json")
    first = tg.TopologicalRouter().route(problem, Budget(seed=0))
    second = tg.TopologicalRouter().route(problem, Budget(seed=0))
    assert first.fingerprint() == second.fingerprint()


def test_the_seed_is_not_read_because_nothing_is_random():
    problem = load_instance(INSTANCE_DIR / "matrix-ldo-3v3__usb-c-power.json")
    a = tg.TopologicalRouter().route(problem, Budget(seed=0))
    b = tg.TopologicalRouter().route(problem, Budget(seed=12345))
    assert a.fingerprint() == b.fingerprint()


def test_the_budget_is_counted_and_a_tiny_one_stops_the_run():
    problem = load_instance(INSTANCE_DIR / "matrix-rp2040-core__usb-c-data.json")
    solution = tg.TopologicalRouter().route(
        problem, Budget(max_iterations=5, max_nodes=100, seed=0)
    )
    assert solution.complete is False
    assert solution.unrouted_nets
    assert score(problem, solution).errors == 0


# ---------------------------------------------------------------------------
# 5. The crossing analysis
# ---------------------------------------------------------------------------


def test_two_nets_that_cross_are_reported_as_crossing():
    rules = DesignRules()
    pads = (
        routerfix.pad("a1", "NA", -5.0, -5.0),
        routerfix.pad("a2", "NA", 5.0, 5.0),
        routerfix.pad("b1", "NB", -5.0, 5.0),
        routerfix.pad("b2", "NB", 5.0, -5.0),
    )
    nets = (
        Net(id="NA", name="NA", net_class="signal", pads=("a1", "a2"),
            min_width_mm=0.2),
        Net(id="NB", name="NB", net_class="signal", pads=("b1", "b2"),
            min_width_mm=0.2),
    )
    problem = RoutingProblem(
        id="x", board=Board(width_mm=20.0, height_mm=20.0), rules=rules,
        pads=pads, nets=nets,
    )
    report = tg.crossing_analysis(problem)
    assert report["segmentCrossings"] == 1
    assert report["singleLayerFeasible"] is False
    # Two mutually crossing nets are trivially two-colourable, and one of them
    # has to move: the matching lower bound is exactly 1.
    assert report["twoLayerViaFreeFeasible"] is True
    assert report["viaLowerBound"] == 1
    assert len(report["coverNets"]) == 1


def test_a_board_with_no_crossings_says_so():
    problem = routerfix.two_pad_board()
    report = tg.crossing_analysis(problem)
    assert report["segmentCrossings"] == 0
    assert report["singleLayerFeasible"] is True
    assert report["viaLowerBound"] == 0


def test_the_via_lower_bound_never_exceeds_the_cover():
    """It is a *lower* bound, and a bound that can be wrong in the unsafe
    direction is worse than no bound. A maximal matching is never larger than a
    vertex cover, on every real instance."""
    for path in sorted(INSTANCE_DIR.glob("*.json")):
        report = tg.crossing_analysis(load_instance(path))
        assert report["viaLowerBound"] <= len(report["coverNets"]) or (
            report["segmentCrossings"] == 0
        )
