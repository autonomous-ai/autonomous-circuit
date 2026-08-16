"""Spatial decomposition: cut the board into regions, give each one an expert.

Our own keyboard is the argument for this. It is three routing problems wearing
one coat — a 10x5 matrix at 10mm pitch (textbook channel routing), a 0.4mm-pitch
QFN escape (a small exact problem), and ordinary signals across open board
between them. A general router that handles all three adequately is worse than
three specialists, and the tournament said the same thing from the other side:
selecting one family per *board* is worth two nets, but the nine families
between them route 378 of 380 nets. The winner varies **inside** a board.

What this module does, in the order that works
----------------------------------------------

1. **Partition the components**, not the nets. A package cannot be split, so a
   component is the atom. The cut is a sweep bisection — sort the components
   along one axis, try every position where the coordinate changes, keep the
   one that cuts the fewest nets subject to a balance constraint — applied
   recursively. Sweeping rather than clustering keeps a region convex and
   contiguous, which is what makes "this region has a character" a true
   statement about geometry rather than about a label.

2. **Route the crossing nets first, on the whole board.** This is the part
   spatial decomposition usually gets wrong. A region routed optimally in
   isolation can leave no legal crossing at its edge, so the boundary
   conditions have to be fixed before any region solves. ``boundary_clearance``
   widens the global stage's target clearance so the copper that fixes the
   crossings leaves room for the regions that have to route around it.

   *"Low resolution" here means which nets are visible, not a coarser grid.*
   The global stage sees every obstacle at full precision and only the nets
   that span regions. There is no shared coarse-grid notion across nine
   families to exploit, and inventing one for this module would make its copper
   incomparable with everything else in the package.

3. **Route each region's interior nets with its expert**, hardest region first,
   with every piece of copper already down as an obstacle. Because each stage's
   ``Workspace`` treats the previous stages' copper as an obstacle, the
   composition cannot invent a clearance violation that no stage could see —
   the same property that makes the relay safe.

4. **Optionally hand the residue to a follower chain**, which is the relay
   applied to whatever the regions could not finish.

What is measured and what is a hypothesis
-----------------------------------------

The partition is measured: :func:`partition` reports, per instance, how many
nets end up interior to a region and how many cross. **A board with no useful
seam is a real outcome** and is reported as one — :attr:`Partition.seam` is
False with a reason, and the composition then degenerates to its global router
by construction rather than by accident.

The expert *table* is a hypothesis until the A/B runs. The board-level
tournament already refuted the two intuitions it is built on — "regular matrix
-> the structured router" and "small and dense -> exact on windows" both rank
``exact-and-structured`` 7th or 8th of nine — so the same claim at region level
has to earn its place against the same partition routed entirely by the global
router. :data:`REJECTED_ASSIGNMENTS` records what has already failed.
"""

from __future__ import annotations

import dataclasses
import statistics
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

from routerlib import connectivity as conn
from routerlib.compositions.registry import Registry
from routerlib.model import (
    Budget,
    Pad,
    RoutingProblem,
    RoutingSolution,
)

# Imported rather than reimplemented: the lattice fit that decides whether a
# board is "regular" has to give the same answer for a region as the benchmark
# gives for a whole board, or the word means two things.
from routerlib.bench import _regularity as _regularity_of

# ---------------------------------------------------------------------------
# Who routes what
# ---------------------------------------------------------------------------

#: The family that routes the nets spanning two or more regions, and every
#: region the character detector could not name. ``pathfinder-negotiated`` is
#: at or tied with the best completeness on 12 of the 16 benchmark instances
#: and never commits, so ordering does not bite it — both of which matter more
#: for the stage that fixes everyone else's boundary conditions than for any
#: single region.
GLOBAL_EXPERT = "pathfinder-negotiated"

