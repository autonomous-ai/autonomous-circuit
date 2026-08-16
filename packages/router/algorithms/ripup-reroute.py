"""Rip-up and reroute: route greedily, then take the worst failure apart.

This is the family commercial routers actually belong to. The single-net engine
underneath is an ordinary grid A*; the interesting part is what happens *after*
the greedy pass, when a net cannot get through because four nets routed before
it took the corridor. A greedy router reports that net unrouted and stops. This
one asks a different question — *who is in the way* — rips those nets out, makes
the contested region more expensive for everyone, and reroutes the group in a
different order.

Three properties are non-negotiable and each one costs something:

**It never places copper it cannot defend.** Every path is planned on a grid
whose free space is already inset by the design rules, and every emitted
polyline and via is then re-checked through :class:`routerlib.workspace.Workspace`
— the same geometry the scorer grades with. A path that fails verification is
thrown away, not shipped. That is why the error column stays at zero and why the
completeness column is lower than a router that guesses would report.

**It is deterministic by construction, not by luck.** Every random choice comes
from ``random.Random(budget.seed)``. Every iteration over a mapping is over
sorted keys. Every stopping rule is a *count* — search nodes, rip-up passes —
never a clock. Run it twice on the same board and you get the same bytes; run it
on a loaded machine and you get the same bytes.

**It self-limits below the harness budget.** ``Budget(max_nodes=20_000_000)`` is
more search than a Python A* can spend in a sane wall-clock, so the router caps
itself at :data:`DEFAULT_NODE_CAP` expansions. That is a constant, not a clock,
so the cap does not make the output machine-dependent — it makes it smaller. A
caller who wants more passes it in.

## How the board becomes a grid

A uniform grid, pitch chosen from board area so a layer holds around 400k cells,
and one float per cell: ``avail`` — the widest copper *half-width* whose centre
may sit anywhere in that cell without breaking a rule. It is the minimum, over
every obstacle class, of

    distance(cell square, obstacle) - radius(obstacle) - clearance(that class)

measured from the whole cell **square**, not its centre. That detail is what
makes the grid trustworthy: the segment between two adjacent cell centres —
orthogonal or diagonal — lies inside the union of those two cells, so if both
cells clear ``h`` then the whole swept capsule of half-width ``h`` clears the
rule. No sampling, no fudge factor.

Clearances come from the rules, per class, and each sits *above* the floor the
scorer blocks at, because a router that aims at the floor lands under it:

| obstacle | floor | what the grid designs to |
|---|---|---|
| other copper | 0.09 gate / 0.10 fab | ``target_clearance_mm`` (0.147) |
| component plated hole | 0.28 | 0.30 hard, 0.45 as a soft cost |
| via hole, unplated hole | 0.20 | 0.22 |
| board edge | 0.20 | 0.22 |

The soft row is the one that is easy to miss: ``dfm_hole_clearance`` errors below
0.28 but *warns* below ``warn_pth_to_copper_mm`` = 0.35. Warnings are the third
term of the score key, so the grid prices the warn band rather than forbidding
it — the router leaves it when it can and enters it when the alternative is an
unrouted net.

## Order: most constrained first, measured

Nets are routed in ascending order of **escape room** — the number of free grid
cells around the net's tightest pad, counted on the empty board. A pin inside a
0.5mm-pitch connector has a handful; a 0402's pad has hundreds. Route the wide
rails first and the connector pin has none left, which is exactly what happened
before this ordering existed: two signal nets on
``matrix-ldo-3v3__usb-c-power`` were reported *geometrically impossible* when a
0.5mm ground track had simply covered every cell they could leave from.

The number is measured from the grid, never inferred from a footprint name. Net
class is the tie-break, not the primary key.

## What rip-up actually does here

Because nothing illegal is ever committed, a conflict does not appear as
overlapping copper. It appears as a *failed connection*. So the conflict has to
be discovered on purpose: the failing net is re-searched with other nets' copper
made passable at a heavy price, and the nets whose copper that path walks
through are the blockers. Then:

* history cost is added to every contested cell — PathFinder's negotiated
  congestion, applied on demand rather than every pass — so the next attempt by
  anyone prices that corridor honestly;
* the failing net and up to ``rip_cap`` blockers are ripped out;
* they are rerouted with the failing net **first** and the blockers in a
  seeded-shuffled order;
* the blockers go on a tabu list for ``tabu_tenure`` passes, so the loop cannot
  spend its budget swapping the same two nets back and forth.

The working state is deliberately never rolled back — escaping a local minimum
is the point — but the best solution seen is kept and returned. A pass that
makes things worse still happened; the answer is still the best board found.
The loop stops when :data:`DEFAULT_STALL_LIMIT` consecutive passes fail to
improve on that best, because a net that twenty-four different orderings could
not close will not yield to the twenty-fifth, and the remaining budget buys more
on the next board.

A connection that fails *even with every other net's copper made passable* is
blocked by geometry no rip-up can move. It is marked hopeless and never retried,
which is both honest and the single biggest saving of search budget.

## The control

``ripup-greedy-control`` is this same router with ``rip_up_passes=0``. It exists
so the benchmark can attribute the difference to rip-up and not to the maze
router, the grid, or the pad-escape logic. Comparing against ``baseline-pattern``
would measure all four at once.
"""

from __future__ import annotations

import heapq
import math
import random
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from routerlib.geometry import capsule_bbox, core_halfplanes, segment_gap
from routerlib.model import (
    BOTTOM,
    TOP,
    Budget,
    Net,
    Pad,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)
from routerlib.workspace import Workspace

# ---------------------------------------------------------------------------
# Tuning constants. Every one is a count or a millimetre — never a second — so
# the router's output does not depend on the machine it ran on.
# ---------------------------------------------------------------------------

#: Widest copper half-width the router will ever place: a 0.6mm via pad.
#: Nothing further than this from an obstacle can be constrained by it, which is
#: what bounds the rasterising loops.
MAX_HALF_MM = 0.30

#: Grid pitch is chosen so a layer holds about this many cells. Bigger boards
#: get a coarser grid; the alternative is a benchmark that never finishes the
#: keyboard and therefore says nothing about the keyboard.
TARGET_CELLS = 400_000
MIN_PITCH_MM = 0.15
MAX_PITCH_MM = 0.30
PITCH_STEP_MM = 0.05

#: Safety added above each fab floor before the grid calls a cell usable.
HOLE_MARGIN_MM = 0.02
EDGE_MARGIN_MM = 0.02

#: Copper inside ``warn_pth_to_copper_mm`` of a component plated hole is legal
#: and produces a warning. Priced, not forbidden.
HOLE_WARN_BAND_MM = 0.45
HOLE_WARN_COST_MM = 6.0

#: Cost of a layer change, in millimetres of copper. Vias are the fourth term of
#: the score key: worth avoiding, never worth failing over.
VIA_COST_MM = 2.2

#: Extra cost per millimetre of routing on a layer that already carries
#: somebody else's pour. Crossing a pour can island it — a defect the scorer
#: explicitly does not check (``coverage_gaps``), so the router prices it itself
#: rather than taking free credit for a check nobody runs.
#:
#: Per millimetre, not per cell, and that distinction cost a benchmark run: at a
#: flat 1.2mm per cell a 0.15mm grid charged 80mm to cross 10mm of pour, which
#: is not a preference, it is a ban. The plane variant of hydrate-coaster scored
#: *below* its planeless twin because of it.
PLANE_CROSS_PER_MM = 0.6

#: What entering another net's copper costs during *conflict discovery* only.
#: Large enough that the discovery path prefers to go around; small enough that
#: it goes through rather than failing.
CONFLICT_COST_MM = 40.0

#: History added to a contested cell when a rip-up pass fires, and the ceiling
#: it accumulates to. Both matter. An early version charged 1.5mm per pass with
#: no ceiling and bumped *every* cell the discovery path crossed, including the
#: open ones: after forty passes a cell cost 60mm, every A* explored its whole
#: box before accepting any route, and the router got monotonically worse the
#: longer it ran. History belongs only on cells somebody else's copper actually
#: blocks, and it has to stay the same order of magnitude as a path length.
HISTORY_STEP_MM = 0.35
HISTORY_CEILING_MM = 6.0

#: Search-box escalation, in millimetres around the two ends of a connection.
#: Widths are tried *inside* each box, not outside: the expensive whole-board
#: level then costs one search per width instead of one box sweep per width.
BOX_SLACK_MM = (4.0, 16.0, 1e9)

#: Radii at which a pad looks for its own pour, matched to the boxes above.
PLANE_TARGET_RADIUS_MM = (3.0, 9.0, 9.0)

#: How many pad-escape candidates to accept, and how many to test before giving
#: up on a pad. Both counts, both deterministic.
MAX_ACCESS_PER_PAD = 8
MAX_ACCESS_TRIED = 40

#: Look-ahead when straightening a staircase into a polyline.
SIMPLIFY_LOOKAHEAD = 6

