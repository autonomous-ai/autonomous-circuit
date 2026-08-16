"""The routing contract: what a router is given, what it must return.

Everything here is a frozen dataclass in millimetres, because two different
algorithms have to be comparable and a mutable problem is a problem that the
first algorithm can edit before the second one sees it.

Three types carry the whole contract:

* :class:`RoutingProblem` — a *placed* board. Components are where they are;
  the router may not move them. It carries the copper that already exists
  (pads, planes, any pre-routed net), the holes and keepouts it must avoid, the
  nets it must join, and the rules it is judged against.
* :class:`Budget` — how much search the router may spend, counted in
  **iterations and expanded nodes, never seconds**. Wall-clock is the reason
  the router we ship today gives a different board on a loaded machine than on
  an idle one; a benchmark that inherits that cannot measure anything.
* :class:`RoutingSolution` — traces, vias, and a self-reported ``complete``
  flag. The flag is never trusted: :mod:`routerlib.scoring` recomputes
  connectivity from the copper. It exists so a router can say *"I know I did
  not finish"*, which is the honest failure and the one we want to reward over
  a plausible-looking illegal board.

Units are millimetres and the coordinate frame is circuit.json's board frame
(origin at the board centre unless the board says otherwise). Y is up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Iterable, Literal, Protocol, Sequence, runtime_checkable

TOP = "top"
BOTTOM = "bottom"
LAYERS_2 = (TOP, BOTTOM)

#: What a net is for. The class decides the width it is routed at, whether it
#: prefers a plane to a trace, and whether it has a partner it must track.
NetClass = Literal["signal", "power", "ground", "diff_pair"]

#: Quantum for every coordinate a router emits: 1 nanometre. Coarser than
#: float noise, three orders of magnitude finer than anything a fab can hold.
#: Solutions are quantised on construction so "same input, same output" is a
#: property of the bytes and not of the floating-point weather.
QUANTUM_MM = 1e-6


def q(value: float) -> float:
    """Snap a coordinate to the 1 nm grid. ``-0.0`` normalises to ``0.0``."""
    snapped = round(float(value) / QUANTUM_MM) * QUANTUM_MM
    return snapped + 0.0


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", q(self.x))
        object.__setattr__(self, "y", q(self.y))

    def distance_to(self, other: "Point") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


def _points(raw: Iterable) -> tuple[Point, ...]:
    out = []
    for item in raw:
        if isinstance(item, Point):
            out.append(item)
        elif isinstance(item, dict):
            out.append(Point(float(item["x"]), float(item["y"])))
        else:
            x, y = item
            out.append(Point(float(x), float(y)))
    return tuple(out)


# ---------------------------------------------------------------------------
# The board and what is already on it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Board:
    """The substrate. ``outline`` is the real routed edge as a closed polygon
    (circuit.json emits a rounded rectangle as a few hundred points); ``width``
    and ``height`` are the bounding box the fab quotes on."""

    width_mm: float
    height_mm: float
    center: Point = Point(0.0, 0.0)
    outline: tuple[Point, ...] = ()
    layer_count: int = 2
    thickness_mm: float = 1.6

    @property
    def layers(self) -> tuple[str, ...]:
        return LAYERS_2[: self.layer_count] if self.layer_count <= 2 else LAYERS_2

    @property
    def area_mm2(self) -> float:
        if len(self.outline) >= 3:
            area = 0.0
            pts = self.outline
            for i in range(len(pts)):
                a, b = pts[i], pts[(i + 1) % len(pts)]
                area += a.x * b.y - b.x * a.y
            return abs(area) / 2.0
        return self.width_mm * self.height_mm

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            self.center.x - self.width_mm / 2.0,
            self.center.y - self.height_mm / 2.0,
            self.center.x + self.width_mm / 2.0,
            self.center.y + self.height_mm / 2.0,
        )


@dataclass(frozen=True)
class Drill:
    """A hole. ``plated`` decides which of JLC's three copper-to-hole numbers
    applies, and a slot is a real slot: ``width`` and ``height`` differ, and
    the copper-clearance check sweeps the segment between the two end centres.
    Modelling a 0.8 x 1.6mm USB-C alignment slot as a circle understates its
    long axis by 0.4mm, which is how copper 0.3mm off the end of one measured
    as 0.7mm clear."""

    id: str
    center: Point
    width_mm: float
    height_mm: float
    plated: bool
    net: str | None = None
    component: str = ""
    #: Set when this drill belongs to a pad, so a pad is never measured
    #: against its own hole.
    pad_id: str | None = None
    #: Counter-clockwise degrees. A 0.8 x 1.6mm slot turned 90 degrees is a
    #: different obstacle from the same slot upright, and the pipeline's own
    #: DFM gate does not read this field yet — see README, "known divergence".
    rotation_deg: float = 0.0
    #: ``pill`` covers a round hole and a slot, which is every hole
    #: circuit.json emits. A drill that says ``rect`` gets a rectangle.
    shape: str = "pill"


@dataclass(frozen=True)
class Pad:
    """A landing the router must reach. ``kind`` is ``"smd"`` or
    ``"plated_hole"``; a plated hole is reachable from both layers, an SMD pad
    only from the one it sits on.

    **``shape`` is load-bearing geometry, not a label.** Until 2026-08-16 this
    class kept only width, height and rotation, so a ``polygon`` pad's vertices
    and a ``circle`` pad's radius were thrown away before any router saw them
    and every rectangle was measured as its inscribed stadium — the corner of a
    1.0mm square pad protruding 0.21mm through a 0.09mm gate. ``vertices`` and
    ``corner_radius_mm`` exist so the real outline survives the trip from
    circuit.json into the benchmark.
    """

    id: str
    net: str | None
    center: Point
    width_mm: float
    height_mm: float
    layers: tuple[str, ...]
    kind: Literal["smd", "plated_hole"] = "smd"
    #: ``rect`` / ``rotated_rect`` / ``circle`` / ``pill`` / ``rotated_pill`` /
    #: ``polygon``, straight off ``pcb_smtpad``. An unknown shape is read as a
    #: rectangle, which is the conservative reading.
    shape: str = "rect"
    component: str = ""
    port_id: str | None = None
    drill_id: str | None = None
    #: Counter-clockwise degrees, from circuit.json's ``ccw_rotation``. A
    #: 2.25 x 0.63mm pill at 270 degrees on a 1.27mm pitch is eight separate
    #: pads; read as unrotated it is one bar shorting six nets.
    rotation_deg: float = 0.0
    #: The true outline of a ``polygon`` pad. Present, it wins over everything
    #: else; ``center``/``width``/``height`` stay as the bounding box so
    #: features and displays keep working.
    vertices: tuple[Point, ...] = ()
    #: Corner rounding, from ``pcb_smtpad.radius``. At ``min(w, h) / 2`` a
    #: rounded rectangle *is* a pill, and the shape model degenerates to the
    #: stadium on its own.
    corner_radius_mm: float = 0.0

    @property
    def is_smd(self) -> bool:
        return self.kind == "smd"

    def reachable_from(self, layer: str) -> bool:
        return layer in self.layers


@dataclass(frozen=True)
class Keepout:
    """Copper is forbidden here on the listed layers. Antenna clearances,
    connector shells, mounting-boss footprints.

    A keepout is where the inscribed-stadium model did the most damage: on the
    7.3 x 1.23mm rectangle of the USB-C block it cut 0.255mm off each corner,
    2.8 times the clearance gate, and turned a board that was ``fab.ready``
    into five blocking findings the harness could not see.
    """

    id: str
    center: Point
    width_mm: float
    height_mm: float
    layers: tuple[str, ...] = LAYERS_2
    shape: str = "rect"
    rotation_deg: float = 0.0
    vertices: tuple[Point, ...] = ()


@dataclass(frozen=True)
class Plane:
    """A poured copper region on one layer, already belonging to a net. A pad
    or via that lands inside it is connected to that net without a trace —
    which is the whole point of a ground plane and the thing the router we ship
    today cannot see (it counted our 73 ground vias as 73 obstacles and routed
    exactly the same copper)."""

    id: str
    net: str
    layer: str
    outline: tuple[Point, ...]
    holes: tuple[tuple[Point, ...], ...] = ()


@dataclass(frozen=True)
class Net:
    """One electrical net. ``pads`` are pad ids and they are sorted, so two
    runs iterate them in the same order.

    ``min_width_mm`` is the width this net must be routed at — resolved once,
    at problem-build time, from the net class and the rules, so a router never
    has to know the width policy and two routers cannot disagree about it."""

    id: str
    name: str
    net_class: NetClass
    pads: tuple[str, ...]
    min_width_mm: float
    source_net_id: str | None = None
    diff_partner: str | None = None
    #: Lower routes first when an algorithm wants a deterministic order.
    priority: int = 100

    @property
    def routable(self) -> bool:
        return len(self.pads) >= 2


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesignRules:
    """Every number the router is graded against, in one object.

    Built by :meth:`from_profile` from ``circuitpy.fab.get_profile`` — the
    single owner of every DFM number in this repo. Nothing here is transcribed
    by hand except the two router-only defaults (via drill and via pad), which
    are the sizes we actually order.

    The distinction that matters: ``min_clearance_mm`` is what JLC holds
    (0.10mm) and ``clearance_gate_mm`` is what we *block* at
    (0.10 - drc_tolerance = 0.09mm), because two geometry engines disagree by a
    few microns and a gate at the exact floor rejects legal boards. A router
    must aim at ``target_clearance_mm``, which is deliberately above both — the
    router we ship aims at the floor and therefore lands under it, and that one
    fact is why four attempts to steer it all failed."""

    profile_id: str = "jlcpcb"
    layer_count: int = 2

    min_trace_mm: float = 0.10
    warn_trace_mm: float = 0.15
    power_trace_mm: float = 0.5
    signal_trace_mm: float = 0.2

    min_clearance_mm: float = 0.10
    clearance_gate_mm: float = 0.09
    #: What a router should design to. Above the gate on purpose.
    target_clearance_mm: float = 0.15

    min_pth_to_copper_mm: float = 0.28
    min_npth_to_copper_mm: float = 0.20
    min_via_to_copper_mm: float = 0.20
    min_hole_to_hole_mm: float = 0.20

    via_drill_mm: float = 0.30
    via_pad_mm: float = 0.60
    min_via_drill_mm: float = 0.15
    min_via_diameter_mm: float = 0.30
    min_via_annular_mm: float = 0.075

    min_edge_clearance_mm: float = 0.20
    #: A via inside an SMD pad needs a fab process we do not buy.
    allow_via_in_pad: bool = False
    #: Coupled length a differential pair should hold, as a fraction.
    diff_pair_target_coupling: float = 0.8
    diff_pair_gap_mm: float = 0.2

    @classmethod
    def from_profile(
        cls,
        profile,
        *,
        layer_count: int = 2,
        via_drill_mm: float = 0.30,
        via_pad_mm: float = 0.60,
        target_clearance_mm: float | None = None,
    ) -> "DesignRules":
        """Read the rules off a ``circuitpy.fab.FabProfile`` (duck-typed, so a
        test can pass a stub). Never transcribe — import."""
        gate = float(profile.min_clearance_mm) - float(
            getattr(profile, "drc_tolerance_mm", 0.0)
        )
        return cls(
            profile_id=str(profile.id),
            layer_count=layer_count,
            min_trace_mm=float(profile.min_trace_mm),
            warn_trace_mm=float(profile.warn_trace_mm),
            power_trace_mm=float(profile.warn_power_trace_mm),
            min_clearance_mm=float(profile.min_clearance_mm),
            clearance_gate_mm=gate,
            target_clearance_mm=(
                float(profile.warn_clearance_mm) + 0.02
                if target_clearance_mm is None
                else float(target_clearance_mm)
            ),
            min_pth_to_copper_mm=float(profile.min_pth_to_copper_mm),
            min_npth_to_copper_mm=float(profile.min_npth_to_copper_mm),
            min_via_to_copper_mm=float(profile.min_via_to_copper_mm),
            via_drill_mm=float(via_drill_mm),
            via_pad_mm=float(via_pad_mm),
            min_via_drill_mm=float(profile.min_via_drill_mm),
            min_via_diameter_mm=float(profile.min_via_diameter_mm),
            min_via_annular_mm=float(profile.min_via_annular_mm),
            min_edge_clearance_mm=float(profile.min_edge_clearance_mm),
        )

    @classmethod
    def jlcpcb(cls, **kwargs) -> "DesignRules":
        """The shipping profile. Imports circuitpy lazily so the model module
        can be read without the pipeline on the path."""
        from circuitpy.fab import get_profile

        return cls.from_profile(get_profile("jlcpcb"), **kwargs)

    def width_for(self, net_class: NetClass) -> float:
        if net_class in ("power", "ground"):
            return self.power_trace_mm
        return self.signal_trace_mm

    def hole_clearance(self, drill: Drill) -> float:
        """JLC publishes three copper-to-hole numbers, not one."""
        if drill.pad_id is None and drill.plated:
            return self.min_via_to_copper_mm
        if drill.plated:
            return self.min_pth_to_copper_mm
        return self.min_npth_to_copper_mm

    def as_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


# ---------------------------------------------------------------------------
# The problem
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingProblem:
    """A placed board with holes in its copper.

    Built by :func:`routerlib.adapters.problem_from_circuit_json`, or loaded
    from a benchmark instance. Immutable: an algorithm that wants to mutate
    state builds its own working copy."""

    id: str
    board: Board
    rules: DesignRules
    pads: tuple[Pad, ...] = ()
    drills: tuple[Drill, ...] = ()
    keepouts: tuple[Keepout, ...] = ()
    planes: tuple[Plane, ...] = ()
    nets: tuple[Net, ...] = ()
    #: Copper already routed that the router must keep and must not violate.
    existing_traces: tuple["Trace", ...] = ()
    existing_vias: tuple["Via", ...] = ()
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pads", tuple(sorted(self.pads, key=lambda p: p.id)))
        object.__setattr__(
            self, "drills", tuple(sorted(self.drills, key=lambda d: d.id))
        )
        object.__setattr__(self, "nets", tuple(sorted(self.nets, key=_net_sort_key)))

    # -- lookups ---------------------------------------------------------
    @property
    def pads_by_id(self) -> dict[str, Pad]:
        return {p.id: p for p in self.pads}

    @property
    def nets_by_id(self) -> dict[str, Net]:
        return {n.id: n for n in self.nets}

    @property
    def routable_nets(self) -> tuple[Net, ...]:
        """Nets with at least two pads. A one-pad net is already connected and
        counting it inflates completeness for free."""
        return tuple(n for n in self.nets if n.routable)

    def pads_of(self, net_id: str) -> tuple[Pad, ...]:
        index = self.pads_by_id
        return tuple(index[p] for p in self.nets_by_id[net_id].pads if p in index)

    def net_of_pad(self, pad_id: str) -> str | None:
        pad = self.pads_by_id.get(pad_id)
        return pad.net if pad else None


def _net_sort_key(net: Net) -> tuple:
    return (net.priority, net.name, net.id)


# ---------------------------------------------------------------------------
# The solution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Trace:
    """One run of copper on one layer. A layer change is a :class:`Via`, so a
    trace never spans layers — that keeps the geometry checks a straight
    polyline problem and makes a via count meaningful."""

    id: str
    net: str
    layer: str
    points: tuple[Point, ...]
    width_mm: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", _points(self.points))
        object.__setattr__(self, "width_mm", q(self.width_mm))

    @property
    def segments(self) -> tuple[tuple[Point, Point], ...]:
        return tuple(zip(self.points, self.points[1:]))

    @property
    def length_mm(self) -> float:
        return sum(a.distance_to(b) for a, b in self.segments)


@dataclass(frozen=True)
class Via:
    """A plated hole the router places to change layers. 0.3mm drill / 0.6mm
    pad by default, and never inside an SMD pad."""

    id: str
    net: str
    center: Point
    drill_mm: float = 0.30
    pad_mm: float = 0.60
    from_layer: str = TOP
    to_layer: str = BOTTOM

    def __post_init__(self) -> None:
        object.__setattr__(self, "drill_mm", q(self.drill_mm))
        object.__setattr__(self, "pad_mm", q(self.pad_mm))


@dataclass(frozen=True)
class RoutingSolution:
    """What a router returns.

    ``complete`` is the router's *own* claim. The scorer recomputes it from the
    copper and reports both; a router that claims completion it did not achieve
    is scored on the recomputed number and the disagreement is printed. Saying
    "I did not finish" costs nothing and is always the better answer than
    emitting copper you cannot defend."""

    router: str
    traces: tuple[Trace, ...] = ()
    vias: tuple[Via, ...] = ()
    complete: bool = False
    unrouted_nets: tuple[str, ...] = ()
    iterations: int = 0
    nodes_expanded: int = 0
    #: Information only. Never a gate, never in the score key.
    wall_clock_s: float = 0.0
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "traces", tuple(self.traces))
        object.__setattr__(self, "vias", tuple(self.vias))
        object.__setattr__(self, "unrouted_nets", tuple(sorted(self.unrouted_nets)))

    @property
    def copper_length_mm(self) -> float:
        return sum(t.length_mm for t in self.traces)

    def canonical(self) -> "RoutingSolution":
        """The same copper with a deterministic order and no timing. This is
        what the determinism check hashes: a router that finds the same copper
        in a different internal order is still deterministic, but a router that
        finds *different* copper is not."""
        return replace(
            self,
            traces=tuple(
                sorted(
                    self.traces,
                    key=lambda t: (t.net, t.layer, t.width_mm, tuple(
                        (p.x, p.y) for p in t.points)),
                )
            ),
            vias=tuple(
                sorted(
                    self.vias,
                    key=lambda v: (v.net, v.center.x, v.center.y, v.drill_mm),
                )
            ),
            wall_clock_s=0.0,
        )

    def fingerprint(self) -> str:
        """sha256 over the canonical copper, ids and timings removed."""
        import hashlib
        import json

        canon = self.canonical()
        payload = {
            "traces": [
                {
                    "net": t.net,
                    "layer": t.layer,
                    "w": t.width_mm,
                    "pts": [[p.x, p.y] for p in t.points],
                }
                for t in canon.traces
            ],
            "vias": [
                {
                    "net": v.net,
                    "x": v.center.x,
                    "y": v.center.y,
                    "d": v.drill_mm,
                    "p": v.pad_mm,
                }
                for v in canon.vias
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


class BudgetExhausted(Exception):
    """Raised by :meth:`BudgetMeter.tick` when a router opts into hard stops."""


@dataclass(frozen=True)
class Budget:
    """How much search a router may spend — **counted, never timed**.

    Wall-clock is precisely why the router we ship today is nondeterministic:
    the same board on a loaded machine gets a different amount of search and
    therefore a different board. A benchmark that inherits that measures the
    machine, not the algorithm. So the currency is:

    * ``max_iterations`` — one unit of algorithm-defined outer work (a net
      attempted, a rip-up round, a pattern tried). **Algorithm-defined is the
      operative word and it is a real limitation**: at the same cap of two
      million, ``pathfinder-negotiated`` spent 20 rounds, ``plane-and-classes``
      283 nets and ``exact-and-structured`` 38,427 candidates. The number
      bounds one family's search honestly and compares nothing across two, so
      :meth:`routerlib.scoring.ScoreResult.key` does not rank on it.
    * ``max_nodes`` — expanded search nodes, for anything that searches a grid
      or a graph. The finer of the two currencies and the one that actually
      bounds an A*.
    * ``seed`` — every random choice must derive from this. A router that reads
      the system RNG is not deterministic and will be caught by the check.

    ``wall_clock_cap_s`` exists only as a **safety valve against a hung run**.
    It is deliberately generous and any solution that hits it is reported as
    ``budget_stop="wall_clock"``, which the harness treats as a failed run
    rather than a result — a number produced by a clock is not comparable."""

    max_iterations: int = 200_000
    max_nodes: int = 5_000_000
    max_rip_up_passes: int = 0
    seed: int = 0
    #: Safety valve only. Hitting it invalidates the run.
    wall_clock_cap_s: float = 900.0

    def meter(self) -> "BudgetMeter":
        return BudgetMeter(self)


class BudgetMeter:
    """The accountant. A router calls :meth:`tick` / :meth:`expand` and checks
    :attr:`exhausted`; the harness reads :attr:`iterations` and :attr:`nodes`
    out of the returned solution to score cost."""

    __slots__ = ("budget", "iterations", "nodes", "_started", "stop_reason")

    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.iterations = 0
        self.nodes = 0
        self.stop_reason: str | None = None
        import time

        self._started = time.perf_counter()

    def tick(self, n: int = 1) -> bool:
        self.iterations += n
        return not self.exhausted

    def expand(self, n: int = 1) -> bool:
        self.nodes += n
        return not self.exhausted

    @property
    def elapsed_s(self) -> float:
        import time

        return time.perf_counter() - self._started

    @property
    def exhausted(self) -> bool:
        if self.iterations >= self.budget.max_iterations:
            self.stop_reason = self.stop_reason or "iterations"
            return True
        if self.nodes >= self.budget.max_nodes:
            self.stop_reason = self.stop_reason or "nodes"
            return True
        if self.elapsed_s >= self.budget.wall_clock_cap_s:
            self.stop_reason = "wall_clock"
            return True
        return False


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------


@runtime_checkable
class Router(Protocol):
    """Every algorithm in the tournament implements exactly this.

    ``name`` must be stable — it is the key the leaderboard and the instance
    manifests are written under."""

    name: str

    def route(
        self, problem: RoutingProblem, budget: Budget
    ) -> RoutingSolution:  # pragma: no cover - protocol
        ...


def empty_solution(router: str = "empty") -> RoutingSolution:
    """The zero-copper solution. Its score is the floor of the floor, and its
    legality is the instance's *baseline*: any DRC violation it reports was
    already in the placement and cannot be blamed on a router."""
    return RoutingSolution(router=router, complete=False)


__all__ = [
    "BOTTOM",
    "TOP",
    "Board",
    "Budget",
    "BudgetExhausted",
    "BudgetMeter",
    "DesignRules",
    "Drill",
    "Keepout",
    "Net",
    "NetClass",
    "Pad",
    "Plane",
    "Point",
    "QUANTUM_MM",
    "Router",
    "RoutingProblem",
    "RoutingSolution",
    "Trace",
    "Via",
    "empty_solution",
    "q",
]


def segments_of(traces: Sequence[Trace]):
    """(layer, x0, y0, x1, y1, width, net) for every segment in a run."""
    for trace in traces:
        for a, b in trace.segments:
            yield (trace.layer, a.x, a.y, b.x, b.y, trace.width_mm, trace.net)
