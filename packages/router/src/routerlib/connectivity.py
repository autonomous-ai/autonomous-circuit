"""Is the net actually connected?

Completeness is recomputed from copper, never read off the router's own
``complete`` flag. The reason is the failure mode we are trying to eliminate:
an answer that looks finished and is not. A router can claim anything; two
pieces of copper either overlap on the same layer or they do not.

The model is a union-find over ``(copper item, layer)`` nodes:

* an item spans one node per layer it lives on — an SMD pad has one, a via and
  a plated hole have two
* two nodes merge when they are on the **same layer** and their copper touches
* a via therefore merges top and bottom, which is the only way to change layers
* a poured :class:`~routerlib.model.Plane` is one node, and same-net copper
  landing inside it merges with it — that is how a ground plane replaces
  seventy traces

A net is complete when every one of its pads sits in one component. A net with
one pad is already complete and is excluded from the denominator, because
counting it would inflate the score for free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from routerlib.drc import CopperItem, copper_items
from routerlib.geometry import GridIndex, PolygonIndex, capsule_gap
from routerlib.model import RoutingProblem, RoutingSolution

#: Copper touches when the gap is zero or negative. No slack: a "nearly
#: touching" connection is an open circuit on the built board.
TOUCH_TOL_MM = 1e-9


class _UnionFind:
    __slots__ = ("parent",)

    def __init__(self) -> None:
        self.parent: dict[object, object] = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Deterministic merge direction: the smaller repr wins, so the
            # component labels do not depend on insertion order.
            lo, hi = sorted((ra, rb), key=repr)
            self.parent[hi] = lo


@dataclass(frozen=True)
class ConnectivityResult:
    completeness: float
    routable_nets: int
    connected_nets: tuple[str, ...]
    unconnected_nets: tuple[str, ...]
    #: net id -> how many disjoint pieces its pads fall into (1 == connected)
    fragments: dict[str, int]
    #: Copper that belongs to a net but touches none of that net's pads.
    orphan_copper: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.unconnected_nets


def analyse(problem: RoutingProblem, solution: RoutingSolution) -> ConnectivityResult:
    items = copper_items(problem, solution)
    uf = _UnionFind()

    grids: dict[str, GridIndex] = {}
    for item in items:
        for layer in item.layers:
            grids.setdefault(layer, GridIndex(cell_mm=2.0)).insert(item.capsule, item)
        # An item spans its own layers: a via joins top to bottom.
        first = (item.id, item.layers[0])
        for layer in item.layers[1:]:
            uf.union(first, (item.id, layer))

    for item in items:
        for layer in item.layers:
            grid = grids[layer]
            for _, other in grid.query(item.capsule, margin=0.0):
                if other.id == item.id or other.net != item.net:
                    continue
                if capsule_gap(item.capsule, other.capsule) <= TOUCH_TOL_MM:
                    uf.union((item.id, layer), (other.id, layer))

    # Planes: same-net copper whose midpoint lands inside the poured region.
    for plane in problem.planes:
        node = (f"__plane__{plane.id}", plane.layer)
        uf.find(node)
        outline = PolygonIndex(plane.outline)
        holes = [PolygonIndex(ring) for ring in plane.holes]
        for item in items:
            if item.net != plane.net or plane.layer not in item.layers:
                continue
            ax, ay, bx, by, _ = item.capsule
            mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
            if not outline.contains(mx, my):
                continue
            if any(hole.contains(mx, my) for hole in holes):
                continue
            uf.union(node, (item.id, plane.layer))

    pads_by_id = problem.pads_by_id
    fragments: dict[str, int] = {}
    connected: list[str] = []
    unconnected: list[str] = []
    pad_roots: dict[str, set] = {}
    for net in problem.routable_nets:
        roots = set()
        for pad_id in net.pads:
            pad = pads_by_id.get(pad_id)
            if pad is None:
                continue
            roots.add(uf.find((pad.id, pad.layers[0])))
        pad_roots[net.id] = roots
        fragments[net.id] = len(roots) if roots else 0
        (connected if len(roots) <= 1 else unconnected).append(net.id)

    routable = len(problem.routable_nets)
    completeness = (len(connected) / routable) if routable else 1.0

    orphans: list[str] = []
    for trace in solution.traces:
        roots = pad_roots.get(trace.net)
        if roots is None:
            continue
        if uf.find((f"{trace.id}#0", trace.layer)) not in roots:
            orphans.append(trace.id)

    return ConnectivityResult(
        completeness=completeness,
        routable_nets=routable,
        connected_nets=tuple(sorted(connected)),
        unconnected_nets=tuple(sorted(unconnected)),
        fragments=dict(sorted(fragments.items())),
        orphan_copper=tuple(sorted(orphans)),
    )


__all__ = ["ConnectivityResult", "TOUCH_TOL_MM", "analyse"]
