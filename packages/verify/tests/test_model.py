"""Circuit-json joins that every independent check relies on."""

from __future__ import annotations

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