#: Defaults the router imposes on itself, under any harness budget. These are
#: the numbers that decide how long a run takes, and they are counts: the same
#: board gets the same amount of search on a busy laptop and an idle one.
DEFAULT_NODE_CAP = 1_500_000
DEFAULT_SEARCH_NODE_CAP = 30_000
DEFAULT_PROBE_NODE_CAP = 15_000
DEFAULT_PASS_CAP = 400
DEFAULT_RIP_CAP = 6
DEFAULT_TABU_TENURE = 6
#: Consecutive passes allowed to pass without improving the best board. A net
#: that ten different orderings could not close is not going to yield to the
#: eleventh, and the budget buys more elsewhere.
DEFAULT_STALL_LIMIT = 24

_SQRT2 = math.sqrt(2.0)
_OCTILE = _SQRT2 - 1.0
_BIG = 1e9
_LAYERS = (TOP, BOTTOM)


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def _pitch_for(area_mm2: float) -> float:
    """Grid pitch from board area, rounded up to a 0.05mm step.

    Deterministic and monotone: the same board always gets the same grid, and a
    bigger board never gets a finer one.
    """
    if area_mm2 <= 0:
        return MIN_PITCH_MM
    raw = math.sqrt(area_mm2 / TARGET_CELLS)
    stepped = math.ceil(raw / PITCH_STEP_MM - 1e-9) * PITCH_STEP_MM
    return round(min(MAX_PITCH_MM, max(MIN_PITCH_MM, stepped)), 4)


