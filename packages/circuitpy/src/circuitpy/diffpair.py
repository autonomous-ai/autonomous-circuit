"""Route USB D+/D- as one coupled pair, after the autorouter, before anything reads it.

**The defect this closes.** EE review 2026-08-15, finding 3: *"D+ and D- are
high-speed signals and need to be routed as a differential pair — parallel,
constant gap, with a proper GND reference."* Until now we only *reported* it.
Measured at 52d0803, the two legs of the pair barely meet:

    hydrate-coaster    51.71 / 37.80 mm,  7% coupled, widest gap 15.21 mm
    harness-puck       68.71 / 49.49 mm,  1% coupled, widest gap 16.98 mm
    terminal-keyboard  50.91 / 40.28 mm, 25% coupled, widest gap  7.76 mm

The straight-line distance on hydrate-coaster is 23.05 mm. The router is not
holding a pair; it is solving two unrelated nets that happen to share a name.

**Why this is not seeded best-of-N.** Best-of-N needs N different routes, and
there is only one. The capacity autorouter in ``@tscircuit/cli``'s bundle
contains no randomness: all 86 ``Math.random`` sites in the 25.9 MB file are id
generation, React, three.js, undici and brace-expansion, and **none is within
200 kB of the autorouter**, whose solve loops run to ``MAX_ITERATIONS`` rather
than a clock. That agrees with the six-build measurement in ledger #35 — one
route hash across a 2.9x wall-clock spread. Re-seeding a deterministic search
returns the same copper N times.

**Why this is not pre-routing before the autorouter, either.** tscircuit does
offer a pre-route hook — ``pcbPath`` on a trace pins its exact copper and takes
that connection out of the autorouter's list. It is unsafe here, and the reason
is in the bundle: the subcircuit autorouter builds its obstacle set from

    pcb_component, pcb_smtpad, pcb_plated_hole, pcb_hole, pcb_via, pcb_cutout

(``getSimpleRouteJsonFromCircuitJson``) — ``pcb_trace`` is **not** in that list,
even though ``getObstaclesFromCircuitJson`` knows how to turn one into an
obstacle. Copper we lay down before the autorouter runs is copper it cannot
see, so it would route straight through the pair. The other hook, ``<tracehint>``,
only reaches connections built from two-port ``source_trace`` rows; D+/D- reach
the MCU through a *net*, and net connections never get hint points inserted.

**But the corridor CAN be reserved, and that is the way out for the boards
this pass refuses.** Measured in the bundle 2026-08-16, correcting what the
paragraph above used to imply: the obstacle builder has an explicit
``element.type === "pcb_keepout"`` branch that pushes an oval or a rect with
``connectedTo: []``, and the same for ``pcb_cutout``. A keepout is not copper,
so nothing has to be deleted from the output — the autorouter simply refuses to
enter it, and the pair pass can then lay its two legs in a channel that is
guaranteed clear instead of hunting for one in the leftovers. The pair's
endpoints are known at *placement* time, before any routing happens, so the
reservation costs no extra build. That is what `harness-puck` and
`terminal-keyboard` need, and it is the concrete shape of what
`pipeline-v2.md` calls the v2 route stage.

(The earlier reading of this file listed the obstacle set as components, pads,
holes, vias and cutouts, and concluded that nothing could be reserved. That
list is `getSimpleRouteJsonFromCircuitJson`'s *pre-filter*, not the obstacle
builder, and it is why this pass shipped believing it had no option. The
`pcb_trace` half of the claim stands: copper laid before the autorouter is
still copper it routes straight through.)

**As shipped today the pair is routed after the autorouter, in the space its
own detour frees.** The old copper is deleted first, which is what makes room: a 51.71 mm
wander vacates far more board than a 23 mm pair needs. Then one A* search finds
the pair's **centreline** through a mask dilated by half the pair's span plus
clearance, so both tracks are clear by construction, and the two tracks are that
centreline offset by +/- half the pitch — parallel and constant-gap because they
are the same polyline.

**What it will not do.** It refuses rather than regresses. A pair is left
exactly as the router produced it unless the new copper clears every pad, hole,
via, keepout, pour, other-net trace and the board edge by the fab profile's own
floors, *and* is measurably better on coupling and skew. Every refusal says
which test failed. It also does not claim controlled impedance: 90 ohm
differential is not reachable on 1.6 mm two-layer FR-4 at these widths, and
RP2040 native USB is full-speed. What is delivered is geometry — parallel,
constant gap, matched length — which is what the EE asked for and what the
skew and coupling numbers measure.

**What it costs, measured 2026-08-16 against the same three boards.**

    hydrate-coaster     routed  coupling 7% -> 95%, skew 13.91 -> 1.95 mm,
                                worst clearance 0.115 -> 0.128 mm,
                                8 vias out / 8 in, 8 s
    harness-puck        refused no corridor: a pair at via pitch needs 0.96 mm
                                of clear copper and neither layer has it
    terminal-keyboard   refused no clear fan-out from the RP2040's USB_DM pad

Two of three refuse, and that is the honest limit of running last: the pair
can only use what the autorouter left over. Reserving the corridor *first* is
the v2 route stage's job (`docs/architecture/pipeline-v2.md`), and these two
numbers are what it has to beat.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

try:  # numpy is in the pipeline's environment; the pass is optional without it
    import numpy as _np
except Exception:  # pragma: no cover - exercised only on a stripped runtime
    _np = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants. Every number here is either a fab floor (from FabProfile) or a
# search parameter, and the search parameters are named so a bad one is
# visible in a report rather than buried in an expression.
# ---------------------------------------------------------------------------

#: Search resolution. 0.05 mm is a quarter of the finest clearance we hold and
#: half the smallest via annulus, so a cell can never straddle a rule.
GRID_MM = 0.05

#: What a 45-degree turn costs, in millimetres of equivalent length. Without it
#: A* returns staircases that are the same length as a straight run and look
#: like a router that has lost the plot; 0.25 mm buys clean geometry without
#: ever preferring a detour longer than one via.
TURN_COST_MM = 0.25

#: The search box, as a margin around the two endpoints. Tried in order: a
#: tight box is fast and almost always enough, and the whole board is the
#: honest fallback. Deterministic escalation — the first margin that yields a
#: path wins, so the result does not depend on how fast the machine is.
SEARCH_MARGINS_MM = (6.0, 18.0, None)

#: What a layer change costs the search, in millimetres of equivalent length.
#: A pair may change layers — both legs together, one via each, side by side —
#: because on a two-layer board with parts on top there is often no top-side
#: corridor at all: measured on hydrate-coaster, the top-layer free space
#: around R3/R4 is a 7.6 mm2 pocket with no way out. The cost is high enough
#: that a via is never taken to save a millimetre. It is deliberately high:
#: one layer change costs *two* vias, an impedance step and a stub on both
#: legs, so a pair should only dive when the top side genuinely has no room.
VIA_COST_MM = 6.0

#: Pair pitch: centre-to-centre spacing of the two tracks, as a multiple of the
#: trace width. 2x means the copper gap equals the trace width, which is the
#: usual "tightly coupled" starting point and sits at half the 4-width budget
#: ``netclass_pair_coupling`` grades against.
PAIR_PITCH_WIDTHS = 2.0

#: The budget the coupling check grades against, in trace widths. Kept here
#: because the pitch has to stay inside it — a pair the router widened past the
#: check's own definition of "together" would report as uncoupled, and moving
#: the check to fit the router is how a number stops meaning anything.
COUPLING_WIDTHS = 4.0

#: Slack on top of a fab floor when the floor is what sizes the geometry, so a
#: number is never exactly at the limit where float noise decides.
FLOOR_MARGIN_MM = 0.02

#: The tightest half-angle the centreline can present, as a cosine. A* turns
#: by at most 90 degrees, so the half-angle is at least 45 and cos is at least
#: 1/sqrt(2); the mitre is therefore never more than sqrt(2) times the offset.
MITRE_LIMIT = 0.7071

#: Two points closer than this are the same point.
_EPS_MM = 1e-9

#: Name tokens that make a net one half of a pair. Order matters: the longest
#: token is tested first so ``USB_DP`` is not read as ``…P``.
_PAIR_TOKENS: tuple[tuple[str, str], ...] = (
    ("_DP", "_DM"),
    ("D+", "D-"),
    ("DP", "DM"),
)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class PairMeasurement:
    """One pair's geometry, measured the same way before and after."""

    length_p_mm: float = 0.0
    length_n_mm: float = 0.0
    skew_mm: float = 0.0
    coupled_fraction: float = 0.0
    worst_gap_mm: float = 0.0
    #: Smallest distance from this pair's copper to anything it must not touch.
    #: ``None`` when there is no copper to measure.
    worst_clearance_mm: float | None = None

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "lengthPmm": round(self.length_p_mm, 3),
            "lengthNmm": round(self.length_n_mm, 3),
            "skewMm": round(self.skew_mm, 3),
            "coupledFraction": round(self.coupled_fraction, 4),
            "worstGapMm": round(self.worst_gap_mm, 3),
        }
        if self.worst_clearance_mm is not None:
            out["worstClearanceMm"] = round(self.worst_clearance_mm, 4)
        return out


@dataclass
class PairOutcome:
    """What happened to one pair, in enough detail to argue with."""

    net_p: str
    net_n: str
    status: str  # "routed" | "refused" | "skipped"
    reason: str = ""
    before: PairMeasurement | None = None
    after: PairMeasurement | None = None
    layer: str = ""
    pitch_mm: float = 0.0

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "pair": f"{self.net_p}/{self.net_n}",
            "status": self.status,
        }
        if self.reason:
            out["reason"] = self.reason
        if self.layer:
            out["layer"] = self.layer
        if self.pitch_mm:
            out["pitchMm"] = round(self.pitch_mm, 3)
        if self.before is not None:
            out["before"] = self.before.as_dict()
        if self.after is not None:
            out["after"] = self.after.as_dict()
        return out


