"""Verify the product's declared physical-layout intent against routed copper.

Electrical rules alone cannot answer product questions such as "the USB socket
is centred on the bottom edge" or "only the key field is assembled on the
front".  Those are explicit design decisions.  ``product.json`` owns them and
this module measures the compiled board against them.

The checker deliberately consumes a plain dictionary rather than importing
``circuitpy``'s product model.  ``packages/verify`` remains an independent
second opinion; the pipeline adapter is responsible for passing the product's
``layout`` member through.
"""

from __future__ import annotations

import fnmatch
import math
from typing import Any

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board, Component, Net, Poly, Rect, Trace


def _patterns(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item]
    return []


def _first_side_rule(name: str, rules: list[dict[str, Any]]) -> str | None:
    """Return the first matching side rule.

    First-match semantics let a product put a specific population on top and
    then use ``"*"`` as the bottom-side default without enumerating every
    resistor and capacitor.
    """

    for rule in rules:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in _patterns(rule.get("match"))):
            side = rule.get("side")
            if side in ("top", "bottom"):
                return str(side)
    return None


@never_raises
def _board_size(board: Board, intent: dict[str, Any]) -> list[Finding]:
    expected = intent.get("boardSizeMm")
    if not (
        isinstance(expected, list)
        and len(expected) == 2
        and all(isinstance(value, (int, float)) and value > 0 for value in expected)
    ):
        return []
    if board.outline is None:
        return [
            finding(
                "board",
                "layout_intent_board_size",
                "product layout declares an exact board size but circuit.json has no rectangular outline",
                "error",
            )
        ]
    tolerance = intent.get("boardSizeToleranceMm", 0.1)
    tolerance = float(tolerance) if isinstance(tolerance, (int, float)) else 0.1
    width, height = float(expected[0]), float(expected[1])
    dw = abs(board.outline.width - width)
    dh = abs(board.outline.height - height)
    if dw <= tolerance and dh <= tolerance:
        return []
    return [
        finding(
            "board",
            "layout_intent_board_size",
            f"compiled outline is {board.outline.width:g}x{board.outline.height:g}mm; "
            f"the approved mechanical size is {width:g}x{height:g}mm +/-{tolerance:g}mm",
            "error",
        )
    ]


@never_raises
def _copper_clearance(board: Board, intent: dict[str, Any]) -> list[Finding]:
    """Require the authoring substrate to route against the product margin.

    The independent KiCad leg receives the same number from circuitpy.  This
    preflight catches the easier and more dangerous failure mode: declaring a
    0.15mm product margin while compiling the authoring board at its 0.10mm
    defaults.  Actual violations at these declared tolerances remain ordinary
    parsed ``*_clearance_error`` elements; the check does not pretend a board
    setting is proof that the router met it.
    """

    required = intent.get("minCopperClearanceMm")
    if not isinstance(required, (int, float)) or isinstance(required, bool):
        return []
    pcb_board = next(iter(board.of_type("pcb_board")), None)
    if not isinstance(pcb_board, dict):
        return [
            finding(
                "board",
                "layout_intent_clearance_contract",
                "product layout declares a copper-clearance margin but circuit.json "
                "has no pcb_board routing tolerances",
                "error",
            )
        ]
    fields = {
        "trace-to-pad": pcb_board.get("min_trace_to_pad_edge_clearance"),
        "via-to-pad": pcb_board.get("min_via_edge_to_pad_edge_clearance"),
    }
    missing = [
        f"{label}={float(value):g}mm" if isinstance(value, (int, float)) else f"{label}=unset"
        for label, value in fields.items()
        if not isinstance(value, (int, float)) or float(value) + 1e-9 < float(required)
    ]
    if not missing:
        return []
    return [
        finding(
            "board",
            "layout_intent_clearance_contract",
            f"product requires at least {float(required):g}mm copper clearance, but "
            f"the authoring board declares {', '.join(missing)}. Set both routing "
            "tolerances before autorouting; parsed clearance errors and KiCad DRC "
            "still prove the resulting geometry",
            "error",
        )
    ]


@never_raises
def _component_sides(board: Board, intent: dict[str, Any]) -> list[Finding]:
    raw_rules = intent.get("componentSides")
    if not isinstance(raw_rules, list):
        return []
    rules = [rule for rule in raw_rules if isinstance(rule, dict)]
    out: list[Finding] = []
    for component in board.placed():
        expected = _first_side_rule(component.name, rules)
        if expected is None or component.layer == expected:
            continue
        out.append(
            finding(
                component.name,
                "layout_intent_component_side",
                f"{component.name} is compiled on {component.layer}; the first matching "
                f"product placement rule requires {expected}",
                "error",
            )
        )
    return out


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _zone_center(shape: dict[str, Any]) -> tuple[float, float] | None:
    center = shape.get("center")
    if not isinstance(center, list) or len(center) != 2:
        return None
    x, y = _finite_number(center[0]), _finite_number(center[1])
    return (x, y) if x is not None and y is not None else None


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_squared))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _poly_radius_span(poly: Poly, center: tuple[float, float]) -> tuple[float, float]:
    """Minimum/maximum radius of the polygon's filled area about ``center``."""

    points = list(poly.points)
    if not points:
        return (math.inf, math.inf)
    maximum = max(math.dist(center, point) for point in points)
    if poly.contains(*center):
        return (0.0, maximum)
    minimum = min(
        _point_segment_distance(center, points[index], points[(index + 1) % len(points)])
        for index in range(len(points))
    )
    return minimum, maximum