#: Region character -> family. **A hypothesis, not a measurement.** The
#: A/B that decides whether it stays is this table against
#: ``{c: GLOBAL_EXPERT}`` on the same partition; if the table does not win,
#: it moves to :data:`REJECTED_ASSIGNMENTS` and the default becomes the
#: constant, exactly as the board-level selector did.
EXPERTS: Mapping[str, str] = {
    "lattice": "exact-and-structured",
    "fine-pitch": "exact-and-structured",
    "open": "pathfinder-negotiated",
}

#: Assignments tried and false, with the measurement that killed them. Kept
#: rather than deleted: a tried-and-failed rule is worth more than an untried
#: one, and this benchmark is small enough that a guess looks like a finding.
REJECTED_ASSIGNMENTS: tuple[tuple[str, str], ...] = ()

#: Character thresholds. Measured off the region, never off a part name.
LATTICE_GRID_SCORE = 0.5
LATTICE_MIN_COMPONENTS = 4
FINE_PITCH_MM = 0.5


# ---------------------------------------------------------------------------
# The atoms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One component and every pad on it. The atom of the partition, because a
    package is not divisible: half a QFN in each region is not a region."""

    id: str
    pads: tuple[str, ...]
    weight: int
    cx: float
    cy: float
    min_pitch_mm: float | None
    bbox: tuple[float, float, float, float]


def cells_of(problem: RoutingProblem) -> dict[str, Cell]:
    """Group pads into components. A pad with no component is its own cell —
    a loose pad is a thing that can be on either side of a cut."""
    grouped: dict[str, list[Pad]] = {}
    for pad in problem.pads:
        grouped.setdefault(pad.component or pad.id, []).append(pad)
    out: dict[str, Cell] = {}
    for name in sorted(grouped):
        pads = sorted(grouped[name], key=lambda p: p.id)
        xs = [p.center.x for p in pads]
        ys = [p.center.y for p in pads]
        out[name] = Cell(
            id=name,
            pads=tuple(p.id for p in pads),
            weight=len(pads),
            cx=statistics.fmean(xs),
            cy=statistics.fmean(ys),
            min_pitch_mm=_min_pitch(pads),
            bbox=(
                min(x - p.width_mm / 2.0 for x, p in zip(xs, pads)),
                min(y - p.height_mm / 2.0 for y, p in zip(ys, pads)),
                max(x + p.width_mm / 2.0 for x, p in zip(xs, pads)),
                max(y + p.height_mm / 2.0 for y, p in zip(ys, pads)),
            ),
        )
    return out


def _min_pitch(pads: Sequence[Pad]) -> float | None:
    """Closest centre-to-centre distance between two pads of one component.

    Measured, and it is a pitch rather than a package guess: a 0.5mm-pitch QFN
    and a 0.5mm-pitch connector are the same escape problem whatever their
    footprints are called.
    """
    if len(pads) < 2:
        return None
    ordered = sorted(pads, key=lambda p: (p.center.x, p.center.y))
    best: float | None = None
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 : i + 6]:
            d = a.center.distance_to(b.center)
            if d > 1e-6 and (best is None or d < best):
                best = d
    return best


def net_cells(problem: RoutingProblem, cells: Mapping[str, Cell]) -> dict[str, frozenset[str]]:
    """net id -> the set of cells it touches. Routable nets only."""
    owner: dict[str, str] = {}
    for name, cell in cells.items():
        for pad_id in cell.pads:
            owner[pad_id] = name
    return {
        net.id: frozenset(owner[p] for p in net.pads if p in owner)
        for net in problem.routable_nets
    }


# ---------------------------------------------------------------------------
# The cut
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cut:
    """One accepted bisection, kept so a partition can explain itself."""

    axis: str
    at_mm: float
    cut_nets: int
    inside_nets: int
    left_pads: int
    right_pads: int

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _sweep_bisect(
    members: frozenset[str],
    cells: Mapping[str, Cell],
    nets: Mapping[str, frozenset[str]],
    *,
    balance: float,
) -> tuple[frozenset[str], frozenset[str], Cut] | None:
    """The best half-plane split of ``members``, or ``None`` if none is legal.

    Every candidate is a real half-plane: sort the components along one axis
    and cut between two distinct coordinates. That is a weaker family than a
    graph partitioner's, and deliberately so — a min-cut that scatters a region
    across the board optimises the number this function reports and destroys
    the thing the number is for, which is a region with a location and a
    character.

    ``balance`` is the smallest share of the pads either side may hold. Score
    is (nets cut, then the more balanced split, then a fixed axis order), so
    the answer does not depend on dict iteration.
    """
    total = sum(cells[m].weight for m in members)
    if total <= 0:
        return None
    inside = [nid for nid, cs in nets.items() if cs and cs <= members]
    best: tuple[tuple, frozenset[str], frozenset[str], Cut] | None = None
    for axis in ("x", "y"):
        coord = (lambda c: c.cx) if axis == "x" else (lambda c: c.cy)
        order = sorted(members, key=lambda m: (coord(cells[m]), m))
        acc = 0
        for i in range(len(order) - 1):
            acc += cells[order[i]].weight
            here, nxt = coord(cells[order[i]]), coord(cells[order[i + 1]])
            if here >= nxt - 1e-9:
                continue
            if acc < balance * total or total - acc < balance * total:
                continue
            left = frozenset(order[: i + 1])
            right = frozenset(order[i + 1 :])
            cut = sum(1 for nid in inside if nets[nid] & left and nets[nid] & right)
            key = (cut, -min(acc, total - acc), axis)
            if best is None or key < best[0]:
                best = (
                    key,
                    left,
                    right,
                    Cut(
                        axis=axis,
                        at_mm=round((here + nxt) / 2.0, 4),
                        cut_nets=cut,
                        inside_nets=len(inside),
                        left_pads=acc,
                        right_pads=total - acc,
                    ),
                )
    if best is None:
        return None
    _key, left, right, cut = best
    return left, right, cut


# ---------------------------------------------------------------------------
# The regions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Region:
    """A contiguous piece of board, its measured character, and its expert."""

    id: str
    cells: tuple[str, ...]
    pads: tuple[str, ...]
    bbox: tuple[float, float, float, float]
    component_count: int
    pad_count: int
    area_mm2: float
    pad_density_per_cm2: float
    finest_pitch_mm: float | None
    grid_score: float
    repeat_ratio: float
    character: str
    expert: str
    interior_nets: tuple[str, ...]

    @property
    def difficulty_key(self) -> tuple:
        """Hardest first: finest pitch, then densest, then biggest."""
        return (
            self.finest_pitch_mm if self.finest_pitch_mm is not None else 99.0,
            -self.pad_density_per_cm2,
            -self.pad_count,
            self.id,
        )

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["interior_nets"] = list(self.interior_nets)
        d["cells"] = list(self.cells)
        d["pads"] = list(self.pads)
        d["bbox"] = list(self.bbox)
        return d


def _characterise(
    cells: Sequence[Cell], problem: RoutingProblem
) -> tuple[str, float, float, float, float, float | None]:
    """``(character, area, density, grid_score, repeat_ratio, finest_pitch)``.

    Every input is a measurement off the geometry. ``grid_score`` comes from
    the same lattice fit the benchmark uses for a whole board, so "regular"
    means one thing in this package.
    """
    pads_by_id = problem.pads_by_id
    by_component = {
        cell.id: [pads_by_id[p] for p in cell.pads if p in pads_by_id]
        for cell in cells
    }
    repeat, grid = _regularity_of({k: v for k, v in by_component.items() if v})
    x0 = min(c.bbox[0] for c in cells)
    y0 = min(c.bbox[1] for c in cells)
    x1 = max(c.bbox[2] for c in cells)
    y1 = max(c.bbox[3] for c in cells)
    area = max((x1 - x0) * (y1 - y0), 1e-6)
    pad_count = sum(c.weight for c in cells)
    density = pad_count / (area / 100.0)
    pitches = [c.min_pitch_mm for c in cells if c.min_pitch_mm is not None]
    finest = min(pitches) if pitches else None

    if grid >= LATTICE_GRID_SCORE and len(cells) >= LATTICE_MIN_COMPONENTS:
        character = "lattice"
    elif finest is not None and finest <= FINE_PITCH_MM:
        character = "fine-pitch"
    else:
        character = "open"
    return character, area, density, grid, repeat, finest


@dataclass(frozen=True)
class Partition:
    """The seam, or the honest absence of one."""

    instance: str
    regions: tuple[Region, ...]
    crossing_nets: tuple[str, ...]
    cuts: tuple[Cut, ...]
    seam: bool
    why: str
    params: dict

    @property
    def interior_nets(self) -> int:
        return sum(len(r.interior_nets) for r in self.regions)

    @property
    def routable_nets(self) -> int:
        return self.interior_nets + len(self.crossing_nets)

    @property
    def interior_share(self) -> float:
        total = self.routable_nets
        return (self.interior_nets / total) if total else 0.0

    def line(self) -> str:
        if not self.seam:
            return f"{self.instance}: no seam — {self.why}"
        return (
            f"{self.instance}: {len(self.regions)} regions, "
            f"{self.interior_nets}/{self.routable_nets} nets interior "
            f"({self.interior_share * 100:.0f}%), "
            f"{len(self.crossing_nets)} crossing — "
            + " ".join(
                f"{r.id}:{r.character}/{r.pad_count}p/{len(r.interior_nets)}n"
                for r in self.regions
            )
        )

    def as_dict(self) -> dict:
        return {
            "instance": self.instance,
            "seam": self.seam,
            "why": self.why,
            "params": dict(self.params),
            "interiorNets": self.interior_nets,
            "crossingNets": list(self.crossing_nets),
            "routableNets": self.routable_nets,
            "interiorShare": round(self.interior_share, 4),
            "regions": [r.as_dict() for r in self.regions],
            "cuts": [c.as_dict() for c in self.cuts],
        }


def partition(
    problem: RoutingProblem,
    *,
    min_cells: int = 5,
    max_depth: int = 3,
    balance: float = 0.30,
    max_cut_ratio: float = 1.0,
    experts: Mapping[str, str] | None = None,
    global_expert: str = GLOBAL_EXPERT,
) -> Partition:
    """Cut the board until cutting stops paying, then name what is left.

    A split is accepted only when it isolates more nets than it cuts
    (``cut_nets < max_cut_ratio * inside_nets``). That is the whole guard
    against the failure this composition is prone to: a partition that looks
    tidy, cuts every net, and hands the global router the entire board while
    reporting five regions.
    """
    experts = dict(EXPERTS if experts is None else experts)
    cells = cells_of(problem)
    nets = net_cells(problem, cells)
    params = {
        "minCells": min_cells,
        "maxDepth": max_depth,
        "balance": balance,
        "maxCutRatio": max_cut_ratio,
        "globalExpert": global_expert,
        "experts": dict(sorted(experts.items())),
    }

    groups: list[frozenset[str]] = [frozenset(cells)]
    cuts: list[Cut] = []
    refusals: list[str] = []
    for _depth in range(max_depth):
        nxt: list[frozenset[str]] = []
        split_any = False
        for group in groups:
            if len(group) < min_cells * 2:
                nxt.append(group)
                continue
            found = _sweep_bisect(group, cells, nets, balance=balance)
            if found is None:
                nxt.append(group)
                refusals.append("no split met the balance constraint")
                continue
            left, right, cut = found
            if cut.inside_nets == 0 or cut.cut_nets >= max_cut_ratio * cut.inside_nets:
                nxt.append(group)
                refusals.append(
                    f"best split cut {cut.cut_nets} of {cut.inside_nets} nets"
                )
                continue
            cuts.append(cut)
            nxt.extend((left, right))
            split_any = True
        groups = nxt
        if not split_any:
            break

    ordered = sorted(
        groups,
        key=lambda g: (
            round(min(cells[m].bbox[0] for m in g), 6),
            round(min(cells[m].bbox[1] for m in g), 6),
            min(g),
        ),
    )
    regions: list[Region] = []
    for index, group in enumerate(ordered):
        members = [cells[m] for m in sorted(group)]
        character, area, density, grid, repeat, finest = _characterise(members, problem)
        interior = tuple(
            sorted(nid for nid, cs in nets.items() if cs and cs <= group)
        )
        regions.append(
            Region(
                id=f"r{index}",
                cells=tuple(sorted(group)),
                pads=tuple(sorted(p for m in members for p in m.pads)),
                bbox=(
                    round(min(m.bbox[0] for m in members), 4),
                    round(min(m.bbox[1] for m in members), 4),
                    round(max(m.bbox[2] for m in members), 4),
                    round(max(m.bbox[3] for m in members), 4),
                ),
                component_count=len(members),
                pad_count=sum(m.weight for m in members),
                area_mm2=round(area, 3),
                pad_density_per_cm2=round(density, 3),
                finest_pitch_mm=(round(finest, 4) if finest is not None else None),
                grid_score=round(grid, 3),
                repeat_ratio=round(repeat, 3),
                character=character,
                expert=experts.get(character, global_expert),
                interior_nets=interior,
            )
        )

    assigned = {nid for r in regions for nid in r.interior_nets}
    crossing = tuple(sorted(nid for nid in nets if nid not in assigned))
    interior_total = len(assigned)

    if len(regions) <= 1:
        seam, why = False, (
            refusals[0] if refusals else "board too small to bisect under the balance constraint"
        )
    elif interior_total == 0:
        seam, why = False, "every net spans two or more regions"
    else:
        seam, why = True, (
            f"{len(regions)} regions isolate {interior_total} of "
            f"{len(nets)} nets"
        )

    if not seam:
        # A board with no seam gets the plain global router, not the expert
        # its one region's character happens to name. Otherwise "spatial"
        # would quietly be doing per-board *selection* by character — the
        # thing the tournament measured and refuted — and any difference from
        # the relay on such a board would be attributed to decomposition that
        # never happened.
        regions = [dataclasses.replace(r, expert=global_expert) for r in regions]

    return Partition(
        instance=problem.id,
        regions=tuple(regions),
        crossing_nets=crossing,
        cuts=tuple(cuts),
        seam=seam,
        why=why,
        params=params,
    )


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------

#: Region visiting order. ``hardest-first`` gives the specialist a clean sheet;
#: ``largest-first`` and ``id`` exist so the choice can be A/B'd rather than
#: asserted.
REGION_ORDERS = ("hardest-first", "largest-first", "id")


@dataclass(frozen=True)
class Stage:
    """One router's turn, and what the board looked like after it."""

    stage: str
    router: str
    scope: str
    asked_nets: int
    added_nets: int
    completeness: float
    vias: int
    copper_mm: float
    seconds: float

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class SpatialResult:
    solution: RoutingSolution
    partition: Partition
    stages: tuple[Stage, ...] = ()
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "partition": self.partition.as_dict(),
            "stages": [s.as_dict() for s in self.stages],
            "notes": list(self.notes),
        }


