"""Design-for-assembly: what the pick-and-place line needs.

**The gap this closes.** Our four existing detection sources all reason about
copper. The assembly line reasons about *bodies*: a nozzle has to descend onto
a part without hitting its neighbour, a conveyor grips the board edges, and the
Economic tier places one side only. A board can be DRC-clean, gerber-clean,
fully orderable — and come back mis-assembled, or be held at review.

Measured on our own three example boards before a line of this was written:

* ``terminal-keyboard`` — J1's courtyard extends **1.151 mm past the board
  outline**, and SW2/SW3 sit **1.80 mm** from the edge where the line wants
  2.5 mm.
* ``harness-puck`` — **nine courtyard overlaps**, none of which
  ``@tscircuit/checks`` reports, because its overlap test compares
  ``pcb_courtyard_rect`` against ``pcb_courtyard_rect`` and these collisions
  are rect-against-``pcb_courtyard_outline``.

That last one is the clearest illustration of why a second implementation is
worth having even when a first one exists.

**What this cannot see.** Component *height* (circuit.json carries no z extent
for most footprints, so a tall part beside a connector is invisible), nozzle
diameter per package, and whether a specific part is actually on a reel. Those
are reported as coverage, never as a pass.
"""

from __future__ import annotations

import itertools
import math

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board, Component
from verifylib.rules import (
    ASSEMBLY_TIERS,
    JLCPCB_ECONOMIC,
    POLARISED_PREFIXES,
    ROTATION_PRONE_HINTS,
    AssemblyRules,
)

#: Parts intended to sit at the board edge. A USB-C receptacle is *supposed* to
#: overhang, and grading it against the 2.5 mm rule would flag every board we
#: build — the "gate set to a preference" failure in reverse. They are still
#: reported, at info, with the measurement, because the placement preview is
#: where a human confirms them.
_EDGE_INTENDED_FTYPES = {"simple_connector"}
_EDGE_INTENDED_PREFIXES = ("J", "P", "CN", "USB")


def _edge_intended(component: Component) -> bool:
    if component.ftype in _EDGE_INTENDED_FTYPES:
        return True
    return component.prefix in _EDGE_INTENDED_PREFIXES


def _edge_margin(component: Component, outline) -> float:
    """Smallest distance from any of the part's keep-out polygons to the board
    edge. Uses the real polygons, not their bounding box — a part rotated 22.5
    degrees has a bbox 40% larger than itself and would be graded as far closer
    to the edge than it is."""
    return min(poly.bounds.inset_margin(outline) for poly in component.keepout_parts)


def _pin_pitch(component: Component) -> float | None:
    """Smallest centre-to-centre distance between two of a part's pads."""
    centres = [(p.x, p.y) for p in component.pads]
    if len(centres) < 2:
        return None
    return min(
        math.hypot(a[0] - b[0], a[1] - b[1])
        for a, b in itertools.combinations(centres, 2)
    )


@never_raises
def _edge_clearance(board: Board, rules: AssemblyRules) -> list[Finding]:
    if board.outline is None:
        return []
    out: list[Finding] = []
    for component in board.placed():
        margin = _edge_margin(component, board.outline)
        intended = _edge_intended(component)
        if margin < 0:
            severity = "info" if intended else "error"
            note = (
                " — intended for an edge-mounted part, confirm it on JLCPCB's "
                "placement preview"
                if intended
                else " — a part outside the outline cannot be placed"
            )
            out.append(
                finding(
                    component.name,
                    "dfa_off_board",
                    f"{component.name}'s keep-out extends {abs(margin):.3f}mm "
                    f"past the board outline{note}",
                    severity,
                )
            )
        elif margin < rules.body_to_edge_hard_mm:
            out.append(
                finding(
                    component.name,
                    "dfa_edge_clearance",
                    f"{component.name} sits {margin:.3f}mm from the board edge; "
                    f"the assembly line grips the outer "
                    f"{rules.body_to_edge_mm:g}mm and needs "
                    f"{rules.body_to_edge_hard_mm:g}mm as an absolute floor",
                    "info" if intended else "error",
                )
            )
        elif margin < rules.body_to_edge_mm:
            out.append(
                finding(
                    component.name,
                    "dfa_edge_clearance",
                    f"{component.name} sits {margin:.3f}mm from the board edge; "
                    f"JLCPCB assembly asks for {rules.body_to_edge_mm:g}mm of "
                    "component-body clearance (conveyor rails and depanel "
                    "routing pass through that strip)",
                    "info" if intended else "warning",
                )
            )
    return out


