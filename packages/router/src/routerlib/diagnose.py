"""When the router cannot finish, say what it needs — measured, not guessed.

A board with seven unconnected nets currently produces seven findings and a
person who has to guess. That is the wrong output. The right output is a
**request**: *these four nets failed, they all fail at the same 0.42mm gap
between U3 and C7, and that gap needs 0.70mm — moving C7 0.3mm north opens it.*

Everything in this module exists to make a sentence like that true rather than
plausible. Three rules it is built to keep, in order:

1. **Every claim carries the measurement behind it.** A named obstacle comes
   from an argmin over real shapes; a gap comes from
   :func:`routerlib.geometry.capsule_gap` on the two shapes named; a suggested
   move is *performed* — the part is translated, the geometry re-measured, and
   the suggestion is only emitted when the new numbers clear the rule.
2. **The optimistic bound decides whether we claim a blockage.** The channel
   search runs on a grid, and a grid can miss a channel it did not sample. So
   the grid's answer is only ever used to say "I could not find a way through
   at this sampling", never "there is no way through", and the number that goes
   in front of a person is the exact one measured between two named shapes.
3. **"I cannot tell you why" is a finished answer.** A net whose failure cannot
   be attributed to geometry is reported as unattributed, with what was ruled
   out. Inventing a helpful-sounding cause is the one failure this module must
   not have, because the person reading it has no way to check.

The measurement, in one paragraph
---------------------------------

For a net, every piece of copper that is *not* its own is an obstacle, and each
obstacle demands a clearance (0.10mm for copper, 0.20 or 0.28mm for a hole,
0.20mm for the board edge). Rasterise ``room(p) = min over obstacles of
(distance(p, o) - clearance(o))`` — the widest half-trace that could be centred
at ``p``. Sort the cells by ``room`` descending and add them to a union-find in
that order: the level at which the net's disconnected fragments all join is the
**widest channel** that exists between them. Compare it against the half-width
the net needs. If it falls short, walk the best path, find the narrowest cell,
name the two shapes that pinch it, and measure their gap exactly.

``room`` is 1-Lipschitz in position, so a grid of pitch ``r`` knows the true
channel to within ``r/sqrt(2)``. That tolerance is carried through every
comparison and printed with every number, which is why the grid may say
"blocked" only when the *optimistic* bound also falls short.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from routerlib.connectivity import analyse, pad_components
from routerlib.geometry import (
    Capsule,
    GridIndex,
    Shape,
    capsule_bbox,
    capsule_gap,
    disc_capsule,
    pad_capsule,
    point_shape_distance,
    segment_capsule,
    PolygonIndex,
)
from routerlib.model import (
    BOTTOM,
    TOP,
    Drill,
    Pad,
    Point,
    RoutingProblem,
    RoutingSolution,
)

#: The room the field bothers to measure. Beyond this a cell is "open enough"
#: for anything we ask about, and clamping keeps the raster local: an obstacle
#: only stamps the cells within ``clearance + ROOM_CAP_MM`` of itself.
ROOM_CAP_MM = 0.60

#: Channel levels are bucketed to this before the union-find walks them. Fab
#: numbers live at 0.01mm; a finer bucket buys nothing and costs a pass.
LEVEL_STEP_MM = 0.005

#: How far below legal the search will still follow a channel. Without this the
#: answer for a pad whose only exit is 0.02mm too tight is "no gap of any width
#: reaches these pads", which is both alarming and false. Going negative lets
#: the report say the true thing instead: *the only way out is 0.02mm narrower
#: than the fab allows.*
FLOOR_MM = -0.30

#: A cell this far below zero is off the board. Kept as a single sentinel so
#: "outside" is one comparison rather than a polygon test in the inner loop.
BLOCKED = -1.0e9


# ---------------------------------------------------------------------------
# Obstacles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Obstacle:
    """One thing copper has to keep away from, and how far away.

    ``owner`` is what a person would move — a reference designator where there
    is one, a net where the obstacle is copper, ``"board"`` for the edge. It is
    the join key for every suggestion in this module: two obstacles with the
    same owner are one thing to a person, however many shapes they are.
    """

    id: str
    kind: str  # pad | trace | via | drill | keepout | edge
    owner: str
    label: str
    net: str | None
    layers: tuple[str, ...]
    capsule: Capsule
    clearance_mm: float
    component: str = ""
    #: The net's readable name, kept so two pads of one part can be told apart
    #: in a sentence. "the gap between R21 and R21" is not a sentence.
    net_name: str = ""

    @property
    def movable(self) -> bool:
        """A part can be moved. Copper is re-routed, not moved, and the board
        edge and a keepout are decisions rather than objects."""
        return bool(self.component)

    @property
    def wall(self) -> str:
        """What this obstacle is *one side of*.

        Two shapes on the same net are one wall however many parts they belong
        to — a pad and the trace leaving it are not a channel, they are a
        connection. Measuring the "gap" between them reports a large negative
        number and names a pinch that does not exist, which is exactly what the
        first run of this module did.
        """
        return self.net or self.owner


def _net_name(problem: RoutingProblem, net_id: str | None) -> str:
    if not net_id:
        return ""
    net = problem.nets_by_id.get(net_id)
    return net.name if net else net_id


def _pad_label(pad: Pad) -> str:
    return pad.component or pad.id


def build_obstacles(
    problem: RoutingProblem, solution: RoutingSolution
) -> list[Obstacle]:
    """Everything on the board that copper must respect, in a stable order.

    Pads, the copper the solution laid down, holes, keepouts and the board
    outline — each with the clearance the fab rules demand of *that* class of
    thing. Three numbers for copper-to-hole, not one, which is the reason this
    package exists (:mod:`routerlib.model`, ``DesignRules.hole_clearance``).
    """
    rules = problem.rules
    out: list[Obstacle] = []
    for pad in problem.pads:
        out.append(
            Obstacle(
                id=f"pad:{pad.id}",
                kind="pad",
                owner=pad.component or f"net:{pad.net or pad.id}",
                label=_pad_label(pad),
                net=pad.net,
                layers=tuple(pad.layers),
                capsule=pad_capsule(pad),
                clearance_mm=rules.min_clearance_mm,
                component=pad.component,
                net_name=_net_name(problem, pad.net),
            )
        )
    for source, tag in (
        (problem.existing_traces, "existing"),
        (solution.traces, "trace"),
    ):
        for trace in source:
            for index, (a, b) in enumerate(trace.segments):
                out.append(
                    Obstacle(
                        id=f"{tag}:{trace.id}#{index}",
                        kind="trace",
                        owner=f"net:{trace.net}",
                        label=_net_name(problem, trace.net) or trace.net,
                        net=trace.net,
                        layers=(trace.layer,),
                        capsule=segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm),
                        clearance_mm=rules.min_clearance_mm,
                    )
                )
    for source, tag in (
        (problem.existing_vias, "existing_via"),
        (solution.vias, "via"),
    ):
        for via in source:
            out.append(
                Obstacle(
                    id=f"{tag}:{via.id}",
                    kind="via",
                    owner=f"net:{via.net}",
                    label=_net_name(problem, via.net) or via.net,
                    net=via.net,
                    layers=(TOP, BOTTOM),
                    capsule=disc_capsule(via.center.x, via.center.y, via.pad_mm),
                    clearance_mm=rules.min_clearance_mm,
                )
            )
            out.append(
                Obstacle(
                    id=f"{tag}_hole:{via.id}",
                    kind="drill",
                    owner=f"net:{via.net}",
                    label=_net_name(problem, via.net) or via.net,
                    net=via.net,
                    layers=(TOP, BOTTOM),
                    capsule=disc_capsule(via.center.x, via.center.y, via.drill_mm),
                    clearance_mm=rules.min_via_to_copper_mm,
                )
            )
    for drill in problem.drills:
        out.append(
            Obstacle(
                id=f"drill:{drill.id}",
                kind="drill",
                owner=drill.component or f"hole {drill.id}",
                label=drill.component or f"hole {drill.id}",
                net=drill.net,
                layers=(TOP, BOTTOM),
                capsule=_drill_shape(drill),
                clearance_mm=rules.hole_clearance(drill),
                component=drill.component,
            )
        )
    for keepout in problem.keepouts:
        from routerlib.geometry import keepout_capsule

        out.append(
            Obstacle(
                id=f"keepout:{keepout.id}",
                kind="keepout",
                owner=f"keepout:{keepout.id}",
                label="a no-copper area",
                net=None,
                layers=tuple(keepout.layers),
                capsule=keepout_capsule(keepout),
                clearance_mm=0.0,
                component="",
            )
        )
    outline = problem.board.outline
    if len(outline) >= 3:
        for index in range(len(outline)):
            a = outline[index]
            b = outline[(index + 1) % len(outline)]
            if a.x == b.x and a.y == b.y:
                continue
            out.append(
                Obstacle(
                    id=f"edge:{index}",
                    kind="edge",
                    owner="board",
                    label="the board edge",
                    net=None,
                    layers=(TOP, BOTTOM),
                    capsule=segment_capsule(a.x, a.y, b.x, b.y, 0.0),
                    clearance_mm=rules.min_edge_clearance_mm,
                )
            )
    return out


def _drill_shape(drill: Drill) -> Capsule:
    from routerlib.geometry import drill_capsule

    return drill_capsule(drill)


def _translate(capsule: Capsule, dx: float, dy: float) -> Capsule:
    """The same shape, moved. Cores move with it — a rectangle that kept its
    stadium and dropped its corners would be the pad-model bug again."""
    core = getattr(capsule, "core", None)
    ax, ay, bx, by, r = capsule[0], capsule[1], capsule[2], capsule[3], capsule[4]
    if core is None:
        return (ax + dx, ay + dy, bx + dx, by + dy, r)
    return Shape(
        ax + dx,
        ay + dy,
        bx + dx,
        by + dy,
        r,
        tuple((px + dx, py + dy) for px, py in core),
        capsule.sweep,
    )


# ---------------------------------------------------------------------------
# The room field
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """``room`` on a grid: the widest half-trace that fits at each cell.

    Two layers, one flat list each, row-major. ``tolerance_mm`` is the honest
    error bar: ``room`` is 1-Lipschitz, cell centres are at most ``r/sqrt(2)``
    from any point, so every level this grid reports is within that of the
    truth. Nothing downstream is allowed to forget it.
    """

    x0: float
    y0: float
    cols: int
    rows: int
    step: float
    room: dict[str, list[float]]
    tolerance_mm: float

    @property
    def window(self) -> tuple[float, float, float, float]:
        return (
            self.x0,
            self.y0,
            self.x0 + self.cols * self.step,
            self.y0 + self.rows * self.step,
        )

    def index(self, x: float, y: float) -> int | None:
        ix = int((x - self.x0) / self.step)
        iy = int((y - self.y0) / self.step)
        if ix < 0 or iy < 0 or ix >= self.cols or iy >= self.rows:
            return None
        return iy * self.cols + ix

    def centre(self, cell: int) -> tuple[float, float]:
        iy, ix = divmod(cell, self.cols)
        return (self.x0 + (ix + 0.5) * self.step, self.y0 + (iy + 0.5) * self.step)


def build_field(
    problem: RoutingProblem,
    obstacles: Sequence[Obstacle],
    *,
    exclude_net: str | None,
    window: tuple[float, float, float, float],
    step: float,
) -> Field:
    """Rasterise ``room`` over ``window``.

    ``exclude_net`` is the net being diagnosed: its own copper and pads are not
    obstacles to itself, and a plated hole carrying its net is exempt exactly as
    :class:`routerlib.workspace.Workspace` exempts it.

    Cost is bounded by the clamp: an obstacle only writes the cells within
    ``clearance + ROOM_CAP_MM`` of its bounding box, so the raster is a thin
    band around each shape rather than a sweep of the board.
    """
    x0, y0, x1, y1 = window
    cols = max(1, int(math.ceil((x1 - x0) / step)))
    rows = max(1, int(math.ceil((y1 - y0) / step)))
    room = {TOP: [ROOM_CAP_MM] * (cols * rows), BOTTOM: [ROOM_CAP_MM] * (cols * rows)}
    if problem.board.layer_count < 2:
        # A one-layer board has no back to escape to. Leaving the bottom plane
        # open would let the search walk under every obstacle on the board.
        room[BOTTOM] = [BLOCKED] * (cols * rows)

    for obstacle in obstacles:
        if exclude_net is not None and obstacle.net == exclude_net:
            # Same-net copper is a target, not an obstacle. A plated hole on
            # our own net is the pipeline's settled exemption.
            continue
        clearance = obstacle.clearance_mm
        reach = clearance + ROOM_CAP_MM
        bx0, by0, bx1, by1 = capsule_bbox(obstacle.capsule)
        ix0 = int((bx0 - reach - x0) / step)
        ix1 = int((bx1 + reach - x0) / step)
        iy0 = int((by0 - reach - y0) / step)
        iy1 = int((by1 + reach - y0) / step)
        if ix1 < 0 or iy1 < 0 or ix0 >= cols or iy0 >= rows:
            continue
        ix0 = max(0, ix0)
        iy0 = max(0, iy0)
        ix1 = min(cols - 1, ix1)
        iy1 = min(rows - 1, iy1)
        capsule = obstacle.capsule
        for layer in obstacle.layers:
            plane = room.get(layer)
            if plane is None:
                continue
            for iy in range(iy0, iy1 + 1):
                cy = y0 + (iy + 0.5) * step
                base = iy * cols
                for ix in range(ix0, ix1 + 1):
                    cx = x0 + (ix + 0.5) * step
                    value = point_shape_distance(cx, cy, capsule) - clearance
                    if value < plane[base + ix]:
                        plane[base + ix] = value

    # Off the board is not free space. Without this, a channel that does not
    # exist can be found by walking round the outside of the outline.
    if len(problem.board.outline) >= 3:
        index = PolygonIndex(problem.board.outline)
        for iy in range(rows):
            cy = y0 + (iy + 0.5) * step
            base = iy * cols
            for ix in range(cols):
                cell = base + ix
                if room[TOP][cell] <= 0.0 and room[BOTTOM][cell] <= 0.0:
                    continue
                if not index.contains(x0 + (ix + 0.5) * step, cy):
                    room[TOP][cell] = BLOCKED
                    room[BOTTOM][cell] = BLOCKED

    return Field(
        x0=x0,
        y0=y0,
        cols=cols,
        rows=rows,
        step=step,
        room=room,
        tolerance_mm=step / math.sqrt(2.0),
    )


# ---------------------------------------------------------------------------
# The widest channel
# ---------------------------------------------------------------------------


class _UnionFind:
    __slots__ = ("parent",)

    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, node: int) -> int:
        parent = self.parent
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Lower index wins, so component labels never depend on the order
            # cells arrived in.
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            self.parent[hi] = lo


@dataclass(frozen=True)
class ChannelResult:
    """How wide a channel joins a net's fragments, and where it narrows."""

    joined: bool
    level_mm: float
    tolerance_mm: float
    #: The last two fragments to join — the pair the whole net waits on.
    binding_pair: tuple[int, int] | None
    #: Cell index and layer of the narrowest point on the best path.
    pinch_cell: int | None
    pinch_layer: str
    pinch_room_mm: float
    #: The narrowest few points on the best path, each with the direction the
    #: path runs there: ``(cell, layer, room, along_x, along_y)``. The caller
    #: refines across each of them, because the grid's own value is only good
    #: to ``tolerance_mm`` and every number a person reads should be better.
    narrow: tuple[tuple[int, str, float, float, float], ...] = ()
    #: Bounding box in millimetres of the path the channel runs along. A second,
    #: finer pass over just this box measures the same corridor without paying
    #: for the rest of the board.
    path_bbox: tuple[float, float, float, float] | None = None