@dataclass
class DiffPairResult:
    pairs: list[PairOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    vias_removed: int = 0
    vias_added: int = 0

    @property
    def changed(self) -> bool:
        return any(p.status == "routed" for p in self.pairs)

    def summary(self) -> str:
        parts: list[str] = []
        for pair in self.pairs:
            if pair.status != "routed" or pair.before is None or pair.after is None:
                continue
            parts.append(
                f"{pair.net_p}/{pair.net_n} routed as a pair on {pair.layer}: "
                f"coupling {pair.before.coupled_fraction:.0%} -> "
                f"{pair.after.coupled_fraction:.0%}, skew "
                f"{pair.before.skew_mm:.2f}mm -> {pair.after.skew_mm:.2f}mm, "
                f"length {pair.before.length_p_mm:.1f}/"
                f"{pair.before.length_n_mm:.1f}mm -> "
                f"{pair.after.length_p_mm:.1f}/{pair.after.length_n_mm:.1f}mm"
            )
        return "; ".join(parts)

    def as_dict(self) -> dict[str, object]:
        return {
            "pairs": [p.as_dict() for p in self.pairs],
            "viasRemoved": self.vias_removed,
            "viasAdded": self.vias_added,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Exact geometry. Everything the accept/refuse decision rests on is measured
# here, in millimetres, against the real shapes — never against the raster.
# ---------------------------------------------------------------------------


def _seg_point_distance(
    x0: float, y0: float, x1: float, y1: float, px: float, py: float
) -> float:
    dx, dy = x1 - x0, y1 - y0
    denom = dx * dx + dy * dy
    if denom <= _EPS_MM:
        return math.hypot(px - x0, py - y0)
    t = ((px - x0) * dx + (py - y0) * dy) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _segments_intersect(a: tuple[float, float, float, float],
                        b: tuple[float, float, float, float]) -> bool:
    (ax0, ay0, ax1, ay1) = a
    (bx0, by0, bx1, by1) = b

    def cross(ox, oy, px, py, qx, qy):
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)

    d1 = cross(ax0, ay0, ax1, ay1, bx0, by0)
    d2 = cross(ax0, ay0, ax1, ay1, bx1, by1)
    d3 = cross(bx0, by0, bx1, by1, ax0, ay0)
    d4 = cross(bx0, by0, bx1, by1, ax1, ay1)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def _seg_seg_distance(a: tuple[float, float, float, float],
                      b: tuple[float, float, float, float]) -> float:
    if _segments_intersect(a, b):
        return 0.0
    return min(
        _seg_point_distance(a[0], a[1], a[2], a[3], b[0], b[1]),
        _seg_point_distance(a[0], a[1], a[2], a[3], b[2], b[3]),
        _seg_point_distance(b[0], b[1], b[2], b[3], a[0], a[1]),
        _seg_point_distance(b[0], b[1], b[2], b[3], a[2], a[3]),
    )


def _point_in_poly(points: Sequence[tuple[float, float]], px: float, py: float) -> bool:
    inside = False
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        if (y0 > py) != (y1 > py):
            x_at = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
            if px < x_at:
                inside = not inside
    return inside


@dataclass(frozen=True)
class Obstacle:
    """Anything new copper must stay away from, plus how far away.

    Three shapes cover every element in a circuit.json: a disc (round pads,
    drills, via barrels), a polygon (rect/rotated-rect/pill/polygon pads,
    keepouts, pours) and a capsule (a routed segment, which is a segment with
    a half-width). ``clearance`` is the rule that applies to *this* obstacle —
    a plated hole is 0.28 mm and a track is 0.10 mm, and conflating them is
    ledger #24's mistake in the other direction.
    """

    kind: str
    clearance: float
    layers: tuple[str, ...]
    label: str
    #: The net this copper belongs to, when it has one. Same-net copper is
    #: exempt from its own clearance rule (ledger #24) — a track arriving at
    #: its own pad is a connection, not a violation.
    net: str | None = None
    #: disc
    cx: float = 0.0
    cy: float = 0.0
    radius: float = 0.0
    #: polygon
    poly: tuple[tuple[float, float], ...] = ()
    #: capsule
    seg: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    half_width: float = 0.0

    def distance_to_segment(self, seg: tuple[float, float, float, float]) -> float:
        """Edge-to-centreline distance, i.e. how far the given *centreline* is
        from this obstacle's outer boundary. Negative means overlapping."""
        if self.kind == "disc":
            return _seg_point_distance(*seg, self.cx, self.cy) - self.radius
        if self.kind == "capsule":
            return _seg_seg_distance(seg, self.seg) - self.half_width
        # polygon
        pts = self.poly
        if not pts:
            return math.inf
        if _point_in_poly(pts, seg[0], seg[1]) or _point_in_poly(pts, seg[2], seg[3]):
            return 0.0
        best = math.inf
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            best = min(best, _seg_seg_distance(seg, (x0, y0, x1, y1)))
        return best

    def bounds(self) -> tuple[float, float, float, float]:
        if self.kind == "disc":
            return (self.cx - self.radius, self.cy - self.radius,
                    self.cx + self.radius, self.cy + self.radius)
        if self.kind == "capsule":
            x0, y0, x1, y1 = self.seg
            return (min(x0, x1) - self.half_width, min(y0, y1) - self.half_width,
                    max(x0, x1) + self.half_width, max(y0, y1) + self.half_width)
        xs = [p[0] for p in self.poly] or [0.0]
        ys = [p[1] for p in self.poly] or [0.0]
        return (min(xs), min(ys), max(xs), max(ys))


def _rotated_rect(cx: float, cy: float, w: float, h: float,
                  ccw_deg: float) -> tuple[tuple[float, float], ...]:
    rad = math.radians(ccw_deg or 0.0)
    cos, sin = math.cos(rad), math.sin(rad)
    out = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        dx, dy = sx * w / 2, sy * h / 2
        out.append((cx + dx * cos - dy * sin, cy + dx * sin + dy * cos))
    return tuple(out)


# ---------------------------------------------------------------------------
# Reading the board
# ---------------------------------------------------------------------------


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


class _Board:
    """The joins this pass needs, and nothing else."""

    def __init__(self, elements: Sequence[dict]):
        self.elements = elements
        self.by_type: dict[str, list[dict]] = {}
        for element in elements:
            if isinstance(element, dict) and isinstance(element.get("type"), str):
                self.by_type.setdefault(element["type"], []).append(element)

        self.pcb_ports = {
            e["pcb_port_id"]: e for e in self.by_type.get("pcb_port", [])
            if e.get("pcb_port_id")
        }
        self.source_ports = {
            e["source_port_id"]: e for e in self.by_type.get("source_port", [])
            if e.get("source_port_id")
        }
        self.source_nets = {
            e["source_net_id"]: e for e in self.by_type.get("source_net", [])
            if e.get("source_net_id")
        }
        self.source_traces = {
            e["source_trace_id"]: e for e in self.by_type.get("source_trace", [])
            if e.get("source_trace_id")
        }
        self.board = (self.by_type.get("pcb_board") or [{}])[0]
        self.layers = self._layers()
        self._port_net = self._port_net_map()

    def _layers(self) -> tuple[str, ...]:
        count = _f(self.board.get("num_layers"), 2) or 2
        if int(count) <= 1:
            return ("top",)
        return ("top", "bottom")

    def _port_net_map(self) -> dict[str, str]:
        """pcb_port_id -> connectivity key. The key *is* the net identity; a
        name is only a label some nets happen to carry. The key lives on the
        *source* port — a ``pcb_port`` carries only the join to it."""
        out: dict[str, str] = {}
        for port in self.by_type.get("pcb_port", []):
            pcb_port_id = port.get("pcb_port_id")
            source = self.source_ports.get(str(port.get("source_port_id") or ""))
            key = source.get("subcircuit_connectivity_map_key") if source else None
            if isinstance(key, str) and pcb_port_id:
                out[str(pcb_port_id)] = key
        return out

    def net_key_of_pcb_port(self, pcb_port_id: str) -> str | None:
        return self._port_net.get(pcb_port_id)

    def pcb_ports_of_net(self, net_key: str) -> list[dict]:
        return [
            self.pcb_ports[pid] for pid, key in self._port_net.items()
            if key == net_key and pid in self.pcb_ports
        ]

    def trace_net_key(self, trace: dict) -> str | None:
        """A pcb_trace's net, via any pcb_port it touches."""
        for pid in trace.get("connectsTo") or []:
            key = self._port_net.get(str(pid))
            if key:
                return key
        for point in trace.get("route") or []:
            for field_name in ("start_pcb_port_id", "end_pcb_port_id"):
                pid = point.get(field_name)
                if pid:
                    key = self._port_net.get(str(pid))
                    if key:
                        return key
        key = trace.get("subcircuit_connectivity_map_key")
        return key if isinstance(key, str) else None


def _named_nets(board: _Board) -> dict[str, str]:
    """net name -> connectivity key, for every net that carries a name."""
    out: dict[str, str] = {}
    for net in board.source_nets.values():
        name = net.get("name")
        key = net.get("subcircuit_connectivity_map_key")
        if isinstance(name, str) and isinstance(key, str):
            out[name] = key
    return out


def find_pairs(names: Iterable[str]) -> list[tuple[str, str]]:
    """Every ``…DP…``/``…DM…`` pair among these net names.

    Matching is on the *token*, wherever it sits, so ``USB_DP_CONN`` pairs with
    ``USB_DM_CONN``. The check that shipped before this only tested the end of
    the name, which is why the connector-side pair — the run from the USB-C
    receptacle through the ESD diode — was never measured on any board.
    """
    by_name = {n: n for n in names if n}
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in sorted(by_name):
        if name in seen:
            continue
        upper = name.upper()
        for token_p, token_n in _PAIR_TOKENS:
            index = upper.rfind(token_p)
            if index < 0:
                continue
            partner = name[:index] + token_n + name[index + len(token_p):]
            match = None
            for candidate in by_name:
                if candidate.upper() == partner.upper():
                    match = candidate
                    break
            if match is not None and match not in seen:
                pairs.append((name, match))
                seen.add(name)
                seen.add(match)
            break
    return pairs


# ---------------------------------------------------------------------------
# Measuring a pair
# ---------------------------------------------------------------------------


def _route_segments(trace: dict) -> list[tuple[float, float, float, float, str, float]]:
    """(x0, y0, x1, y1, layer, width) for every wire run in a pcb_trace."""
    out = []
    route = trace.get("route") or []
    previous = None
    for point in route:
        if point.get("route_type") != "wire":
            previous = None
            continue
        x, y = _f(point.get("x")), _f(point.get("y"))
        if x is None or y is None:
            previous = None
            continue
        layer = str(point.get("layer") or "top")
        width = _f(point.get("width"), 0.15) or 0.15
        if previous is not None and previous[2] == layer:
            if abs(previous[0] - x) > _EPS_MM or abs(previous[1] - y) > _EPS_MM:
                out.append((previous[0], previous[1], x, y, layer,
                            min(previous[3], width)))
        previous = (x, y, layer, width)
    return out


def _measure(segs_p: Sequence[tuple], segs_n: Sequence[tuple],
             coupling_widths: float) -> PairMeasurement:
    """Coupled fraction and skew, sampled the same way ``verifylib.netclass``
    samples them so the two numbers are comparable."""
    measurement = PairMeasurement()
    measurement.length_p_mm = sum(math.hypot(s[2] - s[0], s[3] - s[1])
                                  for s in segs_p)
    measurement.length_n_mm = sum(math.hypot(s[2] - s[0], s[3] - s[1])
                                  for s in segs_n)
    measurement.skew_mm = abs(measurement.length_p_mm - measurement.length_n_mm)
    if not segs_p or not segs_n:
        return measurement
    width = min(s[5] for s in segs_p if s[5] > 0)
    budget = coupling_widths * width
    step = 0.5
    total = coupled = 0.0
    worst = 0.0
    for seg in segs_p:
        length = math.hypot(seg[2] - seg[0], seg[3] - seg[1])
        if length <= 0:
            continue
        steps = max(1, int(length / step))
        piece = length / steps
        for i in range(steps):
            t = (i + 0.5) / steps
            px = seg[0] + (seg[2] - seg[0]) * t
            py = seg[1] + (seg[3] - seg[1]) * t
            gap = min(_seg_point_distance(s[0], s[1], s[2], s[3], px, py)
                      for s in segs_n)
            total += piece
            if gap <= budget:
                coupled += piece
            elif gap > worst:
                worst = gap
    if total > 0:
        measurement.coupled_fraction = coupled / total
    measurement.worst_gap_mm = worst
    return measurement


# ---------------------------------------------------------------------------
# The obstacle set
# ---------------------------------------------------------------------------


def _pad_polygon(pad: dict) -> tuple[tuple[float, float], ...] | None:
    shape = pad.get("shape")
    x, y = _f(pad.get("x")), _f(pad.get("y"))
    if shape == "polygon":
        points = pad.get("points")
        if not isinstance(points, list) or len(points) < 3:
            return None
        out = []
        for point in points:
            px, py = _f(point.get("x")), _f(point.get("y"))
            if px is None or py is None:
                return None
            out.append((px, py))
        return tuple(out)
    if x is None or y is None:
        return None
    width, height = _f(pad.get("width")), _f(pad.get("height"))
    if width is None or height is None:
        return None
    rotation = _f(pad.get("ccw_rotation"), 0.0) or 0.0
    return _rotated_rect(x, y, width, height, rotation)


def _stadium_poly(cx: float, cy: float, w: float, h: float,
                  rotation: float = 0.0) -> tuple[tuple[float, float], ...]:
    """A pill as a polygon: the rectangle plus 8 points per end cap. Only ever
    over-covers, which is the safe direction for a clearance obstacle."""
    radius = min(w, h) / 2
    if w >= h:
        a = (-(w / 2 - radius), 0.0)
        b = ((w / 2 - radius), 0.0)
    else:
        a = (0.0, -(h / 2 - radius))
        b = (0.0, (h / 2 - radius))
    points: list[tuple[float, float]] = []
    for centre, start in ((b, -90.0), (a, 90.0)):
        for i in range(9):
            angle = math.radians(start + i * 22.5)
            points.append((centre[0] + radius * math.cos(angle),
                           centre[1] + radius * math.sin(angle)))
    if w < h:
        points = [(px, py) for py, px in ((p[1], p[0]) for p in points)]
    rad = math.radians(rotation or 0.0)
    cos, sin = math.cos(rad), math.sin(rad)
    return tuple(
        (cx + px * cos - py * sin, cy + px * sin + py * cos) for px, py in points
    )


def collect_obstacles(board: _Board, profile: Any,
                      skip_trace_ids: set[str]) -> list[Obstacle]:
    """Every copper, drill, keepout and pour on the board, with its own rule.

    ``skip_trace_ids`` are the traces this pass is about to delete: their copper
    and their vias are not obstacles for the route that replaces them.
    """
    clear = float(getattr(profile, "min_clearance_mm", 0.10))
    npth = float(getattr(profile, "min_npth_to_copper_mm", 0.20))
    pth = float(getattr(profile, "min_pth_to_copper_mm", 0.28))
    via_hole = float(getattr(profile, "min_via_to_copper_mm", 0.20))
    every = board.layers

    out: list[Obstacle] = []

    for pad in board.by_type.get("pcb_smtpad", []):
        layer = str(pad.get("layer") or "top")
        net = board.net_key_of_pcb_port(str(pad.get("pcb_port_id") or ""))
        label = str(pad.get("pcb_smtpad_id") or "pad")
        if pad.get("shape") == "circle":
            cx, cy, radius = _f(pad.get("x")), _f(pad.get("y")), _f(pad.get("radius"))
            if cx is None or cy is None or radius is None:
                continue
            out.append(Obstacle("disc", clear, (layer,), label, cx=cx, cy=cy,
                                radius=radius))
        elif pad.get("shape") in ("pill", "rotated_pill"):
            cx, cy = _f(pad.get("x")), _f(pad.get("y"))
            w, h = _f(pad.get("width")), _f(pad.get("height"))
            if None in (cx, cy, w, h):
                continue
            out.append(Obstacle(
                "poly", clear, (layer,), label,
                poly=_stadium_poly(cx, cy, w, h, _f(pad.get("ccw_rotation"), 0.0) or 0.0),
            ))
        else:
            poly = _pad_polygon(pad)
            if poly is None:
                continue
            out.append(Obstacle("poly", clear, (layer,), label, poly=poly))
        if net:
            out[-1] = _with_net(out[-1], net)

    for hole in board.by_type.get("pcb_plated_hole", []):
        cx, cy = _f(hole.get("x")), _f(hole.get("y"))
        if cx is None or cy is None:
            continue
        net = board.net_key_of_pcb_port(str(hole.get("pcb_port_id") or ""))
        label = str(hole.get("pcb_plated_hole_id") or "pth")
        pad_w = _f(hole.get("outer_width")) or _f(hole.get("rect_pad_width")) \
            or _f(hole.get("outer_diameter"))
        pad_h = _f(hole.get("outer_height")) or _f(hole.get("rect_pad_height")) \
            or _f(hole.get("outer_diameter"))
        if pad_w and pad_h:
            obstacle = Obstacle(
                "poly", clear, every, f"{label}.pad",
                poly=_stadium_poly(cx, cy, pad_w, pad_h,
                                   _f(hole.get("ccw_rotation"), 0.0) or 0.0),
            )
            out.append(_with_net(obstacle, net) if net else obstacle)
        drill_w = _f(hole.get("hole_width")) or _f(hole.get("hole_diameter"))
        drill_h = _f(hole.get("hole_height")) or _f(hole.get("hole_diameter"))
        if drill_w and drill_h:
            # ledger #23/#24: a slot is a stadium, and the hole rule is
            # net-blind on purpose *except* for the pad it belongs to, which
            # `_with_net` handles by exempting same-net copper.
            obstacle = Obstacle(
                "poly", pth, every, f"{label}.drill",
                poly=_stadium_poly(cx, cy, drill_w, drill_h,
                                   _f(hole.get("ccw_rotation"), 0.0) or 0.0),
            )
            out.append(_with_net(obstacle, net) if net else obstacle)

    for hole in board.by_type.get("pcb_hole", []):
        cx, cy = _f(hole.get("x")), _f(hole.get("y"))
        if cx is None or cy is None:
            continue
        label = str(hole.get("pcb_hole_id") or "hole")
        if hole.get("hole_shape") in ("oval", "pill", "rect"):
            w = _f(hole.get("hole_width")) or _f(hole.get("hole_diameter")) or 0.0
            h = _f(hole.get("hole_height")) or _f(hole.get("hole_diameter")) or 0.0
            if w and h:
                out.append(Obstacle("poly", npth, every, label,
                                    poly=_stadium_poly(cx, cy, w, h)))
            continue
        diameter = _f(hole.get("hole_diameter"))
        if diameter:
            out.append(Obstacle("disc", npth, every, label, cx=cx, cy=cy,
                                radius=diameter / 2))

    for via in board.by_type.get("pcb_via", []):
        if str(via.get("pcb_trace_id") or "") in skip_trace_ids:
            continue
        cx, cy = _f(via.get("x")), _f(via.get("y"))
        if cx is None or cy is None:
            continue
        net = via.get("subcircuit_connectivity_map_key")
        label = str(via.get("pcb_via_id") or "via")
        outer = _f(via.get("outer_diameter"), 0.6) or 0.6
        drill = _f(via.get("hole_diameter"), 0.3) or 0.3
        pad_obstacle = Obstacle("disc", clear, every, f"{label}.pad", cx=cx, cy=cy,
                                radius=outer / 2)
        out.append(_with_net(pad_obstacle, net) if isinstance(net, str) else pad_obstacle)
        out.append(Obstacle("disc", via_hole, every, f"{label}.drill", cx=cx, cy=cy,
                            radius=drill / 2))

    for keepout in board.by_type.get("pcb_keepout", []):
        centre = keepout.get("center") or {}
        cx, cy = _f(centre.get("x")), _f(centre.get("y"))
        layers = tuple(str(x) for x in (keepout.get("layers") or every))
        label = str(keepout.get("pcb_keepout_id") or "keepout")
        if cx is None or cy is None:
            continue
        if keepout.get("shape") == "circle":
            radius = _f(keepout.get("radius"))
            if radius:
                out.append(Obstacle("disc", 0.0, layers, label, cx=cx, cy=cy,
                                    radius=radius))
            continue
        w, h = _f(keepout.get("width")), _f(keepout.get("height"))
        if w and h:
            out.append(Obstacle("poly", 0.0, layers, label,
                                poly=_rotated_rect(cx, cy, w, h, 0.0)))

    for cutout in board.by_type.get("pcb_cutout", []):
        label = str(cutout.get("pcb_cutout_id") or "cutout")
        edge = float(getattr(profile, "min_edge_clearance_mm", 0.2))
        shape = cutout.get("shape")
        cx, cy = _f(cutout.get("center", {}).get("x")), _f(cutout.get("center", {}).get("y"))
        if shape == "circle" and cx is not None and cy is not None:
            radius = _f(cutout.get("radius"))
            if radius:
                out.append(Obstacle("disc", edge, every, label, cx=cx, cy=cy,
                                    radius=radius))
        elif shape == "rect" and cx is not None and cy is not None:
            w, h = _f(cutout.get("width")), _f(cutout.get("height"))
            if w and h:
                out.append(Obstacle("poly", edge, every, label,
                                    poly=_rotated_rect(cx, cy, w, h,
                                                       _f(cutout.get("rotation"), 0.0) or 0.0)))
        elif shape == "polygon":
            points = cutout.get("points") or []
            poly = tuple((_f(p.get("x"), 0.0) or 0.0, _f(p.get("y"), 0.0) or 0.0)
                         for p in points)
            if len(poly) >= 3:
                out.append(Obstacle("poly", edge, every, label, poly=poly))

    for pour in board.by_type.get("pcb_copper_pour", []):
        layer = str(pour.get("layer") or "top")
        label = str(pour.get("pcb_copper_pour_id") or "pour")
        net_element = board.source_nets.get(str(pour.get("source_net_id") or ""))
        net = net_element.get("subcircuit_connectivity_map_key") if net_element else None
        poly: tuple[tuple[float, float], ...] = ()
        brep = pour.get("brep_shape") or {}
        ring = brep.get("outer_ring")
        vertices = ring.get("vertices") if isinstance(ring, dict) else ring
        if isinstance(vertices, list) and len(vertices) >= 3:
            poly = tuple((_f(v.get("x"), 0.0) or 0.0, _f(v.get("y"), 0.0) or 0.0)
                         for v in vertices)
        elif pour.get("shape") == "rect":
            centre = pour.get("center") or {}
            cx, cy = _f(centre.get("x")), _f(centre.get("y"))
            w, h = _f(pour.get("width")), _f(pour.get("height"))
            if None not in (cx, cy, w, h):
                poly = _rotated_rect(cx, cy, w, h, _f(pour.get("rotation"), 0.0) or 0.0)
        if len(poly) >= 3:
            obstacle = Obstacle("poly", clear, (layer,), label, poly=poly)
            out.append(_with_net(obstacle, net) if isinstance(net, str) else obstacle)

    for trace in board.by_type.get("pcb_trace", []):
        if str(trace.get("pcb_trace_id") or "") in skip_trace_ids:
            continue
        net = board.trace_net_key(trace)
        label = str(trace.get("pcb_trace_id") or "trace")
        for x0, y0, x1, y1, layer, width in _route_segments(trace):
            obstacle = Obstacle("capsule", clear, (layer,), label,
                                seg=(x0, y0, x1, y1), half_width=width / 2)
            out.append(_with_net(obstacle, net) if net else obstacle)

    return out


def _with_net(obstacle: Obstacle, net: str) -> Obstacle:
    return replace(obstacle, net=net)


# ---------------------------------------------------------------------------
# The search grid
# ---------------------------------------------------------------------------


class _Grid:
    """A blocked/free raster of one layer, at ``GRID_MM``.

    Obstacles are rasterised *already grown* by their own clearance plus the
    pair's half-span, so a free cell is a legal position for the pair's
    centreline. Growing at rasterisation time rather than dilating afterwards
    keeps the geometry exact for every shape — a rotated pad stays rotated.
    """

    def __init__(self, x0: float, y0: float, x1: float, y1: float):
        self.x0, self.y0 = x0, y0
        self.nx = max(1, int(math.ceil((x1 - x0) / GRID_MM)))
        self.ny = max(1, int(math.ceil((y1 - y0) / GRID_MM)))
        self.blocked = _np.zeros((self.ny, self.nx), dtype=bool)
        self._xs = x0 + (_np.arange(self.nx) + 0.5) * GRID_MM
        self._ys = y0 + (_np.arange(self.ny) + 0.5) * GRID_MM

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        col = int((x - self.x0) / GRID_MM)
        row = int((y - self.y0) / GRID_MM)
        return (max(0, min(self.ny - 1, row)), max(0, min(self.nx - 1, col)))

    def centre_of(self, row: int, col: int) -> tuple[float, float]:
        return (float(self._xs[col]), float(self._ys[row]))

    def _slice(self, bx0: float, by0: float, bx1: float, by1: float):
        c0 = max(0, int(math.floor((bx0 - self.x0) / GRID_MM)))
        c1 = min(self.nx, int(math.ceil((bx1 - self.x0) / GRID_MM)) + 1)
        r0 = max(0, int(math.floor((by0 - self.y0) / GRID_MM)))
        r1 = min(self.ny, int(math.ceil((by1 - self.y0) / GRID_MM)) + 1)
        if c0 >= c1 or r0 >= r1:
            return None
        return r0, r1, c0, c1

    def block(self, obstacle: Obstacle, grow: float) -> None:
        radius = obstacle.clearance + grow
        bx0, by0, bx1, by1 = obstacle.bounds()
        window = self._slice(bx0 - radius, by0 - radius, bx1 + radius, by1 + radius)
        if window is None:
            return
        r0, r1, c0, c1 = window
        xs = self._xs[c0:c1][_np.newaxis, :]
        ys = self._ys[r0:r1][:, _np.newaxis]
        if obstacle.kind == "disc":
            distance = _np.hypot(xs - obstacle.cx, ys - obstacle.cy) - obstacle.radius
        elif obstacle.kind == "capsule":
            distance = _seg_distance_grid(xs, ys, obstacle.seg) - obstacle.half_width
        else:
            distance = _poly_distance_grid(xs, ys, obstacle.poly)
        self.blocked[r0:r1, c0:c1] |= distance <= radius

    def block_outside(self, poly: Sequence[tuple[float, float]], margin: float) -> None:
        """Everything outside ``poly``, and everything within ``margin`` of its
        edge, is blocked. The board outline is the last obstacle."""
        xs = self._xs[_np.newaxis, :]
        ys = self._ys[:, _np.newaxis]
        inside = _point_in_poly_grid(xs, ys, poly)
        near_edge = _np.zeros_like(inside)
        for i in range(len(poly)):
            ax, ay = poly[i]
            bx, by = poly[(i + 1) % len(poly)]
            near_edge |= _seg_distance_grid(xs, ys, (ax, ay, bx, by)) <= margin
        self.blocked |= (~inside) | near_edge

    def clone(self) -> "_Grid":
        copy = object.__new__(_Grid)
        copy.x0, copy.y0 = self.x0, self.y0
        copy.nx, copy.ny = self.nx, self.ny
        copy.blocked = self.blocked.copy()
        copy._xs, copy._ys = self._xs, self._ys
        return copy

    def nearest_free(self, row: int, col: int, limit_cells: int) -> tuple[int, int] | None:
        """Breadth-first, so the answer is the closest free cell in cells and
        the tie-break is a fixed neighbour order — the same board gives the
        same seed point every run."""
        if not self.blocked[row, col]:
            return (row, col)
        seen = {(row, col)}
        frontier = [(row, col)]
        for _ in range(limit_cells):
            following: list[tuple[int, int]] = []
            for r, c in frontier:
                for dr, dc in ((-1, 0), (0, -1), (0, 1), (1, 0)):
                    nr, nc = r + dr, c + dc
                    if not (0 <= nr < self.ny and 0 <= nc < self.nx):
                        continue
                    if (nr, nc) in seen:
                        continue
                    seen.add((nr, nc))
                    if not self.blocked[nr, nc]:
                        return (nr, nc)
                    following.append((nr, nc))
            if not following:
                return None
            frontier = following
        return None


def _seg_distance_grid(xs, ys, seg: tuple[float, float, float, float]):
    x0, y0, x1, y1 = seg
    dx, dy = x1 - x0, y1 - y0
    denom = dx * dx + dy * dy
    if denom <= _EPS_MM:
        return _np.hypot(xs - x0, ys - y0)
    t = ((xs - x0) * dx + (ys - y0) * dy) / denom
    t = _np.clip(t, 0.0, 1.0)
    return _np.hypot(xs - (x0 + t * dx), ys - (y0 + t * dy))


def _point_in_poly_grid(xs, ys, poly: Sequence[tuple[float, float]]):
    inside = _np.zeros((ys.shape[0], xs.shape[1]), dtype=bool)
    X = _np.broadcast_to(xs, inside.shape)
    Y = _np.broadcast_to(ys, inside.shape)
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if ay == by:
            continue
        straddles = ((ay > Y) != (by > Y))
        with _np.errstate(divide="ignore", invalid="ignore"):
            x_at = ax + (Y - ay) * (bx - ax) / (by - ay)
        inside ^= straddles & (X < x_at)
    return inside


def _poly_distance_grid(xs, ys, poly: Sequence[tuple[float, float]]):
    shape = (ys.shape[0], xs.shape[1])
    best = _np.full(shape, _np.inf)
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        best = _np.minimum(best, _seg_distance_grid(xs, ys, (ax, ay, bx, by)))
    best = _np.where(_point_in_poly_grid(xs, ys, poly), 0.0, best)
    return best


#: Eight neighbours, in a fixed order. The order is part of the determinism
#: contract: equal-cost paths are broken by push order and push order is this.
_STEPS: tuple[tuple[int, int, float], ...] = (
    (-1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0), (1, 0, 1.0),
    (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)),
    (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2)),
)


