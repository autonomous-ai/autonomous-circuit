"""Legality: is this copper buildable?

Two sources, and the split is deliberate.

**What the pipeline already checks, the pipeline checks.**
:func:`pipeline_findings` renders the solution as circuit.json and runs
``circuitpy.checks.dfm_warnings`` over it — the same function, the same profile,
the same numbers that decide ``fab.ready``. Copper-to-drill, trace width, via
geometry, annular ring, pad-to-edge and power width all come from there. A
scorer that disagrees with the pipeline is worse than no scorer, so it does not
get its own opinion about any of those.

**What nothing upstream of KiCad can see, this module checks.** Copper-to-copper
clearance and shorts have no representation in ``circuit.json`` at all: today
they are found by exporting gerbers and running KiCad DRC, which costs a
toolchain, a temp directory and about a minute. A router being scored a
thousand times cannot pay that, and — more to the point — a router cannot
*consult* it while routing. So this module owns five checks:

======================  ==========================================
``short``               copper of two nets overlapping
``clearance``           copper of two nets closer than the gate
``via_in_pad``          a via drilled inside an SMD pad
``keepout``             copper inside a declared keepout
``trace_edge``          a trace off the board or too near its edge
======================  ==========================================

The clearance number is the pipeline's own: JLC holds 0.10mm and we block at
``min_clearance - drc_tolerance`` = 0.09mm, exactly as
``kicad_normalize`` configures KiCad. Between 0.09 and 0.10 is a warning, and a
router that aims at 0.10 will live there — which is the whole diagnosis of the
router we ship.

**What is not checked, said out loud.** :data:`COVERAGE_GAPS` is returned with
every result. v1 does not check plane islanding (a trace cutting a poured plane
into two pieces), acid traps, or copper balance, and it models a rectangular pad
as its inscribed stadium, which rounds corners inward — that can miss a finding
at a pad corner, never invent one. A cross-check against KiCad DRC lives in
``tests/test_kicad_agreement.py`` and skips when kicad-cli is absent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from routerlib.geometry import (
    Capsule,
    GridIndex,
    PolygonIndex,
    capsule_gap,
    disc_capsule,
    distance_to_polygon,
    pad_capsule,
    point_in_polygon,
    rect_capsule,
    segment_capsule,
)
from routerlib.model import (
    BOTTOM,
    TOP,
    Pad,
    Point,
    RoutingProblem,
    RoutingSolution,
)

#: Checks this module knowingly does not perform. Shipped with every result so
#: a clean score is never read as "everything was looked at".
COVERAGE_GAPS: tuple[str, ...] = (
    "plane islanding — a trace that cuts a poured plane in two is not detected",
    "acid traps and copper balance — no manufacturability heuristics beyond the rule table",
    "rectangular pads are modelled as their inscribed stadium, so a finding at a "
    "sharp pad corner can be missed (never invented)",
    "solder mask, silkscreen and paste are out of scope — they are not routing",
)

#: DFM findings no router can influence: they are properties of the placement
#: and the board size. Reported separately rather than counted against a route.
BOARD_LEVEL_KINDS: frozenset[str] = frozenset(
    {"dfm_board_size", "dfm_price_tier", "dfm_thickness", "board_exceeds_envelope"}
)


@dataclass(frozen=True, order=True)
class Violation:
    """One reason the board would be scrapped, or one reason to worry."""

    kind: str
    severity: str
    detail: str
    layer: str = ""
    at_x: float = 0.0
    at_y: float = 0.0
    items: tuple[str, ...] = ()
    source: str = "routerlib"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "detail": self.detail,
            "layer": self.layer,
            "at": [self.at_x, self.at_y],
            "items": list(self.items),
            "source": self.source,
        }


@dataclass(frozen=True)
class CopperItem:
    """Anything conductive, reduced to a capsule and a net.

    Unnetted copper (a mechanical SMD pad, a fiducial) gets a synthetic net id
    of its own, so it is correctly treated as something every real net must
    keep away from rather than as free space.
    """

    id: str
    net: str
    layers: tuple[str, ...]
    capsule: Capsule
    kind: str
    detail: str = ""


def _pad_net(pad: Pad) -> str:
    return pad.net or f"__unnetted__{pad.id}"


def copper_items(
    problem: RoutingProblem, solution: RoutingSolution
) -> list[CopperItem]:
    """Every piece of copper on the board, in a deterministic order.

    Pads and pre-existing copper first (they are the board), then the
    solution's traces and vias.
    """
    items: list[CopperItem] = []
    for pad in problem.pads:
        items.append(
            CopperItem(
                id=pad.id,
                net=_pad_net(pad),
                layers=tuple(pad.layers),
                capsule=pad_capsule(pad),
                kind="pad",
                detail=f"{pad.component}:{pad.id}" if pad.component else pad.id,
            )
        )
    for source, tag in ((problem.existing_traces, "existing"), (solution.traces, "trace")):
        for trace in source:
            for index, (a, b) in enumerate(trace.segments):
                items.append(
                    CopperItem(
                        id=f"{trace.id}#{index}",
                        net=trace.net or f"__unnetted__{trace.id}",
                        layers=(trace.layer,),
                        capsule=segment_capsule(a.x, a.y, b.x, b.y, trace.width_mm),
                        kind=tag,
                        detail=f"{tag} on {trace.layer}",
                    )
                )
    for source, tag in ((problem.existing_vias, "existing_via"), (solution.vias, "via")):
        for via in source:
            items.append(
                CopperItem(
                    id=via.id,
                    net=via.net or f"__unnetted__{via.id}",
                    layers=(TOP, BOTTOM),
                    capsule=disc_capsule(via.center.x, via.center.y, via.pad_mm),
                    kind=tag,
                    detail=f"{tag} {via.id}",
                )
            )
    return items


def _index_by_layer(items: Sequence[CopperItem]) -> dict[str, GridIndex]:
    grids: dict[str, GridIndex] = {}
    for item in items:
        for layer in item.layers:
            grids.setdefault(layer, GridIndex(cell_mm=2.0)).insert(item.capsule, item)
    return grids


# ---------------------------------------------------------------------------
# The checks routerlib owns
# ---------------------------------------------------------------------------


def copper_clearance_findings(
    problem: RoutingProblem, solution: RoutingSolution
) -> list[Violation]:
    """Shorts and clearance, same layer, different nets.

    Reported once per pair, keyed on the closer of the two ids, so a trace that
    runs beside another for 20mm produces one finding and not forty.
    """
    rules = problem.rules
    items = copper_items(problem, solution)
    grids = _index_by_layer(items)
    worst: dict[tuple[str, str], tuple[float, CopperItem, CopperItem, str]] = {}

    margin = max(rules.min_clearance_mm, rules.target_clearance_mm) + 0.01
    for item in items:
        for layer in item.layers:
            grid = grids.get(layer)
            if grid is None:
                continue
            for _, other in grid.query(item.capsule, margin=margin):
                if other.id == item.id or other.net == item.net:
                    continue
                gap = capsule_gap(item.capsule, other.capsule)
                if gap >= rules.min_clearance_mm - 1e-9:
                    continue
                key = tuple(sorted((item.id, other.id)))  # type: ignore[assignment]
                if key not in worst or gap < worst[key][0]:  # type: ignore[index]
                    worst[key] = (gap, item, other, layer)  # type: ignore[index]

    out: list[Violation] = []
    for gap, a, b, layer in worst.values():
        ax, ay = _capsule_mid(a.capsule)
        if gap < 0.0:
            out.append(
                Violation(
                    kind="short",
                    severity="error",
                    detail=(
                        f"{a.kind} on net {_pretty(a.net)} overlaps {b.kind} on net "
                        f"{_pretty(b.net)} by {abs(gap):.3f}mm — these two nets are "
                        "one net on the built board"
                    ),
                    layer=layer,
                    at_x=ax,
                    at_y=ay,
                    items=(a.id, b.id),
                )
            )
        elif gap < rules.clearance_gate_mm - 1e-9:
            out.append(
                Violation(
                    kind="clearance",
                    severity="error",
                    detail=(
                        f"{a.kind} ({_pretty(a.net)}) passes {gap:.3f}mm from {b.kind} "
                        f"({_pretty(b.net)}); the fab needs "
                        f"{rules.min_clearance_mm:g}mm and we block below "
                        f"{rules.clearance_gate_mm:g}mm"
                    ),
                    layer=layer,
                    at_x=ax,
                    at_y=ay,
                    items=(a.id, b.id),
                )
            )
        else:
            out.append(
                Violation(
                    kind="clearance",
                    severity="warning",
                    detail=(
                        f"{a.kind} ({_pretty(a.net)}) passes {gap:.3f}mm from {b.kind} "
                        f"({_pretty(b.net)}) — above the "
                        f"{rules.clearance_gate_mm:g}mm gate but under the "
                        f"{rules.min_clearance_mm:g}mm fab floor: legal only "
                        "because of measurement tolerance"
                    ),
                    layer=layer,
                    at_x=ax,
                    at_y=ay,
                    items=(a.id, b.id),
                )
            )
    return out


def via_in_pad_findings(
    problem: RoutingProblem, solution: RoutingSolution
) -> list[Violation]:
    """A via drilled inside an SMD pad. Needs via-in-pad plating, which is a
    process we do not buy; on the economy tier the paste runs down the hole and
    the joint starves."""
    if problem.rules.allow_via_in_pad:
        return []
    smd = [p for p in problem.pads if p.is_smd]
    grid = GridIndex(cell_mm=2.0)
    for pad in smd:
        grid.insert(pad_capsule(pad), pad)
    out: list[Violation] = []
    for via in sorted(solution.vias, key=lambda v: (v.net, v.center.x, v.center.y)):
        drill = disc_capsule(via.center.x, via.center.y, via.drill_mm)
        for _, pad in grid.query(drill, margin=0.05):
            if capsule_gap(drill, pad_capsule(pad)) < 0.0:
                out.append(
                    Violation(
                        kind="via_in_pad",
                        severity="error",
                        detail=(
                            f"via {via.id} is drilled inside SMD pad {pad.id}"
                            f"{' on ' + pad.component if pad.component else ''} — "
                            "via-in-pad needs filled-and-capped plating we do not order"
                        ),
                        layer=pad.layers[0] if pad.layers else TOP,
                        at_x=via.center.x,
                        at_y=via.center.y,
                        items=(via.id, pad.id),
                    )
                )
                break
    return out


def keepout_findings(
    problem: RoutingProblem, solution: RoutingSolution
) -> list[Violation]:
    """Copper inside a declared keepout. Antenna clearances and connector
    shells are declared, not guessed, and a router that ignores one produces a
    board that fails in the field rather than at the fab."""
    if not problem.keepouts:
        return []
    out: list[Violation] = []
    routed = [
        item
        for item in copper_items(problem, solution)
        if item.kind in ("trace", "via")
    ]
    for keepout in problem.keepouts:
        zone = rect_capsule(
            keepout.center.x, keepout.center.y, keepout.width_mm, keepout.height_mm
        )
        for item in routed:
            if not set(item.layers) & set(keepout.layers):
                continue
            if capsule_gap(item.capsule, zone) < 0.0:
                x, y = _capsule_mid(item.capsule)
                out.append(
                    Violation(
                        kind="keepout",
                        severity="error",
                        detail=(
                            f"{item.kind} on net {_pretty(item.net)} enters keepout "
                            f"{keepout.id} at ({x:.2f}, {y:.2f})"
                        ),
                        layer=item.layers[0],
                        at_x=x,
                        at_y=y,
                        items=(item.id, keepout.id),
                    )
                )
    return out


def edge_findings(
    problem: RoutingProblem, solution: RoutingSolution
) -> list[Violation]:
    """Routed copper against the board edge.

    The pipeline's DFM gate checks pads, vias and plated holes against the
    board's bounding *rectangle*. It does not check traces at all, and a
    rounded outline is not a rectangle — so this one is ours, measured against
    the real outline polygon when the board has one.
    """
    board = problem.board
    outline = board.outline
    index = PolygonIndex(outline) if len(outline) >= 3 else None
    limit = problem.rules.min_edge_clearance_mm
    out: list[Violation] = []

    routed = [
        item
        for item in copper_items(problem, solution)
        if item.kind in ("trace", "via")
    ]
    x0, y0, x1, y1 = board.bbox
    for item in routed:
        if index is not None:
            gap = index.clearance(item.capsule, limit)
            mx, my = _capsule_mid(item.capsule)
            inside = index.contains(mx, my)
            if not inside:
                out.append(
                    Violation(
                        kind="off_board",
                        severity="error",
                        detail=(
                            f"{item.kind} on net {_pretty(item.net)} is outside the "
                            "board outline"
                        ),
                        layer=item.layers[0],
                        at_x=mx,
                        at_y=my,
                        items=(item.id,),
                    )
                )
                continue
            if gap < limit - 1e-9:
                out.append(
                    Violation(
                        kind="trace_edge",
                        severity="error",
                        detail=(
                            f"{item.kind} on net {_pretty(item.net)} is {gap:.3f}mm "
                            f"from the board edge (fab minimum {limit:g}mm)"
                        ),
                        layer=item.layers[0],
                        at_x=mx,
                        at_y=my,
                        items=(item.id,),
                    )
                )
            continue
        ax, ay, bx, by, r = item.capsule
        margin = min(
            min(ax, bx) - r - x0,
            min(ay, by) - r - y0,
            x1 - (max(ax, bx) + r),
            y1 - (max(ay, by) + r),
        )
        if margin < limit - 1e-9:
            mx, my = _capsule_mid(item.capsule)
            out.append(
                Violation(
                    kind="trace_edge",
                    severity="error",
                    detail=(
                        f"{item.kind} on net {_pretty(item.net)} is {margin:.3f}mm "
                        f"from the board edge (fab minimum {limit:g}mm)"
                    ),
                    layer=item.layers[0],
                    at_x=mx,
                    at_y=my,
                    items=(item.id,),
                )
            )
    return out


# ---------------------------------------------------------------------------
# The pipeline's own gate
# ---------------------------------------------------------------------------


def pipeline_findings(
    problem: RoutingProblem, solution: RoutingSolution
) -> tuple[list[Violation], list[Violation]]:
    """``(route findings, board findings)`` from ``circuitpy.checks``.

    The board findings — size, price tier, thickness, envelope — are separated
    out because no router can change them; hiding them would be dishonest, and
    counting them against a route would be wrong.
    """
    from circuitpy.checks import dfm_warnings
    from circuitpy.fab import get_profile
    from circuitpy.spec import ResolvedProduct

    from routerlib.adapters import circuit_json_for_scoring

    elements = circuit_json_for_scoring(problem, solution)
    product = ResolvedProduct(
        name=problem.id,
        description="routerlib scoring",
        power="usb-c-5v",
        envelope_mm=None,
        layers=problem.board.layer_count,
        fab=problem.rules.profile_id,
        assembly=True,
        path=Path("."),
    )
    profile = get_profile(problem.rules.profile_id)
    route_out: list[Violation] = []
    board_out: list[Violation] = []
    # circuitpy.checks.Warning is a plain dict: {part, kind, detail, severity}.
    for warning in dfm_warnings(elements, product, profile):
        kind = str(warning.get("kind") or "")
        part = str(warning.get("part") or "")
        violation = Violation(
            kind=kind,
            severity=str(warning.get("severity") or "warning"),
            detail=str(warning.get("detail") or ""),
            items=(part,) if part else (),
            source="circuitpy.checks",
        )
        (board_out if kind in BOARD_LEVEL_KINDS else route_out).append(violation)
    return route_out, board_out


# ---------------------------------------------------------------------------
# Everything, together
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DrcResult:
    violations: tuple[Violation, ...]
    board_violations: tuple[Violation, ...]
    coverage_gaps: tuple[str, ...] = COVERAGE_GAPS

    @property
    def errors(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.is_error)

    @property
    def warnings(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if not v.is_error)

    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for violation in self.violations:
            counts[violation.kind] = counts.get(violation.kind, 0) + 1
        return dict(sorted(counts.items()))

    def error_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for violation in self.errors:
            counts[violation.kind] = counts.get(violation.kind, 0) + 1
        return dict(sorted(counts.items()))


def check(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    use_pipeline: bool = True,
) -> DrcResult:
    """Every legality check, in one call.

    ``use_pipeline=False`` skips the ``circuitpy.checks`` half — for a fast
    inner loop inside a router, never for a score.
    """
    found: list[Violation] = []
    found += copper_clearance_findings(problem, solution)
    found += via_in_pad_findings(problem, solution)
    found += keepout_findings(problem, solution)
    found += edge_findings(problem, solution)
    board: list[Violation] = []
    if use_pipeline:
        route_findings, board = pipeline_findings(problem, solution)
        found += route_findings
    found.sort()
    board.sort()
    return DrcResult(tuple(found), tuple(board))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capsule_mid(capsule: Capsule) -> tuple[float, float]:
    ax, ay, bx, by, _ = capsule
    return ((ax + bx) / 2.0, (ay + by) / 2.0)


def _pretty(net: str) -> str:
    """Connectivity keys are long and machine-minted; show the tail."""
    if net.startswith("__unnetted__"):
        return "(no net)"
    return net.rsplit("_", 1)[-1] if len(net) > 24 else net


__all__ = [
    "BOARD_LEVEL_KINDS",
    "COVERAGE_GAPS",
    "CopperItem",
    "DrcResult",
    "Violation",
    "check",
    "copper_clearance_findings",
    "copper_items",
    "edge_findings",
    "keepout_findings",
    "pipeline_findings",
    "via_in_pad_findings",
]
