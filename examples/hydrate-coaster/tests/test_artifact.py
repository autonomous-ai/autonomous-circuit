"""Parsed-artifact regressions for Hydrate's reusable routing contracts.

These checks intentionally read the committed build artifact.  A clean CLI
exit is not evidence that a PCB is connected: tscircuit serializes compiler
errors inside circuit.json, and a geometrically touching trace may still end
on the wrong copper layer.  The tests below prove authored power/data trees,
hidden routing boundaries, short plane drops, and solved pour copper from the
parsed artifact independently of the renderer.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
ARTIFACT = Path(
    os.environ.get(
        "HYDRATE_ARTIFACT",
        PROJECT_ROOT / "boards" / "main.circuit.json",
    )
)

sys.path.insert(0, str(REPO_ROOT / "packages" / "circuitpy" / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "verify" / "src"))

from circuitpy.checks import (  # noqa: E402
    _component_names,
    _trace_endpoint_layer_warnings,
)
from verifylib import intent, netclass  # noqa: E402
from verifylib.model import Board  # noqa: E402


@pytest.fixture(scope="session")
def elements() -> list[dict]:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def product() -> dict:
    return json.loads((PROJECT_ROOT / "product.json").read_text(encoding="utf-8"))


def _one(elements: list[dict], element_type: str, **fields: object) -> dict:
    matches = [
        element
        for element in elements
        if element.get("type") == element_type
        and all(element.get(key) == value for key, value in fields.items())
    ]
    assert len(matches) == 1, (element_type, fields, len(matches))
    return matches[0]


def _source_port(elements: list[dict], component: str, hint: str) -> dict:
    component_id = _one(
        elements, "source_component", name=component
    )["source_component_id"]
    matches = [
        element
        for element in elements
        if element.get("type") == "source_port"
        and element.get("source_component_id") == component_id
        and (
            element.get("name") == hint
            or hint in (element.get("port_hints") or [])
        )
    ]
    assert len(matches) == 1, (component, hint, len(matches))
    return matches[0]


def _pcb_port(elements: list[dict], source_port: dict) -> dict:
    return _one(
        elements,
        "pcb_port",
        source_port_id=source_port["source_port_id"],
    )


def _pcb_trace(elements: list[dict], source_trace: dict) -> dict:
    source_trace_id = source_trace["source_trace_id"]
    matches = [
        element
        for element in elements
        if element.get("type") == "pcb_trace"
        and (
            element.get("source_trace_id") == source_trace_id
            or element.get("connection_name") == source_trace_id
        )
    ]
    assert len(matches) == 1, (source_trace.get("name"), len(matches))
    return matches[0]


def _source_trace(elements: list[dict], name: str) -> dict:
    return _one(elements, "source_trace", name=name)


def _source_net(elements: list[dict], name: str) -> dict:
    return _one(elements, "source_net", name=name)


def _pcb_component(elements: list[dict], name: str) -> dict:
    source = _one(elements, "source_component", name=name)
    return _one(
        elements,
        "pcb_component",
        source_component_id=source["source_component_id"],
    )


def _trace_vias(elements: list[dict], trace: dict) -> list[dict]:
    return [
        element
        for element in elements
        if element.get("type") == "pcb_via"
        and element.get("pcb_trace_id") == trace["pcb_trace_id"]
    ]


def _endpoint_layers(trace: dict) -> dict[str, str]:
    endpoints: dict[str, str] = {}
    for point in trace.get("route") or []:
        if point.get("route_type") != "wire":
            continue
        for field in ("start_pcb_port_id", "end_pcb_port_id"):
            if point.get(field):
                endpoints[str(point[field])] = str(point.get("layer") or "")
    return endpoints


def _route_length_mm(trace: dict) -> float:
    points = [
        (float(point["x"]), float(point["y"]))
        for point in trace.get("route") or []
        if isinstance(point.get("x"), (int, float))
        and isinstance(point.get("y"), (int, float))
    ]
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
    )


def _assert_port_to_port_route(
    elements: list[dict], source_trace: dict, source_ports: list[dict]
) -> dict:
    expected_source_ids = {port["source_port_id"] for port in source_ports}
    assert set(source_trace.get("connected_source_port_ids") or []) == expected_source_ids
    assert source_trace.get("connected_source_net_ids") in (None, [])

    topology_key = source_trace.get("subcircuit_connectivity_map_key")
    assert topology_key
    assert {
        port.get("subcircuit_connectivity_map_key") for port in source_ports
    } == {topology_key}

    pcb_ports = [_pcb_port(elements, port) for port in source_ports]
    trace = _pcb_trace(elements, source_trace)
    endpoint_layers = _endpoint_layers(trace)
    assert set(endpoint_layers) == {port["pcb_port_id"] for port in pcb_ports}
    for port in pcb_ports:
        assert endpoint_layers[port["pcb_port_id"]] in set(port.get("layers") or [])
    return trace


def test_product_declares_the_plane_and_local_fanout_contract(product: dict) -> None:
    layout = product["layout"]
    assert layout["boardSizeMm"] == [80, 80]
    assert layout["minCopperClearanceMm"] == 0.15
    assert layout["groundPlanes"] == {
        "layers": ["top", "bottom"],
        "maxRoutedLengthMm": 30,
        "maxFanoutLengthMm": 2.0,
        "stitchingPitchMm": 10,
    }
    assert layout["decoupling"] == {
        "maxDistanceMm": 2.0,
        "exclude": ["U1"],
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
            "name": "USB_ATTACH_POWER",
            "nets": ["VBUS_RAW"],
            "minTrunkWidthMm": 0.8,
            "minNeckdownWidthMm": 0.2,
            "maxNeckdownLengthMm": 3,
            "minViaOuterDiameterMm": 0.8,
            "minViaHoleDiameterMm": 0.5,
        },
        {
            "name": "CONTROL_SIGNAL",
            "nets": [
                "LED_NUDGE",
                "BTN_MUTE",
                "SWCLK",
                "SWD",
                "USB_POWER_FAULT",
                "CAP_DRIVE",
                "CAP_A_SENSE",
                "CAP_B_SENSE",
            ],
            "minTrunkWidthMm": 0.25,
            "minNeckdownWidthMm": 0.15,
            "maxNeckdownLengthMm": 1,
        },
    ]


def test_compiled_artifact_satisfies_every_layout_intent(
    elements: list[dict], product: dict
) -> None:
    assert intent.check(Board(elements), product["layout"]).findings == []


def test_compiled_artifact_has_no_serialized_errors_or_endpoint_opens(
    elements: list[dict],
) -> None:
    assert [
        element
        for element in elements
        if str(element.get("type") or "").endswith("_error")
    ] == []
    assert _trace_endpoint_layer_warnings(elements, _component_names(elements)) == []


def test_testen_and_debug_ground_are_independent_short_plane_contacts(
    elements: list[dict],
) -> None:
    testen = _source_port(elements, "U3", "TESTEN")
    debug_gnd = _source_port(elements, "TP3", "pin1")
    ground = _one(elements, "source_net", name="GND")
    source_traces = []
    for name, port in (("TR_U3_testen", testen), ("TR_TP3", debug_gnd)):
        source_trace = _source_trace(elements, name)
        source_traces.append(source_trace)
        assert source_trace.get("connected_source_port_ids") == [
            port["source_port_id"]
        ]
        assert source_trace.get("connected_source_net_ids") == [
            ground["source_net_id"]
        ]
        assert {
            source_trace.get("subcircuit_connectivity_map_key"),
            port.get("subcircuit_connectivity_map_key"),
            ground.get("subcircuit_connectivity_map_key"),
        } == {ground["subcircuit_connectivity_map_key"]}
        assert _route_length_mm(_pcb_trace(elements, source_trace)) <= 2.0 + 1e-9

    # TESTEN is no longer visually tied to the probe.  Each pad has its own
    # one-port plane contact, so a future debug-port move cannot stretch the
    # QFN ground escape across the board.
    assert source_traces[0]["source_trace_id"] != source_traces[1]["source_trace_id"]
    assert set(_pcb_port(elements, debug_gnd).get("layers") or []) == {
        "top",
        "bottom",
    }


def test_usb_raw_power_is_one_authored_wide_tree(elements: list[dict]) -> None:
    vbus1 = _source_port(elements, "J1", "VBUS1")
    vbus2 = _source_port(elements, "J1", "VBUS2")
    right = _source_port(elements, "N13", "pin1")
    left = _source_port(elements, "N14", "pin1")
    rail = _source_port(elements, "N15", "pin1")
    raw = _source_net(elements, "VBUS_RAW")

    neck1_source = _source_trace(elements, "TR_J1_vbus1_neck")
    neck1 = _assert_port_to_port_route(elements, neck1_source, [vbus1, right])
    neck2_source = _source_trace(elements, "TR_J1_vbus2_neck")
    neck2 = _assert_port_to_port_route(elements, neck2_source, [vbus2, left])
    rail_source = _source_trace(elements, "TR_J1_vbus_rail")
    _assert_port_to_port_route(elements, rail_source, [left, rail])
    source_trace = _source_trace(elements, "TR_J1_vbus_trunk")
    trace = _assert_port_to_port_route(elements, source_trace, [right, left])

    for neck_source, neck in ((neck1_source, neck1), (neck2_source, neck2)):
        assert neck_source["min_trace_thickness"] == pytest.approx(0.2)
        assert _route_length_mm(neck) <= 2.0 + 1e-9
    assert source_trace["min_trace_thickness"] == pytest.approx(0.8)
    assert rail_source["min_trace_thickness"] == pytest.approx(0.8)

    boundary = _source_trace(elements, "TR_J1_vbus_boundary")
    assert boundary.get("connected_source_port_ids") == [rail["source_port_id"]]
    assert boundary.get("connected_source_net_ids") == [raw["source_net_id"]]
    assert boundary["min_trace_thickness"] == pytest.approx(0.8)
    assert {
        right.get("subcircuit_connectivity_map_key"),
        left.get("subcircuit_connectivity_map_key"),
        rail.get("subcircuit_connectivity_map_key"),
        raw.get("subcircuit_connectivity_map_key"),
        boundary.get("subcircuit_connectivity_map_key"),
    } == {raw["subcircuit_connectivity_map_key"]}

    # The two reversible connector pads are leaves of this tree. They must
    # not also own direct VBUS_RAW edges that recreate a parallel cycle.
    assert [
        candidate
        for candidate in elements
        if candidate.get("type") == "source_trace"
        and candidate.get("source_trace_id")
        not in {neck1_source["source_trace_id"], neck2_source["source_trace_id"]}
        and {vbus1["source_port_id"], vbus2["source_port_id"]}.intersection(
            candidate.get("connected_source_port_ids") or []
        )
        and raw["source_net_id"] in (candidate.get("connected_source_net_ids") or [])
    ] == []

    route = trace.get("route") or []
    vias = [point for point in route if point.get("route_type") == "via"]
    assert len(vias) == 2
    assert {
        (float(via["via_diameter"]), float(via["via_hole_diameter"]))
        for via in vias
    } == {(0.8, 0.5)}
    assert {
        (via.get("from_layer"), via.get("to_layer")) for via in vias
    } == {("top", "bottom"), ("bottom", "top")}
    endpoint_layers = set(_endpoint_layers(trace).values())
    assert endpoint_layers == {"top"}
    assert "bottom" in {
        point.get("layer")
        for point in route
        if point.get("route_type") == "wire"
    }

    # Route-point metadata alone is insufficient: fabrication exports consume
    # the standalone pcb_via records.  The trace-local style must reach both.
    compiled_vias = _trace_vias(elements, trace)
    assert len(compiled_vias) == 2
    assert {
        (float(via["outer_diameter"]), float(via["hole_diameter"]))
        for via in compiled_vias
    } == {(0.8, 0.5)}
    assert all(
        via.get("subcircuit_connectivity_map_key")
        == source_trace["subcircuit_connectivity_map_key"]
        for via in compiled_vias
    )


def test_usb_orientation_pairs_are_explicit_symmetric_local_trees(
    elements: list[dict],
) -> None:
    channels = {
        "dp": {
            "connector": ["DP1", "DP2"],
            "resistor": "R3",
            "esd": ["IO1", "IO1B"],
            "net": "USB_DP",
        },
        "dm": {
            "connector": ["DM1", "DM2"],
            "resistor": "R4",
            "esd": ["IO2", "IO2B"],
            "net": "USB_DM",
        },
    }
    pair_lengths: dict[str, float] = {}
    for suffix, channel in channels.items():
        connector_ports = [
            _source_port(elements, "J1", str(hint))
            for hint in channel["connector"]
        ]
        pair_source = _source_trace(elements, f"TR_J1_{suffix}_pair")
        pair_trace = _assert_port_to_port_route(elements, pair_source, connector_ports)
        pair_lengths[suffix] = _route_length_mm(pair_trace)

        route_vias = [
            point
            for point in pair_trace.get("route") or []
            if point.get("route_type") == "via"
        ]
        assert len(route_vias) == 2
        assert {
            (via.get("from_layer"), via.get("to_layer")) for via in route_vias
        } == {("top", "bottom"), ("bottom", "top")}
        assert {
            (float(via["via_diameter"]), float(via["via_hole_diameter"]))
            for via in route_vias
        } == {(0.6, 0.3)}
        assert {
            (float(via["outer_diameter"]), float(via["hole_diameter"]))
            for via in _trace_vias(elements, pair_trace)
        } == {(0.6, 0.3)}

        resistor_pin = _source_port(elements, str(channel["resistor"]), "pin1")
        connector_boundary = connector_ports[1 if suffix == "dp" else 0]
        esd_ports = [
            _source_port(elements, "U1", str(hint)) for hint in channel["esd"]
        ]
        esd_trace = _assert_port_to_port_route(
            elements,
            _source_trace(elements, f"TR_J1_{suffix}_esd"),
            [connector_boundary, esd_ports[0]],
        )
        resistor_trace = _assert_port_to_port_route(
            elements,
            _source_trace(elements, f"TR_U1_{suffix}_r"),
            [esd_ports[1], resistor_pin],
        )
        pair_lengths[suffix] += _route_length_mm(esd_trace)
        pair_lengths[suffix] += _route_length_mm(resistor_trace)

        # The USBLC6 channel is physically continuous inside the package.
        # The two copper edges terminate on opposite clamp pins; there is no
        # board-level bypass around the ESD device.
        assert {
            esd_ports[0].get("subcircuit_connectivity_map_key"),
            esd_ports[1].get("subcircuit_connectivity_map_key"),
            pair_source.get("subcircuit_connectivity_map_key"),
        } == {pair_source["subcircuit_connectivity_map_key"]}

        # Only the protected, series-resistor output may meet the board net;
        # connector and ESD-tree pads must not join a competing aggregate MST.
        board_net = _source_net(elements, str(channel["net"]))
        tree_port_ids = {
            port["source_port_id"]
            for port in [*connector_ports, *esd_ports, resistor_pin]
        }
        assert [
            trace
            for trace in elements
            if trace.get("type") == "source_trace"
            and board_net["source_net_id"]
            in (trace.get("connected_source_net_ids") or [])
            and tree_port_ids.intersection(trace.get("connected_source_port_ids") or [])
        ] == []

    assert {
        net.get("name")
        for net in elements
        if net.get("type") == "source_net"
        and net.get("name") in {"USB_DP_CONN", "USB_DM_CONN"}
    } == set()

    assert abs(pair_lengths["dp"] - pair_lengths["dm"]) <= 3.8
    assert [
        finding
        for finding in netclass.check(Board(elements)).findings
        if finding.get("kind") == "netclass_pair_skew"
    ] == []


def test_internal_routing_nodes_are_masked_and_debug_escapes_are_bounded(
    elements: list[dict],
) -> None:
    hidden_refs = {f"N{index}" for index in range(1, 16)}
    component_names = {
        element["name"]
        for element in elements
        if element.get("type") == "source_component"
    }
    assert hidden_refs <= component_names
    assert {
        name for name in component_names if name.startswith("TP")
    } == {"TP1", "TP2", "TP3", "TP4", "TP5", "TP6", "TP7", "TP10"}

    for ref in hidden_refs:
        component = _pcb_component(elements, ref)
        assert component.get("do_not_place") is True
        pads = [
            element
            for element in elements
            if element.get("type") == "pcb_smtpad"
            and element.get("pcb_component_id") == component["pcb_component_id"]
        ]
        assert len(pads) == 1, ref
        assert pads[0].get("covered_with_solder_mask") is True

    for signal, ref in (("SWCLK", "N1"), ("SWD", "N2")):
        qfn = _source_port(elements, "U3", signal)
        boundary = _source_port(elements, ref, "pin1")
        escape_source = _source_trace(
            elements, f"TR_U3_{signal.lower()}_escape"
        )
        escape = _assert_port_to_port_route(
            elements, escape_source, [qfn, boundary]
        )
        assert escape_source["min_trace_thickness"] == pytest.approx(0.15)
        assert _route_length_mm(escape) <= 1.0 + 1e-9

        wide = _source_trace(elements, f"TR_{ref}")
        board_net = _source_net(elements, signal)
        assert wide.get("connected_source_port_ids") == [
            boundary["source_port_id"]
        ]
        assert wide.get("connected_source_net_ids") == [
            board_net["source_net_id"]
        ]
        assert wide["min_trace_thickness"] == pytest.approx(0.25)


def test_rp2040_power_is_an_acyclic_local_tree_with_one_wide_boundary(
    elements: list[dict],
) -> None:
    # N3..N12 are the board-allocated internal rail waypoints.  Nine wide
    # edges over ten nodes is a tree; N11 is its sole V3_3 boundary.
    rail_edges = {
        "TR_V3_BULK_TOPRIGHT": ("N11", "N8"),
        "TR_V3_TOPRIGHT_TOPMIDDLE": ("N8", "N9"),
        "TR_V3_TOPMIDDLE_TOPLEFT": ("N9", "N10"),
        "TR_V3_TOPLEFT_WESTUPPER": ("N10", "N3"),
        "TR_V3_WESTUPPER_WESTLOWER": ("N3", "N4"),
        "TR_V3_WESTLOWER_SOUTH": ("N4", "N5"),
        "TR_V3_TOPRIGHT_EASTUPPER": ("N8", "N7"),
        "TR_V3_EASTUPPER_EASTLOWER": ("N7", "N6"),
        "TR_V3_TOPLEFT_FLASH": ("N10", "N12"),
    }
    adjacency: dict[str, set[str]] = {f"N{index}": set() for index in range(3, 13)}
    for trace_name, (left_ref, right_ref) in rail_edges.items():
        left = _source_port(elements, left_ref, "pin1")
        right = _source_port(elements, right_ref, "pin1")
        source = _source_trace(elements, trace_name)
        _assert_port_to_port_route(elements, source, [left, right])
        assert source["min_trace_thickness"] == pytest.approx(0.8)
        adjacency[left_ref].add(right_ref)
        adjacency[right_ref].add(left_ref)

    seen: set[str] = set()
    stack = ["N11"]
    while stack:
        ref = stack.pop()
        if ref in seen:
            continue
        seen.add(ref)
        stack.extend(adjacency[ref] - seen)
    assert seen == set(adjacency)
    assert sum(len(neighbours) for neighbours in adjacency.values()) // 2 == 9

    rail = _source_net(elements, "V3_3")
    bulk = _source_port(elements, "N11", "pin1")
    boundary = _source_trace(elements, "TR_V3_ESCAPE")
    assert boundary.get("connected_source_port_ids") == [bulk["source_port_id"]]
    assert boundary.get("connected_source_net_ids") == [rail["source_net_id"]]
    assert boundary["min_trace_thickness"] == pytest.approx(0.8)

    neck_names = {
        "TR_V3_C4_NECK",
        "TR_V3_C5_NECK",
        "TR_V3_C6_NECK",
        "TR_V3_C7_NECK",
        "TR_V3_C8_NECK",
        "TR_V3_C9_NECK",
        "TR_V3_C10_NECK",
        "TR_V3_C11_NECK",
        "TR_V3_C14_NECK",
        "TR_V3_C17_NECK",
        "TR_V3_R12_NECK",
        "TR_U4_vcc_rail",
    }
    for name in neck_names:
        source = _source_trace(elements, name)
        assert source["min_trace_thickness"] == pytest.approx(0.2)
        assert _route_length_mm(_pcb_trace(elements, source)) <= 3.0 + 1e-9

    dvdd = _source_net(elements, "DVDD")
    dvdd_boundary = _source_trace(elements, "TR_DVDD_ESCAPE")
    assert dvdd_boundary.get("connected_source_net_ids") == [dvdd["source_net_id"]]
    assert dvdd_boundary["min_trace_thickness"] == pytest.approx(0.8)
    for name in (
        "TR_U3_vregout_C13",
        "TR_U3_dvdd1_C13",
        "TR_U3_dvdd2_C12",
    ):
        assert _source_trace(elements, name)["min_trace_thickness"] == pytest.approx(
            0.2
        )
    assert _source_trace(elements, "TR_DVDD_C13_C12")[
        "min_trace_thickness"
    ] == pytest.approx(0.8)


def test_ldo_caps_are_local_and_vout_tab_are_one_physical_rail(
    elements: list[dict],
) -> None:
    vin = _source_port(elements, "U2", "VIN")
    tab = _source_port(elements, "U2", "TAB")
    vout = _source_port(elements, "U2", "VOUT")
    cin = _source_port(elements, "C2", "pin1")
    cout = _source_port(elements, "C3", "pin1")

    assert tab["subcircuit_connectivity_map_key"] == vout[
        "subcircuit_connectivity_map_key"
    ]
    vin_source = _source_trace(elements, "TR_U2_vin_C2")
    vin_route = _assert_port_to_port_route(elements, vin_source, [vin, cin])
    assert vin_source["min_trace_thickness"] == pytest.approx(0.2)
    assert _route_length_mm(vin_route) <= 3.0 + 1e-9

    tab_source = _source_trace(elements, "TR_U2_tab_C3")
    _assert_port_to_port_route(elements, tab_source, [tab, cout])
    assert tab_source["min_trace_thickness"] == pytest.approx(0.8)

    v5_boundary = _source_trace(elements, "TR_C2_V5_boundary")
    v5 = _source_net(elements, "V5")
    assert v5_boundary.get("connected_source_port_ids") == [cin["source_port_id"]]
    assert v5_boundary.get("connected_source_net_ids") == [v5["source_net_id"]]
    assert v5_boundary["min_trace_thickness"] == pytest.approx(0.8)

    # The board-level V3_3 PowerTrunk starts at TAB, so the block must not add
    # a second cap-to-net boundary around the package-internal VOUT/TAB short.
    assert [
        trace
        for trace in elements
        if trace.get("type") == "source_trace"
        and trace.get("name") == "TR_C3_V3_3_boundary"
    ] == []


def test_usb_power_entry_is_current_limited_and_fault_visible(
    elements: list[dict],
) -> None:
    raw = _source_net(elements, "VBUS_RAW")
    v5 = _source_net(elements, "V5")
    fault = _source_net(elements, "USB_POWER_FAULT")
    c24 = _source_port(elements, "C24", "pin1")
    out = _source_port(elements, "U7", "OUT")
    fault_pad = _source_port(elements, "TP10", "pin1")

    raw_boundary = _source_trace(elements, "TR_C24_raw")
    assert raw_boundary.get("connected_source_port_ids") == [c24["source_port_id"]]
    assert raw_boundary.get("connected_source_net_ids") == [raw["source_net_id"]]
    assert raw_boundary["min_trace_thickness"] == pytest.approx(0.3)
    assert _route_length_mm(_pcb_trace(elements, raw_boundary)) <= 3.0 + 1e-9

    # OUT is owned only by the board PowerTrunk. A second block edge to V5
    # would close the old duplicate source-net cycle.
    assert [
        trace
        for trace in elements
        if trace.get("type") == "source_trace"
        and trace.get("name") == "TR_U7_out"
    ] == []
    assert [
        trace
        for trace in elements
        if trace.get("type") == "source_trace"
        and out["source_port_id"] in (trace.get("connected_source_port_ids") or [])
        and v5["source_net_id"] in (trace.get("connected_source_net_ids") or [])
    ] == []

    fault_boundary = _source_trace(elements, "TR_TP10_fault")
    assert fault_boundary.get("connected_source_port_ids") == [
        fault_pad["source_port_id"]
    ]
    assert fault_boundary.get("connected_source_net_ids") == [
        fault["source_net_id"]
    ]
    assert fault_boundary["min_trace_thickness"] == pytest.approx(0.25)

    gpio5 = _source_port(elements, "U3", "GPIO5")
    board_fault = _source_trace(elements, "TR_USB_POWER_FAULT")
    assert board_fault.get("connected_source_port_ids") == [gpio5["source_port_id"]]
    assert board_fault.get("connected_source_net_ids") == [fault["source_net_id"]]
    assert board_fault["min_trace_thickness"] == pytest.approx(0.25)


def test_power_trunks_are_acyclic_wide_copper_with_short_boundaries(
    elements: list[dict],
) -> None:
    component_names = [
        element["name"]
        for element in elements
        if element.get("type") == "source_component"
    ]
    assert len(component_names) == len(set(component_names))
    assert {"TP4", "TP5", "TP6", "TP7"} <= set(component_names)

    for trunk, start_ref, end_ref, rail in (
        ("V5_ENTRY", "TP4", "TP5", "V5"),
        ("V3_3_REG", "TP6", "TP7", "V3_3"),
    ):
        start = _source_port(elements, start_ref, "pin1")
        end = _source_port(elements, end_ref, "pin1")
        rail_net = _source_net(elements, rail)
        incoming = _source_trace(elements, f"TR_{trunk}_IN")
        fixed = _source_trace(elements, f"TR_{trunk}_TRUNK")
        outgoing = _source_trace(elements, f"TR_{trunk}_OUT")

        assert len(incoming.get("connected_source_port_ids") or []) == 2
        assert start["source_port_id"] in incoming["connected_source_port_ids"]
        source_port_id = next(
            port_id
            for port_id in incoming["connected_source_port_ids"]
            if port_id != start["source_port_id"]
        )
        assert incoming.get("connected_source_net_ids") in (None, [])
        _assert_port_to_port_route(elements, fixed, [start, end])
        assert outgoing.get("connected_source_port_ids") == [end["source_port_id"]]
        assert outgoing.get("connected_source_net_ids") == [rail_net["source_net_id"]]
        assert {
            incoming.get("subcircuit_connectivity_map_key"),
            fixed.get("subcircuit_connectivity_map_key"),
            outgoing.get("subcircuit_connectivity_map_key"),
            rail_net.get("subcircuit_connectivity_map_key"),
        } == {rail_net["subcircuit_connectivity_map_key"]}

        # The block source edge is replaced, not duplicated: otherwise the
        # source -> rail direct edge closes a redundant cycle around the trunk.
        assert [
            trace
            for trace in elements
            if trace.get("type") == "source_trace"
            and trace.get("source_trace_id") != incoming["source_trace_id"]
            and source_port_id in (trace.get("connected_source_port_ids") or [])
            and rail_net["source_net_id"]
            in (trace.get("connected_source_net_ids") or [])
        ] == []

        assert incoming["min_trace_thickness"] == pytest.approx(0.2)
        assert fixed["min_trace_thickness"] == pytest.approx(0.8)
        assert outgoing["min_trace_thickness"] == pytest.approx(0.8)
        fixed_pcb = _pcb_trace(elements, fixed)
        assert _trace_vias(elements, fixed_pcb) == []
        assert {
            float(point["width"])
            for point in fixed_pcb.get("route") or []
            if point.get("route_type") == "wire"
        } == {0.8}
        assert _pcb_component(elements, start_ref).get("do_not_place") is True
        assert _pcb_component(elements, end_ref).get("do_not_place") is True


def test_control_signals_are_authored_at_the_product_width(
    elements: list[dict],
) -> None:
    # These are the outboard/control branches where 0.25mm copper fits.  The
    # two fixed QFN debug toes are tested separately as bounded 0.15mm escapes.
    wide_control_traces = {
        "TR_LED_NUDGE",
        "TR_R21_rail",
        "TR_BTN_MUTE",
        "TR_USB_POWER_FAULT",
        "TR_R41_fault",
        "TR_TP10_fault",
        "TR_SW1_p1",
        "TR_SW1_p2",
        "TR_CAPDRV",
        "TR_R30_drv",
        "TR_R31_drv",
        "TR_R32_mcu",
        "TR_R33_mcu",
        "TR_SENSE_A",
        "TR_SENSE_B",
    }
    for name in wide_control_traces:
        assert _source_trace(elements, name)["min_trace_thickness"] == pytest.approx(
            0.25
        )

    fault_escape_source = _source_trace(elements, "TR_U7_fault")
    assert fault_escape_source["min_trace_thickness"] == pytest.approx(0.15)
    assert _route_length_mm(_pcb_trace(elements, fault_escape_source)) <= 1.0 + 1e-9
    assert _source_trace(elements, "TR_R41_pullup")[
        "min_trace_thickness"
    ] == pytest.approx(0.2)

    # USB, QSPI/crystal, and the CAP_A/CAP_B electrode copper are deliberate
    # signal-integrity/fine-pitch exceptions, not forgotten control nets.
    exceptions = {
        "USB_DP",
        "USB_DM",
        "CAP_A",
        "CAP_B",
    }
    control = {
        "LED_NUDGE",
        "BTN_MUTE",
        "SWCLK",
        "SWD",
        "USB_POWER_FAULT",
        "CAP_DRIVE",
        "CAP_A_SENSE",
        "CAP_B_SENSE",
    }
    assert control.isdisjoint(exceptions)
    assert {
        net["name"]
        for net in elements
        if net.get("type") == "source_net" and net.get("name") in exceptions
    } == exceptions


def test_ground_planes_preserve_the_top_electrode_zone(elements: list[dict]) -> None:
    nets = {
        element["name"]: element
        for element in elements
        if element.get("type") == "source_net"
    }
    assert {"GND", "CAP_A", "CAP_B"} <= set(nets)

    pours = [
        element for element in elements if element.get("type") == "pcb_copper_pour"
    ]
    by_net = {
        name: [
            pour
            for pour in pours
            if pour.get("source_net_id") == nets[name]["source_net_id"]
        ]
        for name in ("GND", "CAP_A", "CAP_B")
    }
    assert {pour.get("layer") for pour in by_net["GND"]} == {"top", "bottom"}

    top_ground = [pour for pour in by_net["GND"] if pour.get("layer") == "top"]
    assert top_ground
    for pour in top_ground:
        vertices = pour["brep_shape"]["outer_ring"]["vertices"]
        assert max(float(point["y"]) for point in vertices) <= -3 + 1e-7

    for electrode in ("CAP_A", "CAP_B"):
        assert by_net[electrode]
        assert {pour.get("layer") for pour in by_net[electrode]} == {"top"}
        assert all(pour.get("covered_with_solder_mask") is True for pour in by_net[electrode])
        assert any(
            max(
                float(point["y"])
                for point in pour["brep_shape"]["outer_ring"]["vertices"]
            )
            > 20
            for pour in by_net[electrode]
        )


def test_ground_stitch_and_plane_fanouts_are_physically_connected(
    elements: list[dict],
) -> None:
    ground = _one(elements, "source_net", name="GND")
    expected_stitches = {
        (-34, -34), (-24, -34), (-14, -34), (-6, -34),
        (6, -34), (16, -34), (26, -34), (34, -34),
        (-34, -24), (-30, -24), (-12, -24), (-4, -24),
        (6, -24), (16, -24), (22, -24), (34, -24),
        (-34, -14), (-24, -14), (-14, -14), (-4, -14),
        (6, -14), (20, -14), (34, -14), (30, -10),
        (-34, -4), (-24, -4), (-14, -4), (-4, -4),
        (6, -4), (16, -4), (26, -4), (34, -4),
    }
    stitches = [
        element
        for element in elements
        if element.get("type") == "pcb_via"
        and (
            float(element.get("x", 1e9)),
            float(element.get("y", 1e9)),
        ) in expected_stitches
    ]
    assert {
        (float(via["x"]), float(via["y"])) for via in stitches
    } == expected_stitches
    assert all(set(via.get("layers") or []) == {"top", "bottom"} for via in stitches)
    assert all(
        via.get("subcircuit_connectivity_map_key")
        == ground["subcircuit_connectivity_map_key"]
        for via in stitches
    )
    assert {
        (float(via["outer_diameter"]), float(via["hole_diameter"]))
        for via in stitches
    } == {(0.6, 0.3)}

    # Count is not enough: every authored through-stitch must physically land
    # in solved material on both poured faces.  This rejects a nominal lattice
    # point that falls into a component/electrode clearance void.
    ground_islands = [
        island
        for pour in elements
        if pour.get("type") == "pcb_copper_pour"
        and pour.get("source_net_id") == ground["source_net_id"]
        and (island := intent._island(pour)) is not None
    ]
    for x, y in expected_stitches:
        assert {
            island["layer"]
            for island in ground_islands
            if intent._island_contains(island, float(x), float(y))
        } == {"top", "bottom"}, (x, y)

    # The plane planner owns the named GND net. Direct drops remain authored
    # source traces; synthesizing a whole-board source_net route is redundant
    # and was the capacity failure exposed by this board.
    assert [
        trace
        for trace in elements
        if trace.get("type") == "pcb_trace"
        and trace.get("connection_name") == ground["source_net_id"]
    ] == []

    plane_findings = [
        finding
        for finding in intent.check(Board(elements), None).findings
        if finding.get("kind")
        in {"pcb_plane_connectivity_error", "pcb_copper_pour_short_error"}
    ]
    assert plane_findings == []


def test_every_direct_ground_drop_is_a_short_local_dogbone(
    elements: list[dict],
) -> None:
    ground = _one(elements, "source_net", name="GND")
    direct_drop_ids = {
        trace["source_trace_id"]
        for trace in elements
        if trace.get("type") == "source_trace"
        and trace.get("connected_source_net_ids") == [ground["source_net_id"]]
        and len(trace.get("connected_source_port_ids") or []) == 1
    }
    fanouts = [
        trace
        for trace in elements
        if trace.get("type") == "pcb_trace"
        and str(trace.get("pcb_trace_id") or "").startswith("fanout:")
    ]
    assert len(direct_drop_ids) >= 34
    assert {
        trace.get("source_trace_id") or trace.get("connection_name")
        for trace in fanouts
    } == direct_drop_ids

    lengths = {
        str(trace.get("pcb_trace_id")): _route_length_mm(trace) for trace in fanouts
    }
    assert lengths
    assert max(lengths.values()) <= 2.0 + 1e-9, sorted(
        lengths.items(), key=lambda item: item[1], reverse=True
    )[:10]