def _seed_cells(
    field: Field, pads: Sequence[Pad]
) -> dict[str, list[int]]:
    """The grid cells a fragment already owns — the copper of its own pads."""
    out: dict[str, list[int]] = {TOP: [], BOTTOM: []}
    for pad in pads:
        bx0, by0, bx1, by1 = capsule_bbox(pad_capsule(pad))
        ix0 = max(0, int((bx0 - field.x0) / field.step))
        ix1 = min(field.cols - 1, int((bx1 - field.x0) / field.step))
        iy0 = max(0, int((by0 - field.y0) / field.step))
        iy1 = min(field.rows - 1, int((by1 - field.y0) / field.step))
        capsule = pad_capsule(pad)
        hit = False
        for iy in range(iy0, iy1 + 1):
            cy = field.y0 + (iy + 0.5) * field.step
            for ix in range(ix0, ix1 + 1):
                cx = field.x0 + (ix + 0.5) * field.step
                if point_shape_distance(cx, cy, capsule) <= 0.0:
                    for layer in pad.layers:
                        if layer in out:
                            out[layer].append(iy * field.cols + ix)
                    hit = True
        if not hit:
            # A pad smaller than a cell still has to be reachable, or a
            # fine-pitch escape reads as "not on the board".
            cell = field.index(pad.center.x, pad.center.y)
            if cell is not None:
                for layer in pad.layers:
                    if layer in out:
                        out[layer].append(cell)
    return {layer: sorted(set(cells)) for layer, cells in out.items()}


