"""Tests for the plane-and-classes router.

Run them explicitly — they are not under ``tests/``, because ``pytest.ini``
belongs to the harness and this is one algorithm family among several:

    cd packages/router
    PYTHONPATH=src:../circuitpy/src python3.12 -m pytest algorithms -q

Three things are worth more than the rest and each has a test that would fail
loudly if it stopped being true:

* the grid model is *conservative but not committed to* — every path is
  verified exactly, so a scored run has zero DRC errors;
* the router's ``complete`` claim agrees with connectivity recomputed from the
  copper;
* nothing in the control flow reads a clock, proven by running with the
  wall-clock valve slammed shut and getting byte-identical copper.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from plane_and_classes_loader import load  # noqa: E402

from routerlib import connectivity, drc  # noqa: E402
from routerlib.adapters import solution_to_elements  # noqa: E402
from routerlib.bench import load_instance, with_ground_plane  # noqa: E402
from routerlib.geometry import capsule_gap, disc_capsule, pad_capsule  # noqa: E402
from routerlib.model import (  # noqa: E402
    BOTTOM,
    TOP,
    Board,
    Budget,
    DesignRules,
    Drill,
    Net,
    Pad,
    Point,
    RoutingProblem,
)
from routerlib.scoring import score  # noqa: E402

pac = load()

INSTANCES = HERE.parent / "benchmarks" / "instances"
#: Small enough to route many times inside a test run.
TINY = ("matrix-i2c-bus", "matrix-status-led", "matrix-status-led__ws2812-chain")


def instance(name: str):
    return load_instance(INSTANCES / f"{name}.json")


def route(problem, **budget_kwargs):
    return pac.PlaneAndClassesRouter().route(problem, Budget(**budget_kwargs))


# ---------------------------------------------------------------------------
# Geometry: the stamp has to agree with the distance it claims to encode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spine",
    [
        (0.0, 0.0, 0.0, 0.0),
        (-1.0, 0.5, 2.0, 0.5),
        (0.3, -2.0, 0.3, 1.5),
        (-1.0, -1.0, 1.4, 0.9),
        (2.0, 1.0, -0.5, 2.5),
    ],
)
def test_row_span_matches_brute_force(spine):
    """The analytic row slice of a capsule is exactly the set of points within
    the radius: it covers every point that is inside, and both of its ends sit
    on the boundary rather than somewhere convenient outside it."""
    ax, ay, bx, by = spine
    radius = 0.37
    for step in range(-40, 41):
        cy = step * 0.05
        span = pac._stadium_row_span(ax, ay, bx, by, radius, cy)
        inside = [
            i * 0.025
            for i in range(-120, 121)
            if _distance(i * 0.025, cy, ax, ay, bx, by) <= radius + 1e-12
        ]
        if not inside:
            # Nothing in this row is inside; a span, if reported, must be
            # degenerate rather than a swathe of free board marked occupied.
            assert span is None or _distance(
                (span[0] + span[1]) / 2, cy, ax, ay, bx, by
            ) <= radius + 1e-9
            continue
        assert span is not None, (spine, cy)
        assert span[0] <= min(inside) + 1e-9
        assert span[1] >= max(inside) - 1e-9
        for edge in span:
            assert _distance(edge, cy, ax, ay, bx, by) <= radius + 1e-6


def _distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


def test_field_ownership_is_shared_then_hard():
    """One net near a cell owns it; two different nets make it nobody's."""
    grid = pac.Grid(x0=0.0, y0=0.0, nx=20, ny=20, pitch=0.1)
    field = pac.Field(grid, fill=pac.FREE)
    field.stamp(0.5, 0.5, 0.5, 0.5, 0.25, 7)
    cell = grid.cell_at(0.5, 0.5)
    assert field.cells[cell] == 7
    field.stamp(0.5, 0.5, 0.5, 0.5, 0.25, 7)  # same net again: still its own
    assert field.cells[cell] == 7
    field.stamp(0.6, 0.5, 0.6, 0.5, 0.25, 9)  # a second net: nobody's
    assert field.cells[cell] == pac.HARD
    far = grid.cell_at(1.5, 1.5)
    assert field.cells[far] == pac.FREE