@never_raises
def _part_spacing(board: Board, rules: AssemblyRules) -> list[Finding]:
    out: list[Finding] = []
    components = board.placed()
    for a, b in itertools.combinations(components, 2):
        if a.layer != b.layer:
            continue
        # Cheap reject on the union boxes before the per-rect work.
        if a.keepout.gap_to(b.keepout) > 2.0:
            continue
        gap = a.keepout_gap_to(b)
        if gap < 0:
            out.append(
                finding(
                    f"{a.name},{b.name}",
                    "dfa_courtyard_overlap",
                    f"{a.name} and {b.name} overlap by {abs(gap):.3f}mm of "
                    "keep-out; the courtyard is the space the placement nozzle "
                    "and rework iron need, so overlapping ones mean one part "
                    "cannot be worked on without disturbing the other",
                    "error",
                )
            )
            continue
        pad_gap = a.pad_gap_to(b)
        if pad_gap is not None and pad_gap < rules.smd_to_smd_mm:
            out.append(
                finding(
                    f"{a.name},{b.name}",
                    "dfa_part_spacing",
                    f"{a.name} and {b.name} have {pad_gap:.3f}mm between their "
                    f"nearest pads; JLCPCB asks for "
                    f"{rules.smd_to_smd_mm:g}mm between adjacent SMD parts "
                    "(solder bridging and placement-head access)",
                    "warning",
                )
            )
    return out


@never_raises
def _sides(board: Board, rules: AssemblyRules) -> list[Finding]:
    if rules.smt_sides >= 2:
        return []
    bottom = [c for c in board.placed() if c.layer == "bottom"]
    if not bottom:
        return []
    names = ", ".join(sorted(c.name for c in bottom)[:8])
    more = f" (+{len(bottom) - 8} more)" if len(bottom) > 8 else ""
    return [
        finding(
            names.split(",")[0].strip(),
            "dfa_bottom_side",
            f"{len(bottom)} part(s) are on the bottom layer ({names}{more}) but "
            "Economic PCBA places one side only — they will not be fitted, and "
            "the order will not tell you so",
            "error",
        )
    ]


@never_raises
def _pin_pitch_check(board: Board, rules: AssemblyRules) -> list[Finding]:
    out: list[Finding] = []
    for component in board.placed():
        pitch = _pin_pitch(component)
        if pitch is None:
            continue
        if pitch < rules.min_pin_pitch_mm - 1e-6:
            out.append(
                finding(
                    component.name,
                    "dfa_pin_pitch",
                    f"{component.name}'s finest pad pitch is {pitch:.3f}mm; the "
                    f"Economic line places down to "
                    f"{rules.min_pin_pitch_mm:g}mm (Standard PCBA does 0.35mm)",
                    "error",
                )
            )
        elif pitch < rules.min_pin_pitch_mm + 1e-6:
            out.append(
                finding(
                    component.name,
                    "dfa_pin_pitch",
                    f"{component.name} sits exactly on the "
                    f"{rules.min_pin_pitch_mm:g}mm pitch floor — legal, with no "
                    "margin for a footprint revision",
                    "info",
                )
            )
    return out