class _Grid:
    """Static free space, one float per cell per layer.

    ``avail[layer][cell]`` is the widest copper half-width that may sit anywhere
    inside that cell. Negative means no copper at all. Built once per
    ``route()`` and never mutated: everything after it is dynamic occupancy on
    top.
    """

    __slots__ = (
        "problem", "pitch", "x0", "y0", "nx", "ny", "ncells",
        "avail", "hole_warn", "plane_cells", "via_ok", "inside",
    )

    def __init__(self, problem: RoutingProblem, pitch: float | None = None):
        self.problem = problem
        board = problem.board
        outline = board.outline
        if len(outline) >= 3:
            xs = [p.x for p in outline]
            ys = [p.y for p in outline]
            bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
        else:
            bx0, by0, bx1, by1 = board.bbox
        area = max(1.0, (bx1 - bx0) * (by1 - by0))
        self.pitch = _pitch_for(area) if pitch is None else float(pitch)
        p = self.pitch
        self.x0 = bx0 - p
        self.y0 = by0 - p
        self.nx = max(1, int(math.ceil((bx1 - bx0) / p)) + 2)
        self.ny = max(1, int(math.ceil((by1 - by0) / p)) + 2)
        self.ncells = self.nx * self.ny

        self.avail: list[list[float]] = [
            [-_BIG] * self.ncells for _ in _LAYERS
        ]
        self.hole_warn: set[int] = set()
        self.plane_cells: list[set[int]] = [set(), set()]

        self._mark_inside(outline, (bx0, by0, bx1, by1))
        self._stamp_edges(outline, (bx0, by0, bx1, by1))
        self._stamp_obstacles()
        for plane in problem.planes:
            index = 0 if plane.layer == TOP else 1
            self.plane_cells[index] |= self._cells_in_polygon(plane.outline)
        half = problem.rules.via_pad_mm / 2.0
        top, bottom = self.avail
        self.via_ok = bytearray(
            1 if (top[i] >= half and bottom[i] >= half) else 0
            for i in range(self.ncells)
        )

    # -- construction ----------------------------------------------------

    def _mark_inside(self, outline, bbox) -> None:
        """Everything starts blocked; the board's interior is opened by a
        scanline over the real outline. One pass over the outline's thousand
        tessellated edges per grid row, not one per cell."""
        nx, ny, p, x0, y0 = self.nx, self.ny, self.pitch, self.x0, self.y0
        inside = bytearray(self.ncells)
        spans = self._interior_spans(outline, bbox)
        for base, i0, i1 in spans:
            inside[base + i0 : base + i1 + 1] = b"\x01" * (i1 - i0 + 1)
        self.inside = inside
        for layer_avail in self.avail:
            for base, i0, i1 in spans:
                layer_avail[base + i0 : base + i1 + 1] = [_BIG] * (i1 - i0 + 1)

    def _interior_spans(self, outline, bbox) -> list[tuple[int, int, int]]:
        nx, ny, p, x0, y0 = self.nx, self.ny, self.pitch, self.x0, self.y0
        out: list[tuple[int, int, int]] = []
        if len(outline) >= 3:
            for j, crossings in self._row_crossings(outline).items():
                base = j * nx
                for k in range(0, len(crossings) - 1, 2):
                    i0 = max(0, int(math.ceil((crossings[k] - x0) / p - 0.5)))
                    i1 = min(nx - 1,
                             int(math.floor((crossings[k + 1] - x0) / p - 0.5)))
                    if i1 >= i0:
                        out.append((base, i0, i1))
            return out
        ax0, ay0, ax1, ay1 = bbox
        for j in range(ny):
            y = y0 + (j + 0.5) * p
            if not (ay0 <= y <= ay1):
                continue
            i0 = max(0, int(math.ceil((ax0 - x0) / p - 0.5)))
            i1 = min(nx - 1, int(math.floor((ax1 - x0) / p - 0.5)))
            if i1 >= i0:
                out.append((j * nx, i0, i1))
        return out

    def _row_crossings(self, poly: Sequence[Point]) -> dict[int, list[float]]:
        """Sorted x-crossings of ``poly`` with each grid row's centre line."""
        ny, p, y0 = self.ny, self.pitch, self.y0
        rows: dict[int, list[tuple[float, float, float, float]]] = {}
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            lo = int(math.floor((min(a.y, b.y) - y0) / p))
            hi = int(math.floor((max(a.y, b.y) - y0) / p))
            edge = (a.x, a.y, b.x, b.y)
            for j in range(max(0, lo), min(ny - 1, hi) + 1):
                rows.setdefault(j, []).append(edge)
        out: dict[int, list[float]] = {}
        for j in sorted(rows):
            y = y0 + (j + 0.5) * p
            crossings = [
                (ex1 - ex0) * (y - ey0) / (ey1 - ey0) + ex0
                for ex0, ey0, ex1, ey1 in rows[j]
                if (ey0 > y) != (ey1 > y)
            ]
            if len(crossings) >= 2:
                crossings.sort()
                out[j] = crossings
        return out

    def _cells_in_polygon(self, poly: Sequence[Point]) -> set[int]:
        if len(poly) < 3:
            return set()
        nx, p, x0 = self.nx, self.pitch, self.x0
        out: set[int] = set()
        for j, crossings in self._row_crossings(poly).items():
            base = j * nx
            for k in range(0, len(crossings) - 1, 2):
                i0 = max(0, int(math.ceil((crossings[k] - x0) / p - 0.5)))
                i1 = min(nx - 1,
                         int(math.floor((crossings[k + 1] - x0) / p - 0.5)))
                if i1 >= i0:
                    out.update(range(base + i0, base + i1 + 1))
        return out

    def _stamp_edges(self, outline, bbox) -> None:
        limit = self.problem.rules.min_edge_clearance_mm + EDGE_MARGIN_MM
        if len(outline) >= 3:
            n = len(outline)
            for i in range(n):
                a, b = outline[i], outline[(i + 1) % n]
                self._stamp((a.x, a.y, b.x, b.y, 0.0), limit, (0, 1))
        else:
            ax0, ay0, ax1, ay1 = bbox
            for x0e, y0e, x1e, y1e in (
                (ax0, ay0, ax1, ay0), (ax1, ay0, ax1, ay1),
                (ax1, ay1, ax0, ay1), (ax0, ay1, ax0, ay0),
            ):
                self._stamp((x0e, y0e, x1e, y1e, 0.0), limit, (0, 1))

    def _stamp_obstacles(self) -> None:
        from routerlib.geometry import (
            disc_capsule,
            drill_capsule,
            keepout_capsule,
            pad_capsule,
            segment_capsule,
            stadium_capsule,
        )

        problem = self.problem
        rules = problem.rules
        copper_gap = rules.target_clearance_mm

        for pad in problem.pads:
            self._stamp(pad_capsule(pad), copper_gap,
                        self._layer_indexes(pad.layers))

        for drill in problem.drills:
            needed = rules.hole_clearance(drill) + HOLE_MARGIN_MM
            # Both readings of the same hole, on purpose. routerlib honours
            # ``ccw_rotation`` on a slot and circuitpy.checks does not (README,
            # "known divergence"), so a rotated USB-C alignment slot is two
            # different obstacles depending on who is asking. Stamping the union
            # satisfies whichever one turns out to be right.
            shapes = [drill_capsule(drill)]
            if drill.rotation_deg and abs(drill.width_mm - drill.height_mm) > 1e-9:
                shapes.append(
                    stadium_capsule(drill.center.x, drill.center.y,
                                    drill.width_mm, drill.height_mm, 0.0)
                )
            warn_at = (
                HOLE_WARN_BAND_MM
                if drill.plated and drill.pad_id is not None
                else None
            )
            for shape in shapes:
                self._stamp(shape, needed, (0, 1), warn_clearance=warn_at)

        for keepout in problem.keepouts:
            self._stamp(
                keepout_capsule(keepout),
                EDGE_MARGIN_MM,
                self._layer_indexes(keepout.layers),
            )

        for trace in problem.existing_traces:
            layers = self._layer_indexes((trace.layer,))
            for a, b in trace.segments:
                self._stamp(
                    segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm),
                    copper_gap, layers,
                )
        for via in problem.existing_vias:
            self._stamp(
                disc_capsule(via.center.x, via.center.y, via.pad_mm),
                copper_gap, (0, 1),
            )

    # -- the rasteriser --------------------------------------------------

    def _stamp(
        self,
        capsule: tuple[float, float, float, float, float],
        clearance: float,
        layers: Sequence[int],
        *,
        warn_clearance: float | None = None,
    ) -> None:
        """Lower ``avail`` in every cell this obstacle constrains.

        Distance is measured from the **cell square** to the capsule's spine,
        which is exact and cheap for an axis-aligned spine (the common case: a
        round drill, a Manhattan trace) and falls back to four
        segment-to-segment gaps otherwise. Measuring from the square rather than
        the centre is what lets a path through cell centres be trusted without
        re-checking its interior.

        A shape with a core — a rectangular pad, a keepout, a polygon pad —
        takes the branch below instead, because its spine is not its shape.
        """
        core = getattr(capsule, "core", None)
        if core is not None:
            self._stamp_core(
                capsule, clearance, layers, warn_clearance=warn_clearance
            )
            return
        ax, ay, bx, by, r = capsule
        reach = r + max(clearance, warn_clearance or 0.0) + MAX_HALF_MM
        p, x0, y0, nx, ny = self.pitch, self.x0, self.y0, self.nx, self.ny
        i0 = max(0, int(math.floor((min(ax, bx) - reach - x0) / p)))
        i1 = min(nx - 1, int(math.floor((max(ax, bx) + reach - x0) / p)))
        j0 = max(0, int(math.floor((min(ay, by) - reach - y0) / p)))
        j1 = min(ny - 1, int(math.floor((max(ay, by) + reach - y0) / p)))
        if i1 < i0 or j1 < j0:
            return
        axis = abs(ax - bx) < 1e-9 or abs(ay - by) < 1e-9
        sx0, sx1 = (ax, bx) if ax <= bx else (bx, ax)
        sy0, sy1 = (ay, by) if ay <= by else (by, ay)
        targets = [self.avail[i] for i in layers]
        warn = self.hole_warn if warn_clearance is not None else None
        hypot = math.hypot
        for j in range(j0, j1 + 1):
            cy0 = y0 + j * p
            cy1 = cy0 + p
            base = j * nx
            for i in range(i0, i1 + 1):
                cx0 = x0 + i * p
                cx1 = cx0 + p
                if axis:
                    dx = sx0 - cx1 if sx0 > cx1 else (cx0 - sx1 if cx0 > sx1 else 0.0)
                    dy = sy0 - cy1 if sy0 > cy1 else (cy0 - sy1 if cy0 > sy1 else 0.0)
                    d = hypot(dx, dy) if (dx or dy) else 0.0
                else:
                    d = min(
                        segment_gap(ax, ay, bx, by, cx0, cy0, cx1, cy0),
                        segment_gap(ax, ay, bx, by, cx1, cy0, cx1, cy1),
                        segment_gap(ax, ay, bx, by, cx1, cy1, cx0, cy1),
                        segment_gap(ax, ay, bx, by, cx0, cy1, cx0, cy0),
                    )
                free = d - r - clearance
                idx = base + i
                for layer_avail in targets:
                    if free < layer_avail[idx]:
                        layer_avail[idx] = free
                if warn is not None and d - r < warn_clearance:
                    warn.add(idx)

    def _stamp_core(
        self,
        capsule,
        clearance: float,
        layers: Sequence[int],
        *,
        warn_clearance: float | None = None,
    ) -> None:
        """:meth:`_stamp` for a shape that is not a stadium.

        The cell square is measured against the core's own outward edge lines
        rather than against a spine: negative inside the shape, the true
        distance outside it, and a slight under-read in the wedge past a
        corner, which costs the router a little space and never lends it any.
        Read as an inscribed stadium instead, a 1.0mm square pad's corner was
        0.21mm of free space that is not there.
        """
        planes = core_halfplanes(capsule)
        if not planes:
            return
        sweep = capsule.sweep
        reach = sweep + max(clearance, warn_clearance or 0.0) + MAX_HALF_MM
        p, x0, y0, nx, ny = self.pitch, self.x0, self.y0, self.nx, self.ny
        bx0, by0, bx1, by1 = capsule_bbox(capsule)
        i0 = max(0, int(math.floor((bx0 - reach - x0) / p)))
        i1 = min(nx - 1, int(math.floor((bx1 + reach - x0) / p)))
        j0 = max(0, int(math.floor((by0 - reach - y0) / p)))
        j1 = min(ny - 1, int(math.floor((by1 + reach - y0) / p)))
        if i1 < i0 or j1 < j0:
            return
        targets = [self.avail[i] for i in layers]
        warn = self.hole_warn if warn_clearance is not None else None
        for j in range(j0, j1 + 1):
            cy0 = y0 + j * p
            cy1 = cy0 + p
            base = j * nx
            for i in range(i0, i1 + 1):
                cx0 = x0 + i * p
                cx1 = cx0 + p
                d = -1e18
                for pnx, pny, off in planes:
                    # The corner of the cell square furthest into this
                    # half-plane's inside is the one that decides the square.
                    x = cx0 if pnx > 0.0 else cx1
                    y = cy0 if pny > 0.0 else cy1
                    value = pnx * x + pny * y - off
                    if value > d:
                        d = value
                if d > reach:
                    continue
                free = d - sweep - clearance
                idx = base + i
                for layer_avail in targets:
                    if free < layer_avail[idx]:
                        layer_avail[idx] = free
                if warn is not None and d - sweep < warn_clearance:
                    warn.add(idx)

    # -- lookups ---------------------------------------------------------

    def _layer_indexes(self, layers: Iterable[str]) -> tuple[int, ...]:
        out = []
        for layer in layers:
            if layer == TOP:
                out.append(0)
            elif layer == BOTTOM:
                out.append(1)
        return tuple(out) or (0,)

    def center(self, cell: int) -> Point:
        i = cell % self.nx
        j = cell // self.nx
        return Point(self.x0 + (i + 0.5) * self.pitch,
                     self.y0 + (j + 0.5) * self.pitch)

    def cells_near(self, point: Point, radius: float) -> list[int]:
        """Cells whose centre is within ``radius``, nearest first, ties broken
        by index so the order never depends on a hash."""
        p, x0, y0, nx, ny = self.pitch, self.x0, self.y0, self.nx, self.ny
        i0 = max(0, int(math.floor((point.x - radius - x0) / p)))
        i1 = min(nx - 1, int(math.floor((point.x + radius - x0) / p)))
        j0 = max(0, int(math.floor((point.y - radius - y0) / p)))
        j1 = min(ny - 1, int(math.floor((point.y + radius - y0) / p)))
        out: list[tuple[float, int]] = []
        r2 = radius * radius
        for j in range(j0, j1 + 1):
            cy = y0 + (j + 0.5) * p
            base = j * nx
            for i in range(i0, i1 + 1):
                cx = x0 + (i + 0.5) * p
                d2 = (cx - point.x) ** 2 + (cy - point.y) ** 2
                if d2 <= r2:
                    out.append((d2, base + i))
        out.sort()
        return [cell for _, cell in out]


# ---------------------------------------------------------------------------
# Dynamic occupancy: what the routed nets have taken so far
# ---------------------------------------------------------------------------


