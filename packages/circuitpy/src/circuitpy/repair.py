"""Surgical copper edits on a routed ``circuit.json`` — repair mode's hands.

**Why this exists (2026-09-10).** Astra run #4 spent twelve rebuilds chasing one
via; Claude's desk cube went 3 → 1 → 7 fixing a 12 mm crystal trace by moving
the crystal, because every fix in this pipeline was "edit the TSX, regenerate":
the router re-routes every net and the defect moves somewhere else. Astra's
harness-free board was built the other way — route once, then read each finding
and fix that copper — and it converged in a 1-minute cycle. This module is that
cycle's edit step; :func:`circuitpy.generation.build_board` in repair mode is the
check step.

**What it will do.** Move one route point of one trace. Move a via together with
every route point that sits on it, so the join the contiguity check measures
stays a join. Insert a point on a trace. Delete points from a trace.

**What it will not do.** Move the wire endpoint that sits on a via by itself
(``move_via`` moves the join whole). Change a net, a pad, a part, a layer stack or a board
outline — none of those are copper repairs, and each is the way a "repair"
silently makes a different board. It never moves a route point anchored to a
pad (`start_pcb_port_id` / `end_pcb_port_id`) and never moves a via point
through ``move_point`` — a via is placement, use ``move_via``. It refuses the
whole edit list on the first bad edit rather than applying half of it.

Edits are a JSON list. Each edit is one of::

    {"op": "move_point",    "trace": "<pcb_trace_id>", "index": 7, "x": 1.2, "y": 3.4}
    {"op": "move_via",      "via": "<pcb_via_id>",     "x": 1.2, "y": 3.4}
    {"op": "insert_point",  "trace": "<pcb_trace_id>", "after": 7, "x": 1.2, "y": 3.4}
    {"op": "delete_points", "trace": "<pcb_trace_id>", "indices": [8, 9]}
    {"op": "set_layer",     "trace": "<pcb_trace_id>", "indices": [5, 6], "layer": "top"}
    {"op": "remove_via",    "via": "<pcb_via_id>"}
    {"op": "reroute",       "trace": "<pcb_trace_id>", "from_index": 3, "to_index": 9, "clearance": 0.15}

``reroute`` is the local router Astra asked for on 2026-09-11 ("xóa/đổi đường
của một net hoặc một vùng nhỏ; giữ phần còn lại; nhìn thấy toàn bộ copper"):
it replaces the wire points strictly between two vertices of one trace with a
new planar path found by A* on a 0.1 mm grid, avoiding every piece of
foreign copper on that layer (pads, plated holes, vias, other traces — the same
obstacle model `trace_clearance` measures with) by ``clearance`` plus half the
trace width, 45° moves allowed, corner cutting refused. Same layer at both
ends and no via between them; refused when no path exists inside the search
window (endpoints' bounding box grown by ``margin``, default 6 mm).

``set_layer`` re-layers wire vertices (not pad-anchored ones — a pad's layer is
the pad's). ``remove_via`` deletes a via and its route point once the copper
on both sides of it is on one layer, and refuses while the trace would still
change layer anywhere without a via. Together they take a two-via hop back to
one layer — the crystal net's 2 × 1.6 mm of barrel that no planar edit can
shorten (Claude desk cube, 2026-09-10: 7.94 mm straight + 3.2 mm of via = 11.1 mm
against a 10 mm ceiling).

Coordinates are board millimetres, the same frame the sidecar's findings use.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

#: Two points closer than this are "the same place" — a wire endpoint sitting
#: on its via. Below the contiguity check's 0.01 mm so a via that was already
#: within tolerance carries its wires with it.
COINCIDENT_MM = 0.005

#: A repair round that needs more edits than this is a re-route, not a repair.
MAX_EDITS = 200

OPS = ("move_point", "move_via", "insert_point", "delete_points", "set_layer", "remove_via", "reroute")


class RepairError(ValueError):
    """An edit this module refuses to apply. Nothing was written."""


def _num(edit: dict, key: str) -> float:
    value = edit.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RepairError(f"edit {edit.get('op')}: {key!r} must be a finite number, got {value!r}")
    return float(value)


def _anchored(point: dict) -> bool:
    return bool(point.get("start_pcb_port_id") or point.get("end_pcb_port_id"))


def _is_via(point: dict) -> bool:
    return str(point.get("route_type") or "") == "via"


def _on_a_via(route: list, i: int) -> bool:
    """Whether route[i] (a wire) sits on an adjacent via point."""
    px, py = route[i].get("x"), route[i].get("y")
    if not isinstance(px, (int, float)) or not isinstance(py, (int, float)):
        return False
    for j in (i - 1, i + 1):
        if 0 <= j < len(route) and _is_via(route[j]):
            vx, vy = route[j].get("x"), route[j].get("y")
            if isinstance(vx, (int, float)) and isinstance(vy, (int, float)) \
                    and math.hypot(px - vx, py - vy) <= COINCIDENT_MM:
                return True
    return False


def _trace(elements: list, trace_id: str, edit: dict) -> dict:
    for e in elements:
        if isinstance(e, dict) and e.get("type") == "pcb_trace" and e.get("pcb_trace_id") == trace_id:
            route = e.get("route")
            if not isinstance(route, list):
                raise RepairError(f"edit {edit.get('op')}: trace {trace_id} has no route")
            return e
    raise RepairError(f"edit {edit.get('op')}: no pcb_trace with id {trace_id!r}")


def _index(route: list, key: str, edit: dict, *, allow_end: bool = False) -> int:
    value = edit.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepairError(f"edit {edit.get('op')}: {key!r} must be an integer index")
    hi = len(route) - (0 if allow_end else 1)
    if not 0 <= value <= hi:
        raise RepairError(
            f"edit {edit.get('op')}: {key}={value} is outside the route (0..{hi})"
        )
    return value


def _move_point(elements: list, edit: dict) -> dict:
    trace = _trace(elements, str(edit.get("trace")), edit)
    route = trace["route"]
    i = _index(route, "index", edit)
    point = route[i]
    if _is_via(point):
        raise RepairError(
            f"move_point: route[{i}] of {trace['pcb_trace_id']} is a via — use move_via, "
            f"which carries the wires that meet it"
        )
    if _anchored(point):
        raise RepairError(
            f"move_point: route[{i}] of {trace['pcb_trace_id']} is anchored to a pad — "
            f"moving it disconnects the net; move the part instead"
        )
    if _on_a_via(route, i):
        # The wire endpoint that lands on a via is the via's anchor — moving
        # it alone is exactly how `trace_clearance` manufactured the
        # "via misaligned" blockers of 2026-09-10 (finding A).
        raise RepairError(
            f"move_point: route[{i}] of {trace['pcb_trace_id']} sits on a via — "
            f"use move_via, which moves the via and every wire that meets it"
        )
    x, y = _num(edit, "x"), _num(edit, "y")
    before = (point.get("x"), point.get("y"))
    point["x"], point["y"] = x, y
    return {"op": "move_point", "trace": trace["pcb_trace_id"], "index": i,
            "from": list(before), "to": [x, y]}


def _move_via(elements: list, edit: dict) -> dict:
    via_id = str(edit.get("via"))
    via = next(
        (e for e in elements
         if isinstance(e, dict) and e.get("type") == "pcb_via" and e.get("pcb_via_id") == via_id),
        None,
    )
    if via is None:
        raise RepairError(f"move_via: no pcb_via with id {via_id!r}")
    ox, oy = via.get("x"), via.get("y")
    if not isinstance(ox, (int, float)) or not isinstance(oy, (int, float)):
        raise RepairError(f"move_via: {via_id} has no position")
    x, y = _num(edit, "x"), _num(edit, "y")
    carried: list[dict] = []
    for e in elements:
        if not (isinstance(e, dict) and e.get("type") == "pcb_trace"):
            continue
        for i, point in enumerate(e.get("route") or []):
            px, py = point.get("x"), point.get("y")
            if not isinstance(px, (int, float)) or not isinstance(py, (int, float)):
                continue
            if math.hypot(px - ox, py - oy) > COINCIDENT_MM:
                continue
            if _anchored(point):
                raise RepairError(
                    f"move_via: {via_id} sits on a pad-anchored point of "
                    f"{e.get('pcb_trace_id')} — that is not a via to move"
                )
            carried.append(point)
    if not any(_is_via(p) for p in carried):
        raise RepairError(
            f"move_via: no trace has a via point at {via_id}'s position "
            f"({ox:.4f}, {oy:.4f}) — the IR does not join them, refusing to guess"
        )
    via["x"], via["y"] = x, y
    for point in carried:
        point["x"], point["y"] = x, y
    return {"op": "move_via", "via": via_id, "from": [ox, oy], "to": [x, y],
            "carriedPoints": len(carried)}


def _insert_point(elements: list, edit: dict) -> dict:
    trace = _trace(elements, str(edit.get("trace")), edit)
    route = trace["route"]
    i = _index(route, "after", edit)
    x, y = _num(edit, "x"), _num(edit, "y")
    # A new vertex lives on the layer of the wire it extends. After a via the
    # copper is on the layer the via lands on — that is the NEXT point's.
    template = route[i + 1] if (_is_via(route[i]) and i + 1 < len(route)) else route[i]
    if _is_via(template):
        raise RepairError(f"insert_point: no wire beside route[{i}] of {trace['pcb_trace_id']} to copy a layer from")
    new_point = {
        "route_type": "wire",
        "x": x, "y": y,
        "layer": template.get("layer"),
        "width": template.get("width"),
    }
    route.insert(i + 1, new_point)
    return {"op": "insert_point", "trace": trace["pcb_trace_id"], "index": i + 1, "to": [x, y]}


def _delete_points(elements: list, edit: dict) -> dict:
    trace = _trace(elements, str(edit.get("trace")), edit)
    route = trace["route"]
    indices = edit.get("indices")
    if not isinstance(indices, list) or not indices:
        raise RepairError("delete_points: 'indices' must be a non-empty list")
    seen: set[int] = set()
    for raw in indices:
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw < len(route):
            raise RepairError(f"delete_points: index {raw!r} is outside the route (0..{len(route) - 1})")
        point = route[raw]
        if _is_via(point):
            raise RepairError(f"delete_points: route[{raw}] of {trace['pcb_trace_id']} is a via — use move_via")
        if _anchored(point):
            raise RepairError(f"delete_points: route[{raw}] of {trace['pcb_trace_id']} is anchored to a pad")
        seen.add(raw)
    if len(route) - len(seen) < 2:
        raise RepairError(f"delete_points: {trace['pcb_trace_id']} would be left with fewer than two points")
    trace["route"] = [p for k, p in enumerate(route) if k not in seen]
    return {"op": "delete_points", "trace": trace["pcb_trace_id"], "indices": sorted(seen)}


COPPER_LAYERS = ("top", "bottom", "inner1", "inner2")


def _layer_changes_without_via(route: list) -> int | None:
    """Index of the first wire→wire step that changes layer with no via
    between, or None when every layer change sits on a via."""
    prev = None
    for i, p in enumerate(route):
        if _is_via(p):
            prev = None
            continue
        layer = p.get("layer")
        if prev is not None and layer != prev:
            return i
        prev = layer
    return None


def _set_layer(elements: list, edit: dict) -> dict:
    trace = _trace(elements, str(edit.get("trace")), edit)
    route = trace["route"]
    layer = str(edit.get("layer") or "")
    if layer not in COPPER_LAYERS:
        raise RepairError(f"set_layer: layer must be one of {', '.join(COPPER_LAYERS)}, got {layer!r}")
    indices = edit.get("indices")
    if not isinstance(indices, list) or not indices:
        raise RepairError("set_layer: 'indices' must be a non-empty list")
    changed = []
    for raw in indices:
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw < len(route):
            raise RepairError(f"set_layer: index {raw!r} is outside the route (0..{len(route) - 1})")
        point = route[raw]
        if _is_via(point):
            raise RepairError(f"set_layer: route[{raw}] of {trace['pcb_trace_id']} is a via")
        if _anchored(point):
            raise RepairError(
                f"set_layer: route[{raw}] of {trace['pcb_trace_id']} is anchored to a pad — "
                f"its layer is the pad's"
            )
        if point.get("layer") != layer:
            point["layer"] = layer
            changed.append(raw)
    return {"op": "set_layer", "trace": trace["pcb_trace_id"], "indices": sorted(changed), "layer": layer}


def _remove_via(elements: list, edit: dict) -> dict:
    via_id = str(edit.get("via"))
    via_index = next(
        (k for k, e in enumerate(elements)
         if isinstance(e, dict) and e.get("type") == "pcb_via" and e.get("pcb_via_id") == via_id),
        None,
    )
    if via_index is None:
        raise RepairError(f"remove_via: no pcb_via with id {via_id!r}")
    via = elements[via_index]
    ox, oy = via.get("x"), via.get("y")
    if not isinstance(ox, (int, float)) or not isinstance(oy, (int, float)):
        raise RepairError(f"remove_via: {via_id} has no position")
    hosts: list[tuple[dict, int]] = []
    for e in elements:
        if not (isinstance(e, dict) and e.get("type") == "pcb_trace"):
            continue
        for i, point in enumerate(e.get("route") or []):
            if _is_via(point) and isinstance(point.get("x"), (int, float)) \
                    and math.hypot(point["x"] - ox, point["y"] - oy) <= COINCIDENT_MM:
                hosts.append((e, i))
    if not hosts:
        raise RepairError(f"remove_via: no trace has a via point at {via_id}'s position — refusing to guess")
    if len(hosts) > 1:
        raise RepairError(f"remove_via: {via_id} is shared by {len(hosts)} traces — not a via this pass removes")
    trace, i = hosts[0]
    route = trace["route"]
    before = route[i - 1] if i > 0 else None
    after = route[i + 1] if i + 1 < len(route) else None
    if before is None or after is None or _is_via(before) or _is_via(after):
        raise RepairError(f"remove_via: {via_id} is not between two wires of {trace['pcb_trace_id']}")
    if before.get("layer") != after.get("layer"):
        raise RepairError(
            f"remove_via: the wires either side of {via_id} are on {before.get('layer')} and "
            f"{after.get('layer')} — set_layer one side first"
        )
    new_route = route[:i] + route[i + 1:]
    # The two wire ends that met on the via now coincide on one layer; keep one.
    if math.hypot(before["x"] - after["x"], before["y"] - after["y"]) <= COINCIDENT_MM:
        keep = before if _anchored(before) or not _anchored(after) else after
        new_route = [p for p in new_route if p is not (after if keep is before else before)]
    bad = _layer_changes_without_via(new_route)
    if bad is not None:
        raise RepairError(
            f"remove_via: {trace['pcb_trace_id']} would change layer at route[{bad}] with no via — "
            f"set_layer the whole run first"
        )
    trace["route"] = new_route
    del elements[via_index]
    return {"op": "remove_via", "via": via_id, "trace": trace["pcb_trace_id"], "at": [ox, oy]}


# --- reroute: local A* for one net segment ---------------------------------

GRID_MM = 0.1
DEFAULT_CLEARANCE_MM = 0.15
DEFAULT_MARGIN_MM = 6.0
MAX_CELLS = 250_000


def _astar_path(start, goal, blocked, cols, rows):
    """8-connected A* on a cell grid; `blocked(c, r)` is the obstacle test.
    Diagonal moves are refused when either orthogonal neighbour is blocked
    (no corner cutting through a pad's corner). Returns cell list or None."""
    import heapq
    (sc, sr), (gc, gr) = start, goal
    if blocked(sc, sr) or blocked(gc, gr):
        return None
    def h(c, r):
        dx, dy = abs(c - gc), abs(r - gr)
        return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)
    open_heap = [(h(sc, sr), 0.0, (sc, sr))]
    g = {(sc, sr): 0.0}
    came: dict = {}
    closed = set()
    steps = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2)))
    while open_heap:
        _, gcur, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        if cur == (gc, gr):
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            return path[::-1]
        closed.add(cur)
        c, r = cur
        for dc, dr, w in steps:
            nc, nr = c + dc, r + dr
            if not (0 <= nc < cols and 0 <= nr < rows) or (nc, nr) in closed or blocked(nc, nr):
                continue
            if dc and dr and (blocked(c + dc, r) or blocked(c, r + dr)):
                continue
            ng = gcur + w
            if ng < g.get((nc, nr), math.inf):
                g[(nc, nr)] = ng
                came[(nc, nr)] = cur
                heapq.heappush(open_heap, (ng + h(nc, nr), ng, (nc, nr)))
    return None


