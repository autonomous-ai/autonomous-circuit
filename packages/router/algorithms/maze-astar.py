"""maze-astar — grid maze routing: Lee/BFS's descendant, A* with a real cost model.

The classical foundation, done properly. Rasterise the board onto a grid whose
cells encode *who may put copper here*, then find shortest legal paths with A*
over ``(layer, cell)`` states. A via is an edge between the two layer copies of
one cell and it is expensive, so the search buys a layer change only when the
detour it avoids costs more.

Everything that makes this different from a textbook Lee router is in the grid,
not in the search:

* **A cell is not "free" or "blocked", it has an owner.** ``0`` is free, ``-1``
  is blocked for everyone, and any other value is the index of the one net that
  may enter. That is what lets a net run right up to its own pad and its own
  plated hole while everything else keeps its distance, and it is why the search
  never has to ask a geometry question in its inner loop.
* **The grid is built per trace width.** An obstacle is stamped inflated by
  ``clearance + width/2``, so the cells a 0.5mm rail may occupy are a strict
  subset of the cells a 0.2mm signal may occupy. One grid for both would either
  leak violations on the rails or wall off channels the signals fit through.
* **Copper-to-hole is three numbers.** ``rules.hole_clearance(drill)`` is asked
  per drill — 0.20mm to a via, 0.28mm to a component plated hole, 0.20mm to a
  non-plated one — and stamped at that radius. This is the defect the package
  exists because of, and here it is a property of the grid rather than a check
  somebody remembered to run.
* **A plane is a net, not 73 obstacles.** A net with a poured plane starts with
  the whole pour already in its tree; each pad then searches for the cheapest
  way *into* the pour, which is normally one escape and one via.
* **Vias have their own grid**, because a via is legal on different terms from a
  trace: hole-to-hole against every drill on the board including its own net's,
  and never inside an SMD pad whatever net that pad carries.

One rule holds the whole thing honest: **the grid is an approximation, the
Workspace is the truth.** Nothing is committed until ``Workspace.path_ok`` /
``via_ok`` accepts it with the geometry the scorer grades with. When the
Workspace refuses, the offending cell is blocked and the connection is searched
again; after a few refusals the connection is dropped and the net is reported
unrouted. An incomplete board is a bad score. A plausible illegal one is a
scrapped board.

Net ordering matters enormously, so three are implemented and the question is
answered with numbers rather than folklore:

``shortest-first``          ascending MST length — easy nets first, on the
                            theory that they use little room and leave the
                            board open.
``longest-first``           descending MST length — hard nets first, on the
                            theory that a long net has the fewest alternatives.
``most-constrained-first``  descending ``mst_length / pad-bbox area``: measured
                            copper demand per square millimetre of the net's own
                            territory, pad count as tie-break. "Constrained" as a
                            measurement, not as a guess about which net looks hard.

Run it:

    python3.12 packages/router/algorithms/maze-astar.py run
    python3.12 packages/router/algorithms/maze-astar.py orderings
"""

from __future__ import annotations

import heapq
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Container, Sequence


def _bootstrap() -> None:
    """Make ``routerlib`` importable when this file is run directly.

    It lives in ``algorithms/``, outside the package, on purpose: an entrant
    must not be able to change the foundation by accident.
    """
    try:
        import routerlib  # noqa: F401
    except ModuleNotFoundError:  # pragma: no cover - path plumbing
        package = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(package / "src"))
        sys.path.insert(0, str(package.parent / "circuitpy" / "src"))


_bootstrap()

