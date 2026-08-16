"""Search the space of net orders, not the space of paths.

Every greedy router loses the same way: the net it routed third took the
channel the net it routes ninth needed, and by then the channel is gone. The
path was never the problem — the *order* was. So this family holds the inner
router fixed and searches over what the inner router is asked to do:

* **the order** nets are attempted in,
* **the layer** each net prefers,
* **the topology** each net is decomposed into (which pad connects to which).

Two metaheuristics, one genome, one fitness:

``meta-anneal``   simulated annealing over the genome, fixed geometric cooling
``meta-genetic``  a genetic algorithm, order crossover plus uniform crossover
``maze-greedy``   the same inner router, run **once**, default order

The third one is not decoration. It is the control, and without it a headline
like "the annealer routes 82% of terminal-keyboard" says nothing about
annealing — it says something about the maze router underneath. Every number
this module reports is reported against that control.

The inner router
----------------

Pattern first, maze second, and the maze is the interesting half.

1. A net with a poured plane on a layer its pads can see is stitched to the
   plane with one via per pad — no traces. Same as the baseline, for the same
   reason: 73 ground vias into a plane is 73 connections, not 73 obstacles.
2. Every other net becomes a tree over its pads (which tree is a gene), and
   each tree edge is routed on its own.
3. An edge is first tried against straight/L/Z patterns on the preferred layer
   and then the other. This is cheap and it succeeds on most edges of most
   boards, so it is worth trying before anything expensive.
4. If no pattern is legal, an **A\\* maze router** runs over a uniform grid
   with two layers, where a layer change is a via with a real cost. This is
   what finds the doglegs a pattern router cannot express, and it is why the
   completeness numbers move at all.
5. Nothing is committed without ``Workspace`` saying yes — the same geometry
   the scorer grades with. The grid is an accelerator, never an authority: it
   is deliberately built to *over*-block, and every path it proposes is
   re-measured against real geometry before it becomes copper. A path the grid
   likes and the workspace rejects is dropped, and the edge is reported
   unrouted.

The grid, and why it is allowed to exist
----------------------------------------

A* needs to answer "may this net occupy this cell" in constant time, a few
hundred thousand times per candidate. ``Workspace`` answers the same question
exactly but in milliseconds. So the obstacles are rasterised once per problem
into an integer field per layer:

``-1`` free · ``k`` reachable only by net ``k`` · ``-2`` reachable by nobody

A cell holds ``k`` when exactly one net's clearance halo covers it, and ``-2``
when two do — because copper there would violate somebody whatever net it
belongs to. Halos are grown by the *routed* net's own half-width, so the field
is built three times, once for signal width, once for rail width, and once for
a via pad. Static obstacles are cached per placement; the per-candidate cost is
a memcpy.

Determinism
-----------

Every random choice comes from ``random.Random(budget.seed)``. Ties in the A*
priority queue break on the node index, not on insertion order. The grid is
numpy, which is deterministic for these operations. Candidate populations are
sorted by the harness score key, which is a total order. Two runs of the same
router on the same problem with the same budget produce byte-identical copper,
and ``tests/test_metaheuristic.py`` asserts it.

Measured, 2026-08-16, all 16 instances, ruler ``b3c77d55b171``
--------------------------------------------------------------

===================  =====  ==========  ======  =====  ============
router               clean  completeness  errors  det   candidates
===================  =====  ==========  ======  =====  ============
``baseline-pattern``  2/16       57.3%        0  16/16  n/a
``maze-off-greedy``   1/16       26.9%        0  16/16  1
``maze-greedy``       3/16       71.1%        0  16/16  1
``meta-anneal``       4/16       84.0%        0  16/16  ≤32
``meta-genetic``      4/16       83.4%        0  16/16  8 × 4
===================  =====  ==========  ======  =====  ============

**Search buys 12.9 points of completeness over the same inner router run
once** — and the decomposition matters more than the headline:

============================================  ========  ====================
default net order, one candidate                 71.1%
best of six fixed heuristic orders               81.5%  +10.4 for 5 more runs
thirty-two-candidate anneal on top of those      84.0%  +2.5 for 26 more runs
============================================  ========  ====================

So **most of what "metaheuristic" buys here is six good orders, not
annealing.** Trying longest-net-first, shortest-first, most-pins-first and
three siblings costs five evaluations and moves ten points. The stochastic
search on top is real and reproducible but it is +2.5 points for five times
the compute. Anyone reaching for a metaheuristic on this benchmark should
spend the first five evaluations on the classic orders before spending the
next thousand on a temperature schedule.

What this family is not good at, said plainly
---------------------------------------------

Search over orderings is the right tool when the board is *congested* — when
nets genuinely compete for the same channel. **On 6 of the 16 instances it
bought exactly 0.0 points**, and those six split into two kinds:

* two are already at 100% after one run — every order wins, so there is
  nothing to search;
* four are stuck at 60–80% for a reason no ordering can fix. The dominant
  failure in the inner router is *"no legal grid cell to enter or leave a
  pad"* — 12% to 21% of maze attempts on the dense boards. A 0.4mm-pitch pad
  needs 0.494mm of channel to escape at 0.2mm width and target clearance, and
  no permutation creates space that the placement does not have. That needs
  a fanout stage or a narrower escape width, not a better order.

The honest reading: this family is an amplifier on the inner router, not a
router. It multiplies what the inner router can already do (+12.9 points) and
it cannot conjure a connection the inner router has no way to make.
"""

from __future__ import annotations

import heapq
import math
import random
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

try:  # pragma: no cover - only taken when run as a script
    import routerlib  # noqa: F401
except ImportError:  # pragma: no cover
    _ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_ROOT / "src"))
    sys.path.insert(0, str(_ROOT.parent / "circuitpy" / "src"))

from routerlib.geometry import (
    Capsule,
    GridIndex,
    PolygonIndex,
    capsule_bbox,
    capsule_gap,
    core_halfplanes,
    disc_capsule,
    drill_capsule,
    keepout_capsule,
    pad_capsule,
    segment_capsule,
    stadium,
)
from routerlib.model import (
    BOTTOM,
    TOP,
    Budget,
    BudgetMeter,
    Pad,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)
from routerlib.workspace import Workspace

LAYERS = (TOP, BOTTOM)

#: Cells per layer the rasteriser is allowed to allocate. The pitch is chosen
#: to fit under this, so a 10,000mm² board and a 200mm² board cost the same
#: memory and roughly the same search.
CELL_BUDGET = 130_000
_MIN_PITCH_MM = 0.20
_MAX_PITCH_MM = 0.45

#: A* costs, in integer units so two runs cannot disagree by a float ulp.
_STEP = 10
_OFF_LAYER_SURCHARGE = 1
_VIA_COST = 110

#: Expansions one edge may spend before it is declared unroutable, and the
#: ceiling for a whole candidate. Both are counted, never timed.
_EDGE_NODE_CAP = 16_000
_CANDIDATE_NODE_CAP = 600_000

#: How far ahead the polyline simplifier looks when it tries to replace a
#: staircase with one straight segment.
_LOS_WINDOW = 40

#: Entry cells around a pad: how many are measured against the workspace, and
#: how many legal ones the A* is seeded with. Both bounded, because these are
#: real geometry calls and a pad is not worth fifty of them.
_TERMINAL_TRIES = 22
_TERMINAL_KEEP = 4

#: Radius keys for the three rasters. The number is the half-width of the
#: copper being placed, which is what the halo has to be grown by.
_THIN = "thin"
_THICK = "thick"
_VIA = "via"


# ---------------------------------------------------------------------------
# The genome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Genome:
    """What the metaheuristics actually search.

    ``order`` is a permutation of routable-net indices; ``layer`` is 0 (top
    first) or 1 (bottom first) per net; ``topo`` picks how a net's pads are
    decomposed into tree edges. All three are per-net and all three change the
    board — ``order`` most of all, which is the point of the family."""

    order: tuple[int, ...]
    layer: tuple[int, ...]
    topo: tuple[int, ...]

    def key(self) -> tuple:
        return (self.order, self.layer, self.topo)


