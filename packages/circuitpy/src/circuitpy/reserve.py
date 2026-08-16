"""Reserve the differential pair's corridor *before* the autorouter fills it.

**The defect this exists for.** EE review 2026-08-15, finding 3: D+/D- must run
parallel, at a constant gap, over a ground reference. `circuitpy.diffpair`
(stage 0c) routes the pair as a pair, and on two of three boards it **refuses**,
for one reason: it runs after the autorouter, so it can only use what the
autorouter left over, and no channel wide enough survives. Measured 2026-08-16
from the two sidecars' own refusal text — harness-puck needs **0.99 mm** of
clear copper two-layer (0.57 mm one-layer), terminal-keyboard **1.04 mm**
(0.77 mm one-layer) — and neither has it *after* routing. Both have it
*before*: the same corridor probe over placement alone finds one on both boards
(`docs/architecture/reserve-before-route.md`).

**The mechanism, read in the shipped bundle rather than inferred.**
`toolchain/node_modules/tscircuit/dist/webworker.min.js` has, in the router's
obstacle builder, an explicit ``element.type === "pcb_keepout"`` branch pushing
an oval or a rect with ``connectedTo: []``. **A keepout is an obstacle the
autorouter honours, and it is not copper.** So a reserved channel deflects
routing and costs nothing in the output — nothing has to be deleted from the
copper, only the reservation itself has to be taken back out of the artifact
(ledger #45). The pair's endpoints are fixed at *placement* time, before any
routing, so the reservation needs no extra probe build either: placement is
invariant across it (probe vs routed artifact 287/291 identical, the 4 being
stage 0a's ring repair; baseline-final vs reserved-compiled 295/295).

This module is the **author** of a reservation. `circuitpy.diffpair` is its
consumer, and the pipeline is the thing that carries one from here to there:

    plan_reservation(...)  -> a corridor, or a refusal that says which test failed
    reservation_tsx(...)   -> the `<keepout>` elements, as text
    inject_reservation(..) -> that text into the **mirrored** board source only
    release_reservation(..)-> those exact elements back out of circuit.json

**Three rules this module is organised around.**

1. **It refuses rather than strangles.** Thirteen refusals, each named and each
   reported. A board with no room keeps its reservations out and says why — the
   honest answer for harness-puck today is *"refused, and here is the corridor
   width that does exist"*, which points at placement, where the fix belongs.
2. **It plans with the consumer's own ruler.** The corridor width comes from
   `diffpair.pair_span`, the obstacle field from `diffpair.collect_obstacles`,
   the raster from `diffpair._Grid` and the entry point from
   `diffpair._pair_seed`. A corridor planned with a different ruler is a
   corridor the pass cannot enter, and that is measured, not feared: with the
   corridor provably empty (0 segments, 0 vias, both reserved builds) stage 0c
   *still* refused, because its A* rasters at 0.05 mm and the emptied corridor
   left **0.0093 mm** of slack at the tightest point. `RESERVE_MARGIN_MM` is
   three raster cells of answer to that.
3. **It removes only what it wrote, by the numbers it wrote.** There is no
   provenance field on a `pcb_keepout` — the emitter writes no name and no
   description, and all four of harness-puck's report the same
   `subcircuit_id`. Ids renumber: in the measured proof build the injected
   element came back as `pcb_keepout_1`, and in the baseline `pcb_keepout_1` is
   a `MountingHole` circle (ledger #7). So the strip matches the recorded
   ``(shape, layers, center, radius)`` tuple, requires **exactly one match per
   entry**, and on zero or two strips nothing at all. Never by id, never by
   type, never "all keepouts".

**What it does not do, and will not.** It never reserves for a power rail.
Finding 4 is not closed by any reservation and the measurements say so: 3 of
the 8 rail necks on the three boards are pinned by **router-created vias** that
do not exist when a reservation is planned; 2 of the 3 pad-pinned necks are
both U3 pin 47, inside a 0.400 mm-pitch QFN escape with 0.200 mm between pads,
where 0.5 mm of copper is geometrically impossible; and a keepout reserves
*emptiness* while a rail needs *copper* — the obstacle it pushes is
``connectedTo: []``, net-blind by construction, so it can deny space to
everyone and grant it to no one. That fix is per-net-class width applied
*while* routing (ledger #31, `routerlib` already models `net_class` /
`min_width_mm`), and it is not this.

**Two places this module knowingly departs from
`docs/architecture/reserve-before-route.md`, both because the rule as written
can never fire.** Recorded here rather than quietly:

* *Courtyards.* The design lists courtyards in the placement obstacle field and
  makes any intersection with one a refusal. Every anchor is a pad, and every
  pad is inside its own component's courtyard, so that rule refuses 100% of
  reservations before the first one is measured. Courtyards are therefore
  **measured and reported** (`courtyardOverlaps`), not enforced — a keepout
  over a courtyard denies routing space, which is what it is for, and does not
  touch assembly clearance. The obstacle field is the one the design's own
  corridor probe used: pads, drills, holes, keepouts, cutouts, board edge.
* *End pockets.* The design reserves "the largest clear disc at the anchor
  midpoint, capped at 1.0 mm, and refuses if it is smaller than `reserve_r`".
  The midpoint of two USB pads 0.6 mm apart has ~0.2 mm of clear space around
  it, so that too refuses always — and a pocket sized to *cover* the pair's own
  pads would be worse than refusing, because then the autorouter cannot reach
  the pair at all and the reserved build comes back unroutable. What the pocket
  is *for* is enterability, so that is what is tested: the corridor ends at the
  cell `diffpair._pair_seed` would choose (within `SEED_RADIUS_MM` of the pads,
  with a clear straight fan to both), and refuses with `no_end_pocket` when no
  such cell exists. That refusal is not theoretical — it is terminal-keyboard's
  actual stage 0c refusal today ("no clear fan-out from the RP2040's USB_DM
  pad").
"""

from __future__ import annotations

import json
import math
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from circuitpy import diffpair
from circuitpy.diffpair import (
    GRID_MM,
    SEARCH_MARGINS_MM,
    SEED_RADIUS_MM,
    Obstacle,
    _Board,
    _Grid,
    collect_obstacles,
)

try:  # numpy is in the pipeline's environment; every gate here needs it
    import numpy as _np
except Exception:  # pragma: no cover - exercised only on a stripped runtime
    _np = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants. Every one of these is either measured or a fab floor, and the
# measured ones carry the measurement.
# ---------------------------------------------------------------------------

#: Slack on top of the pair's own half-span, in millimetres. **Load-bearing and
#: measured.** With a reserved rect provably empty — 12 trace segments and 2
#: vias before, 0 and 0 after, both reserved builds — stage 0c still refused,
#: because its A* rasters at ``GRID_MM`` and the emptied corridor left
#: **0.0093 mm** of slack over its own dilation at the tightest point (worst
#: legal half-width 0.5050 mm against 0.4957 mm required, set by
#: ``pcb_smtpad_156``). A reservation sized at exactly the pass's minimum is a
#: reservation the pass cannot enter. 0.15 mm is three raster cells; the
#: smallest margin that works is **unmeasured**, and the first reserved build
#: measures it.
RESERVE_MARGIN_MM = 0.15

#: Never reserve more area than has ever been built and graded at all, in mm².
#: 53.3 mm² is the largest of the two reservations that were compiled and put
#: through the pipeline's gate on harness-puck (0.991 mm and 1.292 mm
#: corridors). **Both of those lost**, so this cap is not a licence — it
#: ratchets *down* to the largest reservation that has ever graded clean, and
#: today that number is zero, which means the pipeline's gate is carrying the
#: whole load and this constant says so out loud.
RESERVE_MAX_AREA_MM2 = 53.3

#: Cap on an end disc, in millimetres of radius. A corridor end wider than this
#: stops being a pocket and starts being a hole in the board's routing space.
RESERVE_POCKET_MAX_MM = 1.0

#: How close a reservation entry's numbers have to be to a keepout's before the
#: two are the same element. The pipeline writes millimetres into TSX and reads
#: millimetres back out of circuit.json; every emitted number is rounded to this
#: same grid first (`_round`), so the round trip is exact rather than merely
#: close. Deliberately not a "close enough" tolerance: matching loosely is how a
#: foreign keepout gets deleted.
RESERVE_MATCH_TOL_MM = 1e-6