from routerlib.geometry import (  # noqa: E402
    Capsule,
    capsule_bbox,
    core_halfplanes,
    disc_capsule,
    drill_capsule,
    keepout_capsule,
    pad_capsule,
    point_shape_distance,
    segment_capsule,
)
from routerlib.model import (  # noqa: E402
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
from routerlib.workspace import Workspace  # noqa: E402

# ---------------------------------------------------------------------------
# The numbers that are choices, named so they can be argued with
# ---------------------------------------------------------------------------

#: Candidate grid pitches, finest first; the finest whose grid fits in
#: :data:`MAX_CELLS` wins. A* cost scales with the *area* it explores measured
#: in cells — i.e. with 1/pitch² — which is why this is capped by cell count
#: rather than fixed at some pretty resolution.
PITCH_LADDER: tuple[float, ...] = (0.1, 0.125, 0.15, 0.2, 0.25, 0.3, 0.4)

#: Cells per layer. 500k keeps the largest instance (112 x 90mm) at 0.15mm.
MAX_CELLS = 500_000

#: Integer step costs in thousandths of a cell. Integers, not floats, because a
#: priority queue ordered by float sums has tie-breaks that depend on the last
#: bit of an addition, and a router whose tie-breaks move is not deterministic.
ORTH_COST = 1000
DIAG_COST = 1414  # round(1000 * sqrt(2))

#: What a via costs, expressed as the copper it must save to be worth placing.
#: Vias are the third thing the scorer ranks on and the second thing the fab
#: charges for.
VIA_COST_MM = 4.0

#: Hard ceiling on expanded nodes for one connection. Without it, one hopeless
#: net floods the board and eats the budget every other net needed.
MAX_NODES_PER_CONNECTION = 120_000

#: How many times a connection is re-searched after the Workspace refuses what
#: the grid proposed. Each refusal blocks the offending cell first, so a retry
#: is a strictly harder search and the loop terminates.
MAX_WORKSPACE_RETRIES = 3

#: Safety margin added to every grid stamp, in cells — **measured to zero**.
#:
#: The argument for a margin is sound: the grid answers "may a trace *centred
#: here* go here", but copper also exists between two cells, and the midpoint of
#: a diagonal step is 0.71 cells from either end. Distance is 1-Lipschitz, so
#: 0.72 cells of margin makes the grid provably unable to approve anything the
#: Workspace will refuse.
#:
#: It was also wrong. The Workspace refusals it was meant to remove were not
#: sub-cell error at all — they were the two *off-grid* points in a polyline,
#: the stub to a pad centre and the anchor on existing copper, neither of which
#: any cell stands for. Fixing those took refusals on
#: ``matrix-rp2040-core__usb-c-data`` from 76 to 8. Measured over
#: ``matrix-rp2040-core__{usb-c-data,sw-tact}``, mean completeness by margin:
#: **0.0 -> 86.9%**, 0.25 -> 84.5%, 0.5 -> 77.4%. The margin buys a soundness
#: the Workspace gate already provides, and pays 9.5 points of completeness for
#: it. Kept as a knob, defaulted to what the measurement said.
GRID_MARGIN_CELLS = 0.0

#: Heuristic multiplier for the second attempt at a connection whose optimal
#: search ran out of nodes. Weighted A* trades optimality for reach: the path
#: may be up to this factor longer than the best one, and it is found in a
#: fraction of the expansions.
GREEDY_WEIGHT = 3

#: A step into a cell beside an already-routed differential partner costs this
#: fraction of normal. The heuristic is scaled by the same factor so A* stays
#: admissible: the discount buys coupling, not a wrong answer.
COUPLING_BONUS = 0.75

ORDERINGS: tuple[str, ...] = (
    "shortest-first",
    "longest-first",
    "most-constrained-first",
)

BLOCKED = -1
FREE = 0


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GridSpec:
    """Cell centres at ``(x0 + (i + 0.5) * pitch, y0 + (j + 0.5) * pitch)``.

    One cell of margin all round, permanently blocked, so the search's inner
    loop needs no bounds check on the horizontal neighbours.
    """

    x0: float
    y0: float
    pitch: float
    nx: int
    ny: int

    @property
    def ncells(self) -> int:
        return self.nx * self.ny

    def cell_at(self, x: float, y: float) -> int:
        i = int((x - self.x0) / self.pitch)
        j = int((y - self.y0) / self.pitch)
        if i < 0:
            i = 0
        elif i >= self.nx:
            i = self.nx - 1
        if j < 0:
            j = 0
        elif j >= self.ny:
            j = self.ny - 1
        return j * self.nx + i

    def centre(self, cell: int) -> tuple[float, float]:
        j, i = divmod(cell, self.nx)
        return (self.x0 + (i + 0.5) * self.pitch, self.y0 + (j + 0.5) * self.pitch)


def choose_pitch(problem: RoutingProblem, *, max_cells: int = MAX_CELLS) -> float:
    """The finest ladder pitch whose grid fits. A function of the board's size
    alone — never of the clock, the machine, or how loaded either one is."""
    x0, y0, x1, y1 = problem.board.bbox
    width, height = x1 - x0, y1 - y0
    for pitch in PITCH_LADDER:
        nx = int(math.ceil(width / pitch)) + 2
        ny = int(math.ceil(height / pitch)) + 2
        if nx * ny <= max_cells:
            return pitch
    return PITCH_LADDER[-1]


def make_spec(problem: RoutingProblem, pitch: float) -> GridSpec:
    x0, y0, x1, y1 = problem.board.bbox
    return GridSpec(
        x0=x0 - pitch,
        y0=y0 - pitch,
        pitch=pitch,
        nx=int(math.ceil((x1 - x0) / pitch)) + 2,
        ny=int(math.ceil((y1 - y0) / pitch)) + 2,
    )


def _width_key(width: float) -> int:
    return int(round(width * 10000))


def _merge_collinear(points: Sequence[Point]) -> list[Point]:
    """Drop points that lie on the straight line between their neighbours.

    Exact: the polyline that comes out covers the same copper as the one that
    went in. A grid path is mostly long straight runs with a few turns, so this
    alone takes a 250-point staircase down to a dozen corners.
    """
    if len(points) <= 2:
        return list(points)
    out = [points[0]]
    for previous, current, following in zip(points, points[1:], points[2:]):
        cross = (current.x - previous.x) * (following.y - previous.y) - (
            current.y - previous.y
        ) * (following.x - previous.x)
        if abs(cross) > 1e-12:
            out.append(current)
    out.append(points[-1])
    return out


def _stamp(grid: list, spec: GridSpec, cap: Capsule, extra: float, value: int) -> None:
    """Paint ``value`` over every cell whose centre is within ``extra`` of the
    capsule's copper.

    Owner semantics, and they are the whole clearance model: free takes the
    value, a different owner becomes :data:`BLOCKED`, the same owner is left
    alone. The geometry happens once, here, and never again inside the search.
    """
    planes = core_halfplanes(cap)
    if planes is not None:
        _stamp_shape(grid, spec, cap, planes, extra, value)
        return
    ax, ay, bx, by, r = cap
    rr = r + extra
    if rr <= 0.0:
        return
    px = spec.pitch
    i0 = int((min(ax, bx) - rr - spec.x0) / px)
    i1 = int((max(ax, bx) + rr - spec.x0) / px)
    j0 = int((min(ay, by) - rr - spec.y0) / px)
    j1 = int((max(ay, by) + rr - spec.y0) / px)
    if i1 < 0 or j1 < 0 or i0 >= spec.nx or j0 >= spec.ny:
        return
    if i0 < 0:
        i0 = 0
    if j0 < 0:
        j0 = 0
    if i1 >= spec.nx:
        i1 = spec.nx - 1
    if j1 >= spec.ny:
        j1 = spec.ny - 1

    rr2 = rr * rr
    dx, dy = bx - ax, by - ay
    span2 = dx * dx + dy * dy
    nx = spec.nx
    x_start = spec.x0 + (i0 + 0.5) * px
    if span2 <= 1e-18:  # a disc
        for j in range(j0, j1 + 1):
            ddy = spec.y0 + (j + 0.5) * px - ay
            room = rr2 - ddy * ddy
            if room < 0.0:
                continue
            base = j * nx
            x = x_start
            for i in range(i0, i1 + 1):
                ddx = x - ax
                if ddx * ddx <= room:
                    idx = base + i
                    cur = grid[idx]
                    if cur == FREE:
                        grid[idx] = value
                    elif cur != value:
                        grid[idx] = BLOCKED
                x += px
        return
    inv = 1.0 / span2
    for j in range(j0, j1 + 1):
        y = spec.y0 + (j + 0.5) * px
        base = j * nx
        x = x_start
        for i in range(i0, i1 + 1):
            t = ((x - ax) * dx + (y - ay) * dy) * inv
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            ex = x - (ax + t * dx)
            ey = y - (ay + t * dy)
            if ex * ex + ey * ey <= rr2:
                idx = base + i
                cur = grid[idx]
                if cur == FREE:
                    grid[idx] = value
                elif cur != value:
                    grid[idx] = BLOCKED
            x += px


def _stamp_shape(
    grid: list, spec: GridSpec, cap, planes, extra: float, value: int
) -> None:
    """:func:`_stamp` for a shape that is not a stadium — a rectangular pad, a
    keepout, a polygon pad.

    The cell test is ``max`` over the core's outward edge lines rather than a
    distance to a spine. Inside the core every line is negative, so the pad's
    interior is covered; outside, the value is the true distance except in the
    wedge past a corner, where it under-reads and therefore paints a few cells
    more than strictly necessary. Under this model the router is slightly
    *cautious* at a pad corner. Under the stadium it was 0.21mm optimistic, and
    that is the defect this replaces.
    """
    reach = cap.sweep + extra
    x0, y0, x1, y1 = capsule_bbox(cap)
    px = spec.pitch
    i0 = max(0, int((x0 - extra - spec.x0) / px))
    i1 = min(spec.nx - 1, int((x1 + extra - spec.x0) / px) + 1)
    j0 = max(0, int((y0 - extra - spec.y0) / px))
    j1 = min(spec.ny - 1, int((y1 + extra - spec.y0) / px) + 1)
    if i1 < i0 or j1 < j0:
        return
    nx = spec.nx
    for j in range(j0, j1 + 1):
        y = spec.y0 + (j + 0.5) * px
        base = j * nx
        x = spec.x0 + (i0 + 0.5) * px
        for i in range(i0, i1 + 1):
            worst = -1e18
            for pnx, pny, off in planes:
                d = pnx * x + pny * y - off
                if d > worst:
                    worst = d
            if worst <= reach:
                idx = base + i
                cur = grid[idx]
                if cur == FREE:
                    grid[idx] = value
                elif cur != value:
                    grid[idx] = BLOCKED
            x += px


def polygon_spans(
    poly: Sequence[Point], spec: GridSpec
) -> list[list[tuple[int, int]]]:
    """Per grid row, the ``[i_lo, i_hi]`` runs of cell centres inside ``poly``.

    Scanline, bucketed by row. circuit.json tessellates a rounded rectangle into
    a couple of thousand points; testing every cell against every edge would be
    half a billion comparisons for a picture we can get with one pass.
    """
    px = spec.pitch
    edges = [
        (
            poly[i].x, poly[i].y,
            poly[(i + 1) % len(poly)].x, poly[(i + 1) % len(poly)].y,
        )
        for i in range(len(poly))
    ]
    buckets: dict[int, list[int]] = {}
    for index, (_, ey0, _, ey1) in enumerate(edges):
        lo = int((min(ey0, ey1) - spec.y0) / px)
        hi = int((max(ey0, ey1) - spec.y0) / px)
        for j in range(max(0, lo - 1), min(spec.ny - 1, hi + 1) + 1):
            buckets.setdefault(j, []).append(index)

    rows: list[list[tuple[int, int]]] = []
    for j in range(spec.ny):
        y = spec.y0 + (j + 0.5) * px
        crossings: list[float] = []
        for index in buckets.get(j, ()):
            ex0, ey0, ex1, ey1 = edges[index]
            if (ey0 > y) != (ey1 > y):
                crossings.append((ex1 - ex0) * (y - ey0) / (ey1 - ey0) + ex0)
        crossings.sort()
        spans: list[tuple[int, int]] = []
        for k in range(0, len(crossings) - 1, 2):
            i_lo = max(0, int(math.ceil((crossings[k] - spec.x0) / px - 0.5)))
            i_hi = min(spec.nx - 1, int(math.floor((crossings[k + 1] - spec.x0) / px - 0.5)))
            if i_lo <= i_hi:
                spans.append((i_lo, i_hi))
        rows.append(spans)
    return rows


def board_spans(problem: RoutingProblem, spec: GridSpec) -> list[list[tuple[int, int]]]:
    """The rows of the board itself: the outline polygon when there is one, the
    bounding box otherwise — which is also what the pipeline's own DFM gate
    assumes for a board with no outline."""
    outline = problem.board.outline
    if len(outline) >= 3:
        return polygon_spans(outline, spec)
    x0, y0, x1, y1 = problem.board.bbox
    px = spec.pitch
    i_lo = max(0, int(math.ceil((x0 - spec.x0) / px - 0.5)))
    i_hi = min(spec.nx - 1, int(math.floor((x1 - spec.x0) / px - 0.5)))
    rows: list[list[tuple[int, int]]] = []
    for j in range(spec.ny):
        y = spec.y0 + (j + 0.5) * px
        rows.append([(i_lo, i_hi)] if y0 <= y <= y1 and i_lo <= i_hi else [])
    return rows


def _blank_grid(spec: GridSpec, spans: Sequence[Sequence[tuple[int, int]]]) -> list:
    """A grid where everything off the board is already blocked."""
    grid = [BLOCKED] * spec.ncells
    nx = spec.nx
    for j, row in enumerate(spans):
        base = j * nx
        for i_lo, i_hi in row:
            grid[base + i_lo : base + i_hi + 1] = [FREE] * (i_hi - i_lo + 1)
    return grid


class GridBoard:
    """Every occupancy grid this problem needs, built once and kept current.

    ``occ[(layer, width_key)]`` is the trace grid for one width on one layer.
    ``via_occ`` is separate because a via is legal on different terms: it is
    judged as 0.6mm of copper on *both* layers at once, it owes hole-to-hole to
    every drill on the board including its own net's — two barrels too close
    break into each other whatever they carry — and it may never be drilled
    inside an SMD pad, its own net's included.
    """

    def __init__(
        self,
        problem: RoutingProblem,
        *,
        clearance: float,
        widths: Sequence[float],
        pitch: float | None = None,
    ) -> None:
        self.problem = problem
        self.rules = problem.rules
        self.clearance = clearance
        self.pitch = pitch or choose_pitch(problem)
        self.spec = make_spec(problem, self.pitch)
        self.widths = tuple(sorted({round(w, 4) for w in widths}))
        self.net_index: dict[str, int] = {
            net.id: i + 1
            for i, net in enumerate(sorted(problem.nets, key=lambda n: n.id))
        }
        self.spans = board_spans(problem, self.spec)
        self.occ: dict[tuple[str, int], list] = {}
        self.via_occ: list = []
        self._build()

    # -- construction ----------------------------------------------------

    def _rim_capsules(self) -> list[Capsule]:
        outline = self.problem.board.outline
        if len(outline) >= 3:
            return [
                segment_capsule(
                    outline[i].x, outline[i].y,
                    outline[(i + 1) % len(outline)].x,
                    outline[(i + 1) % len(outline)].y,
                    0.0,
                )
                for i in range(len(outline))
            ]
        x0, y0, x1, y1 = self.problem.board.bbox
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return [
            segment_capsule(*corners[i], *corners[(i + 1) % 4], 0.0) for i in range(4)
        ]

    def _build(self) -> None:
        spec = self.spec
        problem = self.problem
        rules = self.rules
        layers = (TOP, BOTTOM)

        # The board edge is the same on every layer, so it is rasterised once
        # per required radius and then copied. On a 2205-point outline that is
        # the difference between one second and seven.
        rim = self._rim_capsules()
        rim_cells: dict[int, list[int]] = {}
        radii = {
            _width_key(width): rules.min_edge_clearance_mm + width / 2.0
            for width in self.widths
        }
        radii[_width_key(rules.via_pad_mm)] = (
            rules.min_edge_clearance_mm + rules.via_pad_mm / 2.0
        )
        for key, radius in sorted(radii.items()):
            scratch = _blank_grid(spec, self.spans)
            for cap in rim:
                _stamp(scratch, spec, cap, radius, BLOCKED)
            rim_cells[key] = [
                index for index, value in enumerate(scratch) if value == BLOCKED
            ]

        for layer in layers:
            for width in self.widths:
                key = _width_key(width)
                grid = [FREE] * spec.ncells
                for index in rim_cells[key]:
                    grid[index] = BLOCKED
                self.occ[(layer, key)] = grid
        self.via_occ = [FREE] * spec.ncells
        for index in rim_cells[_width_key(rules.via_pad_mm)]:
            self.via_occ[index] = BLOCKED

        # Keepouts: copper may not overlap one at all — no clearance term, the
        # zone is the zone.
        for keepout in problem.keepouts:
            cap = keepout_capsule(keepout)
            for layer in keepout.layers:
                for width in self.widths:
                    key = (layer, _width_key(width))
                    if key in self.occ:
                        _stamp(self.occ[key], spec, cap, width / 2.0, BLOCKED)
            _stamp(self.via_occ, spec, cap, rules.via_pad_mm / 2.0, BLOCKED)

        # Drills. Three copper-to-hole numbers, asked per drill, never averaged.
        # A plated hole's *own* net may reach it: that is the pipeline's settled
        # rule, and it is the only way a leg ever gets connected.
        for drill in problem.drills:
            cap = drill_capsule(drill)
            needed = rules.hole_clearance(drill)
            owner = BLOCKED
            if drill.plated and drill.net:
                owner = self.net_index.get(drill.net, BLOCKED)
            for layer in layers:
                for width in self.widths:
                    _stamp(
                        self.occ[(layer, _width_key(width))],
                        spec, cap, needed + width / 2.0, owner,
                    )
            _stamp(self.via_occ, spec, cap, needed + rules.via_pad_mm / 2.0, owner)
            _stamp(
                self.via_occ, spec, cap,
                rules.min_hole_to_hole_mm + rules.via_drill_mm / 2.0, BLOCKED,
            )

        for pad in problem.pads:
            cap = pad_capsule(pad)
            owner = self.net_index.get(pad.net, BLOCKED) if pad.net else BLOCKED
            for layer in pad.layers:
                for width in self.widths:
                    key = (layer, _width_key(width))
                    if key in self.occ:
                        _stamp(
                            self.occ[key], spec, cap,
                            self.clearance + width / 2.0, owner,
                        )
            _stamp(
                self.via_occ, spec, cap,
                self.clearance + rules.via_pad_mm / 2.0, owner,
            )
            if pad.is_smd and not rules.allow_via_in_pad:
                _stamp(self.via_occ, spec, cap, rules.via_drill_mm / 2.0, BLOCKED)

        for trace in problem.existing_traces:
            self.add_trace(trace)
        for via in problem.existing_vias:
            self.add_via(via)

    # -- mutation --------------------------------------------------------

    def add_trace(self, trace: Trace) -> None:
        owner = self.net_index.get(trace.net, BLOCKED)
        for a, b in trace.segments:
            cap = segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm)
            for width in self.widths:
                _stamp(
                    self.occ[(trace.layer, _width_key(width))],
                    self.spec, cap, self.clearance + width / 2.0, owner,
                )
            _stamp(
                self.via_occ, self.spec, cap,
                self.clearance + self.rules.via_pad_mm / 2.0, owner,
            )

    def add_via(self, via: Via) -> None:
        owner = self.net_index.get(via.net, BLOCKED)
        cap = disc_capsule(via.center.x, via.center.y, via.pad_mm)
        for layer in (TOP, BOTTOM):
            for width in self.widths:
                _stamp(
                    self.occ[(layer, _width_key(width))],
                    self.spec, cap, self.clearance + width / 2.0, owner,
                )
        _stamp(
            self.via_occ, self.spec, cap,
            self.clearance + self.rules.via_pad_mm / 2.0, owner,
        )
        _stamp(
            self.via_occ, self.spec,
            disc_capsule(via.center.x, via.center.y, via.drill_mm),
            self.rules.min_hole_to_hole_mm + self.rules.via_drill_mm / 2.0, BLOCKED,
        )

    def block_cell(self, layer: str, cell: int) -> None:
        """Mark one cell unusable on every width grid of one layer.

        Called when the Workspace refuses a path the grid thought was fine, so
        the grid learns and the retry is a strictly harder search rather than
        the same one again.
        """
        for width in self.widths:
            self.occ[(layer, _width_key(width))][cell] = BLOCKED

    def block_via_cell(self, cell: int) -> None:
        self.via_occ[cell] = BLOCKED

    def plane_mask(self, plane: Plane) -> bytearray:
        """One byte per cell: is this cell inside the pour?"""
        mask = bytearray(self.spec.ncells)
        nx = self.spec.nx
        for j, row in enumerate(polygon_spans(plane.outline, self.spec)):
            base = j * nx
            for i_lo, i_hi in row:
                mask[base + i_lo : base + i_hi + 1] = b"\x01" * (i_hi - i_lo + 1)
        for ring in plane.holes:
            for j, row in enumerate(polygon_spans(ring, self.spec)):
                base = j * nx
                for i_lo, i_hi in row:
                    mask[base + i_lo : base + i_hi + 1] = b"\x00" * (i_hi - i_lo + 1)
        return mask


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """A path as ``(layer_index, cell)`` steps, or why there was not one."""

    path: list[tuple[int, int]] = field(default_factory=list)
    nodes: int = 0
    reason: str = ""

    def __bool__(self) -> bool:
        return bool(self.path)