def widest_channel(
    field: Field,
    fragments: Sequence[Sequence[Pad]],
    *,
    via_half_mm: float,
    floor_mm: float = FLOOR_MM,
) -> ChannelResult:
    """The level at which every fragment becomes one component.

    Kruskal on the room field: add cells in descending ``room``, union each with
    the neighbours already added, and after every level check whether the
    fragments have met. The first level at which they have is the widest channel
    between them — one pass answers every pair at once, which is why this is a
    sort and not a search per pair.

    A layer change is an edge between the two copies of a cell, admitted only at
    levels where a via's *pad* fits on both layers. A via that does not fit is
    not a channel.
    """
    cols, rows = field.cols, field.rows
    n = cols * rows
    top = field.room[TOP]
    bottom = field.room[BOTTOM]
    uf = _UnionFind(2 * n)

    # Fragment seeds are copper that already exists, so they are in from the
    # start whatever their room is: a trace may run over its own pad.
    seeds: list[int] = []
    roots_of: list[int] = []
    for pads in fragments:
        cells = _seed_cells(field, pads)
        first: int | None = None
        for layer, offset in ((TOP, 0), (BOTTOM, n)):
            for cell in cells[layer]:
                node = cell + offset
                seeds.append(node)
                if first is None:
                    first = node
                else:
                    uf.union(first, node)
        if first is None:
            return ChannelResult(
                False, FLOOR_MM, field.tolerance_mm, None, None, "", FLOOR_MM
            )
        roots_of.append(first)

    added = bytearray(2 * n)
    seed_of: dict[int, int] = {}
    for index, node in enumerate(seeds):
        added[node] = 1
    for index, pads in enumerate(fragments):
        cells = _seed_cells(field, pads)
        for layer, offset in ((TOP, 0), (BOTTOM, n)):
            for cell in cells[layer]:
                seed_of[cell + offset] = index

    # Bucket every cell by its level. Cells under the floor are never added:
    # nothing can pass there and walking them only costs time.
    buckets: dict[int, list[int]] = {}
    for cell in range(n):
        for value, offset in ((top[cell], 0), (bottom[cell], n)):
            if value < floor_mm:
                continue
            key = int(math.floor(value / LEVEL_STEP_MM))
            buckets.setdefault(key, []).append(cell + offset)

    def _link(node: int) -> None:
        # Eight neighbours, not four. ``room`` is 1-Lipschitz, so a grid of
        # pitch r knows the true channel to within r/sqrt(2) — but only if a
        # diagonal run of copper maps to a chain of adjacent cells. On four
        # neighbours a 45-degree channel has to be walked as a staircase
        # through cells that are not in it, and the error stops being bounded:
        # measured on hydrate-coaster as 0.02mm at a 0.08mm pitch against
        # 0.15mm at 0.05mm, three times the tolerance the bound allows.
        cell = node if node < n else node - n
        offset = 0 if node < n else n
        iy, ix = divmod(cell, cols)
        left = ix > 0
        right = ix + 1 < cols
        down = iy > 0
        up = iy + 1 < rows
        for delta, ok in (
            (-1, left),
            (1, right),
            (-cols, down),
            (cols, up),
            (-cols - 1, down and left),
            (-cols + 1, down and right),
            (cols - 1, up and left),
            (cols + 1, up and right),
        ):
            if ok and added[node + delta]:
                uf.union(node, node + delta)
        # The layer change, priced as a via rather than as a trace.
        other = cell + (n if offset == 0 else 0)
        if added[other] and min(top[cell], bottom[cell]) >= via_half_mm:
            uf.union(node, other)

    # Seeds are already present; wire them to each other before the walk so a
    # fragment sitting on top of another one is seen immediately.
    for node in seeds:
        _link(node)

    # Which pair of fragments is still apart, and at what level each pair meets.
    # With three or more islands the *last* pair to meet is the one the whole
    # net is waiting on, and it is not always the first two.
    pending = [
        (i, j)
        for i in range(len(fragments))
        for j in range(i + 1, len(fragments))
    ]

    def _still_apart() -> list[tuple[int, int]]:
        return [
            (i, j) for i, j in pending if uf.find(roots_of[i]) != uf.find(roots_of[j])
        ]

    pending = _still_apart()
    if not pending:
        return ChannelResult(
            True, ROOM_CAP_MM, field.tolerance_mm, None, None, "", ROOM_CAP_MM
        )

    binding: tuple[int, int] | None = None
    for key in sorted(buckets, reverse=True):
        for node in buckets[key]:
            if added[node]:
                continue
            added[node] = 1
            _link(node)
        remaining = _still_apart()
        if len(remaining) < len(pending):
            joined_now = [pair for pair in pending if pair not in remaining]
            binding = joined_now[0]
            pending = remaining
        if not pending:
            level = key * LEVEL_STEP_MM
            pair = binding or (0, 1)
            cell, layer, room_mm, narrow, bbox = _constrictions(
                field, level, fragments, pair, via_half_mm, seed_of
            )
            return ChannelResult(
                True, level, field.tolerance_mm, pair, cell, layer, room_mm,
                narrow, bbox,
            )

    return ChannelResult(
        False, FLOOR_MM, field.tolerance_mm, pending[0], None, "", FLOOR_MM
    )


def _constrictions(
    field: Field,
    level: float,
    fragments: Sequence[Sequence[Pad]],
    pair: tuple[int, int],
    via_half_mm: float,
    seed_of: dict[int, int],
) -> tuple[
    int | None,
    str,
    float,
    tuple[tuple[int, str, float, float, float], ...],
    tuple[float, float, float, float] | None,
]:
    """Where the channel is actually decided, not merely where a path happens
    to be narrow.

    The first version of this ran a breadth-first search at the channel level
    and took the narrowest cell on whatever path came back. A breadth-first
    search returns the fewest-hops path, which hugs walls, so the "pinch" it
    named was a cell 0.017mm from a via with a quarter of a millimetre of open
    board on its other side — a true measurement of an irrelevant place.

    The constriction is a topological fact and can be found as one. Flood both
    fragments at one level **above** the channel: they cannot meet, by
    definition. The cells that then join the two floods are exactly the ones the
    channel waits on, and every one of them is a place worth naming.
    """
    cols, rows = field.cols, field.rows
    n = cols * rows
    top = field.room[TOP]
    bottom = field.room[BOTTOM]

    def _room(node: int) -> float:
        return top[node] if node < n else bottom[node - n]

    def _neighbours(node: int) -> list[int]:
        cell = node if node < n else node - n
        offset = 0 if node < n else n
        iy, ix = divmod(cell, cols)
        left, right = ix > 0, ix + 1 < cols
        down, up = iy > 0, iy + 1 < rows
        out = [
            node + delta
            for delta, ok in (
                (-1, left),
                (1, right),
                (-cols, down),
                (cols, up),
                (-cols - 1, down and left),
                (-cols + 1, down and right),
                (cols - 1, up and left),
                (cols + 1, up and right),
            )
            if ok
        ]
        if min(top[cell], bottom[cell]) >= via_half_mm:
            out.append(cell + (n if offset == 0 else 0))
        return out

    above = level + LEVEL_STEP_MM

    def _flood(index: int) -> set[int]:
        cells = _seed_cells(field, fragments[index])
        stack = [
            cell + offset
            for layer, offset in ((TOP, 0), (BOTTOM, n))
            for cell in cells[layer]
        ]
        seen = set(stack)
        while stack:
            node = stack.pop()
            for nb in _neighbours(node):
                if nb in seen:
                    continue
                if nb not in seed_of and _room(nb) < above:
                    continue
                seen.add(nb)
                stack.append(nb)
        return seen

    side_a = _flood(pair[0])
    side_b = _flood(pair[1])

    # A constriction is rarely one cell wide along its length, so no single cell
    # bridges the two floods. Take the whole band at the channel level, split it
    # into connected pieces, and keep the pieces that touch both sides: those
    # are the bridges, and every cell in one is a place the channel waits on.
    band = [
        node
        for node in range(2 * n)
        if node not in seed_of and level - 1e-12 <= _room(node) < above
    ]
    in_band = set(band)
    seen: set[int] = set()
    found: list[tuple[float, int, str, float, float]] = []
    for start in band:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        piece: list[int] = []
        touches_a: int | None = None
        touches_b: int | None = None
        while stack:
            node = stack.pop()
            piece.append(node)
            for nb in _neighbours(node):
                if nb in in_band:
                    if nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
                    continue
                if touches_a is None and nb in side_a:
                    touches_a = nb
                if touches_b is None and nb in side_b:
                    touches_b = nb
        if touches_a is None or touches_b is None:
            continue
        ax, ay = (touches_a % n) % cols, (touches_a % n) // cols
        bx, by = (touches_b % n) % cols, (touches_b % n) // cols
        along = (float(bx - ax), float(by - ay))
        if abs(along[0]) < 1e-9 and abs(along[1]) < 1e-9:
            along = (1.0, 0.0)
        for node in piece:
            cell = node if node < n else node - n
            layer = TOP if node < n else BOTTOM
            found.append((_room(node), cell, layer, along[0], along[1]))

    if not found:
        return (None, "", 0.0, (), None)
    found.sort(key=lambda row: (row[0], row[1], row[2]))
    narrow = tuple(
        (cell, layer, room, ax, ay) for room, cell, layer, ax, ay in found[:40]
    )
    xs = [field.centre(cell)[0] for _, cell, _, _, _ in found]
    ys = [field.centre(cell)[1] for _, cell, _, _, _ in found]
    for index in pair:
        for pad in fragments[index]:
            xs.append(pad.center.x)
            ys.append(pad.center.y)
    bbox = (min(xs), min(ys), max(xs), max(ys))
    room, cell, layer, _, _ = found[0]
    return (cell, layer, room, narrow, bbox)


# ---------------------------------------------------------------------------
# Naming what pinched
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pinch:
    """The narrowest point on the best path, named and measured exactly.

    Neither number here is a grid number. The grid finds *where* the corridor
    narrows; the point is then refined across the corridor until the largest
    disc that fits is found, and ``usable_mm`` is twice its radius — the widest
    trace that could actually run there. ``gap_mm`` is the distance between the
    two walls through that point.

    Keeping these exact matters more than it sounds. The first version reported
    the closest approach of the two named shapes anywhere on the board, which on
    a real board was 0.27mm at a spot 4mm away from the corridor it was
    describing, and produced the sentence "cannot get through the 0.27mm gap"
    about a net 0.20mm wide.
    """

    x: float
    y: float
    layer: str
    first: Obstacle
    second: Obstacle | None
    gap_mm: float
    usable_mm: float

    def as_dict(self) -> dict:
        return {
            "at": [round(self.x, 4), round(self.y, 4)],
            "layer": self.layer,
            "between": [
                {
                    "label": self.first.label,
                    "kind": self.first.kind,
                    "owner": self.first.owner,
                    "part": self.first.component,
                }
            ]
            + (
                [
                    {
                        "label": self.second.label,
                        "kind": self.second.kind,
                        "owner": self.second.owner,
                        "part": self.second.component,
                    }
                ]
                if self.second
                else []
            ),
            "gapMm": round(self.gap_mm, 4),
            "usableMm": round(self.usable_mm, 4),
        }


