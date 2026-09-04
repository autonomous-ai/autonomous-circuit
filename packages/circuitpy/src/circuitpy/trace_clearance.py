"""Push routed copper off other-net copper it only just clears.

**The defect this closes.** Every board this pipeline has shipped carries a few
hundred copper gaps that are legal and have nothing to spare. Measured
2026-09-04 by re-running the margin DRC pass over the five boards in the
2026-09-03 fab packet — the same pass `clearance_margin_warnings` reads, floor
raised to the profile's ``warn_clearance_mm``:

    board                  gaps in [0.10, 0.127)
    rc-car-wifi-4servo       10
    rc-servo-driver-4ch     264
    weather-badge-27        297
    macropad-12-oled        312
    weather-badge-32        327

An outside hardware reviewer read one of these on 2026-08-27 and named it the
reason the board is fault-prone — *"chạy thì hên xui"*, it may work by luck.

**It is not congestion, and the ledger's name for it was wrong.** The backlog
called this a missing "density-vs-area grade", on the reading that copper is
crammed into one corner of a mostly-empty board. Three measurements say
otherwise, all from the same 2026-09-04 sweep:

* Density is *anti*-correlated. rc-car is the most locally concentrated board
  of the five — its densest tenth of cells holds 62.8% of its copper — and has
  10 gaps. macropad-12-oled is the least concentrated (2.2% hot-cell area) and
  has 312.
* The pour is not involved at all. Classifying every tight violation by the
  pair of items KiCad names, across all five boards: `Track|Via`, `Pad|Track`,
  `Track|Track` and `Pad|Via` account for **100%** of them. Zero involve a
  `Zone`.
* The gap is quantized. **0.1150mm exactly** is 20-43% of each board's
  violations (70% of rc-car's), with a second mode at exactly 0.1000mm.
  Congestion does not produce one repeated value; a constant does.

The constant belongs to the shipped `@tscircuit/capacity-autorouter`
(``obstacleMargin`` defaults 0.1/0.15 against a 0.05mm cell step in its
bundle). We cannot reach it: raising `minTraceWidth`/clearance props as a
routing lever was measured at 7 errors to 125 and is recorded in
`generation.py` so nobody retries it, and `packages/router` — which aims at
``warn_clearance_mm + 0.02`` and would not produce these — is off by default
until it matches the shipped router on completeness.

**So this is a repair pass, for the same reason `pour_clearance` is one:** the
number is upstream, the board is ours, and "marginal" is the class of defect
that survives one fab's process and fails another's.

**What it does.** For every routed segment that comes closer than the profile's
design margin to other-net copper, it pushes the segment's own route points
away along the perpendicular — far enough to hold the margin, and no further.
The pushes are tiny by construction: the gap it repairs is at worst
``warn_clearance_mm - min_clearance_mm`` = 0.027mm wide, and the median case
measured is 0.012mm. There is room for them. Sampling 40 tight violations per
board and sweeping 24 directions for clear space, **75-82% have at least
0.5mm of empty board beside them**, median past the 1.5mm search ceiling.

**What it will not do.**

* It never moves a route point anchored to a `pcb_port`. That point is where
  copper meets a pad; moving it is how a repair pass silently disconnects a
  net.
* It never moves a pad, a via or a plated hole — including a `route_type:
  "via"` point, which *is* the via. Those are placement, and placement belongs
  to the board's author.
* It never widens, never changes a layer, and never adds or removes a route
  point. **It therefore cannot fix a segment whose every endpoint is pinned**:
  a pad-to-pad hop with no interior vertex has nothing to push. Those are
  counted and reported as `trace_clearance_unrelieved` rather than passed over
  in silence. Splitting such an edge to give it a vertex is the obvious next
  move and is deliberately not in this pass: `pour_clearance._split_edges_near`
  already carries a known parallel-edge limitation, and a repair that adds
  geometry deserves its own measurement.
* It refuses rather than regresses, and the guarantee is enforced twice.
  Per move: a point whose push leaves its trace's narrowest gap worse than it
  found it goes straight back. Then, at the end, against freshly measured
  copper: the ends of any segment now under the fab floor plus the tolerance
  two geometry engines disagree by are put back, and any trace still nearer
  than it started is put back whole.

  The second pass is not belt-and-braces. A sweep judges a move against copper
  as it stood when the round began, so two traces can each step toward the
  other inside one round and neither sees it; measured on weather-badge-27,
  2026-09-04, that produced a 0.0959mm gap on a board whose narrowest was
  0.1000mm — the pass creating the exact defect it exists to remove.

  The two conditions are separate on purpose. Written as one
  ``now >= min(started, floor)`` the floor drops out for every trace that began
  at or below it, which on these boards is most of them, and wb-27 kept its
  sub-floor gap through a guard that claimed to forbid it.
* A board with nothing wrong is returned byte-identical, which is what keeps
  the determinism hash meaningful. The pass is deterministic — the same input
  gives byte-identical output, verified on three boards — but it is **not**
  idempotent: the sweep is bounded rather than run to convergence, so a second
  invocation over its own output moves more copper. Nothing in the pipeline
  does that (each compile attempt hands it a freshly built circuit.json), and
  running it to convergence is not better: see `_MAX_ROUNDS`.

**What it is worth.** A/B through the real gauntlet — export,
`normalize_for_fab`, `kicad-cli` DRC at the design margin — gaps in
[0.10, 0.127):

    board                  before   after
    rc-car-wifi-4servo         10       3
    rc-servo-driver-4ch       264     182
    weather-badge-27          297     127
    macropad-12-oled          312     182
    weather-badge-32          327     160

Nothing under the fab floor on any of them, no board's narrowest gap worse than
it started, and every non-clearance violation count unchanged — the pass moves
copper and breaks nothing else.

The remainder is real and is reported. Roughly a third to a half of the gaps
stay, because the guarantee above is strict: a trace that cannot reach the
margin without going under the floor is put back rather than improved a little.
`clearance_no_margin` still grades what is left, which is the point — this pass
narrows the problem honestly instead of silencing the check.

**Why it runs before the pour pass.** The pour is the only stage that must see
final copper. This stage moves copper, so it has to be finished before the pour
is pushed off it.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import diffpair

#: How much past the required margin a push aims for. The repair is measured in
#: microns and floating-point comparison at exactly the threshold is how a pass
#: reports success on a gap the next engine still reads as short.
_OVERSHOOT_MM = 0.002

#: A push is a nudge or it is a placement decision. Nothing legitimate needs
#: more than this — the widest gap the pass can be asked to close is
#: ``warn_clearance_mm - min_clearance_mm``, an order of magnitude under it —
#: and a cap keeps a degenerate geometry from throwing copper across the board.
_MAX_PUSH_MM = 0.25

#: Rounds of sweep. A point pushed off one obstacle can land nearer another, so
#: the sweep repeats — but it does **not** converge, and the pass is therefore
#: not idempotent: run it twice and it moves more copper the second time.
#:
#: More rounds is not reliably better, which is why this is 3 and not higher.
#: Measured 2026-09-04, gaps left inside the margin:
#:
#:     rounds        wb-32   macropad   servo-4ch   wb-27   worst time
#:          3          124        143         153      88        2.0s
#:          6          117        143         153      71        2.8s
#:         10          112        135         149      65        3.9s
#:         16          119        139         143      61        5.6s
#:
#: wb-32 gets worse from 10 to 16 and macropad from 10 to 16 as well: pushes
#: interact, and a later round can undo an earlier one's good placement. Paying
#: 2.8x the time for a result that moves in both directions is not a trade
#: worth making inside a chat turn. What is left is reported, not hidden.
_MAX_ROUNDS = 3

#: Rounds of the undo loop, which converges for a different reason than the
#: sweep does — putting copper back only ever widens its neighbours' gaps, so
#: the loop is monotone toward the board it started from. Its own constant, so
#: that tuning the sweep does not silently retune the guarantee.
_MAX_REVERT_ROUNDS = 4

#: Bucket size for the obstacle index, in mm. Large enough that a segment spans
#: few cells, small enough that a cell holds few obstacles.
_CELL_MM = 2.0


@dataclass(frozen=True)
class TraceFix:
    """One trace, and what had to move."""

    trace_id: str
    layer: str
    points_moved: int
    worst_before_mm: float
    worst_after_mm: float
    required_mm: float
    offender: str | None = None


@dataclass(frozen=True)
class TraceClearanceResult:
    ran: bool
    fixes: tuple[TraceFix, ...] = ()
    required_mm: float = 0.0
    elapsed_s: float = 0.0
    #: Gaps still short after the pass — copper with nowhere to go. Reported
    #: rather than hidden: a repair that fixes most of a defect and says
    #: nothing about the rest is how a board ships believing it is clean.
    unresolved: int = 0
    worst_unresolved_mm: float = 0.0
    note: str | None = None

    @property
    def changed(self) -> bool:
        return any(f.points_moved for f in self.fixes)

    def as_dict(self) -> dict:
        return {
            "ran": self.ran,
            "changed": self.changed,
            "requiredMm": self.required_mm,
            "elapsed_s": round(self.elapsed_s, 3),
            "unresolved": self.unresolved,
            "worstUnresolvedMm": round(self.worst_unresolved_mm, 4),
            "note": self.note,
            "traces": [
                {
                    "id": f.trace_id,
                    "layer": f.layer,
                    "pointsMoved": f.points_moved,
                    "worstBeforeMm": round(f.worst_before_mm, 4),
                    "worstAfterMm": round(f.worst_after_mm, 4),
                    "offender": f.offender,
                }
                for f in self.fixes if f.points_moved
            ],
        }

    def findings(self) -> list[dict]:
        if not self.ran:
            return []
        out: list[dict] = []
        if self.changed:
            moved = sum(f.points_moved for f in self.fixes)
            worst = min(f.worst_before_mm for f in self.fixes if f.points_moved)
            after = min(f.worst_after_mm for f in self.fixes if f.points_moved)
            culprit = next(
                (f.offender for f in self.fixes if f.points_moved and f.offender),
                None,
            )
            out.append({
                "part": "board",
                "kind": "trace_clearance_relieved",
                "severity": "info",
                "detail": (
                    f"routed copper came {worst:.4f}mm from other-net copper "
                    f"against a {self.required_mm:g}mm design margin"
                    + (f" (nearest: {culprit})" if culprit else "")
                    + f"; {moved} route point(s) pushed back across "
                    f"{sum(1 for f in self.fixes if f.points_moved)} trace(s), "
                    f"worst gap now {after:.4f}mm. The shipped autorouter aims "
                    f"at its own obstacle margin and lands a repeated "
                    f"0.1150mm, which is inside our margin and outside its "
                    f"reach — structural rather than this board's"
                ),
            })
        if self.unresolved:
            out.append({
                "part": "board",
                "kind": "trace_clearance_unrelieved",
                "severity": "info",
                "detail": (
                    f"{self.unresolved} copper gap(s) stayed inside the "
                    f"{self.required_mm:g}mm design margin, narrowest "
                    f"{self.worst_unresolved_mm:.4f}mm: the copper there has "
                    f"nowhere to go without moving a pad, a via or a port "
                    f"anchor, and this pass moves none of those. They remain "
                    f"legal and remain graded by clearance_no_margin"
                ),
            })
        return out


# ---------------------------------------------------------------------------
# Obstacles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Round:
    """A via, a round pad or a plated hole: exact as a disc."""

    label: str
    net: str | None
    cx: float
    cy: float
    radius: float
    #: For a via, the trace that placed it. A via is a trace's own layer
    #: change: the copper runs into it on purpose, at zero gap. Reading it as
    #: foreign is how the pour pass spent three boards pushing the ground plane
    #: off its own ground (2026-09-02), and it is the same shape here.
    owner: str | None = None

    def distance(self, seg: tuple[float, float, float, float]) -> float:
        d = diffpair._seg_point_distance(seg[0], seg[1], seg[2], seg[3],
                                         self.cx, self.cy)
        return d - self.radius

    def bbox(self) -> tuple[float, float, float, float]:
        return (self.cx - self.radius, self.cy - self.radius,
                self.cx + self.radius, self.cy + self.radius)


@dataclass(frozen=True)
class _Poly:
    """A rectangular pad, as its four corners.

    Deliberately *not* the circumscribed disc `pour_clearance` uses. That disc
    is the safe direction for a pour, which only ever loses area by it; here it
    would invent a shortfall KiCad never measured and push copper for nothing —
    the same phantom the ledger recorded for pill holes sized by
    ``hypot(w, h) / 2``.
    """

    label: str
    net: str | None
    points: tuple[tuple[float, float], ...]

    def distance(self, seg: tuple[float, float, float, float]) -> float:
        pts = self.points
        best = math.inf
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            best = min(best, diffpair._seg_seg_distance(seg, (x0, y0, x1, y1)))
        if best > 0 and (
            diffpair._point_in_poly(pts, seg[0], seg[1])
            or diffpair._point_in_poly(pts, seg[2], seg[3])
        ):
            return 0.0
        return best

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class _Capsule:
    """Another trace's segment, or a pill pad: a line with a half-width."""

    label: str
    net: str | None
    seg: tuple[float, float, float, float]
    half_width: float
    #: The trace this copper belongs to, so a trace is never measured against
    #: itself. Net alone is not enough: a point-to-point trace carries no
    #: source_net and reads as netless (see `_Board.trace_net_key`).
    owner: str | None = None

    def distance(self, seg: tuple[float, float, float, float]) -> float:
        return diffpair._seg_seg_distance(seg, self.seg) - self.half_width

    def bbox(self) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = self.seg
        h = self.half_width
        return (min(x0, x1) - h, min(y0, y1) - h,
                max(x0, x1) + h, max(y0, y1) + h)