def _astar(track: Sequence[_Grid], via: Sequence[_Grid],
           start: tuple[int, int, int],
           goal: tuple[int, int, int]) -> list[tuple[int, int, int]] | None:
    """Shortest free path for the pair's centreline across every layer.

    A state is ``(layer, row, col)``. A planar move needs the cell free in that
    layer's *track* mask — dilated by half the pair's span, so both tracks
    clear. A layer change needs the cell free in **every** layer's *via* mask,
    dilated by half the pair's span measured at the via pad, because a layer
    change is two vias side by side, not one.

    Deterministic by construction: a fixed neighbour order, a monotonic push
    counter for ties, and no wall clock anywhere.
    """
    layers = len(track)
    ny, nx = track[0].ny, track[0].nx
    if track[start[0]].blocked[start[1], start[2]]:
        return None
    if track[goal[0]].blocked[goal[1], goal[2]]:
        return None

    def heuristic(state: tuple[int, int, int]) -> float:
        dr = abs(state[1] - goal[1])
        dc = abs(state[2] - goal[2])
        planar = (max(dr, dc) + (math.sqrt(2) - 1) * min(dr, dc)) * GRID_MM
        return planar + (VIA_COST_MM if state[0] != goal[0] else 0.0)

    counter = 0
    best_cost: dict[tuple[int, int, int], float] = {start: 0.0}
    came: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    #: the planar step we arrived by. A layer change keeps the step it was
    #: travelling on, and records the state in ``after_via``.
    arrival: dict[tuple[int, int, int], tuple[int, int] | None] = {}
    #: states reached by a layer change. The next move out of one of these is
    #: **forced** to continue in the same direction, so a via never lands on a
    #: corner. That matters because the two legs are the centreline offset by
    #: plus and minus half the pitch: at a corner the offsets swing around the
    #: junction, and one leg walks straight into the other leg's via. Measured
    #: on hydrate-coaster before the rule, -0.258mm.
    after_via: set[tuple[int, int, int]] = set()
    queue: list[tuple[float, int, tuple[int, int, int]]] = [
        (heuristic(start), 0, start)
    ]
    while queue:
        _, _, state = heapq.heappop(queue)
        if state == goal:
            path = [state]
            while path[-1] != start:
                path.append(came[path[-1]])
            path.reverse()
            return path
        cost = best_cost[state]
        layer, row, col = state
        previous = arrival.get(state)
        forced = previous if state in after_via else None
        moves: list[tuple[tuple[int, int, int], float, tuple[int, int] | None, bool]] = []
        for dr, dc, step in _STEPS:
            if forced is not None and (dr, dc) != forced:
                continue
            # No turn sharper than 90 degrees. Not a style rule: the two legs
            # are this line offset both ways, and the mitre at a corner sits
            # `1/cos(half-angle)` times the offset from it. Capping the turn
            # caps that at sqrt(2), which keeps both legs inside the corridor
            # the search proved clear.
            if previous is not None and previous[0] * dr + previous[1] * dc < 0:
                continue
            nr, nc = row + dr, col + dc
            if not (0 <= nr < ny and 0 <= nc < nx):
                continue
            if track[layer].blocked[nr, nc]:
                continue
            turn = TURN_COST_MM if previous is not None and previous != (dr, dc) else 0.0
            moves.append(((layer, nr, nc), step * GRID_MM + turn, (dr, dc), False))
        # A layer change, only from a state we walked into and never straight
        # out of another one: two vias on one point is not a route, it is a
        # loop.
        if (previous is not None and state not in after_via
                and all(not g.blocked[row, col] for g in via)):
            for other in range(layers):
                if other != layer:
                    moves.append(((other, row, col), VIA_COST_MM, previous, True))
        for key, delta, how, is_via in moves:
            candidate = cost + delta
            if candidate + 1e-12 >= best_cost.get(key, math.inf):
                continue
            best_cost[key] = candidate
            came[key] = state
            arrival[key] = how
            if is_via:
                after_via.add(key)
            else:
                after_via.discard(key)
            counter += 1
            heapq.heappush(queue, (candidate + heuristic(key), counter, key))
    return None


