"""Spatial decomposition, and the four ways it could quietly lie.

The interesting assertions here are not "the partition looks sensible". They
are the properties that stop a composition from reporting a board it did not
build:

* **every routable net is claimed exactly once** — interior to one region or
  crossing. A net that falls between two regions is a net nobody routes, and a
  net counted by two regions is copper drawn twice;
* **stage copper ids never collide** — two stages that both mint ``v0`` merge
  into one union-find node carrying two nets, which is a connection that does
  not exist and a short that is never checked;
* **completeness never goes down across stages** — the whole point of fixing
  the boundary conditions first;
* **a board with no seam says so** and degenerates to one router, rather than
  reporting five regions and routing nothing in them.

The stub routers are deliberately dumb: straight copper, no legality. A unit
test must not need nine algorithm files on disk, and the families are covered
by the tournament.
"""

from __future__ import annotations

import dataclasses
import unittest

import routerfix
from routerlib import connectivity as conn
from routerlib.bench import instance_paths, load_instance
from routerlib.compositions import spatial
from routerlib.drc import copper_items
from routerlib.model import (
    TOP,
    Board,
    Budget,
    DesignRules,
    Net,
    Pad,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)

BUDGET = Budget(max_iterations=1_000, max_nodes=10_000, seed=0)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubRouter:
    """Joins every net's pads with straight copper and remembers the ask.

    ``via_id`` is the same for every instance on purpose: two stages minting
    the same via id is the collision the namespacing exists to stop, and a stub
    that avoided it would test nothing.
    """

    name = "stub"
    calls: list[dict] = []

    def __init__(self, label: str = "stub") -> None:
        self.label = label

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        StubRouter.calls.append(
            {
                "label": self.label,
                "nets": tuple(n.id for n in problem.routable_nets),
                "clearance": problem.rules.target_clearance_mm,
                "existing": len(problem.existing_traces),
            }
        )
        traces = []
        for index, net in enumerate(problem.routable_nets):
            pads = problem.pads_of(net.id)
            if len(pads) < 2:
                continue
            traces.append(
                Trace(
                    id=f"t{index}",
                    net=net.id,
                    layer=TOP,
                    points=tuple(p.center for p in pads),
                    width_mm=net.min_width_mm,
                )
            )
        return RoutingSolution(
            router=self.label,
            traces=tuple(traces),
            vias=(Via(id="v0", net="none", center=Point(0.0, 0.0)),),
            complete=True,
        )


class DeadRouter:
    name = "dead"

    def route(self, problem, budget):
        raise RuntimeError("this family always dies")


def registry(*names: str) -> dict:
    return {name: (lambda n=name: StubRouter(n)) for name in names}


# ---------------------------------------------------------------------------
# A board with two obvious clusters, and one wire between them
# ---------------------------------------------------------------------------


def two_cluster_board() -> RoutingProblem:
    """Two blocks of four components, 40mm apart, with one net crossing.

    Each block has an interior net; one net joins the blocks. The right answer
    is two regions, two interior nets and one crossing net, and it is obvious
    enough that a partitioner which gets it wrong is broken rather than
    differently tuned.
    """
    pads: list[Pad] = []
    nets: list[Net] = []
    for side, x0 in (("L", -30.0), ("R", 10.0)):
        for i in range(4):
            pads.append(
                routerfix.pad(f"{side}{i}a", f"{side}_LOCAL", x0 + i * 4.0, -4.0,
                              component=f"{side}U{i}")
            )
            pads.append(
                routerfix.pad(f"{side}{i}b", f"{side}_LOCAL", x0 + i * 4.0, 4.0,
                              component=f"{side}U{i}")
            )
        nets.append(
            Net(
                id=f"{side}_LOCAL", name=f"{side}_LOCAL", net_class="signal",
                pads=tuple(sorted(p.id for p in pads if p.net == f"{side}_LOCAL")),
                min_width_mm=0.2,
            )
        )
    pads.append(routerfix.pad("Lx", "SPAN", -30.0, 10.0, component="LU0"))
    pads.append(routerfix.pad("Rx", "SPAN", 22.0, 10.0, component="RU3"))
    nets.append(
        Net(id="SPAN", name="SPAN", net_class="signal", pads=("Lx", "Rx"),
            min_width_mm=0.2)
    )
    return RoutingProblem(
        id="two-cluster",
        board=Board(width_mm=90.0, height_mm=40.0),
        rules=DesignRules(),
        pads=tuple(pads),
        nets=tuple(nets),
    )


