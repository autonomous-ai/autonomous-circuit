"""circuit.json in, circuit.json out.

Two directions, and both have to be exact or the benchmark is measuring a
translation bug:

* :func:`problem_from_circuit_json` — read a *placed* board and hand back a
  :class:`~routerlib.model.RoutingProblem`. Optionally strip the routes that
  are already there, which is how a solved board becomes an instance.
* :func:`solution_to_elements` / :func:`apply_solution` — write the copper back
  as ``pcb_trace`` and ``pcb_via`` elements the existing pipeline consumes
  unchanged. No new element types, no new fields the exporter has to learn.

**Net identity is tscircuit's ``subcircuit_connectivity_map_key``**, not a name
we invent. Two pads carry the same key exactly when they are the same net, and
that is the same key ``circuitpy.checks`` uses, so a net in a problem and a net
in a DRC finding are the same object.

Nothing here infers a package from geometry, and nothing here guesses a net
class from a pad. Names come from ``source_net``, flags come from
``is_power`` / ``is_ground``, and a differential pair is only a pair when
swapping the polarity token in one net's name yields **another net that
actually exists**.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable, Sequence

from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    DesignRules,
    Drill,
    Keepout,
    Net,
    NetClass,
    Pad,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
    q,
)

#: Reused from the pipeline so a rail is a rail in both places.
try:  # pragma: no cover - exercised whenever circuitpy is importable
    from circuitpy.checks import _GROUND_NET_RE, _POWER_NET_RE
except ImportError:  # pragma: no cover
    _POWER_NET_RE = re.compile(
        r"^(v?bus|v_?bus|vusb|\+?5v|v_?5v?|\+?3v?3|v_?3_?3|3_?3v|\+?1v?8|v_?1_?8"
        r"|vcc|vdd|vin|vsys|vbat)$",
        re.I,
    )
    _GROUND_NET_RE = re.compile(r"^(gnd|agnd|dgnd|vss|ground)$", re.I)

#: Polarity tokens. A pair is confirmed only when the swap names a net that is
#: really in the netlist, so "LEDPWR" cannot accidentally pair with anything.
_POLARITY_SUBS: tuple[tuple[str, str], ...] = (
    ("DP", "DM"),
    ("D+", "D-"),
    ("_P_", "_N_"),
    ("_P", "_N"),
    ("+", "-"),
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _elements(circuit_json: Sequence[dict], etype: str) -> list[dict]:
    return [
        e for e in circuit_json if isinstance(e, dict) and e.get("type") == etype
    ]


def _num(value, default: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _polygon_points(points) -> tuple[Point, ...]:
    """A polygon pad's own outline, in order, as far as it is well-formed."""
    return tuple(
        Point(float(p["x"]), float(p["y"]))
        for p in (points or [])
        if isinstance(p, dict)
        and isinstance(p.get("x"), (int, float))
        and isinstance(p.get("y"), (int, float))
    )


def _corner_radius(
    shape: str, radius: float | None, width: float | None, height: float | None
) -> float:
    """``pcb_smtpad.radius`` as a corner radius, clamped to what fits.

    A ``pill`` states a radius of exactly half its short side and is therefore
    a stadium; anything smaller is a rounded rectangle, and the shape model
    covers both from this one number.
    """
    if radius is None or radius <= 0 or not width or not height:
        return 0.0
    return min(float(radius), min(float(width), float(height)) / 2.0)


def _polygon_box(points) -> tuple[float, float, float, float] | None:
    """``(cx, cy, width, height)`` of a polygon pad's bounding box.

    The box is no longer the pad's *model* — :func:`routerlib.geometry.pad_capsule`
    reads the vertices — but a bounding centre and size are what the feature
    measurements, the placement hash and every display expect a pad to have.
    """
    verts = _polygon_points(points)
    if len(verts) < 3:
        return None
    xs = [p.x for p in verts]
    ys = [p.y for p in verts]
    return (
        (min(xs) + max(xs)) / 2.0,
        (min(ys) + max(ys)) / 2.0,
        max(max(xs) - min(xs), 1e-6),
        max(max(ys) - min(ys), 1e-6),
    )


