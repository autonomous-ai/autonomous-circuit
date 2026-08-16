"""plane-and-classes: a router that knows what a net is *for*.

The router we ship treats every net as a wire and every plane as an obstacle.
This one starts from the two facts that description throws away.

**A plane is a net, not seventy-three obstacles.** Where a layer carries a
poured plane, the plane's net is not routed at all: every pad of it drops one
via into the copper it is already sitting on top of, and the router's whole job
for that net is placing those vias legally. Ground is routinely the largest net
on our boards — 61 of 228 pads on harness-puck — so this does not refine the
problem, it removes the biggest piece of it. It also has to happen *last*, not
first, which is the opposite of what it sounds like; the measurement is in
``_PLANE_GROUP``.

**A net has a class, and the class decides how it is routed.** Power gets the
width the rules resolved for it. A differential pair is routed as a pair: the
second half is steered to run beside the first rather than shortest-path across
the board. Everything else is an ordinary signal. Classes are routed in order,
so the constrained nets choose from an empty board and the free ones take what
is left.

Underneath both is a grid maze router — A* over 8-connected cells, one lattice
per layer, a layer change costs a via — with three properties that matter more
than the search does.

**It never commits copper it has not verified exactly.** The grid is an
approximation: a cell is free when its *centre* is far enough from foreign
copper, which says nothing about the point halfway to the next cell. So every
path the search returns is re-checked segment by segment against
:class:`~routerlib.workspace.Workspace`, which measures with the same geometry
the scorer does. A path that fails is thrown away, not shipped. That is the
whole reason the DRC column can be trusted: this router cannot place a
violation, it can only fail to route.

**It designs above the floor.** Clearance comes from
``rules.target_clearance_mm`` (0.147mm), never from ``min_clearance_mm``. A
router that aims at the fab minimum lands under it — that is the diagnosis of
the one we ship, and it is a one-line choice.

**Its budget is counted, never timed.** Node caps, iteration caps, a fixed
number of passes. Nothing in the control flow reads a clock, because a router
that stops on elapsed time gives a different board on a loaded machine, which
is exactly how the shipped router became nondeterministic.

Where it is the wrong tool is in the report rather than hidden here: it is a
*sequential* router with pass-level reordering rather than true rip-up, so on a
genuinely congested board the nets routed last are the nets that lose.

Deliberately not registered in ``routerlib.cli.registry()`` — several algorithm
families are being built against this contract at once and a shared one-line
registry is a shared merge conflict. ``bench_plane_and_classes.py`` beside this
file runs the same ``routerlib.bench.run_suite`` the CLI runs, so the numbers
are the same numbers.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

from routerlib.bench import inset_polygon
from routerlib.geometry import (
    PolygonIndex,
    capsule_bbox,
    capsule_gap,
    core_halfplanes,
    disc_capsule,
    drill_capsule,
    keepout_capsule,
    pad_capsule,
    segment_capsule,
    stadium_capsule,
)
from routerlib.model import (
    BOTTOM,
    TOP,
    Budget,
    BudgetMeter,
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
# Tuning. Every one of these is a count or a distance, never a duration.
# ---------------------------------------------------------------------------

#: Cell-ownership sentinels. ``FREE`` means no copper is close enough to
#: matter; a positive value is the net that owns the cell; ``HARD`` means two
#: different nets are both too close, so nobody may route there — which is the
#: correct answer, not a conservative one: a trace there would be under
#: clearance to one of them whatever net it carried.
FREE = 0
HARD = -1

#: Candidate grid pitches, finest first. The grid is the router's entire model
#: of where copper may go, so the pitch is the resolution of every decision it
#: makes. Finer is strictly better and strictly slower.
_PITCHES: tuple[float, ...] = (0.1, 0.125, 0.15, 0.2, 0.25, 0.3)

#: Ceiling on cells per layer, so the largest instance we have (terminal-
#: keyboard, 112 x 90mm) still lands on a 0.125mm grid.
_MAX_CELLS = 700_000

#: What a layer change costs, as the length of copper it is worth. Vias are
#: tier 3 in the score and a real cost on the panel, so the search takes a 2mm
#: detour over a via and not the other way round.
_VIA_COST_MM = 2.0

#: Expanded nodes one A* may spend on one connection. An unrouted net reported
#: honestly is cheaper than a search that never ends.
_NODE_CAP = 60_000

#: ...and for the short Dijkstra that finds a boxed-in pad a way into a plane.
_PLANE_NODE_CAP = 12_000

#: How many cells near a pad centre a search may start from.
_ACCESS_FANOUT = 10

#: Longest shortcut the pull-taut pass attempts, in path points. Bounds
#: simplification at O(points x this) exact checks instead of O(points^2).
_SHORTCUT_SPAN = 24

#: Step-cost multipliers, in tenths. Every one is >= 10 so the Euclidean
#: heuristic stays admissible.
_M_ONE = 10
#: Routing on the layer that carries a plane carves the pour. Legal, and
#: invisible to the DRC (plane islanding is a declared coverage gap), and still
#: the wrong instinct — so it costs half as much again.
_M_PLANE_LAYER = 15
#: A pair's second half is pushed into the corridor beside its partner by
#: making everywhere else more expensive.
_M_OFF_CORRIDOR = 30

#: Heuristic scale, in cost units per millimetre. A step costs 1000 per mm, so
#: 1000 here is the admissible Euclidean heuristic and anything above it is
#: weighted A*: fewer nodes expanded, paths a few percent longer. Completeness
#: is tier 1 of the score and copper length is tier 3, so the trade is the
#: right way round — and it is a constant, so it does not cost determinism.
_H_WEIGHT = 1200.0

#: Which class-order slot plane stitching takes. **Last**, and that is a
#: measurement overruling an intuition. "Plane vias first" reads as obviously
#: right — a stitching via is the cheapest object on the board, so get them
#: down before the board fills up. But a stitching via is also a 0.6mm pad and
#: a 0.3mm hole, and there is one per ground pad: 40 of them on
#: hydrate-coaster, 36 on matrix-ldo-3v3__rp2040-core__usb-c-power. Placed
#: first they are 40 obstacles every other net then has to get around; placed
#: last they fit in the gaps the other nets left. Measured over the two
#: plane instances with everything else fixed: first 80.1% mean completeness,
#: last 85.2%, and matrix-ldo-3v3__rp2040-core__usb-c-power-plane goes from
#: 82.4% to 100%. Ground still *loses* nothing by going last, because a pad
#: that can no longer reach the pour falls back to being routed.
_PLANE_GROUP = 5

#: Full restarts, in order: ``(label, net-ordering, relaxed margins)``. Each
#: one re-routes the whole board and the best result wins, so a later pass can
#: only help. Measured on matrix-ldo-3v3__usb-c-power: a relaxed *restart* gets
#: 100%, a relaxed *extension* of the clean board gets 60% — because the clean
#: copper it has to keep is what closes the last channel. Both are kept for
#: that reason.
PASS_PLAN: tuple[tuple[str, int, bool], ...] = (
    ("clean", 0, False),
    ("clean-retry", 1, False),
    ("relaxed", 2, True),
)

#: Ring geometry for "drop a via next to this pad".
_RING_DIRECTIONS = 12
_RING_STEPS = (0.0, 0.15, 0.35)


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Grid:
    """A uniform lattice over the board, with a deliberately chosen phase.

    The phase is not cosmetic. A 0.5mm-pitch USB-C pad row leaves a corridor
    about 0.1mm wide for a 0.2mm trace to escape along a pad's own axis; if the
    lattice lands half a cell off that axis the corridor disappears and eight
    nets become unroutable for a reason that is arithmetic rather than
    electrical. So the origin is shifted to the *modal* residue of the pad
    centres — the phase that puts the most pads exactly on a node.
    """

    x0: float
    y0: float
    nx: int
    ny: int
    pitch: float

    @property
    def size(self) -> int:
        return self.nx * self.ny

    def col_of(self, x: float) -> int:
        return int(round((x - self.x0) / self.pitch))

    def row_of(self, y: float) -> int:
        return int(round((y - self.y0) / self.pitch))

    def xy(self, cell: int) -> tuple[float, float]:
        row, col = divmod(cell, self.nx)
        return (self.x0 + col * self.pitch, self.y0 + row * self.pitch)

    def point(self, cell: int) -> Point:
        x, y = self.xy(cell)
        return Point(x, y)

    def cell_at(self, x: float, y: float) -> int | None:
        col, row = self.col_of(x), self.row_of(y)
        if 0 <= col < self.nx and 0 <= row < self.ny:
            return row * self.nx + col
        return None


def _modal_residue(values: Sequence[float], pitch: float) -> float:
    """The lattice phase that puts the most of ``values`` on a node."""
    if not values:
        return 0.0
    counts: dict[float, int] = {}
    for value in values:
        key = round(round((value % pitch) / pitch * 64.0) / 64.0 * pitch, 6)
        counts[key] = counts.get(key, 0) + 1
    # Sorted first, so a tie is broken by the residue and never by dict order.
    return max(sorted(counts.items()), key=lambda kv: (kv[1], -kv[0]))[0]


def _grid_for(problem: RoutingProblem) -> Grid:
    x0, y0, x1, y1 = problem.board.bbox
    if len(problem.board.outline) >= 3:
        xs = [p.x for p in problem.board.outline]
        ys = [p.y for p in problem.board.outline]
        x0, y0 = min(x0, min(xs)), min(y0, min(ys))
        x1, y1 = max(x1, max(xs)), max(y1, max(ys))
    width, height = x1 - x0, y1 - y0
    pitch = _PITCHES[-1]
    for candidate in _PITCHES:
        if (width / candidate + 3) * (height / candidate + 3) <= _MAX_CELLS:
            pitch = candidate
            break
    phase_x = _modal_residue([p.center.x for p in problem.pads], pitch)
    phase_y = _modal_residue([p.center.y for p in problem.pads], pitch)
    ox = (x0 - pitch) + ((phase_x - (x0 - pitch)) % pitch)
    oy = (y0 - pitch) + ((phase_y - (y0 - pitch)) % pitch)
    nx = int(math.floor((x1 - ox) / pitch)) + 2
    ny = int(math.floor((y1 - oy) / pitch)) + 2
    return Grid(x0=ox, y0=oy, nx=nx, ny=ny, pitch=pitch)


# ---------------------------------------------------------------------------
# Stamping copper into cells
# ---------------------------------------------------------------------------


def _stadium_row_span(
    ax: float, ay: float, bx: float, by: float, radius: float, cy: float
) -> tuple[float, float] | None:
    """``[xlo, xhi]`` of ``{x : distance((x, cy), segment ab) <= radius}``.

    A capsule is convex, so its intersection with a horizontal line is one
    interval, and that interval is the union of the two end discs' slices with
    the slice of the rectangle between them. Exact — nothing is sampled, so a
    cell is never marked because a probe happened to land inside it.
    """
    lo = math.inf
    hi = -math.inf
    for px, py in ((ax, ay), (bx, by)):
        dy = cy - py
        if abs(dy) <= radius:
            span = math.sqrt(max(0.0, radius * radius - dy * dy))
            lo = min(lo, px - span)
            hi = max(hi, px + span)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length > 1e-12:
        nx, ny = -dy / length * radius, dx / length * radius
        corners = (
            (ax + nx, ay + ny),
            (bx + nx, by + ny),
            (bx - nx, by - ny),
            (ax - nx, ay - ny),
        )
        for i in range(4):
            x1, y1 = corners[i]
            x2, y2 = corners[(i + 1) % 4]
            if (y1 <= cy <= y2) or (y2 <= cy <= y1):
                if abs(y2 - y1) < 1e-15:
                    lo, hi = min(lo, x1, x2), max(hi, x1, x2)
                else:
                    x = x1 + (x2 - x1) * (cy - y1) / (y2 - y1)
                    lo, hi = min(lo, x), max(hi, x)
    if hi < lo:
        return None
    return (lo, hi)


class Field:
    """One occupancy array: for one layer and one trace width, who owns which
    cell.

    ``cells[i]`` is ``FREE``, a net code, or ``HARD``. A net may sit on a cell
    that is free or that it owns itself.
    """

    __slots__ = ("grid", "cells")

    def __init__(self, grid: Grid, fill: int = HARD):
        self.grid = grid
        self.cells = [fill] * grid.size

    # -- construction ----------------------------------------------------

    def open_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        grid = self.grid
        c0 = max(0, int(math.ceil((x0 - grid.x0) / grid.pitch - 1e-9)))
        c1 = min(grid.nx - 1, int(math.floor((x1 - grid.x0) / grid.pitch + 1e-9)))
        r0 = max(0, int(math.ceil((y0 - grid.y0) / grid.pitch - 1e-9)))
        r1 = min(grid.ny - 1, int(math.floor((y1 - grid.y0) / grid.pitch + 1e-9)))
        if c1 < c0 or r1 < r0:
            return
        run = [FREE] * (c1 - c0 + 1)
        for row in range(r0, r1 + 1):
            base = row * grid.nx
            self.cells[base + c0 : base + c1 + 1] = run

    def open_polygon(self, poly: Sequence[Point]) -> None:
        """Free every cell inside a simple polygon, by scanline."""
        grid = self.grid
        if len(poly) < 3:
            return
        edges: list[tuple[float, float, float, float]] = []
        rows: dict[int, list[int]] = {}
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            index = len(edges)
            edges.append((a.x, a.y, b.x, b.y))
            lo = max(0, int(math.floor((min(a.y, b.y) - grid.y0) / grid.pitch)))
            hi = min(
                grid.ny - 1, int(math.ceil((max(a.y, b.y) - grid.y0) / grid.pitch))
            )
            for row in range(lo, hi + 1):
                rows.setdefault(row, []).append(index)
        for row, indices in sorted(rows.items()):
            cy = grid.y0 + row * grid.pitch
            crossings: list[float] = []
            for index in indices:
                x1, y1, x2, y2 = edges[index]
                if (y1 > cy) != (y2 > cy):
                    crossings.append((x2 - x1) * (cy - y1) / (y2 - y1) + x1)
            crossings.sort()
            for i in range(0, len(crossings) - 1, 2):
                self.open_rect(crossings[i], cy, crossings[i + 1], cy)

    # -- mutation --------------------------------------------------------

    def stamp(
        self, ax: float, ay: float, bx: float, by: float, radius: float, code: int
    ) -> None:
        """Claim every cell whose centre is within ``radius`` of segment ab."""
        grid = self.grid
        pitch = grid.pitch
        nx = grid.nx
        cells = self.cells
        r0 = max(0, int(math.ceil((min(ay, by) - radius - grid.y0) / pitch)))
        r1 = min(grid.ny - 1, int(math.floor((max(ay, by) + radius - grid.y0) / pitch)))
        for row in range(r0, r1 + 1):
            cy = grid.y0 + row * pitch
            span = _stadium_row_span(ax, ay, bx, by, radius, cy)
            if span is None:
                continue
            c0 = max(0, int(math.ceil((span[0] - grid.x0) / pitch)))
            c1 = min(nx - 1, int(math.floor((span[1] - grid.x0) / pitch)))
            if c1 < c0:
                continue
            base = row * nx
            for i in range(base + c0, base + c1 + 1):
                value = cells[i]
                if value == code:
                    continue
                cells[i] = code if value == FREE else HARD

    def stamp_shape(self, capsule, extra: float, code: int) -> None:
        """:meth:`stamp` for a shape that is not a stadium.

        Row spans come from the core's own outward edge lines instead of a
        stadium's: ``max`` over them is negative inside the shape and the true
        distance outside it, under-reading only in the wedge past a corner,
        where it claims a cell or two more than it must. That is the safe
        direction; the inscribed stadium this replaces left the corner of every
        rectangular pad unclaimed by 0.21mm.
        """
        planes = core_halfplanes(capsule)
        if not planes:
            return
        reach = capsule.sweep + extra
        grid = self.grid
        pitch = grid.pitch
        nx = grid.nx
        cells = self.cells
        bx0, by0, bx1, by1 = capsule_bbox(capsule)
        r0 = max(0, int(math.ceil((by0 - extra - grid.y0) / pitch)))
        r1 = min(grid.ny - 1, int(math.floor((by1 + extra - grid.y0) / pitch)))
        c_lo = max(0, int(math.ceil((bx0 - extra - grid.x0) / pitch)))
        c_hi = min(nx - 1, int(math.floor((bx1 + extra - grid.x0) / pitch)))
        if r1 < r0 or c_hi < c_lo:
            return
        for row in range(r0, r1 + 1):
            cy = grid.y0 + row * pitch
            base = row * nx
            cx = grid.x0 + c_lo * pitch
            for column in range(c_lo, c_hi + 1):
                worst = -1e18
                for pnx, pny, off in planes:
                    d = pnx * cx + pny * cy - off
                    if d > worst:
                        worst = d
                if worst <= reach:
                    index = base + column
                    value = cells[index]
                    if value != code:
                        cells[index] = code if value == FREE else HARD
                cx += pitch

    def mark(
        self, ax: float, ay: float, bx: float, by: float, radius: float, value: int
    ) -> None:
        """Unconditional set. Used for the differential-pair corridor, which is
        a preference and not an ownership claim."""
        grid = self.grid
        pitch = grid.pitch
        cells = self.cells
        r0 = max(0, int(math.ceil((min(ay, by) - radius - grid.y0) / pitch)))
        r1 = min(grid.ny - 1, int(math.floor((max(ay, by) + radius - grid.y0) / pitch)))
        for row in range(r0, r1 + 1):
            cy = grid.y0 + row * pitch
            span = _stadium_row_span(ax, ay, bx, by, radius, cy)
            if span is None:
                continue
            c0 = max(0, int(math.ceil((span[0] - grid.x0) / pitch)))
            c1 = min(grid.nx - 1, int(math.floor((span[1] - grid.x0) / pitch)))
            if c1 < c0:
                continue
            base = row * grid.nx
            for i in range(base + c0, base + c1 + 1):
                cells[i] = value


# ---------------------------------------------------------------------------
# What the rules mean for a router, in one place
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Margins:
    """The distances one routing pass designs to.

    Two sets exist, for a reason worth stating. The **clean** set sits above
    every fab floor *and* above every warn band, so a board routed at it scores
    zero errors and zero warnings. The **relaxed** set sits above the fab
    floors and below the warn bands: it buys a connection at the price of a
    warning, and it is only ever applied to nets the clean pass could not place
    at all. The score is lexicographic — one unrouted net outranks every
    warning on the board — so that is the trade the ruler asks for. It is a
    trade and not a discount: nothing here ever goes below what JLC holds.
    """

    clearance: float
    pth_to_copper: float
    npth_to_copper: float
    via_to_copper: float
    hole_to_hole: float
    edge: float
    edge_via: float
    keepout: float = 0.03

    @classmethod
    def clean(cls, rules, profile) -> "Margins":
        return cls(
            clearance=rules.target_clearance_mm,
            # JLC's *recommended* 0.35mm to a component plated hole, not the
            # 0.28mm floor. The gap between those two numbers is a warning
            # nobody has to earn.
            pth_to_copper=float(
                getattr(profile, "warn_pth_to_copper_mm", rules.min_pth_to_copper_mm)
            ),
            npth_to_copper=rules.min_npth_to_copper_mm + 0.03,
            via_to_copper=rules.min_via_to_copper_mm + 0.03,
            hole_to_hole=rules.min_hole_to_hole_mm + 0.02,
            # Traces are checked against the outline at the fab minimum; only
            # pads, vias and plated holes have a warn band, so only the via
            # margin is pushed out to it.
            edge=rules.min_edge_clearance_mm + 0.03,
            edge_via=float(getattr(profile, "warn_edge_clearance_mm", 0.3)),
        )

    @classmethod
    def relaxed(cls, rules, profile) -> "Margins":
        return cls(
            clearance=max(rules.min_clearance_mm + 0.012, rules.clearance_gate_mm),
            pth_to_copper=rules.min_pth_to_copper_mm + 0.008,
            npth_to_copper=rules.min_npth_to_copper_mm + 0.008,
            via_to_copper=rules.min_via_to_copper_mm + 0.008,
            hole_to_hole=rules.min_hole_to_hole_mm + 0.008,
            edge=rules.min_edge_clearance_mm + 0.008,
            edge_via=rules.min_edge_clearance_mm + 0.008,
        )

    def hole(self, drill) -> float:
        """JLC publishes three copper-to-hole numbers, not one."""
        if drill.pad_id is None and drill.plated:
            return self.via_to_copper
        if drill.plated:
            return self.pth_to_copper
        return self.npth_to_copper


# ---------------------------------------------------------------------------
# Spanning tree and components
# ---------------------------------------------------------------------------


def _mst_edges(pads: Sequence[Pad]) -> list[tuple[Pad, Pad]]:
    """Prim over pad centres, ties broken by pad id. The tree is part of the
    answer, so it cannot depend on dict order."""
    if len(pads) < 2:
        return []
    ordered = sorted(pads, key=lambda p: (p.center.x, p.center.y, p.id))
    inside = [ordered[0]]
    outside = list(ordered[1:])
    edges: list[tuple[Pad, Pad]] = []
    while outside:
        best_key: tuple | None = None
        best_pair: tuple[Pad, Pad] | None = None
        for a in inside:
            for b in outside:
                key = (round(a.center.distance_to(b.center), 9), a.id, b.id)
                if best_key is None or key < best_key:
                    best_key, best_pair = key, (a, b)
        assert best_pair is not None
        edges.append(best_pair)
        inside.append(best_pair[1])
        outside.remove(best_pair[1])
    return edges


def _mst_length(pads: Sequence[Pad]) -> float:
    return sum(a.center.distance_to(b.center) for a, b in _mst_edges(pads))


class _Union:
    __slots__ = ("parent",)

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a: str, b: str) -> str:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        lo, hi = sorted((ra, rb))
        self.parent[hi] = lo
        return lo


def _polygon_area(poly: Sequence[Point]) -> float:
    area = 0.0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        area += a.x * b.y - b.x * a.y
    return abs(area) / 2.0


def _profile_of(rules):
    try:
        from circuitpy.fab import get_profile

        return get_profile(rules.profile_id)
    except Exception:  # noqa: BLE001 - a missing profile must not fail a route
        return rules


def _spent(meter: BudgetMeter, limits: "_Limits") -> bool:
    """Counted budget only. Deliberately never reads ``meter.elapsed_s``: a
    router that stops on a clock gives a different board on a busy machine."""
    return meter.nodes >= limits.max_nodes or meter.iterations >= limits.max_iterations


@dataclass(frozen=True)
class _Limits:
    node_cap: int
    plane_node_cap: int
    max_nodes: int
    max_iterations: int


# ---------------------------------------------------------------------------
# One routing pass
# ---------------------------------------------------------------------------


@dataclass
class PassResult:
    traces: list[Trace]
    vias: list[Via]
    connected: set[str]
    failed: list[str]
    notes: list[str]

    @property
    def key(self) -> tuple:
        """Lower is better, in the scorer's own priority order: connections
        first, then vias, then copper. Legality is not in the key because no
        pass can produce an illegal board."""
        return (
            -len(self.connected),
            len(self.vias),
            round(sum(t.length_mm for t in self.traces), 3),
        )


class _Session:
    """All the mutable state of one pass over one board."""

    def __init__(
        self,
        problem: RoutingProblem,
        margins: Margins,
        meter: BudgetMeter,
        limits: _Limits,
        widths: dict[str, list[float]],
        *,
        seq_start: int = 0,
    ):
        self.problem = problem
        self.rules = problem.rules
        self.margins = margins
        self.meter = meter
        self.limits = limits
        self.widths = widths
        self.grid = _grid_for(problem)
        self.ws = Workspace(problem, clearance=margins.clearance)

        self.traces: list[Trace] = []
        self.vias: list[Via] = []
        # Ids have to stay unique across passes: the scorer's union-find is
        # keyed on them, and two different traces sharing an id would merge
        # into one node and overstate connectivity.
        self._seq = seq_start

        # Net codes start at 1 so FREE stays 0. Unnetted copper gets HARD:
        # nobody owns it, everybody keeps away from it.
        self.code: dict[str, int] = {
            net.id: index + 1 for index, net in enumerate(problem.nets)
        }
        self.plane_of: dict[str, list[Plane]] = {}
        for plane in problem.planes:
            self.plane_of.setdefault(plane.net, []).append(plane)
        self.plane_shape = {p.id: PolygonIndex(p.outline) for p in problem.planes}
        self.plane_holes = {
            p.id: [PolygonIndex(ring) for ring in p.holes] for p in problem.planes
        }
        self.planed_layers = {p.layer for p in problem.planes}

        self._fields: dict[float, dict[str, Field]] = {}
        self._via_hard: Field | None = None
        # Component plated holes only. Cached because the warn-band check walks
        # them per candidate path and there are only a handful.
        self._pth = tuple(
            d for d in problem.drills if d.plated and d.pad_id is not None
        )
        # Pads whose two models disagree. ``routerlib.geometry`` reads
        # ``ccw_rotation``; ``circuitpy.checks`` does not (README, "known
        # divergence"), so a 2.25 x 0.63mm pill turned 90 degrees is a tall pill
        # to the scorer's clearance check and a wide bar to the same scorer's
        # hole-clearance check. Measured on matrix-rp2040-core__sw-tact: five
        # errors against copper that is genuinely 0.3mm clear. Until the
        # pipeline reads the field, the only defensible answer is to clear
        # *both* shapes — strictly more conservative than either, so it can
        # cost a route but can never place a real violation.
        self._rotated_pads = tuple(
            p
            for p in problem.pads
            if abs(((p.rotation_deg + 90.0) % 180.0) - 90.0) > 1e-6
        )

    # -- masks -----------------------------------------------------------

    def field(self, width: float) -> dict[str, Field]:
        """Occupancy for a trace of ``width``, one array per layer. Built on
        first use, because a board with no rails never needs the 0.5mm one."""
        key = round(width, 6)
        cached = self._fields.get(key)
        if cached is not None:
            return cached
        problem = self.problem
        margins = self.margins
        half = width / 2.0
        fields = {layer: Field(self.grid) for layer in (TOP, BOTTOM)}

        inset = margins.edge + half
        for layer_field in fields.values():
            self._open_board(layer_field, inset)

        for pad in problem.pads:
            code = self.code.get(pad.net, HARD) if pad.net else HARD
            shapes = [pad_capsule(pad)]
            if pad in self._rotated_pads:
                shapes.append(
                    stadium_capsule(
                        pad.center.x, pad.center.y, pad.width_mm, pad.height_mm, 0.0
                    )
                )
            for shape in shapes:
                for layer in pad.layers:
                    target = fields.get(layer)
                    if target is None:
                        continue
                    if getattr(shape, "core", None) is not None:
                        target.stamp_shape(shape, half + margins.clearance, code)
                    else:
                        ax, ay, bx, by, radius = shape
                        target.stamp(
                            ax, ay, bx, by,
                            radius + half + margins.clearance, code,
                        )

        for drill in problem.drills:
            ax, ay, bx, by, radius = drill_capsule(drill)
            code = (
                self.code.get(drill.net, HARD) if (drill.plated and drill.net) else HARD
            )
            reach = radius + half + margins.hole(drill)
            for layer_field in fields.values():
                layer_field.stamp(ax, ay, bx, by, reach, code)

        for keepout in problem.keepouts:
            zone = keepout_capsule(keepout)
            for layer in keepout.layers:
                target = fields.get(layer)
                if target is None:
                    continue
                if getattr(zone, "core", None) is not None:
                    target.stamp_shape(zone, half + margins.keepout, HARD)
                else:
                    ax, ay, bx, by, radius = zone
                    target.stamp(
                        ax, ay, bx, by, radius + half + margins.keepout, HARD
                    )

        for trace in problem.existing_traces:
            target = fields.get(trace.layer)
            if target is None:
                continue
            code = self.code.get(trace.net, HARD)
            reach = trace.width_mm / 2.0 + half + margins.clearance
            for a, b in trace.segments:
                target.stamp(a.x, a.y, b.x, b.y, reach, code)
        for via in problem.existing_vias:
            code = self.code.get(via.net, HARD)
            reach = via.pad_mm / 2.0 + half + margins.clearance
            for layer_field in fields.values():
                layer_field.stamp(
                    via.center.x, via.center.y, via.center.x, via.center.y, reach, code
                )

        self._fields[key] = fields
        return fields

    def _open_board(self, target: Field, inset: float) -> None:
        """Free the copper-bearing region: inside the outline, off the edge."""
        outline = self.problem.board.outline
        if len(outline) >= 3:
            region = inset_polygon(outline, inset)
            if len(region) >= 3 and _polygon_area(region) > 0.25 * _polygon_area(
                outline
            ):
                target.open_polygon(region)
                return
        x0, y0, x1, y1 = self.problem.board.bbox
        target.open_rect(x0 + inset, y0 + inset, x1 - inset, y1 - inset)

    @property
    def via_hard(self) -> Field:
        """Cells where **no** via may go, whatever net it carries: inside an
        SMD pad (via-in-pad needs plating we do not order), too close to
        another hole, or too near the board edge for the fab's comfort."""
        if self._via_hard is not None:
            return self._via_hard
        rules = self.rules
        field_ = Field(self.grid)
        self._open_board(field_, self.margins.edge_via + rules.via_pad_mm / 2.0)
        drill_r = rules.via_drill_mm / 2.0
        for pad in self.problem.pads:
            if not pad.is_smd:
                continue
            shape = pad_capsule(pad)
            if getattr(shape, "core", None) is not None:
                field_.stamp_shape(shape, drill_r, HARD)
            else:
                ax, ay, bx, by, radius = shape
                field_.stamp(ax, ay, bx, by, radius + drill_r, HARD)
            if pad in self._rotated_pads:
                ax, ay, bx, by, radius = stadium_capsule(
                    pad.center.x, pad.center.y, pad.width_mm, pad.height_mm, 0.0
                )
                field_.stamp(ax, ay, bx, by, radius + drill_r, HARD)
        for drill in self.problem.drills:
            ax, ay, bx, by, radius = drill_capsule(drill)
            field_.stamp(
                ax, ay, bx, by, radius + drill_r + self.margins.hole_to_hole, HARD
            )
        self._via_hard = field_
        return field_

    def _stamp_trace(self, trace: Trace) -> None:
        half = trace.width_mm / 2.0
        code = self.code.get(trace.net, HARD)
        for width, fields in self._fields.items():
            target = fields.get(trace.layer)
            if target is None:
                continue
            reach = half + width / 2.0 + self.margins.clearance
            for a, b in trace.segments:
                target.stamp(a.x, a.y, b.x, b.y, reach, code)

    def _stamp_via(self, via: Via) -> None:
        code = self.code.get(via.net, HARD)
        half = via.pad_mm / 2.0
        x, y = via.center.x, via.center.y
        for width, fields in self._fields.items():
            reach = half + width / 2.0 + self.margins.clearance
            for target in fields.values():
                target.stamp(x, y, x, y, reach, code)
        if self._via_hard is not None:
            self._via_hard.stamp(
                x,
                y,
                x,
                y,
                via.drill_mm / 2.0
                + self.rules.via_drill_mm / 2.0
                + self.margins.hole_to_hole,
                HARD,
            )

    # -- exact verification ----------------------------------------------

    def path_legal(
        self, layer: str, points: Sequence[Point], width: float, net: str
    ) -> bool:
        """The grid says maybe; this says yes.

        :class:`Workspace` measures with the geometry the scorer uses, so a
        path that passes here cannot produce a clearance, keepout, edge or
        hole-clearance *error* in the score. The extra loop is the one thing
        Workspace does not carry: JLC's recommended 0.35mm to a component
        plated hole, which is a warning rather than an error and so has no
        field in ``DesignRules``.
        """
        if self.ws.path_ok(layer, points, width, net) is not True:
            return False
        margin = self.margins.pth_to_copper
        if margin <= self.rules.min_pth_to_copper_mm + 1e-9 or not self._pth:
            return True
        for a, b in zip(points, points[1:]):
            if a == b:
                continue
            copper = segment_capsule(a.x, a.y, b.x, b.y, width)
            for drill in self._pth:
                if drill.net and drill.net == net:
                    continue
                if capsule_gap(copper, drill_capsule(drill)) < margin:
                    return False
        return True

    def via_legal(self, center: Point, net: str) -> bool:
        if self.ws.via_ok(center, net) is not True:
            return False
        rules = self.rules
        half = rules.via_pad_mm / 2.0
        x0, y0, x1, y1 = self.problem.board.bbox
        margin = min(
            center.x - half - x0,
            center.y - half - y0,
            x1 - center.x - half,
            y1 - center.y - half,
        )
        if margin < self.margins.edge_via - 1e-9:
            return False
        if self.margins.pth_to_copper > rules.min_pth_to_copper_mm + 1e-9:
            pad = disc_capsule(center.x, center.y, rules.via_pad_mm)
            for drill in self._pth:
                if drill.net and drill.net == net:
                    continue
                if capsule_gap(pad, drill_capsule(drill)) < self.margins.pth_to_copper:
                    return False
        # The scorer's hole-clearance check reads a turned pad as if it were
        # upright, so a via that is 0.30mm clear of the real copper can be
        # reported as overlapping. Clear the unrotated shape too.
        if self._rotated_pads:
            hole = disc_capsule(center.x, center.y, rules.via_drill_mm)
            for other in self._rotated_pads:
                if other.net and other.net == net:
                    continue
                flat = stadium_capsule(
                    other.center.x, other.center.y, other.width_mm, other.height_mm, 0.0
                )
                if capsule_gap(hole, flat) < rules.min_via_to_copper_mm:
                    return False
        return True

    # -- commit ----------------------------------------------------------

    def commit_trace(
        self, net: str, layer: str, points: Sequence[Point], width: float
    ) -> Trace:
        trace = Trace(
            id=f"{net}~{self._seq}",
            net=net,
            layer=layer,
            points=tuple(points),
            width_mm=width,
        )
        self._seq += 1
        self.ws.commit_trace(trace)
        self._stamp_trace(trace)
        self.traces.append(trace)
        return trace

    def commit_via(self, net: str, center: Point) -> Via:
        via = Via(
            id=f"v{self._seq}",
            net=net,
            center=center,
            drill_mm=self.rules.via_drill_mm,
            pad_mm=self.rules.via_pad_mm,
        )
        self._seq += 1
        self.ws.commit_via(via)
        self._stamp_via(via)
        self.vias.append(via)
        return via

    # -- geometry helpers -------------------------------------------------

    def cells_under(self, layer: str, points: Sequence[Point]) -> set[int]:
        """Grid states the committed centreline actually passes through.

        Taken from the *committed* polyline and never from the search path: the
        pull-taut pass moves the copper off the cells the search walked, and a
        later branch that started from one of those cells would be a trace that
        touches nothing — copper the scorer would count as orphaned and the net
        as unconnected, while this router believed it had joined them.
        """
        grid = self.grid
        offset = 0 if layer == TOP else grid.size
        out: set[int] = set()
        for a, b in zip(points, points[1:]):
            length = a.distance_to(b)
            steps = max(1, int(math.ceil(length / (grid.pitch * 0.5))))
            for i in range(steps + 1):
                t = i / steps
                cell = grid.cell_at(
                    a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t
                )
                if cell is not None:
                    out.add(cell + offset)
        return out

    def in_plane(self, planes: Sequence[Plane], x: float, y: float) -> bool:
        for plane in planes:
            if not self.plane_shape[plane.id].contains(x, y):
                continue
            if any(hole.contains(x, y) for hole in self.plane_holes[plane.id]):
                continue
            return True
        return False

    def via_cell_ok(self, cell: int, net_id: str) -> bool:
        """The grid's opinion on whether a via may sit on this cell."""
        if self.via_hard.cells[cell] != FREE:
            return False
        code = self.code.get(net_id, HARD)
        fields = self.field(self.rules.via_pad_mm)
        for layer in (TOP, BOTTOM):
            value = fields[layer].cells[cell]
            if value != FREE and value != code:
                return False
        return True


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