def _obstacle_index(obstacles: Sequence[Obstacle]) -> dict[str, GridIndex]:
    grids: dict[str, GridIndex] = {TOP: GridIndex(2.0), BOTTOM: GridIndex(2.0)}
    for obstacle in obstacles:
        for layer in obstacle.layers:
            grids.setdefault(layer, GridIndex(2.0)).insert(obstacle.capsule, obstacle)
    return grids


def _nearby(
    grids: dict[str, GridIndex],
    x: float,
    y: float,
    layer: str,
    radius: float,
    exclude_net: str | None,
) -> list[Obstacle]:
    probe = disc_capsule(x, y, 2.0 * radius)
    out: list[Obstacle] = []
    for _, obstacle in grids.get(layer, GridIndex(2.0)).query(probe, radius):
        if exclude_net is not None and obstacle.net == exclude_net:
            continue
        out.append(obstacle)
    return out


def _room_at(x: float, y: float, obstacles: Sequence[Obstacle]) -> float:
    best = math.inf
    for obstacle in obstacles:
        value = point_shape_distance(x, y, obstacle.capsule) - obstacle.clearance_mm
        if value < best:
            best = value
    return best


def refine_pinch(
    grids: dict[str, GridIndex],
    x: float,
    y: float,
    layer: str,
    along: tuple[float, float],
    *,
    exclude_net: str | None,
    span_mm: float,
    steps: int = 41,
) -> tuple[float, float, float]:
    """Slide across the corridor and stop at its widest point.

    The grid finds a cell inside the constriction; the true narrowest
    *cross-section* is the largest disc that fits there, and the grid centre is
    almost never at its middle. Sampling the perpendicular line with the exact
    distance function costs a few hundred evaluations and removes the sampling
    error from every number that reaches a person.
    """
    px, py = -along[1], along[0]
    norm = math.hypot(px, py)
    if norm < 1e-12:
        return (x, y, _room_at(x, y, _nearby(grids, x, y, layer, 2.0, exclude_net)))
    px, py = px / norm, py / norm
    near = _nearby(grids, x, y, layer, span_mm + 2.0 * ROOM_CAP_MM, exclude_net)
    best = (x, y, _room_at(x, y, near))
    fine = 2.0 * span_mm / max(steps - 1, 1)
    # Climb to the **first** maximum on each side, not the best value in the
    # whole span. A pinch cell that happens to sit beside a single obstacle with
    # open board behind it has a monotonically rising room, and taking the span
    # maximum walks the measurement out of the corridor it is supposed to be
    # describing — measured on hydrate-coaster as a 0.043mm channel "refined"
    # to 0.42mm, which is the width of the empty board next to it.
    for sign in (1.0, -1.0):
        previous = best[2] if sign > 0 else _room_at(x, y, near)
        for index in range(1, steps):
            offset = sign * fine * index
            sx, sy = x + px * offset, y + py * offset
            value = _room_at(sx, sy, near)
            if value <= previous:
                break
            previous = value
            if value > best[2]:
                best = (sx, sy, value)
    return best


def name_pinch(
    obstacles: Sequence[Obstacle],
    grids: dict[str, GridIndex],
    x: float,
    y: float,
    layer: str,
    *,
    exclude_net: str | None,
) -> Pinch | None:
    """The two walls that decide the corridor here, and what fits between them.

    Two shapes on the same net are one wall, and two shapes of one part are one
    thing to a person, so the second name is the nearest obstacle that is
    neither. When there is only one — a pad against open board — the pinch is
    reported with one name, which usually means the corridor is against the
    board edge or a keepout rather than between two parts.
    """
    near = _nearby(grids, x, y, layer, 2.0 * ROOM_CAP_MM + 0.5, exclude_net)
    if not near:
        return None
    ranked = sorted(
        (
            (point_shape_distance(x, y, o.capsule) - o.clearance_mm, o.id, o)
            for o in near
        ),
        key=lambda row: (row[0], row[1]),
    )
    first = ranked[0][2]
    room = ranked[0][0]
    # The other side of a corridor faces it. Two tests, both geometric and both
    # necessary:
    #
    #   * it has to be **binding** — within 0.03mm of holding the corridor down
    #     as tightly as the first wall does. A shape a millimetre further away
    #     is not a side of anything, and naming it produced "R21 and GND come
    #     within 1.105mm of each other, so 0.040mm is left": two true numbers
    #     that cannot both be about the same place.
    #   * its outward normal has to **oppose** the first one. That is what
    #     separates the two pads of an 0402 (a real channel under the body)
    #     from a pad and the trace soldered to it (one wall, and a gap between
    #     them that is a connection rather than a corridor).
    normal = _outward(x, y, first.capsule)
    second: Obstacle | None = None
    second_room = math.inf
    for value, _, candidate in ranked[1:]:
        if value - room > 0.03:
            break
        if candidate.id == first.id:
            continue
        other = _outward(x, y, candidate.capsule)
        if normal[0] * other[0] + normal[1] * other[1] > -0.2:
            continue
        second = candidate
        second_room = value
        break
    if second is None:
        return Pinch(x, y, layer, first, None, math.inf, 2.0 * room)
    gap = (room + first.clearance_mm) + (second_room + second.clearance_mm)
    if first.label == second.label:
        first = _relabel(first)
        second = _relabel(second)
    return Pinch(x, y, layer, first, second, gap, 2.0 * room)


def _relabel(obstacle: Obstacle) -> Obstacle:
    """Two pads of one part, told apart by what they carry."""
    from dataclasses import replace as _replace

    if not obstacle.net_name or obstacle.net_name in obstacle.label:
        return obstacle
    return _replace(obstacle, label=f"{obstacle.label}'s {obstacle.net_name} pad")


# ---------------------------------------------------------------------------
# Congestion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Congestion:
    """How packed the neighbourhood of a pinch is, in numbers only.

    ``free_fraction`` is the share of the sampled disc where the net could
    legally sit; ``tightest_gap_mm`` is the closest approach between two
    *different* parts in it. "These three parts are packed at 1.2mm" is that
    second number and the names that produced it.
    """

    radius_mm: float
    free_fraction: float
    parts: tuple[str, ...]
    tightest_gap_mm: float
    tightest_between: tuple[str, str] | None

    def as_dict(self) -> dict:
        return {
            "radiusMm": self.radius_mm,
            "freeFraction": round(self.free_fraction, 4),
            "parts": list(self.parts),
            "tightestGapMm": (
                None
                if not math.isfinite(self.tightest_gap_mm)
                else round(self.tightest_gap_mm, 4)
            ),
            "tightestBetween": list(self.tightest_between)
            if self.tightest_between
            else None,
        }


def measure_congestion(
    field: Field,
    obstacles: Sequence[Obstacle],
    grids: dict[str, GridIndex],
    x: float,
    y: float,
    layer: str,
    needed_half_mm: float,
    *,
    radius_mm: float = 3.0,
) -> Congestion:
    plane = field.room.get(layer) or []
    total = 0
    free = 0
    span = int(radius_mm / field.step)
    centre = field.index(x, y)
    if centre is not None and plane:
        iy0, ix0 = divmod(centre, field.cols)
        for iy in range(max(0, iy0 - span), min(field.rows, iy0 + span + 1)):
            for ix in range(max(0, ix0 - span), min(field.cols, ix0 + span + 1)):
                dx = (ix - ix0) * field.step
                dy = (iy - iy0) * field.step
                if dx * dx + dy * dy > radius_mm * radius_mm:
                    continue
                total += 1
                if plane[iy * field.cols + ix] >= needed_half_mm:
                    free += 1

    probe = disc_capsule(x, y, 2.0 * radius_mm)
    nearby: dict[str, list[Obstacle]] = {}
    for _, obstacle in grids.get(layer, GridIndex(2.0)).query(probe, radius_mm):
        if not obstacle.component:
            continue
        if point_shape_distance(x, y, obstacle.capsule) > radius_mm:
            continue
        nearby.setdefault(obstacle.component, []).append(obstacle)
    parts = sorted(nearby)
    tightest = math.inf
    between: tuple[str, str] | None = None
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            for a in nearby[parts[i]]:
                for b in nearby[parts[j]]:
                    gap = capsule_gap(a.capsule, b.capsule)
                    if gap < tightest:
                        tightest = gap
                        between = (parts[i], parts[j])
    return Congestion(
        radius_mm=radius_mm,
        free_fraction=(free / total) if total else 0.0,
        parts=tuple(parts),
        tightest_gap_mm=tightest,
        tightest_between=between,
    )