def _despike(points: Sequence[tuple[float, float]], min_segment: float,
             free: _Grid) -> list[tuple[float, float]]:
    """Remove wiggles the pair is too wide to follow.

    A grid path can turn twice inside a tenth of a millimetre — two 90-degree
    turns in a row are a 180-degree reversal, and offsetting one puts the two
    legs' mitres on top of each other. Measured on hydrate-coaster: a 0.07mm
    zig-zag on the bottom layer walked D+ to **0.07mm** from D-'s via.

    A centreline cannot carry a pair through a corner shorter than the pair is
    wide, so any interior segment under ``min_segment`` has one of its ends
    dropped — whichever moves the line less, **and only if the line that
    replaces it is still inside the corridor**. Without that condition the
    shortcut is free to cut a corner into a via: measured, 0.0664mm from
    ``pcb_via_50``, which is a short.
    """
    out = [tuple(p) for p in points]
    for _ in range(len(out) * 2):
        if len(out) <= 2:
            break
        worst_index = None
        worst_length = min_segment
        for i in range(1, len(out) - 2):
            length = math.hypot(out[i + 1][0] - out[i][0],
                                out[i + 1][1] - out[i][1])
            if length < worst_length:
                worst_length = length
                worst_index = i
        if worst_index is None:
            break
        # Drop whichever end of the short segment moves the polyline less —
        # and only into space the corridor already cleared.
        options = [worst_index]
        if worst_index + 1 < len(out) - 1:
            here = _seg_point_distance(*out[worst_index - 1], *out[worst_index + 1],
                                       *out[worst_index])
            there = _seg_point_distance(*out[worst_index], *out[worst_index + 2],
                                        *out[worst_index + 1])
            options = ([worst_index + 1, worst_index] if there < here
                       else [worst_index, worst_index + 1])
        dropped = False
        for drop in options:
            if _line_is_free(free, out[drop - 1], out[drop + 1]):
                out.pop(drop)
                dropped = True
                break
        if not dropped:
            break
    return _simplify(out)


def _runs_from_path(path: Sequence[tuple[int, int, int]], grid: _Grid,
                    layers: Sequence[str], min_segment: float,
                    track: Sequence[_Grid]) -> list[tuple[str, list]]:
    """Split an A* path into one polyline per layer, in walking order."""
    runs: list[tuple[str, list]] = []
    current_layer = path[0][0]
    cells: list[tuple[int, int]] = []
    for layer_index, row, col in path:
        if layer_index != current_layer:
            runs.append((layers[current_layer],
                         _despike([grid.centre_of(r, c) for r, c in cells],
                                  min_segment, track[current_layer])))
            current_layer = layer_index
            cells = [(row, col)]
            continue
        if not cells or cells[-1] != (row, col):
            cells.append((row, col))
    runs.append((layers[current_layer],
                 _despike([grid.centre_of(r, c) for r, c in cells], min_segment,
                          track[current_layer])))
    return [run for run in runs if len(run[1]) >= 1]


