"""pathfinder-negotiated — negotiated congestion routing for two-layer boards.

The algorithm is McMurchie and Ebeling's PathFinder, the one that made FPGA
routing tractable. Its idea is one sentence long: **let every net take the route
it wants, then make the routes that are fought over expensive, and iterate until
nobody is fighting.**

The price a net pays to use a routing resource ``n`` is

    cost(n) = (base(n) + history(n)) * present(n)

* ``base`` is what the resource costs when nobody else wants it — here,
  millimetres of copper, or a flat charge for a layer change.
* ``present`` rises *within* a round with the number of other nets already on
  the resource. It is what makes a net that has an alternative go around.
* ``history`` rises *between* rounds, permanently, on every resource that was
  contended. It is the term people leave out, and leaving it out is why
  rip-up-and-retry thrashes: with only a present cost two nets swap places for
  ever. History remembers that a region was fought over even after the fight has
  moved, so the second-cheapest route becomes genuinely cheaper and the net that
  needs the region most keeps it.

Why it suits this problem: a greedy router commits net 1 before it knows what
net 40 needs, and no board has an order that is right for every net. PathFinder
never commits. Every round re-routes into the current price map, so the ordering
that mattered on the first pass has stopped mattering by the fourth.

## What a "resource" is on a PCB

An FPGA has discrete wires. A board has continuous copper, so the resources here
are cells of a uniform grid, one plane per layer — and the model has to carry
clearance or the negotiation converges happily on a short.

Two nets conflict when their centre lines run closer than
``w_i/2 + w_j/2 + clearance``. That is symmetric in the two widths, so the grid
splits it in half: a net **stamps** every cell within ``w_i/2 + clearance + h_q``
of the cells it occupies, once per query class ``q`` (``h_q`` is that class's
half width), and a net of class ``q`` then **reads a single cell**. Occupancy is
an exact statement about centre-line separation, and the read in the inner loop
of A* is one array index instead of a neighbourhood scan.

Everything a router may not move — pads, drills, keep-outs, the board edge — is
burnt into a static map before the first search, one map per half width. A cell
is ``FREE``, owned by exactly one net (the only net whose copper may go there),
or ``BLOCKED``. That map is where the rules the shipped autorouter cannot
represent actually live, and every number in it is read from ``DesignRules``
rather than transcribed: copper-to-hole is three different numbers depending on
whether the hole is a via, a component plated hole or a mounting hole, and a via
is refused inside an SMD pad because we do not buy filled-and-capped plating.

## Where the grid is wrong, and what stops that reaching the board

A model can lie in two directions.

*It can say yes when the answer is no.* Cell legality is measured exactly — the
distance from the cell centre to the obstacle is the real distance — but a
straight step between two legal cells sags towards an obstacle sitting between
them. Worst case for a diagonal step of ``p*sqrt(2)`` at the clearance limit is
about 0.07mm, which is more than the 0.047mm between our design target and the
fab floor. So every inflation carries ``sag = 0.3 * pitch`` on top, which covers
it — and **nothing is emitted on the grid's word.** Every trace and every via is
re-checked against :class:`~routerlib.workspace.Workspace`, the same geometry
the scorer grades with, and a net whose copper does not survive that check is
dropped and reported unrouted. An incomplete board scores badly. An illegal
board is a scrapped one.

*It can say no when the answer is yes*, and that only costs completeness. The
sag margin, the inscribed-stadium pad model and the finite pitch all round that
way on purpose.

## What it does that pattern routing cannot

* **A plane is a destination, not an obstacle.** For a net with a poured plane
  the target of the search is *any* cell of the pour on that layer, or any
  copper of the same net that already reached it — so ground pads share vias
  instead of each buying one. A pad already sitting in the pour is not routed at
  all.
* **Net classes reach the geometry.** Width comes from the net class, and where
  a rail cannot get into its own pad at rail width the stub — only the stub —
  necks down, which is what an EE does by hand.
* **Differential pairs are routed as a pair.** The second half pays a surcharge
  on every step that is not beside the first half.
* **Nothing is returned that the real gate has not passed.** After the copper is
  assembled it is scored with ``circuitpy.checks`` — the function that decides
  ``fab.ready`` — and if anything is illegal a whole net is dropped and it is
  scored again. Zero errors is a property of the output, not a claim about the
  model.
* **More compute cannot make it worse.** Negotiation is not monotone; a later
  round can price a net out of the only channel it had. The best round is kept.

## Honest limits

Written down here rather than discovered in the results table:

* Congestion is negotiated per cell, so two nets that want one channel resolve.
  A net with no legal escape from its own pad cannot be rescued by negotiation:
  that failure is static, and it is reported as static.
* Vias contend for the hole-to-hole rule as a **hard** block, not a negotiated
  one. It is the one place this router is order-dependent.
* Paths are 8-way grid paths with collinear runs merged. There is no
  shortcutting pass, so copper is a few per cent longer than the corridor
  allows. That is tier-3 quality, deliberately traded for not leaving the
  corridor the negotiation reserved.
* The scorer does not check copper against a poured plane (a real pour is carved
  around whatever is routed later), so this router treats plane copper as free
  space for other nets. If that check is ever added, the plane instances get
  worse, and they should.
* **The grid pitch is bounded by the search budget, not by the geometry.** A
  failed search expands every reachable cell, so halving the pitch quadruples
  the price of a failure — and on a congested board most late searches fail.
  Measured: 0.2mm pitch finishes 62% of five boards, 0.3mm finishes 88% of the
  same five in a third of the time. The router is therefore coarser than the
  0.1mm features it is routing around, and the boards where it leaves nets open
  are the boards where a finer grid would have helped. That is the ceiling on
  this implementation and the first thing to attack.
* Two nets that both need the same 0.6mm channel resolve; a net that needs a
  channel narrower than the grid can express never sees it.
"""

from __future__ import annotations

import heapq
import math
import time
from array import array
from dataclasses import dataclass, field
from typing import Sequence

from routerlib.geometry import (
    PolygonIndex,
    drill_capsule,
    pad_capsule,
    point_segment_distance,
    rect_capsule,
    stadium,
)
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

SQRT2 = math.sqrt(2.0)
LAYERS: tuple[str, str] = (TOP, BOTTOM)

#: Cell states in the static map. A positive value is ``net_index + 1``: the one
#: net whose copper may occupy that cell.
FREE = 0
BLOCKED = -1

# --- tuning -----------------------------------------------------------------
# Every one of these is a constant. None of them is a clock and none of them is
# a random draw, so the same problem gives the same board on any machine.

#: Grid pitch, and it is measured rather than reasoned: 0.2, 0.25, 0.3, 0.35 and
#: 0.4mm over five instances gave mean completeness 62 / 86 / **88** / 83 / 81
#: per cent, and 0.3mm was also two to three times faster than 0.25mm.
#:
#: The reason the finest grid loses is worth stating, because the instinct is
#: the other way round. A search that fails expands every reachable cell before
#: it gives up, so halving the pitch quadruples the price of a failure — and on
#: a congested board most searches in the late rounds fail. At 0.2mm the router
#: spent its whole node budget on four boards and finished 62% of them; at 0.3mm
#: it finished 88% and had budget to spare. Resolution the negotiation cannot
#: afford to use is not resolution.
DEFAULT_PITCH_MM = 0.30

#: Millimetres of copper a layer change is worth. High enough that a via is a
#: decision, low enough that the router takes one rather than fail.
VIA_COST_MM = 4.0

#: PathFinder's two schedules. ``present`` starts soft, so the first round is
#: close to shortest-path, and grows fast enough to converge inside the budget.
PRESENT_FACTOR_START = 0.6
PRESENT_FACTOR_GROWTH = 1.7
PRESENT_FACTOR_CAP = 400.0
#: Millimetres added to a resource's permanent price each round it is contended.
HISTORY_INCREMENT_MM = 0.4

#: Outer rounds. One round is a full re-route of every contended net.
DEFAULT_MAX_ROUNDS = 20

#: Rounds in a row a net may fail before it is set aside until the endgame.
MAX_FAIL_RETRIES = 3

#: Share of the node budget the negotiation rounds may spend; the rest is held
#: for the endgame. Negotiation that cannot pay for its own cleanup wastes the
#: rounds it bought: with no reserve, terminal-keyboard spent everything on
#: rounds, legalisation had nothing left to route with, and **ten nets routed on
#: the grid were dropped at final verification** because nobody had moved off
#: the cells they were sharing.
#:
#: The size of the reserve is measured and it is not "more is better".
#: terminal-keyboard finished 86.5% at a 35% reserve, 88.8% at none, and 89.9%
#: at 15% — with 107 vias instead of 134 and no width warnings. Rounds are worth
#: more than cleanup right up to the point where there is no cleanup at all.
NEGOTIATION_BUDGET_SHARE = 0.85

#: Self-imposed ceilings, below the harness defaults, because the harness
#: default of 20M expanded nodes is a number for a compiled router.
DEFAULT_NODE_BUDGET = 12_000_000
PER_SEARCH_NODE_CAP = 220_000

