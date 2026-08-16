"""Geometry. The tripwire test is :func:`assert_primitives` — if it fails, the
package is measuring copper with something other than the pipeline's ruler and
every number it prints is unmoored."""

from __future__ import annotations

import math
import unittest

from routerlib import geometry as geo
from routerlib.model import Point


class TrueShapes(unittest.TestCase):
    """The defect this module was rewritten for, pinned as arithmetic.

    Until 2026-08-16 a rectangle was its inscribed stadium, so the corner of a
    1.0mm square pad stuck 0.207mm out of the model against a 0.09mm gate.
    """

    def test_a_square_pad_has_corners(self):
        square = geo.rect_capsule(0, 0, 1.0, 1.0)
        trace = geo.segment_capsule(0.6, 0.6, 2.0, 2.0, 0.2)
        true_gap = geo.capsule_gap(trace, square)
        stadium_gap = geo.capsule_gap(trace, geo.stadium_capsule(0, 0, 1.0, 1.0))
        self.assertAlmostEqual(true_gap, math.hypot(0.1, 0.1) - 0.1, places=9)
        self.assertGreater(stadium_gap - true_gap, 0.2)

    def test_copper_inside_a_pad_reads_negative(self):
        square = geo.rect_capsule(0, 0, 1.0, 1.0)
        trace = geo.segment_capsule(-2.0, 0.4, 2.0, 0.4, 0.2)
        self.assertLess(geo.capsule_gap(trace, square), 0.0)

    def test_two_pads_of_one_footprint_do_not_short(self):
        """0.54mm pads on a 1.02mm pitch clear by 0.48mm. Reading the
        circumscribed stadium's radius as the core's sweep says they overlap,
        which is how that mistake was caught."""
        a = geo.rect_capsule(-0.51, 1.48, 0.54, 0.64)
        b = geo.rect_capsule(0.51, 1.48, 0.54, 0.64)
        self.assertAlmostEqual(geo.capsule_gap(a, b), 0.48, places=9)

    def test_a_keepout_is_its_rectangle(self):
        """``pcb_keepout_0`` of the USB-C block, 7.3 x 1.23mm, and a trace in
        the corner of it. This is the case that turned a ``fab.ready`` board
        into five blocking findings: the stadium cuts 0.255mm off each corner,
        2.8 times the clearance gate, and reports the trace as clear."""
        zone = geo.rect_capsule(0, 0, 7.3, 1.23)
        trace = geo.segment_capsule(3.45, 0.55, 3.6, 0.55, 0.1)
        self.assertLess(geo.capsule_gap(trace, zone), 0.0)
        self.assertGreater(
            geo.capsule_gap(trace, geo.stadium_capsule(0, 0, 7.3, 1.23)), 0.0
        )

    def test_a_polygon_pad_is_its_outline(self):
        tab = geo.polygon_capsule(
            [(0.0, 0.0), (1.0, 0.0), (1.0, 0.3), (0.3, 0.3), (0.3, 1.0), (0.0, 1.0)]
        )
        probe = geo.disc_capsule(0.8, 0.8, 0.2)
        # The nearest copper is the top edge of the L's horizontal arm, 0.5mm
        # below the probe — not the re-entrant corner a bounding box would put
        # there, and not the 0.29mm the bounding box's inscribed stadium did.
        self.assertAlmostEqual(geo.capsule_gap(probe, tab), 0.4, places=9)
        self.assertLess(geo.capsule_gap(geo.disc_capsule(0.15, 0.15, 0.1), tab), 0.0)

    def test_a_pill_is_still_exactly_a_stadium(self):
        """The one shape the old model got right, and it stays right."""
        pill = geo.rect_capsule(0, 0, 2.25, 0.63, 0.0, 0.315)
        stadium = geo.stadium_capsule(0, 0, 2.25, 0.63)
        probe = geo.disc_capsule(2.0, 0.5, 0.3)
        self.assertAlmostEqual(
            geo.capsule_gap(probe, pill), geo.capsule_gap(probe, stadium), places=9
        )


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
        ax, ay, bx, by, r = geo.stadium_capsule(0, 0, 2.25, 0.63)
        self.assertAlmostEqual(r, 0.315)
        self.assertAlmostEqual(bx - ax, 2.25 - 0.63)
        self.assertAlmostEqual(ay, 0.0)

    def test_270_degrees_turns_it_vertical(self):
        ax, ay, bx, by, r = geo.stadium_capsule(0, 0, 2.25, 0.63, 270.0)
        self.assertAlmostEqual(r, 0.315)
        self.assertAlmostEqual(abs(by - ay), 2.25 - 0.63, places=9)
        self.assertAlmostEqual(bx - ax, 0.0, places=9)

    def test_rotation_stops_the_invented_short(self):
        """Two 2.25 x 0.63mm pills at 270 degrees, 1.27mm apart. Read
        unrotated they overlap; read correctly they clear by 0.64mm. This is
        the bug that put six shorts on a clean hydrate-coaster."""
        flat_a = geo.stadium_capsule(-31.095, -25.53, 2.25, 0.63)
        flat_b = geo.stadium_capsule(-32.365, -25.53, 2.25, 0.63)
        self.assertLess(geo.capsule_gap(flat_a, flat_b), 0.0)

        turned_a = geo.stadium_capsule(-31.095, -25.53, 2.25, 0.63, 270.0)
        turned_b = geo.stadium_capsule(-32.365, -25.53, 2.25, 0.63, 270.0)
        self.assertAlmostEqual(
            geo.capsule_gap(turned_a, turned_b), 1.27 - 0.63, places=6
        )

    def test_a_turned_rectangle_is_a_turned_rectangle(self):
        """A 2.0 x 1.0mm pad at 90 degrees is 1.0 wide and 2.0 tall, and its
        corners are where the corners are."""
        turned = geo.rect_capsule(0, 0, 2.0, 1.0, 90.0)
        x0, y0, x1, y1 = geo.capsule_bbox(turned)
        self.assertAlmostEqual(x1 - x0, 1.0, places=9)
        self.assertAlmostEqual(y1 - y0, 2.0, places=9)
        self.assertAlmostEqual(geo.point_shape_distance(0.5, 1.0, turned), 0.0, places=9)


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