def _simplify(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge collinear runs. The A* path is one point per cell; a 20 mm
    straight run should be two points, not four hundred."""
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    for i in range(1, len(points) - 1):
        ax, ay = out[-1]
        bx, by = points[i]
        cx, cy = points[i + 1]
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(cross) > 1e-9:
            out.append(points[i])
    out.append(points[-1])
    return out


# ---------------------------------------------------------------------------
# Turning one centreline into two tracks
# ---------------------------------------------------------------------------


def _offset_polyline(points: Sequence[tuple[float, float]],
                     distance: float) -> list[tuple[float, float]]:
    """The same polyline, shifted ``distance`` along its left normal.

    Mitred at the joints, bevelled where a mitre would spike. Both tracks come
    from this function with opposite signs, which is what makes the gap
    constant: they are the same shape.
    """
    if len(points) < 2:
        return list(points)
    normals: list[tuple[float, float]] = []
    for i in range(len(points) - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        length = math.hypot(dx, dy) or 1.0
        normals.append((-dy / length, dx / length))

    out: list[tuple[float, float]] = [
        (points[0][0] + normals[0][0] * distance,
         points[0][1] + normals[0][1] * distance)
    ]
    for i in range(1, len(points) - 1):
        n0, n1 = normals[i - 1], normals[i]
        bx, by = n0[0] + n1[0], n0[1] + n1[1]
        norm = math.hypot(bx, by)
        if norm < 1e-6:  # a reversal; a mitre is meaningless
            out.append((points[i][0] + n0[0] * distance,
                        points[i][1] + n0[1] * distance))
            out.append((points[i][0] + n1[0] * distance,
                        points[i][1] + n1[1] * distance))
            continue
        bx, by = bx / norm, by / norm
        cos_half = bx * n0[0] + by * n0[1]
        # Mitred, and bounded: the search cannot turn the centreline by more
        # than 90 degrees (`_STEPS` plus the dot-product rule in `_astar`), so
        # the half-angle is at least 45 degrees and the mitre never sits more
        # than sqrt(2) times the offset from the centreline. That bound is why
        # a mitre is safe here at all — an unbounded one at a 135-degree turn
        # reaches 2.6x, leaves the corridor the search proved clear, and put
        # the pair 0.1428mm inside USB_DM_CONN on hydrate-coaster. Bevelling
        # instead was tried and is worse: on the inner side of a sharp corner
        # the two bevels both cut across the centreline and the legs touch
        # (-0.2646mm, same board).
        scale = distance / max(cos_half, MITRE_LIMIT)
        out.append((points[i][0] + bx * scale, points[i][1] + by * scale))
    out.append((points[-1][0] + normals[-1][0] * distance,
                points[-1][1] + normals[-1][1] * distance))
    return out


# ---------------------------------------------------------------------------
# The accept/refuse test
# ---------------------------------------------------------------------------


@dataclass
class _Violation:
    detail: str
    margin_mm: float


def _worst_clearance(segments: Sequence[tuple], obstacles: Sequence[Obstacle],
                     outline: Sequence[tuple[float, float]] | None,
                     edge_clearance: float) -> tuple[float, _Violation | None]:
    """Smallest copper-to-copper gap this geometry leaves, and the first rule
    it breaks. Exact, against real shapes — the raster is only ever a search
    aid, never evidence."""
    worst = math.inf
    violation: _Violation | None = None
    for x0, y0, x1, y1, layer, width, net in segments:
        seg = (x0, y0, x1, y1)
        half = width / 2
        for obstacle in obstacles:
            if layer not in obstacle.layers:
                continue
            if obstacle.net is not None and net and obstacle.net == net:
                continue
            distance = obstacle.distance_to_segment(seg)
            gap = distance - half
            if gap < worst:
                worst = gap
            margin = gap - obstacle.clearance
            if margin < 0 and (violation is None or margin < violation.margin_mm):
                violation = _Violation(
                    f"{obstacle.label} is {gap:.4f}mm from the new copper, "
                    f"under the {obstacle.clearance:.2f}mm floor",
                    margin,
                )
        if outline:
            for i in range(len(outline)):
                ax, ay = outline[i]
                bx, by = outline[(i + 1) % len(outline)]
                gap = _seg_seg_distance(seg, (ax, ay, bx, by)) - half
                if gap < worst:
                    worst = gap
                margin = gap - edge_clearance
                if margin < 0 and (violation is None or margin < violation.margin_mm):
                    violation = _Violation(
                        f"the board edge is {gap:.4f}mm from the new copper, "
                        f"under the {edge_clearance:.2f}mm floor",
                        margin,
                    )
            for px, py in ((x0, y0), (x1, y1)):
                if not _point_in_poly(outline, px, py):
                    violation = _Violation(
                        f"a new segment ends at ({px:.2f}, {py:.2f}), outside "
                        "the board outline",
                        -math.inf,
                    )
    return (worst if worst is not math.inf else 0.0), violation


def _pair_self_clearance(segs_p: Sequence[tuple], segs_n: Sequence[tuple]) -> float:
    """How close the two legs come *on the same layer*.

    Layer matters here and nowhere else in this file: at a crossover the two
    legs pass over each other on purpose, and a layer-blind measurement reads
    that as a short.
    """
    worst = math.inf
    for x0, y0, x1, y1, layer_p, wp, _ in segs_p:
        for a0, b0, a1, b1, layer_n, wn, _ in segs_n:
            if layer_p != layer_n:
                continue
            gap = _seg_seg_distance((x0, y0, x1, y1), (a0, b0, a1, b1))
            worst = min(worst, gap - wp / 2 - wn / 2)
    # Nothing shares a layer, so nothing can short: infinity, not zero. Zero
    # would read as "touching" and refuse every crossover.
    return worst


# ---------------------------------------------------------------------------
# Routing one pair
# ---------------------------------------------------------------------------


def _board_outline(board: _Board) -> list[tuple[float, float]] | None:
    outline = board.board.get("outline")
    if isinstance(outline, list) and len(outline) >= 3:
        points = []
        for point in outline:
            x, y = _f(point.get("x")), _f(point.get("y"))
            if x is None or y is None:
                return None
            points.append((x, y))
        return points
    centre = board.board.get("center") or {}
    cx, cy = _f(centre.get("x"), 0.0) or 0.0, _f(centre.get("y"), 0.0) or 0.0
    w, h = _f(board.board.get("width")), _f(board.board.get("height"))
    if w is None or h is None:
        return None
    return list(_rotated_rect(cx, cy, w, h, 0.0))


def _pad_layer(pcb_port: dict) -> str | None:
    layers = pcb_port.get("layers")
    if isinstance(layers, list) and layers:
        return str(layers[0])
    return None


def _nodes_to_segments(nodes: Sequence[dict], width: float,
                       net: str) -> list[tuple]:
    """(x0, y0, x1, y1, layer, width, net) for every wire run between nodes."""
    out: list[tuple] = []
    for a, b in zip(nodes, nodes[1:]):
        if a["kind"] != "wire" or b["kind"] != "wire":
            continue
        if a["layer"] != b["layer"]:
            continue
        if math.hypot(b["x"] - a["x"], b["y"] - a["y"]) <= _EPS_MM:
            continue
        out.append((a["x"], a["y"], b["x"], b["y"], a["layer"], width, net))
    return out


def _via_segments(nodes: Sequence[dict], layers: Sequence[str], outer: float,
                  net: str) -> list[tuple]:
    """A via, as a zero-length segment of via-pad width on every layer, so the
    same clearance measurement covers it."""
    out: list[tuple] = []
    for node in nodes:
        if node["kind"] != "via":
            continue
        for layer in layers:
            out.append((node["x"], node["y"], node["x"], node["y"], layer,
                        outer, net))
    return out


def _route_one_pair(board: _Board, obstacles: Sequence[Obstacle],
                    outline: Sequence[tuple[float, float]] | None,
                    profile: Any, ends: Sequence[tuple[dict, dict]],
                    layer: str, width: float,
                    net_p: str, net_n: str
                    ) -> tuple[list[dict], list[dict], float, str]:
    """Two coupled tracks as ordered route nodes, or ``([], [], pitch, why)``.

    Two attempts, widest first. A pair that may change layers needs a corridor
    as wide as a via pair; one that may not needs only two tracks, which is
    half as much, and on a congested board that is sometimes the difference
    between a pair and no pair. The wide attempt goes first because a pair that
    can dive reaches ends a single layer cannot.
    """
    reasons: list[str] = []
    pitch_reported = PAIR_PITCH_WIDTHS * width
    for allow_layers in (True, False):
        nodes_p, nodes_n, pitch, why = _route_pair_attempt(
            board, obstacles, outline, profile, ends, layer, width,
            net_p, net_n, allow_layers)
        if nodes_p and nodes_n:
            return nodes_p, nodes_n, pitch, ""
        pitch_reported = pitch
        label = "two layers" if allow_layers else "one layer"
        reasons.append(f"{label}: {why}")
    return [], [], pitch_reported, "; ".join(reasons)


def _route_pair_attempt(board: _Board, obstacles: Sequence[Obstacle],
                        outline: Sequence[tuple[float, float]] | None,
                        profile: Any, ends: Sequence[tuple[dict, dict]],
                        layer: str, width: float,
                        net_p: str, net_n: str, allow_layers: bool
                        ) -> tuple[list[dict], list[dict], float, str]:
    clearance = float(getattr(profile, "min_clearance_mm", 0.10))
    edge_clearance = float(getattr(profile, "min_edge_clearance_mm", 0.2))
    # The via geometry the boards already ship with, so a pair route never
    # trips the warn band on via pad or drill (ledger #28: a smaller via pad
    # was tried on the whole board and measured much worse).
    via_outer = max(float(getattr(profile, "min_via_diameter_mm", 0.3)),
                    float(getattr(profile, "warn_via_diameter_mm", 0.45)))
    via_drill = float(getattr(profile, "warn_via_drill_mm", 0.3))
    # **The pitch is set by the via, not by the trace.** Two tracks 2x width
    # apart is the textbook tight pair, but the two vias at a layer change sit
    # on the same pitch, and a 0.45mm via pad 0.30mm from its partner overlaps
    # it by 0.15mm — measured, and the first thing that blocked this pass. So
    # the pitch opens up to fit the vias, and stays inside the coupling budget
    # or the pair does not change layers at all.
    via_to_copper = float(getattr(profile, "min_via_to_copper_mm", 0.20))
    # Three rules bind two stacked vias, and the tightest is not the obvious
    # one. Measured on the first end-to-end build: at a 0.57mm pitch the pass
    # produced eight `dfm_hole_clearance` errors and five KiCad hole-clearance
    # violations, all of them one via's **pad** 0.196mm from the other via's
    # **drill** against a 0.20mm rule — copper-to-copper was fine, and
    # drill-to-drill was fine, and the cross term was the one nobody modelled.
    tight = PAIR_PITCH_WIDTHS * width
    pitch = max(
        tight,
        via_outer + clearance + FLOOR_MARGIN_MM,              # pad to pad
        via_outer / 2 + via_drill / 2 + via_to_copper + FLOOR_MARGIN_MM,
        via_drill + via_to_copper + FLOOR_MARGIN_MM,          # drill to drill
    )
    allow_layer_change = allow_layers and pitch <= COUPLING_WIDTHS * width + 1e-9
    if not allow_layer_change:
        pitch = tight
    if pitch - width < clearance - 1e-9:
        return [], [], pitch, (
            f"a {pitch:.3f}mm pitch on {width:.3f}mm copper leaves "
            f"{pitch - width:.3f}mm between the tracks, under the "
            f"{clearance:.2f}mm floor"
        )
    # How far the pair's outermost copper can sit from its centreline. Not
    # `pitch/2 + width/2`: at a corner the mitre pushes the offset out by
    # `1/cos(half-angle)`, and A*'s 90-degree turn cap bounds that at
    # 1/MITRE_LIMIT. Dilating by the smaller number leaves the mitre outside
    # the corridor the search proved — measured, 0.0664mm from pcb_via_50 on
    # hydrate-coaster, which is a short the search believed it had avoided.
    half_span = pitch / 2 / MITRE_LIMIT + width / 2
    half_span_via = pitch / 2 + via_outer / 2
    layers = list(board.layers)
    if layer not in layers:
        layers = [layer] + layers
    if not allow_layer_change:
        layers = [layer]
    start_layer = layers.index(layer)

    anchors = []
    for port_p, port_n in ends:
        px, py = _f(port_p.get("x")), _f(port_p.get("y"))
        nx, ny = _f(port_n.get("x")), _f(port_n.get("y"))
        if None in (px, py, nx, ny):
            return [], [], pitch, "an endpoint pad has no position"
        anchors.append(((px, py), (nx, ny), ((px + nx) / 2, (py + ny) / 2)))

    if outline:
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        board_box = (min(xs), min(ys), max(xs), max(ys))
        raster_outline = _thinned(outline, 192)
    else:
        board_box = (-1e3, -1e3, 1e3, 1e3)
        raster_outline = None

    # One raster per leg for the fan-outs: a single track's clearance, with
    # that leg's own net exempt so a fan may end inside its own pad. Built
    # before the search because the seed is chosen with them — where the pair
    # starts is a question about the two fans, not about the corridor.
    pad_xs = [anchors[i][j][0] for i in (0, 1) for j in (0, 1)]
    pad_ys = [anchors[i][j][1] for i in (0, 1) for j in (0, 1)]
    fan_box = (max(board_box[0], min(pad_xs) - 8.0),
               max(board_box[1], min(pad_ys) - 8.0),
               min(board_box[2], max(pad_xs) + 8.0),
               min(board_box[3], max(pad_ys) + 8.0))
    fan_grids: dict[str, _Grid] = {}
    for net in (net_p, net_n):
        grid = _Grid(*fan_box)
        for obstacle in obstacles:
            if layer not in obstacle.layers:
                continue
            if obstacle.net is not None and obstacle.net == net:
                continue
            grid.block(obstacle, width / 2)
        if raster_outline:
            grid.block_outside(raster_outline, edge_clearance + width / 2)
        fan_grids[net] = grid

    runs: list[tuple[str, list]] | None = None
    for margin in SEARCH_MARGINS_MM:
        if margin is None:
            box = board_box
        else:
            xs = [anchors[0][2][0], anchors[1][2][0]]
            ys = [anchors[0][2][1], anchors[1][2][1]]
            box = (max(board_box[0], min(xs) - margin),
                   max(board_box[1], min(ys) - margin),
                   min(board_box[2], max(xs) + margin),
                   min(board_box[3], max(ys) + margin))
        probe = _Grid(*box)
        if probe.nx * probe.ny * len(layers) > 16_000_000:
            return [], [], pitch, "the search box is larger than this pass will raster"
        track_grids = [_Grid(*box) for _ in layers]
        via_grids = [_Grid(*box) for _ in layers]
        for obstacle in obstacles:
            for index, name in enumerate(layers):
                if name not in obstacle.layers:
                    continue
                track_grids[index].block(obstacle, half_span)
                via_grids[index].block(obstacle, half_span_via)
        if raster_outline:
            for index in range(len(layers)):
                track_grids[index].block_outside(
                    raster_outline, edge_clearance + half_span)
                via_grids[index].block_outside(
                    raster_outline, edge_clearance + half_span_via)
        seed = _pair_seed(track_grids[start_layer], fan_grids,
                          anchors[0][0], anchors[0][1], net_p, net_n,
                          anchors[1][2])
        target = _pair_seed(track_grids[start_layer], fan_grids,
                            anchors[1][0], anchors[1][1], net_p, net_n,
                            anchors[0][2])
        if seed is None or target is None:
            continue
        path = _astar(track_grids, via_grids,
                      (start_layer, *seed), (start_layer, *target))
        if path is None:
            continue
        runs = _runs_from_path(path, probe, layers, pitch, track_grids)
        break
    if not runs:
        return [], [], pitch, (
            "no corridor wide enough for the pair exists between the two ends "
            f"(needs {2 * half_span:.2f}mm of clear copper plus clearance, on "
            f"any of {', '.join(layers)})"
        )
    if any(len(points) < 2 for points in (r[1] for r in runs)):
        return [], [], pitch, "the pair route ends on a layer change; nothing to offset"

    #: (stage reached, message). A refusal at a later stage says more about
    #: why the pair cannot be routed than one at an earlier stage, and the
    #: last combination tried is not the most informative one.
    best_reason = (0, "no side assignment or crossover point produced a legal pair")
    swap_points: list[int | None] = [None] + list(range(len(runs) - 1))
    for side in (1.0, -1.0):
        for swap in swap_points:
            built = _build_pair(runs, side, swap, pitch, width,
                                via_outer, via_drill, clearance, via_to_copper)
            if built is None:
                continue
            nodes_p, nodes_n = built
            fanned = _attach_fans(nodes_p, nodes_n, anchors, fan_grids,
                                  net_p, net_n, width, layer, clearance)
            if isinstance(fanned, str):
                best_reason = max(best_reason, (1, fanned))
                continue
            nodes_p, nodes_n = fanned

            segs_p = _nodes_to_segments(nodes_p, width, net_p)
            segs_n = _nodes_to_segments(nodes_n, width, net_n)
            vias = (_via_segments(nodes_p, layers, via_outer, net_p)
                    + _via_segments(nodes_n, layers, via_outer, net_n))
            _, violation = _worst_clearance(segs_p + segs_n + vias, obstacles,
                                            outline, edge_clearance)
            if violation is not None:
                best_reason = max(best_reason, (2, violation.detail))
                continue
            self_gap = _pair_self_clearance(
                segs_p + [v for v in vias if v[6] == net_p],
                segs_n + [v for v in vias if v[6] == net_n],
            )
            if self_gap < clearance - 1e-9:
                best_reason = max(best_reason, (3, (
                    f"the two legs come within {self_gap:.4f}mm of each "
                    f"other on one layer, under the {clearance:.2f}mm floor"
                )))
                continue
            return nodes_p, nodes_n, pitch, ""
    return [], [], pitch, best_reason[1]


#: How far from the two pads the pair's end may sit, and how coarsely the
#: candidates are sampled. 2.5mm is about as far as a fan-out should ever run
#: from a pad it belongs to; 0.1mm steps keep the candidate set at a few
#: thousand without ever being coarser than a pad.
SEED_RADIUS_MM = 2.5
SEED_STEP_CELLS = 2

#: How hard an unbalanced fan-out is punished, relative to its length. The two
#: tracks are congruent by construction, so the fans are the *only* thing that
#: can put skew into a pair — weighting the difference three times the length
#: buys a matched pair at the cost of a fraction of a millimetre of detour.
SEED_BALANCE_WEIGHT = 3.0


def _pair_seed(track: _Grid, fan_grids: dict[str, _Grid],
               pad_p: tuple[float, float], pad_n: tuple[float, float],
               net_p: str, net_n: str,
               toward: tuple[float, float]) -> tuple[int, int] | None:
    """Where the pair should start, given the two pads it has to reach.

    Not "the nearest free cell to the midpoint" — that was the first version
    and it is measurably wrong. On a pair of pads 0.6mm apart the midpoint and
    everything around it is inside both pads' clearance, so the nearest free
    cell lands *past* the pads and both fans have to double back around them:
    on the two-pad bench that produced 17.2mm against 13.9mm, 3.3mm of skew on
    a pair whose tracks are congruent.

    So the seed is scored instead: reachable from both pads in a straight line,
    short, and above all **balanced**, because the fans are the only place skew
    can come from. ``toward`` is the pair's other end: without it the two free
    cells either side of a pad pair score identically and the tie-break picks
    whichever has the lower index, which on the two-pad bench sent the pair out
    of the back of the pads and both fans the long way round them.
    """
    mid = ((pad_p[0] + pad_n[0]) / 2, (pad_p[1] + pad_n[1]) / 2)
    row0, col0 = track.cell_of(*mid)
    span = int(SEED_RADIUS_MM / GRID_MM)
    best: tuple[float, int, int, tuple[int, int]] | None = None
    for dr in range(-span, span + 1, SEED_STEP_CELLS):
        for dc in range(-span, span + 1, SEED_STEP_CELLS):
            row, col = row0 + dr, col0 + dc
            if not (0 <= row < track.ny and 0 <= col < track.nx):
                continue
            if track.blocked[row, col]:
                continue
            point = track.centre_of(row, col)
            if math.hypot(point[0] - mid[0], point[1] - mid[1]) > SEED_RADIUS_MM:
                continue
            if not _line_is_free(fan_grids[net_p], pad_p, point):
                continue
            if not _line_is_free(fan_grids[net_n], pad_n, point):
                continue
            reach_p = math.hypot(point[0] - pad_p[0], point[1] - pad_p[1])
            reach_n = math.hypot(point[0] - pad_n[0], point[1] - pad_n[1])
            onward = math.hypot(point[0] - toward[0], point[1] - toward[1])
            # Both legs pay for the corridor, so the run to the far end counts
            # twice, exactly as the two fans do.
            score = (reach_p + reach_n + 2.0 * onward
                     + SEED_BALANCE_WEIGHT * abs(reach_p - reach_n))
            candidate = (round(score, 6), row, col, (row, col))
            if best is None or candidate < best:
                best = candidate
    if best is not None:
        return best[3]
    # Nothing scored: fall back to the nearest free cell so a pair on a board
    # this heuristic cannot read still gets a chance, and the exact tests
    # decide whether the result is legal.
    return track.nearest_free(row0, col0, limit_cells=int(4.0 / GRID_MM))


def _attach_fans(nodes_p: list[dict], nodes_n: list[dict],
                 anchors: Sequence[tuple], fan_grids: dict[str, _Grid],
                 net_p: str, net_n: str, width: float, layer: str,
                 clearance: float) -> tuple[list[dict], list[dict]] | str:
    """Connect each pad to its end of the pair, out of the way of everything.

    The fan-out is not a straight line from the pad to the track. Measured on
    hydrate-coaster, a straight one crosses ``USB_DM_CONN`` where that net
    arrives at R4's other pad — the two ends of a 0402 are a millimetre apart
    and there is other copper between them. So each fan is its own search, at a
    single track's clearance, and the second leg is routed with the first leg's
    fan already on the board.
    """
    grid_p = fan_grids[net_p].clone()
    grid_n = fan_grids[net_n].clone()
    #: Each leg is an obstacle for the other, tracks first.
    for segments, grid in ((_nodes_to_segments(nodes_n, width, net_n), grid_p),
                           (_nodes_to_segments(nodes_p, width, net_p), grid_n)):
        for x0, y0, x1, y1, seg_layer, seg_width, _ in segments:
            if seg_layer != layer:
                continue
            grid.block(Obstacle("capsule", clearance, (layer,), "leg",
                                seg=(x0, y0, x1, y1), half_width=seg_width / 2),
                       width / 2)

    head_p, head_n = list(nodes_p), list(nodes_n)
    for index, (pad_p, pad_n, _) in enumerate(anchors):
        for leg, nodes, grid, other_grid in (
            ("p", head_p, grid_p, grid_n),
            ("n", head_n, grid_n, grid_p),
        ):
            pad = pad_p if leg == "p" else pad_n
            tip = nodes[0] if index == 0 else nodes[-1]
            if tip["layer"] != layer:
                return "the pair's end sits on a layer its pads are not on"
            path = _fan_path(grid, pad, (tip["x"], tip["y"]))
            if path is None:
                return (
                    f"no clear fan-out from the pad at "
                    f"({pad[0]:.2f}, {pad[1]:.2f}) to the pair"
                )
            # ``path`` runs pad -> tip inclusive. The tip already exists as a
            # node, so only the points before it are inserted — but the whole
            # polyline, tip included, is what the other leg has to avoid.
            body = path[:-1]
            points = body if index == 0 else list(reversed(body))
            new_nodes = [{"kind": "wire", "x": x, "y": y, "layer": layer}
                         for x, y in points]
            if index == 0:
                nodes[:0] = new_nodes
            else:
                nodes.extend(new_nodes)
            for a, b in zip(path, path[1:]):
                other_grid.block(
                    Obstacle("capsule", clearance, (layer,), "fan",
                             seg=(a[0], a[1], b[0], b[1]), half_width=width / 2),
                    width / 2)
    return head_p, head_n


def _fan_path(grid: _Grid, pad: tuple[float, float],
              tip: tuple[float, float]) -> list[tuple[float, float]] | None:
    """Pad centre to track tip, avoiding everything that is not this net.

    Returns the polyline from the pad to the tip, both included. A straight
    line is tried first, because that is what a fan-out should be and usually
    is; the search is for the cases where it is not — measured on
    hydrate-coaster, a straight fan from R4's outer pad crosses ``USB_DM_CONN``
    arriving at R4's inner pad, a millimetre away.
    """
    start = grid.cell_of(*pad)
    goal = grid.cell_of(*tip)
    if not grid.blocked[goal] and _line_is_free(grid, pad, tip):
        return [pad, tip]
    seed = grid.nearest_free(*start, limit_cells=int(1.5 / GRID_MM))
    target = grid.nearest_free(*goal, limit_cells=int(1.5 / GRID_MM))
    if seed is None or target is None:
        return None
    path = _astar([grid], [grid], (0, *seed), (0, *target))
    if path is None:
        return None
    points = _simplify([grid.centre_of(r, c) for _, r, c in path])
    return [pad] + points + [tip]


def _line_is_free(grid: _Grid, a: tuple[float, float],
                  b: tuple[float, float]) -> bool:
    """Sample the straight fan at half a cell; the exact test comes later."""
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    steps = max(1, int(length / (GRID_MM / 2)))
    for i in range(steps + 1):
        t = i / steps
        row, col = grid.cell_of(a[0] + (b[0] - a[0]) * t,
                                a[1] + (b[1] - a[1]) * t)
        if grid.blocked[row, col]:
            return False
    return True


def _build_pair(runs: Sequence[tuple[str, list]], side: float,
                swap: int | None, pitch: float, width: float,
                via_outer: float, via_drill: float, clearance: float,
                via_to_copper: float
                ) -> tuple[list[dict], list[dict]] | None:
    """Two coupled tracks from one centreline, optionally crossing over once.

    **Why a crossover is needed at all.** D+ and D- do not always leave one end
    on the same side they arrive at the other: on hydrate-coaster the series
    resistors put D+ left of D- and the RP2040's pins put D+ right of D-, so
    *some* swap is unavoidable. Measured, that is not a hydrate-coaster quirk
    — it is what `usb-c-data`'s R3/R4 order plus the RP2040 pinout produce on
    every board built from those two blocks.

    **How it swaps.** At a layer change, and only there. The pair is already
    putting one via on each leg at that point; the swap staggers them along the
    direction of travel instead of stacking them across it, so each leg makes
    exactly one equal-length diagonal and the two diagonals are on *different*
    layers. One via per leg, congruent lengths — no skew is added, and the only
    cost is the millimetre of the crossover itself, where the gap is not
    constant. That is the textbook pattern, and it is the reason this pass
    changes layers rather than refusing.
    """
    half = pitch / 2
    if swap is not None and (swap < 0 or swap >= len(runs) - 1):
        return None

    def unit(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= _EPS_MM:
            return None
        return (dx / length, dy / length)

    # Per-run offset polylines, in the current side assignment.
    sides = []
    current = side
    for index in range(len(runs)):
        sides.append(current)
        if swap is not None and index == swap:
            current = -current

    offsets_p = [[name, _offset_polyline(points, sides[i] * half)]
                 for i, (name, points) in enumerate(runs)]
    offsets_n = [[name, _offset_polyline(points, -sides[i] * half)]
                 for i, (name, points) in enumerate(runs)]
    crossed = False

    for index in range(len(runs) - 1):
        points_in = runs[index][1]
        junction = points_in[-1]
        incoming = unit(points_in[-2], junction)
        if incoming is None:
            return None
        normal = (-incoming[1], incoming[0])
        offset = sides[index] * half
        if swap is None or index != swap:
            # A stacked via pair: both legs change layer on the same
            # cross-section, using the *incoming* normal. Using the outgoing
            # one instead swings both legs around a 90-degree corner at the
            # junction and walks one of them into the other's via — measured
            # on hydrate-coaster at -0.24mm.
            via_p = (junction[0] + offset * normal[0],
                     junction[1] + offset * normal[1])
            via_n = (junction[0] - offset * normal[0],
                     junction[1] - offset * normal[1])
            offsets_p[index][1][-1] = via_p
            offsets_n[index][1][-1] = via_n
            offsets_p[index + 1][1][0] = via_p
            offsets_n[index + 1][1][0] = via_n
            continue
        # The crossover. Both legs still change layer exactly once here; the
        # two vias are staggered along the run instead of stacked across it,
        # which is what lets the legs pass each other on different layers.
        #
        # How far apart they have to be is geometry, not taste. The tight spot
        # is P's via against N's diagonal: with the vias ``d`` apart along the
        # run and ``a = pitch`` across it, that distance is a*d/hypot(d, a),
        # and it has to clear ``need``. Solving for d and taking half again as
        # much for margin:
        need = max(via_outer / 2 + width / 2 + clearance,
                   via_drill / 2 + width / 2 + via_to_copper)
        across = pitch
        if across <= need:
            return None
        stagger = 1.5 * need * across / math.sqrt(across * across - need * need)
        run_in = math.hypot(junction[0] - points_in[-2][0],
                            junction[1] - points_in[-2][1])
        if run_in < stagger + 0.2:
            return None
        near = (junction[0] - stagger * incoming[0],
                junction[1] - stagger * incoming[1])
        p_via = (near[0] + offset * normal[0], near[1] + offset * normal[1])
        p_land = (junction[0] - offset * normal[0], junction[1] - offset * normal[1])
        n_start = (near[0] - offset * normal[0], near[1] - offset * normal[1])
        n_via = (junction[0] + offset * normal[0], junction[1] + offset * normal[1])
        offsets_p[index][1][-1] = p_via
        offsets_n[index][1][-1] = n_start
        offsets_n[index][1].append(n_via)
        offsets_p[index + 1][1][0] = p_land
        offsets_n[index + 1][1][0] = n_via
        crossed = True

    nodes_p = _nodes_from_runs(offsets_p, via_outer, via_drill)
    nodes_n = _nodes_from_runs(offsets_n, via_outer, via_drill)
    if not nodes_p or not nodes_n:
        return None
    if swap is not None and not crossed:
        return None
    return nodes_p, nodes_n


def _nodes_from_runs(offsets: Sequence[Sequence], via_outer: float,
                     via_drill: float) -> list[dict]:
    """Offset polylines to ordered route nodes, with a via at every layer
    change. Both the stacked and the staggered transition put each leg's via
    at the last point it reaches on the old layer, so this stays uniform."""
    nodes: list[dict] = []
    for index, (name, points) in enumerate(offsets):
        if index > 0:
            at = (nodes[-1]["x"], nodes[-1]["y"])
            nodes.append({
                "kind": "via", "x": at[0], "y": at[1],
                "from_layer": offsets[index - 1][0], "to_layer": name,
                "outer": via_outer, "drill": via_drill,
            })
            # The wire has to restart *at the via* on the new layer. Without
            # this, a crossover's first point on the new layer is the far end
            # of its diagonal and `@tscircuit/checks` reports "Via in trace is
            # misaligned" — an error, on the board, from a route that is
            # otherwise fine.
            nodes.append({"kind": "wire", "x": at[0], "y": at[1],
                          "layer": name})
        for x, y in points:
            if nodes and nodes[-1]["kind"] == "wire" \
                    and abs(nodes[-1]["x"] - x) <= _EPS_MM \
                    and abs(nodes[-1]["y"] - y) <= _EPS_MM \
                    and nodes[-1]["layer"] == name:
                continue
            nodes.append({"kind": "wire", "x": x, "y": y, "layer": name})
    return nodes


def _thinned(points: Sequence[tuple[float, float]],
             limit: int) -> list[tuple[float, float]]:
    """Every nth vertex of a long outline, for the raster only.

    The board outline arrives as a thousand-point circle and rasterising every
    edge over the search box costs more than the search does. The exact
    accept/refuse test always uses the full outline; this is a search aid.
    """
    if len(points) <= limit:
        return list(points)
    stride = math.ceil(len(points) / limit)
    return [points[i] for i in range(0, len(points), stride)]


def _trace_route(nodes: Sequence[dict], width: float, start_port: str,
                 end_port: str) -> list[dict]:
    """Route nodes as circuit-json ``pcb_trace.route`` points."""
    route: list[dict] = []
    for node in nodes:
        if node["kind"] == "via":
            route.append({
                "route_type": "via",
                "x": round(node["x"], 6),
                "y": round(node["y"], 6),
                "from_layer": node["from_layer"],
                "to_layer": node["to_layer"],
                "via_diameter": node["outer"],
                "via_hole_diameter": node["drill"],
            })
            continue
        route.append({
            "route_type": "wire",
            "x": round(node["x"], 6),
            "y": round(node["y"], 6),
            "width": width,
            "layer": node["layer"],
        })
    if route:
        route[0]["start_pcb_port_id"] = start_port
        route[-1]["end_pcb_port_id"] = end_port
    return route


def _via_elements(nodes: Sequence[dict], trace_id: str, net: str,
                  subcircuit_id: str | None, index: int) -> list[dict]:
    """The ``pcb_via`` rows a layer-changing pair route needs.

    Ids are derived from the trace they belong to and their position in it, so
    two builds of the same board name the same via the same thing — a random
    id here would undo ledger #35 one element at a time.
    """
    out: list[dict] = []
    order = 0
    for node in nodes:
        if node["kind"] != "via":
            continue
        element = {
            "type": "pcb_via",
            "pcb_via_id": f"pcb_via_diffpair_{index}_{order}",
            "pcb_trace_id": trace_id,
            "x": round(node["x"], 6),
            "y": round(node["y"], 6),
            "hole_diameter": node["drill"],
            "outer_diameter": node["outer"],
            "layers": [node["from_layer"], node["to_layer"]],
            "from_layer": node["from_layer"],
            "to_layer": node["to_layer"],
        }
        if subcircuit_id:
            element["subcircuit_id"] = subcircuit_id
        element["subcircuit_connectivity_map_key"] = net
        out.append(element)
        order += 1
    return out


def _route_length(route: Sequence[dict]) -> float:
    """The length tscircuit records: wire runs, plus 1.6mm per via for the
    barrel (``getTraceLength`` in the CLI bundle)."""
    total = 0.0
    for a, b in zip(route, route[1:]):
        if a.get("route_type") == "via":
            total += 1.6
            continue
        if b.get("route_type") == "wire":
            total += math.hypot(b["x"] - a["x"], b["y"] - a["y"])
    return total


def _pair_ends(ports_p: Sequence[dict],
               ports_n: Sequence[dict]) -> list[tuple[dict, dict]]:
    """Which end of P belongs with which end of N — whichever pairing puts the
    two pads of an end closest together."""
    def gap(a: dict, b: dict) -> float:
        return math.hypot((_f(a.get("x"), 0.0) or 0.0) - (_f(b.get("x"), 0.0) or 0.0),
                          (_f(a.get("y"), 0.0) or 0.0) - (_f(b.get("y"), 0.0) or 0.0))

    straight = gap(ports_p[0], ports_n[0]) + gap(ports_p[1], ports_n[1])
    crossed = gap(ports_p[0], ports_n[1]) + gap(ports_p[1], ports_n[0])
    if crossed < straight:
        return [(ports_p[0], ports_n[1]), (ports_p[1], ports_n[0])]
    return [(ports_p[0], ports_n[0]), (ports_p[1], ports_n[1])]


def route_diff_pairs(path: Path, profile: Any,
                     grade: "Callable[[list, Path], list[dict]] | None" = None
                     ) -> DiffPairResult:
    """Re-route every two-terminal differential pair in ``path`` as a pair.

    Idempotent in effect and a no-op on a board with no pair: the file is only
    rewritten when copper actually moved. Never raises — a repair pass that
    takes the build down is worse than a board with an uncoupled pair.

    ``grade`` is how the pass is held to the same ruler as the board. Given the
    element list and a path to read it from, it returns the pipeline's own
    findings; the rewrite is kept only when it adds no error the board did not
    already have. **This is not belt and braces — it is the check that caught
    the first version.** That one measured every clearance rule it knew about,
    passed its own test, and produced a board with eight `dfm_hole_clearance`
    errors and five KiCad hole-clearance violations: the rule it did not know
    about was one via's pad against another via's drill. A repair pass graded
    only by its own model will always be exactly as right as its model.
    """
    result = DiffPairResult()
    if _np is None:
        result.notes.append(
            "the differential-pair route pass needs numpy and it is not "
            "installed; the pair is reported but not routed"
        )
        return result
    try:
        original = path.read_text(encoding="utf-8")
        elements = json.loads(original)
    except (OSError, ValueError) as exc:
        result.notes.append(f"could not read {path.name}: {exc}")
        return result
    if not isinstance(elements, list):
        return result

    try:
        return _route_diff_pairs(path, elements, profile, result, grade)
    except Exception as exc:  # noqa: BLE001
        result.notes.append(
            f"the differential-pair route pass aborted "
            f"({type(exc).__name__}: {exc}); the circuit.json was left exactly "
            "as the toolchain produced it"
        )
        try:
            path.write_text(original, encoding="utf-8")
        except OSError:
            pass
        return DiffPairResult(notes=result.notes)


def _route_diff_pairs(path: Path, elements: list, profile: Any,
                      result: DiffPairResult,
                      grade: "Callable[[list, Path], list[dict]] | None" = None
                      ) -> DiffPairResult:
    board = _Board(elements)
    names = _named_nets(board)
    pairs = find_pairs(names)
    if not pairs:
        return result

    outline = _board_outline(board)
    coupling_widths = 4.0  # the same budget verifylib.netclass grades against
    clearance_floor = float(getattr(profile, "min_clearance_mm", 0.10))
    changed_traces: dict[str, list[dict]] = {}
    dead_trace_ids: set[str] = set()
    new_vias: list[dict] = []

    for name_p, name_n in pairs:
        outcome = PairOutcome(net_p=name_p, net_n=name_n, status="skipped")
        result.pairs.append(outcome)
        key_p, key_n = names[name_p], names[name_n]
        ports_p = board.pcb_ports_of_net(key_p)
        ports_n = board.pcb_ports_of_net(key_n)
        if len(ports_p) != 2 or len(ports_n) != 2:
            outcome.reason = (
                f"{name_p} has {len(ports_p)} pads and {name_n} has "
                f"{len(ports_n)}; this pass routes two-terminal pairs only, so "
                "a multi-drop pair keeps the autorouter's copper"
            )
            continue
        traces_p = [t for t in board.by_type.get("pcb_trace", [])
                    if board.trace_net_key(t) == key_p]
        traces_n = [t for t in board.by_type.get("pcb_trace", [])
                    if board.trace_net_key(t) == key_n]
        if len(traces_p) != 1 or len(traces_n) != 1:
            outcome.reason = (
                f"{name_p} is drawn by {len(traces_p)} trace(s) and {name_n} by "
                f"{len(traces_n)}; expected exactly one each"
            )
            continue

        segs_p_before = [(*s[:5], s[5], key_p) for s in _route_segments(traces_p[0])]
        segs_n_before = [(*s[:5], s[5], key_n) for s in _route_segments(traces_n[0])]
        before = _measure(segs_p_before, segs_n_before, coupling_widths)
        outcome.before = before
        if not segs_p_before or not segs_n_before:
            outcome.reason = "one half of the pair has no copper to compare against"
            continue

        layers = {s[4] for s in segs_p_before} | {s[4] for s in segs_n_before}
        pad_layers = {_pad_layer(p) for p in ports_p + ports_n}
        pad_layers.discard(None)
        if len(pad_layers) != 1:
            outcome.reason = (
                f"the pair's four pads sit on {sorted(pad_layers)}; a single-layer "
                "pair needs them all on one layer"
            )
            continue
        layer = next(iter(pad_layers))  # type: ignore[arg-type]
        width = min([s[5] for s in segs_p_before + segs_n_before if s[5] > 0]
                    or [0.15])

        skip = {str(traces_p[0].get("pcb_trace_id") or ""),
                str(traces_n[0].get("pcb_trace_id") or "")}
        obstacles = collect_obstacles(board, profile, skip)
        before.worst_clearance_mm, _ = _worst_clearance(
            segs_p_before + segs_n_before, obstacles, outline,
            float(getattr(profile, "min_edge_clearance_mm", 0.2)),
        )

        ends = _pair_ends(ports_p, ports_n)
        nodes_p, nodes_n, pitch_mm, reason = _route_one_pair(
            board, obstacles, outline, profile, ends, layer, width, key_p, key_n
        )
        if not nodes_p or not nodes_n:
            outcome.status = "refused"
            outcome.reason = reason or "no pair route was found"
            continue

        segs_p = _nodes_to_segments(nodes_p, width, key_p)
        segs_n = _nodes_to_segments(nodes_n, width, key_n)
        after = _measure(segs_p, segs_n, coupling_widths)
        via_outer = max(float(getattr(profile, "min_via_diameter_mm", 0.3)),
                        float(getattr(profile, "warn_via_diameter_mm", 0.45)))
        after.worst_clearance_mm, _ = _worst_clearance(
            segs_p + segs_n
            + _via_segments(nodes_p, board.layers, via_outer, key_p)
            + _via_segments(nodes_n, board.layers, via_outer, key_n),
            obstacles, outline,
            float(getattr(profile, "min_edge_clearance_mm", 0.2)),
        )
        outcome.after = after
        outcome.layer = layer
        outcome.pitch_mm = pitch_mm

        if after.coupled_fraction <= before.coupled_fraction:
            outcome.status = "refused"
            outcome.reason = (
                f"the pair route is no more coupled than the router's "
                f"({after.coupled_fraction:.0%} against "
                f"{before.coupled_fraction:.0%})"
            )
            continue
        if after.skew_mm > before.skew_mm + 1e-6:
            outcome.status = "refused"
            outcome.reason = (
                f"the pair route skews more than the router's "
                f"({after.skew_mm:.2f}mm against {before.skew_mm:.2f}mm)"
            )
            continue
        if (after.worst_clearance_mm or 0.0) < clearance_floor - 1e-9:
            outcome.status = "refused"
            outcome.reason = (
                f"the pair route leaves {after.worst_clearance_mm:.4f}mm of "
                f"clearance, under the {clearance_floor:.2f}mm floor"
            )
            continue

        start_p = str(ends[0][0].get("pcb_port_id") or "")
        end_p = str(ends[1][0].get("pcb_port_id") or "")
        start_n = str(ends[0][1].get("pcb_port_id") or "")
        end_n = str(ends[1][1].get("pcb_port_id") or "")
        trace_p_id = str(traces_p[0].get("pcb_trace_id"))
        trace_n_id = str(traces_n[0].get("pcb_trace_id"))
        changed_traces[trace_p_id] = _trace_route(nodes_p, width, start_p, end_p)
        changed_traces[trace_n_id] = _trace_route(nodes_n, width, start_n, end_n)
        new_vias.extend(_via_elements(
            nodes_p, trace_p_id, key_p,
            traces_p[0].get("subcircuit_id"), len(new_vias)))
        new_vias.extend(_via_elements(
            nodes_n, trace_n_id, key_n,
            traces_n[0].get("subcircuit_id"), len(new_vias)))
        dead_trace_ids |= skip
        outcome.status = "routed"

    if not changed_traces:
        return result

    rewritten: list[Any] = []
    for element in elements:
        if not isinstance(element, dict):
            rewritten.append(element)
            continue
        if element.get("type") == "pcb_via" and \
                str(element.get("pcb_trace_id") or "") in dead_trace_ids:
            result.vias_removed += 1
            continue  # the router's vias went with the copper they belonged to
        if element.get("type") == "pcb_trace":
            trace_id = str(element.get("pcb_trace_id") or "")
            if trace_id in changed_traces:
                element = dict(element)
                element["route"] = changed_traces[trace_id]
                element["trace_length"] = round(_route_length(element["route"]), 6)
        rewritten.append(element)
    rewritten.extend(new_vias)
    result.vias_added = len(new_vias)

    text = json.dumps(rewritten, indent=2)
    if json.loads(text) != rewritten:
        result.notes.append(
            "the differential-pair rewrite did not round-trip; the circuit.json "
            "was left exactly as the toolchain produced it"
        )
        return DiffPairResult(notes=result.notes)

    if grade is not None:
        added = _new_errors(elements, rewritten, path, grade)
        if added:
            for pair in result.pairs:
                if pair.status == "routed":
                    pair.status = "refused"
                    pair.reason = (
                        "the pipeline's own gate found "
                        f"{len(added)} new blocking finding(s) in the paired "
                        f"copper that the router's did not have: "
                        + "; ".join(sorted(added)[:3])
                    )
            return result

    path.write_text(text, encoding="utf-8")
    return result


def _new_errors(before: list, after: list, path: Path,
                grade: "Callable[[list, Path], list[dict]]") -> list[str]:
    """Blocking findings the paired copper has and the router's copper did not.

    Both sides are graded the same way, in the same run, so a check that is
    noisy on this board cancels out and only what the rewrite *introduced*
    survives.
    """
    candidate = path.with_name(path.name + ".diffpair-candidate")
    try:
        candidate.write_text(json.dumps(after, indent=2), encoding="utf-8")
        was = _error_bag(grade(before, path))
        now = _error_bag(grade(after, candidate))
    except Exception as exc:  # noqa: BLE001
        # A grader that cannot run is not permission to ship ungraded copper.
        return [f"the gate could not be run ({type(exc).__name__}: {exc})"]
    finally:
        try:
            candidate.unlink()
        except OSError:
            pass
    added: list[str] = []
    for key, count in now.items():
        if count > was.get(key, 0):
            added.append(f"{key} x{count - was.get(key, 0)}")
    return added


def _error_bag(findings: Sequence[dict]) -> dict[str, int]:
    bag: dict[str, int] = {}
    for finding_ in findings or []:
        if str(finding_.get("severity")) != "error":
            continue
        kind = str(finding_.get("kind") or "")
        bag[kind] = bag.get(kind, 0) + 1
    return bag