def _point_in_zone(point: tuple[float, float], shape: dict[str, Any]) -> bool:
    center = _zone_center(shape)
    kind = shape.get("kind")
    if center is None:
        return False
    epsilon = 1e-9
    if kind == "circle":
        radius = _finite_number(shape.get("radiusMm"))
        return radius is not None and math.dist(point, center) <= radius + epsilon
    if kind == "annulus":
        inner = _finite_number(shape.get("innerRadiusMm"))
        outer = _finite_number(shape.get("outerRadiusMm"))
        distance = math.dist(point, center)
        return (
            inner is not None
            and outer is not None
            and inner - epsilon <= distance <= outer + epsilon
        )
    if kind == "rect":
        width = _finite_number(shape.get("widthMm"))
        height = _finite_number(shape.get("heightMm"))
        return (
            width is not None
            and height is not None
            and abs(point[0] - center[0]) <= width / 2 + epsilon
            and abs(point[1] - center[1]) <= height / 2 + epsilon
        )
    return False


def _poly_in_zone(poly: Poly, shape: dict[str, Any]) -> bool:
    center = _zone_center(shape)
    kind = shape.get("kind")
    if center is None or not poly.points:
        return False
    epsilon = 1e-9
    if kind == "circle":
        radius = _finite_number(shape.get("radiusMm"))
        return radius is not None and all(
            math.dist(point, center) <= radius + epsilon for point in poly.points
        )
    if kind == "annulus":
        inner = _finite_number(shape.get("innerRadiusMm"))
        outer = _finite_number(shape.get("outerRadiusMm"))
        if inner is None or outer is None:
            return False
        minimum, maximum = _poly_radius_span(poly, center)
        return minimum + epsilon >= inner and maximum <= outer + epsilon
    if kind == "rect":
        width = _finite_number(shape.get("widthMm"))
        height = _finite_number(shape.get("heightMm"))
        return (
            width is not None
            and height is not None
            and all(
                abs(point[0] - center[0]) <= width / 2 + epsilon
                and abs(point[1] - center[1]) <= height / 2 + epsilon
                for point in poly.points
            )
        )
    return False


def _zone_label(shape: dict[str, Any]) -> str:
    center = _zone_center(shape)
    center_text = "unknown center" if center is None else f"center {center}mm"
    kind = shape.get("kind")
    if kind == "circle":
        return f"circle at {center_text}, radius {shape.get('radiusMm')!r}mm"
    if kind == "annulus":
        return (
            f"annulus at {center_text}, radii "
            f"{shape.get('innerRadiusMm')!r}..{shape.get('outerRadiusMm')!r}mm"
        )
    if kind == "rect":
        return (
            f"rectangle at {center_text}, "
            f"{shape.get('widthMm')!r}x{shape.get('heightMm')!r}mm"
        )
    return f"unknown shape {kind!r}"


@never_raises
def _component_zones(board: Board, intent: dict[str, Any]) -> list[Finding]:
    raw_rules = intent.get("componentZones")
    if not isinstance(raw_rules, list):
        return []
    placed = board.placed()
    out: list[Finding] = []
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            continue
        patterns = _patterns(rule.get("match"))
        matched = [
            component
            for component in placed
            if any(fnmatch.fnmatchcase(component.name, pattern) for pattern in patterns)
        ]
        if not matched:
            out.append(
                finding(
                    f"componentZones[{index}]",
                    "layout_intent_component_zone_unmatched",
                    f"placement-zone rule {index} patterns {patterns!r} match no populated "
                    "compiled component; the declared zone is not tied to hardware",
                    "error",
                )
            )
            continue
        shape = rule.get("shape")
        if not isinstance(shape, dict):
            continue
        containment = rule.get("containment")
        for component in matched:
            if containment == "center":
                inside = _point_in_zone(component.center, shape)
                measured = f"center {component.center}mm"
            elif containment == "courtyard":
                inside = all(
                    _poly_in_zone(part, shape) for part in component.keepout_parts
                )
                measured = "compiled courtyard/body envelope"
            else:
                continue
            if inside:
                continue
            out.append(
                finding(
                    component.name,
                    "layout_intent_component_zone",
                    f"{component.name} {measured} is outside its required "
                    f"{_zone_label(shape)} ({containment} containment)",
                    "error",
                )
            )
    return out


def _source_pad_rects(board: Board) -> dict[str, Rect]:
    """Join source ports to their emitted copper landings.

    Decoupling distance is the copper path that remains between the IC supply
    pad and the capacitor's rail pad, not component-centre distance.  The
    source/PCB split makes this a two-hop join through ``pcb_port``.
    """

    pcb_port_by_source = {
        str(element.get("source_port_id") or ""): str(
            element.get("pcb_port_id") or ""
        )
        for element in board.of_type("pcb_port")
        if element.get("source_port_id") and element.get("pcb_port_id")
    }
    pads_by_port = {
        pad.port_id: pad.rect
        for component in board.components
        for pad in component.pads
        if pad.port_id
    }
    return {
        source_port_id: pads_by_port[pcb_port_id]
        for source_port_id, pcb_port_id in pcb_port_by_source.items()
        if pcb_port_id in pads_by_port
    }


def _authored_port_graph(board: Board) -> dict[str, set[str]]:
    """Return only explicitly authored port-to-port topology.

    Separate ``pin -> net`` declarations let an MST choose an electrically
    equivalent but physically poor tree.  A local decoupling loop must instead
    be present in the source as a two-port edge (possibly through masked copper
    nodes); the one marked boundary then joins that local tree to the rail.
    """

    graph: dict[str, set[str]] = {}
    for trace in board.of_type("source_trace"):
        ports = {
            str(value)
            for value in (trace.get("connected_source_port_ids") or [])
            if value
        }
        nets = [
            value
            for value in (trace.get("connected_source_net_ids") or [])
            if value
        ]
        if len(ports) != 2 or nets:
            continue
        left, right = tuple(ports)
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)
    return graph


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    pending = [start]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(graph.get(current, ()))
    return seen