class PlaneAndClassesRouter:
    """Planes are nets. Nets have classes. The class decides the route."""

    name = "plane-and-classes"

    #: Class order: the nets with a *width* constraint, then the nets with a
    #: *symmetry* constraint, then ground, then plain signals — and plane
    #: stitching last (see ``_PLANE_GROUP``, which is where the intuition and
    #: the measurement disagree).
    GROUP_ORDER = ("power", "diff_pair", "ground", "signal", "plane")

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        import time

        started = time.perf_counter()
        meter = budget.meter()
        limits = _Limits(
            node_cap=_NODE_CAP,
            plane_node_cap=_PLANE_NODE_CAP,
            max_nodes=budget.max_nodes,
            max_iterations=budget.max_iterations,
        )
        profile = _profile_of(problem.rules)
        clean = Margins.clean(problem.rules, profile)
        routable = {n.id for n in problem.routable_nets}
        notes: list[str] = []

        relaxed = Margins.relaxed(problem.rules, profile)
        # Restarts, in order, each keeping the best board so far. A restart is
        # this router's substitute for rip-up: weaker, because it can change
        # *who* loses a contested channel but cannot open one by moving copper
        # that is already down — and much simpler to keep deterministic.
        best: PassResult | None = None
        for label, reorder, is_relaxed in PASS_PLAN:
            if best is not None and (not best.failed or _spent(meter, limits)):
                break
            margins = relaxed if is_relaxed else clean
            result = self._pass(
                problem,
                margins,
                meter,
                limits,
                failed_before=set(best.failed) if best else set(),
                reorder=reorder,
                relaxed=is_relaxed,
            )
            if best is None or result.key < best.key:
                best = result
        assert best is not None

        # Finally, extend the winner rather than replacing it: copper that
        # already verified clean stays exactly where it is, and only the nets
        # still open are retried on top of it at the relaxed margins.
        if best.failed and not _spent(meter, limits):
            best = self._extend(problem, best, relaxed, meter, limits)
        if _spent(meter, limits):
            notes.append(
                f"the counted budget ran out ({meter.iterations} iterations, "
                f"{meter.nodes} nodes) — some nets were never attempted"
            )
        unrouted = tuple(sorted(routable - best.connected))
        notes.extend(best.notes)
        return RoutingSolution(
            router=self.name,
            traces=tuple(best.traces),
            vias=tuple(best.vias),
            complete=not unrouted,
            unrouted_nets=unrouted,
            iterations=meter.iterations,
            nodes_expanded=meter.nodes,
            wall_clock_s=time.perf_counter() - started,
            notes=tuple(notes),
        )

    # -- one pass --------------------------------------------------------

    def _pass(
        self,
        problem: RoutingProblem,
        margins: Margins,
        meter: BudgetMeter,
        limits: _Limits,
        *,
        failed_before: set[str],
        reorder: int,
        relaxed: bool = False,
        only: set[str] | None = None,
        seq_start: int = 0,
    ) -> PassResult:
        widths = _width_ladders(problem, relaxed=relaxed)
        session = _Session(problem, margins, meter, limits, widths, seq_start=seq_start)
        connected: set[str] = set()
        failed: list[str] = []
        for job in self._jobs(problem, failed_before, reorder):
            if only is not None and not any(n.id in only for n in job.nets):
                continue
            if _spent(meter, limits):
                failed.extend(net.id for net in job.nets)
                continue
            meter.tick()
            done = job.run(session)
            for net in job.nets:
                if net.id in done:
                    connected.add(net.id)
                else:
                    failed.append(net.id)
        return PassResult(
            traces=session.traces,
            vias=session.vias,
            connected=connected,
            failed=failed,
            notes=[],
        )

    def _extend(
        self,
        problem: RoutingProblem,
        base: PassResult,
        margins: Margins,
        meter: BudgetMeter,
        limits: _Limits,
    ) -> PassResult:
        """Retry only the nets still open, on top of the copper already placed.

        Two things make this better than another full pass. The copper that
        already verified clean is *kept*, so a relaxed retry can never cost a
        clean route. And the partial copper of a net that failed is **dropped**
        — a half-routed net is dead metal that only blocks the retry, and
        leaving it in would also leave orphan copper the scorer would rightly
        report.
        """
        from dataclasses import replace

        open_nets = set(base.failed)
        keep_traces = [t for t in base.traces if t.net not in open_nets]
        keep_vias = [v for v in base.vias if v.net not in open_nets]
        seeded = replace(
            problem,
            existing_traces=problem.existing_traces + tuple(keep_traces),
            existing_vias=problem.existing_vias + tuple(keep_vias),
        )
        extra = self._pass(
            seeded, margins, meter, limits,
            failed_before=open_nets, reorder=2, relaxed=True,
            only=open_nets, seq_start=len(keep_traces) + len(keep_vias) + 1,
        )
        notes = list(base.notes)
        if extra.connected:
            notes.append(
                f"{len(extra.connected)} net(s) were placed only on the relaxed "
                f"pass, at {margins.clearance:g}mm clearance or narrower copper: "
                "above every fab floor, below the warn bands, so each buys its "
                "connection with a DFM warning rather than staying open"
            )
        return PassResult(
            traces=keep_traces + extra.traces,
            vias=keep_vias + extra.vias,
            connected=base.connected | extra.connected,
            failed=sorted(open_nets - extra.connected),
            notes=notes,
        )

    def _jobs(
        self, problem: RoutingProblem, failed_before: set[str], reorder: int
    ) -> list["_Job"]:
        by_id = problem.nets_by_id
        planed = {p.net for p in problem.planes}
        seen: set[str] = set()
        jobs: list[_Job] = []
        for net in problem.nets:
            if net.id in seen or not net.routable:
                continue
            pads = problem.pads_of(net.id)
            if net.id in planed:
                seen.add(net.id)
                jobs.append(
                    _PlaneJob(nets=(net,), length=_mst_length(pads), group=_PLANE_GROUP)
                )
                continue
            partner = by_id.get(net.diff_partner or "")
            if (
                net.net_class == "diff_pair"
                and partner is not None
                and partner.routable
                and partner.id not in planed
            ):
                first, second = sorted((net, partner), key=lambda n: n.id)
                seen.add(first.id)
                seen.add(second.id)
                jobs.append(
                    _PairJob(
                        nets=(first, second),
                        length=_mst_length(problem.pads_of(first.id))
                        + _mst_length(problem.pads_of(second.id)),
                        group=2,
                    )
                )
                continue
            seen.add(net.id)
            group = {"power": 1, "diff_pair": 2, "ground": 3}.get(net.net_class, 4)
            jobs.append(_NetJob(nets=(net,), length=_mst_length(pads), group=group))

        def sort_key(job: "_Job") -> tuple:
            retry = 0 if any(n.id in failed_before for n in job.nets) else 1
            base = (round(job.length, 6), job.nets[0].id)
            if reorder == 0:
                return (job.group, 1, *base)
            if reorder == 1:
                return (job.group, retry, *base)
            # Last pass: whatever is still open goes first, class order second.
            return (retry, job.group, *base)

        return sorted(jobs, key=sort_key)


