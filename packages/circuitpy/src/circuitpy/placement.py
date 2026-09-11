"""The placement score — routability as numbers, before any copper exists.

Measured 2026-09-11 on desk-cube-astra-run7: the agent called `preflight` 62
times and `fastcheck` 11 times — it *was* iterating placement — and the board
still routed to 190 vias and 789 jogs, because the ruler it iterated against
grades overlaps, decoupling distance and price tier, not whether the parts sit
where the copper can reach them. The harness-free Astra board on the same
brief had 83 vias: same model, a ruler it could see (the ratsnest in KiCad)
and a loop that cost seconds. A placement loop cannot optimise what nothing
measures, so this is the ruler.

Everything here is derived from the unrouted IR alone (`routingDisabled`
compiles produce it too), deterministic, and advisory: `build.placement` in
the sidecar, the same block in `preflight` and `fastcheck`, and one
`placement_summary` info finding. It never blocks.

Definitions
- **ratsnest**: for every net with ≥ 2 pins, the minimum spanning tree of its
  pins (Euclidean, Prim). Its length is the shortest copper that could ever
  connect the net; the router only adds to it.
- **crossing**: two ratsnest edges of different nets that intersect. Each one
  is a via pair or a detour the router will have to pay for.
- **congestion**: pins per `CELL_MM` square. The worst cell is where the
  router runs out of room first.
- **decoupling**: a capacitor with one pin on a ground net and one on a power
  net, measured to the nearest chip pin on that power net.
- **crystal**: a crystal measured to the nearest chip pin on either of its nets.
- **connector to edge**: a connector's body to the nearest board edge.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable

#: Congestion cell size. 5 mm is about one QFN plus its decoupling.
CELL_MM = 5.0

#: A decoupling capacitor further than this from its pin is a finding an
#: engineer would raise on sight (the loop inductance grows with the distance).
DECOUPLING_MM = 3.0

#: A crystal further than this from the oscillator pins is the same story.
CRYSTAL_MM = 10.0

#: How many worst offenders to name per list.
TOP_N = 5


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _mst_edges(points: list[tuple[float, float]]) -> list[tuple[int, int, float]]:
    """Prim's tree over ``points``; ``(i, j, length)`` per edge."""
    n = len(points)
    if n < 2:
        return []
    in_tree = [False] * n
    best = [math.inf] * n
    parent = [-1] * n
    best[0] = 0.0
    edges: list[tuple[int, int, float]] = []
    for _ in range(n):
        u = min((k for k in range(n) if not in_tree[k]), key=lambda k: best[k])
        in_tree[u] = True
        if parent[u] >= 0:
            edges.append((parent[u], u, best[u]))
        for v in range(n):
            if not in_tree[v]:
                d = _dist(points[u], points[v])
                if d < best[v]:
                    best[v] = d
                    parent[v] = u
    return edges


