"""Board-floor ratchet for testbenches that make routed-copper claims."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from board_source_contract import force_routing_enabled, routing_is_definitely_disabled


TESTBENCH_DIR = Path(__file__).resolve().parents[1] / "testbench"

# Machine-readable classification: every routing-enabled testbench must be a
# strict authoritative proof, an explicit current blocker, or a geometry-only
# fixture. Source-only transform/topology benches declare ``routingDisabled``
# and are discovered separately below.
ROUTING_AUTHORITATIVE_BENCHES = (
    "external-rail-attachments-routed",
    "gnd-planes",
    "i2c-bus",
    "ldo-3v3",
    "ldo-3v3-bottom",
    "ldo-3v3-external-tab",
    "ldo-3v3-external-vin-bottom",
    "ldo-3v3-external-vin-top",
    "power-trunk",
    "power-trunk-cross-layer",
    "sensor-bme280",
    "status-led",
    "status-led-bottom",
    "status-led-signal",
    "sw-tact",
    "usb-c-data-bottom",
    "usb-c-data-routing",
    "usb-power-entry",
    "usb-power-entry-bottom",
    "ws2812-chain",
    "ws2812-chain-bottom",
    "ws2812-level-shifter",
)

ROUTING_CONTRACT_BLOCKERS = {
    "rp2040-critical-routing": {
        "trace": "TR_U4_clk",
        "width_mm": 0.15,
        "declares_real_floors": True,
    },
}

NON_ROUTING_GEOMETRY_BENCHES = {"glue-safety"}

# Existing heavy USB, power, and RP tests already own their full exact-routing
# proofs. These are the compact behavior builds added by this ratchet for the
# benches whose board contracts were previously implicit.
FLOOR_BEHAVIOR_BENCHES = (
    "i2c-bus",
    "ldo-3v3",
    "ldo-3v3-bottom",
    "ldo-3v3-external-tab",
    "ldo-3v3-external-vin-bottom",
    "ldo-3v3-external-vin-top",
    "power-trunk-cross-layer",
    "sensor-bme280",
    "status-led",
    "status-led-bottom",
    "status-led-signal",
    "sw-tact",
    "ws2812-chain",
    "ws2812-chain-bottom",
    "ws2812-level-shifter",
)


def test_every_routing_enabled_bench_has_fail_closed_contract_status() -> None:
    sources = {
        path.stem: path.read_text()
        for path in TESTBENCH_DIR.glob("*.tsx")
    }
    routing_enabled = {
        bench
        for bench, source in sources.items()
        if not routing_is_definitely_disabled(source)
    }
    classified = (
        set(ROUTING_AUTHORITATIVE_BENCHES)
        | set(ROUTING_CONTRACT_BLOCKERS)
        | NON_ROUTING_GEOMETRY_BENCHES
    )
    assert routing_enabled == classified
    assert not (
        set(ROUTING_AUTHORITATIVE_BENCHES) & set(ROUTING_CONTRACT_BLOCKERS)
    )


@pytest.mark.parametrize("bench", ROUTING_AUTHORITATIVE_BENCHES)
def test_routing_authoritative_benches_declare_real_board_floors(bench: str) -> None:
    source = (TESTBENCH_DIR / f"{bench}.tsx").read_text()
    assert not routing_is_definitely_disabled(source)
    assert 'minTraceToPadEdgeClearance="0.15mm"' in source
    assert 'minViaEdgeToPadEdgeClearance="0.15mm"' in source


@pytest.mark.parametrize("bench,blocker", ROUTING_CONTRACT_BLOCKERS.items())
def test_non_authoritative_routing_benches_remain_explicit_blockers(
    bench: str, blocker: dict
) -> None:
    source = (TESTBENCH_DIR / f"{bench}.tsx").read_text()
    declares_real_floors = (
        'minTraceToPadEdgeClearance="0.15mm"' in source
        and 'minViaEdgeToPadEdgeClearance="0.15mm"' in source
    )
    assert declares_real_floors is blocker["declares_real_floors"]
    assert blocker["trace"]
    assert float(blocker["width_mm"]) >= 0.15


@pytest.mark.parametrize(
    ("source", "disabled"),
    (
        ('export default () => <board routingDisabled />', True),
        ('export default () => <board routingDisabled={true} />', True),
        ('export default () => <board routingDisabled={false} />', False),
        (
            '// routingDisabled is supported for geometry-only callers\n'
            'export default () => <board />',
            False,
        ),
        (
            '/* <board routingDisabled> is only documentation */\n'
            'export default () => <board data-note="routingDisabled ..." />',
            False,
        ),
        (
            'export const B = (props: { routingDisabled?: boolean } = {}) => '
            '<board routingDisabled={props.routingDisabled ?? false} />',
            False,
        ),
        ('export default () => <board routingDisabled={chosen} />', False),
        ('export default () => <board {...props} routingDisabled={false} />', False),
        ('export default () => <board routingDisabled={false} {...props} />', False),
    ),
)
def test_routing_classification_reads_the_board_prop_not_a_substring(
    source: str, disabled: bool
) -> None:
    assert routing_is_definitely_disabled(source) is disabled


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            'export default () => <board routingDisabled />',
            'export default () => <board routingDisabled={false} />',
        ),
        (
            'export default () => <board routingDisabled={true} />',
            'export default () => <board routingDisabled={false} />',
        ),
        (
            'export default () => <board routingDisabled={false} />',
            'export default () => <board routingDisabled={false} />',
        ),
        (
            'export const B = (props: { routingDisabled?: boolean } = {}) => '
            '<board routingDisabled={props.routingDisabled ?? false} />',
            'export const B = (props: { routingDisabled?: boolean } = {}) => '
            '<board routingDisabled={props.routingDisabled ?? false} />',
        ),
        (
            '/* <board routingDisabled> is documentation */\n'
            'export default () => <board data-note="routingDisabled ..." />',
            '/* <board routingDisabled> is documentation */\n'
            'export default () => <board data-note="routingDisabled ..." />',
        ),
        (
            'export default () => <board {...props} routingDisabled={false} />',
            'export default () => <board {...props} routingDisabled={false} />',
        ),
    ),
)
def test_gauntlet_routing_override_rewrites_only_definite_true(
    source: str, expected: str
) -> None:
    assert force_routing_enabled(source) == expected


@pytest.mark.parametrize(
    "source",
    (
        'export default () => <board routingDisabled={chosen} />',
        'export default () => <board {...props} />',
        'export default () => <board routingDisabled={false} {...props} />',
    ),
)
def test_gauntlet_routing_override_refuses_dynamic_controls(source: str) -> None:
    with pytest.raises(AssertionError, match="dynamic routingDisabled"):
        force_routing_enabled(source)


@pytest.mark.parametrize("bench", FLOOR_BEHAVIOR_BENCHES)
def test_routing_authoritative_benches_emit_clean_copper_at_real_floors(
    graph, bench: str
) -> None:
    compiled = graph(bench)
    board = next(
        element
        for element in compiled.elements
        if element.get("type") == "pcb_board"
    )
    assert float(board["min_trace_to_pad_edge_clearance"]) == pytest.approx(0.15)
    assert float(board["min_via_edge_to_pad_edge_clearance"]) == pytest.approx(0.15)
    assert compiled.errors() == []
    assert compiled.warnings() == []
    assert any(element.get("type") == "source_trace" for element in compiled.elements)
    assert any(element.get("type") == "pcb_trace" for element in compiled.elements)


def _route_for_source_trace(compiled, trace_name: str) -> list[dict]:
    source = next(
        element
        for element in compiled.elements
        if element.get("type") == "source_trace"
        and element.get("name") == trace_name
    )
    source_id = source["source_trace_id"]
    routed = [
        element
        for element in compiled.elements
        if element.get("type") == "pcb_trace"
        and source_id
        in {element.get("source_trace_id"), element.get("connection_name")}
    ]
    assert len(routed) == 1
    return list(routed[0].get("route") or [])


def _collapsed_wire_path(route: list[dict]) -> list[tuple[float, float]]:
    assert route
    assert all(point.get("route_type") == "wire" for point in route)
    collapsed: list[tuple[float, float]] = []
    for point in route:
        coordinate = (float(point["x"]), float(point["y"]))
        if not collapsed or coordinate != collapsed[-1]:
            collapsed.append(coordinate)
    return collapsed


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


def _segments_intersect(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
    fourth: tuple[float, float],
) -> bool:
    def orientation(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (
            b[1] - a[1]
        ) * (c[0] - a[0])

    values = (
        orientation(first, second, third),
        orientation(first, second, fourth),
        orientation(third, fourth, first),
        orientation(third, fourth, second),
    )
    return values[0] * values[1] <= 0 and values[2] * values[3] <= 0


def _segment_distance(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
    fourth: tuple[float, float],
) -> float:
    if _segments_intersect(first, second, third, fourth):
        return 0.0
    return min(
        _point_segment_distance(first, third, fourth),
        _point_segment_distance(second, third, fourth),
        _point_segment_distance(third, first, second),
        _point_segment_distance(fourth, first, second),
    )


def _segment_rect_distance(
    first: tuple[float, float], second: tuple[float, float], element: dict
) -> float:
    half_width = float(element["width"]) / 2
    half_height = float(element["height"]) / 2
    left = float(element["x"]) - half_width
    right = float(element["x"]) + half_width
    bottom = float(element["y"]) - half_height
    top = float(element["y"]) + half_height
    corners = ((left, bottom), (right, bottom), (right, top), (left, top))
    return min(
        _segment_distance(first, second, corner, corners[(index + 1) % 4])
        for index, corner in enumerate(corners)
    )


def test_bme_csb_strap_is_an_exact_transform_safe_authored_path(graph) -> None:
    top = graph("sensor-bme280")
    bottom = graph("sensor-bme280-bottom")
    expected_top = [(-1.25855, 0.325), (1.25855, -0.325)]
    for compiled, layer, expected in (
        (top, "top", expected_top),
        (bottom, "bottom", [(-x, y) for x, y in expected_top]),
    ):
        assert compiled.errors() == []
        assert compiled.warnings() == []
        route = _route_for_source_trace(compiled, "TR_U5_csb")
        actual = _collapsed_wire_path(route)
        assert len(actual) == len(expected)
        for actual_point, expected_point in zip(actual, expected):
            assert actual_point == pytest.approx(expected_point, abs=1e-9)
        assert {point["layer"] for point in route} == {layer}
        assert {float(point["width"]) for point in route} == {0.2}


def test_bme_csb_strap_clears_other_lga_pads_and_stays_masked(graph) -> None:
    compiled = graph("sensor-bme280")
    route = _route_for_source_trace(compiled, "TR_U5_csb")
    segment = _collapsed_wire_path(route)
    assert len(segment) == 2

    source_component_id = compiled.components["U5"]["source_component_id"]
    pcb_component = next(
        element
        for element in compiled.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") == source_component_id
    )
    endpoint_port_ids = {
        point[key]
        for point in route
        for key in ("start_pcb_port_id", "end_pcb_port_id")
        if point.get(key)
    }
    other_pads = [
        element
        for element in compiled.elements
        if element.get("type") == "pcb_smtpad"
        and element.get("pcb_component_id") == pcb_component["pcb_component_id"]
        and element.get("pcb_port_id") not in endpoint_port_ids
    ]
    assert len(other_pads) == 6
    copper_edge_gaps = [
        _segment_rect_distance(segment[0], segment[1], pad) - 0.2 / 2
        for pad in other_pads
    ]
    # Pill pads fit inside these conservative rectangular bounds, so this is
    # at least as strict as exact pill geometry.
    assert min(copper_edge_gaps) >= 0.15 - 1e-9

    # A pcb_trace is solder-mask-covered copper. Exposed apertures and paste
    # are distinct pad/paste primitives; neither may be attached to this trace
    # or occupy the open package interior between the two endpoint pads.
    midpoint = (
        (segment[0][0] + segment[1][0]) / 2,
        (segment[0][1] + segment[1][1]) / 2,
    )
    apertures = [
        element
        for element in compiled.elements
        if element.get("type") in {"pcb_smtpad", "pcb_solder_paste"}
    ]
    assert not [
        element
        for element in apertures
        if abs(midpoint[0] - float(element["x"])) <= float(element["width"]) / 2
        and abs(midpoint[1] - float(element["y"]))
        <= float(element["height"]) / 2
    ]


def test_shifter_gnd_fanouts_terminate_on_the_authoritative_plane(graph) -> None:
    compiled = graph("ws2812-level-shifter")
    assert compiled.errors() == []
    assert compiled.warnings() == []
    pours = [
        element
        for element in compiled.elements
        if element.get("type") == "pcb_copper_pour"
    ]
    assert len(pours) == 1
    assert pours[0]["layer"] == "top"
    for trace_name in ("TR_U6_oe", "TR_U6_gnd", "TR_C20_g"):
        route = _route_for_source_trace(compiled, trace_name)
        assert len(route) == 1
        assert route[0].get("is_inside_copper_pour") is True
        assert route[0]["layer"] == "top"
        assert float(route[0]["width"]) == pytest.approx(0.2)