def _namespaced(solution: RoutingSolution, label: str):
    """The stage's copper with its ids prefixed by the stage label.

    Not cosmetic, and not optional. :mod:`routerlib.connectivity` unions
    ``(copper id, layer)`` nodes and :mod:`routerlib.drc` skips a pair when
    ``other.id == item.id``, so two stages that both mint ``v0`` collapse into
    one union-find node carrying two nets — **a connection that does not exist
    and a short that is never checked, at once**. ``maze-astar`` and
    ``plane-and-classes`` both mint ``v0``, so this is reachable on the first
    two-stage board and not a theoretical concern.
    """
    return (
        tuple(dataclasses.replace(t, id=f"{label}.{t.id}") for t in solution.traces),
        tuple(dataclasses.replace(v, id=f"{label}.{v.id}") for v in solution.vias),
    )


def _merge(base: RoutingSolution, extra: RoutingSolution, label: str) -> RoutingSolution:
    traces, vias = _namespaced(extra, label)
    return dataclasses.replace(
        base,
        traces=tuple(base.traces) + traces,
        vias=tuple(base.vias) + vias,
        iterations=base.iterations + extra.iterations,
        nodes_expanded=base.nodes_expanded + extra.nodes_expanded,
        wall_clock_s=base.wall_clock_s + extra.wall_clock_s,
    )