class Partitioning(unittest.TestCase):
    def setUp(self) -> None:
        StubRouter.calls = []

    def test_two_clusters_split_on_the_obvious_seam(self):
        part = spatial.partition(two_cluster_board(), min_cells=2, max_depth=1)
        self.assertTrue(part.seam, part.why)
        self.assertEqual(len(part.regions), 2)
        self.assertEqual(part.crossing_nets, ("SPAN",))
        self.assertEqual(
            sorted(n for r in part.regions for n in r.interior_nets),
            ["L_LOCAL", "R_LOCAL"],
        )
        self.assertEqual(part.cuts[0].axis, "x")

    def test_every_net_is_claimed_exactly_once(self):
        for path in instance_paths():
            problem = load_instance(path)
            part = spatial.partition(problem)
            claimed: list[str] = list(part.crossing_nets)
            for region in part.regions:
                claimed.extend(region.interior_nets)
            self.assertEqual(
                sorted(claimed),
                sorted(n.id for n in problem.routable_nets),
                f"{problem.id}: a net was lost or double-counted",
            )
            self.assertEqual(len(claimed), len(set(claimed)), problem.id)

    def test_regions_partition_the_components(self):
        for path in instance_paths():
            problem = load_instance(path)
            part = spatial.partition(problem)
            cells = spatial.cells_of(problem)
            seen: list[str] = [c for r in part.regions for c in r.cells]
            self.assertEqual(len(seen), len(set(seen)), f"{problem.id}: cell in two regions")
            self.assertEqual(set(seen), set(cells), f"{problem.id}: cell in no region")

    def test_partition_is_deterministic(self):
        for path in instance_paths():
            problem = load_instance(path)
            first = spatial.partition(problem).as_dict()
            second = spatial.partition(load_instance(path)).as_dict()
            self.assertEqual(first, second, problem.id)

    def test_balance_constraint_is_honoured(self):
        for path in instance_paths():
            problem = load_instance(path)
            part = spatial.partition(problem, balance=0.30)
            for cut in part.cuts:
                total = cut.left_pads + cut.right_pads
                self.assertGreaterEqual(min(cut.left_pads, cut.right_pads) / total, 0.30)

    def test_a_split_must_isolate_more_than_it_cuts(self):
        for path in instance_paths():
            problem = load_instance(path)
            part = spatial.partition(problem, max_cut_ratio=1.0)
            for cut in part.cuts:
                self.assertLess(cut.cut_nets, cut.inside_nets, problem.id)

    def test_no_seam_is_reported_as_an_outcome(self):
        """A board with no structure is a result, not a crash and not silence."""
        problem = routerfix.two_pad_board()
        part = spatial.partition(problem)
        self.assertFalse(part.seam)
        self.assertTrue(part.why.strip())
        self.assertEqual(len(part.regions), 1)
        self.assertEqual(part.crossing_nets, ())

    def test_character_is_measured_not_named(self):
        """The keyboard's regions are lattice; nothing here reads a refdes."""
        problem = load_instance(
            [p for p in instance_paths() if p.stem == "terminal-keyboard"][0]
        )
        part = spatial.partition(problem)
        characters = {r.character for r in part.regions}
        self.assertIn("lattice", characters)
        for region in part.regions:
            self.assertIn(region.character, spatial.EXPERTS)
            self.assertEqual(region.expert, spatial.EXPERTS[region.character])

    def test_unknown_character_falls_back_to_the_global_expert(self):
        part = spatial.partition(
            two_cluster_board(), min_cells=2, max_depth=1, experts={}
        )
        for region in part.regions:
            self.assertEqual(region.expert, spatial.GLOBAL_EXPERT)