def _connectivity(circuit_json: Sequence[dict]) -> tuple[dict, dict, dict]:
    """``(pcb_port_id -> net key, source_net_id -> net key, net key -> source_net)``."""
    port_to_key: dict[str, str] = {}
    source_port_key: dict[str, str] = {}
    source_net_key: dict[str, str] = {}
    net_meta: dict[str, dict] = {}
    for element in circuit_json:
        if not isinstance(element, dict):
            continue
        key = element.get("subcircuit_connectivity_map_key")
        if not isinstance(key, str) or not key:
            continue
        if element.get("type") == "source_port":
            sid = element.get("source_port_id")
            if isinstance(sid, str):
                source_port_key[sid] = key
        elif element.get("type") == "source_net":
            nid = element.get("source_net_id")
            if isinstance(nid, str):
                source_net_key[nid] = key
                net_meta[key] = element
    for element in _elements(circuit_json, "pcb_port"):
        key = source_port_key.get(str(element.get("source_port_id") or ""))
        pid = element.get("pcb_port_id")
        if key and isinstance(pid, str):
            port_to_key[pid] = key
    return port_to_key, source_net_key, net_meta


def _component_names(circuit_json: Sequence[dict]) -> dict[str, str]:
    """``pcb_component_id -> refdes``."""
    source_names = {
        str(e.get("source_component_id")): str(e.get("name") or "")
        for e in _elements(circuit_json, "source_component")
    }
    out: dict[str, str] = {}
    for element in _elements(circuit_json, "pcb_component"):
        cid = str(element.get("pcb_component_id") or "")
        out[cid] = source_names.get(str(element.get("source_component_id")), cid)
    return out


def classify_nets(
    names: dict[str, str], meta: dict[str, dict]
) -> tuple[dict[str, NetClass], dict[str, str]]:
    """``(net key -> class, net key -> diff partner key)``.

    Ground and power come from the compiler's own ``is_ground`` / ``is_power``
    flags first and the shared rail-name table second. A differential pair is
    established by the swap test described in the module docstring, and it wins
    over ``signal`` but never over a rail — a net that is both a rail and half a
    pair is a netlist bug, not a routing constraint.
    """
    classes: dict[str, NetClass] = {}
    for key, name in names.items():
        info = meta.get(key) or {}
        if info.get("is_ground") or _GROUND_NET_RE.match(name or ""):
            classes[key] = "ground"
        elif info.get("is_power") or _POWER_NET_RE.match(name or ""):
            classes[key] = "power"
        else:
            classes[key] = "signal"

    by_name: dict[str, str] = {}
    for key, name in sorted(names.items()):
        if name:
            by_name.setdefault(name.upper(), key)

    partners: dict[str, str] = {}
    for key, name in sorted(names.items()):
        if not name or classes[key] in ("power", "ground"):
            continue
        upper = name.upper()
        for pos_token, neg_token in _POLARITY_SUBS:
            for a, b in ((pos_token, neg_token), (neg_token, pos_token)):
                if a not in upper:
                    continue
                swapped = upper.replace(a, b, 1)
                other = by_name.get(swapped)
                if other and other != key and classes.get(other) == "signal":
                    partners[key] = other
                    break
            if key in partners:
                break
    # Only keep symmetric pairs: A says B and B says A.
    confirmed = {
        k: v for k, v in partners.items() if partners.get(v) == k
    }
    for key in confirmed:
        classes[key] = "diff_pair"
    return classes, confirmed


