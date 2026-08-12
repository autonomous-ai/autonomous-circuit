"""Graded testbenches for every v1 golden block.

Per block: the bench must build CLEAN (zero *_error AND zero *_warning
elements — the golden bar), the topology assertions must hold (netlist
traversal over source_trace elements), the pinned BOM must be intact
(exact LCSC numbers — the block is the part lock), and the committed
summary snapshot must match (``CIRCUIT_UPDATE_SNAPSHOTS=1`` regenerates
after a reviewed change).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import SNAPSHOT_DIR

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "verify" / "src"))
from verifylib import intent  # noqa: E402
from verifylib.model import Board  # noqa: E402

BLOCK_IDS = [
    "usb-c-power",
    "usb-power-entry",
    "ldo-3v3",
    "status-led",
    "sw-tact",
    "i2c-bus",
    "rp2040-core",
    "usb-c-data",
    "sensor-bme280",
    "ws2812-level-shifter",
    "ws2812-chain",
]

# refdes → exact pinned LCSC number per bench (subset assertion).
PINNED_BOM: dict[str, dict[str, str]] = {
    "usb-c-power": {
        "J1": "C165948", "R1": "C25905", "R2": "C25905",
        "U1": "C2687116", "C1": "C52923",
    },
    "usb-power-entry": {
        "U7": "C55266", "C24": "C1525", "R31": "C32297",
        "R32": "C25741",
    },
    "ldo-3v3": {"U2": "C500795", "C2": "C19702", "C3": "C19702"},
    "status-led": {"LED1": "C2297", "R20": "C11702"},
    "sw-tact": {"SW1": "C318884"},
    "i2c-bus": {"R8": "C25900", "R9": "C25900"},
    "rp2040-core": {
        "U3": "C2040", "U4": "C97521", "Y1": "C20625731",
        "R11": "C11702", "R12": "C25744", "R13": "C11702",
        "SW2": "C318884", "SW3": "C318884",
        "C4": "C1525", "C12": "C1525", "C13": "C1525",
        "C15": "C1548", "C17": "C15850", "C25": "C52923",
        "C26": "C52923",
    },
    "usb-c-data": {
        "J1": "C165948", "R1": "C25905", "R2": "C25905",
        "R3": "C25100", "R4": "C25100", "U1": "C2687116", "C1": "C52923",
    },
    "sensor-bme280": {"U5": "C92489", "C18": "C1525", "C19": "C1525"},
    "ws2812-level-shifter": {"U6": "C7484", "C20": "C1525"},
    "ws2812-chain": {
        "D10": "C2761795", "D11": "C2761795", "D12": "C2761795", "D13": "C2761795",
        "C40": "C1525", "C41": "C1525", "C42": "C1525", "C43": "C1525",
        "R30": "C25104",
    },
}

# (a, b) pairs that must be in the same electrical group.
CONNECTED: dict[str, list[tuple[str, str]]] = {
    "usb-c-power": [
        ("J1.VBUS1", "net.VBUS_RAW"),
        ("J1.VBUS2", "net.VBUS_RAW"),
        ("J1.GND1", "net.GND"),
        ("J1.SHELL1", "net.GND"),
        ("J1.CC1", "R1.pin1"),
        ("R1.pin2", "net.GND"),
        ("J1.CC2", "R2.pin1"),
        ("U1.VBUS", "net.VBUS_RAW"),
        ("U1.IO1", "J1.CC1"),
        ("U1.IO2", "J1.CC2"),
        ("C1.pin1", "net.VBUS_RAW"),
        ("C1.pin2", "net.GND"),
    ],
    "usb-power-entry": [
        ("U7.IN", "net.VBUS_RAW"),
        ("U7.EN", "net.VBUS_RAW"),
        ("U7.OUT", "net.V5"),
        ("U7.GND", "net.GND"),
        ("U7.ILIM", "R31.pin1"),
        ("R31.pin2", "net.GND"),
        ("C24.pin1", "net.VBUS_RAW"),
        ("C24.pin2", "net.GND"),
        ("U7.FAULT", "net.USB_POWER_FAULT"),
        ("TP10.pin1", "net.USB_POWER_FAULT"),
        ("R32.pin1", "net.USB_POWER_FAULT"),
        ("R32.pin2", "net.V3_3"),
    ],
    "ldo-3v3": [
        ("U2.VIN", "net.V5"),
        ("U2.VOUT", "net.V3_3"),
        ("U2.GND1", "net.GND"),
        ("U2.GND2", "net.GND"),
        ("C2.pin1", "net.V5"),
        ("C3.pin1", "net.V3_3"),
    ],
    "status-led": [
        ("R20.pin1", "net.V3_3"),
        ("R20.pin2", "LED1.anode"),
        ("LED1.cathode", "net.GND"),
    ],
    "sw-tact": [
        ("SW1.pin1", "net.BTN1"),
        ("SW1.pin2", "net.BTN1"),
        ("SW1.pin3", "net.GND"),
        ("SW1.pin4", "net.GND"),
    ],
    "i2c-bus": [
        ("R8.pin1", "net.V3_3"),
        ("R8.pin2", "net.SDA"),
        ("R9.pin1", "net.V3_3"),
        ("R9.pin2", "net.SCL"),
    ],
    "rp2040-core": [
        ("U3.IOVDD1", "net.V3_3"),
        ("U3.USB_VDD", "net.V3_3"),
        ("U3.VREG_VOUT", "net.DVDD"),
        ("U3.DVDD1", "net.DVDD"),
        ("U3.GND", "net.GND"),
        ("U3.TESTEN", "net.GND"),
        ("U3.USB_DP", "net.USB_DP"),
        ("U3.USB_DM", "net.USB_DM"),
        ("U3.SWCLK", "net.SWCLK"),
        ("U3.SWD", "net.SWD"),
        ("TP1.pin1", "net.SWCLK"),
        ("TP2.pin1", "net.SWD"),
        ("TP3.pin1", "net.GND"),
        ("N1.pin1", "net.SWCLK"),
        ("N2.pin1", "net.SWD"),
        ("U4.CS", "U3.QSPI_SS"),
        ("U4.CLK", "U3.QSPI_SCLK"),
        ("U4.IO0", "U3.QSPI_SD0"),
        ("U4.VCC", "net.V3_3"),
        ("Y1.pin1", "U3.XIN"),
        ("U3.XOUT", "R11.pin1"),
        ("R11.pin2", "Y1.pin3"),
        ("R12.pin1", "net.V3_3"),
        ("R12.pin2", "U3.RUN"),
        ("SW3.pin1", "U3.RUN"),
        ("SW2.pin3", "net.GND"),
        ("R13.pin1", "U3.QSPI_SS"),
        ("C12.pin1", "net.DVDD"),
        ("C13.pin1", "net.DVDD"),
        ("C25.pin1", "net.DVDD"),
        ("C26.pin1", "net.V3_3"),
        ("C17.pin1", "net.V3_3"),
    ],
    "usb-c-data": [
        ("J1.VBUS1", "net.VBUS_RAW"),
        ("J1.CC1", "R1.pin1"),
        ("J1.DP1", "J1.DP2"),
        ("J1.DM1", "J1.DM2"),
        ("U1.IO1", "J1.DP1"),
        ("U1.IO2", "J1.DM1"),
        ("R3.pin1", "J1.DP1"),
        ("R3.pin2", "net.USB_DP"),
        ("R4.pin1", "J1.DM1"),
        ("R4.pin2", "net.USB_DM"),
    ],
    "sensor-bme280": [
        ("U5.VDD", "net.V3_3"),
        ("U5.VDDIO", "net.V3_3"),
        ("U5.CSB", "net.V3_3"),
        ("U5.SDO", "net.GND"),
        ("U5.SDA", "net.SDA"),
        ("U5.SCL", "net.SCL"),
        ("U5.GND1", "net.GND"),
        ("C18.pin1", "net.V3_3"),
    ],
    "ws2812-level-shifter": [
        ("U6.OE", "net.GND"),
        ("U6.A", "net.LED_DATA_3V3"),
        ("U6.GND", "net.GND"),
        ("U6.Y", "net.LED_DATA_5V"),
        ("U6.VCC", "net.V5"),
        ("C20.pin1", "net.V5"),
        ("C20.pin2", "net.GND"),
    ],
    "ws2812-chain": [
        # the damping resistor sits between the driving net and the first pixel
        ("R30.pin1", "net.LED_DATA_5V"),
        ("R30.pin2", "D10.DIN"),
        # the chain hands data pixel to pixel
        ("D10.DOUT", "D11.DIN"),
        ("D11.DOUT", "D12.DIN"),
        ("D12.DOUT", "D13.DIN"),
        # every pixel powered, and every pixel has its own local cap
        ("D10.VDD", "net.V5"),
        ("D13.VDD", "net.V5"),
        ("D13.GND", "net.GND"),
        ("C40.pin1", "net.V5"),
        ("C43.pin1", "net.V5"),
        ("C43.pin2", "net.GND"),
    ],
}

# Pairs that must NOT be connected (would be a short / wrong wiring).
ISOLATED: dict[str, list[tuple[str, str]]] = {
    "usb-c-power": [("net.VBUS_RAW", "net.GND"), ("J1.CC1", "J1.CC2")],
    "usb-power-entry": [
        ("net.VBUS_RAW", "net.V5"),
        ("net.V5", "net.GND"),
        ("net.USB_POWER_FAULT", "net.V3_3"),
    ],
    "ldo-3v3": [("net.V5", "net.V3_3"), ("net.V3_3", "net.GND")],
    "status-led": [("net.V3_3", "net.GND"), ("R20.pin1", "LED1.anode")],
    "sw-tact": [("net.BTN1", "net.GND")],
    "i2c-bus": [("net.SDA", "net.SCL")],
    "rp2040-core": [
        ("net.V3_3", "net.GND"),
        ("net.V3_3", "net.DVDD"),
        ("net.USB_DP", "net.USB_DM"),
        ("TP1.pin1", "TP2.pin1"),
        ("U3.XIN", "Y1.pin3"),          # series R must separate XIN/XOUT sides
        ("U3.QSPI_SS", "net.GND"),      # BOOTSEL open at rest
    ],
    "usb-c-data": [
        ("net.USB_DP", "net.USB_DM"),
        ("J1.DP1", "net.USB_DP"),       # series R separates connector from MCU side
        ("net.VBUS_RAW", "net.GND"),
    ],
    "sensor-bme280": [("net.SDA", "net.SCL"), ("net.V3_3", "net.GND")],
    "ws2812-level-shifter": [
        ("net.V5", "net.GND"),
        ("net.LED_DATA_3V3", "net.LED_DATA_5V"),
        ("U6.A", "U6.Y"),
    ],
    "ws2812-chain": [
        ("net.V5", "net.GND"),
        # the GPIO must reach the first pixel only through the resistor
        ("net.LED_DATA_5V", "D10.DIN"),
        # data must not backfeed: a pixel's input is not its neighbour's input
        ("D10.DIN", "D11.DIN"),
    ],
}

LAYER_COMPONENTS: dict[str, set[str]] = {
    "usb-power-entry": {"U7", "C24", "R31", "R32", "TP10"},
    "usb-c-data": {
        "J1", "R1", "R2", "R3", "R4", "U1", "C1",
        "N3", "N4", "N15", "N16",
    },
    "ldo-3v3": {"U2", "C2", "C3"},
    "status-led": {"LED1", "R20"},
    "sw-tact": {"SW1"},
    "sensor-bme280": {"U5", "C18", "C19"},
    "ws2812-level-shifter": {"U6", "C20"},
    "ws2812-chain": {
        "R30", *{f"D{i}" for i in range(10, 14)},
        *{f"C{i}" for i in range(40, 44)},
        *{f"N{i}" for i in range(30, 34)},
    },
    "rp2040-core": {
        "U3", "U4", "Y1", "R11", "R12", "R13", "SW2", "SW3",
        "TP1", "TP2", "TP3", "N1", "N2",
        # N18 is the intentional opposite-face DVDD junction. Its dedicated
        # rail test below requires that layer transition and the .8/.5 via;
        # this component-side assertion covers only the same-face nodes.
        *{f"N{i}" for i in range(5, 18)},
        *{f"C{i}" for i in range(4, 18)}, "C25", "C26",
    },
}

# The all-block bottom bench needs collision-free hidden-node references.
# Keep this separate from the individual top benches so the layer assertion
# cannot accidentally find another block's same-named node and pass while the
# intended component is missing or on the wrong face.
BOTTOM_LAYER_COMPONENTS = {
    **LAYER_COMPONENTS,
    "usb-c-data": {
        "J1", "R1", "R2", "R3", "R4", "U1", "C1",
        "N21", "N22", "N23", "N24",
    },
}


def _assert_block_layer(graph, bench: str, names: set[str], layer: str) -> None:
    """Assert the compiled PCB objects, not just the JSX props."""
    g = graph(bench)
    source_ids = {
        source["source_component_id"]
        for name, source in g.components.items()
        if name in names
    }
    assert len(source_ids) == len(names), (
        f"{bench}: expected {sorted(names)}, found "
        f"{sorted(name for name in g.components if name in names)}"
    )
    pcb_components = [
        element for element in g.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") in source_ids
    ]
    assert len(pcb_components) == len(names)
    assert {component.get("layer") for component in pcb_components} == {layer}

    pcb_component_ids = {
        component["pcb_component_id"] for component in pcb_components
    }
    smd_pads = [
        element for element in g.elements
        if element.get("type") == "pcb_smtpad"
        and element.get("pcb_component_id") in pcb_component_ids
    ]
    assert smd_pads, f"{bench}: expected compiled SMD pads"
    assert {pad.get("layer") for pad in smd_pads} == {layer}

    # Through-hole pads are intentionally present on both copper layers, but
    # their owning connector/test-point component still has a placement side.
    plated_holes = [
        element for element in g.elements
        if element.get("type") == "pcb_plated_hole"
        and element.get("pcb_component_id") in pcb_component_ids
    ]
    assert all(layer in (hole.get("layers") or []) for hole in plated_holes)


@pytest.mark.parametrize("bench,names", LAYER_COMPONENTS.items())
def test_composed_blocks_default_to_top(graph, bench, names):
    _assert_block_layer(graph, bench, names, "top")


@pytest.mark.parametrize("names", BOTTOM_LAYER_COMPONENTS.values())
def test_composed_blocks_propagate_bottom_layer(graph, names):
    _assert_block_layer(graph, "layer-propagation-bottom", names, "bottom")


@pytest.mark.parametrize("bench", BLOCK_IDS)
def test_bench_builds_clean(graph, bench):
    g = graph(bench)
    errs = [(e["type"], str(e.get("message", ""))[:200]) for e in g.errors()]
    assert not errs, f"{bench}: error elements in circuit.json: {errs}"
    warns = [(e["type"], str(e.get("message", ""))[:200]) for e in g.warnings()]
    assert not warns, f"{bench}: golden benches must be warning-free: {warns}"


@pytest.mark.parametrize("bench", BLOCK_IDS)
def test_pinned_bom(graph, bench):
    g = graph(bench)
    lcsc = g.lcsc()
    for refdes, expected in PINNED_BOM[bench].items():
        assert lcsc.get(refdes) == expected, (
            f"{bench}: {refdes} should pin {expected}, got {lcsc.get(refdes)}"
        )


def test_rp2040_debug_port_is_dnp_unsourced_furniture(graph):
    """SWD access is DNP copper and needs no supplier part."""
    g = graph("rp2040-core")
    lcsc = g.lcsc()
    for name in ("TP1", "TP2", "TP3"):
        source = g.components.get(name)
        assert source is not None, f"rp2040-core: missing debug pad {name}"
        assert source.get("ftype") == "simple_test_point"
        assert name not in lcsc, f"rp2040-core: {name} must not require a part"
        pcb = next(
            e for e in g.elements
            if e.get("type") == "pcb_component"
            and e.get("source_component_id") == source["source_component_id"]
        )
        assert pcb.get("do_not_place") is True, (
            f"rp2040-core: {name} must be marked do-not-place"
        )


def test_rp2040_debug_boundaries_are_masked_internal_copper(graph):
    """Every authored waypoint is hidden copper, never assembly furniture."""
    g = graph("rp2040-core")
    hidden_names = {"N1", "N2", *{f"N{i}" for i in range(5, 19)}}
    for name in hidden_names:
        source = g.components.get(name)
        assert source is not None
        assert source.get("ftype") == "simple_chip"
        assert source.get("manufacturer_part_number") == "MASKED_COPPER_NODE"
        assert name not in g.lcsc()
        source_ports = [
            element for element in g.elements
            if element.get("type") == "source_port"
            and element.get("source_component_id") == source["source_component_id"]
        ]
        assert len(source_ports) == 1
        pcb = next(
            element for element in g.elements
            if element.get("type") == "pcb_component"
            and element.get("source_component_id") == source["source_component_id"]
        )
        assert pcb.get("do_not_place") is True
        pads = [
            element for element in g.elements
            if element.get("type") == "pcb_smtpad"
            and element.get("pcb_component_id") == pcb["pcb_component_id"]
        ]
        assert len(pads) == 1
        assert pads[0].get("is_covered_with_solder_mask") is True
        assert not [
            element for element in g.elements
            if element.get("type") == "pcb_solder_paste"
            and element.get("pcb_component_id") == pcb["pcb_component_id"]
        ]
        assert not [
            element for element in g.elements
            if str(element.get("type") or "").startswith("pcb_silkscreen")
            and element.get("pcb_component_id") == pcb["pcb_component_id"]
        ]

    testpoints = {
        name for name, source in g.components.items()
        if source.get("ftype") == "simple_test_point"
    }
    assert testpoints == {"TP1", "TP2", "TP3"}
    assert {
        name for name, source in g.components.items()
        if source.get("manufacturer_part_number") == "MASKED_COPPER_NODE"
    } == hidden_names


def test_masked_nodes_survive_bom_cpl_kicad_and_real_gerber_boundaries(
    graph, farm, tmp_path,
):
    """Prove the hidden-node contract across both independent export paths."""
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "packages" / "circuitpy" / "src"))
    from circuitpy import fab as fab_mod, toolchain as toolchain_mod
    from circuitpy.kicad_normalize import _balanced_spans, normalize_for_fab

    found_kicad = toolchain_mod.kicad_cli_exe()
    if found_kicad is None:
        pytest.skip("kicad-cli unavailable")
    kicad = Path(found_kicad)

    g = graph("rp2040-core")
    hidden = {
        name for name, source in g.components.items()
        if source.get("manufacturer_part_number") == "MASKED_COPPER_NODE"
    }
    assert hidden == {"N1", "N2", *{f"N{i}" for i in range(5, 19)}}
    circuit_path = tmp_path / "circuit.json"
    shutil.copy2(farm.circuit_json_path("rp2040-core"), circuit_path)

    toolchain = repo / "toolchain" / "node_modules"
    cli_bin = toolchain / ".bin"
    cli_main = toolchain / "@tscircuit" / "cli" / "dist" / "cli" / "main.js"
    tsx_loader = toolchain / "tsx" / "dist" / "loader.mjs"
    node = shutil.which("node")
    assert node is not None
    assert cli_main.is_file() and tsx_loader.is_file()
    env = dict(os.environ)
    env["PATH"] = f"{cli_bin}{os.pathsep}" + env.get("PATH", "")
    export_prefix = [
        node,
        "--import",
        str(tsx_loader),
        str(cli_main),
        "export",
        str(circuit_path),
    ]
    subprocess.run(
        [*export_prefix, "-f", "gerbers", "-o", "source.zip"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    excluded = fab_mod.do_not_place_designators(g.elements)
    assert hidden <= excluded
    with zipfile.ZipFile(tmp_path / "source.zip") as packet:
        bom = fab_mod.parse_exporter_bom(
            packet.read("bom.csv").decode("utf-8", "replace")
        )
        cpl = packet.read("pick_and_place.csv").decode("utf-8", "replace")
    filtered_bom = fab_mod.exclude_designators_from_bom(bom, excluded)
    filtered_cpl = fab_mod.exclude_designators_from_cpl(cpl, excluded)
    assert hidden.isdisjoint({row["designator"] for row in filtered_bom})
    assert all(
        not re.search(rf"(?m)^{re.escape(name)},", filtered_cpl)
        for name in hidden
    )

    subprocess.run(
        [*export_prefix, "-f", "kicad_pcb", "-o", "board.kicad_pcb"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    board_path = tmp_path / "board.kicad_pcb"
    normalized = normalize_for_fab(board_path, fab_mod.get_profile("jlcpcb"))
    assert normalized.hidden_nodes_normalized == len(hidden)
    board_text = board_path.read_text(encoding="utf-8")
    hidden_footprints = [
        board_text[start:end]
        for start, end in _balanced_spans(board_text, "(footprint")
        if "MASKED_COPPER_NODE" in board_text[start:end]
    ]
    assert len(hidden_footprints) == len(hidden)
    for footprint in hidden_footprints:
        assert "(hide yes)" in footprint
        assert "F.Paste" not in footprint and "B.Paste" not in footprint
        assert "F.Mask" not in footprint and "B.Mask" not in footprint
        assert "exclude_from_pos_files" in footprint
        assert "exclude_from_bom" in footprint

    gerber_dir = tmp_path / "gerbers"
    gerber_dir.mkdir()
    kicad_env = dict(os.environ)
    kicad_env["KICAD_CONFIG_HOME"] = str(tmp_path / "kicad-config")
    subprocess.run(
        [
            str(kicad), "pcb", "export", "gerbers", "-o",
            str(gerber_dir) + os.sep, str(board_path),
        ],
        env=kicad_env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    for layer in ("F_Paste.gtp", "F_Mask.gts", "F_Silkscreen.gto"):
        plotted = (gerber_dir / f"board-{layer}").read_text(encoding="utf-8")
        assert "%TO.C,C4*%" in plotted  # X2 component attribution is present.
        for name in hidden:
            assert f"%TO.C,{name}*%" not in plotted


def test_rp2040_debug_qfn_escapes_are_fixed_then_widen(graph):
    """Fine-pitch copper leaves perpendicular before the 0.25mm board route."""
    g = graph("rp2040-critical-routing")
    source_traces = {
        element["name"]: element["source_trace_id"]
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    for signal, boundary in (("swclk", "N1"), ("swd", "N2")):
        escape_id = source_traces[f"TR_U3_{signal}_escape"]
        escape = next(
            element
            for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("connection_name") == escape_id
        )
        wires = [
            point for point in escape.get("route") or []
            if point.get("route_type") == "wire"
        ]
        assert wires
        assert all(float(point["width"]) == pytest.approx(0.15) for point in wires)
        assert max(float(point["x"]) for point in wires) - min(
            float(point["x"]) for point in wires
        ) <= 1e-9

        boundary_id = source_traces[f"TR_{boundary}"]
        widened = [
            element
            for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("connection_name") == boundary_id
        ]
        assert widened
        assert all(
            float(point["width"]) == pytest.approx(0.25)
            for element in widened
            for point in element.get("route") or []
            if point.get("route_type") == "wire"
        )


@pytest.mark.parametrize(
    ("bench", "expected_layer"),
    [("rp2040-core", "top"), ("layer-propagation-bottom", "bottom")],
)
def test_rp2040_dvdd_bypass_paths_escape_perpendicular_without_vias(
    graph, bench, expected_layer,
):
    """The three placement-owned core-rail loops are bounded real copper."""
    g = graph(bench)
    decoupling_findings = [
        finding
        for finding in intent.check(
            Board(g.elements), {"decoupling": {"maxDistanceMm": 2.0}}
        ).findings
        if str(finding.get("kind") or "").startswith(
            "layout_intent_decoupling_"
        )
        and str(finding.get("part") or "").startswith(("U3.", "U4."))
    ]
    assert decoupling_findings == [], (bench, decoupling_findings)
    source_traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    expected = {
        "TR_U3_vregout_C25": ("U3.VREG_VOUT", "C25.pin1"),
        "TR_U3_dvdd1_C13": ("U3.DVDD1", "C13.pin1"),
        "TR_U3_dvdd2_C12": ("U3.DVDD2", "C12.pin1"),
    }
    for name, (start, end) in expected.items():
        source = source_traces[name]
        assert set(source.get("connected_source_port_ids") or []) == {
            g.port_id(start), g.port_id(end)
        }
        assert not source.get("connected_source_net_ids")
        assert float(source["min_trace_thickness"]) == pytest.approx(0.2)
        assert float(source["max_length"]) == pytest.approx(2.0)

        routed = [
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("source_trace_id") == source["source_trace_id"]
        ]
        assert len(routed) == 1
        route = routed[0].get("route") or []
        wires = [point for point in route if point.get("route_type") == "wire"]
        assert wires
        assert {float(point["width"]) for point in wires} == {0.2}
        assert {point.get("layer") for point in wires} == {expected_layer}
        assert not [point for point in route if point.get("route_type") == "via"]

        coordinates: list[tuple[float, float]] = []
        for point in wires:
            coordinate = (float(point["x"]), float(point["y"]))
            if not coordinates or math.dist(coordinate, coordinates[-1]) > 1e-9:
                coordinates.append(coordinate)
        assert len(coordinates) >= 2
        assert coordinates[0][0] == pytest.approx(coordinates[1][0])
        length = sum(math.dist(a, b) for a, b in zip(coordinates, coordinates[1:]))
        assert 0 < length <= 2.0, (name, length)


@pytest.mark.parametrize(
    ("bench", "component_layer", "rail_layer", "nodes"),
    [
        ("rp2040-core", "top", "bottom", ("N15", "N16", "N17", "N18")),
        (
            "layer-propagation-bottom", "bottom", "top",
            ("N15", "N16", "N17", "N18"),
        ),
    ],
)
def test_rp2040_dvdd_tree_has_scoped_power_vias_and_one_boundary(
    graph, bench, component_layer, rail_layer, nodes,
):
    """The core rail is one acyclic .2/.8 tree with three real .8/.5 vias."""
    g = graph(bench)
    source_traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    left_node, right_node, south_node, junction_node = nodes
    expected_edges = {
        "TR_DVDD_C13_NECK": ("C13.pin1", f"{left_node}.pin1", 0.2),
        "TR_DVDD_C25_NECK": ("C25.pin1", f"{right_node}.pin1", 0.2),
        "TR_DVDD_C12_NECK": ("C12.pin1", f"{south_node}.pin1", 0.2),
        "TR_DVDD_LEFT_JUNCTION": (
            f"{left_node}.pin1", f"{junction_node}.pin1", 0.8,
        ),
        "TR_DVDD_RIGHT_JUNCTION": (
            f"{right_node}.pin1", f"{junction_node}.pin1", 0.8,
        ),
        "TR_DVDD_SOUTH_JUNCTION": (
            f"{south_node}.pin1", f"{junction_node}.pin1", 0.8,
        ),
    }
    tree_keys: set[str] = set()
    for name, (start, end, width) in expected_edges.items():
        source = source_traces[name]
        assert set(source.get("connected_source_port_ids") or []) == {
            g.port_id(start), g.port_id(end),
        }
        assert not source.get("connected_source_net_ids")
        assert float(source["min_trace_thickness"]) == pytest.approx(width)
        tree_keys.add(str(source["subcircuit_connectivity_map_key"]))
    assert len(tree_keys) == 1

    dvdd_net = next(
        element for element in g.elements
        if element.get("type") == "source_net" and element.get("name") == "DVDD"
    )
    boundary = source_traces["TR_DVDD_ESCAPE"]
    assert boundary.get("connected_source_port_ids") == [
        g.port_id(f"{junction_node}.pin1")
    ]
    assert boundary.get("connected_source_net_ids") == [dvdd_net["source_net_id"]]
    assert float(boundary["min_trace_thickness"]) == pytest.approx(0.8)
    assert str(boundary["subcircuit_connectivity_map_key"]) in tree_keys

    # The cap/node graph has seven physical ports and six internal edges:
    # exactly a tree. The marked N18 edge is its sole named-net attachment.
    physical_ports = {
        port
        for name in expected_edges
        for port in (source_traces[name].get("connected_source_port_ids") or [])
    }
    assert len(physical_ports) == 7
    assert len(expected_edges) == len(physical_ports) - 1
    assert [
        trace for trace in source_traces.values()
        if trace.get("connected_source_net_ids") == [dvdd_net["source_net_id"]]
        and set(trace.get("connected_source_port_ids") or []) & physical_ports
    ] == [boundary]

    for node in nodes:
        source_id = g.components[node]["source_component_id"]
        pcb_component = next(
            element for element in g.elements
            if element.get("type") == "pcb_component"
            and element.get("source_component_id") == source_id
        )
        expected_layer = rail_layer if node == junction_node else component_layer
        assert pcb_component.get("layer") == expected_layer

    for name in (
        "TR_DVDD_LEFT_JUNCTION",
        "TR_DVDD_RIGHT_JUNCTION",
        "TR_DVDD_SOUTH_JUNCTION",
    ):
        source_id = source_traces[name]["source_trace_id"]
        routed = [
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("source_trace_id") == source_id
        ]
        assert len(routed) == 1
        route = routed[0].get("route") or []
        wires = [point for point in route if point.get("route_type") == "wire"]
        route_vias = [point for point in route if point.get("route_type") == "via"]
        assert wires and {float(point["width"]) for point in wires} == {0.8}
        assert {point.get("layer") for point in wires} == {
            component_layer, rail_layer,
        }
        assert len(route_vias) == 1
        assert float(route_vias[0]["via_diameter"]) == pytest.approx(0.8)
        assert float(route_vias[0]["via_hole_diameter"]) == pytest.approx(0.5)
        physical_vias = [
            element for element in g.elements
            if element.get("type") == "pcb_via"
            and element.get("pcb_trace_id") == routed[0]["pcb_trace_id"]
        ]
        assert len(physical_vias) == 1
        assert float(physical_vias[0]["outer_diameter"]) == pytest.approx(0.8)
        assert float(physical_vias[0]["hole_diameter"]) == pytest.approx(0.5)


def test_rp2040_regulator_caps_have_official_values_and_independent_branches(graph):
    """Freeze the RP2040 datasheet values to the pin-specific local trees."""
    g = graph("rp2040-core")
    traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    expected = {
        # One 100nF bypass at each DVDD toe.
        "TR_U3_dvdd1_C13": ("U3.DVDD1", "C13.pin1", "C13", 100e-9),
        "TR_U3_dvdd2_C12": ("U3.DVDD2", "C12.pin1", "C12", 100e-9),
        # VREG_OUT and VREG_IN each require their own 1uF capacitor.
        "TR_U3_vregout_C25": ("U3.VREG_VOUT", "C25.pin1", "C25", 1e-6),
        "TR_U3_vregin_C26": ("U3.VREG_IN", "C26.pin1", "C26", 1e-6),
    }
    for name, (supply, cap_pin, cap, capacitance) in expected.items():
        assert set(traces[name].get("connected_source_port_ids") or []) == {
            g.port_id(supply), g.port_id(cap_pin),
        }
        assert not traces[name].get("connected_source_net_ids")
        assert float(g.components[cap]["capacitance"]) == pytest.approx(capacitance)

    # ADC_AVDD keeps a separate 100nF part; it must not silently reuse either
    # regulator capacitor while the four values above still look plausible.
    assert set(traces["TR_U3_adcavdd_C9"].get("connected_source_port_ids") or []) == {
        g.port_id("U3.ADC_AVDD"), g.port_id("C9.pin1"),
    }
    assert float(g.components["C9"]["capacitance"]) == pytest.approx(100e-9)
    assert "TR_U3_vregout_C13" not in traces


def test_rp2040_debug_port_does_not_block_the_bom_gate():
    """The exporter emits TP rows; the assembly gate must treat them as copper."""
    import sys

    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "packages" / "circuitpy" / "src"))
    from circuitpy.checks import bom_gate

    rows = [
        {
            "designator": name,
            "comment": "",
            "value": "",
            "footprint": "",
            "lcsc": "",
        }
        for name in ("TP1", "TP2", "TP3")
    ]
    assert bom_gate(rows, assembly=True) == []


def test_status_led_compiles_explicit_rail_and_signal_widths(graph):
    """GPIO-driven indicators must not inherit the old generic 0.15mm route."""
    rail = graph("status-led")
    signal = graph("status-led-signal")

    def width(g, trace_name: str) -> float:
        trace = next(
            element for element in g.elements
            if element.get("type") == "source_trace"
            and element.get("name") == trace_name
        )
        return float(trace["min_trace_thickness"])

    assert width(rail, "TR_R20_rail") == pytest.approx(0.2)
    assert width(rail, "TR_R20_led") == pytest.approx(0.25)
    assert width(signal, "TR_R20_rail") == pytest.approx(0.25)
    assert width(signal, "TR_R20_led") == pytest.approx(0.25)


def test_ldo_uses_local_cap_trees_and_one_physical_output_boundary(graph):
    """AP7361 local copper and board rail ownership cannot form a V3 cycle."""
    normal = graph("ldo-3v3")
    external = graph("ldo-3v3-external-vout")
    assert not normal.errors() and not normal.warnings()
    assert not external.errors() and not external.warnings()

    u2_ports = {
        element["name"]
        for element in normal.elements
        if element.get("type") == "source_port"
        and element.get("source_component_id")
        == normal.components["U2"]["source_component_id"]
    }
    assert u2_ports == {"VIN", "GND1", "VOUT", "GND2"}
    assert "TAB" not in u2_ports

    def source_traces(g):
        return {
            element["name"]: element
            for element in g.elements
            if element.get("type") == "source_trace"
        }

    normal_traces = source_traces(normal)
    external_traces = source_traces(external)
    assert set(normal_traces["TR_U2_vin_C2"]["connected_source_port_ids"]) == {
        normal.port_id("U2.VIN"), normal.port_id("C2.pin1")
    }
    assert set(normal_traces["TR_U2_vout_C3"]["connected_source_port_ids"]) == {
        normal.port_id("U2.VOUT"), normal.port_id("C3.pin1")
    }
    assert float(normal_traces["TR_U2_vin_C2"]["min_trace_thickness"]) == pytest.approx(0.2)
    assert float(normal_traces["TR_U2_vin_C2"]["max_length"]) == pytest.approx(2.0)
    assert float(normal_traces["TR_U2_vout_C3"]["min_trace_thickness"]) == pytest.approx(0.8)
    assert float(normal_traces["TR_U2_vout_C3"]["max_length"]) == pytest.approx(2.0)
    assert "TR_C3_V3_3_boundary" in normal_traces
    assert "TR_C3_V3_3_boundary" not in external_traces
    assert external_traces["TR_BOARD_V3_VOUT_BOUNDARY"]["connected_source_port_ids"] == [
        external.port_id("U2.VOUT")
    ]
    assert len(external_traces["TR_BOARD_V3_VOUT_BOUNDARY"]["connected_source_net_ids"]) == 1

    # The selected E package has two physical ground contacts. Its broad tab
    # must terminate into GND and must never be offered as an output selector.
    assert set(normal_traces["TR_U2_gnd1"]["connected_source_port_ids"]) == {
        normal.port_id("U2.GND1")
    }
    assert set(normal_traces["TR_U2_tab_gnd"]["connected_source_port_ids"]) == {
        normal.port_id("U2.GND2")
    }
    for name in ("TR_U2_gnd1", "TR_U2_tab_gnd"):
        assert normal_traces[name]["connected_source_net_ids"] == [
            normal.nets["GND"]["source_net_id"]
        ]

    def routed_width_and_length(g, trace_name: str) -> tuple[set[float], float, int]:
        source = source_traces(g)[trace_name]
        routed = [
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and (
                element.get("source_trace_id") == source["source_trace_id"]
                or element.get("connection_name") == source["source_trace_id"]
            )
        ]
        assert routed
        widths: set[float] = set()
        length = 0.0
        for element in routed:
            points = [
                point for point in element.get("route") or []
                if isinstance(point.get("x"), (int, float))
                and isinstance(point.get("y"), (int, float))
            ]
            widths.update(
                float(point["width"]) for point in points
                if point.get("route_type") == "wire"
                and isinstance(point.get("width"), (int, float))
            )
            length += sum(
                math.dist((float(a["x"]), float(a["y"])),
                          (float(b["x"]), float(b["y"])))
                for a, b in zip(points, points[1:])
            )
        via_count = sum(
            1 for element in g.elements
            if element.get("type") == "pcb_via"
            and element.get("pcb_trace_id") in {
                trace.get("pcb_trace_id") for trace in routed
            }
        )
        return widths, length, via_count

    in_widths, in_length, in_vias = routed_width_and_length(normal, "TR_U2_vin_C2")
    assert in_widths == {0.2}
    assert 0 < in_length <= 2.0
    assert in_vias == 0
    out_widths, out_length, out_vias = routed_width_and_length(normal, "TR_U2_vout_C3")
    assert out_widths == {0.8}
    assert 0 < out_length <= 2.0
    assert out_vias == 0


def test_power_trunk_preserves_wide_rail_and_narrow_escapes(graph):
    """The source-to-net tree must not promote its trunk into the escapes."""
    g = graph("power-trunk")
    errs = [(e["type"], str(e.get("message", ""))[:200]) for e in g.errors()]
    warns = [(e["type"], str(e.get("message", ""))[:200]) for e in g.warnings()]
    assert not errs
    assert not warns

    source_components = {
        str(element.get("source_component_id")): str(element.get("name") or "")
        for element in g.elements
        if element.get("type") == "source_component"
    }
    centers = {
        source_components.get(str(element.get("source_component_id")), ""):
        element.get("center")
        for element in g.elements
        if element.get("type") == "pcb_component"
    }
    assert centers["TP901"] == {"x": -7.5, "y": -3}
    assert centers["TP902"] == {"x": 7.5, "y": -3}
    assert centers["TP905"] == {"x": -7.5, "y": 3}
    assert centers["TP906"] == {"x": 7.5, "y": 3}

    sources = {
        str(element.get("source_trace_id")): str(element.get("name") or "")
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    source_nets = {
        str(element.get("source_net_id")): str(element.get("name") or "")
        for element in g.elements
        if element.get("type") == "source_net"
    }
    routed: list[tuple[str, str, set[float], float]] = []
    for element in g.elements:
        if element.get("type") != "pcb_trace":
            continue
        source_trace_name = sources.get(str(element.get("source_trace_id")), "")
        net_name = source_nets.get(str(element.get("connection_name")), "")
        widths = {
            float(point["width"])
            for point in element.get("route") or []
            if point.get("route_type") == "wire"
            and isinstance(point.get("width"), (int, float))
        }
        route = [
            point
            for point in element.get("route") or []
            if isinstance(point.get("x"), (int, float))
            and isinstance(point.get("y"), (int, float))
        ]
        length = sum(
            ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
            for a, b in zip(route, route[1:])
        )
        routed.append((source_trace_name, net_name, widths, length))

    # Each source tree has one fixed 15mm trunk plus one contracted 1.5mm
    # named-net attachment. Both are deliberately .8mm; the only narrow
    # copper is the two <=2mm source escapes. Two simultaneous trees are the
    # regression: redundant source-net cycles used to stall before routing.
    wide = [
        (source_trace_name, net_name, length)
        for source_trace_name, net_name, widths, length in routed
        if widths == {0.8}
    ]
    narrow = [
        (source_trace_name, net_name, length)
        for source_trace_name, net_name, widths, length in routed
        if widths == {0.2}
    ]
    assert sorted(wide) == [
        ("", "V3_3", 1.5),
        ("", "V5", 1.5),
        ("TR_V3V3_MAIN_TRUNK", "", 15.0),
        ("TR_V5_MAIN_TRUNK", "", 15.0),
    ]
    assert sorted(narrow) == [
        ("TR_V3V3_MAIN_IN", "", 1.5),
        ("TR_V5_MAIN_IN", "", 1.5),
    ]

    source_traces = [
        element
        for element in g.elements
        if element.get("type") == "source_trace"
        and str(element.get("name") or "").startswith("TR_V5_MAIN_")
    ]
    assert len(source_traces) == 3
    # Only the OUT leg owns the named rail. The source and the two boundary
    # pads form one tree branch instead of a redundant same-net cycle.
    assert [
        element.get("name")
        for element in source_traces
        if element.get("connected_source_net_ids")
    ] == ["TR_V5_MAIN_OUT"]

    v3v3_source_traces = [
        element
        for element in g.elements
        if element.get("type") == "source_trace"
        and str(element.get("name") or "").startswith("TR_V3V3_MAIN_")
    ]
    assert len(v3v3_source_traces) == 3
    assert [
        element.get("name")
        for element in v3v3_source_traces
        if element.get("connected_source_net_ids")
    ] == ["TR_V3V3_MAIN_OUT"]


def test_compact_tactile_variant_is_real_two_pin_orderable_copper(graph):
    g = graph("sw-tact-compact")
    assert not g.errors()
    assert not g.warnings()
    assert g.lcsc()["SW1"] == "C2828561"
    assert g.connected("SW1.pin1", "net.BTN1")
    assert g.connected("SW1.pin2", "net.GND")
    assert not g.connected("net.BTN1", "net.GND")
    switch = g.components["SW1"]
    source_id = switch["source_component_id"]
    ports = [
        element
        for element in g.elements
        if element.get("type") == "source_port"
        and element.get("source_component_id") == source_id
    ]
    assert len(ports) == 2
    pcb = next(
        element
        for element in g.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") == source_id
    )
    assert float(pcb["width"]) < 5.0
    assert float(pcb["height"]) < 3.0


def test_ws2812_level_shifter_freezes_exact_c7484_footprint(graph):
    """The part lock includes supplier copper, not just a matching package name."""
    g = graph("ws2812-level-shifter")
    source_id = g.components["U6"]["source_component_id"]
    pcb_component = next(
        element
        for element in g.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") == source_id
    )
    center = pcb_component["center"]
    pads = [
        element
        for element in g.elements
        if element.get("type") == "pcb_smtpad"
        and element.get("pcb_component_id") == pcb_component["pcb_component_id"]
    ]
    assert len(pads) == 5
    actual = sorted(
        (
            tuple(element.get("port_hints") or []),
            round(float(element["x"]) - float(center["x"]), 6),
            round(float(element["y"]) - float(center["y"]), 6),
            round(float(element["width"]), 7),
            round(float(element["height"]), 7),
        )
        for element in pads
    )
    assert actual == sorted([
        (("pin1",), 1.300099, -0.949960, 1.0999978, 0.5999988),
        (("pin2",), 1.300099, 0.0, 1.0999978, 0.5999988),
        (("pin3",), 1.300099, 0.949960, 1.0999978, 0.5999988),
        (("pin4",), -1.300099, 0.949960, 1.0999978, 0.5999988),
        (("pin5",), -1.300099, -0.949960, 1.0999978, 0.5999988),
    ])


def test_usb_power_entry_freezes_ti_pinout_and_exact_c55266_footprint(graph):
    """The current-limit topology must sit on the supplier's audited copper."""
    g = graph("usb-power-entry")
    source_id = g.components["U7"]["source_component_id"]
    pcb_component = next(
        element
        for element in g.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") == source_id
    )
    center = pcb_component["center"]
    pads = [
        element
        for element in g.elements
        if element.get("type") == "pcb_smtpad"
        and element.get("pcb_component_id") == pcb_component["pcb_component_id"]
    ]
    assert len(pads) == 6
    actual = sorted(
        (
            tuple(element.get("port_hints") or []),
            round(float(element["x"]) - float(center["x"]), 6),
            round(float(element["y"]) - float(center["y"]), 6),
            round(float(element["width"]), 7),
            round(float(element["height"]), 7),
        )
        for element in pads
    )
    assert actual == sorted([
        (("pin1",), 1.350010, -0.949960, 1.0999978, 0.5999988),
        (("pin2",), 1.350010, 0.0, 1.0999978, 0.5999988),
        (("pin3",), 1.350010, 0.949960, 1.0999978, 0.5999988),
        (("pin4",), -1.350010, 0.949960, 1.0999978, 0.5999988),
        (("pin5",), -1.350010, 0.0, 1.0999978, 0.5999988),
        (("pin6",), -1.350010, -0.949960, 1.0999978, 0.5999988),
    ])
    expected_hints = {
        "IN": {"IN", "pin1", "1"},
        "GND": {"GND", "pin2", "2"},
        "EN": {"EN", "pin3", "3"},
        "FAULT": {"FAULT", "pin4", "4"},
        "ILIM": {"ILIM", "pin5", "5"},
        "OUT": {"OUT", "pin6", "6"},
    }
    for logical, hints in expected_hints.items():
        source_port = next(
            element for element in g.elements
            if element.get("type") == "source_port"
            and element.get("source_port_id") == g.port_id(f"U7.{logical}")
        )
        assert hints.issubset(set(source_port.get("port_hints") or []))


