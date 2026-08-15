"""Tests for the metaheuristic family.

The bar these tests hold is not "the router routes something". It is the three
properties a tournament entrant has to have before its score means anything:

* **it never emits copper the scorer would fail** — every trace and via it
  places is re-measured with the same DRC the harness scores with,
* **it is deterministic** — byte-identical copper and byte-identical
  circuit.json on a second run with the same seed,
* **it does not lie about what it finished** — the ``complete`` flag agrees
  with connectivity recomputed from the copper.

The grid is checked separately against the workspace, because the grid is the
one place in this family where an approximation lives, and an approximation
that is optimistic in the wrong direction is how a plausible illegal board gets
built.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms"))

import metaheuristic as meta  # noqa: E402

from routerlib import scoring  # noqa: E402
from routerlib.bench import INSTANCE_DIR, load_instance  # noqa: E402
from routerlib.connectivity import analyse  # noqa: E402
from routerlib.drc import check  # noqa: E402
from routerlib.geometry import disc_capsule  # noqa: E402
from routerlib.model import (  # noqa: E402
    BOTTOM,
    TOP,
    Budget,
    Point,
    RoutingSolution,
)
from routerlib.workspace import Workspace  # noqa: E402

#: Small enough that the whole file runs in seconds on a shared machine, and
#: varied enough to cover a plane, a real outline, a fine pitch and a trivial
#: two-pad net.
SMALL = (
    "matrix-status-led",
    "matrix-status-led__ws2812-chain",
    "matrix-ldo-3v3__usb-c-power",
    "matrix-i2c-bus__ldo-3v3__usb-c-power",
)


def load(name: str):
    return load_instance(INSTANCE_DIR / f"{name}.json")


@pytest.fixture(scope="module")
def tiny():
    return load("matrix-status-led__ws2812-chain")


# ---------------------------------------------------------------------------
# The genome
# ---------------------------------------------------------------------------


def test_default_genome_is_the_problems_own_order():
    genome = meta.default_genome(5)
    assert genome.order == (0, 1, 2, 3, 4)
    assert set(genome.layer) == {0}
    assert set(genome.topo) == {0}


def test_crossover_returns_a_permutation():
    import random

    rng = random.Random(7)
    a = meta.Genome((0, 1, 2, 3, 4, 5), (0,) * 6, (0,) * 6)
    b = meta.Genome((5, 4, 3, 2, 1, 0), (1,) * 6, (2,) * 6)
    for _ in range(50):
        child = meta._crossover(rng, a, b, 6)
        assert sorted(child.order) == list(range(6)), child.order
        assert len(child.layer) == 6 and len(child.topo) == 6


def test_crossover_keeps_a_contiguous_run_of_one_parent():
    """Order crossover exists to preserve sub-orders. If a child never shares a
    run with a parent, the operator has degenerated into a shuffle."""
    import random

    rng = random.Random(3)
    a = meta.Genome(tuple(range(12)), (0,) * 12, (0,) * 12)
    b = meta.Genome(tuple(reversed(range(12))), (0,) * 12, (0,) * 12)
    shared = 0
    for _ in range(30):
        child = meta._crossover(rng, a, b, 12)
        runs = [
            i
            for i in range(11)
            if child.order[i + 1] - child.order[i] == 1
        ]
        shared += len(runs)
    assert shared > 30


def test_mutation_prefers_a_broken_net():
    """Moves are informed: a proposal that touches an already-connected net is
    mostly a wasted evaluation."""
    import random

    rng = random.Random(11)
    genome = meta.default_genome(8)
    index_of = {"broken": 6}
    touched = []
    for _ in range(200):
        child = meta._mutate(rng, genome, ("broken",), index_of, 8)
        moved = [i for i in range(8) if child.order[i] != genome.order[i]]
        if moved:
            touched.append(6 in child.order[: min(moved) + 1] or 6 in moved)
    assert sum(touched) > len(touched) * 0.4


def test_topologies_all_span_the_pads(tiny):
    net = next(n for n in tiny.routable_nets if len(n.pads) >= 3)
    pads = tiny.pads_of(net.id)
    for topo in range(meta.TOPOLOGIES):
        edges = meta._tree_edges(pads, topo)
        assert len(edges) == len(pads) - 1
        seen = {pads[0].id}
        for _ in edges:  # a tree connects everything, whatever order we walk
            for a, b in edges:
                if a.id in seen or b.id in seen:
                    seen |= {a.id, b.id}
        assert seen == {p.id for p in pads}, topo


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def test_grid_fits_the_cell_budget_and_covers_the_board():
    for name in ("terminal-keyboard", "matrix-status-led", "harness-puck"):
        problem = load(name)
        grid, _ = meta._static_for(problem)
        assert grid.n <= meta.CELL_BUDGET * 1.05, (name, grid.n)
        x0, y0, x1, y1 = problem.board.bbox
        for point in (Point(x0, y0), Point(x1, y1)):
            cell = grid.cell_of(point)
            assert 0 <= cell < grid.n
            assert grid.center(cell).distance_to(point) < grid.pitch * 1.5


def test_grid_cell_indexing_round_trips():
    problem = load("matrix-status-led")
    grid, _ = meta._static_for(problem)
    for cell in (0, 1, grid.nx, grid.n - 1, grid.n // 2):
        assert grid.cell_of(grid.center(cell)) == cell
        assert grid.xof[cell] == cell % grid.nx
        assert grid.yof[cell] == cell // grid.nx


def test_a_cell_the_grid_calls_free_is_not_inside_a_foreign_pad():
    """The grid may be pessimistic. It may not be *optimistic about copper* —
    a cell it hands to net A must not be sitting on net B's pad."""
    problem = load("matrix-ldo-3v3__usb-c-power")
    grid, static = meta._static_for(problem)
    field = meta._Field(problem, grid, static)
    net = problem.routable_nets[0]
    index = static.net_index[net.id]
    free = field.free_nodes(meta._THIN, index)
    ws = Workspace(problem)
    checked = 0
    for pad in problem.pads:
        if pad.net == net.id or pad.net is None:
            continue
        for cell in grid.cells_near(
            disc_capsule(pad.center.x, pad.center.y, 0.0), 0.05
        ).tolist():
            for slot, layer in enumerate(meta.LAYERS):
                if layer not in pad.layers:
                    continue
                assert not free[cell * 2 + slot], (pad.id, cell, layer)
                checked += 1
    assert checked > 0


