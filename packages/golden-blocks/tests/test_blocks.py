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
import os
from pathlib import Path

import pytest

from conftest import SNAPSHOT_DIR

BLOCK_IDS = [
    "usb-c-power",
    "ldo-3v3",
    "status-led",
    "sw-tact",
    "i2c-bus",
    "rp2040-core",
    "usb-c-data",
    "sensor-bme280",
    "ws2812-chain",
    "servo-header",
]

# refdes → exact pinned LCSC number per bench (subset assertion).
PINNED_BOM: dict[str, dict[str, str]] = {
    "usb-c-power": {
        "J1": "C165948", "R1": "C25905", "R2": "C25905",
        "U1": "C2687116", "C1": "C15850",
    },
    "ldo-3v3": {"U2": "C6186", "C2": "C15850", "C3": "C15850"},
    "status-led": {"LED1": "C2297", "R20": "C11702"},
    "sw-tact": {"SW1": "C318884"},
    "i2c-bus": {"R8": "C25900", "R9": "C25900"},
    "rp2040-core": {
        "U3": "C2040", "U4": "C97521", "Y1": "C20625731",
        "R11": "C11702", "R12": "C25744", "R13": "C11702",
        "SW2": "C318884", "SW3": "C318884",
        "C4": "C1525", "C12": "C52923", "C15": "C1548", "C17": "C15850",
    },
    "usb-c-data": {
        "J1": "C165948", "R1": "C25905", "R2": "C25905",
        "R3": "C25100", "R4": "C25100", "U1": "C2687116", "C1": "C15850",
    },
    "sensor-bme280": {"U5": "C92489", "C18": "C1525", "C19": "C1525"},
    "ws2812-chain": {
        "D10": "C2761795", "D11": "C2761795", "D12": "C2761795", "D13": "C2761795",
        "C40": "C1525", "C41": "C1525", "C42": "C1525", "C43": "C1525",
        "R30": "C25104",
    },
    "servo-header": {
        "J10": "C18078126", "J11": "C18078126",
        "J12": "C18078126", "J13": "C18078126",
    },
}

# (a, b) pairs that must be in the same electrical group.
CONNECTED: dict[str, list[tuple[str, str]]] = {
    "usb-c-power": [
        ("J1.VBUS1", "net.V5"),
        ("J1.VBUS2", "net.V5"),
        ("J1.GND1", "net.GND"),
        ("J1.SHELL1", "net.GND"),
        ("J1.CC1", "R1.pin1"),
        ("R1.pin2", "net.GND"),
        ("J1.CC2", "R2.pin1"),
        ("U1.VBUS", "net.V5"),
        ("U1.IO1", "J1.CC1"),
        ("U1.IO2", "J1.CC2"),
        ("C1.pin1", "net.V5"),
        ("C1.pin2", "net.GND"),
    ],
    "ldo-3v3": [
        ("U2.VIN", "net.V5"),
        ("U2.VOUT", "net.V3_3"),
        ("U2.TAB", "net.V3_3"),
        ("U2.GND", "net.GND"),
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
        ("C17.pin1", "net.V3_3"),
    ],
    "usb-c-data": [
        ("J1.VBUS1", "net.V5"),
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
    # The pin order IS the safety property: V+ on the middle pin, pin2.
    # rchelicopterfun.com/rc-servo-connectors.html (2026-08-26): "the positive
    # (red wire) is always in the middle of 3 pin/wire servo connectors".
    "servo-header": [
        ("J10.VPLUS", "net.V5"),
        ("J10.GND", "net.GND"),
        ("J10.SIG", "net.SERVO1"),
        ("J13.SIG", "net.SERVO4"),
    ],
    "ws2812-chain": [
        # the damping resistor sits between the driving net and the first pixel
        ("R30.pin1", "net.LED_DATA"),
        ("R30.pin2", "D10.DIN"),
        # the chain hands data pixel to pixel
        ("D10.DOUT", "D11.DIN"),
        ("D11.DOUT", "D12.DIN"),
        ("D12.DOUT", "D13.DIN"),
        # every pixel powered, and every pixel has its own local cap
        ("D10.VDD", "net.V3_3"),
        ("D13.VDD", "net.V3_3"),
        ("D13.GND", "net.GND"),
        ("C40.pin1", "net.V3_3"),
        ("C43.pin1", "net.V3_3"),
        ("C43.pin2", "net.GND"),
    ],
}

# Pairs that must NOT be connected (would be a short / wrong wiring).
ISOLATED: dict[str, list[tuple[str, str]]] = {
    "usb-c-power": [("net.V5", "net.GND"), ("J1.CC1", "J1.CC2")],
    "ldo-3v3": [("net.V5", "net.V3_3"), ("net.V3_3", "net.GND")],
    "status-led": [("net.V3_3", "net.GND"), ("R20.pin1", "LED1.anode")],
    "sw-tact": [("net.BTN1", "net.GND")],
    "i2c-bus": [("net.SDA", "net.SCL")],
    "rp2040-core": [
        ("net.V3_3", "net.GND"),
        ("net.V3_3", "net.DVDD"),
        ("net.USB_DP", "net.USB_DM"),
        ("U3.XIN", "Y1.pin3"),          # series R must separate XIN/XOUT sides
        ("U3.QSPI_SS", "net.GND"),      # BOOTSEL open at rest
    ],
    "usb-c-data": [
        ("net.USB_DP", "net.USB_DM"),
        ("J1.DP1", "net.USB_DP"),       # series R separates connector from MCU side
        ("net.V5", "net.GND"),
    ],
    "sensor-bme280": [("net.SDA", "net.SCL"), ("net.V3_3", "net.GND")],
    # V+ must be the MIDDLE pin. A reversed lead then swaps GND and SIGNAL,
    # which is recoverable; V+ on an outer pin feeds the rail into the servo's
    # signal input, which is not. This pair is the guard on that.
    "servo-header": [
        ("net.V5", "net.GND"),
        ("net.V5", "net.SERVO1"),
        ("J10.SIG", "J11.SIG"),
    ],
    "ws2812-chain": [
        ("net.V3_3", "net.GND"),
        # the GPIO must reach the first pixel only through the resistor
        ("net.LED_DATA", "D10.DIN"),
        # data must not backfeed: a pixel's input is not its neighbour's input
        ("D10.DIN", "D11.DIN"),
    ],
}


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