class _Occupancy:
    """Copper the router itself has placed, indexed so it can be taken back.

    ``block[layer][cell]`` maps a net id to the widest half-width that net still
    leaves usable in that cell. Keeping contributions *per net* is what makes
    rip-up exact: removing a net removes exactly its own entries and the cell
    reverts to whatever the remaining nets left, with no rebuild and no drift.
    """

    __slots__ = ("grid", "block", "owned")

    def __init__(self, grid: _Grid):
        self.grid = grid
        self.block: list[dict[int, dict[str, float]]] = [{}, {}]
        self.owned: dict[str, list[tuple[int, int]]] = {}

    def _stamp(
        self, net: str, layers: Sequence[int],
        capsule: tuple[float, float, float, float, float], clearance: float,
    ) -> None:
        grid = self.grid
        ax, ay, bx, by, r = capsule
        reach = r + clearance + MAX_HALF_MM
        p, x0, y0, nx, ny = grid.pitch, grid.x0, grid.y0, grid.nx, grid.ny
        i0 = max(0, int(math.floor((min(ax, bx) - reach - x0) / p)))
        i1 = min(nx - 1, int(math.floor((max(ax, bx) + reach - x0) / p)))
        j0 = max(0, int(math.floor((min(ay, by) - reach - y0) / p)))
        j1 = min(ny - 1, int(math.floor((max(ay, by) + reach - y0) / p)))
        if i1 < i0 or j1 < j0:
            return
        axis = abs(ax - bx) < 1e-9 or abs(ay - by) < 1e-9
        sx0, sx1 = (ax, bx) if ax <= bx else (bx, ax)
        sy0, sy1 = (ay, by) if ay <= by else (by, ay)
        owned = self.owned.setdefault(net, [])
        hypot = math.hypot
        for layer in layers:
            table = self.block[layer]
            for j in range(j0, j1 + 1):
                cy0 = y0 + j * p
                cy1 = cy0 + p
                base = j * nx
                for i in range(i0, i1 + 1):
                    cx0 = x0 + i * p
                    cx1 = cx0 + p
                    if axis:
                        dx = sx0 - cx1 if sx0 > cx1 else (cx0 - sx1 if cx0 > sx1 else 0.0)
                        dy = sy0 - cy1 if sy0 > cy1 else (cy0 - sy1 if cy0 > sy1 else 0.0)
                        d = hypot(dx, dy) if (dx or dy) else 0.0
                    else:
                        d = min(
                            segment_gap(ax, ay, bx, by, cx0, cy0, cx1, cy0),
                            segment_gap(ax, ay, bx, by, cx1, cy0, cx1, cy1),
                            segment_gap(ax, ay, bx, by, cx1, cy1, cx0, cy1),
                            segment_gap(ax, ay, bx, by, cx0, cy1, cx0, cy0),
                        )
                    free = d - r - clearance
                    if free >= MAX_HALF_MM:
                        continue
                    idx = base + i
                    entry = table.get(idx)
                    if entry is None:
                        table[idx] = {net: free}
                        owned.append((layer, idx))
                    elif net not in entry:
                        entry[net] = free
                        owned.append((layer, idx))
                    elif free < entry[net]:
                        entry[net] = free

    def add_trace(self, net: str, layer: str, points: Sequence[Point],
                  width: float, clearance: float) -> None:
        layers = (0,) if layer == TOP else (1,)
        half = width / 2.0
        for a, b in zip(points, points[1:]):
            self._stamp(net, layers, (a.x, a.y, b.x, b.y, half), clearance)

    def add_via(self, net: str, center: Point, pad_mm: float,
                clearance: float) -> None:
        self._stamp(net, (0, 1),
                    (center.x, center.y, center.x, center.y, pad_mm / 2.0),
                    clearance)

    def remove_net(self, net: str) -> None:
        for layer, idx in self.owned.pop(net, ()):
            entry = self.block[layer].get(idx)
            if entry is None:
                continue
            entry.pop(net, None)
            if not entry:
                del self.block[layer][idx]

    def nets_blocking(self, layer: int, cell: int, half: float,
                      net: str) -> list[str]:
        entry = self.block[layer].get(cell)
        if not entry:
            return []
        return sorted(n for n, free in entry.items() if n != net and free < half)


# ---------------------------------------------------------------------------
# Per-net bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class _NetPlan:
    """What connecting one net requires, and how far we got.

    ``links`` is a spanning tree over the net's pads — or, for a net that owns a
    pour, one link per pad that the pour does not already reach. Fixing the tree
    once means a rerouted net asks for exactly the same connections it asked for
    the first time, which is what makes a rip-up comparable to the attempt it
    replaced.
    """

    net: Net
    links: list[tuple[str, str]]
    done: set[int] = field(default_factory=set)
    #: Links that failed even with every other net's copper made passable.
    hopeless: set[int] = field(default_factory=set)
    attempts: int = 0
    mst_mm: float = 0.0
    necked: bool = False
    #: Free grid cells around this net's tightest pad, on the empty board.
    #: Measured, not guessed at from a footprint name.
    escape_room: int = 0

    @property
    def complete(self) -> bool:
        return len(self.done) == len(self.links)

    @property
    def missing(self) -> int:
        return len(self.links) - len(self.done)

    @property
    def solvable_missing(self) -> int:
        return sum(
            1 for i in range(len(self.links))
            if i not in self.done and i not in self.hopeless
        )


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