_Obstacle = Any  # _Round | _Poly | _Capsule


def _pad_obstacles(board: diffpair._Board, layer: str,
                   net_of_trace: dict[str, str | None]) -> list[_Obstacle]:
    """Every pad, plated hole and via that occupies copper on ``layer``."""
    out: list[_Obstacle] = []

    for pad in board.by_type.get("pcb_smtpad", []):
        if str(pad.get("layer") or "top") != layer:
            continue
        net = board.net_key_of_pcb_port(str(pad.get("pcb_port_id") or ""))
        label = str(pad.get("pcb_smtpad_id") or "pad")
        # Measured across the five packet boards, 2026-09-04: rect 762,
        # rotated_pill 32, polygon 17, circle 36, pill 16. A `rotated_pill`
        # read as a rectangle is not a rounding error — its corners are copper
        # that is not there, and on weather-badge-32 that phantom reported a
        # 0.0192mm gap against `pcb_smtpad_94` where KiCad measures nothing
        # under 0.1000mm. A pass that invents a shortfall moves copper for
        # nothing, which is the one thing a repair pass must not do.
        shape = str(pad.get("shape") or "rect")
        # A polygon pad carries its outline and **no centre at all** — no `x`,
        # no `y`. Demanding one before reading the shape silently dropped all
        # four of weather-badge-27's, J1 pin 13 among them, and the pass then
        # pushed a V5 track into a GND pad it could not see: 0.0932mm, under
        # the fab floor, on a board whose narrowest gap was 0.1000mm before.
        # Read the shape first; ask for the fields that shape actually has.
        if shape == "polygon":
            pts = [
                (diffpair._f(p.get("x")), diffpair._f(p.get("y")))
                for p in (pad.get("points") or [])
                if isinstance(p, dict)
            ]
            pts = [(px, py) for px, py in pts if px is not None and py is not None]
            if len(pts) >= 3:
                out.append(_Poly(label, net, tuple(pts)))
            continue
        x, y = diffpair._f(pad.get("x")), diffpair._f(pad.get("y"))
        if x is None or y is None:
            continue
        if shape == "circle":
            r = diffpair._f(pad.get("radius"), 0.0) or 0.0
            if r > 0:
                out.append(_Round(label, net, x, y, r))
            continue
        w = diffpair._f(pad.get("width"), 0.0) or 0.0
        h = diffpair._f(pad.get("height"), 0.0) or 0.0
        if w <= 0 or h <= 0:
            continue
        rot = diffpair._f(pad.get("ccw_rotation"), 0.0) or 0.0
        if shape in ("pill", "rotated_pill"):
            # A stadium is exactly a capsule: the segment between the two end
            # circle centres, with the short half-dimension as its radius.
            if w >= h:
                half, run = h / 2, (w - h) / 2
                dx, dy = run, 0.0
            else:
                half, run = w / 2, (h - w) / 2
                dx, dy = 0.0, run
            ax, ay = _rotate(-dx, -dy, rot)
            bx, by = _rotate(dx, dy, rot)
            out.append(_Capsule(label, net, (x + ax, y + ay, x + bx, y + by), half))
            continue
        out.append(_Poly(label, net, _rect_points(x, y, w, h, rot)))

    for hole in board.by_type.get("pcb_plated_hole", []):
        x, y = diffpair._f(hole.get("x")), diffpair._f(hole.get("y"))
        if x is None or y is None:
            continue
        net = board.net_key_of_pcb_port(str(hole.get("pcb_port_id") or ""))
        label = str(hole.get("pcb_plated_hole_id") or "pth")
        w = diffpair._f(hole.get("outer_width"))
        h = diffpair._f(hole.get("outer_height"))
        d = diffpair._f(hole.get("outer_diameter"))
        if w and h and abs(w - h) > 1e-9:
            # An oval pad is a stadium, not a disc. Sizing it by
            # ``hypot(w, h) / 2`` is the recorded +0.18mm phantom.
            if w >= h:
                half, run = h / 2, (w - h) / 2
                seg = (x - run, y, x + run, y)
            else:
                half, run = w / 2, (h - w) / 2
                seg = (x, y - run, x, y + run)
            out.append(_Capsule(label, net, seg, half))
        else:
            r = ((w or h or d) or 0.0) / 2
            if r > 0:
                out.append(_Round(label, net, x, y, r))

    for via in board.by_type.get("pcb_via", []):
        x, y = diffpair._f(via.get("x")), diffpair._f(via.get("y"))
        if x is None or y is None:
            continue
        layers = via.get("layers")
        if isinstance(layers, list) and layers and layer not in [str(v) for v in layers]:
            continue
        r = (diffpair._f(via.get("outer_diameter"), 0.0) or 0.0) / 2
        if r <= 0:
            continue
        # Measured on weather-badge-32, 2026-09-04: all 112 vias carry a
        # `subcircuit_connectivity_map_key` and none carry a `pcb_port_id`,
        # while every trace on that board resolves its net through ports
        # instead — so the two keys never compare equal and a key-only lookup
        # reads every via as foreign copper. `pcb_trace_id` is the field that
        # actually joins them, and it is populated.
        owner = via.get("pcb_trace_id")
        owner = str(owner) if owner else None
        net = net_of_trace.get(owner) if owner else None
        if net is None:
            key = via.get("subcircuit_connectivity_map_key")
            net = key if isinstance(key, str) else None
        out.append(
            _Round(str(via.get("pcb_via_id") or "via"), net, x, y, r, owner))

    return out


