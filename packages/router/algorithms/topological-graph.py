#!/usr/bin/env python3.12
"""Topological routing: decide the topology first, draw the copper afterwards.

A grid router asks *"which cells are free?"*. A topological router asks a
different question first: **which side of each obstacle does this net pass on?**
Only once that is settled does it work out where the copper actually goes. The
two halves are separable because the first one is combinatorial and the second
one is geometry, and mixing them is what makes a maze router spend its whole
budget rediscovering that a pad is still a pad.

Four ideas, in the order the code uses them.

**1. The free space is a triangulation, not a grid.**
Every obstacle — pad, drill, board edge — is covered by discs, and the disc
centres are Delaunay-triangulated. A triangle is a piece of free space; a
triangulation edge between two obstacles is a **gate** a wire can pass through.
The gate's width is measured, not assumed: it is ``capsule_gap`` between the two
real obstacle shapes, minus each one's own keep-away. That is where this router
gets the thing the shipped one cannot represent — a component plated hole keeps
copper 0.28mm away and a pad keeps it 0.147mm away, so the same 0.9mm gap holds
three wires between two pads and none between two holes, and the graph knows
that before any search starts.

**2. A route is a path in the dual graph.**
Nodes are ``(triangle, layer)``; an edge is a gate; a layer change is an edge
from ``(t, top)`` to ``(t, bottom)`` priced at a via. So the topology — which
side of every obstacle, and where the layer changes — falls out of one shortest
path (A*, with straight-line distance to what is left to reach), and the
geometry is not consulted at all while it is being chosen. A pad is a terminal
of that graph and never a corridor: expanding one lets a route hop
pad → triangle → pad and come out as a straight line through somebody else's
copper, carrying no gate, so nothing downstream knows what it crossed.

**3. A multi-pin net is a Steiner tree, not k-1 wires.**
A 33-pad ground net routed as 32 point-to-point connections is 32 chances to box
yourself in and a lot of duplicated copper. This uses the shortest-path
heuristic (Takahashi–Matsuyama): grow one tree, repeatedly attaching the nearest
terminal *to the tree* rather than to a pad. Branches join the trunk wherever
the trunk already runs. It is a 2-approximation of the graph Steiner minimum
tree and the trunk sharing is where the copper saving comes from.

**4. Embedding is separate, and it is checked.**
The path becomes a polyline by picking one crossing point per gate — gates are
*slotted*, so the second wire through a gap sits beside the first rather than on
it — then it is pulled taut against the obstacles by greedy shortcutting.
**Every segment is verified with** ``Workspace``, the same geometry the scorer
grades with. A segment that cannot be made legal is not emitted. This router
never places copper it cannot defend; it reports the net as unrouted instead.

When the topology is wrong rather than the geometry, gates that were used get a
congestion cost and the whole board is ripped up and re-routed (PathFinder-style
negotiated congestion). Pass 0 routes rails first and pass 1 routes them last —
which of those wins is a property of the board, not something to have an opinion
about — and later passes put the nets that failed at the front. The best pass by
measured completeness is the one returned.

## Measured, 2026-08-16, ruler ``b3c77d55b171``

```
                    clean   completeness   errors   vias (terminal-keyboard)
baseline-pattern     2/16          57.3%        0   158
topological-graph    3/16          78.9%        0    26
```

16/16 deterministic. Three `dfm_power_trace_width` **warnings**, all from the
one deliberate trade below. Biggest gains where the baseline is weakest:
`matrix-ldo-3v3__rp2040-core__usb-c-power` 23.5% → 76.5%,
`matrix-rp2040-core__usb-c-data` 19.0% → 66.7%, `harness-puck` 38.9% → 69.4%.
The via column is the Steiner tree paying for itself: same board, a sixth of the
layer changes.

**The one deliberate trade, stated so nobody has to find it.** A power or ground
net is routed at 0.5mm. If no channel on the board is that wide, the net is
retried at signal width rather than left unrouted — an unrouted rail is a dead
board and a thin rail is a warning the fab builds anyway. It happened three
times in sixteen instances and each one is named in the solution's notes.

## Also produced: the crossing analysis

``crossing_analysis()`` answers a question that is useful whichever router we
ship: **which nets must change layer?** It builds each net's Euclidean minimum
spanning tree, counts the pairs that cross in the plane, and reports

* whether the crossing graph is bipartite — if it is, a two-layer board with *no
  vias at all* is topologically possible; if it is not, no via budget avoids
  vias;
* a **lower** bound on vias from a greedy maximal matching (a minimum vertex
  cover is never smaller than any matching, so the bound errs safe);
* the specific nets a greedy cover would move, which is the matching upper
  bound on the same question.

## Where this approach is the wrong tool

Said plainly, because a family measured honestly is worth more than one that
claims wins it cannot reproduce:

* **Escape routing from a fine-pitch part.** The triangulation puts a gate
  between two adjacent QFN pads at 0.4mm pitch; that gate holds zero 0.2mm
  wires, so the graph correctly says "no". But the real answer there is a
  narrower trace or a via-in-pad process, and neither is a topology question.
* **Very short two-pad nets.** The triangulation is pure overhead; a straight
  line was always going to work and pattern routing finds it in one try.
* **Anything needing the wire to be somewhere specific** — length matching,
  impedance-controlled spacing over a reference plane. Topology does not model
  it, and this router will happily produce a legal board that is wrong.
* **Differential pairs**, honestly. The second half of a pair is given a
  discount on the gates the first half used, which is a preference, not a
  constraint: measured coupling stays near zero. A pair wants both wires planned
  as one object through one sequence of gates, and that is a different
  algorithm, not a tuning of this one.
* **The last 20% of a dense board.** Two wires crossing the same triangle in
  different directions are separated at the gates and nothing separates them in
  the middle; the copper is still checked, so the failure is an honest "not
  routed" rather than a short, but it is the reason completeness stalls where it
  does. The fix is an ordering constraint inside each face — a real rubber-band
  sketch — not more search.

Run it standalone::

    python3.12 packages/router/algorithms/topological-graph.py            # suite
    python3.12 packages/router/algorithms/topological-graph.py --only ID
    python3.12 packages/router/algorithms/topological-graph.py --analysis
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from heapq import heappop, heappush
from pathlib import Path
from typing import Sequence

_HERE = Path(__file__).resolve()
_PKG_ROOT = _HERE.parent.parent
for _candidate in (_PKG_ROOT / "src", _PKG_ROOT.parent / "circuitpy" / "src"):
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from routerlib.geometry import (  # noqa: E402
    Capsule,
    GridIndex,
    PolygonIndex,
    capsule_gap,
    disc_capsule,
    drill_capsule,
    pad_capsule,
    rect_capsule,
    segments_cross,
)
from routerlib.model import (  # noqa: E402
    BOTTOM,
    TOP,
    Budget,
    BudgetMeter,
    Net,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)
from routerlib.workspace import Workspace  # noqa: E402

LAYERS = (TOP, BOTTOM)

# --- tuning -----------------------------------------------------------------
# Every constant here shapes the search. None of them is a fab rule: those come
# from DesignRules, which reads circuitpy.fab, and are never written down twice.

#: Sub-disc spacing along an obstacle's spine, as a fraction of its radius. At
#: 0.6 the union of discs is at least 95% of the true capsule width at its
#: thinnest; the sliver that is missed can only make a gate look *wider* than it
#: is, and Workspace then refuses the wire that tried to use the difference.
_DISC_STEP = 0.6
#: Cap on discs per obstacle. A USB-C shell is one obstacle, not forty sites.
_MAX_DISCS = 8
#: Board-edge sample spacing. Fine enough that the site hull is the board.
_EDGE_SAMPLE_MM = 2.0

#: What a layer change costs, in millimetres of copper. Via count ranks above
#: length in the score, so a via is worth a few millimetres of detour — but only
#: a few: on a two-layer board with SMD parts on top, the bottom layer is the
#: only real highway and a router that will not pay for a via cannot use it.
_VIA_COST_MM = 3.0
#: A triangle any wire has already crossed is more expensive for the next one.
#: Gate slots keep wires apart *at* a gate; nothing keeps them apart in the
#: middle of a triangle, so the honest fix is to price the crowd.
_TRI_LOAD_MM = 2.0
#: Refinement: split triangles longer than this so an empty board area becomes a
#: channel with slots rather than one enormous cell.
_MAX_TRI_EDGE_MM = 4.0
_REFINE_ROUNDS = 2
#: A refinement point must be at least this far from every obstacle, so
#: refinement never manufactures a sliver.
_REFINE_MIN_CLEAR_MM = 0.5
#: A gate narrower than this is uncomfortable; cost grows linearly below it.
_COMFORT_MM = 0.9
_NARROW_WEIGHT = 4.0
#: Added to a gate every time a route fails while wanting it (PathFinder
#: history costs: the second pass pays for the first pass's congestion).
_HISTORY_STEP = 2.0
#: Cost multiplier on gates the other half of a differential pair already used.
_PAIR_DISCOUNT = 0.35
#: Rip-up rounds when the caller leaves ``Budget.max_rip_up_passes`` at its
#: default of 0. The harness only ever sets iterations/nodes/seed, so 0 there
#: means "unspecified", not "forbidden". Recorded in the report.
_DEFAULT_RIP_UP_PASSES = 3
#: Perpendicular offsets tried when one segment of an embedded path is illegal,
#: and the points along the segment they are measured from.
_BEND_OFFSETS_MM = (0.3, 0.7, 1.5)
_BEND_FRACTIONS = (0.5, 0.3, 0.7)
#: How deep the bend repair recurses, and how many half-successes it is allowed
#: to follow at each level. Both are small: a repair that needs a wide search is
#: a topology that was wrong, and re-searching is cheaper and more honest than
#: an exponential hunt for a shape.
_BEND_DEPTH = 1
_BEND_MAX_DEFERRED = 3
#: Re-searches allowed for one terminal after the geometry refused a path. The
#: corridor it wanted is penalised first, so each retry is a different topology.
_EMBED_RETRIES = 2
#: How far ahead the taut step may shortcut. Bounded so a pulled wire stays in
#: the corridor whose gates it actually reserved.
_TAUT_WINDOW = 4
#: Barycentric lattice scored when looking for room for a via: every
#: (i, j, k)/6 with all parts positive, plus the centroid.
_VIA_LATTICE = tuple(
    (i / 6.0, j / 6.0, (6 - i - j) / 6.0)
    for i in range(1, 5)
    for j in range(1, 6 - i)
) + ((1 / 3, 1 / 3, 1 / 3),)
#: How many of the roomiest spots the embedder actually tests.
_VIA_TRIES = 4
#: Edge tags in the dual graph. A gate carries its own id; these two do not.
_VIA_EDGE = -2
_PAD_EDGE = -1
#: Guard against a pathological walk in point location.
_MAX_LOCATE_STEPS = 4096
#: How often the search reads the budget. Checking a clock per node costs more
#: than the node does.
_BUDGET_CHECK_EVERY = 256


# ===========================================================================
# Delaunay triangulation
# ===========================================================================


def _orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _in_circle(
    ax: float, ay: float, bx: float, by: float, cx: float, cy: float,
    px: float, py: float,
) -> float:
    """> 0 when ``p`` is strictly inside the circumcircle of CCW triangle abc."""
    adx, ady = ax - px, ay - py
    bdx, bdy = bx - px, by - py
    cdx, cdy = cx - px, cy - py
    return (
        (adx * adx + ady * ady) * (bdx * cdy - bdy * cdx)
        - (bdx * bdx + bdy * bdy) * (adx * cdy - ady * cdx)
        + (cdx * cdx + cdy * cdy) * (adx * bdy - ady * bdx)
    )


def _insertion_order(count: int) -> list[int]:
    """A fixed pseudo-random insertion order.

    Sorted insertion makes the point-location walk O(n) per point; a scrambled
    order makes it O(1) amortised. The scramble is a fixed LCG and deliberately
    *not* the caller's seed: the triangulation describes the placement, and it
    must not move when the search seed does.
    """
    order = list(range(count))
    state = 0x2545F491
    for i in range(count - 1, 0, -1):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        j = state % (i + 1)
        order[i], order[j] = order[j], order[i]
    return order


def delaunay(points: Sequence[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Bowyer–Watson. Returns CCW triangles as index triples into ``points``.

    Points are perturbed by an index-derived picometre before the predicates
    run. Board layouts are full of exactly-collinear and exactly-cocircular
    points — a key matrix is nothing else — and an exact degeneracy leaves
    Bowyer–Watson's cavity ill-defined. A 1e-9mm nudge is a thousand times below
    the model's own 1nm quantum and six orders below anything a fab can hold, so
    it changes the topology guide and nothing else: every emitted segment is
    still measured against the unperturbed geometry by ``Workspace``.
    """
    count = len(points)
    if count < 3:
        return []
    pts: list[tuple[float, float]] = [
        (x + (i % 1237) * 1e-9, y + ((i * 7919) % 1451) * 1e-9)
        for i, (x, y) in enumerate(points)
    ]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 1000.0
    pts.extend([(cx - span, cy - span), (cx + span, cy - span), (cx, cy + span)])

    tv: list[list[int]] = [[count, count + 1, count + 2]]
    tn: list[list[int]] = [[-1, -1, -1]]
    dead = bytearray([0])
    current = 0

    def locate(px: float, py: float, start: int) -> int:
        tri = start
        if dead[tri]:
            tri = next((t for t in range(len(tv) - 1, -1, -1) if not dead[t]), 0)
        for _ in range(_MAX_LOCATE_STEPS):
            v = tv[tri]
            worst = 0.0
            step = -1
            for i in range(3):
                bx, by = pts[v[(i + 1) % 3]]
                ex, ey = pts[v[(i + 2) % 3]]
                side = _orient(bx, by, ex, ey, px, py)
                if side < worst and tn[tri][i] >= 0:
                    worst = side
                    step = i
            if step < 0:
                return tri
            tri = tn[tri][step]
        return tri

    for index in _insertion_order(count):
        px, py = pts[index]
        current = locate(px, py, current)
        bad = [current]
        seen = {current}
        stack = [current]
        while stack:
            tri = stack.pop()
            for i in range(3):
                nb = tn[tri][i]
                if nb < 0 or nb in seen:
                    continue
                a, b, c = tv[nb]
                if _in_circle(*pts[a], *pts[b], *pts[c], px, py) > 0.0:
                    seen.add(nb)
                    bad.append(nb)
                    stack.append(nb)

        boundary = _cavity_boundary(tv, tn, bad, seen)
        if boundary is None:
            # Floating point handed back a cavity that is not one closed ring.
            # Retriangulating it would corrupt the mesh, so fall back to the
            # single containing triangle, which always is one.
            seen = {current}
            boundary = _cavity_boundary(tv, tn, [current], seen)
            assert boundary is not None
            bad = [current]

        for tri in bad:
            dead[tri] = 1

        fresh: list[int] = []
        pending: dict[tuple[int, int], tuple[int, int]] = {}
        for u, v, nb, slot in boundary:
            new_id = len(tv)
            tv.append([u, v, index])
            tn.append([-1, -1, nb])
            dead.append(0)
            if nb >= 0 and slot >= 0:
                tn[nb][slot] = new_id
            fresh.append(new_id)
            # Slot 0 sits opposite u (edge v-index); slot 1 opposite v (edge
            # index-u). Each such edge is shared with exactly one other new
            # triangle, so the first sighting waits and the second one pairs.
            for other_vertex, my_slot in ((v, 0), (u, 1)):
                key = (min(other_vertex, index), max(other_vertex, index))
                partner = pending.pop(key, None)
                if partner is None:
                    pending[key] = (new_id, my_slot)
                else:
                    other_id, other_slot = partner
                    tn[new_id][my_slot] = other_id
                    tn[other_id][other_slot] = new_id
        if fresh:
            current = fresh[0]

    out: list[tuple[int, int, int]] = []
    for tri in range(len(tv)):
        if dead[tri]:
            continue
        a, b, c = tv[tri]
        if a >= count or b >= count or c >= count:
            continue
        if _orient(*points[a], *points[b], *points[c]) <= 0.0:
            a, b = b, a
        out.append((a, b, c))
    out.sort()
    return out