class Maze:
    """A* over ``(layer, cell)``. The only geometry it knows is the owner grid."""

    def __init__(self, board: GridBoard) -> None:
        self.board = board
        self.spec = board.spec
        nx = self.spec.nx
        self.ncells = self.spec.ncells
        # 8-connected: the four axes then the four diagonals. A diagonal step
        # also needs both of its orthogonal neighbours, or the copper cuts a
        # corner the grid never looked at.
        self.offsets = (1, -1, nx, -nx, nx + 1, nx - 1, -nx + 1, -nx - 1)
        self.step_costs = (
            ORTH_COST, ORTH_COST, ORTH_COST, ORTH_COST,
            DIAG_COST, DIAG_COST, DIAG_COST, DIAG_COST,
        )
        self.corner_a = (0, 0, 0, 0, 1, -1, 1, -1)
        self.corner_b = (0, 0, 0, 0, nx, nx, -nx, -nx)
        self.via_cost = int(round(VIA_COST_MM / self.spec.pitch * ORTH_COST))

    def octile(self, cell_a: int, cell_b: int) -> int:
        nx = self.spec.nx
        ja, ia = divmod(cell_a, nx)
        jb, ib = divmod(cell_b, nx)
        dx = ia - ib if ia > ib else ib - ia
        dy = ja - jb if ja > jb else jb - ja
        if dx < dy:
            dx, dy = dy, dx
        return ORTH_COST * (dx - dy) + DIAG_COST * dy

    def search(
        self,
        *,
        sources: Sequence[tuple[int, int]],
        goals: Container,
        width: float,
        net_index: int,
        target_cell: int | None = None,
        target_layers: tuple[int, ...] = (),
        slack: int = 0,
        node_cap: int = MAX_NODES_PER_CONNECTION,
        weight: int = 1,
        goal_mask: bytearray | None = None,
        goal_mask_layer: int = 1,
        bonus_cells: frozenset[int] | None = None,
    ) -> SearchResult:
        """Multi-source A*; the goal is membership in ``goals`` (node ids) or,
        for a poured plane, a set bit in ``goal_mask`` on ``goal_mask_layer``.

        ``target_cell`` drives the heuristic. When it is ``None`` — routing a
        pad into a pour, where the goal is everywhere — all that is left to say
        is that a pad on the far layer owes at least one via, and the search
        becomes a Dijkstra disc around the pad. Correct, and cheap, because a
        pour is never far.

        ``weight`` multiplies the heuristic. At 1 the path is optimal for the
        cost function. Above 1 the search is greedier: it stops fanning out and
        drives at the target, which is how a congested connection gets found at
        all instead of exhausting its node cap proving nothing. The path can
        then be longer than optimal, and that is the right trade — copper is the
        third tier of the score and an unconnected net is the first.
        """
        ncells = self.ncells
        grids = (
            self.board.occ[(TOP, _width_key(width))],
            self.board.occ[(BOTTOM, _width_key(width))],
        )
        via_grid = self.board.via_occ
        offsets = self.offsets
        step_costs = self.step_costs
        corner_a = self.corner_a
        corner_b = self.corner_b
        via_cost = self.via_cost
        octile = self.octile
        scale = COUPLING_BONUS if bonus_cells else 1.0

        def h_of(layer: int, cell: int) -> int:
            if target_cell is None:
                # Nothing to aim at but the layer: a pad on the wrong side of
                # the board owes at least one via to reach a pour on the other.
                return (
                    via_cost * weight
                    if goal_mask is not None and layer != goal_mask_layer
                    else 0
                )
            value = octile(cell, target_cell) - slack
            if value < 0:
                value = 0
            if target_layers and layer not in target_layers:
                value += via_cost
            return int(value * scale) * weight

        dist: dict[int, int] = {}
        parent: dict[int, int] = {}
        heap: list[tuple[int, int, int]] = []
        for layer, cell in sorted(set(sources)):
            node = layer * ncells + cell
            dist[node] = 0
            parent[node] = -1
            heapq.heappush(heap, (h_of(layer, cell), 0, node))

        closed: set[int] = set()
        expanded = 0
        found = -1
        while heap:
            _, _, node = heapq.heappop(heap)
            if node in closed:
                continue
            closed.add(node)
            if node >= ncells:
                layer, cell = 1, node - ncells
            else:
                layer, cell = 0, node
            if node in goals or (
                goal_mask is not None
                and layer == goal_mask_layer
                and goal_mask[cell]
            ):
                found = node
                break
            expanded += 1
            if expanded >= node_cap:
                return SearchResult(nodes=expanded, reason="node cap")
            g = dist[node]
            grid = grids[layer]
            for k in range(8):
                nb = cell + offsets[k]
                if nb < 0 or nb >= ncells:
                    continue
                owner = grid[nb]
                if owner != FREE and owner != net_index:
                    continue
                if k >= 4:
                    corner = grid[cell + corner_a[k]]
                    if corner != FREE and corner != net_index:
                        continue
                    corner = grid[cell + corner_b[k]]
                    if corner != FREE and corner != net_index:
                        continue
                cost = step_costs[k]
                if bonus_cells is not None and nb in bonus_cells:
                    cost = int(cost * COUPLING_BONUS)
                ng = g + cost
                nnode = layer * ncells + nb
                seen = dist.get(nnode)
                if seen is not None and seen <= ng:
                    continue
                dist[nnode] = ng
                parent[nnode] = node
                heapq.heappush(heap, (ng + h_of(layer, nb), ng, nnode))
            # The layer change: judged against the via grid, not the trace grid.
            owner = via_grid[cell]
            if owner == FREE or owner == net_index:
                other = 1 - layer
                other_owner = grids[other][cell]
                if other_owner == FREE or other_owner == net_index:
                    ng = g + via_cost
                    nnode = other * ncells + cell
                    seen = dist.get(nnode)
                    if seen is None or seen > ng:
                        dist[nnode] = ng
                        parent[nnode] = node
                        heapq.heappush(heap, (ng + h_of(other, cell), ng, nnode))

        if found < 0:
            return SearchResult(nodes=expanded, reason="no path")
        path: list[tuple[int, int]] = []
        node = found
        while node != -1:
            if node >= ncells:
                path.append((1, node - ncells))
            else:
                path.append((0, node))
            node = parent[node]
        path.reverse()
        return SearchResult(path=path, nodes=expanded)