def _rotate(dx: float, dy: float, degrees: float) -> tuple[float, float]:
    if not degrees:
        return dx, dy
    rad = math.radians(degrees)
    c, s = math.cos(rad), math.sin(rad)
    return dx * c - dy * s, dx * s + dy * c


def _rect_points(x: float, y: float, w: float, h: float,
                 degrees: float) -> tuple[tuple[float, float], ...]:
    hw, hh = w / 2, h / 2
    corners = ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
    return tuple((x + rx, y + ry) for rx, ry in
                 (_rotate(dx, dy, degrees) for dx, dy in corners))


def _trace_capsules(board: diffpair._Board, layer: str) -> list[_Capsule]:
    """Every routed segment on ``layer``, tagged with its trace and its net."""
    out: list[_Capsule] = []
    for trace in board.by_type.get("pcb_trace", []):
        tid = str(trace.get("pcb_trace_id") or "")
        net = board.trace_net_key(trace)
        route = trace.get("route") or []
        for i in range(len(route) - 1):
            a, b = route[i], route[i + 1]
            seg_layer, half = _seg_layer_half(a, b)
            if seg_layer != layer:
                continue
            ax, ay = diffpair._f(a.get("x")), diffpair._f(a.get("y"))
            bx, by = diffpair._f(b.get("x")), diffpair._f(b.get("y"))
            if None in (ax, ay, bx, by):
                continue
            out.append(_Capsule(f"{tid}[{i}]", net, (ax, ay, bx, by), half, tid))
    return out


