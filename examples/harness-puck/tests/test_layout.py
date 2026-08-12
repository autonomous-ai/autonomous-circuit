"""Fast regression for the harness puck's reusable layout contracts.

The real composition is compiled with routing disabled.  This still exercises
the pinned footprints, board/group transforms, mounting-hole keepouts, side
assignment, connector insertion geometry, explicit plane pours, and stitching
via placement.  A full source-fresh build separately proves routed copper.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
TOOLCHAIN = REPO_ROOT / "toolchain"
CLI = TOOLCHAIN / "node_modules" / ".bin" / "tscircuit-cli"
CLI_MAIN = TOOLCHAIN / "node_modules" / "@tscircuit" / "cli" / "dist" / "cli" / "main.js"
TSX_LOADER = TOOLCHAIN / "node_modules" / "tsx" / "dist" / "loader.mjs"

sys.path.insert(0, str(REPO_ROOT / "packages" / "verify" / "src"))
sys.path.insert(0, str(REPO_ROOT / "skills" / "circuitcode"))

from verifylib import intent  # noqa: E402
from verifylib.model import Board  # noqa: E402
from circuitlib.helpers import (  # noqa: E402
    board_plan,
    usb_power_budget_for_plan,
)


@pytest.fixture(scope="session")
def product() -> dict:
    return json.loads((PROJECT_ROOT / "product.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def layout_elements(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    node = shutil.which("node")
    if not CLI.exists() or not CLI_MAIN.exists() or not TSX_LOADER.exists() or node is None:
        pytest.skip("pinned tscircuit toolchain is not installed")

    work = tmp_path_factory.mktemp("harness-puck-layout")
    for directory in ("blocks", "boards", "testbench"):
        shutil.copytree(PROJECT_ROOT / directory, work / directory)
    for filename in ("package.json", "tsconfig.json", "tscircuit.config.json"):
        shutil.copy2(PROJECT_ROOT / filename, work / filename)

    env = dict(os.environ)
    bin_dir = TOOLCHAIN / "node_modules" / ".bin"
    env["PATH"] = f"{bin_dir}{os.pathsep}" + env.get("PATH", "")
    env["NODE_PATH"] = str(TOOLCHAIN / "node_modules")
    # Invoke the bundled JS through tsx's loader directly. The convenience
    # entrypoint launches tsx's IPC coordinator, which is unavailable in
    # filesystem-sandboxed CI even though ordinary TypeScript evaluation is.
    process = subprocess.run(
        [
            node,
            "--import",
            str(TSX_LOADER),
            str(CLI_MAIN),
            "build",
            "testbench/layout.tsx",
            "--disable-parts-engine",
        ],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    artifact = work / "dist" / "testbench" / "layout" / "circuit.json"
    assert artifact.exists(), (
        f"placement compile produced no circuit.json (exit={process.returncode})\n"
        f"stdout:\n{process.stdout[-2000:]}\nstderr:\n{process.stderr[-2000:]}"
    )
    return json.loads(artifact.read_text(encoding="utf-8"))


def _components(elements: list[dict]) -> dict[str, dict]:
    source_names = {
        element["source_component_id"]: element["name"]
        for element in elements
        if element.get("type") == "source_component"
    }
    return {
        source_names[element["source_component_id"]]: element
        for element in elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") in source_names
    }


def _source_components(elements: list[dict]) -> dict[str, dict]:
    return {
        element["name"]: element
        for element in elements
        if element.get("type") == "source_component"
    }


def test_product_declares_the_reviewed_layout_contract(product: dict) -> None:
    assert product["assembly"] is True
    assert product["assemblyTier"] == "economic"
    assert product["envelopeMm"] == [70, 70]
    assert product["layout"] == {
        "boardSizeMm": [70, 70],
        "boardSizeToleranceMm": 0.01,
        "minCopperClearanceMm": 0.15,
        "decoupling": {
            "maxDistanceMm": 2.0,
            "exclude": ["U1"],
        },
        "componentSides": [{"match": "*", "side": "top"}],
        "edgeConnectors": [
            {
                "ref": "J1",
                "edge": "bottom",
                "alignment": "center",
                "edgeToleranceMm": 1.0,
                "centerToleranceMm": 0.1,
            }
        ],
        "groundPlanes": {
            "layers": ["top", "bottom"],
            "maxRoutedLengthMm": 25,
            "maxFanoutLengthMm": 2.0,
            "stitchingPitchMm": 10,
        },
        "netClasses": [
            {
                "name": "POWER",
                "nets": ["V5", "V3_3"],
                "minTrunkWidthMm": 0.8,
                "minNeckdownWidthMm": 0.2,
                "maxNeckdownLengthMm": 3,
                "minViaOuterDiameterMm": 0.8,
                "minViaHoleDiameterMm": 0.5,
            },
            {
                "name": "USB_ATTACH_POWER",
                "nets": ["VBUS_RAW"],
                "minTrunkWidthMm": 0.8,
                "minNeckdownWidthMm": 0.2,
                "maxNeckdownLengthMm": 3,
                "minViaOuterDiameterMm": 0.8,
                "minViaHoleDiameterMm": 0.5,
            },
            {
                "name": "HARNESS_SIGNAL",
                "nets": ["BTN_*", "LED_DATA_*", "PX_*", "USB_POWER_FAULT"],
                "minTrunkWidthMm": 0.25,
                "minNeckdownWidthMm": 0.25,
                "maxNeckdownLengthMm": 0,
            },
            {
                "name": "DEBUG_SIGNAL",
                "nets": ["SWCLK", "SWD"],
                "minTrunkWidthMm": 0.25,
                "minNeckdownWidthMm": 0.15,
                "maxNeckdownLengthMm": 1.0,
            },
        ],
    }
    assert product["powerBudget"] == {
        "usb": {
            "rawVbusNet": "VBUS_RAW",
            "protectedVbusNet": "V5",
            "rawAttachCapacitanceMaxUf": 10,
            "sourceCurrentMaxMa": 500,
            "fixedOperationalLoadMa": 113,
            "currentLimiter": {
                "ref": "U7",
                "inputPin": "IN",
                "outputPin": "OUT",
                    "lcsc": "C55266",
                    "minTripMa": 400.6,
                    "maxTripMa": 500,
                    "settingPin": "ILIM",
                    "settingResistor": {
                        "ref": "R31",
                        "lcsc": "C32297",
                        "resistanceOhms": 59000,
                        "returnNet": "GND",
                    },
                },
            "firmwareLimitedLoads": [
                {
                    "match": ["D1[0-7]"],
                    "perDevicePhysicalPeakMa": 60,
                    "aggregateOperationalMaxMa": 280,
                }
            ],
        }
    }


def test_product_usb_budget_is_generated_from_the_exact_board_plan(product: dict) -> None:
    plan = board_plan(
        capabilities=["mcu", "rgb-pixels"],
        counts={"ws2812-chain": 8},
        firmware_load_caps_ma={"ws2812-chain": 280},
        exposed_nets=["SWCLK", "SWD"],
    )
    assert plan.source_budget["physical_peak_ma"] == pytest.approx(593)
    assert plan.source_budget["fixed_operational_load_ma"] == pytest.approx(113)
    assert plan.source_budget["operational_load_ma"] == pytest.approx(393)
    assert product["powerBudget"] == usb_power_budget_for_plan(
        plan,
        firmware_load_matches={"ws2812-chain": "D1[0-7]"},
    )


def test_source_uses_reusable_critical_route_and_plane_contracts() -> None:
    source = (PROJECT_ROOT / "boards" / "main.tsx").read_text(encoding="utf-8")
    assert source.count("region={RP_CRITICAL_ROUTING_REGION}") == 2
    assert "<GndPlanes" in source
    assert 'fanoutLayer=' not in source
    assert "...polarRing(34, 16)" in source
    assert "...polarRing(23.5, 12)" in source
    assert source.count("<GndFanoutTrace") == 4
    assert 'buttonVariant="compact"' in source
    assert 'debugSwclkEscapeRef="TP8"' in source
    assert 'debugSwdEscapeRef="TP9"' in source
    assert "debugSignalTraceWidthMm={0.25}" in source
    assert 'name="H1" diameter={2.2} pcbX={0} pcbY={30}' in source

    rp_source = (
        PROJECT_ROOT / "blocks" / "rp2040-core" / "rp2040-core.tsx"
    ).read_text(encoding="utf-8")
    assert rp_source.count("routingPhaseIndex={0}") == 6
    assert rp_source.count("routingPhaseIndex={1}") == 5
    assert 'thickness="0.15mm"' in rp_source
    assert "thickness={`${debugSignalTraceWidthMm}mm`}" in rp_source


def test_pixel_power_and_level_shift_are_sourced_reusable_contracts() -> None:
    source = (PROJECT_ROOT / "boards" / "main.tsx").read_text(encoding="utf-8")
    assert source.count("<Ldo3v3") == 1
    assert "V3_3_LED" not in source
    assert "<Ws2812LevelShifter" in source
    assert "inputNet={LED_DATA_3V3}" in source
    assert "outputNet={LED_DATA_5V}" in source
    assert 'from=".U3 > .GPIO16" to={`net.${LED_DATA_3V3}`}' in source
    assert source.count("<PowerTrunk") == 2
    assert 'name="V5_MAIN"' in source
    assert 'name="V3V3_MAIN"' in source
    assert source.count("trunkWidthMm={0.8}") == 2
    assert source.count("neckdownWidthMm={0.2}") == 2
    assert 'const SIGNAL_TRACE_WIDTH = "0.25mm"' in source
    assert source.count("thickness={SIGNAL_TRACE_WIDTH}") == 7

    shifter = (
        PROJECT_ROOT
        / "blocks"
        / "ws2812-level-shifter"
        / "ws2812-level-shifter.tsx"
    ).read_text(encoding="utf-8")
    assert 'supplierPartNumbers={{ jlcpcb: ["C7484"] }}' in shifter
    assert 'manufacturerPartNumber="SN74AHCT1G125DBVR"' in shifter
    assert 'pin1: ["OE"]' in shifter
    assert 'pin2: ["A"]' in shifter
    assert 'pin3: ["GND"]' in shifter
    assert 'pin4: ["Y"]' in shifter
    assert 'pin5: ["VCC"]' in shifter
    assert '<GndFanoutTrace name={`TR_${u}_oe`}' in shifter
    assert 'capacitance="100nF"' in shifter


def test_parts_lock_freezes_the_level_shifter_and_is_catalog_checked() -> None:
    lock = json.loads((PROJECT_ROOT / "parts.json").read_text(encoding="utf-8"))
    assert lock["generator"] == "parts-book"
    assert lock["summary"] == {
        "lines": 22,
        "basic": 13,
        "extended": 9,
        "checked": 22,
    }
    c7484 = next(part for part in lock["parts"] if part["lcsc"] == "C7484")
    assert c7484["mfr"] == "SN74AHCT1G125DBVR"
    assert c7484["package"] == "SOT-23-5"
    assert c7484["stock_checked"] == "2026-08-11"
    assert c7484["source"] in {"jlcsearch", "jlcsearch-cached"}
    assert c7484["stock"] > 0
    assert c7484["blocks"] == ["ws2812-level-shifter"]


def test_placement_compile_has_no_geometry_errors(layout_elements: list[dict]) -> None:
    findings = [
        element
        for element in layout_elements
        if str(element.get("type", "")).endswith(("_error", "_warning"))
    ]
    assert findings == []


def test_compiled_mechanics_population_and_planes_match_intent(
    layout_elements: list[dict], product: dict
) -> None:
    board_element = next(
        element for element in layout_elements if element.get("type") == "pcb_board"
    )
    assert (board_element["width"], board_element["height"]) == (70, 70)
    assert board_element["min_trace_to_pad_edge_clearance"] == pytest.approx(0.15)
    assert board_element["min_via_edge_to_pad_edge_clearance"] == pytest.approx(0.15)

    components = _components(layout_elements)
    assert {component.get("layer") for component in components.values()} == {"top"}

    board = Board(layout_elements)
    assert intent.check(board, product["layout"]).findings == []
    assert len(list(board.of_type("pcb_via"))) == 27
    assert {pour.get("layer") for pour in board.of_type("pcb_copper_pour")} == {
        "top",
        "bottom",
    }


def test_each_ring_bypass_is_local_to_its_rotated_vdd_pad(
    layout_elements: list[dict],
) -> None:
    sources = _source_components(layout_elements)
    source_ports = [
        element for element in layout_elements
        if element.get("type") == "source_port"
    ]
    pcb_ports = {
        element["source_port_id"]: element
        for element in layout_elements
        if element.get("type") == "pcb_port"
    }
    source_traces = {
        element["name"]: element
        for element in layout_elements
        if element.get("type") == "source_trace"
    }
    v5_id = next(
        element["source_net_id"]
        for element in layout_elements
        if element.get("type") == "source_net" and element.get("name") == "V5"
    )

    def source_port_id(ref: str, pin: str) -> str:
        source_id = sources[ref]["source_component_id"]
        return next(
            port["source_port_id"]
            for port in source_ports
            if port.get("source_component_id") == source_id
            and (
                port.get("name") == pin
                or pin in (port.get("port_hints") or [])
            )
        )

    distances: dict[str, float] = {}
    for index in range(8):
        pixel = f"D{10 + index}"
        capacitor = f"C{40 + index}"
        vdd_id = source_port_id(pixel, "VDD")
        cap_id = source_port_id(capacitor, "pin1")

        local = source_traces[f"TR_{pixel}_vdd"]
        assert set(local.get("connected_source_port_ids") or []) == {
            vdd_id, cap_id,
        }
        assert not local.get("connected_source_net_ids")
        assert float(local["min_trace_thickness"]) == pytest.approx(0.2)
        assert float(local["max_length"]) == pytest.approx(2.0)

        boundary = source_traces[f"TR_{capacitor}_v"]
        assert boundary.get("connected_source_port_ids") == [cap_id]
        assert boundary.get("connected_source_net_ids") == [v5_id]
        assert float(boundary["min_trace_thickness"]) == pytest.approx(0.8)

        routed = [
            element for element in layout_elements
            if element.get("type") == "pcb_trace"
            and (
                element.get("source_trace_id") == local["source_trace_id"]
                or element.get("connection_name") == local["source_trace_id"]
            )
        ]
        assert len(routed) == 1, (pixel, len(routed))
        route = routed[0].get("route") or []
        assert not [point for point in route if point.get("route_type") == "via"]
        wires = [point for point in route if point.get("route_type") == "wire"]
        assert {float(point["width"]) for point in wires} == {0.2}
        assert {point.get("layer") for point in wires} == {"top"}

        coordinates: list[tuple[float, float]] = []
        for point in wires:
            xy = (float(point["x"]), float(point["y"]))
            if not coordinates or math.dist(xy, coordinates[-1]) > 1e-9:
                coordinates.append(xy)
        length = sum(math.dist(a, b) for a, b in zip(coordinates, coordinates[1:]))
        distances[pixel] = length
        expected_endpoints = (
            (float(pcb_ports[vdd_id]["x"]), float(pcb_ports[vdd_id]["y"])),
            (float(pcb_ports[cap_id]["x"]), float(pcb_ports[cap_id]["y"])),
        )
        assert coordinates[0] == pytest.approx(expected_endpoints[0])
        assert coordinates[-1] == pytest.approx(expected_endpoints[1])
    assert set(round(value, 6) for value in distances.values()) == {1.8}, distances


def test_rp_critical_components_fit_the_board_global_phase_regions(
    layout_elements: list[dict],
) -> None:
    components = _components(layout_elements)
    critical = {"minX": -18, "maxX": 12, "minY": -2, "maxY": 29}

    def inside(name: str, region: dict[str, float]) -> None:
        component = components[name]
        center = component["center"]
        assert center["x"] - component["width"] / 2 >= region["minX"], name
        assert center["x"] + component["width"] / 2 <= region["maxX"], name
        assert center["y"] - component["height"] / 2 >= region["minY"], name
        assert center["y"] + component["height"] / 2 <= region["maxY"], name

    for name in ("U3", "U4", "Y1", "R11", "C15", "C16", "TP8", "TP9"):
        inside(name, critical)


def test_usb_debug_and_service_buttons_are_physically_reachable(
    layout_elements: list[dict],
) -> None:
    components = _components(layout_elements)
    connector = components["J1"]
    assert connector["center"]["x"] == pytest.approx(0)
    assert connector["cable_insertion_center"]["y"] == pytest.approx(-35.0516, abs=1e-3)

    for name, expected_x in (("TP1", 11.46), ("TP2", 14.0), ("TP3", 16.54)):
        component = components[name]
        assert component["do_not_place"] is True
        assert component["layer"] == "top"
        assert (component["center"]["x"], component["center"]["y"]) == pytest.approx(
            (expected_x, 12.0)
        )

    for name, expected_x in (("TP8", -1.0), ("TP9", -0.6)):
        component = components[name]
        assert component["do_not_place"] is True
        assert component["layer"] == "top"
        assert (component["center"]["x"], component["center"]["y"]) == pytest.approx(
            (expected_x, 6.75)
        )

    sources = _source_components(layout_elements)
    for name in ("SW2", "SW3"):
        assert sources[name]["supplier_part_numbers"] == {"jlcpcb": ["C2828561"]}
        assert sources[name]["manufacturer_part_number"] == "TPT-2C1"