def test_static_field_is_not_mutated_by_routing():
    """The per-candidate field is a copy. If a candidate could write through to
    the cache, the second run of a determinism check would route a different
    board — the exact failure the harness is built to catch."""
    problem = load("matrix-status-led__ws2812-chain")
    grid, static = meta._static_for(problem)
    before = [array.copy() for array in static.own[meta._THIN]]
    meta.GreedyMazeRouter().route(problem, Budget(seed=0))
    after = static.own[meta._THIN]
    for was, now in zip(before, after):
        assert (was == now).all()


# ---------------------------------------------------------------------------
# The inner router
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SMALL)
def test_greedy_emits_no_drc_errors(name):
    """Every piece of copper is workspace-approved before it is committed, so
    the DRC the harness scores with has to agree."""
    problem = load(name)
    solution = meta.GreedyMazeRouter().route(problem, Budget(seed=0))
    result = check(problem, solution)
    assert result.errors == (), [v.detail for v in result.errors]


@pytest.mark.parametrize("name", SMALL)
def test_greedy_never_claims_more_than_it_connected(name):
    problem = load(name)
    solution = meta.GreedyMazeRouter().route(problem, Budget(seed=0))
    linked = analyse(problem, solution)
    assert solution.complete == (linked.completeness >= 1.0)
    assert set(solution.unrouted_nets) == set(linked.unconnected_nets)


def test_the_maze_beats_patterns_alone_on_a_congested_board():
    """The maze is not decoration. If turning it off changed nothing, the whole
    inner router would be the baseline with extra steps."""
    problem = load("matrix-status-led__ws2812-chain")
    with_maze = meta.GreedyMazeRouter().route(problem, Budget(seed=0))
    without = meta.PatternGreedyRouter().route(problem, Budget(seed=0))
    linked_with = analyse(problem, with_maze).completeness
    linked_without = analyse(problem, without).completeness
    assert linked_with > linked_without


def test_every_trace_is_on_one_layer_and_has_the_nets_width():
    problem = load("matrix-i2c-bus__ldo-3v3__usb-c-power")
    solution = meta.GreedyMazeRouter().route(problem, Budget(seed=0))
    widths = {n.id: n.min_width_mm for n in problem.nets}
    for trace in solution.traces:
        assert trace.layer in (TOP, BOTTOM)
        assert len(trace.points) >= 2
        assert trace.width_mm >= widths[trace.net] - 1e-9
        assert trace.width_mm >= problem.rules.min_trace_mm - 1e-9


def test_no_via_lands_in_an_smd_pad():
    for name in SMALL:
        problem = load(name)
        solution = meta.GreedyMazeRouter().route(problem, Budget(seed=0))
        result = check(problem, solution, use_pipeline=False)
        assert not [v for v in result.violations if v.kind == "via_in_pad"]


def test_simplify_refuses_rather_than_returning_illegal_copper():
    """A simplifier that cannot find a legal shortcut has exactly two honest
    answers: the original polyline, or nothing. Never a shorter illegal one."""
    problem = load("matrix-status-led")
    ws = Workspace(problem)
    net = problem.routable_nets[0]
    pad = problem.pads_of(net.id)[0]
    outside = Point(problem.board.bbox[0] - 50.0, problem.board.bbox[1] - 50.0)
    assert meta._simplify(ws, TOP, (pad.center, outside), 0.2, net.id) is None