def test_usb_power_entry_freezes_limit_bypass_and_observable_fault(graph):
    g = graph("usb-power-entry")
    assert float(g.components["R31"]["resistance"]) == pytest.approx(59000.0)
    assert float(g.components["R32"]["resistance"]) == pytest.approx(100000.0)
    assert float(g.components["C24"]["capacitance"]) == pytest.approx(100e-9)

    pcb_ports = {
        element["source_port_id"]: element
        for element in g.elements
        if element.get("type") == "pcb_port"
    }
    in_port = pcb_ports[g.port_id("U7.IN")]
    cap_port = pcb_ports[g.port_id("C24.pin1")]
    assert math.dist(
        (float(in_port["x"]), float(in_port["y"])),
        (float(cap_port["x"]), float(cap_port["y"])),
    ) <= 2.0

    tp = g.components["TP10"]
    assert tp.get("ftype") == "simple_test_point"
    assert "TP10" not in g.lcsc()
    pcb_tp = next(
        element for element in g.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id") == tp["source_component_id"]
    )
    assert pcb_tp.get("do_not_place") is True
    assert g.connected("U7.FAULT", "R32.pin1")
    assert g.connected("U7.FAULT", "TP10.pin1")
    assert g.connected("U7.FAULT", "net.USB_POWER_FAULT")
    assert g.connected("R32.pin2", "net.V3_3")
    assert not g.connected("net.USB_POWER_FAULT", "net.V3_3")
    traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    assert float(traces["TR_U7_fault"]["min_trace_thickness"]) == pytest.approx(0.15)
    assert float(traces["TR_U7_fault"]["max_length"]) == pytest.approx(1.0)
    assert float(traces["TR_R32_fault"]["min_trace_thickness"]) == pytest.approx(0.25)
    assert float(traces["TR_TP10_fault"]["min_trace_thickness"]) == pytest.approx(0.25)
    assert float(traces["TR_R32_pullup"]["min_trace_thickness"]) == pytest.approx(0.2)
    assert float(traces["TR_R32_pullup"]["max_length"]) == pytest.approx(3.0)