def _pads_and_drills(
    circuit_json: Sequence[dict],
    port_to_key: dict[str, str],
    comp_names: dict[str, str],
) -> tuple[list[Pad], list[Drill]]:
    pads: list[Pad] = []
    drills: list[Drill] = []

    for element in _elements(circuit_json, "pcb_smtpad"):
        x, y = _num(element.get("x")), _num(element.get("y"))
        radius = _num(element.get("radius"))
        width = _num(element.get("width"), radius * 2 if radius else None)
        height = _num(element.get("height"), radius * 2 if radius else width)
        rotation = _num(element.get("ccw_rotation"), 0.0) or 0.0
        shape = str(element.get("shape") or "rect")
        vertices: tuple[Point, ...] = ()
        if shape == "polygon":
            # A polygon pad carries no centre and no size — only its outline.
            # Dropping it (what a missing x/y used to do) would hand the router
            # a USB-C shell tab as free space; keeping only its bounding box
            # (what this did until 2026-08-16) hands it a shape that is neither
            # the pad nor a bound on it. The vertices are right here.
            vertices = _polygon_points(element.get("points"))
            box = _polygon_box(element.get("points"))
            if box is None:
                continue
            x, y, width, height = box
            rotation = 0.0
        if x is None or y is None or width is None or height is None:
            continue
        pad_id = str(element.get("pcb_smtpad_id") or "")
        port_id = element.get("pcb_port_id")
        layer = str(element.get("layer") or TOP)
        pads.append(
            Pad(
                id=pad_id,
                net=port_to_key.get(str(port_id or "")),
                center=Point(x, y),
                width_mm=width,
                height_mm=height,
                layers=(layer,),
                kind="smd",
                shape=shape,
                component=comp_names.get(str(element.get("pcb_component_id") or ""), ""),
                port_id=str(port_id) if isinstance(port_id, str) else None,
                rotation_deg=rotation,
                vertices=vertices,
                corner_radius_mm=_corner_radius(shape, radius, width, height),
            )
        )

    for element in _elements(circuit_json, "pcb_plated_hole"):
        x, y = _num(element.get("x")), _num(element.get("y"))
        if x is None or y is None:
            continue
        outer_d = _num(element.get("outer_diameter"))
        outer_w = _num(element.get("outer_width"), outer_d)
        outer_h = _num(element.get("outer_height"), outer_d or outer_w)
        hole_d = _num(element.get("hole_diameter"))
        hole_w = _num(element.get("hole_width"), hole_d)
        hole_h = _num(element.get("hole_height"), hole_d or hole_w)
        if None in (outer_w, outer_h, hole_w, hole_h):
            continue
        pad_id = str(element.get("pcb_plated_hole_id") or "")
        port_id = element.get("pcb_port_id")
        net = port_to_key.get(str(port_id or ""))
        rotation = _num(element.get("ccw_rotation"), 0.0) or 0.0
        drill_id = f"drill_{pad_id}"
        shape = str(element.get("shape") or "circle")
        # A plated hole's copper is a ring: ``pill`` and ``circle`` are both
        # exactly stadiums, and a ``rect`` pad on a hole is a real rectangle.
        hole_shape = str(element.get("hole_shape") or "").lower() or (
            "pill" if shape in ("pill", "circle", "oval") else shape
        )
        pads.append(
            Pad(
                id=pad_id,
                net=net,
                center=Point(x, y),
                width_mm=outer_w,
                height_mm=outer_h,
                layers=tuple(element.get("layers") or (TOP, BOTTOM)),
                kind="plated_hole",
                shape=shape,
                component=comp_names.get(str(element.get("pcb_component_id") or ""), ""),
                port_id=str(port_id) if isinstance(port_id, str) else None,
                drill_id=drill_id,
                rotation_deg=rotation,
                corner_radius_mm=_corner_radius(
                    shape, _num(element.get("radius")), outer_w, outer_h
                ),
            )
        )
        drills.append(
            Drill(
                id=drill_id,
                center=Point(x, y),
                width_mm=hole_w,
                height_mm=hole_h,
                plated=True,
                net=net,
                component=comp_names.get(str(element.get("pcb_component_id") or ""), ""),
                pad_id=pad_id,
                rotation_deg=rotation,
                shape=hole_shape,
            )
        )

    for element in _elements(circuit_json, "pcb_hole"):
        x, y = _num(element.get("x")), _num(element.get("y"))
        hole_d = _num(element.get("hole_diameter"))
        hole_w = _num(element.get("hole_width"), hole_d)
        hole_h = _num(element.get("hole_height"), hole_d or hole_w)
        if x is None or y is None or hole_w is None or hole_h is None:
            continue
        drills.append(
            Drill(
                id=str(element.get("pcb_hole_id") or ""),
                center=Point(x, y),
                width_mm=hole_w,
                height_mm=hole_h,
                plated=False,
                net=None,
                component=comp_names.get(str(element.get("pcb_component_id") or ""), ""),
                shape=str(element.get("hole_shape") or "circle"),
            )
        )
    return pads, drills