# ---------------------------------------------------------------------------
# Net ordering
# ---------------------------------------------------------------------------


def mst_edges(pads: Sequence[Pad]) -> list[tuple[Pad, Pad]]:
    """Prim over pad centres, ties broken by pad id so the tree never moves."""
    if len(pads) < 2:
        return []
    inside = [pads[0]]
    outside = list(pads[1:])
    edges: list[tuple[Pad, Pad]] = []
    while outside:
        best: tuple[float, str, str] | None = None
        best_pair: tuple[Pad, Pad] | None = None
        for a in inside:
            for b in outside:
                key = (a.center.distance_to(b.center), a.id, b.id)
                if best is None or key < best:
                    best, best_pair = key, (a, b)
        assert best_pair is not None
        edges.append(best_pair)
        inside.append(best_pair[1])
        outside.remove(best_pair[1])
    return edges


def _mst_length(pads: Sequence[Pad]) -> float:
    return sum(a.center.distance_to(b.center) for a, b in mst_edges(pads))


@dataclass(frozen=True)
class PadAccess:
    """How a net reaches one of its pads.

    ``ports`` are the grid nodes a trace may touch down on. ``anchor`` is
    ``None`` in the good case — the ports sit inside the pad's own copper, so
    the polyline needs no extra point to make the join. It is the pad centre
    only when the pad is smaller than the grid, and then the stub to it is
    copper like any other and is checked like any other.
    """

    ports: tuple[tuple[int, int], ...]
    anchor: Point | None


@dataclass(frozen=True)
class NetStat:
    net: Net
    pads: tuple[Pad, ...]
    mst_mm: float
    bbox_mm2: float
    demand: float


def net_stats(problem: RoutingProblem) -> list[NetStat]:
    out: list[NetStat] = []
    for net in problem.routable_nets:
        pads = tuple(
            sorted(problem.pads_of(net.id), key=lambda p: (p.center.x, p.center.y, p.id))
        )
        if len(pads) < 2:
            continue
        xs = [p.center.x for p in pads]
        ys = [p.center.y for p in pads]
        area = max(1.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))
        mst = _mst_length(pads)
        out.append(
            NetStat(net=net, pads=pads, mst_mm=mst, bbox_mm2=area, demand=mst / area)
        )
    return out


def order_nets(stats: Sequence[NetStat], ordering: str) -> list[NetStat]:
    """The three orderings. Each is a total order — net id is always the last
    key — so an ordering is a property of the problem and not of dict order."""
    if ordering == "shortest-first":
        return sorted(stats, key=lambda s: (round(s.mst_mm, 6), s.net.id))
    if ordering == "longest-first":
        return sorted(stats, key=lambda s: (-round(s.mst_mm, 6), s.net.id))
    if ordering == "most-constrained-first":
        # Copper demand per square millimetre of the net's own territory: how
        # much has to fit into how little room. Measured, not guessed.
        return sorted(
            stats, key=lambda s: (-round(s.demand, 9), -len(s.pads), s.net.id)
        )
    raise ValueError(f"unknown ordering {ordering!r} (have {', '.join(ORDERINGS)})")


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