def _width_ladders(problem: RoutingProblem, *, relaxed: bool) -> dict[str, list[float]]:
    """Widths to try per net, widest first.

    Every net is *first* offered the width the rules resolved for it. The
    ladder below it exists because of what the score says: a rail routed under
    ``warn_power_trace_mm`` earns exactly one DFM warning for the net, and one
    warning is cheaper than one unrouted net in every comparison the scorer
    makes. So a rail narrows rather than staying open — measured on
    matrix-ldo-3v3__usb-c-power, which is 60% complete with rails pinned at
    0.5mm and 100% complete when they may fall back to 0.2mm.

    It is a trade and not a discount: the last rung is the fab's own
    recommended minimum (0.15mm), never its floor, and a real board that hits
    the bottom rung is telling you the placement is too tight for its rails.
    """
    rules = problem.rules
    ladders: dict[str, list[float]] = {}
    for net in problem.nets:
        full = max(net.min_width_mm, rules.min_trace_mm)
        steps = [full]
        for candidate in (rules.signal_trace_mm, rules.warn_trace_mm):
            value = round(max(candidate, rules.min_trace_mm), 6)
            if value < steps[-1] - 1e-9:
                steps.append(value)
        ladders[net.id] = steps
    return ladders


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@dataclass
class _Job:
    nets: tuple[Net, ...]
    length: float
    group: int

    def run(self, session: _Session) -> set[str]:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class _PlaneJob(_Job):
    """A net that owns a poured plane. It is not routed; its pads are stitched.

    Every pad either already sits in the pour — a plated hole through it, or an
    SMD pad on the poured layer — or takes one via into it. That is the whole
    strategy for what is usually the biggest net on the board, and it is the
    thing the router we ship cannot express: handed a plane with 73 stitching
    vias it produced byte-identical copper, because it saw 73 obstacles.
    """

    def run(self, session: _Session) -> set[str]:
        net = self.nets[0]
        planes = session.plane_of.get(net.id, [])
        pads = sorted(session.problem.pads_of(net.id), key=lambda p: p.id)
        # A poured rail carries its own current, so its stubs are exempt from
        # the power-width rule (``checks.power_width_warnings`` skips a poured
        # net). Stitching at signal width is correct here, not a shortcut.
        width = max(session.rules.signal_trace_mm, session.rules.min_trace_mm)
        stranded: list[Pad] = []
        for pad in pads:
            session.meter.tick()
            # Already in the pour on a layer this pad reaches: the scorer merges
            # same-net copper whose midpoint lands inside a plane, so there is
            # nothing to place.
            on_pad_layer = [p for p in planes if p.layer in pad.layers]
            if session.in_plane(on_pad_layer, pad.center.x, pad.center.y):
                continue
            if _stitch_pad(session, net, pad, planes, width):
                continue
            stranded.append(pad)
        if not stranded:
            return {net.id}
        # Whatever could not reach the pour is routed the ordinary way, to a
        # pad that did. Calling a plane net connected while one pad floats is
        # precisely the failure this package exists to stop.
        stranded_ids = {p.id for p in stranded}
        anchored = [p for p in pads if p.id not in stranded_ids]
        subset = stranded + anchored[:1]
        if len(subset) >= 2 and _route_net(
            session, net, width_override=width, pads_override=subset
        ):
            return {net.id}
        return set()