def _segments_cross(p1, p2, p3, p4) -> bool:
    """Proper intersection of two segments; touching at an endpoint is not a
    crossing (two nets meeting at a shared pad centre cannot happen, and a
    ratsnest edge ending exactly on another's line is a coincidence, not a
    routing cost)."""
    def orient(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


class _Board:
    """The unrouted IR, indexed for the questions below."""

    def __init__(self, elements: Iterable[Any]):
        els = [e for e in elements if isinstance(e, dict)]
        self.by_type: dict[str, list[dict]] = defaultdict(list)
        for e in els:
            self.by_type[str(e.get("type") or "")].append(e)
        self.source_port = {str(p.get("source_port_id")): p for p in self.by_type["source_port"]}
        self.source_component = {
            str(c.get("source_component_id")): c for c in self.by_type["source_component"]
        }
        self.pcb_component = {
            str(c.get("pcb_component_id")): c for c in self.by_type["pcb_component"]
        }
        # net key -> display name (unnamed nets keep their key)
        self.net_name: dict[str, str] = {}
        self.net_flags: dict[str, dict] = {}
        for n in self.by_type["source_net"]:
            key = str(n.get("subcircuit_connectivity_map_key") or n.get("source_net_id") or "")
            if key:
                self.net_name[key] = str(n.get("name") or key)
                self.net_flags[key] = n
        # pins: (x, y, net key, pcb_component_id, refdes, source_port)
        self.pins: list[dict] = []
        for port in self.by_type["pcb_port"]:
            if not (_num(port.get("x")) and _num(port.get("y"))):
                continue
            sp = self.source_port.get(str(port.get("source_port_id")))
            if sp is None:
                continue
            key = str(sp.get("subcircuit_connectivity_map_key") or "")
            if not key:
                continue
            comp = self.pcb_component.get(str(port.get("pcb_component_id")))
            sc = self.source_component.get(str(comp.get("source_component_id"))) if comp else None
            self.pins.append({
                "x": float(port["x"]), "y": float(port["y"]), "net": key,
                "component": str(port.get("pcb_component_id") or ""),
                "refdes": str((sc or {}).get("name") or ""),
                "ftype": str((sc or {}).get("ftype") or ""),
            })
        self.by_net: dict[str, list[dict]] = defaultdict(list)
        for p in self.pins:
            self.by_net[p["net"]].append(p)
        board = next(iter(self.by_type["pcb_board"]), None)
        self.board = board
        if board and _num(board.get("width")) and _num(board.get("height")):
            c = board.get("center") or {}
            cx = float(c.get("x") or 0.0)
            cy = float(c.get("y") or 0.0)
            w, h = float(board["width"]), float(board["height"])
            self.edges = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        else:
            self.edges = None

    def name(self, key: str) -> str:
        return self.net_name.get(key, key)

    def is_ground(self, key: str) -> bool:
        n = self.net_flags.get(key) or {}
        return bool(n.get("is_ground")) or self.name(key).upper() in {"GND", "GROUND"}

    def is_power(self, key: str) -> bool:
        n = self.net_flags.get(key) or {}
        if n.get("is_power") or n.get("is_positive_voltage_source"):
            return True
        nm = self.name(key).upper()
        return nm.startswith(("V", "+")) and not self.is_ground(key)


def ratsnest(board: _Board) -> tuple[list[dict], dict[str, float]]:
    """Every MST edge as ``{net, a:(x,y), b:(x,y), mm}`` and length per net."""
    edges: list[dict] = []
    per_net: dict[str, float] = {}
    for key, pins in board.by_net.items():
        pts = [(p["x"], p["y"]) for p in pins]
        if len(pts) < 2:
            continue
        total = 0.0
        for i, j, d in _mst_edges(pts):
            edges.append({"net": key, "a": pts[i], "b": pts[j], "mm": d})
            total += d
        per_net[key] = total
    return edges, per_net


def crossings(edges: list[dict]) -> int:
    n = 0
    for i in range(len(edges)):
        e = edges[i]
        for j in range(i + 1, len(edges)):
            f = edges[j]
            if e["net"] == f["net"]:
                continue
            if _segments_cross(e["a"], e["b"], f["a"], f["b"]):
                n += 1
    return n


def congestion(board: _Board, cell_mm: float = CELL_MM) -> dict[str, Any]:
    cells: dict[tuple[int, int], int] = defaultdict(int)
    for p in board.pins:
        cells[(math.floor(p["x"] / cell_mm), math.floor(p["y"] / cell_mm))] += 1
    if not cells:
        return {"cellMm": cell_mm, "worst": 0, "worstCell": None, "mean": 0.0, "cells": 0}
    worst_cell, worst = max(cells.items(), key=lambda kv: (kv[1], kv[0]))
    return {
        "cellMm": cell_mm,
        "worst": worst,
        "worstCell": [round((worst_cell[0] + 0.5) * cell_mm, 2), round((worst_cell[1] + 0.5) * cell_mm, 2)],
        "mean": round(sum(cells.values()) / len(cells), 2),
        "cells": len(cells),
    }


def _nearest_chip_pin(board: _Board, key: str, frm: tuple[float, float], exclude: str) -> tuple[float, dict | None]:
    best, who = math.inf, None
    for p in board.by_net.get(key, []):
        if p["component"] == exclude or p["ftype"] != "simple_chip":
            continue
        d = _dist(frm, (p["x"], p["y"]))
        if d < best:
            best, who = d, p
    return best, who


def decoupling(board: _Board) -> list[dict]:
    """Every (ground, power) capacitor, measured to the nearest chip pin on
    its power net. Sorted worst first."""
    out: list[dict] = []
    caps: dict[str, list[dict]] = defaultdict(list)
    for p in board.pins:
        if p["ftype"] == "simple_capacitor":
            caps[p["component"]].append(p)
    for comp, pins in caps.items():
        nets = {p["net"] for p in pins}
        grounds = [k for k in nets if board.is_ground(k)]
        powers = [k for k in nets if board.is_power(k) and not board.is_ground(k)]
        if not grounds or not powers:
            continue
        power = powers[0]
        power_pin = next(p for p in pins if p["net"] == power)
        d, chip = _nearest_chip_pin(board, power, (power_pin["x"], power_pin["y"]), comp)
        if chip is None:
            continue
        out.append({
            "cap": pins[0]["refdes"], "net": board.name(power), "chip": chip["refdes"], "mm": round(d, 2),
        })
    out.sort(key=lambda r: -r["mm"])
    return out


def crystals(board: _Board) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for p in board.pins:
        if p["ftype"] != "simple_crystal" or p["component"] in seen:
            continue
        seen.add(p["component"])
        best, chip = math.inf, None
        for q in board.pins:
            if q["component"] != p["component"]:
                continue
            # A four-pin crystal carries two ground pins; measured to the
            # nearest chip on GND it reads whatever sits closest, not the
            # oscillator. Only the oscillator nets count.
            if board.is_ground(q["net"]) or board.is_power(q["net"]):
                continue
            d, who = _nearest_chip_pin(board, q["net"], (q["x"], q["y"]), p["component"])
            if d < best:
                best, chip = d, who
        if chip is not None:
            out.append({"crystal": p["refdes"], "chip": chip["refdes"], "mm": round(best, 2)})
    out.sort(key=lambda r: -r["mm"])
    return out


def connectors_to_edge(board: _Board) -> list[dict]:
    if board.edges is None:
        return []
    x0, y0, x1, y1 = board.edges
    out: list[dict] = []
    for comp in board.by_type["pcb_component"]:
        sc = board.source_component.get(str(comp.get("source_component_id"))) or {}
        if str(sc.get("ftype") or "") != "simple_connector":
            continue
        c = comp.get("center") or {}
        if not (_num(c.get("x")) and _num(c.get("y")) and _num(comp.get("width")) and _num(comp.get("height"))):
            continue
        cx, cy, w, h = float(c["x"]), float(c["y"]), float(comp["width"]), float(comp["height"])
        d = min(cx - w / 2 - x0, x1 - (cx + w / 2), cy - h / 2 - y0, y1 - (cy + h / 2))
        out.append({"connector": str(sc.get("name") or ""), "mm": round(d, 2)})
    out.sort(key=lambda r: -r["mm"])
    return out


def score(elements: Iterable[Any]) -> dict[str, Any]:
    """The `build.placement` block for an IR, routed or not."""
    board = _Board(elements)
    edges, per_net = ratsnest(board)
    longest = sorted(per_net.items(), key=lambda kv: -kv[1])[:TOP_N]
    dec = decoupling(board)
    return {
        "pins": len(board.pins),
        "nets": len(per_net),
        "ratsnestMm": round(sum(per_net.values()), 1),
        "crossings": crossings(edges),
        "congestion": congestion(board),
        "decoupling": {
            "worstMm": dec[0]["mm"] if dec else None,
            "overLimit": sum(1 for r in dec if r["mm"] > DECOUPLING_MM),
            "limitMm": DECOUPLING_MM,
            "worst": dec[:TOP_N],
        },
        "crystals": crystals(board),
        "connectorsToEdgeMm": connectors_to_edge(board),
        "longestNets": [{"net": board.name(k), "mm": round(v, 1)} for k, v in longest],
    }


def ratsnest_edges(elements: Iterable[Any]) -> list[dict]:
    """The MST edges with display names, for the placement image."""
    board = _Board(elements)
    edges, _ = ratsnest(board)
    return [
        {"net": board.name(e["net"]), "a": list(e["a"]), "b": list(e["b"]), "mm": round(e["mm"], 3)}
        for e in edges
    ]


def summary_finding(placement: dict[str, Any]) -> dict[str, Any]:
    """One info finding so the placement round and the panel see the numbers."""
    dec = placement.get("decoupling") or {}
    cong = placement.get("congestion") or {}
    lead = (
        f"ratsnest {placement['ratsnestMm']}mm over {placement['nets']} nets, "
        f"{placement['crossings']} crossings, worst cell {cong.get('worst')} pins per "
        f"{cong.get('cellMm')}mm at {cong.get('worstCell')}"
    )
    parts = [lead]
    if dec.get("worst"):
        w = dec["worst"][0]
        parts.append(
            f"{dec['overLimit']} decoupling cap(s) over {dec['limitMm']}mm "
            f"(worst {w['cap']} {w['mm']}mm from {w['chip']} on {w['net']})"
        )
    for c in placement.get("crystals") or []:
        parts.append(f"{c['crystal']} {c['mm']}mm from {c['chip']}")
    return {
        "part": "board",
        "kind": "placement_summary",
        "detail": "; ".join(parts) + " — advisory: the ruler the placement loop pushes down before routing",
        "severity": "info",
    }