def _keepouts(circuit_json: Sequence[dict]) -> list[Keepout]:
    out: list[Keepout] = []
    for element in _elements(circuit_json, "pcb_keepout"):
        center = element.get("center") or {}
        x, y = _num(center.get("x")), _num(center.get("y"))
        if x is None or y is None:
            continue
        radius = _num(element.get("radius"))
        width = _num(element.get("width"), radius * 2 if radius else None)
        height = _num(element.get("height"), radius * 2 if radius else width)
        if width is None or height is None:
            continue
        out.append(
            Keepout(
                id=str(element.get("pcb_keepout_id") or ""),
                center=Point(x, y),
                width_mm=width,
                height_mm=height,
                layers=tuple(element.get("layers") or (TOP, BOTTOM)),
                shape=str(element.get("shape") or "rect"),
                rotation_deg=_num(element.get("ccw_rotation"), 0.0) or 0.0,
                vertices=_polygon_points(element.get("points")),
            )
        )
    return out


def _planes(
    circuit_json: Sequence[dict], source_net_key: dict[str, str]
) -> list[Plane]:
    out: list[Plane] = []
    for element in _elements(circuit_json, "pcb_copper_pour"):
        brep = element.get("brep_shape") or {}
        outer = (brep.get("outer_ring") or {}).get("vertices") or []
        if len(outer) < 3:
            continue
        key = source_net_key.get(str(element.get("source_net_id") or ""))
        if not key:
            continue
        holes = tuple(
            tuple(Point(float(v["x"]), float(v["y"])) for v in (ring.get("vertices") or []))
            for ring in (brep.get("inner_rings") or [])
            if len(ring.get("vertices") or []) >= 3
        )
        out.append(
            Plane(
                id=str(element.get("pcb_copper_pour_id") or ""),
                net=key,
                layer=str(element.get("layer") or BOTTOM),
                outline=tuple(Point(float(v["x"]), float(v["y"])) for v in outer),
                holes=holes,
            )
        )
    return out


def _existing_copper(
    circuit_json: Sequence[dict],
    port_to_key: dict[str, str],
    source_net_key: dict[str, str],
) -> tuple[list[Trace], list[Via]]:
    traces: list[Trace] = []
    vias: list[Via] = []
    for element in _elements(circuit_json, "pcb_trace"):
        net = source_net_key.get(str(element.get("connection_name") or ""))
        if net is None:
            for pid in element.get("connectsTo") or []:
                net = port_to_key.get(str(pid))
                if net:
                    break
        trace_id = str(element.get("pcb_trace_id") or "")
        run: list[Point] = []
        layer = TOP
        width = 0.2
        index = 0
        for point in element.get("route") or []:
            if not isinstance(point, dict):
                continue
            if point.get("route_type") == "via":
                if len(run) >= 2:
                    traces.append(
                        Trace(f"{trace_id}#{index}", net or "", layer, tuple(run), width)
                    )
                    index += 1
                run = []
                continue
            x, y = _num(point.get("x")), _num(point.get("y"))
            if x is None or y is None:
                continue
            point_layer = str(point.get("layer") or layer)
            if run and point_layer != layer:
                if len(run) >= 2:
                    traces.append(
                        Trace(f"{trace_id}#{index}", net or "", layer, tuple(run), width)
                    )
                    index += 1
                run = []
            layer = point_layer
            # A route may change width part-way along — a 0.5mm rail narrowing
            # into a 0.15mm pad entry is one ``pcb_trace`` carrying two widths.
            # A ``Trace`` holds exactly one, so the run is cut where the width
            # changes. Carrying the last point's width across the whole run
            # instead *widened copper nobody had touched*: re-reading
            # hydrate-coaster's own 113 traces scored 0 harness errors while
            # every point was 0.15mm and 130 the day the boards gained per-net
            # rail widths, 42 of them shorts that are not on the board.
            #
            # The segment that spans the change is claimed by whichever side is
            # wider, so copper is over-reported at the seam and never
            # under-reported. A clearance check that has to err must err that
            # way.
            point_width = _num(point.get("width"), width) or width
            if run and point_width != width:
                if point_width < width:
                    run.append(Point(x, y))
                    if len(run) >= 2:
                        traces.append(
                            Trace(f"{trace_id}#{index}", net or "", layer,
                                  tuple(run), width)
                        )
                        index += 1
                    run = [Point(x, y)]
                    width = point_width
                    continue
                if len(run) >= 2:
                    traces.append(
                        Trace(f"{trace_id}#{index}", net or "", layer, tuple(run), width)
                    )
                    index += 1
                run = [run[-1]]
            width = point_width
            run.append(Point(x, y))
        if len(run) >= 2:
            traces.append(Trace(f"{trace_id}#{index}", net or "", layer, tuple(run), width))

    for element in _elements(circuit_json, "pcb_via"):
        x, y = _num(element.get("x")), _num(element.get("y"))
        if x is None or y is None:
            continue
        vias.append(
            Via(
                id=str(element.get("pcb_via_id") or ""),
                net=str(element.get("subcircuit_connectivity_map_key") or ""),
                center=Point(x, y),
                drill_mm=_num(element.get("hole_diameter"), 0.3) or 0.3,
                pad_mm=_num(element.get("outer_diameter"), 0.6) or 0.6,
            )
        )
    return traces, vias