class MazeAStarRouter:
    """Grid maze routing with A*, a per-width owner grid, and a Workspace gate."""

    name = "maze-astar"

    def __init__(
        self,
        *,
        ordering: str = "most-constrained-first",
        clearance: float | None = None,
        verify_clearance: float | None = None,
        rip_up_passes: int = 0,
        pitch: float | None = None,
        grid_margin_cells: float = GRID_MARGIN_CELLS,
        neck_width_mm: float | None = None,
        name: str | None = None,
    ) -> None:
        if ordering not in ORDERINGS:
            raise ValueError(f"unknown ordering {ordering!r}")
        self.ordering = ordering
        #: What the router *designs* to. ``None`` means
        #: ``rules.target_clearance_mm`` — the contract's number, deliberately
        #: above the fab floor because a router that aims at the floor lands
        #: under it.
        self.clearance = clearance
        #: What the Workspace *accepts*. ``None`` means the same as the design
        #: clearance. Setting it lower says "design to 0.147, and rather than
        #: drop a net, accept a run that only made 0.12" — still above the
        #: 0.10mm fab floor, so still zero findings, and the write-up reports
        #: how much copper landed in that band.
        self.verify_clearance = verify_clearance
        self.rip_up_passes = rip_up_passes
        self.pitch = pitch
        self.grid_margin_cells = grid_margin_cells
        #: Fallback width when a net will not fit at its class width. ``None``
        #: means the profile's ``warn_trace_mm``; 0 or a value at or above the
        #: class width turns necking off.
        self.neck_width_mm = neck_width_mm
        if name:
            self.name = name

    def resolved_neck(self, rules) -> float:
        """The neck width in millimetres, or 0 when necking is off.

        ``None`` means the profile's ``warn_trace_mm`` — 0.15mm, the narrowest
        trace that is neither a fab error (below 0.10mm) nor a DFM warning on
        its own. Clamped to the fab minimum, so a caller cannot ask for copper
        the fab will not etch.
        """
        if self.neck_width_mm is None:
            return float(rules.warn_trace_mm)
        if self.neck_width_mm <= 0:
            return 0.0
        return float(max(self.neck_width_mm, rules.min_trace_mm))

    # -- the interface ---------------------------------------------------

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        import time

        started = time.perf_counter()
        meter = budget.meter()
        design = (
            problem.rules.target_clearance_mm
            if self.clearance is None
            else self.clearance
        )
        verify = design if self.verify_clearance is None else self.verify_clearance
        neck = self.resolved_neck(problem.rules)
        pitch = self.pitch or choose_pitch(problem)
        grid_clearance = design + self.grid_margin_cells * pitch
        widths = sorted(
            {
                round(max(n.min_width_mm, problem.rules.min_trace_mm), 4)
                for n in problem.routable_nets
            }
            | {round(problem.rules.signal_trace_mm, 4)}
            | ({round(neck, 4)} if neck else set())
        )
        state = _State(
            problem=problem,
            board=GridBoard(
                problem, clearance=grid_clearance, widths=widths, pitch=pitch
            ),
            meter=meter,
            grid_clearance=grid_clearance,
            verify_clearance=verify,
        )
        stats = order_nets(net_stats(problem), self.ordering)

        # ``Budget.max_rip_up_passes`` defaults to 0 and the CLI never sets it,
        # so 0 is read as "unspecified" and the router's own setting applies; a
        # non-zero budget is a hard cap. Reported in the write-up as an
        # interface ambiguity rather than resolved quietly.
        passes = (
            self.rip_up_passes
            if budget.max_rip_up_passes <= 0
            else min(self.rip_up_passes, budget.max_rip_up_passes)
        )

        for stat in stats:
            if meter.exhausted:
                break
            self._route_net(state, stat)
        for _ in range(passes):
            if meter.exhausted or not state.unrouted:
                break
            if not self._rip_up_pass(state, stats):
                break

        notes: list[str] = [
            f"grid {state.board.spec.nx}x{state.board.spec.ny} @ "
            f"{state.board.spec.pitch:g}mm, designed to {design:g}mm clearance "
            f"(grid stamps at {grid_clearance:g}mm, accepted at {verify:g}mm), "
            f"ordering {self.ordering}, rip-up passes {passes}"
        ]
        if meter.stop_reason == "wall_clock":
            notes.insert(
                0,
                "hit the wall-clock safety valve — this run is not a comparable "
                "result, only evidence that the router hung",
            )
        elif meter.stop_reason:
            notes.insert(0, f"budget exhausted ({meter.stop_reason})")
        if state.workspace_rejections:
            why = ", ".join(
                f"{count}x {reason}"
                for reason, count in sorted(
                    state.refusal_reasons.items(), key=lambda kv: (-kv[1], kv[0])
                )
            )
            notes.append(
                f"{state.workspace_rejections} path(s) the grid allowed and the "
                f"Workspace refused — {why}; re-searched with those cells blocked"
            )
        if state.pull_rejections:
            notes.append(
                f"{state.pull_rejections} path(s) kept their staircase because "
                "the straightened version did not survive the Workspace"
            )
        if state.dropped_connections:
            why = ", ".join(
                f"{count}x {reason}"
                for reason, count in sorted(
                    state.drop_reasons.items(), key=lambda kv: (-kv[1], kv[0])
                )
            )
            notes.append(
                f"{state.dropped_connections} connection(s) dropped rather than "
                f"commit copper we cannot defend — {why}"
            )

        return RoutingSolution(
            router=self.name,
            traces=tuple(state.traces),
            vias=tuple(state.vias),
            complete=not state.unrouted,
            unrouted_nets=tuple(sorted(state.unrouted)),
            iterations=meter.iterations,
            nodes_expanded=meter.nodes,
            wall_clock_s=time.perf_counter() - started,
            notes=tuple(notes),
        )

    # -- one net ---------------------------------------------------------

    def _widths_for(self, state: "_State", net: Net) -> list[float]:
        """The widths to try for one net, best first.

        A net is routed at its class width. When that fails, a **neck** is
        tried: ``warn_trace_mm`` (0.15mm), the narrowest width that is neither a
        fab error nor a DFM warning on its own. Necking a rail at a 0.4mm-pitch
        pad is what an EE does with a mouse, and the trade is explicit: on a
        power or ground net it buys the connection at the price of one
        ``dfm_power_trace_width`` warning, and a connected net at a warning
        beats an unconnected net at none — completeness is the first tier of the
        score because an open circuit is a dead board.
        """
        width = round(max(net.min_width_mm, state.problem.rules.min_trace_mm), 4)
        neck = self.resolved_neck(state.problem.rules)
        if not neck or neck >= width - 1e-9:
            return [width]
        return [width, round(neck, 4)]

    def _route_net(self, state: "_State", stat: NetStat) -> bool:
        problem = state.problem
        net = stat.net
        net_index = state.board.net_index[net.id]
        ncells = state.board.spec.ncells
        planes = [p for p in problem.planes if p.net == net.id]
        tree = state.trees.setdefault(net.id, {})
        widths = self._widths_for(state, net)
        ok = True

        if planes:
            # A plane is a net. Every cell of the pour is already in the tree,
            # so a pad's job is to find the cheapest way in — normally one
            # escape and one via, and never a trace to another pad.
            mask, mask_layer = state.plane_goal(planes)
            for pad in stat.pads:
                if state.meter.exhausted:
                    ok = False
                    break
                state.meter.tick()
                if state.pad_in_plane(pad, planes):
                    continue
                done = False
                for width in widths:
                    access = state.pad_access(pad, width, net_index, net.id)
                    if not access.ports:
                        continue
                    if self._connect(
                        state, net, width, net_index,
                        sources=access.ports,
                        goals=tree,
                        goal_mask=mask,
                        goal_mask_layer=mask_layer,
                        source_anchor=access.anchor,
                        target_pad=None,
                        target_anchor=None,
                    ):
                        done = True
                        break
                if not done:
                    ok = False
            state.unrouted.discard(net.id)
            if not ok:
                state.unrouted.add(net.id)
            return ok

        pads = stat.pads
        connected: set[str] = {pads[0].id}
        for width in widths:
            first = state.pad_access(pads[0], width, net_index, net.id)
            for layer_index, cell in first.ports:
                tree.setdefault(
                    layer_index * ncells + cell,
                    first.anchor or Point(*state.board.spec.centre(cell)),
                )
        for pad_a, pad_b in mst_edges(list(pads)):
            if state.meter.exhausted:
                ok = False
                break
            state.meter.tick()
            target = pad_b if pad_b.id not in connected else pad_a
            if target.id in connected:
                continue
            done = False
            for width in widths:
                access = state.pad_access(target, width, net_index, net.id)
                if not access.ports:
                    continue
                sources = state.tree_sources(net.id, width, net_index)
                if not sources:
                    continue
                if self._connect(
                    state, net, width, net_index,
                    sources=sources,
                    goals=frozenset(
                        layer_index * ncells + cell
                        for layer_index, cell in access.ports
                    ),
                    source_anchor=None,
                    target_pad=target,
                    target_anchor=access.anchor,
                ):
                    done = True
                    break
            if done:
                connected.add(target.id)
            else:
                ok = False
        state.unrouted.discard(net.id)
        if not ok:
            state.unrouted.add(net.id)
        return ok

    # -- one connection --------------------------------------------------

    def _connect(
        self,
        state: "_State",
        net: Net,
        width: float,
        net_index: int,
        *,
        sources,
        goals: Container,
        source_anchor: Point | None,
        target_pad: Pad | None,
        target_anchor: Point | None = None,
        goal_mask: bytearray | None = None,
        goal_mask_layer: int = 1,
    ) -> bool:
        """Search, put the answer to the Workspace, then commit or drop.

        The grid is an approximation and the Workspace is the truth. On a
        refusal the offending cell is blocked and the search runs again, up to
        :data:`MAX_WORKSPACE_RETRIES`; after that the connection is dropped.
        Copper we cannot defend is never emitted.
        """
        board = state.board
        ncells = board.spec.ncells
        source_list = [
            (node // ncells, node % ncells) if isinstance(node, int) else node
            for node in sources
        ]
        if not source_list:
            state.drop(net, "no legal grid cell on a pad of this net")
            return False

        target_cell = None
        target_layers: tuple[int, ...] = ()
        slack = 0
        if target_pad is not None:
            target_cell = board.spec.cell_at(target_pad.center.x, target_pad.center.y)
            target_layers = tuple(0 if layer == TOP else 1 for layer in target_pad.layers)
            slack = int(
                max(target_pad.width_mm, target_pad.height_mm)
                / 2.0 / board.spec.pitch * ORTH_COST
            )

        bonus = state.coupling_cells(net)
        remaining = state.meter.budget.max_nodes - state.meter.nodes
        node_cap = max(1, min(MAX_NODES_PER_CONNECTION, remaining))
        weight = 1

        for _ in range(MAX_WORKSPACE_RETRIES + 1):
            result = state.maze.search(
                sources=source_list,
                goals=goals,
                width=width,
                net_index=net_index,
                target_cell=target_cell,
                target_layers=target_layers,
                slack=slack,
                node_cap=node_cap,
                weight=weight,
                goal_mask=goal_mask,
                goal_mask_layer=goal_mask_layer,
                bonus_cells=bonus,
            )
            if result.reason == "node cap" and weight < GREEDY_WEIGHT:
                # Optimal search ran out of nodes. Try again greedy: worse
                # copper, but a connection instead of a hole in the board.
                state.meter.expand(result.nodes)
                state.meter.tick()
                weight = GREEDY_WEIGHT
                continue
            state.meter.expand(result.nodes)
            if not result.path:
                state.drop(net, result.reason)
                return False
            anchor = (
                source_anchor
                if source_anchor is not None
                else state.anchor_of(net.id, result.path[0])
            )
            pulled, exact, vias = state.to_runs(
                result.path,
                source_anchor=anchor,
                target_point=target_anchor,
                width=width,
                net_index=net_index,
            )
            refusal = state.validate(pulled, vias, net, width)
            if refusal is None:
                state.commit(net, width, pulled, vias, target_pad)
                return True
            # The pull was a shortcut and the Workspace did not buy it. Fall
            # back to the copper the search actually proved before blaming the
            # grid: same path, collinear points merged, no geometry invented.
            if pulled != exact:
                state.pull_rejections += 1
                refusal = state.validate(exact, vias, net, width)
                if refusal is None:
                    state.commit(net, width, exact, vias, target_pad)
                    return True
            state.workspace_rejections += 1
            state.refusal_reasons[refusal[3]] = (
                state.refusal_reasons.get(refusal[3], 0) + 1
            )
            state.meter.tick()
            layer_index, cell, is_via = refusal[0], refusal[1], refusal[2]
            if is_via:
                board.block_via_cell(cell)
            else:
                board.block_cell(TOP if layer_index == 0 else BOTTOM, cell)
            source_list = [
                item for item in source_list
                if item != (layer_index, cell)
            ]
            if not source_list:
                break
        state.drop(net, "workspace refused every path the grid offered")
        return False

    # -- rip-up ----------------------------------------------------------

    def _rip_up_pass(self, state: "_State", stats: Sequence[NetStat]) -> bool:
        """Rip up what is in the way of the failed nets and route them first.

        The blockers are *measured*: the straight line each failed net wants is
        walked across the grid and every net that owns a cell under it is a
        blocker. Then the board is rebuilt without those nets, the failed nets
        go first, and the ripped ones are re-routed behind them. If the pass
        does not connect more nets than before it is rolled back — a rip-up that
        makes things worse is not an improvement worth keeping.
        """
        before = len(state.unrouted)
        failed = [s for s in stats if s.net.id in state.unrouted]
        if not failed:
            return False
        victims: set[str] = set()
        for stat in failed:
            victims |= state.blockers_of(stat)
            if len(victims) > 24:
                break
        victims -= {s.net.id for s in failed}
        if not victims:
            return False

        snapshot = state.snapshot()
        state.rebuild(drop_nets=victims)
        for stat in failed:
            if state.meter.exhausted:
                break
            self._route_net(state, stat)
        for stat in stats:
            if stat.net.id not in victims:
                continue
            if state.meter.exhausted:
                break
            self._route_net(state, stat)
        if len(state.unrouted) >= before:
            state.restore(snapshot)
            return False
        return True


# ---------------------------------------------------------------------------
# Mutable routing state
# ---------------------------------------------------------------------------


class _State:
    """Everything that changes while a board is routed.

    Kept off the router object so two runs of the same instance cannot share a
    byte. That is one of the two ways a router silently stops being
    deterministic; the other is reading the clock.
    """

    def __init__(
        self,
        *,
        problem: RoutingProblem,
        board: GridBoard,
        meter: BudgetMeter,
        grid_clearance: float,
        verify_clearance: float,
    ) -> None:
        self.problem = problem
        self.board = board
        self.maze = Maze(board)
        self.ws = Workspace(problem, clearance=verify_clearance)
        self.meter = meter
        #: What the grid was stamped with — design clearance plus the sub-cell
        #: margin. Used anywhere a millimetre has to agree with a cell.
        self.clearance = grid_clearance
        #: What the Workspace holds copper to. Never above the grid's number.
        self.verify_clearance = verify_clearance
        self.traces: list[Trace] = []
        self.vias: list[Via] = []
        self.unrouted: set[str] = set()
        self.trees: dict[str, dict[int, Point]] = {}
        self.workspace_rejections = 0
        #: Times the string-pulled polyline was refused and the exact path was
        #: committed instead. Copper we could have shortened and did not.
        self.pull_rejections = 0
        self.dropped_connections = 0
        #: Why connections were dropped, counted by reason. Reported in the
        #: solution's notes: "13 connections failed" is a number, "13 failed
        #: because the Workspace refused every path the grid could find" is a
        #: diagnosis, and only the second one tells anybody what to fix.
        self.drop_reasons: dict[str, int] = {}
        #: Which Workspace check disagreed with the grid, counted by kind.
        self.refusal_reasons: dict[str, int] = {}
        self._plane_masks: dict[str, tuple[bytearray, int]] = {}
        self._coupling_cache: dict[str, frozenset[int] | None] = {}
        self._trace_seq = 0
        self._via_seq = 0
        self._simplify_grid: list | None = None
        self._simplify_owner = 0

    def drop(self, net: Net, reason: str) -> None:
        """Record a connection we refused to make, and why."""
        self.dropped_connections += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1

    # -- pads and planes -------------------------------------------------

    def pad_access(
        self, pad: Pad, width: float, net_index: int, net_id: str
    ) -> "PadAccess":
        """Where a trace for this pad's net may touch down, and whether it has
        to reach for the pad centre to do it.

        Cells whose centre lands **inside** the pad's own copper are the good
        case: a trace centred there overlaps the pad's stadium by at least half
        its own width, so the join is real and the polyline needs no stub. That
        matters more than it sounds. Running the polyline to the pad *centre*
        regardless — the obvious thing, and what the first version did — puts
        copper on a point the grid never judged, and on a 0.4mm-pitch part the
        pad centre is routinely inside a *neighbour's* clearance halo. Every one
        of the 30 clearance refusals measured on
        ``matrix-rp2040-core__usb-c-data`` was that stub.

        Only when the pad is too small for any cell centre to land inside it do
        we fall back to the nearest legal cells plus an explicit stub to the pad
        centre. That stub is copper on a point no grid cell stands for, so it is
        put to the Workspace **here**, and a port whose stub is refused is not
        offered. Measured before that check existed: 52 of 76 refusals on
        ``matrix-rp2040-core__usb-c-data`` were a stub to a pad centre sitting
        inside a neighbour's clearance halo.
        """
        spec = self.board.spec
        cap = pad_capsule(pad)
        # ``point_shape_distance`` is signed against the pad's real outline: at
        # most 0 inside it, positive outside. Read off the inscribed stadium
        # instead, a cell in the corner of a 1.0mm square pad measured 0.21mm
        # of air and was offered as a landing outside the copper.
        reach = width / 2.0 + self.clearance
        layers = [0 if layer == TOP else 1 for layer in pad.layers]
        grids = {
            index: self.board.occ[(TOP if index == 0 else BOTTOM, _width_key(width))]
            for index in layers
        }
        inside: list[tuple[float, int, int]] = []
        near: list[tuple[float, int, int]] = []
        bx0, by0, bx1, by1 = capsule_bbox(cap)
        i0 = max(0, int((bx0 - reach - spec.x0) / spec.pitch))
        i1 = min(spec.nx - 1, int((bx1 + reach - spec.x0) / spec.pitch) + 1)
        j0 = max(0, int((by0 - reach - spec.y0) / spec.pitch))
        j1 = min(spec.ny - 1, int((by1 + reach - spec.y0) / spec.pitch) + 1)
        for j in range(j0, j1 + 1):
            y = spec.y0 + (j + 0.5) * spec.pitch
            base = j * spec.nx
            for i in range(i0, i1 + 1):
                x = spec.x0 + (i + 0.5) * spec.pitch
                d = point_shape_distance(x, y, cap)
                if d > reach:
                    continue
                cell = base + i
                for index in layers:
                    owner = grids[index][cell]
                    if owner != FREE and owner != net_index:
                        continue
                    (inside if d <= 0.0 else near).append(
                        (round(d, 6), index, cell)
                    )
        if inside:
            return PadAccess(
                ports=tuple((i, c) for _, i, c in sorted(inside)[:24]), anchor=None
            )
        ports: list[tuple[int, int]] = []
        for _, index, cell in sorted(near)[:8]:
            x, y = spec.centre(cell)
            layer = TOP if index == 0 else BOTTOM
            if self.ws.segment_ok(layer, pad.center, Point(x, y), width, net_id) is True:
                ports.append((index, cell))
        return PadAccess(ports=tuple(ports), anchor=pad.center if ports else None)

    def pad_in_plane(self, pad: Pad, planes: Sequence[Plane]) -> bool:
        """A pad already joined to the pour, needing no copper at all.

        A plated hole's barrel passes through the pour; an SMD pad sitting on
        the poured layer is inside it. Both are joins the connectivity check
        finds on its own, so routing them would be copper for nothing.
        """
        spec = self.board.spec
        for plane in planes:
            mask, _ = self._mask_of(plane)
            if not mask[spec.cell_at(pad.center.x, pad.center.y)]:
                continue
            if pad.kind == "plated_hole" or plane.layer in pad.layers:
                return True
        return False

    def _mask_of(self, plane: Plane) -> tuple[bytearray, int]:
        cached = self._plane_masks.get(plane.id)
        if cached is None:
            cached = (
                self.board.plane_mask(plane),
                0 if plane.layer == TOP else 1,
            )
            self._plane_masks[plane.id] = cached
        return cached

    def plane_goal(self, planes: Sequence[Plane]) -> tuple[bytearray, int]:
        """One mask covering every pour this net owns, and the layer it is on."""
        first, layer = self._mask_of(planes[0])
        if len(planes) == 1:
            return first, layer
        merged = bytearray(first)
        for plane in planes[1:]:
            mask, _ = self._mask_of(plane)
            for index, value in enumerate(mask):
                if value:
                    merged[index] = 1
        return merged, layer

    def coupling_cells(self, net: Net) -> frozenset[int] | None:
        """Cells beside an already-routed differential partner.

        Steps into them are discounted so the second half of a pair follows the
        first instead of taking its own shortest path. The heuristic is scaled
        by the same factor, so A* stays admissible.
        """
        if net.net_class != "diff_pair" or not net.diff_partner:
            return None
        if net.id in self._coupling_cache:
            return self._coupling_cache[net.id]
        window = self.problem.rules.diff_pair_gap_mm * 3.0
        spec = self.board.spec
        cells: set[int] = set()
        for trace in self.traces:
            if trace.net != net.diff_partner:
                continue
            reach = window + trace.width_mm / 2.0
            span = int(reach / spec.pitch) + 1
            for a, b in trace.segments:
                steps = max(1, int(a.distance_to(b) / spec.pitch) + 1)
                for s in range(steps + 1):
                    t = s / steps
                    ci = spec.cell_at(
                        a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t
                    )
                    cj, cii = divmod(ci, spec.nx)
                    for j in range(max(0, cj - span), min(spec.ny - 1, cj + span) + 1):
                        base = j * spec.nx
                        for i in range(
                            max(0, cii - span), min(spec.nx - 1, cii + span) + 1
                        ):
                            cells.add(base + i)
        out = frozenset(cells) or None
        self._coupling_cache[net.id] = out
        return out

    # -- path -> copper --------------------------------------------------

    def tree_sources(
        self, net_id: str, width: float, net_index: int
    ) -> list[tuple[int, int]]:
        """The cells of this net's committed copper a new branch may start on.

        A tree cell is a cell the copper *passes through*, and that is not the
        same as a cell a new trace may be *centred* on: our own track can run
        through a cell whose halo also belongs to somebody else, and the grid
        marks that cell blocked for everyone. Seeding the search there produced
        a first segment nothing had ever checked — 24 of 76 Workspace refusals
        on ``matrix-rp2040-core__usb-c-data``.
        """
        ncells = self.board.spec.ncells
        grids = (
            self.board.occ[(TOP, _width_key(width))],
            self.board.occ[(BOTTOM, _width_key(width))],
        )
        out: list[tuple[int, int]] = []
        for node in self.trees.get(net_id, ()):
            layer, cell = divmod(node, ncells)
            owner = grids[layer][cell]
            if owner == FREE or owner == net_index:
                out.append((layer, cell))
        return out

    def anchor_of(self, net_id: str, first: tuple[int, int]) -> Point:
        """Where a path that starts on existing copper actually starts.

        The tree records, for every cell it owns, the exact point on the
        committed polyline that put it there. Starting the new trace at that
        point rather than at the cell centre is what makes the join a real
        electrical join rather than a near miss the connectivity check calls an
        open circuit.
        """
        spec = self.board.spec
        point = (self.trees.get(net_id) or {}).get(first[0] * spec.ncells + first[1])
        if point is not None:
            return point
        x, y = spec.centre(first[1])
        return Point(x, y)

    def to_runs(
        self,
        path: Sequence[tuple[int, int]],
        *,
        source_anchor: Point,
        target_point: Point | None,
        width: float,
        net_index: int,
    ) -> tuple[
        list[tuple[str, list[Point]]],
        list[tuple[str, list[Point]]],
        list[tuple[int, Point]],
    ]:
        """A grid path becomes per-layer polylines, plus the vias between them.

        Two versions of the same copper come back, and the caller tries them in
        order:

        * **pulled** — string-pulled straight against the grid. Shorter, fewer
          corners, and only an approximation of legality because the grid is one.
        * **exact** — the A* path with collinear points merged. Merging collinear
          points changes no geometry at all, so this polyline *is* the path the
          search proved, and it is the honest fallback when the pull is refused.
        """
        spec = self.board.spec
        runs: list[tuple[str, list[Point]]] = []
        vias: list[tuple[int, Point]] = []
        current_layer = path[0][0]
        current: list[Point] = [source_anchor]
        for layer, cell in path:
            x, y = spec.centre(cell)
            point = Point(x, y)
            if layer != current_layer:
                vias.append((cell, point))
                if current[-1] != point:
                    current.append(point)
                runs.append((TOP if current_layer == 0 else BOTTOM, current))
                current = [point]
                current_layer = layer
                continue
            if current[-1] != point:
                current.append(point)
        if target_point is not None and current[-1] != target_point:
            current.append(target_point)
        runs.append((TOP if current_layer == 0 else BOTTOM, current))

        self._simplify_owner = net_index
        pulled: list[tuple[str, list[Point]]] = []
        exact: list[tuple[str, list[Point]]] = []
        for layer, points in runs:
            if len(points) < 2:
                continue
            straight = _merge_collinear(points)
            exact.append((layer, straight))
            self._simplify_grid = self.board.occ[(layer, _width_key(width))]
            pulled.append((layer, self._string_pull(straight)))
        return pulled, exact, vias

    def _string_pull(self, points: list[Point]) -> list[Point]:
        """Pull the staircase straight, using the grid as a cheap oracle.

        A grid path is a staircase and the straight line between two of its
        points is shorter and has fewer corners. The grid answers in
        microseconds where the Workspace takes tens of them; the Workspace then
        gets the final say on the whole polyline, and when it says no the caller
        falls back to the exact path rather than arguing.
        """
        if len(points) <= 2:
            return points
        out = [points[0]]
        i = 0
        n = len(points)
        while i < n - 1:
            best = i + 1
            j = i + 2
            while j < n and self._line_clear(points[i], points[j]):
                best = j
                j += 1
            out.append(points[best])
            i = best
        return out

    def _line_clear(self, a: Point, b: Point) -> bool:
        grid = self._simplify_grid
        if grid is None:
            return False
        spec = self.board.spec
        owner = self._simplify_owner
        steps = max(2, int(a.distance_to(b) / (spec.pitch * 0.5)) + 1)
        dx, dy = b.x - a.x, b.y - a.y
        for s in range(steps + 1):
            t = s / steps
            value = grid[spec.cell_at(a.x + dx * t, a.y + dy * t)]
            if value != FREE and value != owner:
                return False
        return True

    # -- validation and commit -------------------------------------------

    def validate(
        self,
        runs: Sequence[tuple[str, list[Point]]],
        vias: Sequence[tuple[int, Point]],
        net: Net,
        width: float,
    ) -> tuple[int, int, bool, str] | None:
        """``None`` when the Workspace accepts every piece of this connection.

        Otherwise ``(layer_index, cell, is_via, reason)`` naming the first thing
        it refused, so the grid can be told, the search repeated, and the reason
        counted. This is the gate that stops the router shipping copper it
        cannot defend: the Workspace measures with the geometry the scorer
        grades with.
        """
        spec = self.board.spec
        for cell, point in vias:
            verdict = self.ws.via_ok(point, net.id)
            if verdict is not True:
                return (0, cell, True, f"via/{getattr(verdict, 'reason', '?')}")
        for layer, points in runs:
            layer_index = 0 if layer == TOP else 1
            for a, b in zip(points, points[1:]):
                if a == b:
                    continue
                verdict = self.ws.segment_ok(layer, a, b, width, net.id)
                if verdict is not True:
                    return (
                        layer_index,
                        spec.cell_at((a.x + b.x) / 2.0, (a.y + b.y) / 2.0),
                        False,
                        f"trace/{getattr(verdict, 'reason', '?')}",
                    )
        return None

    def commit(
        self,
        net: Net,
        width: float,
        runs: Sequence[tuple[str, list[Point]]],
        vias: Sequence[tuple[int, Point]],
        target_pad: Pad | None,
    ) -> None:
        spec = self.board.spec
        tree = self.trees.setdefault(net.id, {})
        for layer, points in runs:
            trace = Trace(
                id=f"{net.id}~{self._trace_seq}",
                net=net.id,
                layer=layer,
                points=tuple(points),
                width_mm=width,
            )
            self._trace_seq += 1
            self.traces.append(trace)
            self.ws.commit_trace(trace)
            self.board.add_trace(trace)
            self._index_trace(tree, trace)
        for cell, point in vias:
            via = Via(
                id=f"v{self._via_seq}",
                net=net.id,
                center=point,
                drill_mm=self.problem.rules.via_drill_mm,
                pad_mm=self.problem.rules.via_pad_mm,
            )
            self._via_seq += 1
            self.vias.append(via)
            self.ws.commit_via(via)
            self.board.add_via(via)
            tree[cell] = point
            tree[spec.ncells + cell] = point
        if target_pad is not None:
            net_index = self.board.net_index[net.id]
            access = self.pad_access(target_pad, width, net_index, net.id)
            for layer_index, cell in access.ports:
                tree.setdefault(
                    layer_index * spec.ncells + cell,
                    access.anchor or Point(*spec.centre(cell)),
                )
        if net.diff_partner:
            self._coupling_cache.pop(net.diff_partner, None)

    def _index_trace(self, tree: dict[int, Point], trace: Trace) -> None:
        """Record which cells this trace owns, and where on it they touch.

        Sampling twice per cell means every cell the copper passes through gets
        an anchor, and the anchor is a point that is genuinely on the polyline —
        so the next branch of this net starts on metal, not near it.
        """
        spec = self.board.spec
        layer_index = 0 if trace.layer == TOP else 1
        offset = layer_index * spec.ncells
        for a, b in trace.segments:
            steps = max(1, int(a.distance_to(b) / (spec.pitch * 0.5)) + 1)
            dx, dy = b.x - a.x, b.y - a.y
            for s in range(steps + 1):
                t = s / steps
                x, y = a.x + dx * t, a.y + dy * t
                tree[offset + spec.cell_at(x, y)] = Point(x, y)

    # -- rip-up support --------------------------------------------------

    def blockers_of(self, stat: NetStat) -> set[str]:
        """Which nets' copper stands between this net's pads.

        Measured by walking the straight line each MST edge wants and reading
        back the owner of every cell under it. A guess at which neighbour is in
        the way would rip up the wrong net.
        """
        spec = self.board.spec
        width = max(stat.net.min_width_mm, self.problem.rules.min_trace_mm)
        grids = (
            self.board.occ[(TOP, _width_key(width))],
            self.board.occ[(BOTTOM, _width_key(width))],
        )
        mine = self.board.net_index.get(stat.net.id)
        owners: set[int] = set()
        for a, b in mst_edges(list(stat.pads)):
            steps = max(1, int(a.center.distance_to(b.center) / spec.pitch) + 1)
            dx = b.center.x - a.center.x
            dy = b.center.y - a.center.y
            for s in range(steps + 1):
                t = s / steps
                cell = spec.cell_at(a.center.x + dx * t, a.center.y + dy * t)
                for grid in grids:
                    value = grid[cell]
                    if value > 0 and value != mine:
                        owners.add(value)
        by_index = {index: net_id for net_id, index in self.board.net_index.items()}
        return {by_index[i] for i in owners if i in by_index}

    def snapshot(self) -> dict:
        return {
            "traces": list(self.traces),
            "vias": list(self.vias),
            "unrouted": set(self.unrouted),
            "trace_seq": self._trace_seq,
            "via_seq": self._via_seq,
        }

    def restore(self, snapshot: dict) -> None:
        self._reload(snapshot["traces"], snapshot["vias"])
        self.unrouted = set(snapshot["unrouted"])
        self._trace_seq = snapshot["trace_seq"]
        self._via_seq = snapshot["via_seq"]

    def rebuild(self, *, drop_nets: set[str]) -> None:
        self._reload(
            [t for t in self.traces if t.net not in drop_nets],
            [v for v in self.vias if v.net not in drop_nets],
        )
        self.unrouted |= drop_nets

    def _reload(self, traces: Sequence[Trace], vias: Sequence[Via]) -> None:
        """Throw the board away and lay the kept copper down again.

        Un-stamping is not possible: a cell two nets both reached is
        :data:`BLOCKED` and does not remember which two. A rebuild is a second
        and it is exactly right, which is the better trade at four rip-up passes.
        """
        self.board = GridBoard(
            self.problem,
            clearance=self.clearance,
            widths=self.board.widths,
            pitch=self.board.pitch,
        )
        self.maze = Maze(self.board)
        self.ws = Workspace(self.problem, clearance=self.verify_clearance)
        self.traces = []
        self.vias = []
        self.trees = {}
        self._plane_masks = {}
        self._coupling_cache = {}
        spec = self.board.spec
        for trace in traces:
            self.traces.append(trace)
            self.ws.commit_trace(trace)
            self.board.add_trace(trace)
            self._index_trace(self.trees.setdefault(trace.net, {}), trace)
        for via in vias:
            self.vias.append(via)
            self.ws.commit_via(via)
            self.board.add_via(via)
            tree = self.trees.setdefault(via.net, {})
            cell = spec.cell_at(via.center.x, via.center.y)
            tree[cell] = via.center
            tree[spec.ncells + cell] = via.center


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def maze_astar() -> MazeAStarRouter:
    return MazeAStarRouter(ordering="most-constrained-first")


def maze_astar_ripup() -> MazeAStarRouter:
    return MazeAStarRouter(
        ordering="most-constrained-first", rip_up_passes=4, name="maze-astar-ripup"
    )


#: The registry entry the tournament reads. Same shape as
#: ``routerlib.baseline.ROUTERS``, so wiring it into ``routerlib.cli.registry()``
#: is one line in a file this family does not own.
ROUTERS = {
    "maze-astar": maze_astar,
    "maze-astar-ripup": maze_astar_ripup,
}


__all__ = [
    "BLOCKED",
    "FREE",
    "GREEDY_WEIGHT",
    "GridBoard",
    "GridSpec",
    "MAX_CELLS",
    "Maze",
    "MazeAStarRouter",
    "NetStat",
    "PadAccess",
    "ORDERINGS",
    "PITCH_LADDER",
    "ROUTERS",
    "board_spans",
    "choose_pitch",
    "make_spec",
    "maze_astar",
    "maze_astar_ripup",
    "mst_edges",
    "net_stats",
    "order_nets",
    "polygon_spans",
]


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse
    import json

    from routerlib import bench, scoring

    parser = argparse.ArgumentParser(prog="maze-astar", description="grid maze A* router")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="route + score the suite")
    run.add_argument("--router", default="maze-astar", choices=sorted(ROUTERS))
    run.add_argument("--only", default=None)
    run.add_argument("--ordering", default=None, choices=list(ORDERINGS))
    run.add_argument("--clearance", type=float, default=None)
    run.add_argument("--max-nodes", type=int, default=20_000_000)
    run.add_argument("--max-iterations", type=int, default=2_000_000)
    run.add_argument("--report", default=None)
    run.add_argument("--no-determinism", action="store_true")

    orderings = sub.add_parser("orderings", help="every ordering on every instance")
    orderings.add_argument("--only", default=None)
    orderings.add_argument("--clearance", type=float, default=None)
    orderings.add_argument("--report", default=None)

    args = parser.parse_args(argv)
    problems = bench.load_all()
    if args.only:
        wanted = set(args.only.split(","))
        problems = [p for p in problems if p.id in wanted]

    if args.command == "orderings":
        rows = []
        for problem in problems:
            for ordering in ORDERINGS:
                router = MazeAStarRouter(
                    ordering=ordering,
                    clearance=args.clearance,
                    name=f"maze/{ordering}",
                )
                result = scoring.score(problem, router.route(problem, Budget()))
                print(result.line(), flush=True)
                rows.append(result.as_dict())
        if args.report:
            Path(args.report).write_text(
                json.dumps(rows, indent=1) + "\n", encoding="utf-8"
            )
        return 0

    base = ROUTERS[args.router]()
    ordering = args.ordering or base.ordering
    clearance = args.clearance
    passes = base.rip_up_passes
    label = base.name
    if args.ordering:
        label = f"{label}/{args.ordering}"
    if clearance is not None:
        label = f"{label}@{clearance:g}"

    def factory() -> MazeAStarRouter:
        return MazeAStarRouter(
            ordering=ordering,
            clearance=clearance,
            rip_up_passes=passes,
            name=label,
        )

    budget = Budget(max_iterations=args.max_iterations, max_nodes=args.max_nodes)
    report = bench.run_suite(
        factory, problems, budget,
        check_determinism=not args.no_determinism,
        on_done=lambda score, row: print(score.line(), flush=True),
    )
    print()
    print(report.summary())
    print(report.ruler_line)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report.as_dict(), indent=1) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