#: Cell size for the strangle test, in millimetres. Coarser than ``GRID_MM`` on
#: purpose — this is a reachability question over the whole board, not a
#: geometry question, and it is asked **twice** (with the corridor and without)
#: so the answer is a difference and the coarseness cancels: a net that the
#: grid cannot connect either way is not something the corridor broke.
STRANGLE_GRID_MM = 0.2

#: How many bisection steps are spent measuring the pinch when no corridor
#: exists. The answer is only ever reported, never acted on, so five steps
#: (about 3% of the requested width) is as precise as it needs to be.
RESERVE_PINCH_STEPS = 5

#: What the router owns and placement does not. These are the element types a
#: rebuild re-solves from scratch — attempt 1's copper is not evidence about
#: where attempt 2's copper will go (122 vias became 129 on the measured
#: reserved build), so a corridor planned around them is planned around a
#: fiction. Ignoring them is the whole reason a reservation can be planned from
#: an already-routed artifact without a probe build.
ROUTER_OWNED = frozenset({"pcb_trace", "pcb_via", "pcb_copper_pour"})

#: Net names that must never get a reservation. Same two patterns
#: ``powerwidth`` uses, imported rather than retyped, because a rail is exactly
#: what a keepout cannot help (see the module docstring).
_POWER_NET_RE = re.compile(
    r"^(v(cc|dd|bus|in|out|sys|bat|\d.*)|.*_?\d+v\d*.*|\d+v\d*|pwr|power|vpp)$",
    re.I,
)
_GROUND_NET_RE = re.compile(r"^(gnd|agnd|dgnd|vss|ground)$", re.I)

#: Stage 0c's refusal for want of room, as prose. Only used when the outcome
#: carries no ``reason_code`` — a vendored runtime can lag the tree by a whole
#: day (ledger #42), and a consumer test that silently stops firing is worse
#: than one coupled to a sentence. A test pins this against the string
#: `diffpair` actually produces, so the coupling is checked rather than hoped.
_NO_CORRIDOR_PROSE = re.compile(
    r"no corridor wide enough for the pair exists", re.I
)

_EPS = 1e-9


#: How much a *measured* radius is shrunk before it is written down, in
#: millimetres. **Measured, and it is a rounding artefact, not a rule.** An end
#: disc grows to exactly the largest radius that touches nothing, and writing
#: that number onto the 1e-6 grid can round it *up*: on terminal-keyboard the
#: first disc came back overlapping ``pcb_smtpad_346`` by **3.458e-07 mm** —
#: 0.35 nanometres — and the exact overlap check refused the whole reservation
#: for it, correctly, because zero tolerance means zero. The answer is to write
#: down slightly less than was measured, never to widen the check: 1e-5 mm is
#: ten nanometres, fourteen times the worst rounding error (a 5e-7 shift in x
#: and y is 7.1e-7 of travel) and four orders of magnitude under the 0.1 mm
#: clearance floor.
_ROUND_SLACK_MM = 1e-5


def _round(value: float) -> float:
    """Every number we write out, on one grid.

    The record and the TSX must agree *exactly*, not nearly, because the strip
    pass demands exactly one match at ``RESERVE_MATCH_TOL_MM``. Rounding both
    to the same 1e-6 grid at construction is what makes that true by
    construction instead of by luck.
    """
    return round(float(value), 6)


def _round_down(value: float) -> float:
    """A measured radius, onto the same grid, never upward.

    A position may round either way — it is a coordinate. A radius derived
    from a measurement may not: rounding it up spends clearance that was
    measured to be absent.
    """
    return math.floor((float(value) - _ROUND_SLACK_MM) * 1e6) / 1e6


# ---------------------------------------------------------------------------
# What a reservation is
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Disc:
    """One reserved circle, on one layer.

    **Why a circle and not a rectangle.** ``pcb_keepout`` has exactly two
    shapes in circuit-json: an axis-aligned ``rect`` with no rotation field,
    and a ``circle``. A corridor that turns cannot be a rect: the emitter
    computes ``isRotated90`` and swaps width/height only at 90 and 270 degrees
    (bundle offset 8,128,950), so at any other angle it emits the wrong shape
    **silently**. A chain of overlapping discs turns wherever the centreline
    turns and has no orientation to get wrong.
    """

    layer: str
    cx: float
    cy: float
    radius: float

    def as_element(self) -> dict[str, object]:
        """The record, shaped exactly like the ``pcb_keepout`` element the
        compile will produce from it — so the same dict is what we inject,
        what `diffpair.Reservation.from_obj` reads, and what the strip pass
        matches against."""
        return {
            "shape": "circle",
            "layers": [self.layer],
            "center": {"x": self.cx, "y": self.cy},
            "radius": self.radius,
        }

    def as_tsx(self) -> str:
        return (
            f'<keepout shape="circle" radius="{self.radius:.6f}mm" '
            f'pcbX={{{self.cx:.6f}}} pcbY={{{self.cy:.6f}}} '
            f'layers={{["{self.layer}"]}} />'
        )

    def matches(self, keepout: dict) -> bool:
        """Is this circuit.json ``pcb_keepout`` the element we wrote?"""
        if not isinstance(keepout, dict) or keepout.get("shape") != "circle":
            return False
        layers = keepout.get("layers")
        if not isinstance(layers, (list, tuple)):
            return False
        if tuple(str(x) for x in layers) != (self.layer,):
            return False
        centre = keepout.get("center") or {}
        if not isinstance(centre, dict):
            return False
        try:
            cx = float(centre.get("x"))
            cy = float(centre.get("y"))
            radius = float(keepout.get("radius"))
        except (TypeError, ValueError):
            return False
        return (abs(cx - self.cx) <= RESERVE_MATCH_TOL_MM
                and abs(cy - self.cy) <= RESERVE_MATCH_TOL_MM
                and abs(radius - self.radius) <= RESERVE_MATCH_TOL_MM)

    def distance_from(self, keepout: dict) -> float | None:
        """How far a candidate is from this record, for a refusal message.
        A near miss and a total mismatch are different problems — a coordinate
        frame that shifted everything by the board centre would show up as a
        constant offset here, in one build, instead of as silence."""
        centre = keepout.get("center") or {}
        if not isinstance(centre, dict):
            return None
        try:
            return math.hypot(float(centre.get("x")) - self.cx,
                              float(centre.get("y")) - self.cy)
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Reservation:
    """A planned corridor: which pair it is for, and every disc it is made of."""

    net_p: str
    net_n: str
    layer: str
    reserve_r_mm: float
    margin_mm: float
    half_span_mm: float
    width_mm: float
    pitch_mm: float
    discs: tuple[Disc, ...]
    centreline: tuple[tuple[float, float], ...]
    path_mm: float
    straight_mm: float
    area_mm2: float
    board_area_mm2: float
    end_reach_mm: tuple[float, float]
    courtyard_overlaps: tuple[str, ...] = ()

    def record(self) -> dict[str, object]:
        """What the pipeline carries from the injector to the strip pass, and
        hands to `diffpair` as its reservation. Plain JSON on purpose: the two
        modules agree on element-shaped numbers, not on a Python class."""
        return {
            "pair": [self.net_p, self.net_n],
            "keepouts": [d.as_element() for d in self.discs],
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "pair": f"{self.net_p}/{self.net_n}",
            "layer": self.layer,
            "reserveRadiusMm": round(self.reserve_r_mm, 4),
            "marginMm": round(self.margin_mm, 4),
            "halfSpanMm": round(self.half_span_mm, 4),
            "traceWidthMm": round(self.width_mm, 4),
            "pitchMm": round(self.pitch_mm, 4),
            "discs": len(self.discs),
            "pathMm": round(self.path_mm, 3),
            "straightMm": round(self.straight_mm, 3),
            "detourRatio": (round(self.path_mm / self.straight_mm, 3)
                            if self.straight_mm > _EPS else None),
            "areaMm2": round(self.area_mm2, 3),
            "areaFraction": (round(self.area_mm2 / self.board_area_mm2, 5)
                             if self.board_area_mm2 > _EPS else None),
            "endReachMm": [round(v, 3) for v in self.end_reach_mm],
            "courtyardOverlaps": list(self.courtyard_overlaps),
        }