@never_raises
def _decoupling(board: Board, intent: dict[str, Any]) -> list[Finding]:
    """Enforce the product's local bypass-loop contract.

    ``requires_power`` is emitted from the component pin definition, so this
    does not guess supply pins from their spelling.  A qualifying capacitor is
    populated, bridges the same rail to GND, has measurable pads, and is joined
    to the supply pin by authored two-port topology.  That last requirement is
    what prevents a correct-looking schematic from delegating the bypass loop
    to a board-wide minimum-spanning tree.
    """

    policy = intent.get("decoupling")
    if not isinstance(policy, dict):
        return []
    maximum = policy.get("maxDistanceMm")
    if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
        return []
    excluded = _patterns(policy.get("exclude"))
    raw_overrides = policy.get("overrides", [])
    override_rules: list[tuple[int, list[str], float]] = []
    invalid_overrides: list[tuple[str, str]] = []
    if not isinstance(raw_overrides, list):
        invalid_overrides.append(
            ("layout.decoupling.overrides", "must be a list of override rules")
        )
        raw_overrides = []
    for index, rule in enumerate(raw_overrides):
        label = f"layout.decoupling.overrides[{index}]"
        if not isinstance(rule, dict):
            invalid_overrides.append((label, "must be an object"))
            continue
        match_value = rule.get("match")
        match_valid = (
            isinstance(match_value, str)
            and bool(match_value)
        ) or (
            isinstance(match_value, list)
            and bool(match_value)
            and all(isinstance(item, str) and item for item in match_value)
        )
        patterns = _patterns(match_value)
        override_maximum = rule.get("maxDistanceMm")
        source = rule.get("source")
        unknown = sorted(set(rule) - {"match", "maxDistanceMm", "source"})
        if (
            not match_valid
            or not isinstance(override_maximum, (int, float))
            or isinstance(override_maximum, bool)
            or not math.isfinite(float(override_maximum))
            or float(override_maximum) <= 0
            or not isinstance(source, str)
            or not source.strip()
            or unknown
        ):
            invalid_overrides.append(
                (
                    label,
                    "requires non-empty match/source, a positive finite "
                    "maxDistanceMm, and no unknown members",
                )
            )
            continue
        override_rules.append((index, patterns, float(override_maximum)))
    all_populated = {
        component.source_id: component
        for component in board.placed()
        if component.ftype == "simple_chip"
    }
    populated = {
        component_id: component
        for component_id, component in all_populated.items()
        if not any(
            fnmatch.fnmatchcase(component.name, pattern) for pattern in excluded
        )
    }
    source_ports = {
        str(element.get("source_port_id") or ""): element
        for element in board.of_type("source_port")
        if element.get("source_port_id")
    }
    pad_rects = _source_pad_rects(board)
    graph = _authored_port_graph(board)
    ground = board.ground

    # One capacitor can legitimately serve adjacent supply pins, but it must
    # be a real populated part and its other terminal must physically belong to
    # GND. Index its rail-side source port by connectivity key.
    caps_by_net: dict[str, list[tuple[str, Component]]] = {}
    for component in board.placed():
        if component.ftype != "simple_capacitor":
            continue
        ports = [
            (port_id, board.net_of_port(port_id))
            for port_id, element in source_ports.items()
            if element.get("source_component_id") == component.source_id
        ]
        if ground is None or not any(
            net is not None and net.key == ground.key for _, net in ports
        ):
            continue
        for port_id, net in ports:
            if net is not None and net.key != ground.key:
                caps_by_net.setdefault(net.key, []).append((port_id, component))

    out: list[Finding] = [
        finding(
            label,
            "layout_intent_decoupling_override_invalid",
            f"{label} {detail}",
            "error",
        )
        for label, detail in invalid_overrides
    ]
    matched_overrides: set[int] = set()
    for component in all_populated.values():
        matching = [
            index
            for index, patterns, _ in override_rules
            if any(fnmatch.fnmatchcase(component.name, pattern) for pattern in patterns)
        ]
        matched_overrides.update(matching)
        if matching and any(
            fnmatch.fnmatchcase(component.name, pattern) for pattern in excluded
        ):
            out.append(
                finding(
                    component.name,
                    "layout_intent_decoupling_policy_conflict",
                    f"{component.name} matches both layout.decoupling.exclude and a "
                    "distance override; choose one explicit policy",
                    "error",
                )
            )
    for index, patterns, _ in override_rules:
        if index not in matched_overrides:
            out.append(
                finding(
                    ",".join(patterns),
                    "layout_intent_decoupling_override_unmatched",
                    f"decoupling override {index} matches no populated chip reference: "
                    f"{', '.join(patterns)}",
                    "error",
                )
            )
    for port_id, source_port in source_ports.items():
        component = populated.get(str(source_port.get("source_component_id") or ""))
        if component is None or source_port.get("requires_power") is not True:
            continue
        matching_maxima = [
            override_maximum
            for index, patterns, override_maximum in override_rules
            if any(fnmatch.fnmatchcase(component.name, pattern) for pattern in patterns)
        ]
        # Overlapping wildcard rules fail safely by choosing the strictest
        # applicable bound. Product authors do not get an order-dependent
        # escape hatch, while a broad family rule can still be narrowed.
        component_maximum = min(matching_maxima) if matching_maxima else float(maximum)
        pin_name = str(source_port.get("name") or source_port.get("pin_number") or port_id)
        part = f"{component.name}.{pin_name}"
        net = board.net_of_port(port_id)
        candidates = caps_by_net.get(net.key, []) if net is not None else []
        if not candidates:
            out.append(
                finding(
                    part,
                    "layout_intent_decoupling_missing",
                    f"{part} requires power but has no populated capacitor from "
                    f"{net.label if net else 'its rail'} to GND",
                    "error",
                )
            )
            continue

        reachable = _reachable(graph, port_id)
        connected = [candidate for candidate in candidates if candidate[0] in reachable]
        if not connected:
            nearest_names = ", ".join(
                sorted({component.name for _, component in candidates})[:4]
            )
            out.append(
                finding(
                    part,
                    "layout_intent_decoupling_topology",
                    f"{part} shares {net.label if net else 'a rail'} with "
                    f"{nearest_names}, but no authored port-to-port tree connects the "
                    "supply pin to a capacitor rail pad; do not delegate the bypass "
                    "loop to aggregate-net/MST routing",
                    "error",
                )
            )
            continue

        supply_rect = pad_rects.get(port_id)
        measurable = [
            (cap_port_id, cap, pad_rects[cap_port_id])
            for cap_port_id, cap in connected
            if cap_port_id in pad_rects
        ]
        if supply_rect is None or not measurable:
            out.append(
                finding(
                    part,
                    "layout_intent_decoupling_geometry",
                    f"{part} has authored decoupling topology but its supply pad or "
                    "connected capacitor rail pad has no measurable PCB copper",
                    "error",
                )
            )
            continue

        distance, cap = min(
            [
                (
                    max(0.0, supply_rect.gap_to(cap_rect)),
                    cap,
                )
                for _, cap, cap_rect in measurable
            ],
            key=lambda item: item[0],
        )
        if distance > component_maximum + 1e-9:
            out.append(
                finding(
                    part,
                    "layout_intent_decoupling_distance",
                    f"{part}'s nearest authored bypass is {cap.name}, "
                    f"{distance:.2f}mm supply-pad to capacitor-pad; the product "
                    f"allows at most {component_maximum:g}mm",
                    "error",
                )
            )
    return out


