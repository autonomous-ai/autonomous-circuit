"""The benchmark: instances round-trip, features are measured, the manifest
tells the truth about what is on disk."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import routerfix
from routerlib import bench
from routerlib.model import BOTTOM, Point


class Instances(unittest.TestCase):
    def test_there_are_enough_of_them(self):
        paths = bench.instance_paths()
        self.assertGreaterEqual(
            len(paths), 12,
            "the suite is meant to span 12-20 instances from easy to brutal; "
            "run packages/router/scripts/build_instances.py",
        )
        self.assertLessEqual(len(paths), 40)

    def test_every_instance_loads(self):
        for path in bench.instance_paths():
            with self.subTest(instance=path.stem):
                problem = bench.load_instance(path)
                self.assertTrue(problem.pads)
                self.assertTrue(problem.nets)
                self.assertGreater(problem.board.area_mm2, 0)

    def test_round_trip_is_lossless(self):
        for path in bench.instance_paths()[:4]:
            with self.subTest(instance=path.stem):
                first = bench.load_instance(path)
                again = bench.problem_from_dict(bench.problem_to_dict(first))
                self.assertEqual(first.pads, again.pads)
                self.assertEqual(first.drills, again.drills)
                self.assertEqual(first.nets, again.nets)
                self.assertEqual(first.keepouts, again.keepouts)
                self.assertEqual(first.planes, again.planes)
                self.assertEqual(first.board, again.board)

    def test_the_suite_spans_easy_to_brutal(self):
        """A benchmark where every instance is the same size measures one
        thing. The spread is the point."""
        features = [bench.features_of(p) for p in bench.load_all()]
        nets = sorted(f.routable_net_count for f in features)
        self.assertLessEqual(nets[0], 6, "no easy instance")
        self.assertGreaterEqual(nets[-1], 60, "no brutal instance")
        self.assertTrue(any(f.regular for f in features), "no regular layout")
        self.assertTrue(any(not f.regular for f in features), "no irregular layout")
        self.assertTrue(any(f.has_plane for f in features), "no plane instance")
        self.assertTrue(any(f.diff_pair_count for f in features), "no diff pairs")

    def test_manifest_matches_the_directory(self):
        manifest = json.loads(bench.MANIFEST.read_text(encoding="utf-8"))
        listed = {row["file"] for row in manifest["instances"]}
        on_disk = {p.name for p in bench.instance_paths()}
        self.assertEqual(listed, on_disk)
        for row in manifest["instances"]:
            self.assertIn("features", row)
            self.assertIn("baseline", row)
            self.assertIn("source", row)


class PlacementCorrespondence(unittest.TestCase):
    """An instance is a fixture; ``examples/`` is not. Scoring copper from a
    rebuilt board against a stale instance prints ``0.0% routed``, which reads
    as a verdict on the router and is not one. The guard has to say so."""

    def test_every_instance_carries_its_placement_hash(self):
        for path in bench.instance_paths():
            with self.subTest(instance=path.stem):
                data = json.loads(path.read_text(encoding="utf-8"))
                stored = data.get("placementHash")
                self.assertTrue(stored, "instance has no placementHash")
                self.assertEqual(
                    stored,
                    bench.placement_hash(bench.problem_from_dict(data)),
                    "stored placement hash disagrees with the file's own geometry",
                )

    def test_a_board_corresponds_to_itself(self):
        problem = routerfix.two_pad_board(gap_mm=4.0)
        match = bench.correspondence(problem, problem)
        self.assertTrue(match.matches)
        self.assertEqual(match.missing_pads, ())
        self.assertEqual(match.extra_pads, ())

    def test_a_moved_pad_breaks_correspondence_and_is_named(self):
        from dataclasses import replace

        problem = routerfix.two_pad_board(gap_mm=4.0)
        moved = replace(
            problem,
            pads=(
                replace(problem.pads[0], center=Point(
                    problem.pads[0].center.x + 1.0, problem.pads[0].center.y)),
            ) + problem.pads[1:],
        )
        match = bench.correspondence(problem, moved)
        self.assertFalse(match.matches)
        self.assertEqual(match.moved_pads, (problem.pads[0].id,))
        self.assertIn("does not match instance", match.report())

    def test_renumbered_pads_are_reported_as_missing_not_as_a_bad_route(self):
        from dataclasses import replace

        problem = routerfix.two_pad_board(gap_mm=4.0)
        renamed = replace(
            problem,
            pads=tuple(replace(p, id=p.id + "_rebuilt") for p in problem.pads),
        )
        match = bench.correspondence(problem, renamed)
        self.assertFalse(match.matches)
        self.assertEqual(len(match.missing_pads), len(problem.pads))
        self.assertEqual(len(match.extra_pads), len(problem.pads))

    def test_a_plane_variant_shares_its_base_placement(self):
        """A pour is copper, not placement. The plane twin must stay scoreable
        against the same routed board as its base."""
        problems = {p.id: p for p in bench.load_all()}
        base = problems.get("hydrate-coaster")
        twin = problems.get("hydrate-coaster-plane")
        if base is None or twin is None:
            self.skipTest("hydrate-coaster pair not present")
        self.assertEqual(bench.placement_hash(base), bench.placement_hash(twin))

    def test_no_copper_is_reported_even_when_the_placement_matches(self):
        problem = routerfix.two_pad_board(gap_mm=4.0)
        match = bench.correspondence(problem, problem)
        self.assertEqual(match.copper_elements, 0)
        self.assertIn("no traces or vias", match.report())


class FeatureMeasurement(unittest.TestCase):
    def test_finest_pitch_is_measured_from_pads(self):
        problem = routerfix.two_pad_board(gap_mm=0.5)
        self.assertAlmostEqual(bench.features_of(problem).finest_pin_pitch_mm, 0.5)

    def test_mst_length_is_a_lower_bound_on_copper(self):
        problem = routerfix.two_pad_board(gap_mm=8.0)
        self.assertAlmostEqual(bench.features_of(problem).mst_length_mm, 8.0)

    def test_a_key_matrix_reads_as_regular(self):
        for problem in bench.load_all():
            if problem.id == "terminal-keyboard":
                features = bench.features_of(problem)
                self.assertTrue(features.regular, features)
                self.assertGreater(features.grid_score, 0.5)
                return
        self.skipTest("terminal-keyboard instance not present")

    def test_an_irregular_board_does_not(self):
        for problem in bench.load_all():
            if problem.id == "hydrate-coaster":
                self.assertFalse(bench.features_of(problem).regular)
                return
        self.skipTest("hydrate-coaster instance not present")


class Planes(unittest.TestCase):
    def test_inset_shrinks_a_square_on_every_side(self):
        square = (Point(-5, -5), Point(5, -5), Point(5, 5), Point(-5, 5))
        inset = bench.inset_polygon(square, 0.2)
        self.assertEqual(len(inset), 4)
        for point in inset:
            self.assertAlmostEqual(abs(point.x), 4.8, places=9)
            self.assertAlmostEqual(abs(point.y), 4.8, places=9)

    def test_a_synthetic_plane_is_inside_the_board(self):
        from routerlib.geometry import PolygonIndex

        problems = {p.id: p for p in bench.load_all()}
        base = problems.get("hydrate-coaster")
        if base is None:
            self.skipTest("hydrate-coaster instance not present")
        planed = bench.with_ground_plane(base)
        self.assertTrue(planed.planes)
        outline = PolygonIndex(base.board.outline)
        for point in planed.planes[0].outline:
            self.assertTrue(outline.contains(point.x, point.y), point)

    def test_the_plane_is_synthetic_not_inherited(self):
        problems = {p.id: p for p in bench.load_all()}
        base = problems.get("hydrate-coaster")
        if base is None:
            self.skipTest("hydrate-coaster instance not present")
        planed = bench.with_ground_plane(base)
        self.assertIn("syntheticPlane", planed.meta)
        self.assertEqual(planed.planes[0].layer, BOTTOM)


if __name__ == "__main__":
    unittest.main()
