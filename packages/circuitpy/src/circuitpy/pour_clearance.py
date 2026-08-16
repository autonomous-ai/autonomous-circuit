"""Push a copper pour back off anything it is too close to.

**The defect this closes.** A pour is drawn as polygons: one outer ring and an
inner ring punched around every piece of copper it must avoid. Both are
approximations of curves, and the approximation always errs *inward* — a circle
of radius R drawn as an n-gon through its own vertices measures only
``R · cos(π/n)`` at each chord midpoint. tscircuit uses 32 sides, so
``R · 0.995185``.

`POUR_CUTOUT_MARGIN_MM` (ledger #2) already handles that for board cutouts by
asking for a bigger margin than we need. **Vias have no such lever.**
`<copperpour>` takes `padMargin`, `traceMargin`, `clearance`, `boardEdgeMargin`
and `cutoutMargin`, and measured 2026-08-16 a via obeys none of them: with all
of them set to 0.3mm the cutout still came out at ``via_radius + 0.1``. The
arithmetic then closes exactly:

    (0.3 + 0.1) · 0.995185 − 0.3 = 0.09807 mm

against the 0.15mm clearance the zone itself declares, and under JLCPCB's
0.1mm floor. That is the single blocking finding on terminal-keyboard, and it
is one number away from being on every board we ever pour.

**Why this is a pipeline pass and not a prop.** There is no prop. Waiting for
one upstream means every poured board carries a real, marginal clearance
violation in the meantime, and "marginal" is the kind that survives DRC on one
fab's process and fails on another's.

**What it does.** For each pour, for each piece of copper on that layer
belonging to another net, it measures the pour's boundary — outer ring *and*
every inner ring — and where the gap is short it pushes the offending vertices
radially away from the obstacle until the gap holds *including* the chord error
of the ring they sit on. Vertices are only ever moved outward from copper, so
the pour can lose area and can never gain any.

**What it will not do.** It never moves a vertex it does not have to, never
merges or deletes a ring, and never touches a pour whose geometry already
holds. A board with nothing wrong is returned byte-identical, which is what
lets the determinism hash stay meaningful.

A note on why the measurement missed this for a day: the first pass over this
geometry read `brep_shape.outer_ring` and stopped. Every hole the pour punches
lives in `inner_rings`, so the closest copper on the board was measured at
0.297mm when the true figure was 0.098mm. The check that reads a shape has to
read all of it — ledger lesson A, one shape further in.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import diffpair

#: Rings are regular polygons through their own vertices, so the boundary sags
#: to ``R · cos(π/n)`` between them. Measured on the real geometry rather than
#: assumed: every ring tscircuit emits for a round obstacle has 32 vertices.
DEFAULT_RING_SIDES = 32

#: Never rewrite a vertex for less than this. A pour that shifts by a
#: nanometre is churn in the artifact and noise in the determinism hash.
MIN_PUSH_MM = 1e-4

#: Pushing a vertex clear of one obstacle can carry it toward the next, so the
#: sweep repeats until nothing moves. Bounded rather than open-ended: a vertex
#: caught between two obstacles that are closer together than the rule cannot
#: be placed anywhere legal, and looping forever on it would hide that. When
#: the budget runs out the pour keeps its best position and the measured gap
#: is reported as it stands.
MAX_PASSES = 8


@dataclass(frozen=True)
class PourFix:
    """One pour, and what had to move."""

    pour_id: str
    layer: str
    vertices_moved: int
    worst_before_mm: float
    worst_after_mm: float
    required_mm: float
    offender: str | None = None


@dataclass(frozen=True)
class PourResult:
    ran: bool
    fixes: tuple[PourFix, ...] = ()
    required_mm: float = 0.0
    elapsed_s: float = 0.0
    note: str | None = None

    @property
    def changed(self) -> bool:
        return any(f.vertices_moved for f in self.fixes)

    def as_dict(self) -> dict:
        return {
            "ran": self.ran,
            "changed": self.changed,
            "requiredMm": self.required_mm,
            "elapsed_s": round(self.elapsed_s, 3),
            "note": self.note,
            "pours": [
                {
                    "id": f.pour_id,
                    "layer": f.layer,
                    "verticesMoved": f.vertices_moved,
                    "worstBeforeMm": round(f.worst_before_mm, 4),
                    "worstAfterMm": round(f.worst_after_mm, 4),
                    "offender": f.offender,
                }
                for f in self.fixes if f.vertices_moved
            ],
        }

    def findings(self) -> list[dict]:
        if not self.ran or not self.changed:
            return []
        moved = sum(f.vertices_moved for f in self.fixes)
        worst = min(f.worst_before_mm for f in self.fixes if f.vertices_moved)
        after = min(f.worst_after_mm for f in self.fixes if f.vertices_moved)
        culprit = next(
            (f.offender for f in self.fixes if f.vertices_moved and f.offender), None
        )
        return [{
            "part": "board",
            "kind": "pour_clearance_repaired",
            "severity": "info",
            "detail": (
                f"the copper pour came {worst:.4f}mm from other-net copper "
                f"against a {self.required_mm:g}mm floor"
                + (f" (nearest: {culprit})" if culprit else "")
                + f"; {moved} boundary vertices pushed back, worst gap now "
                f"{after:.4f}mm. A pour cuts a 32-gon around each obstacle and "
                "a via obeys none of the pour's margin props, so the shortfall "
                "is structural rather than this board's"
            ),
        }]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _rings(pour: dict) -> list[list[dict]]:
    """Every boundary of this pour: the outer ring and each hole in it.

    Reading only `outer_ring` is how the true worst gap on terminal-keyboard
    was measured as 0.297mm when it was 0.098mm.
    """
    brep = pour.get("brep_shape") or {}
    out: list[list[dict]] = []
    for key in ("outer_ring", "inner_rings"):
        value = brep.get(key)
        if value is None:
            continue
        candidates = value if key == "inner_rings" else [value]
        for ring in candidates or []:
            verts = ring.get("vertices") if isinstance(ring, dict) else ring
            if isinstance(verts, list) and len(verts) >= 3:
                out.append(verts)
    return out


def _ring_gap(verts: Sequence[dict], obstacle: "_Round") -> float:
    """The real gap between a ring's boundary and an obstacle.

    Measured against the polygon's **edges**, which is exact for any polygon.
    The first version multiplied the vertex distance by ``cos(π/n)`` to allow
    for the chord sag, which is right for a 32-gon standing in for a circle and
    badly wrong for the pour's outer boundary: a rectangle has four vertices,
    ``cos(π/4)`` is 0.707, and the model claimed the boundary sat at 70% of the
    corner distance. Measuring the edge needs no model and is right for both.
    """
    best = math.inf
    n = len(verts)
    for i in range(n):
        ax, ay = diffpair._f(verts[i].get("x")), diffpair._f(verts[i].get("y"))
        bx, by = diffpair._f(verts[(i + 1) % n].get("x")), \
            diffpair._f(verts[(i + 1) % n].get("y"))
        if None in (ax, ay, bx, by):
            continue
        best = min(best, diffpair._seg_point_distance(
            ax, ay, bx, by, obstacle.cx, obstacle.cy))
    return best - obstacle.radius


def _split_edges_near(pour: dict, obstacles: Sequence["_Round"],
                      required: float) -> int:
    """Give every too-close edge a vertex at its closest point.

    The radial push below moves *vertices*, which is the whole answer for a
    cutout drawn as a 32-gon — a vertex is always near the obstacle it goes
    round. It is no answer at all for the pour's outer boundary, which on a
    rectangular board is four vertices at the corners: an edge can run 0.1mm
    past a via with its nearest vertex 40mm away, and a vertex-only sweep
    reports the pour as clear.

    Splitting is the minimal repair. One new vertex on the existing line
    changes no geometry by itself — it only gives the push something local to
    act on, so the boundary dents around the obstacle instead of the whole
    edge swinging.
    """
    added = 0
    brep = pour.get("brep_shape") or {}
    rings: list[Any] = []
    if brep.get("outer_ring") is not None:
        rings.append(brep["outer_ring"])
    rings.extend(brep.get("inner_rings") or [])
    for ring in rings:
        verts = ring.get("vertices") if isinstance(ring, dict) else ring
        if not isinstance(verts, list) or len(verts) < 3:
            continue
        out: list[dict] = []
        for i, vertex in enumerate(verts):
            out.append(vertex)
            nxt = verts[(i + 1) % len(verts)]
            ax, ay = diffpair._f(vertex.get("x")), diffpair._f(vertex.get("y"))
            bx, by = diffpair._f(nxt.get("x")), diffpair._f(nxt.get("y"))
            if None in (ax, ay, bx, by):
                continue
            dx, dy = bx - ax, by - ay
            length_sq = dx * dx + dy * dy
            if length_sq <= 1e-18:
                continue
            for obstacle in obstacles:
                t = ((obstacle.cx - ax) * dx + (obstacle.cy - ay) * dy) / length_sq
                if not (1e-6 < t < 1 - 1e-6):
                    continue          # the closest point is an existing vertex
                px, py = ax + t * dx, ay + t * dy
                if math.hypot(px - obstacle.cx, py - obstacle.cy) \
                        - obstacle.radius >= required - 1e-9:
                    continue
                out.append({"x": round(px, 6), "y": round(py, 6)})
                added += 1
                break
        if added and isinstance(ring, dict):
            ring["vertices"] = out
    return added


@dataclass(frozen=True)
class _Round:
    """An obstacle reduced to a disc: the only shape a radial push is exact
    for. Rectangular pads use their circumscribed disc, which pushes the pour
    slightly further than strictly needed — the safe direction."""

    label: str
    cx: float
    cy: float
    radius: float


def _round_obstacles(board: diffpair._Board, pour_net: str | None,
                     layer: str) -> list[_Round]:
    """Other-net copper on this layer, as discs."""
    out: list[_Round] = []

    def keep(net: Any) -> bool:
        return not (pour_net and net and net == pour_net)

    for via in board.by_type.get("pcb_via", []):
        x, y = diffpair._f(via.get("x")), diffpair._f(via.get("y"))
        if x is None or y is None:
            continue
        net = board.net_key_of_pcb_port(str(via.get("pcb_port_id") or "")) \
            or via.get("subcircuit_connectivity_map_key")
        if not keep(net):
            continue
        radius = (diffpair._f(via.get("outer_diameter"), 0.6) or 0.6) / 2
        out.append(_Round(str(via.get("pcb_via_id") or "via"), x, y, radius))

    for pad in board.by_type.get("pcb_smtpad", []):
        if str(pad.get("layer") or "top") != layer:
            continue
        x, y = diffpair._f(pad.get("x")), diffpair._f(pad.get("y"))
        if x is None or y is None:
            continue
        if not keep(board.net_key_of_pcb_port(str(pad.get("pcb_port_id") or ""))):
            continue
        if pad.get("shape") == "circle":
            radius = diffpair._f(pad.get("radius"), 0.0) or 0.0
        else:
            w = diffpair._f(pad.get("width"), 0.0) or 0.0
            h = diffpair._f(pad.get("height"), 0.0) or 0.0
            radius = math.hypot(w, h) / 2
        if radius > 0:
            out.append(_Round(str(pad.get("pcb_smtpad_id") or "pad"), x, y, radius))

    for hole in board.by_type.get("pcb_plated_hole", []):
        x, y = diffpair._f(hole.get("x")), diffpair._f(hole.get("y"))
        if x is None or y is None:
            continue
        if not keep(board.net_key_of_pcb_port(str(hole.get("pcb_port_id") or ""))):
            continue
        w = diffpair._f(hole.get("outer_width")) or diffpair._f(hole.get("outer_diameter")) or 0.0
        h = diffpair._f(hole.get("outer_height")) or diffpair._f(hole.get("outer_diameter")) or 0.0
        radius = math.hypot(w, h) / 2
        if radius > 0:
            out.append(_Round(
                str(hole.get("pcb_plated_hole_id") or "pth"), x, y, radius))

    return out


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def repair_pour_clearance(
    circuit_json_path: Path, profile: Any, *, required_mm: float | None = None,
) -> PourResult:
    """Push every pour boundary off other-net copper. Never raises.

    ``required_mm`` defaults to the strictest number the packet has to satisfy:
    the fab's own copper floor and the clearance the exported KiCad zone
    declares about itself, whichever is larger. Satisfying only the smaller one
    is how a board passes our gate and fails the reviewer's.
    """
    import time

    started = time.monotonic()
    path = Path(circuit_json_path)
    try:
        elements = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PourResult(ran=False, note="circuit.json unreadable")
    if not isinstance(elements, list):
        return PourResult(ran=False, note="circuit.json is not an element array")

    pours = [
        e for e in elements
        if isinstance(e, dict) and e.get("type") == "pcb_copper_pour"
    ]
    if not pours:
        return PourResult(ran=False, note="no copper pour on this board")

    required = float(
        required_mm
        if required_mm is not None
        else max(
            float(getattr(profile, "min_clearance_mm", 0.10) or 0.10),
            float(getattr(profile, "kicad_zone_clearance_mm", 0.15) or 0.15),
        )
    )

    board = diffpair._Board(elements)
    net_of: dict[str, str] = {}
    for net in board.by_type.get("source_net", []):
        nid = str(net.get("source_net_id") or "")
        key = net.get("subcircuit_connectivity_map_key")
        if nid and isinstance(key, str):
            net_of[nid] = key

    fixes: list[PourFix] = []
    for pour in pours:
        layer = str(pour.get("layer") or "bottom")
        pour_net = net_of.get(str(pour.get("source_net_id") or ""))
        obstacles = _round_obstacles(board, pour_net, layer)
        if not obstacles:
            continue

        moved = 0
        worst_before = math.inf
        worst_after = math.inf
        culprit: str | None = None
        touched: set[int] = set()
        # A vertex-only sweep cannot see a long straight edge sliding past an
        # obstacle: the pour's *outer* boundary is a rectangle whose corners
        # are metres away from the via its edge grazes. Split such an edge at
        # its closest point first, so the sweep below has a vertex to push.
        split = _split_edges_near(pour, obstacles, required)
        # Iterate to a fixed point. Pushing a vertex clear of one via can carry
        # it toward the next one, and a single pass leaves those behind — on
        # terminal-keyboard the first version came out at -0.1019mm and
        # reported itself fixed. Bounded, because a vertex trapped between two
        # obstacles closer together than the rule cannot be placed at all, and
        # the honest answer there is to stop and let the check say so.
        for _ in range(MAX_PASSES):
            worst_pass = math.inf
            progressed = False
            for ring in _rings(pour):
                for obstacle in obstacles:
                    gap = _ring_gap(ring, obstacle)
                    worst_pass = min(worst_pass, gap)
                    if gap < worst_before:
                        worst_before = gap
                        culprit = obstacle.label
                    if gap >= required - 1e-9:
                        continue
                    # Push every vertex that is inside the required disc out to
                    # its rim, plus the shortfall this pass measured. The
                    # overshoot is what pays for the chord: the boundary
                    # between two pushed vertices sits inside both of them, and
                    # rather than model how far, the loop measures again.
                    shortfall = required - gap
                    target = obstacle.radius + required + shortfall
                    for vertex in ring:
                        vx = diffpair._f(vertex.get("x"))
                        vy = diffpair._f(vertex.get("y"))
                        if vx is None or vy is None:
                            continue
                        dx, dy = vx - obstacle.cx, vy - obstacle.cy
                        distance = math.hypot(dx, dy)
                        if distance <= 1e-9:
                            # A vertex exactly on the obstacle's centre has no
                            # direction to be pushed in. Leave it; the measured
                            # gap below will report the truth.
                            continue
                        if distance >= target - MIN_PUSH_MM:
                            continue
                        scale = target / distance
                        vertex["x"] = round(obstacle.cx + dx * scale, 6)
                        vertex["y"] = round(obstacle.cy + dy * scale, 6)
                        touched.add(id(vertex))
                        progressed = True
            if not progressed or worst_pass >= required - 1e-9:
                break
        moved = len(touched) + split

        # Re-measure what was written rather than trusting the intent.
        for ring in _rings(pour):
            for obstacle in obstacles:
                worst_after = min(worst_after, _ring_gap(ring, obstacle))

        fixes.append(PourFix(
            pour_id=str(pour.get("pcb_copper_pour_id") or "pour"),
            layer=layer,
            vertices_moved=moved,
            worst_before_mm=0.0 if worst_before is math.inf else worst_before,
            worst_after_mm=0.0 if worst_after is math.inf else worst_after,
            required_mm=required,
            offender=culprit,
        ))

    result = PourResult(
        ran=True, fixes=tuple(fixes), required_mm=required,
        elapsed_s=time.monotonic() - started,
    )
    if result.changed:
        path.write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")
    return result
