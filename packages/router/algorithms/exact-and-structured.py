#!/usr/bin/env python3.12
"""exact-and-structured — choose the copper optimally instead of first-fit.

Two ideas, one router.

**Structured.** Our boards are not random. A 10x5 key matrix at 10mm pitch is a
channel-routing problem with a known answer: every ROW net is ten pads on one
horizontal line, every COL net is five pads on one vertical line. A general
router rediscovers that with search, badly. This one detects the line and lays a
**spine** — a single track parallel to it, one stub per pad — which is what a
human draws and what the textbook calls river routing.

**Exact.** Every other family in this tournament decides one net at a time:
route it, keep it, move on. That is the failure the brief names — a greedy order
walls off the region a later net needs. This router does not decide nets one at
a time. It **enumerates candidate copper for every connection, computes which
candidates physically exclude each other, and then solves the choice as an
integer program**:

    minimise   NET_PENALTY * (nets with an unrouted connection)
             + VIA_COST    * vias
             + copper length
    subject to at most one candidate per connection
               no two chosen candidates closer than the clearance they were
               designed to

That is an ILP, and it is solved by a branch-and-bound written here rather than
by CBC or HiGHS, because this machine has no MIP solver installed and a family
that only runs where someone remembered to `pip install pulp` is not a family we
can compare against. The bound is admissible, the presolve is standard
(dominance elimination, connected-component decomposition), and when the search
closes, the answer is **provably the best assignment in the candidate set** —
which is the one number nothing else in this tournament can produce.

## What "optimal" means here, precisely

It does not mean optimal copper. It means:

> Given the candidate set this file generated, no other assignment of those
> candidates to connections has a lower cost.

The candidate set is a discretisation — rectilinear and single-diagonal shapes
between a bounded list of pad access points — so a maze router that finds a
15-bend path through a gap we never proposed can beat a "proved optimal" answer
here. Every optimality claim in the report is stamped with the candidate count
it was proved over, and the search reports `proved: false` the moment it runs
out of nodes. A proof over a set we chose is worth exactly what the set is
worth, and saying so is the point of the family.

## Where it does not decide optimally, said out loud

* **Big components are repaired, not proved.** The conflict graph usually falls
  apart into components of two to twenty connections, and those close. When it
  does not — a 68-connection knot on `matrix-rp2040-core__usb-c-data`, 205 on
  `terminal-keyboard` — branch-and-bound is *useless* there, and measurably so:
  13k nodes and 1.36M nodes returned the identical answer, because depth-first
  search polishes the bottom of the tree while the damage is at the top. Such a
  component instead gets the treatment this family is named for. Around each
  connection the incumbent failed, cut a window — that connection, the rest of
  its net, and every connection whose chosen route blocks it — hold everything
  outside it fixed, and **solve the window exactly**. Each window is a closed
  proof; the component is not, and the report separates the two counts. On the
  RP2040 board that step alone moved 42.9% routed to 61.9%.
* **Across components.** Components are solved in a fixed order, and a net
  already broken by an earlier one costs nothing in a later one. Exact inside a
  component; a fixed sequence across them.
* **Task decomposition.** A net is decomposed into pad-to-pad connections by a
  Euclidean MST before anything is enumerated. The MST is not necessarily the
  best tree, and the ILP cannot repair a bad one — only the narrow
  edge-substitution pass below can, and only for an edge with no legal route at
  all.
* **Widths and tiers are chosen before the solve**, not by it.

## It never designs into a warning

`min_clearance` is what JLC holds (0.10mm) and `target_clearance` is what a
router should aim at (0.147mm). This one carries two tiers, and *both* sit above
every warning threshold in the profile: copper-to-copper 0.147 falling back to
0.115 (the DFM gate warns below 0.10, so the fallback is free), copper to a
plated hole 0.35 (the *warn* number, not the 0.28 error floor), copper to the
board edge 0.30 (again the warn number). A candidate that cannot meet the second
tier is not generated. The one place it accepts a warning is a power net it can
only route at signal width, and it takes that trade knowingly: completeness is
tier 1 of the score and a warning is tier 2.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Sequence

_HERE = Path(__file__).resolve().parent
_PACKAGE = _HERE.parent
for _path in (_PACKAGE / "src", _PACKAGE.parent / "circuitpy" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from routerlib.geometry import (  # noqa: E402
    Capsule,
    GridIndex,
    capsule_gap,
    disc_capsule,
    segment_capsule,
)
from routerlib.model import (  # noqa: E402
    BOTTOM,
    TOP,
    Budget,
    BudgetMeter,
    DesignRules,
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

NAME = "exact-and-structured"

# --------------------------------------------------------------------------
# Tunables. Every one of these is a number with a reason.
# --------------------------------------------------------------------------

#: Cost of one net left with an unrouted connection. Above any sum of via and
#: length costs a component can accumulate, so the solver never trades a net
#: away for copper — which is the score's own lexicographic order.
NET_PENALTY = 1e9

#: Cost of one via, in the same currency as a millimetre of copper. The score
#: compares via count before copper length, so a via has to outweigh any length
#: difference a single candidate can show (a board spine is ~200mm).
VIA_COST = 1000.0

#: Small nudge so the solver prefers the roomier of two otherwise equal
#: candidates. Never large enough to trade a via for it.
TIGHT_TIER_COST = 5.0

#: How many legal candidates to keep per connection. More gives the solver more
#: freedom and costs quadratic conflict work.
MAX_CANDIDATES = 14

#: How many candidate shapes to *test* per connection before giving up. The
#: expensive case is a connection with no legal route at all: it pays the whole
#: cap and returns nothing.
MAX_SHAPE_TESTS = 150

#: Above this many connections the caps tighten — the machine is shared.
BIG_PROBLEM_TASKS = 200
BIG_MAX_CANDIDATES = 8
BIG_MAX_SHAPE_TESTS = 90

#: Perpendicular shifts of the straight line, in mm — a parallel corridor
#: beside the obvious one. These are what the solver chooses *between*; without
#: them every candidate uses the same corridor and the ILP has no freedom to
#: exercise. Measured on terminal-keyboard: fifteen buses converge on one QFN
#: and every one of them wants the middle of the board.
DETOURS_MM = (1.0, 2.2, 4.0, 7.0, 11.0)

#: A route at least this long is offered its lateral shifts before its bends.
LONG_ROUTE_MM = 8.0

#: Pad access points tried per pad. Ordered best-first, so the tail is the
#: least useful part of a quadratic loop.
MAX_ACCESSES = 3

#: Fractions along the bounding box where a Z pattern turns.
Z_FRACTIONS = (0.5, 0.25, 0.75)

#: Distances (beyond the minimum) at which a via may sit off a pad.
VIA_STANDOFFS_MM = (0.0, 0.45, 1.0)

#: Distances (beyond the minimum) at which a same-layer escape may end.
ESCAPE_STANDOFFS_MM = (0.0, 0.5)

#: How far two pad centres may differ on an axis and still count as one line.
COLINEAR_TOL_MM = 0.02

#: Spine offsets from the pad line, beyond the minimum, in mm.
SPINE_OFFSETS_MM = (0.0, 0.35, 0.9, 1.8, 3.0)

#: Widths a power or ground net will accept, in order. The first is the width
#: the net asked for; the rest are the knowing trade of one DFM *warning*
#: against a whole net of completeness.
POWER_WIDTH_FALLBACKS_MM = (0.35, 0.25, 0.2)

#: Nodes the branch-and-bound may expand for one conflict component before it
#: gives up on proving optimality. Scaled by the component's size. Deliberately
#: modest: measured on the 68-connection component of
#: matrix-rp2040-core__usb-c-data, 13k nodes and 1.36M nodes gave the *same*
#: answer, so the budget belongs in the exact repairs below, not here.
NODES_PER_TASK = 600
MIN_COMPONENT_NODES = 20_000

#: Exact repair. A window is at most this many connections — small enough that
#: the sub-solve closes, big enough to contain a failure and everything
#: blocking it.
LNS_WINDOW = 14
LNS_NODES = 40_000
LNS_PASSES = 3

#: Rounds of the drop-and-reroute loop. Each round removes the nets that could
#: not be finished and re-solves, because their copper was occupying space.
MAX_ROUNDS = 3


@dataclass(frozen=True)
class Tier:
    """A clearance policy the candidate generator designs to.

    Two of them, and both are above every warning threshold the profile
    publishes. ``index`` 0 is what we want, 1 is what we accept.
    """

    index: int
    clearance_mm: float
    rules: DesignRules
    workspace: Workspace = field(repr=False, default=None)  # type: ignore[assignment]


def _tiers(problem: RoutingProblem) -> tuple[Tier, ...]:
    """Build the two design tiers and their static obstacle maps.

    A tier is expressed as a *modified rule set*, so ``Workspace`` enforces it
    with the same geometry the scorer measures with. Copper-to-hole and
    copper-to-edge are raised from the error floor to the profile's warn number
    in both tiers: a board that only clears the error floor scores a warning,
    and a warning is a tier-2 defect in the score.
    """
    rules = problem.rules
    warn_pth = _profile_number(rules, "warn_pth_to_copper_mm", 0.35)
    warn_edge = _profile_number(rules, "warn_edge_clearance_mm", 0.30)
    out = []
    for index, clearance in ((0, rules.target_clearance_mm), (1, 0.115)):
        tier_rules = replace(
            rules,
            min_pth_to_copper_mm=max(rules.min_pth_to_copper_mm, warn_pth),
            min_edge_clearance_mm=max(rules.min_edge_clearance_mm, warn_edge),
            target_clearance_mm=clearance,
        )
        tier_problem = replace(problem, rules=tier_rules)
        out.append(
            Tier(
                index=index,
                clearance_mm=clearance,
                rules=tier_rules,
                workspace=Workspace(tier_problem, clearance=clearance),
            )
        )
    return tuple(out)


def _profile_number(rules: DesignRules, field_name: str, fallback: float) -> float:
    """Read a warn threshold from the fab profile, never from memory."""
    try:
        from circuitpy.fab import get_profile

        return float(getattr(get_profile(rules.profile_id), field_name))
    except Exception:  # noqa: BLE001 - a synthetic problem may have no profile
        return fallback


# --------------------------------------------------------------------------
# Copper, before it has an id
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """One run of copper on one layer, before it is given a trace id."""

    layer: str
    points: tuple[Point, ...]
    width_mm: float

    @property
    def length_mm(self) -> float:
        return sum(
            a.distance_to(b) for a, b in zip(self.points, self.points[1:])
        )


@dataclass(frozen=True)
class Access:
    """How a candidate reaches a pad.

    ``prefix`` is copper on the main layer (the polyline simply starts further
    back); ``pieces`` is copper on the *other* layer, which only happens when a
    via is involved.
    """

    point: Point
    prefix: tuple[Point, ...] = ()
    pieces: tuple[Piece, ...] = ()
    vias: tuple[Point, ...] = ()
    cost_hint: float = 0.0


@dataclass(frozen=True)
class Task:
    """One thing that has to be electrically true.

    Either "join these two pads" or, when the net owns a poured plane, "get
    this pad into the plane".
    """

    index: int
    net: str
    a: Pad
    b: Pad | None = None
    plane: Plane | None = None

    @property
    def is_stitch(self) -> bool:
        return self.b is None


@dataclass(frozen=True)
class Candidate:
    """One way to satisfy one or more tasks, with the geometry to prove it."""

    index: int
    net: str
    tasks: tuple[int, ...]
    pieces: tuple[Piece, ...]
    vias: tuple[Point, ...]
    tier: int
    kind: str
    cost: float
    #: (layer, capsule, bbox) for every piece of copper this candidate places.
    shapes: tuple[tuple[str, Capsule, tuple[float, float, float, float]], ...]

    @property
    def sort_key(self) -> tuple:
        return (self.cost, self.kind, self.net, self.tasks, self.index)


def _capsules_of(
    pieces: Sequence[Piece], vias: Sequence[Point], via_pad_mm: float
) -> tuple[tuple[str, Capsule, tuple[float, float, float, float]], ...]:
    out = []
    for piece in pieces:
        for a, b in zip(piece.points, piece.points[1:]):
            if a == b:
                continue
            cap = segment_capsule(a.x, a.y, b.x, b.y, piece.width_mm)
            out.append((piece.layer, cap, _bbox(cap)))
    for via in vias:
        cap = disc_capsule(via.x, via.y, via_pad_mm)
        box = _bbox(cap)
        out.append((TOP, cap, box))
        out.append((BOTTOM, cap, box))
    return tuple(out)


def _bbox(cap: Capsule) -> tuple[float, float, float, float]:
    ax, ay, bx, by, r = cap
    return (min(ax, bx) - r, min(ay, by) - r, max(ax, bx) + r, max(ay, by) + r)


# --------------------------------------------------------------------------
# Pad access: which way copper leaves a pad
# --------------------------------------------------------------------------


def _pad_axes(pad: Pad) -> tuple[tuple[float, float], tuple[float, float]]:
    """The pad's own axes in board coordinates, long axis first."""
    if pad.rotation_deg:
        theta = math.radians(pad.rotation_deg)
        u = (math.cos(theta), math.sin(theta))
        v = (-math.sin(theta), math.cos(theta))
    else:
        u, v = (1.0, 0.0), (0.0, 1.0)
    return (u, v) if pad.width_mm >= pad.height_mm else (v, u)