def _subproblem(
    problem: RoutingProblem,
    merged: RoutingSolution,
    net_ids: Sequence[str],
    *,
    clearance_scale: float = 1.0,
) -> RoutingProblem:
    """The same placed board, a subset of the nets, and every committed piece
    of copper as an obstacle.

    Only the net list changes. Every pad, drill, keepout and plane stays, so a
    stage still has to keep clear of the parts it is not routing — the property
    that makes a composition unable to invent a violation no stage could see.
    """
    wanted = set(net_ids)
    rules = problem.rules
    if clearance_scale != 1.0:
        rules = dataclasses.replace(
            rules,
            target_clearance_mm=round(rules.target_clearance_mm * clearance_scale, 6),
        )
    return dataclasses.replace(
        problem,
        nets=tuple(n for n in problem.nets if n.id in wanted),
        rules=rules,
        existing_traces=tuple(merged.traces),
        existing_vias=tuple(merged.vias),
    )


def _run(registry: Registry, name: str, problem: RoutingProblem, budget: Budget):
    if name not in registry:
        raise KeyError(
            f"router {name!r} is not registered "
            f"(have: {', '.join(sorted(registry))})"
        )
    started = time.perf_counter()
    solution = registry[name]().route(problem, budget)
    return solution, time.perf_counter() - started