#: How far a stub may reach from a pad centre to find a legal grid cell.
ESCAPE_REACH_MM = 1.6
#: Entry cells kept per pad per layer, nearest first.
MAX_ENTRIES = 6
#: Exact-geometry stub checks attempted per pad per layer before giving up.
MAX_ENTRY_PROBES = 40

#: Extra inflation covering a straight step that sags towards an obstacle
#: between its two (individually legal) endpoints. See the preamble for where
#: 0.3 * pitch comes from: it covers the worst dip of a diagonal step at the
#: clearance limit, about 0.07mm at 0.25mm pitch.
#:
#: Two knobs, because the two cases could in principle differ — a pad is a
#: corner copper can curl around, two polylines cannot approach each other the
#: same way, and the looser net-to-net number packs tracks 0.4mm apart instead
#: of 0.447mm at 0.2mm pitch. **It was tried and it lost**: at a third of the
#: sag, mean completeness over four instances fell from 81% to 73%, because the
#: routes the tighter model found were then dropped at exact verification and a
#: dropped net costs more than a tight channel gains. Both stay at 0.3. The knob
#: is left in place because the result is worth being able to re-measure.
SAG_FRACTION_OF_PITCH = 0.3
DYNAMIC_SAG_FRACTION = 0.3

#: Keep vias clear of the unrotated ghost of a rotated pad. See the comment at
#: the marking site: it works around a rotation blind spot in the DFM gate.
AVOID_PHANTOM_PADS = True

#: Surcharge on a differential pair's second half for a step that is not beside
#: its partner, as a fraction of the step's base cost.
DIFF_PAIR_SURCHARGE = 0.35

#: Search-window margin around the terminals of one connection.
SEARCH_MARGIN_MM = 4.0
SEARCH_MARGIN_FRACTION = 0.35


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


class Grid:
    """A uniform lattice over the board, with a guard border.

    The border is not decoration. Stamping writes into a flat array by index
    offset, and without a margin wider than the widest stamp a write near the
    right-hand edge would land on the next row. Every cell a route can reach is
    at least ``guard`` cells inside the array boundary.
    """

    __slots__ = ("pitch", "x0", "y0", "nx", "ny", "n")

    def __init__(
        self, bbox: tuple[float, float, float, float], pitch: float, guard: int
    ) -> None:
        x0, y0, x1, y1 = bbox
        self.pitch = pitch
        self.x0 = x0 - guard * pitch
        self.y0 = y0 - guard * pitch
        self.nx = int(math.ceil((x1 - x0) / pitch)) + 2 * guard + 1
        self.ny = int(math.ceil((y1 - y0) / pitch)) + 2 * guard + 1
        self.n = self.nx * self.ny

    def cell_of(self, x: float, y: float) -> int:
        ix = int(round((x - self.x0) / self.pitch))
        iy = int(round((y - self.y0) / self.pitch))
        ix = 0 if ix < 0 else (self.nx - 1 if ix >= self.nx else ix)
        iy = 0 if iy < 0 else (self.ny - 1 if iy >= self.ny else iy)
        return iy * self.nx + ix

    def xy_of(self, cell: int) -> tuple[float, float]:
        iy, ix = divmod(cell, self.nx)
        return (self.x0 + ix * self.pitch, self.y0 + iy * self.pitch)

    def point_of(self, cell: int) -> Point:
        iy, ix = divmod(cell, self.nx)
        return Point(self.x0 + ix * self.pitch, self.y0 + iy * self.pitch)

    def cells_near(self, x: float, y: float, radius: float) -> list[tuple[float, int]]:
        """``(distance, cell)`` for every cell centre within ``radius``, sorted.

        The distance comes out of the scan rather than being recomputed by the
        caller: pad escape asks this question once per pad per layer and the
        recompute was the second-most expensive thing in setup.
        """
        p = self.pitch
        r2 = radius * radius
        out: list[tuple[float, int]] = []
        iy0 = max(0, int(math.floor((y - radius - self.y0) / p)))
        iy1 = min(self.ny - 1, int(math.ceil((y + radius - self.y0) / p)))
        for iy in range(iy0, iy1 + 1):
            dy = (self.y0 + iy * p) - y
            dy2 = dy * dy
            if dy2 > r2:
                continue
            span = math.sqrt(r2 - dy2)
            ix0 = max(0, int(math.floor((x - span - self.x0) / p)))
            ix1 = min(self.nx - 1, int(math.ceil((x + span - self.x0) / p)))
            row = iy * self.nx
            for ix in range(ix0, ix1 + 1):
                dx = (self.x0 + ix * p) - x
                d2 = dx * dx + dy2
                if d2 <= r2:
                    out.append((round(math.sqrt(d2), 6), row + ix))
        out.sort()
        return out

    def offsets_within(self, radius: float) -> tuple[int, ...]:
        """Flat index offsets for every cell centre within ``radius`` of a cell
        centre. Deterministic order, computed once per radius."""
        p = self.pitch
        reach = int(math.floor(radius / p))
        r2 = radius * radius
        out: list[int] = []
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                if (dx * p) ** 2 + (dy * p) ** 2 <= r2:
                    out.append(dy * self.nx + dx)
        return tuple(out)

    def offsets_annulus(self, lo: float, hi: float) -> tuple[int, ...]:
        p = self.pitch
        reach = int(math.floor(hi / p))
        lo2, hi2 = lo * lo, hi * hi
        out: list[int] = []
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                d2 = (dx * p) ** 2 + (dy * p) ** 2
                if lo2 <= d2 <= hi2:
                    out.append(dy * self.nx + dx)
        return tuple(out)


# ---------------------------------------------------------------------------
# Static obstacles
# ---------------------------------------------------------------------------


def _mark_capsule(arr: array, grid: Grid, capsule, extra: float, value: int) -> None:
    """Claim every cell whose centre is within ``extra`` of a capsule.

    ``value`` is ``BLOCKED`` for copper nobody may approach, or ``net + 1`` for
    copper only one net may approach. A cell claimed by two different nets
    becomes ``BLOCKED``: copper there is inside somebody's clearance whoever owns
    it.
    """
    ax, ay, bx, by, r = capsule
    reach = r + extra
    p = grid.pitch
    ix0 = max(0, int(math.floor((min(ax, bx) - reach - grid.x0) / p)))
    ix1 = min(grid.nx - 1, int(math.ceil((max(ax, bx) + reach - grid.x0) / p)))
    iy0 = max(0, int(math.floor((min(ay, by) - reach - grid.y0) / p)))
    iy1 = min(grid.ny - 1, int(math.ceil((max(ay, by) + reach - grid.y0) / p)))
    for iy in range(iy0, iy1 + 1):
        cy = grid.y0 + iy * p
        row = iy * grid.nx
        for ix in range(ix0, ix1 + 1):
            cx = grid.x0 + ix * p
            if point_segment_distance(cx, cy, ax, ay, bx, by) > reach:
                continue
            index = row + ix
            current = arr[index]
            if current == BLOCKED:
                continue
            if value == BLOCKED or (current != FREE and current != value):
                arr[index] = BLOCKED
            elif current == FREE:
                arr[index] = value


def _inside_mask(grid: Grid, outline: Sequence[Point]) -> bytearray:
    """1 for every cell centre inside a simple polygon, by scan line.

    One ray cast per grid row over only the edges spanning that row, rather than
    a point-in-polygon per cell: circuit.json tessellates a rounded rectangle
    into about a thousand edges and the big instance is 160k cells.
    """
    mask = bytearray(grid.n)
    if len(outline) < 3:
        return mask
    edges: list[tuple[float, float, float, float]] = []
    rows: dict[int, list[int]] = {}
    for i in range(len(outline)):
        a = outline[i]
        b = outline[(i + 1) % len(outline)]
        if a.y == b.y:
            continue
        index = len(edges)
        edges.append((a.x, a.y, b.x, b.y))
        lo = int(math.floor((min(a.y, b.y) - grid.y0) / grid.pitch))
        hi = int(math.ceil((max(a.y, b.y) - grid.y0) / grid.pitch))
        for row in range(max(0, lo), min(grid.ny - 1, hi) + 1):
            rows.setdefault(row, []).append(index)
    for iy in range(grid.ny):
        bucket = rows.get(iy)
        if not bucket:
            continue
        y = grid.y0 + iy * grid.pitch
        crossings: list[float] = []
        for index in bucket:
            x0, y0, x1, y1 = edges[index]
            if (y0 > y) != (y1 > y):
                crossings.append((x1 - x0) * (y - y0) / (y1 - y0) + x0)
        if len(crossings) < 2:
            continue
        crossings.sort()
        base = iy * grid.nx
        for k in range(0, len(crossings) - 1, 2):
            ix0 = max(0, int(math.ceil((crossings[k] - grid.x0) / grid.pitch)))
            ix1 = min(
                grid.nx - 1, int(math.floor((crossings[k + 1] - grid.x0) / grid.pitch))
            )
            for ix in range(ix0, ix1 + 1):
                mask[base + ix] = 1
    return mask