def _simplify(cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop interior cells that lie on a straight run (same step vector)."""
    if len(cells) <= 2:
        return cells
    out = [cells[0]]
    for prev, cur, nxt in zip(cells, cells[1:], cells[2:]):
        if (cur[0] - prev[0], cur[1] - prev[1]) != (nxt[0] - cur[0], nxt[1] - cur[1]):
            out.append(cur)
    out.append(cells[-1])
    return out


def _reroute(elements: list, edit: dict) -> dict:
    from . import diffpair, trace_clearance as tc
    trace = _trace(elements, str(edit.get("trace")), edit)
    route = trace["route"]
    i = _index(route, "from_index", edit)
    j = _index(route, "to_index", edit)
    if j <= i + 0:
        raise RepairError("reroute: to_index must be greater than from_index")
    a, b = route[i], route[j]
    if _is_via(a) or _is_via(b):
        raise RepairError("reroute: both ends must be wire vertices (a via is placement)")
    if a.get("layer") != b.get("layer"):
        raise RepairError(f"reroute: the ends are on {a.get('layer')} and {b.get('layer')} — one layer per reroute")
    if any(_is_via(p) for p in route[i + 1:j]):
        raise RepairError("reroute: a via sits between the ends — remove_via or pick ends on one layer")
    layer = str(a.get("layer") or "")
    width = float(a.get("width") or b.get("width") or 0.127)
    half = width / 2
    clearance = float(edit.get("clearance") or DEFAULT_CLEARANCE_MM)
    margin = float(edit.get("margin") or DEFAULT_MARGIN_MM)
    tid = trace["pcb_trace_id"]

    board = diffpair._Board(elements)
    net = board.trace_net_key(trace)
    net_of_trace = {
        str(t.get("pcb_trace_id") or ""): board.trace_net_key(t)
        for t in elements if isinstance(t, dict) and t.get("type") == "pcb_trace"
    }
    obstacles = [
        ob for ob in tc._pad_obstacles(board, layer, net_of_trace) + tc._trace_capsules(board, layer)
        if tc._foreign(ob, net, tid)
    ]
    # the segment being replaced is not an obstacle to itself
    index = tc._Index(obstacles)

    ax, ay, bx, by = float(a["x"]), float(a["y"]), float(b["x"]), float(b["y"])
    x0, y0 = min(ax, bx) - margin, min(ay, by) - margin
    x1, y1 = max(ax, bx) + margin, max(ay, by) + margin
    pb = board.board or {}
    if isinstance(pb.get("width"), (int, float)) and isinstance(pb.get("height"), (int, float)):
        cx = float((pb.get("center") or {}).get("x", 0.0)); cy = float((pb.get("center") or {}).get("y", 0.0))
        hw, hh = float(pb["width"]) / 2 - half - clearance, float(pb["height"]) / 2 - half - clearance
        x0, x1 = max(x0, cx - hw), min(x1, cx + hw)
        y0, y1 = max(y0, cy - hh), min(y1, cy + hh)
    cols = int((x1 - x0) / GRID_MM) + 1
    rows = int((y1 - y0) / GRID_MM) + 1
    if cols * rows > MAX_CELLS:
        raise RepairError(f"reroute: search window {cols}×{rows} cells is too large — lower margin")
    keep = clearance + half
    cache: dict[tuple[int, int], bool] = {}

    def blocked(c: int, r: int) -> bool:
        k = (c, r)
        if k in cache:
            return cache[k]
        x, y = x0 + c * GRID_MM, y0 + r * GRID_MM
        seg = (x, y, x, y)
        hit = any(ob.distance(seg) < keep for ob in index.near(seg, keep))
        cache[k] = hit
        return hit

    def cell(x: float, y: float) -> tuple[int, int]:
        return (int(round((x - x0) / GRID_MM)), int(round((y - y0) / GRID_MM)))

    # the endpoints sit on same-net copper (or the old path) and must not
    # count as blocked by the obstacle test's rounding
    sc, gc = cell(ax, ay), cell(bx, by)
    cache[sc] = False
    cache[gc] = False
    cells = _astar_path(sc, gc, blocked, cols, rows)
    if cells is None:
        raise RepairError(
            f"reroute: no path on {layer} from route[{i}] to route[{j}] with {clearance}mm clearance "
            f"inside a {2 * margin:.0f}mm window — the corridor is closed; move a via or a part"
        )
    pts = _simplify(cells)
    new_points = [
        {"route_type": "wire", "x": round(x0 + c * GRID_MM, 4), "y": round(y0 + r * GRID_MM, 4),
         "layer": layer, "width": width}
        for c, r in pts[1:-1]
    ]
    before = route[i + 1:j]
    def _len(seq):
        return sum(math.hypot(q["x"] - p["x"], q["y"] - p["y"]) for p, q in zip(seq, seq[1:]))
    old_len = _len([a, *before, b])
    new_len = _len([a, *new_points, b])
    trace["route"] = route[:i + 1] + new_points + route[j:]
    return {"op": "reroute", "trace": tid, "from_index": i, "to_index": j, "layer": layer,
            "pointsBefore": len(before), "pointsAfter": len(new_points),
            "lengthBeforeMm": round(old_len, 3), "lengthAfterMm": round(new_len, 3)}


_APPLY = {
    "move_point": _move_point,
    "move_via": _move_via,
    "insert_point": _insert_point,
    "delete_points": _delete_points,
    "set_layer": _set_layer,
    "remove_via": _remove_via,
    "reroute": _reroute,
}


#: Where `--edits` parks the IR it is about to change. Under `.circuit/`, which
#: the artifact watcher ignores, so taking a checkpoint never looks like the
#: board changed. The newest `KEEP_CHECKPOINTS` survive.
CHECKPOINT_DIR = (".circuit", "repair-undo")
KEEP_CHECKPOINTS = 10


def _checkpoint_root(circuit_json_path: Path) -> Path:
    # boards/<stem>.circuit.json → <project>/.circuit/repair-undo
    return circuit_json_path.resolve().parent.parent.joinpath(*CHECKPOINT_DIR)


def checkpoint(circuit_json_path: Path | str) -> Path | None:
    """Copy the routed IR (and its sidecar, if any) aside before an edit round."""
    path = Path(circuit_json_path)
    if not path.is_file():
        return None
    import shutil
    import time
    root = _checkpoint_root(path)
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    dest = root / stamp
    n = 0
    while dest.exists():  # two rounds inside one millisecond still get two checkpoints
        n += 1
        dest = root / f"{stamp}-{n}"
    dest.mkdir()
    shutil.copy2(path, dest / path.name)
    sidecar = path.with_name(path.name.replace(".circuit.json", ".board.json"))
    if sidecar.is_file():
        shutil.copy2(sidecar, dest / sidecar.name)
    for old in sorted(p for p in root.iterdir() if p.is_dir())[:-KEEP_CHECKPOINTS]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def undo_last_checkpoint(circuit_json_path: Path | str) -> str | None:
    """Restore the newest checkpoint's IR over the current one and consume it.
    Returns the checkpoint name, or None when there is none."""
    path = Path(circuit_json_path)
    root = _checkpoint_root(path)
    if not root.is_dir():
        return None
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / path.name).is_file())
    if not dirs:
        return None
    import shutil
    latest = dirs[-1]
    shutil.copy2(latest / path.name, path)
    shutil.rmtree(latest, ignore_errors=True)
    return latest.name


def apply_edits(circuit_json_path: Path | str, edits: list[dict]) -> list[dict]:
    """Apply ``edits`` to the routed IR at ``circuit_json_path`` and write it back.

    All or nothing: the first refused edit raises :class:`RepairError` and the
    file is untouched. Returns one summary per edit, in order, for the sidecar.
    """
    path = Path(circuit_json_path)
    if not isinstance(edits, list):
        raise RepairError("edits must be a JSON list")
    if len(edits) > MAX_EDITS:
        raise RepairError(f"{len(edits)} edits is a re-route, not a repair (limit {MAX_EDITS})")
    try:
        elements = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RepairError(f"cannot read {path}: {exc}") from exc
    if not isinstance(elements, list):
        raise RepairError(f"{path} is not an element array")
    applied: list[dict] = []
    for n, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise RepairError(f"edit #{n} is not an object")
        op = str(edit.get("op") or "")
        if op not in _APPLY:
            raise RepairError(f"edit #{n}: unknown op {op!r} (one of {', '.join(OPS)})")
        applied.append(_APPLY[op](elements, edit))
    path.write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")
    return applied


def apply_edits_file(circuit_json_path: Path | str, edits_path: Path | str) -> list[dict]:
    try:
        edits: Any = json.loads(Path(edits_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RepairError(f"cannot read edits {edits_path}: {exc}") from exc
    if isinstance(edits, dict) and isinstance(edits.get("edits"), list):
        edits = edits["edits"]
    return apply_edits(circuit_json_path, edits)