def _stage(problem, label, router, scope, asked, before, merged, seconds) -> Stage:
    linked = conn.analyse(problem, merged)
    return Stage(
        stage=label,
        router=router,
        scope=scope,
        asked_nets=asked,
        added_nets=len(linked.connected_nets) - before,
        completeness=round(linked.completeness, 6),
        vias=len(merged.vias),
        copper_mm=round(merged.copper_length_mm, 4),
        seconds=round(seconds, 3),
    )


def route(
    problem: RoutingProblem,
    budget: Budget,
    registry: Registry,
    *,
    global_expert: str = GLOBAL_EXPERT,
    experts: Mapping[str, str] | None = None,
    region_order: str = "hardest-first",
    boundary_clearance: float = 1.0,
    residue: Sequence[str] = (),
    partition_kwargs: Mapping | None = None,
    given: Partition | None = None,
) -> SpatialResult:
    """Crossings first, then each region with its expert, then the residue.

    Deterministic: the partition is a function of the placement, the region
    order is a total order on measured numbers with the region id as the last
    tie-break, every family is itself deterministic under a counted budget, and
    nothing here reads a clock except to report one.

    A family missing from the registry costs a stage and says so in the notes;
    it never silently returns a board with less copper on it than the caller
    asked for.
    """
    if region_order not in REGION_ORDERS:
        raise ValueError(
            f"unknown region order {region_order!r} "
            f"(have: {', '.join(REGION_ORDERS)})"
        )
    part = given or partition(
        problem,
        experts=experts,
        global_expert=global_expert,
        **dict(partition_kwargs or {}),
    )

    merged = RoutingSolution(
        router="spatial",
        traces=problem.existing_traces,
        vias=problem.existing_vias,
        complete=False,
    )
    stages: list[Stage] = []
    notes: list[str] = []
    if not part.seam:
        notes.append(f"no seam: {part.why}; every net goes to {global_expert}")

    def run_stage(label: str, router: str, scope: str, net_ids: Sequence[str],
                  clearance_scale: float = 1.0) -> None:
        nonlocal merged
        if not net_ids:
            return
        linked = conn.analyse(problem, merged)
        before = len(linked.connected_nets)
        open_nets = [n for n in net_ids if n in set(linked.unconnected_nets)]
        if not open_nets:
            notes.append(f"{label}: every net already connected, skipped")
            return
        stage_problem = _subproblem(
            problem, merged, open_nets, clearance_scale=clearance_scale
        )
        try:
            solution, seconds = _run(registry, router, stage_problem, budget)
        except Exception as exc:  # noqa: BLE001 — a family that dies costs a stage
            notes.append(f"{label}: {router} raised {type(exc).__name__}: {exc}; skipped")
            return
        merged = _merge(merged, solution, label)
        stages.append(
            _stage(problem, label, router, scope, len(open_nets), before, merged, seconds)
        )

    # 1. The crossings, at the boundary clearance, before anything else.
    run_stage(
        "crossing",
        global_expert,
        "crossing",
        part.crossing_nets,
        clearance_scale=boundary_clearance,
    )

    # 2. Each region, inside those fixed boundary conditions.
    if region_order == "hardest-first":
        order = sorted(part.regions, key=lambda r: r.difficulty_key)
    elif region_order == "largest-first":
        order = sorted(part.regions, key=lambda r: (-r.pad_count, r.id))
    else:
        order = sorted(part.regions, key=lambda r: r.id)
    for region in order:
        run_stage(region.id, region.expert, region.character, region.interior_nets)

    # 3. Whatever is left, to the followers, which is the relay applied to the
    #    residue rather than to the whole board.
    for name in residue:
        linked = conn.analyse(problem, merged)
        if not linked.unconnected_nets:
            break
        run_stage(f"residue[{name}]", name, "residue", linked.unconnected_nets)

    linked = conn.analyse(problem, merged)
    chain = "+".join(s.router for s in stages) or "none"
    solution = dataclasses.replace(
        merged,
        router=f"spatial[{chain}]",
        complete=linked.completeness >= 1.0,
        unrouted_nets=linked.unconnected_nets,
        notes=tuple(notes),
    )
    return SpatialResult(
        solution=solution, partition=part, stages=tuple(stages), notes=tuple(notes)
    )


class SpatialRouter:
    """The composition as a :class:`~routerlib.model.Router`, so the tournament
    harness, the scorer and the determinism check can treat it like a family.

    It needs a registry, because it is nine families in a coat. There is no
    default: a composition that silently falls back to one router when its
    registry is empty would report a spatial result for a board it never
    partitioned.
    """

    name = "spatial-regions"

    def __init__(self, registry: Registry, **options) -> None:
        if not registry:
            raise ValueError("SpatialRouter needs a registry of routers to compose")
        self.registry = registry
        self.options = options
        self.last: SpatialResult | None = None

    def route(self, problem: RoutingProblem, budget: Budget) -> RoutingSolution:
        result = route(problem, budget, self.registry, **self.options)
        self.last = result
        return result.solution


__all__ = [
    "Cell",
    "Cut",
    "EXPERTS",
    "FINE_PITCH_MM",
    "GLOBAL_EXPERT",
    "LATTICE_GRID_SCORE",
    "LATTICE_MIN_COMPONENTS",
    "Partition",
    "REGION_ORDERS",
    "REJECTED_ASSIGNMENTS",
    "Region",
    "SpatialResult",
    "SpatialRouter",
    "Stage",
    "cells_of",
    "net_cells",
    "partition",
    "route",
]