def _cavity_boundary(
    tv: list[list[int]], tn: list[list[int]], bad: Sequence[int], seen: set[int]
) -> list[tuple[int, int, int, int]] | None:
    """Edges of the cavity as ``(u, v, outside triangle, its slot)``.

    ``None`` when the edges do not form a single closed ring, which is the one
    failure mode of Bowyer–Watson worth guarding against.
    """
    boundary: list[tuple[int, int, int, int]] = []
    degree: dict[int, int] = {}
    for tri in bad:
        for i in range(3):
            nb = tn[tri][i]
            if nb in seen:
                continue
            u = tv[tri][(i + 1) % 3]
            v = tv[tri][(i + 2) % 3]
            slot = -1
            if nb >= 0:
                slot = next((k for k in range(3) if tn[nb][k] == tri), -1)
                if slot < 0:
                    return None
            boundary.append((u, v, nb, slot))
            degree[u] = degree.get(u, 0) + 1
            degree[v] = degree.get(v, 0) + 1
    if not boundary or any(d != 2 for d in degree.values()):
        return None
    return boundary


# ===========================================================================
# Obstacle sites
# ===========================================================================


@dataclass(frozen=True)
class Site:
    """One disc of one obstacle, carrying the clearance that obstacle demands.

    ``keepaway`` is why this router can represent what the shipped one cannot: a
    via hole wants 0.20mm, a component plated hole 0.28mm, a non-plated hole
    0.20mm and copper 0.147mm — four different numbers, one field, read from the
    rules rather than assumed.
    """

    x: float
    y: float
    r: float
    keepaway: float
    net: str | None
    layers: tuple[str, ...]
    kind: str
    owner: str

    @property
    def capsule(self) -> Capsule:
        return (self.x, self.y, self.x, self.y, self.r)


