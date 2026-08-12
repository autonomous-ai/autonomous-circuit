"""Behavioral regression for the reversible USB raw-VBUS rail crossover."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


def _source_trace(g, name: str) -> dict:
    return next(
        element
        for element in g.elements
        if element.get("type") == "source_trace" and element.get("name") == name
    )


def _pcb_trace(g, source_trace_id: str) -> dict:
    return next(
        element
        for element in g.elements
        if element.get("type") == "pcb_trace"
        and (
            element.get("connection_name") == source_trace_id
            or element.get("pcb_trace_id") == f"{source_trace_id}_0"
        )
    )


def _pcb_port_id(g, ref: str) -> str:
    source_port_id = g.port_id(ref)
    return next(
        element["pcb_port_id"]
        for element in g.elements
        if element.get("type") == "pcb_port"
        and element.get("source_port_id") == source_port_id
    )


def _independent_checks(circuit_path: Path) -> list[dict]:
    checks_module = (
        REPO_ROOT
        / "toolchain"
        / "node_modules"
        / "@tscircuit"
        / "checks"
        / "dist"
        / "index.js"
    )
    program = "\n".join(
        [
            'import fs from "node:fs"',
            f'import * as checks from {json.dumps(checks_module.as_uri())}',
            'const circuit = JSON.parse(fs.readFileSync(process.argv[1], "utf8"))',
            'console.log(JSON.stringify(await checks.runAllRoutingChecks(circuit)))',
        ]
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", program, str(circuit_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(proc.stdout)


def test_usb_data_raw_vbus_rail_returns_to_component_face_top_and_bottom(
    graph, farm
) -> None:
    built = {}
    for bench, face, opposite in (
        ("usb-c-data-routing", "top", "bottom"),
        ("usb-c-data-bottom", "bottom", "top"),
    ):
        g = graph(bench)
        built[bench] = g
        assert g.errors() == []
        assert g.warnings() == []

        source = _source_trace(g, "TR_J1_vbus_rail")
        assert float(source["min_trace_thickness"]) == pytest.approx(0.8)
        trace = _pcb_trace(g, source["source_trace_id"])
        assert trace["connectsTo"] == [
            _pcb_port_id(g, "N4.pin1"),
            _pcb_port_id(g, "N15.pin1"),
        ]

        ports = {
            element["pcb_port_id"]: element
            for element in g.elements
            if element.get("type") == "pcb_port"
        }
        assert ports[trace["connectsTo"][0]]["layers"] == [face]
        assert ports[trace["connectsTo"][1]]["layers"] == [face]

        route = list(trace["route"])
        wires = [point for point in route if point.get("route_type") == "wire"]
        vias = [point for point in route if point.get("route_type") == "via"]
        assert wires[0]["start_pcb_port_id"] == trace["connectsTo"][0]
        assert wires[-1]["end_pcb_port_id"] == trace["connectsTo"][1]
        assert wires[0]["layer"] == wires[-1]["layer"] == face
        assert {float(point["width"]) for point in wires} == {0.8}
        assert len(vias) == 2
        assert {
            (float(via["via_diameter"]), float(via["via_hole_diameter"]))
            for via in vias
        } == {(0.8, 0.5)}
        assert [(via["from_layer"], via["to_layer"]) for via in vias] == [
            (face, opposite),
            (opposite, face),
        ]
        assert _independent_checks(farm.circuit_json_path(bench)) == []

    top = _pcb_trace(
        built["usb-c-data-routing"],
        _source_trace(built["usb-c-data-routing"], "TR_J1_vbus_rail")["source_trace_id"],
    )["route"]
    bottom = _pcb_trace(
        built["usb-c-data-bottom"],
        _source_trace(built["usb-c-data-bottom"], "TR_J1_vbus_rail")["source_trace_id"],
    )["route"]
    assert len(top) == len(bottom)
    for top_point, bottom_point in zip(top, bottom):
        assert float(bottom_point["x"]) == pytest.approx(-float(top_point["x"]), abs=1e-9)
        assert float(bottom_point["y"]) == pytest.approx(float(top_point["y"]), abs=1e-9)
        if top_point.get("layer"):
            assert bottom_point["layer"] == (
                "bottom" if top_point["layer"] == "top" else "top"
            )