class _Index:
    """A uniform grid over the obstacles, so a segment is measured against the
    copper near it rather than against the whole board. Boards here carry ~900
    obstacles and ~600 segments; the full product is 540k exact distance
    computations per round, and this turns it into a few thousand."""

    def __init__(self, obstacles: Sequence[_Obstacle]) -> None:
        self.obstacles = list(obstacles)
        self.cells: dict[tuple[int, int], list[int]] = {}
        for i, ob in enumerate(self.obstacles):
            x0, y0, x1, y1 = ob.bbox()
            for cx in range(int(x0 // _CELL_MM), int(x1 // _CELL_MM) + 1):
                for cy in range(int(y0 // _CELL_MM), int(y1 // _CELL_MM) + 1):
                    self.cells.setdefault((cx, cy), []).append(i)

    def near(self, seg: tuple[float, float, float, float],
             pad: float) -> Iterable[_Obstacle]:
        x0 = min(seg[0], seg[2]) - pad
        x1 = max(seg[0], seg[2]) + pad
        y0 = min(seg[1], seg[3]) - pad
        y1 = max(seg[1], seg[3]) + pad
        seen: set[int] = set()
        for cx in range(int(x0 // _CELL_MM), int(x1 // _CELL_MM) + 1):
            for cy in range(int(y0 // _CELL_MM), int(y1 // _CELL_MM) + 1):
                for i in self.cells.get((cx, cy), ()):
                    if i not in seen:
                        seen.add(i)
                        yield self.obstacles[i]


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def _seg_layer_half(a: dict, b: dict) -> tuple[str, float]:
    """The layer a segment sits on, and its half-width.

    A `route_type: "via"` point carries neither — measured on the five packet
    boards, 120 of 1362 segments start at one. Reading the layer off the start
    point alone dropped the whole segment leaving every via, so those never got
    checked and never acted as obstacles. The layer is the *other* end's: the
    via is the transition, the copper after it is on the layer it lands on.

    The width is the **wider** end, not the start. A route tapers: measured on
    weather-badge-27, 75 of its segments have two different widths, the widest
    disagreement 0.3mm. Taking the start's width understates that segment's
    copper by up to 0.15mm, and an understated obstacle is a gap this pass
    believes it has already opened. That is not a rounding error — it is how
    the first version of this pass left a 0.0959mm gap on wb-27 while its own
    guard reported the board clear.
    """
    layer = a.get("layer") or b.get("layer")
    width = max(
        diffpair._f(a.get("width"), 0.0) or 0.0,
        diffpair._f(b.get("width"), 0.0) or 0.0,
    )
    return (str(layer) if layer else ""), width / 2


def _movable(point: dict) -> bool:
    """A route point this pass is allowed to move.

    Not a port anchor — that point is where copper meets a pad, and moving it
    is how a repair pass silently disconnects a net. Not a via either: a via
    route point *is* the via, and moving it is placement.
    """
    return not (
        point.get("start_pcb_port_id")
        or point.get("end_pcb_port_id")
        or str(point.get("route_type") or "") == "via"
    )


def _foreign(ob: _Obstacle, net: str | None, trace_id: str) -> bool:
    """Copper this segment has to stay away from.

    Same-net copper is excluded — touching it is what a net is. An obstacle
    with no net at all is treated as foreign: an unnamed net reads as netless,
    and reading netless copper as *ours* is the shape that hides shorts.
    """
    if getattr(ob, "owner", None) == trace_id:
        return False
    if net is not None and ob.net is not None and ob.net == net:
        return False
    return True


def _worst_gap(seg: tuple[float, float, float, float], half: float,
               index: _Index, net: str | None, trace_id: str,
               limit: float) -> tuple[float, _Obstacle | None]:
    """Narrowest edge-to-edge gap between this segment's copper and foreign
    copper near it."""
    worst = math.inf
    culprit: _Obstacle | None = None
    for ob in index.near(seg, limit + half):
        if not _foreign(ob, net, trace_id):
            continue
        gap = ob.distance(seg) - half
        if gap < worst:
            worst, culprit = gap, ob
    return worst, culprit


def relieve_trace_clearance(
    circuit_json_path: Path, profile: Any, *, required_mm: float | None = None,
) -> TraceClearanceResult:
    """Push routed copper off foreign copper it only just clears. Never raises.

    ``required_mm`` defaults to the profile's ``warn_clearance_mm`` — the design
    margin `clearance_margin_warnings` grades against, so the pass and the check
    are answering the same question. Never transcribed; imported.
    """
    started = time.monotonic()
    path = Path(circuit_json_path)
    try:
        elements = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TraceClearanceResult(ran=False, note="circuit.json unreadable")
    if not isinstance(elements, list):
        return TraceClearanceResult(
            ran=False, note="circuit.json is not an element array")

    try:
        board = diffpair._Board(elements)
        traces = [
            e for e in elements
            if isinstance(e, dict) and e.get("type") == "pcb_trace"
        ]
        if not traces:
            return TraceClearanceResult(ran=False, note="no routed copper")

        required = float(
            required_mm if required_mm is not None
            else (getattr(profile, "warn_clearance_mm", 0.127) or 0.127)
        )

        layers = sorted({
            str(p.get("layer"))
            for t in traces for p in (t.get("route") or [])
            if p.get("layer")
        })
        # Resolved once: `trace_net_key` walks ports for every call, and the
        # via lookup below needs it per via, per layer, per round.
        net_of_trace: dict[str, str | None] = {
            str(t.get("pcb_trace_id") or ""): board.trace_net_key(t)
            for t in traces
        }

        #: Every point this pass moved, and where it started. A sweep judges a
        #: move against a one-round-stale index, so two traces can each step
        #: toward the other inside one round and neither sees it. Measured on
        #: weather-badge-27, 2026-09-04: that produced one gap at 0.0959mm on a
        #: board whose narrowest was 0.1000mm before — the pass creating the
        #: exact defect it exists to remove. Keeping the origin makes the
        #: guarantee recoverable instead of hopeful.
        origin: dict[int, tuple[Any, float, float]] = {}
        #: Each trace's narrowest gap before anything moved, per layer.
        origin_worst: dict[tuple[str, str], float] = {}

        moved_by_trace: dict[str, int] = {}
        before_by_trace: dict[str, float] = {}
        after_by_trace: dict[str, float] = {}
        offender_by_trace: dict[str, str] = {}
        layer_by_trace: dict[str, str] = {}

        for layer in layers:
            for _round in range(_MAX_ROUNDS):
                index = _Index(
                    _pad_obstacles(board, layer, net_of_trace)
                    + _trace_capsules(board, layer)
                )
                if _round == 0:
                    for trace in traces:
                        tid = str(trace.get("pcb_trace_id") or "")
                        origin_worst[(tid, layer)] = _trace_worst(
                            board, trace, layer, index, required)
                touched = 0
                for trace in traces:
                    tid = str(trace.get("pcb_trace_id") or "")
                    net = board.trace_net_key(trace)
                    route = trace.get("route") or []
                    for i in range(len(route) - 1):
                        a, b = route[i], route[i + 1]
                        seg_layer, half = _seg_layer_half(a, b)
                        if seg_layer != layer:
                            continue
                        ax, ay = diffpair._f(a.get("x")), diffpair._f(a.get("y"))
                        bx, by = diffpair._f(b.get("x")), diffpair._f(b.get("y"))
                        if None in (ax, ay, bx, by):
                            continue
                        seg = (ax, ay, bx, by)
                        gap, culprit = _worst_gap(
                            seg, half, index, net, tid, required)
                        if gap >= required or culprit is None:
                            continue
                        # Push perpendicular to the segment, away from the
                        # obstacle. Both ends move together, so the segment
                        # translates rather than pivoting into its neighbours.
                        push = min(required - gap + _OVERSHOOT_MM, _MAX_PUSH_MM)
                        nx, ny = _away(seg, culprit)
                        if nx == 0.0 and ny == 0.0:
                            continue
                        for point, px, py in ((a, ax, ay), (b, bx, by)):
                            if not _movable(point):
                                continue
                            keep = (point.get("x"), point.get("y"))
                            origin.setdefault(id(point), (point, px, py))
                            before = _trace_worst(
                                board, trace, layer, index, required)
                            point["x"] = px + nx * push
                            point["y"] = py + ny * push
                            after = _trace_worst(
                                board, trace, layer, index, required)
                            # Refuse rather than regress. A move may leave a gap
                            # short — some copper has nowhere to go — but it may
                            # never leave it shorter than it found it.
                            if after < min(before, required):
                                point["x"], point["y"] = keep
                                continue
                            touched += 1
                            moved_by_trace[tid] = moved_by_trace.get(tid, 0) + 1
                            layer_by_trace[tid] = layer
                            if tid not in before_by_trace or gap < before_by_trace[tid]:
                                before_by_trace[tid] = gap
                                offender_by_trace[tid] = culprit.label
                if not touched:
                    break

        # The guarantee, enforced rather than asserted. A trace whose copper
        # now sits nearer than it started — or under the fab's floor plus the
        # tolerance two geometry engines disagree by — is put back whole. The
        # board began with no gap under the floor, so reverting always lands
        # somewhere safe, and reverting one trace only ever helps its
        # neighbours: the loop settles.
        floor = float(getattr(profile, "min_clearance_mm", 0.10) or 0.10) + float(
            getattr(profile, "drc_tolerance_mm", 0.01) or 0.01
        )
        by_id = {str(t.get("pcb_trace_id") or ""): t for t in traces}

        def _put_back(points: Iterable[dict]) -> int:
            undone = 0
            for point in points:
                found = origin.pop(id(point), None)
                if found is not None:
                    _, ox, oy = found
                    point["x"], point["y"] = ox, oy
                    undone += 1
            return undone

        def _forget(tid: str) -> None:
            moved_by_trace.pop(tid, None)
            before_by_trace.pop(tid, None)
            offender_by_trace.pop(tid, None)
            layer_by_trace.pop(tid, None)

        for _ in range(_MAX_REVERT_ROUNDS):
            undone = 0
            for layer in layers:
                index = _Index(
                    _pad_obstacles(board, layer, net_of_trace)
                    + _trace_capsules(board, layer)
                )
                for tid, trace in by_id.items():
                    if tid not in moved_by_trace:
                        continue
                    route = trace.get("route") or []
                    # First, the narrow undo: put back only the ends of the
                    # segments actually under the floor. A trace where one
                    # segment is stuck and nineteen improved should keep the
                    # nineteen.
                    for i in range(len(route) - 1):
                        a, b = route[i], route[i + 1]
                        seg_layer, half = _seg_layer_half(a, b)
                        if seg_layer != layer:
                            continue
                        ax, ay = diffpair._f(a.get("x")), diffpair._f(a.get("y"))
                        bx, by = diffpair._f(b.get("x")), diffpair._f(b.get("y"))
                        if None in (ax, ay, bx, by):
                            continue
                        gap, _ = _worst_gap(
                            (ax, ay, bx, by), half,
                            index, net_of_trace.get(tid), tid, required)
                        if gap < floor:
                            undone += _put_back((a, b))
                    # Then the whole-trace one: never leave a trace nearer
                    # than it started, whatever the per-segment picture.
                    was = origin_worst.get((tid, layer), math.inf)
                    now = _trace_worst(board, trace, layer, index, required)
                    if now < floor or now < was:
                        undone += _put_back(route)
            # A trace with nothing left of its moves is not a fix, and must
            # not appear in the report as one.
            for tid, trace in by_id.items():
                if tid in moved_by_trace and not any(
                    id(p) in origin for p in (trace.get("route") or [])
                ):
                    _forget(tid)
            if not undone:
                break

        # Final truth, measured on the copper as it now stands rather than
        # predicted from the moves: what still sits inside the margin.
        unresolved = 0
        worst_left = math.inf
        for layer in layers:
            index = _Index(
                _pad_obstacles(board, layer, net_of_trace)
                + _trace_capsules(board, layer)
            )
            for trace in traces:
                tid = str(trace.get("pcb_trace_id") or "")
                net = board.trace_net_key(trace)
                route = trace.get("route") or []
                for i in range(len(route) - 1):
                    a, b = route[i], route[i + 1]
                    seg_layer, half = _seg_layer_half(a, b)
                    if seg_layer != layer:
                        continue
                    ax, ay = diffpair._f(a.get("x")), diffpair._f(a.get("y"))
                    bx, by = diffpair._f(b.get("x")), diffpair._f(b.get("y"))
                    if None in (ax, ay, bx, by):
                        continue
                    gap, _ = _worst_gap(
                        (ax, ay, bx, by), half, index, net, tid, required)
                    if gap < required:
                        unresolved += 1
                        worst_left = min(worst_left, gap)
                    if tid in moved_by_trace:
                        after_by_trace[tid] = min(
                            after_by_trace.get(tid, math.inf), gap)

        fixes = tuple(
            TraceFix(
                trace_id=tid,
                layer=layer_by_trace.get(tid, ""),
                points_moved=count,
                worst_before_mm=before_by_trace.get(tid, 0.0),
                worst_after_mm=after_by_trace.get(tid, 0.0),
                required_mm=required,
                offender=offender_by_trace.get(tid),
            )
            for tid, count in sorted(moved_by_trace.items())
        )

        if fixes:
            path.write_text(
                json.dumps(elements, ensure_ascii=False), encoding="utf-8")

        return TraceClearanceResult(
            ran=True,
            fixes=fixes,
            required_mm=required,
            elapsed_s=time.monotonic() - started,
            unresolved=unresolved,
            worst_unresolved_mm=(0.0 if worst_left is math.inf else worst_left),
        )
    except Exception as exc:  # noqa: BLE001
        # Advisory by construction, exactly like the pour pass: a repair that
        # dies costs a repair, never a verdict. The gate still runs and
        # `clearance_no_margin` still grades whatever copper is there.
        return TraceClearanceResult(
            ran=False,
            elapsed_s=time.monotonic() - started,
            note=f"trace clearance pass raised {type(exc).__name__}: {exc}",
        )


def _away(seg: tuple[float, float, float, float],
          ob: _Obstacle) -> tuple[float, float]:
    """Unit vector pointing from the obstacle toward the segment, projected
    perpendicular to the segment. Zero when the two are concentric enough that
    no direction is better than another — the caller then leaves it alone."""
    mx, my = (seg[0] + seg[2]) / 2, (seg[1] + seg[3]) / 2
    ox, oy = _centre(ob)
    dx, dy = mx - ox, my - oy
    sx, sy = seg[2] - seg[0], seg[3] - seg[1]
    length = math.hypot(sx, sy)
    if length > 1e-9:
        # Perpendicular component only: sliding a segment along itself moves it
        # past the obstacle rather than away from it.
        ux, uy = sx / length, sy / length
        proj = dx * ux + dy * uy
        dx, dy = dx - proj * ux, dy - proj * uy
    norm = math.hypot(dx, dy)
    if norm < 1e-9:
        if length > 1e-9:
            return -sy / length, sx / length
        return 0.0, 0.0
    return dx / norm, dy / norm


def _centre(ob: _Obstacle) -> tuple[float, float]:
    if isinstance(ob, _Round):
        return ob.cx, ob.cy
    if isinstance(ob, _Capsule):
        return (ob.seg[0] + ob.seg[2]) / 2, (ob.seg[1] + ob.seg[3]) / 2
    xs = [p[0] for p in ob.points]
    ys = [p[1] for p in ob.points]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _trace_worst(board: diffpair._Board, trace: dict, layer: str,
                 index: _Index, required: float) -> float:
    """The narrowest gap anywhere along this trace on ``layer``.

    Measured against a one-round-stale index — it holds the copper as it stood
    when the round began — which is exactly the comparison wanted: a move is
    judged against the copper it is trying to clear, not against copper that
    moved in the same sweep. Anything the same round moved is re-measured on
    the next one.
    """
    tid = str(trace.get("pcb_trace_id") or "")
    net = board.trace_net_key(trace)
    route = trace.get("route") or []
    worst = math.inf
    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        seg_layer, half = _seg_layer_half(a, b)
        if seg_layer != layer:
            continue
        ax, ay = diffpair._f(a.get("x")), diffpair._f(a.get("y"))
        bx, by = diffpair._f(b.get("x")), diffpair._f(b.get("y"))
        if None in (ax, ay, bx, by):
            continue
        gap, _ = _worst_gap((ax, ay, bx, by), half, index, net, tid, required)
        worst = min(worst, gap)
    return worst