@pytest.mark.parametrize("bench", ["usb-c-power", "usb-c-data"])
def test_usb_connector_exposes_raw_vbus_with_one_uf_attach_cap(graph, bench):
    g = graph(bench)
    assert "VBUS_RAW" in g.nets
    assert "V5" not in g.nets
    assert float(g.components["C1"]["capacitance"]) == pytest.approx(1e-6)


@pytest.mark.parametrize("bench", ["usb-c-power", "usb-c-data"])
def test_usb_connector_vbus_is_one_measured_authored_power_tree(graph, bench):
    """Both reversible pads share one .8mm trunk and one named boundary."""
    g = graph(bench)
    source_traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    expected = {
        "TR_J1_vbus1_neck": (0.2, 2.0),
        "TR_J1_vbus_trunk": (0.8, None),
        "TR_J1_vbus2_neck": (0.2, 2.0),
        "TR_J1_vbus_rail": (0.8, None),
        "TR_J1_vbus_boundary": (0.8, None),
        "TR_U1_vbus": (0.2, 3.0),
        "TR_C1_vbus": (0.2, 3.0),
    }
    if bench == "usb-c-data":
        expected["TR_U1_vbus_backbone"] = (0.8, None)
    for name, (width, max_length) in expected.items():
        trace = source_traces[name]
        assert float(trace["min_trace_thickness"]) == pytest.approx(width)
        if max_length is not None:
            assert float(trace["max_length"]) == pytest.approx(max_length)
    assert source_traces["TR_J1_vbus1_neck"]["connected_source_port_ids"] == [
        g.port_id("J1.VBUS1"), g.port_id("N3.pin1")
    ]
    assert source_traces["TR_J1_vbus2_neck"]["connected_source_port_ids"] == [
        g.port_id("J1.VBUS2"), g.port_id("N4.pin1")
    ]
    assert source_traces["TR_J1_vbus_trunk"]["connected_source_port_ids"] == [
        g.port_id("N3.pin1"), g.port_id("N4.pin1")
    ]
    assert source_traces["TR_J1_vbus_rail"]["connected_source_port_ids"] == [
        g.port_id("N4.pin1"), g.port_id("N15.pin1")
    ]
    u1_vbus_node = "N16" if bench == "usb-c-data" else "N15"
    assert source_traces["TR_U1_vbus"]["connected_source_port_ids"] == [
        g.port_id("U1.VBUS"), g.port_id(f"{u1_vbus_node}.pin1")
    ]
    assert source_traces["TR_C1_vbus"]["connected_source_port_ids"] == [
        g.port_id("C1.pin1"), g.port_id("N15.pin1")
    ]
    if bench == "usb-c-data":
        assert source_traces["TR_U1_vbus_backbone"]["connected_source_port_ids"] == [
            g.port_id("N15.pin1"), g.port_id("N16.pin1")
        ]
    assert source_traces["TR_J1_vbus_boundary"]["connected_source_port_ids"] == [
        g.port_id("N15.pin1")
    ]
    named = [
        trace["name"] for trace in source_traces.values()
        if trace["name"].startswith("TR_J1_vbus")
        and trace.get("connected_source_net_ids")
    ]
    assert named == ["TR_J1_vbus_boundary"]

    node_centers: dict[str, tuple[float, float]] = {}
    node_names = ["N3", "N4", "N15"]
    if bench == "usb-c-data":
        node_names.append("N16")
    for name in node_names:
        source = g.components[name]
        assert source.get("manufacturer_part_number") == "MASKED_COPPER_NODE"
        node_pcb = next(
            element for element in g.elements
            if element.get("type") == "pcb_component"
            and element.get("source_component_id") == source["source_component_id"]
        )
        assert node_pcb.get("do_not_place") is True
        node_centers[name] = (
            float(node_pcb["center"]["x"]), float(node_pcb["center"]["y"])
        )
        pad = next(
            element for element in g.elements
            if element.get("type") == "pcb_smtpad"
            and element.get("pcb_component_id") == node_pcb["pcb_component_id"]
        )
        assert pad.get("is_covered_with_solder_mask") is True
    # Final connector geometry moves the masked VBUS nodes to local x=+/-3.2
    # so both 0.25mm CC routes clear them at the production 0.15mm floor.
    assert node_centers["N3"][0] - node_centers["N4"][0] == pytest.approx(6.4)
    assert node_centers["N3"][1] == pytest.approx(node_centers["N4"][1])
    bottom_bench = {
        "usb-c-power": "usb-c-power-bottom",
        "usb-c-data": "usb-c-data-bottom",
    }[bench]
    bottom = graph(bottom_bench)
    bottom_centers = {}
    for name in node_names:
        source_id = bottom.components[name]["source_component_id"]
        component = next(
            element for element in bottom.elements
            if element.get("type") == "pcb_component"
            and element.get("source_component_id") == source_id
        )
        bottom_centers[name] = (
            float(component["center"]["x"]),
            float(component["center"]["y"]),
        )
        assert bottom_centers[name] == pytest.approx(
            (-node_centers[name][0], node_centers[name][1])
        )
    # The data variant places the raw rail beside its rotated ESD/cap cluster;
    # the power-only variant keeps the same electrical tree but has a smaller
    # connector-local placement.  Both relative geometries are intentional
    # block contracts, not one board-global coordinate.
    expected_rail_delta = {
        "usb-c-power": (3.7, 8.8),
        "usb-c-data": (0.4, 4.35),
    }[bench]
    assert node_centers["N15"][0] - node_centers["N4"][0] == pytest.approx(
        expected_rail_delta[0]
    )
    assert node_centers["N15"][1] - node_centers["N4"][1] == pytest.approx(
        expected_rail_delta[1]
    )
    if bench == "usb-c-data":
        # U1's VBUS pad cannot reach N15 within the 3mm fine-neck budget
        # without detouring around IO1B.  N16 is the placement-owned local
        # landing; a separate .8/.5mm top-bottom-top backbone joins it to N15.
        assert node_centers["N16"][0] - node_centers["N15"][0] == pytest.approx(2.8)
        assert node_centers["N16"][1] - node_centers["N15"][1] == pytest.approx(0.95)

    trunk_source_id = source_traces["TR_J1_vbus_trunk"]["source_trace_id"]
    trunk = next(
        element for element in g.elements
        if element.get("type") == "pcb_trace"
        and element.get("source_trace_id") == trunk_source_id
    )
    wires = [point for point in trunk["route"] if point.get("route_type") == "wire"]
    vias = [point for point in trunk["route"] if point.get("route_type") == "via"]
    assert wires and {float(point["width"]) for point in wires} == {0.8}
    assert len(vias) == 2
    assert {float(point["via_diameter"]) for point in vias} == {0.8}
    assert {float(point["via_hole_diameter"]) for point in vias} == {0.5}
    assert math.dist(
        (float(wires[0]["x"]), float(wires[0]["y"])),
        (float(wires[-1]["x"]), float(wires[-1]["y"])),
    ) == pytest.approx(6.4)

    if bench == "usb-c-data":
        backbone_source_id = source_traces["TR_U1_vbus_backbone"]["source_trace_id"]
        backbone = next(
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("source_trace_id") == backbone_source_id
        )
        backbone_wires = [
            point for point in backbone["route"]
            if point.get("route_type") == "wire"
        ]
        backbone_vias = [
            point for point in backbone["route"]
            if point.get("route_type") == "via"
        ]
        assert {float(point["width"]) for point in backbone_wires} == {0.8}
        assert len(backbone_vias) == 2
        assert {
            (float(point["via_diameter"]), float(point["via_hole_diameter"]))
            for point in backbone_vias
        } == {(0.8, 0.5)}


