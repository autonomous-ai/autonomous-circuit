"""Measure the harness's pad model against the pad. Independently.

Written to size a defect and kept as the tripwire for it. Until 2026-08-16
``routerlib.geometry`` modelled a rectangular pad as its **inscribed stadium**;
for a square pad that is the inscribed circle, and each corner stuck out by
``(sqrt(2) - 1) * w / 2`` — 0.21mm on a 1.0mm pad, more than twice the 0.09mm
gate. Measured here over every routed cell, that was **3 to 137 pad-trace pairs
per router** where the true pad overlapped copper the scorer called clear.

It walks every (pad, trace segment) pair of a routed instance and computes the
gap twice: once with ``routerlib.geometry.capsule_gap``, and once with the
arithmetic in this file, which shares no code with it. A rotated rectangle is
rotated into the pad's frame and measured against four edges; a stadium is
measured against its spine; a polygon against its own edges. Any pair where the
two disagree across the gate is reported.

**The expected result is now zero, and a non-zero one is a regression** — either
in the shape model or in this reference, and the two being written separately is
the point. The check runs over the copper of every family on disk, which is a
few hundred thousand pairs of real geometry.

    python3.12 scripts/pad_corner_gap.py --tournament work/tournament
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
for extra in (PACKAGE / "src", PACKAGE.parent / "circuitpy" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import verify_pipeline as vp  # noqa: E402


def _seg_point_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / den))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_seg_dist(ax, ay, bx, by, cx, cy, dx_, dy_) -> float:
    def cross(ox, oy, px, py, qx, qy):
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)

    d1 = cross(cx, cy, dx_, dy_, ax, ay)
    d2 = cross(cx, cy, dx_, dy_, bx, by)
    d3 = cross(ax, ay, bx, by, cx, cy)
    d4 = cross(ax, ay, bx, by, dx_, dy_)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(
        _seg_point_dist(ax, ay, cx, cy, dx_, dy_),
        _seg_point_dist(bx, by, cx, cy, dx_, dy_),
        _seg_point_dist(cx, cy, ax, ay, bx, by),
        _seg_point_dist(dx_, dy_, ax, ay, bx, by),
    )


def seg_to_rect_gap(seg, pad) -> float:
    """Distance from a segment's *centre line* to a rotated rectangle.

    Zero when the line enters the rectangle. Rotating the segment into the
    pad's own frame turns the problem into segment-vs-axis-aligned-rect, which
    is four segment-segment distances plus a containment test.
    """
    (ax, ay), (bx, by) = seg
    cx, cy = pad.center.x, pad.center.y
    theta = math.radians(-pad.rotation_deg)
    ct, st = math.cos(theta), math.sin(theta)

    def to_local(px, py):
        dx, dy = px - cx, py - cy
        return (dx * ct - dy * st, dx * st + dy * ct)

    lax, lay = to_local(ax, ay)
    lbx, lby = to_local(bx, by)
    hw, hh = pad.width_mm / 2.0, pad.height_mm / 2.0
    inside = lambda x, y: -hw <= x <= hw and -hh <= y <= hh  # noqa: E731
    if inside(lax, lay) or inside(lbx, lby):
        return 0.0
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    best = math.inf
    for i in range(4):
        c = corners[i]
        d = corners[(i + 1) % 4]
        best = min(best, _seg_seg_dist(lax, lay, lbx, lby, c[0], c[1], d[0], d[1]))
    return best


#: Shapes whose true outline is a stadium, not a rectangle. Measuring one of
#: these against its bounding rectangle is the *reference* being wrong, which
#: is how this script read 109 phantom misses on a model that was right.
STADIUM_SHAPES = frozenset(
    {"circle", "pill", "rotated_pill", "oval", "rotated_oval", "capsule"}
)


def seg_to_stadium_gap(seg, pad) -> float:
    """Distance from a segment's centre line to a pill or a round pad."""
    (ax, ay), (bx, by) = seg
    cx, cy = pad.center.x, pad.center.y
    radius = min(pad.width_mm, pad.height_mm) / 2.0
    half = abs(pad.height_mm - pad.width_mm) / 2.0
    if pad.height_mm >= pad.width_mm:
        sx0, sy0, sx1, sy1 = cx, cy - half, cx, cy + half
    else:
        sx0, sy0, sx1, sy1 = cx - half, cy, cx + half, cy
    if pad.rotation_deg:
        theta = math.radians(pad.rotation_deg)
        ct, st = math.cos(theta), math.sin(theta)

        def spin(px, py):
            dx, dy = px - cx, py - cy
            return (cx + dx * ct - dy * st, cy + dx * st + dy * ct)

        sx0, sy0 = spin(sx0, sy0)
        sx1, sy1 = spin(sx1, sy1)
    return max(0.0, _seg_seg_dist(ax, ay, bx, by, sx0, sy0, sx1, sy1) - radius)