class RipUpRerouteRouter:
    """Grid A* per connection, then conflict-driven rip-up over the failures."""

    name = "ripup-reroute"

    def __init__(
        self,
        *,
        rip_up_passes: int = DEFAULT_PASS_CAP,
        rip_cap: int = DEFAULT_RIP_CAP,
        tabu_tenure: int = DEFAULT_TABU_TENURE,
        stall_limit: int = DEFAULT_STALL_LIMIT,
        node_cap: int = DEFAULT_NODE_CAP,
        search_node_cap: int = DEFAULT_SEARCH_NODE_CAP,
        probe_node_cap: int = DEFAULT_PROBE_NODE_CAP,
        pitch: float | None = None,
    ) -> None:
        self.rip_up_passes = rip_up_passes
        self.rip_cap = rip_cap
        self.tabu_tenure = tabu_tenure
        self.stall_limit = stall_limit
        self.node_cap = node_cap
        self.search_node_cap = search_node_cap
        self.probe_node_cap = probe_node_cap
        self.pitch = pitch

    # -- entry point -----------------------------------------------------

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        started = time.perf_counter()
        meter = budget.meter()
        self._rng = random.Random(budget.seed)
        self._problem = problem
        self._meter = meter
        self._notes: list[str] = []
        self._nodes = 0
        self._capped = False
        self._node_cap = min(self.node_cap, budget.max_nodes)

        self._grid = _Grid(problem, self.pitch)
        self._occ = _Occupancy(self._grid)
        self._hist: list[dict[int, float]] = [{}, {}]
        self._traces: list[Trace] = []
        self._vias: list[Via] = []
        # Cells too close to an existing via's *drill* to take another one.
        # Hole-to-hole is the one rule that does not care whose net it is: two
        # barrels 0.45mm apart break out into each other whatever they carry,
        # and ``Workspace.via_ok`` says so. The grid did not, which is why an
        # early run logged fifty-two "grid proposed an illegal via on GND —
        # dropped" on one five-net board, each one a connection thrown away.
        self._via_block: set[int] = set()
        self._via_gap_mm = (
            problem.rules.via_drill_mm + problem.rules.min_hole_to_hole_mm - 1e-9
        )
        self._seq = 0
        self._ws = Workspace(problem)
        self._planes_by_net: dict[str, list[Plane]] = {}
        for plane in problem.planes:
            self._planes_by_net.setdefault(plane.net, []).append(plane)
        self._penalty_cache: dict[str, list[set[int] | None]] = {}
        # The copper and holes whose shape the two geometry engines disagree
        # about: turned, non-square. Everything else reads the same both ways
        # and needs no second opinion.
        self._skew_pads = [
            (pad, _stadium_unrotated(pad))
            for pad in problem.pads
            if pad.is_smd and pad.rotation_deg
            and abs(pad.width_mm - pad.height_mm) > 1e-9
        ]
        self._skew_drills = [
            (drill, _stadium_unrotated(drill))
            for drill in problem.drills
            if drill.rotation_deg
            and abs(drill.width_mm - drill.height_mm) > 1e-9
        ]

        self._plans = {p.net.id: p for p in self._build_plans(problem)}
        self._escape_room: dict[str, int] = {}
        for plan in self._plans.values():
            plan.escape_room = min(
                (self._pad_escape_room(pad)
                 for pad in problem.pads_of(plan.net.id)),
                default=0,
            )
        # Most-constrained-first. A net with a pin inside a 0.5mm-pitch
        # connector has almost nowhere to leave from, and if a 0.5mm rail is
        # routed past that pin first there is nowhere at all. Measuring the
        # escape room on the empty board and taking the tightest net first is
        # the cheap version of what a real flow does with a separate fanout
        # phase. Class priority is the tie-break, not the primary key.
        order = sorted(
            self._plans.values(),
            key=lambda p: (p.escape_room, p.net.priority, round(p.mst_mm, 3),
                           p.net.name, p.net.id),
        )
        for plan in order:
            if self._out_of_budget():
                break
            self._route_net(plan)

        best = self._snapshot()
        passes_used, best = self._rip_up_loop(best)
        self._restore_plans(best)

        traces, vias = best[2], best[3]
        unrouted = tuple(sorted(
            plan.net.id for plan in self._plans.values() if not plan.complete
        ))
        self._report(passes_used)
        meter.expand(self._nodes)
        return RoutingSolution(
            router=self.name,
            traces=tuple(traces),
            vias=tuple(vias),
            complete=not unrouted,
            unrouted_nets=unrouted,
            iterations=meter.iterations,
            nodes_expanded=self._nodes,
            wall_clock_s=time.perf_counter() - started,
            notes=tuple(self._notes),
        )

    def _report(self, passes_used: int) -> None:
        grid = self._grid
        self._notes.append(
            f"grid {grid.pitch:g}mm, {grid.nx}x{grid.ny} cells, "
            f"{passes_used} rip-up pass(es), {self._nodes} nodes expanded"
        )
        if self._nodes >= self._node_cap:
            self._notes.append(
                f"stopped on the {self._node_cap} node cap — the answer is the "
                "best board found inside it, not a proof that no better one exists"
            )
        if self._meter.stop_reason == "wall_clock":
            self._notes.append(
                "hit the wall-clock safety valve — not a comparable result, "
                "only evidence that the router hung"
            )
        necked = sorted(p.net.name for p in self._plans.values() if p.necked)
        if necked:
            self._notes.append(
                f"{len(necked)} net(s) routed below their class width to close a "
                f"connection: {', '.join(necked[:6])}"
                + (" ..." if len(necked) > 6 else "")
            )
        hopeless = sum(
            1 for p in self._plans.values()
            if p.missing > 0 and p.solvable_missing == 0
        )
        if hopeless:
            self._notes.append(
                f"{hopeless} net(s) have a connection no rip-up can open — "
                "blocked by placement geometry, not by other copper"
            )

    # -- planning --------------------------------------------------------

    def _build_plans(self, problem: RoutingProblem) -> list[_NetPlan]:
        plans: list[_NetPlan] = []
        for net in problem.nets:
            if not net.routable:
                continue
            pads = sorted(problem.pads_of(net.id), key=lambda p: p.id)
            if len(pads) < 2:
                continue
            planes = self._planes_by_net.get(net.id)
            if planes:
                links = [
                    (pad.id, "__plane__")
                    for pad in pads
                    if not _pad_in_plane(pad, planes)
                ]
                mst = 0.0
            else:
                edges = _mst_edges(pads)
                links = [(a.id, b.id) for a, b in edges]
                mst = sum(a.center.distance_to(b.center) for a, b in edges)
            plans.append(_NetPlan(net=net, links=links, mst_mm=mst))
        return plans

    #: Half-width the escape-room measurement probes with: one signal trace.
    #: Fixed for every net on purpose — the number has to describe the
    #: *placement*, not the net class, or a rail would look constrained
    #: everywhere and the ordering would say nothing.
    ESCAPE_PROBE_HALF_MM = 0.10

    def _pad_escape_room(self, pad: Pad) -> int:
        """How many free grid cells a pad could leave from on the empty board."""
        cached = self._escape_room.get(pad.id)
        if cached is not None:
            return cached
        grid = self._grid
        half = self.ESCAPE_PROBE_HALF_MM
        reach = min(
            2.6,
            max(pad.width_mm, pad.height_mm) / 2.0
            + self._problem.rules.target_clearance_mm + half + 0.9,
        )
        count = 0
        for layer_index, layer in enumerate(_LAYERS):
            if layer not in pad.layers:
                continue
            layer_avail = grid.avail[layer_index]
            for cell in grid.cells_near(pad.center, reach):
                if layer_avail[cell] >= half:
                    count += 1
        self._escape_room[pad.id] = count
        return count

    # -- routing one net -------------------------------------------------

    def _route_net(self, plan: _NetPlan) -> None:
        pads = self._problem.pads_by_id
        for index, (a_id, b_id) in enumerate(plan.links):
            if index in plan.done or index in plan.hopeless:
                continue
            if self._out_of_budget():
                return
            self._meter.tick()
            if self._route_link(plan, pads.get(a_id), b_id):
                plan.done.add(index)

    def _route_link(self, plan: _NetPlan, pad_a: Pad | None, b_id: str) -> bool:
        if pad_a is None:
            return False
        to_plane = b_id == "__plane__"
        pad_b = None if to_plane else self._problem.pads_by_id.get(b_id)
        if not to_plane and pad_b is None:
            return False
        widths = _width_ladder(plan.net.min_width_mm, self._problem.rules)
        outcome = self._attempt(plan, pad_a, pad_b, widths, conflict=False)
        if outcome is None:
            return False
        found, width = outcome
        if not self._commit(plan, found, width):
            return False
        if width < widths[0] - 1e-9:
            plan.necked = True
        return True

    # -- one connection attempt ------------------------------------------

    def _attempt(self, plan: _NetPlan, pad_a: Pad, pad_b: Pad | None,
                 widths: Sequence[float], *, conflict: bool):
        """Return ``((path, sources, targets), width)`` or ``None``.

        ``pad_b is None`` means the target is the net's own poured plane.

        Boxes are the outer loop and widths the inner one, which is not an
        accident: a failed search costs roughly its box, so sweeping every width
        through every box would pay the whole-board level once per width. This
        way the cheap box answers first for whichever width fits, and the
        expensive level is reached at most once.
        """
        net_id = plan.net.id
        # In conflict mode the escape must ignore other nets' copper too.
        # Otherwise a pin whose every escape cell is currently covered looks
        # geometrically impossible, gets marked hopeless, and rip-up never even
        # asks who covered it — exactly the failure this router exists to fix.
        access: dict[float, tuple[dict, dict | None]] = {}
        for width in widths:
            half = width / 2.0
            sources = self._access(pad_a, half, width, net_id, soft=conflict)
            if not sources:
                continue
            if pad_b is not None:
                targets = self._access(pad_b, half, width, net_id, soft=conflict)
                if not targets:
                    continue
            else:
                targets = None
            access[width] = (sources, targets)
        if not access:
            return None
        if pad_b is not None:
            lo, hi = _bbox_of((pad_a.center, pad_b.center))
        else:
            lo, hi = _bbox_of((pad_a.center,))

        for step, slack in enumerate(BOX_SLACK_MM):
            box = (lo[0] - slack, lo[1] - slack, hi[0] + slack, hi[1] + slack)
            for width in widths:
                entry = access.get(width)
                if entry is None:
                    continue
                sources, targets = entry
                half = width / 2.0
                if targets is None:
                    targets = self._plane_targets(
                        plan, half, pad_a.center, PLANE_TARGET_RADIUS_MM[step]
                    )
                    if not targets:
                        continue
                found = self._search(plan, sources, targets, half, box,
                                     conflict=conflict)
                if found is not None:
                    return (found, width)
                if self._out_of_budget():
                    return None
            if slack >= 1e8:
                break
        return None

    def _search(self, plan: _NetPlan, sources: dict, targets: dict,
                half: float, box, *, conflict: bool):
        grid = self._grid
        occ = self._occ
        nx, ncells, pitch = grid.nx, grid.ncells, grid.pitch
        gx0, gy0 = grid.x0, grid.y0
        avail = grid.avail
        blocks = occ.block
        hist = self._hist
        hole_warn = grid.hole_warn
        via_ok = grid.via_ok
        via_block = self._via_block
        net_id = plan.net.id
        penalty = self._plane_penalty(net_id)
        via_half = self._problem.rules.via_pad_mm / 2.0
        plane_step = PLANE_CROSS_PER_MM * pitch

        i_lo = max(0, int(math.floor((box[0] - gx0) / pitch)))
        i_hi = min(nx - 1, int(math.floor((box[2] - gx0) / pitch)))
        j_lo = max(0, int(math.floor((box[1] - gy0) / pitch)))
        j_hi = min(grid.ny - 1, int(math.floor((box[3] - gy0) / pitch)))
        if i_hi < i_lo or j_hi < j_lo:
            return None

        tx0, ty0, tx1, ty1 = _target_bbox(grid, targets)
        diag = pitch * _SQRT2

        heap: list[tuple[float, int, int]] = []
        g: dict[int, float] = {}
        came: dict[int, int] = {}
        counter = 0
        for node, (cost, _stub) in sorted(sources.items()):
            g[node] = cost
            cell = node % ncells
            x = gx0 + (cell % nx + 0.5) * pitch
            y = gy0 + (cell // nx + 0.5) * pitch
            dx = tx0 - x if x < tx0 else (x - tx1 if x > tx1 else 0.0)
            dy = ty0 - y if y < ty0 else (y - ty1 if y > ty1 else 0.0)
            est = (dx + _OCTILE * dy) if dx > dy else (dy + _OCTILE * dx)
            heapq.heappush(heap, (cost + est, counter, node))
            counter += 1
        closed: set[int] = set()
        cap = self.probe_node_cap if conflict else self.search_node_cap
        budget_left = min(cap, max(0, self._node_cap - self._nodes))
        expanded = 0
        goal: int | None = None
        push = heapq.heappush
        pop = heapq.heappop

        while heap:
            _f, _tie, node = pop(heap)
            if node in closed:
                continue
            closed.add(node)
            expanded += 1
            if expanded > budget_left:
                self._capped = True
                break
            if node in targets:
                goal = node
                break
            base_cost = g[node]
            layer = node // ncells
            cell = node - layer * ncells
            i = cell % nx
            j = cell // nx
            layer_avail = avail[layer]
            layer_block = blocks[layer]
            layer_hist = hist[layer]
            layer_plane = penalty[layer]
            row = j * nx

            for di, dj, cost in (
                (1, 0, pitch), (-1, 0, pitch), (0, 1, pitch), (0, -1, pitch),
                (1, 1, diag), (1, -1, diag), (-1, 1, diag), (-1, -1, diag),
            ):
                ni = i + di
                nj = j + dj
                if ni < i_lo or ni > i_hi or nj < j_lo or nj > j_hi:
                    continue
                ncell = nj * nx + ni
                if layer_avail[ncell] < half:
                    continue
                if di and dj:
                    # A diagonal step's copper bulges into both side cells at
                    # the shared corner, so all four have to clear. This is the
                    # no-corner-cutting rule, and it also stops the router
                    # squeezing through a gap that is not there.
                    if layer_avail[row + ni] < half:
                        continue
                    if layer_avail[nj * nx + i] < half:
                        continue
                extra = 0.0
                if layer_block:
                    entry = layer_block.get(ncell)
                    if entry:
                        worst = MAX_HALF_MM
                        for other, free in entry.items():
                            if other != net_id and free < worst:
                                worst = free
                        if worst < half:
                            if not conflict:
                                continue
                            extra = CONFLICT_COST_MM
                if ncell in hole_warn:
                    extra += HOLE_WARN_COST_MM
                if layer_plane is not None and ncell in layer_plane:
                    extra += plane_step
                if layer_hist:
                    extra += layer_hist.get(ncell, 0.0)
                nnode = layer * ncells + ncell
                tentative = base_cost + cost + extra
                if tentative < g.get(nnode, 1e18) - 1e-12:
                    g[nnode] = tentative
                    came[nnode] = node
                    x = gx0 + (ni + 0.5) * pitch
                    y = gy0 + (nj + 0.5) * pitch
                    dx = tx0 - x if x < tx0 else (x - tx1 if x > tx1 else 0.0)
                    dy = ty0 - y if y < ty0 else (y - ty1 if y > ty1 else 0.0)
                    est = (dx + _OCTILE * dy) if dx > dy else (dy + _OCTILE * dx)
                    push(heap, (tentative + est, counter, nnode))
                    counter += 1

            if via_ok[cell] and cell not in via_block:
                other_layer = 1 - layer
                onode = other_layer * ncells + cell
                extra = VIA_COST_MM
                blocked = False
                for check in (0, 1):
                    entry = blocks[check].get(cell)
                    if not entry:
                        continue
                    worst = MAX_HALF_MM
                    for other, free in entry.items():
                        if other != net_id and free < worst:
                            worst = free
                    if worst < via_half:
                        if not conflict:
                            blocked = True
                            break
                        extra += CONFLICT_COST_MM
                if not blocked:
                    if cell in hole_warn:
                        extra += HOLE_WARN_COST_MM
                    other_hist = hist[other_layer]
                    if other_hist:
                        extra += other_hist.get(cell, 0.0)
                    tentative = base_cost + extra
                    if tentative < g.get(onode, 1e18) - 1e-12:
                        g[onode] = tentative
                        came[onode] = node
                        x = gx0 + (i + 0.5) * pitch
                        y = gy0 + (j + 0.5) * pitch
                        dx = tx0 - x if x < tx0 else (x - tx1 if x > tx1 else 0.0)
                        dy = ty0 - y if y < ty0 else (y - ty1 if y > ty1 else 0.0)
                        est = (dx + _OCTILE * dy) if dx > dy else (dy + _OCTILE * dx)
                        push(heap, (tentative + est, counter, onode))
                        counter += 1

        self._nodes += expanded
        if goal is None:
            return None
        path = [goal]
        while path[-1] in came:
            path.append(came[path[-1]])
        path.reverse()
        return (path, sources, targets)

    # -- turning a path into copper --------------------------------------

    def _commit(self, plan: _NetPlan, found, width: float) -> bool:
        path, sources, targets = found
        grid = self._grid
        ncells = grid.ncells
        net_id = plan.net.id
        clearance = self._problem.rules.target_clearance_mm

        start_stub = sources[path[0]][1]
        end_stub = targets[path[-1]][1]

        runs: list[tuple[int, list[Point]]] = []
        via_cells: list[int] = []
        current_layer = path[0] // ncells
        current: list[Point] = [grid.center(path[0] % ncells)]
        for node in path[1:]:
            layer = node // ncells
            cell = node - layer * ncells
            point = grid.center(cell)
            if layer != current_layer:
                runs.append((current_layer, current))
                via_cells.append(cell)
                current_layer = layer
                current = [point]
            else:
                current.append(point)
        runs.append((current_layer, current))

        # Attach the exact endpoints: the pad centre at one end, the other pad's
        # centre (or nothing, for a pour) at the other. These two segments are
        # the only copper the grid cannot vouch for, because they deliberately
        # run into the net's own pad — so they are checked exactly, below.
        if start_stub is not None:
            runs[0][1].insert(0, start_stub)
        if end_stub is not None:
            runs[-1][1].append(end_stub)

        new_traces: list[Trace] = []
        for layer_index, points in runs:
            layer = _LAYERS[layer_index]
            simple = self._simplify(layer, points, width, net_id)
            if len(simple) < 2:
                continue
            new_traces.append(
                Trace(id=f"rr{self._seq + len(new_traces)}_{net_id}",
                      net=net_id, layer=layer, points=tuple(simple),
                      width_mm=width)
            )
        new_vias = [
            Via(id=f"rrv{self._seq + len(new_traces) + k}", net=net_id,
                center=grid.center(cell),
                drill_mm=self._problem.rules.via_drill_mm,
                pad_mm=self._problem.rules.via_pad_mm)
            for k, cell in enumerate(via_cells)
        ]

        # The gate. Everything above is planning; this is the only thing that
        # decides whether copper exists. A rejection here is a bug in the grid,
        # not a routing failure, and the note says exactly that.
        for via in new_vias:
            if self._ws.via_ok(via.center, net_id) is not True:
                self._notes.append(
                    f"grid proposed an illegal via on {plan.net.name} — dropped"
                )
                return False
            if not self._via_clear(via, net_id):
                self._notes.append(
                    f"unrotated-pad guard rejected a via on {plan.net.name}"
                )
                return False
        for trace in new_traces:
            if self._ws.path_ok(trace.layer, trace.points, width, net_id) is not True:
                self._notes.append(
                    f"grid proposed illegal copper on {plan.net.name} — dropped"
                )
                return False
            if not self._holes_clear(trace, net_id):
                self._notes.append(
                    f"unrotated-drill guard rejected copper on {plan.net.name}"
                )
                return False

        self._seq += len(new_traces) + len(new_vias)
        for via in new_vias:
            self._ws.commit_via(via)
            self._occ.add_via(net_id, via.center, via.pad_mm, clearance)
            self._via_block.update(grid.cells_near(via.center, self._via_gap_mm))
            self._vias.append(via)
        for trace in new_traces:
            self._ws.commit_trace(trace)
            self._occ.add_trace(net_id, trace.layer, trace.points, width, clearance)
            self._traces.append(trace)
        return True

    def _simplify(self, layer: str, points: Sequence[Point], width: float,
                  net_id: str) -> list[Point]:
        """Staircase to polyline. Collapse collinear runs first (free), then try
        a bounded straight shortcut, each one checked exactly — so the
        simplification pass doubles as the legality pass for the copper it
        keeps."""
        pts = _collapse(points)
        if len(pts) <= 2:
            return list(pts)
        out = [pts[0]]
        i = 0
        last = len(pts) - 1
        while i < last:
            best = i + 1
            for j in range(min(i + SIMPLIFY_LOOKAHEAD, last), i + 1, -1):
                if self._ws.segment_ok(layer, pts[i], pts[j], width, net_id) is True:
                    best = j
                    break
            out.append(pts[best])
            i = best
        return out

    def _via_clear(self, via: Via, net_id: str) -> bool:
        """The same divergence, from the other side: a via is a *hole*.

        ``dfm_hole_clearance`` measures every SMD pad against every via at
        ``min_via_to_copper_mm``, and it reads the pad **unrotated**. A via that
        clears a 2.25 x 0.63mm pill turned 270 degrees by 0.30mm in routerlib's
        geometry can read as -0.05mm in the gate that decides ``fab.ready``.
        Measured on terminal-keyboard, 2026-08-16: one error, exactly here.

        Only pads that read differently are checked — a square or unrotated pad
        is the same shape either way and ``Workspace`` has already covered it.
        """
        rules = self._problem.rules
        limit = rules.min_via_to_copper_mm
        drill_r = via.drill_mm / 2.0
        cx, cy = via.center.x, via.center.y
        for pad, (ax, ay, bx, by, r) in self._skew_pads:
            if pad.net and pad.net == net_id:
                continue
            span = r + drill_r + limit
            if abs(cx - (ax + bx) / 2.0) > span + abs(bx - ax) / 2.0:
                continue
            if abs(cy - (ay + by) / 2.0) > span + abs(by - ay) / 2.0:
                continue
            if segment_gap(cx, cy, cx, cy, ax, ay, bx, by) - r - drill_r < limit:
                return False
        for drill, (ax, ay, bx, by, r) in self._skew_drills:
            if drill.plated and drill.net and drill.net == net_id:
                continue
            needed = rules.hole_clearance(drill)
            pad_r = via.pad_mm / 2.0
            if segment_gap(cx, cy, cx, cy, ax, ay, bx, by) - r - pad_r < needed:
                return False
        return True

    def _holes_clear(self, trace: Trace, net_id: str) -> bool:
        """The guard for the one place routerlib and the pipeline disagree.

        ``routerlib.geometry.rect_capsule`` rotates a slot; ``circuitpy.checks``
        does not. Copper that satisfies only the rotated reading can still trip
        ``dfm_hole_clearance`` in the gate that decides ``fab.ready``. So every
        emitted segment is measured against **both** readings of every rotated
        slot and has to clear the stricter one. A round hole reads the same
        either way and is skipped.
        """
        rules = self._problem.rules
        half = trace.width_mm / 2.0
        for drill, (ax, ay, bx, by, r) in self._skew_drills:
            if drill.plated and drill.net and drill.net == net_id:
                continue
            needed = rules.hole_clearance(drill)
            for p0, p1 in trace.segments:
                gap = segment_gap(p0.x, p0.y, p1.x, p1.y, ax, ay, bx, by) - r - half
                if gap < needed:
                    return False
        return True

    # -- pad escape ------------------------------------------------------

    def _access(self, pad: Pad, half: float, width: float, net_id: str,
                *, soft: bool = False) -> dict[int, tuple[float, Point]]:
        """Grid nodes this pad can be reached from, with the stub that reaches
        it. The pad blocks its own neighbourhood in the grid — it is copper —
        so the escape is a short verified segment out of it. That is what a
        fanout is.

        ``soft`` lets an escape cell that another net currently covers count, at
        :data:`CONFLICT_COST_MM`. Only conflict discovery passes it: the point
        of that search is to find out *who* is covering the pin.
        """
        grid = self._grid
        reach = min(
            2.6,
            max(pad.width_mm, pad.height_mm) / 2.0
            + self._problem.rules.target_clearance_mm + half + 0.9,
        )
        out: dict[int, tuple[float, Point]] = {}
        tried = 0
        for layer_index, layer in enumerate(_LAYERS):
            if layer not in pad.layers:
                continue
            layer_avail = grid.avail[layer_index]
            for cell in grid.cells_near(pad.center, reach):
                if len(out) >= MAX_ACCESS_PER_PAD or tried >= MAX_ACCESS_TRIED:
                    break
                if layer_avail[cell] < half:
                    continue
                penalty = 0.0
                if self._occ.nets_blocking(layer_index, cell, half, net_id):
                    if not soft:
                        continue
                    penalty = CONFLICT_COST_MM
                point = grid.center(cell)
                tried += 1
                if not soft and self._ws.segment_ok(
                    layer, pad.center, point, width, net_id
                ) is not True:
                    continue
                out[layer_index * grid.ncells + cell] = (
                    pad.center.distance_to(point) + penalty, pad.center
                )
        return out

    def _plane_targets(self, plan: _NetPlan, half: float, near: Point,
                       radius: float) -> dict[int, tuple[float, None]]:
        """Grid nodes that land this net inside its own pour.

        Reaching the pour *is* the connection: the pour is copper of that net,
        so a via that lands in it or a track that runs inside it closes the
        circuit with no further copper. That is why a plane variant is a
        different problem and not the same problem with an extra obstacle.
        """
        grid = self._grid
        out: dict[int, tuple[float, None]] = {}
        for plane in self._planes_by_net.get(plan.net.id, ()):
            index = 0 if plane.layer == TOP else 1
            cells = grid.plane_cells[index]
            if not cells:
                continue
            layer_avail = grid.avail[index]
            for cell in grid.cells_near(near, radius):
                if cell in cells and layer_avail[cell] >= half:
                    out[index * grid.ncells + cell] = (0.0, None)
        return out

    def _plane_penalty(self, net_id: str) -> list[set[int] | None]:
        """Somebody else's pour is not an obstacle — our pipeline pours *after*
        routing, so the copper flows around a track. It is still a cost, because
        a track across a pour can island it and nothing in the scorer looks for
        that."""
        cached = self._penalty_cache.get(net_id)
        if cached is not None:
            return cached
        out: list[set[int] | None] = [None, None]
        for plane in self._problem.planes:
            if plane.net == net_id:
                continue
            index = 0 if plane.layer == TOP else 1
            cells = self._grid.plane_cells[index]
            out[index] = cells if cells else None
        self._penalty_cache[net_id] = out
        return out

    # -- rip-up ----------------------------------------------------------

    def _rip_up_loop(self, best):
        """Pick the worst failure, find who is in the way, take them out, and
        try again in a different order."""
        if self.rip_up_passes <= 0:
            return 0, best
        tabu: dict[str, int] = {}
        used = 0
        stalled = 0
        for step in range(1, self.rip_up_passes + 1):
            if self._out_of_budget() or stalled >= self.stall_limit:
                break
            plan = self._worst_failure()
            if plan is None:
                break
            used = step
            self._meter.tick()
            plan.attempts += 1

            blockers, contested = self._diagnose(plan)
            for layer, cell in contested:
                table = self._hist[layer]
                table[cell] = min(
                    HISTORY_CEILING_MM, table.get(cell, 0.0) + HISTORY_STEP_MM
                )

            candidates = [n for n in blockers
                          if n != plan.net.id and tabu.get(n, 0) < step]
            if not candidates:
                candidates = [n for n in blockers if n != plan.net.id]
            if not candidates:
                # Nothing to rip: the congestion is not another net's fault.
                # History has been bumped, so the net gets a differently-priced
                # board next time; the attempts counter rotates it to the back
                # so it cannot monopolise the budget.
                self._rip((plan,))
                self._route_net(plan)
            else:
                candidates.sort(key=lambda n: (
                    round(self._plans[n].mst_mm, 3), len(self._plans[n].links), n
                ))
                chosen = candidates[: self.rip_cap]
                for net_id in chosen:
                    tabu[net_id] = step + self.tabu_tenure
                group = [plan] + [self._plans[n] for n in chosen]
                self._rip(group)
                shuffled = [self._plans[n] for n in chosen]
                self._rng.shuffle(shuffled)
                for victim in [plan] + shuffled:
                    if self._out_of_budget():
                        break
                    self._route_net(victim)

            if self._score_tuple() < best[0]:
                best = self._snapshot()
                stalled = 0
            else:
                stalled += 1
        return used, best

    def _worst_failure(self) -> _NetPlan | None:
        """The net to work on next.

        Fewest attempts first, so the loop rotates rather than grinding one net;
        then the most connections still open; then the longest; then the name.
        Every term is a count or a millimetre — nothing here reads a clock or a
        hash."""
        candidates = [p for p in self._plans.values() if p.solvable_missing > 0]
        if not candidates:
            return None
        candidates.sort(key=lambda p: (
            p.attempts, -p.solvable_missing, -round(p.mst_mm, 3),
            p.net.name, p.net.id,
        ))
        return candidates[0]

    def _diagnose(self, plan: _NetPlan):
        """Who is in the way, and where.

        Re-runs the failing connections with other nets' copper made passable at
        :data:`CONFLICT_COST_MM`. The nets that path walks through are the
        blockers; the cells it walks through are the contested region that earns
        the history bump. A connection that fails even here is blocked by
        placement geometry, which no rip-up can move — mark it hopeless once and
        never spend budget on it again.
        """
        blockers: list[str] = []
        contested: list[tuple[int, int]] = []
        seen: set[str] = set()
        ncells = self._grid.ncells
        occ = self._occ
        pads = self._problem.pads_by_id
        widths = _width_ladder(plan.net.min_width_mm, self._problem.rules)
        half = widths[-1] / 2.0
        for index, (a_id, b_id) in enumerate(plan.links):
            if index in plan.done or index in plan.hopeless:
                continue
            if self._out_of_budget():
                break
            pad_a = pads.get(a_id)
            if pad_a is None:
                plan.hopeless.add(index)
                continue
            to_plane = b_id == "__plane__"
            pad_b = None if to_plane else pads.get(b_id)
            if pad_b is None and not to_plane:
                plan.hopeless.add(index)
                continue
            self._capped = False
            outcome = self._attempt(plan, pad_a, pad_b, widths[-1:], conflict=True)
            if outcome is None:
                # "Hopeless" is a claim about the *board*, so it may only be
                # made when the search really finished and found nothing. A
                # search that ran out of nodes proves nothing and must not be
                # recorded as geometry.
                if not self._capped and not self._out_of_budget():
                    plan.hopeless.add(index)
                continue
            found = outcome[0]
            for node in found[0]:
                layer = node // ncells
                cell = node - layer * ncells
                in_the_way = occ.nets_blocking(layer, cell, half, plan.net.id)
                if not in_the_way:
                    continue
                # Only cells somebody else's copper actually blocks. Charging
                # the whole path would price open space nobody is fighting over.
                contested.append((layer, cell))
                for other in in_the_way:
                    if other not in seen and other in self._plans:
                        seen.add(other)
                        blockers.append(other)
        return blockers, contested

    def _rip(self, plans: Sequence[_NetPlan]) -> None:
        ids = {p.net.id for p in plans}
        self._traces = [t for t in self._traces if t.net not in ids]
        self._vias = [v for v in self._vias if v.net not in ids]
        for plan in plans:
            self._occ.remove_net(plan.net.id)
            plan.done.clear()
        self._rebuild_via_block()
        self._rebuild_workspace()

    def _rebuild_via_block(self) -> None:
        grid = self._grid
        self._via_block = set()
        for via in self._vias:
            self._via_block.update(grid.cells_near(via.center, self._via_gap_mm))

    # -- state -----------------------------------------------------------

    def _rebuild_workspace(self) -> None:
        """``Workspace`` is append-only, so a rip-up rebuilds it. That is the
        price of asking the same oracle the scorer uses instead of keeping a
        second opinion in this file, and it is worth paying."""
        ws = Workspace(self._problem)
        for via in self._vias:
            ws.commit_via(via)
        for trace in self._traces:
            ws.commit_trace(trace)
        self._ws = ws

    def _score_tuple(self) -> tuple:
        """The router's own read on the current state.

        The harness's order for the terms a router controls — connected nets,
        then vias, then copper — with one term inserted that the harness does
        not have: **connections placed**. Without it a pass that failed *more*
        of an already-incomplete net scored better, because it left less copper
        behind, and the loop happily walked a net from ten links down to three.
        Errors are absent because by construction there are none.
        """
        connected = sum(1 for p in self._plans.values() if p.complete)
        placed = sum(len(p.done) for p in self._plans.values())
        copper = sum(t.length_mm for t in self._traces)
        return (-connected, -placed, len(self._vias), round(copper, 4))

    def _snapshot(self):
        return (
            self._score_tuple(),
            {k: (frozenset(v.done), v.necked) for k, v in self._plans.items()},
            list(self._traces),
            list(self._vias),
        )

    def _restore_plans(self, snapshot) -> None:
        for net_id, (done, necked) in snapshot[1].items():
            plan = self._plans[net_id]
            plan.done = set(done)
            plan.necked = necked

    def _out_of_budget(self) -> bool:
        if self._nodes >= self._node_cap:
            return True
        return self._meter.exhausted


class GreedyControlRouter(RipUpRerouteRouter):
    """The same maze router with rip-up switched off.

    The control the brief asks for. Comparing rip-up against
    ``baseline-pattern`` would measure the grid, the A*, the pad-escape logic
    and the rip-up loop all at once; comparing against this isolates the last
    one.
    """

    name = "ripup-greedy-control"

    def __init__(self, **kwargs) -> None:
        kwargs["rip_up_passes"] = 0
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _pad_in_plane(pad: Pad, planes: Sequence[Plane]) -> bool:
    """A pad sitting inside a pour on a layer it lives on is already joined to
    that net — no via, no trace. This is the fact the router we ship cannot
    represent: it counted our seventy-three ground vias as seventy-three
    obstacles and produced byte-identical copper."""
    from routerlib.geometry import point_in_polygon

    for plane in planes:
        if plane.layer not in pad.layers:
            continue
        if not point_in_polygon(pad.center.x, pad.center.y, plane.outline):
            continue
        if any(point_in_polygon(pad.center.x, pad.center.y, ring)
               for ring in plane.holes):
            continue
        return True
    return False


def _width_ladder(net_width: float, rules) -> list[float]:
    """The two widths to try for one connection, widest first.

    A rail is routed at the power width, because that is what the net class
    means. If the connection will not close there, the ladder necks it down one
    step: ``dfm_power_trace_width`` is a *warning* and an unconnected net is a
    dead board, so the trade is worth making — recorded in the notes, not
    hidden.

    Two rungs, not three, and the reason is measured rather than aesthetic:
    every extra rung multiplies the cost of a *failed* connection by a whole
    search, and 0.20mm versus 0.15mm changes the corridor a track needs by
    0.05mm against a 0.294mm clearance pair — almost never the difference
    between routable and not. Neither rung goes below ``warn_trace_mm``, well
    clear of the 0.10mm width the fab errors at.
    """
    floor = max(rules.min_trace_mm, rules.warn_trace_mm)
    wide = round(max(net_width, floor), 6)
    narrow = round(max(rules.signal_trace_mm, floor), 6)
    if narrow >= wide:
        narrow = floor
    return [wide] if narrow >= wide else [wide, narrow]


def _collapse(points: Sequence[Point]) -> list[Point]:
    """Drop points sitting on the straight line between their neighbours."""
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    for prev, cur, nxt in zip(points, points[1:], points[2:]):
        cross = ((cur.x - prev.x) * (nxt.y - prev.y)
                 - (cur.y - prev.y) * (nxt.x - prev.x))
        if abs(cross) > 1e-9:
            out.append(cur)
    out.append(points[-1])
    return out


def _bbox_of(points: Sequence[Point]):
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return ((min(xs), min(ys)), (max(xs), max(ys)))


def _target_bbox(grid: _Grid, targets) -> tuple[float, float, float, float]:
    """Bounding box of the target nodes, in millimetres.

    The A* heuristic is the octile distance to this box. Every target is inside
    it, so the estimate never exceeds the true remaining cost — admissible, and
    therefore the path A* returns is a genuine minimum under the cost function,
    not a plausible one.
    """
    nx, ncells, pitch = grid.nx, grid.ncells, grid.pitch
    lo_i = lo_j = 1 << 30
    hi_i = hi_j = -(1 << 30)
    for node in targets:
        cell = node % ncells
        i = cell % nx
        j = cell // nx
        if i < lo_i:
            lo_i = i
        if i > hi_i:
            hi_i = i
        if j < lo_j:
            lo_j = j
        if j > hi_j:
            hi_j = j
    return (
        grid.x0 + (lo_i + 0.5) * pitch,
        grid.y0 + (lo_j + 0.5) * pitch,
        grid.x0 + (hi_i + 0.5) * pitch,
        grid.y0 + (hi_j + 0.5) * pitch,
    )


def _stadium_unrotated(shape) -> tuple[float, float, float, float, float]:
    """A pad or a drill as ``circuitpy.checks`` reads it: width and height in
    the board frame, ``ccw_rotation`` ignored. The second opinion the router
    has to satisfy alongside routerlib's rotated one."""
    from routerlib.geometry import stadium

    return stadium(shape.center.x, shape.center.y, shape.width_mm, shape.height_mm)


def _mst_edges(pads: Sequence[Pad]) -> list[tuple[Pad, Pad]]:
    """Prim over pad centres, O(n^2), ties broken by pad id.

    One fixed spanning tree per net, computed once from geometry, so the same
    net asks for the same connections on every pass: a rip-up is then comparable
    to the attempt it replaced rather than a different question.
    """
    if len(pads) < 2:
        return []
    n = len(pads)
    in_tree = [False] * n
    best_cost = [math.inf] * n
    best_from = [0] * n
    in_tree[0] = True
    for k in range(1, n):
        best_cost[k] = pads[0].center.distance_to(pads[k].center)
    edges: list[tuple[Pad, Pad]] = []
    for _ in range(n - 1):
        pick = -1
        pick_key: tuple | None = None
        for k in range(n):
            if in_tree[k]:
                continue
            key = (round(best_cost[k], 9), pads[k].id)
            if pick_key is None or key < pick_key:
                pick_key = key
                pick = k
        if pick < 0:
            break
        in_tree[pick] = True
        edges.append((pads[best_from[pick]], pads[pick]))
        for k in range(n):
            if in_tree[k]:
                continue
            d = pads[pick].center.distance_to(pads[k].center)
            if d < best_cost[k]:
                best_cost[k] = d
                best_from[k] = pick
    return edges


#: The registry entry, same shape as ``routerlib.baseline.ROUTERS``.
ROUTERS = {
    RipUpRerouteRouter.name: RipUpRerouteRouter,
    GreedyControlRouter.name: GreedyControlRouter,
}


__all__ = ["GreedyControlRouter", "ROUTERS", "RipUpRerouteRouter"]
