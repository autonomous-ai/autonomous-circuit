"""The benchmark: real instances, measured features, one runner.

An instance is a *placed* board with the copper taken off. They are extracted
from boards we have actually built — the three examples and a spread of
composition-matrix cells — and then **committed as fixtures**, because the
boards in ``examples/`` are rebuilt by other agents several times a day and a
benchmark that moves under you is not a benchmark.

Every instance records its features, because the portfolio phase selects on
exactly those: net count, pad count, board area, pad density, whether the
layout is regular, finest pin pitch, whether there are differential pairs.
Every feature is **measured from the geometry**, never inferred from a name —
the day this repo shipped a BOM calling every 0402 an 0603, it was because
someone inferred a package from its footprint size instead of looking it up.

Each instance also stores its **baseline**: the DRC result of the empty
solution. Any violation there belongs to the placement, not to a router, and a
score that did not report it separately would be blaming the wrong thing.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    Budget,
    DesignRules,
    Drill,
    Keepout,
    Net,
    Pad,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    empty_solution,
)

#: ``@2`` adds the fields that make a pad its real shape: a polygon pad's
#: vertices, a rounded pad's corner radius, a drill's and a keepout's own
#: shape and rotation. ``@1`` files are refused rather than silently read as
#: rectangles, because reading them that way is exactly the defect.
SCHEMA = "routerlib/instance@2"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
INSTANCE_DIR = PACKAGE_ROOT / "benchmarks" / "instances"
MANIFEST = PACKAGE_ROOT / "benchmarks" / "manifest.json"


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def problem_to_dict(problem: RoutingProblem, *, source: dict | None = None) -> dict:
    board = problem.board
    return {
        "schema": SCHEMA,
        "id": problem.id,
        "source": source or problem.meta.get("source", {}),
        #: Fingerprint of the placement, so drift is visible from the file
        #: without loading the board it came from. See :func:`placement_hash`.
        "placementHash": placement_hash(problem),
        "rules": {
            "profile": problem.rules.profile_id,
            "layerCount": problem.rules.layer_count,
            "viaDrillMm": problem.rules.via_drill_mm,
            "viaPadMm": problem.rules.via_pad_mm,
        },
        "board": {
            "widthMm": board.width_mm,
            "heightMm": board.height_mm,
            "center": [board.center.x, board.center.y],
            "layerCount": board.layer_count,
            "thicknessMm": board.thickness_mm,
            "outline": [[p.x, p.y] for p in board.outline],
        },
        "pads": [
            {
                "id": p.id,
                "net": p.net,
                "x": p.center.x,
                "y": p.center.y,
                "w": p.width_mm,
                "h": p.height_mm,
                "layers": list(p.layers),
                "kind": p.kind,
                "shape": p.shape,
                "component": p.component,
                "portId": p.port_id,
                "drillId": p.drill_id,
                "rot": p.rotation_deg,
                # Schema 2. A pad that arrives without these is a pad whose
                # real outline was thrown away upstream, which is the defect
                # this schema exists to close.
                "points": [[v.x, v.y] for v in p.vertices] or None,
                "cornerRadiusMm": p.corner_radius_mm or None,
            }
            for p in problem.pads
        ],
        "drills": [
            {
                "id": d.id,
                "x": d.center.x,
                "y": d.center.y,
                "w": d.width_mm,
                "h": d.height_mm,
                "plated": d.plated,
                "net": d.net,
                "component": d.component,
                "padId": d.pad_id,
                "rot": d.rotation_deg,
                "shape": d.shape,
            }
            for d in problem.drills
        ],
        "keepouts": [
            {
                "id": k.id,
                "x": k.center.x,
                "y": k.center.y,
                "w": k.width_mm,
                "h": k.height_mm,
                "layers": list(k.layers),
                "shape": k.shape,
                "rot": k.rotation_deg,
                "points": [[v.x, v.y] for v in k.vertices] or None,
            }
            for k in problem.keepouts
        ],
        "planes": [
            {
                "id": pl.id,
                "net": pl.net,
                "layer": pl.layer,
                "outline": [[p.x, p.y] for p in pl.outline],
                "holes": [[[p.x, p.y] for p in ring] for ring in pl.holes],
            }
            for pl in problem.planes
        ],
        "nets": [
            {
                "id": n.id,
                "name": n.name,
                "class": n.net_class,
                "pads": list(n.pads),
                "minWidthMm": n.min_width_mm,
                "sourceNetId": n.source_net_id,
                "diffPartner": n.diff_partner,
                "priority": n.priority,
            }
            for n in problem.nets
        ],
        "features": asdict(features_of(problem)),
    }


def problem_from_dict(data: dict, *, rules: DesignRules | None = None) -> RoutingProblem:
    if data.get("schema") != SCHEMA:
        raise ValueError(f"unknown instance schema {data.get('schema')!r}")
    raw_rules = data.get("rules") or {}
    if rules is None:
        rules = DesignRules.jlcpcb(
            layer_count=int(raw_rules.get("layerCount", 2)),
            via_drill_mm=float(raw_rules.get("viaDrillMm", 0.30)),
            via_pad_mm=float(raw_rules.get("viaPadMm", 0.60)),
        )
    b = data["board"]
    board = Board(
        width_mm=float(b["widthMm"]),
        height_mm=float(b["heightMm"]),
        center=Point(*b.get("center", (0.0, 0.0))),
        outline=tuple(Point(x, y) for x, y in b.get("outline", ())),
        layer_count=int(b.get("layerCount", 2)),
        thickness_mm=float(b.get("thicknessMm", 1.6)),
    )
    pads = tuple(
        Pad(
            id=p["id"],
            net=p.get("net"),
            center=Point(p["x"], p["y"]),
            width_mm=float(p["w"]),
            height_mm=float(p["h"]),
            layers=tuple(p.get("layers") or (TOP,)),
            kind=p.get("kind", "smd"),
            shape=p.get("shape", "rect"),
            component=p.get("component", ""),
            port_id=p.get("portId"),
            drill_id=p.get("drillId"),
            rotation_deg=float(p.get("rot", 0.0) or 0.0),
            vertices=tuple(Point(x, y) for x, y in (p.get("points") or ())),
            corner_radius_mm=float(p.get("cornerRadiusMm") or 0.0),
        )
        for p in data.get("pads", ())
    )
    drills = tuple(
        Drill(
            id=d["id"],
            center=Point(d["x"], d["y"]),
            width_mm=float(d["w"]),
            height_mm=float(d["h"]),
            plated=bool(d.get("plated")),
            net=d.get("net"),
            component=d.get("component", ""),
            pad_id=d.get("padId"),
            rotation_deg=float(d.get("rot", 0.0) or 0.0),
            shape=d.get("shape", "pill"),
        )
        for d in data.get("drills", ())
    )
    keepouts = tuple(
        Keepout(
            id=k["id"],
            center=Point(k["x"], k["y"]),
            width_mm=float(k["w"]),
            height_mm=float(k["h"]),
            layers=tuple(k.get("layers") or (TOP, BOTTOM)),
            shape=k.get("shape", "rect"),
            rotation_deg=float(k.get("rot", 0.0) or 0.0),
            vertices=tuple(Point(x, y) for x, y in (k.get("points") or ())),
        )
        for k in data.get("keepouts", ())
    )
    planes = tuple(
        Plane(
            id=pl["id"],
            net=pl["net"],
            layer=pl.get("layer", BOTTOM),
            outline=tuple(Point(x, y) for x, y in pl.get("outline", ())),
            holes=tuple(
                tuple(Point(x, y) for x, y in ring) for ring in pl.get("holes", ())
            ),
        )
        for pl in data.get("planes", ())
    )
    nets = tuple(
        Net(
            id=n["id"],
            name=n["name"],
            net_class=n["class"],
            pads=tuple(n.get("pads") or ()),
            min_width_mm=float(n.get("minWidthMm", rules.signal_trace_mm)),
            source_net_id=n.get("sourceNetId"),
            diff_partner=n.get("diffPartner"),
            priority=int(n.get("priority", 100)),
        )
        for n in data.get("nets", ())
    )
    return RoutingProblem(
        id=data["id"],
        board=board,
        rules=rules,
        pads=pads,
        drills=drills,
        keepouts=keepouts,
        planes=planes,
        nets=nets,
        meta={"source": data.get("source", {})},
    )


def load_instance(path: str | Path) -> RoutingProblem:
    return problem_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Does this copper belong to this instance?
# ---------------------------------------------------------------------------


def placement_hash(problem: RoutingProblem) -> str:
    """Fingerprint of the *placement* — everything a router is not allowed to
    move. Board, pads, drills, keepouts. Copper is deliberately absent, so a
    routed board and the instance it was routed from hash the same.

    This exists because an instance is a fixture and ``examples/`` is not:
    other agents rebuild those boards several times a day, and a rebuild can
    renumber every pad. Scoring drifted copper against a stale instance
    produces a number that looks like a score and means nothing.

    **Shape is in the hash as of 2026-08-16.** It was not, and it had to be:
    two placements that differ only in whether a pad is a rectangle or a
    polygon are two different boards, and the hash that called them the same
    was hiding the geometry the router is graded against. Adding it moved every
    instance's hash once, on purpose — see the manifest's ``rebaseline`` block.
    """
    import hashlib

    payload = {
        "board": [
            round(problem.board.width_mm, 6),
            round(problem.board.height_mm, 6),
            problem.board.center.x,
            problem.board.center.y,
            problem.board.layer_count,
        ],
        "pads": sorted(
            [p.id, p.net or "", p.center.x, p.center.y, p.width_mm, p.height_mm,
             p.kind, round(p.rotation_deg, 4), list(p.layers), p.shape,
             round(p.corner_radius_mm, 6),
             [[v.x, v.y] for v in p.vertices]]
            for p in problem.pads
        ),
        "drills": sorted(
            [d.id, d.center.x, d.center.y, d.width_mm, d.height_mm, d.plated,
             round(d.rotation_deg, 4), d.shape]
            for d in problem.drills
        ),
        "keepouts": sorted(
            [k.id, k.center.x, k.center.y, k.width_mm, k.height_mm, k.shape,
             round(k.rotation_deg, 4), [[v.x, v.y] for v in k.vertices]]
            for k in problem.keepouts
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]


@dataclass(frozen=True)
class Correspondence:
    """Whether a routed board is the *same placed board* as an instance."""

    matches: bool
    instance_hash: str
    other_hash: str
    missing_pads: tuple[str, ...]
    extra_pads: tuple[str, ...]
    moved_pads: tuple[str, ...]
    missing_nets: tuple[str, ...]
    copper_elements: int

    def report(self) -> str:
        if self.matches and self.copper_elements:
            return (
                f"placement matches instance ({self.instance_hash}), "
                f"{self.copper_elements} copper element(s)"
            )
        lines = [
            f"placement {self.other_hash} does not match instance "
            f"{self.instance_hash}"
            if not self.matches
            else f"placement matches instance ({self.instance_hash})"
        ]
        if not self.copper_elements:
            lines.append(
                "  the routed file has no traces or vias at all — this scores "
                "0% completeness for a reason that is not the router's"
            )
        for label, ids in (
            ("pads in the instance that the routed file does not have", self.missing_pads),
            ("pads in the routed file that the instance does not have", self.extra_pads),
            ("pads that exist in both but moved", self.moved_pads),
            ("nets in the instance that the routed file does not have", self.missing_nets),
        ):
            if ids:
                shown = ", ".join(ids[:4]) + (" ..." if len(ids) > 4 else "")
                lines.append(f"  {len(ids)} {label}: {shown}")
        lines.append(
            "  re-extract the instance from this board, or score against the "
            "board the instance came from"
        )
        return "\n".join(lines)


def correspondence(
    instance: RoutingProblem, other: RoutingProblem, *, tolerance_mm: float = 1e-4
) -> Correspondence:
    """Compare an instance with a board someone wants to score against it.

    Reports *what* diverged, not just that something did. A bare "0% routed" is
    the worst kind of output: it reads as a router that failed when the truth
    may be that the board was rebuilt underneath the fixture.
    """
    mine = {p.id: p for p in instance.pads}
    theirs = {p.id: p for p in other.pads}
    shared = set(mine) & set(theirs)
    moved = sorted(
        pid
        for pid in shared
        if mine[pid].center.distance_to(theirs[pid].center) > tolerance_mm
    )
    their_nets = {n.id for n in other.nets} | {
        p.net for p in other.pads if p.net
    }
    missing_nets = sorted(
        n.id for n in instance.routable_nets if n.id not in their_nets
    )
    ours = placement_hash(instance)
    others = placement_hash(other)
    return Correspondence(
        matches=(ours == others),
        instance_hash=ours,
        other_hash=others,
        missing_pads=tuple(sorted(set(mine) - set(theirs))),
        extra_pads=tuple(sorted(set(theirs) - set(mine))),
        moved_pads=tuple(moved),
        missing_nets=tuple(missing_nets),
        copper_elements=len(other.existing_traces) + len(other.existing_vias),
    )


def instance_paths(directory: str | Path | None = None) -> list[Path]:
    root = Path(directory) if directory else INSTANCE_DIR
    return sorted(root.glob("*.json"))


def load_all(directory: str | Path | None = None) -> list[RoutingProblem]:
    return [load_instance(p) for p in instance_paths(directory)]


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Features:
    """Everything the portfolio phase gets to select on. All measured."""

    net_count: int
    routable_net_count: int
    terminal_count: int
    pad_count: int
    smd_pad_count: int
    plated_hole_count: int
    drill_count: int
    component_count: int
    keepout_count: int
    board_width_mm: float
    board_height_mm: float
    board_area_mm2: float
    pad_density_per_cm2: float
    avg_net_degree: float
    max_net_degree: int
    finest_pin_pitch_mm: float | None
    diff_pair_count: int
    power_net_count: int
    ground_net_count: int
    ground_pad_count: int
    has_plane: bool
    #: Sum of every net's Euclidean minimum spanning tree. A hard lower bound
    #: on total copper, and the single best one-number difficulty proxy.
    mst_length_mm: float
    #: mm of required copper per square millimetre of board.
    routing_demand_per_mm2: float
    #: Regularity, measured: how much of the board is one repeated part on a
    #: lattice. A 50-key matrix scores high; a mixed analog board scores low.
    repeat_ratio: float
    grid_score: float
    regular: bool


def features_of(problem: RoutingProblem) -> Features:
    pads = problem.pads
    routable = problem.routable_nets
    area = problem.board.area_mm2 or 1.0
    degrees = [len(n.pads) for n in routable] or [0]
    by_component: dict[str, list[Pad]] = {}
    for pad in pads:
        if pad.component:
            by_component.setdefault(pad.component, []).append(pad)

    finest = _finest_pitch(by_component)
    mst = sum(_mst_length(problem.pads_of(n.id)) for n in routable)
    repeat, grid = _regularity(by_component)
    ground_nets = [n for n in problem.nets if n.net_class == "ground"]

    return Features(
        net_count=len(problem.nets),
        routable_net_count=len(routable),
        terminal_count=sum(len(n.pads) for n in routable),
        pad_count=len(pads),
        smd_pad_count=sum(1 for p in pads if p.is_smd),
        plated_hole_count=sum(1 for p in pads if not p.is_smd),
        drill_count=len(problem.drills),
        component_count=len(by_component),
        keepout_count=len(problem.keepouts),
        board_width_mm=round(problem.board.width_mm, 4),
        board_height_mm=round(problem.board.height_mm, 4),
        board_area_mm2=round(area, 3),
        pad_density_per_cm2=round(len(pads) / (area / 100.0), 3),
        avg_net_degree=round(sum(degrees) / len(degrees), 3),
        max_net_degree=max(degrees),
        finest_pin_pitch_mm=(round(finest, 4) if finest is not None else None),
        diff_pair_count=sum(
            1 for n in problem.nets
            if n.net_class == "diff_pair" and n.diff_partner and n.id < n.diff_partner
        ),
        power_net_count=sum(1 for n in problem.nets if n.net_class == "power"),
        ground_net_count=len(ground_nets),
        ground_pad_count=sum(len(n.pads) for n in ground_nets),
        has_plane=bool(problem.planes),
        mst_length_mm=round(mst, 3),
        routing_demand_per_mm2=round(mst / area, 5),
        repeat_ratio=round(repeat, 3),
        grid_score=round(grid, 3),
        regular=bool(grid >= 0.5),
    )


def _finest_pitch(by_component: dict[str, list[Pad]]) -> float | None:
    """Smallest centre-to-centre distance between two pads of one component.

    This is *measured*, and it is a pitch, not a package guess: an 0.5mm-pitch
    QFN and a 0.5mm-pitch connector are the same routing problem whatever their
    footprints are called.
    """
    best: float | None = None
    for pads in by_component.values():
        if len(pads) < 2:
            continue
        ordered = sorted(pads, key=lambda p: (p.center.x, p.center.y))
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 : i + 6]:
                d = a.center.distance_to(b.center)
                if d > 1e-6 and (best is None or d < best):
                    best = d
    return best


def _mst_length(pads: Sequence[Pad]) -> float:
    if len(pads) < 2:
        return 0.0
    points = [p.center for p in pads]
    inside = [0]
    outside = list(range(1, len(points)))
    total = 0.0
    while outside:
        best = None
        pick = None
        for i in inside:
            for j in outside:
                d = points[i].distance_to(points[j])
                if best is None or d < best:
                    best, pick = d, j
        total += best or 0.0
        inside.append(pick)
        outside.remove(pick)
    return total


#: A footprint has to repeat at least this many times before "regular" means
#: anything. Three of a thing is a coincidence.
_MIN_GROUP = 4

#: How far a component centre may sit off the fitted lattice and still count.
_LATTICE_TOL_MM = 0.1


def _regularity(by_component: dict[str, list[Pad]]) -> tuple[float, float]:
    """``(repeat_ratio, grid_score)`` — how much of this board is one thing,
    stamped out on a grid.

    Components are grouped by a **measured** footprint signature: pad count and
    mean pad size. Never by refdes prefix and never by a package name, because
    a name is a claim and a pad is a measurement.

    * ``repeat_ratio`` — the share of components that belong to any group with
      at least four members.
    * ``grid_score`` — the share of *all* components that sit on their own
      group's fitted lattice. The lattice pitch is the modal spacing of that
      group's distinct centre coordinates, and the phase is the modal residual,
      not the first value — a 50-key matrix with two stray switches must not
      score zero because one outlier was picked as the origin.

    A key matrix scores ~0.75 and up; a mixed analog board scores near zero.
    """
    if not by_component:
        return (0.0, 0.0)
    signatures: dict[tuple, list[str]] = {}
    centroids: dict[str, tuple[float, float]] = {}
    for name, pads in by_component.items():
        sig = (
            len(pads),
            round(statistics.fmean(p.width_mm for p in pads), 3),
            round(statistics.fmean(p.height_mm for p in pads), 3),
        )
        signatures.setdefault(sig, []).append(name)
        centroids[name] = (
            statistics.fmean(p.center.x for p in pads),
            statistics.fmean(p.center.y for p in pads),
        )
    total = len(by_component)
    repeated = 0
    on_lattice = 0.0
    for _, members in sorted(signatures.items()):
        if len(members) < _MIN_GROUP:
            continue
        repeated += len(members)
        xs = [centroids[m][0] for m in sorted(members)]
        ys = [centroids[m][1] for m in sorted(members)]
        fit_x = _lattice_fit(xs)
        fit_y = _lattice_fit(ys)
        fits = [f for f in (fit_x, fit_y) if f is not None]
        if fits:
            on_lattice += len(members) * min(fits)
    return (repeated / total, on_lattice / total)


def _lattice_fit(values: Sequence[float]) -> float | None:
    """Fraction of ``values`` landing on the best single-pitch lattice.

    ``None`` when the coordinate is degenerate (every component on one line),
    which is a fit question that does not apply rather than a fit of zero.
    """
    distinct = sorted({round(v, 2) for v in values})
    if len(distinct) < 2:
        return None
    gaps = [round(b - a, 2) for a, b in zip(distinct, distinct[1:]) if b - a > 0.05]
    if not gaps:
        return None
    pitch = statistics.mode(gaps)
    if pitch <= 0:
        return None
    residuals = sorted({round(v % pitch, 2) for v in values})
    best = 0
    for phase in residuals:
        hits = sum(
            1 for v in values if _circular_gap(v % pitch, phase, pitch) <= _LATTICE_TOL_MM
        )
        best = max(best, hits)
    return best / len(values)


def _circular_gap(a: float, b: float, period: float) -> float:
    delta = abs(a - b)
    return min(delta, period - delta)


# ---------------------------------------------------------------------------
# Instance construction helpers
# ---------------------------------------------------------------------------


def inset_polygon(poly: Sequence[Point], distance: float) -> tuple[Point, ...]:
    """Offset a simple polygon inward by ``distance``.

    Edge-offset-and-intersect, which is exact for a convex outline — and every
    board outline we ship is a rounded rectangle. The caller checks the result
    and falls back rather than trusting it blindly.
    """
    if len(poly) < 3:
        return ()
    pts = list(poly)
    area = sum(
        pts[i].x * pts[(i + 1) % len(pts)].y - pts[(i + 1) % len(pts)].x * pts[i].y
        for i in range(len(pts))
    )
    if area < 0:
        pts.reverse()
    lines = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b.x - a.x, b.y - a.y
        length = math.hypot(dx, dy)
        if length < 1e-12:
            continue
        # Inward normal for a counter-clockwise ring.
        nx, ny = -dy / length, dx / length
        lines.append((a.x + nx * distance, a.y + ny * distance, dx, dy))
    out: list[Point] = []
    for i in range(len(lines)):
        x1, y1, dx1, dy1 = lines[i]
        x2, y2, dx2, dy2 = lines[(i + 1) % len(lines)]
        denom = dx1 * dy2 - dy1 * dx2
        if abs(denom) < 1e-12:
            out.append(Point(x2, y2))
            continue
        t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / denom
        out.append(Point(x1 + dx1 * t, y1 + dy1 * t))
    return tuple(out)


def with_ground_plane(
    problem: RoutingProblem,
    *,
    layer: str = BOTTOM,
    net_class: str = "ground",
    suffix: str = "-plane",
) -> RoutingProblem:
    """A copy of the problem with a clean plane poured on ``layer``.

    Synthesised, never inherited: the pours on our built boards were generated
    *after* routing and their outlines are carved around the old traces, so
    reusing one would leak the previous solution into the problem. This one is
    the board outline inset by the edge clearance, and nothing else.
    """
    from dataclasses import replace

    ground = [n for n in problem.nets if n.net_class == net_class]
    if not ground:
        raise ValueError(f"{problem.id} has no {net_class} net to pour")
    outline = problem.board.outline
    inset = problem.rules.min_edge_clearance_mm
    poured = inset_polygon(outline, inset) if len(outline) >= 3 else ()
    if not poured:
        x0, y0, x1, y1 = problem.board.bbox
        poured = (
            Point(x0 + inset, y0 + inset),
            Point(x1 - inset, y0 + inset),
            Point(x1 - inset, y1 - inset),
            Point(x0 + inset, y1 - inset),
        )
    planes = tuple(
        Plane(id=f"plane_{net.id}", net=net.id, layer=layer, outline=poured)
        for net in ground
    )
    return replace(
        problem,
        id=f"{problem.id}{suffix}",
        planes=problem.planes + planes,
        meta={**problem.meta, "syntheticPlane": {"layer": layer, "insetMm": inset}},
    )


def baseline_of(problem: RoutingProblem) -> dict:
    """The empty solution's DRC result — the instance's own defects.

    Any violation here was already in the placement. A score that counted it
    against a router would be blaming the wrong thing, and one that hid it
    would be manufacturing confidence.
    """
    from routerlib import drc as drc_mod

    result = drc_mod.check(problem, empty_solution())
    return {
        "errors": len(result.errors),
        "warnings": len(result.warnings),
        "byKind": result.by_kind(),
        "boardFindings": [v.as_dict() for v in result.board_violations],
    }


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


@dataclass
class SuiteReport:
    rows: list[dict]
    ruler_hash: str
    ruler_line: str

    def summary(self) -> str:
        if not self.rows:
            return "no instances"
        clean = sum(1 for r in self.rows if r["score"]["clean"])
        det = sum(1 for r in self.rows if r.get("deterministic", True))
        completeness = statistics.fmean(
            r["score"]["completeness"] for r in self.rows
        )
        errors = sum(r["score"]["errors"] for r in self.rows)
        return (
            f"{clean}/{len(self.rows)} instances clean, "
            f"{completeness * 100:.1f}% mean completeness, "
            f"{errors} DRC errors total, "
            f"{det}/{len(self.rows)} deterministic"
        )

    def as_dict(self) -> dict:
        return {
            "schema": "routerlib/suite@1",
            "rulerHash": self.ruler_hash,
            "ruler": self.ruler_line,
            "summary": self.summary(),
            "rows": self.rows,
        }


def run_suite(
    router_factory,
    problems: Sequence[RoutingProblem],
    budget: Budget | None = None,
    *,
    check_determinism: bool = True,
    on_done=None,
) -> SuiteReport:
    """Route and score every instance. Serial by default — the machine is
    shared, and a benchmark that starves another agent's build is a worse
    outcome than a slower benchmark."""
    from routerlib import scoring

    budget = budget or Budget()
    rows: list[dict] = []
    ruler = None
    for problem in problems:
        router = router_factory()
        solution = router.route(problem, budget)
        result = scoring.score(problem, solution)
        ruler = ruler or result.ruler
        row = {
            "instance": problem.id,
            "features": asdict(features_of(problem)),
            "baseline": baseline_of(problem),
            "score": result.as_dict(),
        }
        if check_determinism:
            again = scoring.determinism_check(
                router_factory(), problem, budget, runs=2
            )
            row["deterministic"] = again.deterministic
            row["determinismDetail"] = again.detail
        rows.append(row)
        if on_done:
            on_done(result, row)
    from routerlib.scoring import ruler_for

    ruler = ruler or ruler_for(DesignRules.jlcpcb())
    return SuiteReport(rows=rows, ruler_hash=ruler.hash, ruler_line=ruler.line())


__all__ = [
    "Correspondence",
    "Features",
    "INSTANCE_DIR",
    "MANIFEST",
    "SCHEMA",
    "SuiteReport",
    "baseline_of",
    "correspondence",
    "features_of",
    "inset_polygon",
    "instance_paths",
    "load_all",
    "load_instance",
    "placement_hash",
    "problem_from_dict",
    "problem_to_dict",
    "run_suite",
    "with_ground_plane",
]