def _cover(capsule: Capsule) -> list[tuple[float, float, float]]:
    """A capsule as a chain of discs along its spine."""
    ax, ay, bx, by, r = capsule
    spine = math.hypot(bx - ax, by - ay)
    if spine <= 1e-9 or r <= 0.0:
        return [(ax, ay, max(r, 1e-6))]
    steps = min(_MAX_DISCS, max(2, int(math.ceil(spine / max(r * _DISC_STEP, 0.05))) + 1))
    return [
        (ax + (bx - ax) * i / (steps - 1), ay + (by - ay) * i / (steps - 1), r)
        for i in range(steps)
    ]


def _boundary_points(problem: RoutingProblem) -> list[tuple[float, float]]:
    """The board edge as sample points, so the site hull is the board."""
    outline = problem.board.outline
    if len(outline) < 3:
        x0, y0, x1, y1 = problem.board.bbox
        outline = (Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1))
    # Walk the perimeter by arc length, not per vertex: circuit.json tessellates
    # a rounded rectangle into a couple of thousand points, and one site per
    # vertex would make the board edge the largest obstacle on the board. The
    # edge needs enough sites to bound the triangulation, not to describe itself.
    kept: list[tuple[float, float]] = []
    carry = 0.0
    n = len(outline)
    for i in range(n):
        a, b = outline[i], outline[(i + 1) % n]
        length = a.distance_to(b)
        if length <= 1e-9:
            continue
        walked = -carry
        while walked + _EDGE_SAMPLE_MM <= length:
            walked += _EDGE_SAMPLE_MM
            t = walked / length
            kept.append((a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t))
        carry = length - walked
    if len(kept) < 3:
        kept = [(p.x, p.y) for p in outline]
    return kept


def build_sites(problem: RoutingProblem) -> tuple[list[Site], dict[str, list[int]]]:
    """Every obstacle as discs, plus ``pad id -> site indices``."""
    rules = problem.rules
    sites: list[Site] = []
    pad_sites: dict[str, list[int]] = {}

    for pad in problem.pads:
        indices: list[int] = []
        for x, y, r in _cover(pad_capsule(pad)):
            indices.append(len(sites))
            sites.append(
                Site(
                    x=x, y=y, r=r,
                    keepaway=rules.target_clearance_mm,
                    net=pad.net,
                    layers=tuple(pad.layers),
                    kind="pad",
                    owner=pad.id,
                )
            )
        pad_sites[pad.id] = indices

    for drill in problem.drills:
        keepaway = rules.hole_clearance(drill)
        for x, y, r in _cover(drill_capsule(drill)):
            sites.append(
                Site(
                    x=x, y=y, r=r,
                    keepaway=keepaway,
                    net=drill.net if drill.plated else None,
                    layers=LAYERS,
                    kind="drill",
                    owner=drill.id,
                )
            )

    for x, y in _boundary_points(problem):
        sites.append(
            Site(
                x=x, y=y, r=0.0,
                keepaway=rules.min_edge_clearance_mm,
                net=None,
                layers=LAYERS,
                kind="edge",
                owner="board",
            )
        )
    return sites, pad_sites


# ===========================================================================
# The topology: triangles, gates, and the dual graph
# ===========================================================================


@dataclass
class Gate:
    """A passage between two obstacles: one triangulation edge, measured."""

    site_a: int
    site_b: int
    tri_a: int
    tri_b: int
    #: Free span in mm on (top, bottom) with every obstacle active.
    usable: tuple[float, float]
    #: Extra span available when the wire belongs to site A's / site B's own
    #: net: copper does not need clearance from its own pad.
    bonus_a: tuple[float, float]
    bonus_b: tuple[float, float]
    net_a: str | None
    net_b: str | None
    length: float