@dataclass
class _NetJob(_Job):
    def run(self, session: _Session) -> set[str]:
        net = self.nets[0]
        return {net.id} if _route_net(session, net) else set()


@dataclass
class _PairJob(_Job):
    """Both halves of a differential pair, routed back to back.

    The first half is routed normally. The second is routed with every cell
    *outside* a corridor beside the first made twice as expensive, so the
    cheapest path is the one that runs alongside its partner. That buys the
    coupling the scorer measures without pretending to be a length-matched
    bundle: the gap is steered, not constructed, and the report says so.
    """

    def run(self, session: _Session) -> set[str]:
        first, second = self.nets
        done: set[str] = set()
        before = len(session.traces)
        if _route_net(session, first):
            done.add(first.id)
        corridor = _corridor_from(session, session.traces[before:])
        if _route_net(session, second, corridor=corridor):
            done.add(second.id)
        return done


def _corridor_from(
    session: _Session, traces: Sequence[Trace]
) -> dict[str, Field] | None:
    """Cells within the scorer's coupling window of the copper just placed."""
    if not traces:
        return None
    window = session.rules.diff_pair_gap_mm * 3.0
    fields = {layer: Field(session.grid, fill=0) for layer in (TOP, BOTTOM)}
    for trace in traces:
        target = fields.get(trace.layer)
        if target is None:
            continue
        for a, b in trace.segments:
            target.mark(a.x, a.y, b.x, b.y, window, 1)
    return fields