def _half_extent(pad: Pad, direction: tuple[float, float]) -> float:
    """How far the pad's copper reaches along ``direction`` from its centre."""
    if pad.rotation_deg:
        theta = math.radians(pad.rotation_deg)
        u = (math.cos(theta), math.sin(theta))
        v = (-math.sin(theta), math.cos(theta))
    else:
        u, v = (1.0, 0.0), (0.0, 1.0)
    dx, dy = direction
    return abs(dx * u[0] + dy * u[1]) * pad.width_mm / 2.0 + abs(
        dx * v[0] + dy * v[1]
    ) * pad.height_mm / 2.0


def _escape_directions(
    pad: Pad, centroid: Point, toward: Point | None
) -> tuple[tuple[float, float], ...]:
    """Where copper may leave this pad, best first.

    The first direction is the pad's long axis pointing away from its own
    component's centre: on a 0.4mm-pitch QFN that is the *only* way out, because
    the 0.2mm gap between two neighbouring pads cannot hold a 0.1mm track and
    two clearances. The four axes follow as fallbacks for square pads.
    """
    long_axis, short_axis = _pad_axes(pad)
    dx, dy = pad.center.x - centroid.x, pad.center.y - centroid.y
    ordered: list[tuple[float, float]] = []
    if abs(dx) > 1e-9 or abs(dy) > 1e-9:
        sign = 1.0 if (dx * long_axis[0] + dy * long_axis[1]) >= 0 else -1.0
        ordered.append((long_axis[0] * sign, long_axis[1] * sign))
        sign = 1.0 if (dx * short_axis[0] + dy * short_axis[1]) >= 0 else -1.0
        ordered.append((short_axis[0] * sign, short_axis[1] * sign))
    else:
        ordered.append(long_axis)
    if toward is not None:
        tx, ty = toward.x - pad.center.x, toward.y - pad.center.y
        norm = math.hypot(tx, ty)
        if norm > 1e-9:
            candidate = (tx / norm, ty / norm)
            if all(
                abs(candidate[0] - d[0]) + abs(candidate[1] - d[1]) > 0.2
                for d in ordered
            ):
                ordered.append(candidate)
    for axis in ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)):
        if all(abs(axis[0] - d[0]) + abs(axis[1] - d[1]) > 0.2 for d in ordered):
            ordered.append(axis)
    return tuple(ordered)


# --------------------------------------------------------------------------
# The candidate factory
# --------------------------------------------------------------------------


