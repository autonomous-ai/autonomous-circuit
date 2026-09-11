"""The craft score — what the copper looks like to an engineer, as numbers.

The gauntlet grades the technical floor: connectivity, clearance, current,
DRC. It has never had a number for the things a hardware reviewer sees first
— how many vias, how far a net wanders past the straight line, how many
stubs and jogs, whether related traces travel together. Measured 2026-09-10
on the same brief: the harness-free Astra board had 83 vias and 45° copper;
the app's had 150 vias, 341 off-grid stubs and 245 KiCad advisories, and no
finding in the sidecar said so. A repair loop cannot optimise what nothing
measures, and a panel cannot score "tidy" without a ruler.

Everything here is derived from the routed IR alone, deterministic, and
advisory: one `craft_summary` info finding plus a `build.craft` block. It never
blocks — a board is orderable on the floor, not on taste.

Definitions
- **detour**: routed length of a `pcb_trace` over the straight-line distance
  between its first and last point (planar; vias add nothing here).
- **jog**: a wire segment shorter than `JOG_MM` — the 0.05 mm-grid stutter
  the shipped router leaves in corners.
- **off-grid**: a segment whose angle is not a multiple of 45°.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

#: A segment shorter than this is a stutter, not a route decision.
JOG_MM = 0.25

#: Ignore detours on hops shorter than this — a 0.6 mm pad-to-cap hop that
#: routes at 1.4 mm is a 2.3× ratio and means nothing.
MIN_DETOUR_BASE_MM = 2.0

#: How many worst-detour nets to name.
TOP_N = 5


def _length(route: list[dict]) -> tuple[float, int, int, int, int]:
    """(planar length, segments, jogs, off-grid segments, vias)."""
    pts = [p for p in route if isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float))]
    length = 0.0
    segs = jogs = off = 0
    vias = sum(1 for p in route if str(p.get("route_type") or "") == "via")
    for a, b in zip(pts, pts[1:]):
        if str(a.get("route_type") or "") == "via" or str(b.get("route_type") or "") == "via":
            continue
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        d = math.hypot(dx, dy)
        if d <= 1e-9:
            continue
        length += d
        segs += 1
        if d < JOG_MM:
            jogs += 1
        ang = abs(math.degrees(math.atan2(dy, dx))) % 45.0
        if min(ang, 45.0 - ang) > 0.5:
            off += 1
    return length, segs, jogs, off, vias


def score(elements: Iterable[Any]) -> dict[str, Any]:
    """The `build.craft` block for a routed IR."""
    traces = [e for e in elements if isinstance(e, dict) and e.get("type") == "pcb_trace"]
    total_len = 0.0
    total_segs = total_jogs = total_off = 0
    detours: list[tuple[float, str, float, float]] = []
    for t in traces:
        route = t.get("route") or []
        length, segs, jogs, off, _ = _length(route)
        total_len += length
        total_segs += segs
        total_jogs += jogs
        total_off += off
        pts = [p for p in route if isinstance(p.get("x"), (int, float))]
        if len(pts) >= 2:
            straight = math.hypot(pts[-1]["x"] - pts[0]["x"], pts[-1]["y"] - pts[0]["y"])
            if straight >= MIN_DETOUR_BASE_MM and length > 0:
                detours.append((length / straight, str(t.get("pcb_trace_id") or ""), length, straight))
    vias = sum(1 for e in elements if isinstance(e, dict) and e.get("type") == "pcb_via")
    vias_by_net, pair_vias = _vias_by_net(elements)
    detours.sort(reverse=True)
    worst = [
        {"trace": tid, "ratio": round(r, 2), "routedMm": round(l, 2), "straightMm": round(s, 2)}
        for r, tid, l, s in detours[:TOP_N]
    ]
    mean_detour = (sum(r for r, *_ in detours) / len(detours)) if detours else 1.0
    return {
        "vias": vias,
        "routedCopperMm": round(total_len, 1),
        "segments": total_segs,
        "jogs": total_jogs,
        "offGridSegments": total_off,
        "meanDetour": round(mean_detour, 2),
        "worstDetours": worst,
        "viasByNet": vias_by_net,
        "pairVias": pair_vias,
    }


def _vias_by_net(elements: Iterable[Any]) -> tuple[list[dict], dict[str, int]]:
    """The nets that spend the most vias, and every differential pair's via
    count. The harness-free desk cube (2026-09-11) shaped USB D+/D- on one
    layer by hand and dropped two signal vias; a pair that changes layer is
    a pair whose reference plane changes under it, so the number to push to
    is zero."""
    from . import diffpair

    els = [e for e in elements if isinstance(e, dict)]
    names: dict[str, str] = {}
    for n in els:
        if n.get("type") == "source_net":
            names[str(n.get("source_net_id") or "")] = str(n.get("name") or n.get("source_net_id") or "")
    counts: dict[str, int] = {}
    for v in els:
        if v.get("type") != "pcb_via":
            continue
        net = names.get(str(v.get("source_net_id") or ""), str(v.get("source_net_id") or ""))
        if net:
            counts[net] = counts.get(net, 0) + 1
    by_net = [{"net": k, "vias": n} for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]]
    pairs: dict[str, int] = {}
    for p, n in diffpair.find_pairs(names.values()):
        pairs[f"{p}/{n}"] = counts.get(p, 0) + counts.get(n, 0)
    return by_net, pairs


def summary_finding(craft: dict[str, Any]) -> dict[str, Any]:
    """One info finding so the craft round and the panel see the numbers."""
    worst = craft.get("worstDetours") or []
    lead = (
        f"{craft['vias']} vias, {craft['routedCopperMm']}mm of copper in "
        f"{craft['segments']} segments ({craft['jogs']} jogs under {JOG_MM}mm, "
        f"{craft['offGridSegments']} off the 45° grid), mean detour {craft['meanDetour']}×"
    )
    pairs = craft.get("pairVias") or {}
    if pairs:
        lead += "; pair vias " + ", ".join(f"{k} {v}" for k, v in pairs.items())
    tail = (
        "; worst: " + ", ".join(
            f"{w['trace']} {w['ratio']}× ({w['routedMm']}mm for {w['straightMm']}mm)"
            for w in worst[:3]
        )
        if worst else ""
    )
    return {
        "part": "board",
        "kind": "craft_summary",
        "detail": lead + tail + " — advisory: the floor is met; repair mode can shorten a detour or drop a via without a re-route",
        "severity": "info",
    }