def test_split_runs_turns_layer_changes_into_vias():
    # node = cell * 2 + layer; two cells on top, a via, two cells on bottom.
    path = [10 * 2 + 0, 11 * 2 + 0, 11 * 2 + 1, 12 * 2 + 1]
    runs, vias = meta._split_runs(path)
    assert [slot for slot, _ in runs] == [0, 1]
    assert [cells for _, cells in runs] == [[10, 11], [11, 12]]
    assert vias == [11]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_anneal_never_returns_worse_than_its_starting_point():
    """The annealer keeps the best candidate on the harness key, so its answer
    is bounded below by the greedy run it started from."""
    problem = load("matrix-status-led__ws2812-chain")
    budget = Budget(seed=0, max_nodes=400_000)
    greedy = meta.GreedyMazeRouter().route(problem, budget)
    annealed = meta.AnnealRouter(evaluations=8).route(problem, budget)
    assert scoring.score(problem, annealed).key() <= scoring.score(
        problem, greedy
    ).key()


def test_genetic_never_returns_worse_than_its_seed():
    problem = load("matrix-ldo-3v3__usb-c-power")
    budget = Budget(seed=0, max_nodes=400_000)
    greedy = meta.GreedyMazeRouter().route(problem, budget)
    evolved = meta.GeneticRouter(population=4, generations=1).route(problem, budget)
    assert scoring.score(problem, evolved).key() <= scoring.score(
        problem, greedy
    ).key()


def test_search_respects_the_node_budget():
    """The budget is counted, never timed. A router asked for a thousand
    candidates on a twenty-thousand-node budget stops at the budget, and the
    overshoot is bounded by the cap one edge may spend."""
    problem = load("matrix-rp2040-core__usb-c-data")
    budget = Budget(seed=0, max_nodes=20_000, max_iterations=10_000_000)
    solution = meta.AnnealRouter(evaluations=1_000).route(problem, budget)
    assert solution.nodes_expanded >= 20_000
    assert solution.nodes_expanded < 20_000 + meta._CANDIDATE_NODE_CAP


def test_a_different_genome_can_produce_different_copper():
    """If order did not matter, this family would have nothing to search."""
    problem = load("matrix-rp2040-core__usb-c-data")
    inner = meta.InnerRouter()
    budget = Budget(seed=0)
    count = len(problem.routable_nets)
    first, _ = inner.route(problem, meta.default_genome(count), budget.meter())
    reversed_order = meta.Genome(
        order=tuple(reversed(range(count))),
        layer=(0,) * count,
        topo=(0,) * count,
    )
    second, _ = inner.route(problem, reversed_order, budget.meter())
    assert first.fingerprint() != second.fingerprint()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        meta.GreedyMazeRouter,
        lambda: meta.AnnealRouter(evaluations=6),
        lambda: meta.GeneticRouter(population=4, generations=1),
    ],
    ids=["maze-greedy", "meta-anneal", "meta-genetic"],
)
def test_same_input_same_output(factory):
    problem = load("matrix-status-led__ws2812-chain")
    budget = Budget(seed=0, max_nodes=400_000)
    result = scoring.determinism_check(factory(), problem, budget, runs=2)
    assert result.deterministic, result.detail


def test_a_different_seed_is_allowed_to_differ_but_a_repeat_is_not():
    problem = load("matrix-ldo-3v3__usb-c-power")
    one = meta.AnnealRouter(evaluations=12).route(problem, Budget(seed=1))
    again = meta.AnnealRouter(evaluations=12).route(problem, Budget(seed=1))
    assert one.fingerprint() == again.fingerprint()


def test_the_inner_router_is_a_pure_function_of_the_genome():
    problem = load("matrix-ldo-3v3__usb-c-power")
    genome = meta.Genome(
        order=(3, 1, 4, 0, 2), layer=(1, 0, 1, 0, 1), topo=(2, 0, 1, 3, 0)
    )
    inner = meta.InnerRouter()
    first, _ = inner.route(problem, genome, Budget(seed=0).meter())
    second, _ = inner.route(problem, genome, Budget(seed=99).meter())
    assert first.fingerprint() == second.fingerprint()


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_every_router_in_the_registry_answers_the_interface():
    problem = load("matrix-status-led")
    for name, factory in sorted(meta.ROUTERS.items()):
        router = factory()
        assert router.name == name
        solution = router.route(problem, Budget(seed=0, max_nodes=100_000))
        assert isinstance(solution, RoutingSolution)
        assert solution.router == name
        assert solution.iterations >= 0


def test_the_empty_board_is_left_alone():
    """A problem with nothing routable is finished before it starts, and the
    honest answer is no copper rather than a via somewhere hopeful."""
    problem = load("matrix-i2c-bus")
    stripped = problem.__class__(
        id=problem.id,
        board=problem.board,
        rules=problem.rules,
        pads=(),
        drills=problem.drills,
        keepouts=problem.keepouts,
        planes=(),
        nets=(),
    )
    solution = meta.GreedyMazeRouter().route(stripped, Budget(seed=0))
    assert solution.traces == ()
    assert solution.vias == ()
    assert solution.complete is True