def test_usb_data_uses_explicit_reversible_orientation_trees(graph):
    """The ESD part is a real flow-through element, never a copper-bypassed stub."""
    g = graph("usb-c-data")
    traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }

    expected_edges = {
        "TR_J1_dp_pair": ("J1.DP1", "J1.DP2"),
        "TR_J1_dp_esd": ("J1.DP1", "U1.IO1"),
        "TR_U1_dp_r": ("U1.IO1B", "R3.pin1"),
        "TR_J1_dm_pair": ("J1.DM1", "J1.DM2"),
        "TR_J1_dm_esd": ("J1.DM1", "U1.IO2"),
        "TR_U1_dm_r": ("U1.IO2B", "R4.pin1"),
    }
    for trace_name, (left, right) in expected_edges.items():
        trace = traces[trace_name]
        assert float(trace["min_trace_thickness"]) == pytest.approx(0.15)
        assert set(trace.get("connected_source_port_ids") or []) == {
            g.port_id(left), g.port_id(right)
        }
        assert not trace.get("connected_source_net_ids")

    internal_pairs = {
        frozenset(element.get("source_port_ids") or [])
        for element in g.elements
        if element.get("type") == "source_component_internal_connection"
    }
    assert frozenset({g.port_id("U1.IO1"), g.port_id("U1.IO1B")}) in internal_pairs
    assert frozenset({g.port_id("U1.IO2"), g.port_id("U1.IO2B")}) in internal_pairs
    assert not {
        "TR_U1_dp_pair", "TR_U1_dm_pair", "TR_J1_dp1", "TR_J1_dp2",
        "TR_J1_dm1", "TR_J1_dm2", "TR_R3_conn_boundary",
        "TR_R4_conn_boundary",
    } & set(traces)

    for name in ("TR_J1_dp_pair", "TR_J1_dm_pair"):
        source_id = traces[name]["source_trace_id"]
        routed = next(
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("source_trace_id") == source_id
        )
        wires = [point for point in routed["route"] if point.get("route_type") == "wire"]
        vias = [point for point in routed["route"] if point.get("route_type") == "via"]
        assert {float(point["width"]) for point in wires} == {0.15}
        assert len(vias) == 2
        assert {float(point["via_diameter"]) for point in vias} == {0.6}
        assert {float(point["via_hole_diameter"]) for point in vias} == {0.3}

    # Freeze the constructive central-return breakout that makes the
    # interleaved DP/DM/DP/DM connector pad order routable. The return vias
    # land between the connector's two exact NPTH guards, while each channel
    # uses its own opposite-face corridor before re-entering the mate pad.
    # Values are block-local (subtract J1's center), so a board may translate
    # the whole block without turning this into board-global folklore.
    j_pcb = next(
        element for element in g.elements
        if element.get("type") == "pcb_component"
        and element.get("source_component_id")
        == g.components["J1"]["source_component_id"]
    )
    block_group = next(
        element for element in g.elements
        if element.get("type") == "pcb_group"
        and element.get("pcb_group_id") == j_pcb.get("pcb_group_id")
    )
    block_origin = (
        float(block_group["anchor_position"]["x"]),
        float(block_group["anchor_position"]["y"]),
    )
    expected_breakouts = {
        "TR_J1_dp_pair": {
            "vias": [(-0.38, 3.6), (1.6, 1.0)],
            "opposite_y": 4.3,
        },
        "TR_J1_dm_pair": {
            "vias": [(0.38, 3.6), (-1.6, 1.0)],
            "opposite_y": 2.8,
        },
    }
    for name, expected in expected_breakouts.items():
        source_id = traces[name]["source_trace_id"]
        routed = next(
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("source_trace_id") == source_id
        )
        local_vias = [
            (
                float(point["x"]) - block_origin[0],
                float(point["y"]) - block_origin[1],
            )
            for point in routed["route"]
            if point.get("route_type") == "via"
        ]
        assert local_vias == pytest.approx(expected["vias"])
        assert all(
            point.get("from_layer") != point.get("to_layer")
            for point in routed["route"]
            if point.get("route_type") == "via"
        )
        opposite_layer_wires = [
            point for point in routed["route"]
            if point.get("route_type") == "wire"
            and point.get("layer") == "bottom"
        ]
        assert opposite_layer_wires
        assert any(
            float(point["y"]) - block_origin[1]
            == pytest.approx(expected["opposite_y"])
            for point in opposite_layer_wires
        )

    # The ESD package is a flow-through part in the physical layout too: its
    # connector-facing pads and resistor-facing pads form two ordered lanes.
    # Freezing this relative geometry prevents a future refactor from turning
    # the native pair back into the earlier vertical/stub arrangement whose
    # only possible routes separated for most of their length.
    pcb_components = {
        element["source_component_id"]: element
        for element in g.elements
        if element.get("type") == "pcb_component"
    }
    u_pcb = pcb_components[g.components["U1"]["source_component_id"]]
    r_dp_pcb = pcb_components[g.components["R3"]["source_component_id"]]
    r_dm_pcb = pcb_components[g.components["R4"]["source_component_id"]]
    assert float(u_pcb["rotation"]) == pytest.approx(90.0)
    assert float(r_dp_pcb["center"]["y"]) == pytest.approx(
        float(r_dm_pcb["center"]["y"])
    )
    assert (
        float(r_dp_pcb["center"]["x"]) + float(r_dm_pcb["center"]["x"])
    ) / 2 == pytest.approx(float(u_pcb["center"]["x"]))

    pcb_ports = {
        element["source_port_id"]: element
        for element in g.elements
        if element.get("type") == "pcb_port"
    }

    def port_xy(selector: str) -> tuple[float, float]:
        port = pcb_ports[g.port_id(selector)]
        return float(port["x"]), float(port["y"])

    connector_dp = port_xy("J1.DP1")
    connector_dm = port_xy("J1.DM1")
    clamp_dp_in = port_xy("U1.IO1")
    clamp_dm_in = port_xy("U1.IO2")
    clamp_dp_out = port_xy("U1.IO1B")
    clamp_dm_out = port_xy("U1.IO2B")
    resistor_dp = port_xy("R3.pin1")
    resistor_dm = port_xy("R4.pin1")
    assert connector_dp[0] < connector_dm[0]
    assert clamp_dp_in[0] < clamp_dm_in[0]
    assert clamp_dp_out[0] < clamp_dm_out[0]
    assert resistor_dp[0] < resistor_dm[0]
    # The resistor row is inset by exactly 20um per side. This tiny symmetric
    # offset preserves the flow-through lane while opening the independently
    # checked 0.25mm CC2 corridor; treating it as exact collinearity regressed
    # that ordinary-width route by 5.97um.
    assert resistor_dp[0] - clamp_dp_out[0] == pytest.approx(0.02)
    assert clamp_dm_out[0] - resistor_dm[0] == pytest.approx(0.02)
    assert math.dist(clamp_dp_out, resistor_dp) == pytest.approx(
        math.dist(clamp_dm_out, resistor_dm)
    )