@dataclass(frozen=True)
class WarnBands:
    """The numbers the DFM gate warns at, which ``DesignRules`` does not carry.

    ``DesignRules`` exposes the *floors* — the distances that make a finding an
    error. The gate also has a band above each floor where the finding is a
    warning, and a warning is scored. Read from the profile rather than typed in
    here, and defaulted to the floor if the profile cannot be reached, so a
    missing pipeline degrades into "no warnings avoided", never into a wrong
    number.
    """

    pth_to_copper_mm: float
    edge_clearance_mm: float
    trace_mm: float

    @classmethod
    def of(cls, rules) -> "WarnBands":
        try:
            from circuitpy.fab import get_profile

            profile = get_profile(rules.profile_id)
            return cls(
                pth_to_copper_mm=float(profile.warn_pth_to_copper_mm),
                edge_clearance_mm=float(profile.warn_edge_clearance_mm),
                trace_mm=float(profile.warn_trace_mm),
            )
        except Exception:  # noqa: BLE001 - a probe must never fail a route
            return cls(
                pth_to_copper_mm=rules.min_pth_to_copper_mm,
                edge_clearance_mm=rules.min_edge_clearance_mm,
                trace_mm=rules.warn_trace_mm,
            )


@dataclass
class StaticField:
    """What the placement forbids, per half width and per layer.

    ``owner[half][layer][cell]`` is ``FREE``, ``BLOCKED`` or ``net + 1``.
    ``via_owner[layer]`` answers the same question for a via padstack, and it
    carries three rules a trace does not have: hole-to-hole spacing against
    every other drill, never inside an SMD pad, and the via's own drill
    clearance to copper.
    """

    owner: dict[float, list[array]]
    via_owner: list[array]
    plane_mask: dict[str, list[bytearray]]


def _blank(n: int) -> array:
    return array("i", bytes(4 * n))


def _apply_board_edge(
    maps: Sequence[array],
    grid: Grid,
    problem: RoutingProblem,
    inside: bytearray,
    has_outline: bool,
    extra: float,
) -> None:
    """Block everything off the board and everything too near its edge.

    Measured against the real outline, not the bounding box: a rounded corner on
    a 112mm board is most of a millimetre of copper the bounding box calls legal.
    """
    limit = problem.rules.min_edge_clearance_mm + extra
    if has_outline:
        for arr in maps:
            for index in range(grid.n):
                if not inside[index]:
                    arr[index] = BLOCKED
        outline = problem.board.outline
        for arr in maps:
            for i in range(len(outline)):
                a = outline[i]
                b = outline[(i + 1) % len(outline)]
                _mark_capsule(arr, grid, (a.x, a.y, b.x, b.y, 0.0), limit, BLOCKED)
        return
    x0, y0, x1, y1 = problem.board.bbox
    for arr in maps:
        for index in range(grid.n):
            cx, cy = grid.xy_of(index)
            if cx - x0 < limit or cy - y0 < limit or x1 - cx < limit or y1 - cy < limit:
                arr[index] = BLOCKED


def build_static(
    problem: RoutingProblem,
    grid: Grid,
    halves: Sequence[float],
    net_index: dict[str, int],
    sag: float,
    warn: WarnBands | None = None,
) -> StaticField:
    rules = problem.rules
    warn = warn or WarnBands.of(rules)
    clearance = rules.target_clearance_mm
    slot_of = {TOP: 0, BOTTOM: 1}
    inside = _inside_mask(grid, problem.board.outline)
    has_outline = len(problem.board.outline) >= 3

    owner: dict[float, list[array]] = {}
    for half in halves:
        maps = [_blank(grid.n), _blank(grid.n)]
        _apply_board_edge(maps, grid, problem, inside, has_outline, half + sag)
        owner[half] = maps

    via_half = rules.via_pad_mm / 2.0
    via_maps = [_blank(grid.n), _blank(grid.n)]
    # A via is one of the three element types the gate measures against the
    # board's bounding *rectangle*, and it warns at 0.3mm, not at the 0.2mm
    # floor. Vias near the rim are rare, so designing to the warn band is nearly
    # free and removes a whole class of finding.
    _apply_board_edge(
        via_maps,
        grid,
        problem,
        inside,
        has_outline,
        via_half + sag + (warn.edge_clearance_mm - rules.min_edge_clearance_mm),
    )

    def value_of(net: str | None) -> int:
        return net_index[net] + 1 if net in net_index else BLOCKED

    # Pads. Only a pad's own net may come within clearance of it; an unnetted
    # pad (a shell tab, a fiducial) is copper nobody may approach, never free
    # space.
    for pad in problem.pads:
        capsule = pad_capsule(pad)
        value = value_of(pad.net)
        for layer in pad.layers:
            slot = slot_of.get(layer)
            if slot is None:
                continue
            for half in halves:
                _mark_capsule(
                    owner[half][slot], grid, capsule, half + clearance + sag, value
                )
            _mark_capsule(
                via_maps[slot], grid, capsule, via_half + clearance + sag, value
            )
        if pad.is_smd:
            # A via drilled inside an SMD pad needs filled-and-capped plating we
            # do not order. Illegal for the pad's own net too, hence BLOCKED.
            for slot in (0, 1):
                _mark_capsule(
                    via_maps[slot],
                    grid,
                    capsule,
                    rules.via_drill_mm / 2.0 + sag,
                    BLOCKED,
                )
        # The phantom pad. ``circuitpy.checks`` does not read ``ccw_rotation``,
        # so its hole-clearance gate measures a 2.25 x 0.63mm pill turned 90
        # degrees as if it were lying flat — and a via 0.81mm clear of the real
        # pad is 0.105mm from the imaginary one and scores as an error. That is
        # a bug in the gate and the README says so, but the gate is the ruler
        # and it is also what decides fab.ready today, so a via keeps clear of
        # both shapes. Cost: a little routability beside rotated packages.
        # Delete this block the day the gate reads the field.
        if pad.rotation_deg % 180.0 and AVOID_PHANTOM_PADS:
            flat = stadium(pad.center.x, pad.center.y, pad.width_mm, pad.height_mm)
            for slot in (0, 1):
                _mark_capsule(
                    via_maps[slot],
                    grid,
                    flat,
                    rules.via_drill_mm / 2.0 + rules.min_via_to_copper_mm + sag,
                    value,
                )

    # Drills. Three different copper-to-hole numbers chosen by what the hole is;
    # the pipeline exempts the hole's own net on a plated hole, so we do too. A
    # *component* plated hole additionally warns up to 0.35mm, so that is what
    # copper is designed to; a via and a mounting hole have no warn band.
    for drill in problem.drills:
        capsule = drill_capsule(drill)
        needed = rules.hole_clearance(drill)
        if drill.plated and drill.pad_id is not None:
            needed = max(needed, warn.pth_to_copper_mm)
        value = value_of(drill.net) if drill.plated else BLOCKED
        for slot in (0, 1):
            for half in halves:
                _mark_capsule(
                    owner[half][slot], grid, capsule, half + needed + sag, value
                )
            _mark_capsule(via_maps[slot], grid, capsule, via_half + needed + sag, value)
            # Hole to hole is not a net question: two barrels this close break
            # into each other whatever they carry.
            _mark_capsule(
                via_maps[slot],
                grid,
                capsule,
                rules.via_drill_mm / 2.0 + rules.min_hole_to_hole_mm + sag,
                BLOCKED,
            )

    for keepout in problem.keepouts:
        capsule = rect_capsule(
            keepout.center.x, keepout.center.y, keepout.width_mm, keepout.height_mm
        )
        for layer in keepout.layers:
            slot = slot_of.get(layer)
            if slot is None:
                continue
            for half in halves:
                _mark_capsule(owner[half][slot], grid, capsule, half + sag, BLOCKED)
            _mark_capsule(via_maps[slot], grid, capsule, via_half + sag, BLOCKED)

    for trace in problem.existing_traces:
        slot = slot_of.get(trace.layer)
        if slot is None:
            continue
        value = value_of(trace.net)
        for a, b in trace.segments:
            capsule = (a.x, a.y, b.x, b.y, trace.width_mm / 2.0)
            for half in halves:
                _mark_capsule(
                    owner[half][slot], grid, capsule, half + clearance + sag, value
                )
            _mark_capsule(
                via_maps[slot], grid, capsule, via_half + clearance + sag, value
            )
    for via in problem.existing_vias:
        cx, cy = via.center.x, via.center.y
        pad_cap = (cx, cy, cx, cy, via.pad_mm / 2.0)
        hole_cap = (cx, cy, cx, cy, via.drill_mm / 2.0)
        value = value_of(via.net)
        for slot in (0, 1):
            for half in halves:
                _mark_capsule(
                    owner[half][slot], grid, pad_cap, half + clearance + sag, value
                )
                _mark_capsule(
                    owner[half][slot],
                    grid,
                    hole_cap,
                    half + rules.min_via_to_copper_mm + sag,
                    value,
                )
            _mark_capsule(
                via_maps[slot], grid, pad_cap, via_half + clearance + sag, value
            )
            _mark_capsule(
                via_maps[slot],
                grid,
                hole_cap,
                rules.via_drill_mm / 2.0 + rules.min_hole_to_hole_mm + sag,
                BLOCKED,
            )

    plane_mask: dict[str, list[bytearray]] = {}
    for plane in problem.planes:
        slot = slot_of.get(plane.layer)
        if slot is None:
            continue
        masks = plane_mask.setdefault(
            plane.net, [bytearray(grid.n), bytearray(grid.n)]
        )
        pour = _inside_mask(grid, plane.outline)
        for ring in plane.holes:
            for index, bit in enumerate(_inside_mask(grid, ring)):
                if bit:
                    pour[index] = 0
        target = masks[slot]
        for index, bit in enumerate(pour):
            if bit:
                target[index] = 1

    return StaticField(owner=owner, via_owner=via_maps, plane_mask=plane_mask)