@never_raises
def _board_size(board: Board, rules: AssemblyRules) -> list[Finding]:
    if board.outline is None:
        return []
    smallest = min(board.outline.width, board.outline.height)
    if smallest < rules.min_board_mm:
        return [
            finding(
                "board",
                "dfa_board_size",
                f"board is {board.outline.width:g}x{board.outline.height:g}mm; "
                f"the assembly line needs at least {rules.min_board_mm:g}mm on "
                "a side (the fab alone would accept 3mm — this is an assembly "
                "limit, not a fab one)",
                "error",
            )
        ]
    return []


@never_raises
def _holes_in_keepouts(board: Board) -> list[Finding]:
    """A mounting hole inside a part's keep-out means the screw head, standoff
    or drill breakout lands on the part. KiCad flags this as
    ``pth_inside_courtyard``; nothing in our chain does."""
    out: list[Finding] = []
    for hole in board.holes:
        for component in board.placed():
            if component.pcb_id and hole.component_id == component.pcb_id:
                continue  # the part's own hole
            for poly in component.keepout_parts:
                radius = hole.diameter / 2
                if poly.distance_to_point(hole.x, hole.y) < radius:
                    out.append(
                        finding(
                            component.name,
                            "dfa_hole_in_keepout",
                            f"a {hole.diameter:.2f}mm "
                            f"{'plated' if hole.plated else 'mounting'} hole at "
                            f"({hole.x:.2f}, {hole.y:.2f}) falls inside "
                            f"{component.name}'s keep-out",
                            "warning",
                        )
                    )
                    break
    return out


#: A hole this wide or wider is a mounting point, not a via or a component
#: through-hole leg. M2 clears at 2.2mm and the corpus uses M2.5 (2.7mm); the
#: largest via drill in the fab profile is 0.3mm and a USB-C leg is under 1mm,
#: so 1.0mm separates them with room to spare.
MOUNTING_HOLE_MIN_MM = 1.0

#: Below this on both sides, a board is small enough that two screws or an
#: enclosure's own clips are a normal answer and four holes would be unusual.
MOUNTING_SCORED_ABOVE_MM = 50.0

#: What a rigid mount takes. **Not invented here** — measured across the whole
#: corpus 2026-08-25 and then confirmed by the owner, who has built the boards:
#: 27 of 32 boards carry four or more, at the corners. The five that do not are
#: harness-puck (3) and weather-badge-5, -8, -12 and -28 (2 each) — and every
#: one of those four put its pair on a **diagonal**. weather-badge-29 is the
#: only board in the corpus with both holes on one edge.
MOUNTING_POINTS_EXPECTED = 4


@never_raises
def _mounting_support(board: Board) -> list[Finding]:
    """Whether the board can actually be screwed down.

    **Why this exists.** Nothing scored mounting, and that made it a one-way
    trade: a mounting hole carries a keep-out, a keep-out eats a routing
    channel, and on weather-badge-28 a third hole failed the build outright
    (``pcb_trace_error`` on ``pcb_keepout_2``). So dropping a hole cost nothing
    on the scoreboard while keeping it could cost a build, and the board agent
    optimised exactly what it could measure — correctly, against a ruler that
    was missing a dimension. wb-27 has four at the corners, wb-28 two on a
    diagonal, wb-29 two on the same edge.

    It is a **warning**, deliberately. An error would block wb-28 and wb-29 with
    no move available to the agent but to grow the board or drop parts, and that
    is a product decision rather than one a gate should make. `macropad-6` is
    60x45mm — the same size as wb-28 — with four holes at the corners, so the
    room is a function of what is on the board, not of its size.
    """
    if board.outline is None:
        return []
    holes = [
        h for h in board.holes
        if h.diameter >= MOUNTING_HOLE_MIN_MM and h.component_id is None
    ]
    longest = max(board.outline.width, board.outline.height)
    if longest <= MOUNTING_SCORED_ABOVE_MM:
        return []

    out: list[Finding] = []
    if len(holes) < MOUNTING_POINTS_EXPECTED:
        out.append(
            finding(
                "board",
                "dfa_mounting_points",
                f"the board is {board.outline.width:g}x{board.outline.height:g}mm "
                f"and carries {len(holes)} mounting point(s); a rigid mount "
                f"takes {MOUNTING_POINTS_EXPECTED} and 27 of the 32 boards "
                "built so far have four at the corners. Fewer is a real choice "
                "on a board with no room — make it on purpose, and say so",
                "warning",
            )
        )
    if len(holes) >= 2:
        xs = {round(h.x, 3) for h in holes}
        ys = {round(h.y, 3) for h in holes}
        if len(xs) == 1 or len(ys) == 1:
            axis = "x" if len(xs) == 1 else "y"
            out.append(
                finding(
                    "board",
                    "dfa_mounting_collinear",
                    f"every mounting point shares one {axis} — they sit on a "
                    "single edge, so the rest of the board is cantilevered off "
                    "that line and free to flex and rotate about it. No other "
                    "board in the corpus does this; the ones with two holes put "
                    "them on a diagonal",
                    "warning",
                )
            )
    return out