def test_grid_phase_puts_pads_on_nodes():
    """A lattice that misses the pad centres closes escape corridors that are
    only a tenth of a millimetre wide, so the phase is chosen, not inherited."""
    problem = _bare_problem(
        pads=[
            _pad(f"p{i}", "n1", 1.0 + 0.5 * i, 2.0, 0.3, 1.3) for i in range(6)
        ]
    )
    grid = pac._grid_for(problem)
    for pad in problem.pads:
        x, y = grid.xy(grid.cell_at(pad.center.x, pad.center.y))
        assert abs(x - pad.center.x) < 1e-6
        assert abs(y - pad.center.y) < 1e-6


# ---------------------------------------------------------------------------
# The claim that matters: nothing illegal, ever
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", TINY)
def test_no_drc_errors(name):
    problem = instance(name)
    result = score(problem, route(problem))
    assert result.errors == 0, result.error_kinds


@pytest.mark.parametrize("name", TINY)
def test_completeness_claim_is_honest(name):
    """``complete`` is the router's own claim and the scorer recomputes it. The
    two must agree — a plausible board that is not actually connected is the
    worst output this package can produce."""
    problem = instance(name)
    solution = route(problem)
    recomputed = connectivity.analyse(problem, solution)
    assert solution.complete == (recomputed.completeness >= 1.0)
    assert set(solution.unrouted_nets) == set(recomputed.unconnected_nets)


@pytest.mark.parametrize("name", TINY)
def test_no_orphan_copper(name):
    """Every trace touches a pad of its own net. A branch that starts from a
    cell the pull-taut pass moved the copper off would be copper connected to
    nothing, and the router would still believe it had joined two pads."""
    problem = instance(name)
    assert connectivity.analyse(problem, route(problem)).orphan_copper == ()


def test_verification_rejects_what_the_grid_would_allow():
    """A gap too narrow for the trace is refused rather than squeezed into.

    Two pads of foreign nets 0.45mm apart leave 0.25mm of air; a 0.2mm trace
    needs 0.2 + 2 x 0.147 = 0.494mm. The router must route around or not at
    all — it must never thread it.
    """
    left = _pad("a0", "na", -3.0, 0.0, 0.6, 0.6)
    right = _pad("a1", "na", 3.0, 0.0, 0.6, 0.6)
    wall_lo = _pad("w0", "nb", 0.0, -0.525, 0.6, 0.6)
    wall_hi = _pad("w1", "nb", 0.0, 0.525, 0.6, 0.6)
    problem = _bare_problem(
        pads=[left, right, wall_lo, wall_hi],
        nets=[
            ("na", "SIG", "signal", ("a0", "a1")),
            ("nb", "OTHER", "signal", ("w0", "w1")),
        ],
        width=12.0,
        height=8.0,
    )
    solution = route(problem)
    assert drc.check(problem, solution).errors == ()
    for trace in solution.traces:
        if trace.net != "na":
            continue
        for a, b in trace.segments:
            from routerlib.geometry import segment_capsule

            copper = segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm)
            for pad in (wall_lo, wall_hi):
                assert capsule_gap(copper, pad_capsule(pad)) >= 0.1


# ---------------------------------------------------------------------------
# Determinism, including the one that is easy to get wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", TINY)
def test_deterministic(name):
    problem = instance(name)
    first, second = route(problem), route(problem)
    assert first.fingerprint() == second.fingerprint()
    assert solution_to_elements(problem, first) == solution_to_elements(problem, second)


def test_the_clock_cannot_change_the_board():
    """The strongest form of the budget rule: run with the wall-clock safety
    valve set to zero — so ``BudgetMeter.exhausted`` is true from the first
    tick — and the copper must be byte-identical to a normal run. If any branch
    read the clock, this would differ."""
    problem = instance("matrix-status-led__ws2812-chain")
    normal = route(problem)
    starved = route(problem, wall_clock_cap_s=0.0)
    assert normal.fingerprint() == starved.fingerprint()