def _edge_coordinate(component: Component, edge: str) -> tuple[float, str]:
    insertion = component.cable_insertion_center
    if insertion is not None:
        return (
            insertion[1] if edge in ("bottom", "top") else insertion[0],
            "cable insertion point",
        )
    body = component.body
    return (
        {
            "bottom": body.y0,
            "top": body.y1,
            "left": body.x0,
            "right": body.x1,
        }[edge],
        "body",
    )


def _outline_edge(board: Board, edge: str) -> float:
    assert board.outline is not None
    return {
        "bottom": board.outline.y0,
        "top": board.outline.y1,
        "left": board.outline.x0,
        "right": board.outline.x1,
    }[edge]


@never_raises
def _edge_connectors(board: Board, intent: dict[str, Any]) -> list[Finding]:
    raw_rules = intent.get("edgeConnectors")
    if not isinstance(raw_rules, list) or board.outline is None:
        return []
    out: list[Finding] = []
    for rule in raw_rules:
        if not isinstance(rule, dict):
            continue
        ref = str(rule.get("ref") or "")
        edge = str(rule.get("edge") or "")
        if not ref or edge not in ("top", "bottom", "left", "right"):
            continue
        component = board.by_name.get(ref)
        if component is None or component.pcb_id is None:
            out.append(
                finding(
                    ref or "connector",
                    "layout_intent_connector_missing",
                    f"product layout requires {ref or 'a connector'} on the {edge} edge, "
                    "but no placed component with that reference exists",
                    "error",
                )
            )
            continue
        edge_tolerance = rule.get("edgeToleranceMm", 1.0)
        edge_tolerance = (
            float(edge_tolerance) if isinstance(edge_tolerance, (int, float)) else 1.0
        )
        component_edge, edge_datum = _edge_coordinate(component, edge)
        inset = abs(component_edge - _outline_edge(board, edge))
        if inset > edge_tolerance:
            out.append(
                finding(
                    ref,
                    "layout_intent_connector_edge",
                    f"{ref}'s {edge_datum} is {inset:.2f}mm from the {edge} outline; the product "
                    f"allows at most {edge_tolerance:g}mm",
                    "error",
                )
            )
        if rule.get("alignment") != "center":
            continue
        center_tolerance = rule.get("centerToleranceMm", 0.5)
        center_tolerance = (
            float(center_tolerance)
            if isinstance(center_tolerance, (int, float))
            else 0.5
        )
        alignment_center = component.cable_insertion_center or component.center
        measured = abs(
            (alignment_center[0] - board.outline.center[0])
            if edge in ("top", "bottom")
            else (alignment_center[1] - board.outline.center[1])
        )
        if measured > center_tolerance:
            out.append(
                finding(
                    ref,
                    "layout_intent_connector_alignment",
                    f"{ref} is {measured:.2f}mm off the {edge}-edge centreline; the "
                    f"product tolerance is {center_tolerance:g}mm",
                    "error",
                )
            )
    return out


def _ground_pour_layers(board: Board) -> set[str]:
    ground = board.ground
    if ground is None:
        return set()
    source_ids = {
        str(element.get("source_net_id"))
        for element in board.of_type("source_net")
        if element.get("subcircuit_connectivity_map_key") == ground.key
        and element.get("source_net_id")
    }
    return {
        str(element.get("layer"))
        for element in board.of_type("pcb_copper_pour")
        if (
            element.get("subcircuit_connectivity_map_key") == ground.key
            or element.get("source_net_id") in source_ids
        )
        and element.get("layer")
    }


def _ground_vias(board: Board) -> list[dict]:
    ground = board.ground
    if ground is None:
        return []
    return [
        element
        for element in board.of_type("pcb_via")
        if element.get("subcircuit_connectivity_map_key") == ground.key
    ]


