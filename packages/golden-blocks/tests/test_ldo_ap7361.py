"""Physical and thermal contract for the selected AP7361C regulator."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
import sys

import pytest


GB_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = GB_ROOT.parents[1]
BLOCK_SOURCE = GB_ROOT / "blocks" / "ldo-3v3" / "ldo-3v3.tsx"

sys.path.insert(0, str(REPO_ROOT / "packages" / "circuitpy" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "verify" / "src"))
from circuitpy.checks import dfm_warnings  # noqa: E402
from circuitpy.fab import get_profile  # noqa: E402
from circuitpy.spec import ResolvedProduct  # noqa: E402
from verifylib import power_intent  # noqa: E402
from verifylib.model import Board  # noqa: E402


REGULATOR_POLICY = {
    "regulators": [{
        "profile": "ap7361c-33e-c500795-v1",
        "ref": "U2",
        "inputNet": "V5",
        "outputNet": "V3_3",
        "inputCapRef": "C2",
        "outputCapRef": "C3",
        "maxAmbientC": 60.0,
    }],
}


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


def _pad_for(g, ref: str) -> dict:
    pcb_port_id = _pcb_port(g, ref)["pcb_port_id"]
    matches = [
        element
        for element in g.elements
        if element.get("type") == "pcb_smtpad"
        and element.get("pcb_port_id") == pcb_port_id
    ]
    assert len(matches) == 1, (ref, len(matches))
    return matches[0]


def _source_traces(g) -> dict[str, dict]:
    return {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }


def _routed(g, name: str) -> dict:
    source_id = _source_traces(g)[name]["source_trace_id"]
    matches = [
        element
        for element in g.elements
        if element.get("type") == "pcb_trace"
        and source_id in {
            element.get("source_trace_id"), element.get("connection_name")
        }
    ]
    assert len(matches) == 1, (name, len(matches))
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


@pytest.mark.parametrize(
    ("bench", "layer", "mirror"),
    [("ldo-3v3", "top", False), ("ldo-3v3-bottom", "bottom", True)],
)
def test_ap7361_uses_manufacturer_land_and_two_material_ground_contacts(
    graph, farm, bench: str, layer: str, mirror: bool
) -> None:
    g = graph(bench)
    assert g.errors() == []
    assert g.warnings() == []

    u2 = g.components["U2"]
    assert u2["manufacturer_part_number"] == "AP7361C-33E-13"
    assert u2["supplier_part_numbers"] == {"jlcpcb": ["C500795"]}
    assert not [
        port
        for port in g.ports
        if port.get("source_component_id") == u2["source_component_id"]
        and port.get("name") in {"EN", "NC", "TAB"}
    ]
    for ref in ("C2", "C3"):
        assert g.components[ref]["supplier_part_numbers"] == {
            "jlcpcb": ["C19702"]
        }
        assert float(g.components[ref]["capacitance"]) == pytest.approx(10e-6)

    pads = {
        name: _pad_for(g, f"U2.{name}")
        for name in ("VIN", "GND1", "VOUT", "GND2")
    }
    assert (float(pads["VIN"]["width"]), float(pads["VIN"]["height"])) == (
        1.6, 1.2
    )
    assert (float(pads["GND1"]["width"]), float(pads["GND1"]["height"])) == (
        1.6, 1.2
    )
    assert (float(pads["VOUT"]["width"]), float(pads["VOUT"]["height"])) == (
        1.6, 1.2
    )
    assert (float(pads["GND2"]["width"]), float(pads["GND2"]["height"])) == (
        1.6, 3.3
    )
    assert math.dist(
        (float(pads["VIN"]["x"]), float(pads["VIN"]["y"])),
        (float(pads["VOUT"]["x"]), float(pads["VOUT"]["y"])),
    ) == pytest.approx(4.6)
    assert abs(float(pads["VIN"]["x"]) - float(pads["GND2"]["x"])) == (
        pytest.approx(6.4)
    )
    outer_span = (
        abs(float(pads["VIN"]["x"]) - float(pads["GND2"]["x"]))
        + float(pads["VIN"]["width"]) / 2
        + float(pads["GND2"]["width"]) / 2
    )
    assert outer_span == pytest.approx(8.0)
    assert sum(float(pad["width"]) * float(pad["height"]) for pad in pads.values()) == (
        pytest.approx(11.04)
    )

    # The electrical distance contract is measured at copper edges as well as
    # at source endpoints. Both exact 0603 ceramics sit 0.525mm from their supply
    # pad and their authored centerline is 1.725mm, with no via.
    for supply, cap_ref, trace_name in (
        ("VIN", "C2", "TR_U2_vin_C2"),
        ("VOUT", "C3", "TR_U2_vout_C3"),
    ):
        regulator_pad = pads[supply]
        cap_pad = _pad_for(g, f"{cap_ref}.pin1")
        horizontal_gap = (
            abs(float(regulator_pad["x"]) - float(cap_pad["x"]))
            - float(regulator_pad["width"]) / 2
            - float(cap_pad["width"]) / 2
        )
        assert horizontal_gap == pytest.approx(0.525, abs=1e-9)
        route = list(_routed(g, trace_name).get("route") or [])
        wires = [point for point in route if point.get("route_type") == "wire"]
        collapsed: list[tuple[float, float]] = []
        for point in wires:
            coordinate = (float(point["x"]), float(point["y"]))
            if not collapsed or coordinate != collapsed[-1]:
                collapsed.append(coordinate)
        assert len(collapsed) == 2
        assert math.dist(*collapsed) == pytest.approx(1.725, abs=1e-9)
        assert math.dist(*collapsed) <= 2.0
        assert not [point for point in route if point.get("route_type") == "via"]

    for name, ref in (("TR_U2_gnd1", "U2.GND1"), ("TR_U2_tab_gnd", "U2.GND2")):
        route = list(_routed(g, name).get("route") or [])
        assert len(route) == 1
        assert route[0]["layer"] == layer
        assert route[0].get("is_inside_copper_pour") is True
        port = _pcb_port(g, ref)
        assert route[0]["start_pcb_port_id"] == port["pcb_port_id"]
        assert route[0]["end_pcb_port_id"] == port["pcb_port_id"]

    # Top and bottom use the same manufacturer land and material plane contact.
    # The X sign changes about the block's -1mm anchor; dimensions do not.
    assert (float(pads["VIN"]["x"]) < -1) is mirror
    assert _routing_checks(farm.circuit_json_path(bench)) == []
    assert power_intent.check(Board(g.elements), REGULATOR_POLICY).findings == []

    # The fab-profile DFM gate consumes the same compiled geometry. This
    # catches a land/copper change even when routing and the source topology
    # still look plausible.
    product = ResolvedProduct(
        name=f"{bench}-dfm",
        description="AP7361 top/bottom golden proof",
        power="usb-c-5v",
        envelope_mm=(40.0, 30.0),
        layers=2,
        fab="jlcpcb",
        assembly=True,
        path=GB_ROOT / "testbench" / f"{bench}.tsx",
    )
    assert dfm_warnings(g.elements, product, get_profile("jlcpcb")) == []


def test_ap7361_import_delta_and_150ma_thermal_contract_are_frozen() -> None:
    source = BLOCK_SOURCE.read_text(encoding="utf-8")
    for literal in (
        "2.4649938x1.0500106mm leads",
        "row center 5.715mm",
        "2.4649938x3.539998mm tab",
        "1.20x1.60mm leads",
        "3.30x1.60mm GND tab",
        "6.40mm row-center distance",
        "8.00mm total span",
    ):
        assert literal in source

    # Worst reviewed protected-USB envelope. IQ is charged at VIN; downstream
    # output load alone is charged at the 3V3 rail.
    vin, vout = 5.25, 3.3
    output_current_a = 0.150
    iq_a = 0.000080
    theta_ja_c_per_w = 110.0
    ambient_c = 60.0
    design_junction_c = 125.0
    dissipation_w = (vin - vout) * output_current_a + vin * iq_a
    junction_c = ambient_c + theta_ja_c_per_w * dissipation_w
    assert junction_c == pytest.approx(92.2212, abs=1e-4)
    assert design_junction_c - junction_c == pytest.approx(32.7788, abs=1e-4)
    assert design_junction_c - junction_c >= 30.0
