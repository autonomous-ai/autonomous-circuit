"""Tests for the exact-and-structured router.

The interesting ones are the last two groups. Everything above them checks that
the router produces legal copper; those check the two claims the family is
actually making — that the conflict test is the same geometry the scorer uses,
and that the branch-and-bound really does return the optimum and not a good
answer with a confident name. The optimality tests do it the only way worth
doing: against brute force, on hundreds of random instances small enough to
enumerate completely.
"""

from __future__ import annotations

import importlib.util
import math
import random
import sys
from pathlib import Path

import pytest

from routerfix import RULES, pad, two_pad_board
from routerlib import drc as drc_mod
from routerlib.geometry import segment_capsule
from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    Budget,
    DesignRules,
    Drill,
    Keepout,
    Net,
    Pad,
    Plane,
    Point,
    RoutingProblem,
)
from routerlib.scoring import determinism_check, score

ALGORITHM = (
    Path(__file__).resolve().parents[1] / "algorithms" / "exact-and-structured.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("exact_and_structured", ALGORITHM)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules and a module that is not there yet fails to build a field.
    sys.modules["exact_and_structured"] = module
    spec.loader.exec_module(module)
    return module


exs = _load()


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_implements_the_router_protocol():
    from routerlib.model import Router

    router = exs.ExactStructuredRouter()
    assert isinstance(router, Router)
    assert router.name == "exact-and-structured"


def test_routes_the_simplest_board_with_no_via():
    problem = two_pad_board(gap_mm=8.0)
    solution = exs.ExactStructuredRouter().route(problem, Budget(seed=0))
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.errors == 0
    assert result.quality.via_count == 0
    assert solution.complete is True


def test_honest_when_it_cannot_finish():
    """A pad walled in by a keepout is unroutable, and saying so is the job."""
    problem = two_pad_board(
        gap_mm=8.0,
        keepouts=(
            Keepout(id="k", center=Point(0.0, 0.0), width_mm=3.0, height_mm=18.0),
        ),
    )
    solution = exs.ExactStructuredRouter().route(problem, Budget(seed=0))
    result = score(problem, solution)
    assert solution.complete is False
    assert solution.unrouted_nets == ("N1",)
    assert result.completeness == 0.0
    assert result.errors == 0  # it emitted nothing rather than something illegal
    assert result.claim_honest


def test_never_places_copper_in_a_keepout_or_off_the_board():
    problem = two_pad_board(
        gap_mm=14.0,
        keepouts=(
            Keepout(id="k", center=Point(0.0, 0.0), width_mm=2.0, height_mm=2.0),
        ),
    )
    solution = exs.ExactStructuredRouter().route(problem, Budget(seed=0))
    result = drc_mod.check(problem, solution, use_pipeline=False)
    assert [v for v in result.errors] == []


def test_uses_the_second_layer_when_the_first_is_blocked():
    """A wall of foreign pads right across the board, gaps 0.3mm wide — too
    narrow for a 0.2mm track and two clearances. The only way across is down
    and back up, which costs two vias, and it takes them rather than losing the
    net."""
    wall = tuple(
        pad(f"w{i}", None, 0.0, -9.0 + i * 1.5, w=1.2, h=1.2, component="W")
        for i in range(13)
    )
    problem = two_pad_board(gap_mm=12.0, extra_pads=wall)
    solution = exs.ExactStructuredRouter().route(problem, Budget(seed=0))
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.errors == 0
    assert result.quality.via_count == 2
    assert {t.layer for t in solution.traces} == {TOP, BOTTOM}


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_input_same_output():
    problem = two_pad_board(gap_mm=9.0)
    check = determinism_check(
        exs.ExactStructuredRouter(), problem, Budget(seed=0), runs=3
    )
    assert check.deterministic, check.detail


def test_seed_changes_nothing_because_nothing_is_random():
    problem = two_pad_board(gap_mm=9.0)
    router = exs.ExactStructuredRouter()
    a = router.route(problem, Budget(seed=0))
    b = router.route(problem, Budget(seed=12345))
    assert a.fingerprint() == b.fingerprint()


def test_deterministic_on_a_real_instance():
    from routerlib.bench import INSTANCE_DIR, load_instance

    path = INSTANCE_DIR / "matrix-status-led__ws2812-chain.json"
    if not path.exists():  # pragma: no cover - fixtures are committed
        pytest.skip("instance fixtures not built")
    problem = load_instance(path)
    check = determinism_check(
        exs.ExactStructuredRouter(), problem, Budget(seed=0), runs=2
    )
    assert check.deterministic, check.detail


# ---------------------------------------------------------------------------
# It designs above the warning line, not above the error line
# ---------------------------------------------------------------------------


def test_tiers_sit_above_every_warning_threshold():
    problem = two_pad_board()
    tiers = exs._tiers(problem)
    assert len(tiers) == 2
    assert tiers[0].clearance_mm == pytest.approx(problem.rules.target_clearance_mm)
    assert tiers[1].clearance_mm > problem.rules.min_clearance_mm
    for tier in tiers:
        # The fab *errors* below 0.28 to a plated hole and 0.20 to the edge, and
        # *warns* below 0.35 and 0.30. A router that designs to the error floor
        # ships a board full of warnings.
        assert tier.rules.min_pth_to_copper_mm >= 0.35 - 1e-9
        assert tier.rules.min_edge_clearance_mm >= 0.30 - 1e-9


def test_a_rail_necks_down_rather_than_losing_the_net():
    rails = Net(
        id="P", name="V3_3", net_class="power", pads=("a", "b"), min_width_mm=0.5
    )
    widths = exs._widths_to_try(rails, 0.5, RULES)
    assert widths[0] == 0.5
    assert widths[-1] < 0.5
    assert min(widths) >= RULES.warn_trace_mm
    signal = Net(
        id="S", name="SIG", net_class="signal", pads=("a", "b"), min_width_mm=0.2
    )
    assert exs._widths_to_try(signal, 0.2, RULES) == [0.2]


# ---------------------------------------------------------------------------
# The structured half
# ---------------------------------------------------------------------------


def _matrix_row_problem() -> RoutingProblem:
    """Four pads on one line with a blocking pad between each neighbouring
    pair — the key-matrix shape, in miniature. Straight through is a short; the
    answer is a track beside the line with four stubs."""
    row = []
    others = []
    for i in range(4):
        row.append(pad(f"r{i}", "ROW", -12.0 + i * 8.0, 0.0, w=1.0, h=0.75,
                       component=f"SW{i}"))
        others.append(
            pad(f"k{i}", f"K{i}", -8.0 + i * 8.0, 0.0, w=1.0, h=0.75,
                component=f"SW{i}")
        )
    nets = (
        Net(id="ROW", name="ROW0", net_class="signal",
            pads=tuple(p.id for p in row), min_width_mm=0.2),
    ) + tuple(
        Net(id=f"K{i}", name=f"K{i}", net_class="signal", pads=(f"k{i}",),
            min_width_mm=0.2)
        for i in range(4)
    )
    return RoutingProblem(
        id="matrix-row",
        board=Board(width_mm=40.0, height_mm=20.0),
        rules=RULES,
        pads=tuple(row + others),
        nets=nets,
    )


def test_a_line_of_pads_becomes_one_spine():
    problem = _matrix_row_problem()
    tiers = exs._tiers(problem)
    factory = exs.Candidates(problem, tiers)
    tasks, widths, _ = exs._build_tasks(problem)
    row_tasks = [t for t in tasks if t.net == "ROW"]
    assert len(row_tasks) == 3
    spines = factory.spines(
        problem.nets_by_id["ROW"], row_tasks, [0.2], Budget().meter()
    )
    assert spines, "a straight row of pads should produce a spine"
    best = spines[0]
    assert best.kind == "spine"
    # One decision covering all three connections is the whole point.
    assert set(best.tasks) == {t.index for t in row_tasks}
    # One track plus one stub per pad.
    assert len(best.pieces) == 5
    assert best.vias == ()


def test_the_spine_actually_routes_the_row():
    problem = _matrix_row_problem()
    solution = exs.ExactStructuredRouter().route(problem, Budget(seed=0))
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.errors == 0
    assert result.quality.via_count == 0


# ---------------------------------------------------------------------------
# The plane
# ---------------------------------------------------------------------------


def test_a_plane_is_stitched_not_traced():
    """A ground plane means one via per pad and no pad-to-pad copper. The
    router we ship cannot express this: it saw a pour as obstacles."""
    pads = tuple(
        pad(f"g{i}", "GND", -6.0 + i * 4.0, 0.0, w=1.0, h=1.0, component=f"C{i}")
        for i in range(4)
    )
    outline = (
        Point(-9.5, -9.5), Point(9.5, -9.5), Point(9.5, 9.5), Point(-9.5, 9.5),
    )
    problem = RoutingProblem(
        id="planed",
        board=Board(width_mm=20.0, height_mm=20.0),
        rules=RULES,
        pads=pads,
        nets=(
            Net(id="GND", name="GND", net_class="ground",
                pads=tuple(p.id for p in pads), min_width_mm=0.5),
        ),
        planes=(
            Plane(id="pour", net="GND", layer=BOTTOM, outline=outline),
        ),
    )
    solution = exs.ExactStructuredRouter().route(problem, Budget(seed=0))
    result = score(problem, solution)
    assert result.completeness == 1.0
    assert result.errors == 0
    assert len(solution.vias) == 4
    # Stubs only: no pad-to-pad run anywhere near the 4mm pitch.
    assert max(t.length_mm for t in solution.traces) < 2.0


# ---------------------------------------------------------------------------
# The conflict test is geometry, and it is the scorer's geometry
# ---------------------------------------------------------------------------


def _fake_candidate(index, net, tasks, x0, y0, x1, y1, width=0.2, cost=1.0,
                    tier=0):
    piece = exs.Piece(TOP, (Point(x0, y0), Point(x1, y1)), width)
    return exs.Candidate(
        index=index,
        net=net,
        tasks=tuple(tasks),
        pieces=(piece,),
        vias=(),
        tier=tier,
        kind="pattern",
        cost=cost,
        shapes=exs._capsules_of((piece,), (), 0.6),
    )


def test_conflicts_are_found_exactly_at_the_clearance_line():
    problem = two_pad_board()
    tiers = exs._tiers(problem)
    clearance = tiers[0].clearance_mm
    width = 0.2
    # Two parallel 0.2mm tracks: gap = pitch - width.
    for pitch, expect in (
        (width + clearance - 0.01, True),
        (width + clearance + 0.01, False),
    ):
        a = _fake_candidate(1, "A", (0,), -5.0, 0.0, 5.0, 0.0, width)
        b = _fake_candidate(2, "B", (1,), -5.0, pitch, 5.0, pitch, width)
        conflicts = exs.conflict_sets([a, b], tiers)
        assert (1 in conflicts[0]) is expect, (pitch, expect)


def test_same_net_copper_never_conflicts():
    problem = two_pad_board()
    tiers = exs._tiers(problem)
    a = _fake_candidate(1, "A", (0,), -5.0, 0.0, 5.0, 0.0)
    b = _fake_candidate(2, "A", (1,), -5.0, 0.0, 5.0, 0.0)
    conflicts = exs.conflict_sets([a, b], tiers)
    assert conflicts[0] == set() and conflicts[1] == set()


def test_committed_copper_agrees_with_the_scorer():
    """Everything the router emits on a real instance passes the scorer's own
    DRC. This is the property that makes a completeness number meaningful."""
    from routerlib.bench import INSTANCE_DIR, load_instance

    path = INSTANCE_DIR / "matrix-ldo-3v3__usb-c-power.json"
    if not path.exists():  # pragma: no cover
        pytest.skip("instance fixtures not built")
    problem = load_instance(path)
    solution = exs.ExactStructuredRouter().route(problem, Budget(seed=0))
    result = score(problem, solution)
    assert result.errors == 0, result.error_kinds


# ---------------------------------------------------------------------------
# The exact half, against brute force
# ---------------------------------------------------------------------------


def _brute_force(tasks, options, cands, conflicts, net_of, broken_already):
    """Every assignment, enumerated. Only usable because it is tiny — which is
    the whole point of checking an optimiser this way."""
    best = math.inf
    order = sorted(tasks)

    def walk(i, chosen, cost, broken):
        nonlocal best
        if cost >= best:
            return
        if i == len(order):
            best = min(best, cost)
            return
        task = order[i]
        for position in options[task]:
            if any(position in conflicts[other] for other in chosen):
                continue
            walk(i + 1, chosen + [position], cost + cands[position].cost, broken)
        net = net_of[task]
        penalty = 0.0 if net in broken else exs.NET_PENALTY
        walk(i + 1, chosen, cost + penalty, broken | {net})

    walk(0, [], 0.0, frozenset(broken_already))
    return best


@pytest.mark.parametrize("trial", range(60))
def test_the_search_returns_the_true_optimum(trial):
    """Random tiny ILPs, solved both ways. If the branch-and-bound ever returns
    a cost the exhaustive search can beat, the word "optimal" in this file is a
    lie, and this test is the only thing standing between that and the report.
    """
    rng = random.Random(trial)
    task_count = rng.randint(2, 5)
    tasks = list(range(task_count))
    net_of = {t: f"N{rng.randint(0, max(0, task_count - 2))}" for t in tasks}
    cands = []
    options = {}
    index = 0
    for task in tasks:
        picks = []
        for _ in range(rng.randint(1, 3)):
            cands.append(
                exs.Candidate(
                    index=index,
                    net=net_of[task],
                    tasks=(task,),
                    pieces=(),
                    vias=(),
                    tier=0,
                    kind="pattern",
                    cost=float(rng.randint(1, 40)),
                    shapes=(),
                )
            )
            picks.append(index)
            index += 1
        options[task] = tuple(picks)
    conflicts = [set() for _ in cands]
    for a in range(len(cands)):
        for b in range(a + 1, len(cands)):
            if cands[a].net == cands[b].net:
                continue
            if rng.random() < 0.45:
                conflicts[a].add(b)
                conflicts[b].add(a)

    selector = exs.ExactSelector([], [], exs._tiers(two_pad_board()),
                                 Budget().meter())
    assign, cost, proved, _ = selector._search(
        tasks, options, cands, conflicts, net_of, set(), 10_000_000
    )
    assert proved, "the search must close on an instance this small"
    expected = _brute_force(tasks, options, cands, conflicts, net_of, set())
    assert cost == pytest.approx(expected), (trial, cost, expected)
    # And the assignment it returns must really be legal and really cost that.
    used = sorted(set(assign.values()))
    for i, a in enumerate(used):
        for b in used[i + 1:]:
            assert b not in conflicts[a]
    broken = {net_of[t] for t in tasks if t not in assign}
    recomputed = sum(cands[p].cost for p in used) + exs.NET_PENALTY * len(broken)
    assert recomputed == pytest.approx(cost)


def test_the_solver_beats_greedy_where_greedy_has_to_lose():
    """The instance the whole family exists for.

    Connection A can take a cheap route or an expensive one. Connection B, on a
    different net, has exactly one route, and it collides with A's cheap one.
    First-fit takes the cheap route and loses B's net. The optimum spends the
    expensive route and keeps both, and it is 1e9 better on the objective.
    """
    net_of = {0: "A", 1: "B"}
    cheap = exs.Candidate(0, "A", (0,), (), (), 0, "pattern", 1.0, ())
    dear = exs.Candidate(1, "A", (0,), (), (), 0, "pattern", 50.0, ())
    only = exs.Candidate(2, "B", (1,), (), (), 0, "pattern", 1.0, ())
    cands = [cheap, dear, only]
    conflicts = [{2}, set(), {0}]
    options = {0: (0, 1), 1: (2,)}
    selector = exs.ExactSelector([], [], exs._tiers(two_pad_board()),
                                 Budget().meter())
    assign, cost, proved, _ = selector._search(
        [0, 1], options, cands, conflicts, net_of, set(), 100_000
    )
    assert proved
    assert assign == {0: 1, 1: 2}
    assert cost == pytest.approx(51.0)


def test_dominance_presolve_keeps_the_optimum():
    """Deleting a column must never delete the answer. Checked by solving with
    and without the presolve and comparing the cost."""
    rng = random.Random(7)
    for trial in range(25):
        tasks = list(range(4))
        net_of = {t: f"N{t}" for t in tasks}
        cands = []
        options = {}
        index = 0
        for task in tasks:
            picks = []
            for _ in range(3):
                cands.append(
                    exs.Candidate(index, net_of[task], (task,), (), (), 0,
                                  "pattern", float(rng.randint(1, 20)), ())
                )
                picks.append(index)
                index += 1
            options[task] = tuple(picks)
        conflicts = [set() for _ in cands]
        for a in range(len(cands)):
            for b in range(a + 1, len(cands)):
                if cands[a].net != cands[b].net and rng.random() < 0.4:
                    conflicts[a].add(b)
                    conflicts[b].add(a)
        selector = exs.ExactSelector([], [], exs._tiers(two_pad_board()),
                                     Budget().meter())
        full = selector._search(
            tasks, options, cands, [set(c) for c in conflicts], net_of, set(),
            10_000_000,
        )[1]
        masks = [set(c) for c in conflicts]
        dead = selector._drop_dominated(cands, masks)
        pruned = {
            t: tuple(p for p in options[t] if p not in dead) for t in tasks
        }
        assert all(pruned[t] for t in tasks), "a task must keep an option"
        after = selector._search(
            tasks, pruned, cands, masks, net_of, set(), 10_000_000
        )[1]
        assert after == pytest.approx(full), trial


def test_a_composite_candidate_covers_all_its_connections():
    """A spine is one decision for several connections; taking it must satisfy
    all of them and taking nothing must break the net once, not three times."""
    net_of = {0: "A", 1: "A", 2: "A"}
    spine = exs.Candidate(0, "A", (0, 1, 2), (), (), 0, "spine", 30.0, ())
    singles = [
        exs.Candidate(i + 1, "A", (i,), (), (), 0, "pattern", 20.0, ())
        for i in range(3)
    ]
    cands = [spine] + singles
    conflicts = [set() for _ in cands]
    options = {i: (0, i + 1) for i in range(3)}
    selector = exs.ExactSelector([], [], exs._tiers(two_pad_board()),
                                 Budget().meter())
    assign, cost, proved, _ = selector._search(
        [0, 1, 2], options, cands, conflicts, net_of, set(), 100_000
    )
    assert proved
    assert assign == {0: 0, 1: 0, 2: 0}
    assert cost == pytest.approx(30.0)


def test_budget_is_counted_never_timed():
    problem = two_pad_board(gap_mm=9.0)
    solution = exs.ExactStructuredRouter().route(problem, Budget(seed=0))
    assert solution.iterations > 0
    assert solution.wall_clock_s >= 0.0
    # Wall clock is information, not a gate: a tiny cap must not change copper.
    tight = exs.ExactStructuredRouter().route(
        problem, Budget(seed=0, wall_clock_cap_s=1e-9)
    )
    assert tight.fingerprint() == solution.fingerprint()