def seg_to_polygon_gap(seg, pad) -> float:
    """Distance from a segment's centre line to a polygon pad's own outline."""
    (ax, ay), (bx, by) = seg
    verts = [(v.x, v.y) for v in pad.vertices]
    n = len(verts)
    inside = False
    for px, py in ((ax, ay), (bx, by)):
        crossings = False
        j = n - 1
        for i in range(n):
            xi, yi = verts[i]
            xj, yj = verts[j]
            if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                crossings = not crossings
            j = i
        inside = inside or crossings
    if inside:
        return 0.0
    best = math.inf
    for i in range(n):
        cx, cy = verts[i]
        dx_, dy_ = verts[(i + 1) % n]
        best = min(best, _seg_seg_dist(ax, ay, bx, by, cx, cy, dx_, dy_))
    return best


def reference_gap(seg, pad) -> float:
    """The pad's true outline, measured by arithmetic that is not the
    harness's. One dispatch, three shapes, no bounding boxes."""
    if pad.vertices:
        return seg_to_polygon_gap(seg, pad)
    if (pad.shape or "rect").lower() in STADIUM_SHAPES:
        return seg_to_stadium_gap(seg, pad)
    return seg_to_rect_gap(seg, pad)


def analyse(problem, solution, gate: float) -> dict:
    from routerlib.geometry import capsule_gap, pad_capsule, segment_capsule

    pads = list(problem.pads)
    missed_clearance: list[dict] = []
    missed_short: list[dict] = []
    seen = 0
    for trace in solution.traces:
        half = trace.width_mm / 2.0
        for p0, p1 in trace.segments:
            seg_cap = segment_capsule(p0.x, p0.y, p1.x, p1.y, trace.width_mm)
            for pad in pads:
                if pad.net == trace.net:
                    continue
                if not pad.reachable_from(trace.layer):
                    continue
                if abs(pad.center.x - p0.x) > 12 and abs(pad.center.x - p1.x) > 12:
                    continue
                if abs(pad.center.y - p0.y) > 12 and abs(pad.center.y - p1.y) > 12:
                    continue
                seen += 1
                model_gap = capsule_gap(seg_cap, pad_capsule(pad))
                true_gap = reference_gap(((p0.x, p0.y), (p1.x, p1.y)), pad) - half
                if model_gap >= gate > true_gap:
                    rec = {
                        "pad": pad.id,
                        "padNet": pad.net,
                        "traceNet": trace.net,
                        "layer": trace.layer,
                        "padShape": pad.shape,
                        "padWidth": round(pad.width_mm, 4),
                        "padHeight": round(pad.height_mm, 4),
                        "modelGapMm": round(model_gap, 4),
                        "trueGapMm": round(true_gap, 4),
                        "at": [round(p0.x, 3), round(p0.y, 3)],
                    }
                    (missed_short if true_gap <= 0 else missed_clearance).append(rec)
    return {
        "pairsChecked": seen,
        "missedShorts": len(missed_short),
        "missedClearance": len(missed_clearance),
        "worstMissMm": round(
            min([r["trueGapMm"] for r in missed_short + missed_clearance] or [gate]), 4
        ),
        "examples": (missed_short + missed_clearance)[:8],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tournament", default=str(PACKAGE.parent.parent / "work" / "tournament"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    from routerlib.bench import INSTANCE_DIR, load_instance

    root = Path(args.tournament)
    results: dict = {}
    for rdir in sorted((root / "copper").iterdir()):
        if not rdir.is_dir():
            continue
        for f in sorted(rdir.glob("*.json")):
            problem = load_instance(Path(INSTANCE_DIR) / f"{f.stem}.json")
            gate = problem.rules.clearance_gate_mm
            solution = vp.rebuild_solution(problem, json.loads(f.read_text()))
            r = analyse(problem, solution, gate)
            results.setdefault(rdir.name, {})[f.stem] = r
            if r["missedShorts"] or r["missedClearance"]:
                print(f"{rdir.name:<24}{f.stem:<48}"
                      f"shorts={r['missedShorts']:<4}clearance={r['missedClearance']:<4}"
                      f"worst={r['worstMissMm']}", flush=True)
    print()
    print(f"{'router':<24}{'missed shorts':>15}{'missed clearance':>18}")
    for router, per in sorted(results.items()):
        s = sum(v["missedShorts"] for v in per.values())
        c = sum(v["missedClearance"] for v in per.values())
        print(f"{router:<24}{s:>15}{c:>18}")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