def test_usb_data_exports_only_direct_physical_differential_pair_selectors():
    """The reusable API must never select an aggregate USB named net."""
    source = (
        Path(__file__).resolve().parents[1]
        / "blocks"
        / "usb-c-data"
        / "usb-c-data.tsx"
    ).read_text(encoding="utf-8")
    for positive, negative in (
        ("TR_${j}_dp_esd", "TR_${j}_dm_esd"),
        ("TR_${u}_dp_r", "TR_${u}_dm_r"),
    ):
        assert f"positiveConnection={{`{positive}`}}" in source
        assert f"negativeConnection={{`{negative}`}}" in source
    assert "export const UsbDeviceDifferentialPair" in source
    assert 'from={`.${mcu} > .USB_DP`} to={`.${rDp} > .pin2`}' in source
    assert 'from={`.${mcu} > .USB_DM`} to={`.${rDm} > .pin2`}' in source
    assert "props.emitMcuNetLeaves !== false" in source
    rp_source = (
        Path(__file__).resolve().parents[1]
        / "blocks"
        / "rp2040-core"
        / "rp2040-core.tsx"
    ).read_text(encoding="utf-8")
    assert "props.emitUsbNetLeaves !== false" in rp_source
    assert "positiveConnection={`net." not in source
    assert "negativeConnection={`net." not in source


def test_usb_device_pair_composes_as_two_direct_edges_without_named_leaves(graph):
    g = graph("usb-device-differential-pair")
    assert not g.errors()
    assert not g.warnings()
    traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    assert set(traces["TR_U3_R3_usb_dp"]["connected_source_port_ids"]) == {
        g.port_id("U3.USB_DP"), g.port_id("R3.pin2"),
    }
    assert set(traces["TR_U3_R4_usb_dm"]["connected_source_port_ids"]) == {
        g.port_id("U3.USB_DM"), g.port_id("R4.pin2"),
    }
    assert not {
        "TR_U3_usbdp", "TR_U3_usbdm", "TR_R3_mcu", "TR_R4_mcu",
    } & set(traces)
    for name in ("TR_U3_R3_usb_dp", "TR_U3_R4_usb_dm"):
        trace = traces[name]
        assert len(trace.get("connected_source_port_ids") or []) == 2
        assert not trace.get("connected_source_net_ids")
        assert float(trace["min_trace_thickness"]) == pytest.approx(0.15)


