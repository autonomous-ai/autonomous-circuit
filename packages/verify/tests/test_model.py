"""Circuit-json joins that every independent check relies on."""

from __future__ import annotations

import pytest

from verifylib.model import Board


def test_fixed_pcbpath_source_trace_is_joined_to_its_net() -> None:
    board = Board(
        [
            {
                "type": "source_net",
                "source_net_id": "source_net_v5",
                "name": "V5",
                "is_power": True,
                "subcircuit_connectivity_map_key": "conn_V5",
            },
            {
                "type": "source_trace",
                "source_trace_id": "source_trace_power_trunk",
                "name": "TR_V5_TRUNK",
                "connected_source_port_ids": ["source_port_a", "source_port_b"],
                "subcircuit_connectivity_map_key": "conn_V5",
            },
            {
                "type": "pcb_trace",
                "pcb_trace_id": "pcb_trace_fixed_power_trunk",
                # Fixed pcbPath serialization deliberately has no
                # ``connection_name``; this is the regression shape.
                "source_trace_id": "source_trace_power_trunk",
                "route": [
                    {
                        "route_type": "wire",
                        "x": 0,
                        "y": 0,
                        "width": 0.8,
                        "layer": "top",
                    },
                    {
                        "route_type": "wire",
                        "x": 12,
                        "y": 0,
                        "width": 0.8,
                        "layer": "top",
                    },
                ],
            },
        ]
    )
    v5 = board.net_named("V5")
    assert v5 is not None
    traces = board.traces_on(v5)
    assert len(traces) == 1
    assert traces[0].id == "pcb_trace_fixed_power_trunk"
    assert traces[0].net_name == "V5"
    assert traces[0].length == 12
    assert traces[0].min_width == 0.8


def test_polygon_smt_pad_is_retained_from_its_real_bounds() -> None:
    """Custom connector lands are real pads even without scalar x/y fields."""
    board = Board(
        [
            {
                "type": "source_component",
                "source_component_id": "source_component_j1",
                "name": "J1",
                "manufacturer_part_number": "TEST-CONCAVE-PAD",
                "supplier_part_numbers": {"jlcpcb": ["C123"]},
            },
            {
                "type": "pcb_component",
                "pcb_component_id": "pcb_component_j1",
                "source_component_id": "source_component_j1",
                "center": {"x": 0, "y": 0},
                "width": 4,
                "height": 3,
                "layer": "top",
            },
            {
                "type": "pcb_smtpad",
                "pcb_smtpad_id": "pcb_smtpad_custom",
                "pcb_component_id": "pcb_component_j1",
                "layer": "top",
                "shape": "polygon",
                "points": [
                    {"x": 2.1, "y": -0.65},
                    {"x": 2.7, "y": -0.65},
                    {"x": 2.7, "y": 0.65},
                    {"x": 2.1, "y": 0.65},
                ],
            },
        ]
    )
    assert len(board.components) == 1
    assert len(board.components[0].pads) == 1
    pad = board.components[0].pads[0]
    assert pad.id == "pcb_smtpad_custom"
    assert (pad.x, pad.y, pad.width, pad.height) == pytest.approx(
        (2.4, 0.0, 0.6, 1.3)
    )
    assert pad.copper_outline.points == (
        (2.1, -0.65),
        (2.7, -0.65),
        (2.7, 0.65),
        (2.1, 0.65),
    )
    assert board.components[0].manufacturer_part_number == "TEST-CONCAVE-PAD"
    assert board.components[0].lcsc == "C123"