@dataclass
class ReserveResult:
    """A plan, or the named reason there is not one.

    ``status`` is one of ``planned`` / ``refused`` / ``skipped``. Skipped means
    there was no consumer — nothing was asked of us. Refused means there was
    and we said no, which is a result and gets reported like one: **a
    reservation that fired and lost still gets reported**, because an absent
    value makes no claim and nothing checks a claim nobody makes (ledger
    lesson F).
    """

    status: str = "skipped"
    reason: str = ""
    reason_code: str = ""
    reservation: Reservation | None = None
    measurements: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def planned(self) -> bool:
        return self.status == "planned" and self.reservation is not None

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"status": self.status}
        if self.reason:
            out["reason"] = self.reason
        if self.reason_code:
            out["reasonCode"] = self.reason_code
        if self.reservation is not None:
            out["reservation"] = self.reservation.as_dict()
        if self.measurements:
            out["measured"] = dict(self.measurements)
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def _refused(code: str, reason: str, **measured: object) -> ReserveResult:
    return ReserveResult(status="refused", reason_code=code, reason=reason,
                         measurements=dict(measured))


def _skipped(code: str, reason: str) -> ReserveResult:
    return ReserveResult(status="skipped", reason_code=code, reason=reason)


# ---------------------------------------------------------------------------
# The consumer test: is anybody actually asking for a corridor?
# ---------------------------------------------------------------------------


def wants_reservation(result: Any) -> tuple[str, str] | None:
    """The pair stage 0c refused **for want of room**, or None.

    A reservation is a denial to every net on the board — the one measured
    reservation re-solved 88 of 123 traces and added 7 vias — so the default is
    not to make one. It fires only where it has a consumer. A pair that routed,
    a pair that was skipped, and a pair refused for any *other* reason (a
    multi-drop pair, pads on two layers, a clearance the new copper broke) are
    all "no": reserving a corridor for a pair that already had one buys nothing
    and costs the whole board a re-solve.
    """
    if result is None:
        return None
    for outcome in getattr(result, "pairs", ()) or ():
        if getattr(outcome, "status", "") != "refused":
            continue
        code = str(getattr(outcome, "reason_code", "") or "")
        if code:
            if code != "no_corridor":
                continue
        elif not _NO_CORRIDOR_PROSE.search(str(getattr(outcome, "reason", ""))):
            continue
        name_p = str(getattr(outcome, "net_p", ""))
        name_n = str(getattr(outcome, "net_n", ""))
        if name_p and name_n:
            return (name_p, name_n)
    return None


# ---------------------------------------------------------------------------
# Reading placement out of a routed artifact
# ---------------------------------------------------------------------------


def placement_elements(elements: Sequence[dict]) -> list[dict]:
    """Everything a rebuild will reproduce, and nothing it will re-solve.

    Measured both ways, so this is not an assumption. A ``routingDisabled``
    probe build against the routed artifact of record is **287 of 291
    identical** (224 smtpads, 4 plated holes, 5 holes, 61 components, 1
    outline) — the 4 that differ are the USB-C polygon pads and the only
    difference is the repeated closing vertex stage 0a opens (ledger #33). And
    baseline-final against reserved-compiled is **295 of 295 identical**: the
    reservation does not move the thing it was computed from, so there is no
    fixed point to iterate to.
    """
    return [e for e in elements
            if isinstance(e, dict) and e.get("type") not in ROUTER_OWNED]


def _courtyards(elements: Sequence[dict]) -> list[Obstacle]:
    """Component courtyards, as obstacles — **measured, never enforced.**

    See the module docstring: enforcing these refuses every reservation,
    because every anchor pad is inside its own component's courtyard. They are
    collected so the report can say how much of the corridor lies over one.
    """
    out: list[Obstacle] = []
    for rect in elements:
        if not isinstance(rect, dict) or rect.get("type") != "pcb_courtyard_rect":
            continue
        centre = rect.get("center") or {}
        try:
            cx, cy = float(centre.get("x")), float(centre.get("y"))
            w, h = float(rect.get("width")), float(rect.get("height"))
        except (TypeError, ValueError):
            continue
        out.append(Obstacle(
            "poly", 0.0, (str(rect.get("layer") or "top"),),
            str(rect.get("pcb_courtyard_rect_id") or "courtyard"),
            poly=diffpair._rotated_rect(cx, cy, w, h, 0.0),
        ))
    return out


def _poured_layers(elements: Sequence[dict]) -> set[str]:
    return {str(e.get("layer") or "top") for e in elements
            if isinstance(e, dict) and e.get("type") == "pcb_copper_pour"}