class Candidates:
    """Enumerates legal copper for a task, checked against the static board.

    "Static" is the important word: every candidate is checked against pads,
    drills, keepouts, the outline and any pre-existing copper — never against
    another candidate. Candidate-versus-candidate is the solver's job, and
    keeping the two separate is what lets the solver reason about the whole
    board at once instead of about whatever happened to be committed first.
    """

    def __init__(self, problem: RoutingProblem, tiers: Sequence[Tier]):
        self.problem = problem
        self.tiers = tuple(tiers)
        self.rules = problem.rules
        self.centroids: dict[str, Point] = {}
        groups: dict[str, list[Pad]] = {}
        for pad in problem.pads:
            groups.setdefault(pad.component or pad.id, []).append(pad)
        for name, pads in groups.items():
            self.centroids[name] = Point(
                sum(p.center.x for p in pads) / len(pads),
                sum(p.center.y for p in pads) / len(pads),
            )
        self._counter = 0

    # -- pad access ------------------------------------------------------

    def accesses(
        self, pad: Pad, layer: str, tier: Tier, toward: Point | None, width: float
    ) -> list[Access]:
        centroid = self.centroids.get(pad.component or pad.id, pad.center)
        directions = _escape_directions(pad, centroid, toward)
        out: list[Access] = []
        if pad.reachable_from(layer):
            out.append(Access(point=pad.center))
            for direction in directions[:2]:
                for extra in ESCAPE_STANDOFFS_MM:
                    reach = (
                        _half_extent(pad, direction)
                        + tier.clearance_mm
                        + width / 2.0
                        + 0.05
                        + extra
                    )
                    out.append(
                        Access(
                            point=Point(
                                pad.center.x + direction[0] * reach,
                                pad.center.y + direction[1] * reach,
                            ),
                            prefix=(pad.center,),
                            cost_hint=reach,
                        )
                    )
            return out
        source = pad.layers[0] if pad.layers else TOP
        for direction in directions[:3]:
            for extra in VIA_STANDOFFS_MM:
                reach = (
                    _half_extent(pad, direction)
                    + tier.clearance_mm
                    + self.rules.via_pad_mm / 2.0
                    + 0.05
                    + extra
                )
                point = Point(
                    pad.center.x + direction[0] * reach,
                    pad.center.y + direction[1] * reach,
                )
                out.append(
                    Access(
                        point=point,
                        pieces=(Piece(source, (pad.center, point), width),),
                        vias=(point,),
                        cost_hint=reach + VIA_COST,
                    )
                )
        return out

    # -- shapes ----------------------------------------------------------

    def families(self, long_route: bool) -> tuple[tuple, ...]:
        """The shape families, in the order worth sampling.

        A family is a *kind* of route, not a route: "L, corner to the east",
        "shifted 2.5mm to the left of the straight line". The generator takes at
        most one candidate from each, which is what makes ten candidates ten
        genuinely different choices instead of ten wiggles in the same corridor.

        The order changes with length, and that is the fix for the failure that
        cost this router the whole keyboard: fifteen buses converging on one
        QFN, every candidate hugging the straight line, so no assignment existed
        where they all fit. A long route's useful freedom is *lateral*, so the
        shifted corridors come before the bends.
        """
        bends: list[tuple] = [("L", 0), ("L", 1)]
        zs: list[tuple] = [("Z", axis, 0.5) for axis in ("x", "y")]
        tail = [("Z", axis, frac) for frac in (0.25, 0.75) for axis in ("x", "y")]
        shifts: list[tuple] = []
        for offset in DETOURS_MM:
            shifts.append(("D", offset))
            shifts.append(("D", -offset))
        if long_route:
            return tuple([("S",)] + shifts + bends + zs + tail)
        return tuple([("S",)] + bends + zs + shifts + tail)

    @staticmethod
    def shape(family: tuple, a: Point, b: Point) -> tuple[Point, ...] | None:
        kind = family[0]
        if kind == "S":
            return (a, b)
        if kind == "L":
            if a.x == b.x or a.y == b.y:
                return None
            return (a, Point(b.x, a.y), b) if family[1] == 0 else (
                a, Point(a.x, b.y), b
            )
        if kind == "Z":
            if a.x == b.x or a.y == b.y:
                return None
            _, axis, frac = family
            if axis == "x":
                mid = a.x + (b.x - a.x) * frac
                return (a, Point(mid, a.y), Point(mid, b.y), b)
            mid = a.y + (b.y - a.y) * frac
            return (a, Point(a.x, mid), Point(b.x, mid), b)
        offset = family[1]
        dx, dy = b.x - a.x, b.y - a.y
        norm = math.hypot(dx, dy)
        if norm < 1e-9:
            return None
        ox, oy = -dy / norm * offset, dx / norm * offset
        return (a, Point(a.x + ox, a.y + oy), Point(b.x + ox, b.y + oy), b)
    # -- assembly --------------------------------------------------------

    def _make(
        self,
        tasks: tuple[int, ...],
        net: str,
        pieces: Sequence[Piece],
        vias: Sequence[Point],
        tier: Tier,
        kind: str,
    ) -> Candidate:
        length = sum(p.length_mm for p in pieces)
        cost = (
            length
            + VIA_COST * len(vias)
            + TIGHT_TIER_COST * tier.index
        )
        self._counter += 1
        return Candidate(
            index=self._counter,
            net=net,
            tasks=tasks,
            pieces=tuple(pieces),
            vias=tuple(vias),
            tier=tier.index,
            kind=kind,
            cost=cost,
            shapes=_capsules_of(pieces, vias, self.rules.via_pad_mm),
        )

    def _legal(
        self, tier: Tier, net: str, pieces: Sequence[Piece], vias: Sequence[Point]
    ) -> bool:
        ws = tier.workspace
        for via in vias:
            if ws.via_ok(via, net) is not True:
                return False
        for piece in pieces:
            if ws.path_ok(piece.layer, piece.points, piece.width_mm, net) is not True:
                return False
        return True

    # -- one pad-to-pad connection ---------------------------------------

    def for_task(
        self,
        task: Task,
        width: float,
        meter: BudgetMeter,
        max_candidates: int,
        max_tests: int,
    ) -> list[Candidate]:
        if task.is_stitch:
            return self._stitch(task, width, meter, max_candidates)
        found: dict[tuple, Candidate] = {}
        pad_a, pad_b = task.a, task.b
        assert pad_b is not None
        # A quota per layer, not one pot. Without it the top layer fills the
        # whole list, the solver never sees a bottom-layer option, and the
        # router silently becomes single-sided — which is exactly how it scored
        # 0 vias and 60% on a board the baseline finished at 80% with 16.
        # One candidate per shape family, per layer. Two quotas, and both were
        # bought with a measured regression:
        #
        #   * without a per-*layer* quota the top layer filled the list, the
        #     solver never saw a bottom-layer option, and a board the baseline
        #     finished at 80% came back at 60% with zero vias;
        #   * without a per-*family* quota every candidate hugged the straight
        #     line, so fifteen buses converging on one QFN had no assignment
        #     that fitted and the whole keyboard matrix went unrouted.
        layer_cap = max(4, (max_candidates + 1) // 2)
        layer_tests = max(24, max_tests // 2)
        long_route = pad_a.center.distance_to(pad_b.center) >= LONG_ROUTE_MM
        families = self.families(long_route)
        for tier in self.tiers:
            for layer in self._layer_order(pad_a, pad_b):
                tests = 0
                here = 0
                list_a = self.accesses(
                    pad_a, layer, tier, pad_b.center, width
                )[:MAX_ACCESSES]
                list_b = self.accesses(
                    pad_b, layer, tier, pad_a.center, width
                )[:MAX_ACCESSES]
                pairs = [
                    (access_a, access_b)
                    for access_a in list_a
                    for access_b in list_b
                    if access_a.point != access_b.point
                ]
                for family in families:
                    if tests >= layer_tests or here >= layer_cap:
                        break
                    for access_a, access_b in pairs:
                        if tests >= layer_tests:
                            break
                        points = self.shape(
                            family, access_a.point, access_b.point
                        )
                        if points is None:
                            break  # this family does not apply to this pair
                        tests += 1
                        meter.tick()
                        main = (
                            access_a.prefix
                            + points
                            + tuple(reversed(access_b.prefix))
                        )
                        pieces = (
                            list(access_a.pieces)
                            + [Piece(layer, main, width)]
                            + list(access_b.pieces)
                        )
                        vias = list(access_a.vias) + list(access_b.vias)
                        bucket = self._bucket(layer, main, vias)
                        if bucket in found:
                            continue
                        if not self._legal(tier, task.net, pieces, vias):
                            continue
                        found[bucket] = self._make(
                            (task.index,), task.net, pieces, vias, tier,
                            "pattern",
                        )
                        here += 1
                        break  # one per family: spread beats depth
            if found:
                break  # tier 0 succeeded; do not pay for tier 1
        return sorted(found.values(), key=lambda c: c.sort_key)[:max_candidates]

    def _layer_order(self, pad_a: Pad, pad_b: Pad) -> tuple[str, ...]:
        """Try the layer both pads already live on first."""
        shared = [
            layer
            for layer in (TOP, BOTTOM)
            if pad_a.reachable_from(layer) and pad_b.reachable_from(layer)
        ]
        rest = [layer for layer in (TOP, BOTTOM) if layer not in shared]
        return tuple(shared + rest)

    def _bucket(
        self, layer: str, points: Sequence[Point], vias: Sequence[Point]
    ) -> tuple:
        """Coarse identity, so ten candidates are ten *different* routes.

        Two polylines that round to the same half-millimetre corridor are the
        same choice as far as the solver is concerned, and keeping both costs
        conflict work for nothing.
        """
        return (
            layer,
            len(vias),
            tuple((round(p.x * 2) / 2, round(p.y * 2) / 2) for p in points),
        )

    # -- plane stitching -------------------------------------------------

    def _stitch(
        self, task: Task, width: float, meter: BudgetMeter, max_candidates: int
    ) -> list[Candidate]:
        """Get one pad into a poured plane, with one via and the shortest stub.

        This is the case the router we ship cannot express at all: it counted
        our 73 ground vias as 73 obstacles and produced byte-identical copper
        with and without the pour.
        """
        plane = task.plane
        assert plane is not None
        shape = self._plane_index(plane)
        pad = task.a
        found: dict[tuple, Candidate] = {}
        for tier in self.tiers:
            centroid = self.centroids.get(pad.component or pad.id, pad.center)
            for direction in _escape_directions(pad, centroid, None):
                for extra in VIA_STANDOFFS_MM:
                    if len(found) >= max_candidates:
                        break
                    meter.tick()
                    reach = (
                        _half_extent(pad, direction)
                        + tier.clearance_mm
                        + self.rules.via_pad_mm / 2.0
                        + 0.05
                        + extra
                    )
                    point = Point(
                        pad.center.x + direction[0] * reach,
                        pad.center.y + direction[1] * reach,
                    )
                    if not shape.contains(point.x, point.y):
                        continue
                    layer = pad.layers[0] if pad.layers else TOP
                    pieces = [Piece(layer, (pad.center, point), width)]
                    vias = [point]
                    bucket = self._bucket(layer, (pad.center, point), vias)
                    if bucket in found:
                        continue
                    if not self._legal(tier, task.net, pieces, vias):
                        continue
                    found[bucket] = self._make(
                        (task.index,), task.net, pieces, vias, tier, "stitch"
                    )
            if found:
                break
        return sorted(found.values(), key=lambda c: c.sort_key)

    def _plane_index(self, plane: Plane):
        from routerlib.geometry import PolygonIndex

        cache = getattr(self, "_plane_cache", None)
        if cache is None:
            cache = {}
            self._plane_cache = cache  # type: ignore[attr-defined]
        if plane.id not in cache:
            cache[plane.id] = PolygonIndex(plane.outline)
        return cache[plane.id]

    # -- the structured half ---------------------------------------------

    def spines(
        self,
        net: Net,
        tasks: Sequence[Task],
        widths: Sequence[float],
        meter: BudgetMeter,
    ) -> list[Candidate]:
        """A run of collinear connections becomes one track and N stubs.

        This is the whole structured half. Five ROW nets on a key matrix are
        ten pads each on one horizontal line 10mm apart; the textbook answer is
        a channel beside the line, not ten independent searches. Detecting the
        line is three lines of arithmetic and it collapses nine connections
        into one decision the solver can accept or reject as a unit.
        """
        out: list[Candidate] = []
        for axis in ("x", "y"):
            for chain in self._chains(tasks, axis):
                # Widest first. A 0.5mm rail cannot leave a 0.4mm-pitch QFN pad
                # — the stub alone would sit 0.05mm from the neighbour — so a
                # power spine that fails at its own width is retried necked
                # down, and the report counts the DFM warning that buys.
                for width in widths:
                    found = self._spine_for(net, chain, axis, width, meter)
                    if found:
                        out.extend(found)
                        break
        return out

    def _chains(self, tasks: Sequence[Task], axis: str) -> list[list[Task]]:
        """Maximal groups of connections whose two pads share a coordinate."""
        other = "y" if axis == "x" else "x"
        groups: dict[float, list[Task]] = {}
        for task in tasks:
            if task.is_stitch or task.b is None:
                continue
            va = getattr(task.a.center, other)
            vb = getattr(task.b.center, other)
            if abs(va - vb) > COLINEAR_TOL_MM:
                continue
            groups.setdefault(round(va, 2), []).append(task)
        return [
            sorted(group, key=lambda t: t.index)
            for _, group in sorted(groups.items())
            if len(group) >= 2
        ]

    def _spine_for(
        self,
        net: Net,
        chain: Sequence[Task],
        axis: str,
        width: float,
        meter: BudgetMeter,
    ) -> list[Candidate]:
        other = "y" if axis == "x" else "x"
        pads: dict[str, Pad] = {}
        for task in chain:
            pads[task.a.id] = task.a
            if task.b is not None:
                pads[task.b.id] = task.b
        ordered = sorted(pads.values(), key=lambda p: getattr(p.center, axis))
        line = getattr(ordered[0].center, other)
        lo = getattr(ordered[0].center, axis)
        hi = getattr(ordered[-1].center, axis)
        task_ids = tuple(sorted(t.index for t in chain))
        direction = (0.0, 1.0) if axis == "x" else (1.0, 0.0)
        clear_need = max(_half_extent(p, direction) for p in ordered)

        out: list[Candidate] = []
        for tier in self.tiers:
            base = clear_need + tier.clearance_mm + width / 2.0 + 0.05
            for sign in (1.0, -1.0):
                for extra in SPINE_OFFSETS_MM:
                    meter.tick()
                    track = line + sign * (base + extra)
                    pieces = [
                        Piece(
                            TOP if ordered[0].reachable_from(TOP) else BOTTOM,
                            self._spine_points(axis, lo, hi, track),
                            width,
                        )
                    ]
                    layer = pieces[0].layer
                    if not all(p.reachable_from(layer) for p in ordered):
                        continue
                    for pad in ordered:
                        pieces.append(
                            Piece(
                                layer,
                                (
                                    pad.center,
                                    self._on_track(axis, pad.center, track),
                                ),
                                width,
                            )
                        )
                    if not self._legal(tier, net.id, pieces, ()):
                        continue
                    out.append(
                        self._make(task_ids, net.id, pieces, (), tier, "spine")
                    )
                    break  # the closest legal offset on this side is the best
            if out:
                break
        return out

    @staticmethod
    def _spine_points(axis: str, lo: float, hi: float, track: float):
        if axis == "x":
            return (Point(lo, track), Point(hi, track))
        return (Point(track, lo), Point(track, hi))

    @staticmethod
    def _on_track(axis: str, centre: Point, track: float) -> Point:
        return Point(centre.x, track) if axis == "x" else Point(track, centre.y)


# --------------------------------------------------------------------------
# The exact half: an ILP, and a branch-and-bound that closes it
# --------------------------------------------------------------------------


@dataclass
class SolveStats:
    components: int = 0
    proved: int = 0
    nodes: int = 0
    largest_component: int = 0
    unproved_tasks: int = 0
    repairs: int = 0
    repairs_proved: int = 0

    def as_dict(self) -> dict:
        return {
            "components": self.components,
            "provedOptimal": self.proved,
            "nodes": self.nodes,
            "largestComponent": self.largest_component,
            "tasksInUnprovedComponents": self.unproved_tasks,
            "exactRepairs": self.repairs,
            "exactRepairsProved": self.repairs_proved,
        }


def conflict_sets(
    candidates: Sequence[Candidate], tiers: Sequence[Tier]
) -> list[set[int]]:
    """Which candidates physically exclude which.

    Two candidates conflict when copper of *different* nets comes closer than
    the clearance the looser of the two was designed to. Same-net copper never
    conflicts: two branches of one net are allowed to touch, and in fact that is
    how a net gets connected.

    Bounding boxes are compared inline before any distance is measured, because
    the distance function is the pipeline's and a Python call per pair is what
    makes this quadratic instead of merely large.
    """
    out: list[set[int]] = [set() for _ in candidates]
    clearance = [tiers[c.tier].clearance_mm for c in candidates]
    reach = max(t.clearance_mm for t in tiers)
    grids: dict[str, GridIndex] = {}
    for position, cand in enumerate(candidates):
        for layer, cap, _ in cand.shapes:
            grids.setdefault(layer, GridIndex(4.0)).insert(cap, position)
    for position, cand in enumerate(candidates):
        for layer, cap, box in cand.shapes:
            grid = grids.get(layer)
            if grid is None:
                continue
            x0, y0, x1, y1 = box
            mine = out[position]
            for other_cap, other_pos in grid.query(cap, margin=reach):
                if other_pos <= position or other_pos in mine:
                    continue
                if candidates[other_pos].net == cand.net:
                    continue
                need = min(clearance[position], clearance[other_pos])
                ox0, oy0, ox1, oy1 = _bbox(other_cap)
                if (
                    ox0 - x1 > need
                    or x0 - ox1 > need
                    or oy0 - y1 > need
                    or y0 - oy1 > need
                ):
                    continue
                if capsule_gap(cap, other_cap) < need:
                    mine.add(other_pos)
                    out[other_pos].add(position)
    return out


class ExactSelector:
    """Branch-and-bound over the candidate assignment.

    The model, written out:

    * one binary per (connection, candidate); at most one per connection
    * one binary per net, forced on when any of its connections is unrouted
    * a pairwise exclusion for every conflicting candidate pair
    * minimise ``NET_PENALTY * broken nets + VIA_COST * vias + length``

    Presolve is two standard steps and both matter here. **Dominance**: a
    candidate that costs more than another for the same connection *and*
    conflicts with a superset of the same candidates can never be in an optimal
    solution, so it is deleted. **Decomposition**: the conflict graph almost
    always falls apart into small components, and components are independent
    problems.
    """

    def __init__(
        self,
        tasks: Sequence[Task],
        candidates: Sequence[Candidate],
        tiers: Sequence[Tier],
        meter: BudgetMeter,
    ):
        self.tasks = list(tasks)
        self.tiers = list(tiers)
        self.meter = meter
        self.candidates = sorted(candidates, key=lambda c: c.sort_key)
        self.stats = SolveStats()

    def solve(self) -> tuple[dict[int, Candidate], SolveStats]:
        cands = self.candidates
        if not cands:
            return {}, self.stats
        masks = conflict_sets(cands, self.tiers)
        dead = self._drop_dominated(cands, masks)

        by_task: dict[int, list[int]] = {}
        for position, cand in enumerate(cands):
            if position in dead:
                continue
            for task_index in cand.tasks:
                by_task.setdefault(task_index, []).append(position)

        parent: dict[int, int] = {}

        def find(x: int) -> int:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        for task in self.tasks:
            find(task.index)
        for position, cand in enumerate(cands):
            if position in dead:
                continue
            for task_index in cand.tasks[1:]:
                union(cand.tasks[0], task_index)
            for other in sorted(masks[position]):
                if other in dead:
                    continue
                union(cand.tasks[0], cands[other].tasks[0])

        components: dict[int, list[int]] = {}
        for task in self.tasks:
            components.setdefault(find(task.index), []).append(task.index)

        net_of = {t.index: t.net for t in self.tasks}
        chosen: dict[int, Candidate] = {}
        broken: set[str] = set()
        ordered = sorted(
            components.values(), key=lambda members: (-len(members), members[0])
        )
        self.stats.components = len(ordered)
        for members in ordered:
            self.stats.largest_component = max(
                self.stats.largest_component, len(members)
            )
            local, proved = self._solve_component(
                members, by_task, cands, masks, net_of, broken
            )
            if proved:
                self.stats.proved += 1
            else:
                self.stats.unproved_tasks += len(members)
            covered = set()
            for position in sorted(set(local.values())):
                for task_index in cands[position].tasks:
                    if task_index in members:
                        covered.add(task_index)
                        chosen[task_index] = cands[position]
            for task_index in members:
                if task_index not in covered:
                    broken.add(net_of[task_index])
        return chosen, self.stats

    # -- presolve --------------------------------------------------------

    def _drop_dominated(
        self, cands: Sequence[Candidate], masks: list[set[int]]
    ) -> set[int]:
        """Delete candidates that can never be in an optimal solution.

        ``b`` dominates ``a`` when it covers the same connections, costs no
        more, and conflicts with a subset of what ``a`` conflicts with. Anything
        ``a`` fits into, ``b`` fits into at least as cheaply. Standard ILP
        presolve; on our instances it removes about a third of the columns and
        the removal is provably safe — an optimal solution using ``a`` stays
        optimal when ``a`` is swapped for ``b``.
        """
        by_tasks: dict[tuple[int, ...], list[int]] = {}
        for position, cand in enumerate(cands):
            by_tasks.setdefault(cand.tasks, []).append(position)
        dead: set[int] = set()
        for _, group in sorted(by_tasks.items()):
            if len(group) < 2:
                continue
            for a in group:
                for b in group:
                    if a == b or b in dead:
                        continue
                    if cands[b].cost > cands[a].cost:
                        continue
                    if cands[b].cost == cands[a].cost and b > a:
                        continue
                    if masks[b] <= masks[a]:
                        dead.add(a)
                        break
        for position, conflicts in enumerate(masks):
            if dead:
                masks[position] = conflicts - dead
        return dead

    # -- one component ---------------------------------------------------

    def _solve_component(
        self,
        members: Sequence[int],
        by_task: dict[int, list[int]],
        cands: Sequence[Candidate],
        masks: list[set[int]],
        net_of: dict[int, str],
        broken_already: set[str],
    ) -> tuple[dict[int, int], bool]:
        """Solve one conflict component: prove it if it is small, repair it
        exactly in pieces if it is not.

        A component of eight connections is closed in a few thousand nodes and
        the answer is a proof. A component of sixty-eight is not, and measured
        on ``matrix-rp2040-core__usb-c-data`` the plain branch-and-bound was
        useless there: 13k nodes and 1.36M nodes produced the *same* answer,
        because depth-first search improves the bottom of the tree and the
        damage is at the top.

        So a big component gets the treatment this family is for: take the
        incumbent, and around each connection it failed, cut out a small
        neighbourhood — that connection, the rest of its net, and every
        connection whose chosen route blocks it — and **solve that
        neighbourhood exactly**, holding everything else fixed. Each repair is
        a closed proof over its own window; the component as a whole is not,
        and the report says so.
        """
        options: dict[int, tuple[int, ...]] = {}
        for task_index in members:
            options[task_index] = tuple(
                sorted(
                    by_task.get(task_index, ()),
                    key=lambda p: (cands[p].cost, cands[p].index),
                )
            )

        node_limit = max(MIN_COMPONENT_NODES, NODES_PER_TASK * len(members))
        assign, cost, proved, nodes = self._search(
            members, options, cands, masks, net_of, broken_already, node_limit
        )
        self.stats.nodes += nodes
        if proved:
            return assign, True
        assign = self._repair_exactly(
            members, options, cands, masks, net_of, broken_already, assign, cost
        )
        return assign, False

    # -- the search ------------------------------------------------------

    def _search(
        self,
        members: Sequence[int],
        options: dict[int, tuple[int, ...]],
        cands: Sequence[Candidate],
        masks: list[set[int]],
        net_of: dict[int, str],
        broken_already: set[str],
        node_limit: int,
    ) -> tuple[dict[int, int], float, bool, int]:
        """Depth-first branch-and-bound with forward checking and MRV.

        * **Forward checking.** Choosing a candidate deletes, right then, every
          candidate it conflicts with from the remaining connections' option
          lists. A connection whose list empties is one we now know is lost, and
          the bound learns it immediately instead of at the bottom of the tree.
        * **Minimum remaining values.** The next connection to decide is always
          the one with the fewest options left.
        * **Least-constraining value.** Its candidates are tried in order of how
          much they exclude, not how cheap they are — the cheapest route is
          usually the straight one down the middle of the corridor everyone else
          needs.
        * **An admissible bound.** Cost so far, plus for every remaining
          connection the cheaper of its cheapest surviving candidate and the
          penalty for losing its net. Never optimistic in the wrong direction,
          so a search that closes is a proof and not a hope.
        """
        unit = [c.cost / len(c.tasks) for c in cands]
        order_key = [
            (len(masks[p]), cands[p].cost, cands[p].index)
            for p in range(len(cands))
        ]
        # The bound reads ``opts[0]`` as the cheapest surviving candidate, so
        # the option lists are sorted here rather than by contract. A caller who
        # passed them in another order would silently make the bound
        # inadmissible — which is to say, would make "proved optimal" false.
        # Caught by the brute-force test, which is the only reason to have one.
        options = {
            task_index: tuple(
                sorted(opts, key=lambda p: (cands[p].cost, cands[p].index))
            )
            for task_index, opts in options.items()
        }
        state: dict = {"best": math.inf, "assign": {}, "nodes": 0, "proved": True}

        def search(
            cost: float,
            broken: frozenset,
            assigned: dict[int, int],
            remaining: list[tuple[int, tuple[int, ...]]],
        ) -> None:
            if not state["proved"]:
                return
            state["nodes"] += 1
            self.meter.expand()
            if state["nodes"] > node_limit:
                state["proved"] = False
                return

            # Unit propagation: a connection with no surviving candidate is one
            # we have already lost, and its net with it.
            live: list[tuple[int, tuple[int, ...]]] = []
            for task_index, opts in remaining:
                if opts:
                    live.append((task_index, opts))
                    continue
                net = net_of[task_index]
                if net not in broken:
                    broken = broken | {net}
                    cost += NET_PENALTY
            if cost >= state["best"]:
                return

            bound = cost
            for task_index, opts in live:
                skip = 0.0 if net_of[task_index] in broken else NET_PENALTY
                bound += min(unit[opts[0]], skip)
                if bound >= state["best"]:
                    return

            if not live:
                state["best"] = cost
                state["assign"] = dict(assigned)
                return

            live.sort(key=lambda item: (len(item[1]), item[0]))
            task_index, opts = live[0]
            rest = live[1:]
            for choice in sorted(opts, key=lambda p: order_key[p]):
                cand = cands[choice]
                covered = set(cand.tasks)
                nxt = dict(assigned)
                for other in cand.tasks:
                    nxt[other] = choice
                blocked = masks[choice]
                pruned = [
                    (t2, tuple(p for p in o2 if p not in blocked))
                    for t2, o2 in rest
                    if t2 not in covered
                ]
                search(cost + cand.cost, broken, nxt, pruned)
                if not state["proved"]:
                    return
            net = net_of[task_index]
            penalty = 0.0 if net in broken else NET_PENALTY
            search(cost + penalty, broken | {net}, assigned, rest)

        search(
            0.0,
            frozenset(broken_already),
            {},
            [(t, options[t]) for t in sorted(members)],
        )
        assign = {
            t: p for t, p in dict(state["assign"]).items() if t in set(members)
        }
        return assign, float(state["best"]), bool(state["proved"]), state["nodes"]

    # -- exact repair of a neighbourhood ---------------------------------

    def _repair_exactly(
        self,
        members: Sequence[int],
        options: dict[int, tuple[int, ...]],
        cands: Sequence[Candidate],
        masks: list[set[int]],
        net_of: dict[int, str],
        broken_already: set[str],
        assign: dict[int, int],
        cost: float,
    ) -> dict[int, int]:
        """Cut a window around each failure and solve that window optimally.

        The window is: the failed connection, every other connection of its net,
        and every connection whose *chosen* route is one of the things blocking
        it. Everything outside the window stays exactly where it is, so the
        candidates inside are filtered by what the outside already occupies and
        the sub-problem is a genuine, smaller instance of the same ILP. It is
        solved to closure, so a repair that lands is optimal for its window.
        """
        for _ in range(LNS_PASSES):
            improved = False
            failures = sorted(t for t in members if t not in assign)
            seen_nets: set[str] = set()
            for failed in failures:
                if failed in assign:
                    continue
                net = net_of[failed]
                if net in seen_nets:
                    continue
                seen_nets.add(net)
                window = self._window(
                    failed, members, options, cands, masks, net_of, assign
                )
                if len(window) < 2:
                    continue
                outside = {
                    p for t, p in assign.items() if t not in window
                }
                sub_options = {}
                for task_index in sorted(window):
                    sub_options[task_index] = tuple(
                        p
                        for p in options[task_index]
                        # not blocked by copper we are holding fixed, and — for
                        # a spine — not reaching outside the window, because a
                        # decision half inside it is not a decision.
                        if not (masks[p] & outside)
                        and set(cands[p].tasks) <= window
                    )
                broken_outside = set(broken_already) | {
                    net_of[t]
                    for t in members
                    if t not in window and t not in assign
                }
                before = _window_cost(
                    window, assign, cands, net_of, broken_outside
                )
                sub_assign, after, proved, nodes = self._search(
                    sorted(window), sub_options, cands, masks, net_of,
                    broken_outside, LNS_NODES,
                )
                self.stats.nodes += nodes
                self.stats.repairs += 1
                if proved:
                    self.stats.repairs_proved += 1
                if after < before - 1e-9:
                    for task_index in window:
                        assign.pop(task_index, None)
                    assign.update(sub_assign)
                    improved = True
            if not improved:
                break
        return assign

    def _window(
        self,
        failed: int,
        members: Sequence[int],
        options: dict[int, tuple[int, ...]],
        cands: Sequence[Candidate],
        masks: list[set[int]],
        net_of: dict[int, str],
        assign: dict[int, int],
    ) -> set[int]:
        blockers: set[int] = set()
        for position in options[failed]:
            blockers |= masks[position]
        window = {failed}
        for task_index in sorted(members):
            if net_of[task_index] == net_of[failed]:
                window.add(task_index)
            elif assign.get(task_index) in blockers:
                window.add(task_index)
            if len(window) >= LNS_WINDOW:
                break
        # A candidate covering several connections is one decision: if one of
        # them is inside the window all of them have to be, or releasing it
        # would leave copper claiming a connection nobody chose.
        for task_index in sorted(window):
            position = assign.get(task_index)
            if position is not None:
                window |= set(cands[position].tasks)
        return window & set(members)


def _window_cost(
    window: set[int],
    assign: dict[int, int],
    cands: Sequence[Candidate],
    net_of: dict[int, str],
    broken_outside: set[str],
) -> float:
    """What the current answer costs inside one window, on the same scale the
    sub-solve reports — otherwise "better" would not mean better."""
    cost = 0.0
    for position in sorted({assign[t] for t in window if t in assign}):
        cost += cands[position].cost
    broken = set(broken_outside)
    for task_index in sorted(window):
        if task_index in assign:
            continue
        net = net_of[task_index]
        if net not in broken:
            broken.add(net)
            cost += NET_PENALTY
    return cost


# --------------------------------------------------------------------------
# The router
# --------------------------------------------------------------------------


class ExactStructuredRouter:
    """Detect the structure, enumerate the copper, choose it with an ILP."""

    name = NAME

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        started = time.perf_counter()
        meter = budget.meter()
        notes: list[str] = []
        tiers = _tiers(problem)
        factory = Candidates(problem, tiers)

        # Enumerate once. A candidate is checked against the *static* board
        # only, so nothing about it changes when another net is dropped — which
        # means the rip-up rounds below cost a re-solve, not a re-enumeration.
        all_tasks, widths, trees = _build_tasks(problem)
        budgets = _caps(len(all_tasks))
        per_net: dict[str, list[Task]] = {}
        for task in all_tasks:
            per_net.setdefault(task.net, []).append(task)
        nets_by_id = problem.nets_by_id
        all_candidates: list[Candidate] = []
        for net_id in sorted(per_net):
            net = nets_by_id[net_id]
            width = widths[net_id]
            all_candidates.extend(
                factory.spines(
                    net,
                    per_net[net_id],
                    _widths_to_try(net, width, problem.rules),
                    meter,
                )
            )
            for task in per_net[net_id]:
                if meter.exhausted:
                    break
                all_candidates.extend(
                    factory.for_task(task, width, meter, budgets[0], budgets[1])
                )
        repaired = _repair(
            problem, factory, all_tasks, all_candidates, widths, trees, meter,
            budgets,
        )
        notes.append(
            f"{len(all_tasks)} connection(s), {len(all_candidates)} candidate "
            f"route(s) enumerated "
            f"({sum(1 for c in all_candidates if c.kind == 'spine')} structured "
            f"spines, {repaired} connection(s) rescued by a narrower rail, a "
            "different tree edge, or a hop to a neighbour already in the pour)"
        )

        dropped: set[str] = set()
        pieces: list[Piece] = []
        piece_nets: list[str] = []
        vias: list[Point] = []
        via_nets: list[str] = []
        stats = SolveStats()

        for round_index in range(MAX_ROUNDS):
            tasks = [t for t in all_tasks if t.net not in dropped]
            candidates = [c for c in all_candidates if c.net not in dropped]
            if not tasks:
                break
            selector = ExactSelector(tasks, candidates, tiers, meter)
            chosen, stats = selector.solve()
            failed = sorted({t.net for t in tasks if t.index not in chosen})
            pieces, piece_nets, vias, via_nets = _collect(
                {k: v for k, v in chosen.items() if v.net not in set(failed)}
            )
            if not failed:
                break
            if dropped | set(failed) == dropped:
                break
            dropped |= set(failed)
            notes.append(
                f"round {round_index + 1}: {len(failed)} net(s) could not be "
                "finished; their copper was removed and the rest re-solved"
            )

        unrouted = sorted(dropped)
        solution = _emit(problem, pieces, piece_nets, vias, via_nets, unrouted,
                         meter, started, notes, stats)
        solution = _defend(problem, solution, notes)
        return solution

    # A convenience the tournament harness does not need but a human does.
    def report(self, problem: RoutingProblem, budget: Budget) -> dict:
        solution = self.route(problem, budget)
        return {"instance": problem.id, "notes": list(solution.notes)}


def _caps(task_count: int) -> tuple[int, int]:
    if task_count > BIG_PROBLEM_TASKS:
        return (BIG_MAX_CANDIDATES, BIG_MAX_SHAPE_TESTS)
    return (MAX_CANDIDATES, MAX_SHAPE_TESTS)


def _build_tasks(
    problem: RoutingProblem,
) -> tuple[list[Task], dict[str, float], dict[str, list[tuple[Pad, Pad]]]]:
    """What has to be true, and how wide the copper has to be.

    A net with a poured plane becomes one *stitch* per pad — get into the
    plane — and no pad-to-pad connections at all. Everything else becomes the
    edges of a Euclidean minimum spanning tree over its pads. The tree is
    returned alongside, because an edge with no legal route can be swapped for
    a different edge that joins the same two halves of it.
    """
    planes_by_net: dict[str, list[Plane]] = {}
    for plane in problem.planes:
        planes_by_net.setdefault(plane.net, []).append(plane)

    tasks: list[Task] = []
    widths: dict[str, float] = {}
    trees: dict[str, list[tuple[Pad, Pad]]] = {}
    index = 0
    for net in problem.nets:
        if not net.routable:
            continue
        width = max(net.min_width_mm, problem.rules.min_trace_mm)
        widths[net.id] = width
        pads = sorted(
            problem.pads_of(net.id), key=lambda p: (p.center.x, p.center.y, p.id)
        )
        planes = planes_by_net.get(net.id)
        if planes:
            from routerlib.geometry import PolygonIndex

            shapes = {p.id: PolygonIndex(p.outline) for p in planes}
            for pad in pads:
                inside = next(
                    (
                        p
                        for p in planes
                        if pad.reachable_from(p.layer)
                        and shapes[p.id].contains(pad.center.x, pad.center.y)
                    ),
                    None,
                )
                if inside is not None and pad.kind == "plated_hole":
                    continue  # the barrel already lands in the pour
                target = next(
                    (
                        p
                        for p in planes
                        if shapes[p.id].contains(pad.center.x, pad.center.y)
                    ),
                    planes[0],
                )
                tasks.append(Task(index, net.id, pad, None, target))
                index += 1
            continue
        edges = _mst_edges(pads)
        trees[net.id] = edges
        for pad_a, pad_b in edges:
            tasks.append(Task(index, net.id, pad_a, pad_b))
            index += 1
    return tasks, widths, trees


def _mst_edges(pads: Sequence[Pad]) -> list[tuple[Pad, Pad]]:
    """Prim over pad centres, ties broken by pad id so the tree is stable."""
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


def _repair(
    problem: RoutingProblem,
    factory: Candidates,
    tasks: list[Task],
    candidates: list[Candidate],
    widths: dict[str, float],
    trees: dict[str, list[tuple[Pad, Pad]]],
    meter: BudgetMeter,
    budgets: tuple[int, int],
) -> int:
    """Rescue the connections that came back with nothing.

    Two rescues, in the order that costs the score least.

    **A narrower rail.** A power net asks for 0.5mm copper and sometimes there
    is no 0.5mm corridor. Routing it at 0.25mm earns exactly one DFM *warning*
    for that net; not routing it loses the whole net, and completeness is tier 1
    of the score while a warning is tier 2. So we take the warning, and the
    report says how many times.

    **A different tree edge.** The connection list came from a minimum spanning
    tree, and the MST is a guess. If the edge U1-R2 has no legal route, any
    other pad pair that joins the same two halves of the tree does the same
    electrical job — so we try those, nearest first. This is the one place the
    task decomposition is allowed to change, and it never changes an edge a
    structured spine is holding.
    """
    covered = {t for cand in candidates for t in cand.tasks}
    missing = [t for t in tasks if t.index not in covered]
    if not missing:
        return 0
    fixed = 0
    for task in missing:
        if task.is_stitch or task.b is None:
            # A pad with nowhere to put its own via is not a lost pad: any
            # neighbour of the same net that *did* reach the plane will do, and
            # a 2mm hop to it is cheaper than losing the whole ground net.
            if task.is_stitch and _reroute_stitch(
                problem, factory, tasks, candidates, covered, task, widths,
                meter, budgets,
            ):
                fixed += 1
            continue
        net = problem.nets_by_id[task.net]
        found: list[Candidate] = []
        for width in _widths_to_try(net, widths[net.id], problem.rules):
            found = factory.for_task(task, width, meter, budgets[0], budgets[1])
            if found:
                break
        if not found:
            for pad_a, pad_b in _alternative_edges(trees.get(net.id, []), task):
                swapped = Task(task.index, task.net, pad_a, pad_b)
                for width in _widths_to_try(net, widths[net.id], problem.rules):
                    found = factory.for_task(
                        swapped, width, meter, budgets[0], budgets[1]
                    )
                    if found:
                        break
                if found:
                    tasks[task.index] = swapped
                    break
        if found:
            candidates.extend(found)
            fixed += 1
    return fixed


def _reroute_stitch(
    problem: RoutingProblem,
    factory: Candidates,
    tasks: list[Task],
    candidates: list[Candidate],
    covered: set[int],
    task: Task,
    widths: dict[str, float],
    meter: BudgetMeter,
    budgets: tuple[int, int],
) -> bool:
    """Reach the plane through a neighbour instead of through your own via."""
    anchored = [
        other.a
        for other in tasks
        if other.net == task.net
        and other.index != task.index
        and other.is_stitch
        and other.index in covered
    ]
    in_plane = {other.a.id for other in tasks if other.net == task.net}
    for pad in problem.pads_of(task.net):
        if pad.id not in in_plane:
            anchored.append(pad)  # a barrel already sitting in the pour
    anchored.sort(key=lambda p: (task.a.center.distance_to(p.center), p.id))
    net = problem.nets_by_id[task.net]
    for pad in anchored[:6]:
        swapped = Task(task.index, task.net, task.a, pad)
        for width in _widths_to_try(net, widths[net.id], problem.rules):
            found = factory.for_task(
                swapped, width, meter, budgets[0], budgets[1]
            )
            if found:
                tasks[task.index] = swapped
                candidates.extend(found)
                return True
    return False


def _widths_to_try(net: Net, width: float, rules: DesignRules) -> list[float]:
    """The asked-for width, then the fallbacks a rail may accept."""
    if net.net_class not in ("power", "ground"):
        return [width]
    out = [width]
    for fallback in POWER_WIDTH_FALLBACKS_MM:
        if fallback < width - 1e-9 and fallback >= rules.warn_trace_mm - 1e-9:
            out.append(fallback)
    return out


def _alternative_edges(
    edges: Sequence[tuple[Pad, Pad]], task: Task
) -> list[tuple[Pad, Pad]]:
    """Pad pairs that join the same two halves of the tree as ``task`` does."""
    if task.b is None:
        return []
    target = (task.a.id, task.b.id)
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pads: dict[str, Pad] = {}
    for pad_a, pad_b in edges:
        pads[pad_a.id] = pad_a
        pads[pad_b.id] = pad_b
        if (pad_a.id, pad_b.id) == target or (pad_b.id, pad_a.id) == target:
            continue
        ra, rb = find(pad_a.id), find(pad_b.id)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    side_a = find(task.a.id)
    left = sorted(pid for pid in pads if find(pid) == side_a)
    right = sorted(pid for pid in pads if find(pid) != side_a)
    pairs = [
        (pads[x], pads[y])
        for x in left
        for y in right
        if (x, y) != target and (y, x) != target
    ]
    pairs.sort(key=lambda pair: (pair[0].center.distance_to(pair[1].center),
                                 pair[0].id, pair[1].id))
    return pairs[:8]


def _collect(
    chosen: dict[int, Candidate]
) -> tuple[list[Piece], list[str], list[Point], list[str]]:
    seen: set[int] = set()
    pieces: list[Piece] = []
    piece_nets: list[str] = []
    vias: list[Point] = []
    via_nets: list[str] = []
    via_seen: set[tuple[str, float, float]] = set()
    for _, cand in sorted(chosen.items()):
        if cand.index in seen:
            continue
        seen.add(cand.index)
        for piece in cand.pieces:
            pieces.append(piece)
            piece_nets.append(cand.net)
        for via in cand.vias:
            key = (cand.net, via.x, via.y)
            if key in via_seen:
                continue
            via_seen.add(key)
            vias.append(via)
            via_nets.append(cand.net)
    return pieces, piece_nets, vias, via_nets


def _emit(
    problem: RoutingProblem,
    pieces: Sequence[Piece],
    piece_nets: Sequence[str],
    vias: Sequence[Point],
    via_nets: Sequence[str],
    unrouted: Sequence[str],
    meter: BudgetMeter,
    started: float,
    notes: Sequence[str],
    stats: SolveStats,
) -> RoutingSolution:
    """Copper with deterministic ids: sorted first, numbered second."""
    ordered = sorted(
        zip(piece_nets, pieces),
        key=lambda item: (
            item[0],
            item[1].layer,
            item[1].width_mm,
            tuple((p.x, p.y) for p in item[1].points),
        ),
    )
    traces = tuple(
        Trace(
            id=f"exs_t{index}",
            net=net,
            layer=piece.layer,
            points=piece.points,
            width_mm=piece.width_mm,
        )
        for index, (net, piece) in enumerate(ordered)
    )
    ordered_vias = sorted(
        zip(via_nets, vias), key=lambda item: (item[0], item[1].x, item[1].y)
    )
    via_objects = tuple(
        Via(
            id=f"exs_v{index}",
            net=net,
            center=point,
            drill_mm=problem.rules.via_drill_mm,
            pad_mm=problem.rules.via_pad_mm,
        )
        for index, (net, point) in enumerate(ordered_vias)
    )
    extra = [
        f"exact (final round): {stats.proved}/{stats.components} conflict "
        "components proved optimal over the candidate set; largest is "
        f"{stats.largest_component} connections, {stats.nodes} nodes expanded"
    ]
    if stats.unproved_tasks:
        extra.append(
            f"{stats.unproved_tasks} connection(s) sit in components too big to "
            f"close; {stats.repairs_proved}/{stats.repairs} exact window "
            "repairs closed inside them — good answers there, not proved ones"
        )
    if unrouted:
        extra.append(
            f"{len(unrouted)} net(s) left unrouted, and their copper was "
            "removed rather than shipped as a partial net"
        )
    return RoutingSolution(
        router=NAME,
        traces=traces,
        vias=via_objects,
        complete=not unrouted,
        unrouted_nets=tuple(sorted(unrouted)),
        iterations=meter.iterations,
        nodes_expanded=meter.nodes,
        wall_clock_s=time.perf_counter() - started,
        notes=tuple(list(notes) + extra),
    )


def _defend(
    problem: RoutingProblem,
    solution: RoutingSolution,
    notes: list[str],
) -> RoutingSolution:
    """Score our own copper before anyone else does, and delete what fails.

    The candidates were checked against the static board and against each
    other, so this should find nothing. It runs anyway, with the scorer's own
    checker, because "should" is not a measurement — and if it *does* find
    something, the copper of the nets involved is removed rather than shipped.
    A net we cannot defend is an unrouted net, which is an honest bad score;
    copper that violates a rule is a scrapped board.
    """
    from routerlib import drc as drc_mod

    for _ in range(2):
        result = drc_mod.check(problem, solution, use_pipeline=False)
        errors = [v for v in result.errors if v.kind != "dfm_power_trace_width"]
        if not errors:
            return solution
        mine = {t.id: t.net for t in solution.traces}
        mine.update({v.id: v.net for v in solution.vias})
        guilty: set[str] = set()
        for violation in errors:
            for item in violation.items:
                base = item.split("#")[0]
                if base in mine:
                    guilty.add(mine[base])
        if not guilty:
            notes.append(
                f"{len(errors)} DRC error(s) the router could not attribute to "
                "its own copper — reported, not hidden"
            )
            return replace(solution, notes=tuple(list(solution.notes) + notes[-1:]))
        notes.append(
            f"self-check found {len(errors)} error(s); dropped "
            f"{len(guilty)} net(s) rather than ship copper we cannot defend"
        )
        solution = replace(
            solution,
            traces=tuple(t for t in solution.traces if t.net not in guilty),
            vias=tuple(v for v in solution.vias if v.net not in guilty),
            unrouted_nets=tuple(sorted(set(solution.unrouted_nets) | guilty)),
            complete=False,
            notes=tuple(list(solution.notes) + notes[-1:]),
        )
    return solution


ROUTERS = {ExactStructuredRouter.name: ExactStructuredRouter}


# --------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    from routerlib.bench import load_all, run_suite

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
        ExactStructuredRouter,
        problems,
        budget,
        check_determinism=args.determinism,
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


def _exact(args: argparse.Namespace) -> int:
    """Print what the exact half proved, per instance."""
    from routerlib.bench import load_all
    from routerlib.scoring import score

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
    for problem in problems:
        router = ExactStructuredRouter()
        solution = router.route(problem, budget)
        result = score(problem, solution)
        print(result.line())
        for note in solution.notes:
            print(f"    {note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=NAME, description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("run", _run), ("exact", _exact)):
        p = sub.add_parser(name)
        p.add_argument("--dir", default=None)
        p.add_argument("--only", default=None)
        p.add_argument("--max-iterations", type=int, default=20_000_000)
        p.add_argument("--max-nodes", type=int, default=20_000_000)
        p.add_argument("--wall-clock-cap", type=float, default=1800.0)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--report", default=None)
        p.add_argument("--determinism", action="store_true")
        p.set_defaults(func=func)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