class Routing(unittest.TestCase):
    def setUp(self) -> None:
        StubRouter.calls = []

    def test_crossings_are_routed_before_any_region(self):
        problem = two_cluster_board()
        part = spatial.partition(problem, min_cells=2, max_depth=1,
                                 experts={"open": "expert"})
        result = spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT, "expert"), given=part
        )
        self.assertEqual(result.stages[0].stage, "crossing")
        self.assertEqual(result.stages[0].asked_nets, 1)
        self.assertEqual([s.stage for s in result.stages[1:]], ["r0", "r1"])

    def test_stage_copper_ids_never_collide(self):
        """Two stages both mint ``v0``; one node with two nets is a false
        connection and an unchecked short at the same time."""
        problem = two_cluster_board()
        part = spatial.partition(problem, min_cells=2, max_depth=1)
        result = spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT), given=part
        )
        ids = [item.id for item in copper_items(problem, result.solution)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(
            sum(1 for v in result.solution.vias if v.id.endswith(".v0")), 2
        )

    def test_completeness_never_goes_down_across_stages(self):
        problem = two_cluster_board()
        result = spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT),
            partition_kwargs={"min_cells": 2, "max_depth": 1},
        )
        seen = [s.completeness for s in result.stages]
        self.assertEqual(seen, sorted(seen))
        self.assertGreaterEqual(min(s.added_nets for s in result.stages), 0)

    def test_a_region_only_ever_sees_its_own_nets(self):
        problem = two_cluster_board()
        part = spatial.partition(problem, min_cells=2, max_depth=1)
        spatial.route(problem, BUDGET, registry(spatial.GLOBAL_EXPERT), given=part)
        asked = [call["nets"] for call in StubRouter.calls]
        self.assertEqual(asked[0], ("SPAN",))
        self.assertEqual(sorted(n for a in asked[1:] for n in a),
                         ["L_LOCAL", "R_LOCAL"])

    def test_regions_see_the_copper_already_down(self):
        problem = two_cluster_board()
        spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT),
            partition_kwargs={"min_cells": 2, "max_depth": 1},
        )
        self.assertEqual(StubRouter.calls[0]["existing"], 0)
        for call in StubRouter.calls[1:]:
            self.assertGreater(call["existing"], 0)

    def test_boundary_clearance_reaches_the_global_stage_only(self):
        problem = two_cluster_board()
        base = problem.rules.target_clearance_mm
        spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT),
            boundary_clearance=1.5,
            partition_kwargs={"min_cells": 2, "max_depth": 1},
        )
        self.assertAlmostEqual(StubRouter.calls[0]["clearance"], base * 1.5, places=6)
        for call in StubRouter.calls[1:]:
            self.assertAlmostEqual(call["clearance"], base, places=6)

    def test_no_seam_sends_everything_to_one_router(self):
        problem = routerfix.two_pad_board()
        result = spatial.route(problem, BUDGET, registry(spatial.GLOBAL_EXPERT))
        self.assertFalse(result.partition.seam)
        self.assertEqual(len(result.stages), 1)
        self.assertEqual(result.stages[0].router, spatial.GLOBAL_EXPERT)
        self.assertTrue(any("no seam" in n for n in result.notes))

    def test_a_family_that_dies_costs_a_stage_and_says_so(self):
        problem = two_cluster_board()
        reg = dict(registry(spatial.GLOBAL_EXPERT))
        reg["expert"] = DeadRouter
        part = spatial.partition(problem, min_cells=2, max_depth=1,
                                 experts={"open": "expert"})
        result = spatial.route(problem, BUDGET, reg, given=part)
        self.assertTrue(any("RuntimeError" in n for n in result.notes))
        self.assertEqual([s.stage for s in result.stages], ["crossing"])

    def test_a_family_that_is_missing_is_not_a_family_that_failed(self):
        problem = two_cluster_board()
        part = spatial.partition(problem, min_cells=2, max_depth=1,
                                 experts={"open": "nobody"})
        result = spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT), given=part
        )
        self.assertTrue(any("not registered" in n or "KeyError" in n
                            for n in result.notes), result.notes)

    def test_escape_first_puts_the_fine_pitch_regions_before_the_crossings(self):
        problem = two_cluster_board()
        part = spatial.partition(problem, min_cells=2, max_depth=1)
        part = dataclasses.replace(
            part,
            regions=(dataclasses.replace(part.regions[0], character="fine-pitch"),
                     part.regions[1]),
        )
        result = spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT), given=part,
            escape_first=True,
        )
        self.assertEqual([s.stage for s in result.stages], ["r0", "crossing", "r1"])
        # And exactly one turn each: two stages under one label would
        # double-count the nets asked.
        self.assertEqual(len({s.stage for s in result.stages}), len(result.stages))

    def test_crossing_chain_retries_before_the_regions_take_the_space(self):
        problem = two_cluster_board()
        part = spatial.partition(problem, min_cells=2, max_depth=1)
        reg = dict(registry("second"))
        reg[spatial.GLOBAL_EXPERT] = DeadRouter
        result = spatial.route(
            problem, BUDGET, reg, given=part, crossing_chain=("second",)
        )
        labels = [s.stage for s in result.stages]
        self.assertEqual(labels[0], "crossing[second]")
        self.assertEqual(result.stages[0].asked_nets, 1)
        self.assertTrue(all(l.startswith("crossing") is False for l in labels[1:]))

    def test_residue_runs_only_on_what_is_left(self):
        problem = two_cluster_board()
        part = spatial.partition(problem, min_cells=2, max_depth=1,
                                 experts={"open": "dead"})
        reg = dict(registry(spatial.GLOBAL_EXPERT, "cleanup"))
        reg["dead"] = DeadRouter
        result = spatial.route(
            problem, BUDGET, reg, given=part, residue=("cleanup",)
        )
        residue = [s for s in result.stages if s.stage.startswith("residue")]
        self.assertEqual(len(residue), 1)
        self.assertEqual(residue[0].asked_nets, 2)

    def test_the_solution_reports_what_it_actually_connected(self):
        problem = two_cluster_board()
        result = spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT),
            partition_kwargs={"min_cells": 2, "max_depth": 1},
        )
        linked = conn.analyse(problem, result.solution)
        self.assertEqual(result.solution.unrouted_nets, linked.unconnected_nets)
        self.assertEqual(result.solution.complete, linked.completeness >= 1.0)