def problem_from_circuit_json(
    circuit_json: Sequence[dict],
    *,
    problem_id: str,
    rules: DesignRules | None = None,
    strip_routes: bool = True,
    strip_planes: bool = True,
) -> RoutingProblem:
    """Build a routing problem from a built board.

    ``strip_routes`` removes the copper the old router placed — that is how a
    solved board becomes an instance nobody has solved.

    ``strip_planes`` removes existing copper pours, and it defaults to **on**
    for a reason worth stating: the pours in our built boards were generated
    *after* routing, so their outlines are carved around the old traces.
    Handing one to a new router leaks the previous solution into the problem.
    Where a plane belongs in the problem it is synthesised clean (see
    :func:`routerlib.bench.with_ground_plane`), not inherited.
    """
    rules = rules or DesignRules.jlcpcb()
    board_el = next(iter(_elements(circuit_json, "pcb_board")), None)
    if board_el is None:
        raise ValueError("circuit.json has no pcb_board element")
    center = board_el.get("center") or {}
    outline = tuple(
        Point(float(p["x"]), float(p["y"]))
        for p in (board_el.get("outline") or [])
        if isinstance(p, dict) and "x" in p and "y" in p
    )
    layer_count = int(board_el.get("num_layers") or 2)
    board = Board(
        width_mm=float(board_el.get("width") or 0.0),
        height_mm=float(board_el.get("height") or 0.0),
        center=Point(float(center.get("x") or 0.0), float(center.get("y") or 0.0)),
        outline=outline,
        layer_count=layer_count,
        thickness_mm=float(board_el.get("thickness") or 1.6),
    )

    port_to_key, source_net_key, net_meta = _connectivity(circuit_json)
    comp_names = _component_names(circuit_json)
    pads, drills = _pads_and_drills(circuit_json, port_to_key, comp_names)
    keepouts = _keepouts(circuit_json)
    planes = [] if strip_planes else _planes(circuit_json, source_net_key)

    existing_traces: list[Trace] = []
    existing_vias: list[Via] = []
    if not strip_routes:
        existing_traces, existing_vias = _existing_copper(
            circuit_json, port_to_key, source_net_key
        )

    # Nets: every connectivity key that at least one pad carries.
    key_to_pads: dict[str, list[str]] = {}
    for pad in pads:
        if pad.net:
            key_to_pads.setdefault(pad.net, []).append(pad.id)
    key_to_source: dict[str, str] = {v: k for k, v in source_net_key.items()}
    names = {
        key: str((net_meta.get(key) or {}).get("name") or key.split("_")[-1])
        for key in key_to_pads
    }
    classes, partners = classify_nets(names, net_meta)

    nets = [
        Net(
            id=key,
            name=names[key],
            net_class=classes[key],
            pads=tuple(sorted(pad_ids)),
            min_width_mm=rules.width_for(classes[key]),
            source_net_id=key_to_source.get(key),
            diff_partner=partners.get(key),
            priority=_priority(classes[key]),
        )
        for key, pad_ids in sorted(key_to_pads.items())
    ]

    return RoutingProblem(
        id=problem_id,
        board=board,
        rules=rules,
        pads=tuple(pads),
        drills=tuple(drills),
        keepouts=tuple(keepouts),
        planes=tuple(planes),
        nets=tuple(nets),
        existing_traces=tuple(existing_traces),
        existing_vias=tuple(existing_vias),
        meta={"strippedRoutes": strip_routes, "strippedPlanes": strip_planes},
    )