#: Tree shapes a net's pads can be decomposed into. Four, because a fifth
#: would need evidence it helps and there is none yet.
TOPOLOGIES = 4


def default_genome(count: int) -> Genome:
    """The problem's own order, top layer first, minimum spanning tree.

    This is the control's genome and the annealer's starting point, so a search
    that finds nothing still returns something no worse than the greedy run."""
    return Genome(
        order=tuple(range(count)),
        layer=tuple(0 for _ in range(count)),
        topo=tuple(0 for _ in range(count)),
    )


# ---------------------------------------------------------------------------
# Grid: obstacles as integers
# ---------------------------------------------------------------------------


def _pitch_for(problem: RoutingProblem) -> float:
    x0, y0, x1, y1 = problem.board.bbox
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    for step in range(0, 60):
        pitch = round(_MIN_PITCH_MM + 0.05 * step, 4)
        if pitch >= _MAX_PITCH_MM:
            return _MAX_PITCH_MM
        nx = int(math.ceil(width / pitch)) + 3
        ny = int(math.ceil(height / pitch)) + 3
        if nx * ny <= CELL_BUDGET:
            return pitch
    return _MAX_PITCH_MM


def _segment_distance_field(
    px: np.ndarray, py: np.ndarray, ax: float, ay: float, bx: float, by: float
) -> np.ndarray:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-18:
        return np.hypot(px - ax, py - ay)
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / length2, 0.0, 1.0)
    return np.hypot(px - (ax + t * dx), py - (ay + t * dy))


class _Grid:
    """Cell geometry. Owns no occupancy — that is :class:`_Static`."""

    __slots__ = ("pitch", "x0", "y0", "nx", "ny", "n", "_xs", "_ys", "xof", "yof")

    def __init__(self, problem: RoutingProblem, pitch: float) -> None:
        bx0, by0, bx1, by1 = problem.board.bbox
        self.pitch = pitch
        self.x0 = bx0 - pitch
        self.y0 = by0 - pitch
        self.nx = int(math.ceil((bx1 - bx0) / pitch)) + 3
        self.ny = int(math.ceil((by1 - by0) / pitch)) + 3
        self.n = self.nx * self.ny
        self._xs = self.x0 + (np.arange(self.nx) + 0.5) * pitch
        self._ys = self.y0 + (np.arange(self.ny) + 0.5) * pitch
        # Column and row of every cell, as plain lists. The A* asks for these
        # a million times a candidate and a list index is the cheapest lookup
        # CPython has.
        self.xof: list[int] = np.tile(np.arange(self.nx), self.ny).tolist()
        self.yof: list[int] = np.repeat(np.arange(self.ny), self.nx).tolist()

    def cell_of(self, point: Point) -> int:
        ix = int((point.x - self.x0) / self.pitch)
        iy = int((point.y - self.y0) / self.pitch)
        ix = min(max(ix, 0), self.nx - 1)
        iy = min(max(iy, 0), self.ny - 1)
        return iy * self.nx + ix

    def center(self, cell: int) -> Point:
        iy, ix = divmod(cell, self.nx)
        return Point(
            self.x0 + (ix + 0.5) * self.pitch, self.y0 + (iy + 0.5) * self.pitch
        )

    def cells_near(self, capsule: Capsule, grow: float) -> np.ndarray:
        """Flat indices of every cell whose centre is within ``grow`` of the
        capsule's copper edge. Conservative by half a cell is fine; the
        workspace re-measures anything that becomes copper."""
        ax, ay, bx, by, radius = capsule
        planes = core_halfplanes(capsule)
        reach = (capsule.sweep if planes is not None else radius) + grow
        bx0, by0, bx1, by1 = capsule_bbox(capsule)
        lo_x = int(math.floor((bx0 - grow - self.x0) / self.pitch))
        hi_x = int(math.ceil((bx1 + grow - self.x0) / self.pitch))
        lo_y = int(math.floor((by0 - grow - self.y0) / self.pitch))
        hi_y = int(math.ceil((by1 + grow - self.y0) / self.pitch))
        lo_x = max(lo_x, 0)
        lo_y = max(lo_y, 0)
        hi_x = min(hi_x, self.nx - 1)
        hi_y = min(hi_y, self.ny - 1)
        if hi_x < lo_x or hi_y < lo_y:
            return np.empty(0, dtype=np.int64)
        xs = self._xs[lo_x : hi_x + 1][None, :]
        ys = self._ys[lo_y : hi_y + 1][:, None]
        if planes is None:
            field = _segment_distance_field(xs, ys, ax, ay, bx, by)
        else:
            # A rectangle, a keepout, a polygon pad: the largest signed
            # distance to the core's own edge lines. Negative inside, exact
            # outside except in the wedge past a corner, where it under-reads
            # and claims a cell or two extra — the safe direction, and the
            # opposite of the inscribed stadium this replaced.
            field = None
            for pnx, pny, off in planes:
                d = pnx * xs + pny * ys - off
                field = d if field is None else np.maximum(field, d)
        rows, cols = np.nonzero(field <= reach)
        if rows.size == 0:
            return np.empty(0, dtype=np.int64)
        return (rows + lo_y).astype(np.int64) * self.nx + (cols + lo_x)


def _own_radius(problem: RoutingProblem, key: str) -> float:
    rules = problem.rules
    if key == _THIN:
        return max(rules.signal_trace_mm, rules.min_trace_mm) / 2.0
    if key == _THICK:
        return rules.power_trace_mm / 2.0
    return rules.via_pad_mm / 2.0


