"""Behavioral prevention for board-owned protected-V5 attachment trees."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest


GB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = GB_ROOT.parents[1]

sys.path.insert(0, str(REPO_ROOT / "packages" / "verify" / "src"))
from verifylib import intent  # noqa: E402
from verifylib.model import Board  # noqa: E402


LAYOUT = {
    "minCopperClearanceMm": 0.15,
    "groundPlanes": {
        "layers": ["top", "bottom"],
        "maxFanoutLengthMm": 2.0,
        "stitchingPitchMm": 20.0,
        "maxRoutedLengthMm": 20.0,
    },
    "netClasses": [{
        "name": "POWER",
        "nets": ["V5"],
        "minTrunkWidthMm": 0.8,
        "minNeckdownWidthMm": 0.2,
        "maxNeckdownLengthMm": 3.0,
        "minViaOuterDiameterMm": 0.8,
        "minViaHoleDiameterMm": 0.5,
    }],
}


def _traces(g) -> dict[str, dict]:
    return {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }


def _route(g, name: str) -> dict:
    source_id = _traces(g)[name]["source_trace_id"]
    matches = [
        element
        for element in g.elements
        if element.get("type") == "pcb_trace"
        and source_id in {
            element.get("source_trace_id"),
            element.get("connection_name"),
        }
    ]
    assert len(matches) == 1, (name, len(matches))
    return matches[0]


def _port(g, ref: str) -> dict:
    source_id = g.port_id(ref)
    matches = [
        element
        for element in g.elements
        if element.get("type") == "pcb_port"
        and element.get("source_port_id") == source_id
    ]
    assert len(matches) == 1, (ref, len(matches))
    return matches[0]


def _routing_checks(path: Path) -> list[dict]:
    module = (
        REPO_ROOT / "toolchain" / "node_modules" / "@tscircuit"
        / "checks" / "dist" / "index.js"
    )
    program = "\n".join([
        'import fs from "node:fs"',
        f'import * as checks from {json.dumps(module.as_uri())}',
        'const c = JSON.parse(fs.readFileSync(process.argv[1], "utf8"))',
        'console.log(JSON.stringify(await checks.runAllRoutingChecks(c)))',
    ])
    result = subprocess.run(
        ["node", "--input-type=module", "-e", program, str(path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def _wire_points(route: dict) -> list[dict]:
    points = []
    for point in route.get("route") or []:
        if point.get("route_type") != "wire":
            continue
        if not points or any(
            point.get(key) != points[-1].get(key)
            for key in ("x", "y", "layer", "width")
        ):
            points.append(point)
    return points


def test_old_unscoped_ldo_input_attachment_has_two_v5_boundaries(graph) -> None:
    """Freeze the failure that the typed opt-out is designed to prevent."""
    broken = graph("ldo-3v3-external-vin-duplicate")
    traces = _traces(broken)
    v5_id = broken.nets["V5"]["source_net_id"]
    assert [
        trace["name"]
        for trace in traces.values()
        if v5_id in (trace.get("connected_source_net_ids") or [])
    ] == ["TR_C2_V5_boundary", "TR_BOARD_V5_BOUNDARY"]


@pytest.mark.parametrize(
    ("bench", "component_layer", "trunk_layer", "sign"),
    [
        ("ldo-3v3-external-vin-top", "top", "bottom", 1),
        ("ldo-3v3-external-vin-bottom", "bottom", "top", -1),
    ],
)
def test_typed_ldo_input_attachment_is_one_exact_mirrored_tree(
    graph, farm, bench: str, component_layer: str, trunk_layer: str, sign: int
) -> None:
    g = graph(bench)
    assert g.errors() == []
    assert g.warnings() == []

    board = next(e for e in g.elements if e.get("type") == "pcb_board")
    assert float(board["min_trace_to_pad_edge_clearance"]) == pytest.approx(0.15)
    assert float(board["min_via_edge_to_pad_edge_clearance"]) == pytest.approx(0.15)

    traces = _traces(g)
    assert "TR_C2_V5_boundary" not in traces
    assert set(traces["TR_U2_vin_C2"]["connected_source_port_ids"]) == {
        g.port_id("U2.VIN"), g.port_id("C2.pin1")
    }
    assert float(traces["TR_U2_vin_C2"]["min_trace_thickness"]) == 0.2
    assert float(traces["TR_U2_vin_C2"]["max_length"]) == 3.0
    assert set(traces["TR_BOARD_V5_ATTACH"]["connected_source_port_ids"]) == {
        g.port_id("N90.pin1"), g.port_id("C2.pin1")
    }
    assert traces["TR_BOARD_V5_BOUNDARY"]["connected_source_port_ids"] == [
        g.port_id("N90.pin1")
    ]
    assert traces["TR_BOARD_V5_BOUNDARY"]["connected_source_net_ids"] == [
        g.nets["V5"]["source_net_id"]
    ]
    assert [
        trace["name"]
        for trace in traces.values()
        if g.nets["V5"]["source_net_id"]
        in (trace.get("connected_source_net_ids") or [])
    ] == ["TR_BOARD_V5_BOUNDARY"]
    for ref in ("U2.VIN", "C2.pin1", "N90.pin1", "net.V5"):
        assert g.connected("U2.VIN", ref)

    cin = _port(g, "C2.pin1")
    node = _port(g, "N90.pin1")
    assert (float(cin["x"]), float(cin["y"])) == pytest.approx(
        (sign * 5.8875, 2.3)
    )
    assert (float(node["x"]), float(node["y"])) == pytest.approx(
        (sign * 5.8, -3)
    )
    assert set(cin["layers"]) == {component_layer}
    assert set(node["layers"]) == {trunk_layer}

    local = _route(g, "TR_U2_vin_C2")
    local_route = list(local.get("route") or [])
    local_wires = _wire_points(local)
    assert {float(point["width"]) for point in local_wires} == {0.2}
    assert {point["layer"] for point in local_wires} == {component_layer}
    assert not [point for point in local_route if point.get("route_type") == "via"]
    local_length = sum(
        math.dist(
            (float(first["x"]), float(first["y"])),
            (float(second["x"]), float(second["y"])),
        )
        for first, second in zip(local_wires, local_wires[1:])
    )
    assert 0 < local_length <= 3.0

    attachment = _route(g, "TR_BOARD_V5_ATTACH")
    route = list(attachment.get("route") or [])
    wires = _wire_points(attachment)
    assert {float(point["width"]) for point in wires} == {0.8}
    assert {point["layer"] for point in wires} == {component_layer, trunk_layer}
    vias = [point for point in route if point.get("route_type") == "via"]
    assert len(vias) == 1
    via = vias[0]
    assert (float(via["x"]), float(via["y"])) == pytest.approx((sign * 5.8, 0))
    assert (via["from_layer"], via["to_layer"]) == (
        trunk_layer, component_layer
    )
    assert float(via["via_diameter"]) == 0.8
    assert float(via["via_hole_diameter"]) == 0.5
    physical = [
        element
        for element in g.elements
        if element.get("type") == "pcb_via"
        and element.get("pcb_trace_id") == attachment["pcb_trace_id"]
    ]
    assert len(physical) == 1
    assert float(physical[0]["outer_diameter"]) == 0.8
    assert float(physical[0]["hole_diameter"]) == 0.5

    # The transition must be off both endpoint pads, not merely noncoincident.
    via_center = (float(via["x"]), float(via["y"]))
    for endpoint in (cin, node):
        center = (float(endpoint["x"]), float(endpoint["y"]))
        assert math.dist(via_center, center) - 0.4 - 0.4 >= 0.15

    assert _routing_checks(farm.circuit_json_path(bench)) == []
    findings = intent.check(Board(g.elements), LAYOUT).findings
    assert findings == []