class Topology:
    """Everything about a placed board that does not depend on the search."""

    def __init__(self, problem: RoutingProblem):
        self.problem = problem
        self.rules = problem.rules
        self.sites, self.pad_sites = build_sites(problem)
        points = [(s.x, s.y) for s in self.sites]

        outline = problem.board.outline
        self.outline = PolygonIndex(outline) if len(outline) >= 3 else None
        x0, y0, x1, y1 = problem.board.bbox

        raw = self._refine(points)
        self.tris: list[tuple[int, int, int]] = []
        for a, b, c in raw:
            mx = (points[a][0] + points[b][0] + points[c][0]) / 3.0
            my = (points[a][1] + points[b][1] + points[c][1]) / 3.0
            inside = (
                self.outline.contains(mx, my)
                if self.outline is not None
                else (x0 <= mx <= x1 and y0 <= my <= y1)
            )
            if inside:
                self.tris.append((a, b, c))
        self.tri_count = len(self.tris)
        self.centroids: list[tuple[float, float]] = [
            (
                (points[a][0] + points[b][0] + points[c][0]) / 3.0,
                (points[a][1] + points[b][1] + points[c][1]) / 3.0,
            )
            for a, b, c in self.tris
        ]
        #: A triangle wholly inside a poured plane is a safe place to land: any
        #: point of it, and the midpoint of any segment inside it, is in the
        #: pour, so connectivity to the plane is not a near-boundary gamble.
        self.tri_vertices_xy = [
            (points[a], points[b], points[c]) for a, b, c in self.tris
        ]

        self.site_tris: dict[int, list[int]] = {}
        edge_tris: dict[tuple[int, int], list[int]] = {}
        for index, (a, b, c) in enumerate(self.tris):
            for s in (a, b, c):
                self.site_tris.setdefault(s, []).append(index)
            for u, v in ((a, b), (b, c), (c, a)):
                edge_tris.setdefault((min(u, v), max(u, v)), []).append(index)

        self.gates: list[Gate] = []
        self.gate_of: dict[tuple[int, int], int] = {}
        for (u, v), owners in sorted(edge_tris.items()):
            if len(owners) != 2:
                continue  # a hull edge has nothing on the other side
            usable, bonus_a, bonus_b = self._measure(u, v)
            self.gate_of[(u, v)] = len(self.gates)
            self.gates.append(
                Gate(
                    site_a=u, site_b=v,
                    tri_a=owners[0], tri_b=owners[1],
                    usable=usable, bonus_a=bonus_a, bonus_b=bonus_b,
                    net_a=self.sites[u].net, net_b=self.sites[v].net,
                    length=math.hypot(
                        self.sites[u].x - self.sites[v].x,
                        self.sites[u].y - self.sites[v].y,
                    ),
                )
            )
        self._build_graph()

    # -- refinement --------------------------------------------------------

    def _refine(self, points: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
        """Triangulate, then split the triangles that are too big to steer in.

        An empty corner of a board triangulates into one 20mm triangle, and a
        corridor whose cells are 20mm across is not a corridor — two nets
        crossing it in different directions get no warning from the graph and
        collide when the copper is drawn. Adding free points (radius 0,
        keep-away 0, obstacles to nobody) inside the big triangles turns open
        board into a channel with slots, which is the only place a topological
        router can pack wires side by side.

        Bounded to a couple of rounds: refinement buys resolution and costs a
        whole triangulation.
        """
        tris = delaunay(points)
        grid: dict[tuple[int, int], list[int]] = {}
        cell = 2.0
        for i, s in enumerate(self.sites):
            if s.kind == "free":
                continue
            grid.setdefault((int(s.x // cell), int(s.y // cell)), []).append(i)

        def clear_of_obstacles(x: float, y: float) -> bool:
            gx, gy = int(x // cell), int(y // cell)
            for ox in (gx - 1, gx, gx + 1):
                for oy in (gy - 1, gy, gy + 1):
                    for i in grid.get((ox, oy), ()):
                        s = self.sites[i]
                        if (
                            math.hypot(s.x - x, s.y - y) - s.r - s.keepaway
                            < _REFINE_MIN_CLEAR_MM
                        ):
                            return False
            return True

        x0, y0, x1, y1 = self.problem.board.bbox
        for _round in range(_REFINE_ROUNDS):
            extra: list[tuple[float, float]] = []
            seen: set[tuple[int, int]] = set()
            for a, b, c in tris:
                pa, pb, pc = points[a], points[b], points[c]
                longest = max(
                    math.hypot(pb[0] - pa[0], pb[1] - pa[1]),
                    math.hypot(pc[0] - pb[0], pc[1] - pb[1]),
                    math.hypot(pa[0] - pc[0], pa[1] - pc[1]),
                )
                if longest <= _MAX_TRI_EDGE_MM:
                    continue
                mx = (pa[0] + pb[0] + pc[0]) / 3.0
                my = (pa[1] + pb[1] + pc[1]) / 3.0
                key = (round(mx * 20), round(my * 20))
                if key in seen:
                    continue
                inside = (
                    self.outline.contains(mx, my)
                    if self.outline is not None
                    else (x0 <= mx <= x1 and y0 <= my <= y1)
                )
                if not inside or not clear_of_obstacles(mx, my):
                    continue
                seen.add(key)
                extra.append((mx, my))
            if not extra:
                break
            for mx, my in extra:
                self.sites.append(
                    Site(
                        x=mx, y=my, r=0.0, keepaway=0.0, net=None,
                        layers=LAYERS, kind="free", owner="refine",
                    )
                )
                points.append((mx, my))
            tris = delaunay(points)
        return tris

    # -- measurement -----------------------------------------------------

    def _measure(
        self, i: int, j: int
    ) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        a, b = self.sites[i], self.sites[j]
        gap = capsule_gap(a.capsule, b.capsule)
        usable: list[float] = []
        bonus_a: list[float] = []
        bonus_b: list[float] = []
        for layer in LAYERS:
            width = gap
            if layer in a.layers:
                width -= a.keepaway
                bonus_a.append(a.keepaway)
            else:
                width += a.r  # not an obstacle on this layer: its disc is free
                bonus_a.append(0.0)
            if layer in b.layers:
                width -= b.keepaway
                bonus_b.append(b.keepaway)
            else:
                width += b.r
                bonus_b.append(0.0)
            usable.append(width)
        return (
            (usable[0], usable[1]),
            (bonus_a[0], bonus_a[1]),
            (bonus_b[0], bonus_b[1]),
        )

    def room(self, gate: Gate, layer_index: int, net: str | None) -> float:
        """Free span of a gate for one net on one layer.

        A net's own pad does not need clearance from that net's copper, which is
        exactly the room a fine-pitch escape depends on.
        """
        width = gate.usable[layer_index]
        if net is not None and gate.net_a == net:
            width += gate.bonus_a[layer_index]
        if net is not None and gate.net_b == net:
            width += gate.bonus_b[layer_index]
        return width

    def span(self, gate: Gate, layer_index: int, net: str | None) -> tuple[float, float]:
        """``(start, end)`` of the usable interval, in mm from site A."""
        layer = LAYERS[layer_index]
        a, b = self.sites[gate.site_a], self.sites[gate.site_b]
        start = 0.0
        if layer in a.layers:
            start = a.r + (0.0 if net is not None and a.net == net else a.keepaway)
        end = gate.length
        if layer in b.layers:
            end -= b.r + (0.0 if net is not None and b.net == net else b.keepaway)
        return (start, end)

    def gate_point(self, gate: Gate, distance: float) -> Point:
        a, b = self.sites[gate.site_a], self.sites[gate.site_b]
        t = distance / gate.length if gate.length > 0 else 0.0
        return Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)

    def via_places(self, tri: int) -> tuple[tuple[float, float, float], ...]:
        """``(clearance, x, y)`` for the roomiest spots in a triangle, best first.

        A triangle is not a point: the centroid of a long thin one can be too
        close to a pad while a spot a third of the way along is fine. Scoring a
        small barycentric lattice against the three obstacle discs costs almost
        nothing and it is the *same* number that decides whether the graph offers
        a via edge here at all — so the search is never promised a layer change
        the embedder cannot place.
        """
        a, b, c = self.tris[tri]
        sa, sb, sc = self.sites[a], self.sites[b], self.sites[c]
        out: list[tuple[float, float, float]] = []
        for wa, wb, wc in _VIA_LATTICE:
            x = sa.x * wa + sb.x * wb + sc.x * wc
            y = sa.y * wa + sb.y * wb + sc.y * wc
            room = min(
                math.hypot(sa.x - x, sa.y - y) - sa.r - sa.keepaway,
                math.hypot(sb.x - x, sb.y - y) - sb.r - sb.keepaway,
                math.hypot(sc.x - x, sc.y - y) - sc.r - sc.keepaway,
            )
            out.append((room, x, y))
        out.sort(key=lambda item: (-item[0], item[1], item[2]))
        return tuple(out[:_VIA_TRIES])

    # -- the dual graph ---------------------------------------------------

    def _build_graph(self) -> None:
        rules = self.rules
        self.pad_list = list(self.problem.pads)
        self.pad_index = {pad.id: i for i, pad in enumerate(self.pad_list)}
        self.node_count = self.tri_count * 2 + len(self.pad_list)
        self.adj: list[list[tuple[int, float, int]]] = [
            [] for _ in range(self.node_count)
        ]

        thinnest = rules.min_trace_mm
        for gate_id, gate in enumerate(self.gates):
            for li in (0, 1):
                # The generous reading — a wire of one of the two owning nets —
                # decides whether the edge exists at all; the per-net capacity
                # test in the search does the rest.
                widest = (
                    gate.usable[li] + max(gate.bonus_a[li], gate.bonus_b[li])
                )
                if widest < thinnest:
                    continue
                ax, ay = self.centroids[gate.tri_a]
                bx, by = self.centroids[gate.tri_b]
                cost = math.hypot(bx - ax, by - ay) + _NARROW_WEIGHT * max(
                    0.0, _COMFORT_MM - gate.usable[li]
                )
                self.adj[gate.tri_a * 2 + li].append((gate.tri_b * 2 + li, cost, gate_id))
                self.adj[gate.tri_b * 2 + li].append((gate.tri_a * 2 + li, cost, gate_id))

        #: Where every node "is", for the A* heuristic. A triangle node sits at
        #: its centroid, a pad node at the pad.
        self.node_xy: list[tuple[float, float]] = [(0.0, 0.0)] * self.node_count
        for tri in range(self.tri_count):
            self.node_xy[tri * 2] = self.centroids[tri]
            self.node_xy[tri * 2 + 1] = self.centroids[tri]
        for i, pad in enumerate(self.pad_list):
            self.node_xy[self.tri_count * 2 + i] = (pad.center.x, pad.center.y)

        via_pad_r = rules.via_pad_mm / 2.0
        self.via_spots = [self.via_places(t) for t in range(self.tri_count)]
        self.via_ready = [
            bool(spots) and spots[0][0] >= via_pad_r for spots in self.via_spots
        ]
        for tri in range(self.tri_count):
            if not self.via_ready[tri]:
                continue
            self.adj[tri * 2].append((tri * 2 + 1, _VIA_COST_MM, _VIA_EDGE))
            self.adj[tri * 2 + 1].append((tri * 2, _VIA_COST_MM, _VIA_EDGE))

        #: Every pad as the *pipeline* sees it — ``_stadium`` with no rotation.
        #: ``circuitpy.checks`` does not read ``ccw_rotation`` (routerlib's
        #: README calls it a known divergence), so a 2.25 x 0.63mm pad turned 90
        #: degrees is a horizontal bar to the gate that decides ``fab.ready``.
        #: A via that clears the real pad can still be an error against that
        #: model, and an error is a scrapped board whichever model found it — so
        #: vias are placed clear of both. Measured cost on harness-puck: one
        #: `dfm_hole_clearance` error, gone.
        self.pipeline_pads = GridIndex(cell_mm=2.0)
        for pad in self.pad_list:
            self.pipeline_pads.insert(
                rect_capsule(pad.center.x, pad.center.y, pad.width_mm, pad.height_mm),
                (pad.net, pad.id),
            )

        for i, pad in enumerate(self.pad_list):
            node = self.tri_count * 2 + i
            touched: list[int] = []
            seen: set[int] = set()
            for s in self.pad_sites.get(pad.id, ()):
                for tri in self.site_tris.get(s, ()):
                    if tri not in seen:
                        seen.add(tri)
                        touched.append(tri)
            for tri in sorted(touched):
                mx, my = self.centroids[tri]
                cost = math.hypot(pad.center.x - mx, pad.center.y - my)
                for li, layer in enumerate(LAYERS):
                    if layer not in pad.layers:
                        continue
                    self.adj[node].append((tri * 2 + li, cost, _PAD_EDGE))
                    self.adj[tri * 2 + li].append((node, cost, _PAD_EDGE))

    # -- node helpers ------------------------------------------------------

    def pad_node(self, pad_id: str) -> int:
        return self.tri_count * 2 + self.pad_index[pad_id]

    def pad_of_node(self, node: int):
        if node < self.tri_count * 2:
            return None
        return self.pad_list[node - self.tri_count * 2]

    def node_layer(self, node: int) -> str | None:
        return None if node >= self.tri_count * 2 else LAYERS[node % 2]

    def node_tri(self, node: int) -> int | None:
        return None if node >= self.tri_count * 2 else node // 2

    def gate_between(self, a: int, b: int) -> int:
        for other, _cost, gate_id in self.adj[a]:
            if other == b:
                return gate_id
        return -1


#: Building a topology costs more than routing on it, and it depends only on the
#: placement — while the harness routes each instance three times (once scored,
#: twice to prove determinism). Keyed by instance id plus the placement digest,
#: so a rebuilt board can never reuse a stale graph.
_TOPOLOGY_CACHE: dict[tuple[str, str], Topology] = {}


def topology_for(problem: RoutingProblem) -> Topology:
    from routerlib.bench import placement_hash

    key = (problem.id, placement_hash(problem))
    cached = _TOPOLOGY_CACHE.get(key)
    if cached is None:
        cached = Topology(problem)
        _TOPOLOGY_CACHE[key] = cached
    return cached


# ===========================================================================
# Crossing / planarity analysis
# ===========================================================================


def _prim(points: Sequence[tuple[float, float, str]]) -> list[tuple[int, int]]:
    """Prim over labelled points; ties broken by label, so it is deterministic."""
    if len(points) < 2:
        return []
    inside = [0]
    outside = list(range(1, len(points)))
    edges: list[tuple[int, int]] = []
    while outside:
        best: tuple[float, str, str] | None = None
        pick: tuple[int, int] | None = None
        for i in inside:
            for j in outside:
                d = math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1])
                key = (d, points[i][2], points[j][2])
                if best is None or key < best:
                    best, pick = key, (i, j)
        assert pick is not None
        edges.append(pick)
        inside.append(pick[1])
        outside.remove(pick[1])
    return edges


def crossing_analysis(problem: RoutingProblem) -> dict:
    """Which nets *must* leave the layer, from topology alone.

    Each net is reduced to its Euclidean minimum spanning tree — the cheapest
    single-layer shape it could take. Two nets whose trees cross cannot both stay
    on one layer, whatever router draws them. From that crossing graph:

    * ``twoLayerViaFreeFeasible`` — the crossing graph is bipartite, so the board
      could in principle be two-coloured onto two layers with no vias at all. If
      it is false, no via budget makes a via-free board.
    * ``viaLowerBound`` — the size of a greedy *maximal matching*. A minimum
      vertex cover is never smaller than any matching, and every net in the cover
      has to change layer at least once, so this is a genuine lower bound rather
      than an estimate.
    * ``coverNets`` — a greedy cover: moving these is *sufficient*. Upper bound
      on the same question, and the two bracket the answer.

    The MST is a lower bound on the shape, so the crossing count is a lower bound
    on the crossings any router will face; a router that takes a longer route can
    always create more.
    """
    segments: list[tuple[float, float, float, float, str]] = []
    for net in problem.routable_nets:
        pads = sorted(problem.pads_of(net.id), key=lambda p: (p.center.x, p.center.y, p.id))
        pts = [(p.center.x, p.center.y, p.id) for p in pads]
        for i, j in _prim(pts):
            segments.append((pts[i][0], pts[i][1], pts[j][0], pts[j][1], net.id))

    crossings = 0
    conflicts: dict[str, set[str]] = {}
    for i in range(len(segments)):
        ax, ay, bx, by, na = segments[i]
        for j in range(i + 1, len(segments)):
            cx, cy, dx, dy, nb = segments[j]
            if na == nb:
                continue
            if segments_cross(ax, ay, bx, by, cx, cy, dx, dy):
                crossings += 1
                conflicts.setdefault(na, set()).add(nb)
                conflicts.setdefault(nb, set()).add(na)

    names = {n.id: n.name for n in problem.nets}
    nodes = sorted(conflicts)

    colour: dict[str, int] = {}
    bipartite = True
    for start in nodes:
        if start in colour:
            continue
        colour[start] = 0
        queue = [start]
        while queue:
            node = queue.pop()
            for other in sorted(conflicts[node]):
                if other not in colour:
                    colour[other] = 1 - colour[node]
                    queue.append(other)
                elif colour[other] == colour[node]:
                    bipartite = False

    matched: set[str] = set()
    matching = 0
    for node in nodes:
        if node in matched:
            continue
        for other in sorted(conflicts[node]):
            if other not in matched:
                matched.add(node)
                matched.add(other)
                matching += 1
                break

    remaining = {n: set(v) for n, v in conflicts.items()}
    cover: list[str] = []
    while True:
        pick = max(sorted(remaining), key=lambda n: (len(remaining[n]), n), default=None)
        if pick is None or not remaining[pick]:
            break
        cover.append(pick)
        for other in list(remaining[pick]):
            remaining[other].discard(pick)
        remaining[pick] = set()

    return {
        "instance": problem.id,
        "routableNets": len(problem.routable_nets),
        "netsWithCrossings": len(nodes),
        "segmentCrossings": crossings,
        "netPairsCrossing": sum(len(v) for v in conflicts.values()) // 2,
        "singleLayerFeasible": crossings == 0,
        "twoLayerViaFreeFeasible": bipartite,
        "viaLowerBound": matching,
        "coverNets": sorted(names.get(n, n) for n in cover),
    }


# ===========================================================================
# Embedding helpers
# ===========================================================================


def _dedupe(points: Sequence[Point]) -> list[Point]:
    out: list[Point] = []
    for point in points:
        if not out or out[-1] != point:
            out.append(point)
    return out


def _closest_on_polyline(points: Sequence[Point], target: Point) -> Point:
    """The point of an emitted polyline nearest ``target``.

    A Steiner branch has to join the trunk *on the copper*. The trunk's shape
    changes when it is pulled taut, so the planned junction may no longer be one
    of its vertices; this projects it back onto whatever was actually committed,
    and the two capsules then overlap by construction rather than by luck.
    """
    best = points[0]
    best_d = math.inf
    for a, b in zip(points, points[1:]):
        dx, dy = b.x - a.x, b.y - a.y
        length2 = dx * dx + dy * dy
        if length2 <= 1e-18:
            candidate = a
        else:
            t = ((target.x - a.x) * dx + (target.y - a.y) * dy) / length2
            candidate = Point(a.x + dx * min(1.0, max(0.0, t)), a.y + dy * min(1.0, max(0.0, t)))
        d = candidate.distance_to(target)
        if d < best_d:
            best_d, best = d, candidate
    return best


def _taut(
    ws: Workspace, layer: str, points: Sequence[Point], width: float, net: str
) -> list[Point]:
    """Greedy shortcutting: the rubber-band step, done with real checks.

    A topological route is a homotopy class, not a shape. Pulling it taut inside
    that class is what turns "passes left of C7" into copper, and it hands the
    middle of the channel back to whichever net needs it next.
    """
    if len(points) <= 2:
        return list(points)
    out = [points[0]]
    i = 0
    last = len(points) - 1
    while i < last:
        # Bounded look-ahead on purpose. An unbounded shortcut can leave the
        # corridor the search reserved and cross gates this net never paid for;
        # the next net then plans through a gap that is already full and only
        # finds out when its copper is refused. A few steps is enough to remove
        # the zigzag between adjacent gates, which is all the slack there is.
        j = min(last, i + _TAUT_WINDOW)
        while j > i + 1:
            if ws.segment_ok(layer, points[i], points[j], width, net) is True:
                break
            j -= 1
        out.append(points[j])
        i = j
    return out


@dataclass
class _Run:
    """One layer's worth of an embedded path, before it becomes a Trace."""

    layer: str
    points: list[Point]


# ===========================================================================
# The router
# ===========================================================================


class TopologicalRouter:
    """Triangulated free space, Steiner trees in its dual, taut embedding."""

    name = "topological-graph"

    def __init__(self, *, rip_up_passes: int | None = None):
        self._rip_up_override = rip_up_passes
        #: Why routes were refused, counted. Reported in the solution's notes,
        #: because "57% routed" without a reason is a number nobody can act on.
        self.failures: dict[str, int] = {}

    def _refuse(self, reason: str) -> None:
        self.failures[reason] = self.failures.get(reason, 0) + 1

    # -- entry point ------------------------------------------------------

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        started = time.perf_counter()
        meter = budget.meter()
        self.failures = {}
        topo = topology_for(problem)
        passes = self._rip_up_override
        if passes is None:
            passes = budget.max_rip_up_passes or _DEFAULT_RIP_UP_PASSES

        history: dict[int, float] = {}
        routable = [n for n in problem.nets if n.routable]
        # Pass 0 takes the problem's own order — rails, pairs, then signals.
        # Pass 1 inverts it. Which of the two wins is a property of the board,
        # not a thing to have an opinion about: a wide ground net routed first
        # owns the channels, routed last it has none, and only one of those is
        # right per board. Every later pass is negotiated congestion: the nets
        # that failed go first and the corridors they lost in cost more.
        order = list(routable)
        best: tuple[tuple, list[Trace], list[Via], list[str], list[str]] | None = None

        for attempt in range(passes + 1):
            traces, vias, unrouted, notes = self._one_pass(problem, topo, meter, history, order)
            rank = (len(routable) - len(unrouted), -len(vias),
                    -round(sum(t.length_mm for t in traces), 3))
            if best is None or rank > best[0]:
                best = (rank, traces, vias, unrouted, list(notes))
            if not unrouted or meter.exhausted or attempt >= passes:
                break
            if attempt == 0:
                order = list(reversed(routable))
            else:
                failed = set(unrouted)
                order = [n for n in order if n.id in failed] + [
                    n for n in order if n.id not in failed
                ]

        assert best is not None
        _, traces, vias, unrouted, notes = best
        notes.append(f"{self._describe(topo)}; {passes} rip-up pass(es) allowed")
        if self.failures:
            notes.append(
                "refusals: "
                + ", ".join(
                    f"{count}x {reason}"
                    for reason, count in sorted(self.failures.items())
                )
            )
        if meter.stop_reason == "wall_clock":
            notes.append(
                "hit the wall-clock safety valve — evidence the router hung, "
                "not a comparable result"
            )
        return RoutingSolution(
            router=self.name,
            traces=tuple(traces),
            vias=tuple(vias),
            complete=not unrouted,
            unrouted_nets=tuple(sorted(set(unrouted))),
            iterations=meter.iterations,
            nodes_expanded=meter.nodes,
            wall_clock_s=time.perf_counter() - started,
            notes=tuple(notes),
        )

    @staticmethod
    def _describe(topo: Topology) -> str:
        return (
            f"{len(topo.sites)} obstacle discs, {topo.tri_count} triangles, "
            f"{len(topo.gates)} gates"
        )

    # -- one full rip-up pass ---------------------------------------------

    def _one_pass(
        self,
        problem: RoutingProblem,
        topo: Topology,
        meter: BudgetMeter,
        history: dict[int, float],
        order: Sequence[Net],
    ) -> tuple[list[Trace], list[Via], list[str], list[str]]:
        ws = Workspace(problem)
        traces: list[Trace] = []
        vias: list[Via] = []
        unrouted: list[str] = []
        notes: list[str] = []
        thin: list[str] = []
        used: dict[tuple[int, int], float] = {}
        load: dict[int, int] = {}
        dead_vias: set[int] = set()
        planes: dict[str, list[Plane]] = {}
        for plane in problem.planes:
            planes.setdefault(plane.net, []).append(plane)
        #: net id -> {gate id: discount}. Filled by the first half of a pair so
        #: the second half prefers the same channel.
        pair_hint: dict[str, dict[int, float]] = {}

        for position, net in enumerate(order):
            meter.tick()
            if meter.exhausted:
                notes.append(f"budget exhausted ({meter.stop_reason}) at {net.name}")
                unrouted.extend(n.id for n in order[position:])
                break
            hint = pair_hint.get(net.id, {})
            # A rail is routed at power width. If the board has no channel that
            # wide, a thinner rail is a *warning* from the pipeline and an
            # unrouted net is a dead board — so the fallback is worth taking,
            # once, and worth saying out loud in the notes.
            widths = [max(net.min_width_mm, problem.rules.min_trace_mm)]
            if (
                net.net_class in ("power", "ground")
                and widths[0] > problem.rules.signal_trace_mm
            ):
                widths.append(problem.rules.signal_trace_mm)

            ok = False
            gates: list[int] = []
            for attempt, width in enumerate(widths):
                mark = (len(traces), len(vias), dict(used), dict(load))
                if net.id in planes:
                    ok, gates = self._stitch_plane(
                        problem, topo, ws, meter, history, used, load, net, width,
                        planes[net.id], traces, vias, dead_vias,
                    )
                else:
                    ok, gates = self._route_net(
                        problem, topo, ws, meter, history, used, load, net, width,
                        traces, vias, hint, dead_vias,
                    )
                if ok:
                    if attempt:
                        thin.append(net.name)
                    break
                for gate_id in gates:
                    history[gate_id] = history.get(gate_id, 0.0) + _HISTORY_STEP
                # Roll the net back. Half a net earns no completeness — the
                # metric is per net — and its copper would block every net after
                # it. Keeping it would trade a real connection for nothing.
                del traces[mark[0]:]
                del vias[mark[1]:]
                used.clear()
                used.update(mark[2])
                load.clear()
                load.update(mark[3])
                ws = _fresh_workspace(problem, traces, vias)
            if not ok:
                unrouted.append(net.id)
            elif net.net_class == "diff_pair" and net.diff_partner:
                pair_hint[net.diff_partner] = {g: _PAIR_DISCOUNT for g in gates}
        if thin:
            notes.append(
                f"{len(thin)} rail(s) routed at signal width because no channel "
                f"held {problem.rules.power_trace_mm:g}mm: "
                + ", ".join(sorted(thin))
                + " — each is a dfm_power_trace_width warning, not an error"
            )
        return traces, vias, unrouted, notes

    # -- one net -----------------------------------------------------------

    def _route_net(
        self,
        problem: RoutingProblem,
        topo: Topology,
        ws: Workspace,
        meter: BudgetMeter,
        history: dict[int, float],
        used: dict[tuple[int, int], float],
        load: dict[int, int],
        net: Net,
        width: float,
        traces: list[Trace],
        vias: list[Via],
        hint: dict[int, float],
        dead_vias: set[int],
    ) -> tuple[bool, list[int]]:
        """Shortest-path Steiner heuristic over the dual graph."""
        pads = [p for p in problem.pads_of(net.id) if p.id in topo.pad_index]
        if len(pads) < 2:
            return True, []
        pads.sort(key=lambda p: (p.center.x, p.center.y, p.id))

        cx = sum(p.center.x for p in pads) / len(pads)
        cy = sum(p.center.y for p in pads) / len(pads)
        start = min(pads, key=lambda p: (math.hypot(p.center.x - cx, p.center.y - cy), p.id))

        tree: dict[int, Point] = {topo.pad_node(start.id): start.center}
        remaining = {topo.pad_node(p.id) for p in pads if p.id != start.id}
        touched: list[int] = []
        #: Gates this net has been told to stop asking for, inside this attempt.
        avoid: dict[int, float] = {}

        while remaining:
            meter.tick()
            connected = False
            for _try in range(_EMBED_RETRIES + 1):
                path = self._search(
                    topo, meter, history, used, load, net.id, width,
                    set(tree), remaining, hint, avoid, dead_vias,
                )
                if path is None:
                    self._refuse("no path in the dual graph")
                    return False, touched
                anchors = self._embed(
                    problem, topo, ws, net, width, path, tree, used, load,
                    traces, vias, dead_vias,
                )
                if anchors is not None:
                    touched.extend(self._gates_on(topo, path))
                    tree.update(anchors)
                    remaining.discard(path[-1])
                    connected = True
                    break
                # The topology was fine and the geometry was not. Make this
                # corridor expensive for the retry rather than guessing again.
                for gate_id in self._gates_on(topo, path):
                    avoid[gate_id] = avoid.get(gate_id, 0.0) + _HISTORY_STEP
                    history[gate_id] = history.get(gate_id, 0.0) + _HISTORY_STEP
            if not connected:
                return False, touched
        return True, touched

    # -- a net with a poured plane -----------------------------------------

    def _stitch_plane(
        self,
        problem: RoutingProblem,
        topo: Topology,
        ws: Workspace,
        meter: BudgetMeter,
        history: dict[int, float],
        used: dict[tuple[int, int], float],
        load: dict[int, int],
        net: Net,
        width: float,
        planes: Sequence[Plane],
        traces: list[Trace],
        vias: list[Via],
        dead_vias: set[int],
    ) -> tuple[bool, list[int]]:
        """A net with a pour is not routed pad-to-pad; every pad only has to
        reach the pour.

        This is the case the shipped router cannot express: it saw our 73 ground
        vias as 73 obstacles and emitted byte-identical copper with and without
        the plane. Here the pour is a *sink* in the same dual graph, so a pad
        that cannot be stitched where it stands is allowed to run somewhere it
        can — which is more than a fanout-only stitcher can do.
        """
        shapes = {p.id: PolygonIndex(p.outline) for p in planes}
        sinks: set[int] = set()
        for tri in range(topo.tri_count):
            corners = topo.tri_vertices_xy[tri]
            mx, my = topo.centroids[tri]
            for plane in planes:
                index = shapes[plane.id]
                # Whole triangle inside the pour, so any point of it — and the
                # midpoint of any segment in it — is genuinely in the plane.
                if index.contains(mx, my) and all(index.contains(x, y) for x, y in corners):
                    sinks.add(tri * 2 + (0 if plane.layer == TOP else 1))
                    break
        if not sinks:
            self._refuse("no triangle lies wholly inside the pour")
            return False, []

        touched: list[int] = []
        all_ok = True
        pads = sorted(
            (p for p in problem.pads_of(net.id) if p.id in topo.pad_index),
            key=lambda p: (p.center.x, p.center.y, p.id),
        )
        for pad in pads:
            meter.tick()
            if any(
                plane.layer in pad.layers
                and shapes[plane.id].contains(pad.center.x, pad.center.y)
                for plane in planes
            ):
                continue  # the pour already owns this pad
            node = topo.pad_node(pad.id)
            avoid: dict[int, float] = {}
            stitched = False
            for _try in range(_EMBED_RETRIES + 1):
                path = self._search(
                    topo, meter, history, used, load, net.id, width,
                    {node}, sinks, {}, avoid, dead_vias,
                )
                if path is None:
                    self._refuse("no path from pad to the pour")
                    break
                anchors = self._embed(
                    problem, topo, ws, net, width, path, {node: pad.center},
                    used, load, traces, vias, dead_vias,
                )
                if anchors is not None:
                    touched.extend(self._gates_on(topo, path))
                    self._reach_into_pour(ws, topo, net, width, path, anchors, traces)
                    stitched = True
                    break
                for gate_id in self._gates_on(topo, path):
                    avoid[gate_id] = avoid.get(gate_id, 0.0) + _HISTORY_STEP
                    history[gate_id] = history.get(gate_id, 0.0) + _HISTORY_STEP
            if not stitched:
                all_ok = False
        return all_ok, touched

    def _reach_into_pour(
        self,
        ws: Workspace,
        topo: Topology,
        net: Net,
        width: float,
        path: Sequence[int],
        anchors: dict[int, Point],
        traces: list[Trace],
    ) -> None:
        """Nudge the last of the copper to the centre of the sink triangle.

        Landing on a pour's boundary and landing in a pour are different
        outcomes, and only one of them is a connection.
        """
        layer = topo.node_layer(path[-1])
        tri = topo.node_tri(path[-1])
        if tri is None or layer is None:
            return
        last = anchors.get(path[-1])
        centre = Point(*topo.centroids[tri])
        if last is None or last == centre:
            return
        if ws.segment_ok(layer, last, centre, width, net.id) is not True:
            return
        trace = Trace(
            id=f"{net.id}~{len(traces)}",
            net=net.id,
            layer=layer,
            points=(last, centre),
            width_mm=width,
        )
        ws.commit_trace(trace)
        traces.append(trace)

    # -- the search --------------------------------------------------------

    def _gates_on(self, topo: Topology, path: Sequence[int]) -> list[int]:
        out: list[int] = []
        for a, b in zip(path, path[1:]):
            gate_id = topo.gate_between(a, b)
            if gate_id >= 0:
                out.append(gate_id)
        return out

    def _search(
        self,
        topo: Topology,
        meter: BudgetMeter,
        history: dict[int, float],
        used: dict[tuple[int, int], float],
        load: dict[int, int],
        net_id: str,
        width: float,
        sources: set[int],
        targets: set[int],
        hint: dict[int, float],
        avoid: dict[int, float],
        dead_vias: set[int],
    ) -> list[int] | None:
        """Multi-source A* from the whole tree to the nearest unconnected
        terminal.

        This is the growth step of the shortest-path Steiner heuristic: the tree
        is the source set, so a branch attaches wherever the trunk already runs
        rather than at a pad.

        Two things keep it honest. **Gate capacity is hard** — a gate with no
        room left for one more wire of this width is simply not an edge, so the
        search never proposes a topology the geometry cannot hold. And the
        heuristic is the straight-line distance to the bounding box of what is
        left to reach, which never overestimates: every edge costs at least the
        distance it covers, so A* returns the same path Dijkstra would and
        touches a fraction of the graph doing it.
        """
        xy = topo.node_xy
        bx0 = min(xy[t][0] for t in targets)
        bx1 = max(xy[t][0] for t in targets)
        by0 = min(xy[t][1] for t in targets)
        by1 = max(xy[t][1] for t in targets)

        def under(node: int) -> float:
            x, y = xy[node]
            dx = bx0 - x if x < bx0 else (x - bx1 if x > bx1 else 0.0)
            dy = by0 - y if y < by0 else (y - by1 if y > by1 else 0.0)
            return math.hypot(dx, dy)

        dist: dict[int, float] = {}
        prev: dict[int, int] = {}
        heap: list[tuple[float, int]] = []
        for node in sorted(sources):
            dist[node] = 0.0
            heappush(heap, (under(node), node))
        popped = 0
        while heap:
            priority, node = heappop(heap)
            cost = dist.get(node, math.inf)
            if priority > cost + under(node) + 1e-12:
                continue
            popped += 1
            if popped % _BUDGET_CHECK_EVERY == 0:
                meter.expand(_BUDGET_CHECK_EVERY)
                if meter.exhausted:
                    return None
            if node in targets and node not in sources:
                meter.expand(popped % _BUDGET_CHECK_EVERY)
                path = [node]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                path.reverse()
                return path
            if node >= topo.tri_count * 2 and node not in sources:
                # A pad is a terminal, never a corridor. Expanding one lets a
                # path hop pad -> triangle -> pad and come out as a straight line
                # through somebody else's copper — with no gate on it, so nothing
                # downstream even knows what it crossed.
                continue
            for other, base, gate_id in topo.adj[node]:
                step = base
                if gate_id == _VIA_EDGE and (node >> 1) in dead_vias:
                    continue
                if gate_id >= 0:
                    layer_index = node % 2
                    gate = topo.gates[gate_id]
                    spent = used.get((gate_id, layer_index), 0.0)
                    if spent + width > topo.room(gate, layer_index, net_id):
                        continue
                    step = (
                        step + history.get(gate_id, 0.0) + avoid.get(gate_id, 0.0)
                    ) * hint.get(gate_id, 1.0)
                crowd = load.get(other, 0)
                if crowd:
                    step += _TRI_LOAD_MM * crowd
                nxt = cost + step
                if nxt < dist.get(other, math.inf) - 1e-12:
                    dist[other] = nxt
                    prev[other] = node
                    heappush(heap, (nxt + under(other), other))
        meter.expand(popped % _BUDGET_CHECK_EVERY)
        return None

    # -- embedding ---------------------------------------------------------

    def _embed(
        self,
        problem: RoutingProblem,
        topo: Topology,
        ws: Workspace,
        net: Net,
        width: float,
        path: Sequence[int],
        tree: dict[int, Point],
        used: dict[tuple[int, int], float],
        load: dict[int, int],
        traces: list[Trace],
        vias: list[Via],
        dead_vias: set[int],
    ) -> dict[int, Point] | None:
        """Dual-graph path -> checked copper.

        Returns the points on the committed copper for every node the path
        visited, or ``None`` — in which case nothing at all was committed, and
        the caller reports the net unrouted rather than emitting copper it cannot
        defend.
        """
        anchor = tree.get(path[0])
        if anchor is None:
            self._refuse("no anchor on the tree")
            return None

        layer = topo.node_layer(path[0])
        if layer is None:
            layer = topo.node_layer(path[1]) if len(path) > 1 else TOP
        current = _Run(layer=layer or TOP, points=[anchor])
        runs: list[_Run] = []
        via_points: list[tuple[Point, str, str]] = []
        planned: dict[int, Point] = {}
        claimed: list[tuple[tuple[int, int], float]] = []

        for a, b in zip(path, path[1:]):
            gate_id = topo.gate_between(a, b)
            layer_a, layer_b = topo.node_layer(a), topo.node_layer(b)
            if gate_id >= 0:
                li = 0 if layer_a == TOP else 1
                slot = self._gate_slot(topo, gate_id, li, net.id, width, used)
                if slot is None:
                    self._refuse("gate full at embed time")
                    return None
                point, spend = slot
                claimed.append(((gate_id, li), spend))
                current.points.append(point)
                planned[b] = point
            elif layer_a is not None and layer_b is not None and layer_a != layer_b:
                spot = self._via_spot(topo, ws, net.id, a >> 1)
                if spot is None:
                    # Nothing fits here now and nothing will until copper moves:
                    # take the layer change out of the graph so the retry looks
                    # for a different triangle instead of the same one.
                    dead_vias.add(a >> 1)
                    self._refuse("no legal via position in the triangle")
                    return None
                current.points.append(spot)
                runs.append(current)
                via_points.append((spot, layer_a, layer_b))
                current = _Run(layer=layer_b, points=[spot])
                planned[b] = spot
            else:
                if layer_b is not None:
                    current.layer = layer_b
                planned.setdefault(b, current.points[-1])

        end_pad = topo.pad_of_node(path[-1])
        if end_pad is not None:
            current.points.append(end_pad.center)
        runs.append(current)

        # Pin escape. A pad-to-triangle hop carries no geometry of its own, so
        # without this the wire jumps from the pad centre straight to a gate on
        # the far side of the triangle and clips whatever is between. One point
        # just outside the pad, aimed where the wire is going, is the whole fix —
        # and it is where every real router starts too.
        start_pad = topo.pad_of_node(path[0])
        if start_pad is not None and len(path) > 1 and len(runs[0].points) >= 2:
            escape = self._escape(topo, start_pad, topo.node_tri(path[1]), width,
                                  runs[0].points[1])
            if escape is not None:
                runs[0].points.insert(1, escape)
        if end_pad is not None and len(path) > 1 and len(runs[-1].points) >= 2:
            tail = runs[-1].points
            escape = self._escape(topo, end_pad, topo.node_tri(path[-2]), width,
                                  tail[-2])
            if escape is not None:
                tail.insert(len(tail) - 1, escape)

        final: list[_Run] = []
        for run in runs:
            points = _dedupe(run.points)
            if len(points) < 2:
                continue
            legal = self._legalise(ws, run.layer, points, width, net.id)
            if legal is None:
                self._refuse("segment could not be made legal")
                return None
            final.append(_Run(run.layer, _taut(ws, run.layer, legal, width, net.id)))

        for point, _from, _to in via_points:
            if ws.via_ok(point, net.id) is not True:
                self._refuse("via position taken by earlier copper")
                return None

        for point, from_layer, to_layer in via_points:
            via = Via(
                id=f"{net.id}~v{len(vias)}",
                net=net.id,
                center=point,
                drill_mm=problem.rules.via_drill_mm,
                pad_mm=problem.rules.via_pad_mm,
                from_layer=from_layer,
                to_layer=to_layer,
            )
            ws.commit_via(via)
            vias.append(via)
        for run in final:
            trace = Trace(
                id=f"{net.id}~{len(traces)}",
                net=net.id,
                layer=run.layer,
                points=tuple(run.points),
                width_mm=width,
            )
            ws.commit_trace(trace)
            traces.append(trace)
        for key, spend in claimed:
            used[key] = used.get(key, 0.0) + spend
        for node in path:
            if node < topo.tri_count * 2:
                load[node] = load.get(node, 0) + 1

        anchors: dict[int, Point] = {}
        for node, target in planned.items():
            want = topo.node_layer(node)
            best: Point | None = None
            best_d = math.inf
            for run in final:
                if want is not None and run.layer != want:
                    continue
                candidate = _closest_on_polyline(run.points, target)
                d = candidate.distance_to(target)
                if d < best_d:
                    best_d, best = d, candidate
            if best is not None:
                anchors[node] = best
        if end_pad is not None:
            anchors[path[-1]] = end_pad.center
        for node in path:
            anchors.setdefault(node, tree.get(node, anchor))
        return anchors

    def _gate_slot(
        self,
        topo: Topology,
        gate_id: int,
        layer_index: int,
        net_id: str,
        width: float,
        used: dict[tuple[int, int], float],
    ) -> tuple[Point, float] | None:
        """Where on this gate this wire sits, and what it consumes.

        Wires pack from the A end of the free span, so the second net through a
        gap gets a different crossing point instead of the same one. Sharing a
        channel is the whole reason a topological router beats one wire per
        corridor.
        """
        gate = topo.gates[gate_id]
        start, end = topo.span(gate, layer_index, net_id)
        if end - start < width:
            return None
        spent = used.get((gate_id, layer_index), 0.0)
        offset = start + spent + width / 2.0
        if offset + width / 2.0 > end:
            offset = end - width / 2.0
        if offset - width / 2.0 < start - 1e-9:
            return None
        return (topo.gate_point(gate, offset), width + topo.rules.target_clearance_mm)

    def _escape(
        self,
        topo: Topology,
        pad,
        tri: int | None,
        width: float,
        aim: Point,
    ) -> Point | None:
        """A point just clear of ``pad``, inside ``tri``, pointing at ``aim``.

        The offset is the pad's own disc plus the target clearance plus half the
        wire — measured, not guessed — so the escape is exactly as far out as the
        rules require and no further. Anything further wastes the room the next
        wire needs.
        """
        if tri is None:
            return None
        own = [s for s in topo.tris[tri] if topo.sites[s].owner == pad.id]
        if not own:
            return None
        site = topo.sites[
            min(own, key=lambda i: math.hypot(topo.sites[i].x - aim.x,
                                              topo.sites[i].y - aim.y))
        ]
        dx, dy = aim.x - site.x, aim.y - site.y
        length = math.hypot(dx, dy)
        if length < 1e-9:
            cx, cy = topo.centroids[tri]
            dx, dy = cx - site.x, cy - site.y
            length = math.hypot(dx, dy)
        if length < 1e-9:
            return None
        reach = site.r + topo.rules.target_clearance_mm + width / 2.0 + 0.02
        if reach >= length:
            return None  # the aim point is already inside the escape radius
        return Point(site.x + dx / length * reach, site.y + dy / length * reach)

    def _via_spot(
        self, topo: Topology, ws: Workspace, net_id: str, tri: int
    ) -> Point | None:
        drill = topo.rules.via_drill_mm
        for _room, x, y in topo.via_spots[tri]:
            point = Point(x, y)
            if ws.via_ok(point, net_id) is not True:
                continue
            if not _via_clears_pipeline_pads(topo, point, net_id, drill):
                continue
            return point
        return None

    # -- geometry repair ---------------------------------------------------

    def _legalise(
        self, ws: Workspace, layer: str, points: Sequence[Point], width: float, net: str
    ) -> list[Point] | None:
        """Make every segment legal, or give up on the whole path.

        A corridor of triangles keeps the wire in free space *topologically*, but
        the corner of a very obtuse triangle can still put a straight segment on
        top of an obstacle. Rather than trust the corridor, each segment is
        measured and bent around whatever it hit.
        """
        out = [points[0]]
        index = 1
        last = len(points) - 1
        while index <= last:
            source = out[-1]
            target = points[index]
            if source == target:
                index += 1
                continue
            if ws.segment_ok(layer, source, target, width, net) is True:
                out.append(target)
                index += 1
                continue
            bend = self._bend(ws, layer, source, target, width, net, _BEND_DEPTH)
            if bend is not None:
                out.extend(bend)
                out.append(target)
                index += 1
                continue
            # A waypoint that cannot be reached and cannot be bent around is a
            # gate slot the corridor did not really have. Dropping it and aiming
            # at the next one is cheaper than losing the route — the endpoints
            # are not droppable and are still checked.
            if index < last:
                index += 1
                continue
            return None
        return out if len(out) >= 2 else None

    def _bend(
        self,
        ws: Workspace,
        layer: str,
        a: Point,
        b: Point,
        width: float,
        net: str,
        depth: int,
    ) -> list[Point] | None:
        """Detour points that make ``a -> b`` legal, or ``None``.

        Offsets are tried nearest-first, so a repair stays inside the corridor
        the search chose rather than wandering into someone else's. The
        recursion lets one blocked straight line become a Z instead of a
        failure; past two bends the topology was wrong, not the geometry, and
        the caller should re-search rather than keep bending.
        """
        dx, dy = b.x - a.x, b.y - a.y
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return None
        nx, ny = -dy / length, dx / length
        candidates: list[Point] = []
        for offset in _BEND_OFFSETS_MM:
            for fraction in _BEND_FRACTIONS:
                px, py = a.x + dx * fraction, a.y + dy * fraction
                candidates.append(Point(px + nx * offset, py + ny * offset))
                candidates.append(Point(px - nx * offset, py - ny * offset))
        candidates.append(Point(b.x, a.y))
        candidates.append(Point(a.x, b.y))

        deferred: list[tuple[Point, bool]] = []
        for point in candidates:
            first = ws.segment_ok(layer, a, point, width, net) is True
            second = ws.segment_ok(layer, point, b, width, net) is True
            if first and second:
                return [point]
            if depth > 0 and (first or second) and len(deferred) < _BEND_MAX_DEFERRED:
                deferred.append((point, first))
        for point, first in deferred:
            if first:
                more = self._bend(ws, layer, point, b, width, net, depth - 1)
                if more is not None:
                    return [point] + more
            else:
                more = self._bend(ws, layer, a, point, width, net, depth - 1)
                if more is not None:
                    return more + [point]
        return None


def _via_clears_pipeline_pads(
    topo: Topology, point: Point, net_id: str, drill_mm: float
) -> bool:
    """Would ``circuitpy.checks.dfm_hole_clearance`` accept a via here?

    ``Workspace`` measures a rotated pad correctly and the pipeline does not, so
    the two disagree about a pad turned 90 degrees by up to its own length. The
    pipeline is the gate that decides ``fab.ready``, so a via has to satisfy
    both — the stricter of two models is the only one that ships. Same net is
    exempt in both, for the same reason: the drill's own pad is already there.
    """
    limit = topo.rules.min_via_to_copper_mm
    hole = disc_capsule(point.x, point.y, drill_mm)
    for capsule, (pad_net, _pad_id) in topo.pipeline_pads.query(hole, limit + 0.1):
        if pad_net is not None and pad_net == net_id:
            continue
        if capsule_gap(hole, capsule) < limit - 1e-9:
            return False
    return True


def _fresh_workspace(
    problem: RoutingProblem, traces: Sequence[Trace], vias: Sequence[Via]
) -> Workspace:
    """A Workspace holding only the copper listed. ``Workspace`` can add copper
    and not remove it, so rolling a failed net back means building a new one."""
    ws = Workspace(problem)
    for via in vias:
        ws.commit_via(via)
    for trace in traces:
        ws.commit_trace(trace)
    return ws


# ===========================================================================
# Standalone runner
# ===========================================================================


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="topological-graph router")
    parser.add_argument("--only", default=None, help="comma-separated instance ids")
    parser.add_argument("--max-iterations", type=int, default=2_000_000)
    parser.add_argument("--max-nodes", type=int, default=20_000_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--rip-up-passes", type=int, default=None)
    parser.add_argument("--no-determinism", action="store_true")
    parser.add_argument("--report", default=None)
    parser.add_argument(
        "--analysis", action="store_true",
        help="print the crossing analysis instead of routing",
    )
    args = parser.parse_args(argv)

    from routerlib.bench import load_all, run_suite

    problems = load_all()
    if args.only:
        wanted = set(args.only.split(","))
        problems = [p for p in problems if p.id in wanted]

    if args.analysis:
        rows = [crossing_analysis(p) for p in problems]
        header = (
            f"{'instance':<46}{'nets':>6}{'xings':>7}{'pairs':>7}"
            f"{'1layer':>8}{'2L novia':>10}{'vias>=':>8}"
        )
        print(header)
        print("-" * len(header))
        for row in rows:
            print(
                f"{row['instance']:<46}{row['routableNets']:>6}"
                f"{row['segmentCrossings']:>7}{row['netPairsCrossing']:>7}"
                f"{str(row['singleLayerFeasible']):>8}"
                f"{str(row['twoLayerViaFreeFeasible']):>10}"
                f"{row['viaLowerBound']:>8}"
            )
        if args.report:
            Path(args.report).write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
            print(f"wrote {args.report}")
        return 0

    budget = Budget(
        max_iterations=args.max_iterations,
        max_nodes=args.max_nodes,
        seed=args.seed,
    )
    report = run_suite(
        lambda: TopologicalRouter(rip_up_passes=args.rip_up_passes),
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
    return 0 if all(r.get("deterministic", True) for r in report.rows) else 1


ROUTERS = {TopologicalRouter.name: TopologicalRouter}

__all__ = [
    "Gate",
    "ROUTERS",
    "Site",
    "Topology",
    "TopologicalRouter",
    "build_sites",
    "crossing_analysis",
    "delaunay",
    "topology_for",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
