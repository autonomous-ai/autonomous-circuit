"""Net-class decomposition: does each stage get the right nets, and does the
merge tell the truth about what came back?

The composition itself places no copper, so there is nothing to check about its
geometry. What there is to check is everything around the stage boundary —
which nets a stage is asked for, what it sees of the copper already down, and
whether two families that mint the same id can make the scorer believe in a
connection nobody routed. That last one is the reason this file exists: it is
a bug the composition can create out of two correct routers.
"""

from __future__ import annotations

import dataclasses

import pytest
from routerlib import connectivity as conn
from routerlib.compositions import netclass as nc
from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    Budget,
    DesignRules,
    Net,
    Pad,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)

BUDGET = Budget(max_iterations=1000, max_nodes=1000, seed=0)
RULES = DesignRules()


# ---------------------------------------------------------------------------
# A board with one net of every class, and stub routers to compose over it
# ---------------------------------------------------------------------------


def _pad(pad_id: str, net: str, x: float, y: float, layer: str = TOP) -> Pad:
    return Pad(
        id=pad_id, net=net, center=Point(x, y), width_mm=1.0, height_mm=1.0,
        layers=(layer,), kind="smd", component="U1", port_id=f"port_{pad_id}",
    )


def _net(net_id: str, net_class: str, pads: tuple[str, ...], **kw) -> Net:
    return Net(
        id=net_id, name=net_id.upper(), net_class=net_class, pads=pads,
        min_width_mm=0.5 if net_class in ("power", "ground") else 0.2,
        source_net_id=f"source_{net_id}", **kw,
    )


def board(*, planed: str | None = None, lone: bool = False) -> RoutingProblem:
    """One net of every class on a 40 x 40mm board.

    ``planed`` pours a plane belonging to that net, which is what moves it out
    of the ordinary classes and into the plane stage. ``lone`` adds a one-pad
    net, which no stage should ever be asked to route.
    """
    pads = [
        _pad("g1", "gnd", -15.0, -15.0), _pad("g2", "gnd", 15.0, -15.0),
        _pad("v1", "vcc", -15.0, -5.0), _pad("v2", "vcc", 15.0, -5.0),
        _pad("dp1", "dp", -15.0, 5.0), _pad("dp2", "dp", 15.0, 5.0),
        _pad("dm1", "dm", -15.0, 8.0), _pad("dm2", "dm", 15.0, 8.0),
        _pad("s1", "sig", -15.0, 15.0), _pad("s2", "sig", 15.0, 15.0),
    ]
    nets = [
        _net("gnd", "ground", ("g1", "g2")),
        _net("vcc", "power", ("v1", "v2")),
        _net("dp", "diff_pair", ("dp1", "dp2"), diff_partner="dm"),
        _net("dm", "diff_pair", ("dm1", "dm2"), diff_partner="dp"),
        _net("sig", "signal", ("s1", "s2")),
    ]
    if lone:
        pads.append(_pad("x1", "lone", 0.0, 18.0))
        nets.append(_net("lone", "signal", ("x1",)))
    planes: tuple[Plane, ...] = ()
    if planed:
        planes = (
            Plane(
                id="pour0", net=planed, layer=BOTTOM,
                outline=(Point(-18, -18), Point(18, -18), Point(18, 18),
                         Point(-18, 18)),
            ),
        )
    return RoutingProblem(
        id="netclass-fixture",
        board=Board(width_mm=40.0, height_mm=40.0),
        rules=RULES,
        pads=tuple(pads),
        nets=tuple(nets),
        planes=planes,
    )