class _Static:
    """The obstacles, rasterised once per placement.

    Three fields per radius key, two layers each:

    ``hard`` no net may ever occupy this cell (board edge, keepout, a hole
    nobody owns) · ``own`` ``-1`` free, ``k`` only net ``k``, ``-2`` nobody.

    Plus a via-only ``via_hard``, which carries the two rules that are about a
    *hole* rather than about copper: a via may not be drilled inside an SMD pad,
    and two barrels may not sit closer than the hole-to-hole minimum whatever
    they carry.
    """

    __slots__ = (
        "grid", "hard", "own", "via_hard", "net_index", "pitch", "flat_pads",
        "flat_own",
    )

    def __init__(self, problem: RoutingProblem, grid: _Grid) -> None:
        self.grid = grid
        self.pitch = grid.pitch
        rules = problem.rules
        nets = [n for n in problem.nets]
        self.net_index = {net.id: i for i, net in enumerate(nets)}

        self.hard: dict[str, list[np.ndarray]] = {}
        self.own: dict[str, list[np.ndarray]] = {}
        inside = self._inside_mask(problem, grid)

        for key in (_THIN, _THICK, _VIA):
            radius = _own_radius(problem, key)
            usable = _erode(inside, (rules.min_edge_clearance_mm + radius), grid)
            hard = [
                (~usable).astype(np.uint8).reshape(-1).copy(),
                (~usable).astype(np.uint8).reshape(-1).copy(),
            ]
            own = [
                np.full(grid.n, -1, dtype=np.int32),
                np.full(grid.n, -1, dtype=np.int32),
            ]
            self.hard[key] = hard
            self.own[key] = own

            for keepout in problem.keepouts:
                capsule = keepout_capsule(keepout)
                cells = grid.cells_near(capsule, radius)
                for layer in keepout.layers:
                    if layer in LAYERS:
                        hard[LAYERS.index(layer)][cells] = 1

            for pad in problem.pads:
                cells = grid.cells_near(
                    pad_capsule(pad), rules.target_clearance_mm + radius
                )
                if cells.size == 0:
                    continue
                index = self.net_index.get(pad.net or "", None)
                for layer in pad.layers:
                    if layer not in LAYERS:
                        continue
                    slot = LAYERS.index(layer)
                    if index is None:
                        hard[slot][cells] = 1
                    else:
                        _claim(own[slot], cells, index)

            for drill in problem.drills:
                needed = rules.hole_clearance(drill)
                cells = grid.cells_near(drill_capsule(drill), needed + radius)
                if cells.size == 0:
                    continue
                index = (
                    self.net_index.get(drill.net or "", None) if drill.plated else None
                )
                for slot in (0, 1):
                    if index is None:
                        hard[slot][cells] = 1
                    else:
                        _claim(own[slot], cells, index)

            for trace in problem.existing_traces:
                index = self.net_index.get(trace.net or "", None)
                if trace.layer not in LAYERS:
                    continue
                slot = LAYERS.index(trace.layer)
                for a, b in trace.segments:
                    cells = grid.cells_near(
                        segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm),
                        rules.target_clearance_mm + radius,
                    )
                    if index is None:
                        hard[slot][cells] = 1
                    else:
                        _claim(own[slot], cells, index)

            for via in problem.existing_vias:
                index = self.net_index.get(via.net or "", None)
                cells = grid.cells_near(
                    disc_capsule(via.center.x, via.center.y, via.pad_mm),
                    rules.target_clearance_mm + radius,
                )
                for slot in (0, 1):
                    if index is None:
                        hard[slot][cells] = 1
                    else:
                        _claim(own[slot], cells, index)

        # Hole rules. A via drill inside an SMD pad needs plating we do not buy;
        # two drills closer than the hole-to-hole minimum break out into each
        # other whatever net they carry, so this one is not net-exempt.
        via_hard = np.zeros(grid.n, dtype=np.uint8)
        drill_radius = rules.via_drill_mm / 2.0
        for pad in problem.pads:
            if not pad.is_smd:
                continue
            via_hard[grid.cells_near(pad_capsule(pad), drill_radius)] = 1
        for drill in problem.drills:
            via_hard[
                grid.cells_near(
                    drill_capsule(drill), rules.min_hole_to_hole_mm + drill_radius
                )
            ] = 1
        for via in problem.existing_vias:
            via_hard[
                grid.cells_near(
                    disc_capsule(via.center.x, via.center.y, via.drill_mm),
                    rules.min_hole_to_hole_mm + drill_radius,
                )
            ] = 1
        self.via_hard = via_hard

        # Every pad as the pipeline reads it: **unrotated**. See
        # :func:`_via_clears_flat_pads` — this index exists because the score
        # is taken with a ruler that has a blind spot, and a router that
        # ignores the ruler it is measured with is not being honest, it is
        # being right in private.
        flat = GridIndex(2.0)
        flat_own = np.full(grid.n, -1, dtype=np.int32)
        keep_out = rules.min_via_to_copper_mm + drill_radius
        for pad in problem.pads:
            capsule = stadium(
                pad.center.x, pad.center.y, pad.width_mm, pad.height_mm
            )
            flat.insert(capsule, (pad.id, pad.net))
            index = self.net_index.get(pad.net or "", None)
            cells = grid.cells_near(capsule, keep_out)
            if index is None:
                via_hard[cells] = 1
            else:
                _claim(flat_own, cells, index)
        self.flat_pads = flat
        self.flat_own = flat_own

    @staticmethod
    def _inside_mask(problem: RoutingProblem, grid: _Grid) -> np.ndarray:
        """Which cell centres are on the board, as an ``(ny, nx)`` bool array.

        A real outline is scan-converted from the polygon; a board that only
        knows its bounding box gets the bounding box, which is exactly what
        ``Workspace`` falls back to as well."""
        outline = problem.board.outline
        xs = grid._xs
        ys = grid._ys
        if len(outline) < 3:
            bx0, by0, bx1, by1 = problem.board.bbox
            row = (xs >= bx0) & (xs <= bx1)
            col = (ys >= by0) & (ys <= by1)
            return col[:, None] & row[None, :]
        px = np.array([p.x for p in outline], dtype=np.float64)
        py = np.array([p.y for p in outline], dtype=np.float64)
        qx = np.roll(px, -1)
        qy = np.roll(py, -1)
        mask = np.zeros((grid.ny, grid.nx), dtype=bool)
        for row, yc in enumerate(ys):
            spans = (py > yc) != (qy > yc)
            if not spans.any():
                continue
            y1 = py[spans]
            y2 = qy[spans]
            x1 = px[spans]
            x2 = qx[spans]
            crossings = (x2 - x1) * (yc - y1) / (y2 - y1) + x1
            crossings.sort()
            counts = np.searchsorted(crossings, xs, side="right")
            mask[row] = (counts % 2) == 1
        return mask


def _via_clears_flat_pads(
    static: _Static, rules, center: Point, drill_mm: float, net_id: str
) -> bool:
    """Would the *pipeline's* rotation-blind pad model call this via too close?

    ``circuitpy.checks`` does not read ``ccw_rotation`` on a ``pcb_smtpad``, so
    a 2.25 x 0.63mm pill turned 90 degrees is measured as a bar lying the wrong
    way. ``Workspace`` reads the field and measures the pad where it really is.
    Both are in this repo and they disagree, and the one that scores is the
    blind one.

    Measured on terminal-keyboard, 2026-08-16: the annealer put a via 0.32mm
    from ``pcb_smtpad_363`` — legal by 0.12mm — and the pipeline, reading that
    pad flat, called it 0.185mm *inside* the pad and raised
    ``dfm_hole_clearance``, the exact defect the router package was built to
    eliminate. Three instances lost a clean DRC to it.

    So a via has to clear a foreign pad measured **both** ways. That is the
    conservative direction: it costs via positions, never legality. The real
    fix is upstream in ``circuitpy.checks``, and it is not this file's to make.
    """
    hole = disc_capsule(center.x, center.y, drill_mm)
    limit = rules.min_via_to_copper_mm
    for capsule, (_pad_id, pad_net) in static.flat_pads.query(hole, limit + 0.1):
        if pad_net and pad_net == net_id:
            continue
        if capsule_gap(hole, capsule) < limit - 1e-9:
            return False
    return True


def _claim(own: np.ndarray, cells: np.ndarray, index: int) -> None:
    """Mark ``cells`` as belonging to net ``index``, or to nobody if another
    net already claimed them. Idempotent, so one net stamping the same cell
    twice does not lock itself out."""
    if cells.size == 0:
        return
    cells = np.unique(cells)
    current = own[cells]
    own[cells] = np.where(
        (current == -1) | (current == index), np.int32(index), np.int32(-2)
    )


def _erode(inside: np.ndarray, distance_mm: float, grid: _Grid) -> np.ndarray:
    """Cells whose whole ``distance_mm`` neighbourhood is on the board.

    Sampled at cell centres, so it can be optimistic by up to half a cell —
    which is why nothing this function approves becomes copper without a
    ``Workspace`` check."""
    reach = int(distance_mm / grid.pitch)
    if reach <= 0:
        return inside
    out = inside.copy()
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            if dx * dx + dy * dy > reach * reach:
                continue
            if dx == 0 and dy == 0:
                continue
            shifted = np.zeros_like(inside)
            ys = slice(max(0, dy), inside.shape[0] + min(0, dy))
            yd = slice(max(0, -dy), inside.shape[0] + min(0, -dy))
            xs = slice(max(0, dx), inside.shape[1] + min(0, dx))
            xd = slice(max(0, -dx), inside.shape[1] + min(0, -dx))
            shifted[yd, xd] = inside[ys, xs]
            out &= shifted
    return out


_STATIC_CACHE: "OrderedDict[tuple, tuple[_Grid, _Static]]" = OrderedDict()
_STATIC_CACHE_MAX = 3