# ---------------------------------------------------------------------------
# Trying the move rather than proposing it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    """A move that was **performed and re-measured**, never one that was
    reasoned about. ``after_usable_mm`` is the gap after the part was
    translated and the geometry taken again; ``headroom_mm`` is how far the
    part could go before it hits something else, found by trying."""

    part: str
    dx_mm: float
    dy_mm: float
    distance_mm: float
    heading: str
    after_usable_mm: float
    headroom_mm: float
    #: What was checked and what was not. Printed with the suggestion.
    caveat: str

    def as_dict(self) -> dict:
        return {
            "part": self.part,
            "dxMm": round(self.dx_mm, 3),
            "dyMm": round(self.dy_mm, 3),
            "distanceMm": round(self.distance_mm, 3),
            "heading": self.heading,
            "afterUsableMm": round(self.after_usable_mm, 4),
            "headroomMm": round(self.headroom_mm, 3),
            "caveat": self.caveat,
        }


_COMPASS = (
    (0.0, "east"),
    (45.0, "north-east"),
    (90.0, "north"),
    (135.0, "north-west"),
    (180.0, "west"),
    (225.0, "south-west"),
    (270.0, "south"),
    (315.0, "south-east"),
)


def _heading(dx: float, dy: float) -> str:
    angle = math.degrees(math.atan2(dy, dx)) % 360.0
    best = min(_COMPASS, key=lambda row: min(abs(angle - row[0]), 360 - abs(angle - row[0])))
    return best[1]


def _outward(x: float, y: float, capsule: Capsule) -> tuple[float, float]:
    """Unit vector pointing away from a shape at a point, by finite difference
    on the exact distance function. Four evaluations, no assumption about what
    the shape is."""
    h = 1e-3
    dx = point_shape_distance(x + h, y, capsule) - point_shape_distance(x - h, y, capsule)
    dy = point_shape_distance(x, y + h, capsule) - point_shape_distance(x, y - h, capsule)
    norm = math.hypot(dx, dy)
    if norm < 1e-12:
        return (0.0, 0.0)
    return (dx / norm, dy / norm)


def _part_shapes(obstacles: Sequence[Obstacle], part: str) -> list[Obstacle]:
    return [o for o in obstacles if o.component == part]


def owned_keepouts(
    obstacles: Sequence[Obstacle], grids: dict[str, GridIndex], part: str, rules
) -> set[str]:
    """Which no-copper areas travel with a part, inferred from geometry.

    circuit.json's ``pcb_keepout`` carries no owner, so nothing in the file says
    that the 7.3 x 1.23mm rectangle across a USB-C socket belongs to that
    socket. Its pads sitting inside it does say so: a keepout the part already
    violates at rest is a keepout the part brought with it, and moving the part
    moves it too. Without this, every USB-C connector on the benchmark reads as
    a part that cannot be nudged by 0.05mm in any direction — which is true of
    the model and false of the board.
    """
    shapes = _part_shapes(obstacles, part)
    owned: set[str] = set()
    for shape in shapes:
        for layer in shape.layers:
            for _, other in grids.get(layer, GridIndex(2.0)).query(shape.capsule, 0.2):
                if other.kind != "keepout":
                    continue
                if capsule_gap(shape.capsule, other.capsule) < rules.min_clearance_mm:
                    owned.add(other.id)
    return owned


def _required_between(a: Obstacle, b: Obstacle, rules) -> float:
    if a.kind == "drill" and b.kind == "drill":
        return rules.min_hole_to_hole_mm
    if a.kind == "drill":
        return a.clearance_mm
    if b.kind == "drill":
        return b.clearance_mm
    if a.kind == "edge" or b.kind == "edge":
        return rules.min_edge_clearance_mm
    return rules.min_clearance_mm


def try_move(
    problem: RoutingProblem,
    obstacles: Sequence[Obstacle],
    grids: dict[str, GridIndex],
    pinch: Pinch,
    part: str,
    direction: tuple[float, float],
    needed_mm: float,
    *,
    steps: int = 8,
    step_mm: float = 0.0,
) -> Move | None:
    """Translate ``part`` and measure. Returns the smallest move that both
    widens the pinch past ``needed_mm`` **and** leaves the part legal, or None.

    The part is moved for real: every shape it owns is translated and re-checked
    against every other shape on the board at the clearance that pair demands.
    A move that opens the channel and buries a pad in a neighbour is not a
    suggestion, it is a different defect.
    """
    if not part:
        return None
    dx, dy = direction
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None
    shapes = _part_shapes(obstacles, part)
    if not shapes:
        return None
    anchor = None
    if pinch.second is not None:
        if pinch.first.component == part:
            anchor = pinch.second
        elif pinch.second.component == part:
            anchor = pinch.first
    owned = owned_keepouts(obstacles, grids, part, problem.rules)
    if anchor is not None and anchor.kind == "keepout":
        # Moving a part away from a keepout it carries opens a gap on paper and
        # nothing at all on the board. When the keepout is *not* the part's own,
        # there is still no owner recorded anywhere, so the move stays unsaid
        # either way — but the two cases deserve different sentences.
        return None
    moving_ids = {shape.id for shape in shapes}
    others = [o for o in obstacles if o.id not in moving_ids]
    rules = problem.rules

    # Measure the corridor the same way it was measured in the first place —
    # the largest disc that fits at the pinch — rather than the gap between two
    # named shapes. It is the same number when both sides bind, and it is the
    # only number available when only one does.
    near = _nearby(grids, pinch.x, pinch.y, pinch.layer, 3.0, exclude_net=None)
    near = [o for o in near if o.id not in moving_ids]
    mine = [
        shape
        for shape in shapes
        if pinch.layer in shape.layers
        and point_shape_distance(pinch.x, pinch.y, shape.capsule) < 3.0
    ]
    if not mine:
        return None

    shortfall = max(needed_mm - pinch.usable_mm, 0.0)
    increment = step_mm if step_mm > 0 else max(0.05, math.ceil(shortfall * 20) / 20)
    headroom = 0.0
    best: Move | None = None
    for index in range(1, steps + 1):
        distance = round(increment * index, 4)
        ox, oy = dx * distance, dy * distance
        if not _part_legal(shapes, others, grids, rules, ox, oy, problem, owned):
            break
        headroom = distance
        # Scan across the corridor, not at the point it used to be narrowest.
        # When a part slides away the corridor's middle slides with it, and
        # measuring at the old centre reports the fixed wall's number for ever
        # — which is how the first version concluded that no move on any of the
        # sixteen benchmark boards changed anything.
        moved = [_translate(shape.capsule, ox, oy) for shape in mine]
        room = -math.inf
        for index in range(-20, 21):
            offset = (distance + 0.2) * index / 20.0
            sx, sy = pinch.x + dx * offset, pinch.y + dy * offset
            value = min(
                [_room_at(sx, sy, near)]
                + [
                    point_shape_distance(sx, sy, capsule) - shape.clearance_mm
                    for capsule, shape in zip(moved, mine)
                ]
            )
            if value > room:
                room = value
        if best is None and 2.0 * room >= needed_mm:
            best = Move(
                part=part,
                dx_mm=ox,
                dy_mm=oy,
                distance_mm=distance,
                heading=_heading(dx, dy),
                after_usable_mm=2.0 * room,
                headroom_mm=distance,
                caveat=(
                    "the corridor and the moved part's own clearances were "
                    "re-measured; whether the net then routes was not"
                ),
            )
    if best is None:
        return None
    return Move(
        part=best.part,
        dx_mm=best.dx_mm,
        dy_mm=best.dy_mm,
        distance_mm=best.distance_mm,
        heading=best.heading,
        after_usable_mm=best.after_usable_mm,
        headroom_mm=headroom,
        caveat=best.caveat,
    )


def _part_legal(
    shapes: Sequence[Obstacle],
    others: Sequence[Obstacle],
    grids: dict[str, GridIndex],
    rules,
    dx: float,
    dy: float,
    problem: RoutingProblem,
    owned: frozenset[str] | set[str] = frozenset(),
) -> bool:
    outline = (
        PolygonIndex(problem.board.outline)
        if len(problem.board.outline) >= 3
        else None
    )
    for shape in shapes:
        moved = _translate(shape.capsule, dx, dy)
        if outline is not None:
            if outline.clearance(moved, rules.min_edge_clearance_mm) < rules.min_edge_clearance_mm:
                return False
        for layer in shape.layers:
            for _, other in grids.get(layer, GridIndex(2.0)).query(moved, 0.5):
                if other.component and other.component == shape.component:
                    continue
                if other.kind == "edge":
                    continue
                # Copper is not an obstacle to a *placement* change. Moving a
                # part re-routes the board, so a trace the part would land on
                # is a trace that will not be there. Checking against it
                # rejected every move on all sixteen benchmark boards: on a
                # dense board something is always routed past a pad at the
                # minimum clearance, and 0.05mm in any direction touches it.
                if other.kind in ("trace", "via"):
                    continue
                if other.kind == "keepout" and other.id in owned:
                    continue
                if other.net is not None and other.net == shape.net:
                    continue
                need = _required_between(shape, other, rules)
                if capsule_gap(moved, other.capsule) < need:
                    return False
    return True


# ---------------------------------------------------------------------------
# The diagnosis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NetDiagnosis:
    net: str
    name: str
    net_class: str
    width_mm: float
    pads: int
    fragments: int
    #: ``pinched`` | ``no_channel`` | ``router_limit`` | ``unattributed``
    verdict: str
    channel_mm: float
    tolerance_mm: float
    needed_mm: float
    pinch: Pinch | None
    #: True when even the grid's *optimistic* bound falls short of what the net
    #: needs. Only then may the report say there is no way through; otherwise
    #: it says this is the widest way through it found, which is a weaker and
    #: true claim.
    proven: bool = False
    congestion: Congestion | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "net": self.net,
            "name": self.name,
            "class": self.net_class,
            "widthMm": self.width_mm,
            "pads": self.pads,
            "fragments": self.fragments,
            "verdict": self.verdict,
            "channelMm": round(self.channel_mm, 4),
            "toleranceMm": round(self.tolerance_mm, 4),
            "neededMm": round(self.needed_mm, 4),
            "proven": self.proven,
            "pinch": self.pinch.as_dict() if self.pinch else None,
            "congestion": self.congestion.as_dict() if self.congestion else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Ask:
    """One request for a decision, with everything behind it.

    ``kind`` decides how it reads, and every kind is a measurement:

    ``move_part``   two named shapes pinch a channel and a tried move opens it
    ``tight_gap``   the same pinch, no move opened it — the numbers, no advice
    ``narrow_net``  the net is wider than the channel and thinner would fit
    ``router_limit`` there is room; the router did not find it
    ``unattributed`` the failure could not be tied to geometry
    """

    kind: str
    nets: tuple[str, ...]
    headline: str
    at: tuple[float, float] | None
    layer: str
    pinch: Pinch | None
    needed_mm: float
    move: Move | None
    congestion: Congestion | None
    evidence: tuple[str, ...]
    proven: bool = False

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "nets": list(self.nets),
            "headline": self.headline,
            "at": [round(self.at[0], 4), round(self.at[1], 4)] if self.at else None,
            "layer": self.layer,
            "pinch": self.pinch.as_dict() if self.pinch else None,
            "neededMm": round(self.needed_mm, 4),
            "proven": self.proven,
            "move": self.move.as_dict() if self.move else None,
            "congestion": self.congestion.as_dict() if self.congestion else None,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class Diagnosis:
    board: str
    routable_nets: int
    connected_nets: int
    resolution_mm: float
    nets: tuple[NetDiagnosis, ...]
    asks: tuple[Ask, ...]
    notes: tuple[str, ...]
    seconds: float = 0.0

    @property
    def complete(self) -> bool:
        return self.connected_nets >= self.routable_nets

    def as_dict(self) -> dict:
        return {
            "schema": "routing-help@1",
            "board": self.board,
            "routableNets": self.routable_nets,
            "connectedNets": self.connected_nets,
            "unroutedNets": self.routable_nets - self.connected_nets,
            "resolutionMm": self.resolution_mm,
            "nets": [n.as_dict() for n in self.nets],
            "asks": [a.as_dict() for a in self.asks],
            "notes": list(self.notes),
            "seconds": round(self.seconds, 2),
        }


@dataclass(frozen=True)
class Probe:
    """How hard to look. Counted in cells and tries, never in seconds — the
    same rule the routers obey, and for the same reason."""

    resolution_mm: float = 0.0  # 0 => choose from the window, see _resolution
    max_cells: int = 220_000
    window_margin_mm: float = 6.0
    congestion_radius_mm: float = 3.0
    move_steps: int = 8
    verify_moves: bool = True
    max_nets: int = 12


DEFAULT_PROBE = Probe()


def _resolution(
    window: tuple[float, float, float, float], probe: Probe, needed_mm: float
) -> float:
    """The grid pitch, chosen by what is being decided rather than by habit.

    A grid of pitch ``r`` knows the channel to within ``r/sqrt(2)``, so the
    pitch has to be small against the half-width the net needs or the answer is
    inside its own error bar — measured at 0.08mm against a 0.10mm requirement,
    where nothing could be proven either way. A wide power rail needs less
    precision than a signal and pays less for it. The cell budget is the floor:
    a board-sized window cannot have both.
    """
    if probe.resolution_mm > 0:
        return probe.resolution_mm
    width = max(window[2] - window[0], 1e-6)
    height = max(window[3] - window[1], 1e-6)
    budget = math.sqrt(width * height / probe.max_cells)
    want = max(needed_mm * 0.4, 0.03)
    return min(0.25, max(want, budget))


def _window(
    problem: RoutingProblem, pads: Sequence[Pad], margin: float
) -> tuple[float, float, float, float]:
    xs = [p.center.x for p in pads] or [problem.board.center.x]
    ys = [p.center.y for p in pads] or [problem.board.center.y]
    bx0, by0, bx1, by1 = problem.board.bbox
    return (
        max(bx0 - 1.0, min(xs) - margin),
        max(by0 - 1.0, min(ys) - margin),
        min(bx1 + 1.0, max(xs) + margin),
        min(by1 + 1.0, max(ys) + margin),
    )


def diagnose(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    probe: Probe | None = None,
) -> Diagnosis:
    """Why this board is not finished, and what would most likely unblock it."""
    import time

    started = time.perf_counter()
    probe = probe or DEFAULT_PROBE
    linked = analyse(problem, solution)
    notes: list[str] = []
    if not linked.unconnected_nets:
        return Diagnosis(
            board=problem.id,
            routable_nets=linked.routable_nets,
            connected_nets=len(linked.connected_nets),
            resolution_mm=0.0,
            nets=(),
            asks=(),
            notes=("every net is connected",),
            seconds=time.perf_counter() - started,
        )

    obstacles = build_obstacles(problem, solution)
    grids = _obstacle_index(obstacles)
    groups = pad_components(problem, solution)
    pads_by_id = problem.pads_by_id
    nets_by_id = problem.nets_by_id

    ordered = sorted(linked.unconnected_nets, key=lambda nid: (nets_by_id[nid].name, nid))
    if len(ordered) > probe.max_nets:
        notes.append(
            f"{len(ordered)} nets are unconnected; the {probe.max_nets} named "
            "first were measured and the rest were not"
        )
        ordered = ordered[: probe.max_nets]

    results: list[NetDiagnosis] = []
    #: Everything the move verification needs to re-measure this net's channel
    #: with a part translated, kept per net so the check does not have to guess.
    replay: dict[str, tuple] = {}
    step_used = 0.0
    for net_id in ordered:
        net = nets_by_id[net_id]
        fragments = [
            [pads_by_id[pid] for pid in group if pid in pads_by_id]
            for group in groups.get(net_id, ())
        ]
        fragments = [frag for frag in fragments if frag]
        if len(fragments) < 2:
            results.append(
                NetDiagnosis(
                    net=net_id,
                    name=net.name,
                    net_class=net.net_class,
                    width_mm=net.min_width_mm,
                    pads=len(net.pads),
                    fragments=len(fragments),
                    verdict="unattributed",
                    channel_mm=0.0,
                    tolerance_mm=0.0,
                    needed_mm=net.min_width_mm / 2.0,
                    pinch=None,
                    reason="the net's pads did not resolve into two pieces to join",
                )
            )
            continue

        needed = net.min_width_mm / 2.0
        window = _window(problem, [pad for frag in fragments for pad in frag],
                         probe.window_margin_mm)
        via_half = problem.rules.via_pad_mm / 2.0

        # Two passes, and most nets never need the second. A coarse grid can
        # *prove* there is room — its error bar is two-sided, so a channel that
        # clears the requirement by more than the tolerance clears it for real
        # — and proving that is the whole answer for a net nothing was in the
        # way of. Measured on a harness-puck board with a third of its copper
        # deleted: 12 of 12 nets settled on the coarse pass, 44s to 8s.
        coarse_step = max(needed * 1.2, 0.10)
        coarse = None
        if coarse_step > _resolution(window, probe, needed) * 1.5:
            field = build_field(
                problem, obstacles, exclude_net=net_id, window=window,
                step=coarse_step,
            )
            coarse = widest_channel(field, fragments, via_half_mm=via_half)
            if coarse.joined and coarse.level_mm - field.tolerance_mm >= needed:
                results.append(
                    NetDiagnosis(
                        net=net_id,
                        name=net.name,
                        net_class=net.net_class,
                        width_mm=net.min_width_mm,
                        pads=len(net.pads),
                        fragments=len(fragments),
                        verdict="router_limit",
                        channel_mm=coarse.level_mm,
                        tolerance_mm=field.tolerance_mm,
                        needed_mm=needed,
                        pinch=None,
                        reason=(
                            f"a channel at least "
                            f"{(coarse.level_mm - field.tolerance_mm) * 2:.3f}mm "
                            f"wide joins these pads and the net is "
                            f"{net.min_width_mm:.3f}mm — the placement leaves "
                            "room the router did not use"
                        ),
                    )
                )
                step_used = coarse_step
                continue

        step = _resolution(window, probe, needed)
        step_used = step
        field = build_field(
            problem, obstacles, exclude_net=net_id, window=window, step=step
        )
        channel = widest_channel(field, fragments, via_half_mm=via_half)

        tolerance = field.tolerance_mm
        replay[net_id] = (fragments, field.window, field.step, via_half, needed)

        if not channel.joined:
            results.append(
                NetDiagnosis(
                    net=net_id,
                    name=net.name,
                    net_class=net.net_class,
                    width_mm=net.min_width_mm,
                    pads=len(net.pads),
                    fragments=len(fragments),
                    verdict="no_channel",
                    channel_mm=FLOOR_MM,
                    tolerance_mm=tolerance,
                    needed_mm=needed,
                    pinch=None,
                    proven=True,
                    reason=(
                        "at "
                        f"{step:.2f}mm sampling nothing joins these pads on "
                        "either layer, even allowing copper to cut "
                        f"{abs(FLOOR_MM):.2f}mm into what is already there"
                    ),
                )
            )
            continue

        # Refine every narrow point across the corridor. The grid's own value is
        # only good to its tolerance, and the corridor's true half-width is the
        # largest disc that fits — almost never centred on a cell.
        pinch: Pinch | None = None
        congestion: Congestion | None = None
        refined = math.inf
        # The grid knows the channel to within its tolerance, so refinement may
        # correct a number inside that bar and never past it. A refined value
        # above the bar means the sample drifted out of the corridor, and the
        # grid cell is then the more honest answer.
        ceiling = channel.level_mm + channel.tolerance_mm
        for cell, layer, grid_room, ax, ay in channel.narrow:
            cx, cy = field.centre(cell)
            rx, ry, room = refine_pinch(
                grids,
                cx,
                cy,
                layer,
                (ax, ay),
                exclude_net=net_id,
                # Only as far as the grid could have been wrong. Any further
                # and the sample leaves the corridor it is describing.
                span_mm=1.5 * channel.tolerance_mm,
            )
            if room > ceiling:
                rx, ry, room = cx, cy, min(grid_room, ceiling)
            if room >= refined:
                continue
            candidate = name_pinch(
                obstacles, grids, rx, ry, layer, exclude_net=net_id
            )
            if candidate is None:
                continue
            pinch = candidate
            refined = room
            congestion = measure_congestion(
                field,
                obstacles,
                grids,
                rx,
                ry,
                layer,
                needed,
                radius_mm=probe.congestion_radius_mm,
            )
        if pinch is not None:
            channel = ChannelResult(
                joined=channel.joined,
                level_mm=refined,
                tolerance_mm=channel.tolerance_mm,
                binding_pair=channel.binding_pair,
                pinch_cell=channel.pinch_cell,
                pinch_layer=pinch.layer,
                pinch_room_mm=refined,
                narrow=channel.narrow,
            )

        # Three readings, and the difference between them is what may be
        # claimed. The grid knows the true channel to within its tolerance, so
        # "there is no way through" is only said when the optimistic bound also
        # falls short; otherwise the report says "the widest way through I
        # found", which is weaker and true.
        if channel.level_mm >= needed:
            verdict = "router_limit"
            reason = (
                f"a channel at least {channel.level_mm * 2:.3f}mm wide joins "
                f"these pads and the net is {net.min_width_mm:.3f}mm — the "
                "placement leaves room the router did not use"
            )
        elif pinch is not None and pinch.second is not None:
            verdict = "pinched"
            reason = ""
        else:
            verdict = "unattributed"
            reason = (
                f"the widest channel found is {channel.level_mm * 2:.3f}mm "
                f"against the net's {net.min_width_mm:.3f}mm, and the narrow "
                "point could not be tied to two separate things"
            )
        results.append(
            NetDiagnosis(
                net=net_id,
                name=net.name,
                net_class=net.net_class,
                width_mm=net.min_width_mm,
                pads=len(net.pads),
                fragments=len(fragments),
                verdict=verdict,
                channel_mm=channel.level_mm,
                tolerance_mm=tolerance,
                needed_mm=needed,
                pinch=pinch,
                proven=channel.level_mm + tolerance < needed,
                congestion=congestion,
                reason=reason,
            )
        )

    asks = _build_asks(problem, obstacles, grids, results, probe, notes, replay)
    return Diagnosis(
        board=problem.id,
        routable_nets=linked.routable_nets,
        connected_nets=len(linked.connected_nets),
        resolution_mm=step_used,
        nets=tuple(results),
        asks=tuple(asks),
        notes=tuple(notes),
        seconds=time.perf_counter() - started,
    )


def joined_names(items: Sequence[str], conjunction: str = "and") -> str:
    """``["a"]`` -> ``"a"``; ``["a","b"]`` -> ``"a and b"``; more -> commas."""
    values = [str(item) for item in items if item]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return f"{', '.join(values[:-1])} {conjunction} {values[-1]}"


def bare_board_diagnosis(
    problem: RoutingProblem, solution: RoutingSolution
) -> "Diagnosis | None":
    """The answer for a board with no copper on it at all, or None.

    Grinding a channel search over every net to conclude "the router did not
    run" costs 43 seconds on a 36-net board and says what the copper count
    already said. This is the same answer for free.

    It is deliberately **not** inside :func:`diagnose`, which must keep
    measuring the geometry of an empty board — "these two pads have 0.10mm
    between them and the connection needs 0.20mm" is a real answer whether or
    not anything else is routed yet. The shortcut belongs to the caller that
    knows the difference between an unrouted board and an unroutable one.
    """
    import time

    started = time.perf_counter()
    if solution.traces or solution.vias or problem.existing_traces:
        return None
    linked = analyse(problem, solution)
    if not linked.unconnected_nets:
        return None
    names = tuple(
        sorted(
            problem.nets_by_id[net_id].name or net_id
            for net_id in linked.unconnected_nets
        )
    )
    return Diagnosis(
        board=problem.id,
        routable_nets=linked.routable_nets,
        connected_nets=len(linked.connected_nets),
        resolution_mm=0.0,
        nets=(),
        asks=(
            Ask(
                kind="router_limit",
                nets=names,
                headline=f"no copper was drawn for any of {len(names)} connections",
                at=None,
                layer="",
                pinch=None,
                needed_mm=0.0,
                move=None,
                congestion=None,
                evidence=(
                    "there is not one track or hole on this board, so nothing "
                    "was in anything's way — the step that draws the copper "
                    "produced nothing at all",
                ),
            ),
        ),
        notes=("no copper on the board; the geometry was not measured",),
        seconds=time.perf_counter() - started,
    )


def _pinch_key(diagnosis: NetDiagnosis) -> tuple:
    """Two nets pinched by the same pair of things within a millimetre are one
    request, not two. Grouping is what turns seven findings into one decision."""
    # "There is room and it was not used" is one request however many
    # connections it covers, so it groups on the verdict alone. A pinch groups
    # on the two things that make it, because that is what would be changed.
    if diagnosis.verdict != "pinched":
        return (diagnosis.verdict,)
    pinch = diagnosis.pinch
    if pinch is None or pinch.second is None:
        return (diagnosis.verdict,)
    owners = tuple(sorted((pinch.first.owner, pinch.second.owner)))
    return ("pinched", owners, round(pinch.x, 0), round(pinch.y, 0), pinch.layer)


def _build_asks(
    problem: RoutingProblem,
    obstacles: Sequence[Obstacle],
    grids: dict[str, GridIndex],
    results: Sequence[NetDiagnosis],
    probe: Probe,
    notes: list[str],
    replay: dict[str, tuple],
) -> list[Ask]:
    buckets: dict[tuple, list[NetDiagnosis]] = {}
    for row in results:
        buckets.setdefault(_pinch_key(row), []).append(row)

    asks: list[Ask] = []
    for key in sorted(buckets, key=lambda k: (str(k),)):
        rows = buckets[key]
        names = tuple(sorted({row.name or row.net for row in rows}))
        verdict = rows[0].verdict

        if verdict == "router_limit":
            widest = max(row.channel_mm for row in rows)
            asks.append(
                Ask(
                    kind="router_limit",
                    nets=names,
                    headline=(
                        f"{len(names)} net{'s' if len(names) != 1 else ''} could "
                        "fit but were not routed"
                    ),
                    at=None,
                    layer="",
                    pinch=None,
                    needed_mm=max(row.needed_mm for row in rows),
                    move=None,
                    congestion=None,
                    evidence=tuple(
                        [
                            f"{row.name or row.net}: a {row.channel_mm:.3f}mm "
                            f"channel exists and {row.needed_mm:.3f}mm is needed"
                            for row in sorted(rows, key=lambda r: r.name)
                        ]
                        + [
                            "nothing on the board has to move for these; the "
                            "router ran out of search before it found the path"
                        ]
                    ),
                )
            )
            continue

        if verdict in ("unattributed", "no_channel") or rows[0].pinch is None:
            asks.append(
                Ask(
                    kind="unattributed" if verdict != "no_channel" else "no_channel",
                    nets=names,
                    headline=(
                        f"{len(names)} net{'s' if len(names) != 1 else ''} failed "
                        + (
                            "and no gap of any width reaches them"
                            if verdict == "no_channel"
                            else "and the cause could not be measured"
                        )
                    ),
                    at=None,
                    layer="",
                    pinch=None,
                    needed_mm=max(row.needed_mm for row in rows),
                    move=None,
                    congestion=None,
                    evidence=tuple(
                        f"{row.name or row.net}: {row.reason}"
                        for row in sorted(rows, key=lambda r: r.name)
                    ),
                )
            )
            continue

        pinch = rows[0].pinch
        assert pinch is not None and pinch.second is not None
        needed = max(row.needed_mm * 2.0 for row in rows)  # full trace width
        usable = pinch.usable_mm
        congestion = next((row.congestion for row in rows if row.congestion), None)

        move = None
        evidence: list[str] = [
            f"{pinch.first.label} and {pinch.second.label} come within "
            f"{pinch.gap_mm:.3f}mm of each other at "
            f"({pinch.x:.2f}, {pinch.y:.2f}) on the {pinch.layer} layer",
            f"after the clearance each of them needs, {usable:.3f}mm is left "
            f"for copper, and the widest of these nets is {needed:.3f}mm",
        ]
        if congestion and congestion.tightest_between and math.isfinite(
            congestion.tightest_gap_mm
        ):
            evidence.append(
                f"{len(congestion.parts)} parts sit within "
                f"{congestion.radius_mm:.0f}mm of that point and the closest two"
                f" — {congestion.tightest_between[0]} and "
                f"{congestion.tightest_between[1]} — are "
                f"{congestion.tightest_gap_mm:.2f}mm apart; "
                f"{congestion.free_fraction * 100:.0f}% of the area around it "
                "has room for these nets"
            )

        # A rail is wide because of what it carries, and the width is a choice
        # a person can make. Offering it is only honest when the thinner rail
        # would actually fit and would still be a rail the fab will build.
        thinner = [
            row
            for row in rows
            if row.net_class in ("power", "ground")
            and problem.rules.min_trace_mm <= usable < row.width_mm
        ]
        if thinner:
            evidence.append(
                f"{joined_names(tuple(sorted(r.name or r.net for r in thinner)))} "
                f"{'is' if len(thinner) == 1 else 'are'} "
                f"{max(r.width_mm for r in thinner):.2f}mm wide because "
                f"{'it carries' if len(thinner) == 1 else 'they carry'} power; a "
                f"{usable:.2f}mm rail would fit through here and is still above "
                f"the {problem.rules.min_trace_mm:.2f}mm the factory will build"
            )

        if probe.verify_moves:
            move = _best_move(
                problem, obstacles, grids, pinch, needed, probe, rows, replay
            )
            if move is not None:
                evidence.append(
                    f"moving {move.part} {move.distance_mm:.2f}mm {move.heading} "
                    f"takes that gap to {move.after_usable_mm:.3f}mm of usable "
                    "copper — the part was moved and the geometry taken again, "
                    "not estimated"
                )
                if move.headroom_mm > move.distance_mm:
                    evidence.append(
                        f"{move.part} has at least {move.headroom_mm:.2f}mm of "
                        f"clear travel {move.heading} before it touches anything"
                    )
            elif pinch.first.kind in ("trace", "via") and pinch.second.kind in (
                "trace",
                "via",
            ):
                evidence.append(
                    "both sides of this gap are copper that was already laid "
                    "down, not parts — nothing has to move, but one of those "
                    "two routes has to give way"
                )
            elif pinch.first.kind == "keepout" or pinch.second.kind == "keepout":
                keepout = pinch.first if pinch.first.kind == "keepout" else pinch.second
                part = (pinch.second if pinch.first.kind == "keepout" else pinch.first)
                owner = (
                    part.component
                    and keepout.id
                    in owned_keepouts(obstacles, grids, part.component, problem.rules)
                )
                evidence.append(
                    (
                        f"that no-copper area is {part.component}'s own — its "
                        f"pads already sit inside it — so moving "
                        f"{part.component} takes the area with it and changes "
                        "nothing here. The route has to go round it, or under "
                        "it on the other layer"
                    )
                    if owner
                    else (
                        "one side of this gap is a no-copper area, and the "
                        "design file does not say which part it belongs to, so "
                        "no move is being suggested from it"
                    )
                )
            else:
                tried = [
                    label
                    for label in (pinch.first.component, pinch.second.component)
                    if label
                ]
                evidence.append(
                    (
                        "moving "
                        + " or ".join(tried)
                        + " apart was tried in steps and every distance either "
                        "failed to open the gap or ran into something else, so "
                        "no move is being suggested"
                    )
                    if tried
                    else "neither side of this gap is a part that can be moved"
                )

        if move:
            kind = "move_part"
        elif pinch.first.kind in ("trace", "via") and pinch.second.kind in (
            "trace",
            "via",
        ):
            kind = "reroute"
        else:
            kind = "tight_gap"

        asks.append(
            Ask(
                kind=kind,
                nets=names,
                headline=(
                    f"{len(names)} net{'s' if len(names) != 1 else ''} cannot get "
                    f"through the {usable:.2f}mm gap between "
                    f"{pinch.first.label} and {pinch.second.label}"
                ),
                at=(pinch.x, pinch.y),
                layer=pinch.layer,
                pinch=pinch,
                needed_mm=needed,
                move=move,
                congestion=congestion,
                evidence=tuple(evidence),
                proven=all(row.proven for row in rows),
            )
        )
    # Blockages first, then the honest unknowns; a request to decide outranks a
    # note that nothing could be decided.
    order = {"move_part": 0, "tight_gap": 1, "reroute": 2, "no_channel": 3,
             "router_limit": 4, "unattributed": 5}
    asks.sort(key=lambda a: (order.get(a.kind, 9), -len(a.nets), a.headline))
    return asks


def _best_move(
    problem: RoutingProblem,
    obstacles: Sequence[Obstacle],
    grids: dict[str, GridIndex],
    pinch: Pinch,
    needed_mm: float,
    probe: Probe,
    rows: Sequence[NetDiagnosis],
    replay: dict[str, tuple],
) -> Move | None:
    """Try both sides of the pinch, keep the shorter move that survives.

    Direction comes from the exact distance function by finite difference, so
    nothing here assumes a bounding box or a centroid — both of which point the
    wrong way for an L-shaped or a long thin pad.

    A candidate that widens the corridor is then made to prove it: the whole
    channel search is re-run for one of the stuck nets with the part
    translated. Only a move that both opens the corridor *and* raises the
    measured channel is returned, because "these two shapes are further apart"
    and "the net can now get through" are different claims and only the second
    one is worth putting in front of a person.
    """
    # One rule for both sides and for a one-sided pinch: move the part directly
    # away from the point the corridor is narrowest, which is the direction the
    # exact distance function decreases fastest toward it.
    sides = [pinch.first] + ([pinch.second] if pinch.second else [])
    candidates: list[Move] = []
    for side in sides:
        part = side.component
        if not part:
            continue
        out = _outward(pinch.x, pinch.y, side.capsule)
        direction = (-out[0], -out[1])
        move = try_move(
            problem,
            obstacles,
            grids,
            pinch,
            part,
            direction,
            needed_mm,
            steps=probe.move_steps,
        )
        if move is not None:
            candidates.append(move)
    candidates.sort(key=lambda move: (move.distance_mm, move.part))

    for move in candidates:
        subject = next((row for row in rows if row.net in replay), None)
        if subject is None:
            return move
        before = subject.channel_mm
        after = _recheck_channel(problem, obstacles, replay[subject.net], move)
        if after is None:
            return move
        # The re-measure runs on the same grid as the first one, so it carries
        # the same error bar and has to be read with it. Requiring the raw
        # number to clear the requirement threw away a move that took a channel
        # from 0.10mm to 0.24mm, because the grid read the second one as 0.18.
        if after <= before or after + subject.tolerance_mm < subject.needed_mm:
            continue
        return Move(
            part=move.part,
            dx_mm=move.dx_mm,
            dy_mm=move.dy_mm,
            distance_mm=move.distance_mm,
            heading=move.heading,
            after_usable_mm=move.after_usable_mm,
            headroom_mm=move.headroom_mm,
            caveat=(
                f"re-measured after the move: the channel for "
                f"{subject.name or subject.net} goes from {before * 2:.3f}mm to "
                f"{after * 2:.3f}mm of usable width, against the "
                f"{subject.width_mm:.3f}mm the net needs. Whether the router "
                "then finds that path was not measured"
            ),
        )
    return None


def _recheck_channel(
    problem: RoutingProblem,
    obstacles: Sequence[Obstacle],
    replay: tuple,
    move: Move,
) -> float | None:
    """The same channel search, with one part moved. Returns the new level.

    The part's obstacles *and* its pads are translated — a pad that belongs to
    the stuck net is both a wall for other nets and a target for this one, and
    forgetting the second half would measure a channel to where the pad used to
    be.
    """
    from dataclasses import replace as _replace

    fragments, window, step, via_half, _needed = replay
    dx, dy = move.dx_mm, move.dy_mm

    moved_obstacles = [
        _replace(o, capsule=_translate(o.capsule, dx, dy))
        if o.component == move.part
        else o
        for o in obstacles
    ]

    def _shift(pad: Pad) -> Pad:
        if pad.component != move.part:
            return pad
        return _replace(
            pad,
            center=Point(pad.center.x + dx, pad.center.y + dy),
            vertices=tuple(Point(p.x + dx, p.y + dy) for p in pad.vertices),
        )

    shifted = [[_shift(pad) for pad in group] for group in fragments]
    net_id = next(
        (pad.net for group in shifted for pad in group if pad.net), None
    )
    field = build_field(
        problem, moved_obstacles, exclude_net=net_id, window=window, step=step
    )
    result = widest_channel(field, shifted, via_half_mm=via_half)
    return result.level_mm if result.joined else None


__all__ = [
    "Ask",
    "Congestion",
    "Diagnosis",
    "Field",
    "Move",
    "NetDiagnosis",
    "Obstacle",
    "Pinch",
    "Probe",
    "DEFAULT_PROBE",
    "build_field",
    "build_obstacles",
    "diagnose",
    "measure_congestion",
    "name_pinch",
    "try_move",
    "widest_channel",
]