def _stitch_pad(
    session: _Session, net: Net, pad: Pad, planes: Sequence[Plane], width: float
) -> bool:
    """One via from this pad into the pour.

    Tried first as a ring of candidate points around the pad — the common case,
    one short stub and one via — and then, when the pad is boxed in, as a short
    Dijkstra to the nearest cell where a via is legal and lands in the pour.
    """
    layer = pad.layers[0]
    for point in _ring_points(session, pad):
        session.meter.tick()
        if not session.in_plane(planes, point.x, point.y):
            continue
        if not session.via_legal(point, net.id):
            continue
        stub = (pad.center, point)
        if not session.path_legal(layer, stub, width, net.id):
            continue
        session.commit_trace(net.id, layer, stub, width)
        session.commit_via(net.id, point)
        return True

    grid = session.grid
    fields = session.field(width)
    starts = _access_states(session, pad, width, net.id, (layer,))
    if not starts:
        return False

    def goal(cell: int) -> bool:
        x, y = grid.xy(cell)
        return session.in_plane(planes, x, y) and session.via_cell_ok(cell, net.id)

    found = _dijkstra(
        session,
        starts,
        goal,
        fields,
        layer,
        net.id,
        node_cap=session.limits.plane_node_cap,
    )
    if found is None:
        return False
    end_state, came = found
    states = _reconstruct(end_state, came)
    points = [pad.center] + [grid.point(s % grid.size) for s in states]
    simplified = _simplify(session, layer, points, width, net.id)
    if simplified is None:
        return False
    via_point = simplified[-1]
    if not session.via_legal(via_point, net.id):
        return False
    session.commit_trace(net.id, layer, simplified, width)
    session.commit_via(net.id, via_point)
    return True