def _static_for(problem: RoutingProblem) -> tuple[_Grid, _Static]:
    """Rasterise the placement, once. Keyed on the placement hash, because two
    problems with the same id and different pads are two problems."""
    from routerlib.bench import placement_hash

    pitch = _pitch_for(problem)
    key = (problem.id, placement_hash(problem), len(problem.nets), pitch)
    hit = _STATIC_CACHE.get(key)
    if hit is not None:
        _STATIC_CACHE.move_to_end(key)
        return hit
    grid = _Grid(problem, pitch)
    static = _Static(problem, grid)
    _STATIC_CACHE[key] = (grid, static)
    while len(_STATIC_CACHE) > _STATIC_CACHE_MAX:
        _STATIC_CACHE.popitem(last=False)
    return grid, static


# ---------------------------------------------------------------------------
# Per-candidate occupancy
# ---------------------------------------------------------------------------


class _Field:
    """One candidate's mutable copy of the static field.

    Copying beats incremental rip-up here: a candidate is a whole board routed
    in a whole order, so the honest unit of work is a rebuild, and a memcpy of
    a few megabytes is cheaper than the bookkeeping that would let one net be
    removed exactly."""

    __slots__ = (
        "grid", "static", "hard", "own", "via_hard", "flat_own", "rules",
        "_free_cache",
    )

    def __init__(self, problem: RoutingProblem, grid: _Grid, static: _Static) -> None:
        self.grid = grid
        self.static = static
        self.rules = problem.rules
        self.hard = {k: [a.copy() for a in v] for k, v in static.hard.items()}
        self.own = {k: [a.copy() for a in v] for k, v in static.own.items()}
        self.via_hard = static.via_hard.copy()
        self.flat_own = static.flat_own
        self._free_cache: dict[tuple[str, int, int], bytes] = {}

    def free_nodes(self, key: str, net_index: int) -> bytes:
        """A byte per A* node — ``cell * 2 + layer`` — set where net
        ``net_index`` may put copper of this width.

        Built once per (net, width) and thrown away when the net is done,
        because the next net sees a different board."""
        cached = self._free_cache.get((key, net_index))
        if cached is not None:
            return cached
        interleaved = np.empty(2 * self.grid.n, dtype=bool)
        for slot in (0, 1):
            own = self.own[key][slot]
            hard = self.hard[key][slot]
            interleaved[slot::2] = (hard == 0) & ((own == -1) | (own == net_index))
        out = interleaved.tobytes()
        self._free_cache[(key, net_index)] = out
        return out

    def via_free_bytes(self, net_index: int) -> bytes:
        cached = self._free_cache.get((_VIA, 2, net_index))
        if cached is not None:
            return cached
        own_top = self.own[_VIA][0]
        own_bot = self.own[_VIA][1]
        flat = self.flat_own
        mask = (
            (self.via_hard == 0)
            & (self.hard[_VIA][0] == 0)
            & (self.hard[_VIA][1] == 0)
            & ((own_top == -1) | (own_top == net_index))
            & ((own_bot == -1) | (own_bot == net_index))
            & ((flat == -1) | (flat == net_index))
        )
        out = mask.tobytes()
        self._free_cache[(_VIA, 2, net_index)] = out
        return out

    def invalidate(self) -> None:
        self._free_cache.clear()

    def add_trace(self, trace: Trace, net_index: int) -> None:
        if trace.layer not in LAYERS:
            return
        slot = LAYERS.index(trace.layer)
        clearance = self.rules.target_clearance_mm
        for key in (_THIN, _THICK, _VIA):
            grow = clearance + _own_radius_cached(self.rules, key)
            own = self.own[key][slot]
            for a, b in trace.segments:
                cells = self.grid.cells_near(
                    segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm), grow
                )
                _claim(own, cells, net_index)
        self.invalidate()

    def add_via(self, via: Via, net_index: int) -> None:
        clearance = self.rules.target_clearance_mm
        capsule = disc_capsule(via.center.x, via.center.y, via.pad_mm)
        for key in (_THIN, _THICK, _VIA):
            grow = clearance + _own_radius_cached(self.rules, key)
            cells = self.grid.cells_near(capsule, grow)
            for slot in (0, 1):
                _claim(self.own[key][slot], cells, net_index)
        self.via_hard[
            self.grid.cells_near(
                disc_capsule(via.center.x, via.center.y, via.drill_mm),
                self.rules.min_hole_to_hole_mm + via.drill_mm / 2.0,
            )
        ] = 1
        self.invalidate()


def _own_radius_cached(rules, key: str) -> float:
    if key == _THIN:
        return max(rules.signal_trace_mm, rules.min_trace_mm) / 2.0
    if key == _THICK:
        return rules.power_trace_mm / 2.0
    return rules.via_pad_mm / 2.0


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


def _mst_edges(pads: Sequence[Pad]) -> list[tuple[Pad, Pad]]:
    """Prim over pad centres, ties broken by pad id so the tree is stable."""
    if len(pads) < 2:
        return []
    inside = [pads[0]]
    outside = list(pads[1:])
    edges: list[tuple[Pad, Pad]] = []
    while outside:
        best: tuple[float, str, str] | None = None
        pick: tuple[Pad, Pad] | None = None
        for a in inside:
            for b in outside:
                key = (a.center.distance_to(b.center), a.id, b.id)
                if best is None or key < best:
                    best, pick = key, (a, b)
        assert pick is not None
        edges.append(pick)
        inside.append(pick[1])
        outside.remove(pick[1])
    return edges


def _tree_edges(pads: Sequence[Pad], topo: int) -> list[tuple[Pad, Pad]]:
    """One net's pads, decomposed into edges four different ways.

    Topology matters as much as order and for the same reason: a star through
    a congested pad and a chain around the outside are the same netlist and a
    completely different demand on the board."""
    if len(pads) < 2:
        return []
    if topo == 0:
        return _mst_edges(pads)
    if topo == 1:
        edges = _mst_edges(pads)
        return sorted(
            edges, key=lambda e: (e[0].center.distance_to(e[1].center), e[0].id, e[1].id)
        )
    if topo == 2:
        cx = sum(p.center.x for p in pads) / len(pads)
        cy = sum(p.center.y for p in pads) / len(pads)
        hub = min(pads, key=lambda p: (p.center.distance_to(Point(cx, cy)), p.id))
        return [(hub, p) for p in pads if p.id != hub.id]
    ordered = sorted(pads, key=lambda p: (p.center.x, p.center.y, p.id))
    return list(zip(ordered, ordered[1:]))


def _patterns(a: Point, b: Point) -> Iterable[tuple[Point, ...]]:
    yield (a, b)
    if a.x != b.x and a.y != b.y:
        yield (a, Point(b.x, a.y), b)
        yield (a, Point(a.x, b.y), b)
        for frac in (0.5, 0.25, 0.75):
            mid = a.x + (b.x - a.x) * frac
            yield (a, Point(mid, a.y), Point(mid, b.y), b)
        for frac in (0.5, 0.25, 0.75):
            mid = a.y + (b.y - a.y) * frac
            yield (a, Point(a.x, mid), Point(b.x, mid), b)


# ---------------------------------------------------------------------------
# The inner router
# ---------------------------------------------------------------------------


@dataclass
class _RouteState:
    traces: list[Trace]
    vias: list[Via]
    failed_edges: int = 0
    maze_nodes: int = 0
    maze_calls: int = 0
    maze_wins: int = 0
    pattern_wins: int = 0
    workspace_rejects: int = 0