#: Route order when an algorithm has no better idea: rails and pairs first,
#: because they are the ones with a width or a symmetry constraint and the
#: cheapest region to find is the empty one.
_PRIORITY = {"ground": 10, "power": 20, "diff_pair": 30, "signal": 100}


def _priority(net_class: NetClass) -> int:
    return _PRIORITY.get(net_class, 100)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def solution_to_elements(
    problem: RoutingProblem,
    solution: RoutingSolution,
    *,
    source_trace_for: dict[str, str] | None = None,
) -> list[dict]:
    """The copper as ``pcb_trace`` + ``pcb_via`` elements.

    One element per :class:`~routerlib.model.Trace` (single layer, wire points
    only) and one per :class:`~routerlib.model.Via`. tscircuit's own router
    also inlines a ``route_type: "via"`` marker inside the trace it belongs to;
    we do not, because our traces are per-layer by construction and a standalone
    ``pcb_via`` is what both the DFM gate and the KiCad converter actually read.

    Ids are derived from the net, never minted at random, so two runs of the
    same router produce byte-identical output.

    ``source_trace_for`` maps a net id to the ``source_trace_id`` the emitted
    ``pcb_trace`` should claim. It matters more than it looks:
    ``checks.power_width_warnings`` resolves a track's net *through the source
    trace*, so a track with no ``source_trace_id`` is invisible to the rail
    check and a power net routed at signal width would score clean. Callers
    that have the real source graph pass the real ids; the scorer passes
    synthetic ones it also emits.
    """
    nets = problem.nets_by_id
    pads = problem.pads_by_id
    out: list[dict] = []

    per_net: dict[str, int] = {}
    trace_for_net: dict[str, str] = {}
    for trace in sorted(
        solution.traces,
        key=lambda t: (t.net, t.layer, tuple((p.x, p.y) for p in t.points)),
    ):
        net = nets.get(trace.net)
        base = (net.source_net_id if net and net.source_net_id else trace.net) or "net"
        index = per_net.get(base, 0)
        per_net[base] = index + 1
        trace_id = f"{base}_{index}"
        trace_for_net.setdefault(trace.net, trace_id)
        # The fallback id matches the one circuit_json_for_scoring mints, so a
        # synthetic problem whose pads never had ports is still net-resolvable
        # by the DFM gate rather than silently unknown-net.
        connects = sorted(
            pads[p].port_id or f"port_{p}"
            for p in (net.pads if net else ())
            if p in pads
        )
        element = {
            "type": "pcb_trace",
            "pcb_trace_id": trace_id,
            "route": [
                {
                    "route_type": "wire",
                    "x": point.x,
                    "y": point.y,
                    "width": trace.width_mm,
                    "layer": trace.layer,
                }
                for point in trace.points
            ],
        }
        if net and net.source_net_id:
            element["connection_name"] = net.source_net_id
        if connects:
            element["connectsTo"] = connects
        if trace.net:
            element["subcircuit_connectivity_map_key"] = trace.net
        if source_trace_for and trace.net in source_trace_for:
            element["source_trace_id"] = source_trace_for[trace.net]
        out.append(element)

    for index, via in enumerate(
        sorted(solution.vias, key=lambda v: (v.net, v.center.x, v.center.y))
    ):
        element = {
            "type": "pcb_via",
            "pcb_via_id": f"routed_via_{index}",
            "x": via.center.x,
            "y": via.center.y,
            "hole_diameter": via.drill_mm,
            "outer_diameter": via.pad_mm,
            "layers": [BOTTOM, TOP],
            "from_layer": via.from_layer,
            "to_layer": via.to_layer,
        }
        if via.net:
            element["subcircuit_connectivity_map_key"] = via.net
            if via.net in trace_for_net:
                element["pcb_trace_id"] = trace_for_net[via.net]
        out.append(element)
    return out


#: Element types that *are* the route. Everything else is input to it.
ROUTE_ELEMENT_TYPES = ("pcb_trace", "pcb_via")


