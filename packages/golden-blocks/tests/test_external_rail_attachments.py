"""Blocks can join one board-authored rail tree without duplicate leaves."""

from __future__ import annotations

import math
from pathlib import Path


def _source_traces(g) -> dict[str, dict]:
    return {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }


def test_external_rail_attachment_ports_remove_only_named_net_leaves(
    graph, farm
) -> None:
    g = graph("external-rail-attachments")
    assert g.errors() == []
    assert g.warnings() == []

    traces = _source_traces(g)
    assert "TR_U7_out" not in traces
    assert "TR_C24_raw" not in traces
    assert "TR_R32_pullup" not in traces
    assert "TR_R20_rail" not in traces

    # Every local electrical contract remains present. Board composition owns
    # only the three explicitly documented attachment selectors.
    assert {
        "TR_U7_in_C24",
        "TR_U7_en",
        "TR_BOARD_RAW_NECK",
        "TR_BOARD_RAW_TRUNK",
        "TR_BOARD_RAW_BOUNDARY",
        "TR_U7_ilim",
        "TR_U7_fault",
        "TR_R32_fault",
        "TR_TP10_fault",
        "TR_R20_led",
        "TR_LED1_gnd",
    } <= set(traces)
    assert g.connected("U7.FAULT", "R32.pin1")
    assert g.connected("C24.pin1", "net.VBUS_RAW")
    assert g.connected("U7.FAULT", "net.USB_POWER_FAULT")
    assert g.connected("R32.pin2", "net.V3_3")
    assert g.connected("R20.pin1", "net.V3_3")
    assert g.connected("R20.pin2", "LED1.anode")
    assert g.connected("LED1.cathode", "net.GND")
    assert traces["TR_BOARD_V3_ATTACH"]["connected_source_net_ids"] == []
    assert traces["TR_BOARD_V3_BOUNDARY"]["connected_source_port_ids"] == [
        g.port_id("R20.pin1")
    ]
    assert traces["TR_BOARD_V3_BOUNDARY"]["connected_source_net_ids"] == [
        g.nets["V3_3"]["source_net_id"]
    ]
    assert [
        trace["name"]
        for trace in traces.values()
        if g.nets["V3_3"]["source_net_id"]
        in (trace.get("connected_source_net_ids") or [])
    ] == ["TR_BOARD_V3_BOUNDARY"]
    assert traces["TR_BOARD_RAW_NECK"]["connected_source_net_ids"] == []
    assert set(traces["TR_BOARD_RAW_NECK"]["connected_source_port_ids"]) == {
        g.port_id("C24.pin1"),
        g.port_id("N901.pin1"),
    }
    assert float(traces["TR_BOARD_RAW_NECK"]["min_trace_thickness"]) == 0.2
    assert float(traces["TR_BOARD_RAW_NECK"]["max_length"]) == 2
    assert set(traces["TR_BOARD_RAW_TRUNK"]["connected_source_port_ids"]) == {
        g.port_id("N901.pin1"),
        g.port_id("N902.pin1"),
    }
    assert float(traces["TR_BOARD_RAW_TRUNK"]["min_trace_thickness"]) == 0.8
    assert traces["TR_BOARD_RAW_BOUNDARY"]["connected_source_port_ids"] == [
        g.port_id("N902.pin1")
    ]
    assert traces["TR_BOARD_RAW_BOUNDARY"]["connected_source_net_ids"] == [
        g.nets["VBUS_RAW"]["source_net_id"]
    ]
    assert [
        trace["name"]
        for trace in traces.values()
        if g.nets["VBUS_RAW"]["source_net_id"]
        in (trace.get("connected_source_net_ids") or [])
    ] == ["TR_BOARD_RAW_BOUNDARY"]

    routed_graph = graph("external-rail-attachments-routed")
    assert routed_graph.errors() == []
    assert routed_graph.warnings() == []
    routed_traces = _source_traces(routed_graph)
    pcb_by_source = {
        element.get("connection_name"): element
        for element in routed_graph.elements
        if element.get("type") == "pcb_trace"
    }
    for name, expected_width, expected_length in (
        ("TR_BOARD_RAW_NECK", 0.2, 0.9),
        ("TR_BOARD_RAW_TRUNK", 0.8, math.hypot(3.41, 1.6)),
    ):
        routed = pcb_by_source[routed_traces[name]["source_trace_id"]]
        wires = [
            point for point in routed["route"]
            if point.get("route_type") == "wire"
        ]
        assert {float(point["width"]) for point in wires} == {expected_width}
        length = sum(
            math.dist(
                (float(a["x"]), float(a["y"])),
                (float(b["x"]), float(b["y"])),
            )
            for a, b in zip(wires, wires[1:])
        )
        assert math.isclose(length, expected_length, abs_tol=1e-9)
    assert 0.9 <= 2

    status_source = (
        Path(__file__).resolve().parents[1]
        / "blocks"
        / "status-led"
        / "status-led.tsx"
    ).read_text()
    entry_source = (
        Path(__file__).resolve().parents[1]
        / "blocks"
        / "usb-power-entry"
        / "usb-power-entry.tsx"
    ).read_text()
    assert 'externalRailAttachmentPort?: "R"' in status_source
    assert 'externalFaultPullupPort?: "R32"' in entry_source
    assert 'externalRawPowerTrunkPort?: "IN"' in entry_source