def test_counted_budget_is_respected_and_reported():
    problem = instance("matrix-status-led__ws2812-chain")
    tight = route(problem, max_nodes=500)
    assert tight.nodes_expanded <= 500 + pac._NODE_CAP
    assert not tight.complete
    generous = route(problem)
    assert generous.nodes_expanded >= tight.nodes_expanded


def test_mst_is_order_independent():
    pads = [_pad(f"p{i}", "n", math.cos(i) * 4, math.sin(i) * 4, 0.6, 0.6) for i in range(7)]
    a = pac._mst_edges(pads)
    b = pac._mst_edges(list(reversed(pads)))
    assert [(x.id, y.id) for x, y in a] == [(x.id, y.id) for x, y in b]


# ---------------------------------------------------------------------------
# Planes
# ---------------------------------------------------------------------------


def test_plane_net_is_stitched_not_routed():
    """The whole point of the family: with a pour, the ground net's pads get
    vias into it and (almost) no copper runs between them."""
    base = instance("matrix-status-led__ws2812-chain")
    planed = with_ground_plane(base)
    ground = next(n for n in planed.nets if n.net_class == "ground")
    solution = route(planed)

    plain = route(base)
    ground_copper_planed = sum(
        t.length_mm for t in solution.traces if t.net == ground.id
    )
    ground_copper_plain = sum(t.length_mm for t in plain.traces if t.net == ground.id)
    assert ground_copper_planed < ground_copper_plain * 0.5, (
        ground_copper_planed,
        ground_copper_plain,
    )
    assert connectivity.analyse(planed, solution).fragments[ground.id] == 1


def test_every_stitching_via_lands_in_the_pour():
    """A via that misses the pour connects nothing. Checked with the same
    point-in-polygon test the scorer's connectivity uses."""
    from routerlib.geometry import PolygonIndex

    planed = with_ground_plane(instance("matrix-status-led__ws2812-chain"))
    ground = next(n for n in planed.nets if n.net_class == "ground")
    plane = next(p for p in planed.planes if p.net == ground.id)
    shape = PolygonIndex(plane.outline)
    vias = [v for v in route(planed).vias if v.net == ground.id]
    assert vias
    for via in vias:
        assert shape.contains(via.center.x, via.center.y), via


def test_plane_beats_no_plane_on_ground_vias():
    """A plane must change the answer. The router we ship produced
    byte-identical copper with and without one; this asserts the opposite."""
    base = instance("matrix-status-led__ws2812-chain")
    planed = with_ground_plane(base)
    assert route(base).fingerprint() != route(planed).fingerprint()


# ---------------------------------------------------------------------------
# Net classes
# ---------------------------------------------------------------------------


def test_rails_are_offered_their_resolved_width_first():
    ladders = pac._width_ladders(instance("matrix-status-led__ws2812-chain"), relaxed=False)
    problem = instance("matrix-status-led__ws2812-chain")
    for net in problem.nets:
        assert ladders[net.id][0] == pytest.approx(
            max(net.min_width_mm, problem.rules.min_trace_mm)
        )
        assert min(ladders[net.id]) >= problem.rules.min_trace_mm


def test_width_ladder_never_goes_below_the_fab_minimum():
    problem = instance("hydrate-coaster")
    for steps in pac._width_ladders(problem, relaxed=True).values():
        assert steps == sorted(steps, reverse=True)
        assert min(steps) >= problem.rules.min_trace_mm
        # The bottom rung is the *recommended* minimum, not the hard floor.
        assert min(steps) >= problem.rules.warn_trace_mm - 1e-9


def test_class_order_puts_planes_and_rails_first():
    problem = instance("matrix-ldo-3v3__usb-c-power")
    jobs = pac.PlaneAndClassesRouter()._jobs(problem, set(), 0)
    groups = [job.group for job in jobs]
    assert groups == sorted(groups)
    kinds = [
        problem.nets_by_id[job.nets[0].id].net_class
        for job in jobs
        if job.group in (1, 3)
    ]
    assert kinds and set(kinds) <= {"power", "ground"}