def _pair_width(board: _Board, key_p: str, key_n: str,
                profile: Any) -> float:
    """The copper the pair is drawn in today, which is the copper stage 0c will
    lay it in tomorrow — it takes the minimum width of the existing route."""
    widths: list[float] = []
    for trace in board.by_type.get("pcb_trace", []):
        if board.trace_net_key(trace) not in (key_p, key_n):
            continue
        widths += [s[5] for s in diffpair._route_segments(trace) if s[5] > 0]
    if widths:
        return min(widths)
    return float(getattr(profile, "warn_trace_mm", 0.15) or 0.15)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_reservation(elements: Sequence[dict], profile: Any, *,
                     pair: tuple[str, str] | None = None) -> ReserveResult:
    """Plan the corridor for one differential pair, or refuse and say why.

    ``elements`` is attempt 1's own circuit.json — routed. Only its placement
    is read (`placement_elements`); ``pcb_trace``, ``pcb_via`` and the pour are
    the router's, and a rebuild re-solves all three.

    ``pair`` names the two nets. ``None`` means "the one pair on this board",
    which is what a single-consumer caller wants; a board with two pairs must
    say which, because reserving for one and routing the other is how a
    corridor gets spent on nothing.
    """
    if _np is None:
        # A gate that cannot run is a refusal, not a pass (ledger lesson E).
        return _refused(
            "no_numpy",
            "the reservation planner needs numpy and it is not installed; "
            "nothing is reserved",
        )

    full = [e for e in elements if isinstance(e, dict)]
    placement = placement_elements(full)
    routed_board = _Board(full)
    board = _Board(placement)
    names = diffpair._named_nets(board)
    pairs = diffpair.find_pairs(names)
    if not pairs:
        return _skipped("no_pair", "this board has no differential pair")

    if pair is not None:
        wanted = {pair[0], pair[1]}
        chosen = next((p for p in pairs if set(p) == wanted), None)
        if chosen is None:
            return _skipped(
                "no_pair",
                f"{pair[0]}/{pair[1]} is not a differential pair on this board",
            )
    elif len(pairs) > 1:
        return _skipped(
            "ambiguous_pair",
            f"this board has {len(pairs)} differential pairs "
            f"({', '.join('/'.join(p) for p in pairs)}); name the one to reserve",
        )
    else:
        chosen = pairs[0]

    name_p, name_n = chosen
    if _is_power_name(name_p) or _is_power_name(name_n):
        # Never for a rail, and the reason is arithmetic rather than policy:
        # a keepout is `connectedTo: []`, so it denies space to everyone and
        # grants it to nobody, and a rail is a Steiner tree with no A and no B.
        return _refused(
            "power_rail",
            f"{name_p}/{name_n} reads as a power or ground net; a keepout "
            "reserves emptiness and a rail needs copper, so no reservation can "
            "widen one (ledger #31)",
        )

    key_p, key_n = names[name_p], names[name_n]
    ports_p = board.pcb_ports_of_net(key_p)
    ports_n = board.pcb_ports_of_net(key_n)
    if len(ports_p) != 2 or len(ports_n) != 2:
        return _refused(
            "not_two_terminal",
            f"{name_p} has {len(ports_p)} pads and {name_n} has {len(ports_n)}; "
            "stage 0c routes two-terminal pairs only, so a corridor for this "
            "pair has no consumer",
        )

    pad_layers = {diffpair._pad_layer(p) for p in ports_p + ports_n}
    pad_layers.discard(None)
    if len(pad_layers) != 1:
        return _refused(
            "pads_on_multiple_layers",
            f"the pair's four pads sit on {sorted(pad_layers)}; the corridor "
            "is single-layer and there is no one layer to put it on",
        )
    layer = str(next(iter(pad_layers)))

    poured = _poured_layers(full)
    if layer in poured:
        # Measured: the pour solver has 0 references to `keepout` against 7 to
        # `pcb_cutout`, and on the proof rect the bottom pour went 13.5025 ->
        # 16.0775 mm² of a 16.200 mm² rect — it filled *more*. A reservation on
        # a poured layer reserves nothing.
        return _refused(
            "poured_layer",
            f"the pair's pads are on {layer} and {layer} is poured; a copper "
            "pour ignores keepouts entirely, so a corridor there reserves "
            "nothing",
            pouredLayers=sorted(poured),
        )

    width = _pair_width(routed_board, key_p, key_n, profile)
    # The consumer's own ruler. Single-layer, which is the narrower ask and the
    # one to try first: harness-puck needs 0.57mm one-layer against 0.99mm
    # two-layer, terminal-keyboard 0.77 against 1.04 — and on both of those the
    # un-poured layer is top and the pour is bottom, so a top corridor also
    # puts a continuous reference plane under the pair, which is the EE's third
    # clause and is 0% on all three boards today.
    span = diffpair.pair_span(profile, width, allow_layers=False)
    reserve_r = span.half_span_mm + RESERVE_MARGIN_MM

    obstacles = [o for o in collect_obstacles(board, profile, skip_trace_ids=set())
                 if layer in o.layers]
    outline = diffpair._board_outline(board)
    edge_clearance = float(getattr(profile, "min_edge_clearance_mm", 0.2) or 0.2)
    ends = diffpair._pair_ends(ports_p, ports_n)
    anchors = _anchors(ends)
    if anchors is None:
        return _refused("no_endpoint_position",
                        "an endpoint pad has no position")

    search = _Search(obstacles, outline, edge_clearance, layer, width,
                     anchors, key_p, key_n)

    # **Ask the cheap question first.** The exact search is the consumer's own
    # ruler and it is what decides, but on a board with no channel it is also
    # the slowest possible way to find that out: measured on harness-puck with
    # cProfile, one whole-board A* is 49 s of CPU and the escalation runs it
    # after two smaller boxes have already failed. The coarse test is
    # deliberately optimistic by one cell, so a "no" here is a no the exact
    # search would also give — it can save the minutes but it can never refuse
    # a corridor that exists.
    if not search.coarsely_connected(reserve_r):
        widest = search.widest(reserve_r)
        return _refused(
            "no_corridor",
            f"no corridor exists between the two ends on {layer} even before "
            f"the autorouter runs: the pair needs {2 * reserve_r:.2f}mm of "
            f"clear copper and the widest channel that fits is "
            f"{widest:.2f}mm (measured on a {STRANGLE_GRID_MM}mm raster). The "
            "blocker is placement, not routing",
            needMm=round(2 * reserve_r, 3),
            widestCorridorMm=round(widest, 3),
            layer=layer,
        )

    found = search.route(reserve_r)
    if found is None:
        if search.seed_failure is not None:
            which, mid = search.seed_failure
            return _refused(
                "no_end_pocket",
                f"the pair cannot enter a {2 * reserve_r:.2f}mm corridor at its "
                f"{which} end: no cell within {SEED_RADIUS_MM:.1f}mm of "
                f"({mid[0]:.2f}, {mid[1]:.2f}) is both clear of the corridor's "
                "own width and reachable in a straight line from both pads",
                needMm=round(2 * reserve_r, 3),
            )
        # The coarse test said a channel exists and the exact search could not
        # use it. That gap is the raster's, not the board's, so the number is
        # reported with the ruler it came from and the refusal stands.
        widest = search.widest(reserve_r)
        return _refused(
            "no_corridor",
            f"no corridor the pair can use exists between the two ends on "
            f"{layer}: it needs {2 * reserve_r:.2f}mm of clear copper, and the "
            f"widest channel a {STRANGLE_GRID_MM}mm raster finds is "
            f"{widest:.2f}mm — which the exact search at {GRID_MM}mm could not "
            "turn into a route. The blocker is placement, not routing",
            needMm=round(2 * reserve_r, 3),
            widestCorridorMm=round(widest, 3),
            layer=layer,
        )

    centreline, reach = found
    discs = _discs_along(centreline, reserve_r, layer)
    discs = _widen_ends(discs, centreline, obstacles, outline, edge_clearance,
                        layer, reserve_r)
    if not discs:
        return _refused("no_corridor",
                        "the planned centreline produced no reservable shape")

    path_mm = _polyline_length(centreline)
    straight = math.hypot(anchors[1][2][0] - anchors[0][2][0],
                          anchors[1][2][1] - anchors[0][2][1])
    area = _swept_area(path_mm, reserve_r, discs)
    board_area = _board_area(board, outline)

    if area > RESERVE_MAX_AREA_MM2 + _EPS:
        return _refused(
            "area_cap",
            f"the corridor sweeps {area:.1f}mm², over the "
            f"{RESERVE_MAX_AREA_MM2:.1f}mm² cap — no reservation that large has "
            "ever been built and graded, and the cap only moves on a "
            "measurement",
            areaMm2=round(area, 3),
            capMm2=RESERVE_MAX_AREA_MM2,
        )

    overlap = _exact_overlap(discs, obstacles, outline, edge_clearance)
    if overlap is not None:
        return _refused(
            "overlap",
            f"the corridor overlaps {overlap[0]} by {overlap[1]:.4f}mm; a "
            "reservation is never resolved, merged, shrunk, moved or deleted "
            "into place",
            overlaps=overlap[0],
        )

    strangled = _strangle_test(board, obstacles, outline, profile, discs,
                               (key_p, key_n))
    if strangled:
        own = [n for n in strangled if n in (key_p, key_n)]
        others = [n for n in strangled if n not in (key_p, key_n)]
        detail = ""
        if own:
            # The worst version of this, and the one the measured test board
            # hits first: the corridor is the only channel, so the autorouter
            # cannot connect the pair *in the reserved build* — and stage 0c
            # needs exactly one trace per net to work with, so the corridor
            # would cost the pair the very route it was reserved for.
            detail = (" — including the pair itself, so the autorouter could "
                      "not have connected it in the reserved build")
        return _refused(
            "strangle",
            f"the corridor cuts the board's free space: "
            f"{', '.join(own + others)} can no longer reach across it"
            f"{detail}. Refused rather than shipped as a routing problem for "
            "the next stage to discover",
            strangledNets=own + others,
        )

    reservation = Reservation(
        net_p=name_p, net_n=name_n, layer=layer,
        reserve_r_mm=reserve_r, margin_mm=RESERVE_MARGIN_MM,
        half_span_mm=span.half_span_mm, width_mm=width, pitch_mm=span.pitch_mm,
        discs=tuple(discs), centreline=tuple(centreline),
        path_mm=path_mm, straight_mm=straight,
        area_mm2=area, board_area_mm2=board_area,
        end_reach_mm=reach,
        courtyard_overlaps=_courtyard_overlaps(discs, _courtyards(placement)),
    )
    result = ReserveResult(status="planned", reservation=reservation)
    if reservation.courtyard_overlaps:
        result.notes.append(
            f"{len(reservation.courtyard_overlaps)} component courtyard(s) lie "
            "under the corridor; measured and reported, not refused — a "
            "keepout denies routing space, which is what it is for, and does "
            "not touch assembly clearance"
        )
    return result


def _is_power_name(name: str) -> bool:
    return bool(_POWER_NET_RE.match(name) or _GROUND_NET_RE.match(name))


def _anchors(ends: Sequence[tuple[dict, dict]]
             ) -> list[tuple[tuple[float, float], tuple[float, float],
                             tuple[float, float]]] | None:
    out = []
    for port_p, port_n in ends:
        px, py = diffpair._f(port_p.get("x")), diffpair._f(port_p.get("y"))
        nx, ny = diffpair._f(port_n.get("x")), diffpair._f(port_n.get("y"))
        if None in (px, py, nx, ny):
            return None
        out.append(((px, py), (nx, ny), ((px + nx) / 2, (py + ny) / 2)))
    return out