def _ring_points(value: object) -> list[tuple[float, float]]:
    if not isinstance(value, dict) or not isinstance(value.get("vertices"), list):
        return []
    return [
        (float(point["x"]), float(point["y"]))
        for point in value["vertices"]
        if isinstance(point, dict)
        and isinstance(point.get("x"), (int, float))
        and isinstance(point.get("y"), (int, float))
    ]


def _polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(
        sum(
            x0 * y1 - x1 * y0
            for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1])
        )
        / 2
    )


def _island(element: dict[str, Any]) -> dict[str, Any] | None:
    brep = element.get("brep_shape")
    if not isinstance(brep, dict):
        return None
    outer_points = _ring_points(brep.get("outer_ring"))
    if len(outer_points) < 3:
        return None
    inner_values = brep.get("inner_rings")
    inner_points = (
        [_ring_points(value) for value in inner_values]
        if isinstance(inner_values, list)
        else []
    )
    inners = [Poly(points) for points in inner_points if len(points) >= 3]
    return {
        "id": str(element.get("pcb_copper_pour_id") or ""),
        "source_net_id": str(element.get("source_net_id") or ""),
        "subcircuit_id": str(element.get("subcircuit_id") or ""),
        "layer": str(element.get("layer") or ""),
        "outer": Poly(outer_points),
        "inners": inners,
        "area": max(
            0.0,
            _polygon_area(outer_points)
            - sum(_polygon_area(points) for points in inner_points),
        ),
    }


def _island_contains(island: dict[str, Any], x: float, y: float) -> bool:
    outer = island["outer"]
    # A termination exactly on a polygon edge is still copper. Poly.contains
    # uses the conventional strict ray crossing, so include a numerical edge
    # tolerance explicitly.
    in_outer = outer.contains(x, y) or abs(outer.distance_to_point(x, y)) <= 1e-7
    if not in_outer:
        return False
    return not any(
        inner.contains(x, y) or abs(inner.distance_to_point(x, y)) <= 1e-7
        for inner in island["inners"]
    )


