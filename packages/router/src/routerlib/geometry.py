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
* one shape model — **a convex core polygon swept by a radius** — which covers
  a trace segment, a via, a round pad, a pill, a rotated rectangle and a
  polygon pad without approximating any of them
* point-in-polygon, for board outline and plane membership

Why a core polygon and not a stadium
------------------------------------

Until 2026-08-16 *every* rectangle here — pad and keepout alike — was modelled
as its **inscribed stadium**. On a square pad that is the inscribed circle, so
each corner protruded by ``(sqrt(2)-1)*w/2``: 0.21mm on a 1.0mm pad, against a
0.09mm clearance gate. The docstring called it "can miss a finding, never
invent one". It was in fact the dominant failure mode of the whole router
benchmark — routers that scored clean here produced 12, 35 and 27 real KiCad
findings on the three example boards, and the harness ranking inverted against
the pipeline's.

A stadium is the Minkowski sum of a segment and a disc. Generalise the segment
to any polygon and the same one arithmetic covers every shape we have:

======================  ==========================  ==============
shape                   core                        radius
======================  ==========================  ==============
trace segment           the segment                 half the width
via / round pad         a single point              half the diameter
pill (``rotated_pill``) the spine segment           half the short side
rectangle               its four corners            0
rounded rectangle       the corners, inset          the corner radius
polygon pad             its own vertices            0
======================  ==========================  ==============

So :class:`Capsule` still *is* ``(ax, ay, bx, by, radius)`` — old code that
unpacks five floats keeps working and gets the **circumscribed** stadium, which
errs outward — but a shape whose core is not a segment carries that core along
and every distance in this module measures it exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
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

#: A core vertex: a plain ``(x, y)`` pair, not a :class:`~routerlib.model.Point`,
#: because these are read in the inner loop a few million times per suite.
Vertex = tuple[float, float]


class Shape(tuple):
    """``(ax, ay, bx, by, radius)`` — and the exact ``core`` polygon the shape
    is swept from, for everything that is not a stadium.

    The five-float face is the legacy view and it is deliberately the
    **circumscribed** stadium of the real shape: an obstacle read that way is
    never smaller than the truth. Every distance function in this module reads
    ``core`` when it is there, so the five floats are only ever a bounding
    convenience for code that has not been taught the shape model.

    A stadium — a trace segment, a via, a round pad, a pill — needs no core and
    stays a plain tuple, which keeps the hot path allocation-free.

    ``sweep`` is the radius the *core* is swept by and it is not the fifth
    float: a sharp rectangle sweeps its corners by nothing, but the stadium
    that contains it needs a radius of half its short side. Confusing the two
    is a mistake that reads as every two-pad footprint on the board shorting to
    itself, which is how it was caught.
    """

    def __new__(
        cls,
        ax: float,
        ay: float,
        bx: float,
        by: float,
        radius: float,
        core: tuple[Vertex, ...] | None = None,
        sweep: float = 0.0,
    ) -> "Shape":
        self = tuple.__new__(cls, (ax, ay, bx, by, radius))
        self.core = core
        self.sweep = sweep
        return self


#: What every distance function in this module accepts: a plain
#: ``(ax, ay, bx, by, radius)`` stadium, or a :class:`Shape` carrying the exact
#: core polygon of a rectangle, a rounded rectangle or a polygon pad.
Capsule = tuple


def capsule_core(c) -> tuple[Vertex, ...] | None:
    """The exact core polygon, or ``None`` when the shape *is* its stadium."""
    return getattr(c, "core", None)


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


# ---------------------------------------------------------------------------
# Core-polygon arithmetic
# ---------------------------------------------------------------------------


def _core_edges(core: tuple[Vertex, ...]):
    """Every edge of a core, as ``(x0, y0, x1, y1)``.

    A one-vertex core (a disc) yields one degenerate edge and a two-vertex core
    (a stadium) yields one real one, so the same loop covers all three cases.
    """
    n = len(core)
    if n == 1:
        x, y = core[0]
        return ((x, y, x, y),)
    if n == 2:
        (x0, y0), (x1, y1) = core
        return ((x0, y0, x1, y1),)
    return tuple(
        (core[i][0], core[i][1], core[(i + 1) % n][0], core[(i + 1) % n][1])
        for i in range(n)
    )