# ---------------------------------------------------------------------------
# Per-net state
# ---------------------------------------------------------------------------


@dataclass
class _NetPlan:
    net: Net
    index: int
    width_mm: float
    half: float
    qclass: int
    pads: tuple[Pad, ...]
    #: pad id -> layer slot -> [(cell, stub width)], nearest cell first
    entries: dict[str, dict[int, list[tuple[int, float]]]]
    plane_layer: int | None = None
    #: Pads already connected by sitting in the pour. Never routed to.
    free_pads: frozenset[str] = frozenset()

    #: Grid cells the current route occupies, as ``layer * n + cell``.
    nodes: list[int] = field(default_factory=list)
    via_cells: list[int] = field(default_factory=list)
    #: (layer slot, [Point, ...], width) runs of copper, in emission order.
    runs: list[tuple[int, list[Point], float]] = field(default_factory=list)
    via_points: list[Point] = field(default_factory=list)
    #: (query class, layer) -> the cells this net claims, deduplicated. One net
    #: contributes exactly 1 to a cell however many of its own nodes cover it,
    #: which is what makes occupancy a count of *nets*.
    stamps: dict[tuple[int, int], tuple[int, ...]] = field(default_factory=dict)
    via_stamp: tuple[int, ...] = ()

    connected: bool = False
    congested: bool = True
    static_fail: bool = False
    necked: bool = False
    #: Rounds in a row this net has failed to close. A failed search is the most
    #: expensive kind — it expands the whole reachable grid before giving up —
    #: so a net that has failed three times stops being retried until the
    #: endgame, and its half-built copper is torn out so somebody else can use
    #: the room.
    fail_streak: int = 0

    @property
    def value(self) -> int:
        return self.index + 1


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