@never_raises
def _plane_fanout_connectivity(board: Board) -> list[Finding]:
    """Prove that every plane-terminated fanout reaches material plane copper.

    Cross-layer fanout stops at a via tagged with a plane/layer target;
    same-layer fanout emits an explicit zero-length pour-contact marker at its
    source pad. Copper pours are solved later and may be missing or fragment
    around other routes. Circuit JSON still gives the termination and every
    fragment the same logical net key, even when there is no physical contact;
    that is an open hidden by a correct-looking net label.

    A fanout is accepted when its target-layer island is the dominant island,
    or when same-net vias connect it to a dominant island on either poured
    layer. The latter matters on intentionally stitched two-sided planes and
    avoids treating a small bottom fragment as floating when the very same
    copper is demonstrably joined to the main top plane.
    """

    islands = [
        parsed
        for element in board.of_type("pcb_copper_pour")
        if (parsed := _island(element)) is not None
        and parsed["source_net_id"]
        and parsed["layer"]
    ]
    by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    by_net_sub: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for island in islands:
        by_group.setdefault(
            (island["source_net_id"], island["subcircuit_id"], island["layer"]),
            [],
        ).append(island)
        by_net_sub.setdefault(
            (island["source_net_id"], island["subcircuit_id"]), [],
        ).append(island)
        by_id[island["id"]] = island

    source_nets = {
        str(element.get("source_net_id") or ""): element
        for element in board.of_type("source_net")
        if element.get("source_net_id")
    }
    key_to_net: dict[tuple[str, str], str] = {}
    for net_id, element in source_nets.items():
        key = element.get("subcircuit_connectivity_map_key")
        if isinstance(key, str) and key:
            key_to_net[(key, str(element.get("subcircuit_id") or ""))] = net_id

    # Physical connectivity graph between pour fragments. A same-net via whose
    # centre lies in two or more fragments joins those pieces across layers.
    graph: dict[str, set[str]] = {island["id"]: set() for island in islands}
    for via in board.of_type("pcb_via"):
        x, y = via.get("x"), via.get("y")
        key = via.get("subcircuit_connectivity_map_key")
        sub = str(via.get("subcircuit_id") or "")
        if not (
            isinstance(x, (int, float))
            and isinstance(y, (int, float))
            and isinstance(key, str)
        ):
            continue
        net_id = key_to_net.get((key, sub))
        if net_id is None:
            continue
        layers = {
            str(layer)
            for layer in (via.get("layers") or [])
            if isinstance(layer, str)
        }
        touched = [
            island
            for island in by_net_sub.get((net_id, sub), [])
            if island["layer"] in layers
            and _island_contains(island, float(x), float(y))
        ]
        for left in touched:
            for right in touched:
                if left["id"] != right["id"]:
                    graph[left["id"]].add(right["id"])

    dominant_ids = {
        max(group, key=lambda island: island["area"])["id"]
        for group in by_group.values()
        if group
    }

    def reaches_dominant(start: str) -> bool:
        pending = [start]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in dominant_ids:
                return True
            pending.extend(graph.get(current, ()))
        return False

    source_traces = {
        str(element.get("source_trace_id") or ""): element
        for element in board.of_type("source_trace")
        if element.get("source_trace_id")
    }
    pcb_ports_by_source = {
        str(element.get("source_port_id") or ""): element
        for element in board.of_type("pcb_port")
        if element.get("source_port_id")
    }
    vias_by_trace: dict[str, list[dict[str, Any]]] = {}
    for via in board.of_type("pcb_via"):
        trace_id = str(via.get("pcb_trace_id") or "")
        if trace_id:
            vias_by_trace.setdefault(trace_id, []).append(via)

    out: list[Finding] = []
    for pcb_trace in board.of_type("pcb_trace"):
        trace_id = str(pcb_trace.get("pcb_trace_id") or "")
        if not trace_id.startswith("fanout:"):
            continue
        source_trace_id = trace_id.removeprefix("fanout:")
        source_trace = source_traces.get(source_trace_id, {})
        net_ids = [
            str(value)
            for value in source_trace.get("connected_source_net_ids") or []
            if value
        ]
        trace_vias = vias_by_trace.get(trace_id, [])
        termination_via = trace_vias[-1] if trace_vias else None
        route = [
            point
            for point in (pcb_trace.get("route") or [])
            if isinstance(point, dict)
            and isinstance(point.get("x"), (int, float))
            and isinstance(point.get("y"), (int, float))
        ]
        termination = termination_via or (route[-1] if route else {})
        target_layer = str(
            termination.get("to_layer")
            or termination.get("layer")
            or (route[-1].get("layer") if route else "")
            or ""
        )
        sub = str(
            pcb_trace.get("subcircuit_id")
            or termination.get("subcircuit_id")
            or source_trace.get("subcircuit_id")
            or ""
        )
        x, y = termination.get("x"), termination.get("y")
        if len(net_ids) != 1 or not target_layer or not all(
            isinstance(value, (int, float)) for value in (x, y)
        ):
            continue
        net_id = net_ids[0]
        candidates = by_group.get((net_id, sub, target_layer), [])
        containing = [
            island
            for island in candidates
            if _island_contains(island, float(x), float(y))
        ]
        net_name = str(source_nets.get(net_id, {}).get("name") or net_id)
        trace_name = str(source_trace.get("name") or source_trace_id)
        # A same-layer plane contact contains no routed copper: the physical
        # connection is the pad itself touching the solved pour. Do not accept
        # a free-floating point that merely happens to be somewhere in GND.
        # Bind the explicit marker back to the fanout's sole compiled pad.
        if termination_via is None and len(route) == 1:
            source_port_ids = [
                str(value)
                for value in source_trace.get("connected_source_port_ids") or []
                if value
            ]
            source_port = (
                pcb_ports_by_source.get(source_port_ids[0])
                if len(source_port_ids) == 1
                else None
            )
            port_x = source_port.get("x") if source_port else None
            port_y = source_port.get("y") if source_port else None
            port_layers = {
                str(layer)
                for layer in ((source_port or {}).get("layers") or [])
                if isinstance(layer, str)
            }
            marker_is_bound = (
                route[0].get("is_inside_copper_pour") is True
                and isinstance(port_x, (int, float))
                and isinstance(port_y, (int, float))
                and abs(float(x) - float(port_x)) <= 1e-7
                and abs(float(y) - float(port_y)) <= 1e-7
                and target_layer in port_layers
            )
            if not marker_is_bound:
                out.append(
                    finding(
                        trace_name,
                        "pcb_plane_connectivity_error",
                        f"{trace_name} has an unbound same-layer plane marker at "
                        f"({float(x):.3f}, {float(y):.3f}) on {target_layer}; it must "
                        "be explicitly marked at its sole compiled source pad",
                        "error",
                    )
                )
                continue
        if len(containing) != 1:
            detail = (
                "no material pour island"
                if not containing
                else f"overlapping islands {', '.join(item['id'] for item in containing)}"
            )
            out.append(
                finding(
                    trace_name,
                    "pcb_plane_connectivity_error",
                    f"{trace_name} terminates {net_name} on {target_layer} at "
                    f"({float(x):.3f}, {float(y):.3f}) but reaches {detail}; the "
                    "fanout router's plane label is not physical connectivity",
                    "error",
                )
            )
            continue
        landed = containing[0]
        if reaches_dominant(landed["id"]):
            continue
        dominant = max(candidates, key=lambda island: island["area"])
        out.append(
            finding(
                trace_name,
                "pcb_plane_connectivity_error",
                f"{trace_name} terminates {net_name} on isolated {target_layer} pour "
                f"{landed['id']} ({landed['area']:.2f}mm^2) instead of the material "
                f"plane {dominant['id']} ({dominant['area']:.2f}mm^2); add a legal "
                "stitch/route or change fanout direction",
                "error",
            )
        )
    return out


def _island_rings(island: dict[str, Any]) -> list[Poly]:
    return [island["outer"], *island["inners"]]


def _material_islands_overlap(
    left: dict[str, Any], right: dict[str, Any]
) -> bool:
    # Boundary contact is already a zero-clearance short. Include inner-ring
    # boundaries so an electrode that touches the edge of a GND void cannot be
    # mistaken for an island safely contained by the void.
    if any(
        a.boundary_distance_to(b) <= 1e-7
        for a in _island_rings(left)
        for b in _island_rings(right)
    ):
        return True
    # With separated boundaries, one material face overlaps the other iff an
    # outer-ring point of either lies in the other's material (outer minus
    # holes). This also handles full containment without treating an island
    # wholly inside a clearance hole as copper.
    return any(
        _island_contains(right, x, y) for x, y in left["outer"].points
    ) or any(_island_contains(left, x, y) for x, y in right["outer"].points)