def _core_contains(core: tuple[Vertex, ...], px: float, py: float) -> bool:
    """Ray cast against a core with at least three vertices."""
    n = len(core)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = core[i]
        xj, yj = core[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def _convex_hull(core: tuple[Vertex, ...]) -> tuple[Vertex, ...]:
    """Monotone chain. A convex core comes back unchanged (up to rotation)."""
    if len(core) < 3:
        return core
    pts = sorted(set(core))
    if len(pts) < 3:
        return tuple(pts)

    def half(seq):
        out: list[Vertex] = []
        for p in seq:
            while len(out) >= 2:
                (ox, oy), (nx, ny) = out[-2], out[-1]
                if (nx - ox) * (p[1] - oy) - (ny - oy) * (p[0] - ox) > 0:
                    break
                out.pop()
            out.append(p)
        return out

    lower = half(pts)
    upper = half(reversed(pts))
    return tuple(lower[:-1] + upper[:-1])


def _penetration(a: tuple[Vertex, ...], b: tuple[Vertex, ...]) -> float:
    """How deep two overlapping cores are inside each other, in mm.

    The separating-axis theorem over the edge normals of both convex hulls,
    which for convex polygons is the exact minimum translation distance. For a
    **non-convex** core the hull over-states the depth; the *sign* is still
    exact, which is what decides short-versus-clearance, and the only non-convex
    core we have measured (a USB-C shell tab) departs from its hull by 0.15
    nanometres.
    """
    ha, hb = _convex_hull(a), _convex_hull(b)
    best = math.inf
    for poly in (ha, hb):
        n = len(poly)
        if n < 2:
            continue
        for i in range(n if n > 2 else 1):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            ex, ey = x1 - x0, y1 - y0
            length = math.hypot(ex, ey)
            if length < 1e-12:
                continue
            nx, ny = -ey / length, ex / length
            amin = amax = ha[0][0] * nx + ha[0][1] * ny
            for x, y in ha:
                d = x * nx + y * ny
                if d < amin:
                    amin = d
                elif d > amax:
                    amax = d
            bmin = bmax = hb[0][0] * nx + hb[0][1] * ny
            for x, y in hb:
                d = x * nx + y * ny
                if d < bmin:
                    bmin = d
                elif d > bmax:
                    bmax = d
            # How far A must move along this axis to leave B, whichever way is
            # cheaper. Not the length of the interval overlap: a zero-thickness
            # core — a trace segment lying inside a keepout — overlaps by
            # nothing and still has to move 0.115mm to get out.
            left, right = amax - bmin, bmax - amin
            overlap = left if left < right else right
            if overlap <= 0.0:
                return 0.0
            if overlap < best:
                best = overlap
    return 0.0 if best is math.inf else best


def core_distance(a: tuple[Vertex, ...], b: tuple[Vertex, ...]) -> float:
    """Distance between two cores; **negative is penetration depth**.

    Exact for the separated case whatever the cores are (the minimum over every
    pair of edges is the distance between two closed boundaries that do not
    intersect, and containment is caught before it can be mistaken for it).
    """
    na, nb = len(a), len(b)
    if na <= 2 and nb <= 2:
        ax, ay = a[0]
        bx, by = a[-1]
        cx, cy = b[0]
        dx, dy = b[-1]
        return segment_gap(ax, ay, bx, by, cx, cy, dx, dy)

    best = math.inf
    for ax, ay, bx, by in _core_edges(a):
        for cx, cy, dx, dy in _core_edges(b):
            gap = segment_gap(ax, ay, bx, by, cx, cy, dx, dy)
            if gap < best:
                best = gap
                if best == 0.0:
                    break
        if best == 0.0:
            break
    if best > 0.0:
        if _core_contains(b, a[0][0], a[0][1]) or _core_contains(a, b[0][0], b[0][1]):
            return -_penetration(a, b)
        return best
    return -_penetration(a, b)


def capsule_gap(a: Capsule, b: Capsule) -> float:
    """Edge-to-edge distance between two shapes. Negative means they overlap —
    for copper of two different nets that is a short, not a clearance
    violation, and the two are scored separately."""
    core_a = getattr(a, "core", None)
    core_b = getattr(b, "core", None)
    if core_a is None and core_b is None:
        ax, ay, bx, by, ra = a
        cx, cy, dx, dy, rb = b
        return segment_gap(ax, ay, bx, by, cx, cy, dx, dy) - ra - rb
    if core_a is None:
        core_a, ra = ((a[0], a[1]), (a[2], a[3])), a[4]
    else:
        ra = a.sweep
    if core_b is None:
        core_b, rb = ((b[0], b[1]), (b[2], b[3])), b[4]
    else:
        rb = b.sweep
    return core_distance(core_a, core_b) - ra - rb


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def segment_capsule(
    x0: float, y0: float, x1: float, y1: float, width: float
) -> Capsule:
    return (x0, y0, x1, y1, width / 2.0)


def disc_capsule(x: float, y: float, diameter: float) -> Capsule:
    return (x, y, x, y, diameter / 2.0)


def _spin(px: float, py: float, cx: float, cy: float, cos_t: float, sin_t: float):
    dx, dy = px - cx, py - cy
    return (cx + dx * cos_t - dy * sin_t, cy + dx * sin_t + dy * cos_t)


def _circumscribed_stadium(
    x: float, y: float, width: float, height: float, rotation_deg: float
) -> tuple[float, float, float, float, float]:
    """The smallest stadium that *contains* a ``width`` x ``height`` rectangle:
    the full centre line of the long side, with the short side's half as the
    radius. This is the legacy five-float view of a shaped capsule, and it errs
    outward on purpose."""
    radius = min(width, height) / 2.0
    if height >= width:
        half = height / 2.0
        ax, ay, bx, by = x, y - half, x, y + half
    else:
        half = width / 2.0
        ax, ay, bx, by = x - half, y, x + half, y
    if rotation_deg:
        theta = math.radians(rotation_deg)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        ax, ay = _spin(ax, ay, x, y, cos_t, sin_t)
        bx, by = _spin(bx, by, x, y, cos_t, sin_t)
    return (ax, ay, bx, by, radius)


def stadium_capsule(
    x: float, y: float, width: float, height: float, rotation_deg: float = 0.0
) -> Capsule:
    """A pill: the inscribed stadium, rotated in place.

    For a shape that really is a stadium — ``pill``, ``rotated_pill``, a round
    pad, a slotted drill — this is exact, and it is the *only* shape for which
    the inscribed stadium ever was.
    """
    ax, ay, bx, by, radius = stadium(x, y, width, height)
    if rotation_deg:
        theta = math.radians(rotation_deg)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        ax, ay = _spin(ax, ay, x, y, cos_t, sin_t)
        bx, by = _spin(bx, by, x, y, cos_t, sin_t)
    return (ax, ay, bx, by, radius)


def rect_capsule(
    x: float,
    y: float,
    width: float,
    height: float,
    rotation_deg: float = 0.0,
    corner_radius_mm: float = 0.0,
) -> Capsule:
    """A rectangular pad or keepout as the rectangle it is.

    ``rotation`` is not optional. circuit.json marks a turned pad with
    ``ccw_rotation`` and keeps ``width``/``height`` in the pad's own frame; read
    unrotated, the eight 2.25 x 0.63mm pills of a 1.27mm-pitch package at 270
    degrees become one horizontal bar overlapping its neighbours — six invented
    shorts on a board that has none, measured on hydrate-coaster 2026-08-15.

    ``corner_radius_mm`` rounds the corners: the core shrinks by the radius and
    the radius is swept back over it. At ``min(w, h) / 2`` that degenerates
    exactly into :func:`stadium_capsule`, which is why one function covers
    ``rect``, ``rounded_rect`` and ``pill`` without a special case.
    """
    radius = max(0.0, min(float(corner_radius_mm), min(width, height) / 2.0))
    half_w = max(width / 2.0 - radius, 0.0)
    half_h = max(height / 2.0 - radius, 0.0)
    if half_w <= 1e-12 and half_h <= 1e-12:
        return disc_capsule(x, y, radius * 2.0)
    if half_h <= 1e-12 or half_w <= 1e-12:
        # Degenerate to a stadium: the core is a segment, not a rectangle.
        ax, ay = x - half_w, y - half_h
        bx, by = x + half_w, y + half_h
        if rotation_deg:
            theta = math.radians(rotation_deg)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            ax, ay = _spin(ax, ay, x, y, cos_t, sin_t)
            bx, by = _spin(bx, by, x, y, cos_t, sin_t)
        return (ax, ay, bx, by, radius)

    corners = (
        (x - half_w, y - half_h),
        (x + half_w, y - half_h),
        (x + half_w, y + half_h),
        (x - half_w, y + half_h),
    )
    if rotation_deg:
        theta = math.radians(rotation_deg)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        corners = tuple(_spin(px, py, x, y, cos_t, sin_t) for px, py in corners)
    ax, ay, bx, by, _ = _circumscribed_stadium(x, y, width, height, rotation_deg)
    return Shape(
        ax, ay, bx, by, min(width, height) / 2.0, core=corners, sweep=radius
    )


def polygon_capsule(points: Sequence) -> Capsule:
    """A polygon pad, as its own outline.

    Modelled before this as the inscribed stadium of its bounding box, which is
    two approximations stacked in opposite directions. The vertices exist in
    circuit.json; nothing was gained by throwing them away.
    """
    core: tuple[Vertex, ...] = tuple(
        (float(p.x), float(p.y)) if hasattr(p, "x") else (float(p[0]), float(p[1]))
        for p in points
    )
    if not core:
        raise ValueError("polygon_capsule needs at least one vertex")
    if len(core) <= 2:
        (ax, ay), (bx, by) = (core[0], core[-1])
        return (ax, ay, bx, by, 0.0)
    xs = [p[0] for p in core]
    ys = [p[1] for p in core]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    ax, ay, bx, by, radius = _circumscribed_stadium(
        cx, cy, max(xs) - min(xs), max(ys) - min(ys), 0.0
    )
    return Shape(ax, ay, bx, by, radius, core=core, sweep=0.0)


#: Shapes whose true outline *is* the inscribed stadium. Everything else that
#: circuit.json can emit is a rectangle, a polygon, or unknown — and unknown is
#: modelled as a rectangle, which is the conservative reading.
STADIUM_SHAPES = frozenset(
    {"circle", "pill", "rotated_pill", "oval", "rotated_oval", "capsule"}
)


@lru_cache(maxsize=16384)
def pad_capsule(pad) -> Capsule:
    """The one place a :class:`~routerlib.model.Pad` becomes geometry.

    Cached on the pad, which is a frozen dataclass shared by every query in a
    run: the shape is built once per pad per process, not once per collision
    test.
    """
    vertices = getattr(pad, "vertices", ())
    if vertices:
        return polygon_capsule(vertices)
    shape = (pad.shape or "rect").lower()
    corner = float(getattr(pad, "corner_radius_mm", 0.0) or 0.0)
    if shape in STADIUM_SHAPES and (
        corner <= 0.0 or abs(corner - min(pad.width_mm, pad.height_mm) / 2.0) <= 1e-9
    ):
        return stadium_capsule(
            pad.center.x, pad.center.y, pad.width_mm, pad.height_mm, pad.rotation_deg
        )
    return rect_capsule(
        pad.center.x,
        pad.center.y,
        pad.width_mm,
        pad.height_mm,
        pad.rotation_deg,
        corner,
    )


@lru_cache(maxsize=16384)
def drill_capsule(drill) -> Capsule:
    """The one place a :class:`~routerlib.model.Drill` becomes geometry.

    A hole defaults to a stadium — a round hole is the degenerate case and a
    slot is a real slot — because that is what ``pcb_plated_hole`` and
    ``pcb_hole`` actually carry. A drill that declares a rectangular shape gets
    the rectangle.
    """
    shape = (getattr(drill, "shape", "pill") or "pill").lower()
    rotation = getattr(drill, "rotation_deg", 0.0)
    if shape in STADIUM_SHAPES:
        return stadium_capsule(
            drill.center.x, drill.center.y, drill.width_mm, drill.height_mm, rotation
        )
    return rect_capsule(
        drill.center.x, drill.center.y, drill.width_mm, drill.height_mm, rotation
    )


@lru_cache(maxsize=16384)
def keepout_capsule(keepout) -> Capsule:
    """The one place a :class:`~routerlib.model.Keepout` becomes geometry.

    A keepout is where the inscribed stadium did the most damage: on the 7.3 x
    1.23mm ``pcb_keepout_0`` of the USB-C block it cut 0.255mm off each corner,
    2.8 times the clearance gate, and our copper sat 0.168mm inside a rectangle
    the harness read as clear.
    """
    vertices = getattr(keepout, "vertices", ())
    if vertices:
        return polygon_capsule(vertices)
    shape = (keepout.shape or "rect").lower()
    if shape in STADIUM_SHAPES:
        return stadium_capsule(
            keepout.center.x,
            keepout.center.y,
            keepout.width_mm,
            keepout.height_mm,
            getattr(keepout, "rotation_deg", 0.0),
        )
    return rect_capsule(
        keepout.center.x,
        keepout.center.y,
        keepout.width_mm,
        keepout.height_mm,
        getattr(keepout, "rotation_deg", 0.0),
    )


def core_halfplanes(c: Capsule):
    """``((nx, ny, offset), ...)`` — the outward edge lines of the shape's
    convex core, or ``None`` when the shape is a stadium.

    ``max(nx*x + ny*y - offset)`` over the planes is **negative inside** and, on
    the outside, is the true distance wherever the nearest feature is an edge
    and an under-estimate in the wedge beyond a corner. Under-estimating is the
    right way round for a grid stamp: a router marks a little too much space
    near a pad corner instead of a little too little, which is the whole defect
    being fixed here, pointed the other way. Anything that must be exact —
    ``Workspace``, the scorer — uses :func:`capsule_gap` instead.
    """
    core = getattr(c, "core", None)
    if core is None:
        return None
    planes = c.__dict__.get("planes")
    if planes is None:
        hull = _convex_hull(core)
        area = 0.0
        n = len(hull)
        for i in range(n):
            x0, y0 = hull[i]
            x1, y1 = hull[(i + 1) % n]
            area += x0 * y1 - x1 * y0
        if area < 0:  # keep it counter-clockwise so the normals point out
            hull = tuple(reversed(hull))
        built = []
        for i in range(n):
            x0, y0 = hull[i]
            x1, y1 = hull[(i + 1) % n]
            ex, ey = x1 - x0, y1 - y0
            length = math.hypot(ex, ey)
            if length < 1e-12:
                continue
            nx, ny = ey / length, -ex / length
            built.append((nx, ny, nx * x0 + ny * y0))
        planes = tuple(built)
        c.planes = planes
    return planes


def point_shape_distance(px: float, py: float, c: Capsule) -> float:
    """Exact distance from a point to a shape's edge; negative inside it."""
    core = getattr(c, "core", None)
    if core is None:
        return point_segment_distance(px, py, c[0], c[1], c[2], c[3]) - c[4]
    best = math.inf
    for ax, ay, bx, by in _core_edges(core):
        d = point_segment_distance(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    if _core_contains(core, px, py):
        return -best - c.sweep
    return best - c.sweep


def capsule_sweep(c: Capsule) -> float:
    """The radius the shape's core is swept by: half a trace's width, half a
    via's diameter, a pad's corner rounding, and zero for a sharp rectangle."""
    return c.sweep if getattr(c, "core", None) is not None else c[4]


def capsule_bbox(c: Capsule) -> tuple[float, float, float, float]:
    core = getattr(c, "core", None)
    if core is None:
        ax, ay, bx, by, r = c
        return (min(ax, bx) - r, min(ay, by) - r, max(ax, bx) + r, max(ay, by) + r)
    r = c.sweep
    xs = [p[0] for p in core]
    ys = [p[1] for p in core]
    return (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)


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


def capsule_edges(c: Capsule) -> tuple[tuple[float, float, float, float], ...]:
    """The edges a capsule's *core* sweeps — its spine when it has no core."""
    core = getattr(c, "core", None)
    if core is None:
        return ((c[0], c[1], c[2], c[3]),)
    return _core_edges(core)


def distance_to_polygon(c: Capsule, poly: Sequence[Point]) -> float:
    """Signed-ish: distance from a capsule's copper edge to the polygon
    boundary. Zero or negative when the copper touches or crosses it."""
    if len(poly) < 3:
        return math.inf
    best = math.inf
    edges = capsule_edges(c)
    for x0, y0, x1, y1 in polygon_edges(poly):
        for ax, ay, bx, by in edges:
            gap = segment_gap(ax, ay, bx, by, x0, y0, x1, y1)
            if gap < best:
                best = gap
    return best - capsule_sweep(c)


def capsule_inside_polygon(c: Capsule, poly: Sequence[Point]) -> bool:
    """Whole capsule strictly inside the polygon."""
    for ax, ay, bx, by in capsule_edges(c):
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
        r = capsule_sweep(capsule)
        own = capsule_edges(capsule)
        best = cutoff + r
        for _, index in self._grid.query(capsule, margin=cutoff):
            ex0, ey0, ex1, ey1 = self._edges[index]
            for ax, ay, bx, by in own:
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
    "Shape",
    "GridIndex",
    "PolygonIndex",
    "STADIUM_SHAPES",
    "Vertex",
    "assert_primitives",
    "capsule_bbox",
    "capsule_core",
    "capsule_edges",
    "capsule_gap",
    "capsule_inside_polygon",
    "capsule_sweep",
    "core_halfplanes",
    "core_distance",
    "disc_capsule",
    "distance_to_polygon",
    "drill_capsule",
    "keepout_capsule",
    "pad_capsule",
    "point_in_polygon",
    "point_segment_distance",
    "polygon_area",
    "polygon_capsule",
    "point_shape_distance",
    "polygon_edges",
    "rect_capsule",
    "segment_capsule",
    "segment_gap",
    "segments_cross",
    "stadium",
    "stadium_capsule",
]