def test_usb_data_routed_trees_meet_power_width_and_pair_skew_contracts(graph):
    """Exact routed copper, not only JSX intent, is the reusable USB proof."""
    g = graph("usb-c-data-routing")
    assert not g.errors()
    assert not g.warnings()
    plane_findings = [
        finding
        for finding in intent.check(Board(g.elements), None).findings
        if finding.get("kind")
        in {"pcb_plane_connectivity_error", "pcb_copper_pour_short_error"}
    ]
    assert plane_findings == []
    source_traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    pcb_by_source: dict[str, list[dict]] = {}
    for element in g.elements:
        if element.get("type") != "pcb_trace" or not element.get("source_trace_id"):
            continue
        pcb_by_source.setdefault(str(element["source_trace_id"]), []).append(element)

    def routes(name: str) -> list[dict]:
        source_id = source_traces[name]["source_trace_id"]
        # Prefer exact preserved connection identity.  Authored-net-tree
        # contraction may deliberately reuse the fixed trunk's
        # source_trace_id on a different rail edge.
        result = [
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("connection_name") == source_id
        ]
        if not result:
            result = pcb_by_source.get(source_id, [])
        assert result, f"{name}: no compiled PCB trace"
        return result

    def route_length(name: str) -> float:
        total = 0.0
        for routed in routes(name):
            points = [
                point for point in routed.get("route") or []
                if isinstance(point.get("x"), (int, float))
                and isinstance(point.get("y"), (int, float))
            ]
            total += sum(
                math.dist((float(a["x"]), float(a["y"])),
                          (float(b["x"]), float(b["y"])))
                for a, b in zip(points, points[1:])
            )
        return total

    def route_widths(name: str) -> set[float]:
        return {
            float(point["width"])
            for routed in routes(name)
            for point in routed.get("route") or []
            if point.get("route_type") == "wire"
            and isinstance(point.get("width"), (int, float))
        }

    # Connector power leaves are short .2mm branches; both rail sections are
    # .8mm. The manual reversible-pad crossover drills only .8/.5mm vias.
    for name in ("TR_J1_vbus1_neck", "TR_J1_vbus2_neck"):
        assert route_widths(name) == {0.2}
        assert route_length(name) <= 2.0
    for name in ("TR_U1_vbus", "TR_C1_vbus"):
        assert route_widths(name) == {0.2}
        assert route_length(name) <= 3.0
    for name in (
        "TR_J1_vbus_trunk", "TR_J1_vbus_rail", "TR_U1_vbus_backbone",
    ):
        assert route_widths(name) == {0.8}
    crossover = routes("TR_J1_vbus_trunk")
    crossover_vias = [
        point for routed in crossover for point in routed.get("route") or []
        if point.get("route_type") == "via"
    ]
    assert len(crossover_vias) == 2
    compiled_vias = [
        element for element in g.elements
        if element.get("type") == "pcb_via"
        and element.get("pcb_trace_id") == crossover[0]["pcb_trace_id"]
    ]
    assert len(compiled_vias) == 2
    assert {
        (float(via["outer_diameter"]), float(via["hole_diameter"]))
        for via in compiled_vias
    } == {(0.8, 0.5)}

    clamp_backbone = routes("TR_U1_vbus_backbone")
    clamp_vias = [
        point for routed in clamp_backbone for point in routed.get("route") or []
        if point.get("route_type") == "via"
    ]
    assert len(clamp_vias) == 2
    assert {
        (float(via["via_diameter"]), float(via["via_hole_diameter"]))
        for via in clamp_vias
    } == {(0.8, 0.5)}

    for name in ("TR_J1_cc1r", "TR_J1_cc2r"):
        assert route_widths(name) == {0.25}
    for name in (
        "TR_J1_dp_pair", "TR_J1_dp_esd", "TR_U1_dp_r",
        "TR_J1_dm_pair", "TR_J1_dm_esd", "TR_U1_dm_r",
    ):
        assert route_widths(name) == {0.15}

    dp_length = sum(route_length(name) for name in (
        "TR_J1_dp_pair", "TR_J1_dp_esd", "TR_U1_dp_r",
    ))
    dm_length = sum(route_length(name) for name in (
        "TR_J1_dm_pair", "TR_J1_dm_esd", "TR_U1_dm_r",
    ))
    assert abs(dp_length - dm_length) <= 3.8, (dp_length, dm_length)

    # The package-internal channel is not duplicated by a PCB trace.
    assert "TR_U1_dp_pair" not in source_traces
    assert "TR_U1_dm_pair" not in source_traces
    for first, mate in (("IO1", "IO1B"), ("IO2", "IO2B")):
        assert not [
            trace for trace in source_traces.values()
            if set(trace.get("connected_source_port_ids") or [])
            == {g.port_id(f"U1.{first}"), g.port_id(f"U1.{mate}")}
        ]


def test_ws2812_level_shifter_bypass_is_local_to_vcc(graph):
    g = graph("ws2812-level-shifter")
    centers = {
        element["source_component_id"]: element["center"]
        for element in g.elements
        if element.get("type") == "pcb_component"
    }
    cap = centers[g.components["C20"]["source_component_id"]]
    vcc_source_port = g.port_id("U6.VCC")
    vcc = next(
        element
        for element in g.elements
        if element.get("type") == "pcb_port"
        and element.get("source_port_id") == vcc_source_port
    )
    distance = math.dist(
        (float(cap["x"]), float(cap["y"])),
        (float(vcc["x"]), float(vcc["y"])),
    )
    assert distance <= 2.0