class Contract(unittest.TestCase):
    def test_router_needs_a_registry(self):
        with self.assertRaises(ValueError):
            spatial.SpatialRouter({})

    def test_router_protocol(self):
        problem = two_cluster_board()
        router = spatial.SpatialRouter(
            registry(spatial.GLOBAL_EXPERT),
            partition_kwargs={"min_cells": 2, "max_depth": 1},
        )
        solution = router.route(problem, BUDGET)
        self.assertIsInstance(solution, RoutingSolution)
        self.assertTrue(solution.router.startswith("spatial["))
        self.assertIsNotNone(router.last)

    def test_unknown_region_order_is_refused(self):
        with self.assertRaises(ValueError):
            spatial.route(
                two_cluster_board(), BUDGET, registry(spatial.GLOBAL_EXPERT),
                region_order="whatever",
            )

    def test_the_default_expert_map_is_the_constant(self):
        """The table this module was built to test lost the A/B — 114 nets
        against 94 on lattice regions, 28 against 22 on fine-pitch. Putting it
        back is a change that needs a new measurement, not a new opinion."""
        self.assertEqual(
            set(spatial.EXPERTS.values()), {spatial.GLOBAL_EXPERT},
            "a non-constant expert map is a claim; it needs an A/B in "
            "scripts/spatial_suite.py and a line in the routing doc",
        )

    def test_the_escape_goes_first_by_default(self):
        """The one thing in this module that pays. 75.6% / 3 clean with the
        crossings first, 86.9% / 6 clean with the escapes first, on the same
        partition and the same single router."""
        problem = two_cluster_board()
        part = spatial.partition(problem, min_cells=2, max_depth=1)
        part = dataclasses.replace(
            part,
            regions=(dataclasses.replace(part.regions[0], character="fine-pitch"),
                     part.regions[1]),
        )
        result = spatial.route(
            problem, BUDGET, registry(spatial.GLOBAL_EXPERT), given=part
        )
        self.assertEqual(result.stages[0].stage, "r0")

    def test_rejected_assignments_never_shrinks(self):
        self.assertGreaterEqual(len(spatial.REJECTED_ASSIGNMENTS), 2)

    def test_rejected_assignments_carry_their_measurement(self):
        """Same discipline as the selector's ``REJECTED_RULES``: a rule that
        failed is only worth keeping if it says what killed it."""
        for name, why in spatial.REJECTED_ASSIGNMENTS:
            self.assertTrue(name.strip())
            self.assertTrue(any(ch.isdigit() for ch in why), name)


if __name__ == "__main__":
    unittest.main()