class _Search:
    """One A* over the placement-only field, at whatever width is asked for.

    Built as an object because the pinch measurement re-runs it at smaller
    radii, and every run has to use the same obstacles, the same outline and
    the same fan grids — a pinch measured with a different ruler is a number
    that means nothing.
    """

    def __init__(self, obstacles: Sequence[Obstacle],
                 outline: Sequence[tuple[float, float]] | None,
                 edge_clearance: float, layer: str, width: float,
                 anchors: Sequence, net_p: str, net_n: str):
        self.obstacles = list(obstacles)
        self.outline = outline
        self.edge_clearance = edge_clearance
        self.layer = layer
        self.width = width
        self.anchors = list(anchors)
        self.net_p, self.net_n = net_p, net_n
        #: Set when the corridor could not be *entered* rather than not found.
        self.seed_failure: tuple[str, tuple[float, float]] | None = None

        if outline:
            xs = [p[0] for p in outline]
            ys = [p[1] for p in outline]
            self.board_box = (min(xs), min(ys), max(xs), max(ys))
            self.raster_outline = diffpair._thinned(outline, 192)
        else:
            self.board_box = (-1e3, -1e3, 1e3, 1e3)
            self.raster_outline = None

        # The fan grids are the consumer's, exactly: one raster per leg at a
        # single track's clearance with that leg's own net exempt, so a fan may
        # end inside its own pad.
        pad_xs = [self.anchors[i][j][0] for i in (0, 1) for j in (0, 1)]
        pad_ys = [self.anchors[i][j][1] for i in (0, 1) for j in (0, 1)]
        fan_box = (max(self.board_box[0], min(pad_xs) - 8.0),
                   max(self.board_box[1], min(pad_ys) - 8.0),
                   min(self.board_box[2], max(pad_xs) + 8.0),
                   min(self.board_box[3], max(pad_ys) + 8.0))
        self.fan_grids: dict[str, _Grid] = {}
        for net in (net_p, net_n):
            grid = _Grid(*fan_box)
            for obstacle in self.obstacles:
                if obstacle.net is not None and obstacle.net == net:
                    continue
                grid.block(obstacle, width / 2)
            if self.raster_outline:
                grid.block_outside(self.raster_outline,
                                   edge_clearance + width / 2)
            self.fan_grids[net] = grid

    def route(self, reserve_r: float
              ) -> tuple[list[tuple[float, float]], tuple[float, float]] | None:
        """The centreline of the widest-fitting corridor at ``reserve_r``, and
        how far each end sits from its pad-pair midpoint."""
        self.seed_failure = None
        for margin in SEARCH_MARGINS_MM:
            box = self._box(margin)
            probe = _Grid(*box)
            if probe.nx * probe.ny > 16_000_000:
                return None
            track = _Grid(*box)
            for obstacle in self.obstacles:
                track.block(obstacle, reserve_r)
            if self.raster_outline:
                track.block_outside(self.raster_outline,
                                    self.edge_clearance + reserve_r)
            seed = diffpair._pair_seed(
                track, self.fan_grids, self.anchors[0][0], self.anchors[0][1],
                self.net_p, self.net_n, self.anchors[1][2])
            target = diffpair._pair_seed(
                track, self.fan_grids, self.anchors[1][0], self.anchors[1][1],
                self.net_p, self.net_n, self.anchors[0][2])
            if seed is None:
                self.seed_failure = ("first", self.anchors[0][2])
                continue
            if target is None:
                self.seed_failure = ("second", self.anchors[1][2])
                continue
            self.seed_failure = None
            path = diffpair._astar([track], [track], (0, *seed), (0, *target))
            if path is None:
                continue
            runs = diffpair._runs_from_path(path, probe, [self.layer],
                                            2 * reserve_r, [track])
            points = [p for _, run in runs for p in run]
            if len(points) < 2:
                continue
            reach = (
                math.hypot(points[0][0] - self.anchors[0][2][0],
                           points[0][1] - self.anchors[0][2][1]),
                math.hypot(points[-1][0] - self.anchors[1][2][0],
                           points[-1][1] - self.anchors[1][2][1]),
            )
            return points, reach
        return None

    def widest(self, ceiling: float) -> float:
        """The widest corridor that *does* exist, in millimetres of full width.

        **Measured by connectivity on a coarse raster, not by the exact
        search**, and the reason is a measurement: bisecting with `route` costs
        five whole-board A* runs, and one of those on harness-puck is 49
        seconds of CPU on its own (`cProfile`, 2026-08-16 — 98.8 s in
        ``_astar`` across two calls, 46.9 s more in ``block_outside``). This
        number is only ever *reported* — it never shrinks a reservation into
        fitting, which would be changing the ruler to fit the board — so it
        does not need the exact ruler, and paying five minutes for a sentence
        in a refusal message would be the wrong trade.

        "Is there a channel" is reachability, which needs no shortest path, no
        turn cost and no heap. So the answer is a flood over
        ``STRANGLE_GRID_MM`` cells, and the message says which raster it came
        from.
        """
        low, high = 0.0, ceiling
        best = 0.0
        for _ in range(RESERVE_PINCH_STEPS):
            mid = (low + high) / 2
            if mid <= STRANGLE_GRID_MM:
                break
            if self.coarsely_connected(mid):
                best = mid
                low = mid
            else:
                high = mid
        return 2 * best

    def coarsely_connected(self, radius: float) -> bool:
        """Do the two ends share a component of free space at this width?

        Optimistic by exactly one cell: the dilation is reduced by
        ``STRANGLE_GRID_MM``, which is more than the half-diagonal sampling
        error of the raster (0.141 mm). So a "no" here is a no the exact search
        would also give, and this can be used to skip the expensive search
        without ever refusing a corridor that exists.
        """
        if _np is None:
            return False
        grow = max(0.0, radius - STRANGLE_GRID_MM)
        grid = _CoarseGrid(self.board_box, STRANGLE_GRID_MM)
        if grid.nx * grid.ny > 4_000_000:
            return True  # cannot raster it honestly; let the exact search say
        free = grid.free_mask(self.obstacles, grow, self.raster_outline,
                              self.edge_clearance + grow)
        start = grid.snap({self.layer: free}, [self.layer],
                          *self.anchors[0][2], self.layer)
        goal = grid.snap({self.layer: free}, [self.layer],
                         *self.anchors[1][2], self.layer)
        if start is None or goal is None:
            return False
        return _reaches(free, (start[1], start[2]), (goal[1], goal[2]))

    def _box(self, margin: float | None) -> tuple[float, float, float, float]:
        if margin is None:
            return self.board_box
        xs = [self.anchors[0][2][0], self.anchors[1][2][0]]
        ys = [self.anchors[0][2][1], self.anchors[1][2][1]]
        return (max(self.board_box[0], min(xs) - margin),
                max(self.board_box[1], min(ys) - margin),
                min(self.board_box[2], max(xs) + margin),
                min(self.board_box[3], max(ys) + margin))


def _reaches(free, start: tuple[int, int], goal: tuple[int, int]) -> bool:
    """Eight-connected flood from ``start``; does it arrive at ``goal``?"""
    if start == goal:
        return True
    ny, nx = free.shape
    seen = {start}
    frontier = deque([start])
    steps = ((-1, 0), (1, 0), (0, -1), (0, 1),
             (-1, -1), (-1, 1), (1, -1), (1, 1))
    while frontier:
        row, col = frontier.popleft()
        for dr, dc in steps:
            nr, nc = row + dr, col + dc
            if not (0 <= nr < ny and 0 <= nc < nx):
                continue
            if (nr, nc) in seen or not free[nr, nc]:
                continue
            if (nr, nc) == goal:
                return True
            seen.add((nr, nc))
            frontier.append((nr, nc))
    return False