class InnerRouter:
    """Route a whole board once, exactly as one genome asks.

    Deterministic and side-effect free: the same problem and the same genome
    always produce the same copper, which is what makes a population of
    genomes comparable at all."""

    def __init__(self, *, use_maze: bool = True) -> None:
        self.use_maze = use_maze

    def route(
        self,
        problem: RoutingProblem,
        genome: Genome,
        meter: BudgetMeter,
        *,
        grid: _Grid | None = None,
        static: _Static | None = None,
    ) -> tuple[RoutingSolution, _RouteState]:
        if grid is None or static is None:
            grid, static = _static_for(problem)
        field = _Field(problem, grid, static)
        ws = Workspace(problem)
        state = _RouteState(traces=[], vias=[])
        routable = list(problem.routable_nets)
        planes_by_net: dict[str, list[Plane]] = {}
        for plane in problem.planes:
            planes_by_net.setdefault(plane.net, []).append(plane)

        unrouted: list[str] = []
        via_seq = 0
        for slot in genome.order:
            if slot >= len(routable):
                continue
            net = routable[slot]
            net_index = static.net_index.get(net.id, -1)
            pads = sorted(
                problem.pads_of(net.id), key=lambda p: (p.center.x, p.center.y, p.id)
            )
            width = max(net.min_width_mm, problem.rules.min_trace_mm)
            key = _THICK if width > problem.rules.signal_trace_mm + 1e-9 else _THIN
            meter.tick()

            if net.id in planes_by_net:
                via_seq = self._stitch_plane(
                    problem, ws, field, state, net, pads, planes_by_net[net.id],
                    width, net_index, via_seq, meter,
                )
                continue

            failures = 0
            for pad_a, pad_b in _tree_edges(pads, genome.topo[slot]):
                if meter.exhausted or state.maze_nodes > _CANDIDATE_NODE_CAP:
                    failures += 1
                    continue
                meter.tick()
                placed, via_seq = self._route_edge(
                    problem, ws, field, state, net, pad_a, pad_b, width, key,
                    net_index, genome.layer[slot], via_seq, meter,
                )
                if not placed:
                    failures += 1
            if failures:
                unrouted.append(net.id)

        solution = RoutingSolution(
            router="inner",
            traces=tuple(state.traces),
            vias=tuple(state.vias),
            complete=not unrouted,
            unrouted_nets=tuple(sorted(set(unrouted))),
            iterations=meter.iterations,
            nodes_expanded=meter.nodes,
        )
        return solution, state

    # -- planes ----------------------------------------------------------

    def _stitch_plane(
        self, problem, ws, field, state, net, pads, planes, width, net_index,
        via_seq, meter,
    ) -> int:
        """One via per pad into the pour. A pad already inside the plane on a
        layer it can see is connected with no copper at all."""
        shapes = {p.id: PolygonIndex(p.outline) for p in planes}
        rules = problem.rules
        for pad in pads:
            if meter.exhausted:
                break
            meter.tick()
            in_plane = next(
                (
                    p
                    for p in planes
                    if pad.reachable_from(p.layer)
                    and shapes[p.id].contains(pad.center.x, pad.center.y)
                ),
                None,
            )
            if in_plane is not None and pad.kind == "plated_hole":
                continue
            target = next(
                (p for p in planes if shapes[p.id].contains(pad.center.x, pad.center.y)),
                planes[0],
            )
            reach = (
                max(pad.width_mm, pad.height_mm) / 2.0
                + rules.target_clearance_mm
                + rules.via_pad_mm / 2.0
                + 0.05
            )
            stub_layer = pad.layers[0]
            for ux, uy in ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)):
                point = Point(pad.center.x + ux * reach, pad.center.y + uy * reach)
                if not shapes[target.id].contains(point.x, point.y):
                    continue
                if ws.via_ok(point, net.id) is not True:
                    continue
                if not _via_clears_flat_pads(
                    field.static, rules, point, rules.via_drill_mm, net.id
                ):
                    continue
                stub = (pad.center, point)
                if ws.path_ok(stub_layer, stub, width, net.id) is not True:
                    continue
                trace = Trace(
                    id=f"{net.id}~{len(state.traces)}",
                    net=net.id,
                    layer=stub_layer,
                    points=stub,
                    width_mm=width,
                )
                ws.commit_trace(trace)
                field.add_trace(trace, net_index)
                state.traces.append(trace)
                via = Via(
                    id=f"mv{via_seq}",
                    net=net.id,
                    center=point,
                    drill_mm=rules.via_drill_mm,
                    pad_mm=rules.via_pad_mm,
                )
                via_seq += 1
                ws.commit_via(via)
                field.add_via(via, net_index)
                state.vias.append(via)
                break
        return via_seq

    # -- one edge --------------------------------------------------------

    def _route_edge(
        self, problem, ws, field, state, net, pad_a, pad_b, width, key,
        net_index, layer_pref, via_seq, meter,
    ) -> tuple[bool, int]:
        order = (layer_pref, 1 - layer_pref)
        for slot in order:
            layer = LAYERS[slot]
            if not (pad_a.reachable_from(layer) and pad_b.reachable_from(layer)):
                continue
            for points in _patterns(pad_a.center, pad_b.center):
                meter.tick()
                if ws.path_ok(layer, points, width, net.id) is not True:
                    continue
                trace = Trace(
                    id=f"{net.id}~{len(state.traces)}",
                    net=net.id,
                    layer=layer,
                    points=tuple(points),
                    width_mm=width,
                )
                ws.commit_trace(trace)
                field.add_trace(trace, net_index)
                state.traces.append(trace)
                state.pattern_wins += 1
                return True, via_seq
        if not self.use_maze or net_index < 0:
            return False, via_seq
        return self._maze_edge(
            problem, ws, field, state, net, pad_a, pad_b, width, key,
            net_index, layer_pref, via_seq, meter,
        )

    def _maze_edge(
        self, problem, ws, field, state, net, pad_a, pad_b, width, key,
        net_index, layer_pref, via_seq, meter,
    ) -> tuple[bool, int]:
        grid = field.grid
        state.maze_calls += 1
        free = field.free_nodes(key, net_index)
        via_free = field.via_free_bytes(net_index)

        clearance = problem.rules.target_clearance_mm
        starts = _terminals(grid, ws, free, pad_a, width, net.id, clearance)
        goals = _terminals(grid, ws, free, pad_b, width, net.id, clearance)
        if not starts or not goals:
            return False, via_seq
        goal_nodes = {cell * 2 + slot for slot, cell in goals}
        anchor = grid.cell_of(pad_b.center)

        path, expanded = _astar(
            grid, free, via_free, starts, goal_nodes, anchor, layer_pref,
            min(_EDGE_NODE_CAP, max(0, _CANDIDATE_NODE_CAP - state.maze_nodes)),
        )
        state.maze_nodes += expanded
        meter.expand(expanded)
        if not path:
            return False, via_seq

        runs, via_cells = _split_runs(path)
        polylines: list[tuple[str, list[Point]]] = []
        for index, (slot, cells) in enumerate(runs):
            points = [grid.center(c) for c in cells]
            if index == 0:
                points.insert(0, pad_a.center)
            if index == len(runs) - 1:
                points.append(pad_b.center)
            polylines.append((LAYERS[slot], points))

        vias = [
            Via(
                id=f"mv{via_seq + i}",
                net=net.id,
                center=grid.center(cell),
                drill_mm=problem.rules.via_drill_mm,
                pad_mm=problem.rules.via_pad_mm,
            )
            for i, cell in enumerate(via_cells)
        ]
        spacing = problem.rules.min_hole_to_hole_mm + problem.rules.via_drill_mm
        for i, one in enumerate(vias):
            for other in vias[i + 1 :]:
                if one.center.distance_to(other.center) < spacing - 1e-9:
                    return False, via_seq
            if ws.via_ok(one.center, net.id) is not True:
                state.workspace_rejects += 1
                return False, via_seq
            if not _via_clears_flat_pads(
                field.static, problem.rules, one.center,
                problem.rules.via_drill_mm, net.id,
            ):
                state.workspace_rejects += 1
                return False, via_seq

        simplified: list[tuple[str, tuple[Point, ...]]] = []
        for layer, points in polylines:
            reduced = _simplify(ws, layer, points, width, net.id)
            if reduced is None:
                state.workspace_rejects += 1
                return False, via_seq
            simplified.append((layer, reduced))

        for layer, points in simplified:
            if len(points) < 2:
                continue
            if ws.path_ok(layer, points, width, net.id) is not True:
                state.workspace_rejects += 1
                return False, via_seq

        for via in vias:
            ws.commit_via(via)
            field.add_via(via, net_index)
            state.vias.append(via)
        for layer, points in simplified:
            if len(points) < 2:
                continue
            trace = Trace(
                id=f"{net.id}~{len(state.traces)}",
                net=net.id,
                layer=layer,
                points=points,
                width_mm=width,
            )
            ws.commit_trace(trace)
            field.add_trace(trace, net_index)
            state.traces.append(trace)
        state.maze_wins += 1
        return True, via_seq + len(vias)