class Recorder:
    """A router that places nothing and remembers exactly what it was handed."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[RoutingProblem] = []

    def factory(self):
        def make():
            return self
        return make

    def route(self, problem, budget) -> RoutingSolution:
        self.calls.append(problem)
        return RoutingSolution(router=self.name, iterations=1)


class Joiner:
    """Places one straight top-layer trace per net, pad to pad.

    Enough to be genuinely connected under ``routerlib.connectivity``, which is
    what a stage report has to be measured against.
    """

    def __init__(self, name: str, *, vias: bool = False, trace: bool = True) -> None:
        self.name = name
        self.vias = vias
        self.trace = trace

    def factory(self):
        def make():
            return self
        return make

    def route(self, problem, budget) -> RoutingSolution:
        traces: list[Trace] = []
        vias: list[Via] = []
        for index, net in enumerate(problem.routable_nets):
            pads = problem.pads_of(net.id)
            if self.trace:
                traces.append(
                    Trace(id=f"t{index}", net=net.id, layer=TOP,
                          points=tuple(p.center for p in pads), width_mm=0.2)
                )
            if self.vias:
                for k, pad in enumerate(pads):
                    vias.append(
                        Via(id=f"v{index}_{k}", net=net.id, center=pad.center)
                    )
        return RoutingSolution(
            router=self.name, traces=tuple(traces), vias=tuple(vias),
            complete=True, iterations=1,
        )


def registry_of(**routers) -> dict:
    return {name: r.factory() for name, r in routers.items()}


# ---------------------------------------------------------------------------
# A plan is data, so it is checked once
# ---------------------------------------------------------------------------


def test_a_stage_naming_an_unknown_class_is_rejected_at_construction():
    with pytest.raises(ValueError, match="unknown net class"):
        nc.ClassStage("oops", "r", classes=("powr",))


def test_a_catch_all_may_not_also_filter():
    with pytest.raises(ValueError, match="catch-all"):
        nc.ClassStage("oops", "r", classes=("power",), rest=True)


def test_two_stages_may_not_share_a_label():
    with pytest.raises(ValueError, match="unique"):
        nc.validate_plan((
            nc.ClassStage("a", "r", classes=("power",)),
            nc.ClassStage("a", "r", rest=True),
        ))


def test_a_catch_all_in_the_middle_is_rejected_because_it_makes_the_rest_dead():
    with pytest.raises(ValueError, match="dead"):
        nc.validate_plan((
            nc.ClassStage("a", "r", rest=True),
            nc.ClassStage("b", "r", classes=("power",)),
        ))


def test_every_named_plan_validates_and_ends_in_a_catch_all():
    for name, plan in nc.PLANS.items():
        nc.validate_plan(plan)
        assert plan[-1].rest, f"{name} would silently drop nets"


def test_every_stage_names_a_router_the_module_believes_in():
    """A typo in a plan is a stage that never runs and a bucket of nets that
    silently goes unrouted, which reads exactly like a hard board."""
    known = {nc.GENERAL, nc.PLANE_EXPERT, nc.PAIR_EXPERT, "pathfinder-negotiated"}
    for name, plan in nc.PLANS.items():
        for stage in plan:
            assert stage.router in known, f"{name}/{stage.label}: {stage.router}"


def test_the_default_plan_has_no_power_stage():
    """Pinned, because the intuition that put one there is a good one.

    A power stage costs 17.7 points of mean completeness on the benchmark with
    every other stage held fixed. If this assertion starts failing, the plan
    changed; re-run the suite before changing the assertion.
    """
    assert [s.label for s in nc.DEFAULT_PLAN] == ["plane", "pair", "rest"]
    assert nc.DEFAULT_PLAN is nc.PLANS["pairs"]


def test_the_rejected_stages_list_may_not_shrink():
    """A tried-and-failed stage is worth more than an untried one.

    The same guard ``portfolio.REJECTED_RULES`` has, for the same reason:
    without it the next agent re-derives "power needs its own stage" from the
    intuition that already produced it once.
    """
    assert len(nc.REJECTED_STAGES) >= 4
    for claim, evidence in nc.REJECTED_STAGES:
        assert claim and evidence
        assert any(ch.isdigit() for ch in evidence), (
            f"{claim!r} is refuted by an opinion, not a measurement"
        )


def test_every_measured_plan_still_exists_and_carries_a_ruler():
    """A number whose plan has been edited is a number about a different plan."""
    assert nc.MEASURED_RULER
    for name in nc.MEASURED:
        assert name in nc.PLANS, f"{name} was measured and then deleted"


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def test_every_routable_net_lands_in_exactly_one_bucket():
    problem = board()
    split = nc.partition(problem, nc.DEFAULT_PLAN)
    seen = [net_id for _, ids in split.buckets for net_id in ids]
    assert sorted(seen) == sorted(n.id for n in problem.routable_nets)
    assert len(seen) == len(set(seen)), "a net routed twice is copper placed twice"
    assert split.unclaimed == ()


def test_a_one_pad_net_is_never_handed_to_a_stage():
    split = nc.partition(board(lone=True), nc.DEFAULT_PLAN)
    everywhere = {net_id for _, ids in split.buckets for net_id in ids}
    assert "lone" not in everywhere
    assert "lone" not in split.unclaimed


def test_a_poured_ground_goes_to_the_plane_stage_and_a_dry_one_does_not():
    dry = dict(nc.partition(board(), nc.DEFAULT_PLAN).buckets)
    assert dry["plane"] == ()
    assert "gnd" in dry["rest"]

    poured = dict(nc.partition(board(planed="gnd"), nc.DEFAULT_PLAN).buckets)
    assert poured["plane"] == ("gnd",)
    assert "gnd" not in poured["rest"]


def test_a_poured_rail_goes_to_the_plane_stage_not_the_power_stage():
    """``planed`` beats ``classes``: the plane stage is first and a poured net
    is a stitching problem whatever class it carries."""
    poured = dict(nc.partition(board(planed="vcc"), nc.PLANS["brief"]).buckets)
    assert poured["plane"] == ("vcc",)
    assert poured["power"] == ()


def test_a_plan_with_no_catch_all_reports_what_it_dropped_instead_of_hiding_it():
    plan = (nc.ClassStage("power", "join", classes=("power",)),)
    result = nc.compose(
        board(), BUDGET, registry_of(join=Joiner("join")), plan=plan
    )
    assert set(result.partition.unclaimed) == {"gnd", "dp", "dm", "sig"}
    assert any("matched no stage" in note for note in result.notes)
    assert set(result.solution.unrouted_nets) == {"gnd", "dp", "dm", "sig"}


# ---------------------------------------------------------------------------
# What a stage is handed
# ---------------------------------------------------------------------------


def test_a_stage_sees_only_its_own_nets():
    power, rest = Recorder("power"), Recorder("rest")
    nc.compose(
        board(), BUDGET, registry_of(power=power, rest=rest),
        plan=(nc.ClassStage("power", "power", classes=("power",)),
              nc.ClassStage("rest", "rest", rest=True)),
    )
    assert [n.id for n in power.calls[0].nets] == ["vcc"]
    assert sorted(n.id for n in rest.calls[0].nets) == ["dm", "dp", "gnd", "sig"]


def test_a_stage_sees_the_previous_stages_copper_as_obstacles():
    """The guarantee that makes this a composition and not a merge: a later
    stage plans against the copper already down, so it cannot place a violation
    against it that neither stage could see."""
    later = Recorder("later")
    nc.compose(
        board(), BUDGET,
        registry_of(first=Joiner("first", vias=True), later=later),
        plan=(nc.ClassStage("power", "first", classes=("power",)),
              nc.ClassStage("rest", "later", rest=True)),
    )
    seen = later.calls[0]
    assert [t.net for t in seen.existing_traces] == ["vcc"]
    assert {v.net for v in seen.existing_vias} == {"vcc"}


def test_the_boards_own_pre_routed_copper_survives_into_every_stage():
    problem = dataclasses.replace(
        board(),
        existing_traces=(
            Trace(id="pre", net="sig", layer=TOP,
                  points=(Point(-15, 15), Point(15, 15)), width_mm=0.2),
        ),
    )
    first, second = Recorder("first"), Recorder("second")
    nc.compose(
        problem, BUDGET, registry_of(first=first, second=second),
        plan=(nc.ClassStage("power", "first", classes=("power",)),
              nc.ClassStage("rest", "second", rest=True)),
    )
    assert [t.id for t in first.calls[0].existing_traces] == ["pre"]
    assert [t.id for t in second.calls[0].existing_traces] == ["pre"]


def test_the_composition_does_not_re_emit_the_boards_pre_routed_copper():
    """Copper that was already on the board is not this router's output.

    ``routerlib.drc`` counts ``problem.existing_traces`` on its own, so
    returning them again doubles the reported copper and via count."""
    problem = dataclasses.replace(
        board(),
        existing_vias=(Via(id="pre", net="sig", center=Point(0.0, 15.0)),),
    )
    result = nc.compose(
        problem, BUDGET, registry_of(join=Joiner("join")),
        plan=(nc.ClassStage("rest", "join", rest=True),),
    )
    assert "pre" not in {v.id for v in result.solution.vias}


# ---------------------------------------------------------------------------
# The bug a composition creates out of two correct routers
# ---------------------------------------------------------------------------


def _shared_id_registry():
    """Two routers that both mint ``v0`` / ``v1``, which is not hypothetical:
    ``plane-and-classes`` and ``maze-astar`` both do exactly that."""

    class Stitcher:
        """Drops a via on each pad of each net and joins them only if asked.

        Ids are a running ``v0, v1, …`` per *call*, which is exactly what a
        real family does — the sequence restarts every time the router is
        constructed, and a composition constructs one per stage.
        """

        def __init__(self, name: str, *, join: bool) -> None:
            self.name = name
            self.join = join

        def factory(self):
            return lambda: self

        def route(self, problem, budget) -> RoutingSolution:
            vias, traces = [], []
            for net in problem.routable_nets:
                pads = problem.pads_of(net.id)
                for pad in pads:
                    vias.append(
                        Via(id=f"v{len(vias)}", net=net.id, center=pad.center)
                    )
                if self.join:
                    traces.append(
                        Trace(id=f"t{len(traces)}", net=net.id, layer=TOP,
                              points=tuple(p.center for p in pads), width_mm=0.2)
                    )
            return RoutingSolution(router=self.name, traces=tuple(traces),
                                   vias=tuple(vias), iterations=1)

    return {
        "stitch": Stitcher("stitch", join=False).factory(),
        "join": Stitcher("join", join=True).factory(),
    }


PLAN_SHARED = (
    nc.ClassStage("power", "stitch", classes=("power",)),
    nc.ClassStage("rest", "join", rest=True),
)


def test_two_stages_that_mint_the_same_id_do_not_merge_in_the_scorer():
    """The stage that placed no trace must be reported unconnected.

    Un-namespaced, the two stages' ``v0`` and ``v1`` become one union-find node
    each, the joined net's trace welds those two nodes together, and the
    *unjoined* net's two pads land in one component through copper that does
    not exist. The composition would report a connection nobody routed.
    """
    problem = board()
    result = nc.compose(problem, BUDGET, _shared_id_registry(), plan=PLAN_SHARED)

    ids = [t.id for t in result.solution.traces] + [
        v.id for v in result.solution.vias
    ]
    assert len(ids) == len(set(ids)), "stage copper ids collided"
    assert "vcc" in result.solution.unrouted_nets
    assert dict((s.label, s.connected) for s in result.stages)["power"] == 0


def test_and_the_same_copper_without_namespacing_really_does_lie():
    """The guard above is load-bearing, demonstrated rather than asserted.

    Same copper, ids left as the two families minted them: ``vcc`` is reported
    connected while the only copper on it is two vias 30mm apart.
    """
    problem = board()
    result = nc.compose(problem, BUDGET, _shared_id_registry(), plan=PLAN_SHARED)
    naive = dataclasses.replace(
        result.solution,
        traces=tuple(dataclasses.replace(t, id=t.id.split(".", 1)[1])
                     for t in result.solution.traces),
        vias=tuple(dataclasses.replace(v, id=v.id.split(".", 1)[1])
                   for v in result.solution.vias),
    )
    assert "vcc" not in conn.analyse(problem, naive).unconnected_nets
    assert "vcc" in conn.analyse(problem, result.solution).unconnected_nets


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_per_class_completeness_is_measured_against_the_finished_board():
    result = nc.compose(
        board(), BUDGET,
        registry_of(join=Joiner("join"), dead=Joiner("dead", trace=False)),
        plan=(nc.ClassStage("power", "join", classes=("power",)),
              nc.ClassStage("rest", "dead", rest=True)),
    )
    by_label = {s.label: s for s in result.stages}
    assert by_label["power"].completeness == 1.0
    assert by_label["rest"].connected == 0
    assert result.completeness == pytest.approx(1 / 5)


def test_an_empty_class_is_reported_as_empty_rather_than_as_a_failure():
    result = nc.compose(
        board(), BUDGET, registry_of(join=Joiner("join")),
        plan=(nc.ClassStage("plane", "join", planed=True),
              nc.ClassStage("rest", "join", rest=True)),
    )
    plane = next(s for s in result.stages if s.label == "plane")
    assert plane.status == "empty"
    assert plane.completeness == 1.0 and plane.asked == 0


def test_a_router_missing_from_the_registry_costs_its_stage_and_says_so():
    """A configuration failure and a routing failure must not look alike."""
    result = nc.compose(
        board(), BUDGET, registry_of(join=Joiner("join")),
        plan=(nc.ClassStage("power", "nobody-home", classes=("power",)),
              nc.ClassStage("rest", "join", rest=True)),
    )
    power = next(s for s in result.stages if s.label == "power")
    assert power.status == "missing"
    assert "vcc" in result.solution.unrouted_nets
    assert any("not registered" in note for note in result.notes)


def test_a_family_that_raises_costs_its_stage_and_not_the_run():
    class Exploder:
        name = "boom"

        def route(self, problem, budget):
            raise RuntimeError("kaboom")

    registry = {"boom": Exploder, "join": Joiner("join").factory()}
    result = nc.compose(
        board(), BUDGET, registry,
        plan=(nc.ClassStage("power", "boom", classes=("power",)),
              nc.ClassStage("rest", "join", rest=True)),
    )
    power = next(s for s in result.stages if s.label == "power")
    assert power.status == "raised" and "kaboom" in power.detail
    assert result.completeness == pytest.approx(4 / 5)


def test_the_limiting_stage_is_the_one_with_the_most_open_nets():
    result = nc.compose(
        board(), BUDGET,
        registry_of(join=Joiner("join"), dead=Joiner("dead", trace=False)),
        plan=(nc.ClassStage("power", "dead", classes=("power",)),
              nc.ClassStage("rest", "dead", rest=True)),
    )
    limit = result.limiting_stage()
    assert limit is not None and limit.label == "rest"


def test_the_stage_table_names_every_stage_and_the_limiting_one():
    result = nc.compose(
        board(), BUDGET,
        registry_of(join=Joiner("join"), dead=Joiner("dead", trace=False)),
        plan=(nc.ClassStage("power", "join", classes=("power",)),
              nc.ClassStage("rest", "dead", rest=True)),
    )
    table = result.table()
    assert "power" in table and "rest" in table
    assert "limiting stage: rest" in table


# ---------------------------------------------------------------------------
# The composition as a router
# ---------------------------------------------------------------------------


def test_the_composition_is_a_router_and_carries_a_stable_name():
    router = nc.NetClassRouter(registry_of(join=Joiner("join")), "monolithic")
    assert router.name == "netclass[monolithic]"
    assert hasattr(router, "route")
    solution = router.route(board(), BUDGET)
    assert isinstance(solution, RoutingSolution)


def test_an_unknown_plan_name_is_refused_rather_than_defaulted():
    with pytest.raises(KeyError, match="unknown plan"):
        nc.compose(board(), BUDGET, {}, plan="does-not-exist")


def test_the_budget_and_the_counters_add_up_across_stages():
    result = nc.compose(
        board(), BUDGET, registry_of(join=Joiner("join")),
        plan=(nc.ClassStage("power", "join", classes=("power",)),
              nc.ClassStage("rest", "join", rest=True)),
    )
    assert result.solution.iterations == 2  # one per stage that ran


def test_the_same_board_composes_to_the_same_copper_twice():
    registry = registry_of(join=Joiner("join"))
    first = nc.compose(board(), BUDGET, registry, plan="monolithic")
    second = nc.compose(board(), BUDGET, registry, plan="monolithic")
    assert first.solution.fingerprint() == second.solution.fingerprint()


# ---------------------------------------------------------------------------
# Against a real family, on a real instance
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def algorithms():
    from routerlib.compositions.registry import load_algorithms

    registry = load_algorithms()
    if "maze-astar" not in registry or "plane-and-classes" not in registry:
        pytest.skip("the algorithm families are not on disk")
    return registry


def _instance(name: str):
    from pathlib import Path

    from routerlib.bench import INSTANCE_DIR, load_instance

    path = Path(INSTANCE_DIR) / f"{name}.json"
    if not path.exists():
        pytest.skip(f"instance {name} is not on disk")
    return load_instance(path)


def test_a_real_plan_over_a_real_instance_routes_and_reports_every_stage(algorithms):
    problem = _instance("matrix-ldo-3v3__usb-c-power")
    budget = Budget(max_iterations=200_000, max_nodes=2_000_000, seed=0)
    result = nc.compose(problem, budget, algorithms, plan="pairs")
    labels = [s.label for s in result.stages]
    assert labels == ["plane", "pair", "rest"]
    assert sum(s.asked for s in result.stages) == len(problem.routable_nets)
    assert result.completeness > 0.0
    ids = [t.id for t in result.solution.traces] + [
        v.id for v in result.solution.vias
    ]
    assert len(ids) == len(set(ids))


def test_the_plane_stage_takes_the_planed_net_off_the_general_router(algorithms):
    """The one place this composition deletes work instead of moving it."""
    problem = _instance("matrix-ldo-3v3__rp2040-core__usb-c-power-plane")
    budget = Budget(max_iterations=200_000, max_nodes=2_000_000, seed=0)
    result = nc.compose(problem, budget, algorithms, plan="pairs")
    buckets = dict(result.partition.buckets)
    planed = {p.net for p in problem.planes}
    assert set(buckets["plane"]) == planed
    assert not planed & set(buckets["rest"])
    plane_stage = next(s for s in result.stages if s.label == "plane")
    assert plane_stage.vias > 0, "a plane stage that places no via did nothing"


def test_a_real_composition_is_deterministic(algorithms):
    problem = _instance("matrix-ldo-3v3__usb-c-power")
    budget = Budget(max_iterations=200_000, max_nodes=2_000_000, seed=0)
    runs = [
        nc.compose(problem, budget, algorithms, plan="pairs").solution
        for _ in range(2)
    ]
    assert runs[0].fingerprint() == runs[1].fingerprint()