def _discs_along(points: Sequence[tuple[float, float]], radius: float,
                 layer: str) -> list[Disc]:
    """A chain of overlapping discs, spaced at most ``radius`` apart.

    Spacing at the radius rather than the diameter is what makes the chain a
    corridor rather than a string of beads: two discs a radius apart overlap
    over the middle half of the gap, so the union has no waist narrower than
    ``radius * sqrt(3)/2``.
    """
    out: list[Disc] = []
    seen: set[tuple[float, float]] = set()

    def add(x: float, y: float) -> None:
        disc = Disc(layer, _round(x), _round(y), _round(radius))
        key = (disc.cx, disc.cy)
        if key in seen:
            return
        seen.add(key)
        out.append(disc)

    if not points:
        return out
    add(*points[0])
    for a, b in zip(points, points[1:]):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length <= _EPS:
            continue
        steps = max(1, int(math.ceil(length / radius)))
        for i in range(1, steps + 1):
            t = i / steps
            add(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    return out


def _widen_ends(discs: list[Disc], points: Sequence[tuple[float, float]],
                obstacles: Sequence[Obstacle],
                outline: Sequence[tuple[float, float]] | None,
                edge_clearance: float, layer: str,
                reserve_r: float) -> list[Disc]:
    """Give each end of the corridor whatever room actually exists there.

    The fan-outs are the only place skew can enter a pair (they are the only
    part of it that is not congruent by construction), so the ends are where
    room is worth most. The disc grows to the largest radius that still touches
    nothing — geometrically, against the exact shapes, not against the raster —
    capped at ``RESERVE_POCKET_MAX_MM``. It never shrinks below ``reserve_r``:
    a pocket smaller than the corridor is not a pocket.
    """
    if not discs:
        return discs
    out = list(discs)
    for index, point in ((0, points[0]), (len(out) - 1, points[-1])):
        clear = _clear_radius(point, obstacles, outline, edge_clearance)
        radius = _round_down(min(RESERVE_POCKET_MAX_MM, clear))
        if radius <= out[index].radius + _EPS:
            continue
        out[index] = Disc(layer, out[index].cx, out[index].cy, radius)
    return out


def _clear_radius(point: tuple[float, float], obstacles: Sequence[Obstacle],
                  outline: Sequence[tuple[float, float]] | None,
                  edge_clearance: float) -> float:
    """The largest disc centred on ``point`` that touches nothing.

    Geometric distance, with no clearance added: this measures where copper
    *is*, and a keepout that stops short of a pad by nothing at all still
    leaves the pad's own clearance to the pad's own copper untouched.
    """
    seg = (point[0], point[1], point[0], point[1])
    best = math.inf
    for obstacle in obstacles:
        best = min(best, obstacle.distance_to_segment(seg))
    if outline:
        for i in range(len(outline)):
            ax, ay = outline[i]
            bx, by = outline[(i + 1) % len(outline)]
            best = min(best, diffpair._seg_point_distance(
                ax, ay, bx, by, point[0], point[1]) - edge_clearance)
    return max(0.0, best)


def _exact_overlap(discs: Sequence[Disc], obstacles: Sequence[Obstacle],
                   outline: Sequence[tuple[float, float]] | None,
                   edge_clearance: float) -> tuple[str, float] | None:
    """Zero tolerance, measured against the real shapes rather than the raster.

    The A* corridor is clear by construction — every cell it walks is free in a
    grid dilated by ``reserve_r`` plus each obstacle's own clearance. This is
    the independent check on that construction, and it is the one that decides,
    because a raster is a model and a model is exactly as right as itself
    (ledger lesson E).
    """
    worst: tuple[str, float] | None = None
    for disc in discs:
        seg = (disc.cx, disc.cy, disc.cx, disc.cy)
        for obstacle in obstacles:
            gap = obstacle.distance_to_segment(seg) - disc.radius
            if gap < -_EPS and (worst is None or -gap > worst[1]):
                worst = (obstacle.label, -gap)
        if outline:
            for i in range(len(outline)):
                ax, ay = outline[i]
                bx, by = outline[(i + 1) % len(outline)]
                gap = (diffpair._seg_point_distance(ax, ay, bx, by,
                                                    disc.cx, disc.cy)
                       - edge_clearance - disc.radius)
                if gap < -_EPS and (worst is None or -gap > worst[1]):
                    worst = ("the board edge", -gap)
    return worst


def _courtyard_overlaps(discs: Sequence[Disc],
                        courtyards: Sequence[Obstacle]) -> tuple[str, ...]:
    hit: list[str] = []
    for obstacle in courtyards:
        for disc in discs:
            if disc.layer not in obstacle.layers:
                continue
            seg = (disc.cx, disc.cy, disc.cx, disc.cy)
            if obstacle.distance_to_segment(seg) - disc.radius < -_EPS:
                hit.append(obstacle.label)
                break
    return tuple(hit)


def _polyline_length(points: Sequence[tuple[float, float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(points, points[1:]))


def _swept_area(path_mm: float, radius: float,
                discs: Sequence[Disc]) -> float:
    """Area the corridor denies, in mm².

    A capsule of length ``path_mm`` and radius ``radius``, plus the extra ring
    of any end disc that grew past it. Deliberately an over-estimate: the
    discs overlap at every turn, so the true union is smaller, and a cap
    checked against an over-estimate never lets through more than it says.
    """
    area = 2 * radius * path_mm + math.pi * radius * radius
    for disc in discs:
        if disc.radius > radius + _EPS:
            area += math.pi * (disc.radius ** 2 - radius ** 2)
    return area


def _board_area(board: _Board, outline: Sequence[tuple[float, float]] | None
                ) -> float:
    if outline and len(outline) >= 3:
        total = 0.0
        for i in range(len(outline)):
            ax, ay = outline[i]
            bx, by = outline[(i + 1) % len(outline)]
            total += ax * by - bx * ay
        return abs(total) / 2
    w = diffpair._f(board.board.get("width"), 0.0) or 0.0
    h = diffpair._f(board.board.get("height"), 0.0) or 0.0
    return w * h


# ---------------------------------------------------------------------------
# The strangle test
# ---------------------------------------------------------------------------


def _strangle_test(board: _Board, obstacles: Sequence[Obstacle],
                   outline: Sequence[tuple[float, float]] | None,
                   profile: Any, discs: Sequence[Disc],
                   pair_keys: tuple[str, str]) -> list[str]:
    """Every net the corridor cuts off from itself, or an empty list.

    **Every** one, not the first. "Which nets did this break" is what a human
    acts on; "one of them" sends them back for another build to find the next.
    The pair's own nets are included on purpose and are the most important
    case: a corridor that is the board's only channel means the autorouter
    cannot connect the pair in the reserved build, and stage 0c needs exactly
    one trace per net to work with — so the reservation would cost the pair the
    route it was reserved for.

    Asked as a **difference**, twice on the same raster: which nets can reach
    across the board without the corridor, and which can with it. A net the
    grid cannot connect either way is not something the corridor broke — that
    is what makes a coarse raster honest here. The cell is
    ``STRANGLE_GRID_MM`` because this is reachability over a whole board, not
    geometry: at ``GRID_MM`` the same question costs sixteen times the memory
    and answers the same.

    The free space is what the *narrowest legal track* could use, from the fab
    profile — not what the pair needs. A corridor that leaves a 0.15 mm channel
    has not strangled anything; it has made the router work harder.
    """
    if _np is None:
        # A gate that cannot run is a refusal, not a pass (ledger lesson E).
        return ["<the strangle test needs numpy and it is not installed>"]
    layers = list(board.layers) or ["top"]
    track_half = float(getattr(profile, "min_trace_mm", 0.10) or 0.10) / 2
    edge = float(getattr(profile, "min_edge_clearance_mm", 0.2) or 0.2)

    if outline:
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        box = (min(xs), min(ys), max(xs), max(ys))
        raster_outline: Sequence[tuple[float, float]] | None = \
            diffpair._thinned(outline, 192)
    else:
        w = diffpair._f(board.board.get("width"), 50.0) or 50.0
        h = diffpair._f(board.board.get("height"), 50.0) or 50.0
        centre = board.board.get("center") or {}
        cx = diffpair._f(centre.get("x"), 0.0) or 0.0
        cy = diffpair._f(centre.get("y"), 0.0) or 0.0
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        raster_outline = None

    grid = _CoarseGrid(box, STRANGLE_GRID_MM)
    if grid.nx * grid.ny * len(layers) > 4_000_000:
        # Too large to raster honestly. Not a pass: the caller is told the
        # gate did not run, and the pipeline's own gate is what decides.
        return ["<the board is too large for the strangle raster>"]
    free = {}
    for name in layers:
        mask = grid.free_mask(
            [o for o in obstacles if name in o.layers], track_half,
            raster_outline, edge + track_half)
        free[name] = mask

    terminals = _net_terminals(board)
    if not terminals:
        return []

    before = _labels(free, layers)
    after_free = {name: mask.copy() for name, mask in free.items()}
    grid.block_discs(after_free, discs)
    after = _labels(after_free, layers)

    cut: list[str] = []
    for net, points in terminals.items():
        cells = [grid.snap(free, layers, x, y, name) for x, y, name in points]
        cells = [c for c in cells if c is not None]
        if len(cells) < 2:
            continue
        if len({before[c] for c in cells}) != 1:
            continue  # the raster could not connect it either way
        now_cells = [grid.snap(after_free, layers, x, y, name)
                     for x, y, name in points]
        if any(c is None for c in now_cells):
            cut.append(net)
            continue
        if len({after[c] for c in now_cells if c is not None}) != 1:
            cut.append(net)
    # Deterministic order, and the pair's own nets are ranked by the caller.
    return sorted(cut)


def _net_terminals(board: _Board) -> dict[str, list[tuple[float, float, str]]]:
    """Every net with more than one pad, and where those pads are."""
    out: dict[str, list[tuple[float, float, str]]] = {}
    for pad in board.by_type.get("pcb_smtpad", []):
        net = board.net_key_of_pcb_port(str(pad.get("pcb_port_id") or ""))
        x, y = diffpair._f(pad.get("x")), diffpair._f(pad.get("y"))
        if not net or x is None or y is None:
            continue
        out.setdefault(net, []).append((x, y, str(pad.get("layer") or "top")))
    for hole in board.by_type.get("pcb_plated_hole", []):
        net = board.net_key_of_pcb_port(str(hole.get("pcb_port_id") or ""))
        x, y = diffpair._f(hole.get("x")), diffpair._f(hole.get("y"))
        if not net or x is None or y is None:
            continue
        out.setdefault(net, []).append((x, y, "top"))
    return {net: points for net, points in out.items() if len(points) > 1}


class _CoarseGrid:
    """A cheap raster for the reachability question only."""

    def __init__(self, box: tuple[float, float, float, float], cell: float):
        self.x0, self.y0, x1, y1 = box
        self.cell = cell
        self.nx = max(1, int(math.ceil((x1 - self.x0) / cell)))
        self.ny = max(1, int(math.ceil((y1 - self.y0) / cell)))
        self._xs = self.x0 + (_np.arange(self.nx) + 0.5) * cell
        self._ys = self.y0 + (_np.arange(self.ny) + 0.5) * cell

    def _window(self, bx0: float, by0: float, bx1: float, by1: float):
        """The cells an obstacle can possibly reach.

        Not an optimisation — a correctness-preserving one, and the difference
        between seconds and minutes. Rasterising every obstacle over the whole
        board is 1,500 shapes x 122,000 cells on harness-puck; `_Grid.block`
        has always windowed and this is the same arithmetic at a coarser cell.
        """
        c0 = max(0, int(math.floor((bx0 - self.x0) / self.cell)))
        c1 = min(self.nx, int(math.ceil((bx1 - self.x0) / self.cell)) + 1)
        r0 = max(0, int(math.floor((by0 - self.y0) / self.cell)))
        r1 = min(self.ny, int(math.ceil((by1 - self.y0) / self.cell)) + 1)
        if c0 >= c1 or r0 >= r1:
            return None
        return r0, r1, c0, c1

    def free_mask(self, obstacles: Sequence[Obstacle], grow: float,
                  outline: Sequence[tuple[float, float]] | None,
                  edge_margin: float):
        xs = self._xs[_np.newaxis, :]
        ys = self._ys[:, _np.newaxis]
        blocked = _np.zeros((self.ny, self.nx), dtype=bool)
        for obstacle in obstacles:
            radius = obstacle.clearance + grow
            if obstacle.kind == "poly" and not obstacle.poly:
                continue
            bx0, by0, bx1, by1 = obstacle.bounds()
            window = self._window(bx0 - radius, by0 - radius,
                                  bx1 + radius, by1 + radius)
            if window is None:
                continue
            r0, r1, c0, c1 = window
            wxs = self._xs[c0:c1][_np.newaxis, :]
            wys = self._ys[r0:r1][:, _np.newaxis]
            if obstacle.kind == "disc":
                distance = (_np.hypot(wxs - obstacle.cx, wys - obstacle.cy)
                            - obstacle.radius)
            elif obstacle.kind == "capsule":
                distance = (diffpair._seg_distance_grid(wxs, wys, obstacle.seg)
                            - obstacle.half_width)
            else:
                distance = diffpair._poly_distance_grid(wxs, wys, obstacle.poly)
            blocked[r0:r1, c0:c1] |= distance <= radius
        if outline:
            inside = diffpair._point_in_poly_grid(xs, ys, outline)
            near = _np.zeros_like(inside)
            for i in range(len(outline)):
                ax, ay = outline[i]
                bx, by = outline[(i + 1) % len(outline)]
                near |= diffpair._seg_distance_grid(
                    xs, ys, (ax, ay, bx, by)) <= edge_margin
            blocked |= (~inside) | near
        return ~blocked

    def block_discs(self, free: dict[str, Any], discs: Sequence[Disc]) -> None:
        for disc in discs:
            mask = free.get(disc.layer)
            if mask is None:
                continue
            window = self._window(disc.cx - disc.radius, disc.cy - disc.radius,
                                  disc.cx + disc.radius, disc.cy + disc.radius)
            if window is None:
                continue
            r0, r1, c0, c1 = window
            wxs = self._xs[c0:c1][_np.newaxis, :]
            wys = self._ys[r0:r1][:, _np.newaxis]
            mask[r0:r1, c0:c1] &= (
                _np.hypot(wxs - disc.cx, wys - disc.cy) > disc.radius)

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        col = int((x - self.x0) / self.cell)
        row = int((y - self.y0) / self.cell)
        return (max(0, min(self.ny - 1, row)), max(0, min(self.nx - 1, col)))

    def snap(self, free: dict[str, Any], layers: Sequence[str],
             x: float, y: float, layer: str) -> tuple[int, int, int] | None:
        """A terminal's nearest free cell, breadth-first so it is the nearest
        one and the tie-break is a fixed neighbour order."""
        if layer not in layers:
            layer = layers[0]
        index = layers.index(layer)
        mask = free[layer]
        row, col = self.cell_of(x, y)
        if mask[row, col]:
            return (index, row, col)
        seen = {(row, col)}
        frontier = deque([(row, col)])
        limit = int(2.0 / self.cell)
        for _ in range(limit):
            for _ in range(len(frontier)):
                r, c = frontier.popleft()
                for dr, dc in ((-1, 0), (0, -1), (0, 1), (1, 0)):
                    nr, nc = r + dr, c + dc
                    if not (0 <= nr < self.ny and 0 <= nc < self.nx):
                        continue
                    if (nr, nc) in seen:
                        continue
                    seen.add((nr, nc))
                    if mask[nr, nc]:
                        return (index, nr, nc)
                    frontier.append((nr, nc))
            if not frontier:
                return None
        return None


def _labels(free: dict[str, Any], layers: Sequence[str]
            ) -> dict[tuple[int, int, int], int]:
    """Connected components of free space over the union of layers.

    A layer change is allowed wherever both layers are free, which is what a
    via is. Eight-connected in plane, because a router turns at 45 degrees.
    """
    out: dict[tuple[int, int, int], int] = {}
    ny, nx = free[layers[0]].shape
    masks = [free[name] for name in layers]
    steps = ((-1, 0), (1, 0), (0, -1), (0, 1),
             (-1, -1), (-1, 1), (1, -1), (1, 1))
    label = 0
    for start_layer in range(len(layers)):
        mask = masks[start_layer]
        for row in range(ny):
            for col in range(nx):
                if not mask[row, col] or (start_layer, row, col) in out:
                    continue
                label += 1
                stack = [(start_layer, row, col)]
                out[(start_layer, row, col)] = label
                while stack:
                    li, r, c = stack.pop()
                    for dr, dc in steps:
                        nr, nc = r + dr, c + dc
                        if not (0 <= nr < ny and 0 <= nc < nx):
                            continue
                        if not masks[li][nr, nc] or (li, nr, nc) in out:
                            continue
                        out[(li, nr, nc)] = label
                        stack.append((li, nr, nc))
                    for other in range(len(masks)):
                        if other == li or not masks[other][r, c]:
                            continue
                        if (other, r, c) in out:
                            continue
                        out[(other, r, c)] = label
                        stack.append((other, r, c))
    return out


# ---------------------------------------------------------------------------
# Getting it in front of the autorouter
# ---------------------------------------------------------------------------


def reservation_tsx(reservation: Reservation) -> str:
    """The reservation, as TSX children of ``<board>``."""
    lines = [
        f"  {{/* reserved corridor for {reservation.net_p}/"
        f"{reservation.net_n} — written by circuitpy.reserve, stripped from "
        f"circuit.json at stage 0R' before anything reads it */}}"
    ]
    lines += [f"  {d.as_tsx()}" for d in reservation.discs]
    return "\n".join(lines)


@dataclass
class InjectResult:
    ok: bool
    reason: str = ""
    discs: int = 0

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"injected": self.ok, "discs": self.discs}
        if self.reason:
            out["reason"] = self.reason
        return out


def inject_reservation(board_source: Path,
                       reservation: Reservation) -> InjectResult:
    """Write the reservation into the **mirrored** board source.

    Only ever the copy inside ``.circuit/build/`` — the same rule and the same
    insertion point as ``generation._set_autorouter_effort``: the user's file is
    never rewritten, so "generated files are never hand-edited, nothing is
    overwritten" still holds. The caller is responsible for handing this the
    mirror; this function refuses nothing on that score because it cannot tell,
    and saying so is better than pretending.
    """
    try:
        text = board_source.read_text(encoding="utf-8")
    except OSError as exc:
        return InjectResult(False, f"could not read {board_source.name}: {exc}")
    return _write_injected(board_source, text, reservation)


def _write_injected(board_source: Path, text: str,
                    reservation: Reservation) -> InjectResult:
    patched = inject_into_source(text, reservation)
    if isinstance(patched, str) and patched.startswith("!"):
        return InjectResult(False, patched[1:])
    try:
        board_source.write_text(patched, encoding="utf-8")
    except OSError as exc:
        return InjectResult(False, f"could not write {board_source.name}: {exc}")
    return InjectResult(True, discs=len(reservation.discs))


def inject_into_source(text: str, reservation: Reservation) -> str:
    """The patched source, or ``"!"`` followed by why not.

    Split out from the file write so the text transform can be tested without
    a filesystem, which is the only part of it that can be wrong.
    """
    if not reservation.discs:
        return "!the reservation has no discs to write"
    span = _board_tag_end(text)
    if span is None:
        return ("!the board source has no `<board …>` opening tag to insert "
                "children after")
    if "circuitpy.reserve" in text:
        return "!this source already carries a reservation"
    body = reservation_tsx(reservation)
    return text[:span] + "\n" + body + text[span:]


def _board_tag_end(text: str) -> int | None:
    """The index just past the ``<board …>`` opening tag.

    A scanner rather than a regex, because a real board tag carries JSX
    expression props, template literals and comments, and every one of them can
    hold a character the regex would stop on. All three of our example boards
    exercise a different part of this:

    * ``width={`${BOARD_W}mm`}`` — a template literal with braces inside it.
    * ``outline={[{ x: 0, y: 0 }]}`` — a ``>``-free but brace-heavy prop.
    * **terminal-keyboard ends its tag with a block comment**, so the last two
      characters before the ``>`` are ``*/``. The first version of this
      function tested "does the text before ``>`` end in ``/``" to spot a
      self-closing tag, and on that board it answered yes — the feature would
      have refused to inject on one of the three boards it exists for, with a
      message blaming a self-closing tag that is not there. Comments are
      scanned, not skipped over.
    """
    match = re.search(r"<board\b", text)
    if match is None:
        return None
    index = match.end()
    depth = 0
    quote: str | None = None
    comment: str | None = None  # "block" or "line"
    previous = ""  # last significant character, for the `/>` test
    while index < len(text):
        char = text[index]
        if comment == "block":
            if text.startswith("*/", index):
                comment = None
                index += 2
                continue
        elif comment == "line":
            if char == "\n":
                comment = None
        elif quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif text.startswith("/*", index):
            comment = "block"
            index += 2
            continue
        elif text.startswith("//", index):
            comment = "line"
            index += 2
            continue
        elif char in "\"'`":
            quote = char
            previous = char
        elif char == "{":
            depth += 1
            previous = char
        elif char == "}":
            depth -= 1
            previous = char
        elif char == ">" and depth == 0:
            if previous == "/":
                return None  # self-closing: no children, nothing to insert
            return index + 1
        elif not char.isspace():
            previous = char
        index += 1
    return None


# ---------------------------------------------------------------------------
# Taking it back out
# ---------------------------------------------------------------------------


@dataclass
class ReleaseResult:
    """What the strip pass did, or refused to do.

    ``ok`` false means **nothing was removed** and the caller must throw the
    whole reserved attempt away and restore the attempt it replaced. There is
    no partial release: a corridor half-stripped is a routing restriction no
    source explains, left in an artifact the fab never sees (harness-puck's
    committed `kicad-project.zip` contains 0 occurrences of `keepout` or
    `rule_area` while its circuit.json carries 4).
    """

    ok: bool
    removed: int = 0
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"released": self.ok, "removed": self.removed}
        if self.reason:
            out["reason"] = self.reason
        return out


def strip_reserved_keepouts(elements: Sequence[dict], discs: Sequence[Disc]
                            ) -> tuple[list[dict] | None, ReleaseResult]:
    """Remove exactly the keepouts we wrote, or remove nothing.

    One match per recorded disc, matched on ``(shape, layers, center, radius)``
    at ``RESERVE_MATCH_TOL_MM``, and every match claimed by exactly one disc.
    Zero matches or two and the whole release fails, because that rule is the
    only thing protecting a keepout we did not write — and every keepout on
    every board today is block-authored: harness-puck 4, terminal-keyboard 7,
    hydrate-coaster 5, with ledger #1 and #7 closed by exactly those elements.
    """
    if not discs:
        return list(elements), ReleaseResult(True, 0)
    keepouts = [(i, e) for i, e in enumerate(elements)
                if isinstance(e, dict) and e.get("type") == "pcb_keepout"]
    claimed: dict[int, int] = {}
    for order, disc in enumerate(discs):
        hits = [i for i, e in keepouts if disc.matches(e)]
        if len(hits) != 1:
            nearest = _nearest_note(disc, [e for _, e in keepouts])
            return None, ReleaseResult(
                False, 0,
                f"the reserved keepout at ({disc.cx:.4f}, {disc.cy:.4f}) r="
                f"{disc.radius:.4f} on {disc.layer} matches {len(hits)} "
                f"keepouts in the artifact; exactly one is required, so "
                f"nothing was removed and the reserved attempt is discarded"
                + nearest,
            )
        index = hits[0]
        if index in claimed:
            return None, ReleaseResult(
                False, 0,
                f"two reserved discs (#{claimed[index]} and #{order}) match the "
                "same keepout; nothing was removed",
            )
        claimed[index] = order
    kept = [e for i, e in enumerate(elements) if i not in claimed]
    return kept, ReleaseResult(True, len(claimed))


def _nearest_note(disc: Disc, keepouts: Sequence[dict]) -> str:
    """Name the near miss. A coordinate frame that shifted every reservation by
    the board centre would show up here as one constant offset, in the first
    build, instead of as a silent feature that never fires."""
    best: tuple[float, dict] | None = None
    for keepout in keepouts:
        distance = disc.distance_from(keepout)
        if distance is None:
            continue
        if best is None or distance < best[0]:
            best = (distance, keepout)
    if best is None:
        return " (the artifact carries no keepout at all)"
    centre = best[1].get("center") or {}
    return (f" (nearest keepout is {best[0]:.4f}mm away at "
            f"({centre.get('x')}, {centre.get('y')}), shape "
            f"{best[1].get('shape')!r}, radius {best[1].get('radius')!r})")


def release_reservation(path: Path,
                        reservation: Reservation | Sequence[Disc]
                        ) -> ReleaseResult:
    """Strip the reservation out of a circuit.json, in place.

    **Stage 0R', immediately after 0a and before 0b.** Everything downstream
    reads a keepout as a hard obstacle at clearance 0.0 — `diffpair` through
    `collect_obstacles`, and `powerwidth` through the same collector — so a
    corridor left standing blocks the pass it exists to serve *and* necks every
    rail beside it. Our own router has to see the true board too.
    """
    discs = (reservation.discs if isinstance(reservation, Reservation)
             else tuple(reservation))
    try:
        elements = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ReleaseResult(False, 0, f"could not read {path.name}: {exc}")
    if not isinstance(elements, list):
        return ReleaseResult(False, 0,
                             f"{path.name} is not an element array")
    kept, result = strip_reserved_keepouts(elements, discs)
    if kept is None or not result.ok:
        return result
    try:
        path.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    except OSError as exc:
        return ReleaseResult(False, 0, f"could not write {path.name}: {exc}")
    return result


def reservation_from_record(record: Any) -> tuple[Disc, ...] | None:
    """Read back what `Reservation.record()` wrote — the pipeline carries JSON
    between the injecting stage and the releasing one, and JSON is what a
    sidecar can hold. Anything malformed is None: a reservation we cannot read
    in full is one we must not strip by halves."""
    entries: Any
    if isinstance(record, dict):
        entries = record.get("keepouts")
    else:
        entries = record
    if not isinstance(entries, (list, tuple)) or not entries:
        return None
    out: list[Disc] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("shape") != "circle":
            return None
        layers = entry.get("layers")
        centre = entry.get("center") or {}
        if (not isinstance(layers, (list, tuple)) or len(layers) != 1
                or not isinstance(centre, dict)):
            return None
        try:
            out.append(Disc(str(layers[0]), float(centre["x"]),
                            float(centre["y"]), float(entry["radius"])))
        except (TypeError, ValueError, KeyError):
            return None
    return tuple(out)