@never_raises
def _different_net_pour_overlaps(board: Board) -> list[Finding]:
    """Block different-net BREP faces that physically touch or overlap.

    The pour solver currently serializes both faces and no ``*_error`` when a
    new GND plane overlaps an existing capacitive electrode. This check reads
    the actual solved faces, including their inner clearance rings, rather
    than trusting source net labels or the compiler's clean exit.
    """
    islands = [
        parsed
        for element in board.of_type("pcb_copper_pour")
        if (parsed := _island(element)) is not None
        and parsed["source_net_id"]
        and parsed["layer"]
    ]
    source_nets = {
        str(element.get("source_net_id") or ""): element
        for element in board.of_type("source_net")
        if element.get("source_net_id")
    }

    def identity(island: dict[str, Any]) -> str:
        net = source_nets.get(island["source_net_id"], {})
        return str(
            net.get("subcircuit_connectivity_map_key")
            or island["source_net_id"]
        )

    def label(island: dict[str, Any]) -> str:
        net = source_nets.get(island["source_net_id"], {})
        return str(net.get("name") or island["source_net_id"])

    out: list[Finding] = []
    by_layer: dict[str, list[dict[str, Any]]] = {}
    for island in islands:
        by_layer.setdefault(island["layer"], []).append(island)
    for layer, faces in by_layer.items():
        for index, left in enumerate(faces):
            for right in faces[index + 1 :]:
                if identity(left) == identity(right):
                    continue
                if not _material_islands_overlap(left, right):
                    continue
                out.append(
                    finding(
                        f"{label(left)}/{label(right)}",
                        "pcb_copper_pour_short_error",
                        f"different-net {layer} pour faces {left['id']} "
                        f"({label(left)}, {left['area']:.2f}mm^2) and {right['id']} "
                        f"({label(right)}, {right['area']:.2f}mm^2) touch or overlap "
                        "in solved copper; change the outline/clearance before fab",
                        "error",
                    )
                )
    return out


@never_raises
def _ground_planes(board: Board, intent: dict[str, Any]) -> list[Finding]:
    policy = intent.get("groundPlanes")
    if not isinstance(policy, dict):
        return []
    required = set(_patterns(policy.get("layers")))
    present = _ground_pour_layers(board)
    out: list[Finding] = []
    missing = sorted(required - present)
    if missing:
        out.append(
            finding(
                "GND",
                "layout_intent_ground_plane_missing",
                f"product requires GND pours on {', '.join(sorted(required))}; compiled "
                f"copper has {', '.join(sorted(present)) or 'none'} (missing "
                f"{', '.join(missing)})",
                "error",
            )
        )

    ground = board.ground
    routed = sum(trace.length for trace in board.traces_on(ground)) if ground else 0.0
    maximum = policy.get("maxRoutedLengthMm")
    if isinstance(maximum, (int, float)) and routed > float(maximum) + 1e-9:
        out.append(
            finding(
                "GND",
                "layout_intent_ground_route_length",
                f"GND still has {routed:.1f}mm of ordinary routed copper; the plane-fanout "
                f"budget is {float(maximum):g}mm",
                "error",
            )
        )

    # A plane does not excuse a long, inductive detour before the plane via.
    # The fanout router labels these traces explicitly, so measure the emitted
    # copper rather than trusting that "fanout" means a local dogbone. This
    # catches an overlong pad escape even when the phase reports every
    # plane-terminated connection as successfully routed.
    max_fanout = policy.get("maxFanoutLengthMm")
    if isinstance(max_fanout, (int, float)):
        source_names = {
            str(element.get("source_trace_id") or ""): str(
                element.get("name") or element.get("source_trace_id") or "fanout"
            )
            for element in board.of_type("source_trace")
            if element.get("source_trace_id")
        }
        for trace in board.traces:
            if not trace.id.startswith("fanout:"):
                continue
            if trace.length <= float(max_fanout) + 1e-9:
                continue
            source_id = trace.id.removeprefix("fanout:")
            trace_name = source_names.get(source_id, source_id or trace.id)
            out.append(
                finding(
                    trace_name,
                    "layout_intent_ground_fanout_length",
                    f"{trace_name} travels {trace.length:.2f}mm before its plane "
                    f"termination; the product allows {float(max_fanout):g}mm. "
                    "Use a local legal same-layer connection or dogbone/via",
                    "error",
                )
            )

    pitch = policy.get("stitchingPitchMm")
    if (
        isinstance(pitch, (int, float))
        and pitch > 0
        and len(required) >= 2
        and board.outline is not None
        and not missing
    ):
        vias = _ground_vias(board)
        # A perfect square grid is neither possible nor desirable around real
        # footprints. Requiring half its count is a measurable density floor,
        # while DRC remains responsible for each via's legal position.
        ideal = board.outline.width * board.outline.height / (float(pitch) ** 2)
        minimum = max(1, math.ceil(ideal * 0.5))
        if len(vias) < minimum:
            out.append(
                finding(
                    "GND",
                    "layout_intent_ground_stitching",
                    f"the {board.outline.width:g}x{board.outline.height:g}mm board has "
                    f"{len(vias)} GND via(s); a {float(pitch):g}mm stitching policy needs "
                    f"at least {minimum} distributed vias after allowing 50% for component "
                    "keepouts",
                    "warning",
                )
            )
    return out


def _matching_nets(board: Board, patterns: list[str]) -> list[Net]:
    return [
        net
        for net in board.nets
        if net.name and any(fnmatch.fnmatchcase(net.name, pattern) for pattern in patterns)
    ]


def _narrow_run_error(
    trace: Trace,
    *,
    trunk: float,
    neckdown: float,
    max_neckdown: float,
) -> str | None:
    segments = [segment for segment in trace.segments if segment.length > 1e-9]
    if not segments:
        return None
    if any(segment.width < neckdown - 1e-9 for segment in segments):
        measured = min(segment.width for segment in segments)
        return f"contains {measured:g}mm copper below the {neckdown:g}mm neck-down floor"

    wide = [segment.width >= trunk - 1e-9 for segment in segments]
    if not any(wide):
        if trace.length > 2 * max_neckdown + 1e-9:
            return (
                f"is {trace.length:.2f}mm of neck-down copper with no {trunk:g}mm trunk; "
                f"only {max_neckdown:g}mm is allowed at each endpoint"
            )
        return None

    first_wide = wide.index(True)
    last_wide = len(wide) - 1 - list(reversed(wide)).index(True)
    prefix = sum(segment.length for segment in segments[:first_wide])
    suffix = sum(segment.length for segment in segments[last_wide + 1 :])
    if prefix > max_neckdown + 1e-9 or suffix > max_neckdown + 1e-9:
        return (
            f"has {prefix:.2f}mm/{suffix:.2f}mm endpoint neck-downs; each is limited "
            f"to {max_neckdown:g}mm"
        )
    middle_narrow = sum(
        segment.length
        for segment, is_wide in zip(segments[first_wide : last_wide + 1], wide[first_wide : last_wide + 1])
        if not is_wide
    )
    if middle_narrow > 1e-9:
        return f"narrows for {middle_narrow:.2f}mm in the middle of the power trunk"
    return None


