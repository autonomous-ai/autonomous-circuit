"""Fast regression for the terminal keyboard's product-layout contract.

The fixture compiles the real board with routing disabled. That keeps the test
under 30 seconds while still exercising tscircuit's actual footprint transforms,
component layers, plated holes, board outline, and connector insertion geometry.
Routing policy remains declared in product.json and is graded by a full build.
"""

from __future__ import annotations

import json
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

sys.path.insert(0, str(REPO_ROOT / "packages" / "verify" / "src"))

from verifylib import intent  # noqa: E402
from verifylib.model import Board  # noqa: E402


@pytest.fixture(scope="session")
def product() -> dict:
    return json.loads((PROJECT_ROOT / "product.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def layout_elements(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    if not CLI.exists():
        pytest.skip("pinned tscircuit toolchain is not installed")

    work = tmp_path_factory.mktemp("terminal-keyboard-layout")
    for directory in ("blocks", "boards", "testbench"):
        shutil.copytree(PROJECT_ROOT / directory, work / directory)
    for filename in (
        "package.json",
        "tsconfig.json",
        "tscircuit.config.json",
    ):
        shutil.copy2(PROJECT_ROOT / filename, work / filename)

    env = dict(os.environ)
    bin_dir = TOOLCHAIN / "node_modules" / ".bin"
    env["PATH"] = f"{bin_dir}{os.pathsep}" + env.get("PATH", "")
    env["NODE_PATH"] = str(TOOLCHAIN / "node_modules")
    process = subprocess.run(
        [str(CLI), "build", "testbench/layout.tsx", "--disable-parts-engine"],
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
    assert product["assemblyTier"] == "standard"
    assert product["envelopeMm"] == [108, 58]
    layout = product["layout"]
    assert layout["boardSizeMm"] == [108, 58]
    assert layout["minCopperClearanceMm"] == 0.15
    assert layout["decoupling"] == {
        "maxDistanceMm": 2.0,
        "exclude": ["U1"],
    }
    assert layout["componentSides"][-1] == {"match": "*", "side": "bottom"}
    assert layout["edgeConnectors"] == [
        {
            "ref": "J1",
            "edge": "bottom",
            "alignment": "center",
            "edgeToleranceMm": 1.0,
            "centerToleranceMm": 0.1,
        }
    ]

    # These remain blocking intent and are backed by concrete source helpers;
    # the routed artifact still has to prove the resulting copper.
    assert layout["groundPlanes"] == {
        "layers": ["top", "bottom"],
        "maxRoutedLengthMm": 30,
        "maxFanoutLengthMm": 2,
        "stitchingPitchMm": 10,
    }
    assert layout["netClasses"] == [
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
            "name": "KEY_MATRIX_SIGNAL",
            "nets": ["ROW*", "COL*", "K*"],
            "minTrunkWidthMm": 0.25,
            "minNeckdownWidthMm": 0.25,
            "maxNeckdownLengthMm": 0,
        },
    ]


def test_source_uses_preferred_matrix_signal_width() -> None:
    board_source = (PROJECT_ROOT / "boards" / "main.tsx").read_text(encoding="utf-8")
    assert 'const MATRIX_TRACE_WIDTH = "0.25mm"' in board_source
    # The two loop-authored diode legs and the row/column MCU legs all carry
    # the shared width; runtime expansion yields 100 + 5 + 10 declarations.
    assert board_source.count("thickness={MATRIX_TRACE_WIDTH}") == 4


def test_source_uses_the_reusable_ground_plane_contract() -> None:
    board_source = (PROJECT_ROOT / "boards" / "main.tsx").read_text(encoding="utf-8")
    glue_source = (PROJECT_ROOT / "blocks" / "glue.tsx").read_text(encoding="utf-8")

    assert '<GndPlanes\n      layers={["top", "bottom"]}' in board_source
    assert 'fanoutLayer=' not in board_source
    assert "fanoutLayers.map((layer) => [layer, net])" in glue_source
    assert 'buttonVariant="compact"' in board_source
    stitch_block = board_source.split("const GND_STITCHING_VIAS = [", 1)[1].split(
        "]\n", 1
    )[0]
    assert stitch_block.count("{ x:") == 32
    assert "export const GND_FANOUT_PHASE_INDEX = 10" in glue_source
    assert "routingPhaseIndex={GND_FANOUT_PHASE_INDEX}" in glue_source

    # Only direct source-pad drops become phase-10 fanouts. The through-hole
    # debug return and any local two-port ties remain ordinary traces.
    direct_drop_files = (
        "rp2040-core/rp2040-core.tsx",
        "usb-c-data/usb-c-data.tsx",
        "ldo-3v3/ldo-3v3.tsx",
        "status-led/status-led.tsx",
    )
    direct_drop_sources = [
        (PROJECT_ROOT / "blocks" / path).read_text(encoding="utf-8")
        for path in direct_drop_files
    ]
    assert sum(source.count("<GndFanoutTrace") for source in direct_drop_sources) == 35
    direct_ground_ties = sum(source.count('to="net.GND"') for source in direct_drop_sources)
    assert direct_ground_ties == 4  # J1's four through-hole shell anchors


def test_source_uses_acyclic_mixed_width_power_trunks() -> None:
    board_source = (PROJECT_ROOT / "boards" / "main.tsx").read_text(encoding="utf-8")
    glue_source = (PROJECT_ROOT / "blocks" / "glue.tsx").read_text(encoding="utf-8")
    usb_source = (
        PROJECT_ROOT / "blocks" / "usb-c-data" / "usb-c-data.tsx"
    ).read_text(encoding="utf-8")
    ldo_source = (
        PROJECT_ROOT / "blocks" / "ldo-3v3" / "ldo-3v3.tsx"
    ).read_text(encoding="utf-8")

    assert board_source.count("<PowerTrunk") == 2
    assert 'source=".J1 > .VBUS1"' in board_source
    assert 'source=".U2 > .TAB"' in board_source
    assert board_source.count("trunkWidthMm={0.8}") == 2
    assert board_source.count("neckdownWidthMm={0.2}") == 2
    assert 'externalPowerTrunkPort="VBUS1"' in board_source
    assert 'externalPowerTrunkPort="TAB"' in board_source
    assert "source: string" in glue_source and "net: string" in glue_source
    assert "externalPowerTrunkPort?: \"VBUS1\" | \"VBUS2\"" in usb_source
    assert "externalPowerTrunkPort?: \"VOUT\" | \"TAB\"" in ldo_source


def test_compiled_power_trunks_are_wide_bottom_copper_and_topological_trees(
    layout_elements: list[dict],
) -> None:
    source_traces = {
        element["source_trace_id"]: element
        for element in layout_elements
        if element.get("type") == "source_trace"
    }
    source_nets = {
        element["name"]: element["source_net_id"]
        for element in layout_elements
        if element.get("type") == "source_net"
    }
    by_name = {element["name"]: element for element in source_traces.values()}
    for prefix, net_name in (("V5_MAIN", "V5"), ("V3V3_MAIN", "V3_3")):
        branch = [by_name[f"TR_{prefix}_{leg}"] for leg in ("IN", "TRUNK", "OUT")]
        assert [trace.get("connected_source_net_ids") or [] for trace in branch] == [
            [],
            [],
            [source_nets[net_name]],
        ]

    # The source blocks yielded exactly the selected edge; retaining either
    # ordinary trace would close the redundant graph cycle that originally
    # stalled two simultaneous trunks before autorouting even began.
    assert "TR_J1_vbus1" not in by_name
    assert "TR_J1_vbus2" in by_name
    assert "TR_U2_tab" not in by_name
    assert "TR_U2_vout" in by_name

    trunk_routes = {}
    for element in layout_elements:
        if element.get("type") != "pcb_trace":
            continue
        source = source_traces.get(element.get("source_trace_id"))
        if source and str(source.get("name", "")).endswith("_TRUNK"):
            trunk_routes[source["name"]] = element["route"]
    assert set(trunk_routes) == {"TR_V5_MAIN_TRUNK", "TR_V3V3_MAIN_TRUNK"}
    for route in trunk_routes.values():
        wires = [point for point in route if point.get("route_type") == "wire"]
        assert wires and all(point["width"] == pytest.approx(0.8) for point in wires)
        assert {point["layer"] for point in wires} == {"bottom"}

    components = _components(layout_elements)
    assert {
        name: (
            component["center"]["x"],
            component["center"]["y"],
            component["layer"],
            component["do_not_place"],
        )
        for name, component in components.items()
        if name in {"TP4", "TP5", "TP6", "TP7"}
    } == {
        "TP4": (-2.4, -19.1, "bottom", True),
        "TP5": (-22.93, -16.8, "bottom", True),
        "TP6": (-15.4, -21.8, "bottom", True),
        "TP7": (-4.7, -2.6, "bottom", True),
    }


def test_source_bounds_rp_and_usb_critical_phases_in_board_coordinates() -> None:
    board_source = (PROJECT_ROOT / "boards" / "main.tsx").read_text(encoding="utf-8")
    clock_region_source = board_source.split(
        "const RP_CLOCK_ROUTING_REGION = {", 1
    )[1].split("} as const", 1)[0]
    assert "minX: -54" in clock_region_source
    assert "maxX: 54" in clock_region_source
    assert "minY: -29" in clock_region_source
    assert "maxY: 29" in clock_region_source

    qspi_region_source = board_source.split(
        "const RP_QSPI_ROUTING_REGION = {", 1
    )[1].split("} as const", 1)[0]
    assert "minX: -11" in qspi_region_source
    assert "maxX: 10" in qspi_region_source
    assert "minY: -11" in qspi_region_source
    assert "maxY: 25" in qspi_region_source
    assert (
        '<autoroutingphase phaseIndex={0} region={RP_CLOCK_ROUTING_REGION} />'
        in board_source
    )
    assert (
        '<autoroutingphase phaseIndex={1} region={RP_QSPI_ROUTING_REGION} />'
        in board_source
    )

    usb_region_source = board_source.split(
        "const USB_ROUTING_REGION = {", 1
    )[1].split("} as const", 1)[0]
    assert "minX: -12" in usb_region_source
    assert "maxX: 12" in usb_region_source
    assert "minY: -29" in usb_region_source
    assert "maxY: 14" in usb_region_source
    assert (
        '<autoroutingphase phaseIndex={2} region={USB_ROUTING_REGION} />'
        in board_source
    )
    for net in ("USB_DP", "USB_DM", "USB_DP_CONN", "USB_DM_CONN"):
        assert f'<net name="{net}" routingPhaseIndex={{2}} />' in board_source

    rp_source = (
        PROJECT_ROOT / "blocks" / "rp2040-core" / "rp2040-core.tsx"
    ).read_text(encoding="utf-8")
    assert rp_source.count("routingPhaseIndex={0}") == 6
    assert rp_source.count("routingPhaseIndex={1}") == 5


def test_placement_compile_has_no_geometry_errors(layout_elements: list[dict]) -> None:
    findings = [
        element
        for element in layout_elements
        if str(element.get("type", "")).endswith(("_error", "_warning"))
    ]
    assert findings == []


def test_compiled_outline_clearance_and_population_match_intent(
    layout_elements: list[dict], product: dict
) -> None:
    board = next(
        element for element in layout_elements if element.get("type") == "pcb_board"
    )
    assert (board["width"], board["height"]) == (108, 58)
    assert board["min_trace_to_pad_edge_clearance"] == pytest.approx(0.15)
    assert board["min_via_edge_to_pad_edge_clearance"] == pytest.approx(0.15)

    components = _components(layout_elements)
    expected_top = {f"D{index}" for index in range(1, 51)} | {
        f"SW{index}" for index in range(10, 60)
    }
    actual_top = {
        name for name, component in components.items() if component.get("layer") == "top"
    }
    assert actual_top == expected_top
    assert len(actual_top) == 100
    assert {
        component.get("layer")
        for name, component in components.items()
        if name not in expected_top
    } == {"bottom"}

    mechanical_policy = {
        key: product["layout"][key]
        for key in (
            "boardSizeMm",
            "boardSizeToleranceMm",
            "minCopperClearanceMm",
            "componentSides",
            "edgeConnectors",
        )
    }
    assert intent.check(Board(layout_elements), mechanical_policy).findings == []


def test_compiled_rp_critical_cluster_is_inside_declared_global_region(
    layout_elements: list[dict],
) -> None:
    components = _components(layout_elements)
    region = {"minX": -11.0, "maxX": 10.0, "minY": -11.0, "maxY": 25.0}
    for name in ("U3", "U4", "Y1", "R11", "C15", "C16"):
        component = components[name]
        center = component["center"]
        half_width = component["width"] / 2
        half_height = component["height"] / 2
        assert center["x"] - half_width >= region["minX"], name
        assert center["x"] + half_width <= region["maxX"], name
        assert center["y"] - half_height >= region["minY"], name
        assert center["y"] + half_height <= region["maxY"], name


def test_usb_and_board_owned_debug_port_are_physically_reachable(
    layout_elements: list[dict],
) -> None:
    components = _components(layout_elements)
    connector = components["J1"]
    assert connector["layer"] == "bottom"
    assert connector["center"]["x"] == pytest.approx(0)
    board_bottom = -29.0
    body_bottom = connector["center"]["y"] - connector["height"] / 2
    assert body_bottom >= board_bottom
    assert connector["cable_insertion_center"] == pytest.approx(
        {"x": 0.0, "y": -29.001602528}
    )

    expected_debug_centres = {
        "TP1": (3.46, 16.0),
        "TP2": (6.0, 16.0),
        "TP3": (8.54, 16.0),
    }
    for name, expected in expected_debug_centres.items():
        component = components[name]
        assert component["layer"] == "bottom"
        assert component["do_not_place"] is True
        assert (component["center"]["x"], component["center"]["y"]) == pytest.approx(
            expected
        )


def test_compact_boot_and_reset_buttons_use_the_verified_jlc_part(
    layout_elements: list[dict],
) -> None:
    components = _components(layout_elements)
    source_components = _source_components(layout_elements)

    for name in ("SW2", "SW3"):
        source = source_components[name]
        assert source["supplier_part_numbers"] == {"jlcpcb": ["C2828561"]}
        assert source["manufacturer_part_number"] == "TPT-2C1"

        component = components[name]
        assert component["layer"] == "bottom"
        assert component["width"] == pytest.approx(4.1999)
        assert component["height"] == pytest.approx(1.524)