def _ring_points(session: _Session, pad: Pad) -> list[Point]:
    """Candidate via sites around a pad, nearest ring first, fixed order."""
    rules = session.rules
    base = (
        max(pad.width_mm, pad.height_mm) / 2.0
        + session.margins.clearance
        + rules.via_pad_mm / 2.0
    )
    out: list[Point] = []
    seen: set[tuple[float, float]] = set()
    for step in _RING_STEPS:
        reach = base + step
        for k in range(_RING_DIRECTIONS):
            angle = 2.0 * math.pi * k / _RING_DIRECTIONS
            point = Point(
                pad.center.x + math.cos(angle) * reach,
                pad.center.y + math.sin(angle) * reach,
            )
            key = (point.x, point.y)
            if key in seen:
                continue
            seen.add(key)
            out.append(point)
    return out


# ---------------------------------------------------------------------------
# Routing an ordinary net
# ---------------------------------------------------------------------------


def _route_net(
    session: _Session,
    net: Net,
    *,
    width_override: float | None = None,
    corridor: dict[str, Field] | None = None,
    pads_override: Sequence[Pad] | None = None,
) -> bool:
    """Connect every pad of one net, all or nothing.

    A net's copper is **staged, not committed, until the whole net is
    connected.** A half-routed net is worth nothing to the score and worse than
    nothing to the board: it is copper across a channel that the nets after it
    then have to get around, and it is dead metal in the output. So a failed
    net leaves no trace of itself and the next net sees the board it would have
    seen if this one had never been tried.

    Staging is safe because same-net copper is exempt from every clearance rule
    in both the Workspace and the scorer. The one rule it is *not* exempt from
    is hole-to-hole between two vias, so the staged vias are carried into each
    edge's check explicitly.

    True only when every pad ends up in one component — the claim comes from
    the union-find over copper that verified, not from counting edges that
    happened to succeed.
    """
    pads = list(pads_override) if pads_override else list(session.problem.pads_of(net.id))
    pads = sorted({p.id: p for p in pads}.values(), key=lambda p: p.id)
    if len(pads) < 2:
        return True
    ladder = [width_override] if width_override is not None else session.widths[net.id]

    union = _Union()
    cells: dict[str, set[int]] = {}
    prefix: dict[str, dict[int, Point]] = {}
    staged = _Staged()
    for pad_a, pad_b in _mst_edges(pads):
        session.meter.tick()
        if session.meter.nodes >= session.limits.max_nodes:
            break
        if union.find(pad_a.id) == union.find(pad_b.id):
            continue
        for width in ladder:
            if _route_edge(
                session, net, pad_a, pad_b, width, union, cells, prefix, corridor, staged
            ):
                break
    if len({union.find(p.id) for p in pads}) != 1:
        return False
    for layer, points, width in staged.traces:
        session.commit_trace(net.id, layer, points, width)
    for point in staged.vias:
        session.commit_via(net.id, point)
    return True