def solution_from_elements(
    problem: RoutingProblem,
    circuit_json: Sequence[dict],
    *,
    router: str = "replay",
    iterations: int = 0,
    nodes_expanded: int = 0,
) -> RoutingSolution:
    """The inverse of :func:`solution_to_elements`: copper on disk, back.

    This is what makes a re-score cheap. A tournament cell costs minutes of
    routing and milliseconds of scoring, so when the ruler changes the copper
    does not have to be produced again — it is already written out, and the new
    ruler can be held against exactly the boards the old one graded.

    ``iterations`` and ``nodes_expanded`` are not recoverable from copper and
    are carried in by the caller from the original run, because the cost tier
    of the score reads them.
    """
    source_net_key = {
        n.source_net_id: n.id for n in problem.nets if n.source_net_id
    }
    port_to_key = {p.port_id: p.net for p in problem.pads if p.port_id and p.net}
    traces, vias = _existing_copper(circuit_json, port_to_key, source_net_key)
    # ``solution_to_elements`` writes the net key straight onto the element;
    # prefer it over the indirection, so a replay cannot silently lose a net.
    direct: dict[str, str] = {}
    for element in _elements(circuit_json, "pcb_trace"):
        key = element.get("subcircuit_connectivity_map_key")
        tid = element.get("pcb_trace_id")
        if isinstance(key, str) and isinstance(tid, str):
            direct[tid] = key
    fixed = tuple(
        replace(trace, net=direct.get(trace.id.split("#")[0], trace.net))
        for trace in traces
    )
    return RoutingSolution(
        router=router,
        traces=fixed,
        vias=tuple(vias),
        complete=False,
        iterations=iterations,
        nodes_expanded=nodes_expanded,
    )


def apply_solution(
    circuit_json: Sequence[dict],
    problem: RoutingProblem,
    solution: RoutingSolution,
) -> list[dict]:
    """A complete circuit.json with the old copper replaced by the new.

    Everything that is not a route is preserved in its original order, so a
    diff between input and output shows the copper and nothing else. The result
    is what the existing pipeline consumes unchanged.
    """
    kept = [
        e
        for e in circuit_json
        if not (isinstance(e, dict) and e.get("type") in ROUTE_ELEMENT_TYPES)
    ]
    # Re-attach each net to a source trace that already exists in this board, so
    # the pipeline's rail-width check can still resolve the track's net.
    source_trace_for: dict[str, str] = {}
    for element in _elements(circuit_json, "source_trace"):
        key = element.get("subcircuit_connectivity_map_key")
        tid = element.get("source_trace_id")
        if isinstance(key, str) and isinstance(tid, str):
            source_trace_for.setdefault(key, tid)
    return kept + solution_to_elements(
        problem, solution, source_trace_for=source_trace_for
    )