def _assert_local_power_edge(
    g,
    *,
    supply: str,
    capacitor: str,
    local_trace: str,
    boundary_trace: str | None,
    rail: str | None,
    expected_layer: str,
) -> list[tuple[float, float]]:
    """Prove source topology and emitted copper for one bypass branch."""
    traces = {
        element["name"]: element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    local = traces[local_trace]
    assert set(local.get("connected_source_port_ids") or []) == {
        g.port_id(supply), g.port_id(capacitor)
    }
    assert not local.get("connected_source_net_ids")
    assert float(local["min_trace_thickness"]) == pytest.approx(0.2)
    assert float(local["max_length"]) == pytest.approx(2.0)

    if boundary_trace is not None:
        assert rail is not None
        boundary = traces[boundary_trace]
        assert boundary.get("connected_source_port_ids") == [g.port_id(capacitor)]
        assert boundary.get("connected_source_net_ids") == [
            g.nets[rail]["source_net_id"]
        ]
        assert float(boundary["min_trace_thickness"]) == pytest.approx(0.8)

    routed = [
        element
        for element in g.elements
        if element.get("type") == "pcb_trace"
        and (
            element.get("connection_name") == local["source_trace_id"]
            or element.get("source_trace_id") == local["source_trace_id"]
        )
    ]
    assert len(routed) == 1, (
        f"{local_trace}: expected one preserved authored local edge, got "
        f"{len(routed)}"
    )
    route = routed[0].get("route") or []
    widths = {
        float(point["width"])
        for point in route
        if point.get("route_type") == "wire"
        and isinstance(point.get("width"), (int, float))
    }
    assert widths == {0.2}
    wires = [point for point in route if point.get("route_type") == "wire"]
    assert wires
    assert {point.get("layer") for point in wires} == {expected_layer}
    coordinates: list[tuple[float, float]] = []
    for point in wires:
        coordinate = (float(point["x"]), float(point["y"]))
        if not coordinates or math.dist(coordinate, coordinates[-1]) > 1e-9:
            coordinates.append(coordinate)
    assert len(coordinates) >= 2

    def pcb_port_coordinate(ref: str) -> tuple[float, float]:
        source_port_id = g.port_id(ref)
        matches = [
            element for element in g.elements
            if element.get("type") == "pcb_port"
            and element.get("source_port_id") == source_port_id
        ]
        assert len(matches) == 1, (ref, len(matches))
        return (float(matches[0]["x"]), float(matches[0]["y"]))

    expected_endpoints = (
        pcb_port_coordinate(supply), pcb_port_coordinate(capacitor)
    )
    actual_endpoints = (coordinates[0], coordinates[-1])
    endpoints_match = lambda expected: all(
        math.dist(actual, wanted) <= 1e-9
        for actual, wanted in zip(actual_endpoints, expected)
    )
    assert (
        endpoints_match(expected_endpoints)
        or endpoints_match(expected_endpoints[::-1])
    ), (local_trace, actual_endpoints, expected_endpoints)

    length = sum(math.dist(a, b) for a, b in zip(coordinates, coordinates[1:]))
    assert 0 < length <= 2.0 + 1e-9, (local_trace, length)
    assert not [
        point
        for point in route
        if point.get("route_type") == "via"
    ], f"{local_trace}: a bypass loop must not change layers"
    assert not [
        element for element in g.elements
        if element.get("type") == "pcb_via"
        and element.get("pcb_trace_id") == routed[0].get("pcb_trace_id")
    ], f"{local_trace}: a bypass loop must not emit a compiled via"
    return coordinates


def test_powered_sensor_and_pixel_blocks_preserve_local_bypass_trees(graph):
    cases = [
        ("sensor-bme280", "top", [
            ("U5.VDD", "C18.pin1", "TR_U5_vdd", "TR_C18_v", "V3_3"),
            ("U5.VDDIO", "C19.pin1", "TR_U5_vddio", "TR_C19_v", "V3_3"),
        ]),
        ("sensor-bme280-bottom", "bottom", [
            ("U5.VDD", "C18.pin1", "TR_U5_vdd", "TR_C18_v", "V3_3"),
            ("U5.VDDIO", "C19.pin1", "TR_U5_vddio", "TR_C19_v", "V3_3"),
        ]),
        ("ws2812-level-shifter", "top", [
            ("U6.VCC", "C20.pin1", "TR_U6_vcc", "TR_C20_v", "V5"),
        ]),
        ("ws2812-level-shifter-bottom", "bottom", [
            ("U6.VCC", "C20.pin1", "TR_U6_vcc", "TR_C20_v", "V5"),
        ]),
        ("ws2812-chain", "top", [
            (f"D{i}.VDD", f"C{30 + i}.pin1", f"TR_D{i}_vdd", None, None)
            for i in range(10, 14)
        ]),
        ("ws2812-chain-bottom", "bottom", [
            (f"D{i}.VDD", f"C{30 + i}.pin1", f"TR_D{i}_vdd", None, None)
            for i in range(10, 14)
        ]),
    ]
    compiled_routes: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for bench, expected_layer, edges in cases:
        g = graph(bench)
        errors = [
            (element["type"], element.get("message"))
            for element in g.errors()
        ]
        assert errors == [], (bench, errors)
        warnings = [
            (element["type"], element.get("message"))
            for element in g.warnings()
        ]
        assert warnings == [], (bench, warnings)
        decoupling_findings = [
            finding
            for finding in intent.check(
                Board(g.elements), {"decoupling": {"maxDistanceMm": 2.0}}
            ).findings
            if str(finding.get("kind") or "").startswith(
                "layout_intent_decoupling_"
            )
        ]
        assert decoupling_findings == [], (bench, decoupling_findings)
        for supply, capacitor, local_trace, boundary_trace, rail in edges:
            compiled_routes[(bench, local_trace)] = _assert_local_power_edge(
                g,
                supply=supply,
                capacitor=capacitor,
                local_trace=local_trace,
                boundary_trace=boundary_trace,
                rail=rail,
                expected_layer=expected_layer,
            )

    # The bottom artifacts must be exact X-mirrors of the reviewed top copper,
    # not merely independently close enough to happen to pass a radius gate.
    for top_bench, bottom_bench in (
        ("sensor-bme280", "sensor-bme280-bottom"),
        ("ws2812-level-shifter", "ws2812-level-shifter-bottom"),
        ("ws2812-chain", "ws2812-chain-bottom"),
    ):
        top_trace_names = {
            trace_name for bench, trace_name in compiled_routes if bench == top_bench
        }
        for trace_name in top_trace_names:
            top_coordinates = compiled_routes[(top_bench, trace_name)]
            bottom_coordinates = compiled_routes[(bottom_bench, trace_name)]
            mirrored_coordinates = [(-x, y) for x, y in top_coordinates]
            assert len(bottom_coordinates) == len(mirrored_coordinates)
            assert all(
                math.dist(actual, wanted) <= 1e-9
                for actual, wanted in zip(bottom_coordinates, mirrored_coordinates)
            ), (top_bench, trace_name, top_coordinates, bottom_coordinates)

    bme = graph("sensor-bme280")
    traces = {
        element["name"]: element
        for element in bme.elements
        if element.get("type") == "source_trace"
    }
    assert set(traces["TR_U5_csb"]["connected_source_port_ids"]) == {
        bme.port_id("U5.CSB"), bme.port_id("U5.VDDIO")
    }
    assert float(traces["TR_U5_sda"]["min_trace_thickness"]) == pytest.approx(0.25)
    assert float(traces["TR_U5_scl"]["min_trace_thickness"]) == pytest.approx(0.25)


def test_ws2812_chain_authors_one_mirrored_power_tree(graph):
    """The strict V5 tree and direct data chain route on both board faces."""
    top = graph("ws2812-chain")
    bottom = graph("ws2812-chain-bottom")
    node_refs = [f"N{i}" for i in range(30, 34)]
    pixel_refs = [f"D{i}" for i in range(10, 14)]
    cap_refs = [f"C{i}" for i in range(40, 44)]

    layout = {
        "decoupling": {"maxDistanceMm": 2.0},
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

    def traces(g) -> dict[str, dict]:
        return {
            element["name"]: element
            for element in g.elements
            if element.get("type") == "source_trace"
        }

    def route_coordinates(g, name: str, width: float, layer: str):
        source = traces(g)[name]
        routed = [
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and (
                element.get("connection_name") == source["source_trace_id"]
                or element.get("source_trace_id") == source["source_trace_id"]
            )
        ]
        assert len(routed) == 1, (name, len(routed))
        route = routed[0].get("route") or []
        assert not [point for point in route if point.get("route_type") == "via"]
        wires = [point for point in route if point.get("route_type") == "wire"]
        assert wires
        assert {float(point["width"]) for point in wires} == {width}
        assert {point.get("layer") for point in wires} == {layer}
        coordinates: list[tuple[float, float]] = []
        for point in wires:
            coordinate = (float(point["x"]), float(point["y"]))
            if not coordinates or math.dist(coordinate, coordinates[-1]) > 1e-9:
                coordinates.append(coordinate)
        assert len(coordinates) >= 2
        return coordinates

    def port_coordinate(g, ref: str) -> tuple[float, float]:
        port_id = g.port_id(ref)
        matches = [
            element for element in g.elements
            if element.get("type") == "pcb_port"
            and element.get("source_port_id") == port_id
        ]
        assert len(matches) == 1, (ref, len(matches))
        return float(matches[0]["x"]), float(matches[0]["y"])

    def endpoints_match(actual, expected) -> bool:
        return all(
            math.dist(actual_point, expected_point) <= 1e-9
            for actual_point, expected_point in zip(actual, expected)
        )

    compiled: dict[tuple[str, str], list[tuple[float, float]]] = {}
    data_routes: dict[tuple[str, str], list[tuple[float, float]]] = {}
    data_edges = [
        ("TR_R30_out", "R30.pin2", "D10.DIN"),
        ("TR_D10_dout", "D10.DOUT", "D11.DIN"),
        ("TR_D11_dout", "D11.DOUT", "D12.DIN"),
        ("TR_D12_dout", "D12.DOUT", "D13.DIN"),
    ]
    for bench, g, layer in (
        ("top", top, "top"), ("bottom", bottom, "bottom")
    ):
        assert g.errors() == [], (bench, g.errors())
        assert g.warnings() == [], (bench, g.warnings())
        board = next(
            element for element in g.elements
            if element.get("type") == "pcb_board"
        )
        assert board["min_trace_to_pad_edge_clearance"] == pytest.approx(0.15)
        assert board["min_via_edge_to_pad_edge_clearance"] == pytest.approx(0.15)
        assert intent.check(Board(g.elements), layout).findings == []

        source_traces = traces(g)
        for name, start_ref, end_ref in data_edges:
            edge = source_traces[name]
            assert set(edge.get("connected_source_port_ids") or []) == {
                g.port_id(start_ref), g.port_id(end_ref),
            }
            assert not edge.get("connected_source_net_ids")
            assert float(edge["min_trace_thickness"]) == pytest.approx(0.25)
            coordinates = route_coordinates(g, name, 0.25, layer)
            data_routes[(bench, name)] = coordinates
            expected_endpoints = (
                port_coordinate(g, start_ref), port_coordinate(g, end_ref),
            )
            assert (
                endpoints_match(
                    (coordinates[0], coordinates[-1]), expected_endpoints,
                )
                or endpoints_match(
                    (coordinates[0], coordinates[-1]), expected_endpoints[::-1],
                )
            ), (bench, name, coordinates, expected_endpoints)

        input_boundary = source_traces["TR_R30_in"]
        assert input_boundary.get("connected_source_port_ids") == [
            g.port_id("R30.pin1")
        ]
        assert input_boundary.get("connected_source_net_ids") == [
            g.nets["LED_DATA_5V"]["source_net_id"]
        ]
        output_boundary = source_traces["TR_D13_dout"]
        assert output_boundary.get("connected_source_port_ids") == [
            g.port_id("D13.DOUT")
        ]
        assert output_boundary.get("connected_source_net_ids") == [
            g.nets["PX_14_DIN"]["source_net_id"]
        ]
        assert float(input_boundary["min_trace_thickness"]) == pytest.approx(0.25)
        assert float(output_boundary["min_trace_thickness"]) == pytest.approx(0.25)
        assert not {
            str(element.get("name"))
            for element in g.elements
            if element.get("type") == "source_net"
        } & {f"PX_{index}_DIN" for index in range(10, 14)}

        ground_fanouts = [
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and str(element.get("pcb_trace_id") or "").startswith("fanout:")
        ]
        assert len(ground_fanouts) == 8
        assert all(
            len(element.get("route") or []) == 1
            and element["route"][0].get("layer") == layer
            and element["route"][0].get("is_inside_copper_pour") is True
            for element in ground_fanouts
        )

        internal_edges: list[dict] = []
        for pixel, capacitor, node in zip(pixel_refs, cap_refs, node_refs):
            local_name = f"TR_{pixel}_vdd"
            local = source_traces[local_name]
            assert set(local.get("connected_source_port_ids") or []) == {
                g.port_id(f"{pixel}.VDD"), g.port_id(f"{capacitor}.pin1")
            }
            assert float(local["min_trace_thickness"]) == pytest.approx(0.2)
            assert float(local["max_length"]) == pytest.approx(2.0)
            internal_edges.append(local)

            neck_name = f"TR_{capacitor}_V5_NECK"
            neck = source_traces[neck_name]
            assert set(neck.get("connected_source_port_ids") or []) == {
                g.port_id(f"{capacitor}.pin1"), g.port_id(f"{node}.pin1")
            }
            assert not neck.get("connected_source_net_ids")
            assert float(neck["min_trace_thickness"]) == pytest.approx(0.2)
            assert float(neck["max_length"]) == pytest.approx(3.0)
            internal_edges.append(neck)

            for name, width, expected_length, start_ref, end_ref in (
                (
                    local_name, 0.2, 1.8,
                    f"{pixel}.VDD", f"{capacitor}.pin1",
                ),
                (
                    neck_name, 0.2, 1.8,
                    f"{capacitor}.pin1", f"{node}.pin1",
                ),
            ):
                coordinates = route_coordinates(g, name, width, layer)
                compiled[(bench, name)] = coordinates
                expected_endpoints = (
                    port_coordinate(g, start_ref), port_coordinate(g, end_ref),
                )
                assert (
                    endpoints_match(
                        (coordinates[0], coordinates[-1]), expected_endpoints,
                    )
                    or endpoints_match(
                        (coordinates[0], coordinates[-1]), expected_endpoints[::-1],
                    )
                ), (bench, name, coordinates, expected_endpoints)
                length = sum(
                    math.dist(first, second)
                    for first, second in zip(coordinates, coordinates[1:])
                )
                assert length == pytest.approx(expected_length, abs=1e-9), (
                    bench, name, length,
                )

            node_source_id = g.components[node]["source_component_id"]
            node_component = next(
                element for element in g.elements
                if element.get("type") == "pcb_component"
                and element.get("source_component_id") == node_source_id
            )
            assert node_component.get("layer") == layer
            assert node_component.get("do_not_place") is True
            pads = [
                element for element in g.elements
                if element.get("type") == "pcb_smtpad"
                and element.get("pcb_component_id")
                == node_component["pcb_component_id"]
            ]
            assert len(pads) == 1
            assert pads[0].get("is_covered_with_solder_mask") is True
            assert not [
                element for element in g.elements
                if element.get("type") == "pcb_solder_paste"
                and element.get("pcb_component_id")
                == node_component["pcb_component_id"]
            ]
            assert not [
                element for element in g.elements
                if str(element.get("type") or "").startswith("pcb_silkscreen")
                and element.get("pcb_component_id")
                == node_component["pcb_component_id"]
            ]

        for index in range(3):
            name = f"TR_V5_CHAIN_{10 + index}_{11 + index}"
            edge = source_traces[name]
            assert set(edge.get("connected_source_port_ids") or []) == {
                g.port_id(f"N{30 + index}.pin1"),
                g.port_id(f"N{31 + index}.pin1"),
            }
            assert not edge.get("connected_source_net_ids")
            assert float(edge["min_trace_thickness"]) == pytest.approx(0.8)
            internal_edges.append(edge)
            coordinates = route_coordinates(g, name, 0.8, layer)
            compiled[(bench, name)] = coordinates
            expected_endpoints = (
                port_coordinate(g, f"N{30 + index}.pin1"),
                port_coordinate(g, f"N{31 + index}.pin1"),
            )
            assert (
                endpoints_match(
                    (coordinates[0], coordinates[-1]), expected_endpoints,
                )
                or endpoints_match(
                    (coordinates[0], coordinates[-1]), expected_endpoints[::-1],
                )
            ), (bench, name, coordinates, expected_endpoints)
            length = sum(
                math.dist(first, second)
                for first, second in zip(coordinates, coordinates[1:])
            )
            assert length == pytest.approx(7.0, abs=1e-9), (bench, name, length)

        boundary = source_traces["TR_V5_CHAIN_ESCAPE"]
        assert boundary.get("connected_source_port_ids") == [
            g.port_id("N33.pin1")
        ]
        assert boundary.get("connected_source_net_ids") == [
            g.nets["V5"]["source_net_id"]
        ]
        assert float(boundary["min_trace_thickness"]) == pytest.approx(0.8)

        # Twelve physical ports and eleven internal edges: exactly one tree,
        # with the sole named-net edge at its final hidden node.
        physical_ports = {
            port
            for edge in internal_edges
            for port in (edge.get("connected_source_port_ids") or [])
        }
        assert len(physical_ports) == 12
        assert len(internal_edges) == len(physical_ports) - 1
        connectivity_keys = {
            str(edge.get("subcircuit_connectivity_map_key"))
            for edge in [*internal_edges, boundary]
        }
        assert len(connectivity_keys) == 1
        assert [
            trace for trace in source_traces.values()
            if trace.get("connected_source_net_ids")
            == [g.nets["V5"]["source_net_id"]]
            and set(trace.get("connected_source_port_ids") or []) & physical_ports
        ] == [boundary]
        assert not [
            element for element in g.elements
            if element.get("type") == "pcb_trace"
            and element.get("connection_name") == g.nets["V5"]["source_net_id"]
        ]

    for (_bench, name), top_coordinates in [
        (key, value) for key, value in compiled.items() if key[0] == "top"
    ]:
        bottom_coordinates = compiled[("bottom", name)]
        mirrored = [(-x, y) for x, y in top_coordinates]
        assert len(bottom_coordinates) == len(mirrored)
        assert all(
            math.dist(actual, expected) <= 1e-9
            for actual, expected in zip(bottom_coordinates, mirrored)
        ), (name, top_coordinates, bottom_coordinates)

    # Autorouter geometry may choose a different legal dogleg after the layer
    # reversal, but every direct data endpoint is the exact X-mirror and both
    # routed faces retain the full 0.25mm contract without vias.
    for name, start_ref, end_ref in data_edges:
        assert data_routes[("top", name)]
        assert data_routes[("bottom", name)]
        for ref in (start_ref, end_ref):
            top_coordinate = port_coordinate(top, ref)
            bottom_coordinate = port_coordinate(bottom, ref)
            assert bottom_coordinate == pytest.approx(
                (-top_coordinate[0], top_coordinate[1])
            )

    for bench_name in ("ws2812-chain.tsx", "ws2812-chain-bottom.tsx"):
        source = (
            Path(__file__).resolve().parents[1] / "testbench" / bench_name
        ).read_text(encoding="utf-8")
        for index in range(1, 5):
            assert f'phaseIndex={{{index}}}' in source
        assert "dataRoutingPhaseIndices={[1, 2, 3, 4]}" in source
        assert 'minTraceToPadEdgeClearance="0.15mm"' in source
        assert 'minViaEdgeToPadEdgeClearance="0.15mm"' in source
        assert "<GndPlanes" in source


def test_ws2812_chain_rejects_ambiguous_count_aware_allocations(tmp_path):
    """Hidden-node and phase allocations must fail before any board renders."""
    repo_root = Path(__file__).resolve().parents[3]
    block_path = (
        Path(__file__).resolve().parents[1]
        / "blocks" / "ws2812-chain" / "ws2812-chain.tsx"
    )
    script = tmp_path / "ws2812-chain-props.tsx"
    script.write_text(
        f"""
import {{ Ws2812Chain }} from {json.dumps(str(block_path))}

const expectReject = (label: string, render: () => unknown) => {{
  try {{
    render()
  }} catch {{
    return
  }}
  throw new Error(`${{label}} was accepted`)
}}

expectReject("short node allocation", () =>
  Ws2812Chain({{ count: 2, railNodeRefs: ["N30"] }}))
expectReject("duplicate node allocation", () =>
  Ws2812Chain({{ count: 2, railNodeRefs: ["N30", "N30"] }}))
expectReject("probe ref used as hidden node", () =>
  Ws2812Chain({{ count: 2, railNodeRefs: ["N30", "TP1"] }}))
expectReject("short data phase allocation", () =>
  Ws2812Chain({{ count: 2, dataRoutingPhaseIndices: [1] }}))
expectReject("duplicate data phase allocation", () =>
  Ws2812Chain({{ count: 2, dataRoutingPhaseIndices: [1, 1] }}))
expectReject("negative data phase", () =>
  Ws2812Chain({{ count: 2, dataRoutingPhaseIndices: [1, -1] }}))
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(repo_root / "toolchain" / "node_modules" / ".bin" / "tsx"), script],
        cwd=repo_root,
        env={**os.environ, "CIRCUIT_PARTS_ENGINE": "off"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_rp2040_compact_button_variant_rewires_two_pin_parts_safely(graph):
    g = graph("rp2040-compact-buttons")
    assert not g.errors()
    assert not g.warnings()
    assert g.lcsc()["SW2"] == "C2828561"
    assert g.lcsc()["SW3"] == "C2828561"
    assert g.connected("R13.pin2", "SW2.pin1")
    assert g.connected("SW2.pin2", "net.GND")
    assert g.connected("U3.RUN", "SW3.pin1")
    assert g.connected("SW3.pin2", "net.GND")
    assert not g.connected("U3.QSPI_SS", "net.GND")
    assert not g.connected("U3.RUN", "net.GND")
    for name in ("SW2", "SW3"):
        source_id = g.components[name]["source_component_id"]
        assert len([
            element
            for element in g.elements
            if element.get("type") == "source_port"
            and element.get("source_component_id") == source_id
        ]) == 2


def test_gnd_planes_fan_out_each_drop_without_a_phantom_aggregate(graph):
    """Two plane drops become two routes; no hidden aggregate may fail the phase."""
    g = graph("gnd-planes")
    errs = [(e["type"], str(e.get("message", ""))[:200]) for e in g.errors()]
    warns = [(e["type"], str(e.get("message", ""))[:200]) for e in g.warnings()]
    assert not errs
    assert not warns

    fanout = [
        element
        for element in g.elements
        if element.get("type") == "pcb_trace"
        and str(element.get("pcb_trace_id") or "").startswith("fanout:")
    ]
    assert len(fanout) == 2
    assert {
        str(element.get("source_trace_id")) for element in fanout
    } == {"source_trace_0", "source_trace_1"}
    assert all(len(element.get("route") or []) == 1 for element in fanout)
    assert all(
        element["route"][0].get("is_inside_copper_pour") is True
        and element["route"][0].get("layer") == "top"
        for element in fanout
    )
    pours = [
        element
        for element in g.elements
        if element.get("type") == "pcb_copper_pour"
    ]
    assert {str(element.get("layer")) for element in pours} == {"top", "bottom"}
    gnd_vias = [
        element
        for element in g.elements
        if element.get("type") == "pcb_via"
        and element.get("subcircuit_connectivity_map_key")
    ]
    # The two fanouts contact the top pour directly; only explicit stitches
    # drill the board to join top and bottom material planes.
    assert len(gnd_vias) == 2


def _routed_net_metrics(g, ref: str) -> tuple[float, int]:
    """Measure one logical net exactly as the layout verifier does."""
    source_port_id = g.port_id(ref)
    source_port = next(
        element for element in g.elements
        if element.get("type") == "source_port"
        and element.get("source_port_id") == source_port_id
    )
    key = source_port.get("subcircuit_connectivity_map_key")
    assert key, f"{ref}: compiled port has no connectivity key"

    source_nets = {
        str(element.get("source_net_id")): element.get(
            "subcircuit_connectivity_map_key"
        )
        for element in g.elements
        if element.get("type") == "source_net"
    }
    source_traces = {
        str(element.get("source_trace_id")): element.get(
            "subcircuit_connectivity_map_key"
        )
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    length = 0.0
    for element in g.elements:
        if element.get("type") != "pcb_trace":
            continue
        connection = str(element.get("connection_name") or "")
        trace_key = source_nets.get(connection) or source_traces.get(connection)
        if trace_key != key:
            continue
        route = [
            point
            for point in element.get("route") or []
            if isinstance(point.get("x"), (int, float))
            and isinstance(point.get("y"), (int, float))
        ]
        length += sum(
            math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            for a, b in zip(route, route[1:])
        )
    vias = sum(
        1 for element in g.elements
        if element.get("type") == "pcb_via"
        and element.get("subcircuit_connectivity_map_key") == key
    )
    return length, vias


def test_rp2040_critical_cluster_compiles_inside_route_budgets(graph):
    """Guard real routed copper, not only source coordinates or connectivity."""
    g = graph("rp2040-critical-routing")
    assert not g.errors()
    assert not g.warnings()
    budgets = {
        # Both crystal terminals: <=10mm and no layer changes.
        "U3.XIN": (10.0, 0),
        "Y1.pin3": (10.0, 0),
        # Flash clock has the tighter via allowance; data/CS use the data cap.
        "U3.QSPI_SCLK": (25.0, 1),
        "U3.QSPI_SD0": (35.0, 2),
        "U3.QSPI_SD1": (35.0, 2),
        "U3.QSPI_SD2": (35.0, 2),
        "U3.QSPI_SD3": (35.0, 2),
        "U3.QSPI_SS": (35.0, 2),
    }
    measured = {ref: _routed_net_metrics(g, ref) for ref in budgets}
    for ref, (max_length, max_vias) in budgets.items():
        length, vias = measured[ref]
        assert 0 < length <= max_length, (
            f"{ref}: routed {length:.3f}mm, budget is {max_length:g}mm"
        )
        assert vias <= max_vias, (
            f"{ref}: routed through {vias} via(s), budget is {max_vias}"
        )

    placement_errors = [
        (element.get("type"), element.get("message"))
        for element in g.elements
        if element.get("type") in {
            "pcb_component_overlap_error",
            "pcb_courtyard_overlap_error",
            "pcb_footprint_overlap_error",
            "pcb_placement_error",
        }
    ]
    assert not placement_errors


def test_rp2040_critical_bench_owns_global_phase_regions_and_clearance(graph):
    """The block assigns phases; the board owns their untransformed bounds."""
    source = (
        Path(__file__).resolve().parents[1]
        / "testbench"
        / "rp2040-critical-routing.tsx"
    ).read_text(encoding="utf-8")
    assert 'name="rp-clock"' in source
    assert "phaseIndex={0}" in source
    assert "region={RP_CLOCK_ROUTING_REGION}" in source
    qspi_phases = {
        "rp-qspi-io3": 1,
        "rp-qspi-io2": 2,
        "rp-qspi-io1": 3,
        "rp-qspi-io0": 4,
        "rp-qspi-cs": 5,
    }
    for phase_name, phase_index in qspi_phases.items():
        assert f'name="{phase_name}"' in source
        assert f"phaseIndex={{{phase_index}}}" in source
    assert source.count("region={RP_QSPI_ROUTING_REGION}") == 5
    assert 'name="rp-dvdd-local"' in source
    assert "phaseIndex={11}" in source
    assert 'name="rp-dvdd-trunk"' in source
    assert "phaseIndex={12}" in source
    assert 'name="rp-power-west-south"' in source
    assert "phaseIndex={13}" in source
    assert "region={RP_POWER_WEST_SOUTH_ROUTING_REGION}" in source
    assert 'name="rp-power-east"' in source
    assert "phaseIndex={14}" in source
    assert "region={RP_POWER_EAST_ROUTING_REGION}" in source
    assert 'name="rp-power-north-flash"' in source
    assert "phaseIndex={15}" in source
    assert "region={RP_POWER_NORTH_FLASH_ROUTING_REGION}" in source
    assert 'name="rp-power-trunks"' in source
    assert "phaseIndex={16}" in source
    assert 'name="rp-power-necks"' in source
    assert "phaseIndex={17}" in source
    assert 'name="rp-control-debug"' in source
    assert "phaseIndex={18}" in source
    assert "region={RP_CONTROL_ROUTING_REGION}" in source
    assert "criticalRoutingPhaseIndices={{" in source
    assert "clock: 0" in source
    assert "qspiIo3: 1" in source
    assert "qspiIo2: 2" in source
    assert "qspiIo1: 3" in source
    assert "qspiIo0: 4" in source
    assert "qspiCs: 5" in source
    assert "powerRoutingPhaseIndices={{" in source
    assert "dvddLocalBranches: 11" in source
    assert "dvddTrunk: 12" in source
    assert "westSouthBranches: 13" in source
    assert "eastBranches: 14" in source
    assert "northFlashBranches: 15" in source
    assert "railTrunks: 16" in source
    assert "railNecks: 17" in source
    assert "controlRoutingPhaseIndex={18}" in source
    assert "<GndPlanes" in source
    assert 'layers={["top", "bottom"]}' in source
    glue_source = (
        Path(__file__).resolve().parents[1] / "blocks" / "glue.tsx"
    ).read_text(encoding="utf-8")
    assert "<GndFanoutTrace\n        name={`TR_${gndName}`}" in glue_source
    assert "net={gndNet}" in glue_source

    g = graph("rp2040-critical-routing")
    board = next(
        element for element in g.elements
        if element.get("type") == "pcb_board"
    )
    assert board["min_trace_to_pad_edge_clearance"] == pytest.approx(0.15)
    assert board["min_via_edge_to_pad_edge_clearance"] == pytest.approx(0.15)

    source_traces = {
        str(element.get("name")): element
        for element in g.elements
        if element.get("type") == "source_trace"
    }
    pcb_ports = {
        str(element.get("source_port_id")): element
        for element in g.elements
        if element.get("type") == "pcb_port"
    }
    qspi_bounds = {
        "min_x": -11.605,
        "max_x": 4.205,
        "min_y": 0.42505,
        "max_y": 24.1301,
    }
    for name in (
        "TR_U4_cs", "TR_U4_io0", "TR_U4_io1", "TR_U4_io2", "TR_U4_io3",
    ):
        endpoints = source_traces[name].get("connected_source_port_ids") or []
        assert len(endpoints) == 2
        for endpoint in endpoints:
            port = pcb_ports[str(endpoint)]
            assert qspi_bounds["min_x"] <= float(port["x"]) <= qspi_bounds["max_x"]
            assert qspi_bounds["min_y"] <= float(port["y"]) <= qspi_bounds["max_y"]

    ground = next(
        element for element in g.elements
        if element.get("type") == "source_net" and element.get("name") == "GND"
    )
    direct_drop_ids = {
        str(element["source_trace_id"])
        for element in g.elements
        if element.get("type") == "source_trace"
        and element.get("connected_source_net_ids") == [ground["source_net_id"]]
        and len(element.get("connected_source_port_ids") or []) == 1
    }
    fanouts = [
        element for element in g.elements
        if element.get("type") == "pcb_trace"
        and str(element.get("pcb_trace_id") or "").startswith("fanout:")
    ]
    assert len(direct_drop_ids) == 24
    assert {
        str(element.get("source_trace_id") or element.get("connection_name"))
        for element in fanouts
    } == direct_drop_ids
    assert all(
        len(element.get("route") or []) == 1
        and element["route"][0].get("is_inside_copper_pour") is True
        and element["route"][0].get("layer") == "top"
        for element in fanouts
    )
    assert [
        element for element in g.elements
        if element.get("type") == "pcb_trace"
        and element.get("connection_name") == ground["source_net_id"]
    ] == []
    plane_findings = [
        finding
        for finding in intent.check(Board(g.elements), None).findings
        if finding.get("kind")
        in {"pcb_plane_connectivity_error", "pcb_copper_pour_short_error"}
    ]
    assert plane_findings == []


def test_rp2040_supply_pins_have_compiled_local_decoupling(graph):
    """The routed critical artifact independently satisfies the 2mm contract."""
    g = graph("rp2040-critical-routing")
    findings = [
        finding
        for finding in intent.check(
            Board(g.elements), {"decoupling": {"maxDistanceMm": 2.0}}
        ).findings
        if str(finding.get("kind") or "").startswith(
            "layout_intent_decoupling_"
        )
        and str(finding.get("part") or "").startswith(("U3.", "U4."))
    ]
    assert findings == []


@pytest.mark.parametrize("bench", BLOCK_IDS)
def test_topology(graph, bench):
    g = graph(bench)
    for a, b in CONNECTED[bench]:
        assert g.connected(a, b), f"{bench}: {a} should connect to {b}"
    for a, b in ISOLATED[bench]:
        assert not g.connected(a, b), f"{bench}: {a} must NOT connect to {b}"


@pytest.mark.parametrize("bench", BLOCK_IDS)
def test_snapshot(graph, bench):
    g = graph(bench)
    snap_path = SNAPSHOT_DIR / f"{bench}.snap.json"
    current = g.summary()
    if os.environ.get("CIRCUIT_UPDATE_SNAPSHOTS") == "1" or not snap_path.exists():
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    committed = json.loads(snap_path.read_text())
    assert current == committed, (
        f"{bench}: circuit summary drifted from committed snapshot "
        f"{snap_path.name} — review the diff; CIRCUIT_UPDATE_SNAPSHOTS=1 "
        "regenerates after review"
    )


def test_every_block_has_blockmd_and_bench():
    from conftest import GB_ROOT
    block_dirs = sorted(
        p.name for p in (GB_ROOT / "blocks").iterdir() if p.is_dir()
    )
    assert block_dirs == sorted(BLOCK_IDS), (
        "blocks/ dirs and BLOCK_IDS drifted"
    )
    for bid in block_dirs:
        assert (GB_ROOT / "blocks" / bid / f"{bid}.tsx").exists()
        assert (GB_ROOT / "blocks" / bid / "BLOCK.md").exists(), (
            f"{bid} is missing BLOCK.md"
        )
        assert (GB_ROOT / "testbench" / f"{bid}.tsx").exists()
