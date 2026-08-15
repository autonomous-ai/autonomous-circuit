"""Geometry, borrowed rather than rewritten.

Every distance in this module comes from ``circuitpy.checks``. That is
deliberate and it is the single most important decision in the scoring path: a
scorer that measures copper with its own arithmetic will eventually disagree
with the pipeline by a micron, and a scorer that disagrees with the pipeline is
worse than no scorer. So the primitives are *imported*, not copied, and
:func:`assert_primitives` fails loudly if they are ever renamed out from under
us.

What this module adds on top is only bookkeeping:

* a uniform-grid index so a collision query is not O(all copper)
* the stadium (capsule) model of a padstack, a drill or a trace segment, which
  is what all four distance rules are really about
* point-in-polygon, for board outline and plane membership
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

# These four are the pipeline's own measurements. Underscore-prefixed in
# circuitpy.checks because nothing outside that module needed them until now;
# imported here rather than duplicated because agreement matters more than
# tidiness. assert_primitives() below is the tripwire.
from circuitpy.checks import (  # noqa: F401
    _point_segment_distance as point_segment_distance,
    _segment_gap as segment_gap,
    _segments_cross as segments_cross,
    _stadium as stadium,
)

from routerlib.model import Point

#: A capsule: the segment its centre sweeps, plus a radius.
Capsule = tuple[float, float, float, float, float]


def assert_primitives() -> None:
    """Prove we are measuring with the pipeline's ruler, not a copy of it.

    A rounded rectangle 1.0 x 0.4mm has an inscribed stadium of radius 0.2 and
    a 0.6mm spine; two parallel segments 1mm apart gap at exactly 1mm; crossing
    segments gap at 0. If any of these move, the import above is pointing at
    something else and every number this package prints is unmoored.
    """
    ax, ay, bx, by, r = stadium(0.0, 0.0, 1.0, 0.4)
    assert abs(r - 0.2) < 1e-12, "stadium radius drifted"
    assert abs((bx - ax) - 0.6) < 1e-12, "stadium spine drifted"
    assert abs(ay) < 1e-12 and abs(by) < 1e-12
    assert abs(segment_gap(0, 0, 1, 0, 0, 1, 1, 1) - 1.0) < 1e-12
    assert segment_gap(0, 0, 1, 0, 0.5, -1, 0.5, 1) == 0.0
    assert abs(point_segment_distance(0.5, 1.0, 0, 0, 1, 0) - 1.0) < 1e-12


def capsule_gap(a: Capsule, b: Capsule) -> float:
    """Edge-to-edge distance between two capsules. Negative means they
    overlap — for copper of two different nets that is a short, not a
    clearance violation, and the two are scored separately."""
    ax, ay, bx, by, ra = a
    cx, cy, dx, dy, rb = b
    return segment_gap(ax, ay, bx, by, cx, cy, dx, dy) - ra - rb


def segment_capsule(
    x0: float, y0: float, x1: float, y1: float, width: float
) -> Capsule:
    return (x0, y0, x1, y1, width / 2.0)


def disc_capsule(x: float, y: float, diameter: float) -> Capsule:
    return (x, y, x, y, diameter / 2.0)


def rect_capsule(
    x: float, y: float, width: float, height: float, rotation_deg: float = 0.0
) -> Capsule:
    """A rectangular pad as its inscribed stadium, rotated in place.

    The stadium rounds the corners *inward*, so a measured gap is never smaller
    than the true one: the model can miss a finding, it cannot invent one. That
    asymmetry is intentional and is the same one the pipeline's DFM gate makes.

    **Rotation is not optional.** circuit.json marks a turned pad with
    ``ccw_rotation`` and keeps ``width``/``height`` in the pad's own frame. Read
    unrotated, the eight 2.25 x 0.63mm pills of a 1.27mm-pitch SOIC at 270
    degrees become one horizontal bar overlapping its neighbours — six invented
    shorts on a board that has none. Measured on hydrate-coaster,
    2026-08-15. The pipeline's ``circuitpy.checks`` does not read the field yet;
    that divergence is recorded in the README rather than papered over.
    """
    ax, ay, bx, by, radius = stadium(x, y, width, height)
    if not rotation_deg:
        return (ax, ay, bx, by, radius)
    theta = math.radians(rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    def spin(px: float, py: float) -> tuple[float, float]:
        dx, dy = px - x, py - y
        return (x + dx * cos_t - dy * sin_t, y + dx * sin_t + dy * cos_t)

    rax, ray = spin(ax, ay)
    rbx, rby = spin(bx, by)
    return (rax, ray, rbx, rby, radius)


def pad_capsule(pad) -> Capsule:
    """The one place a :class:`~routerlib.model.Pad` becomes geometry."""
    return rect_capsule(
        pad.center.x, pad.center.y, pad.width_mm, pad.height_mm, pad.rotation_deg
    )


def drill_capsule(drill) -> Capsule:
    """The one place a :class:`~routerlib.model.Drill` becomes geometry."""
    return rect_capsule(
        drill.center.x,
        drill.center.y,
        drill.width_mm,
        drill.height_mm,
        getattr(drill, "rotation_deg", 0.0),
    )


def capsule_bbox(c: Capsule) -> tuple[float, float, float, float]:
    ax, ay, bx, by, r = c
    return (min(ax, bx) - r, min(ay, by) - r, max(ax, bx) + r, max(ay, by) + r)


# ---------------------------------------------------------------------------
# Polygons
# ---------------------------------------------------------------------------


def point_in_polygon(px: float, py: float, poly: Sequence[Point]) -> bool:
    """Ray cast, half-open on the upper edge so a vertex is counted once."""
    if len(poly) < 3:
        return False
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i].x, poly[i].y
        xj, yj = poly[j].x, poly[j].y
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside


def polygon_edges(poly: Sequence[Point]) -> Iterable[tuple[float, float, float, float]]:
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        yield (a.x, a.y, b.x, b.y)


def distance_to_polygon(
    c: Capsule, poly: Sequence[Point]
) -> float:
    """Signed-ish: distance from a capsule's copper edge to the polygon
    boundary. Zero or negative when the copper touches or crosses it."""
    ax, ay, bx, by, r = c
    if len(poly) < 3:
        return math.inf
    best = math.inf
    for x0, y0, x1, y1 in polygon_edges(poly):
        best = min(best, segment_gap(ax, ay, bx, by, x0, y0, x1, y1))
    return best - r


def capsule_inside_polygon(c: Capsule, poly: Sequence[Point]) -> bool:
    """Whole capsule strictly inside the polygon."""
    ax, ay, bx, by, _ = c
    if not (point_in_polygon(ax, ay, poly) and point_in_polygon(bx, by, poly)):
        return False
    return distance_to_polygon(c, poly) > 0.0


def polygon_area(poly: Sequence[Point]) -> float:
    area = 0.0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        area += a.x * b.y - b.x * a.y
    return abs(area) / 2.0


class PolygonIndex:
    """A polygon you can ask about a few hundred thousand times.

    circuit.json tessellates a rounded rectangle into about a thousand points,
    so the naive "distance from this capsule to the outline" walks a thousand
    edges — per segment, per candidate, per net. Measured on hydrate-coaster
    that alone took the baseline router past two minutes on an 80mm board.

    Two structures fix it and neither approximates anything:

    * an edge grid for distance queries, with a **cutoff** — a caller only ever
      needs to know "is it at least 0.2mm away", so edges beyond the cutoff are
      never visited and the answer is reported as ``>= cutoff``
    * y-row buckets for the inside test, so a ray cast crosses only the edges
      that span the query's own row
    """

    __slots__ = ("poly", "_edges", "_grid", "_rows", "_row_mm", "_y0", "bbox")

    def __init__(self, poly: Sequence[Point], row_mm: float = 1.0):
        self.poly = tuple(poly)
        self._row_mm = row_mm
        self._edges: list[tuple[float, float, float, float]] = list(polygon_edges(self.poly))
        xs = [p.x for p in self.poly] or [0.0]
        ys = [p.y for p in self.poly] or [0.0]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self._y0 = self.bbox[1]
        self._grid = GridIndex(cell_mm=2.0)
        self._rows: dict[int, list[int]] = {}
        for index, (x0, y0, x1, y1) in enumerate(self._edges):
            self._grid.insert((x0, y0, x1, y1, 0.0), index)
            lo = int((min(y0, y1) - self._y0) // row_mm)
            hi = int((max(y0, y1) - self._y0) // row_mm)
            for row in range(lo, hi + 1):
                self._rows.setdefault(row, []).append(index)

    def __len__(self) -> int:
        return len(self._edges)

    def contains(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        row = int((y - self._y0) // self._row_mm)
        crossings = 0
        for index in self._rows.get(row, ()):
            ex0, ey0, ex1, ey1 = self._edges[index]
            if (ey0 > y) != (ey1 > y):
                x_cross = (ex1 - ex0) * (y - ey0) / (ey1 - ey0) + ex0
                if x < x_cross:
                    crossings += 1
        return crossings % 2 == 1

    def clearance(self, capsule: Capsule, cutoff: float) -> float:
        """Distance from the capsule's copper edge to the boundary, or a value
        ``>= cutoff`` when nothing is that close. Never under-reports."""
        ax, ay, bx, by, r = capsule
        best = cutoff + r
        for _, index in self._grid.query(capsule, margin=cutoff):
            ex0, ey0, ex1, ey1 = self._edges[index]
            gap = segment_gap(ax, ay, bx, by, ex0, ey0, ex1, ey1)
            if gap < best:
                best = gap
        return best - r


# ---------------------------------------------------------------------------
# Spatial index
# ---------------------------------------------------------------------------


@dataclass
class GridIndex:
    """Uniform-grid bucketing over capsules, keyed by an opaque payload.

    Nothing clever: a router asking "what copper is within 0.5mm of this
    segment" on a 112 x 90mm board with a thousand segments should not touch a
    thousand segments. Bucket order is deterministic (insertion order inside a
    cell, cells visited in sorted order) because a collision query that returns
    items in a hash-dependent order makes a first-match router
    nondeterministic.
    """

    cell_mm: float = 2.0
    _cells: dict[tuple[int, int], list[int]] = None  # type: ignore[assignment]
    _items: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._cells = {}
        self._items = []

    def __len__(self) -> int:
        return len(self._items)

    def _keys(self, bbox: tuple[float, float, float, float]):
        x0, y0, x1, y1 = bbox
        cx0, cy0 = int(math.floor(x0 / self.cell_mm)), int(math.floor(y0 / self.cell_mm))
        cx1, cy1 = int(math.floor(x1 / self.cell_mm)), int(math.floor(y1 / self.cell_mm))
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                yield (cx, cy)

    def insert(self, capsule: Capsule, payload) -> int:
        index = len(self._items)
        self._items.append((capsule, payload))
        for key in self._keys(capsule_bbox(capsule)):
            self._cells.setdefault(key, []).append(index)
        return index

    def query(self, capsule: Capsule, margin: float = 0.0):
        """Every (capsule, payload) whose bucket overlaps ``capsule`` grown by
        ``margin``. Deduplicated, and yielded in insertion order."""
        x0, y0, x1, y1 = capsule_bbox(capsule)
        seen: set[int] = set()
        hits: list[int] = []
        for key in self._keys((x0 - margin, y0 - margin, x1 + margin, y1 + margin)):
            for index in self._cells.get(key, ()):
                if index not in seen:
                    seen.add(index)
                    hits.append(index)
        hits.sort()
        for index in hits:
            yield self._items[index]


__all__ = [
    "Capsule",
    "GridIndex",
    "PolygonIndex",
    "assert_primitives",
    "capsule_bbox",
    "capsule_gap",
    "capsule_inside_polygon",
    "disc_capsule",
    "distance_to_polygon",
    "drill_capsule",
    "pad_capsule",
    "point_in_polygon",
    "point_segment_distance",
    "polygon_area",
    "polygon_edges",
    "rect_capsule",
    "segment_capsule",
    "segment_gap",
    "segments_cross",
    "stadium",
]