@never_raises
def _net_classes(board: Board, intent: dict[str, Any]) -> list[Finding]:
    raw_classes = intent.get("netClasses")
    if not isinstance(raw_classes, list):
        return []
    out: list[Finding] = []
    for policy in raw_classes:
        if not isinstance(policy, dict):
            continue
        patterns = _patterns(policy.get("nets"))
        trunk = policy.get("minTrunkWidthMm")
        neckdown = policy.get("minNeckdownWidthMm", trunk)
        max_neckdown = policy.get("maxNeckdownLengthMm", 0.0)
        min_via_outer = policy.get("minViaOuterDiameterMm")
        min_via_hole = policy.get("minViaHoleDiameterMm")
        if not patterns or not all(
            isinstance(value, (int, float)) and value >= 0
            for value in (trunk, neckdown, max_neckdown)
        ):
            continue
        matched = _matching_nets(board, patterns)
        if not matched:
            out.append(
                finding(
                    str(policy.get("name") or "net class"),
                    "layout_intent_netclass_empty",
                    f"no compiled net matches {', '.join(patterns)}",
                    "error",
                )
            )
            continue
        for net in matched:
            traces = board.traces_on(net)
            if not traces:
                continue
            for trace in traces:
                problem = _narrow_run_error(
                    trace,
                    trunk=float(trunk),
                    neckdown=float(neckdown),
                    max_neckdown=float(max_neckdown),
                )
                if problem is None:
                    continue
                out.append(
                    finding(
                        net.label,
                        "layout_intent_power_trunk",
                        f"{net.label} trace {trace.id} {problem}",
                        "error",
                    )
                )

            # Power/current-class vias need their own copper and drill floor.
            # A route can have a perfectly wide 0.8mm trunk and then funnel all
            # of its current through the router's generic 0.6/0.3mm signal via.
            # Associate raw vias through either their routed trace or explicit
            # source-net/connectivity identity, then measure the emitted drill.
            if not any(
                isinstance(value, (int, float))
                for value in (min_via_outer, min_via_hole)
            ):
                continue
            trace_ids = {trace.id for trace in traces}
            source_net_ids = {
                str(element.get("source_net_id") or "")
                for element in board.of_type("source_net")
                if element.get("subcircuit_connectivity_map_key") == net.key
            }
            for via in board.of_type("pcb_via"):
                belongs = (
                    str(via.get("pcb_trace_id") or "") in trace_ids
                    or str(via.get("source_net_id") or "") in source_net_ids
                    or via.get("subcircuit_connectivity_map_key") == net.key
                )
                if not belongs:
                    continue
                actual_outer = via.get("outer_diameter")
                actual_hole = via.get("hole_diameter")
                failures: list[str] = []
                if isinstance(min_via_outer, (int, float)) and (
                    not isinstance(actual_outer, (int, float))
                    or actual_outer < float(min_via_outer) - 1e-9
                ):
                    failures.append(
                        f"outer diameter {actual_outer!r} is below {float(min_via_outer):g}mm"
                    )
                if isinstance(min_via_hole, (int, float)) and (
                    not isinstance(actual_hole, (int, float))
                    or actual_hole < float(min_via_hole) - 1e-9
                ):
                    failures.append(
                        f"drill {actual_hole!r} is below {float(min_via_hole):g}mm"
                    )
                if not failures:
                    continue
                via_id = str(via.get("pcb_via_id") or "via")
                out.append(
                    finding(
                        net.label,
                        "layout_intent_netclass_via",
                        f"{net.label} {via_id} " + " and ".join(failures),
                        "error",
                    )
                )
    return out


def check(board: Board, intent: dict[str, Any] | None = None) -> CheckResult:
    policy = intent if isinstance(intent, dict) else {}
    findings: list[Finding] = []
    findings += _board_size(board, policy)
    findings += _copper_clearance(board, policy)
    findings += _component_sides(board, policy)
    findings += _component_zones(board, policy)
    findings += _decoupling(board, policy)
    findings += _edge_connectors(board, policy)
    findings += _ground_planes(board, policy)
    findings += _plane_fanout_connectivity(board)
    findings += _different_net_pour_overlaps(board)
    findings += _net_classes(board, policy)
    declared = sum(
        1
        for key in (
            "boardSizeMm",
            "minCopperClearanceMm",
            "componentSides",
            "componentZones",
            "decoupling",
            "edgeConnectors",
            "groundPlanes",
            "netClasses",
        )
        if key in policy
    )
    coverage = Coverage(unit="declared layout policies", total=declared, examined=declared)
    if not policy:
        coverage.skip("product.json has no layout policy; product-specific placement intent is unknown")
    return CheckResult(
        name="intent",
        findings=findings,
        coverage=coverage,
        notes=[
            "first matching component-side rule wins",
            "component zones use board-global geometry and every rule must match populated hardware",
            "decoupling distance is pad-edge to pad-edge and requires authored port topology",
            "power trunks permit short endpoint neck-downs but never a narrow middle section",
        ],
    )
