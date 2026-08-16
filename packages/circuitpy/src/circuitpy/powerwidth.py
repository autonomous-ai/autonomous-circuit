"""Widen every power and ground trace to the fab profile's power floor.

**The defect this closes.** EE review 2026-08-15, finding 4: *"the main power
nets, 5V and 3V3, are currently the same width as a signal, 0.2 mm. For a
keyboard's current that still works, but it should be 0.5–1.0 mm depending on
current, trace length and copper thickness, to reduce voltage drop and improve
reliability."* Until now we only *reported* it (`dfm_power_trace_width`,
ledger #31). Measured on the 2026-08-16 builds, all three boards:

    terminal-keyboard   GND, V3_3, V5   0.2 mm
    harness-puck        4 rails         0.2 mm
    hydrate-coaster     3 rails         0.2 mm

The autorouter has one width for the whole board. It has no concept of a net
class, so a rail carrying the whole board's current gets the same copper as a
button that carries a gate charge.

**Why this runs after the router and not inside it.** The same reason the pair
pass does (`diffpair.py`): the shipped autorouter takes a single trace width
and there is no hook to vary it per net. The difference is that widening is a
far easier problem than routing — the path is already legal, and a wider path
along the same centreline is legal too as long as the extra copper fits in the
gap the router already left. So this pass never moves a trace. It only asks,
segment by segment, *how much wider can this exact copper get*, and takes it.

**How wide.** For one segment the answer is exact and needs no search:

    max_half_width = min over every obstacle on this layer, other net, of
                     (distance from obstacle edge to the centreline − its own
                      clearance rule)

so `max_width = 2 × that`, capped at the profile's power floor. One pass over
the obstacles per segment, no candidate ladder, no raster.

Two details make it correct rather than approximately correct:

- **A route point's width is shared by two segments.** Our own reader takes a
  segment's width as `min` of its endpoints (`diffpair._route_segments`), which
  is the conservative reading of a format that stores width per point, so each
  point is assigned the *minimum* of the widths its adjacent segments allow. A
  trace therefore necks down as it approaches a tight pass and opens out again
  after, which is what a human would draw.

- **Two rails competing for the same gap.** Every other trace is an obstacle
  measured at the width it has *now* — but if that trace is also a widening
  candidate it is about to grow into the same gap, and both would be measured
  against a neighbour that no longer exists at that size. Candidate traces are
  therefore inflated to the target width *before* anything is measured against
  them. That under-widens where two rails run side by side, which is the right
  direction to be wrong in.

Same-net copper is exempt from its own clearance, so a rail is never limited by
its own pads or its own other segments — a wide trace arriving at a small pad is
a connection, not a violation.

**What it will not do.** It refuses rather than regresses, and it is graded by
the pipeline's gate rather than by its own model — the rule that caught the
pair pass's first version. Any segment whose widening would add a blocking
finding the board did not already have is reverted, and the pass says so.
Vias on power nets are left alone: a via's barrel is drilled, and growing its
pad is a placement change rather than a width change.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from . import diffpair

#: Widths snap to this grid, so a packet does not carry 0.4173mm traces. A
#: fab builds any width; a reviewer reading a round number trusts it faster.
WIDTH_GRID_MM = 0.05

#: Do not rewrite a segment for less than this — a hundredth of a millimetre of
#: extra copper is churn in the diff and noise in the determinism hash.
MIN_GAIN_MM = 0.02

#: A segment that cannot reach the target somewhere along its length is cut
#: into pieces no longer than this, so the neck stays where the obstacle is.
#:
#: Without it, one tight pass necks the whole run: a route point's width is
#: shared by the two segments that meet at it, so a 20mm rail that squeezes
#: past a single via at its far end comes out 0.2mm end to end. Measured on
#: terminal-keyboard, splitting is the difference between 26% and most of the
#: run reaching the floor. 1mm is the coarsest cut that still lands the neck
#: inside the obstacle's own neighbourhood rather than a millimetre either
#: side of it.
SPLIT_MM = 1.0

#: Segments shorter than this are never split — a fan-out stub is already
#: local, and cutting it adds points to the artifact for no copper.
MIN_SPLIT_LENGTH_MM = 1.5

#: Same classifier as ``checks.power_width_warnings``, imported rather than
#: re-derived so the fix and the check can never disagree about what a rail is.
_POWER_NET_RE = re.compile(
    r"^(v(cc|dd|bus|in|out|sys|bat|\d.*)|.*_?\d+v\d*.*|\d+v\d*|pwr|power|vpp)$",
    re.I,
)
_GROUND_NET_RE = re.compile(r"^(gnd|agnd|dgnd|vss|ground)$", re.I)


# ---------------------------------------------------------------------------
# What came out
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NetWidening:
    """One rail, before and after, measured the way the check measures it."""

    name: str
    before_mm: float
    after_mm: float
    target_mm: float
    segments: int
    segments_widened: int
    #: Fraction of routed length that reaches the target. The narrowest point
    #: is what the check reports, but a rail that is 0.5mm for 95% of its run
    #: and necks to 0.28mm past one via is a different board from one that is
    #: 0.28mm throughout, and only this number tells them apart.
    length_at_target: float
    #: Why the narrowest point is as narrow as it is — the obstacle that set it.
    limiter: str | None = None

    @property
    def reached_target(self) -> bool:
        return self.after_mm >= self.target_mm - 1e-9


@dataclass(frozen=True)
class WidenResult:
    """What the pass did, in the shape the sidecar records."""

    ran: bool
    nets: tuple[NetWidening, ...] = ()
    kept: bool = True
    refusal: str | None = None
    elapsed_s: float = 0.0
    #: Blocking findings before and after, from the pipeline's own gate.
    blocking_before: int | None = None
    blocking_after: int | None = None

    def as_dict(self) -> dict:
        return {
            "ran": self.ran,
            "kept": self.kept,
            "refusal": self.refusal,
            "elapsed_s": round(self.elapsed_s, 3),
            "blocking_before": self.blocking_before,
            "blocking_after": self.blocking_after,
            "nets": [
                {
                    "name": n.name,
                    "before_mm": round(n.before_mm, 4),
                    "after_mm": round(n.after_mm, 4),
                    "target_mm": n.target_mm,
                    "segments": n.segments,
                    "segments_widened": n.segments_widened,
                    "length_at_target": round(n.length_at_target, 4),
                    "limiter": n.limiter,
                }
                for n in self.nets
            ],
        }

    def findings(self) -> list[dict]:
        """Info rows for the sidecar: what widened, what did not, and why.

        Reported even on success. A rail that reached 0.5mm for 40% of its run
        is a fact the reviewer needs, and a pass that only speaks up when it
        fails teaches nobody anything.
        """
        if not self.ran:
            return []
        if not self.kept:
            return [{
                "kind": "power_width_refused",
                "severity": "info",
                "part": "power nets",
                "detail": (
                    f"widening the power rails was reverted: {self.refusal}. "
                    "The board keeps the router's copper"
                ),
            }]
        out: list[dict] = []
        for net in self.nets:
            if not net.segments_widened:
                continue
            pct = round(net.length_at_target * 100)
            detail = (
                f"{net.name}: {net.segments_widened} of {net.segments} segments "
                f"widened, {pct}% of the run now at the {net.target_mm:g}mm power "
                f"floor. Narrowest point {net.before_mm:g}mm -> {net.after_mm:g}mm"
            )
            if net.limiter and not net.reached_target:
                detail += f", set by {net.limiter}"
            out.append({
                "kind": "power_width_widened",
                "severity": "info",
                "part": net.name,
                "detail": detail,
            })
        return out


# ---------------------------------------------------------------------------
# Which traces are rails
# ---------------------------------------------------------------------------


def _rail_source_nets(elements: Sequence[dict]) -> dict[str, str]:
    """source_net_id -> name, for power and ground nets that are not poured.

    Mirrors ``checks.power_width_warnings`` exactly, including the pour
    exemption: where ground is poured the pour is the current path and its
    routed stubs are not what carries the rail, so widening them buys nothing
    and costs routing space.
    """
    named: dict[str, str] = {}
    poured: set[str] = set()
    for element in elements:
        if not isinstance(element, dict):
            continue
        etype = element.get("type")
        if etype == "source_net":
            nid = str(element.get("source_net_id") or "")
            name = str(element.get("name") or "")
            if not nid or not name:
                continue
            if (
                element.get("is_power")
                or element.get("is_ground")
                or _POWER_NET_RE.match(name)
                or _GROUND_NET_RE.match(name)
            ):
                named[nid] = name
        elif etype == "pcb_copper_pour":
            nid = element.get("source_net_id")
            if isinstance(nid, str):
                poured.add(nid)
    return {nid: name for nid, name in named.items() if nid not in poured}


def _rail_traces(elements: Sequence[dict]) -> dict[int, tuple[str, ...]]:
    """index of a pcb_trace in ``elements`` -> the rail names it carries."""
    rails = _rail_source_nets(elements)
    by_trace: dict[str, tuple[str, ...]] = {}
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "source_trace":
            continue
        tid = str(element.get("source_trace_id") or "")
        names = tuple(
            rails[nid]
            for nid in (element.get("connected_source_net_ids") or [])
            if isinstance(nid, str) and nid in rails
        )
        if tid and names:
            by_trace[tid] = names
    out: dict[int, tuple[str, ...]] = {}
    for index, element in enumerate(elements):
        if not isinstance(element, dict) or element.get("type") != "pcb_trace":
            continue
        names = by_trace.get(str(element.get("source_trace_id") or ""))
        if names:
            out[index] = names
    return out


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------


def _segment_limit(seg: tuple[float, float, float, float], layer: str,
                   net: str | None, obstacles: Sequence[diffpair.Obstacle],
                   outline: Sequence[tuple[float, float]] | None,
                   edge_clearance: float,
                   cap: float) -> tuple[float, str | None]:
    """The widest this one segment can be, and what stops it being wider.

    Exact, against real shapes. The bounding-box prefilter is a speed aid only:
    an obstacle whose box is further than the cap could ever reach cannot be
    the limiter, and skipping it changes no answer.
    """
    reach = cap / 2 + 1.0
    x0, y0, x1, y1 = seg
    sminx, smaxx = (x0, x1) if x0 <= x1 else (x1, x0)
    sminy, smaxy = (y0, y1) if y0 <= y1 else (y1, y0)

    best = cap
    limiter: str | None = None
    for obstacle in obstacles:
        if layer not in obstacle.layers:
            continue
        if obstacle.net is not None and net and obstacle.net == net:
            continue
        bminx, bminy, bmaxx, bmaxy = obstacle.bounds()
        if (bminx > smaxx + reach or bmaxx < sminx - reach
                or bminy > smaxy + reach or bmaxy < sminy - reach):
            continue
        allowed = 2.0 * (obstacle.distance_to_segment(seg) - obstacle.clearance)
        if allowed < best:
            best = allowed
            limiter = obstacle.label
    if outline:
        for i in range(len(outline)):
            ax, ay = outline[i]
            bx, by = outline[(i + 1) % len(outline)]
            allowed = 2.0 * (
                diffpair._seg_seg_distance(seg, (ax, ay, bx, by)) - edge_clearance
            )
            if allowed < best:
                best = allowed
                limiter = "the board edge"
    return best, limiter


def _snap(width: float) -> float:
    """Down to the grid, never up — rounding up invents clearance we measured
    we did not have."""
    return math.floor(width / WIDTH_GRID_MM + 1e-9) * WIDTH_GRID_MM


def _inflate_candidates(obstacles: Sequence[diffpair.Obstacle],
                        candidate_labels: set[str],
                        target: float) -> list[diffpair.Obstacle]:
    """Grow every candidate trace to the target before anything measures it.

    Two rails running side by side would otherwise each measure the other at
    its current width, both claim the gap, and both be right about a board that
    no longer exists once the other one grows.
    """
    out: list[diffpair.Obstacle] = []
    for obstacle in obstacles:
        if (obstacle.kind == "capsule" and obstacle.label in candidate_labels
                and obstacle.half_width < target / 2):
            out.append(replace(obstacle, half_width=target / 2))
        else:
            out.append(obstacle)
    return out


def _chunks(x0: float, y0: float, x1: float, y1: float) -> int:
    """How many pieces to cut this segment into.

    Every long segment is cut, not only the ones that hit something. A
    segment's *own* allowance is not what determines whether it necks: width
    lives on the shared route point, so a 20mm run that clears everything
    still gets pulled down to whatever its neighbour could manage. Cutting
    unconditionally bounds how far a neck can spread to one chunk.
    """
    length = math.hypot(x1 - x0, y1 - y0)
    if length < MIN_SPLIT_LENGTH_MM:
        return 1
    return max(1, int(math.ceil(length / SPLIT_MM)))


def _widen_route(
    route: Sequence[dict],
    net: str | None,
    obstacles: Sequence[diffpair.Obstacle],
    outline: Sequence[tuple[float, float]] | None,
    edge_clearance: float,
    target: float,
) -> tuple[list[dict], list[tuple[float, float, float, str | None]]]:
    """Rewrite one trace's route, wider where the board allows it.

    Returns the new route and, for every piece of copper in it,
    ``(width, width_before, length, limiter)`` — measured off the route that
    was actually written rather than off the intent, because the two disagree
    wherever a point is shared by a wide piece and a narrow one.

    Non-wire points (vias) and layer changes pass through untouched and break
    the chain: they are where the copper stops being one continuous run.
    """
    out: list[dict] = []
    measured: list[tuple[float, float, float, str | None]] = []

    # A run is a maximal stretch of consecutive wire points on one layer.
    runs: list[list[dict]] = []
    current: list[dict] = []
    for point in route:
        wire = (
            isinstance(point, dict)
            and point.get("route_type") == "wire"
            and diffpair._f(point.get("x")) is not None
            and diffpair._f(point.get("y")) is not None
        )
        if not wire:
            if current:
                runs.append(current)
                current = []
            runs.append([point])          # passthrough, marked by length 1
            continue
        if current and str(current[-1].get("layer") or "top") != str(
                point.get("layer") or "top"):
            runs.append(current)
            current = []
        current.append(point)
    if current:
        runs.append(current)

    for run in runs:
        if len(run) == 1 and run[0].get("route_type") != "wire":
            out.append(run[0])
            continue
        if len(run) < 2:
            out.extend(run)
            continue

        layer = str(run[0].get("layer") or "top")

        # Cut the run into pieces. Each piece knows where it ends, how wide it
        # may be, and what stopped it being wider. `end` is the original route
        # point when the piece finishes on one, and None for a boundary this
        # pass invented.
        pieces: list[dict] = []
        for k in range(len(run) - 1):
            a, b = run[k], run[k + 1]
            x0, y0 = float(a["x"]), float(a["y"])
            x1, y1 = float(b["x"]), float(b["y"])
            # The copper that is already here. This pass may only add to it:
            # the router's own width was legal where it stands, and anything
            # this pass computes lower than it is a measurement of a rule the
            # existing copper is exempt from, not a reason to cut it back.
            floor = min(
                diffpair._f(a.get("width"), 0.15) or 0.15,
                diffpair._f(b.get("width"), 0.15) or 0.15,
            )
            if abs(x1 - x0) < 1e-9 and abs(y1 - y0) < 1e-9:
                pieces.append({"end": b, "x": x1, "y": y1, "allowed": target,
                               "length": 0.0, "limiter": None, "floor": floor})
                continue
            count = _chunks(x0, y0, x1, y1)
            for c in range(count):
                t0, t1 = c / count, (c + 1) / count
                cx0, cy0 = x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0
                cx1, cy1 = x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1
                allowed, piece_limiter = _segment_limit(
                    (cx0, cy0, cx1, cy1), layer, net, obstacles, outline,
                    edge_clearance, target,
                )
                pieces.append({
                    "end": b if c == count - 1 else None,
                    "x": cx1, "y": cy1,
                    "allowed": allowed,
                    "length": math.hypot(cx1 - cx0, cy1 - cy0),
                    "limiter": piece_limiter,
                    "floor": floor,
                })

        # A point's width is the min of the pieces meeting at it — our own
        # reader takes a piece's width as the min of its endpoints, so this is
        # the assignment that makes the measurement and the copper agree.
        # Each point is also held at or above the widest floor of the pieces
        # meeting there, which is what makes "never narrows" true of the
        # copper rather than only of the intent: a piece's realised width is
        # the min of its two endpoints, so both have to clear its floor.
        allowed_at = []
        for i in range(len(pieces) + 1):
            touching = [pieces[j] for j in (i - 1, i) if 0 <= j < len(pieces)]
            allowed_at.append(max(
                _snap(min(min(p["allowed"] for p in touching), target)),
                max(p["floor"] for p in touching),
            ))

        # Emit. An original point keeps every field it had; an invented
        # boundary copies geometry only — a port id on an invented point would
        # join copper to a pad that is somewhere else. Width never goes down:
        # this pass widens or leaves alone, it does not narrow.
        emitted: list[dict] = []
        #: Parallel to `emitted`: whether this pass invented the point. Tracked
        #: explicitly, because every emitted point is a *copy* of its source —
        #: the first version tested `id(point) in {id(p) for p in run}` and got
        #: False for all of them, so the merge below treated real corners as
        #: removable and deleted them. Two segments meeting at a corner then
        #: became one straight segment across it, and the trace cut through
        #: whatever the corner was going round: measured on hydrate-coaster,
        #: a rail ended up **-0.275mm** from another net, i.e. shorted.
        invented: list[bool] = []
        first = dict(run[0])
        first["width"] = round(
            max(allowed_at[0], diffpair._f(run[0].get("width"), 0.15) or 0.15), 4
        )
        emitted.append(first)
        invented.append(False)
        for i, piece in enumerate(pieces):
            existing = diffpair._f((piece["end"] or {}).get("width"), 0.0) or 0.0
            width = round(max(allowed_at[i + 1], existing), 4)
            if piece["end"] is not None:
                point = dict(piece["end"])
                point["width"] = width
            else:
                point = {
                    "route_type": "wire", "layer": layer, "width": width,
                    "x": round(piece["x"], 6), "y": round(piece["y"], 6),
                }
            emitted.append(point)
            invented.append(piece["end"] is None)

        # Merge back. Splitting is a measuring device, not a design decision:
        # an invented point between two pieces of equal width describes copper
        # a single point already describes, and leaving hundreds of them in
        # would bloat the artifact, the diff and the gerbers for nothing.
        #
        # Only invented points may be dropped. An original point is a corner or
        # carries a port id, and both are load-bearing — the invented ones are
        # collinear with their neighbours by construction, so removing one
        # changes no geometry at all.
        merged: list[dict] = [emitted[0]]
        for i in range(1, len(emitted) - 1):
            point = emitted[i]
            if (invented[i]
                    and point.get("width") == merged[-1].get("width")
                    and point.get("width") == emitted[i + 1].get("width")):
                continue
            merged.append(point)
        if len(emitted) > 1:
            merged.append(emitted[-1])
        out.extend(merged)

        # Measure what was written, not what was intended.
        narrowest_before = min(
            diffpair._f(p.get("width"), 0.15) or 0.15 for p in run
        )
        for i, piece in enumerate(pieces):
            if piece["length"] <= 0:
                continue
            wa = diffpair._f(emitted[i].get("width"), 0.15) or 0.15
            wb = diffpair._f(emitted[i + 1].get("width"), 0.15) or 0.15
            measured.append((
                min(wa, wb), narrowest_before, piece["length"], piece["limiter"],
            ))

    return out, measured


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def widen_power_traces(
    circuit_json_path: Path,
    profile: Any,
    grade: Callable[[list, Path], list[dict]] | None = None,
) -> WidenResult:
    """Widen the rails in place. Returns what happened; never raises.

    ``grade`` is the pipeline's own gate. When supplied, the rewrite is kept
    only if it adds no blocking finding the board did not already have — a
    pass graded by its own model is a pass that grades itself generously.
    """
    import time

    started = time.monotonic()
    try:
        elements = json.loads(Path(circuit_json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WidenResult(ran=False, refusal="circuit.json unreadable")
    if not isinstance(elements, list):
        return WidenResult(ran=False, refusal="circuit.json is not an element array")

    target = float(getattr(profile, "warn_power_trace_mm", 0.5) or 0.5)
    rail_traces = _rail_traces(elements)
    if not rail_traces:
        return WidenResult(ran=False, refusal="no routed power or ground net")

    board = diffpair._Board(elements)
    outline = diffpair._board_outline(board)
    edge_clearance = float(getattr(profile, "min_edge_clearance_mm", 0.20) or 0.20)
    obstacles = diffpair.collect_obstacles(board, profile, skip_trace_ids=set())

    candidate_labels = {
        str(elements[i].get("pcb_trace_id") or "") for i in rail_traces
    }
    candidate_labels.discard("")
    obstacles = _inflate_candidates(obstacles, candidate_labels, target)

    before_snapshot = json.dumps(elements)

    # -- per-net accounting, measured the way the check measures ------------
    stats: dict[str, dict] = {}

    def stat(name: str) -> dict:
        return stats.setdefault(name, {
            "before": math.inf, "after": math.inf, "segments": 0,
            "widened": 0, "at_target": 0.0, "total": 0.0, "limiter": None,
            "worst": math.inf,
        })

    for index, names in sorted(rail_traces.items()):
        trace = elements[index]
        net = board.trace_net_key(trace)
        route, measured = _widen_route(
            trace.get("route") or [], net, obstacles, outline,
            edge_clearance, target,
        )
        trace["route"] = route
        for width, was, length, limiter in measured:
            for name in names:
                s = stat(name)
                s["segments"] += 1
                s["widened"] += 1 if width > was + 1e-9 else 0
                s["total"] += length
                if width >= target - 1e-9:
                    s["at_target"] += length
                if width < s["after"]:
                    s["after"] = width
                    s["limiter"] = limiter

    # Before-widths, from the untouched copy.
    original = json.loads(before_snapshot)
    for index, names in sorted(rail_traces.items()):
        for seg in diffpair._route_segments(original[index]):
            for name in names:
                s = stat(name)
                if seg[5] < s["before"]:
                    s["before"] = seg[5]
                if s["worst"] is math.inf:
                    s["worst"] = seg[5]

    nets = tuple(
        NetWidening(
            name=name,
            before_mm=0.0 if s["before"] is math.inf else s["before"],
            after_mm=0.0 if s["after"] is math.inf else s["after"],
            target_mm=target,
            segments=s["segments"],
            segments_widened=s["widened"],
            length_at_target=(s["at_target"] / s["total"]) if s["total"] else 0.0,
            limiter=s["limiter"],
        )
        for name, s in sorted(stats.items())
    )

    # Keep on *any* copper gained, not on the narrowest point moving. Voltage
    # drop is an integral over the whole path, so a rail that runs 0.5mm for
    # two thirds of its length and necks to 0.2mm past one via is a materially
    # better rail — even though the check, which reports the narrowest point,
    # still names it. Requiring the minimum to move would throw all of that
    # away for the one segment nobody can widen.
    if not any(n.segments_widened for n in nets):
        Path(circuit_json_path).write_text(before_snapshot, encoding="utf-8")
        return WidenResult(
            ran=True, nets=nets, kept=False,
            refusal="no rail could be widened without breaking a clearance rule",
            elapsed_s=time.monotonic() - started,
        )

    Path(circuit_json_path).write_text(
        json.dumps(elements, ensure_ascii=False), encoding="utf-8"
    )

    # -- graded by the pipeline's gate, not by this pass's own model --------
    blocking_before = blocking_after = None
    if grade is not None:
        try:
            before_rows = grade(json.loads(before_snapshot), Path(circuit_json_path))
            after_rows = grade(elements, Path(circuit_json_path))
        except Exception as exc:  # a gate that cannot run is a refusal
            Path(circuit_json_path).write_text(before_snapshot, encoding="utf-8")
            return WidenResult(
                ran=True, nets=nets, kept=False,
                refusal=f"the gate could not be run ({type(exc).__name__}: {exc})",
                elapsed_s=time.monotonic() - started,
            )
        blocking_before = sum(1 for r in before_rows if r.get("severity") == "error")
        blocking_after = sum(1 for r in after_rows if r.get("severity") == "error")
        if blocking_after > blocking_before:
            Path(circuit_json_path).write_text(before_snapshot, encoding="utf-8")
            return WidenResult(
                ran=True, nets=nets, kept=False,
                refusal=(
                    f"it added {blocking_after - blocking_before} blocking "
                    f"finding(s) the board did not have"
                ),
                elapsed_s=time.monotonic() - started,
                blocking_before=blocking_before, blocking_after=blocking_after,
            )

    return WidenResult(
        ran=True, nets=nets, kept=True,
        elapsed_s=time.monotonic() - started,
        blocking_before=blocking_before, blocking_after=blocking_after,
    )
