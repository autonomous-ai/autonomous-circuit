"""Geometry. The tripwire test is :func:`assert_primitives` — if it fails, the
package is measuring copper with something other than the pipeline's ruler and
every number it prints is unmoored."""

from __future__ import annotations

import math
import unittest

from routerlib import geometry as geo
from routerlib.model import Point


class Primitives(unittest.TestCase):
    def test_imported_from_the_pipeline(self):
        import circuitpy.checks as checks

        self.assertIs(geo.segment_gap, checks._segment_gap)
        self.assertIs(geo.stadium, checks._stadium)
        self.assertIs(geo.point_segment_distance, checks._point_segment_distance)

    def test_assert_primitives_passes(self):
        geo.assert_primitives()

    def test_capsule_gap_is_negative_when_copper_overlaps(self):
        a = geo.segment_capsule(0, 0, 10, 0, 0.4)
        b = geo.segment_capsule(0, 0.3, 10, 0.3, 0.4)
        self.assertAlmostEqual(geo.capsule_gap(a, b), 0.3 - 0.4, places=9)


class Rotation(unittest.TestCase):
    def test_unrotated_pill_is_horizontal(self):
        ax, ay, bx, by, r = geo.rect_capsule(0, 0, 2.25, 0.63)
        self.assertAlmostEqual(r, 0.315)
        self.assertAlmostEqual(bx - ax, 2.25 - 0.63)
        self.assertAlmostEqual(ay, 0.0)

    def test_270_degrees_turns_it_vertical(self):
        ax, ay, bx, by, r = geo.rect_capsule(0, 0, 2.25, 0.63, 270.0)
        self.assertAlmostEqual(r, 0.315)
        self.assertAlmostEqual(abs(by - ay), 2.25 - 0.63, places=9)
        self.assertAlmostEqual(bx - ax, 0.0, places=9)

    def test_rotation_stops_the_invented_short(self):
        """Two 2.25 x 0.63mm pills at 270 degrees, 1.27mm apart. Read
        unrotated they overlap; read correctly they clear by 0.64mm. This is
        the bug that put six shorts on a clean hydrate-coaster."""
        flat_a = geo.rect_capsule(-31.095, -25.53, 2.25, 0.63)
        flat_b = geo.rect_capsule(-32.365, -25.53, 2.25, 0.63)
        self.assertLess(geo.capsule_gap(flat_a, flat_b), 0.0)

        turned_a = geo.rect_capsule(-31.095, -25.53, 2.25, 0.63, 270.0)
        turned_b = geo.rect_capsule(-32.365, -25.53, 2.25, 0.63, 270.0)
        self.assertAlmostEqual(
            geo.capsule_gap(turned_a, turned_b), 1.27 - 0.63, places=6
        )


class Polygons(unittest.TestCase):
    def square(self, size=10.0):
        h = size / 2
        return (Point(-h, -h), Point(h, -h), Point(h, h), Point(-h, h))

    def test_point_in_polygon(self):
        poly = self.square()
        self.assertTrue(geo.point_in_polygon(0, 0, poly))
        self.assertFalse(geo.point_in_polygon(6, 0, poly))

    def test_index_agrees_with_brute_force(self):
        poly = self.square()
        index = geo.PolygonIndex(poly)
        for x, y in ((0, 0), (4.9, 0), (-4.95, 2), (0, 4.0)):
            capsule = geo.disc_capsule(x, y, 0.6)
            brute = geo.distance_to_polygon(capsule, poly)
            fast = index.clearance(capsule, cutoff=2.0)
            if brute <= 2.0:
                self.assertAlmostEqual(brute, fast, places=9, msg=f"at {x},{y}")
            else:
                self.assertGreaterEqual(fast, 2.0 - 1e-9)
            self.assertEqual(index.contains(x, y), geo.point_in_polygon(x, y, poly))

    def test_index_on_a_thousand_point_outline(self):
        """The real case: circuit.json tessellates a rounded rectangle."""
        poly = tuple(
            Point(20 * math.cos(t * math.tau / 1000), 20 * math.sin(t * math.tau / 1000))
            for t in range(1000)
        )
        index = geo.PolygonIndex(poly)
        self.assertTrue(index.contains(0, 0))
        self.assertFalse(index.contains(25, 0))
        near = geo.disc_capsule(19.5, 0, 0.6)
        self.assertAlmostEqual(
            index.clearance(near, 1.0), geo.distance_to_polygon(near, poly), places=6
        )


class Grid(unittest.TestCase):
    def test_query_order_is_insertion_order(self):
        grid = geo.GridIndex(cell_mm=2.0)
        for i in range(20):
            grid.insert(geo.disc_capsule(i * 0.1, 0, 0.2), f"item{i}")
        first = [payload for _, payload in grid.query(geo.disc_capsule(0.5, 0, 0.2), 1.0)]
        second = [payload for _, payload in grid.query(geo.disc_capsule(0.5, 0, 0.2), 1.0)]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first, key=lambda s: int(s[4:])))

    def test_query_finds_everything_brute_force_would(self):
        grid = geo.GridIndex(cell_mm=2.0)
        capsules = [geo.disc_capsule(i * 1.3, (i % 5) * 1.7, 0.5) for i in range(50)]
        for i, capsule in enumerate(capsules):
            grid.insert(capsule, i)
        probe = geo.segment_capsule(0, 0, 10, 5, 0.3)
        found = {payload for _, payload in grid.query(probe, margin=0.5)}
        expected = {
            i for i, c in enumerate(capsules) if geo.capsule_gap(probe, c) < 0.5
        }
        self.assertTrue(expected <= found)


if __name__ == "__main__":
    unittest.main()