def _terminals(
    grid: _Grid,
    ws: Workspace,
    free: bytes,
    pad: Pad,
    width: float,
    net: str,
    clearance: float,
) -> list[tuple[int, int]]:
    """Grid cells a net may enter this pad from, nearest centre first.

    Each one is pre-checked with the real workspace: the short segment from the
    pad centre to the cell centre has to be legal copper, or the maze would find
    a path whose first millimetre cannot be built.

    The search radius is the pad's own reach *plus a clearance and two cells*,
    and that is not padding. On a 0.4mm-pitch part the only free cell is past
    the end of the pad — measured on harness-puck, a radius of half the pad plus
    one cell found two cells and both were illegal, while the first legal escape
    sat at 0.68mm. Sixteen pads reported "no way in" for that reason alone."""
    reach = (
        max(pad.width_mm, pad.height_mm) / 2.0
        + clearance
        + width / 2.0
        + 2.0 * grid.pitch
    )
    candidates: list[tuple[float, int, int]] = []
    for layer in pad.layers:
        if layer not in LAYERS:
            continue
        slot = LAYERS.index(layer)
        cells = grid.cells_near(disc_capsule(pad.center.x, pad.center.y, 0.0), reach)
        for cell in cells.tolist():
            if not free[cell * 2 + slot]:
                continue
            candidates.append(
                (pad.center.distance_to(grid.center(cell)), slot, cell)
            )
    candidates.sort()
    out: list[tuple[int, int]] = []
    for _, slot, cell in candidates[:_TERMINAL_TRIES]:
        layer = LAYERS[slot]
        if ws.path_ok(layer, (pad.center, grid.center(cell)), width, net) is not True:
            continue
        out.append((slot, cell))
        if len(out) >= _TERMINAL_KEEP:
            break
    return out


def _astar(
    grid: _Grid,
    free: bytes,
    via_free: bytes,
    starts: Sequence[tuple[int, int]],
    goals: set[int],
    anchor: int,
    layer_pref: int,
    node_cap: int,
) -> tuple[list[int], int]:
    """Two-layer A*. A layer change is a via and costs like one.

    A node is ``cell * 2 + layer``, so a step left is ``node - 2``, a step up
    is ``node - 2 * nx`` and a via is ``node ^ 1`` — the whole neighbourhood is
    integer arithmetic on one number, which is what makes a few hundred
    thousand expansions per candidate affordable in Python at all.

    Ties break on the node index rather than on insertion order, so the queue
    does not smuggle in a dependence on the order cells happened to be pushed.
    ``free`` is one byte per node and ``via_free`` one byte per cell; both are
    built from the same field the workspace measures, and neither is trusted
    without it."""
    if node_cap <= 0:
        return ([], 0)
    nx = grid.nx
    ny = grid.ny
    n = grid.n
    xof = grid.xof
    yof = grid.yof
    anchor_y, anchor_x = divmod(anchor, nx)
    hx = [_STEP * abs(i - anchor_x) for i in range(nx)]
    hy = [_STEP * abs(j - anchor_y) for j in range(ny)]
    off = _STEP + _OFF_LAYER_SURCHARGE
    row_step = 2 * nx
    push = heapq.heappush
    pop = heapq.heappop

    best: dict[int, int] = {}
    came: dict[int, int] = {}
    heap: list[tuple[int, int]] = []
    for slot, cell in starts:
        node = cell * 2 + slot
        if node in best:
            continue
        best[node] = 0
        push(heap, (hx[xof[cell]] + hy[yof[cell]], node))

    closed = bytearray(2 * n)
    expanded = 0
    found: int | None = None
    limit = 1 << 30
    while heap:
        _, node = pop(heap)
        if closed[node]:
            continue
        closed[node] = 1
        if node in goals:
            found = node
            break
        expanded += 1
        if expanded >= node_cap:
            break
        cell = node >> 1
        cost = best[node]
        step = _STEP if (node & 1) == layer_pref else off
        walk = cost + step
        x = xof[cell]
        for node_b in (
            node - 2 if x else -1,
            node + 2 if x + 1 < nx else -1,
            node - row_step,
            node + row_step,
        ):
            if node_b < 0 or node_b >= 2 * n or closed[node_b] or not free[node_b]:
                continue
            if walk < best.get(node_b, limit):
                best[node_b] = walk
                came[node_b] = node
                other = node_b >> 1
                push(heap, (walk + hx[xof[other]] + hy[yof[other]], node_b))
        if via_free[cell]:
            node_b = node ^ 1
            if not closed[node_b] and free[node_b]:
                jump = cost + _VIA_COST
                if jump < best.get(node_b, limit):
                    best[node_b] = jump
                    came[node_b] = node
                    push(heap, (jump + hx[x] + hy[yof[cell]], node_b))

    if found is None:
        return ([], expanded)
    path = [found]
    while path[-1] in came:
        path.append(came[path[-1]])
    path.reverse()
    return (path, expanded)


def _split_runs(path: Sequence[int]) -> tuple[list[tuple[int, list[int]]], list[int]]:
    runs: list[tuple[int, list[int]]] = []
    vias: list[int] = []
    slot = path[0] & 1
    current = [path[0] >> 1]
    for node in path[1:]:
        next_slot = node & 1
        next_cell = node >> 1
        if next_slot != slot:
            runs.append((slot, current))
            vias.append(next_cell)
            slot = next_slot
            current = [next_cell]
        else:
            current.append(next_cell)
    runs.append((slot, current))
    return runs, vias


def _simplify(
    ws: Workspace, layer: str, points: Sequence[Point], width: float, net: str
) -> tuple[Point, ...] | None:
    """Replace a staircase with the fewest straight segments that are legal.

    A 120-cell path is 120 points of copper and 120 DRC items; the same
    connection as four segments is the same connection. Every shortcut is
    approved by the workspace before it is taken, so simplification cannot
    turn a legal path into an illegal one."""
    if len(points) < 2:
        return tuple(points)
    out = [points[0]]
    index = 0
    last = len(points) - 1
    while index < last:
        stop = index + 1
        upper = min(last, index + _LOS_WINDOW)
        for candidate in range(upper, index + 1, -1):
            if ws.segment_ok(layer, points[index], points[candidate], width, net) is True:
                stop = candidate
                break
        else:
            if ws.segment_ok(layer, points[index], points[stop], width, net) is not True:
                return None
        out.append(points[stop])
        index = stop
    deduped = [out[0]]
    for point in out[1:]:
        if point != deduped[-1]:
            deduped.append(point)
    if len(deduped) < 2:
        return None
    return tuple(deduped)