@never_raises
def _rotation_watchlist(board: Board) -> list[Finding]:
    """JLCPCB's zero-rotation convention differs from EDA output for many
    packages and their auto-correction is imperfect, so ORDER.md tells the user
    to eyeball the placement preview. This names *which* parts to look at."""
    watch: list[tuple[str, str]] = []
    for component in board.placed():
        haystack = " ".join(
            filter(None, [component.name, component.ftype or "", component.lcsc or ""])
        ).lower()
        hit = next((h for h in ROTATION_PRONE_HINTS if h in haystack), None)
        polarised = component.prefix in POLARISED_PREFIXES
        if hit or (polarised and len(component.pads) <= 12):
            watch.append((component.name, hit or "polarised"))
    if not watch:
        return []
    watch.sort()
    listed = ", ".join(f"{name}" for name, _ in watch[:20])
    more = f" (+{len(watch) - 20} more)" if len(watch) > 20 else ""
    return [
        finding(
            "board",
            "dfa_rotation_watchlist",
            f"{len(watch)} part(s) are in package families whose zero-rotation "
            f"convention differs between EDA output and JLCPCB's library: "
            f"{listed}{more}. Check pin-1 on each in the placement preview "
            "before paying — that screen is the only place this is catchable",
            "info",
        )
    ]


def check(board: Board, *, assembly: bool = True, tier: str = "economic") -> CheckResult:
    """Run every DFA rule. ``assembly=False`` (bare PCB) skips the line rules
    but keeps the ones that are about the board itself."""
    rules = ASSEMBLY_TIERS.get(tier, JLCPCB_ECONOMIC)
    coverage = Coverage(unit="components", total=len(board.components))
    coverage.examined = len(board.placed())
    without_keepout = sum(1 for c in board.placed() if not c.courtyard_parts)
    if without_keepout:
        coverage.skip(
            f"{without_keepout} part(s) declare no courtyard — their footprint "
            "bounding box was used instead, which is looser than a real keep-out"
        )
    coverage.skip("component height (circuit-json carries no z extent)")
    coverage.skip("nozzle reach per package (needs the assembler's own table)")

    findings: list[Finding] = []
    findings += _part_spacing(board, rules)
    findings += _holes_in_keepouts(board)
    findings += _mounting_support(board)
    if assembly:
        findings += _edge_clearance(board, rules)
        findings += _sides(board, rules)
        findings += _pin_pitch_check(board, rules)
        findings += _board_size(board, rules)
        findings += _rotation_watchlist(board)

    notes = [f"graded against JLCPCB {tier} PCBA rules"]
    if not assembly:
        notes.append("bare-PCB order: line rules (edge, sides, pitch) not applied")
    return CheckResult(
        name="assembly", findings=findings, coverage=coverage, notes=notes
    )
