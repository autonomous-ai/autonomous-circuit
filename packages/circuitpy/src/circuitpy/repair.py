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

OPS = ("move_point", "move_via", "insert_point", "delete_points", "set_layer", "remove_via")


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


_APPLY = {
    "move_point": _move_point,
    "move_via": _move_via,
    "insert_point": _insert_point,
    "delete_points": _delete_points,
    "set_layer": _set_layer,
    "remove_via": _remove_via,
}


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