# ---------------------------------------------------------------------------
# Fitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evaluation:
    genome: Genome
    solution: RoutingSolution
    key: tuple
    completeness: float
    errors: int
    unconnected: tuple[str, ...]

    @property
    def cost(self) -> float:
        """The lexicographic key flattened, for an acceptance probability only.

        Selection everywhere else uses ``key`` directly, because the harness
        order is the order that matters and a weighted sum is not it. An
        annealer needs a *difference* between two candidates, though, and a
        tuple has none — so the weights below exist for exp(-delta/T) and for
        nothing else."""
        return (
            (1.0 - self.completeness) * 1000.0
            + self.errors * 100.0
            + len(self.solution.vias) * 0.02
            + self.solution.copper_length_mm * 0.0005
        )


class _Evaluator:
    """Route a genome, score it with the harness, remember what it cost."""

    def __init__(
        self,
        problem: RoutingProblem,
        meter: BudgetMeter,
        *,
        use_pipeline: bool,
        use_maze: bool,
    ) -> None:
        self.problem = problem
        self.meter = meter
        self.use_pipeline = use_pipeline
        self.inner = InnerRouter(use_maze=use_maze)
        self.grid, self.static = _static_for(problem)
        self.evaluations = 0
        self.cache: dict[tuple, Evaluation] = {}
        self.last_state: _RouteState | None = None

    def __call__(self, genome: Genome) -> Evaluation:
        cached = self.cache.get(genome.key())
        if cached is not None:
            return cached
        from routerlib import scoring

        solution, state = self.inner.route(
            self.problem, genome, self.meter, grid=self.grid, static=self.static
        )
        result = scoring.score(
            self.problem, solution, use_pipeline=self.use_pipeline
        )
        evaluation = Evaluation(
            genome=genome,
            solution=solution,
            key=result.key(),
            completeness=result.completeness,
            errors=result.errors,
            unconnected=result.unconnected,
        )
        self.evaluations += 1
        self.last_state = state
        self.cache[genome.key()] = evaluation
        return evaluation


def _finalise(
    name: str,
    best: Evaluation,
    meter: BudgetMeter,
    started: float,
    notes: Sequence[str],
) -> RoutingSolution:
    """Return the winner with an honest ``complete`` flag.

    The flag is recomputed from the copper rather than copied from the inner
    router's own bookkeeping, so a solution never claims a net it did not
    actually join."""
    said = list(notes)
    if meter.stop_reason == "wall_clock":
        said.append(
            "hit the wall-clock safety valve — the search was cut short by a "
            "clock, so this number is not comparable to one that was not"
        )
    elif meter.stop_reason:
        said.append(f"budget stop: {meter.stop_reason}")
    return replace(
        best.solution,
        router=name,
        complete=best.completeness >= 1.0,
        unrouted_nets=best.unconnected,
        iterations=meter.iterations,
        nodes_expanded=meter.nodes,
        wall_clock_s=time.perf_counter() - started,
        notes=tuple(said),
    )


# ---------------------------------------------------------------------------
# The control
# ---------------------------------------------------------------------------


class GreedyMazeRouter:
    """The inner router, run once, in the problem's own net order.

    This is the number every search result has to beat. It is registered as a
    router in its own right because a control you cannot run is not a control."""

    name = "maze-greedy"

    def __init__(self, *, use_maze: bool = True) -> None:
        self.use_maze = use_maze

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        started = time.perf_counter()
        meter = budget.meter()
        evaluator = _Evaluator(
            problem, meter, use_pipeline=False, use_maze=self.use_maze
        )
        count = len(problem.routable_nets)
        best = evaluator(default_genome(count))
        notes = ["1 candidate evaluated, no search"]
        return _finalise(self.name, best, meter, started, notes)


class PatternGreedyRouter(GreedyMazeRouter):
    """The control's control: pattern routing only, no maze. Its job is to show
    how much of any improvement is the maze rather than the search."""

    name = "maze-off-greedy"

    def __init__(self) -> None:
        super().__init__(use_maze=False)


# ---------------------------------------------------------------------------
# Simulated annealing
# ---------------------------------------------------------------------------


class AnnealRouter:
    """Simulated annealing over net order, layer preference and topology.

    The schedule is fixed and the moves are seeded, so the run is reproducible
    to the byte. Two details are worth naming because they are where the search
    actually gets its leverage:

    * **Moves are informed.** A move that touches a net which is already
      connected mostly wastes an evaluation. Two thirds of proposals target a
      net that is currently *unconnected*, and the most common proposal is to
      move it earlier in the order — the one change that reliably gives a net
      the channel it lost.
    * **Acceptance uses a scalarised cost, selection does not.** The harness
      key is lexicographic and has no metric; exp(-delta/T) needs one. So the
      annealer accepts on a weighted sum and keeps the best on the harness key.
    """

    name = "meta-anneal"

    def __init__(
        self,
        *,
        evaluations: int = 32,
        temperature: float = 8.0,
        cooling: float = 0.94,
        use_pipeline: bool = False,
    ) -> None:
        self.evaluations = evaluations
        self.temperature = temperature
        self.cooling = cooling
        self.use_pipeline = use_pipeline

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        started = time.perf_counter()
        meter = budget.meter()
        rng = random.Random(budget.seed)
        evaluate = _Evaluator(
            problem, meter, use_pipeline=self.use_pipeline, use_maze=True
        )
        count = len(problem.routable_nets)
        index_of = {net.id: i for i, net in enumerate(problem.routable_nets)}

        start = evaluate(default_genome(count))
        best = start
        for genome in seed_genomes(problem)[1:]:
            if evaluate.evaluations >= self.evaluations or meter.exhausted:
                break
            candidate = evaluate(genome)
            if candidate.key < best.key:
                best = candidate
        current = best
        seeded = best.completeness

        accepted = 0
        proposals = 0
        cap = max(self.evaluations * 8, 16)
        temperature = self.temperature
        while (
            evaluate.evaluations < self.evaluations
            and proposals < cap
            and not meter.exhausted
        ):
            temperature = max(temperature * self.cooling, 1e-6)
            proposals += 1
            candidate_genome = _mutate(
                rng, current.genome, current.unconnected, index_of, count
            )
            if candidate_genome.key() == current.genome.key():
                continue
            candidate = evaluate(candidate_genome)
            delta = candidate.cost - current.cost
            if delta <= 0.0 or rng.random() < math.exp(-delta / temperature):
                current = candidate
                accepted += 1
            if candidate.key < best.key:
                best = candidate

        notes = [
            f"{evaluate.evaluations} candidates evaluated, {proposals} proposed, "
            f"{accepted} accepted",
            f"default order {start.completeness * 100:.1f}% -> best seed "
            f"{seeded * 100:.1f}% -> annealed {best.completeness * 100:.1f}% complete",
        ]
        return _finalise(self.name, best, meter, started, notes)


def _mutate(
    rng: random.Random,
    genome: Genome,
    unconnected: Sequence[str],
    index_of: dict[str, int],
    count: int,
) -> Genome:
    """One neighbourhood move. Biased towards nets that are currently broken."""
    if count < 2:
        return genome
    broken = sorted(index_of[n] for n in unconnected if n in index_of)
    order = list(genome.order)
    layer = list(genome.layer)
    topo = list(genome.topo)
    target = rng.choice(broken) if broken and rng.random() < 0.67 else rng.randrange(count)
    roll = rng.random()
    if roll < 0.45:
        position = order.index(target)
        destination = rng.randrange(0, max(1, position) if position else count)
        order.pop(position)
        order.insert(destination, target)
    elif roll < 0.70:
        i = order.index(target)
        j = rng.randrange(count)
        order[i], order[j] = order[j], order[i]
    elif roll < 0.88:
        layer[target] = 1 - layer[target]
    else:
        topo[target] = (topo[target] + 1 + rng.randrange(TOPOLOGIES - 1)) % TOPOLOGIES
    return Genome(tuple(order), tuple(layer), tuple(topo))


# ---------------------------------------------------------------------------
# Genetic algorithm
# ---------------------------------------------------------------------------


