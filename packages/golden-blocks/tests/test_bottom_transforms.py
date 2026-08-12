"""Focused top/bottom geometry proofs for face-aware reusable blocks.

These tests deliberately live outside ``test_blocks.py``.  They compare real
compiled copper and package ports, so a future implementation cannot satisfy
the bottom-side contract by propagating only the component ``layer`` prop.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "verify" / "src"))
from verifylib import intent  # noqa: E402
from verifylib.model import Board  # noqa: E402


def _assert_clean(g) -> None:
    assert g.errors() == []
    assert g.warnings() == []
    assert intent.check(Board(g.elements), None).findings == []


def _source_traces(g) -> dict[str, dict]:
    return {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }


def _pcb_components(g) -> dict[str, dict]:
    source_names = {
        source["source_component_id"]: name
        for name, source in g.components.items()
    }
    return {
        source_names[element["source_component_id"]]: element
        for element in g.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") in source_names
    }


def _pcb_ports(g) -> dict[str, dict]:
    return {
        element["source_port_id"]: element
        for element in g.elements
        if element.get("type") == "pcb_port"
        and element.get("source_port_id")
    }


def _component_source_ports(g, name: str) -> dict[str, dict]:
    source_id = g.components[name]["source_component_id"]
    ports = [
        element
        for element in g.elements
        if element.get("type") == "source_port"
        and element.get("source_component_id") == source_id
    ]
    assert len({port["name"] for port in ports}) == len(ports)
    return {port["name"]: port for port in ports}


def _swap_layers(layers: list[str]) -> set[str]:
    return {
        "bottom" if layer == "top" else "top" if layer == "bottom" else layer
        for layer in layers
    }


def _assert_component_mirror(top, bottom, names: set[str], axis_x: float) -> None:
    top_pcb = _pcb_components(top)
    bottom_pcb = _pcb_components(bottom)
    top_ports = _pcb_ports(top)
    bottom_ports = _pcb_ports(bottom)

    for name in sorted(names):
        top_component = top_pcb[name]
        bottom_component = bottom_pcb[name]
        assert top_component["layer"] == "top"
        assert bottom_component["layer"] == "bottom"
        assert float(bottom_component["center"]["x"]) == pytest.approx(
            2 * axis_x - float(top_component["center"]["x"]), abs=1e-9
        )
        assert float(bottom_component["center"]["y"]) == pytest.approx(
            float(top_component["center"]["y"]), abs=1e-9
        )
        assert float(bottom_component.get("rotation", 0)) % 360 == pytest.approx(
            (-float(top_component.get("rotation", 0))) % 360, abs=1e-9
        )
        assert float(bottom_component["width"]) == pytest.approx(
            float(top_component["width"]), abs=1e-9
        )
        assert float(bottom_component["height"]) == pytest.approx(
            float(top_component["height"]), abs=1e-9
        )

        top_source_ports = _component_source_ports(top, name)
        bottom_source_ports = _component_source_ports(bottom, name)
        assert set(bottom_source_ports) == set(top_source_ports)
        for port_name in top_source_ports:
            top_port = top_ports[top_source_ports[port_name]["source_port_id"]]
            bottom_port = bottom_ports[
                bottom_source_ports[port_name]["source_port_id"]
            ]
            assert float(bottom_port["x"]) == pytest.approx(
                2 * axis_x - float(top_port["x"]), abs=1e-6
            )
            assert float(bottom_port["y"]) == pytest.approx(
                float(top_port["y"]), abs=1e-6
            )
            assert set(bottom_port.get("layers") or []) == _swap_layers(
                list(top_port.get("layers") or [])
            )

        # Package copper is part of the transform contract too.  Port hints
        # are stable for the imported connector, TPS2553, and AMS1117 pads.
        top_pads = sorted([
            (
                tuple(element.get("port_hints") or []),
                element,
            )
            for element in top.elements
            if element.get("type") == "pcb_smtpad"
            and element.get("pcb_component_id") == top_component["pcb_component_id"]
        ], key=lambda item: item[0])
        bottom_pads = sorted([
            (
                tuple(element.get("port_hints") or []),
                element,
            )
            for element in bottom.elements
            if element.get("type") == "pcb_smtpad"
            and element.get("pcb_component_id")
            == bottom_component["pcb_component_id"]
        ], key=lambda item: item[0])
        assert [key for key, _ in bottom_pads] == [key for key, _ in top_pads]
        for (_, top_pad), (_, bottom_pad) in zip(top_pads, bottom_pads):
            assert bottom_pad.get("shape") == top_pad.get("shape")
            assert bottom_pad.get("layer") == "bottom"
            shape = top_pad.get("shape")
            if shape in {"rect", "circle"}:
                assert float(bottom_pad["x"]) == pytest.approx(
                    2 * axis_x - float(top_pad["x"]), abs=1e-6
                )
                assert float(bottom_pad["y"]) == pytest.approx(
                    float(top_pad["y"]), abs=1e-6
                )
            if shape == "rect":
                assert float(bottom_pad["width"]) == pytest.approx(
                    float(top_pad["width"]), abs=1e-9
                )
                assert float(bottom_pad["height"]) == pytest.approx(
                    float(top_pad["height"]), abs=1e-9
                )
            elif shape == "circle":
                assert float(bottom_pad["radius"]) == pytest.approx(
                    float(top_pad["radius"]), abs=1e-9
                )
            elif shape == "polygon":
                top_points = sorted(
                    (float(point["x"]), float(point["y"]))
                    for point in top_pad["points"]
                )
                bottom_points = sorted(
                    (float(point["x"]), float(point["y"]))
                    for point in bottom_pad["points"]
                )
                expected_points = sorted(
                    (2 * axis_x - x, y) for x, y in top_points
                )
                assert len(bottom_points) == len(expected_points)
                for actual, expected in zip(bottom_points, expected_points):
                    assert actual == pytest.approx(expected, abs=1e-6)
            else:
                raise AssertionError(f"unsupported SMT pad shape: {shape!r}")


MIRROR_CASES = [
    (
        "usb-c-power",
        "usb-c-power-bottom",
        {"J1", "R1", "R2", "U1", "C1", "N3", "N4", "N15"},
        0.0,
    ),
    (
        "usb-c-data-routing",
        "usb-c-data-bottom",
        {"J1", "R1", "R2", "R3", "R4", "U1", "C1", "N3", "N4", "N15", "N16"},
        0.0,
    ),
    (
        "usb-power-entry",
        "usb-power-entry-bottom",
        {"U7", "C24", "R31", "R32", "TP10"},
        0.0,
    ),
    ("ldo-3v3", "ldo-3v3-bottom", {"U2", "C2", "C3"}, -1.0),
    ("status-led", "status-led-bottom", {"LED1", "R20"}, 0.0),
]


@pytest.mark.parametrize(
    "top_bench,bottom_bench,names,axis_x",
    MIRROR_CASES,
    ids=[case[0] for case in MIRROR_CASES],
)
def test_bottom_blocks_are_exact_x_mirrors(
    graph, top_bench: str, bottom_bench: str, names: set[str], axis_x: float
) -> None:
    top = graph(top_bench)
    bottom = graph(bottom_bench)
    _assert_clean(top)
    _assert_clean(bottom)
    _assert_component_mirror(top, bottom, names, axis_x)


def _routed(g, trace_name: str) -> list[dict]:
    source = _source_traces(g)[trace_name]
    source_id = source["source_trace_id"]
    # ``connection_name`` is the emitted connection identity.  A routed
    # child of an authored tree can retain the boundary trace's
    # ``source_trace_id`` while carrying its own connection name, so prefer
    # the exact connection key and only fall back for legacy artifacts that
    # omit it.
    routed = [
        element
        for element in g.elements
        if element.get("type") == "pcb_trace"
        and element.get("connection_name") == source_id
    ]
    if not routed:
        routed = [
            element
            for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("source_trace_id") == source_id
        ]
    assert routed, f"{trace_name}: no compiled PCB trace"
    return sorted(routed, key=lambda element: str(element.get("pcb_trace_id")))


def _route_points(g, trace_name: str) -> list[dict]:
    routed = _routed(g, trace_name)
    assert len(routed) == 1, (trace_name, [r.get("pcb_trace_id") for r in routed])
    return list(routed[0].get("route") or [])


def _route_widths(g, trace_name: str) -> set[float]:
    return {
        float(point["width"])
        for routed in _routed(g, trace_name)
        for point in routed.get("route") or []
        if point.get("route_type") == "wire"
        and isinstance(point.get("width"), (int, float))
    }


def _route_length(g, trace_name: str) -> float:
    length = 0.0
    for routed in _routed(g, trace_name):
        points = [
            point
            for point in routed.get("route") or []
            if isinstance(point.get("x"), (int, float))
            and isinstance(point.get("y"), (int, float))
        ]
        length += sum(
            math.dist(
                (float(first["x"]), float(first["y"])),
                (float(second["x"]), float(second["y"])),
            )
            for first, second in zip(points, points[1:])
        )
    return length


def _point_segment_distance(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return math.dist(point, first)
    ratio = (
        (point[0] - first[0]) * dx + (point[1] - first[1]) * dy
    ) / denominator
    ratio = max(0.0, min(1.0, ratio))
    projection = (first[0] + ratio * dx, first[1] + ratio * dy)
    return math.dist(point, projection)


def _copper_bbox(element: dict) -> tuple[float, float, float, float]:
    if element.get("type") == "pcb_plated_hole":
        half_width = float(element["outer_width"]) / 2
        half_height = float(element["outer_height"]) / 2
        return (
            float(element["x"]) - half_width,
            float(element["x"]) + half_width,
            float(element["y"]) - half_height,
            float(element["y"]) + half_height,
        )
    shape = element.get("shape")
    if shape == "rect":
        half_width = float(element["width"]) / 2
        half_height = float(element["height"]) / 2
        return (
            float(element["x"]) - half_width,
            float(element["x"]) + half_width,
            float(element["y"]) - half_height,
            float(element["y"]) + half_height,
        )
    if shape == "circle":
        radius = float(element["radius"])
        return (
            float(element["x"]) - radius,
            float(element["x"]) + radius,
            float(element["y"]) - radius,
            float(element["y"]) + radius,
        )
    if shape == "polygon":
        xs = [float(point["x"]) for point in element["points"]]
        ys = [float(point["y"]) for point in element["points"]]
        return min(xs), max(xs), min(ys), max(ys)
    raise AssertionError(f"unsupported copper shape: {element}")


def _rectangles_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[1] <= second[0]
        or second[1] <= first[0]
        or first[3] <= second[2]
        or second[3] <= first[2]
    )


def _point_inside_copper(point: tuple[float, float], element: dict) -> bool:
    shape = element.get("shape")
    if shape == "rect":
        return (
            abs(point[0] - float(element["x"]))
            <= float(element["width"]) / 2
            and abs(point[1] - float(element["y"]))
            <= float(element["height"]) / 2
        )
    if shape == "circle":
        return math.dist(
            point, (float(element["x"]), float(element["y"]))
        ) <= float(element["radius"])
    if shape == "polygon":
        vertices = [
            (float(vertex["x"]), float(vertex["y"]))
            for vertex in element["points"]
        ]
        inside = False
        previous = vertices[-1]
        for current in vertices:
            if (current[1] > point[1]) != (previous[1] > point[1]):
                crossing_x = (
                    (previous[0] - current[0])
                    * (point[1] - current[1])
                    / (previous[1] - current[1])
                    + current[0]
                )
                if point[0] < crossing_x:
                    inside = not inside
            previous = current
        return inside
    raise AssertionError(f"unsupported SMT copper shape: {element}")


def _assert_route_endpoints(g, trace_name: str) -> None:
    source = _source_traces(g)[trace_name]
    source_port_ids = list(source.get("connected_source_port_ids") or [])
    assert len(source_port_ids) == 2
    ports = _pcb_ports(g)
    expected = [
        (float(ports[port_id]["x"]), float(ports[port_id]["y"]))
        for port_id in source_port_ids
    ]
    points = [
        point
        for routed in _routed(g, trace_name)
        for point in routed.get("route") or []
        if isinstance(point.get("x"), (int, float))
        and isinstance(point.get("y"), (int, float))
    ]
    actual = [
        (float(points[0]["x"]), float(points[0]["y"])),
        (float(points[-1]["x"]), float(points[-1]["y"])),
    ]
    assert (
        math.dist(actual[0], expected[0]) <= 1e-6
        and math.dist(actual[1], expected[1]) <= 1e-6
    ) or (
        math.dist(actual[0], expected[1]) <= 1e-6
        and math.dist(actual[1], expected[0]) <= 1e-6
    ), (trace_name, actual, expected)


def _assert_authored_route_mirror(
    top, bottom, trace_name: str, axis_x: float = 0.0
) -> None:
    top_route = _route_points(top, trace_name)
    bottom_route = _route_points(bottom, trace_name)
    assert len(bottom_route) == len(top_route)
    for top_point, bottom_point in zip(top_route, bottom_route):
        assert bottom_point.get("route_type") == top_point.get("route_type")
        assert float(bottom_point["x"]) == pytest.approx(
            2 * axis_x - float(top_point["x"]), abs=1e-9
        )
        assert float(bottom_point["y"]) == pytest.approx(
            float(top_point["y"]), abs=1e-9
        )
        if top_point.get("route_type") == "wire":
            assert float(bottom_point["width"]) == pytest.approx(
                float(top_point["width"]), abs=1e-9
            )
            assert bottom_point["layer"] == (
                "bottom" if top_point["layer"] == "top" else "top"
            )
        elif top_point.get("route_type") == "via":
            assert float(bottom_point["via_diameter"]) == pytest.approx(
                float(top_point["via_diameter"]), abs=1e-9
            )
            assert float(bottom_point["via_hole_diameter"]) == pytest.approx(
                float(top_point["via_hole_diameter"]), abs=1e-9
            )
            assert bottom_point["from_layer"] == (
                "bottom" if top_point["from_layer"] == "top" else "top"
            )
            assert bottom_point["to_layer"] == (
                "bottom" if top_point["to_layer"] == "top" else "top"
            )


def test_usb_authored_paths_mirror_vertices_layers_and_vias(graph) -> None:
    power_top = graph("usb-c-power")
    power_bottom = graph("usb-c-power-bottom")
    _assert_authored_route_mirror(
        power_top, power_bottom, "TR_J1_vbus_trunk"
    )

    data_top = graph("usb-c-data-routing")
    data_bottom = graph("usb-c-data-bottom")
    for name in (
        "TR_J1_vbus_trunk",
        "TR_J1_dp_pair",
        "TR_J1_dm_pair",
        "TR_J1_cc1r",
        "TR_J1_cc2r",
        "TR_U1_vbus_backbone",
    ):
        _assert_authored_route_mirror(data_top, data_bottom, name)


def test_usb_connector_alignment_drill_guards_are_exact_and_mirrored(graph) -> None:
    top = graph("usb-c-data-routing")
    bottom = graph("usb-c-data-bottom")
    top_guards = {
        element["pcb_keepout_id"]: element
        for element in top.elements
        if element.get("type") == "pcb_keepout"
    }
    bottom_guards = {
        element["pcb_keepout_id"]: element
        for element in bottom.elements
        if element.get("type") == "pcb_keepout"
    }
    assert len(top_guards) == 2
    assert set(bottom_guards) == set(top_guards)
    # The obsolete footprint-wide rectangle must not silently return.
    assert max(float(guard["width"]) for guard in top_guards.values()) == 1.02
    for keepout_id, top_guard in top_guards.items():
        bottom_guard = bottom_guards[keepout_id]
        assert top_guard["shape"] == bottom_guard["shape"] == "rect"
        assert top_guard["layers"] == bottom_guard["layers"] == ["top", "bottom"]
        assert float(top_guard["width"]) == pytest.approx(1.02)
        assert float(top_guard["height"]) == pytest.approx(1.02)
        assert float(bottom_guard["width"]) == pytest.approx(
            float(top_guard["width"])
        )
        assert float(bottom_guard["height"]) == pytest.approx(
            float(top_guard["height"])
        )
        assert float(bottom_guard["center"]["x"]) == pytest.approx(
            -float(top_guard["center"]["x"]), abs=1e-9
        )
        assert float(bottom_guard["center"]["y"]) == pytest.approx(
            float(top_guard["center"]["y"]), abs=1e-9
        )

    for g in (top, bottom):
        connector = _pcb_components(g)["J1"]
        connector_id = connector["pcb_component_id"]
        holes = [
            element
            for element in g.elements
            if element.get("type") == "pcb_hole"
            and element.get("pcb_component_id") == connector_id
        ]
        guards = [
            element
            for element in g.elements
            if element.get("type") == "pcb_keepout"
        ]
        assert len(holes) == len(guards) == 2
        holes.sort(key=lambda element: float(element["x"]))
        guards.sort(key=lambda element: float(element["center"]["x"]))

        connector_copper = [
            element
            for element in g.elements
            if element.get("type") in {"pcb_smtpad", "pcb_plated_hole"}
            and element.get("pcb_component_id") == connector_id
        ]
        guard_boxes = []
        for hole, guard in zip(holes, guards):
            assert float(guard["center"]["x"]) == pytest.approx(
                float(hole["x"]), abs=1e-9
            )
            assert float(guard["center"]["y"]) == pytest.approx(
                float(hole["y"]), abs=1e-9
            )
            drill_radius = float(hole["hole_diameter"]) / 2
            assert float(guard["width"]) / 2 - drill_radius >= 0.20
            assert float(guard["height"]) / 2 - drill_radius >= 0.20
            half_width = float(guard["width"]) / 2
            half_height = float(guard["height"]) / 2
            guard_box = (
                float(guard["center"]["x"]) - half_width,
                float(guard["center"]["x"]) + half_width,
                float(guard["center"]["y"]) - half_height,
                float(guard["center"]["y"]) + half_height,
            )
            guard_boxes.append(guard_box)
            assert not any(
                _rectangles_overlap(guard_box, _copper_bbox(copper))
                for copper in connector_copper
            )

            # The local guard closes both old illegal side channels: the gap
            # above it to the pad row and the gap outside it to shell copper
            # are each too small for a 0.15mm trace plus 0.15mm clearance on
            # both sides.  The central pocket remains intentionally open.
            copper_boxes = [_copper_bbox(copper) for copper in connector_copper]
            vertical_gaps = [
                copper_box[2] - guard_box[3]
                for copper_box in copper_boxes
                if copper_box[2] >= guard_box[3]
                and copper_box[0] < guard_box[1]
                and copper_box[1] > guard_box[0]
            ]
            if float(guard["center"]["x"]) > 0:
                outside_gaps = [
                    copper_box[0] - guard_box[1]
                    for copper_box in copper_boxes
                    if copper_box[0] >= guard_box[1]
                    and copper_box[2] < guard_box[3]
                    and copper_box[3] > guard_box[2]
                ]
            else:
                outside_gaps = [
                    guard_box[0] - copper_box[1]
                    for copper_box in copper_boxes
                    if copper_box[1] <= guard_box[0]
                    and copper_box[2] < guard_box[3]
                    and copper_box[3] > guard_box[2]
                ]
            required_trace_channel = 0.15 + 2 * 0.15
            assert 0 < min(vertical_gaps) < required_trace_channel
            assert 0 < min(outside_gaps) < required_trace_channel

        assert guard_boxes[1][0] - guard_boxes[0][1] > 4.7

        minimum_drill_edge_clearance = math.inf
        for hole in holes:
            hole_center = (float(hole["x"]), float(hole["y"]))
            drill_radius = float(hole["hole_diameter"]) / 2
            for routed in (
                element
                for element in g.elements
                if element.get("type") == "pcb_trace"
            ):
                route = list(routed.get("route") or [])
                for first, second in zip(route, route[1:]):
                    if not all(
                        isinstance(point.get(axis), (int, float))
                        for point in (first, second)
                        for axis in ("x", "y")
                    ):
                        continue
                    wire = first if first.get("route_type") == "wire" else second
                    if wire.get("route_type") != "wire":
                        continue
                    distance = _point_segment_distance(
                        hole_center,
                        (float(first["x"]), float(first["y"])),
                        (float(second["x"]), float(second["y"])),
                    )
                    clearance = distance - drill_radius - float(wire["width"]) / 2
                    minimum_drill_edge_clearance = min(
                        minimum_drill_edge_clearance, clearance
                    )
            for via in (
                element
                for element in g.elements
                if element.get("type") == "pcb_via"
            ):
                clearance = (
                    math.dist(
                        hole_center,
                        (float(via["x"]), float(via["y"])),
                    )
                    - drill_radius
                    - float(via["outer_diameter"]) / 2
                )
                minimum_drill_edge_clearance = min(
                    minimum_drill_edge_clearance, clearance
                )
        assert minimum_drill_edge_clearance >= 0.20


def test_usb_connector_alignment_drill_guards_follow_component_rotation(graph) -> None:
    g = graph("usb-c-connector-rotated")
    connector = _pcb_components(g)["J90"]
    connector_id = connector["pcb_component_id"]
    holes = sorted(
        (
            element
            for element in g.elements
            if element.get("type") == "pcb_hole"
            and element.get("pcb_component_id") == connector_id
        ),
        key=lambda element: float(element["y"]),
    )
    guards = sorted(
        (
            element
            for element in g.elements
            if element.get("type") == "pcb_keepout"
        ),
        key=lambda element: float(element["center"]["y"]),
    )
    assert len(holes) == len(guards) == 2
    for hole, guard in zip(holes, guards):
        assert float(guard["center"]["x"]) == pytest.approx(float(hole["x"]))
        assert float(guard["center"]["y"]) == pytest.approx(float(hole["y"]))
        assert guard["layers"] == ["top", "bottom"]
    # A 90-degree component rotation turns the original horizontal hole pair
    # into a vertical pair without changing its exact 5.799836mm spacing.
    assert float(holes[0]["x"]) == pytest.approx(float(holes[1]["x"]), abs=1e-9)
    assert abs(float(holes[1]["y"]) - float(holes[0]["y"])) == pytest.approx(
        2 * 2.899918, abs=1e-9
    )


def test_usb_pair_rules_are_pinned_on_both_face_fixtures() -> None:
    root = Path(__file__).resolve().parents[1]
    for bench in ("usb-c-data-routing.tsx", "usb-c-data-bottom.tsx"):
        source = (root / "testbench" / bench).read_text(encoding="utf-8")
        assert "pcbTraceGapMm: 0.15" in source
        assert "maxLengthSkewMm: 3.8" in source
        assert "maxUncoupledLengthMm: 3" in source


def test_usb_data_bottom_routed_contract_matches_top(graph) -> None:
    pair = (
        "TR_J1_dp_esd",
        "TR_U1_dp_r",
        "TR_J1_dm_esd",
        "TR_U1_dm_r",
    )
    local_power_cc = (
        "TR_J1_vbus1_neck",
        "TR_J1_vbus2_neck",
        "TR_J1_vbus_rail",
        "TR_J1_cc1r",
        "TR_J1_cc2r",
        "TR_U1_vbus",
        "TR_C1_vbus",
    )
    ground = (
        "TR_J1_gnd1",
        "TR_J1_gnd2",
        "TR_J1_sh1",
        "TR_J1_sh2",
        "TR_J1_sh3",
        "TR_J1_sh4",
        "TR_R1_gnd",
        "TR_R2_gnd",
        "TR_U1_gnd",
        "TR_C1_gnd",
    )

    for bench in ("usb-c-data-routing", "usb-c-data-bottom"):
        g = graph(bench)
        _assert_clean(g)
        traces = _source_traces(g)
        assert len(pair) == 4 and set(pair) <= set(traces)
        assert len(local_power_cc) == 7 and set(local_power_cc) <= set(traces)
        assert len(ground) == 10 and set(ground) <= set(traces)
        for name in (*pair, *local_power_cc, *ground):
            assert _routed(g, name)

        pours = [
            element
            for element in g.elements
            if element.get("type") == "pcb_copper_pour"
        ]
        assert {pour["layer"] for pour in pours} == {"top", "bottom"}
        for name in ground:
            routed = _routed(g, name)
            assert len(routed) == 1
            route = list(routed[0].get("route") or [])
            assert len(route) == 1
            assert route[0].get("is_inside_copper_pour") is True

        for name in pair:
            assert _route_widths(g, name) == {0.15}
            _assert_route_endpoints(g, name)
        for name in ("TR_J1_cc1r", "TR_J1_cc2r"):
            assert _route_widths(g, name) == {0.25}
            _assert_route_endpoints(g, name)
        for name in ("TR_J1_vbus1_neck", "TR_J1_vbus2_neck"):
            assert _route_widths(g, name) == {0.2}
            assert _route_length(g, name) <= 2.0
            _assert_route_endpoints(g, name)
        for name in ("TR_U1_vbus", "TR_C1_vbus"):
            assert _route_widths(g, name) == {0.2}
            assert _route_length(g, name) <= 3.0
            _assert_route_endpoints(g, name)
        for name in (
            "TR_J1_vbus_trunk",
            "TR_J1_vbus_rail",
            "TR_U1_vbus_backbone",
        ):
            assert _route_widths(g, name) == {0.8}

        signal_vias = [
            point
            for name in ("TR_J1_dp_pair", "TR_J1_dm_pair")
            for point in _route_points(g, name)
            if point.get("route_type") == "via"
        ]
        power_vias = [
            point
            for name in ("TR_J1_vbus_trunk", "TR_U1_vbus_backbone")
            for point in _route_points(g, name)
            if point.get("route_type") == "via"
        ]
        assert len(signal_vias) == 4
        assert {
            (float(via["via_diameter"]), float(via["via_hole_diameter"]))
            for via in signal_vias
        } == {(0.6, 0.3)}
        assert len(power_vias) == 4
        assert {
            (float(via["via_diameter"]), float(via["via_hole_diameter"]))
            for via in power_vias
        } == {(0.8, 0.5)}

        smt_pads = [
            element
            for element in g.elements
            if element.get("type") == "pcb_smtpad"
        ]
        vias = [
            element
            for element in g.elements
            if element.get("type") == "pcb_via"
        ]
        assert not [
            (via["pcb_via_id"], pad["pcb_smtpad_id"])
            for via in vias
            for pad in smt_pads
            if _point_inside_copper(
                (float(via["x"]), float(via["y"])), pad
            )
        ]

        dp_length = sum(
            _route_length(g, name)
            for name in ("TR_J1_dp_pair", "TR_J1_dp_esd", "TR_U1_dp_r")
        )
        dm_length = sum(
            _route_length(g, name)
            for name in ("TR_J1_dm_pair", "TR_J1_dm_esd", "TR_U1_dm_r")
        )
        assert abs(dp_length - dm_length) <= 3.8


def test_usb_power_entry_bottom_preserves_power_fault_contract(graph) -> None:
    expected = {
        "TR_U7_in_C24": (0.3, 2.0),
        "TR_U7_en": (0.2, 3.0),
        "TR_U7_ilim": (0.2, 2.0),
        "TR_U7_fault": (0.15, 1.0),
        "TR_R32_fault": (0.25, 2.0),
        "TR_R32_pullup": (0.2, 3.0),
        "TR_TP10_fault": (0.25, 3.0),
    }
    built = {}
    for bench in ("usb-power-entry", "usb-power-entry-bottom"):
        g = graph(bench)
        built[bench] = g
        _assert_clean(g)
        board = next(
            element for element in g.elements if element.get("type") == "pcb_board"
        )
        assert float(board["min_trace_to_pad_edge_clearance"]) == pytest.approx(
            0.15
        )
        assert float(board["min_via_edge_to_pad_edge_clearance"]) == pytest.approx(
            0.15
        )
        traces = _source_traces(g)
        for name, (width, max_length) in expected.items():
            assert float(traces[name]["min_trace_thickness"]) == pytest.approx(width)
            assert float(traces[name]["max_length"]) == pytest.approx(max_length)

        for name, width, max_length in (
            ("TR_U7_in_C24", 0.3, 2.0),
            ("TR_U7_en", 0.2, 3.0),
            ("TR_U7_ilim", 0.2, 2.0),
            ("TR_U7_fault", 0.15, 1.0),
            ("TR_R32_fault", 0.25, 2.0),
        ):
            assert _route_widths(g, name) == {width}
            assert _route_length(g, name) <= max_length
            _assert_route_endpoints(g, name)

        assert set(traces["TR_U7_in_C24"]["connected_source_port_ids"]) == {
            g.port_id("U7.IN"),
            g.port_id("C24.pin1"),
        }
        assert set(traces["TR_U7_fault"]["connected_source_port_ids"]) == {
            g.port_id("U7.FAULT"),
            g.port_id("TP10.pin1"),
        }
        assert set(traces["TR_R32_fault"]["connected_source_port_ids"]) == {
            g.port_id("R32.pin1"),
            g.port_id("TP10.pin1"),
        }
        assert {
            traces[name]["name"]
            for name in ("TR_C24_gnd", "TR_U7_gnd", "TR_R31_gnd")
        } == {"TR_C24_gnd", "TR_U7_gnd", "TR_R31_gnd"}
        vias = [element for element in g.elements if element.get("type") == "pcb_via"]
        assert len(vias) == 4
        assert {
            (float(via["outer_diameter"]), float(via["hole_diameter"]))
            for via in vias
        } == {(0.6, 0.3)}

    top = built["usb-power-entry"]
    bottom = built["usb-power-entry-bottom"]
    _assert_authored_route_mirror(top, bottom, "TR_U7_en")

    expected_top_path = [
        (1.35001, 0.94996),
        (2.2, 0.75),
        (2.59, -0.5),
    ]
    for g, expected_layer, mirror_x in (
        (top, "top", False),
        (bottom, "bottom", True),
    ):
        route = _route_points(g, "TR_U7_en")
        assert all(point.get("route_type") == "wire" for point in route)
        collapsed_path: list[tuple[float, float]] = []
        for point in route:
            coordinate = (float(point["x"]), float(point["y"]))
            if not collapsed_path or coordinate != collapsed_path[-1]:
                collapsed_path.append(coordinate)
            assert point["layer"] == expected_layer
            assert float(point["width"]) == pytest.approx(0.2)
        expected = [
            (-x if mirror_x else x, y) for x, y in expected_top_path
        ]
        assert len(collapsed_path) == len(expected)
        for actual, authored in zip(collapsed_path, expected):
            assert actual == pytest.approx(authored, abs=1e-9)
        assert _route_length(g, "TR_U7_en") == pytest.approx(
            2.182620920607784, abs=1e-9
        )
        assert _route_length(g, "TR_U7_en") <= 3.0


def test_ldo_bottom_preserves_local_cap_trees(graph) -> None:
    for bench in ("ldo-3v3", "ldo-3v3-bottom"):
        g = graph(bench)
        _assert_clean(g)
        traces = _source_traces(g)
        assert set(traces["TR_U2_vin_C2"]["connected_source_port_ids"]) == {
            g.port_id("U2.VIN"),
            g.port_id("C2.pin1"),
        }
        assert set(traces["TR_U2_tab_C3"]["connected_source_port_ids"]) == {
            g.port_id("U2.TAB"),
            g.port_id("C3.pin1"),
        }
        for name, width in (("TR_U2_vin_C2", 0.2), ("TR_U2_tab_C3", 0.8)):
            assert _route_widths(g, name) == {width}
            assert 0 < _route_length(g, name) <= 3.0
            assert not [
                point
                for point in _route_points(g, name)
                if point.get("route_type") == "via"
            ]
            _assert_route_endpoints(g, name)


def test_status_led_bottom_preserves_series_path(graph) -> None:
    for bench in ("status-led", "status-led-bottom"):
        g = graph(bench)
        _assert_clean(g)
        traces = _source_traces(g)
        assert float(traces["TR_R20_rail"]["min_trace_thickness"]) == pytest.approx(
            0.2
        )
        assert float(traces["TR_R20_rail"]["max_length"]) == pytest.approx(3.0)
        assert set(traces["TR_R20_led"]["connected_source_port_ids"]) == {
            g.port_id("R20.pin2"),
            g.port_id("LED1.anode"),
        }
        assert _route_widths(g, "TR_R20_led") == {0.25}
        assert _route_length(g, "TR_R20_led") <= 3.0
        assert not [
            point
            for point in _route_points(g, "TR_R20_led")
            if point.get("route_type") == "via"
        ]
        _assert_route_endpoints(g, "TR_R20_led")