def test_differential_pair_is_one_job():
    problem = instance("matrix-rp2040-core__usb-c-data")
    jobs = pac.PlaneAndClassesRouter()._jobs(problem, set(), 0)
    pairs = [job for job in jobs if isinstance(job, pac._PairJob)]
    assert pairs, "this instance records two differential pairs"
    for job in pairs:
        first, second = job.nets
        assert first.diff_partner == second.id
        assert second.diff_partner == first.id


@pytest.mark.parametrize("name", ["matrix-rp2040-core__usb-c-data", "harness-puck"])
def test_differential_pair_runs_beside_its_partner(name):
    """Coupling is steered by cost, not constructed, so the bar is not
    'perfect' — it is 'unmistakably better than routing the two halves as
    unrelated wires', measured against the router that does exactly that."""
    from routerlib.baseline import PatternRouter

    problem = instance(name)
    ours = score(problem, route(problem)).quality.diff_pair_coupling
    theirs = score(problem, PatternRouter().route(problem, Budget())).quality
    assert ours is not None and theirs.diff_pair_coupling is not None
    assert ours > 0.15
    assert ours > theirs.diff_pair_coupling + 0.1, (ours, theirs.diff_pair_coupling)


# ---------------------------------------------------------------------------
# The rotation divergence, which is a real defect in the pipeline
# ---------------------------------------------------------------------------


def test_vias_clear_the_unrotated_pad_model_too():
    """``routerlib.geometry`` reads ``ccw_rotation`` and ``circuitpy.checks``
    does not, so a via 0.3mm clear of a turned pad can be reported as
    overlapping it. Until the pipeline is fixed, clearing both shapes is the
    only answer that is honest under either reading."""
    from routerlib.geometry import rect_capsule

    problem = instance("matrix-rp2040-core__sw-tact")
    solution = route(problem)
    turned = [p for p in problem.pads if abs(((p.rotation_deg + 90) % 180) - 90) > 1e-6]
    assert turned, "this instance is the one the divergence was measured on"
    for via in solution.vias:
        hole = disc_capsule(via.center.x, via.center.y, via.drill_mm)
        for pad in turned:
            if pad.net and pad.net == via.net:
                continue
            flat = rect_capsule(
                pad.center.x, pad.center.y, pad.width_mm, pad.height_mm, 0.0
            )
            assert capsule_gap(hole, flat) >= problem.rules.min_via_to_copper_mm - 1e-9


def test_that_instance_scores_without_errors():
    problem = instance("matrix-rp2040-core__sw-tact")
    result = score(problem, route(problem))
    assert result.errors == 0, result.error_kinds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pad(pad_id: str, net: str, x: float, y: float, w: float, h: float, **kwargs) -> Pad:
    return Pad(
        id=pad_id,
        net=net,
        center=Point(x, y),
        width_mm=w,
        height_mm=h,
        layers=kwargs.pop("layers", (TOP,)),
        kind=kwargs.pop("kind", "smd"),
        component=kwargs.pop("component", "U1"),
        **kwargs,
    )


def _bare_problem(pads, nets=None, width=20.0, height=12.0) -> RoutingProblem:
    rules = DesignRules.jlcpcb()
    if nets is None:
        by_net: dict[str, list[str]] = {}
        for pad in pads:
            by_net.setdefault(pad.net, []).append(pad.id)
        nets = [
            (key, key.upper(), "signal", tuple(sorted(ids)))
            for key, ids in sorted(by_net.items())
        ]
    return RoutingProblem(
        id="synthetic",
        board=Board(width_mm=width, height_mm=height),
        rules=rules,
        pads=tuple(pads),
        nets=tuple(
            Net(
                id=key,
                name=name,
                net_class=net_class,
                pads=pad_ids,
                min_width_mm=rules.width_for(net_class),
                priority={"ground": 10, "power": 20}.get(net_class, 100),
            )
            for key, name, net_class, pad_ids in nets
        ),
    )