@dataclass
class _Staged:
    """Copper a net has verified but not yet put on the board."""

    traces: list[tuple[str, list[Point], float]] = field(default_factory=list)
    vias: list[Point] = field(default_factory=list)


def _route_edge(
    session: _Session,
    net: Net,
    pad_a: Pad,
    pad_b: Pad,
    width: float,
    union: _Union,
    cells: dict[str, set[int]],
    prefix: dict[str, dict[int, Point]],
    corridor: dict[str, Field] | None,
    staged: "_Staged",
) -> bool:
    grid = session.grid
    ncells = grid.size
    root_a, root_b = union.find(pad_a.id), union.find(pad_b.id)
    start_states, start_prefix = _component_states(
        session, root_a, pad_a, width, net.id, cells, prefix
    )
    goal_states, goal_prefix = _component_states(
        session, root_b, pad_b, width, net.id, cells, prefix
    )
    if not start_states or not goal_states:
        return False

    found = _astar(
        session,
        start_states,
        set(goal_states),
        _anchors(session, root_b, pad_b, cells),
        session.field(width),
        net.id,
        corridor,
        node_cap=session.limits.node_cap,
    )
    if found is None:
        return False
    end_state, came = found
    states = _reconstruct(end_state, came)
    runs = _to_runs(session, states, start_prefix, goal_prefix, ncells)

    # Everything below is exact. Nothing is committed until all of it passes.
    plan_traces: list[tuple[str, list[Point]]] = []
    for layer, points in runs:
        if len(points) < 2:
            continue
        simplified = _simplify(session, layer, points, width, net.id)
        if simplified is None:
            return False
        plan_traces.append((layer, simplified))
    plan_vias = [runs[i][1][-1] for i in range(len(runs) - 1)]

    for point in plan_vias:
        if not session.via_legal(point, net.id):
            return False
    # Two barrels too close break out into each other whatever they carry, so
    # hole-to-hole is the one rule a net is not exempt from against itself.
    # Neither this path's vias nor the net's earlier staged ones are in the
    # Workspace yet, so they are checked here rather than assumed apart.
    floor = session.margins.hole_to_hole + session.rules.via_drill_mm
    everything = list(staged.vias) + plan_vias
    for i in range(len(everything)):
        for j in range(max(i + 1, len(staged.vias)), len(everything)):
            if everything[i].distance_to(everything[j]) < floor - 1e-9:
                return False
    for layer, points in plan_traces:
        if not session.path_legal(layer, points, width, net.id):
            return False

    touched: set[int] = set()
    for layer, points in plan_traces:
        staged.traces.append((layer, points, width))
        touched |= session.cells_under(layer, points)
    for point in plan_vias:
        staged.vias.append(point)
        cell = grid.cell_at(point.x, point.y)
        if cell is not None:
            touched.add(cell)
            touched.add(cell + ncells)

    root = union.union(pad_a.id, pad_b.id)
    merged = cells.pop(root_a, set()) | cells.pop(root_b, set()) | touched
    merged_prefix = {**prefix.pop(root_a, {}), **prefix.pop(root_b, {})}
    for state in touched:
        merged_prefix.pop(state, None)
    cells[root] = merged
    prefix[root] = merged_prefix
    return True


def _component_states(
    session: _Session,
    root: str,
    pad: Pad,
    width: float,
    net_id: str,
    cells: dict[str, set[int]],
    prefix: dict[str, dict[int, Point]],
) -> tuple[dict[int, int], dict[int, Point]]:
    """Where a search may begin, or end, for this component.

    Two kinds of state. Copper already placed for the component is *on* the
    net, so a branch may leave it at no cost — that is what makes a T-junction
    possible instead of a star of parallel runs back to one pad. A pad with no
    copper on it yet is entered at its centre, paying for the short stub out to
    the first cell.
    """
    states: dict[int, int] = {}
    prefixes: dict[int, Point] = {}
    existing = cells.get(root)
    if existing:
        for state in existing:
            states[state] = 0
        prefixes.update(prefix.get(root, {}))
        return states, prefixes
    for state, cost in _access_states(session, pad, width, net_id, pad.layers).items():
        states[state] = cost
        prefixes[state] = pad.center
    return states, prefixes


def _access_states(
    session: _Session, pad: Pad, width: float, net_id: str, layers: Sequence[str]
) -> dict[int, int]:
    """The nearest cells to a pad centre that a trace of ``width`` may legally
    sit on, each with the cost of the stub from the centre out to it."""
    grid = session.grid
    fields = session.field(width)
    code = session.code.get(net_id, HARD)
    reach = max(pad.width_mm, pad.height_mm) / 2.0 + 2.5 * grid.pitch
    radius = int(math.ceil(reach / grid.pitch))
    col0, row0 = grid.col_of(pad.center.x), grid.row_of(pad.center.y)
    found: list[tuple[float, int]] = []
    for drow in range(-radius, radius + 1):
        row = row0 + drow
        if not (0 <= row < grid.ny):
            continue
        for dcol in range(-radius, radius + 1):
            col = col0 + dcol
            if not (0 <= col < grid.nx):
                continue
            cell = row * grid.nx + col
            x, y = grid.xy(cell)
            distance = math.hypot(x - pad.center.x, y - pad.center.y)
            if distance > reach:
                continue
            for layer_index, layer in enumerate((TOP, BOTTOM)):
                if layer not in layers:
                    continue
                value = fields[layer].cells[cell]
                if value != FREE and value != code:
                    continue
                found.append((round(distance, 9), cell + layer_index * grid.size))
    found.sort()
    return {
        state: int(round(distance * 1000.0)) for distance, state in found[:_ACCESS_FANOUT]
    }


