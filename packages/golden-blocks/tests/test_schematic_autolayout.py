"""Regression boundary for dense authored power-tree schematic rendering."""

from __future__ import annotations

from collections import Counter
from pathlib import Path


GB_ROOT = Path(__file__).resolve().parents[1]
BENCH = "multibranch-authored-rail-schematic"


def test_multibranch_external_rails_compile_with_schematic_autolayout(farm) -> None:
    """Do not feed center-collapsed rail trees to the schematic overlap solver.

    With no schematic placement or autolayout, the pinned schematic solver can
    remain in ``TraceOverlapShiftSolver`` for minutes once both a nine-spoke
    V3 tree and a three-spoke DVDD tree exist.  This positive regression keeps
    schematic generation enabled and proves the supported autolayout boundary;
    ``schematicDisabled`` would hide the defect instead of exercising it.
    """

    source = (GB_ROOT / "testbench" / f"{BENCH}.tsx").read_text(
        encoding="utf-8"
    )
    # Check before invoking the compiler so accidental removal fails quickly
    # rather than waiting for the BuildFarm's outer timeout.
    assert "schAutoLayoutEnabled" in source
    assert "schematicDisabled" not in source

    elements = farm.circuit_json(BENCH)
    errors = [e for e in elements if e["type"].endswith("_error")]
    warnings = [e for e in elements if e["type"].endswith("_warning")]
    assert errors == []
    assert warnings == []

    source_traces = {
        element["name"]: element
        for element in elements
        if element.get("type") == "source_trace"
    }
    expected_spokes = {
        *(f"TR_N{index}_V3_HUB" for index in range(1, 10)),
        *(f"TR_N{index}_DVDD_HUB" for index in range(11, 14)),
    }
    assert expected_spokes <= source_traces.keys()
    assert {"TR_V3_ESCAPE", "TR_DVDD_ESCAPE"} <= source_traces.keys()

    # The two externally authored trees remain independent, and every branch
    # in a tree carries the same source-connectivity identity as its boundary.
    v3_key = source_traces["TR_V3_ESCAPE"]["subcircuit_connectivity_map_key"]
    dvdd_key = source_traces["TR_DVDD_ESCAPE"][
        "subcircuit_connectivity_map_key"
    ]
    assert v3_key != dvdd_key
    assert {
        source_traces[name]["subcircuit_connectivity_map_key"]
        for name in expected_spokes
        if "_V3_" in name
    } == {v3_key}
    assert {
        source_traces[name]["subcircuit_connectivity_map_key"]
        for name in expected_spokes
        if "_DVDD_" in name
    } == {dvdd_key}

    # Schematic output must genuinely exist: this is not a PCB-only or
    # schematic-disabled fixture. Match-adapt should spread the node symbols
    # instead of leaving all of them at the default (0, 0) center.
    schematic_components = [
        element
        for element in elements
        if element.get("type") == "schematic_component"
    ]
    schematic_positions = {
        (
            round(float(element["center"]["x"]), 6),
            round(float(element["center"]["y"]), 6),
        )
        for element in schematic_components
    }
    # MaskedCopperNode is intentionally PCB/source-only. The twelve served
    # capacitors plus C14/C17, U3/U4, R13, and SW2 are the schematic symbols
    # whose real RP rail/reset/flash graph triggers the collapsed-layout case.
    assert len(schematic_components) == 18
    assert len(schematic_positions) > 2
    assert any(
        element.get("type") in {"schematic_trace", "schematic_net_label"}
        for element in elements
    )

    # All twelve physical branches are fixed 0.8mm top-to-bottom paths with
    # one legal 0.8/0.5mm via. The port-to-net boundaries intentionally have
    # no duplicate PCB copper.
    pcb_traces_by_source_id = {
        element["source_trace_id"]: element
        for element in elements
        if element.get("type") == "pcb_trace"
        and element.get("source_trace_id")
    }
    pcb_vias_by_trace_id: dict[str, list[dict]] = {}
    for element in elements:
        if element.get("type") != "pcb_via":
            continue
        pcb_vias_by_trace_id.setdefault(element["pcb_trace_id"], []).append(element)
    route_type_counts: Counter[str] = Counter()
    for name in expected_spokes:
        source_id = source_traces[name]["source_trace_id"]
        route = pcb_traces_by_source_id[source_id]["route"]
        route_type_counts.update(point["route_type"] for point in route)
        wire_widths = [
            point["width"]
            for point in route
            if point["route_type"] == "wire"
        ]
        assert wire_widths
        assert set(wire_widths) == {0.8}
        vias = [point for point in route if point["route_type"] == "via"]
        assert len(vias) == 1
        assert vias[0]["from_layer"] == "top"
        assert vias[0]["to_layer"] == "bottom"
        standalone_vias = pcb_vias_by_trace_id[
            pcb_traces_by_source_id[source_id]["pcb_trace_id"]
        ]
        assert len(standalone_vias) == 1
        assert standalone_vias[0]["outer_diameter"] == 0.8
        assert standalone_vias[0]["hole_diameter"] == 0.5
    assert route_type_counts["via"] == 12
    for boundary in ("TR_V3_ESCAPE", "TR_DVDD_ESCAPE"):
        assert source_traces[boundary]["source_trace_id"] not in pcb_traces_by_source_id