class PathFinderNegotiatedRouter:
    """Negotiated congestion routing. See the module docstring for the model."""

    name = "pathfinder-negotiated"

    def __init__(
        self,
        *,
        pitch_mm: float | None = None,
        max_rounds: int | None = None,
        neck_down: bool = True,
        static_sag_fraction: float = SAG_FRACTION_OF_PITCH,
        dynamic_sag_fraction: float = DYNAMIC_SAG_FRACTION,
    ) -> None:
        self.pitch_mm = pitch_mm
        self.max_rounds = max_rounds
        self.neck_down = neck_down
        self.static_sag_fraction = static_sag_fraction
        self.dynamic_sag_fraction = dynamic_sag_fraction

    # -- entry point -----------------------------------------------------

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        started = time.perf_counter()
        meter = budget.meter()
        self._notes: list[str] = []
        self._meter = meter
        self._problem = problem

        nets = [n for n in problem.nets if n.routable]
        if not nets:
            return RoutingSolution(
                router=self.name,
                complete=True,
                wall_clock_s=time.perf_counter() - started,
                notes=("no routable nets",),
            )

        rules = problem.rules
        pitch = self.pitch_mm or DEFAULT_PITCH_MM
        sag = self.static_sag_fraction * pitch
        self._pitch = pitch
        self._node_budget = min(budget.max_nodes, DEFAULT_NODE_BUDGET)

        net_index = {n.id: i for i, n in enumerate(nets)}
        widths = {n.id: max(n.min_width_mm, rules.min_trace_mm) for n in nets}
        halves = sorted({round(w / 2.0, 6) for w in widths.values()})
        # A rail may be rescued at signal width later, so its map has to exist.
        rescue_half = round(rules.signal_trace_mm / 2.0, 6)
        if self.neck_down and rescue_half not in halves:
            halves.append(rescue_half)
            halves.sort()
        qindex = {h: i for i, h in enumerate(halves)}

        widest = max([*halves, rules.via_pad_mm / 2.0])
        guard = (
            int(
                math.ceil(
                    (widest + rules.target_clearance_mm + max(halves) + sag) / pitch
                )
            )
            + 2
        )
        grid = Grid(problem.board.bbox, pitch, guard)
        self._grid = grid
        self._warn = WarnBands.of(rules)
        self._static = build_static(
            problem, grid, halves, net_index, sag, self._warn
        )

        # Occupancy: one plane per query class per layer. A net stamps what a
        # reader of class q must keep away from; a reader of class q then reads
        # exactly one cell.
        self._occ = [
            [_blank(grid.n), _blank(grid.n)] for _ in halves
        ]
        self._hist = [array("d", bytes(8 * grid.n)), array("d", bytes(8 * grid.n))]
        self._via_dyn = _blank(grid.n)

        sag_dyn = self.dynamic_sag_fraction * pitch
        cores = sorted({*halves, rules.via_pad_mm / 2.0})
        self._stamp_offsets = {
            (core, qi): grid.offsets_within(
                core + rules.target_clearance_mm + halves[qi] + sag_dyn
            )
            for core in cores
            for qi in range(len(halves))
        }
        self._via_core = rules.via_pad_mm / 2.0
        # Two vias of different nets have three separations to satisfy, and the
        # binding one is not the obvious one: hole to hole needs 0.50mm, hole to
        # the other pad needs 0.65mm, and pad to pad needs 0.747mm. Modelling
        # only hole-to-hole — the rule with "hole" in the name — leaves a via
        # pair 0.575mm apart that the gate calls a clearance error.
        self._via_dyn_offsets = grid.offsets_within(
            rules.via_pad_mm + rules.target_clearance_mm + sag_dyn
        )

        size = 2 * grid.n
        self._g = array("d", bytes(8 * size))
        self._came = array("i", bytes(4 * size))
        self._seen = array("i", bytes(4 * size))
        self._closed = array("i", bytes(4 * size))
        self._target = array("i", bytes(4 * size))
        self._run = 0
        self._anchor_slack = 0.0

        ws0 = Workspace(problem, clearance=rules.target_clearance_mm)
        plans = self._plan_nets(nets, net_index, widths, qindex, ws0)
        self._plans_by_net = {p.net.id: p for p in plans}
        order = _route_order(plans)

        rounds_limit = (
            self.max_rounds
            if self.max_rounds is not None
            else (
                budget.max_rip_up_passes
                if budget.max_rip_up_passes > 0
                else DEFAULT_MAX_ROUNDS
            )
        )

        present = PRESENT_FACTOR_START
        rounds = 0
        converged = False
        best = self._snapshot(order)
        best_key = self._standing(order)
        for _ in range(max(1, rounds_limit)):
            todo = [
                p
                for p in order
                if (p.congested or not p.connected)
                and not p.static_fail
                and p.fail_streak < MAX_FAIL_RETRIES
            ]
            if not todo:
                converged = not any(p.congested for p in order)
                break
            rounds += 1
            meter.tick()
            for plan in todo:
                # Check before tearing anything out. Running out of budget with
                # a net ripped up loses a route that was already paid for, which
                # is how a starved run came back with 28% of a board it had
                # routed 69% of.
                if self._spent(NEGOTIATION_BUDGET_SHARE):
                    break
                self._rip_up(plan)
                self._route_net(plan, present)
                if plan.connected:
                    plan.fail_streak = 0
                else:
                    plan.fail_streak += 1
                    if plan.fail_streak >= MAX_FAIL_RETRIES:
                        self._rip_up(plan)
            # Overuse is a property of the finished round, not of the moment a
            # net was routed: the net routed first has not seen what the net
            # routed last did to the map. Re-read every route against the
            # settled occupancy or half the contention stays invisible and the
            # negotiation quietly stops negotiating.
            shared = self._resweep(order)
            standing = self._standing(order)
            if standing > best_key:
                best_key = standing
                best = self._snapshot(order)
            if not shared:
                converged = True
                break
            if self._spent(NEGOTIATION_BUDGET_SHARE):
                self._notes.append(
                    f"stopped after {rounds} round(s) on the "
                    f"{meter.stop_reason or 'node'} budget, not on convergence"
                )
                break
            self._bump_history(order)
            present = min(PRESENT_FACTOR_CAP, present * PRESENT_FACTOR_GROWTH)

        # Negotiation is not monotone: a later round can price a net out of the
        # only channel it had. Measured on matrix-rp2040-core__usb-c-data, 24
        # rounds finished 81.0% of a board that 14 rounds finished 85.7% of.
        # Keeping the best round makes "spend more compute" a safe instruction,
        # which it has to be if the budget is ever going to be raised.
        if self._standing(order) < best_key:
            self._restore(order, best)
            self._notes.append(
                f"kept round {best_key[2]} — later rounds priced nets out of "
                "channels they had already won"
            )

        rescued = 0
        if self.neck_down:
            for plan in order:
                if plan.connected or plan.net.net_class not in ("power", "ground"):
                    continue
                if plan.width_mm <= rules.signal_trace_mm + 1e-9:
                    continue
                if self._retry_narrow(plan, qindex, ws0, present):
                    rescued += 1
                    self._resweep(order)

        legalised = self._legalise(order, present)
        return self._commit(order, rounds, converged, rescued, legalised, started)

    # -- endgame ---------------------------------------------------------

    def _spent(self, share: float = 1.0) -> bool:
        return (
            self._meter.exhausted
            or self._meter.nodes >= self._node_budget * share
        )

    def _standing(self, plans: Sequence[_NetPlan]) -> tuple[int, int, int]:
        """How good the board is right now. More closed nets first, then fewer
        nets still sharing a cell. The third element is the round, so the tuple
        is also a label."""
        return (
            sum(1 for p in plans if p.connected),
            -sum(1 for p in plans if p.congested),
            self._meter.iterations,
        )

    def _snapshot(self, plans: Sequence[_NetPlan]) -> list[tuple]:
        return [
            (
                p.connected,
                list(p.nodes),
                list(p.via_cells),
                list(p.runs),
                list(p.via_points),
                p.half,
                p.qclass,
                p.width_mm,
                p.necked,
                p.static_fail,
                p.fail_streak,
            )
            for p in plans
        ]

    def _restore(self, plans: Sequence[_NetPlan], snapshot: Sequence[tuple]) -> None:
        for plan in plans:
            self._rip_up(plan)
        for plan, state in zip(plans, snapshot):
            (
                plan.connected,
                plan.nodes,
                plan.via_cells,
                plan.runs,
                plan.via_points,
                plan.half,
                plan.qclass,
                plan.width_mm,
                plan.necked,
                plan.static_fail,
                plan.fail_streak,
            ) = state
            if plan.nodes or plan.via_cells:
                self._build_stamps(plan)
                self._apply(plan, +1)
        self._resweep(plans)

    def _is_congested(self, plan: _NetPlan) -> bool:
        occ = self._occ[plan.qclass]
        n = self._grid.n
        return any(occ[node // n][node % n] > 1 for node in plan.nodes)

    def _resweep(self, plans: Sequence[_NetPlan]) -> bool:
        """Recompute every net's overuse against the settled occupancy."""
        any_shared = False
        for plan in plans:
            plan.congested = self._is_congested(plan)
            any_shared = any_shared or plan.congested
        return any_shared

    def _legalise(self, plans: Sequence[_NetPlan], present: float) -> int:
        """The endgame: when negotiation runs out of rounds, stop negotiating.

        Whatever is still shared would be a short. Rather than emit it and let
        the exact check throw both nets away, contended nets are torn out **one
        at a time** and re-routed with occupancy as a hard block, in priority
        order. One at a time matters: tearing out both sides of a conflict makes
        both re-route into an empty channel and one of them will lose it again,
        whereas moving one net while the other stays put settles it in a single
        pass and cannot lose a route that was already legal.

        Nets that never connected are torn out first — their copper is blocking
        somebody for a connection that is not going to happen — and retried last,
        with the extra room.
        """
        for plan in plans:
            if not plan.connected and plan.nodes:
                self._rip_up(plan)
        self._resweep(plans)

        fixed = 0
        for plan in plans:
            if self._spent():
                break
            if not plan.connected or not self._is_congested(plan):
                continue
            self._rip_up(plan)
            self._route_net(plan, present, hard=True)
            if plan.connected:
                fixed += 1
        for plan in plans:
            if self._spent():
                break
            if plan.connected or plan.static_fail:
                continue
            self._route_net(plan, present, hard=True)
        self._resweep(plans)
        return fixed

    # -- planning --------------------------------------------------------

    def _plan_nets(
        self,
        nets: Sequence[Net],
        net_index: dict[str, int],
        widths: dict[str, float],
        qindex: dict[float, int],
        ws0: Workspace,
    ) -> list[_NetPlan]:
        problem = self._problem
        pads_by_id = problem.pads_by_id
        slot_of = {TOP: 0, BOTTOM: 1}
        planes_by_net: dict[str, list[Plane]] = {}
        for plane in problem.planes:
            planes_by_net.setdefault(plane.net, []).append(plane)
        pour_index = {p.id: PolygonIndex(p.outline) for p in problem.planes}

        plans: list[_NetPlan] = []
        for net in nets:
            width = widths[net.id]
            half = round(width / 2.0, 6)
            pads = tuple(pads_by_id[p] for p in net.pads if p in pads_by_id)
            plane_layer: int | None = None
            free_pads: set[str] = set()
            for plane in planes_by_net.get(net.id, ()):
                slot = slot_of.get(plane.layer)
                if slot is None:
                    continue
                plane_layer = slot
                shape = pour_index[plane.id]
                for pad in pads:
                    if plane.layer in pad.layers and shape.contains(
                        pad.center.x, pad.center.y
                    ):
                        free_pads.add(pad.id)
            plans.append(
                _NetPlan(
                    net=net,
                    index=net_index[net.id],
                    width_mm=width,
                    half=half,
                    qclass=qindex[half],
                    pads=pads,
                    entries={
                        pad.id: self._entries_for(
                            pad, net, net_index[net.id] + 1, width, half, ws0
                        )
                        for pad in pads
                    },
                    plane_layer=plane_layer,
                    free_pads=frozenset(free_pads),
                )
            )
        return plans

    def _entries_for(
        self,
        pad: Pad,
        net: Net,
        value: int,
        width: float,
        half: float,
        ws0: Workspace,
    ) -> dict[int, list[tuple[int, float]]]:
        """Legal grid cells this net can enter this pad from, nearest first.

        Each entry carries the width of the stub that reaches it. A rail whose
        pad is narrower than the rail necks the *stub* down rather than losing
        the pad — the move an EE makes by hand. It costs a
        ``dfm_power_trace_width`` warning and buys a connection, and the score
        ranks a connection above a warning.
        """
        grid = self._grid
        rules = self._problem.rules
        owner = self._static.owner[half]
        reach = max(pad.width_mm, pad.height_mm) / 2.0 + ESCAPE_REACH_MM
        stub_widths = [width]
        # Neck down to the pad, never below the gate's *recommended* width:
        # 0.10mm is legal and 0.15mm is warning-free, and a stub that has to be
        # thinner than 0.15mm was not going to fit anyway.
        narrow = round(max(self._warn.trace_mm, min(pad.width_mm, pad.height_mm)), 6)
        if self.neck_down and narrow < width - 1e-9:
            stub_widths.append(narrow)

        out: dict[int, list[tuple[int, float]]] = {}
        candidates = grid.cells_near(pad.center.x, pad.center.y, reach)
        for layer in pad.layers:
            slot = 0 if layer == TOP else 1
            found: list[tuple[int, float]] = []
            probes = 0
            for _, cell in candidates:
                if len(found) >= MAX_ENTRIES or probes >= MAX_ENTRY_PROBES:
                    break
                state = owner[slot][cell]
                if state != FREE and state != value:
                    continue
                probes += 1
                point = grid.point_of(cell)
                if point == pad.center:
                    found.append((cell, stub_widths[0]))
                    continue
                for stub_width in stub_widths:
                    if (
                        ws0.segment_ok(layer, pad.center, point, stub_width, net.id)
                        is True
                    ):
                        found.append((cell, stub_width))
                        break
            if found:
                out[slot] = found
        return out

    # -- occupancy -------------------------------------------------------

    def _build_stamps(self, plan: _NetPlan) -> None:
        """The cells this net claims, deduplicated per class and layer.

        Deduplication is the whole point. Neighbouring cells of one net have
        overlapping clearance discs, so counting raw writes would make a net
        collide with itself and every occupancy reading would be fiction.
        """
        n = self._grid.n
        via_offsets = None
        stamps: dict[tuple[int, int], tuple[int, ...]] = {}
        for qi in range(len(self._occ)):
            offsets = self._stamp_offsets[(plan.half, qi)]
            via_offsets = self._stamp_offsets[(self._via_core, qi)]
            per_layer: list[set[int]] = [set(), set()]
            for node in plan.nodes:
                layer, cell = divmod(node, n)
                claimed = per_layer[layer]
                for off in offsets:
                    claimed.add(cell + off)
            for cell in plan.via_cells:
                for layer in (0, 1):
                    claimed = per_layer[layer]
                    for off in via_offsets:
                        claimed.add(cell + off)
            for layer in (0, 1):
                stamps[(qi, layer)] = tuple(per_layer[layer])
        plan.stamps = stamps
        blocked: set[int] = set()
        for cell in plan.via_cells:
            for off in self._via_dyn_offsets:
                blocked.add(cell + off)
        plan.via_stamp = tuple(blocked)

    def _apply(self, plan: _NetPlan, sign: int) -> None:
        occ = self._occ
        for (qi, layer), cells in plan.stamps.items():
            arr = occ[qi][layer]
            for cell in cells:
                arr[cell] += sign
        via_dyn = self._via_dyn
        for cell in plan.via_stamp:
            via_dyn[cell] += sign

    def _rip_up(self, plan: _NetPlan) -> None:
        if plan.stamps or plan.via_stamp:
            self._apply(plan, -1)
        plan.stamps = {}
        plan.via_stamp = ()
        plan.nodes = []
        plan.via_cells = []
        plan.runs = []
        plan.via_points = []

    def _bump_history(self, plans: Sequence[_NetPlan]) -> None:
        n = self._grid.n
        for plan in plans:
            if not plan.congested:
                continue
            occ = self._occ[plan.qclass]
            for node in plan.nodes:
                layer, cell = divmod(node, n)
                if occ[layer][cell] > 1:
                    self._hist[layer][cell] += HISTORY_INCREMENT_MM

    # -- one net ---------------------------------------------------------

    def _route_net(self, plan: _NetPlan, present: float, *, hard: bool = False) -> None:
        pads = [p for p in plan.pads if p.id not in plan.free_pads]
        reachable = [p for p in pads if plan.entries.get(p.id)]
        if len(reachable) < len(pads):
            if not plan.static_fail:
                plan.static_fail = True
                self._notes.append(
                    f"{plan.net.name}: {len(pads) - len(reachable)} pad(s) have no "
                    f"legal escape at {plan.width_mm:g}mm — a placement limit, "
                    "not congestion"
                )
            plan.connected = False
            plan.congested = False
            return

        paths: list[tuple[Pad | None, Pad | None, list[int]]] = []
        if plan.plane_layer is not None:
            # A pad reaches the pour, or it reaches copper that already reached
            # the pour. Routing every ground pad to its own private via is the
            # obvious version and the wrong one: it spends a via and 0.75mm of
            # exclusion per pad, and on a poured keyboard that was 112 vias
            # against 89 for the same board with no pour at all.
            plane = self._static.plane_mask.get(plan.net.id)
            tree: list[int] = []
            ok = True
            for pad in reachable:
                path = self._search(
                    plan,
                    self._entry_nodes(plan, pad),
                    tree or None,
                    plane,
                    present,
                    None,
                    hard=hard,
                )
                if path is None:
                    ok = False
                    continue
                paths.append((pad, None, path))
                tree.extend(path)
            plan.connected = ok and (bool(paths) or not reachable)
        elif len(reachable) < 2:
            plan.connected = True
        else:
            root = reachable[0]
            tree = list(self._entry_nodes(plan, root))
            joined = [root]
            pending = list(reachable[1:])
            ok = True
            while pending:
                sink = min(
                    pending,
                    key=lambda p: (
                        round(min(p.center.distance_to(j.center) for j in joined), 6),
                        p.id,
                    ),
                )
                pending.remove(sink)
                path = self._search(
                    plan,
                    tree,
                    self._entry_nodes(plan, sink),
                    None,
                    present,
                    (joined, sink),
                    hard=hard,
                )
                if path is None:
                    ok = False
                    continue
                paths.append((None, sink, path))
                tree.extend(path)
                joined.append(sink)
            plan.connected = ok

        self._materialise(plan, paths)

    def _entry_nodes(self, plan: _NetPlan, pad: Pad) -> list[int]:
        n = self._grid.n
        return [
            slot * n + cell
            for slot, cells in sorted(plan.entries.get(pad.id, {}).items())
            for cell, _ in cells
        ]

    def _stub_for(self, plan: _NetPlan, node: int) -> tuple[Pad, float] | None:
        """The pad a source node belongs to, if entering there needs a stub."""
        n = self._grid.n
        slot, cell = divmod(node, n)
        for pad in plan.pads:
            for entry_cell, width in plan.entries.get(pad.id, {}).get(slot, ()):
                if entry_cell == cell:
                    return (pad, width)
        return None

    def _materialise(
        self, plan: _NetPlan, paths: Sequence[tuple[Pad | None, Pad | None, list[int]]]
    ) -> None:
        """Grid paths become runs of copper, then the net is stamped back in.

        A run is closed at every layer change and a via is placed there; the pad
        centre is stitched on at each end with its own (possibly necked) width,
        because a trace carries one width and a neck-down is a different width.
        """
        grid = self._grid
        n = grid.n
        runs: list[tuple[int, list[Point], float]] = []
        vias: list[Point] = []
        nodes: list[int] = []
        via_cells: list[int] = []
        emitted: set[int] = set()

        for _, sink, path in paths:
            nodes.extend(path)
            start_stub = None if path[0] in emitted else self._stub_for(plan, path[0])
            layer = path[0] // n
            current = [grid.point_of(path[0] % n)]
            for a, b in zip(path, path[1:]):
                la, ca = divmod(a, n)
                lb, cb = divmod(b, n)
                if ca == cb and la != lb:
                    vias.append(grid.point_of(ca))
                    via_cells.append(ca)
                    runs.append((la, current, plan.width_mm))
                    current = [grid.point_of(cb)]
                    layer = lb
                else:
                    current.append(grid.point_of(cb))
                    layer = lb
            runs.append((layer, current, plan.width_mm))
            if start_stub is not None:
                pad, width = start_stub
                if pad.center != grid.point_of(path[0] % n):
                    runs.append(
                        (
                            path[0] // n,
                            [pad.center, grid.point_of(path[0] % n)],
                            width,
                        )
                    )
            if sink is not None:
                end_slot, end_cell = divmod(path[-1], n)
                width = next(
                    (
                        w
                        for cell, w in plan.entries.get(sink.id, {}).get(end_slot, ())
                        if cell == end_cell
                    ),
                    plan.width_mm,
                )
                if sink.center != grid.point_of(end_cell):
                    runs.append(
                        (end_slot, [grid.point_of(end_cell), sink.center], width)
                    )
            emitted.update(path)

        plan.runs = [
            (layer, _merge_collinear(points), width)
            for layer, points, width in runs
            if len(points) >= 2
        ]
        plan.via_points = vias
        plan.nodes = sorted(set(nodes))
        plan.via_cells = sorted(set(via_cells))

        occ = self._occ[plan.qclass]
        plan.congested = any(occ[node // n][node % n] > 0 for node in plan.nodes)
        self._build_stamps(plan)
        self._apply(plan, +1)

    # -- A* --------------------------------------------------------------

    def _search(
        self,
        plan: _NetPlan,
        sources: Sequence[int],
        targets: Sequence[int] | None,
        plane: list[bytearray] | None,
        present: float,
        focus: tuple[Sequence[Pad], Pad] | None,
        *,
        hard: bool = False,
    ) -> list[int] | None:
        """Multi-source, multi-target A* over ``(layer, cell)``.

        Sources are the net's own copper at zero cost, which is what turns a
        sequence of two-terminal searches into a tree: the second sink routes to
        whatever the first one built, not back to the pad.
        """
        if not sources:
            return None
        window = self._window(focus, targets)
        path = self._search_in(plan, sources, targets, plane, present, window, hard)
        if path is None and window is not None:
            # The window is a speed device only, so a failure inside it is
            # always retried over the whole board before it becomes an answer.
            path = self._search_in(plan, sources, targets, plane, present, None, hard)
        return path

    def _window(
        self, focus: tuple[Sequence[Pad], Pad] | None, targets: Sequence[int] | None
    ) -> tuple[int, int, int, int] | None:
        if focus is None or targets is None:
            return None
        joined, sink = focus
        xs = [p.center.x for p in joined] + [sink.center.x]
        ys = [p.center.y for p in joined] + [sink.center.y]
        margin = max(
            SEARCH_MARGIN_MM,
            SEARCH_MARGIN_FRACTION * math.hypot(max(xs) - min(xs), max(ys) - min(ys)),
        )
        grid = self._grid
        return (
            max(0, int((min(xs) - margin - grid.x0) / grid.pitch)),
            min(grid.nx - 1, int((max(xs) + margin - grid.x0) / grid.pitch) + 1),
            max(0, int((min(ys) - margin - grid.y0) / grid.pitch)),
            min(grid.ny - 1, int((max(ys) + margin - grid.y0) / grid.pitch) + 1),
        )

    def _search_in(
        self,
        plan: _NetPlan,
        sources: Sequence[int],
        targets: Sequence[int] | None,
        plane: list[bytearray] | None,
        present: float,
        window: tuple[int, int, int, int] | None,
        hard: bool,
    ) -> list[int] | None:
        grid = self._grid
        n = grid.n
        nx = grid.nx
        x0, y0, pitch = grid.x0, grid.y0, grid.pitch
        diag = pitch * SQRT2
        meter = self._meter

        self._run += 1
        run = self._run
        g, came, seen, closed, tgt = (
            self._g,
            self._came,
            self._seen,
            self._closed,
            self._target,
        )
        owner = self._static.owner[plan.half]
        occ = self._occ[plan.qclass]
        hist = self._hist
        via_dyn = self._via_dyn
        value = plan.value
        top_owner, bottom_owner = owner[0], owner[1]
        via_top, via_bottom = self._static.via_owner

        if window is None:
            ix0, ix1, iy0, iy1 = 0, nx - 1, 0, grid.ny - 1
        else:
            ix0, ix1, iy0, iy1 = window

        anchor_x = anchor_y = 0.0
        slack = 0.0
        have_targets = False
        if targets is not None:
            live = []
            for node in targets:
                layer, cell = divmod(node, n)
                iy, ix = divmod(cell, nx)
                if not (ix0 <= ix <= ix1 and iy0 <= iy <= iy1):
                    continue
                if hard and occ[layer][cell]:
                    continue
                tgt[node] = run
                live.append(node)
            if not live and plane is None:
                return None
            have_targets = bool(live)
            if live:
                pts = [grid.xy_of(node % n) for node in live]
                anchor_x = sum(p[0] for p in pts) / len(pts)
                anchor_y = sum(p[1] for p in pts) / len(pts)
                slack = max(
                    math.hypot(p[0] - anchor_x, p[1] - anchor_y) for p in pts
                )
        # A pour covers most of the board, so any estimate of the distance to it
        # is either zero or wrong. With a plane in play the search is a plain
        # Dijkstra and finds the nearest legal crossing rather than guessing.
        guided = have_targets and plane is None

        attract = self._attract_set(plan)
        heap: list[tuple[float, int]] = []
        for node in sources:
            layer, cell = divmod(node, n)
            iy, ix = divmod(cell, nx)
            if not (ix0 <= ix <= ix1 and iy0 <= iy <= iy1):
                continue
            if hard and occ[layer][cell]:
                continue
            if seen[node] == run:
                continue
            seen[node] = run
            g[node] = 0.0
            came[node] = -1
            if guided:
                dx = abs(x0 + ix * pitch - anchor_x)
                dy = abs(y0 + iy * pitch - anchor_y)
                lo, hi = (dx, dy) if dx < dy else (dy, dx)
                h = hi + (SQRT2 - 1.0) * lo - slack
                heapq.heappush(heap, (h if h > 0.0 else 0.0, node))
            else:
                heapq.heappush(heap, (0.0, node))
        if not heap:
            return None

        moves = (
            (1, 0, pitch),
            (-1, 0, pitch),
            (0, 1, pitch),
            (0, -1, pitch),
            (1, 1, diag),
            (1, -1, diag),
            (-1, 1, diag),
            (-1, -1, diag),
        )
        # A* never expands a node twice, so a hopeless search is bounded by the
        # grid whatever the cap says. The budget is checked here as well as
        # between rounds because one search over a fine grid can be the whole
        # round's worth of nodes.
        cap = min(PER_SEARCH_NODE_CAP, 2 * n, max(1, self._node_budget - meter.nodes))
        expanded = 0
        found: int | None = None
        while heap:
            _, node = heapq.heappop(heap)
            if closed[node] == run:
                continue
            closed[node] = run
            expanded += 1
            if expanded >= cap:
                break
            layer, cell = divmod(node, n)
            if have_targets and tgt[node] == run:
                found = node
                break
            if plane is not None and plane[layer][cell]:
                found = node
                break

            base = g[node]
            iy, ix = divmod(cell, nx)
            layer_owner = top_owner if layer == 0 else bottom_owner
            layer_occ = occ[layer]
            layer_hist = hist[layer]
            for dx_i, dy_i, step in moves:
                jx = ix + dx_i
                if jx < ix0 or jx > ix1:
                    continue
                jy = iy + dy_i
                if jy < iy0 or jy > iy1:
                    continue
                ncell = jy * nx + jx
                state = layer_owner[ncell]
                if state != FREE and state != value:
                    continue
                if hard and layer_occ[ncell]:
                    continue
                nnode = layer * n + ncell
                if closed[nnode] == run:
                    continue
                cost = step
                if attract is not None and ncell not in attract:
                    cost += step * DIFF_PAIR_SURCHARGE
                ng = base + (cost + layer_hist[ncell]) * (
                    1.0 + present * layer_occ[ncell]
                )
                if seen[nnode] == run and g[nnode] <= ng:
                    continue
                seen[nnode] = run
                g[nnode] = ng
                came[nnode] = node
                if guided:
                    ax = abs(x0 + jx * pitch - anchor_x)
                    ay = abs(y0 + jy * pitch - anchor_y)
                    lo, hi = (ax, ay) if ax < ay else (ay, ax)
                    h = hi + (SQRT2 - 1.0) * lo - slack
                    heapq.heappush(heap, (ng + (h if h > 0.0 else 0.0), nnode))
                else:
                    heapq.heappush(heap, (ng, nnode))

            # Layer change. Hole-to-hole is a hard rule, not a negotiated one.
            via_a = via_top[cell]
            via_b = via_bottom[cell]
            if (
                via_dyn[cell] == 0
                and (via_a == FREE or via_a == value)
                and (via_b == FREE or via_b == value)
            ):
                other = 1 - layer
                nnode = other * n + cell
                if closed[nnode] != run and not (hard and occ[other][cell]):
                    ng = base + (VIA_COST_MM + hist[other][cell]) * (
                        1.0 + present * occ[other][cell]
                    )
                    if not (seen[nnode] == run and g[nnode] <= ng):
                        seen[nnode] = run
                        g[nnode] = ng
                        came[nnode] = node
                        if guided:
                            ax = abs(x0 + ix * pitch - anchor_x)
                            ay = abs(y0 + iy * pitch - anchor_y)
                            lo, hi = (ax, ay) if ax < ay else (ay, ax)
                            h = hi + (SQRT2 - 1.0) * lo - slack
                            heapq.heappush(heap, (ng + (h if h > 0.0 else 0.0), nnode))
                        else:
                            heapq.heappush(heap, (ng, nnode))

        meter.expand(expanded)
        if found is None:
            return None
        path = [found]
        while came[path[-1]] != -1:
            path.append(came[path[-1]])
        path.reverse()
        return path

    def _attract_set(self, plan: _NetPlan) -> set[int] | None:
        """Cells beside an already-routed differential partner.

        The pair cannot run closer than clearance allows, so the band starts
        where the occupancy stamp ends and reaches the scorer's coupling window.
        """
        partner_id = plan.net.diff_partner
        if not partner_id:
            return None
        partner = self._plans_by_net.get(partner_id)
        if partner is None or not partner.nodes:
            return None
        rules = self._problem.rules
        lo = plan.half + partner.half + rules.target_clearance_mm
        hi = rules.diff_pair_gap_mm * 3.0
        if hi <= lo:
            return None
        offsets = self._grid.offsets_annulus(lo, hi)
        n = self._grid.n
        out: set[int] = set()
        for node in partner.nodes:
            cell = node % n
            for off in offsets:
                out.add(cell + off)
        return out

    # -- rescue ----------------------------------------------------------

    def _retry_narrow(
        self,
        plan: _NetPlan,
        qindex: dict[float, int],
        ws0: Workspace,
        present: float,
    ) -> bool:
        """Re-route a rail at signal width when rail width has no room.

        A real trade, and it shows up in the score: the net earns a
        ``dfm_power_trace_width`` warning and the board gains a connection.
        Completeness outranks warnings, so it is the right way round — but it is
        why a board from this router can carry a warning the pattern baseline
        does not.
        """
        rules = self._problem.rules
        narrow = round(rules.signal_trace_mm / 2.0, 6)
        if narrow not in self._static.owner or narrow not in qindex:
            return False
        self._rip_up(plan)
        plan.half = narrow
        plan.qclass = qindex[narrow]
        plan.width_mm = rules.signal_trace_mm
        plan.necked = True
        plan.static_fail = False
        plan.entries = {
            pad.id: self._entries_for(
                pad, plan.net, plan.value, plan.width_mm, narrow, ws0
            )
            for pad in plan.pads
        }
        self._route_net(plan, present)
        return plan.connected

    # -- emit ------------------------------------------------------------

    def _commit(
        self,
        plans: Sequence[_NetPlan],
        rounds: int,
        converged: bool,
        rescued: int,
        legalised: int,
        started: float,
    ) -> RoutingSolution:
        """Turn grid paths into copper, and refuse to emit any of it on trust.

        Each net is verified against the real geometry with everything already
        committed in place. A net that fails at the design target is retried at
        the fab floor — legal, above the gate, and reported — and a net that
        fails there too is dropped whole. Copper you cannot defend is worse than
        no copper.
        """
        problem = self._problem
        rules = problem.rules
        ws = Workspace(problem, clearance=rules.target_clearance_mm)
        traces: list[Trace] = []
        vias: list[Via] = []
        unrouted: list[str] = []
        via_seq = 0
        dropped = 0
        at_floor = 0

        for plan in plans:
            if not plan.connected:
                unrouted.append(plan.net.id)
                continue
            plan_traces = [
                Trace(
                    id=f"{plan.net.id}~{index}",
                    net=plan.net.id,
                    layer=LAYERS[layer],
                    points=tuple(points),
                    width_mm=width,
                )
                for index, (layer, points, width) in enumerate(plan.runs)
            ]
            ok = self._verify(
                ws, plan, plan_traces, plan.via_points, rules.target_clearance_mm
            )
            level = "target"
            if not ok:
                ok = self._verify(
                    ws, plan, plan_traces, plan.via_points, rules.min_clearance_mm
                )
                level = "floor"
            if not ok:
                dropped += 1
                unrouted.append(plan.net.id)
                continue
            if level == "floor":
                at_floor += 1
            for trace in plan_traces:
                ws.commit_trace(trace)
                traces.append(trace)
            for point in plan.via_points:
                via = Via(
                    id=f"pfv{via_seq}",
                    net=plan.net.id,
                    center=point,
                    drill_mm=rules.via_drill_mm,
                    pad_mm=rules.via_pad_mm,
                )
                via_seq += 1
                ws.commit_via(via)
                vias.append(via)

        traces, vias, unrouted, audited, remaining = _audit(
            problem, traces, vias, unrouted
        )

        notes = [
            f"pitch {self._pitch:g}mm, {rounds} negotiation round(s), "
            + ("no shared cells left" if converged else "did not converge"),
            *self._notes,
        ]
        if audited:
            notes.append(
                f"final audit against circuitpy.checks dropped {audited} net(s) "
                "whose copper the gate called illegal — the grid model and the "
                "gate disagreed and the gate wins"
            )
        if remaining:
            notes.append(
                f"AUDIT FAILED: {remaining} DRC error(s) survive. This board is "
                "not buildable and the score should say so"
            )
        if legalised:
            notes.append(
                f"{legalised} net(s) re-routed with congestion as a hard block "
                "after the rounds ran out"
            )
        if rescued:
            notes.append(
                f"{rescued} rail(s) re-routed at signal width to close the net — "
                "each earns a dfm_power_trace_width warning on purpose"
            )
        necked = sum(1 for p in plans if p.necked)
        if necked:
            notes.append(f"{necked} net(s) carry a necked-down stub into a pad")
        if at_floor:
            notes.append(
                f"{at_floor} net(s) verified at the {rules.min_clearance_mm:g}mm fab "
                f"floor rather than the {rules.target_clearance_mm:g}mm design target"
            )
        if dropped:
            notes.append(
                f"{dropped} net(s) routed on the grid but dropped at exact "
                "verification — the grid said yes and the geometry said no"
            )
        if unrouted:
            walled = sum(1 for p in plans if p.static_fail)
            notes.append(
                f"{len(set(unrouted))} net(s) left open: {walled} with no legal "
                f"escape from a pad, {len(set(unrouted)) - walled} with no legal "
                "path between pads that already have one"
            )

        return RoutingSolution(
            router=self.name,
            traces=tuple(traces),
            vias=tuple(vias),
            complete=not unrouted,
            unrouted_nets=tuple(sorted(set(unrouted))),
            iterations=rounds,
            nodes_expanded=self._meter.nodes,
            wall_clock_s=time.perf_counter() - started,
            notes=tuple(notes),
        )

    def _verify(
        self,
        ws: Workspace,
        plan: _NetPlan,
        traces: Sequence[Trace],
        via_points: Sequence[Point],
        clearance: float,
    ) -> bool:
        previous = ws.clearance
        ws.clearance = clearance
        try:
            for point in via_points:
                if ws.via_ok(point, plan.net.id) is not True:
                    return False
            for trace in traces:
                if (
                    ws.path_ok(trace.layer, trace.points, trace.width_mm, plan.net.id)
                    is not True
                ):
                    return False
            return True
        finally:
            ws.clearance = previous


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


#: Nets the audit may sacrifice before it gives up and reports the errors.
MAX_AUDIT_DROPS = 8


def _audit(
    problem: RoutingProblem,
    traces: list[Trace],
    vias: list[Via],
    unrouted: list[str],
) -> tuple[list[Trace], list[Via], list[str], int, int]:
    """Score the copper with the real scorer before returning it.

    Everything above this line is a model of the rules. This is the rules. The
    router has already refused anything :class:`Workspace` rejects, but
    ``Workspace`` and ``circuitpy.checks`` are not the same function and the
    places they differ are exactly the places a router quietly ships a defect —
    ``via_ok``, for one, never measures the via's own drill against other
    copper, which the gate does at 0.20mm.

    So: run the gate. If it finds an error, drop a whole net — the last one
    committed, because the earlier ones were legal when they went down — and run
    it again. Give up after a few and say loudly that errors survive. Dropping a
    connection is a bad board; keeping a short is a scrapped one.

    Attribution is deliberately crude: pipeline findings name a component, not a
    net, so there is no honest way to blame the right net from the finding
    alone. Reverse commit order terminates and never keeps an error, which are
    the two properties that matter. In a converged run this loop does nothing.
    """
    from routerlib import drc as drc_mod

    dropped = 0
    order: list[str] = []
    for trace in traces:
        if trace.net not in order:
            order.append(trace.net)
    for _ in range(MAX_AUDIT_DROPS + 1):
        probe = RoutingSolution(
            router="audit", traces=tuple(traces), vias=tuple(vias)
        )
        errors = drc_mod.check(problem, probe).errors
        if not errors:
            return traces, vias, unrouted, dropped, 0
        if not order:
            break
        victim = order.pop()
        traces = [t for t in traces if t.net != victim]
        vias = [v for v in vias if v.net != victim]
        unrouted.append(victim)
        dropped += 1
    probe = RoutingSolution(router="audit", traces=tuple(traces), vias=tuple(vias))
    remaining = len(drc_mod.check(problem, probe).errors)
    return traces, vias, unrouted, dropped, remaining


def _merge_collinear(points: Sequence[Point]) -> list[Point]:
    """Drop every point that sits on the straight line between its neighbours.

    An 8-way grid path is mostly long straight and diagonal runs; keeping every
    cell centre would put a thousand vertices in a trace that has four corners.
    Zero geometric change, so it cannot invalidate a check that already passed.
    """
    if len(points) < 3:
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


def _route_order(plans: Sequence[_NetPlan]) -> list[_NetPlan]:
    """Rails, then pairs, then signals — with a pair's two halves adjacent, so
    the second half is routed while the first is fresh copper to hug."""
    by_id = {p.net.id: p for p in plans}
    out: list[_NetPlan] = []
    placed: set[str] = set()
    for plan in plans:
        if plan.net.id in placed:
            continue
        out.append(plan)
        placed.add(plan.net.id)
        partner = plan.net.diff_partner
        if partner and partner in by_id and partner not in placed:
            out.append(by_id[partner])
            placed.add(partner)
    return out


#: The registry entry. ``routerlib.cli.registry()`` belongs to the harness and is
#: deliberately not edited from here — the tournament host adds one line, or runs
#: this module directly (``python3.12 pathfinder-negotiated.py --help``).
ROUTERS = {PathFinderNegotiatedRouter.name: PathFinderNegotiatedRouter}


__all__ = ["Grid", "PathFinderNegotiatedRouter", "ROUTERS", "build_static"]


# ---------------------------------------------------------------------------
# Runner: the benchmark, without editing the harness
# ---------------------------------------------------------------------------


def _main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse
    import json
    from pathlib import Path

    from routerlib.bench import load_all, run_suite

    parser = argparse.ArgumentParser(description="run pathfinder-negotiated")
    parser.add_argument("--dir", default=None)
    parser.add_argument("--only", default=None)
    parser.add_argument("--pitch", type=float, default=None)
    parser.add_argument("--rounds", type=int, default=None)
    parser.add_argument("--max-nodes", type=int, default=20_000_000)
    parser.add_argument("--max-iterations", type=int, default=2_000_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-determinism", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    problems = load_all(args.dir)
    if args.only:
        wanted = set(args.only.split(","))
        problems = [p for p in problems if p.id in wanted]

    def factory():
        return PathFinderNegotiatedRouter(pitch_mm=args.pitch, max_rounds=args.rounds)

    budget = Budget(
        max_iterations=args.max_iterations, max_nodes=args.max_nodes, seed=args.seed
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