def _anchors(
    session: _Session, root: str, pad: Pad, cells: dict[str, set[int]]
) -> list[tuple[float, float]]:
    """Points the heuristic measures toward. Capped, so a search over a large
    component does not pay for a minimum over a thousand cells."""
    points = [(pad.center.x, pad.center.y)]
    existing = cells.get(root)
    if existing:
        grid = session.grid
        ordered = sorted(existing)
        points.append(grid.xy(ordered[len(ordered) // 2] % grid.size))
    return points[:2]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _neighbour_table(grid: Grid) -> tuple[tuple[int, int, int, int], ...]:
    """``(dcol, drow, cost, diagonal)`` in a fixed order, so two runs expand the
    same cell in the same sequence."""
    axis = int(round(grid.pitch * 1000.0))
    diag = int(round(grid.pitch * math.sqrt(2.0) * 1000.0))
    return (
        (1, 0, axis, 0),
        (0, 1, axis, 0),
        (-1, 0, axis, 0),
        (0, -1, axis, 0),
        (1, 1, diag, 1),
        (1, -1, diag, 1),
        (-1, 1, diag, 1),
        (-1, -1, diag, 1),
    )


def _astar(
    session: _Session,
    starts: dict[int, int],
    goals: set[int],
    anchors: Sequence[tuple[float, float]],
    fields: dict[str, Field],
    net_id: str,
    corridor: dict[str, Field] | None,
    *,
    node_cap: int,
) -> tuple[int, dict[int, int]] | None:
    """8-connected A* over two layers; a layer change is a via.

    Costs are integers in microns and the queue key is ``(f, state)``, so two
    runs break every tie the same way. That is the determinism property, and it
    is a property of the data structure rather than of good behaviour.
    """
    grid = session.grid
    ncells = grid.size
    nx, ny = grid.nx, grid.ny
    code = session.code.get(net_id, HARD)
    top_cells = fields[TOP].cells
    bottom_cells = fields[BOTTOM].cells
    planed = session.planed_layers
    layer_mult = (
        _M_PLANE_LAYER if TOP in planed else _M_ONE,
        _M_PLANE_LAYER if BOTTOM in planed else _M_ONE,
    )
    corridor_top = corridor[TOP].cells if corridor else None
    corridor_bottom = corridor[BOTTOM].cells if corridor else None
    via_fields = session.field(session.rules.via_pad_mm)
    via_top = via_fields[TOP].cells
    via_bottom = via_fields[BOTTOM].cells
    via_hard = session.via_hard.cells
    via_cost = int(round(_VIA_COST_MM * 1000.0))
    steps = _neighbour_table(grid)
    meter = session.meter
    remaining = session.limits.max_nodes - meter.nodes
    x0, y0, pitch = grid.x0, grid.y0, grid.pitch

    # The heuristic is a minimum over a handful of fixed points, so it depends
    # only on the cell and is worth caching: a cell gets pushed several times
    # as cheaper routes to it are found, and recomputing it each time was a
    # fifth of the search.
    hcache: dict[int, int] = {}

    def heuristic(cell: int) -> int:
        value = hcache.get(cell)
        if value is None:
            x = x0 + (cell % nx) * pitch
            y = y0 + (cell // nx) * pitch
            best = math.inf
            for ax, ay in anchors:
                dx, dy = x - ax, y - ay
                d = dx * dx + dy * dy
                if d < best:
                    best = d
            value = int(math.sqrt(best) * _H_WEIGHT)
            hcache[cell] = value
        return value

    dist: dict[int, int] = {}
    came: dict[int, int] = {}
    closed: set[int] = set()
    heap: list[tuple[int, int, int]] = []
    for state, cost in sorted(starts.items()):
        dist[state] = cost
        h = heuristic(state % ncells)
        heapq.heappush(heap, (cost + h, h, state))

    expanded = 0
    while heap:
        _, _, state = heapq.heappop(heap)
        if state in closed:
            continue
        closed.add(state)
        here = dist[state]
        if state in goals:
            meter.nodes += expanded
            return (state, came)
        expanded += 1
        if expanded > node_cap or expanded > remaining:
            break
        cell = state % ncells
        layer_index = state // ncells
        cells = top_cells if layer_index == 0 else bottom_cells
        corridor_cells = corridor_top if layer_index == 0 else corridor_bottom
        mult = layer_mult[layer_index]
        col = cell % nx
        row = cell // nx
        for dcol, drow, step, diagonal in steps:
            ncol, nrow = col + dcol, row + drow
            if ncol < 0 or ncol >= nx or nrow < 0 or nrow >= ny:
                continue
            target = nrow * nx + ncol
            value = cells[target]
            if value != FREE and value != code:
                continue
            if diagonal:
                # No corner cutting: the diagonal step passes through the
                # shared corner of four cells, so both orthogonal neighbours
                # have to be free or the copper clips whatever is in them.
                side = cells[row * nx + ncol]
                if side != FREE and side != code:
                    continue
                side = cells[nrow * nx + col]
                if side != FREE and side != code:
                    continue
            key = target + layer_index * ncells
            if key in closed:
                continue
            cost = step * mult // 10
            if corridor_cells is not None and not corridor_cells[target]:
                cost = cost * _M_OFF_CORRIDOR // 10
            new = here + cost
            old = dist.get(key)
            if old is None or new < old:
                dist[key] = new
                came[key] = state
                h = heuristic(target)
                heapq.heappush(heap, (new + h, h, key))
        if via_hard[cell] == FREE:
            a, b = via_top[cell], via_bottom[cell]
            if (a == FREE or a == code) and (b == FREE or b == code):
                key = cell + (1 - layer_index) * ncells
                if key not in closed:
                    new = here + via_cost
                    old = dist.get(key)
                    if old is None or new < old:
                        dist[key] = new
                        came[key] = state
                        h = heuristic(cell)
                        heapq.heappush(heap, (new + h, h, key))
    meter.nodes += expanded
    return None


def _dijkstra(
    session: _Session,
    starts: dict[int, int],
    goal: Callable[[int], bool],
    fields: dict[str, Field],
    layer: str,
    net_id: str,
    *,
    node_cap: int,
) -> tuple[int, dict[int, int]] | None:
    """Single-layer shortest path to the first cell satisfying ``goal``.

    Used only for a pad that cannot reach its plane with a fanout via. The goal
    is a predicate rather than a set because "anywhere a via lands in the pour"
    is most of the board, and enumerating it would cost more than the search.
    """
    grid = session.grid
    ncells = grid.size
    nx, ny = grid.nx, grid.ny
    code = session.code.get(net_id, HARD)
    cells = fields[layer].cells
    layer_index = 0 if layer == TOP else 1
    steps = _neighbour_table(grid)
    dist: dict[int, int] = {}
    came: dict[int, int] = {}
    heap: list[tuple[int, int]] = []
    for state, cost in sorted(starts.items()):
        if state // ncells != layer_index:
            continue
        dist[state] = cost
        heapq.heappush(heap, (cost, state))
    expanded = 0
    while heap:
        cost, state = heapq.heappop(heap)
        if cost > dist.get(state, cost):
            continue
        cell = state % ncells
        if state not in starts and goal(cell):
            session.meter.nodes += expanded
            return (state, came)
        expanded += 1
        if expanded > node_cap:
            break
        col, row = cell % nx, cell // nx
        for dcol, drow, step, diagonal in steps:
            ncol, nrow = col + dcol, row + drow
            if ncol < 0 or ncol >= nx or nrow < 0 or nrow >= ny:
                continue
            target = nrow * nx + ncol
            value = cells[target]
            if value != FREE and value != code:
                continue
            if diagonal:
                side_a = cells[row * nx + ncol]
                side_b = cells[nrow * nx + col]
                if (side_a != FREE and side_a != code) or (
                    side_b != FREE and side_b != code
                ):
                    continue
            key = target + layer_index * ncells
            new = cost + step
            old = dist.get(key)
            if old is None or new < old:
                dist[key] = new
                came[key] = state
                heapq.heappush(heap, (new, key))
    session.meter.nodes += expanded
    return None


def _reconstruct(end: int, came: dict[int, int]) -> list[int]:
    path = [end]
    node = end
    while node in came:
        node = came[node]
        path.append(node)
    path.reverse()
    return path


def _to_runs(
    session: _Session,
    states: Sequence[int],
    start_prefix: dict[int, Point],
    goal_prefix: dict[int, Point],
    ncells: int,
) -> list[tuple[str, list[Point]]]:
    """Split a state path into one point list per layer, putting the pad
    centres back on the ends where the path started or finished at a pad."""
    grid = session.grid
    runs: list[tuple[str, list[Point]]] = []
    current_layer: str | None = None
    current: list[Point] = []
    for state in states:
        layer = TOP if state // ncells == 0 else BOTTOM
        point = grid.point(state % ncells)
        if layer != current_layer:
            if current and current_layer is not None:
                runs.append((current_layer, current))
            current_layer = layer
            current = [point]
        else:
            current.append(point)
    if current and current_layer is not None:
        runs.append((current_layer, current))
    if runs and states:
        head = start_prefix.get(states[0])
        if head is not None:
            runs[0] = (runs[0][0], [head] + runs[0][1])
        tail = goal_prefix.get(states[-1])
        if tail is not None:
            runs[-1] = (runs[-1][0], runs[-1][1] + [tail])
    return runs


def _simplify(
    session: _Session, layer: str, points: Sequence[Point], width: float, net_id: str
) -> list[Point] | None:
    """Pull the staircase taut.

    A grid path is a sequence of 45-degree steps; the copper that gets built
    should be the straight line those steps were approximating. Every shortcut
    is accepted only when :class:`Workspace` says the straight segment is
    legal, so simplification can shorten a route but never make it illegal.
    Returns ``None`` when even the unsimplified path fails to verify — which is
    the grid being optimistic about the space between two cell centres, and the
    reason nothing is committed until this has run.
    """
    pts = [points[0]]
    for point in points[1:]:
        if point != pts[-1]:
            pts.append(point)
    if len(pts) < 2:
        return None
    out = [pts[0]]
    i = 0
    while i < len(pts) - 1:
        best = i + 1
        for j in range(min(len(pts) - 1, i + _SHORTCUT_SPAN), i, -1):
            if session.ws.segment_ok(layer, pts[i], pts[j], width, net_id) is True:
                best = j
                break
        out.append(pts[best])
        i = best
    if session.path_legal(layer, out, width, net_id):
        return out
    if session.path_legal(layer, pts, width, net_id):
        return list(pts)
    return None


#: The registry a harness can read without importing this file by path.
ROUTERS = {PlaneAndClassesRouter.name: PlaneAndClassesRouter}

__all__ = ["Field", "Grid", "Margins", "PlaneAndClassesRouter", "ROUTERS"]
