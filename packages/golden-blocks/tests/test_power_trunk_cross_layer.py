"""Exact behavioral contract for PowerTrunk's explicit face transition."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest


GB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = GB_ROOT.parents[1]
TOOLCHAIN_BIN = REPO_ROOT / "toolchain" / "node_modules" / ".bin"

sys.path.insert(0, str(REPO_ROOT / "packages" / "verify" / "src"))
from verifylib import intent  # noqa: E402
from verifylib.model import Board  # noqa: E402


LAYOUT = {
    "minCopperClearanceMm": 0.15,
    "decoupling": {"maxDistanceMm": 2.0},
    "groundPlanes": {
        "layers": ["top", "bottom"],
        "maxFanoutLengthMm": 2.0,
        "stitchingPitchMm": 10.0,
        "maxRoutedLengthMm": 20.0,
    },
    "netClasses": [{
        "name": "POWER",
        "nets": ["V3_3"],
        "minTrunkWidthMm": 0.8,
        "minNeckdownWidthMm": 0.2,
        "maxNeckdownLengthMm": 2.0,
        "minViaOuterDiameterMm": 0.8,
        "minViaHoleDiameterMm": 0.5,
    }],
}


def _source_traces(g) -> dict[str, dict]:
    return {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }


def _routed(g, trace_name: str) -> dict:
    source_id = _source_traces(g)[trace_name]["source_trace_id"]
    matches = [
        element
        for element in g.elements
        if element.get("type") == "pcb_trace"
        and source_id
        in {element.get("source_trace_id"), element.get("connection_name")}
    ]
    assert len(matches) == 1, (trace_name, len(matches))
    return matches[0]


def _collapsed_wires(route: list[dict]) -> list[tuple[float, float, str, float]]:
    collapsed: list[tuple[float, float, str, float]] = []
    for point in route:
        if point.get("route_type") != "wire":
            continue
        wire = (
            float(point["x"]),
            float(point["y"]),
            str(point["layer"]),
            float(point["width"]),
        )
        if not collapsed or wire != collapsed[-1]:
            collapsed.append(wire)
    return collapsed


def _pcb_port(g, ref: str) -> dict:
    source_port_id = g.port_id(ref)
    matches = [
        element
        for element in g.elements
        if element.get("type") == "pcb_port"
        and element.get("source_port_id") == source_port_id
    ]
    assert len(matches) == 1, (ref, len(matches))
    return matches[0]


def _via_to_pad_edge_gap(via: dict, pad: dict) -> float:
    via_radius = float(via["outer_diameter"]) / 2
    point = (float(via["x"]), float(via["y"]))
    center = (float(pad["x"]), float(pad["y"]))
    if pad.get("shape") == "circle":
        return math.dist(point, center) - via_radius - float(pad["radius"])
    assert pad.get("shape") == "rect", pad
    half_width = float(pad["width"]) / 2
    half_height = float(pad["height"]) / 2
    dx = max(abs(point[0] - center[0]) - half_width, 0.0)
    dy = max(abs(point[1] - center[1]) - half_height, 0.0)
    return math.hypot(dx, dy) - via_radius


def _run_independent_routing_checks(circuit_json: Path) -> list[dict]:
    checks_module = (
        REPO_ROOT
        / "toolchain"
        / "node_modules"
        / "@tscircuit"
        / "checks"
        / "dist"
        / "index.js"
    )
    program = "\n".join([
        'import fs from "node:fs"',
        f'import * as checks from {json.dumps(checks_module.as_uri())}',
        'const circuit = JSON.parse(fs.readFileSync(process.argv[1], "utf8"))',
        'console.log(JSON.stringify(await checks.runAllRoutingChecks(circuit)))',
    ])
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", program, str(circuit_json)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(proc.stdout)


def test_power_trunk_cross_layer_rejects_incomplete_or_unsafe_props() -> None:
    proc = subprocess.run(
        [str(TOOLCHAIN_BIN / "tsx"), "tests/power_trunk_validation.tsx"],
        cwd=GB_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.stdout.strip() == (
        "PowerTrunk legacy and cross-layer validation regressions passed"
    )


def test_power_trunk_cross_layer_real_ldo_is_one_clean_authored_tree(
    graph, farm
) -> None:
    g = graph("power-trunk-cross-layer")
    assert g.errors() == []
    assert g.warnings() == []

    board = next(
        element for element in g.elements if element.get("type") == "pcb_board"
    )
    assert float(board["min_trace_to_pad_edge_clearance"]) == pytest.approx(0.15)
    assert float(board["min_via_edge_to_pad_edge_clearance"]) == pytest.approx(
        0.15
    )

    traces = _source_traces(g)
    tree_names = {
        "TR_V3V3_MAIN_IN",
        "TR_V3V3_MAIN_TRUNK",
        "TR_V3V3_MAIN_OUT",
    }
    assert tree_names <= set(traces)
    assert set(traces["TR_V3V3_MAIN_IN"]["connected_source_port_ids"]) == {
        g.port_id("TP13.pin1"),
        g.port_id("U2.VOUT"),
    }
    assert set(traces["TR_V3V3_MAIN_TRUNK"]["connected_source_port_ids"]) == {
        g.port_id("TP13.pin1"),
        g.port_id("TP14.pin1"),
    }
    assert traces["TR_V3V3_MAIN_OUT"]["connected_source_port_ids"] == [
        g.port_id("TP14.pin1")
    ]
    assert traces["TR_V3V3_MAIN_OUT"]["connected_source_net_ids"] == [
        g.nets["V3_3"]["source_net_id"]
    ]
    assert [
        trace["name"]
        for trace in traces.values()
        if g.nets["V3_3"]["source_net_id"]
        in (trace.get("connected_source_net_ids") or [])
    ] == ["TR_V3V3_MAIN_OUT"]
    for ref in ("TP13.pin1", "TP14.pin1", "net.V3_3"):
        assert g.connected("U2.VOUT", ref)

    # The transition owns one acyclic 4-node/3-edge tree, including the sole
    # named-net boundary. The source keeps no independent aggregate leaf.
    vertices = {
        g.port_id("U2.VOUT"),
        g.port_id("TP13.pin1"),
        g.port_id("TP14.pin1"),
        g.net_key("V3_3"),
    }
    assert len(vertices) == 4
    assert len(tree_names) == len(vertices) - 1

    source_port = _pcb_port(g, "U2.VOUT")
    start_port = _pcb_port(g, "TP13.pin1")
    end_port = _pcb_port(g, "TP14.pin1")
    assert (float(source_port["x"]), float(source_port["y"])) == pytest.approx(
        (5.2, 2.3)
    )
    assert set(source_port["layers"]) == {"top"}
    assert (float(start_port["x"]), float(start_port["y"])) == pytest.approx(
        (5.2, 4.3)
    )
    assert set(start_port["layers"]) == {"top"}
    assert (float(end_port["x"]), float(end_port["y"])) == pytest.approx(
        (-10, 4)
    )
    assert set(end_port["layers"]) == {"bottom"}

    neck_route = list(_routed(g, "TR_V3V3_MAIN_IN").get("route") or [])
    neck_wires = _collapsed_wires(neck_route)
    assert [wire[2] for wire in neck_wires] == ["top", "top"]
    assert [(wire[0], wire[1], wire[3]) for wire in neck_wires] == pytest.approx([
        (5.2, 4.3, 0.2),
        (5.2, 2.3, 0.2),
    ])
    neck_length = math.dist(neck_wires[0][:2], neck_wires[1][:2])
    assert neck_length == pytest.approx(2.0, abs=1e-9)
    assert neck_length <= 2
    assert not [point for point in neck_route if point.get("route_type") == "via"]

    trunk = _routed(g, "TR_V3V3_MAIN_TRUNK")
    trunk_route = list(trunk.get("route") or [])
    trunk_wires = _collapsed_wires(trunk_route)
    assert [wire[2] for wire in trunk_wires] == [
        "top", "top", "bottom", "bottom"
    ]
    assert [(wire[0], wire[1], wire[3]) for wire in trunk_wires] == pytest.approx([
        (5.2, 4.3, 0.8),
        (3.6, 5.0, 0.8),
        (3.6, 5.0, 0.8),
        (-10, 4, 0.8),
    ])
    route_vias = [
        point for point in trunk_route if point.get("route_type") == "via"
    ]
    assert route_vias == [{
        "route_type": "via",
        "x": 3.6,
        "y": 5.0,
        "from_layer": "top",
        "to_layer": "bottom",
        "via_hole_diameter": 0.5,
        "via_diameter": 0.8,
    }]

    compiled_vias = [
        element
        for element in g.elements
        if element.get("type") == "pcb_via"
        and element.get("pcb_trace_id") == trunk["pcb_trace_id"]
    ]
    assert len(compiled_vias) == 1
    transition_via = compiled_vias[0]
    assert (float(transition_via["x"]), float(transition_via["y"])) == (
        3.6,
        5.0,
    )
    assert float(transition_via["outer_diameter"]) == pytest.approx(0.8)
    assert float(transition_via["hole_diameter"]) == pytest.approx(0.5)
    assert set(transition_via["layers"]) == {"top", "bottom"}

    # Stronger than a schema-only via-in-pad check: the standalone transition
    # clears every compiled SMD pad edge on both faces by the board's .15mm
    # floor.
    pads = [element for element in g.elements if element.get("type") == "pcb_smtpad"]
    gaps = [_via_to_pad_edge_gap(transition_via, pad) for pad in pads]
    assert min(gaps) >= 0.15
    assert not [
        element
        for element in g.elements
        if element.get("type") == "pcb_via_in_smd_pad_error"
    ]

    assert _run_independent_routing_checks(
        farm.circuit_json_path("power-trunk-cross-layer")
    ) == []
    assert intent.check(Board(g.elements), LAYOUT).findings == []


def test_power_trunk_cross_layer_keeps_legacy_power_trunk_source_unchanged(
    graph,
) -> None:
    legacy = graph("power-trunk")
    assert legacy.errors() == []
    assert legacy.warnings() == []
    assert not [
        element
        for element in legacy.elements
        if element.get("type") == "pcb_via"
        and element.get("pcb_trace_id")
    ]
    assert {
        element.get("name")
        for element in legacy.elements
        if element.get("type") == "source_trace"
    } >= {
        "TR_V5_MAIN_IN",
        "TR_V5_MAIN_TRUNK",
        "TR_V5_MAIN_OUT",
        "TR_V3V3_MAIN_IN",
        "TR_V3V3_MAIN_TRUNK",
        "TR_V3V3_MAIN_OUT",
    }

    glue_source = (GB_ROOT / "blocks" / "glue.tsx").read_text()
    assert "name={`TR_${props.name}_OUT`}" in glue_source
    assert glue_source.count("authoredNetTreeBoundary") >= 2