class GeneticRouter:
    """A GA over the same genome, with order crossover that keeps sub-orders.

    Order crossover (OX) is the right operator here for a concrete reason: what
    a good genome knows is *"these nets go before those nets"*, and OX copies a
    contiguous stretch of one parent and fills the rest in the other parent's
    relative order, so an inherited stretch keeps the relationships that made it
    good. A uniform crossover over positions would shred exactly that."""

    name = "meta-genetic"

    def __init__(
        self,
        *,
        population: int = 8,
        generations: int = 4,
        elite: int = 2,
        mutation_rate: float = 0.5,
        use_pipeline: bool = False,
    ) -> None:
        self.population = population
        self.generations = generations
        self.elite = elite
        self.mutation_rate = mutation_rate
        self.use_pipeline = use_pipeline

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        started = time.perf_counter()
        meter = budget.meter()
        rng = random.Random(budget.seed)
        evaluate = _Evaluator(
            problem, meter, use_pipeline=self.use_pipeline, use_maze=True
        )
        count = len(problem.routable_nets)
        index_of = {net.id: i for i, net in enumerate(problem.routable_nets)}

        seed_genome = default_genome(count)
        population = seed_genomes(problem)[: self.population]
        while len(population) < self.population:
            population.append(_random_genome(rng, count))
        scored = [evaluate(g) for g in population]
        scored.sort(key=lambda e: (e.key, e.genome.key()))

        for _ in range(self.generations):
            if meter.exhausted:
                break
            children: list[Genome] = [e.genome for e in scored[: self.elite]]
            while len(children) < self.population:
                parent_a = _tournament(rng, scored)
                parent_b = _tournament(rng, scored)
                child = _crossover(rng, parent_a.genome, parent_b.genome, count)
                if rng.random() < self.mutation_rate:
                    child = _mutate(
                        rng, child, parent_a.unconnected, index_of, count
                    )
                children.append(child)
            scored = [evaluate(g) for g in children]
            scored.sort(key=lambda e: (e.key, e.genome.key()))

        best = min(
            list(evaluate.cache.values()), key=lambda e: (e.key, e.genome.key())
        )
        notes = [
            f"{evaluate.evaluations} candidates evaluated over "
            f"{self.generations} generations of {self.population}",
            f"seed {evaluate.cache[seed_genome.key()].completeness * 100:.1f}%"
            f" -> best {best.completeness * 100:.1f}% complete",
        ]
        return _finalise(self.name, best, meter, started, notes)


def seed_genomes(problem: RoutingProblem) -> list[Genome]:
    """Orders a human router would try first, in a fixed sequence.

    Every one of these is a classic ordering heuristic and they cost six
    evaluations between them, which is cheap next to the thousands a blind
    search would need to stumble on any of them. The search starts from the
    best of the six rather than from the arbitrary one the netlist happened to
    be written in."""
    nets = list(problem.routable_nets)
    count = len(nets)
    if count == 0:
        return [default_genome(0)]
    span: list[float] = []
    for net in nets:
        pads = problem.pads_of(net.id)
        if len(pads) < 2:
            span.append(0.0)
            continue
        xs = [p.center.x for p in pads]
        ys = [p.center.y for p in pads]
        span.append((max(xs) - min(xs)) + (max(ys) - min(ys)))

    def order_by(keyfn) -> tuple[int, ...]:
        return tuple(sorted(range(count), key=lambda i: (keyfn(i), nets[i].id)))

    orders = [
        tuple(range(count)),
        order_by(lambda i: span[i]),
        order_by(lambda i: -span[i]),
        order_by(lambda i: -len(nets[i].pads)),
        order_by(lambda i: len(nets[i].pads)),
        order_by(lambda i: (nets[i].priority, span[i])),
    ]
    seen: set[tuple[int, ...]] = set()
    out: list[Genome] = []
    for order in orders:
        if order in seen:
            continue
        seen.add(order)
        out.append(Genome(order, (0,) * count, (0,) * count))
    return out


def _random_genome(rng: random.Random, count: int) -> Genome:
    order = list(range(count))
    rng.shuffle(order)
    return Genome(
        order=tuple(order),
        layer=tuple(rng.randrange(2) for _ in range(count)),
        topo=tuple(rng.randrange(TOPOLOGIES) for _ in range(count)),
    )


def _tournament(rng: random.Random, scored: Sequence[Evaluation], size: int = 3) -> Evaluation:
    picks = [scored[rng.randrange(len(scored))] for _ in range(size)]
    return min(picks, key=lambda e: (e.key, e.genome.key()))


def _crossover(
    rng: random.Random, a: Genome, b: Genome, count: int
) -> Genome:
    """Order crossover on the permutation, uniform crossover on the rest."""
    if count < 2:
        return a
    start = rng.randrange(count)
    end = rng.randrange(count)
    if start > end:
        start, end = end, start
    child: list[int | None] = [None] * count
    taken = set()
    for i in range(start, end + 1):
        child[i] = a.order[i]
        taken.add(a.order[i])
    fill = [value for value in b.order if value not in taken]
    cursor = 0
    for i in range(count):
        if child[i] is None:
            child[i] = fill[cursor]
            cursor += 1
    layer = tuple(
        a.layer[i] if rng.random() < 0.5 else b.layer[i] for i in range(count)
    )
    topo = tuple(a.topo[i] if rng.random() < 0.5 else b.topo[i] for i in range(count))
    return Genome(tuple(int(v) for v in child), layer, topo)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


#: The family, by name. ``routerlib.cli.registry()`` is the tournament's
#: registry and this module deliberately does not edit it — a divergent
#: contract makes the tournament meaningless, and so does four agents editing
#: the same function. Merge with one line:
#: ``routers.update(algorithms.metaheuristic.ROUTERS)``.
ROUTERS = {
    GreedyMazeRouter.name: GreedyMazeRouter,
    PatternGreedyRouter.name: PatternGreedyRouter,
    AnnealRouter.name: AnnealRouter,
    GeneticRouter.name: GeneticRouter,
}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - a runner
    """Run the benchmark for one of this family's routers.

    ``python3.12 algorithms/metaheuristic.py --router meta-anneal``
    """
    import argparse
    import json
    from pathlib import Path

    from routerlib.bench import load_all, run_suite

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--router", default="meta-anneal", choices=sorted(ROUTERS))
    parser.add_argument("--only", default=None)
    parser.add_argument("--dir", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--evaluations", type=int, default=None)
    parser.add_argument("--max-iterations", type=int, default=2_000_000)
    parser.add_argument("--max-nodes", type=int, default=40_000_000)
    parser.add_argument(
        "--wall-clock-cap",
        type=float,
        default=7200.0,
        help=(
            "the hang valve, in seconds. Nothing scored depends on it; it is "
            "raised above the harness default because this machine is shared "
            "and a search starved by another agent's build is not a hang."
        ),
    )
    parser.add_argument("--no-determinism", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    factory = ROUTERS[args.router]
    if args.evaluations is not None and args.router == "meta-anneal":
        base = factory
        factory = lambda: base(evaluations=args.evaluations)  # noqa: E731

    problems = load_all(args.dir)
    if args.only:
        wanted = set(args.only.split(","))
        problems = [p for p in problems if p.id in wanted]
    budget = Budget(
        max_iterations=args.max_iterations,
        max_nodes=args.max_nodes,
        seed=args.seed,
        wall_clock_cap_s=args.wall_clock_cap,
    )
    report = run_suite(
        factory,
        problems,
        budget,
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


__all__ = [
    "AnnealRouter",
    "Evaluation",
    "Genome",
    "GeneticRouter",
    "GreedyMazeRouter",
    "InnerRouter",
    "PatternGreedyRouter",
    "ROUTERS",
    "TOPOLOGIES",
    "default_genome",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