def circuit_json_for_scoring(
    problem: RoutingProblem, solution: RoutingSolution
) -> list[dict]:
    """A minimal circuit.json carrying exactly what the DFM gate reads: the
    board, every pad, every hole, and the solution's copper.

    Used by the scorer so legality is judged by ``circuitpy.checks`` rather
    than by anything this package wrote. It is minimal on purpose — the scorer
    must not be able to "pass" a board by omitting an obstacle.
    """
    board = problem.board
    out: list[dict] = [
        {
            "type": "pcb_board",
            "pcb_board_id": "pcb_board_0",
            "center": board.center.as_dict(),
            "width": board.width_mm,
            "height": board.height_mm,
            "thickness": board.thickness_mm,
            "num_layers": board.layer_count,
            "outline": [p.as_dict() for p in board.outline],
        }
    ]
    # Ports + source ports carry the net keys the checks index on.
    for pad in problem.pads:
        port_id = pad.port_id or f"port_{pad.id}"
        source_port_id = f"src_{port_id}"
        out.append(
            {
                "type": "source_port",
                "source_port_id": source_port_id,
                "name": pad.id,
                "subcircuit_connectivity_map_key": pad.net,
            }
        )
        out.append(
            {
                "type": "pcb_port",
                "pcb_port_id": port_id,
                "source_port_id": source_port_id,
                "x": pad.center.x,
                "y": pad.center.y,
            }
        )
        if pad.kind == "smd":
            element = {
                "type": "pcb_smtpad",
                "pcb_smtpad_id": pad.id,
                "pcb_port_id": port_id,
                "layer": pad.layers[0] if pad.layers else TOP,
                "shape": pad.shape,
                "width": pad.width_mm,
                "height": pad.height_mm,
                "x": pad.center.x,
                "y": pad.center.y,
                "ccw_rotation": pad.rotation_deg,
            }
            # Re-emit the shape we read. A polygon pad written back as its
            # bounding rectangle is a different pad, and the check that reads
            # it would be answering about a board we did not measure.
            if pad.vertices:
                element["points"] = [p.as_dict() for p in pad.vertices]
            if pad.corner_radius_mm:
                element["radius"] = pad.corner_radius_mm
            out.append(element)
        else:
            drill = next(
                (d for d in problem.drills if d.pad_id == pad.id), None
            )
            out.append(
                {
                    "type": "pcb_plated_hole",
                    "pcb_plated_hole_id": pad.id,
                    "pcb_port_id": port_id,
                    "shape": pad.shape,
                    "outer_width": pad.width_mm,
                    "outer_height": pad.height_mm,
                    "hole_width": drill.width_mm if drill else pad.width_mm / 2,
                    "hole_height": drill.height_mm if drill else pad.height_mm / 2,
                    "x": pad.center.x,
                    "y": pad.center.y,
                    "layers": list(pad.layers),
                    "ccw_rotation": pad.rotation_deg,
                }
            )
    for drill in problem.drills:
        if drill.pad_id is not None:
            continue
        out.append(
            {
                "type": "pcb_hole",
                "pcb_hole_id": drill.id,
                "hole_shape": (
                    drill.shape
                    if drill.shape not in ("pill", "")
                    else ("circle" if drill.width_mm == drill.height_mm else "oval")
                ),
                "hole_width": drill.width_mm,
                "hole_height": drill.height_mm,
                "hole_diameter": drill.width_mm,
                "x": drill.center.x,
                "y": drill.center.y,
            }
        )
    for keepout in problem.keepouts:
        element = {
            "type": "pcb_keepout",
            "pcb_keepout_id": keepout.id,
            "shape": keepout.shape,
            "center": keepout.center.as_dict(),
            "layers": list(keepout.layers),
        }
        if keepout.shape == "circle":
            element["radius"] = keepout.width_mm / 2.0
        else:
            element["width"] = keepout.width_mm
            element["height"] = keepout.height_mm
        if keepout.rotation_deg:
            element["ccw_rotation"] = keepout.rotation_deg
        if keepout.vertices:
            element["points"] = [p.as_dict() for p in keepout.vertices]
        out.append(element)
    # source_net + source_trace rows so power_width_warnings can see which
    # nets are rails and which track belongs to which.
    source_trace_for: dict[str, str] = {}
    for net in problem.nets:
        source_net_id = net.source_net_id or f"src_{net.id}"
        source_trace_id = f"src_trace_{net.id}"
        source_trace_for[net.id] = source_trace_id
        out.append(
            {
                "type": "source_net",
                "source_net_id": source_net_id,
                "name": net.name,
                "is_power": net.net_class == "power",
                "is_ground": net.net_class == "ground",
                "subcircuit_connectivity_map_key": net.id,
            }
        )
        out.append(
            {
                "type": "source_trace",
                "source_trace_id": source_trace_id,
                "connected_source_net_ids": [source_net_id],
                "connected_source_port_ids": [],
                "subcircuit_connectivity_map_key": net.id,
            }
        )
    # A poured plane carries the rail, so its routed stubs are exempt from the
    # rail-width check. Emitting the pour is what tells the check that.
    net_source = {n.id: (n.source_net_id or f"src_{n.id}") for n in problem.nets}
    for plane in problem.planes:
        out.append(
            {
                "type": "pcb_copper_pour",
                "pcb_copper_pour_id": plane.id,
                "shape": "brep",
                "layer": plane.layer,
                "source_net_id": net_source.get(plane.net, plane.net),
                "brep_shape": {
                    "outer_ring": {"vertices": [p.as_dict() for p in plane.outline]},
                    "inner_rings": [
                        {"vertices": [p.as_dict() for p in ring]}
                        for ring in plane.holes
                    ],
                },
            }
        )
    out.extend(
        solution_to_elements(problem, solution, source_trace_for=source_trace_for)
    )
    return out


__all__ = [
    "ROUTE_ELEMENT_TYPES",
    "solution_from_elements",
    "apply_solution",
    "circuit_json_for_scoring",
    "classify_nets",
    "problem_from_circuit_json",
    "solution_to_elements",
]
